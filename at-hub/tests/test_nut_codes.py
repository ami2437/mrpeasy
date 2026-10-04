"""One spelling for nut codes: -NUT. Typing -NUTS is corrected unless the user keeps it."""
import random

from app.services.item_naming import normalize_code


def test_normalize_code():
    assert normalize_code("15420-NUTS") == "15420-NUT"
    assert normalize_code("15385 - NUT") == "15385-NUT"
    assert normalize_code("31568-Nut") == "31568-NUT"
    assert normalize_code("55659 -NUTS") == "55659-NUT"
    assert normalize_code("41573-HPC-NUTS") == "41573-HPC-NUT"
    assert normalize_code("15420") == "15420" and normalize_code("WALNUTS") == "WALNUTS" and normalize_code("NUT-58") == "NUT-58"


def test_create_corrects_unless_kept(api):
    n = random.randint(100000, 999999)
    a = api.post("/api/stock-items/", json={"code": f"{n}-NUTS", "title": "nut", "category": "Nut"})
    assert a["code"] == f"{n}-NUT"
    b = api.post("/api/stock-items/", json={"code": f"{n + 1}-NUTS", "title": "nut", "category": "Nut", "keep_code": True})
    assert b["code"] == f"{n + 1}-NUTS"
