#!/bin/bash
# Which AI an AT-HUB instance would use right now (the office PC's Ollama over its link, or Claude).
#   athub-ai-status.sh test|prod
set -euo pipefail
. /opt/athub-bin/athub-instances.sh
instance "${1:-prod}"
cd "$DIR/app"
set -a; . "$DIR/.env"; set +a
T=$(sudo -u athub -E "$DIR/venv/bin/python" -c "from app.services.auth import AuthService; print(AuthService.create_access_token({'sub': 'admin'}))")
curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:$PORT/api/ai-orders/status"; echo
