#!/bin/bash
# Deploy a code tarball to one AT-HUB instance, with a database backup first and an automatic rollback.
#   athub-deploy.sh test|prod /root/incoming/code-<sha>.tar.gz
# 1 back up the database   2 unpack the new code next to the old   3 install packages if requirements changed
# 4 swap the code and restart   5 smoke test -- on failure the previous code goes back and is smoke-tested again.
# The database is never replaced (new columns are added by the app at startup and old code ignores them);
# the pre-deploy backup is there if a restore is ever needed.
set -euo pipefail
. /opt/athub-bin/athub-instances.sh
INST=${1:?test or prod}; TAR=${2:?code tarball}
instance "$INST"
TS=$(date -u +%Y%m%d-%H%M%S)

echo "== [$INST] 1/5 backup"
mkdir -p "$DIR/data/backups"
sqlite3 "$DIR/data/at_hub.db" ".backup '$DIR/data/backups/at_hub-$TS-predeploy.db'"
ls -1t "$DIR"/data/backups/*-predeploy.db | tail -n +11 | xargs -r rm -f   # keep the last 10

echo "== [$INST] 2/5 unpack"
rm -rf "$DIR/app.new" && mkdir -p "$DIR/app.new"
tar -xzf "$TAR" -C "$DIR/app.new"

# cache-busting: every page links style.css / *.js with ?v=<release>, so a deploy is seen at once even where a cache
# (Cloudflare's 4-hour browser TTL on static files) would otherwise keep serving the old ones
V=$(cut -d' ' -f1 "$DIR/app.new/VERSION" 2>/dev/null || echo "$TS")
find "$DIR/app.new/frontend" -name '*.html' -exec sed -i -E "s#(src|href)=\"([A-Za-z0-9._-]+\.(js|css))\"#\1=\"\2?v=$V\"#g" {} +
echo "   pages link scripts / styles as ?v=$V"

echo "== [$INST] 3/5 packages"
if ! cmp -s "$DIR/app.new/requirements.txt" "$DIR/app/requirements.txt" 2>/dev/null; then
  "$DIR/venv/bin/pip" install -q -r "$DIR/app.new/requirements.txt" && echo "   requirements changed -- installed"
else
  echo "   unchanged"
fi

echo "== [$INST] 4/5 swap + restart"
rm -rf "$DIR/app.prev"
[ -d "$DIR/app" ] && mv "$DIR/app" "$DIR/app.prev"
mv "$DIR/app.new" "$DIR/app"
chown -R athub:athub "$DIR/app"
systemctl restart "$SVC"

echo "== [$INST] 5/5 smoke test"
if /opt/athub-bin/athub-smoke.sh "$INST"; then
  echo "DEPLOYED to $INST ($(cat "$DIR/app/VERSION" 2>/dev/null || echo '?'))"
  exit 0
fi

echo "!! smoke test failed -- putting the previous version back"
rm -rf "$DIR/app.failed"
mv "$DIR/app" "$DIR/app.failed"
mv "$DIR/app.prev" "$DIR/app"
systemctl restart "$SVC"
if /opt/athub-bin/athub-smoke.sh "$INST"; then
  echo "ROLLED BACK: $INST is on the previous version again (failed code kept in $DIR/app.failed)"
else
  echo "!! ROLLBACK ALSO FAILED -- check: journalctl -u $SVC -n 80"
fi
exit 1
