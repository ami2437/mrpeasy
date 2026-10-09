"""Database backups: automatic every few hours, on demand, and restore.

Uses SQLite's online backup API, so a copy is consistent even while AT-HUB is running. Files:
backups/at_hub-<yyyymmdd-hhmmss>-<kind>.db  (kind: auto | manual | pre-restore). Uploaded files
(uploads/) are not in the database and are not copied here -- OneDrive / a disk backup covers those."""
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from app.config.database import engine
from app.config.settings import settings

KINDS = ("auto", "manual", "pre-restore", "pre-import")


def db_path() -> Path:
    return Path(engine.url.database).resolve()


def backup_dir() -> Path:
    d = Path(settings.backup_dir).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def copy_dirs() -> List[Path]:
    """Second (third...) places every backup is copied to, so one lost disk never loses them all."""
    out = []
    for d in (settings.backup_copies or "").split(";"):
        if d.strip():
            out.append(Path(d.strip()).expanduser().resolve())
    return out


def _mirror(f: Path) -> List[str]:
    """Copy one backup to every copy folder; returns problems (a missing drive never stops the backup)."""
    problems = []
    for d in copy_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, d / f.name)
            if f.name.endswith("-auto.db"):
                for old in sorted(d.glob("at_hub-*-auto.db"))[:-max(1, settings.backup_keep)]:
                    old.unlink(missing_ok=True)
        except OSError as e:
            problems.append(f"{d}: {e}")
    return problems


def _copy(src: Path, dst: Path) -> None:
    s, d = sqlite3.connect(src), sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def backup_now(kind: str = "manual") -> Path:
    if engine.url.get_backend_name() != "sqlite":
        raise RuntimeError("Backups here work for the SQLite database only")
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    dst, n = backup_dir() / f"at_hub-{stamp}-{kind}.db", 1
    while dst.exists():  # two in the same second never overwrite each other
        n += 1
        dst = backup_dir() / f"at_hub-{stamp}{n}-{kind}.db"
    _copy(db_path(), dst)
    if kind == "auto":
        autos = sorted(backup_dir().glob("at_hub-*-auto.db"))
        for old in autos[:-max(1, settings.backup_keep)]:
            old.unlink(missing_ok=True)
    for p in _mirror(dst):
        print(f"[backup] copy failed: {p}")
    return dst


def list_backups() -> List[dict]:
    out = []
    for f in sorted(backup_dir().glob("at_hub-*.db"), reverse=True):
        parts = f.stem.split("-")
        kind = "-".join(parts[3:]) or "manual"
        out.append({"name": f.name, "size": f.stat().st_size, "kind": kind,
                    "copies": [(d / f.name).exists() for d in copy_dirs()],
                    "created": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")})
    return out


def restore(name: str) -> Path:
    """Put a backup back as the live database. The current one is backed up first (pre-restore)."""
    src = (backup_dir() / name).resolve()
    if src.parent != backup_dir() or not src.exists() or src.suffix != ".db":
        raise FileNotFoundError(name)
    check = sqlite3.connect(src)
    try:
        if check.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise ValueError(f"{name} failed its integrity check")
    finally:
        check.close()
    safety = backup_now("pre-restore")
    engine.dispose()  # drop pooled connections so nothing holds the old pages
    _copy(src, db_path())
    return safety


def _loop() -> None:
    every = settings.backup_every_hours * 3600
    while True:
        last = max((f.stat().st_mtime for f in backup_dir().glob("at_hub-*-auto.db")), default=0)
        wait = last + every - time.time()
        if wait <= 0:
            try:
                backup_now("auto")
            except Exception as e:  # never take the app down over a backup
                print(f"[backup] failed: {e}")
            wait = every
        time.sleep(min(wait, 3600))


def start_scheduler() -> None:
    if settings.backup_every_hours > 0 and engine.url.get_backend_name() == "sqlite":
        threading.Thread(target=_loop, name="db-backups", daemon=True).start()


# ---- a copy for the user's own computer (Backups -> Download; required every LOCAL_DUE_DAYS) ----
LOCAL_DUE_DAYS = 3
LOCAL_KEY = "last_local_backup"


def local_status(db) -> dict:
    """When someone last downloaded a backup to their own computer, and whether one is due."""
    import json
    from app.models import AppSetting
    row = db.get(AppSetting, LOCAL_KEY)
    last = json.loads(row.value) if row and row.value else None
    days = None
    if last:
        days = (datetime.now(timezone.utc) - datetime.fromisoformat(last["at"])).total_seconds() / 86400
    return {"last": last, "days_since": round(days, 1) if days is not None else None,
            "due": days is None or days >= LOCAL_DUE_DAYS, "due_days": LOCAL_DUE_DAYS,
            "db_bytes": db_path().stat().st_size if db_path().exists() else 0,
            "files_bytes": _folder_bytes(Path(settings.upload_dir))}


def _folder_bytes(d: Path) -> int:
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d.exists() else 0


def build_local_copy(include_files: bool) -> Path:
    """A .zip in a temp folder: a consistent copy of the database (+ every attached file) and a README on restoring it."""
    import tempfile
    import zipfile
    stamp = f"{datetime.now():%Y-%m-%d-%H%M}"
    tmp = Path(tempfile.mkdtemp(prefix="athub-dl-"))
    db_copy = tmp / "at_hub.db"
    _copy(db_path(), db_copy)
    out = tmp / f"AT-HUB-backup-{stamp}-{'everything' if include_files else 'database'}.zip"
    uploads = Path(settings.upload_dir).resolve()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.write(db_copy, "at_hub.db")
        n = 0
        if include_files and uploads.exists():
            for f in sorted(uploads.rglob("*")):
                if f.is_file():  # PDFs / photos are compressed already: store them as they are
                    z.write(f, "uploads/" + f.relative_to(uploads).as_posix(), compress_type=zipfile.ZIP_STORED)
                    n += 1
        z.writestr("README.txt", (
            f"AT-HUB backup taken {datetime.now():%Y-%m-%d %H:%M} (server time)\n\n"
            f"at_hub.db   the whole database: orders, POs, shipments, invoices, payments, stock, users, settings\n"
            + (f"uploads/    every attached file ({n} files): customer POs, vendor invoices, MTRs, proofs of delivery\n" if include_files
               else "            (attached files are NOT in this copy -- download 'Everything' for those)\n")
            + "\nTo restore: give this file to whoever runs the AT-HUB server. at_hub.db goes back as the database\n"
              "(Backups -> Restore does the same from the server's own copies); uploads/ goes back as the uploads folder.\n"
              "Keep it somewhere safe and private -- it holds all of the company's records.\n"))
    db_copy.unlink(missing_ok=True)
    return out


def record_local_copy(db, by: str, include_files: bool, size: int) -> None:
    import json
    from app.models import AppSetting
    value = json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": by, "files": include_files, "size": size})
    row = db.get(AppSetting, LOCAL_KEY)
    if row:
        row.value, row.updated_by = value, by
    else:
        db.add(AppSetting(key=LOCAL_KEY, value=value, updated_by=by))
    db.commit()
