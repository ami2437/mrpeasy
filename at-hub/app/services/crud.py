import re
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import func
from app.services import filenames
from app.services import clock
from app.services.money import cents

from app.models import (
    StockItem, Lot, InventoryTransaction, Customer, Vendor,
    CustomerOrder, CustomerOrderLine, PurchaseOrder, PurchaseOrderLine, PurchaseOrderPayment,
    Shipment, ShipmentLine, ShipmentBox, PalletWeight, Invoice, InvoiceLine, InvoicePayment,
    LandedCost, LandedCostAllocation, CompanyProfile, ProductGroup, VendorItem, VendorBill,
    PackSizeHistory, FundingImport, InvoiceShipment, PurchaseOrderCharge, VendorPayment, NumberSeries,
)


def not_for_sale(item: StockItem) -> None:
    """Generic bulk stock (58-NUT) is never sold directly: the specific item is, and draws from it when booked."""
    if item is not None and item.is_generic:
        raise HTTPException(status_code=400, detail=f"{item.code} is generic bulk stock and isn't sold directly -- put the specific item "
                                                    f"on the order (e.g. 15420-NUT); it draws from {item.code} when the shipment is booked")


def set_fields(obj, updates: dict) -> None:
    """Apply an edit's fields. A blank (None) sent for a required column or a yes/no flag is ignored rather than
    crashing the save (or leaving a flag that is neither)."""
    from sqlalchemy import Boolean
    columns = obj.__table__.columns
    for key, value in updates.items():
        column = columns.get(key)
        if value is None and column is not None and (not column.nullable or isinstance(column.type, Boolean)):
            continue
        setattr(obj, key, value)


def get_company_profile(db: Session) -> CompanyProfile:
    """Single-row company profile (id=1), created with defaults on first use."""
    profile = db.query(CompanyProfile).filter(CompanyProfile.id == 1).first()
    if not profile:
        profile = CompanyProfile(id=1, name="American Traders LLC - ATind Supplies", email="sales@atindsupplies.com")
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def _next_in_series(existing, prefix: str, width: int) -> str:
    """One past the highest number in use for prefix+digits. (Counting rows would hand out a
    code that already exists once any record has been deleted.)"""
    pat = re.compile(re.escape(prefix) + r"(\d{%d})(?!\d)" % width)  # SH215741-M219-30D counts as 215741
    n = max((int(m.group(1)) for c in existing if c and (m := pat.match(c))), default=0)
    return f"{prefix}{n + 1:0{width}d}"


def generate_code(db: Session, model, prefix: str) -> str:
    """Sequential codes. A NumberSeries row (set by the MRPeasy import) continues that
    numbering, e.g. C89124; otherwise AT-HUB's own CO-0001 style."""
    series = db.query(NumberSeries).filter(NumberSeries.key == prefix).first()
    binned = _binned_codes(db, model.__tablename__)  # a deleted record's number is never handed out again
    if series:
        codes = db.query(model.code).filter(model.code.like(f"{series.prefix}%")).all()
        return _next_in_series([c for (c,) in codes] + [c for c in binned if c.startswith(series.prefix)], series.prefix, series.width)
    codes = db.query(model.code).filter(model.code.like(f"{prefix}-%")).all()
    return _next_in_series([c for (c,) in codes] + [c for c in binned if c.startswith(f"{prefix}-")], f"{prefix}-", 4)


def _new_code(db: Session, model, record, code: str, what: str) -> str:
    """A changed record # (shipment / invoice): letters, digits and - _ . only, not used by another record (or one in the
    recycle bin)."""
    code = (code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail=f"Enter the new {what} #")
    if len(code) > 40 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", code):
        raise HTTPException(status_code=400, detail=f"A {what} # can have letters, digits and - _ . only (no spaces), up to 40 characters")
    if code == record.code:
        return code
    taken = db.query(model).filter(func.lower(model.code) == code.lower(), model.id != record.id).first()
    if taken or code.lower() in (c.lower() for c in _binned_codes(db, model.__tablename__)):
        raise HTTPException(status_code=400, detail=f"{code} is already used by another {what}")
    return code


def _binned_codes(db: Session, table: str) -> List[str]:
    """Codes of records of this table sitting in the recycle bin."""
    import json as _json
    from app.models import DeletedRecord
    out = []
    for (rows,) in db.query(DeletedRecord.rows).filter(DeletedRecord.restored_at.is_(None), DeletedRecord.rows.like(f'%"table": "{table}"%')).all():
        out += [r["cols"].get("code") for r in _json.loads(rows) if r["table"] == table and r["cols"].get("code")]
    return out


# ---- Lot numbering and costing ----
def next_lot_code(db: Session) -> str:
    """Sequential LOT-00001 numbers, unique across receipts and adjustments. Callers that
    create several lots in one transaction must flush between them so this sees the last one."""
    series = db.query(NumberSeries).filter(NumberSeries.key == "LOT").first()
    prefix, width = (series.prefix, series.width) if series else ("LOT-", 5)
    codes = [c for (c,) in db.query(Lot.lot_code).filter(Lot.lot_code.like(f"{prefix}%")).all()]
    # lot #s already promised to open PO lines are taken too
    codes += [c for (c,) in db.query(PurchaseOrderLine.planned_lot_code).filter(PurchaseOrderLine.planned_lot_code.like(f"{prefix}%")).all()]
    return _next_in_series(codes, prefix, width)


def _planned_lot(db: Session, line) -> Optional[str]:
    """The lot # assigned to this PO line before it arrived -- used for its first receipt only."""
    code = (line.planned_lot_code or "").strip()
    if code and not db.query(Lot).filter(Lot.lot_code == code).first():
        return code
    return None


def line_landed_per_unit(db: Session, po_line_id: int) -> float:
    """Sum of every landed cost's per-unit share on this PO line."""
    allocs = db.query(LandedCostAllocation).filter(LandedCostAllocation.po_line_id == po_line_id).all()
    return sum(a.per_unit for a in allocs)


def refresh_item_cost(db: Session, item: StockItem) -> None:
    """cost_price = weighted-average landed cost of the stock still sitting in lots, so
    inventory valuation and profit estimates follow real lot costs. Left alone when no
    costed stock remains (keeps the last known cost)."""
    lots = db.query(Lot).filter(
        Lot.item_id == item.id, Lot.quantity > 1e-9, Lot.unit_cost.isnot(None), Lot.status != "rejected",
    ).all()
    qty = sum(l.quantity for l in lots)
    if qty > 1e-9:
        item.cost_price = round(sum(l.quantity * l.unit_cost for l in lots) / qty, 5)


def recompute_lot_costs(db: Session, po_line_ids) -> None:
    """Re-derive unit_cost = base_unit_cost + landed share for every lot received on these
    PO lines -- run after landed costs change or a line's unit cost is corrected. Lots
    already shipped are updated too, so order profit picks up freight billed after the fact."""
    db.flush()
    item_ids = set()
    for line_id in set(po_line_ids):
        line = db.query(PurchaseOrderLine).filter(PurchaseOrderLine.id == line_id).first()
        if not line:
            continue
        extra = line_landed_per_unit(db, line_id)
        for lot in db.query(Lot).filter(Lot.po_line_id == line_id).all():
            if lot.base_unit_cost is None:
                lot.base_unit_cost = line.unit_cost
            lot.unit_cost = round(lot.base_unit_cost + extra, 6)
            item_ids.add(lot.item_id)
    db.flush()
    for item in db.query(StockItem).filter(StockItem.id.in_(item_ids)).all():
        refresh_item_cost(db, item)


def backfill_line_identity(db: Session) -> None:
    """One-off upgrades so every order line has a line number, and boxes / invoice lines
    created before per-line tracking point at the order line they belong to."""
    for order in db.query(CustomerOrder).all():
        next_no = max((l.line_no or 0 for l in order.lines), default=0)
        for line in sorted(order.lines, key=lambda l: l.id):
            if line.line_no is None:
                next_no += 1
                line.line_no = next_no
    for box in db.query(ShipmentBox).filter(ShipmentBox.order_line_id.is_(None)).all():
        line_ids = {sl.order_line_id for sl in box.shipment.lines if sl.item_id == box.item_id}
        if len(line_ids) == 1:
            box.order_line_id = line_ids.pop()
    for il in db.query(InvoiceLine).filter(InvoiceLine.order_line_id.is_(None), InvoiceLine.item_id.isnot(None)).all():
        shipment = db.query(Shipment).filter(Shipment.id == il.invoice.shipment_id).first() if il.invoice.shipment_id else None
        if shipment:
            line_ids = {sl.order_line_id for sl in shipment.lines if sl.item_id == il.item_id}
            if len(line_ids) == 1:
                il.order_line_id = line_ids.pop()
    # Invoices from before combined invoicing: link their single shipment.
    linked = {i for (i,) in db.query(InvoiceShipment.invoice_id).all()}
    for inv in db.query(Invoice).filter(Invoice.shipment_id.isnot(None)).all():
        if inv.id not in linked:
            db.add(InvoiceShipment(invoice_id=inv.id, shipment_id=inv.shipment_id))
            for line in inv.lines:
                if line.item_id and line.shipment_id is None:
                    line.shipment_id = inv.shipment_id
    db.commit()


def backfill_lot_costing(db: Session) -> None:
    """One-off upgrades for lots created before lot costing existed: split unit_cost into
    base + landed, record the starting quantity, and link purchase lots to their PO line."""
    for lot in db.query(Lot).all():
        if lot.base_unit_cost is None and lot.unit_cost is not None:
            lot.base_unit_cost = lot.unit_cost
        if lot.initial_quantity is None:
            received = db.query(func.sum(InventoryTransaction.quantity_delta)).filter(
                InventoryTransaction.lot_id == lot.id, InventoryTransaction.quantity_delta > 0,
            ).scalar()
            lot.initial_quantity = received or lot.quantity
        if lot.po_line_id is None and lot.source == "purchase" and lot.source_reference:
            lines = db.query(PurchaseOrderLine).join(PurchaseOrder).filter(
                PurchaseOrder.code == lot.source_reference, PurchaseOrderLine.item_id == lot.item_id,
            ).all()
            if len(lines) == 1:
                lot.po_line_id = lines[0].id
    db.commit()


# ---- Stock Items ----
DEFAULT_PRODUCT_GROUPS = ["Anchor", "Bolt", "Nut", "Pin", "Rivet", "Screw", "Stud", "Washer", "Misc"]


def _group_key(name: str) -> str:
    """'NUT', 'Nut', ' nuts ' -> 'nut': names that differ only in capitals, spaces or a plural s are one group."""
    k = " ".join((name or "").lower().split())
    return k[:-1] if len(k) > 3 and k.endswith("s") and not k.endswith("ss") else k


class ProductGroupService:
    """A fixed list of groups to pick from. Nothing creates a group on the fly: items must use one of these,
    and a new one ('+ Add Group') is refused if it only differs from an existing one in capitals or a plural."""

    @staticmethod
    def ensure_defaults(db: Session) -> None:
        """At start-up: merge groups that are really the same ('NUT' into 'Nut', 'Studs' into 'Stud' -- the one
        holding the items wins), then add any predefined group that has no match yet."""
        ProductGroupService.merge_duplicates(db)
        counts = dict(db.query(StockItem.category, func.count(StockItem.id)).group_by(StockItem.category).all())
        spelling = {_group_key(n): n for n in DEFAULT_PRODUCT_GROUPS}
        for g in db.query(ProductGroup).all():  # empty predefined groups seeded in capitals before: "PIN" -> "Pin"
            if g.name.isupper() and not counts.get(g.name) and _group_key(g.name) in spelling:
                g.name = spelling[_group_key(g.name)]
        keys = {_group_key(g.name) for g in db.query(ProductGroup).all()}
        # Groups are never created automatically -- only a manager adds one (Stock Items -> groups).
        # an item whose group isn't on the list (typed in long ago) gets its group added, under the existing spelling if any
        for (c,) in db.query(StockItem.category).distinct().all():
            if c and _group_key(c) not in keys:
                db.add(ProductGroup(name=c))
                keys.add(_group_key(c))
        db.commit()
        ProductGroupService.merge_duplicates(db)

    @staticmethod
    def merge_duplicates(db: Session) -> list:
        counts = dict(db.query(StockItem.category, func.count(StockItem.id)).group_by(StockItem.category).all())
        by_key = {}
        for g in db.query(ProductGroup).order_by(ProductGroup.id).all():
            by_key.setdefault(_group_key(g.name), []).append(g)
        merged = []
        for same in by_key.values():
            if len(same) < 2:
                continue
            # keep the one with the most items; on a tie the mixed-case (MRPeasy) spelling, then the oldest
            keep = max(same, key=lambda g: (counts.get(g.name, 0), g.name != g.name.upper(), -g.id))
            for g in same:
                if g is not keep:
                    merged.append((g.name, keep.name, ProductGroupService._move_items(db, g.name, keep.name)))
                    db.delete(g)
        # items spelled differently from their group ('BOLT' when the group is 'Bolt')
        names = {_group_key(g.name): g.name for g in db.query(ProductGroup).all()}
        for (c,) in db.query(StockItem.category).distinct().all():
            if c and names.get(_group_key(c)) and names[_group_key(c)] != c:
                ProductGroupService._move_items(db, c, names[_group_key(c)])
        db.commit()
        return merged

    @staticmethod
    def _move_items(db: Session, from_name: str, to_name: str) -> int:
        return db.query(StockItem).filter(StockItem.category == from_name).update({StockItem.category: to_name}, synchronize_session=False)

    @staticmethod
    def list(db: Session) -> list:
        counts = dict(db.query(StockItem.category, func.count(StockItem.id)).group_by(StockItem.category).all())
        return [{"id": g.id, "name": g.name, "item_count": counts.get(g.name, 0), "non_stock": bool(g.non_stock)}
                for g in sorted(db.query(ProductGroup).all(), key=lambda g: g.name.lower())]

    NON_STOCK_DEFAULTS = ("do not sell", "service", "services", "monthly recurring fixed charges")

    @staticmethod
    def seed_non_stock(db: Session) -> None:
        """Once per database: the groups that are services / not sold start as non-stock (Product Groups can change it)."""
        from app.models import AppSetting
        if db.get(AppSetting, "non_stock_groups_v1"):
            return
        for g in db.query(ProductGroup).all():
            if g.name.strip().lower() in ProductGroupService.NON_STOCK_DEFAULTS:
                g.non_stock = True
        db.add(AppSetting(key="non_stock_groups_v1", value="true"))
        db.commit()

    @staticmethod
    def set_non_stock(db: Session, group_id: int, on: bool) -> dict:
        g = db.get(ProductGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="Group not found")
        g.non_stock = bool(on)
        db.commit()
        return next(x for x in ProductGroupService.list(db) if x["id"] == group_id)

    @staticmethod
    def canonical(db: Session, name: Optional[str]) -> Optional[str]:
        """The group's name as it's on the list ('BOLT' -> 'Bolt'), or None when there's no such group."""
        key = _group_key(name or "")
        return next((g.name for g in db.query(ProductGroup).all() if _group_key(g.name) == key), None) if key else None

    @staticmethod
    def create(db: Session, name: str) -> ProductGroup:
        name = " ".join((name or "").split())
        if not name:
            raise HTTPException(status_code=400, detail="Group name is required")
        same = ProductGroupService.canonical(db, name)
        if same:
            raise HTTPException(status_code=400, detail=f"That's the existing group '{same}' -- use it instead")
        group = ProductGroup(name=name)
        db.add(group)
        db.commit()
        db.refresh(group)
        return group

    @staticmethod
    def merge(db: Session, group_id: int, into_id: int) -> dict:
        """Move every item of one group into another and remove the first."""
        group = db.query(ProductGroup).filter(ProductGroup.id == group_id).first()
        into = db.query(ProductGroup).filter(ProductGroup.id == into_id).first()
        if not group or not into:
            raise HTTPException(status_code=404, detail="Group not found")
        if group.id == into.id:
            raise HTTPException(status_code=400, detail="Pick a different group to merge into")
        moved = ProductGroupService._move_items(db, group.name, into.name)
        db.delete(group)
        db.commit()
        return {"moved": moved, "into": into.name}

    @staticmethod
    def delete(db: Session, group_id: int) -> None:
        group = db.query(ProductGroup).filter(ProductGroup.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        in_use = db.query(StockItem).filter(StockItem.category == group.name).count()
        if in_use:
            raise HTTPException(status_code=400, detail=f"{in_use} item(s) are in {group.name} -- merge it into another group instead")
        db.delete(group)
        db.commit()

    @staticmethod
    def require(db: Session, name: Optional[str]) -> str:
        """The group an item may use: one on the list, returned in its listed spelling. Never creates one."""
        if not name:
            raise HTTPException(status_code=400, detail="Pick a product group for the item")
        found = ProductGroupService.canonical(db, name)
        if not found:
            raise HTTPException(status_code=400, detail=f"'{name}' isn't one of the product groups -- pick one from the list")
        return found


def price_history(db: Session, item_id: int) -> list:
    """Every price this item was sold at (customer order lines) and bought at (PO lines), newest first."""
    rows = []
    sales = (db.query(CustomerOrderLine, CustomerOrder, Customer)
             .join(CustomerOrder, CustomerOrder.id == CustomerOrderLine.order_id)
             .join(Customer, Customer.id == CustomerOrder.customer_id)
             .filter(CustomerOrderLine.item_id == item_id).all())
    for line, order, customer in sales:
        rows.append({"kind": "sale", "date": order.order_date, "doc_id": order.id, "doc_code": order.code,
                     "party": customer.name, "status": order.status, "quantity": line.quantity, "unit_price": line.unit_price})
    purchases = (db.query(PurchaseOrderLine, PurchaseOrder, Vendor)
                 .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
                 .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
                 .filter(PurchaseOrderLine.item_id == item_id).all())
    for line, po, vendor in purchases:
        rows.append({"kind": "purchase", "date": po.order_date, "doc_id": po.id, "doc_code": po.code,
                     "party": vendor.name, "status": po.status, "quantity": line.quantity, "unit_price": line.unit_cost})
    rows.sort(key=lambda r: (r["date"] or datetime.min, r["doc_id"]), reverse=True)
    return rows


class StockItemService:
    @staticmethod
    def list(db: Session, q: Optional[str] = None, low_stock_only: bool = False) -> List[StockItem]:
        query = db.query(StockItem)
        if q:
            query = query.filter(
                (StockItem.code.ilike(f"%{q}%")) | (StockItem.title.ilike(f"%{q}%"))
                | (StockItem.barcode.ilike(f"%{q}%")) | (StockItem.category.ilike(f"%{q}%"))
            )
        if low_stock_only:
            # Compare against available (on_hand - booked), not raw on_hand -- stock already
            # promised to a confirmed order isn't free for a reorder decision to ignore.
            query = query.filter((StockItem.on_hand - StockItem.booked) <= StockItem.reorder_point)
        return query.order_by(StockItem.code).all()

    @staticmethod
    def get(db: Session, item_id: int) -> StockItem:
        item = db.query(StockItem).filter(StockItem.id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Stock item not found")
        return item

    @staticmethod
    def create(db: Session, data) -> StockItem:
        from app.services.item_naming import normalize_code
        if not data.keep_code:
            data.code = normalize_code(data.code)
        if db.query(StockItem).filter(StockItem.code == data.code).first():
            raise HTTPException(status_code=400, detail=f"Item code '{data.code}' already exists")
        fields = data.dict(exclude={"keep_code"})
        fields["category"] = ProductGroupService.require(db, data.category)
        item = StockItem(**fields)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    @staticmethod
    def delete(db: Session, item_id: int) -> None:
        """Only an item that was never used. One on any order, shipment, invoice, lot or movement is archived
        instead (is_active = False): it stays on those records but can't be picked for new ones."""
        item = StockItemService.get(db, item_id)
        used = _item_references(db, item.id)
        if used:
            raise HTTPException(status_code=409, detail="USED|" + _usage_text(used))
        for row in (db.query(PackSizeHistory).filter(PackSizeHistory.item_id == item.id).all()
                    + db.query(VendorItem).filter(VendorItem.item_id == item.id).all()):
            db.delete(row)  # one by one, so the recycle bin keeps them with the item
        db.delete(item)
        db.commit()

    @staticmethod
    def verify(db: Session, item_id: int, username: str) -> StockItem:
        """A person has checked an item that was created from a scanned PO: it can be picked like any other now."""
        item = StockItemService.get(db, item_id)
        item.verified_by = f"{username}, {datetime.utcnow():%Y-%m-%d %H:%M} UTC"
        db.commit()
        db.refresh(item)
        return item

    @staticmethod
    def update(db: Session, item_id: int, data, created_by: str = None) -> StockItem:
        item = StockItemService.get(db, item_id)
        updates = data.dict(exclude_unset=True)
        if "category" in updates:
            updates["category"] = ProductGroupService.require(db, updates["category"])

        new_on_hand = updates.pop("on_hand", None)
        if new_on_hand is not None and new_on_hand < 0:
            raise HTTPException(status_code=400, detail="On hand can't be below 0")
        adj_cost = updates.pop("adjustment_unit_cost", None)
        adj_lot_code = (updates.pop("adjustment_lot_code", None) or "").strip()
        adj_note = (updates.pop("adjustment_note", None) or "").strip()
        if "default_pack_size" in updates:
            StockItemService.set_pack_size(db, item, updates.pop("default_pack_size"), created_by, "item edit")
        if "parent_item_id" in updates:
            from app.services.stock_transfer import check_parent
            updates["parent_item_id"] = check_parent(db, item, updates["parent_item_id"])
        if updates.get("is_generic") is False and db.query(StockItem).filter(StockItem.parent_item_id == item.id).first():
            raise HTTPException(status_code=400, detail=f"Items draw stock from {item.code} -- unlink them first (their item pages)")
        if updates.get("is_generic") and (updates.get("parent_item_id") or (item.parent_item_id and "parent_item_id" not in updates)):
            raise HTTPException(status_code=400, detail="A generic item can't itself draw from another item")

        set_fields(item, updates)

        if new_on_hand is not None and abs(new_on_hand - item.on_hand) > 1e-9:
            StockItemService._adjust(db, item, new_on_hand - item.on_hand, adj_cost, adj_lot_code, adj_note, created_by)
            refresh_item_cost(db, item)
        db.commit()
        db.refresh(item)
        return item

    @staticmethod
    def _adjust(db: Session, item: StockItem, delta: float, unit_cost: Optional[float],
                lot_code: str = "", note: str = "", created_by: str = None) -> Optional[Lot]:
        """Manual on-hand correction. Increases become a new costed lot; decreases consume lots."""
        note = note or "Manual on-hand correction via Stock Items edit"
        if delta > 0:
            # A correction that only bumped item.on_hand without creating a lot would be
            # invisible to shipping, which only ever pulls from Lots -- so a manual
            # increase has to create a real, shippable lot, not just move the counter.
            # And without a cost, anything booked from that lot can't be profit-tracked.
            if unit_cost is None or unit_cost < 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Enter the unit cost the added {delta:g} {item.unit or 'units'} of {item.code} were acquired at",
                )
            lot = Lot(
                item_id=item.id,
                lot_code=lot_code or next_lot_code(db),
                quantity=delta,
                initial_quantity=delta,
                base_unit_cost=unit_cost,
                unit_cost=unit_cost,
                status="available",
                source="adjustment",
                source_reference="Adjustment",
            )
            db.add(lot)
            db.flush()
            db.add(InventoryTransaction(
                item_id=item.id, lot_id=lot.id, quantity_delta=delta, type="adjustment",
                note=f"{note} (unit cost {unit_cost:g})", created_by=created_by,
            ))
            item.on_hand += delta
            return lot

        # Decrease: take unbooked stock first (oldest lot first) so open shipments keep
        # what they've booked, then dip into booked lots only if free stock can't cover it.
        # If lots can't cover it at all (e.g. a miscount with no lot history), zero out
        # what remains -- a manual correction represents ground truth and must succeed.
        remaining = -delta
        taken = {}
        for lot, free in ShipmentService.free_lot_quantities(db, item.id):
            if remaining <= 1e-9:
                break
            take = min(free, remaining)
            taken[lot.id] = (lot, take)
            remaining -= take
        if remaining > 1e-9:
            # then booked stock, then lots on hold / rejected: they're on the shelf too, so a recount that finds less
            # comes out of them before the shortfall is booked against no lot at all (on hand must match the lots)
            rank = {"available": 0, "on_hold": 1, "rejected": 2}
            for lot in sorted(db.query(Lot).filter(Lot.item_id == item.id, Lot.quantity > 0).all(),
                              key=lambda l: (rank.get(l.status, 3), l.received_date or datetime.min)):
                if remaining <= 1e-9:
                    break
                already = taken.get(lot.id, (lot, 0))[1]
                take = min(lot.quantity - already, remaining)
                if take > 1e-9:
                    taken[lot.id] = (lot, already + take)
                    remaining -= take
        for lot, take in taken.values():
            lot.quantity -= take
            db.add(InventoryTransaction(
                item_id=item.id, lot_id=lot.id, quantity_delta=-take, type="adjustment",
                note=note, created_by=created_by,
            ))
        if remaining > 1e-9:
            db.add(InventoryTransaction(
                item_id=item.id, lot_id=None, quantity_delta=-remaining, type="adjustment",
                note="Manual on-hand correction exceeded available lot quantity", created_by=created_by,
            ))
        item.on_hand += delta
        return None

    @staticmethod
    def delete_pack_size_history(db: Session, entry_id: int) -> None:
        entry = db.get(PackSizeHistory, entry_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Pack size history entry not found")
        db.delete(entry)
        db.commit()

    @staticmethod
    def set_pack_size(db: Session, item: StockItem, pack_size: Optional[int], by: str = None,
                      source: str = "item edit", reference: str = None) -> bool:
        """Change the item's default pack size, keeping the old one in PackSizeHistory.
        Returns False when it was already that size (nothing recorded)."""
        if item.default_pack_size == pack_size:
            return False
        db.add(PackSizeHistory(item_id=item.id, pack_size=pack_size, previous_pack_size=item.default_pack_size,
                               source=source, reference=reference, changed_by=by))
        item.default_pack_size = pack_size
        return True

    @staticmethod
    def bulk_set_pack_sizes(db: Session, entries, by: str = None, source: str = "bulk paste", reference: str = None) -> dict:
        """Paste-a-list bulk update of default_pack_size by item code, same idea as the
        existing portal's Pack Size Processor -- update the catalog for many items at once.
        The newest size becomes the default; the previous one stays in the history."""
        applied, not_found, unchanged = [], [], []
        for entry in entries:
            code = (entry.code or "").strip()
            item = db.query(StockItem).filter(func.lower(StockItem.code) == code.lower()).first()
            if not item:
                not_found.append(code)
                continue
            if entry.pack_size is None or entry.pack_size <= 0:
                raise HTTPException(status_code=400, detail=f"{code}: pack size must be a whole number above 0")
            changed = StockItemService.set_pack_size(db, item, entry.pack_size, by, source, reference)
            (applied if changed else unchanged).append(item.code)
        db.commit()
        return {"applied": applied, "not_found": not_found, "unchanged": unchanged}

    @staticmethod
    def pack_size_history(db: Session, item_id: Optional[int] = None) -> list:
        query = db.query(PackSizeHistory, StockItem.code).join(StockItem, StockItem.id == PackSizeHistory.item_id)
        if item_id:
            query = query.filter(PackSizeHistory.item_id == item_id)
        rows = query.order_by(PackSizeHistory.changed_at.desc(), PackSizeHistory.id.desc()).all()
        return [dict(id=h.id, item_id=h.item_id, item_code=code, pack_size=h.pack_size,
                     previous_pack_size=h.previous_pack_size, source=h.source, reference=h.reference,
                     changed_by=h.changed_by, changed_at=h.changed_at) for h, code in rows]


# ---- Customers / Vendors ----
class PartyService:
    def __init__(self, model):
        self.model = model

    def list(self, db: Session) -> List:
        return db.query(self.model).order_by(self.model.name).all()

    def get(self, db: Session, party_id: int):
        party = db.query(self.model).filter(self.model.id == party_id).first()
        if not party:
            raise HTTPException(status_code=404, detail=f"{self.model.__name__} not found")
        return party

    def create(self, db: Session, data):
        fields = data.dict()
        details = fields.pop("details", None)
        party = self.model(**fields)
        if details is not None:
            party.details = details
        if self.model is Vendor:
            party.code = generate_code(db, Vendor, "V")
        db.add(party)
        db.commit()
        db.refresh(party)
        return party

    def update(self, db: Session, party_id: int, data):
        party = self.get(db, party_id)
        updates = data.dict(exclude_unset=True)
        details = updates.pop("details", None)
        set_fields(party, updates)
        if details is not None:
            party.details = details  # after the single fields: the card decides them
        db.commit()
        db.refresh(party)
        return party


customer_service = PartyService(Customer)
vendor_service = PartyService(Vendor)


def reorder_lines(db: Session, lines: list, line_ids: List[int]) -> None:
    """Save a new order: line_ids must be exactly the order's lines. The line # follows the position (#1 at the top), so
    the screen, packing lists and invoices all list them the same way. Everything links by line id, never by #."""
    by_id = {l.id: l for l in lines}
    if sorted(line_ids) != sorted(by_id):
        raise HTTPException(status_code=400, detail="The list of lines doesn't match this order -- reload and try again")
    for pos, lid in enumerate(line_ids):
        by_id[lid].position = pos
        if hasattr(by_id[lid], "line_no"):
            by_id[lid].line_no = pos + 1
    db.commit()


def renumber_lines(lines: list) -> None:
    """Lines numbered 1..n in their current order (after a line is added or removed)."""
    for pos, l in enumerate(sorted(lines, key=lambda l: (l.position if l.position is not None else 10**6, l.id))):
        l.position = pos
        if hasattr(l, "line_no"):
            l.line_no = pos + 1


def renumber_all_once(db: Session) -> None:
    """Once: every order's line # = its place on the order (before this, dragging only moved lines on screen)."""
    from app.models import AppSetting
    if db.query(AppSetting).filter(AppSetting.key == "line_numbers_follow_position").first():
        return
    for order in db.query(CustomerOrder).all():
        renumber_lines(order.lines)
    for po in db.query(PurchaseOrder).all():
        renumber_lines(po.lines)
    db.add(AppSetting(key="line_numbers_follow_position", value=datetime.utcnow().isoformat()))
    db.commit()


def _remove_attachments(db: Session, entity_type: str, entity_id: int) -> None:
    """Files attached to a record that's being deleted: the rows, their MTR links and the files on disk."""
    from pathlib import Path
    from app.config.settings import settings
    from app.models import Attachment, MtrLink
    root = Path(settings.upload_dir).resolve()
    from app.services.recycle_bin import move_to_trash
    for att in db.query(Attachment).filter(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id).all():
        move_to_trash(att.stored_name)  # kept in uploads/.trash until the recycle bin entry is emptied
        for link in db.query(MtrLink).filter(MtrLink.attachment_id == att.id).all():
            db.delete(link)
        db.delete(att)


def _item_references(db: Session, item_id: int) -> dict:
    """Where an item is used, by record -- {"customer orders": ["C89126 (cancelled)", ...], ...}.
    Anything here means it can be archived but not deleted."""
    from app.models import MtrLink
    def named(rows):
        return sorted({f"{code} ({status})" if status else code for code, status in rows})
    checks = {
        "customer orders": named(db.query(CustomerOrder.code, CustomerOrder.status).join(CustomerOrderLine, CustomerOrderLine.order_id == CustomerOrder.id)
                                 .filter(CustomerOrderLine.item_id == item_id).all()),
        "purchase orders": named(db.query(PurchaseOrder.code, PurchaseOrder.status).join(PurchaseOrderLine, PurchaseOrderLine.po_id == PurchaseOrder.id)
                                 .filter(PurchaseOrderLine.item_id == item_id).all()),
        "shipments": named(db.query(Shipment.code, Shipment.status).join(ShipmentLine, ShipmentLine.shipment_id == Shipment.id)
                           .filter(ShipmentLine.item_id == item_id).all()),
        "invoices": named(db.query(Invoice.code, Invoice.status).join(InvoiceLine, InvoiceLine.invoice_id == Invoice.id)
                          .filter(InvoiceLine.item_id == item_id).all()),
        "lots": named((l, None) for (l,) in db.query(Lot.lot_code).filter(Lot.item_id == item_id).all()),
        "stock movements": [f"{n} movement(s)"] if (n := db.query(InventoryTransaction).filter(InventoryTransaction.item_id == item_id).count()) else [],
        "MTRs": [f"{n} MTR link(s)"] if (n := db.query(MtrLink).filter(MtrLink.item_id == item_id).count()) else [],
    }
    return {k: v for k, v in checks.items() if v}


def _usage_text(used: dict, limit: int = 6) -> str:
    """'customer orders C89126 (cancelled); lots LOT-00012' -- short enough for a message."""
    parts = []
    for kind, names in used.items():
        shown = ", ".join(names[:limit]) + (f" and {len(names) - limit} more" if len(names) > limit else "")
        parts.append(shown if kind in ("stock movements", "MTRs") else f"{kind} {shown}")
    return "; ".join(parts)


# ---- Lots ----
class LotService:
    @staticmethod
    def list(db: Session, item_id: Optional[int] = None) -> List[Lot]:
        query = db.query(Lot)
        if item_id:
            query = query.filter(Lot.item_id == item_id)
        return query.order_by(Lot.received_date).all()

    @staticmethod
    def get(db: Session, lot_id: int) -> Lot:
        lot = db.query(Lot).filter(Lot.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail="Lot not found")
        return lot

    @staticmethod
    def set_status(db: Session, lot_id: int, status: str) -> Lot:
        if status not in ("available", "on_hold", "rejected"):
            raise HTTPException(status_code=400, detail="status must be available, on_hold, or rejected")
        lot = LotService.get(db, lot_id)
        lot.status = status
        db.commit()
        db.refresh(lot)
        return lot

    @staticmethod
    def set_cost(db: Session, lot_id: int, unit_cost: float) -> Lot:
        """Set what this lot was acquired at; any landed costs on its PO line stay on top."""
        lot = LotService.get(db, lot_id)
        lot.base_unit_cost = unit_cost
        lot.unit_cost = round(unit_cost + (line_landed_per_unit(db, lot.po_line_id) if lot.po_line_id else 0), 6)
        db.flush()
        refresh_item_cost(db, db.query(StockItem).filter(StockItem.id == lot.item_id).first())
        db.commit()
        db.refresh(lot)
        return lot

    @staticmethod
    def expiring(db: Session, within_days: int = 30) -> List[Lot]:
        """Available lots with an expiry date within the next N days (or already past)."""
        cutoff = datetime.utcnow() + timedelta(days=within_days)
        return db.query(Lot).filter(
            Lot.status == "available", Lot.quantity > 0,
            Lot.expiry_date.isnot(None), Lot.expiry_date <= cutoff,
        ).order_by(Lot.expiry_date).all()


class InventoryTransactionService:
    @staticmethod
    def recent(db: Session, limit: int = 25) -> List[InventoryTransaction]:
        return db.query(InventoryTransaction).order_by(InventoryTransaction.created_at.desc()).limit(limit).all()


# ---- Customer Orders ----
class CustomerOrderService:
    @staticmethod
    def list(db: Session, status: Optional[str] = None) -> List[CustomerOrder]:
        # load lines -> shipment lines -> shipment/lot in a few queries, not a few per line
        query = db.query(CustomerOrder).options(selectinload(CustomerOrder.lines).selectinload(CustomerOrderLine.shipment_lines)
                                                .options(selectinload(ShipmentLine.shipment).selectinload(Shipment.boxes), selectinload(ShipmentLine.lot)))
        if status:
            query = query.filter(CustomerOrder.status == status)
        return query.order_by(CustomerOrder.id.desc()).all()

    @staticmethod
    def get(db: Session, order_id: int) -> CustomerOrder:
        order = db.query(CustomerOrder).filter(CustomerOrder.id == order_id).first()
        if not order:
            raise HTTPException(status_code=404, detail="Customer order not found")
        return order

    @staticmethod
    def create(db: Session, data, created_by: str) -> CustomerOrder:
        customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
        if not customer:
            raise HTTPException(status_code=400, detail="Customer not found")
        if not data.lines:
            raise HTTPException(status_code=400, detail="Order must have at least one line")
        po = (data.po_number or "").strip()
        if po and not getattr(data, "allow_duplicate", False):
            existing = db.query(CustomerOrder).filter(CustomerOrder.customer_id == data.customer_id,
                                                      CustomerOrder.status != "cancelled").all()
            dup = next((o for o in existing if (o.po_number or "").strip().lower() == po.lower()), None)
            if dup:
                raise HTTPException(status_code=409, detail=f"DUPLICATE_PO|{dup.id}|{dup.code}|Customer PO {po} is already on order {dup.code}")

        order = CustomerOrder(
            code=generate_code(db, CustomerOrder, "CO"),
            customer_id=data.customer_id,
            delivery_date=data.delivery_date,
            po_number=data.po_number,
            customer_po_date=data.customer_po_date,
            job_number=data.job_number,
            ship_to_address=data.ship_to_address or customer.shipping_address or customer.address,
            notes=data.notes,
            expedited=data.expedited if getattr(data, "expedited", None) is not None else (customer.expedited or None),
            status="draft",
            created_by=created_by,
            # "create anyway" on the duplicate-PO prompt is the manager's OK
            duplicate_po_ok=f"{created_by}, {datetime.utcnow():%Y-%m-%d %H:%M} UTC (at creation)" if po and getattr(data, "allow_duplicate", False) else None,
        )
        db.add(order)
        db.flush()

        for line_no, line in enumerate(data.lines, 1):
            if not db.query(StockItem).filter(StockItem.id == line.item_id).first():
                raise HTTPException(status_code=400, detail=f"Stock item {line.item_id} not found")
            not_for_sale(db.query(StockItem).filter(StockItem.id == line.item_id).first())
            if line.quantity <= 0:
                raise HTTPException(status_code=400, detail=f"Line #{line_no}: quantity must be greater than 0")
            if (line.unit_price or 0) < 0:
                raise HTTPException(status_code=400, detail=f"Line #{line_no}: price can't be negative")
            db.add(CustomerOrderLine(
                order_id=order.id,
                line_no=line_no,
                position=line_no,
                item_id=line.item_id,
                quantity=line.quantity,
                unit_price=line.unit_price,
                delivery_date=line.delivery_date or data.delivery_date,
                notes=(line.notes or "").strip() or None, print_notes=line.print_notes is not False,
            ))
            # what the customer's PO called it (scanned orders) -> the item the user settled on
            from app.services import item_alias
            item_alias.learn(db, "customer", data.customer_id, line.item_id, line.source_code, line.source_description)

        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def capture(db: Session, customer_id: int, po_number: Optional[str], created_by: str) -> CustomerOrder:
        """Quick capture: a customer's PO kept as an order with no lines yet, status "validation". Nothing can be confirmed,
        booked or shipped until someone checks it, fills the lines in and validates it (validate())."""
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer:
            raise HTTPException(status_code=400, detail="Customer not found")
        order = CustomerOrder(code=generate_code(db, CustomerOrder, "CO"), customer_id=customer.id,
                              po_number=(po_number or "").strip() or None,
                              ship_to_address=customer.shipping_address or customer.address, status="validation", created_by=created_by)
        db.add(order)
        db.flush()
        return order

    @staticmethod
    def validate(db: Session, order_id: int, by: str, confirm: bool = False) -> CustomerOrder:
        """A captured order checked: on to Draft (or straight to Confirmed). Needs its lines."""
        order = CustomerOrderService.get(db, order_id)
        if order.status != "validation":
            raise HTTPException(status_code=400, detail=f"{order.code} is {order.status} -- only a captured order needs validating")
        if order.ai_pending_lines:
            raise HTTPException(status_code=400, detail=f"{len(order.ai_pending_lines)} line(s) from the PO still need an item -- "
                                                        "pick one for each (or drop it) first")
        if not order.lines:
            raise HTTPException(status_code=400, detail="Add the order's lines from the customer's PO first")
        po = (order.po_number or "").strip().lower()
        if po:
            dup = next((o for o in db.query(CustomerOrder).filter(CustomerOrder.customer_id == order.customer_id, CustomerOrder.id != order.id,
                                                                    CustomerOrder.status != "cancelled").all()
                        if (o.po_number or "").strip().lower() == po), None)
            if dup and not order.duplicate_po_ok:
                raise HTTPException(status_code=409, detail=f"DUPLICATE_PO|{dup.id}|{dup.code}|Customer PO {order.po_number} is already on order {dup.code}")
        order.status = "confirmed" if confirm else "draft"
        order.validated_by, order.validated_at = by, datetime.utcnow()
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def confirm(db: Session, order_id: int) -> CustomerOrder:
        """Confirming is a commitment to the customer, not a stock reservation --
        stock is only checked and booked when a shipment is created."""
        order = CustomerOrderService.get(db, order_id)
        if order.status == "validation":
            raise HTTPException(status_code=400, detail=f"{order.code} was quick-captured -- check it and Validate it first")
        if order.status != "draft":
            raise HTTPException(status_code=400, detail=f"Order is already {order.status}")
        order.status = "confirmed"
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def update(db: Session, order_id: int, data) -> CustomerOrder:
        """Header fields -- always editable unless the order is cancelled."""
        order = CustomerOrderService.get(db, order_id)
        if order.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot edit a cancelled order")
        updates = data.dict(exclude_unset=True)
        if "customer_id" in updates and updates["customer_id"] is not None:
            if not db.query(Customer).filter(Customer.id == updates["customer_id"]).first():
                raise HTTPException(status_code=400, detail="Customer not found")
        if "po_number" in updates and (updates["po_number"] or "").strip().lower() != (order.po_number or "").strip().lower():
            order.duplicate_po_ok = None  # a different PO # needs its own check
        set_fields(order, updates)
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def accept_duplicate_po(db: Session, order_id: int, username: str) -> CustomerOrder:
        """A manager confirms this order really is separate from the earlier one with the same customer PO #."""
        order = CustomerOrderService.get(db, order_id)
        order.duplicate_po_ok = f"{username}, {datetime.utcnow():%Y-%m-%d %H:%M} UTC"
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def _recompute_status(order: CustomerOrder) -> None:
        """Re-derive shipped/confirmed from line data. Draft and cancelled orders are left alone."""
        if order.status in ("validation", "draft", "cancelled"):
            return
        fully_shipped = all(l.shipped_quantity >= l.quantity - 1e-9 for l in order.lines)
        order.status = "shipped" if fully_shipped else "confirmed"

    @staticmethod
    def add_line(db: Session, order_id: int, data) -> CustomerOrder:
        order = CustomerOrderService.get(db, order_id)
        if order.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot edit a cancelled order")
        if not db.query(StockItem).filter(StockItem.id == data.item_id).first():
            raise HTTPException(status_code=400, detail=f"Stock item {data.item_id} not found")
        not_for_sale(db.query(StockItem).filter(StockItem.id == data.item_id).first())
        if data.quantity <= 0:
            raise HTTPException(status_code=400, detail="Quantity must be greater than 0")
        if (data.unit_price or 0) < 0:
            raise HTTPException(status_code=400, detail="Price can't be negative")
        db.add(CustomerOrderLine(
            order_id=order.id,
            line_no=max((l.line_no or 0 for l in order.lines), default=0) + 1,
            position=max((l.position if l.position is not None else i for i, l in enumerate(order.lines)), default=-1) + 1,
            item_id=data.item_id,
            quantity=data.quantity,
            unit_price=data.unit_price,
            delivery_date=data.delivery_date or order.delivery_date,
            notes=(data.notes or "").strip() or None, print_notes=data.print_notes is not False,
        ))
        db.flush()
        db.refresh(order)
        CustomerOrderService._place_nut_under_bolt(db, order, data.item_id)
        CustomerOrderService._recompute_status(order)
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def _place_nut_under_bolt(db: Session, order: CustomerOrder, item_id: int) -> None:
        """A '15439-NUT' line added to an order that has bolt 15439 goes straight under that bolt."""
        import re
        added = db.query(StockItem).filter(StockItem.id == item_id).first()
        m = re.match(r"(.+?)[\s-]*nuts?$", (added.code or "").strip(), re.I) if added else None
        if not m:
            return
        codes = {l.id: (db.query(StockItem.code).filter(StockItem.id == l.item_id).scalar() or "").strip().lower() for l in order.lines}
        lines = list(order.lines)
        new = max(lines, key=lambda l: l.id)
        bolt = next((l for l in lines if l is not new and codes[l.id] == m.group(1).strip().lower()), None)
        if not bolt:
            return
        lines.remove(new)
        lines.insert(lines.index(bolt) + 1, new)
        for pos, l in enumerate(lines):
            l.position = pos

    @staticmethod
    def update_line(db: Session, order_id: int, line_id: int, data) -> CustomerOrder:
        order = CustomerOrderService.get(db, order_id)
        if order.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot edit a cancelled order")
        line = db.query(CustomerOrderLine).filter(
            CustomerOrderLine.id == line_id, CustomerOrderLine.order_id == order.id
        ).first()
        if not line:
            raise HTTPException(status_code=404, detail="Order line not found")

        updates = data.dict(exclude_unset=True)
        if updates.get("item_id") is not None and updates["item_id"] != line.item_id:
            if line.allocated_quantity > 1e-9:
                raise HTTPException(status_code=400, detail="This line is already booked or shipped -- its item can't be changed. Add a new line instead.")
            new_item = db.query(StockItem).filter(StockItem.id == updates["item_id"]).first()
            if not new_item:
                raise HTTPException(status_code=400, detail="Item not found")
            if new_item.is_active is False:
                raise HTTPException(status_code=400, detail=f"{new_item.code} is archived")
            not_for_sale(new_item)
        else:
            updates.pop("item_id", None)
        if "quantity" in updates and updates["quantity"] is not None:
            if updates["quantity"] <= 0:
                raise HTTPException(status_code=400, detail="Quantity must be greater than 0 -- remove the line instead")
            if updates["quantity"] < line.allocated_quantity - 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot reduce quantity below {line.allocated_quantity}: {line.shipped_quantity} "
                           f"already shipped and {line.booked_quantity} booked into open shipments"
                )
        if (updates.get("unit_price") or 0) < 0:
            raise HTTPException(status_code=400, detail="Price can't be negative")
        if "notes" in updates:
            updates["notes"] = (updates["notes"] or "").strip() or None
        if updates.get("print_notes", False) is None:
            updates.pop("print_notes")
        set_fields(line, updates)
        db.flush()
        CustomerOrderService._recompute_status(order)
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def remove_line(db: Session, order_id: int, line_id: int) -> CustomerOrder:
        order = CustomerOrderService.get(db, order_id)
        if order.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot edit a cancelled order")
        line = db.query(CustomerOrderLine).filter(
            CustomerOrderLine.id == line_id, CustomerOrderLine.order_id == order.id
        ).first()
        if not line:
            raise HTTPException(status_code=404, detail="Order line not found")
        if line.allocated_quantity > 0:
            raise HTTPException(status_code=400, detail="Cannot remove a line that is booked into or shipped on a shipment")
        if len(order.lines) <= 1:
            raise HTTPException(status_code=400, detail="Order must have at least one line")
        db.delete(line)
        db.flush()
        db.refresh(order)
        renumber_lines(order.lines)  # no gap in the numbering
        CustomerOrderService._recompute_status(order)
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def cancel(db: Session, order_id: int) -> CustomerOrder:
        order = CustomerOrderService.get(db, order_id)
        if any(l.shipped_quantity > 0 for l in order.lines):
            raise HTTPException(status_code=400, detail="Cannot cancel an order that has already shipped")
        if any(l.booked_quantity > 0 for l in order.lines):
            raise HTTPException(status_code=400, detail="Cancel this order's open shipments first -- they still hold booked stock")
        order.status = "cancelled"
        CustomerOrderService.release_quote(db, order)
        db.commit()
        db.refresh(order)
        return order

    @staticmethod
    def removal_plan(db: Session, order_id: int) -> dict:
        """Everything in the way of cancelling / deleting an order, in the order it has to go:
        its invoices (void, then delete), its shipments (un-ship, then delete), then cancel and delete."""
        order = CustomerOrderService.get(db, order_id)
        invoices = []
        for inv in db.query(Invoice).filter(Invoice.order_id == order.id).order_by(Invoice.id).all():
            sent = inv.status != "draft" or bool(inv.emails)
            block = (f"{len(inv.payments)} payment(s) recorded ({', '.join(f'${p.amount:,.2f}' for p in inv.payments)}) -- remove them on the invoice first"
                     if inv.payments else "invoice funding recorded -- clear it on the invoice first" if (inv.funding_amount or inv.disbursement_date) else None)
            invoices.append({"id": inv.id, "code": inv.code, "status": inv.status, "total": inv.total, "sent": sent,
                             "steps": [] if block else (["delete"] if inv.status == "void" or not sent else ["void", "delete"]), "blocked": block})
        shipments = []
        for sh in db.query(Shipment).filter(Shipment.order_id == order.id).order_by(Shipment.id).all():
            if sh.status == "cancelled":
                shipments.append({"id": sh.id, "code": sh.code, "status": sh.status, "steps": [], "note": "goes with the order"})
                continue
            shipments.append({"id": sh.id, "code": sh.code, "status": sh.status, "delivered": bool(sh.delivered_at),
                              "steps": (["unship", "delete"] if sh.status in ShipmentService.SHIPPED_STATUSES else ["delete"])})
        blocked = [i["code"] + ": " + i["blocked"] for i in invoices if i["blocked"]]
        return {"order_id": order.id, "code": order.code, "status": order.status, "invoices": invoices, "shipments": shipments,
                "blocked": blocked, "can_cancel_now": order.status != "cancelled" and not any(l.shipped_quantity or l.booked_quantity for l in order.lines),
                "can_delete_now": order.status == "cancelled" and all(s["status"] == "cancelled" for s in shipments)
                                  and all(i["status"] == "void" for i in invoices)}

    @staticmethod
    def release_quote(db: Session, order: CustomerOrder) -> None:
        """The order a quote was converted into is cancelled / deleted: the quote goes back to how it was."""
        from app.models import Quote
        for q in db.query(Quote).filter(Quote.order_id == order.id).all():
            q.status = q.status_before_convert if q.status_before_convert in ("draft", "sent", "accepted", "declined") else "accepted"
            q.order_id, q.status_before_convert = None, None

    @staticmethod
    def delete(db: Session, order_id: int) -> None:
        """Delete a cancelled order for good, with its cancelled shipments and attached files.
        Not when anything was shipped or invoiced against it."""
        from app.models import MtrEmail
        order = CustomerOrderService.get(db, order_id)
        if order.status != "cancelled":
            raise HTTPException(status_code=400, detail="Cancel the order first -- only cancelled orders can be deleted")
        shipments = db.query(Shipment).filter(Shipment.order_id == order.id).all()
        if any(s.status != "cancelled" for s in shipments):
            raise HTTPException(status_code=400, detail="This order has shipments that weren't cancelled, so it can't be deleted")
        live = [i.code for i in db.query(Invoice).filter(Invoice.order_id == order.id, Invoice.status != "void").all()]
        if live:
            raise HTTPException(status_code=400, detail=f"{', '.join(live)} still bill this order -- void or delete {'it' if len(live) == 1 else 'them'} first")
        for inv in db.query(Invoice).filter(Invoice.order_id == order.id).all():  # void ones go with the order
            delete_invoice(db, inv.id, commit=False)
        for s in shipments:
            _remove_attachments(db, "shipment", s.id)
            db.delete(s)
        for row in db.query(MtrEmail).filter(MtrEmail.order_id == order.id).all():
            db.delete(row)
        _remove_attachments(db, "customer_order", order.id)
        CustomerOrderService.release_quote(db, order)
        db.flush()
        db.delete(order)
        db.commit()

    @staticmethod
    def create_shipment(db: Session, order_id: int, data, created_by: str) -> Shipment:
        """Create a shipment and book the requested quantities into it from specific
        lots (oldest first). This is the one place stock availability is checked."""
        order = CustomerOrderService.get(db, order_id)
        if order.status in ("shipped", "invoiced", "cancelled"):
            raise HTTPException(status_code=400, detail=f"Order is already {order.status}")
        if order.status == "validation":
            raise HTTPException(status_code=400, detail=f"Order {order.code} was quick-captured -- validate and confirm it before creating a shipment")
        if order.status == "draft":
            raise HTTPException(status_code=400, detail=f"Order {order.code} isn't confirmed yet -- confirm it before creating a shipment")
        requested = [l for l in data.lines if l.quantity > 0]
        if not requested:
            raise HTTPException(status_code=400, detail="Enter a quantity to book on at least one line")

        job = filenames.clean(order.job_number or "").replace(" ", "-")
        shipment = Shipment(
            code=generate_code(db, Shipment, "SH") + (f"-{job}" if job else ""),  # SH215771-M219-30B, as MRPeasy named them
            order_id=order.id,
            status="new",
            carrier=data.carrier,
            tracking_number=data.tracking_number,
            shipping_cost=data.shipping_cost,
            notes=data.notes,
            created_by=created_by,
        )
        db.add(shipment)
        db.flush()

        for req in requested:
            line = db.query(CustomerOrderLine).filter(
                CustomerOrderLine.id == req.line_id,
                CustomerOrderLine.order_id == order.id,
            ).first()
            if not line:
                raise HTTPException(status_code=400, detail=f"Order line {req.line_id} not found on this order")
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            open_qty = line.quantity - line.allocated_quantity
            if req.quantity > open_qty + 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot book {req.quantity} of {item.code}: only {open_qty} of the line is not "
                           f"already shipped or booked into another shipment"
                )

            lots_with_free = ShipmentService.free_lot_quantities(db, item.id)
            total_free = sum(free for _, free in lots_with_free)
            draw_from = getattr(req, "draw_from_item_id", None)
            if total_free + 1e-9 < req.quantity and draw_from:
                # Short, and the user chose a generic item to cover it: transfer exactly the shortfall
                # (new -T lots, cost carried), then book as usual. Rolls back with the shipment on any error.
                from app.services import stock_transfer
                stock_transfer.draw(db, item, draw_from, round(req.quantity - total_free), created_by,
                                    reference=f"{shipment.code} for {order.code}", commit=False)
                lots_with_free = ShipmentService.free_lot_quantities(db, item.id)
                total_free = sum(free for _, free in lots_with_free)
            if total_free + 1e-9 < req.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Not enough stock to book {item.code}: need {req.quantity}, only {total_free} free "
                           f"({item.on_hand} on hand, {item.booked} already booked into other shipments)"
                )

            remaining = req.quantity
            for lot, free in lots_with_free:
                if remaining <= 1e-9:
                    break
                take = min(free, remaining)
                remaining -= take
                db.add(ShipmentLine(
                    shipment_id=shipment.id,
                    order_line_id=line.id,
                    item_id=item.id,
                    lot_id=lot.id,
                    quantity=take,
                    picked_quantity=0,
                    unit_price=line.unit_price,
                ))
            item.booked += req.quantity
            # Flush so the next line's free-lot query sees what this one just booked
            # (two lines for the same item on one order must not double-book a lot).
            db.flush()

        db.commit()
        db.refresh(shipment)
        return shipment


# ---- Shipments (booking -> picking -> shipped, plus packing list / labels) ----
class ShipmentService:
    OPEN_STATUSES = ("new", "ready")
    SHIPPED_STATUSES = ("shipped", "delivered", "invoiced")  # stock has left

    @staticmethod
    def list(db: Session) -> List[Shipment]:
        return (db.query(Shipment).options(selectinload(Shipment.lines).selectinload(ShipmentLine.lot),
                                           selectinload(Shipment.lines).selectinload(ShipmentLine.order_line),
                                           selectinload(Shipment.boxes), selectinload(Shipment.pallets))
                .order_by(Shipment.id.desc()).all())

    @staticmethod
    def get(db: Session, shipment_id: int) -> Shipment:
        shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
        if not shipment:
            raise HTTPException(status_code=404, detail="Shipment not found")
        return shipment

    @staticmethod
    def free_lot_quantities(db: Session, item_id: int) -> list:
        """(lot, free qty) for each available lot, oldest first, where free = lot quantity
        minus what open shipments have already booked from that lot."""
        lots = db.query(Lot).filter(
            Lot.item_id == item_id, Lot.status == "available", Lot.quantity > 0,
        ).order_by(Lot.received_date).all()
        booked_by_lot = dict(db.query(ShipmentLine.lot_id, func.sum(ShipmentLine.quantity))
                             .join(Shipment, Shipment.id == ShipmentLine.shipment_id)
                             .filter(ShipmentLine.item_id == item_id,
                                     Shipment.status.in_(ShipmentService.OPEN_STATUSES))
                             .group_by(ShipmentLine.lot_id).all())
        result = []
        for lot in lots:
            free = lot.quantity - (booked_by_lot.get(lot.id) or 0)
            if free > 1e-9:
                result.append((lot, free))
        return result

    @staticmethod
    def reconcile_bookings(db: Session) -> None:
        """StockItem.booked is a cache of what open shipments hold -- rebuild it from them.
        Run at startup so stale reservations (e.g. from the old book-on-confirm logic) clear."""
        booked = dict(db.query(ShipmentLine.item_id, func.sum(ShipmentLine.quantity))
                      .join(Shipment, Shipment.id == ShipmentLine.shipment_id)
                      .filter(Shipment.status.in_(ShipmentService.OPEN_STATUSES))
                      .group_by(ShipmentLine.item_id).all())
        for item in db.query(StockItem).all():
            item.booked = booked.get(item.id) or 0
        # Shipments that shipped before picking existed were, by definition, fully picked.
        shipped_ids = [sid for (sid,) in db.query(Shipment.id).filter(Shipment.status.in_(ShipmentService.SHIPPED_STATUSES)).all()]
        if shipped_ids:
            db.query(ShipmentLine).filter(ShipmentLine.shipment_id.in_(shipped_ids)).update(
                {ShipmentLine.picked_quantity: ShipmentLine.quantity}, synchronize_session=False)
        db.commit()

    @staticmethod
    def update(db: Session, shipment_id: int, data) -> Shipment:
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot edit a cancelled shipment")
        set_fields(shipment, data.dict(exclude_unset=True))
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def rename(db: Session, shipment_id: int, code: str) -> Shipment:
        shipment = ShipmentService.get(db, shipment_id)
        shipment.code = _new_code(db, Shipment, shipment, code, "shipment")
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def confirm_booking(db: Session, shipment_id: int) -> Shipment:
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status != "new":
            raise HTTPException(status_code=400, detail=f"Shipment is {shipment.status}, bookings can only be confirmed on a new shipment")
        shipment.status = "ready"
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def unbook_all(db: Session, shipment_id: int) -> Shipment:
        """Unbook every unpicked unit on every line (picked units stay). Nothing picked -> the shipment ends up
        with no lines, and unbook() cancels it, as Cancel Shipment would."""
        from types import SimpleNamespace
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status not in ShipmentService.OPEN_STATUSES:
            raise HTTPException(status_code=400, detail=f"Shipment is {shipment.status} -- only new or ready shipments can be unbooked")
        todo = [(l.id, l.quantity - (l.picked_quantity or 0)) for l in shipment.lines if l.quantity - (l.picked_quantity or 0) > 1e-9]
        if not todo:
            raise HTTPException(status_code=400, detail="Nothing left to unbook -- everything on it is picked")
        for line_id, qty in todo:
            if shipment.status not in ShipmentService.OPEN_STATUSES:
                break
            shipment = ShipmentService.unbook(db, shipment_id, SimpleNamespace(shipment_line_id=line_id, order_line_id=None, quantity=qty))
        return shipment

    @staticmethod
    def unconfirm_booking(db: Session, shipment_id: int, by: str) -> Shipment:
        """Undo Confirm Bookings: ready -> new, so bookings can be changed again before picking.
        Stock stays booked. Only while nothing is picked (Unpick first)."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status != "ready":
            raise HTTPException(status_code=400, detail=f"Shipment is {shipment.status} -- only confirmed (ready) bookings can be unconfirmed")
        if any((l.picked_quantity or 0) > 0 for l in shipment.lines):
            raise HTTPException(status_code=400, detail=f"{shipment.code} has picked quantities -- Unpick it first")
        shipment.status = "new"
        shipment.updated_by = by
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def pick(db: Session, shipment_id: int, data, created_by: str) -> Shipment:
        """Record picked quantities. Nothing ships here: once everything is picked, packing is accepted
        and then Ship sends it (ship())."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status != "ready":
            raise HTTPException(
                status_code=400,
                detail="Confirm the bookings before picking" if shipment.status == "new"
                       else f"Shipment is {shipment.status}, nothing to pick",
            )
        lines_by_id = {l.id: l for l in shipment.lines}
        if data.pick_all:
            for line in shipment.lines:
                line.picked_quantity = line.quantity
        else:
            for req in data.lines:
                line = lines_by_id.get(req.shipment_line_id)
                if not line:
                    raise HTTPException(status_code=400, detail=f"Line {req.shipment_line_id} is not on this shipment")
                if req.quantity < 0:
                    raise HTTPException(status_code=400, detail="Picked quantity cannot be negative")
                if (line.picked_quantity or 0) + req.quantity > line.quantity + 1e-9:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot pick {req.quantity}: only {line.quantity - (line.picked_quantity or 0)} "
                               f"left to pick on that line"
                    )
                line.picked_quantity = (line.picked_quantity or 0) + req.quantity
        if data.unbook_rest and not any((l.picked_quantity or 0) > 0 for l in shipment.lines):
            raise HTTPException(status_code=400, detail="Nothing is picked -- unbooking the rest would cancel the shipment (use Unbook All for that)")
        db.commit()
        db.refresh(shipment)
        if data.unbook_rest and any(l.quantity - (l.picked_quantity or 0) > 1e-9 for l in shipment.lines):
            shipment = ShipmentService.unbook_all(db, shipment_id)  # the short qty goes back to stock; the order line stays open for it
        return shipment

    @staticmethod
    def default_boxes(db: Session, shipment: Shipment) -> list:
        """Boxes by each order line's pre-filled pack size (app/services/pack_sizes.py: the customer's / last packed size or
        the item default; none = one box), as the packing screen proposes."""
        from app.services import pack_sizes
        out = []
        first = {}
        for l in shipment.lines:
            first.setdefault(l.order_line_id, l)
        qty, _ = ShipmentService.packing_quantities(shipment)  # a combined nut has no boxes of its own
        for ol_id, total in qty.items():
            l = first[ol_id]
            known = pack_sizes.size_for_line(db, shipment, l.item_id, ol_id)
            pack = int(known or 0) or int(total)
            n, left = 0, total
            while left > 1e-9:
                n += 1
                out.append(ShipmentBox(shipment_id=shipment.id, order_line_id=ol_id, item_id=l.item_id, box_number=n,
                                       quantity_in_box=min(pack, left), pack_size=pack if known else None))
                left -= pack
        return out

    @staticmethod
    def accept_packing(db: Session, shipment_id: int, by: str) -> Shipment:
        """Packing reviewed and accepted (Ship needs it). No boxes saved yet: they're made from the pack sizes."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status not in ShipmentService.OPEN_STATUSES:
            raise HTTPException(status_code=400, detail=f"Shipment is {shipment.status} -- packing is accepted before it ships")
        boxed = {b.order_line_id for b in shipment.boxes}
        for box in ShipmentService.default_boxes(db, shipment):  # lines with no boxes yet get them from pack sizes
            if box.order_line_id not in boxed:
                db.add(box)
        shipment.packed_at, shipment.packed_by = datetime.utcnow(), by
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def unpick(db: Session, shipment_id: int, by: str) -> Shipment:
        """Undo picking on a shipment that hasn't left (picking moves no stock): every line back to 0 picked.
        Bookings and the boxes stay, but an accepted packing has to be re-checked and accepted again once it's
        picked again -- the shipment went back a step, so its packing is reverified before it can ship."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status not in ShipmentService.OPEN_STATUSES:
            raise HTTPException(status_code=400, detail=f"Shipment is {shipment.status} -- only a shipment that hasn't shipped can be unpicked")
        for line in shipment.lines:
            line.picked_quantity = 0
        shipment.packed_at = shipment.packed_by = None  # boxes kept as a proposal; Accept Packaging again
        shipment.updated_by = by
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def unpack(db: Session, shipment_id: int, by: str) -> Shipment:
        """Undo the packing of a shipment that hasn't left: boxes, pallet weights and 'packing accepted' are cleared,
        so it's packed again from scratch. Picking and stock bookings are untouched."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status not in ShipmentService.OPEN_STATUSES:
            raise HTTPException(status_code=400, detail=f"Shipment is {shipment.status} -- only a shipment that hasn't shipped can be unpacked")
        db.query(ShipmentBox).filter(ShipmentBox.shipment_id == shipment.id).delete()
        db.query(PalletWeight).filter(PalletWeight.shipment_id == shipment.id).delete()
        shipment.packed_at = shipment.packed_by = None
        shipment.updated_by = by
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def ship(db: Session, shipment_id: int, created_by: str) -> Shipment:
        """Send it: everything picked and packing accepted -> stock leaves on-hand, status shipped."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status != "ready":
            raise HTTPException(status_code=400, detail="Confirm the bookings and pick first" if shipment.status == "new"
                                else f"Shipment is {shipment.status} -- nothing to ship")
        short = [l for l in shipment.lines if (l.picked_quantity or 0) < l.quantity - 1e-9]
        if short:
            raise HTTPException(status_code=400, detail=f"{len(short)} line(s) aren't fully picked yet -- pick everything before shipping")
        if not shipment.packed_at:
            raise HTTPException(status_code=400, detail="Review and accept the packing before shipping")
        if ShipmentService._packing_mismatch(shipment):
            raise HTTPException(status_code=400, detail="The boxes no longer add up to what's on the shipment -- re-check the packing and accept it again")
        ShipmentService._close(db, shipment, created_by)
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def _close(db: Session, shipment: Shipment, created_by: str) -> None:
        """Everything is picked: stock physically leaves. Deduct lots/on-hand, release the booking."""
        for line in shipment.lines:
            lot = db.query(Lot).filter(Lot.id == line.lot_id).first() if line.lot_id else None
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            if lot and lot.quantity + 1e-9 < line.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Lot {lot.lot_code} of {item.code} now only holds {lot.quantity}, but {line.quantity} "
                           f"is booked from it -- stock was adjusted after booking. Cancel and re-book this shipment."
                )
            if lot:
                lot.quantity -= line.quantity
            item.on_hand -= line.quantity
            item.booked = max(0, item.booked - line.quantity)
            line.order_line.shipped_quantity += line.quantity
            db.add(InventoryTransaction(
                item_id=item.id,
                lot_id=line.lot_id,
                quantity_delta=-line.quantity,
                type="shipment",
                reference=shipment.code,
                created_by=created_by,
            ))
        shipment.status = "shipped"
        shipment.ship_date = datetime.utcnow()  # a moment: UTC (app/services/clock.py)
        db.flush()
        order = db.query(CustomerOrder).filter(CustomerOrder.id == shipment.order_id).first()
        CustomerOrderService._recompute_status(order)

    @staticmethod
    def cancel(db: Session, shipment_id: int) -> Shipment:
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status not in ShipmentService.OPEN_STATUSES:
            raise HTTPException(status_code=400, detail=f"Shipment is {shipment.status} and can no longer be cancelled")
        for line in shipment.lines:
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            item.booked = max(0, item.booked - line.quantity)
        db.query(ShipmentBox).filter(ShipmentBox.shipment_id == shipment.id).delete()
        db.query(PalletWeight).filter(PalletWeight.shipment_id == shipment.id).delete()
        shipment.combos.clear()  # nothing is going out together any more
        shipment.status = "cancelled"
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def mark_delivered(db: Session, shipment: Shipment, delivered_at: Optional[datetime], by: str, commit: bool = True,
                       tz_name: Optional[str] = None) -> Shipment:
        """Record the delivery date. A shipped shipment moves to "delivered"; an already
        invoiced one keeps its status but gets the date (it's used on the invoice)."""
        if shipment.status not in ShipmentService.SHIPPED_STATUSES:
            raise HTTPException(status_code=400, detail=f"{shipment.code} is {shipment.status} -- it has to ship before it can be delivered")
        when = clock.moment_from_input(delivered_at, tz_name) or datetime.utcnow()  # a picked date = noon that day, their zone
        if shipment.ship_date and clock.local(when, tz_name).date() < clock.local(shipment.ship_date, tz_name).date():
            raise HTTPException(status_code=400, detail=f"{shipment.code} shipped on {clock.local(shipment.ship_date, tz_name):%b %d, %Y} -- it can't be delivered before that")
        shipment.delivered_at = when
        shipment.delivered_by = by
        for line in shipment.lines:  # a line's own date that now matches the shipment's is no longer its own
            if line.delivered_at and clock.local(line.delivered_at, tz_name).date() == clock.local(when, tz_name).date():
                line.delivered_at = None
        if shipment.status == "shipped":
            shipment.status = "delivered"
        if commit:
            db.commit()
            db.refresh(shipment)
        return shipment

    @staticmethod
    def set_delivery_dates(db: Session, shipment: Shipment, data, by: str, tz_name: Optional[str] = None) -> Shipment:
        """The Delivery Date pop-up: the shipment's date, and per order line a date only where that line arrived on
        another day (the same day as the shipment, or none, = it came with the shipment)."""
        ShipmentService.mark_delivered(db, shipment, data.delivered_at, by, commit=False, tz_name=tz_name)
        today = clock.local(datetime.utcnow(), tz_name).date()
        shipped_on = clock.local(shipment.ship_date, tz_name).date() if shipment.ship_date else None
        if clock.local(shipment.delivered_at, tz_name).date() > today:
            raise HTTPException(status_code=400, detail="The delivery date can't be in the future")
        by_line = {}
        for line in shipment.lines:
            by_line.setdefault(line.order_line_id, []).append(line)
        for entry in data.lines:
            rows = by_line.get(entry.order_line_id)
            if not rows:
                raise HTTPException(status_code=400, detail=f"Order line {entry.order_line_id} isn't on {shipment.code}")
            when = clock.moment_from_input(entry.delivered_at, tz_name)
            label = f"#{rows[0].line_no}" if rows[0].line_no is not None else "a line"
            if when:
                day = clock.local(when, tz_name).date()
                if day > today:
                    raise HTTPException(status_code=400, detail=f"Line {label}: the delivery date can't be in the future")
                if shipped_on and day < shipped_on:
                    raise HTTPException(status_code=400, detail=f"Line {label}: {shipment.code} shipped on {shipped_on:%b %d, %Y} -- it can't be delivered before that")
                if day == clock.local(shipment.delivered_at, tz_name).date():
                    when = None
            for row in rows:  # every lot row of the order line gets the same date
                row.delivered_at = when
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def clear_delivered(db: Session, shipment_id: int) -> Shipment:
        shipment = ShipmentService.get(db, shipment_id)
        shipment.delivered_at = None
        shipment.delivered_by = None
        for line in shipment.lines:
            line.delivered_at = None
        if shipment.status == "delivered":
            shipment.status = "shipped"
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def undo_plan(db: Session, shipment_id: int) -> dict:
        """What undoing this shipment involves: its live invoice (voided with it) and the steps a sent
        invoice needs first -- remove payments, tell the customer, give a reason, accept combined shipments."""
        from app.models import Attachment
        shipment = ShipmentService.get(db, shipment_id)
        InvoiceService.refuse_if_split(db, shipment)
        inv = InvoiceService.live_invoice_for_shipment(db, shipment.id)
        pods = db.query(Attachment).filter(Attachment.entity_type == "shipment", Attachment.entity_id == shipment.id,
                                           Attachment.category == "pod").count()
        plan = {"shipment_id": shipment.id, "code": shipment.code, "status": shipment.status, "pods": pods,
                "delivered_at": shipment.delivered_at, "invoice": None, "steps": []}
        if inv:
            sent = inv.status != "draft" or bool(inv.emails)
            others = [s.code for s in inv.shipments if s.id != shipment.id]
            cust = db.query(Customer).filter(Customer.id == inv.customer_id).first()
            plan["invoice"] = {
                "id": inv.id, "code": inv.code, "status": inv.status, "total": inv.total, "sent": sent,
                "emails": [{"to": e.to_address, "sent_at": e.sent_at} for e in inv.emails],
                "payments": [{"id": p.id, "amount": p.amount, "paid_date": p.paid_date, "method": p.method, "reference": p.reference}
                             for p in inv.payments],
                "combined_with": others, "customer_email": cust.invoice_email if cust else None}
            if inv.payments:
                plan["steps"].append("remove_payments")
            if sent:
                plan["steps"] += ["notify_customer", "reason"]
            if others:
                plan["steps"].append("combined")
        return plan

    @staticmethod
    def unship(db: Session, shipment_id: int, created_by: str, data=None) -> Shipment:
        """Undo a shipped shipment: stock comes back to its lots/on-hand and stays booked,
        and the shipment returns to "new" so lines can be unbooked, edited, or cancelled.
        Its invoice is voided with it -- a draft one straight away; a sent one only once the
        steps are done (payments removed, customer told, reason given). Proof of delivery
        files stay on the shipment."""
        shipment = ShipmentService.get(db, shipment_id)
        InvoiceService.refuse_if_split(db, shipment)
        live = InvoiceService.live_invoice_for_shipment(db, shipment.id)
        if shipment.status not in ("shipped", "delivered", "invoiced"):
            raise HTTPException(status_code=400, detail=f"Shipment is {shipment.status} -- only shipped shipments can be un-shipped")
        if live:
            reason = ((data.reason if data else None) or "").strip()
            sent = live.status != "draft" or bool(live.emails)
            if live.payments:
                raise HTTPException(status_code=400, detail=f"{live.code} has ${sum(p.amount for p in live.payments):,.2f} in payments -- "
                                                            "remove them first (step 1), then undo the shipment")
            if sent and not (data and data.customer_notified and reason):
                raise HTTPException(status_code=400, detail=f"{live.code} was sent to the customer -- tick that they've been told "
                                                            "it's cancelled and give a reason before undoing the shipment")
            others = [s for s in live.shipments if s.id != shipment.id]
            if others and not (data and data.combined_ok):
                raise HTTPException(status_code=400, detail=f"{live.code} also bills {', '.join(s.code for s in others)} -- "
                                                            "voiding it un-invoices them too; confirm that first")
            for s in live.shipments:
                if s.status == "invoiced":
                    s.status = "delivered" if s.delivered_at else "shipped"
            live.status, live.voided_at, live.voided_by = "void", datetime.utcnow(), created_by
            live.void_reason = reason or f"Draft voided: shipment {shipment.code} was undone"
            db.flush()
            db.refresh(shipment)
        for line in shipment.lines:
            lot = db.query(Lot).filter(Lot.id == line.lot_id).first() if line.lot_id else None
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            if lot:
                lot.quantity += line.quantity
            item.on_hand += line.quantity
            item.booked += line.quantity
            line.order_line.shipped_quantity = max(0, line.order_line.shipped_quantity - line.quantity)
            line.picked_quantity = 0
            line.delivered_at = None
            db.add(InventoryTransaction(
                item_id=item.id,
                lot_id=line.lot_id,
                quantity_delta=line.quantity,
                type="shipment_reversal",
                reference=shipment.code,
                created_by=created_by,
            ))
        shipment.status = "new"
        shipment.packed_at = shipment.packed_by = None  # back before packing: boxes kept, packing re-accepted before it ships again
        shipment.ship_date = None
        shipment.delivered_at = None
        shipment.delivered_by = None
        db.flush()
        order = db.query(CustomerOrder).filter(CustomerOrder.id == shipment.order_id).first()
        CustomerOrderService._recompute_status(order)
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def delete(db: Session, shipment_id: int) -> None:
        """Delete a shipment that never shipped (open ones are released first)."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status in ShipmentService.SHIPPED_STATUSES:
            raise HTTPException(status_code=400, detail="Un-ship this shipment before deleting it")
        if InvoiceService.live_invoice_for_shipment(db, shipment.id):
            raise HTTPException(status_code=400, detail="An invoice bills this shipment -- void it first")
        # Voided invoices keep their lines (and so their history); only the link to this shipment goes.
        db.query(InvoiceShipment).filter(InvoiceShipment.shipment_id == shipment.id).delete()
        db.query(InvoiceLine).filter(InvoiceLine.shipment_id == shipment.id).update({InvoiceLine.shipment_id: None})
        db.query(Invoice).filter(Invoice.shipment_id == shipment.id).update({Invoice.shipment_id: None})
        # Proof of delivery stays on record, on the order, in case a question ever comes up.
        from app.models import Attachment
        for att in db.query(Attachment).filter(Attachment.entity_type == "shipment", Attachment.entity_id == shipment.id,
                                               Attachment.category == "pod").all():
            att.entity_type, att.entity_id = "customer_order", shipment.order_id
            att.note = f"From deleted shipment {shipment.code}" + (f" -- {att.note}" if att.note else "")
        if shipment.status in ShipmentService.OPEN_STATUSES:
            for line in shipment.lines:
                item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
                item.booked = max(0, item.booked - line.quantity)
        db.delete(shipment)
        db.commit()

    @staticmethod
    def unbook(db: Session, shipment_id: int, data) -> Shipment:
        """Release booked-but-unpicked quantity back to stock. Lines that drop to zero are
        removed; a shipment left with no lines is cancelled. Packing for the affected items
        is cleared since its box counts no longer match."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status not in ShipmentService.OPEN_STATUSES:
            raise HTTPException(status_code=400, detail=f"Shipment is {shipment.status} -- only new or ready shipments can be unbooked")
        if data.shipment_line_id:
            lines = [l for l in shipment.lines if l.id == data.shipment_line_id]
        elif data.order_line_id:
            # Newest lot first: undo booking in the reverse of the oldest-first order it was made.
            lines = sorted((l for l in shipment.lines if l.order_line_id == data.order_line_id),
                           key=lambda l: l.id, reverse=True)
        else:
            raise HTTPException(status_code=400, detail="Specify the shipment line or order line to unbook")
        if not lines:
            raise HTTPException(status_code=404, detail="That line is not on this shipment")

        unpicked = sum(l.quantity - (l.picked_quantity or 0) for l in lines)
        if data.quantity > unpicked + 1e-9:
            picked = sum(l.picked_quantity or 0 for l in lines)
            raise HTTPException(
                status_code=400,
                detail=f"Only {unpicked:g} can be unbooked" + (f" -- {picked:g} is already picked" if picked else ""),
            )

        remaining = data.quantity
        touched_lines = set()
        for line in lines:
            if remaining <= 1e-9:
                break
            take = min(line.quantity - (line.picked_quantity or 0), remaining)
            if take <= 1e-9:
                continue
            remaining -= take
            line.quantity -= take
            touched_lines.add(line.order_line_id)
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            item.booked = max(0, item.booked - take)
            if line.quantity <= 1e-9:
                shipment.lines.remove(line)

        for box in [b for b in shipment.boxes if b.order_line_id in touched_lines]:
            shipment.boxes.remove(box)
            shipment.packed_at = shipment.packed_by = None  # its packing changed: re-checked and accepted again
        from app.services.nut_combos import fit_to_bookings
        fit_to_bookings(db, shipment)  # bolts + nuts combined: fewer full sets left -> fewer assembled units
        if not shipment.lines:
            shipment.boxes.clear()
            shipment.pallets.clear()
            shipment.combos.clear()
            shipment.status = "cancelled"
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def set_boxes(db: Session, shipment_id: int, data) -> Shipment:
        """Replace the packing-list/box breakdown for this shipment (used for label printing)."""
        shipment = ShipmentService.get(db, shipment_id)
        if shipment.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot pack a cancelled shipment")

        # Packing is per order line, never merged by item: the same item on two lines is
        # boxed (and labelled) as two separate lines.
        shipped_by_line, order_lines = ShipmentService.packing_quantities(shipment)
        lines_by_item = {}
        for ol in order_lines.values():
            lines_by_item.setdefault(ol.item_id, []).append(ol)

        resolved, boxed_by_line = [], {}
        for box in data.boxes:
            ol = order_lines.get(box.order_line_id) if box.order_line_id else None
            if ol is None:
                candidates = lines_by_item.get(box.item_id, [])
                if box.order_line_id or len(candidates) != 1:
                    raise HTTPException(status_code=400, detail="Each box must name an order line that is on this shipment")
                ol = candidates[0]
            resolved.append((box, ol))
            boxed_by_line[ol.id] = boxed_by_line.get(ol.id, 0) + box.quantity_in_box

        for line_id in set(shipped_by_line) | set(boxed_by_line):
            shipped_qty = shipped_by_line.get(line_id, 0)
            boxed_qty = boxed_by_line.get(line_id, 0)
            if abs(boxed_qty - shipped_qty) > 1e-6:
                ol = order_lines[line_id]
                item = db.query(StockItem).filter(StockItem.id == ol.item_id).first()
                raise HTTPException(
                    status_code=400,
                    detail=f"Line #{ol.line_no} ({item.code if item else ol.item_id}): boxed {boxed_qty:g} "
                           f"but the shipment has {shipped_qty:g}"
                )

        db.query(ShipmentBox).filter(ShipmentBox.shipment_id == shipment.id).delete()
        for box, ol in resolved:
            db.add(ShipmentBox(
                shipment_id=shipment.id,
                order_line_id=ol.id,
                item_id=ol.item_id,
                box_number=box.box_number,
                quantity_in_box=box.quantity_in_box,
                lot_code=box.lot_code,
                pallet_number=box.pallet_number,
                pack_size=box.pack_size,
            ))
        db.flush()
        db.refresh(shipment)
        # a nut left without a pallet goes on its bolt's -- so its labels say so too
        from app.services.nut_pairing import fill_nut_pallets
        codes = {i.id: i.code for i in db.query(StockItem).filter(StockItem.id.in_({b.item_id for b in shipment.boxes})).all()}
        fill_nut_pallets(shipment, codes)
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def quantities_by_order_line(shipment: Shipment):
        """(quantity per order line id, order line by id) -- a line booked from several lots
        is still one line. What actually ships (and is billed): see packing_quantities for boxing."""
        qty, lines = {}, {}
        for sl in shipment.lines:
            qty[sl.order_line_id] = qty.get(sl.order_line_id, 0) + sl.quantity
            lines[sl.order_line_id] = sl.order_line
        return qty, lines

    @staticmethod
    def packing_quantities(shipment: Shipment):
        """Like quantities_by_order_line, but what gets BOXED: a nut combined with its bolt rides in the bolt's boxes
        (app/services/nut_combos.py), so only its separate part (if any) has boxes of its own."""
        from app.services.nut_combos import packing_quantities
        return packing_quantities(shipment)

    @staticmethod
    def set_pallet_weights(db: Session, shipment_id: int, data) -> Shipment:
        """Replace the weight/dimensions entries for this shipment's pallets."""
        shipment = ShipmentService.get(db, shipment_id)
        db.query(PalletWeight).filter(PalletWeight.shipment_id == shipment.id).delete()
        for p in data.pallets:
            db.add(PalletWeight(
                shipment_id=shipment.id,
                pallet_number=p.pallet_number,
                weight=p.weight,
                dimensions=p.dimensions,
            ))
        db.commit()
        db.refresh(shipment)
        return shipment

    @staticmethod
    def _packing_mismatch(shipment: Shipment) -> bool:
        """Some order line's boxes don't add up to what the shipment holds of it."""
        shipped_by_line, _ = ShipmentService.packing_quantities(shipment)
        boxed_by_line = {}
        for box in shipment.boxes:
            boxed_by_line[box.order_line_id] = boxed_by_line.get(box.order_line_id, 0) + box.quantity_in_box
        return any(abs(boxed_by_line.get(line_id, 0) - qty) > 1e-6 for line_id, qty in shipped_by_line.items())

    @staticmethod
    def unpacked(db: Session) -> List[Shipment]:
        """Open (new / ready) shipments whose boxed quantity doesn't match their quantity -- still to be
        packed. Shipped ones are out of the building whatever their box records say."""
        result = []
        for shipment in db.query(Shipment).filter(Shipment.status.in_(ShipmentService.OPEN_STATUSES)).order_by(Shipment.id.desc()).all():
            if ShipmentService._packing_mismatch(shipment):
                result.append(shipment)
        return result


# ---- Invoices ----
class InvoiceService:
    @staticmethod
    def list(db: Session) -> List[Invoice]:
        return (db.query(Invoice).options(selectinload(Invoice.shipments), selectinload(Invoice.lines),
                                          selectinload(Invoice.payments), selectinload(Invoice.emails))
                .order_by(Invoice.id.desc()).all())

    @staticmethod
    def get(db: Session, invoice_id: int) -> Invoice:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")
        return invoice

    @staticmethod
    def live_invoice_for_shipment(db: Session, shipment_id: int) -> Optional[Invoice]:
        """The non-void invoice billing this shipment, if any."""
        return (db.query(Invoice).join(InvoiceShipment, InvoiceShipment.invoice_id == Invoice.id)
                .filter(InvoiceShipment.shipment_id == shipment_id, Invoice.status != "void").first())

    @staticmethod
    def live_invoices_for_shipment(db: Session, shipment_id: int, exclude_id: Optional[int] = None) -> List[Invoice]:
        """Every non-void invoice billing this shipment -- more than one once its lines were split onto other invoices."""
        q = (db.query(Invoice).join(InvoiceShipment, InvoiceShipment.invoice_id == Invoice.id)
             .filter(InvoiceShipment.shipment_id == shipment_id, Invoice.status != "void"))
        if exclude_id:
            q = q.filter(Invoice.id != exclude_id)
        return q.order_by(Invoice.id).all()

    @staticmethod
    def refuse_if_split(db: Session, shipment) -> None:
        """Undoing a shipment voids its invoice; when its lines are split over several, that's for a person to sort out."""
        lives = InvoiceService.live_invoices_for_shipment(db, shipment.id)
        if len(lives) > 1:
            raise HTTPException(status_code=400, detail=f"{shipment.code} is billed on {' and '.join(i.code for i in lives)} (its lines were split) -- "
                                                        "combine them back into one invoice, or void the others, before undoing the shipment")

    @staticmethod
    def release_shipments(db: Session, invoice: Invoice) -> None:
        """An invoice is going (void / delete): its shipments are billable again -- unless another live invoice
        still bills them (lines split onto it)."""
        for shipment in invoice.shipments:
            if shipment.status == "invoiced" and not InvoiceService.live_invoices_for_shipment(db, shipment.id, exclude_id=invoice.id):
                shipment.status = "delivered" if shipment.delivered_at else "shipped"

    @staticmethod
    def split_lines(db: Session, invoice_id: int, picks, created_by: str) -> Invoice:
        """Move some lines (or part of a line's quantity) off a draft invoice onto a new draft. Both invoices keep the
        same order and bill the same shipment(s), so billed-vs-delivered still adds up across them."""
        from app.models import BillingVariance
        invoice = InvoiceService.get(db, invoice_id)
        if invoice.status != "draft" or invoice.emails:
            raise HTTPException(status_code=400, detail=f"{invoice.code} has gone to the customer -- only a draft can be split "
                                                        "(void it and invoice the shipment again, then split the new draft)")
        if invoice.payments or invoice.funding_amount is not None:
            raise HTTPException(status_code=400, detail=f"{invoice.code} has payments or funding recorded -- it can't be split")
        lines = {l.id: l for l in invoice.lines}
        moves = []
        for p in picks:
            line = lines.get(p.line_id)
            if not line:
                raise HTTPException(status_code=400, detail=f"Line {p.line_id} isn't on {invoice.code}")
            qty = line.quantity if p.quantity is None else p.quantity
            if qty <= 0 or qty > line.quantity + 1e-9:
                raise HTTPException(status_code=400, detail=f"{line.description}: move between 1 and {line.quantity:g}")
            if line.item_id is not None and abs(qty - round(qty)) > 1e-9:
                raise HTTPException(status_code=400, detail=f"{line.description}: use a whole number")
            moves.append((line, qty))
        if not moves:
            raise HTTPException(status_code=400, detail="Pick at least one line to split off")
        whole = {line.id for line, qty in moves if abs(qty - line.quantity) < 1e-9}
        if len(whole) == len(lines):
            raise HTTPException(status_code=400, detail="That moves every line -- leave at least one on " + invoice.code)

        new = Invoice(code=generate_code(db, Invoice, "INV"), customer_id=invoice.customer_id, order_id=invoice.order_id,
                      shipment_id=invoice.shipment_id, due_date=invoice.due_date, free_text=invoice.free_text, status="draft",
                      print_zero_lines=invoice.print_zero_lines, print_payments=invoice.print_payments,
                      split_from_id=invoice.id, created_by=created_by)
        db.add(new)
        db.flush()
        for line, qty in moves:
            if line.id in whole:
                invoice.lines.remove(line)
                new.lines.append(line)
            else:  # part of the line: the rest stays here
                line.quantity = line.quantity - qty
                new.lines.append(InvoiceLine(item_id=line.item_id, order_line_id=line.order_line_id, shipment_id=line.shipment_id,
                                             description=line.description, quantity=qty, unit_price=line.unit_price,
                                             notes=line.notes, print_notes=line.print_notes))
        # The new invoice bills the shipments its lines went out on (a line with none: all of this invoice's).
        moved_sids = {l.shipment_id for l in new.lines if l.shipment_id}
        for sh in invoice.shipments:
            if not moved_sids or sh.id in moved_sids:
                new.shipments.append(sh)
        db.flush()
        # ...and this one stops billing a shipment none of its remaining lines came from.
        kept_sids = {l.shipment_id for l in invoice.lines if l.shipment_id}
        if kept_sids:
            for sh in [s for s in invoice.shipments if s.id not in kept_sids]:
                invoice.shipments.remove(sh)
            invoice.shipment_id = invoice.shipments[0].id if invoice.shipments else invoice.shipment_id
        new.shipment_id = new.shipments[0].id if new.shipments else invoice.shipment_id
        # Accepted billed-vs-delivered differences are kept per invoice: re-file them for both halves, same reasons.
        from app.services import billing
        reasons = {v.order_line_id: (v.reason, v.accepted_by)
                   for v in db.query(BillingVariance).filter(BillingVariance.invoice_id == invoice.id).all()}
        for inv in (invoice, new):
            db.flush()
            db.refresh(inv)
            billing.refile_variances(db, inv, created_by, reasons)
        db.commit()
        db.refresh(new)
        return new

    @staticmethod
    def create_from_shipment(db: Session, shipment_id: int, data, created_by: str) -> Invoice:
        return InvoiceService.create_from_shipments(db, [shipment_id], data, created_by)

    @staticmethod
    def create_from_shipments(db: Session, shipment_ids: List[int], data, created_by: str) -> Invoice:
        """One invoice for one or more shipped shipments of the SAME order. Lines stay per
        order line per shipment, so every line still traces back to the shipment it left on."""
        ids = list(dict.fromkeys(shipment_ids))
        if not ids:
            raise HTTPException(status_code=400, detail="Pick at least one shipment")
        shipments = [ShipmentService.get(db, sid) for sid in ids]
        if len({s.order_id for s in shipments}) > 1:
            raise HTTPException(status_code=400, detail="Only shipments of the same order can be combined on one invoice")
        for shipment in shipments:
            if shipment.status not in ("shipped", "delivered", "invoiced"):
                raise HTTPException(status_code=400, detail=f"Shipment {shipment.code} is {shipment.status} -- only shipped shipments can be invoiced")
            live = InvoiceService.live_invoice_for_shipment(db, shipment.id)
            if live:
                raise HTTPException(status_code=400, detail=f"Shipment {shipment.code} is already on invoice {live.code}")
        shipments.sort(key=lambda s: (s.ship_date or s.created_at or datetime.min, s.id))
        order = db.query(CustomerOrder).filter(CustomerOrder.id == shipments[0].order_id).first()
        from app.services.terms import due_date_for
        today = clock.today()

        invoice = Invoice(
            code=generate_code(db, Invoice, "INV"),
            customer_id=order.customer_id,
            order_id=order.id,
            shipment_id=shipments[0].id,
            invoice_date=today,
            due_date=data.due_date or due_date_for(db.get(Customer, order.customer_id), today),  # the customer's terms
            free_text=data.free_text or None,
            status="draft",
            created_by=created_by,
        )
        db.add(invoice)
        db.flush()
        for shipment in shipments:
            db.add(InvoiceShipment(invoice_id=invoice.id, shipment_id=shipment.id))
            # One invoice line per order line: a line booked from several lots collapses into one,
            # but two order lines for the same item stay separate (they may differ in price).
            qty_by_line, order_lines = ShipmentService.quantities_by_order_line(shipment)
            for line_id in sorted(qty_by_line, key=lambda i: (order_lines[i].line_no or 0, i)):
                ol = order_lines[line_id]
                item = db.query(StockItem).filter(StockItem.id == ol.item_id).first()
                db.add(InvoiceLine(
                    invoice_id=invoice.id, item_id=ol.item_id, order_line_id=ol.id, shipment_id=shipment.id,
                    description=item.title if item else f"Item {ol.item_id}",
                    quantity=qty_by_line[line_id], unit_price=ol.unit_price,
                    notes=ol.notes, print_notes=ol.print_notes is not False,
                ))
            shipment.status = "invoiced"

        if data.shipping_charge and data.shipping_charge > 0:
            db.add(InvoiceLine(invoice_id=invoice.id, item_id=None, description="Shipping", quantity=1,
                               unit_price=data.shipping_charge))
        db.commit()
        db.refresh(invoice)
        return invoice

    @staticmethod
    def split(db: Session, invoice_id: int) -> List[Invoice]:
        """Undo a combine. Invoices that were folded in come back under their old number
        (or a new one if it has been reused since); an invoice created from several
        shipments at once is split into one invoice per shipment. The shipping line and
        any payments stay on this invoice. Returns the invoices that were split off."""
        import json
        invoice = InvoiceService.get(db, invoice_id)
        if invoice.status == "void":
            raise HTTPException(status_code=400, detail="This invoice is void")
        info = json.loads(invoice.combined_info) if invoice.combined_info else {}
        if len(invoice.shipments) < 2 and not info.get("merged"):
            raise HTTPException(status_code=400, detail=f"{invoice.code} isn't a combined invoice")
        if invoice.payments or invoice.funding_amount is not None:
            raise HTTPException(status_code=400, detail=f"{invoice.code} has payments or funding recorded -- remove those before splitting it")
        groups = [dict(g) for g in info.get("merged", [])]
        if not groups:  # made combined in one go: every shipment after the first gets its own invoice
            groups = [{"code": None, "shipment_ids": [sh.id], "line_ids": None, "due_date": None, "free_text": None}
                      for sh in invoice.shipments[1:]]
        created = []
        for g in groups:
            code = g["code"] if g["code"] and not db.query(Invoice).filter(Invoice.code == g["code"]).first() else generate_code(db, Invoice, "INV")
            new = Invoice(code=code, customer_id=invoice.customer_id, order_id=invoice.order_id,
                          shipment_id=g["shipment_ids"][0] if g["shipment_ids"] else None,
                          due_date=datetime.fromisoformat(g["due_date"]) if g["due_date"] else invoice.due_date,
                          free_text=g["free_text"] if g["code"] else invoice.free_text,
                          status="draft" if invoice.status == "draft" else "sent",
                          print_zero_lines=invoice.print_zero_lines, print_payments=invoice.print_payments, created_by=invoice.created_by)
            db.add(new)
            db.flush()
            for line in list(invoice.lines):
                belongs = (line.id in g["line_ids"]) if g["line_ids"] is not None else (line.shipment_id in g["shipment_ids"])
                if belongs and not (line.item_id is None and line.description == "Shipping"):
                    invoice.lines.remove(line)
                    new.lines.append(line)
            for sh in [x for x in invoice.shipments if x.id in g["shipment_ids"]]:
                new.shipments.append(sh)
                if not any(l.shipment_id == sh.id for l in invoice.lines):  # still billed here too when lines were split
                    invoice.shipments.remove(sh)
            created.append(new)
        db.flush()
        db.refresh(invoice)
        remaining = [sh.id for sh in invoice.shipments]
        invoice.shipment_id = remaining[0] if remaining else invoice.shipment_id
        invoice.combined_info = None
        db.commit()
        for new in created:
            db.refresh(new)
        return created

    @staticmethod
    def merge(db: Session, target_id: int, other_ids: List[int]) -> Invoice:
        """Fold other DRAFT invoices of the same order into this draft: their lines and
        shipments move here and the emptied drafts are deleted. Anything that has gone out
        (sent/paid), has payments, emails or funding can't be merged -- void it instead."""
        target = InvoiceService.get(db, target_id)
        others = [InvoiceService.get(db, i) for i in dict.fromkeys(other_ids) if i != target_id]
        if not others:
            raise HTTPException(status_code=400, detail="Pick at least one other invoice to combine")
        for inv in [target] + others:
            if inv.status != "draft":
                raise HTTPException(status_code=400, detail=f"{inv.code} is {inv.status} -- only draft invoices can be combined")
            if inv.payments or inv.emails or inv.funding_amount is not None:
                raise HTTPException(status_code=400, detail=f"{inv.code} already has payments, emails or funding -- it can't be combined")
            if inv.order_id != target.order_id or not inv.order_id:
                raise HTTPException(status_code=400, detail=f"{inv.code} is for a different order -- only invoices of the same order can be combined")
            if inv.split_from_id == target.id:
                inv.split_from_id = None  # combining a split-off draft back into where it came from
        import json
        info = json.loads(target.combined_info) if target.combined_info else {}
        merged = info.setdefault("merged", [])
        for inv in others:
            # Remember the draft as it was, so the combine can be undone.
            merged.append({
                "code": inv.code, "shipment_ids": [x.id for x in inv.shipments],
                "line_ids": [l.id for l in inv.lines],
                "due_date": inv.due_date.isoformat() if inv.due_date else None, "free_text": inv.free_text,
            })
        target.combined_info = json.dumps(info)
        for inv in others:
            for line in list(inv.lines):
                if line.item_id is None and line.description == "Shipping":
                    existing = next((l for l in target.lines if l.item_id is None and l.description == "Shipping"), None)
                    if existing:  # one Shipping line, charges added together
                        from app.services.money import line_amount
                        existing.unit_price = line_amount(existing.quantity, existing.unit_price) + line_amount(line.quantity, line.unit_price)
                        existing.quantity = 1
                        inv.lines.remove(line)
                        continue
                # Move through the relationship: deleting the emptied draft cascades to
                # whatever is still in its lines collection.
                inv.lines.remove(line)
                target.lines.append(line)
            # Move the shipments through the relationship; deleting the draft also deletes
            # whatever links its (loaded) shipments collection still holds.
            moved = list(inv.shipments)
            inv.shipments = []
            db.flush()
            for shipment in moved:
                if shipment not in target.shipments:  # a split-off draft bills a shipment the target already has
                    target.shipments.append(shipment)
            for part in list(inv.split_parts):  # drafts split off the one going away now hang off the target
                part.split_from_id = target.id
            db.flush()
            db.info["no_bin"] = True  # merging drafts isn't a delete anyone would want to undo
            db.delete(inv)
            db.flush()
            db.info.pop("no_bin", None)
        db.commit()
        db.refresh(target)
        return target

    @staticmethod
    def rename(db: Session, invoice_id: int, code: str) -> Invoice:
        invoice = InvoiceService.get(db, invoice_id)
        invoice.code = _new_code(db, Invoice, invoice, code, "invoice")
        db.commit()
        db.refresh(invoice)
        return invoice

    @staticmethod
    def update(db: Session, invoice_id: int, data, by: str = None) -> Invoice:
        """Edit line items / free text / due date while still in draft. Billing a different quantity than the
        shipments delivered needs accept_qty_differences; the accepted difference is kept against the order."""
        from app.services import billing
        invoice = InvoiceService.get(db, invoice_id)
        if invoice.status == "void":
            raise HTTPException(status_code=400, detail="This invoice is void -- it can't be edited")
        diffs = billing.invoice_differences(db, invoice, [l.model_dump() for l in data.lines]) if data.lines is not None else None
        if diffs and not getattr(data, "accept_qty_differences", False):
            raise HTTPException(status_code=400, detail="Billing differs from what was delivered: " + "; ".join(
                f"#{d['line_no']} {d['item_code']} delivered {d['delivered']:g}, billing {d['billed']:g}"
                + (f" here + {d['elsewhere']:g} on {', '.join(d['elsewhere_codes'])}" if d.get("elsewhere") else "") for d in diffs)
                + " -- accept the difference to save it")

        if data.due_date is not None:
            invoice.due_date = data.due_date
        if data.free_text is not None:
            invoice.free_text = data.free_text

        if data.lines is not None:
            db.query(InvoiceLine).filter(InvoiceLine.invoice_id == invoice.id).delete()
            for line in data.lines:
                db.add(InvoiceLine(
                    invoice_id=invoice.id,
                    item_id=line.item_id,
                    order_line_id=line.order_line_id,
                    shipment_id=line.shipment_id,
                    description=line.description,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    notes=(line.notes or "").strip() or None,
                    print_notes=line.print_notes is not False,
                ))
            billing.record_variances(db, invoice, diffs, by, getattr(data, "qty_note", None))

        # A sent/paid invoice can be corrected; its paid status follows the new total.
        db.flush()
        db.refresh(invoice)
        if invoice.balance < -0.005:
            db.rollback()
            raise HTTPException(status_code=400, detail=f"That makes the invoice {invoice.total:,.2f}, less than the {invoice.amount_paid:,.2f} "
                                                        "already paid -- remove a payment first")
        if invoice.status == "paid" and invoice.balance > 0.005:
            invoice.status = "sent"
        elif invoice.status == "sent" and invoice.payments and invoice.balance <= 0.005:
            invoice.status = "paid"
        db.commit()
        db.refresh(invoice)
        return invoice

    @staticmethod
    def set_status(db: Session, invoice_id: int, status: str, reason: Optional[str] = None, by: Optional[str] = None) -> Invoice:
        if status not in ("sent", "paid", "void"):
            raise HTTPException(status_code=400, detail="status must be sent, paid, or void")
        invoice = InvoiceService.get(db, invoice_id)
        if invoice.status == "void":
            # its shipments were released to be billed again -- a void invoice stays void
            raise HTTPException(status_code=400, detail=f"{invoice.code} is void -- make a new invoice for its shipments instead")
        if status == "void" and invoice.payments:
            raise HTTPException(status_code=400, detail="Invoice has payments recorded against it, cannot void")
        if status == "sent" and invoice.payments and invoice.balance <= 0.005:
            raise HTTPException(status_code=400, detail=f"{invoice.code} is paid in full -- remove a payment to reopen it")
        if status == "paid" and invoice.balance > 0.005:  # paid means the payments cover it -- record them first
            raise HTTPException(status_code=400, detail=f"{invoice.balance:,.2f} is still open -- record the payment and it's marked paid automatically")
        if status == "void":
            from app.services import billing
            billing.clear_variances(db, invoice)  # a void invoice bills nothing
            invoice.voided_at, invoice.voided_by = datetime.utcnow(), by
            if reason and reason.strip():
                invoice.void_reason = reason.strip()
            # Its shipments become billable again (they can go on a new or combined invoice).
            InvoiceService.release_shipments(db, invoice)
        invoice.status = status
        db.commit()
        db.refresh(invoice)
        return invoice


def _invoice_delete_blocker(invoice) -> Optional[str]:
    """Why this invoice can't be deleted yet, or None. Void ones can; so can drafts that never went out."""
    if invoice.payments:
        return f"{invoice.code} has {len(invoice.payments)} payment(s) recorded -- remove them first"
    if invoice.funding_amount or invoice.disbursement_date:
        return f"{invoice.code} has invoice funding recorded -- clear it first"
    if invoice.status == "void":
        return None
    if invoice.status == "draft" and not invoice.emails:
        return None
    return f"{invoice.code} was sent to the customer -- void it first (that keeps a record that it was cancelled)"


def delete_invoice(db: Session, invoice_id: int, commit: bool = True) -> None:
    """Delete a void invoice, or a draft that never went out. Its shipments become billable again. Row by row
    (not bulk), so the Recycle Bin keeps the whole invoice -- lines, links, email log -- and can restore it."""
    from app.models import BillingVariance, InvoiceEmail, InvoicePayment
    invoice = InvoiceService.get(db, invoice_id)
    why = _invoice_delete_blocker(invoice)
    if why:
        raise HTTPException(status_code=400, detail=why)
    InvoiceService.release_shipments(db, invoice)
    for part in list(invoice.split_parts):  # invoices split off this one stay; they just lose the link
        part.split_from_id = None
    for model in (InvoiceShipment, InvoiceLine, BillingVariance, InvoiceEmail):
        for row in db.query(model).filter(model.invoice_id == invoice.id).all():
            db.delete(row)
    _remove_attachments(db, "invoice", invoice.id)
    # all in one flush, so the Recycle Bin entry holds the invoice with its lines and links; the links are deleted
    # as rows above, so the invoice's shipments collection is marked empty (no second delete of the same rows)
    from sqlalchemy.orm.attributes import set_committed_value
    set_committed_value(invoice, "shipments", [])
    db.delete(invoice)
    if commit:
        db.commit()


class InvoicePaymentService:
    @staticmethod
    def remove(db: Session, invoice_id: int, payment_id: int) -> Invoice:
        invoice = InvoiceService.get(db, invoice_id)
        p = db.query(InvoicePayment).filter(InvoicePayment.id == payment_id, InvoicePayment.invoice_id == invoice.id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Payment not found on this invoice")
        db.delete(p)
        db.flush()
        db.refresh(invoice)
        if invoice.status == "paid" and invoice.balance > 0.005:
            invoice.status = "sent"
        db.commit()
        db.refresh(invoice)
        return invoice

    @staticmethod
    def record(db: Session, invoice_id: int, data, created_by: str) -> Invoice:
        invoice = InvoiceService.get(db, invoice_id)
        if invoice.status != "sent":
            raise HTTPException(status_code=400, detail=f"Invoice is {invoice.status} -- payments can only be recorded against a sent invoice")
        data.amount = cents(data.amount)
        if data.amount <= 0:
            raise HTTPException(status_code=400, detail="Payment amount must be at least $0.01")
        if data.amount > invoice.balance + 0.005:
            raise HTTPException(status_code=400, detail=f"Payment of {data.amount:.2f} exceeds the open balance of {invoice.balance:.2f}")
        db.add(InvoicePayment(
            invoice_id=invoice.id,
            amount=data.amount,
            paid_date=clock.calendar_from_input(data.paid_date) or clock.today(),
            method=payment_method(db, data.method),
            reference=data.reference,
            note=data.note,
            created_by=created_by,
        ))
        db.flush()
        db.refresh(invoice)
        if invoice.balance <= 0.005:
            invoice.status = "paid"
        db.commit()
        db.refresh(invoice)
        return invoice


# ---- Purchase Orders ----
class VendorItemService:
    """Vendor part # <-> our item. One vendor code maps to one of our items; one of our
    items can carry a different code at every vendor."""

    @staticmethod
    def list(db: Session, vendor_id: Optional[int] = None, item_id: Optional[int] = None) -> List[VendorItem]:
        q = db.query(VendorItem)
        if vendor_id:
            q = q.filter(VendorItem.vendor_id == vendor_id)
        if item_id:
            q = q.filter(VendorItem.item_id == item_id)
        return q.order_by(VendorItem.vendor_item_code).all()

    @staticmethod
    def find(db: Session, vendor_id: int, code: str) -> Optional[VendorItem]:
        code = (code or "").strip()
        if not code:
            return None
        return db.query(VendorItem).filter(VendorItem.vendor_id == vendor_id,
                                           func.lower(VendorItem.vendor_item_code) == code.lower()).first()

    @staticmethod
    def for_item(db: Session, vendor_id: int, item_id: int) -> Optional[VendorItem]:
        return (db.query(VendorItem).filter(VendorItem.vendor_id == vendor_id, VendorItem.item_id == item_id)
                .order_by(VendorItem.last_ordered_at.desc().nullslast(), VendorItem.id.desc()).first())

    @staticmethod
    def upsert(db: Session, vendor_id: int, item_id: int, code: str, description: Optional[str] = None,
               unit_cost: Optional[float] = None, ordered: bool = False) -> VendorItem:
        code = (code or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="Vendor item # is required")
        if not db.query(StockItem).filter(StockItem.id == item_id).first():
            raise HTTPException(status_code=400, detail="Stock item not found")
        mapping = VendorItemService.find(db, vendor_id, code)
        if mapping and mapping.item_id != item_id:
            ours = db.query(StockItem).filter(StockItem.id == mapping.item_id).first()
            raise HTTPException(status_code=400,
                                detail=f"Vendor item # {code} is already mapped to our {ours.code if ours else mapping.item_id}")
        if not mapping:
            mapping = VendorItem(vendor_id=vendor_id, item_id=item_id, vendor_item_code=code)
            db.add(mapping)
        if description:
            mapping.vendor_description = description
        if unit_cost is not None:
            mapping.last_unit_cost = unit_cost
        if ordered:
            mapping.last_ordered_at = datetime.utcnow()
        db.flush()
        return mapping

    @staticmethod
    def delete(db: Session, vendor_id: int, mapping_id: int) -> None:
        mapping = db.query(VendorItem).filter(VendorItem.id == mapping_id, VendorItem.vendor_id == vendor_id).first()
        if not mapping:
            raise HTTPException(status_code=404, detail="Cross-reference not found")
        db.delete(mapping)
        db.commit()

    @staticmethod
    def resolve_line(db: Session, vendor_id: int, line) -> tuple:
        """(item_id, vendor_item_code, vendor_description) for a PO line being added: a vendor
        item # alone picks our item; our item alone picks up its known vendor #; both together
        teach the cross-reference."""
        code = (line.vendor_item_code or "").strip() or None
        item_id = line.item_id
        desc = (line.vendor_description or "").strip() or None
        if code:
            mapping = VendorItemService.find(db, vendor_id, code)
            if not item_id:
                if not mapping:
                    raise HTTPException(status_code=400, detail=f"Vendor item # {code} isn't mapped to any of our items yet -- pick the item once and it will be remembered")
                item_id = mapping.item_id
            if mapping and mapping.item_id == item_id:
                code = mapping.vendor_item_code  # keep the vendor's own spelling, not what was typed
            desc = desc or (mapping.vendor_description if mapping else None)
        elif item_id:
            mapping = VendorItemService.for_item(db, vendor_id, item_id)
            if mapping:
                code, desc = mapping.vendor_item_code, desc or mapping.vendor_description
        if not item_id:
            raise HTTPException(status_code=400, detail="Pick an item or enter a known vendor item #")
        if not db.query(StockItem).filter(StockItem.id == item_id).first():
            raise HTTPException(status_code=400, detail=f"Stock item {item_id} not found")
        return item_id, code, desc

    @staticmethod
    def learn(db: Session, vendor_id: int, item_id: int, code: Optional[str], desc: Optional[str], unit_cost: float) -> None:
        from app.services import item_alias
        item_alias.learn(db, "vendor", vendor_id, item_id, code, desc)  # descriptions too: some vendors print no part #
        if code:
            VendorItemService.upsert(db, vendor_id, item_id, code, desc, unit_cost, ordered=True)


def _check_po_line(quantity, unit_cost, where: str = "") -> None:
    if quantity is not None and quantity <= 0:
        raise HTTPException(status_code=400, detail=f"{where}Quantity must be greater than 0")
    if unit_cost is not None and unit_cost < 0:
        raise HTTPException(status_code=400, detail=f"{where}Unit cost can't be negative")


class PurchaseOrderService:
    @staticmethod
    def list(db: Session, status: Optional[str] = None) -> List[PurchaseOrder]:
        # everything the list shows, in a few queries rather than a few per PO
        query = db.query(PurchaseOrder).options(
            selectinload(PurchaseOrder.lines).selectinload(PurchaseOrderLine.allocations),
            selectinload(PurchaseOrder.payments), selectinload(PurchaseOrder.emails), selectinload(PurchaseOrder.charges),
            selectinload(PurchaseOrder.bills).selectinload(VendorBill.payments))
        if status:
            query = query.filter(PurchaseOrder.status == status)
        return query.order_by(PurchaseOrder.id.desc()).all()

    @staticmethod
    def get(db: Session, po_id: int) -> PurchaseOrder:
        po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")
        return po

    @staticmethod
    def create(db: Session, data, created_by: str) -> PurchaseOrder:
        if not db.query(Vendor).filter(Vendor.id == data.vendor_id).first():
            raise HTTPException(status_code=400, detail="Vendor not found")
        if not data.lines:
            raise HTTPException(status_code=400, detail="Order must have at least one line")

        po = PurchaseOrder(
            code=generate_code(db, PurchaseOrder, "PO"),
            vendor_id=data.vendor_id,
            expected_date=data.expected_date,
            vendor_so_number=(data.vendor_so_number or "").strip() or None,
            notes=data.notes,
            status="draft",
            created_by=created_by,
        )
        db.add(po)
        db.flush()

        for pos, line in enumerate(data.lines):
            _check_po_line(line.quantity, line.unit_cost, f"Line #{pos + 1}: ")
            item_id, code, desc = VendorItemService.resolve_line(db, po.vendor_id, line)
            db.add(PurchaseOrderLine(
                po_id=po.id,
                position=pos,
                item_id=item_id,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
                vendor_item_code=code,
                vendor_description=desc,
                notes=(line.notes or "").strip() or None, print_notes=line.print_notes is not False,
            ))
            VendorItemService.learn(db, po.vendor_id, item_id, code, desc, line.unit_cost)

        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def capture(db: Session, vendor_id: int, vendor_so_number: Optional[str], created_by: str) -> PurchaseOrder:
        """Quick capture: a vendor's document kept as a PO with no lines yet, status "validation". It can't be marked
        ordered, emailed or received until someone checks it, fills the lines in and validates it."""
        if not db.query(Vendor).filter(Vendor.id == vendor_id).first():
            raise HTTPException(status_code=400, detail="Vendor not found")
        po = PurchaseOrder(code=generate_code(db, PurchaseOrder, "PO"), vendor_id=vendor_id,
                           vendor_so_number=(vendor_so_number or "").strip() or None, status="validation", created_by=created_by)
        db.add(po)
        db.flush()
        return po

    @staticmethod
    def validate(db: Session, po_id: int, by: str, ordered: bool = False) -> PurchaseOrder:
        """A captured PO checked: on to Draft (or straight to Ordered). Needs its lines."""
        po = PurchaseOrderService.get(db, po_id)
        if po.status != "validation":
            raise HTTPException(status_code=400, detail=f"{po.code} is {po.status} -- only a captured PO needs validating")
        if po.ai_pending_lines:
            raise HTTPException(status_code=400, detail=f"{len(po.ai_pending_lines)} line(s) from the vendor's document still need an item -- "
                                                        "pick one for each (or drop it) first")
        if not po.lines:
            raise HTTPException(status_code=400, detail="Add the PO's lines from the vendor's document first")
        po.status = "ordered" if ordered else "draft"
        po.validated_by, po.validated_at = by, datetime.utcnow()
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def mark_ordered(db: Session, po_id: int) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status == "validation":
            raise HTTPException(status_code=400, detail=f"{po.code} was quick-captured -- check it and Validate it first")
        if po.status != "draft":
            raise HTTPException(status_code=400, detail=f"Order is already {po.status}")
        po.status = "ordered"
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def update(db: Session, po_id: int, data) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status == "cancelled":
            raise HTTPException(status_code=400, detail="Cannot edit a cancelled order")
        updates = data.dict(exclude_unset=True)
        if "vendor_id" in updates and updates["vendor_id"] is not None:
            if not db.query(Vendor).filter(Vendor.id == updates["vendor_id"]).first():
                raise HTTPException(status_code=400, detail="Vendor not found")
        set_fields(po, updates)
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def add_line(db: Session, po_id: int, data) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status == "cancelled":
            raise HTTPException(status_code=400, detail="Order is cancelled")
        _check_po_line(data.quantity, data.unit_cost)
        item_id, code, desc = VendorItemService.resolve_line(db, po.vendor_id, data)
        db.add(PurchaseOrderLine(po_id=po.id, item_id=item_id, quantity=data.quantity, unit_cost=data.unit_cost,
                                 vendor_item_code=code, vendor_description=desc,
                                 notes=(data.notes or "").strip() or None, print_notes=data.print_notes is not False,
                                 position=max((l.position if l.position is not None else i for i, l in enumerate(po.lines)), default=-1) + 1))
        VendorItemService.learn(db, po.vendor_id, item_id, code, desc, data.unit_cost)
        db.flush()
        db.refresh(po)
        PurchaseOrderService.refresh_status(po)
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def update_line(db: Session, po_id: int, line_id: int, data) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status == "cancelled":
            raise HTTPException(status_code=400, detail="Order is cancelled")
        line = db.query(PurchaseOrderLine).filter(
            PurchaseOrderLine.id == line_id, PurchaseOrderLine.po_id == po.id
        ).first()
        if not line:
            raise HTTPException(status_code=404, detail="PO line not found")

        updates = data.dict(exclude_unset=True)
        if updates.get("item_id") is not None and updates["item_id"] != line.item_id:
            if line.received_quantity > 1e-9 or db.query(Lot).filter(Lot.po_line_id == line.id).first():
                raise HTTPException(status_code=400, detail="Stock was received on this line -- its item can't be changed. Add a new line instead.")
            new_item = db.query(StockItem).filter(StockItem.id == updates["item_id"]).first()
            if not new_item:
                raise HTTPException(status_code=400, detail="Item not found")
            if new_item.is_active is False:
                raise HTTPException(status_code=400, detail=f"{new_item.code} is archived")
        else:
            updates.pop("item_id", None)
        _check_po_line(updates.get("quantity"), updates.get("unit_cost"))
        if "quantity" in updates and updates["quantity"] is not None:
            if updates["quantity"] < line.received_quantity - 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot reduce quantity below {line.received_quantity}, which has already been received"
                )
        cost_changed = "unit_cost" in updates and updates["unit_cost"] is not None and abs(updates["unit_cost"] - line.unit_cost) > 1e-9
        for key in ("vendor_item_code", "vendor_description", "notes"):
            if key in updates:
                updates[key] = (updates[key] or "").strip() or None
        if updates.get("print_notes", False) is None:
            updates.pop("print_notes")
        set_fields(line, updates)
        VendorItemService.learn(db, po.vendor_id, line.item_id, line.vendor_item_code, line.vendor_description, line.unit_cost)
        if cost_changed:
            # A corrected PO price flows into lots already received on this line.
            for lot in db.query(Lot).filter(Lot.po_line_id == line.id).all():
                lot.base_unit_cost = line.unit_cost
            recompute_lot_costs(db, [line.id])
        PurchaseOrderService.refresh_status(po)
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def refresh_status(po) -> None:
        """Lines can be added or changed after receipt, so re-derive received/partial/ordered."""
        if po.status in ("validation", "draft", "cancelled"):
            return
        from app.services import vendor_shipments
        vendor_shipments.settle(po)  # vendor shipments whose goods are in complete themselves
        goods = [l for l in po.lines if not getattr(l, "is_charge", False)]
        if goods and all(l.received_quantity >= l.quantity - 1e-9 for l in goods):
            po.status = "received"
        elif any(l.received_quantity > 0 for l in goods):
            po.status = "partially_received"
        elif vendor_shipments.in_transit(po):
            po.status = "shipped"
        else:
            po.status = "ordered"

    @staticmethod
    def remove_line(db: Session, po_id: int, line_id: int) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status == "cancelled":
            raise HTTPException(status_code=400, detail="Order is cancelled")
        line = db.query(PurchaseOrderLine).filter(
            PurchaseOrderLine.id == line_id, PurchaseOrderLine.po_id == po.id
        ).first()
        if not line:
            raise HTTPException(status_code=404, detail="PO line not found")
        if line.received_quantity > 0:
            raise HTTPException(status_code=400, detail="Cannot remove a line that has already been received")
        if line.allocations:
            codes = ", ".join(sorted({a.landed_cost.code for a in line.allocations}))
            raise HTTPException(status_code=400, detail=f"This line carries landed cost {codes} -- edit or delete that landed cost first")
        if len(po.lines) <= 1:
            raise HTTPException(status_code=400, detail="Order must have at least one line")
        from app.models import VendorShipmentLine
        db.query(VendorShipmentLine).filter(VendorShipmentLine.po_line_id == line.id).delete()  # off any vendor shipment too
        db.delete(line)
        db.flush()
        db.refresh(po)
        renumber_lines(po.lines)
        PurchaseOrderService.refresh_status(po)
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def cancel(db: Session, po_id: int) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if any(l.received_quantity > 0 for l in po.lines):
            raise HTTPException(status_code=400, detail="Cannot cancel an order that has already received stock")
        lc_codes = sorted({a.landed_cost.code for l in po.lines for a in l.allocations})
        if lc_codes:
            raise HTTPException(status_code=400, detail=f"Landed cost {', '.join(lc_codes)} is applied to this order -- remove it from that landed cost first")
        po.status = "cancelled"
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def delete(db: Session, po_id: int) -> None:
        """Delete a cancelled PO for good, with its attached files. Not when stock was received,
        or a bill, payment or landed cost is recorded against it."""
        po = PurchaseOrderService.get(db, po_id)
        if po.status != "cancelled":
            raise HTTPException(status_code=400, detail="Cancel the PO first -- only cancelled POs can be deleted")
        line_ids = [l.id for l in po.lines]
        if any(l.received_quantity > 0 for l in po.lines) or (line_ids and db.query(Lot).filter(Lot.po_line_id.in_(line_ids)).first()):
            raise HTTPException(status_code=400, detail="Stock was received on this PO, so it can't be deleted")
        if po.bills or po.payments:
            raise HTTPException(status_code=400, detail="Vendor invoices or payments are recorded on this PO -- delete those first")
        if any(l.allocations for l in po.lines):
            raise HTTPException(status_code=400, detail="A landed cost is applied to this PO, so it can't be deleted")
        _remove_attachments(db, "purchase_order", po.id)
        db.flush()
        db.delete(po)
        db.commit()

    @staticmethod
    def receive(db: Session, po_id: int, data, created_by: str) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status in ("received", "cancelled"):
            raise HTTPException(status_code=400, detail=f"Order is already {po.status}")
        if po.status == "validation":
            raise HTTPException(status_code=400, detail=f"{po.code} was quick-captured -- validate it before receiving stock on it")

        # Validate every line up front so a bad line doesn't leave a half-received PO.
        resolved = []
        for recv_line in data.lines:
            if recv_line.quantity <= 0:
                continue
            line = db.query(PurchaseOrderLine).filter(
                PurchaseOrderLine.id == recv_line.line_id,
                PurchaseOrderLine.po_id == po.id,
            ).first()
            if not line:
                raise HTTPException(status_code=400, detail=f"PO line {recv_line.line_id} not found on this order")
            if line.received_quantity + recv_line.quantity > line.quantity + 1e-9:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot receive {recv_line.quantity} on line {line.id}: only "
                           f"{line.quantity - line.received_quantity} remains unreceived"
                )
            resolved.append((recv_line, line))
        if not resolved:
            raise HTTPException(status_code=400, detail="Enter a quantity to receive on at least one line")

        # Every receipt becomes its own lot (LOT-##### unless the user typed the supplier's
        # lot code). Bookings draw from lots, so this is what ties a shipped unit back to
        # the receipt -- and the price -- it came from.
        touched_items = {}
        for recv_line, line in resolved:
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            landed = line_landed_per_unit(db, line.id)
            lot = Lot(
                item_id=item.id,
                lot_code=(recv_line.lot_code or "").strip() or _planned_lot(db, line) or next_lot_code(db),
                quantity=recv_line.quantity,
                initial_quantity=recv_line.quantity,
                base_unit_cost=line.unit_cost,
                unit_cost=round(line.unit_cost + landed, 6),
                po_line_id=line.id,
                received_date=datetime.utcnow(),
                expiry_date=recv_line.expiry_date,
                status="available",
                source="purchase",
                source_reference=po.code,
            )
            db.add(lot)
            db.flush()

            db.add(InventoryTransaction(
                item_id=item.id,
                lot_id=lot.id,
                quantity_delta=recv_line.quantity,
                type="receipt",
                reference=po.code,
                note=f"Lot {lot.lot_code}: unit cost {line.unit_cost:g} + {landed:.5f} landed" if landed else f"Lot {lot.lot_code}",
                created_by=created_by,
            ))
            item.on_hand += recv_line.quantity
            line.received_quantity += recv_line.quantity
            touched_items[item.id] = item

        db.flush()
        for item in touched_items.values():
            refresh_item_cost(db, item)

        # Older clients may still send freight/tariff with the receipt -- turn each into a
        # landed cost on this PO so it's allocated the same way (by quantity) as any other.
        for amount, cost_type in ((data.freight_cost, "freight"), (data.tariff_cost, "tariff")):
            if amount and amount > 0:
                LandedCostService.create_internal(
                    db, description=f"{cost_type.title()} on {po.code} receipt", cost_type=cost_type,
                    amount=amount, po_ids=[po.id], created_by=created_by,
                )

        PurchaseOrderService.refresh_status(po)
        db.commit()
        db.refresh(po)
        return po


def po_left_to_pay(po: PurchaseOrder) -> float:
    """What's still owed on a PO: its total, or what its vendor invoices add up to if they billed more."""
    return cents(max(po.order_total, sum(b.amount for b in po.bills)) - po.amount_paid)


class PurchaseOrderPaymentService:
    @staticmethod
    def record(db: Session, po_id: int, data, created_by: str) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        data.amount = cents(data.amount)
        if data.amount <= 0:
            raise HTTPException(status_code=400, detail="Payment amount must be greater than 0")
        left = po_left_to_pay(po)
        if data.amount > left + 0.005:
            raise HTTPException(status_code=400, detail=f"{po.code} only has {left:,.2f} left to pay")
        if data.vendor_bill_id:
            bill = db.query(VendorBill).filter(VendorBill.id == data.vendor_bill_id, VendorBill.po_id == po.id).first()
            if not bill:
                raise HTTPException(status_code=400, detail="That vendor invoice isn't on this purchase order")
            if data.amount > bill.balance + 0.005:
                raise HTTPException(status_code=400, detail=f"Vendor invoice {bill.bill_number} only has {bill.balance:,.2f} left to pay")
        db.add(PurchaseOrderPayment(
            vendor_bill_id=data.vendor_bill_id,
            po_id=po.id,
            amount=data.amount,
            currency=data.currency,
            paid_date=data.paid_date,
            method=payment_method(db, data.method),
            reference=data.reference,
            note=data.note,
            created_by=created_by,
        ))
        db.commit()
        db.refresh(po)
        return po


class VendorBillService:
    @staticmethod
    def create(db: Session, po_id: int, data, created_by: str) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        number = (data.bill_number or "").strip()
        if not number:
            raise HTTPException(status_code=400, detail="Enter the vendor's invoice #")
        data.amount = cents(data.amount)
        if data.amount <= 0:
            raise HTTPException(status_code=400, detail="Invoice amount must be greater than 0")
        if any(b.bill_number.lower() == number.lower() for b in po.bills):
            raise HTTPException(status_code=400, detail=f"Vendor invoice {number} is already recorded on {po.code}")
        # the same vendor invoice # on another of this vendor's POs is usually the same bill entered twice
        if not getattr(data, "allow_duplicate", False):
            other = (db.query(VendorBill, PurchaseOrder).join(PurchaseOrder, PurchaseOrder.id == VendorBill.po_id)
                     .filter(PurchaseOrder.vendor_id == po.vendor_id, PurchaseOrder.id != po.id,
                             func.lower(VendorBill.bill_number) == number.lower()).first())
            if other:
                raise HTTPException(status_code=409, detail=f"Vendor invoice {number} is already recorded on {other[1].code} "
                                                            f"(same vendor, {other[0].amount:,.2f}) -- record it here too only if it really covers both POs")
        shipping = cents(data.shipping_amount)
        if shipping < 0 or shipping > data.amount + 0.005:
            raise HTTPException(status_code=400, detail="S&H on the invoice must be between 0 and the invoice amount")
        bill = VendorBill(po_id=po.id, bill_number=number, bill_date=clock.calendar_from_input(data.bill_date) or clock.today(),
                          due_date=data.due_date, amount=cents(data.amount), note=data.note,
                          attachment_id=data.attachment_id, created_by=created_by)
        db.add(bill)
        db.flush()
        if shipping > 0:
            # The shipping billed on this invoice becomes a PO charge, so the PO total grows to match what's billed.
            db.add(PurchaseOrderCharge(po_id=po.id, charge_type=_charge_type(db, data.shipping_type), amount=shipping,
                                       description=f"Billed on invoice {number}", vendor_bill_id=bill.id, created_by=created_by))
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def delete(db: Session, po_id: int, bill_id: int) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        bill = db.query(VendorBill).filter(VendorBill.id == bill_id, VendorBill.po_id == po.id).first()
        if not bill:
            raise HTTPException(status_code=404, detail="Vendor invoice not found")
        if bill.payments:
            raise HTTPException(status_code=400, detail=f"Payments are recorded against {bill.bill_number} -- it can't be deleted")
        for charge in [c for c in po.charges if c.vendor_bill_id == bill.id]:
            po.charges.remove(charge)
        db.delete(bill)
        db.commit()
        db.refresh(po)
        return po




def _charge_type(db: Session, value: Optional[str]) -> str:
    """An S&H type from Company Settings -> Types & Tags (shipping, freight, handling, other + any added there)."""
    from app.services import type_lists
    value = (value or "shipping").strip().lower()
    allowed = type_lists.keys(db, "charge")
    if value not in allowed:
        raise HTTPException(status_code=400, detail=f"Charge type must be one of: {', '.join(sorted(allowed))}")
    return value


def payment_method(db: Session, value: Optional[str]) -> Optional[str]:
    """A payment method from Types & Tags, by key or name ('ach', 'ACH', 'Credit card'); blank -> none. Anything else is
    refused, so the list doesn't sprawl -- add a new method in Company Settings first."""
    from app.services import type_lists
    v = (value or "").strip()
    if not v:
        return None
    norm = type_lists.normalize_label(v).lower()
    for r in type_lists.options(db, "payment_method", include_inactive=True):
        if v.lower() in (r.key, r.label.lower()) or norm == r.label.lower():
            return r.key
    raise HTTPException(status_code=400, detail=f"'{v}' isn't a payment method yet -- pick one from the list, or add it under Company Settings -> Types & Tags")


class PurchaseOrderChargeService:
    """Freight / shipping / handling on a PO, optionally tied to the vendor invoice it was billed on."""

    @staticmethod
    def add(db: Session, po_id: int, data, created_by: str) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        if po.status == "cancelled":
            raise HTTPException(status_code=400, detail="This purchase order is cancelled")
        data.amount = cents(data.amount)
        if data.amount <= 0:
            raise HTTPException(status_code=400, detail="Charge amount must be greater than 0")
        if data.vendor_bill_id and not any(b.id == data.vendor_bill_id for b in po.bills):
            raise HTTPException(status_code=400, detail="That vendor invoice isn't on this purchase order")
        db.add(PurchaseOrderCharge(po_id=po.id, charge_type=_charge_type(db, data.charge_type), amount=cents(data.amount),
                                   description=(data.description or "").strip() or None, vendor_bill_id=data.vendor_bill_id,
                                   created_by=created_by))
        db.commit()
        db.refresh(po)
        return po

    @staticmethod
    def remove(db: Session, po_id: int, charge_id: int) -> PurchaseOrder:
        po = PurchaseOrderService.get(db, po_id)
        charge = next((c for c in po.charges if c.id == charge_id), None)
        if not charge:
            raise HTTPException(status_code=404, detail="Charge not found")
        if charge.vendor_bill_id:
            raise HTTPException(status_code=400, detail=f"This S&H comes from vendor invoice {charge.bill_number} -- "
                                                        "delete or correct that invoice instead")
        po.charges.remove(charge)
        db.commit()
        db.refresh(po)
        return po


class VendorPaymentService:
    """Payments to a vendor recorded on their own (often before the PO exists), then applied
    to one or more POs. Every application is a normal PO payment linked back here."""

    @staticmethod
    def list(db: Session, vendor_id: Optional[int] = None, open_only: bool = False) -> List[VendorPayment]:
        q = db.query(VendorPayment)
        if vendor_id:
            q = q.filter(VendorPayment.vendor_id == vendor_id)
        rows = q.order_by(VendorPayment.id.desc()).all()
        return [r for r in rows if r.unapplied > 0.005] if open_only else rows

    @staticmethod
    def get(db: Session, payment_id: int) -> VendorPayment:
        vp = db.query(VendorPayment).filter(VendorPayment.id == payment_id).first()
        if not vp:
            raise HTTPException(status_code=404, detail="Vendor payment not found")
        return vp

    @staticmethod
    def create(db: Session, data, created_by: str) -> VendorPayment:
        if not db.query(Vendor).filter(Vendor.id == data.vendor_id).first():
            raise HTTPException(status_code=400, detail="Vendor not found")
        data.amount = cents(data.amount)
        if data.amount <= 0:
            raise HTTPException(status_code=400, detail="Payment amount must be greater than 0")
        vp = VendorPayment(code=generate_code(db, VendorPayment, "VP"), vendor_id=data.vendor_id, amount=cents(data.amount),
                           paid_date=clock.calendar_from_input(data.paid_date) or clock.today(), method=payment_method(db, data.method), reference=data.reference,
                           note=data.note, created_by=created_by)
        db.add(vp)
        db.commit()
        db.refresh(vp)
        return vp

    @staticmethod
    def apply(db: Session, payment_id: int, data, created_by: str) -> VendorPayment:
        vp = VendorPaymentService.get(db, payment_id)
        po = PurchaseOrderService.get(db, data.po_id)
        if po.vendor_id != vp.vendor_id:
            raise HTTPException(status_code=400, detail=f"{vp.code} was paid to a different vendor than {po.code}")
        if po.status == "cancelled":
            raise HTTPException(status_code=400, detail=f"{po.code} is cancelled")
        amount = cents(data.amount)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Amount to apply must be greater than 0")
        if amount > vp.unapplied + 0.005:
            raise HTTPException(status_code=400, detail=f"Only {vp.unapplied:,.2f} of {vp.code} is left to apply")
        po_balance = po_left_to_pay(po)
        if amount > po_balance + 0.005:
            raise HTTPException(status_code=400, detail=f"{po.code} only has {po_balance:,.2f} left to pay")
        if data.vendor_bill_id:
            bill = next((b for b in po.bills if b.id == data.vendor_bill_id), None)
            if not bill:
                raise HTTPException(status_code=400, detail="That vendor invoice isn't on this purchase order")
            if amount > bill.balance + 0.005:
                raise HTTPException(status_code=400, detail=f"Vendor invoice {bill.bill_number} only has {bill.balance:,.2f} left to pay")
        db.add(PurchaseOrderPayment(po_id=po.id, amount=amount, paid_date=vp.paid_date, method=vp.method,
                                    reference=vp.reference or vp.code, note=f"Applied from {vp.code}",
                                    vendor_bill_id=data.vendor_bill_id, vendor_payment_id=vp.id, created_by=created_by))
        db.commit()
        db.refresh(vp)
        return vp

    @staticmethod
    def unapply(db: Session, payment_id: int, po_payment_id: int) -> VendorPayment:
        vp = VendorPaymentService.get(db, payment_id)
        application = next((a for a in vp.applications if a.id == po_payment_id), None)
        if not application:
            raise HTTPException(status_code=404, detail="That application isn't part of this payment")
        db.delete(application)
        db.commit()
        db.refresh(vp)
        return vp

    @staticmethod
    def delete(db: Session, payment_id: int) -> None:
        vp = VendorPaymentService.get(db, payment_id)
        if vp.applications:
            raise HTTPException(status_code=400, detail=f"{vp.code} is applied to purchase orders -- unapply it first")
        db.delete(vp)
        db.commit()


def backfill_vendor_codes(db: Session) -> None:
    """Give every vendor created before vendor codes existed its V-#### code."""
    missing = db.query(Vendor).filter(Vendor.code.is_(None)).order_by(Vendor.id).all()
    for v in missing:
        v.code = generate_code(db, Vendor, "V")
        db.flush()
    if missing:
        db.commit()


# ---- Landed costs ----


class LandedCostService:
    @staticmethod
    def list(db: Session) -> List[LandedCost]:
        return db.query(LandedCost).order_by(LandedCost.id.desc()).all()

    @staticmethod
    def get(db: Session, lc_id: int) -> LandedCost:
        lc = db.query(LandedCost).filter(LandedCost.id == lc_id).first()
        if not lc:
            raise HTTPException(status_code=404, detail="Landed cost not found")
        return lc

    @staticmethod
    def plan(db: Session, amount: float, po_ids: List[int]) -> List[dict]:
        """Split amount over every line of the selected POs in proportion to line quantity,
        so each unit across the selection carries the same landed cost per unit."""
        amount = cents(amount) if amount is not None else None
        if amount is None or amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than 0")
        po_ids = list(dict.fromkeys(po_ids or []))
        if not po_ids:
            raise HTTPException(status_code=400, detail="Select at least one purchase order to apply this cost to")
        pos = db.query(PurchaseOrder).filter(PurchaseOrder.id.in_(po_ids)).all()
        if len(pos) != len(po_ids):
            raise HTTPException(status_code=400, detail="One or more selected purchase orders were not found")
        cancelled = [po.code for po in pos if po.status == "cancelled"]
        if cancelled:
            raise HTTPException(status_code=400, detail=f"Cannot apply landed costs to cancelled order {', '.join(cancelled)}")
        lines = [l for po in sorted(pos, key=lambda p: p.id) for l in po.lines if l.quantity > 0]
        total_qty = sum(l.quantity for l in lines)
        if total_qty <= 0:
            raise HTTPException(status_code=400, detail="The selected purchase orders have no quantity to spread the cost over")
        per_unit = amount / total_qty
        return [{
            "po_id": l.po_id, "po_line_id": l.id, "item_id": l.item_id,
            "quantity": l.quantity, "amount": per_unit * l.quantity, "per_unit": per_unit,
        } for l in lines]

    @staticmethod
    def _validate(db: Session, data) -> None:
        if not (data.description or "").strip():
            raise HTTPException(status_code=400, detail="Description is required")
        from app.services import type_lists
        if data.cost_type not in type_lists.keys(db, "landed_cost"):
            raise HTTPException(status_code=400, detail=f"Type must be one of: {', '.join(sorted(type_lists.keys(db, 'landed_cost')))}")

    @staticmethod
    def _allocate(db: Session, lc: LandedCost, po_ids: List[int]) -> List[int]:
        line_ids = []
        for p in LandedCostService.plan(db, lc.amount, po_ids):
            db.add(LandedCostAllocation(
                landed_cost_id=lc.id, po_id=p["po_id"], po_line_id=p["po_line_id"],
                item_id=p["item_id"], quantity=p["quantity"], amount=p["amount"],
            ))
            line_ids.append(p["po_line_id"])
        return line_ids

    @staticmethod
    def create_internal(db: Session, description: str, cost_type: str, amount: float, po_ids: List[int],
                        created_by: str = None, **fields) -> LandedCost:
        """Create and allocate without committing -- callers own the transaction."""
        lc = LandedCost(
            code=generate_code(db, LandedCost, "LC"), description=description.strip(), cost_type=cost_type,
            amount=cents(amount), created_by=created_by, **fields,
        )
        db.add(lc)
        db.flush()
        recompute_lot_costs(db, LandedCostService._allocate(db, lc, po_ids))
        return lc

    @staticmethod
    def create(db: Session, data, created_by: str) -> LandedCost:
        LandedCostService._validate(db, data)
        lc = LandedCostService.create_internal(
            db, description=data.description, cost_type=data.cost_type, amount=data.amount,
            po_ids=data.po_ids, created_by=created_by, paid_to=data.paid_to, reference=data.reference,
            cost_date=data.cost_date, notes=data.notes,
        )
        db.commit()
        db.refresh(lc)
        return lc

    @staticmethod
    def update(db: Session, lc_id: int, data) -> LandedCost:
        """Edit and re-spread: old allocations are dropped and lots on both the old and new
        PO lines are re-costed."""
        LandedCostService._validate(db, data)
        lc = LandedCostService.get(db, lc_id)
        old_line_ids = [a.po_line_id for a in lc.allocations]
        for field in ("description", "cost_type", "amount", "paid_to", "reference", "cost_date", "notes"):
            setattr(lc, field, getattr(data, field))
        lc.description = lc.description.strip()
        lc.amount = cents(lc.amount)
        lc.allocations.clear()
        db.flush()
        new_line_ids = LandedCostService._allocate(db, lc, data.po_ids)
        recompute_lot_costs(db, old_line_ids + new_line_ids)
        db.commit()
        db.refresh(lc)
        return lc

    @staticmethod
    def delete(db: Session, lc_id: int) -> None:
        lc = LandedCostService.get(db, lc_id)
        line_ids = [a.po_line_id for a in lc.allocations]
        db.delete(lc)
        recompute_lot_costs(db, line_ids)
        db.commit()


# ---- Order profit ----
class OrderProfitService:
    @staticmethod
    def calculate(db: Session, order_id: int) -> dict:
        """Revenue vs. lot cost for every unit on the order:
        shipped  -- the lots the units actually left from (actual)
        booked   -- the lots open shipments reserved (committed)
        unbooked -- projected from the free lots booking would draw next, oldest first
        Landed costs are already inside each lot's unit_cost. Shipped revenue is what was actually invoiced for the line
        (live invoices, less credit memos); shipped units not invoiced yet count at the order price."""
        from app.services.credit_memos import credited_by_order_line
        from app.services.money import line_amount
        order = CustomerOrderService.get(db, order_id)
        live_invoices = db.query(Invoice).filter(Invoice.order_id == order.id, Invoice.status != "void").all()
        invoiced = {}  # order line -> [qty, amount]
        for inv in live_invoices:
            for l in inv.lines:
                if l.order_line_id:
                    v = invoiced.setdefault(l.order_line_id, [0.0, 0.0])
                    v[0] += l.quantity or 0
                    v[1] += line_amount(l.quantity, l.unit_price)
        credits = credited_by_order_line(db, order.id)
        buckets = {k: {"quantity": 0.0, "revenue": 0.0, "cost": 0.0, "profit": 0.0} for k in ("shipped", "booked", "unbooked")}
        missing, warnings, lines_out = {}, [], []
        free_lots_by_item = {}  # item_id -> [[lot, free]], consumed as unbooked lines are projected

        def note_missing(lot, item, qty):
            entry = missing.setdefault(lot.id, {
                "lot_id": lot.id, "lot_code": lot.lot_code, "item_code": item.code,
                "quantity": 0.0, "source": lot.source_reference or lot.source,
            })
            entry["quantity"] += qty

        for line in sorted(order.lines, key=lambda l: l.id):
            item = db.query(StockItem).filter(StockItem.id == line.item_id).first()
            comps = []
            for sl in line._active_shipment_lines():
                kind = "shipped" if sl.shipment.status in ShipmentService.SHIPPED_STATUSES else "booked"
                lot = db.query(Lot).filter(Lot.id == sl.lot_id).first() if sl.lot_id else None
                unit_cost = lot.unit_cost if lot else None
                if lot is None:
                    warnings.append(f"{sl.shipment.code}: {sl.quantity:g} × {item.code} has no lot, so its cost is unknown")
                elif unit_cost is None:
                    note_missing(lot, item, sl.quantity)
                comps.append({
                    "kind": kind, "quantity": sl.quantity, "unit_cost": unit_cost,
                    "cost": sl.quantity * (unit_cost or 0), "revenue": sl.quantity * line.unit_price,
                    "shipment_id": sl.shipment.id, "shipment_code": sl.shipment.code,
                    "lot_id": lot.id if lot else None, "lot_code": lot.lot_code if lot else None,
                    "lot_source": (lot.source_reference or lot.source) if lot else None,
                })

            open_qty = line.quantity - line.allocated_quantity
            if open_qty > 1e-9:
                if item.id not in free_lots_by_item:
                    free_lots_by_item[item.id] = [[lot, free] for lot, free in ShipmentService.free_lot_quantities(db, item.id)]
                remaining = open_qty
                for slot in free_lots_by_item[item.id]:
                    if remaining <= 1e-9:
                        break
                    lot, free = slot
                    take = min(free, remaining)
                    if take <= 1e-9:
                        continue
                    slot[1] -= take
                    remaining -= take
                    if lot.unit_cost is None:
                        note_missing(lot, item, take)
                    comps.append({
                        "kind": "unbooked", "quantity": take, "unit_cost": lot.unit_cost,
                        "cost": take * (lot.unit_cost or 0), "revenue": take * line.unit_price,
                        "lot_id": lot.id, "lot_code": lot.lot_code,
                        "lot_source": lot.source_reference or lot.source, "estimated": True,
                    })
                if remaining > 1e-9:
                    est = item.cost_price or 0
                    warnings.append(f"{remaining:g} × {item.code} not in stock yet -- costed at the item's average cost ({est:g})")
                    comps.append({
                        "kind": "unbooked", "quantity": remaining, "unit_cost": est,
                        "cost": remaining * est, "revenue": remaining * line.unit_price, "estimated": True,
                    })

            # shipped units: revenue as invoiced (less credits); any shipped but not yet invoiced at the order price
            shipped = [c for c in comps if c["kind"] == "shipped"]
            shipped_qty = sum(c["quantity"] for c in shipped)
            inv_qty, inv_amt = invoiced.get(line.id, (0.0, 0.0))
            cr_qty, cr_amt = credits.get(line.id, (0.0, 0.0))
            billed_qty, billed_amt = inv_qty - cr_qty, inv_amt - cr_amt
            if shipped and (inv_qty > 1e-9 or cr_qty > 1e-9):
                actual = billed_amt + max(0.0, shipped_qty - billed_qty) * line.unit_price
                at_price = shipped_qty * line.unit_price
                for c in shipped:
                    c["revenue"] = actual * (c["quantity"] / shipped_qty) if shipped_qty > 1e-9 else 0
                if abs(actual - at_price) > 0.01:
                    warnings.append(f"#{line.line_no} {item.code}: revenue is what was invoiced ({cents(actual):,.2f}) -- "
                                    f"at the order price it would be {cents(at_price):,.2f}")
            revenue = sum(c["revenue"] for c in comps)
            cost = sum(c["cost"] for c in comps)
            for c in comps:
                b = buckets[c["kind"]]
                b["quantity"] += c["quantity"]
                b["revenue"] += c["revenue"]
                b["cost"] += c["cost"]
                b["profit"] += c["revenue"] - c["cost"]
            for c in comps:
                c["revenue"], c["cost"] = cents(c["revenue"]), cents(c["cost"])
            lines_out.append({
                "line_id": line.id, "line_no": line.line_no, "item_id": item.id, "item_code": item.code, "item_title": item.title,
                "quantity": line.quantity, "unit_price": line.unit_price,
                "revenue": cents(revenue), "cost": cents(cost), "profit": cents(revenue - cost),
                "margin_pct": (revenue - cost) / revenue * 100 if revenue > 1e-9 else None,
                "components": comps,
            })

        shipping_cost = cents(sum(s.shipping_cost or 0 for s in db.query(Shipment).filter(
            Shipment.order_id == order.id, Shipment.status != "cancelled").all()))
        # non-item invoice lines (shipping charged...), less credit memo lines not tied to an order line
        from app.models import CreditMemo
        general_credit = sum(l.amount for m in db.query(CreditMemo).filter(CreditMemo.order_id == order.id, CreditMemo.status == "issued").all()
                             for l in m.lines if not l.order_line_id)
        other_charges = cents(sum(l.amount for inv in live_invoices for l in inv.lines if l.item_id is None) - general_credit)
        for b in buckets.values():
            b["revenue"], b["cost"], b["profit"] = cents(b["revenue"]), cents(b["cost"]), cents(b["profit"])
        revenue = cents(sum(b["revenue"] for b in buckets.values()))
        cogs = cents(sum(b["cost"] for b in buckets.values()))
        net = cents(revenue - cogs + other_charges - shipping_cost)
        if missing:
            warnings.insert(0, f"{len(missing)} lot(s) have no cost recorded -- enter what they were acquired at for an accurate profit")
        return {
            "order_id": order.id, "order_code": order.code, "lines": lines_out,
            **buckets,
            "revenue": revenue, "cogs": cogs, "gross_profit": cents(revenue - cogs),
            "other_charges": other_charges, "shipping_cost": shipping_cost, "net_profit": net,
            "margin_pct": net / (revenue + other_charges) * 100 if revenue + other_charges > 1e-9 else None,
            "missing_costs": list(missing.values()), "warnings": warnings,
        }
