#!/bin/bash
# Put a secret into AT-HUB's settings without it ever being shown, typed into chat, or kept in shell history.
# Run on the server (DigitalOcean -> Droplet -> Access -> Launch Droplet Console):
#     athub-set-secret anthropic        the Claude API key (sk-ant-...)
#     athub-set-secret smtp             the email password (Gmail: an App Password)
# It asks for the value with typing hidden, writes it to /opt/athub/.env (the real site only -- the test site never
# gets keys), restarts AT-HUB and checks it came back.
set -euo pipefail
case "${1:-}" in
  anthropic) VAR=ANTHROPIC_API_KEY; WHAT="Claude API key (starts with sk-ant-)"; PREFIX="sk-ant-" ;;
  smtp)      VAR=SMTP_PASSWORD;     WHAT="email password / App Password";        PREFIX="" ;;
  *) echo "usage: athub-set-secret anthropic|smtp"; exit 2 ;;
esac
ENV=/opt/athub/.env
read -r -s -p "Paste the $WHAT, then press Enter (nothing will show): " VALUE; echo
VALUE=$(printf '%s' "$VALUE" | tr -d '[:space:]')
[ -n "$VALUE" ] || { echo "Nothing pasted -- no change."; exit 1; }
if [ -n "$PREFIX" ] && [ "${VALUE#"$PREFIX"}" = "$VALUE" ]; then
  echo "That doesn't look like a $WHAT -- no change."; exit 1
fi
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
systemctl restart athub
for i in $(seq 1 30); do curl -fs http://127.0.0.1:8010/api/health >/dev/null && break; sleep 1; done
if curl -fs http://127.0.0.1:8010/api/health >/dev/null; then
  rm -f "$ENV.bak"
  echo "Saved. AT-HUB restarted and is running. ($VAR ends in ...${VALUE: -4})"
else
  mv "$ENV.bak" "$ENV"; systemctl restart athub
  echo "AT-HUB didn't come back -- the old settings were put back. Tell Claude."
  exit 1
fi
