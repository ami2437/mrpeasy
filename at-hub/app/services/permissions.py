"""Roles and permissions: what each role can see and do, module by module.

A permission is a key ("invoices", "shipments.deliver"). Roles are named sets of them, edited on the Users &
Roles screen. Money permissions are flagged so they stand out: without "money.view" every price, cost and total
is blanked in API responses (app/main.py), whatever screen asked.

The server checks permissions (require_perm / require_any in app/dependencies.py); the screens only use them to
hide what you can't use. The built-in roles start exactly where the old fixed ladder was, so nothing changes until
a role is edited."""
import json
from typing import Iterable, Set

from sqlalchemy.orm import Session

# (key, module, label, money?, lowest built-in role that has it by default)
# RULE: a money permission (money? = True) always defaults to "admin" -- only Admin and Super admin get it until someone
# ticks it on the Roles screen. tests/test_permission_audit.py enforces this, and fails for any new /api route that
# isn't behind a permission (or knowingly listed as open to every signed-in user).
CATALOG = [
    # Sales
    ("customers.view", "Sales", "Customers screen (contacts, addresses, history)", False, "manager"),
    ("customers.edit", "Sales", "Add and edit customers", False, "manager"),
    ("orders.view", "Sales", "See customer orders", False, "employee"),
    ("orders.edit", "Sales", "Create, edit and confirm customer orders (prices show only with \"See prices\")", False, "manager"),
    ("quotes", "Sales", "Quotes", True, "admin"),
    # Shipping
    ("shipments.view", "Shipping", "See shipments", False, "employee"),
    ("shipments.work", "Shipping", "Create, pick, pack and ship shipments; print labels and packing lists", False, "employee"),
    ("shipments.deliver", "Shipping", "Mark delivered without a proof of delivery", False, "manager"),
    ("shipments.undo", "Shipping", "Undo, delete and un-deliver shipments", False, "manager"),
    ("pod.upload", "Shipping", "Upload proof of delivery (drivers)", False, "employee"),
    # Warehouse
    ("stock.view", "Warehouse", "See stock items, lots and MTRs", False, "employee"),
    ("stock.edit", "Warehouse", "Add, edit, delete and verify items; product groups; generic stock transfers", False, "manager"),
    ("mtrs.manage", "Warehouse", "Link MTRs to purchase orders", False, "manager"),
    # Money
    ("money.view", "Money", "See prices, costs, totals and money documents anywhere", True, "admin"),
    ("invoices", "Money", "Invoices: create, edit, send, record payments", True, "admin"),
    ("invoices.split", "Money", "Split invoice lines onto a new invoice (same shipment, same order)", True, "admin"),
    ("invoices.funding", "Money", "Invoice funding (factoring)", True, "admin"),
    ("payments.import", "Money", "Import PO payment files", True, "admin"),
    # Purchasing
    ("purchasing", "Purchasing", "Purchase orders and vendor bills", True, "admin"),
    ("vendors", "Purchasing", "Vendors", False, "manager"),
    ("vendor_payments", "Purchasing", "Vendor payments", True, "admin"),
    ("landed_costs", "Purchasing", "Landed costs", True, "admin"),
    # Reports & tools
    ("reports", "Reports", "Reports and the dashboard's money sections", True, "admin"),
    ("insights", "Reports", "Quick Insights pop-up (top bar): orders shipped / pending, shipments in process / not invoiced, "
                            "invoices paid / owed, POs received / owed -- also needs \"See prices\"", True, "admin"),
    ("imports", "Tools", "Import data from files (CSV)", False, "manager"),
    ("ai", "Tools", "AI Desk, AI order drafting and document scanning", False, "manager"),
    ("recycle_bin", "Tools", "Recycle bin (restore deleted records)", False, "manager"),
    ("golive", "Tools", "Go-live cleanup: hide alerts from before go-live (MRP Migrate)", False, "admin"),
    ("simulate", "Tools", "Simulate", False, "admin"),
    # Admin
    ("company", "Admin", "Company settings and logo", False, "admin"),
    ("types.manage", "Admin", "Add and rename document types, S&H types, landed cost types and payment methods", False, "manager"),
    ("templates", "Admin", "Template Designer", False, "admin"),
    ("tasks", "Admin", "Task list", False, "admin"),
    ("users", "Admin", "Users and roles", False, "super_admin"),
    ("backups", "Admin", "Backups", False, "super_admin"),
    ("backups.download", "Admin", "Download a backup to your own computer (database + attached files) -- and be made to, "
                                  "every 3 days", True, "super_admin"),
    ("file_matcher", "Admin", "File Matcher", False, "super_admin"),
]
KEYS = [c[0] for c in CATALOG]
MONEY = {c[0] for c in CATALOG if c[3]}
RANK = {"employee": 1, "manager": 2, "admin": 3, "super_admin": 4}
BUILTIN = {
    "employee": ("Employee", "Shipping floor: orders, picking, packing, shipping, POD, stock. No money."),
    "manager": ("Manager", "Day-to-day work including pricing, invoices, purchasing, vendors and landed costs."),
    "admin": ("Admin", "Everything except users, roles and backups."),
    "super_admin": ("Super admin", "Everything. Can't be changed, so someone can always manage users."),
}
PRESETS = {  # made once, then editable like any other role
    "driver": ("Driver", "Delivers and uploads proof of delivery. Sees only the deliveries -- items, boxes and pallets; no prices, "
                         "orders, customers or other screens.", ["pod.upload"]),
}


def defaults_for(role_key: str) -> list:
    return [k for k, _m, _l, _money, lowest in CATALOG if RANK[role_key] >= RANK[lowest]]


def seed(db: Session) -> None:
    """Make the built-in roles (and presets) the first time; afterwards they're only changed on the Roles screen.
    super_admin is re-filled on every start so a new permission always reaches it."""
    from app.models import Role
    have = {r.key: r for r in db.query(Role).all()}
    for key, (name, desc) in BUILTIN.items():
        if key not in have:
            db.add(Role(key=key, name=name, description=desc, permissions=json.dumps(defaults_for(key)), builtin=True))
    if "super_admin" in have:
        have["super_admin"].permissions = json.dumps(KEYS)
    for key, (name, desc, perms) in PRESETS.items():
        if key not in have:
            db.add(Role(key=key, name=name, description=desc, permissions=json.dumps(perms), builtin=False))
    _grant_new(db, have)
    _driver_v2(db, have)
    _money_admin_only(db, have)
    db.commit()


def _driver_v2(db: Session, have: dict) -> None:
    """2026-10-06: the Driver preset went from shipments + POD to POD only. A driver role still on the old default
    moves with it (one that was edited on the Roles screen is left alone)."""
    from app.models import AppSetting
    if db.get(AppSetting, "driver_v2"):
        return
    role = have.get("driver")
    if role and sorted(json.loads(role.permissions or "[]")) == ["pod.upload", "shipments.view"]:
        role.permissions = json.dumps(["pod.upload"])
        role.description = PRESETS["driver"][1]
    db.add(AppSetting(key="driver_v2", value="true"))


def _money_admin_only(db: Session, have: dict) -> None:
    """2026-10-08: money permissions default to Admin only. "insights" reached the Manager role by default the day it
    was added -- take that back once (if someone ticks it again on the Roles screen, it stays)."""
    from app.models import AppSetting
    if db.get(AppSetting, "money_admin_only"):
        return
    role = have.get("manager")
    if role:
        perms = json.loads(role.permissions or "[]")
        if "insights" in perms:
            role.permissions = json.dumps([p for p in perms if p != "insights"])
    db.add(AppSetting(key="money_admin_only", value="true"))


# Permissions added after roles were first made (tracked from 2026-10-06; earlier ones were all in place already).
_BEFORE_TRACKING_NEW = {"golive", "simulate"}


def _grant_new(db: Session, have: dict) -> None:
    """A permission new to this database goes once to the built-in roles that have it by default -- after that the
    Roles screen decides (taking it away sticks)."""
    from app.models import AppSetting
    row = db.get(AppSetting, "permissions_seen")
    seen = set(json.loads(row.value)) if row and row.value else set(KEYS) - _BEFORE_TRACKING_NEW
    for perm in [k for k in KEYS if k not in seen]:
        for key in BUILTIN:
            role = have.get(key)
            if role and key != "super_admin" and perm in defaults_for(key):
                perms = json.loads(role.permissions or "[]")
                if perm not in perms:
                    role.permissions = json.dumps(perms + [perm])
    if row is None:
        db.add(AppSetting(key="permissions_seen", value=json.dumps(KEYS)))
    else:
        row.value = json.dumps(KEYS)


def perms_for(db: Session, role_key: str) -> Set[str]:
    if role_key == "super_admin":
        return set(KEYS)
    from app.models import Role
    r = db.get(Role, role_key)
    return set(json.loads(r.permissions or "[]")) & set(KEYS) if r else set()


def has(user, perm: str) -> bool:
    return perm in getattr(user, "permissions", set())


def has_any(user, perms: Iterable[str]) -> bool:
    mine = getattr(user, "permissions", set())
    return any(p in mine for p in perms)


def role_name(db: Session, role_key: str) -> str:
    from app.models import Role
    r = db.get(Role, role_key)
    return r.name if r else role_key
