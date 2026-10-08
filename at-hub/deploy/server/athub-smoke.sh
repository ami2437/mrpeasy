#!/bin/bash
# Quick check that an AT-HUB instance really works: it answers, an admin can sign in, the main screens' data loads,
# an invoice PDF renders, and the log has no tracebacks since the last start.   athub-smoke.sh test|prod
set -uo pipefail
. /opt/athub-bin/athub-instances.sh
instance "${1:?test or prod}"
B=http://127.0.0.1:$PORT
for i in $(seq 1 40); do curl -fs "$B/api/health" >/dev/null && break; sleep 1; done
curl -fs "$B/api/health" >/dev/null || { echo "   FAIL: not answering on $PORT"; exit 1; }

TOKEN=$(cd "$DIR/app" && set -a && . "$DIR/.env" && set +a && \
  sudo -u athub -E "$DIR/venv/bin/python" -c "from app.services.auth import AuthService; print(AuthService.create_access_token({'sub': 'admin'}))") \
  || { echo "   FAIL: couldn't make a test login"; exit 1; }
fail=0
for u in /api/auth/me /api/customer-orders/ /api/shipments/ /api/invoices/ /api/purchase-orders/ /api/stock-items/ /api/reports/action-items /api/insights/; do
  code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN" "$B$u")
  [ "$code" = "200" ] || { echo "   FAIL: $u -> $code"; fail=1; }
done
INV=$(curl -s -H "Authorization: Bearer $TOKEN" "$B/api/invoices/" | "$DIR/venv/bin/python" -c "import sys,json; l=json.load(sys.stdin); print(l[0]['id'] if l else '')")
if [ -n "$INV" ]; then
  head=$(curl -s -H "Authorization: Bearer $TOKEN" "$B/api/invoices/$INV/pdf" | head -c 5)
  [ "$head" = "%PDF-" ] || { echo "   FAIL: invoice PDF"; fail=1; }
fi
since=$(systemctl show -p ActiveEnterTimestamp --value "$SVC")
tb=$(journalctl -u "$SVC" --since "$since" --no-pager 2>/dev/null | grep -c "Traceback")
[ "$tb" = "0" ] || { echo "   FAIL: $tb traceback(s) in the log since start"; fail=1; }
[ $fail = 0 ] && echo "   smoke OK ($1: sign-in, orders, shipments, invoices, POs, stock, dashboard, insights, PDF)"
exit $fail
