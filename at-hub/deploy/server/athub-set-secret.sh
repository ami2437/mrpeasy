#!/bin/bash
# Put a secret into AT-HUB's settings without it ever being shown, typed into chat, or kept in shell history.
# Run on the server (DigitalOcean -> Droplet -> Access -> Launch Droplet Console):
#     athub-set-secret anthropic        the Claude API key (sk-ant-...)
#     athub-set-secret smtp             the email password (Gmail: an App Password)
# It asks for the value with typing hidden, writes it, restarts AT-HUB and checks it came back.
# The Claude key goes to the real site AND the test site (user's choice, 2026-10-09); the email password only to the
# real site, so the test site can never email a customer.
set -euo pipefail
case "${1:-}" in
  anthropic) VAR=ANTHROPIC_API_KEY; WHAT="Claude API key (starts with sk-ant-)"; PREFIX="sk-ant-"; SITES="athub:8010 athub-test:8011" ;;
  smtp)      VAR=SMTP_PASSWORD;     WHAT="email password / App Password";        PREFIX="";        SITES="athub:8010" ;;
  *) echo "usage: athub-set-secret anthropic|smtp"; exit 2 ;;
esac
read -r -s -p "Paste the $WHAT, then press Enter (nothing will show): " VALUE; echo
VALUE=$(printf '%s' "$VALUE" | tr -d '[:space:]')
[ -n "$VALUE" ] || { echo "Nothing pasted -- no change."; exit 1; }
if [ -n "$PREFIX" ] && [ "${VALUE#"$PREFIX"}" = "$VALUE" ]; then
  echo "That doesn't look like a $WHAT -- no change."; exit 1
fi
rc=0
for site in $SITES; do
  SVC=${site%%:*}; PORT=${site##*:}; ENV=/opt/$SVC/.env
  [ -f "$ENV" ] || continue
  cp "$ENV" "$ENV.bak"
  if grep -q "^$VAR=" "$ENV"; then
    python3 - "$ENV" "$VAR" "$VALUE" <<'PY'
import sys
path, var, value = sys.argv[1:]
lines = open(path).read().split("\n")
lines = [f"{var}={value}" if l.startswith(var + "=") else l for l in lines]
open(path, "w").write("\n".join(lines))
PY
  else
    printf '%s=%s\n' "$VAR" "$VALUE" >> "$ENV"
  fi
  chmod 600 "$ENV"; chown athub:athub "$ENV"
  systemctl restart "$SVC"
  for i in $(seq 1 30); do curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null && break; sleep 1; done
  if curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null; then
    rm -f "$ENV.bak"; echo "Saved on $SVC -- restarted and running. ($VAR ends in ...${VALUE: -4})"
  else
    mv "$ENV.bak" "$ENV"; systemctl restart "$SVC"; echo "$SVC didn't come back -- its old settings were put back. Tell Claude."; rc=1
  fi
done
exit $rc
