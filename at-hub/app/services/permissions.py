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
CATALOG = [
    # Sales
    ("customers.view", "Sales", "Customers screen (contacts, addresses, history)", False, "manager"),
    ("customers.edit", "Sales", "Add and edit customers", False, "manager"),
    ("orders.view", "Sales", "See customer orders", False, "employee"),
    ("orders.edit", "Sales", "Create, edit and confirm customer orders (prices show only with \"See prices\")", False, "manager"),
    ("quotes", "Sales", "Quotes", True, "manager"),
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
    ("money.view", "Money", "See prices, costs, totals and money documents anywhere", True, "manager"),
    ("invoices", "Money", "Invoices: create, edit, send, record payments", True, "manager"),
    ("invoices.funding", "Money", "Invoice funding (factoring)", True, "manager"),
    ("payments.import", "Money", "Import PO payment files", True, "admin"),
    # Purchasing
    ("purchasing", "Purchasing", "Purchase orders and vendor bills", True, "manager"),
    ("vendors", "Purchasing", "Vendors", False, "manager"),
    ("vendor_payments", "Purchasing", "Vendor payments", True, "manager"),
    ("landed_costs", "Purchasing", "Landed costs", True, "manager"),
    # Reports & tools
    ("reports", "Reports", "Reports and the dashboard's money sections", True, "manager"),
    ("imports", "Tools", "Import data from files (CSV)", False, "manager"),
    ("ai", "Tools", "AI Desk, AI order drafting and document scanning", False, "manager"),
    ("recycle_bin", "Tools", "Recycle bin (restore deleted records)", False, "manager"),
    # Admin
    ("company", "Admin", "Company settings and logo", False, "admin"),
    ("templates", "Admin", "Template Designer", False, "admin"),
    ("tasks", "Admin", "Task list", False, "admin"),
    ("users", "Admin", "Users and roles", False, "super_admin"),
    ("backups", "Admin", "Backups", False, "super_admin"),
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
    "driver": ("Driver", "Delivers and uploads proof of delivery. Sees shipments only -- no prices, no other screens.",
               ["shipments.view", "pod.upload"]),
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
    db.commit()


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
