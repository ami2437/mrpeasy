"""MRPeasy -> AT-HUB import.

    python -m app.importers.mrpeasy extract              download a fresh snapshot (read-only)
    python -m app.importers.mrpeasy load [SNAPSHOT]      build at_hub_import.db from a snapshot
    python -m app.importers.mrpeasy reconcile [SNAPSHOT] compare at_hub_import.db with the snapshot
    python -m app.importers.mrpeasy dryrun               load + reconcile the latest snapshot
    python -m app.importers.mrpeasy promote              back up at_hub.db, then replace it with at_hub_import.db
                                                         (stop AT-HUB first; restart it afterwards)
    python -m app.importers.mrpeasy sample [N]           random-sample validation sheet (local HTML)
"""
import sys
from pathlib import Path


def main(argv):
    cmd = argv[0] if argv else "help"
    snap = Path(argv[1]) if len(argv) > 1 else None
    if cmd == "extract":
        from .extract import extract
        extract()
    elif cmd in ("load", "reconcile", "dryrun"):
        from .extract import latest_snapshot
        snap = snap or latest_snapshot()
        if cmd in ("load", "dryrun"):
            from .load import load
            load(snap)
        if cmd in ("reconcile", "dryrun"):
            from .reconcile import reconcile
            ok = reconcile(snap)
            sys.exit(0 if ok else 1)
    elif cmd == "sample":
        from .extract import latest_snapshot
        from .sample import sample
        sample(latest_snapshot(), int(argv[1]) if len(argv) > 1 else 4)
    elif cmd == "promote":
        from .promote import promote
        promote()
    else:
        print(__doc__)


main(sys.argv[1:])
