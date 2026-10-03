"""Builders: create real records through the API, the way the screens do. Every code is unique,
so tests can share one database without stepping on each other."""
import itertools

_seq = itertools.count(1)


def uid(prefix="T"):
    return f"{prefix}{next(_seq):05d}"


class Builders:
    def __init__(self, api):
        self.api = api

    # ---- parties / items ----
    def customer(self, name=None, **kw):
        return self.api.post("/api/customers/", json={"name": name or uid("Cust "), **kw})

    def vendor(self, name=None, **kw):
        return self.api.post("/api/vendors/", json={"name": name or uid("Vendor "), **kw})

    def item(self, code=None, title=None, group="Bolt", price=1.0, cost=0.5, **kw):
        return self.api.post("/api/stock-items/", json={"code": code or uid("IT"), "title": title or "test item",
                                                        "category": group, "selling_price": price, "cost_price": cost, **kw})

    # ---- purchasing ----
    def po(self, vendor=None, lines=(), **kw):
        vendor = vendor or self.vendor()
        return self.api.post("/api/purchase-orders/", json={"vendor_id": vendor["id"], "lines": [
            {"item_id": it["id"], "quantity": q, "unit_cost": c} for it, q, c in lines], **kw})

    def stock(self, item, qty, cost=1.0):
        """Put stock on hand: a PO for it, ordered and fully received."""
        po = self.po(lines=[(item, qty, cost)])
        self.api.post(f"/api/purchase-orders/{po['id']}/mark-ordered")
        return self.api.post(f"/api/purchase-orders/{po['id']}/receive",
                             json={"lines": [{"line_id": po["lines"][0]["id"], "quantity": qty}]})

    # ---- sales ----
    def order(self, customer=None, lines=(), confirm=True, **kw):
        customer = customer or self.customer()
        o = self.api.post("/api/customer-orders/", json={"customer_id": customer["id"], "po_number": kw.pop("po_number", uid("PO")),
                                                         "lines": [{"item_id": it["id"], "quantity": q, "unit_price": p} for it, q, p in lines], **kw})
        if confirm:
            o = self.api.post(f"/api/customer-orders/{o['id']}/confirm")
        return o

    def ship(self, order, quantities=None):
        """Book, confirm and pick a shipment -> shipped. quantities: {order_line_id: qty} (default: everything open)."""
        if quantities is None:
            quantities = {l["id"]: l["quantity"] - l["shipped_quantity"] - l["booked_quantity"] for l in order["lines"]}
        sh = self.api.post(f"/api/customer-orders/{order['id']}/shipments",
                           json={"lines": [{"line_id": lid, "quantity": q} for lid, q in quantities.items() if q > 0]})
        self.api.post(f"/api/shipments/{sh['id']}/confirm-booking")
        return self.api.post(f"/api/shipments/{sh['id']}/pick", json={"pick_all": True})

    def invoice(self, shipments, shipping=0, **kw):
        if not isinstance(shipments, list):
            shipments = [shipments]
        if len(shipments) == 1:
            return self.api.post(f"/api/invoices/from-shipment/{shipments[0]['id']}", json={"shipping_charge": shipping, **kw})
        inv = self.api.post("/api/invoices/from-shipments", json={"shipment_ids": [s["id"] for s in shipments], **kw})
        return inv

    def sold(self, lines, shipping=0):
        """order -> stock -> ship everything -> draft invoice. lines: [(item, qty, price)]"""
        for it, q, _ in lines:
            self.stock(it, q)
        o = self.order(lines=lines)
        sh = self.ship(o)
        return o, sh, self.invoice(sh, shipping)
