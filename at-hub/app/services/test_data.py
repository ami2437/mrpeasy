"""Keeps a set of TEST-* products with costed stock, a test customer/vendor, and one
untouched test order ready, so there's always something to click through. Runs at
startup when TEST_DATA_ENABLED=true, and on demand from Company Settings."""
from sqlalchemy.orm import Session

from app.models import Customer, Vendor, StockItem, Lot, CustomerOrder
from app.schemas import (
    PurchaseOrderCreate, PurchaseOrderLineCreate, ReceiveOrderRequest, ReceiveLineRequest,
    LandedCostInput, CustomerOrderCreate, CustomerOrderLineCreate,
)
from app.services.crud import (
    PurchaseOrderService, LandedCostService, CustomerOrderService, StockItemService, refresh_item_cost,
)

ACTOR = "test-data"

TEST_CUSTOMER = dict(
    name="TEST Customer — Acme Fabrication", contact_name="Pat Tester", email="test-customer@example.com",
    phone="555-0100", address="100 Test Street\nSpringfield, IL 62701",
)
TEST_VENDOR = dict(
    name="TEST Vendor — Fastener Supply Co", contact_name="Sam Supplier", email="test-vendor@example.com",
    phone="555-0200", address="200 Supply Ave\nChicago, IL 60601",
)
# code, title, category, cost, selling price, pack size, qty on the test order
TEST_ITEMS = [
    ("TEST-BOLT-M8", "TEST Hex Bolt M8 x 40mm, Zinc", "Test Fasteners", 0.08125, 0.21375, 100, 1200),
    ("TEST-NUT-M8", "TEST Hex Nut M8, Zinc", "Test Fasteners", 0.03410, 0.09875, 200, 1200),
    ("TEST-WASHER-M8", "TEST Flat Washer M8", "Test Fasteners", 0.01275, 0.04550, 500, 2400),
    ("TEST-BRKT-L50", "TEST Steel L-Bracket 50mm", "Test Hardware", 1.42500, 3.29990, 25, 60),
]
STOCK_MULTIPLE = 4  # keep enough stock on hand for this many test orders
TEST_FREIGHT = 150.0


def _get_or_create_party(db: Session, model, fields: dict):
    party = db.query(model).filter(model.name == fields["name"]).first()
    if not party:
        party = model(**fields)
        db.add(party)
        db.commit()
        db.refresh(party)
    return party


def _is_untouched_test_order(order: CustomerOrder) -> bool:
    return order.status in ("draft", "confirmed") and order.lines and all(l.allocated_quantity == 0 for l in order.lines)


def ensure_test_data(db: Session) -> dict:
    customer = _get_or_create_party(db, Customer, TEST_CUSTOMER)
    vendor = _get_or_create_party(db, Vendor, TEST_VENDOR)

    items = {}
    for code, title, category, cost, sell, pack, _ in TEST_ITEMS:
        item = db.query(StockItem).filter(StockItem.code == code).first()
        if not item:
            item = StockItem(code=code, title=title, unit="ea", category=category, cost_price=cost,
                             selling_price=sell, reorder_point=0, default_pack_size=pack)
            db.add(item)
            db.commit()
            db.refresh(item)
        items[code] = item

    # First run: stock arrives the real way -- a received PO plus a freight bill spread by
    # quantity -- so lots, landed cost, and profit all have something genuine to show.
    if not db.query(Lot).filter(Lot.item_id.in_([i.id for i in items.values()])).count():
        po = PurchaseOrderService.create(db, PurchaseOrderCreate(
            vendor_id=vendor.id, notes="Auto-created test stock",
            lines=[PurchaseOrderLineCreate(item_id=items[code].id, quantity=order_qty * STOCK_MULTIPLE, unit_cost=cost)
                   for code, _, _, cost, _, _, order_qty in TEST_ITEMS],
        ), created_by=ACTOR)
        PurchaseOrderService.mark_ordered(db, po.id)
        PurchaseOrderService.receive(db, po.id, ReceiveOrderRequest(
            lines=[ReceiveLineRequest(line_id=l.id, quantity=l.quantity) for l in po.lines],
        ), created_by=ACTOR)
        LandedCostService.create(db, LandedCostInput(
            description="TEST inbound freight", cost_type="freight", amount=TEST_FREIGHT,
            paid_to="TEST Freight Lines", reference="TEST-BOL-1", po_ids=[po.id],
        ), created_by=ACTOR)

    # Later runs: top stock back up (as a costed adjustment lot) once testing has used it.
    topped_up = []
    for code, _, _, cost, _, _, order_qty in TEST_ITEMS:
        item = items[code]
        db.refresh(item)
        if item.available < order_qty * 2:
            StockItemService._adjust(db, item, order_qty * STOCK_MULTIPLE - item.available, cost,
                                     note="Test data top-up", created_by=ACTOR)
            refresh_item_cost(db, item)
            db.commit()
            topped_up.append(code)

    order = next((o for o in db.query(CustomerOrder).filter(CustomerOrder.customer_id == customer.id)
                  .order_by(CustomerOrder.id.desc()).all() if _is_untouched_test_order(o)), None)
    created = order is None
    if created:
        order = CustomerOrderService.create(db, CustomerOrderCreate(
            customer_id=customer.id, po_number="TEST-PO", job_number="TEST-JOB", allow_duplicate=True,
            notes="Auto-created test order -- safe to ship, invoice, or cancel. A fresh one is created when this one is used.",
            lines=[CustomerOrderLineCreate(item_id=items[code].id, quantity=order_qty, unit_price=sell)
                   for code, _, _, _, sell, _, order_qty in TEST_ITEMS],
        ), created_by=ACTOR)
        CustomerOrderService.confirm(db, order.id)

    return {
        "order_id": order.id, "order_code": order.code, "order_created": created,
        "items": [code for code, *_ in TEST_ITEMS], "topped_up": topped_up,
    }


# ---- generic stock test: bulk generic 9/16 nuts (no real item is 9/16, so this never shows on real orders) ----
GENERIC_TEST = [  # code, title, generic?, cost, selling price, own stock
    ("TEST-GEN-916-NUT", "TEST 9/16-12 A194 2H HVY HEX NUT HDG -- GENERIC BULK", True, 0.1125, 0.0, 0),
    ("TEST-91612-NUT", "TEST 9/16-12 A194 2H HVY HEX NUT HDG", False, 0.20, 0.0, 400),
    ("TEST-91613-NUT", "TEST 9/16-12 A194-2H HEX NUT", False, 0.20, 0.0, 0),
    ("TEST-91612", "TEST BOLT_HH_9/16-12x3_A325_TYPE1_HDG_w/A194-2H HEX NUT", False, 0.85, 1.95, 0),
]


def ensure_generic_test_data(db: Session) -> dict:
    """A received PO of generic nuts (with freight), two specific nuts that draw from it, a bolt, and an
    untouched draft order whose nut lines are short -- so the Book column offers the generic draw."""
    customer = _get_or_create_party(db, Customer, TEST_CUSTOMER)
    vendor = _get_or_create_party(db, Vendor, TEST_VENDOR)
    items = {}
    for code, title, generic, cost, sell, own in GENERIC_TEST:
        item = db.query(StockItem).filter(StockItem.code == code).first()
        if not item:
            item = StockItem(code=code, title=title, unit="ea", category="Nut" if code.endswith("NUT") else "Bolt",
                             cost_price=cost, selling_price=sell, reorder_point=0, is_generic=generic)
            db.add(item)
            db.commit()
            db.refresh(item)
            if own:
                StockItemService._adjust(db, item, own, cost, note="Test data: own stock", created_by=ACTOR)
                refresh_item_cost(db, item)
                db.commit()
        items[code] = item
    gen, bolt = items["TEST-GEN-916-NUT"], items["TEST-91612"]
    po_code = None
    if not db.query(Lot).filter(Lot.item_id == gen.id).count():
        po = PurchaseOrderService.create(db, PurchaseOrderCreate(
            vendor_id=vendor.id, notes="TEST generic nuts bought in bulk",
            lines=[PurchaseOrderLineCreate(item_id=gen.id, quantity=20000, unit_cost=0.1125),
                   PurchaseOrderLineCreate(item_id=bolt.id, quantity=3000, unit_cost=0.85)],
        ), created_by=ACTOR)
        PurchaseOrderService.mark_ordered(db, po.id)
        PurchaseOrderService.receive(db, po.id, ReceiveOrderRequest(
            lines=[ReceiveLineRequest(line_id=l.id, quantity=l.quantity) for l in po.lines]), created_by=ACTOR)
        LandedCostService.create(db, LandedCostInput(
            description="TEST freight on generic nuts", cost_type="freight", amount=90.0,
            paid_to="TEST Freight Lines", reference="TEST-BOL-GEN", po_ids=[po.id]), created_by=ACTOR)
        po_code = po.code
    order = next((o for o in db.query(CustomerOrder).filter(CustomerOrder.customer_id == customer.id, CustomerOrder.po_number == "TEST-GENERIC")
                  .order_by(CustomerOrder.id.desc()).all() if _is_untouched_test_order(o)), None)
    created = order is None
    if created:
        order = CustomerOrderService.create(db, CustomerOrderCreate(
            customer_id=customer.id, po_number="TEST-GENERIC", job_number="TEST-JOB-GEN", allow_duplicate=True,
            notes="Generic nut test: the nut lines are short -- Create Shipment offers to draw them from TEST-GEN-916-NUT.",
            lines=[CustomerOrderLineCreate(item_id=bolt.id, quantity=2500, unit_price=1.95),
                   CustomerOrderLineCreate(item_id=items["TEST-91612-NUT"].id, quantity=2500, unit_price=0),
                   CustomerOrderLineCreate(item_id=items["TEST-91613-NUT"].id, quantity=600, unit_price=0)],
        ), created_by=ACTOR)  # left as a draft: confirm it on the order screen
    return {"order_id": order.id, "order_code": order.code, "order_created": created, "po_code": po_code,
            "items": [code for code, *_ in GENERIC_TEST]}


# ---- pack-size test orders: real items (with packing history), two TEST customers, a booked shipment each ----
PACK_TEST_CUSTOMERS = [
    dict(name="TEST Pack — Tulsa Yard", contact_name="Pat Tester", email="test-pack-tulsa@example.com",
         address="1 Test Yard Rd\nTulsa, OK 74107"),
    dict(name="TEST Pack — Beasley Plant", contact_name="Sam Tester", email="test-pack-beasley@example.com",
         address="2 Test Plant Way\nBeasley, TX 77417"),
]
PACK_TEST_PREFIX = "TEST-PK-"


def make_pack_test_orders(db: Session, n_orders: int = 10, lines_per_order: int = 15, seed: int = 7) -> dict:
    """n confirmed TEST orders of ~lines_per_order real items each (bolts with their nuts), alternating between two TEST
    customers, each with a shipment booking every line -- they land in Bulk Operations -> To pick. Quantities are
    mixes of full boxes and part boxes at each item's known pack size, so the box splits are worth checking.
    Safe to run again: numbering carries on (TEST-PK-11...)."""
    import random
    from datetime import datetime, timedelta
    from app.models import ShipmentBox
    from app.schemas import BookLineRequest, CreateShipmentRequest
    from app.services.crud import ShipmentService
    from app.services.pack_sizes import usage

    rnd = random.Random(seed + db.query(CustomerOrder).filter(CustomerOrder.po_number.like(f"{PACK_TEST_PREFIX}%")).count())
    custs = [_get_or_create_party(db, Customer, dict(c, shipping_address=c["address"])) for c in PACK_TEST_CUSTOMERS]
    packed = {i for i, in db.query(ShipmentBox.item_id).distinct()}
    items = [i for i in db.query(StockItem).order_by(StockItem.code).all()
             if not i.code.upper().startswith("TEST") and not getattr(i, "is_generic", False) and (i.available or 0) >= 300
             and (i.id in packed or i.default_pack_size)]
    by_code = {i.code: i for i in items}
    uses = usage(db, [i.id for i in items], limit=1)
    known = lambda i: (uses.get(i.id) or [{}])[0].get("pack_size") or i.default_pack_size or 100
    free = {i.id: i.available for i in items}
    # bolts travel with their nut (15420 + 15420-NUT), like the real orders
    groups = []
    for i in items:
        if i.code.upper().endswith(("-NUT", "-NUTS")):
            continue
        nut = by_code.get(f"{i.code}-NUT") or by_code.get(f"{i.code}-NUTS")
        groups.append([i, nut] if nut else [i])
    made = []
    start = db.query(CustomerOrder).filter(CustomerOrder.po_number.like(f"{PACK_TEST_PREFIX}%")).count()
    for k in range(n_orders):
        no = start + k + 1
        cust = custs[k % len(custs)]
        rnd.shuffle(groups)
        lines, used = [], set()
        for g in groups:
            if len(lines) + len(g) > lines_per_order:
                continue
            base = known(g[0])
            qty = base * rnd.randint(1, 6) + rnd.choice([0, 0, rnd.randint(1, max(1, base - 1))])
            if any(free[x.id] < qty for x in g) or any(x.id in used for x in g):
                continue
            for x in g:
                lines.append(CustomerOrderLineCreate(item_id=x.id, quantity=qty, unit_price=round(x.selling_price or 0, 4)))
                free[x.id] -= qty
                used.add(x.id)
            if len(lines) >= lines_per_order:
                break
        order = CustomerOrderService.create(db, CustomerOrderCreate(
            customer_id=cust.id, po_number=f"{PACK_TEST_PREFIX}{no:02d}", job_number=f"TEST-JOB-{no:02d}", allow_duplicate=True,
            delivery_date=datetime.utcnow() + timedelta(days=7 + no % 14), ship_to_address=cust.address,
            notes="TEST order for pack-size testing -- safe to pack, ship or cancel.", lines=lines,
        ), created_by=ACTOR)
        CustomerOrderService.confirm(db, order.id)
        db.refresh(order)
        sh = CustomerOrderService.create_shipment(db, order.id, CreateShipmentRequest(
            lines=[BookLineRequest(line_id=l.id, quantity=l.quantity) for l in order.lines]), created_by=ACTOR)
        made.append({"order": order.code, "po": order.po_number, "customer": cust.name, "lines": len(order.lines), "shipment": sh.code})
    return {"orders": made}
