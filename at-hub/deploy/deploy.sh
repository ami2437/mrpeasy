#!/bin/bash
# Deploy AT-HUB from this PC to the cloud server (run in Git Bash from anywhere).
#
#   deploy/deploy.sh test            tests pass -> the pushed code goes to https://test.americantradershub.com
#   deploy/deploy.sh prod            same, to the real site https://americantradershub.com
#   deploy/deploy.sh refresh-test    give the test site a fresh copy of the real data
#   deploy/deploy.sh push-data       after a fresh MRPeasy import here: its data -> the real site (keeps the site's
#                                    user accounts + company profile; backup first), then test gets a copy
#   deploy/deploy.sh status          what's running where
#   add --no-tests to skip the local test run (only for a deploy right after a full test pass)
#
# What gets deployed is exactly what's pushed to GitHub (origin/feature/at-hub) -- never uncommitted work.
# On the server: database backup first, then the new code, restart, smoke test; if the smoke test fails the previous
# version is put back automatically (deploy/server/athub-deploy.sh).
set -euo pipefail
HOST=${ATHUB_HOST:-192.241.247.199}
KEY=${ATHUB_KEY:-$HOME/.ssh/athub_server}
BRANCH=feature/at-hub
HERE=$(cd "$(dirname "$0")" && pwd)
ATHUB=$(cd "$HERE/.." && pwd)
REPO=$(git -C "$ATHUB" rev-parse --show-toplevel)
SSH="ssh -i $KEY -o BatchMode=yes root@$HOST"

ACTION=${1:-}; shift || true
RUN_TESTS=1
for a in "$@"; do [ "$a" = "--no-tests" ] && RUN_TESTS=0; done

sync_scripts() {
  $SSH "mkdir -p /opt/athub-bin /root/incoming"
  scp -i "$KEY" -o BatchMode=yes -q "$HERE"/server/*.sh "root@$HOST:/opt/athub-bin/"
  $SSH "sed -i 's/\r\$//' /opt/athub-bin/*.sh && chmod 700 /opt/athub-bin/*.sh"
}

case "$ACTION" in
  test|prod)
    git -C "$REPO" fetch -q origin "$BRANCH"
    if [ -n "$(git -C "$REPO" status --porcelain -- at-hub ':!at-hub/*.db*' ':!at-hub/uploads' ':!at-hub/backups')" ]; then
      echo "Uncommitted changes in at-hub/ -- commit and push first (only pushed code is deployed)."; exit 1
    fi
    LOCAL=$(git -C "$REPO" rev-parse HEAD); REMOTE=$(git -C "$REPO" rev-parse "origin/$BRANCH")
    [ "$LOCAL" = "$REMOTE" ] || { echo "HEAD ($LOCAL) isn't what's on GitHub ($REMOTE) -- push first."; exit 1; }
    SHA=$(git -C "$REPO" rev-parse --short "origin/$BRANCH")
    if [ $RUN_TESTS = 1 ]; then
      echo "== tests"
      (cd "$ATHUB" && venv/Scripts/python.exe -m pytest -q -p no:cacheprovider) | tail -3
    fi
    TMP=$(mktemp -d)
    git -C "$REPO" archive --format=tar "origin/$BRANCH:at-hub" > "$TMP/code.tar"
    mkdir -p "$TMP/v" && echo "$SHA $(git -C "$REPO" log -1 --format=%cs origin/$BRANCH) $(git -C "$REPO" log -1 --format=%s origin/$BRANCH | cut -c1-80)" > "$TMP/v/VERSION"
    tar --force-local -rf "$TMP/code.tar" -C "$TMP/v" VERSION && gzip -f "$TMP/code.tar"
    echo "== upload $SHA"
    sync_scripts
    scp -i "$KEY" -o BatchMode=yes -q "$TMP/code.tar.gz" "root@$HOST:/root/incoming/code-$SHA.tar.gz"
    rm -rf "$TMP"
    $SSH "/opt/athub-bin/athub-deploy.sh $ACTION /root/incoming/code-$SHA.tar.gz; rc=\$?; ls -1t /root/incoming/code-*.tar.gz | tail -n +6 | xargs -r rm -f; exit \$rc"
    ;;
  refresh-test)
    sync_scripts
    $SSH "/opt/athub-bin/athub-refresh-test.sh"
    ;;
  push-data)
    # a fresh MRPeasy import made on this PC -> the real site (its own user accounts + company profile kept), then test
    TMP=$(mktemp -d)
    (cd "$ATHUB" && venv/Scripts/python.exe -c "
import sqlite3, sys
a = sqlite3.connect('at_hub.db'); b = sqlite3.connect(sys.argv[1]); a.backup(b)
b.execute('pragma journal_mode=delete'); b.close()" "$TMP/at_hub.import.db")
    sync_scripts
    echo "== upload database"
    scp -i "$KEY" -o BatchMode=yes -q "$TMP/at_hub.import.db" "root@$HOST:/root/incoming/at_hub.import.db"
    rm -rf "$TMP"
    echo "== attached files (new ones only)"
    tar cf - -C "$ATHUB" uploads | $SSH "rm -rf /tmp/up && mkdir -p /tmp/up && tar xf - -C /tmp/up && rsync -a --ignore-existing /tmp/up/uploads/ /opt/athub/data/uploads/ && chown -R athub:athub /opt/athub/data/uploads && rm -rf /tmp/up"
    $SSH "/opt/athub-bin/athub-load-data.sh prod /root/incoming/at_hub.import.db"
    $SSH "/opt/athub-bin/athub-refresh-test.sh"
    ;;
  status)
    $SSH 'for s in athub athub-test; do d=/opt/${s/athub-test/athub-test}; [ $s = athub ] && d=/opt/athub; printf "%-11s %-8s %s\n" "$s" "$(systemctl is-active $s 2>/dev/null)" "$(cat $d/app/VERSION 2>/dev/null || echo -)"; done; free -h | sed -n 2p; df -h / | tail -1'
    ;;
  *)
    sed -n '2,15p' "$0"; exit 2 ;;
esac
