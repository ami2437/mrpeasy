#!/bin/bash
# Put a freshly imported database (made on the PC) on the real site, KEEPING the site's own:
#   - user accounts and company profile (people sign up / change passwords on the cloud)
#   - roles and their permissions (Users & Roles is edited on the site) + the "permissions_seen" setting
#   - Accounting -- every acct_* table + the acct_* settings (the user works on Accounting ON THE SITE; it has nothing to
#     do with MRPeasy) -- copied with app/importers/mrpeasy/keep.py, the same code a fresh import on the PC uses
#   athub-load-data.sh prod|test /root/incoming/at_hub.import.db
# The site is stopped first (nothing typed in during the push is lost), backed up (data/backups/before-import-<time>.db),
# then the import gets the site's users / company / roles / Accounting, the Accounting counts are checked, swap, smoke test;
# on any failure the old database is put back.
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
systemctl stop "$SVC"
restart_old() { systemctl start "$SVC"; }
trap 'echo "stopped before the swap -- the site keeps its own database"; restart_old' ERR
sqlite3 "$D/at_hub.db" ".backup '$BK'"
echo "backup: $BK"
# columns both databases have (the server may run a newer / older schema)
cols() { sqlite3 "$1" "pragma table_info($2)" | cut -d'|' -f2 | sort; }
common() { comm -12 <(cols "$NEW" "$1") <(cols "$BK" "$1") | paste -sd, -; }
U=$(common users); C=$(common company_profile); R=$(common roles); A=$(common app_settings)
sqlite3 "$NEW" "attach '$BK' as live; begin;
  delete from users; insert into users ($U) select $U from live.users;
  delete from company_profile; insert into company_profile ($C) select $C from live.company_profile;
  delete from roles; insert into roles ($R) select $R from live.roles;
  delete from app_settings where key = 'permissions_seen';
  insert into app_settings ($A) select $A from live.app_settings where key = 'permissions_seen';
  commit;"
echo "kept $(sqlite3 "$NEW" 'select count(*) from users') user accounts from the site"
# the roles must match the site's exactly (users point at them by key)
RS=$(sqlite3 "$BK" "select key || ':' || permissions from roles order by key" | md5sum)
RN=$(sqlite3 "$NEW" "select key || ':' || permissions from roles order by key" | md5sum)
[ "$RS" = "$RN" ] || { echo "STOPPED: the site's roles did not copy over"; false; }
echo "kept $(sqlite3 "$NEW" 'select count(*) from roles') roles (with their permissions) from the site"
# the site's Accounting, exactly as it is
cd "$DIR/app"
"$DIR/venv/bin/python" - "$NEW" "$BK" <<'PY'
import sys
from pathlib import Path
from app.importers.mrpeasy.keep import counts, keep_independent
new, live = Path(sys.argv[1]), Path(sys.argv[2])
before = counts(live)
kept = keep_independent(new, live)
after = counts(new)
lost = {t: (n, after.get(t, 0)) for t, n in before.items() if t != "acct_open_items" and after.get(t, 0) != n}
if lost:
    print("STOPPED: Accounting rows would change:", lost)
    sys.exit(4)
print("kept the site's Accounting:", ", ".join(f"{t} {n}" for t, n in kept.items()))
PY
trap - ERR
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
echo "$WHICH site now has the imported data (its own user accounts and Accounting kept)"
