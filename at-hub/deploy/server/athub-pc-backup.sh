#!/bin/bash
# The PC's automatic backups (deploy/pc/athub-backup.ps1) come through here. The "athubbak" key can run only this script
# (athub-pc-backup-setup.sh), with one of:
#   db            -> a .zip on stdout: a consistent copy of the real site's database + README
#   full          -> the same + every attached file (uploads/)
#   saved db|full <bytes>  -> the PC saved it: AT-HUB's "download a backup" reminder counts it (same as a download)
# Same layout as the in-app download (services/backups.build_local_copy), so a restore works the same way.
set -euo pipefail
ARGS=${1:-${SSH_ORIGINAL_COMMAND:-}}
DATA=/opt/athub/data
DB=$DATA/at_hub.db
case "$ARGS" in
  db|full) KIND=$ARGS ;;
  "saved db "[0-9]*|"saved full "[0-9]*)
    read -r _ KIND SIZE <<<"$ARGS"
    [[ "$SIZE" =~ ^[0-9]+$ ]] || { echo "bad size" >&2; exit 2; }
    FILES=false; [ "$KIND" = full ] && FILES=true
    NOW=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
    VALUE="{\"at\": \"$NOW\", \"by\": \"automatic backup (PC)\", \"files\": $FILES, \"size\": $SIZE}"
    sqlite3 "$DB" "insert into app_settings (key, value, updated_by, updated_at) values ('last_local_backup', '$VALUE', 'automatic backup (PC)', datetime('now'))
                   on conflict(key) do update set value = excluded.value, updated_by = excluded.updated_by, updated_at = excluded.updated_at;"
    echo ok
    exit 0 ;;
  *) echo "usage: db | full | saved db|full <bytes>" >&2; exit 2 ;;
esac
TMP=$(mktemp -d /tmp/athub-pcbak.XXXXXX)
trap 'rm -rf "$TMP"' EXIT
sqlite3 "$DB" ".backup '$TMP/at_hub.db'"
[ "$(sqlite3 "$TMP/at_hub.db" 'pragma integrity_check')" = ok ] || { echo "the copy failed its check" >&2; exit 1; }
python3 - "$TMP" "$KIND" "$DATA/uploads" <<'PY'
import sys, zipfile
from datetime import datetime
from pathlib import Path
tmp, kind, uploads = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
stamp = datetime.utcnow()
with zipfile.ZipFile(tmp / "out.zip", "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    z.write(tmp / "at_hub.db", "at_hub.db")
    n = 0
    if kind == "full" and uploads.exists():
        for f in sorted(uploads.rglob("*")):
            if f.is_file():
                z.write(f, "uploads/" + f.relative_to(uploads).as_posix(), compress_type=zipfile.ZIP_STORED)
                n += 1
    z.writestr("README.txt", (
        f"AT-HUB backup taken {stamp:%Y-%m-%d %H:%M} UTC (automatic, from the real site)\n\n"
        "at_hub.db   the whole database: orders, POs, shipments, invoices, payments, stock, Accounting, users, settings\n"
        + (f"uploads/    every attached file ({n} files): customer POs, vendor invoices, MTRs, proofs of delivery\n" if kind == "full"
           else "            (attached files are NOT in this copy -- the 'everything' copies have them)\n")
        + "\nTo restore: give this file to whoever runs the AT-HUB server. at_hub.db goes back as the database;\n"
          "uploads/ goes back as the uploads folder. Saved mailbox passwords need the server's SECRET_KEY (keep it in\n"
          "your password manager) -- without it they're typed in again. Keep this file private: it holds all of the\n"
          "company's records.\n"))
PY
cat "$TMP/out.zip"
