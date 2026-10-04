"""Random-sample validation sheet: pick random records and show MRPeasy's values next to
AT-HUB's, field by field and line by line, with a link to each record in AT-HUB.

    python -m app.importers.mrpeasy sample [N]     (N records of each kind, default 4)

Writes a local HTML page next to the snapshot (business data -- it is never published).
Each record can be ticked "checked" or flagged with a note; "Copy problems" collects them.
"""
import html
import re
import json
import random
import sqlite3
from datetime import datetime
from pathlib import Path

from .load import LIVE_DB, address_text, dt, f, ord_key

APP = "http://localhost:8010"


def _d(v):
    x = dt(v)
    return x.strftime("%Y-%m-%d") if x else ""


def _hd(v):
    return (v or "")[:10]


def _m(v):
    return f"{f(v):,.2f}"


def _q(v):
    return f"{f(v):,.4f}".rstrip("0").rstrip(".")


# The same thing named differently in the two systems -- not a difference.
SAME_STATUS = {("delivered", "shipped"), ("delivered", "invoiced"), ("delivered", "delivered"), ("shipped", "shipped"),
               ("shipped", "invoiced"), ("shipped", "delivered"), ("confirmed", "confirmed"), ("ready for shipment", "confirmed"),
               ("ready for shipment", "ready"), ("paid", "paid"), ("unpaid", "sent"), ("dummy", "draft")}
INFO = "(MRPeasy doesn't link these)"


def _same(a, b):
    a, b = ("" if a is None else str(a)).strip(), ("" if b is None else str(b)).strip()
    if a == INFO or (a.lower(), b.lower()) in SAME_STATUS:
        return True
    a = re.sub(r"^po\s*#?\s*(?=\d)", "", a, flags=re.I)  # "PO # 4098678" is stored as "4098678"
    b = b.replace(" (expected)", "")
    try:
        return abs(float(a.replace(",", "")) - float(b.replace(",", ""))) < 0.006
    except ValueError:
        return " ".join(a.lower().split()) == " ".join(b.lower().split())


def sample(snapshot: Path, n: int = 4, seed=None) -> Path:
    snap = lambda name: json.loads((snapshot / f"{name}.json").read_text(encoding="utf-8"))
    rnd = random.Random(seed)
    db = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    one = lambda sql, *a: db.execute(sql, a).fetchone()
    rows = lambda sql, *a: db.execute(sql, a).fetchall()
    items_by_id = {r["id"]: r for r in rows("select * from stock_items")}
    cards = []

    def card(kind, code, link, fields, line_head, lines):
        cards.append({"kind": kind, "code": code, "link": link, "fields": fields, "line_head": line_head, "lines": lines})

    # --- customer orders
    for o in rnd.sample(snap("customer_orders"), min(n, len(snap("customer_orders")))):
        co = one("select * from customer_orders where mrp_id=?", o["cust_ord_id"])
        cust = one("select name from customers where id=?", co["customer_id"])["name"]
        lines = sorted(rows("select * from customer_order_lines where order_id=?", co["id"]), key=lambda l: l["line_no"] or 0)
        by_mrp = {l["mrp_id"]: l for l in lines}
        total = sum(l["quantity"] * l["unit_price"] for l in lines)
        card("Customer order", o["code"], f"{APP}/customer-orders.html?id={co['id']}", [
            ("Customer", o["customer_name"], cust), ("Customer PO #", o["reference"], co["po_number"]),
            ("Customer PO date", _d(o.get("custom_218")), _hd(co["customer_po_date"])), ("Job #", o.get("custom_814"), co["job_number"]),
            ("Delivery date", _d(o["delivery_date"]), _hd(co["delivery_date"])), ("Status", o["status_txt"], co["status"]),
            ("Ship to", address_text(o["shipping_address"]), co["ship_to_address"]), ("Total", _m(o["total_price"]), _m(total)),
        ], ["Item", "Qty", "Unit price", "Shipped"], [
            ((p["item_code"], items_by_id[by_mrp[p["line_id"]]["item_id"]]["code"]), (_q(p["quantity"]), _q(by_mrp[p["line_id"]]["quantity"])),
             (_m(p["item_price"]), _m(by_mrp[p["line_id"]]["unit_price"])), (_q(p["shipped"]), _q(by_mrp[p["line_id"]]["shipped_quantity"])))
            for p in sorted(o["products"], key=ord_key)])

    # --- purchase orders
    for o in rnd.sample(snap("purchase_orders"), min(n, len(snap("purchase_orders")))):
        po = one("select * from purchase_orders where mrp_id=?", o["pur_ord_id"])
        vend = one("select name from vendors where id=?", po["vendor_id"])["name"]
        lines = {l["mrp_id"]: l for l in rows("select * from purchase_order_lines where po_id=?", po["id"])}
        charges = sum(c["amount"] for c in rows("select amount from purchase_order_charges where po_id=?", po["id"]))
        goods = sum(l["quantity"] * l["unit_cost"] for l in lines.values())
        lots = {r["po_line_id"]: r["codes"] for r in rows("select po_line_id, group_concat(lot_code, ', ') codes from lots where po_line_id in (select id from purchase_order_lines where po_id=?) group by po_line_id", po["id"])}
        card("Purchase order", o["code"], f"{APP}/purchase-orders.html?id={po['id']}", [
            ("Vendor", o["vendor_title"], vend), ("Order date", _d(o["order_date"]) or _d(o["created"]), _hd(po["order_date"])),
            ("Expected", _d(o["expected_date"]), _hd(po["expected_date"])),
            ("Fees + tax − discount (S&H lines)", _m(f(o["fees_sum"]) + f(o["fees_taxable_sum"]) + f(o["tax_sum"]) - f(o["discount_sum"])), _m(charges)),
            ("Total incl. tax", _m(f(o["total_price"]) + f(o["tax_sum"])), _m(goods + charges)),
        ], ["Item", "Qty", "Unit cost", "Lot"], [
            ((p["item_code"], items_by_id[lines[p["line_id"]]["item_id"]]["code"]), (_q(p["quantity"]), _q(lines[p["line_id"]]["quantity"])),
             (f"{f(p['item_price']):.5f}", f"{lines[p['line_id']]['unit_cost']:.5f}"),
             (p["lot_code"] or "", lots.get(lines[p["line_id"]]["id"]) or (f"{lines[p['line_id']]['planned_lot_code']} (expected)" if lines[p["line_id"]]["planned_lot_code"] else "")))
            for p in sorted(o["products"], key=ord_key)])

    # --- shipments
    for s in rnd.sample(snap("shipments"), min(n, len(snap("shipments")))):
        sh = one("select * from shipments where mrp_id=?", s["shipment_id"])
        co = one("select code from customer_orders where id=?", sh["order_id"])["code"]
        inv = one("select group_concat(i.code, ', ') c from invoices i join invoice_shipments x on x.invoice_id=i.id where x.shipment_id=?", sh["id"])["c"]
        got = {}
        for l in rows("select sl.*, l.lot_code from shipment_lines sl left join lots l on l.id=sl.lot_id where shipment_id=?", sh["id"]):
            key = (items_by_id[l["item_id"]]["code"], l["lot_code"])
            got[key] = got.get(key, 0) + l["quantity"]
        want = {}
        for p in s["products"]:
            q = f(p["quantity_picked"]) if s["status"] == "20" else f(p["quantity_picked"]) + f(p["quantity_booked"])
            if q:
                want[(p["item_code"], p["lot_code"])] = want.get((p["item_code"], p["lot_code"]), 0) + q
        card("Shipment", s["code"], f"{APP}/shipments.html?id={sh['id']}", [
            ("Customer order", s["customer_order_code"] or ", ".join(o["customer_order_code"] for o in s["orders"]), co),
            ("Status", s["status_txt"], sh["status"]), ("Shipped date", _d(s["delivery_date"]), _hd(sh["ship_date"])),
            ("Job #", s.get("custom_815"), json.loads(sh["custom_fields"] or "{}").get("Job #")),
            ("Billed on invoice", "(MRPeasy doesn't link these)", inv or "—"),
        ], ["Item", "Lot", "Qty"], [
            ((k[0], k[0] if k in got else "missing"), (k[1], k[1] if k in got else ""), (_q(v), _q(got.get(k, 0)))) for k, v in want.items()])

    # --- invoices
    for i in rnd.sample(snap("invoices"), min(n, len(snap("invoices")))):
        inv = one("select * from invoices where mrp_id=?", i["invoice_id"])
        lines = rows("select * from invoice_lines where invoice_id=?", inv["id"])
        paid = one("select coalesce(sum(amount),0) p from invoice_payments where invoice_id=?", inv["id"])["p"]
        ships = one("select group_concat(s.code, ' + ') c from shipments s join invoice_shipments x on x.shipment_id=s.id where x.invoice_id=?", inv["id"])["c"]
        card("Invoice", i["code"], f"{APP}/invoices.html?id={inv['id']}", [
            ("Customer", i["customer_name"], one("select name from customers where id=?", inv["customer_id"])["name"]),
            ("Customer order", i["customer_order_code"], (one("select code from customer_orders where id=?", inv["order_id"]) or {"code": ""})["code"]),
            ("Date", _d(i["created"]), _hd(inv["invoice_date"])), ("Due", _d(i["due_date"]), _hd(inv["due_date"])),
            ("Status", i["status_txt"], inv["status"]), ("Total", _m(i["total_price"]), _m(sum(l["quantity"] * l["unit_price"] for l in lines))),
            ("Paid", _m(i["total_price"]) if i["status"] == "40" else "0.00", _m(paid)),
            ("Disbursement date", _d(i.get("custom_570")), _hd(inv["disbursement_date"])),
            ("Funding amount", _m(i.get("custom_571")) if i.get("custom_571") else "", _m(inv["funding_amount"]) if inv["funding_amount"] else ""),
            ("Shipments (inferred)", "(MRPeasy doesn't link these)", ships or "—"),
        ], ["Item", "Qty", "Unit price"], [
            ((p["item_code"], items_by_id[l["item_id"]]["code"] if l["item_id"] else ""), (_q(p["quantity"]), _q(l["quantity"])), (_m(p["item_price"]), _m(l["unit_price"])))
            for p, l in zip(sorted(i["products"], key=ord_key), sorted(lines, key=lambda l: l["id"]))])

    # --- stock items (only ones with stock, so there's something to compare)
    inv_rows = [r for r in snap("inventory") if f(r["quantity"])]
    lot_raw = [l for l in snap("lots") if l["status"] == "20"]
    for r in rnd.sample(inv_rows, min(n * 2, len(inv_rows))):
        it = one("select * from stock_items where mrp_id=?", r["article_id"])
        want = {l["code"]: (f(l["available"]) + f(l["booked"]), f(l["item_cost"])) for l in lot_raw if l["article_id"] == r["article_id"]}
        got = {l["lot_code"]: (l["quantity"], l["unit_cost"]) for l in rows("select * from lots where item_id=? and quantity > 0", it["id"])}
        card("Stock item", r["product_code"], f"{APP}/stock-items.html", [
            ("Title", r["product_title"], it["title"]), ("Product group", r["group_title"], it["category"]),
            ("On hand", _q(r["quantity"]), _q(it["on_hand"])), ("Stock value", _m(r["total_cost"]), _m(sum(q * c for q, c in got.values()))),
        ], ["Lot", "Qty", "Unit cost"], [
            ((code, code if code in got else "missing"), (_q(q), _q(got.get(code, (0, 0))[0])), (f"{c:.5f}", f"{got.get(code, (0, 0))[1]:.5f}"))
            for code, (q, c) in sorted(want.items())])

    # --- vendors
    for v in rnd.sample(snap("vendors"), min(n, len(snap("vendors")))):
        hv = one("select * from vendors where mrp_id=?", v["vendor_id"])
        cd = v.get("contact_data") or []
        pick = lambda t: ", ".join(d["value"] for d in cd if d["type"] == t and isinstance(d["value"], str))
        card("Vendor", v["code"], f"{APP}/vendors.html", [
            ("Name", v["title"], hv["name"]), ("Phone", pick("phone"), hv["phone"]), ("Email", pick("email"), hv["email"]),
            ("Address", address_text(next((d["value"] for d in cd if d["type"] == "address"), None)), (hv["address"] or "").split("\nFax:")[0].split("\nWeb:")[0]),
        ], [], [])
    db.close()

    out = snapshot / f"validation-sample-{datetime.now():%Y%m%d-%H%M%S}.html"
    out.write_text(_page(cards, snapshot.name), encoding="utf-8")
    print(f"{len(cards)} records -> {out}")
    return out


def _cell(a, b):
    ok = _same(a, b)
    e = lambda v: html.escape("" if v is None else str(v)).replace("\n", "<br>")
    return f"<td>{e(a)}</td><td class='{'' if ok else 'diff'}'>{e(b)}</td><td class='m'>{'✓' if ok else '≠'}</td>"


def _page(cards, snap_name) -> str:
    body = []
    for n, c in enumerate(cards):
        key = f"{c['kind']}:{c['code']}"
        diffs = sum(1 for _, a, b in c["fields"] if not _same(a, b)) + sum(1 for ln in c["lines"] for a, b in ln if not _same(a, b))
        fields = "".join(f"<tr><th>{html.escape(name)}</th>{_cell(a, b)}</tr>" for name, a, b in c["fields"])
        lines = ""
        if c["lines"]:
            head = "".join(f"<th colspan=3>{html.escape(h)}</th>" for h in c["line_head"])
            sub = "".join("<th>MRPeasy</th><th>AT-HUB</th><th></th>" for _ in c["line_head"])
            lines = (f"<table class='lines'><tr>{head}</tr><tr class='sub'>{sub}</tr>"
                     + "".join("<tr>" + "".join(_cell(a, b) for a, b in ln) + "</tr>" for ln in c["lines"]) + "</table>")
        body.append(f"""
<section class="rec" data-key="{html.escape(key)}">
  <header><span class="kind">{html.escape(c['kind'])}</span><strong>{html.escape(c['code'])}</strong>
    <span class="auto {'bad' if diffs else 'good'}">{'Auto-check: ' + str(diffs) + ' difference' + ('s' if diffs != 1 else '') if diffs else 'Auto-check: identical'}</span>
    <a href="{c['link']}" target="_blank">Open in AT-HUB ↗</a></header>
  <table class="fields"><tr class="sub"><th></th><th>MRPeasy</th><th>AT-HUB</th><th></th></tr>{fields}</table>
  {lines}
  <footer>
    <label><input type="radio" name="v{n}" value="ok"> Checked against MRPeasy: correct</label>
    <label><input type="radio" name="v{n}" value="bad"> Problem</label>
    <input type="text" class="note" placeholder="What's wrong? (e.g. ship-to differs, wrong lot)">
  </footer>
</section>""")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Import Validation Sample</title>
<style>
:root {{ --bg:#eef1f6; --card:#fff; --b:#dbe1ea; --t:#172033; --m:#6b7686; --ok:#15803d; --bad:#c02626; --badbg:#fde8e8; --acc:#2f6fed; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#0f141c; --card:#171e29; --b:#2a3545; --t:#e4e9f1; --m:#8b97a8; --ok:#4ade80; --bad:#f87171; --badbg:#3a1c1c; --acc:#5b8ff9; }} }}
body {{ margin:0; background:var(--bg); color:var(--t); font:14px/1.45 "Segoe UI",-apple-system,Arial,sans-serif; }}
.top {{ position:sticky; top:0; z-index:5; background:var(--card); border-bottom:1px solid var(--b); padding:12px 20px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }}
.top h1 {{ font-size:18px; margin:0; }} .top .muted {{ color:var(--m); font-size:13px; }}
.top button {{ background:var(--acc); color:#fff; border:0; border-radius:6px; padding:7px 14px; cursor:pointer; font-size:13px; }}
main {{ max-width:1100px; margin:0 auto; padding:16px; }}
.rec {{ background:var(--card); border:1px solid var(--b); border-radius:10px; margin-bottom:14px; overflow:hidden; }}
.rec.ok {{ border-left:4px solid var(--ok); }} .rec.bad {{ border-left:4px solid var(--bad); }}
.rec header {{ display:flex; gap:10px; align-items:center; padding:10px 14px; border-bottom:1px solid var(--b); flex-wrap:wrap; }}
.kind {{ font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--m); font-weight:700; }}
.auto {{ font-size:12px; }} .auto.good {{ color:var(--ok); }} .auto.bad {{ color:var(--bad); font-weight:600; }}
.rec header a {{ margin-left:auto; color:var(--acc); text-decoration:none; font-weight:600; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
.fields th:first-child {{ width:190px; text-align:left; color:var(--m); font-weight:600; }}
th, td {{ padding:5px 10px; border-bottom:1px solid var(--b); vertical-align:top; text-align:left; }}
tr.sub th {{ font-size:11px; color:var(--m); font-weight:600; text-transform:uppercase; }}
.lines {{ border-top:2px solid var(--b); }} .lines th {{ font-size:12px; }}
td.m {{ width:18px; color:var(--ok); text-align:center; }} td.diff {{ background:var(--badbg); }} td.diff + td.m {{ color:var(--bad); font-weight:700; }}
.rec footer {{ display:flex; gap:14px; align-items:center; padding:10px 14px; flex-wrap:wrap; }}
.note {{ flex:1; min-width:220px; padding:6px 9px; border:1px solid var(--b); border-radius:6px; background:var(--bg); color:var(--t); }}
@media (max-width:700px) {{ .fields th:first-child {{ width:auto; }} main {{ padding:8px; }} }}
</style></head><body>
<div class="top"><h1>Import Validation Sample</h1>
  <span class="muted">Snapshot {html.escape(snap_name)} · {len(cards)} random records · open each in MRPeasy and AT-HUB and confirm</span>
  <span id="progress" class="muted"></span><button onclick="copyProblems()">Copy problems for Claude</button></div>
<main>{''.join(body)}</main>
<script>
const KEY = "at_hub_validation:{html.escape(snap_name)}";
let state = {{}};
try {{ state = JSON.parse(localStorage.getItem(KEY)) || {{}}; }} catch (e) {{}}
function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(state)); }} catch (e) {{}} paint(); }}
function paint() {{
  let ok = 0, bad = 0;
  document.querySelectorAll(".rec").forEach(r => {{
    const s = state[r.dataset.key] || {{}};
    r.classList.toggle("ok", s.v === "ok"); r.classList.toggle("bad", s.v === "bad");
    if (s.v === "ok") ok++; if (s.v === "bad") bad++;
  }});
  document.getElementById("progress").textContent = `${{ok}} correct · ${{bad}} problems · ${{document.querySelectorAll(".rec").length - ok - bad}} to check`;
}}
document.querySelectorAll(".rec").forEach(r => {{
  const k = r.dataset.key, s = state[k] || {{}};
  r.querySelectorAll("input[type=radio]").forEach(i => {{ i.checked = i.value === s.v; i.onchange = () => {{ state[k] = {{ ...(state[k] || {{}}), v: i.value }}; save(); }}; }});
  const note = r.querySelector(".note"); note.value = s.note || "";
  note.oninput = () => {{ state[k] = {{ ...(state[k] || {{}}), note: note.value }}; save(); }};
}});
function copyProblems() {{
  const lines = Object.entries(state).filter(([, s]) => s.v === "bad").map(([k, s]) => `- ${{k}}: ${{s.note || "(no note)"}}`);
  const text = lines.length ? "Import validation problems:\\n" + lines.join("\\n") : "Import validation: no problems found.";
  navigator.clipboard.writeText(text).then(() => alert(text), () => prompt("Copy this:", text));
}}
paint();
</script></body></html>"""
