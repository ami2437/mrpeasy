#!/bin/bash
# Put a freshly imported database (made on the PC) on the real site, KEEPING the server's own user accounts and
# company profile (people sign up / change passwords on the cloud -- an import must never drop them).
#   athub-load-data.sh prod|test /root/incoming/at_hub.import.db
# Backup first (data/backups/before-import-<time>.db), swap, smoke test; on failure the old database is put back.
set -euo pipefail
. /opt/athub-bin/athub-instances.sh
WHICH=$1
instance "$WHICH"
NEW=$2
[ -f "$NEW" ] || { echo "no file $NEW"; exit 2; }
[ "$(sqlite3 "$NEW" 'pragma integrity_check')" = ok ] || { echo "the uploaded database is damaged -- nothing changed"; exit 1; }
D=$DIR/data
STAMP=$(date -u +%Y%m%d-%H%M%S)
BK=$D/backups/before-import-$STAMP.db
mkdir -p "$D/backups"
sqlite3 "$D/at_hub.db" ".backup '$BK'"
echo "backup: $BK"
# columns both databases have (the server may run a newer / older schema)
cols() { sqlite3 "$1" "pragma table_info($2)" | cut -d'|' -f2 | sort; }
common() { comm -12 <(cols "$NEW" "$1") <(cols "$BK" "$1") | paste -sd, -; }
U=$(common users); C=$(common company_profile)
sqlite3 "$NEW" "attach '$BK' as live; begin;
  delete from users; insert into users ($U) select $U from live.users;
  delete from company_profile; insert into company_profile ($C) select $C from live.company_profile;
  commit;"
echo "kept $(sqlite3 "$NEW" 'select count(*) from users') user accounts from the site"
systemctl stop "$SVC"
rm -f "$D/at_hub.db-wal" "$D/at_hub.db-shm"
mv "$NEW" "$D/at_hub.db"
chown -R athub:athub "$D"
systemctl start "$SVC"
sleep 4
if ! /opt/athub-bin/athub-smoke.sh "$WHICH"; then
  echo "smoke test failed -- putting the old database back"
  systemctl stop "$SVC"; rm -f "$D/at_hub.db-wal" "$D/at_hub.db-shm"
  cp "$BK" "$D/at_hub.db"; chown athub:athub "$D/at_hub.db"; systemctl start "$SVC"; exit 1
fi
ls -1t "$D"/backups/before-import-*.db | tail -n +6 | xargs -r rm -f
echo "$WHICH site now has the imported data (its own accounts kept)"
