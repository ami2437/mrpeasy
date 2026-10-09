"""No word on a printed document is ever split across two lines -- item #s like 79299-HPC-NUT and headers like
BACKORDERED -- in the built-in layouts and in every Template Designer starter (so any template made from them)."""
import copy
import io
import re

import pytest
from pypdf import PdfReader

LONG_CODE = "79299-HPC-NUT-XL"  # longer than any real code, no spaces


def _text(content: bytes) -> str:
    return " ".join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)


def _whole(text: str, word: str) -> bool:
    """The word printed in one piece (a split shows up as two pieces with a line break between them)."""
    return word in text


@pytest.fixture(scope="module")
def records(client, admin_headers):
    from tests.builders import Builders
    from tests.conftest import api as _api  # noqa: F401  (fixture factory not usable here; plain client calls)
    h = admin_headers

    def post(url, **kw):
        r = client.post(url, headers=h, **kw)
        assert r.status_code in (200, 201), r.text
        return r.json()

    class A:  # the small slice of the api fixture the builders use
        def call(self, m, url, expect=None, **kw):
            r = client.request(m, url, headers=h, **kw)
            assert r.status_code in (200, 201, 204), f"{m} {url} {r.text[:300]}"
            return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r
        get = lambda self, u, **k: self.call("GET", u, **k)
        post = lambda self, u, **k: self.call("POST", u, **k)
        put = lambda self, u, **k: self.call("PUT", u, **k)
        delete = lambda self, u, **k: self.call("DELETE", u, **k)
    make = Builders(A())
    long_item = make.item(code=LONG_CODE, title="A563 GR A HEAVY HEX NUT MECH GALV WAX DIPPED", group="Nut", price=0.5)
    other = make.item(title="BOLT_HH_3/4-10x6_A320_L7_MECH GALV_THREAD_FULL LENGTH_W/(2)HEX NUTS_SA194_GR4/7_", price=3)
    make.stock(long_item, 50)
    make.stock(other, 50)
    o = make.order(lines=[(long_item, 40, 0.5), (other, 20, 3)])
    sh = make.ship(o, {o["lines"][0]["id"]: 40, o["lines"][1]["id"]: 10})  # part shipped: a backorder shows
    inv = make.invoice(sh)
    po = make.po(lines=[(long_item, 50, 0.4)])
    q = A().post("/api/quotes/", json={"customer_id": o["customer_id"], "lines": [{"item_id": long_item["id"], "quantity": 5, "unit_price": 1}]})
    return {"packing_list": sh["id"], "invoice": inv["id"], "purchase_order": po["id"], "quote": q["id"]}


@pytest.mark.parametrize("doc_type", ["packing_list", "invoice", "purchase_order", "quote"])
def test_every_starter_keeps_words_whole(client, admin_headers, records, doc_type):
    starters = client.get(f"/api/templates/starters/{doc_type}", headers=admin_headers).json()
    assert starters
    for st in starters:
        spec = copy.deepcopy(st["spec"])
        r = client.post("/api/templates/preview", headers=admin_headers, json={"doc_type": doc_type, "spec": spec, "record_id": records[doc_type]})
        assert r.status_code == 200, (st["key"], r.text[:200])
        text = _text(r.content)
        assert _whole(text, LONG_CODE), (doc_type, st["key"], re.findall(r"79299\S*", text))


def test_narrow_item_column_in_a_design_still_keeps_the_code_whole(client, admin_headers, records):
    """A design that gives the item # a tiny column (what the "Portal Template" packing list did) and a Backordered column."""
    starters = client.get("/api/templates/starters/packing_list", headers=admin_headers).json()
    spec = copy.deepcopy(starters[0]["spec"])
    # like the "Portal Template": its own Part # and Description columns, a narrow item # and a narrow Backordered column
    spec["table"]["columns"] = [{"key": "item_code", "header": "Part #", "w": 0.55, "bold": True},
                                {"key": "description", "header": "Part Description", "w": 0},
                                {"key": "ordered", "header": "Qty Ordered", "w": 0.6}, {"key": "shipped", "header": "Qty Shipped", "w": 0.6},
                                {"key": "backorder", "header": "Backordered", "w": 0.45}]
    spec["table"].setdefault("style", {})["header_upper"] = True
    r = client.post("/api/templates/preview", headers=admin_headers, json={"doc_type": "packing_list", "spec": spec, "record_id": records["packing_list"]})
    assert r.status_code == 200, r.text[:200]
    text = _text(r.content)
    assert _whole(text, LONG_CODE), re.findall(r"79299\S*", text)
    assert "BACKORDERED" in text, re.findall(r"BACKORD\S*\s*\S*", text)


def test_built_in_layouts_keep_words_whole(client, admin_headers, records):
    for url in (f"/api/shipments/{records['packing_list']}/packing-list.pdf", f"/api/invoices/{records['invoice']}/pdf",
                f"/api/purchase-orders/{records['purchase_order']}/pdf", f"/api/quotes/{records['quote']}/pdf"):
        r = client.get(url, headers=admin_headers)
        assert r.status_code == 200, url
        assert _whole(_text(r.content), LONG_CODE), url
