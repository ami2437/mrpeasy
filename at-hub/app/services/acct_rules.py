"""Accounting rules, QuickBooks-style (accounting.py, routes/accounting.py).

A rule: for money in / out / both, on some bank accounts (or all), when ALL (or ANY) of its conditions hold, file the
line in an account -- or split it by % / $ over several, set the payee, add a note -- by itself ("file") or only as a
suggestion ("suggest"). Rules run in their order (priority); the first one that matches wins, before anything AT-HUB
learned. A condition looks at one part of the line:

  text         anywhere in the line: the bank's wording, our wording, the payee, the note (NOT QuickBooks' memo)
  description  the bank's wording (and ours for a typed-in line)
  payee        who (read from the description, or typed)
  note         a person's note
  memo         the text QuickBooks pasted after the bank's wording (its own bank-rule memo -- not the bank's)
  weekday      day of the week (MON ... SUN)
  amount       the amount (always positive -- money in / out is the rule's own setting)
  day          day of the month (rent on the 1st, payroll on the 15th)
  date         the line's date: on / on or after / on or before / between (a rule that only holds for a while)
  group        a group of conditions with its own ALL / ANY -- (A or B) and not C

Words match whole words ("SAUL" doesn't hit "SAULT"); "contains" needs every word (any order), a "quoted phrase" needs
them side by side. Every text test has its NOT (doesn't contain, isn't, doesn't start with, contains none of) and a
pattern (regular expression) for power use. When two switched-on rules match one line and say different things, neither
files it -- it waits in Review ("rules disagree") -- unless one of them is marked to win. Rules made before this (one
"contains" + amount range) read as the same thing (spec()).

A rule can have BRANCHES (user, 2026-10-12: "JASON or PESO -> Jason, but SUPPLIES -> Supplies"): its own conditions (if
any) must hold, then the first branch whose conditions match decides the account / split / note; the last branch may be
"otherwise" (no conditions -- only allowed when the rule has conditions of its own). effective() gives the rule as it
applies to one line, and everything that files or compares rules uses that."""
import json
import re
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from app.services.money import cents

TEXT_FIELDS = {"text": "Anywhere In The Line", "description": "Bank Description", "payee": "Payee", "note": "Note",
               "memo": "QuickBooks Memo", "weekday": "Day Of Week"}
NUM_FIELDS = {"amount": "Amount", "day": "Day Of Month"}
DATE_FIELDS = {"date": "Date"}
FIELDS = {**TEXT_FIELDS, **NUM_FIELDS, **DATE_FIELDS}
TEXT_OPS = {"contains": "contains", "not_contains": "doesn't contain", "any_of": "contains any of", "not_any_of": "contains none of",
            "is": "is exactly", "not_is": "isn't exactly", "starts": "starts with", "not_starts": "doesn't start with",
            "ends": "ends with", "regex": "matches pattern"}
NUM_OPS = {"eq": "equals", "ne": "isn't", "gt": "is more than", "lt": "is less than", "between": "is between"}
DATE_OPS = {"on": "is on", "after": "is on or after", "before": "is on or before", "between": "is between"}
WEEKDAYS = ["MON MONDAY", "TUE TUESDAY", "WED WEDNESDAY", "THU THURSDAY", "FRI FRIDAY", "SAT SATURDAY", "SUN SUNDAY"]


def _words(s: str) -> List[str]:
    return re.findall(r"[A-Z0-9&']+", (s or "").upper())


def _norm(s: str) -> str:
    return " ".join(_words(s))


def line_of(t) -> dict:
    """The parts of a transaction a rule can look at (an AcctTxn, or a dict with the same names)."""
    g = (lambda k: t.get(k)) if isinstance(t, dict) else (lambda k: getattr(t, k, None))
    bank, ours = g("bank_description") or "", g("description") or ""
    desc = " ".join(x for x in (bank, ours if ours != bank else "") if x)
    d = g("date")
    day_ = d.date() if isinstance(d, datetime) else d
    return {"description": desc, "payee": g("payee") or "", "note": g("note") or "", "memo": g("qb_memo") or "",
            "text": "\n".join(x for x in (desc, g("payee") or "", g("note") or "") if x),  # one part per line: a phrase can't span two
            "amount": float(g("amount") or 0.0), "source_id": g("source_id"), "day": d.day if d is not None and hasattr(d, "day") else None,
            "date": day_ if isinstance(day_, date) else None,
            "weekday": WEEKDAYS[day_.weekday()] if isinstance(day_, date) else ""}


def _has(words: List[str], phrase: List[str]) -> bool:
    n = len(phrase)
    return any(words[i:i + n] == phrase for i in range(len(words) - n + 1)) if n else False


def _contains(text: str, value: str) -> bool:
    """Every word of value is a whole word in text; "quoted parts" must be side by side."""
    words = _words(text)
    value = value or ""
    phrases = re.findall(r'"([^"]+)"', value)
    loose = _words(re.sub(r'"[^"]*"', " ", value))
    if not phrases and not loose:
        return False
    ws = set(words)
    parts = [_words(x) for x in str(text).split("\n")]
    return all(w in ws for w in loose) and all(any(_has(pw, _words(p)) for pw in parts) for p in phrases)


def _filled(c: dict) -> bool:
    if c.get("field") == "group":
        return any(_filled(x) for x in c.get("conditions") or [])
    return c.get("value") not in (None, "", [])


def _date(v) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def cond_ok(c: dict, line: dict) -> bool:
    f, op, v = c.get("field") or "text", c.get("op") or "contains", c.get("value")
    if f == "group":
        subs = [x for x in c.get("conditions") or [] if _filled(x)]
        if not subs:
            return False
        ok = [cond_ok(x, line) for x in subs]
        res = any(ok) if c.get("match") == "any" else all(ok)
        return not res if c.get("not") else res
    if f in DATE_FIELDS:
        d = line.get("date")
        if d is None:
            return False
        if op == "between":
            lo, hi = (_date(v[0]), _date(v[1])) if isinstance(v, (list, tuple)) and len(v) == 2 else (None, None)
            return bool(lo and hi) and min(lo, hi) <= d <= max(lo, hi)
        x = _date(v)
        return x is not None and {"on": d == x, "after": d >= x, "before": d <= x}.get(op, False)
    if f in NUM_FIELDS:
        x = abs(line["amount"]) if f == "amount" else line.get("day")
        if x is None:
            return False
        try:
            if op == "between":
                lo, hi = (float(v[0]), float(v[1])) if isinstance(v, (list, tuple)) else (None, None)
                return lo is not None and min(lo, hi) - 0.005 <= x <= max(lo, hi) + 0.005
            y = float(v)
        except (TypeError, ValueError, IndexError):
            return False
        return {"eq": abs(x - y) < 0.005, "ne": abs(x - y) >= 0.005, "gt": x > y + 0.005, "lt": x < y - 0.005}.get(op, False)
    text = line.get(f) or ""
    if op == "contains":
        return _contains(text, v)
    if op == "not_contains":
        return bool(_words(v)) and not _contains(text, v)
    if op in ("any_of", "not_any_of"):  # "SAUL, FAUSTINO, LABOR"
        parts = [part for part in str(v or "").split(",") if part.strip()]
        hit = any(_contains(text, part) for part in parts)
        return hit if op == "any_of" else (bool(parts) and not hit)
    if op == "regex":
        try:
            return bool(re.search(str(v), text, re.I))
        except re.error:
            return False
    t, val = _norm(text), _norm(v)
    if not val:
        return False
    if op in ("not_is", "not_starts"):
        return not {"not_is": t == val, "not_starts": t == val or t.startswith(val + " ")}[op]
    return {"is": t == val, "starts": t == val or t.startswith(val + " "), "ends": t == val or t.endswith(" " + val)}.get(op, False)


def spec(r) -> dict:
    """A rule (AcctRule or dict) as one dict; an old one-word-list rule reads as conditions."""
    g = (lambda k: r.get(k)) if isinstance(r, dict) else (lambda k: getattr(r, k, None))
    conds = g("conditions")
    if isinstance(conds, str):
        conds = json.loads(conds or "[]")
    if not conds:
        conds = []
        if (g("contains") or "").strip():
            conds.append({"field": "text", "op": "contains", "value": g("contains")})
        lo, hi = g("min_amount"), g("max_amount")
        if lo is not None and hi is not None:
            conds.append({"field": "amount", "op": "between", "value": [lo, hi]})
        elif lo is not None:
            conds.append({"field": "amount", "op": "gt", "value": lo - 0.01})
        elif hi is not None:
            conds.append({"field": "amount", "op": "lt", "value": hi + 0.01})
    splits = g("splits")
    if isinstance(splits, str):
        splits = json.loads(splits or "[]")
    srcs = g("source_ids")
    if isinstance(srcs, str):
        srcs = json.loads(srcs or "[]")
    branches = g("branches")
    if isinstance(branches, str):
        branches = json.loads(branches or "[]")
    return {"id": g("id"), "name": g("name") or "", "match": g("match_mode") or g("match") or "all", "conditions": conds,
            "sign": g("sign") or None, "source_ids": [int(x) for x in (srcs or [])], "account_id": g("account_id"),
            "splits": splits or [], "set_payee": g("set_payee") or None, "set_note": g("set_note") or None,
            "action": g("action") or "file", "enabled": g("enabled") is not False, "priority": g("priority") or 0,
            "review": bool(g("review")), "wins": bool(g("wins")),
            "branches": [{"label": b.get("label") or "", "match": b.get("match") or "all", "conditions": b.get("conditions") or [],
                          "account_id": int(b["account_id"]) if b.get("account_id") else None, "splits": b.get("splits") or [],
                          "set_note": b.get("set_note") or None} for b in (branches or [])]}


def _base_ok(s: dict, line: dict, need_conditions: bool) -> bool:
    if s["sign"] == "in" and line["amount"] < 0 or s["sign"] == "out" and line["amount"] >= 0:
        return False
    if s["source_ids"] and line.get("source_id") not in s["source_ids"]:
        return False
    conds = [c for c in s["conditions"] if _filled(c)]
    if not conds:
        return not need_conditions
    ok = [cond_ok(c, line) for c in conds]
    return all(ok) if s["match"] != "any" else any(ok)


def _branch_ok(b: dict, line: dict) -> Optional[bool]:
    """None = an "otherwise" branch (no conditions)."""
    conds = [c for c in b.get("conditions") or [] if _filled(c)]
    if not conds:
        return None
    ok = [cond_ok(c, line) for c in conds]
    return any(ok) if b.get("match") == "any" else all(ok)


def effective(s: dict, line: dict) -> Optional[dict]:
    """The rule as it applies to this line (None = it doesn't): itself, or -- a rule with branches -- the first branch
    that matches (its account / split / note)."""
    br = s.get("branches") or []
    if not br:
        return s if _base_ok(s, line, True) else None
    if not _base_ok(s, line, False):
        return None
    for i, b in enumerate(br):
        ok = _branch_ok(b, line)
        if ok or (ok is None and i == len(br) - 1):
            return {**s, "account_id": b.get("account_id"), "splits": b.get("splits") or [], "set_note": b.get("set_note") or s.get("set_note"),
                    "branch": i, "branch_label": b.get("label") or (f"branch {i + 1}" if ok else "otherwise")}
    return None


def matches(s: dict, line: dict) -> bool:
    return effective(s, line) is not None


def first_match(specs: List[dict], line: dict) -> Optional[dict]:
    """Rules in order; the first switched-on one that matches (as it applies to the line)."""
    for s in specs:
        if s["enabled"]:
            e = effective(s, line)
            if e:
                return e
    return None


def outcome(s: dict) -> tuple:
    """What a rule does to a line -- two rules that do the same thing don't disagree."""
    sp = tuple(sorted((int(p["account_id"]), p.get("pct"), p.get("amount")) for p in s["splits"]))
    return (s["account_id"], sp, s["action"])


def decide_rules(specs: List[dict], line: dict) -> Tuple[Optional[dict], List[dict]]:
    """(the rule that decides, the rules that disagree): every switched-on rule that matches; if they don't all say the
    same thing, the first one marked "wins" decides -- else nobody does and the line waits in Review."""
    hits = [e for e in (effective(s, line) for s in specs if s["enabled"]) if e]
    if not hits:
        return None, []
    if len({outcome(s) for s in hits}) == 1:
        return hits[0], []
    win = next((s for s in hits if s.get("wins")), None)
    if win:
        return win, []
    return None, hits


def explain(s: dict, line: dict) -> dict:
    """Why a rule does / doesn't match one line: each condition true / false, money in / out, the bank account."""
    def one(c):
        d = {"text": describe({**s, "branches": [], "match": "all", "conditions": [c], "sign": None, "source_ids": []}),
             "ok": cond_ok(c, line) if _filled(c) else None}
        if c.get("field") == "group":
            d["parts"] = [one(x) for x in c.get("conditions") or [] if _filled(x)]
        return d
    sign_ok = not (s["sign"] == "in" and line["amount"] < 0 or s["sign"] == "out" and line["amount"] >= 0)
    src_ok = not s["source_ids"] or line.get("source_id") in s["source_ids"]
    e = effective(s, line)
    out = {"matches": e is not None, "enabled": s["enabled"], "sign_ok": sign_ok, "source_ok": src_ok,
           "match": s["match"], "conditions": [one(c) for c in s["conditions"] if _filled(c)]}
    if s.get("branches"):
        out["branches"] = [{"label": b.get("label") or f"branch {i + 1}", "ok": _branch_ok(b, line), "account_id": b.get("account_id"),
                            "conditions": [one(c) for c in b.get("conditions") or [] if _filled(c)]} for i, b in enumerate(s["branches"])]
        out["branch"] = e.get("branch") if e else None
    return out


def parts(s: dict, amount: float) -> List[Tuple[int, float]]:
    """How a rule's split shares out a line: % or $ parts, the rest in the rule's own account."""
    if not s["splits"]:
        return [(s["account_id"], amount)]
    out, left = [], cents(amount)
    sign = -1 if amount < 0 else 1
    for p in s["splits"]:
        if p.get("pct") not in (None, ""):
            a = cents(amount * float(p["pct"]) / 100)
        else:
            a = cents(sign * abs(float(p.get("amount") or 0)))
        if abs(a) > abs(left) + 0.005:
            a = left
        out.append((int(p["account_id"]), a))
        left = cents(left - a)
    if abs(left) > 0.004:
        if s["account_id"]:
            out.append((s["account_id"], left))
        else:
            out[-1] = (out[-1][0], cents(out[-1][1] + left))
    merged: Dict[int, float] = {}
    for aid, a in out:
        merged[aid] = cents(merged.get(aid, 0.0) + a)
    return [(k, v) for k, v in merged.items() if abs(v) > 0.004]


def describe(s: dict, names: Optional[Dict[int, str]] = None) -> str:
    """'Money out · Anywhere contains GOMEZ SAUL and Amount is more than 100'."""
    bits = []
    for c in s["conditions"]:
        f, op, v = c.get("field") or "text", c.get("op") or "contains", c.get("value")
        if f == "group":
            inner = describe({**s, "branches": [], "match": c.get("match") or "all", "conditions": c.get("conditions") or [], "sign": None, "source_ids": []})
            bits.append(f"{'not ' if c.get('not') else ''}({inner})")
            continue
        if f in DATE_FIELDS:
            val = f"{v[0]} and {v[1]}" if op == "between" and isinstance(v, (list, tuple)) else f"{v}"
            bits.append(f"{FIELDS[f]} {DATE_OPS.get(op, op)} {val}")
            continue
        if f in NUM_FIELDS:
            val = f"{v[0]} and {v[1]}" if op == "between" and isinstance(v, (list, tuple)) else f"{v}"
            bits.append(f"{FIELDS[f]} {NUM_OPS.get(op, op)} {val}")
        else:
            bits.append(f"{FIELDS.get(f, f)} {TEXT_OPS.get(op, op)} {v}")
    joined = (" and " if s["match"] != "any" else " or ").join(bits)
    if s.get("branches"):
        bs = []
        for i, b in enumerate(s["branches"]):
            inner = describe({**s, "branches": [], "match": b.get("match") or "all", "conditions": b.get("conditions") or [], "sign": None, "source_ids": []})
            bs.append(f"{'if' if i == 0 else 'else if'} {inner}" if inner else "otherwise")
        joined = (joined + " · then " if joined else "") + "; ".join(bs)
    pre = {"in": "Money in · ", "out": "Money out · "}.get(s["sign"] or "", "")
    if s["source_ids"] and names:
        pre += ", ".join(names.get(i, "?") for i in s["source_ids"]) + " · "
    return pre + joined


def check(s: dict) -> Optional[str]:
    """What's wrong with a rule before saving it (None = fine)."""
    conds = [c for c in s["conditions"] if _filled(c)]
    br = s.get("branches") or []
    if not conds and not br:
        return "Add at least one condition with a value"
    err = _check_conds(conds)
    if err:
        return err
    if br:
        for i, b in enumerate(br):
            bc = [c for c in b.get("conditions") or [] if _filled(c)]
            if not bc and (i != len(br) - 1 or not conds):
                return ("Only the last branch can be \"otherwise\"" if i != len(br) - 1
                        else "An \"otherwise\" branch needs conditions on the rule itself (else it would take every line)")
            err = _check_conds(bc)
            if err:
                return f"Branch {i + 1}: {err}"
            if not b.get("account_id") and not b.get("splits"):
                return f"Branch {i + 1}: pick the account"
        return None
    if not s["account_id"] and not s["splits"]:
        return "Pick the account"
    pcts = [float(p["pct"]) for p in s["splits"] if p.get("pct") not in (None, "")]
    if sum(pcts) > 100.001:
        return "The split adds up to more than 100%"
    if s["splits"] and not s["account_id"] and pcts and abs(sum(pcts) - 100) > 0.001 and len(pcts) == len(s["splits"]):
        return "The split must add up to 100% (or pick an account for the rest)"
    return None


def _check_conds(conds: List[dict]) -> Optional[str]:
    for c in conds:
        f, op = c.get("field"), c.get("op")
        if f == "group":
            subs = [x for x in c.get("conditions") or [] if _filled(x)]
            if not subs:
                return "A group needs at least one condition"
            err = _check_conds(subs)
            if err:
                return err
            continue
        if f not in FIELDS:
            return f"Unknown field {f}"
        if f in NUM_FIELDS and op not in NUM_OPS or f in TEXT_FIELDS and op not in TEXT_OPS or f in DATE_FIELDS and op not in DATE_OPS:
            return f"{FIELDS[f]} can't use '{op}'"
        if f in NUM_FIELDS:
            try:
                [float(x) for x in (c["value"] if op == "between" else [c["value"]])]
            except (TypeError, ValueError):
                return f"{FIELDS[f]} needs a number"
        if f in DATE_FIELDS:
            vals = c["value"] if op == "between" and isinstance(c["value"], (list, tuple)) else [c["value"]]
            if not all(_date(x) for x in vals):
                return "Date needs a date (YYYY-MM-DD)"
        if op == "regex":
            try:
                re.compile(str(c["value"]))
            except re.error as e:
                return f"The pattern doesn't work: {e}"
    return None
