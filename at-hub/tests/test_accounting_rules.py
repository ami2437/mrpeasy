"""Accounting rules (QuickBooks-style: AND / OR conditions on any part of the line, order, suggest-only, splits, copy,
export) and the learning around them (look-alikes after a pick, corrections, what AT-HUB learned, suggested rules).
Made-up lines only."""
from datetime import datetime as dt

from tests.builders import uid
from tests.test_accounting import _csv, _import, _source, _txns


def name(prefix: str) -> str:
    """A unique made-up payee in letters only (the payee reader drops words with digits, like reference numbers)."""
    return prefix + uid("").translate(str.maketrans("0123456789", "QWERTYUIOP")).upper()


def c(f, op, v):
    return {"field": f, "op": op, "value": v}


def test_rule_conditions_and_or_fields_whole_words():
    """acct_rules: AND / OR, whole words, "phrases", doesn't contain, any of, payee / note fields, amount and day."""
    from app.services import acct_rules as R
    line = R.line_of({"bank_description": "ZELLE TO GOMEZ SAUL ON 03/28 REF #RP0Y SAUL", "description": "ZELLE TO GOMEZ SAUL ON 03/28 REF #RP0Y SAUL",
                      "payee": "Gomez Saul (Zelle)", "note": "forklift", "amount": -420.0, "source_id": 7, "date": dt(2031, 3, 28)})

    def rule(conds, **kw):
        return R.spec({"conditions": conds, **kw})
    assert R.matches(rule([c("text", "contains", "saul gomez")]), line)                 # any order, any case
    assert not R.matches(rule([c("text", "contains", "SAULT")]), line)                  # whole words only
    assert R.matches(rule([c("text", "contains", '"GOMEZ SAUL"')]), line)
    assert not R.matches(rule([c("text", "contains", '"SAUL GOMEZ"')]), line)           # a phrase keeps its order
    assert R.matches(rule([c("note", "contains", "FORKLIFT")]), line) and not R.matches(rule([c("description", "contains", "FORKLIFT")]), line)
    assert R.matches(rule([c("text", "contains", "FORKLIFT")]), line)                   # anywhere = the note too
    assert R.matches(rule([c("payee", "starts", "Gomez")]), line)
    both = [c("text", "contains", "SAUL"), c("amount", "gt", 500)]
    assert not R.matches(rule(both), line) and R.matches(rule(both, match_mode="any"), line)
    assert R.matches(rule([c("text", "any_of", "FAUSTINO, SAUL")]), line)
    assert not R.matches(rule([c("text", "contains", "SAUL"), c("text", "not_contains", "FORKLIFT")]), line)
    assert R.matches(rule([c("amount", "between", [400, 450]), c("day", "between", [25, 31])]), line)
    assert not R.matches(rule([c("text", "contains", "SAUL")], sign="in"), line)
    assert not R.matches(rule([c("text", "contains", "SAUL")], source_ids="[8]"), line)
    assert R.matches(rule([c("text", "contains", "SAUL")], source_ids="[7]"), line)
    old = R.spec({"contains": "gomez saul", "min_amount": 400, "max_amount": 450, "sign": "out"})  # a rule made before conditions
    assert R.matches(old, line) and old["conditions"][0]["field"] == "text"
    split = R.spec({"conditions": [c("text", "contains", "SAUL")], "account_id": 1, "splits": [{"account_id": 2, "pct": 25}, {"account_id": 3, "amount": 20}]})
    assert R.parts(split, -420.0) == [(2, -105.0), (3, -20.0), (1, -295.0)]
    assert R.check(R.spec({"conditions": [c("amount", "gt", "abc")], "account_id": 1}))
    assert R.check(R.spec({"conditions": [], "account_id": 1}))


def test_rules_order_suggest_split_copy_export(api, client, admin_headers):
    """Two rules that disagree file nothing (Review, red flag) unless one wins; a split rule splits and adds its note;
    reorder; suggest-only suggests; copy starts off; export; a bad condition is refused."""
    y = 2041
    tag = name("ROSAX")
    src = _source(api)
    labor = api.post("/api/accounting/accounts", json={"name": uid("Labor "), "kind": "expense"})
    rosa = api.post("/api/accounting/accounts", json={"name": uid("Rosa "), "kind": "expense", "parent_id": labor["id"]})
    supplies = api.post("/api/accounting/accounts", json={"name": uid("Supplies "), "kind": "expense"})
    general = api.post("/api/accounting/accounts", json={"name": uid("General "), "kind": "expense"})
    r_big = api.post("/api/accounting/rules", json={"name": "big ones", "conditions": [c("text", "contains", tag), c("amount", "gt", 900)],
                                                    "sign": "out", "account_id": supplies["id"]})["id"]
    r_all = api.post("/api/accounting/rules", json={"conditions": [c("text", "contains", tag)], "sign": "out", "account_id": rosa["id"],
                                                    "splits": [{"account_id": general["id"], "pct": 10}], "set_note": "Rosa pay"})["id"]
    bad = client.post("/api/accounting/rules", headers=admin_headers, json={"conditions": [c("amount", "gt", "lots")], "account_id": rosa["id"]})
    assert bad.status_code == 400
    r = _import(client, admin_headers, src["id"], "wf.csv", _csv([
        (f"06/01/{y}", -1000.0, f"ZELLE TO {tag} ON 06/01 REF #R1"), (f"06/08/{y}", -300.0, f"ZELLE TO {tag} ON 06/08 REF #R2"),
        (f"06/15/{y}", -310.0, f"ZELLE TO {tag} ON 06/15 REF #R3")]))
    assert r["auto"] == 2
    rows = {t["description"][-2:]: t for t in _txns(api, start=f"{y}-06-01", end=f"{y}-06-30", source_id=src["id"])["rows"]}
    # R1 matches both rules and they disagree: neither files it -- it waits in Review with a red flag, both rules offered
    assert rows["R1"]["account_id"] is None and not rows["R1"]["splits"] and rows["R1"]["flag"] == "conflict"
    review = {t["id"]: t for t in _txns(api, status="review", all_dates="true")["rows"]}
    assert {c["rule_id"] for c in review[rows["R1"]["id"]]["conflict"]} == {r_big, r_all}
    assert {x["a"]["id"] for x in api.get("/api/accounting/rule-conflicts")} & {r_big, r_all}
    # "big ones" set to win: the next look files R1 there
    api.put(f"/api/accounting/rules/{r_big}", json={"name": "big ones", "conditions": [c("text", "contains", tag), c("amount", "gt", 900)],
                                                    "sign": "out", "account_id": supplies["id"], "wins": True})
    api.post("/api/accounting/rules/" + str(r_big) + "/apply", json={"ids": [rows["R1"]["id"]]})
    rows = {t["description"][-2:]: t for t in _txns(api, start=f"{y}-06-01", end=f"{y}-06-30", source_id=src["id"])["rows"]}
    assert rows["R1"]["account_id"] == supplies["id"] and not rows["R1"]["flag"]
    assert {(x["account_id"], x["amount"]) for x in rows["R2"]["splits"]} == {(general["id"], -30.0), (rosa["id"], -270.0)}
    assert rows["R2"]["note"] == "Rosa pay"
    api.put("/api/accounting/rules-order", json={"ids": [r_all, r_big]})
    assert [x["id"] for x in api.get("/api/accounting/rules") if x["id"] in (r_all, r_big)] == [r_all, r_big]
    # suggest-only: a new matching line isn't filed, it's suggested
    api.put(f"/api/accounting/rules/{r_all}", json={"conditions": [c("text", "contains", tag)], "sign": "out", "account_id": rosa["id"], "action": "suggest"})
    api.put(f"/api/accounting/rules/{r_big}/enabled", json={"enabled": False})
    r2 = _import(client, admin_headers, src["id"], "wf2.csv", _csv([(f"06/22/{y}", -320.0, f"ZELLE TO {tag} ON 06/22 REF #R4")]))
    assert r2["auto"] == 0
    r4 = next(t for t in _txns(api, start=f"{y}-06-01", end=f"{y}-06-30", source_id=src["id"], status="unfiled")["rows"] if t["description"].endswith("R4"))
    assert r4["suggest"]["account_id"] == rosa["id"] and r4["suggest"]["confidence"] == "guess" and r4["suggest"]["rule_id"] == r_all
    # applying from the pop-up files the picked line the rule's way
    lines = api.get(f"/api/accounting/rules/{r_all}/lines")["groups"]
    assert any(t["id"] == r4["id"] for t in lines["unfiled"])
    assert api.post(f"/api/accounting/rules/{r_all}/apply", json={"ids": [r4["id"]]})["filed"] == 1
    cp = api.post(f"/api/accounting/rules/{r_all}/copy")["id"]
    assert not next(x for x in api.get("/api/accounting/rules") if x["id"] == cp)["enabled"]
    x = client.get("/api/accounting/rules-export.xlsx", headers=admin_headers)
    assert x.status_code == 200 and x.content[:2] == b"PK"
    for rid in (r_all, r_big, cp):
        api.delete(f"/api/accounting/rules/{rid}")


def test_learning_look_alikes_corrections_forget_and_suggested_rules(api, client, admin_headers):
    y = 2043
    src = _source(api)
    labor = api.post("/api/accounting/accounts", json={"name": uid("Labor "), "kind": "expense"})
    supplies = api.post("/api/accounting/accounts", json={"name": uid("Supplies "), "kind": "expense"})
    tag = name("PEDROX")
    _import(client, admin_headers, src["id"], "wf3.csv", _csv([(f"07/0{d}/{y}", -50.0 - d, f"ZELLE TO {tag} ON 07/0{d} REF #P{d}") for d in range(1, 7)]))
    got = {t["description"][-2:]: t for t in _txns(api, start=f"{y}-07-01", end=f"{y}-07-31", source_id=src["id"])["rows"]}
    first = api.put(f"/api/accounting/txns/{got['P1']['id']}", json={"account_id": labor["id"]})
    assert any(a["id"] == got["P2"]["id"] for a in first["alike"])          # one before: not sure yet -> the "like this" list
    second = api.put(f"/api/accounting/txns/{got['P2']['id']}", json={"account_id": labor["id"]})
    assert second["filed_more"] >= 4                                        # two before: sure -> filed by itself
    now = {t["description"][-2:]: t for t in _txns(api, start=f"{y}-07-01", end=f"{y}-07-31", source_id=src["id"])["rows"]}
    assert now["P5"]["account_id"] == labor["id"] and now["P5"]["how"] == "auto"
    fix = api.put(f"/api/accounting/txns/{now['P3']['id']}", json={"account_id": supplies["id"]})
    assert fix["taken_back"] >= 1                                           # its look-alikes in Labor went back to To File
    after = {t["description"][-2:]: t for t in _txns(api, start=f"{y}-07-01", end=f"{y}-07-31", source_id=src["id"])["rows"]}
    assert any(after[k]["account_id"] is None for k in ("P4", "P5", "P6"))
    lr = next(l for l in api.get("/api/accounting/learned") if tag in l["key"])
    assert lr["lines"] == 3 and len(lr["accounts"]) == 2
    assert api.post("/api/accounting/learned/forget", json={"key": lr["key"]})["lines"] >= 3
    assert not any(tag in l["key"] and l["lines"] for l in api.get("/api/accounting/learned"))
    api.post("/api/accounting/learned/forget", json={"key": lr["key"], "forget": False})

    # suggested rules: a payee repeated with no account yet; dismissing one hides it
    tag3 = name("CUBEWORKX")
    _import(client, admin_headers, src["id"], "wf4.csv", _csv([(f"08/0{d}/{y}", -1072.5, f"BUSINESS TO BUSINESS ACH {tag3} RENTAL XXXXX250{d} American Traders LLC") for d in range(1, 5)]))
    s = next(x for x in api.get("/api/accounting/rule-suggestions") if tag3 in x["words"])
    assert s["unfiled"] == 4 and s["sign"] == "out" and s["lines"] == 4
    api.post("/api/accounting/rule-suggestions/dismiss", json={"key": s["key"]})
    assert not any(x["key"] == s["key"] for x in api.get("/api/accounting/rule-suggestions"))
    # one people always file the same way is offered with that account
    tag4 = name("FAUSTX")
    _import(client, admin_headers, src["id"], "wf5.csv", _csv([(f"09/0{d}/{y}", -200.0 - d, f"ZELLE TO {tag4} ON 09/0{d} REF #F{d}") for d in range(1, 5)]))
    f = {t["description"][-2:]: t for t in _txns(api, start=f"{y}-09-01", end=f"{y}-09-30", source_id=src["id"])["rows"]}
    for k in ("F1", "F2"):
        api.put(f"/api/accounting/txns/{f[k]['id']}", json={"account_id": labor["id"], "learn": False})
    s4 = next(x for x in api.get("/api/accounting/rule-suggestions") if tag4 in x["words"])
    assert s4["account_id"] == labor["id"] and s4["already"] == 2 and s4["unfiled"] == 2
