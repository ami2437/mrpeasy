#!/bin/bash
# Give the test site a fresh copy of the real data (database + attached files). The test site's own changes are lost.
set -euo pipefail
. /opt/athub-bin/athub-instances.sh
instance prod; P=$DIR
instance test
systemctl stop "$SVC"
rm -f "$DIR/data/at_hub.db" "$DIR/data/at_hub.db-wal" "$DIR/data/at_hub.db-shm"
sqlite3 "$P/data/at_hub.db" ".backup '$DIR/data/at_hub.db'"   # consistent copy while the real site keeps running
mkdir -p "$DIR/data/uploads"
rsync -a --delete "$P/data/uploads/" "$DIR/data/uploads/"
chown -R athub:athub "$DIR/data"
systemctl start "$SVC"
echo "test site now has the real data as of $(date -u '+%Y-%m-%d %H:%M UTC')"
/opt/athub-bin/athub-smoke.sh test
