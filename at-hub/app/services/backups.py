"""Database backups: automatic every few hours, on demand, and restore.

Uses SQLite's online backup API, so a copy is consistent even while AT-HUB is running. Files:
backups/at_hub-<yyyymmdd-hhmmss>-<kind>.db  (kind: auto | manual | pre-restore). Uploaded files
(uploads/) are not in the database and are not copied here -- OneDrive / a disk backup covers those."""
import sqlite3
import threading
import time
from datetime import datetime
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
    return dst


def list_backups() -> List[dict]:
    out = []
    for f in sorted(backup_dir().glob("at_hub-*.db"), reverse=True):
        parts = f.stem.split("-")
        kind = "-".join(parts[3:]) or "manual"
        out.append({"name": f.name, "size": f.stat().st_size, "kind": kind,
                    "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")})
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
