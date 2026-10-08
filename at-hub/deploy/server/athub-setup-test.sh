#!/bin/bash
# One-time: create the test instance (/opt/athub-test, service athub-test on 127.0.0.1:8011) next to the real one.
# Code comes from the real site's current code; data from athub-refresh-test.sh. Safe to run again.
set -euo pipefail
. /opt/athub-bin/athub-instances.sh
instance test
mkdir -p "$DIR/data/backups" "$DIR/data/uploads"
if [ ! -d "$DIR/app" ]; then cp -a /opt/athub/app "$DIR/app"; fi
if [ ! -f "$DIR/.env" ]; then
  cat > "$DIR/.env" <<EOF
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
DATABASE_URL=sqlite:///$DIR/data/at_hub.db
UPLOAD_DIR=$DIR/data/uploads
BACKUP_DIR=$DIR/data/backups
BACKUP_EVERY_HOURS=0
BUSINESS_TIMEZONE=America/Chicago
TEST_DATA_ENABLED=true
SITE_LABEL="TEST SITE"
PUBLIC_URL=https://$DOMAIN
# no email from the test site, so customers never get a test invoice
SMTP_HOST=
ANTHROPIC_API_KEY=
EOF
fi
chmod 600 "$DIR/.env"
[ -d "$DIR/venv" ] || python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install -q --upgrade pip
"$DIR/venv/bin/pip" install -q -r "$DIR/app/requirements.txt"
chown -R athub:athub "$DIR"
cat > /etc/systemd/system/$SVC.service <<EOF
[Unit]
Description=AT-HUB test site
After=network.target

[Service]
User=athub
Group=athub
WorkingDirectory=$DIR/app
EnvironmentFile=$DIR/.env
ExecStart=$DIR/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port $PORT --workers 1 --proxy-headers
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SVC" >/dev/null 2>&1
echo "test instance ready in $DIR (start it with athub-refresh-test.sh)"
