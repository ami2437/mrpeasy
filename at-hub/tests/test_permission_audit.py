"""Permission audit -- runs with every test pass, so a new feature can't ship without a decision about who may use it.

  * every /api route is behind a permission (require_perm / require_any / require_role), or is listed in OPEN below
    with the reason every signed-in user may call it. A new route that is neither FAILS here: add a permission to
    app/services/permissions.py CATALOG (and AuthGuard.PERM_DEFAULT in auth-guard.js), or list it in OPEN.
  * a money permission ($: prices, costs, totals, invoices, payments) defaults to Admin only -- employees and
    managers start without it; a custom role never gets a new permission until it's ticked on the Roles screen.
  * the screens' fallback list (AuthGuard.PERM_DEFAULT) has exactly the catalog's permissions, money ones at Admin.
"""
import re
from pathlib import Path

from fastapi.routing import APIRoute

from app.main import app
from app.services import permissions as P
from tests.test_roles import _user

# Open to every signed-in user (or before sign-in). Each one checks what it must inside, or holds nothing sensitive.
OPEN = {
    # signing in and your own account
    ("POST", "/api/auth/login"): "sign in",
    ("GET", "/api/auth/me"): "who am I",
    ("POST", "/api/auth/change-password"): "own password",
    ("PUT", "/api/auth/timezone"): "own time zone",
    ("GET", "/api/auth/print-options"): "own print choices",
    ("PUT", "/api/auth/print-options/{doc_type}"): "own print choices",
    ("GET", "/api/auth/recent"): "own recently viewed",
    ("POST", "/api/auth/recent"): "own recently viewed",
    ("GET", "/api/auth/filters/{page}"): "own saved filters",
    ("PUT", "/api/auth/filters/{page}"): "own saved filters",
    ("POST", "/api/auth/2fa/setup"): "own two-step login",
    ("POST", "/api/auth/2fa/enable"): "own two-step login",
    ("POST", "/api/auth/2fa/disable"): "own two-step login",
    ("GET", "/api/health"): "server is up",
    ("POST", "/api/presence"): "who else has this record open",
    ("GET", "/api/files/{fid}/{name}"): "one-time PDF link, made by a permitted request",
    # company name / logo on every page and printout
    ("GET", "/api/company/"): "letterhead (money fields scrubbed by the middleware)",
    ("GET", "/api/company/logo"): "letterhead",
    # personal planner: To-Do, sticky notes, calendar (scoped to the user / money scrubbed)
    **{(m, p): "own To-Do / notes" for m, p in [
        ("GET", "/api/calendar"), ("GET", "/api/notes"), ("POST", "/api/notes"), ("GET", "/api/notes/all"), ("GET", "/api/notes/counts"),
        ("PUT", "/api/notes/{note_id}"), ("DELETE", "/api/notes/{note_id}"), ("POST", "/api/notes/{note_id}/done"),
        ("GET", "/api/todo/counts"), ("GET", "/api/todo/items"), ("POST", "/api/todo/items"), ("PUT", "/api/todo/items/{item_id}"),
        ("DELETE", "/api/todo/items/{item_id}"), ("POST", "/api/todo/items/{item_id}/toggle"), ("GET", "/api/todo/lists"),
        ("POST", "/api/todo/lists"), ("PUT", "/api/todo/lists/{list_id}"), ("DELETE", "/api/todo/lists/{list_id}")]},
    # files: drivers upload proof of delivery; money document types are refused inside without "See prices"
    **{(m, p): "attachments (money types checked inside)" for m, p in [
        ("GET", "/api/attachments/"), ("POST", "/api/attachments/"), ("GET", "/api/attachments/counts"),
        ("PUT", "/api/attachments/{attachment_id}"), ("DELETE", "/api/attachments/{attachment_id}"),
        ("GET", "/api/attachments/{attachment_id}/file"), ("GET", "/api/attachments/{attachment_id}/thumb"),
        ("GET", "/api/attachments/{attachment_id}/preview")]},
    ("GET", "/api/email/health"): "a signed-in user's own top-bar check; returns nothing without the Company Settings permission",
    ("POST", "/api/ai-docs/extract"): "checks 'ai' inside (POD reading is for drivers)",
    ("POST", "/api/ai-docs/validate-po/{po_id}"): "checks 'ai' inside",
    # checked inside main.py (recycle_bin permission; history scrubbed without 'See prices')
    ("GET", "/api/recycle-bin"): "checks 'recycle_bin' inside",
    ("POST", "/api/recycle-bin/{entry_id}/restore"): "checks 'recycle_bin' inside",
    ("DELETE", "/api/recycle-bin/{entry_id}"): "checks 'recycle_bin' inside",
    ("GET", "/api/activity-log"): "checks 'recycle_bin' inside",
    ("GET", "/api/activity/{entity_type}/{entity_id}"): "record history; prices hidden without 'See prices'",
    # dashboard: each section is filtered by the user's permissions inside
    ("GET", "/api/reports/action-items"): "sections filtered by permission",
    ("GET", "/api/reports/today"): "sections filtered by permission",
    # look-ups every screen needs
    ("GET", "/api/types/"): "drop-down lists",
    ("GET", "/api/types/check"): "similar-name check for a new list entry",
    ("GET", "/api/roles/"): "role names for the user list",
    ("GET", "/api/roles/catalog"): "permission names",
    ("GET", "/api/templates/defaults"): "which label designs exist",
    ("GET", "/api/templates/types"): "document types",
    ("POST", "/api/templates/render-labels"): "printing labels (shipping floor)",
}


def _gates(dep, out):
    q = getattr(dep.call, "__qualname__", "") or ""
    if q.startswith(("require_perm.", "require_any.", "require_role.")):
        out.add(q.split(".")[0])
    for d in dep.dependencies:
        _gates(d, out)
    return out


def _api_routes():
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api"):
            for m in r.methods - {"HEAD", "OPTIONS"}:
                yield m, r.path, r


def test_every_api_route_is_behind_a_permission_or_knowingly_open():
    missing = [f"{m} {p}  ({r.endpoint.__module__})" for m, p, r in _api_routes() if not _gates(r.dependant, set()) and (m, p) not in OPEN]
    assert not missing, ("New routes with no permission -- add require_perm(...) with a CATALOG permission "
                         "(money ones default to admin), or list them in OPEN with the reason:\n  " + "\n  ".join(sorted(missing)))


def test_open_list_has_no_stale_entries():
    live = {(m, p) for m, p, _ in _api_routes()}
    assert not set(OPEN) - live, f"OPEN lists routes that no longer exist: {sorted(set(OPEN) - live)}"


def test_money_permissions_default_to_admin_only():
    wrong = [k for k, _m, _l, money, lowest in P.CATALOG if money and lowest not in ("admin", "super_admin")]
    assert not wrong, f"money permissions must default to admin (or super admin only): {wrong}"
    assert not set(P.defaults_for("employee")) & P.MONEY and not set(P.defaults_for("manager")) & P.MONEY
    super_only = {k for k, _m, _l, money, lowest in P.CATALOG if money and lowest == "super_admin"}
    assert P.MONEY - super_only <= set(P.defaults_for("admin"))


def test_screens_fallback_matches_the_catalog():
    js = Path(__file__).resolve().parents[1].joinpath("frontend", "auth-guard.js").read_text(encoding="utf-8")
    block = re.search(r"PERM_DEFAULT: \{(.*?)\}", js, re.S).group(1)
    ranks = {k.strip('"'): int(v) for k, v in re.findall(r'("?[\w.]+"?):\s*(\d)', block)}
    assert set(ranks) == set(P.KEYS), f"AuthGuard.PERM_DEFAULT differs from CATALOG: missing {set(P.KEYS) - set(ranks)}, extra {set(ranks) - set(P.KEYS)}"
    low = [k for k in P.MONEY if ranks[k] < 3]
    assert not low, f"money permissions shown to roles below admin in auth-guard.js: {low}"


def test_history_hides_prices_from_anyone_without_see_prices(client, admin_headers, make, api):
    a = make.item(price=7)
    o = make.order(lines=[(a, 5, 7.25)])
    api.put(f"/api/customer-orders/{o['id']}/lines/{o['lines'][0]['id']}", json={"unit_price": 7.5})
    perms = [p for p in P.defaults_for("admin") if p not in P.MONEY]   # a manager-like role with no money at all
    client.post("/api/roles/", json={"name": "Lead No Money", "permissions": perms}, headers=admin_headers)
    h = _user(client, admin_headers, "lead_no_money")
    rows = client.get(f"/api/activity/customer_order/{o['id']}", headers=h).json()
    assert rows and not any("7.5" in (r["detail"] or "") for r in rows)
    mine = client.get(f"/api/activity/customer_order/{o['id']}", headers=admin_headers).json()
    assert any("7.5" in (r["detail"] or "") for r in mine)


def test_test_data_needs_company_settings(client, admin_headers):
    h = _user(client, admin_headers, "employee")
    assert client.post("/api/test-data/ensure", headers=h).status_code == 403
