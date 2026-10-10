"""The item matcher on real descriptions (tests/data/item_match_cases.json: our item list -- codes, titles, groups --
and real customer / vendor descriptions with the item they really are). Guards: no wrong auto-pick, and the share
picked right doesn't drop. Plus the cases found by hand (2 1/4, A194-2H, NO NUT, a bolt's companion nut, group)."""
import json
from pathlib import Path
from types import SimpleNamespace

from app.services.item_match import ItemMatcher, features, pick

DATA = json.loads((Path(__file__).parent / "data" / "item_match_cases.json").read_text(encoding="utf-8"))


def _items(extra=()):
    out = [SimpleNamespace(id=n + 1, code=i["code"], title=i["title"], category=i["group"], barcode=None) for n, i in enumerate(DATA["items"])]
    for n, (code, title, group) in enumerate(extra):
        out.append(SimpleNamespace(id=10_000 + n, code=code, title=title, category=group, barcode=None))
    return out


def test_real_descriptions_no_wrong_auto_pick_and_no_drop():
    items = _items()
    by_code = {i.code: i.id for i in items}
    m = ItemMatcher(items)
    right = wrong_auto = 0
    for c in DATA["cases"]:
        r = m.rank([], c["text"])
        right += bool(r) and r[0]["item_id"] == by_code[c["code"]]
        p = pick(r)
        if p and p != by_code[c["code"]]:
            wrong_auto += 1
    assert wrong_auto == 0
    assert right >= 47, f"only {right} of {len(DATA['cases'])} picked right (was 47 when this test was written)"


def test_mixed_numbers_nut_grade_no_nut_and_groups():
    f = features("5/8-11 X 2 1/4 A325 HVY BOLT NO NUT HDG")
    assert f["len"] == {"2-1/4"} and f["type"] == {"bolt"} and f["grade"] == {"a325"}
    f = features("BOLT_HH_5/8-11x2-1/4_A325_TYPE1_HDG_w/A194_2H_HEX NUT")
    assert f["dia"] == {"5/8"} and f["type"] == {"bolt"} and "194" not in f["dia"]  # A194-2H is the nut's grade, not a size
    f = features("5/8-11 A194 GR 2H HVY HEX NUT DOM. HDG", "Nut")
    assert f["type"] == {"nut"} and f["grade"]  # on a nut, its grade counts
    assert features("BOLT_HH_3/4-10x2_A325_HDGw/A194-2H HEX NUT", "Bolt")["finish"] >= {"hdg"}  # "HDGw/" glued


def test_the_hudson_pair():
    cust = "BOLT_HH_5/8-11x2-1/4_A325_TYPE1_HDG_w/A194_2H_HEX NUT"
    vend = "5/8-11 X 2 1/4 A325 HVY BOLT NO NUT HDG"
    items = _items([("15423", cust, "Bolt")])
    m = ItemMatcher(items)
    code = {i.id: i.code for i in items}
    r = m.rank([], cust)
    assert code[pick(r)] == "15423"  # its own description
    r = m.rank([], vend)
    # the vendor's line doesn't say Type 1 or full thread: 15423 and 37127 both fit -> it asks, and never a wrong length
    assert {code[x["item_id"]] for x in r[:2]} == {"15423", "37127"} and pick(r) is None
    assert all("x2-1/4" in x["title"].lower().replace(" ", "") or x["score"] < 0.8 for x in r)
    # a vendor's plain nut line only lands on Nut items, never on a bolt whose title names its nut
    r = m.rank([], "5/8-11 A194 2H HEAVY HEX NUT HDG")
    groups = {i.id: i.category for i in items}
    assert r and all(groups[x["item_id"]] == "Nut" for x in r if x["score"] >= 0.8)
