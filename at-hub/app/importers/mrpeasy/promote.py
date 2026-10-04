"""Make the last dry run the database AT-HUB uses: back up at_hub.db, then copy
at_hub_import.db over it. Run with AT-HUB stopped (Windows locks an open database).

Backups are kept as at_hub.backup-<time>.db, so any cycle can be undone by copying
one back.
"""
import shutil
import sqlite3
from datetime import datetime

from .load import LIVE_DB, TARGET_DB


def promote():
    if not TARGET_DB.exists():
        raise SystemExit("No at_hub_import.db yet: run  python -m app.importers.mrpeasy dryrun")
    if not sqlite3.connect(TARGET_DB).execute("select count(*) from number_series").fetchone()[0]:
        raise SystemExit("at_hub_import.db doesn't look like a finished import -- run dryrun again")
    if LIVE_DB.exists():
        # AT-HUB runs SQLite in WAL mode: fold the journal into the file and drop it, so neither the backup
        # nor the new database picks up the other's leftover at_hub.db-wal / -shm.
        try:
            con = sqlite3.connect(LIVE_DB)
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.execute("PRAGMA journal_mode=DELETE")
            con.close()
        except sqlite3.OperationalError:
            raise SystemExit("at_hub.db is in use -- stop AT-HUB first, then run promote again")
        for side in (LIVE_DB.with_name(LIVE_DB.name + "-wal"), LIVE_DB.with_name(LIVE_DB.name + "-shm")):
            if side.exists():
                side.unlink()
        backup = LIVE_DB.with_name(f"at_hub.backup-{datetime.now():%Y%m%d-%H%M%S}.db")
        try:
            LIVE_DB.rename(backup)
        except PermissionError:
            raise SystemExit("at_hub.db is in use -- stop AT-HUB first, then run promote again")
        print(f"Backed up the current database to {backup.name}")
    shutil.copy2(TARGET_DB, LIVE_DB)
    print(f"at_hub.db now holds the imported MRPeasy data. Start AT-HUB to look at it.")
