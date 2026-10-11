"""Accounting rules, QuickBooks-style (accounting.py, routes/accounting.py).

A rule: for money in / out / both, on some bank accounts (or all), when ALL (or ANY) of its conditions hold, file the
line in an account -- or split it by % / $ over several, set the payee, add a note -- by itself ("file") or only as a
suggestion ("suggest"). Rules run in their order (priority); the first one that matches wins, before anything AT-HUB
learned. A condition looks at one part of the line:

  text         anywhere in the line: the bank's wording, our wording, the payee, the note
  description  the bank's wording (and ours for a typed-in line)
  payee        who (read from the description, or typed)
  note         a person's note
  amount       the amount (always positive -- money in / out is the rule's own setting)
  day          day of the month (rent on the 1st, payroll on the 15th)

Words match whole words ("SAUL" doesn't hit "SAULT"); "contains" needs every word (any order), a "quoted phrase" needs
them side by side. Rules made before this (one "contains" + amount range) read as the same thing (spec())."""
import json
import re
from typing import Dict, List, Optional, Tuple

from app.services.money import cents

TEXT_FIELDS = {"text": "Anywhere In The Line", "description": "Bank Description", "payee": "Payee", "note": "Note"}
NUM_FIELDS = {"amount": "Amount", "day": "Day Of Month"}
FIELDS = {**TEXT_FIELDS, **NUM_FIELDS}
TEXT_OPS = {"contains": "contains", "not_contains": "doesn't contain", "any_of": "contains any of", "is": "is exactly",
            "starts": "starts with", "ends": "ends with"}
NUM_OPS = {"eq": "equals", "ne": "isn't", "gt": "is more than", "lt": "is less than", "between": "is between"}


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
    return {"description": desc, "payee": g("payee") or "", "note": g("note") or "",
            "text": "\n".join(x for x in (desc, g("payee") or "", g("note") or "") if x),  # one part per line: a phrase can't span two
            "amount": float(g("amount") or 0.0), "source_id": g("source_id"), "day": d.day if d is not None and hasattr(d, "day") else None}


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


def cond_ok(c: dict, line: dict) -> bool:
    f, op, v = c.get("field") or "text", c.get("op") or "contains", c.get("value")
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
    if op == "any_of":  # "SAUL, FAUSTINO, LABOR"
        return any(_contains(text, part) for part in str(v or "").split(",") if part.strip())
    t, val = _norm(text), _norm(v)
    if not val:
        return False
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
    return {"id": g("id"), "name": g("name") or "", "match": g("match_mode") or g("match") or "all", "conditions": conds,
            "sign": g("sign") or None, "source_ids": [int(x) for x in (srcs or [])], "account_id": g("account_id"),
            "splits": splits or [], "set_payee": g("set_payee") or None, "set_note": g("set_note") or None,
            "action": g("action") or "file", "enabled": g("enabled") is not False, "priority": g("priority") or 0}


def matches(s: dict, line: dict) -> bool:
    if s["sign"] == "in" and line["amount"] < 0 or s["sign"] == "out" and line["amount"] >= 0:
        return False
    if s["source_ids"] and line.get("source_id") not in s["source_ids"]:
        return False
    conds = [c for c in s["conditions"] if c.get("value") not in (None, "", [])]
    if not conds:
        return False
    ok = [cond_ok(c, line) for c in conds]
    return all(ok) if s["match"] != "any" else any(ok)


def first_match(specs: List[dict], line: dict) -> Optional[dict]:
    """Rules in order; the first switched-on one that matches."""
    for s in specs:
        if s["enabled"] and matches(s, line):
            return s
    return None


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
        if f in NUM_FIELDS:
            val = f"{v[0]} and {v[1]}" if op == "between" and isinstance(v, (list, tuple)) else f"{v}"
            bits.append(f"{FIELDS[f]} {NUM_OPS.get(op, op)} {val}")
        else:
            bits.append(f"{FIELDS.get(f, f)} {TEXT_OPS.get(op, op)} {v}")
    joined = (" and " if s["match"] != "any" else " or ").join(bits)
    pre = {"in": "Money in · ", "out": "Money out · "}.get(s["sign"] or "", "")
    if s["source_ids"] and names:
        pre += ", ".join(names.get(i, "?") for i in s["source_ids"]) + " · "
    return pre + joined


def check(s: dict) -> Optional[str]:
    """What's wrong with a rule before saving it (None = fine)."""
    conds = [c for c in s["conditions"] if c.get("value") not in (None, "", [])]
    if not conds:
        return "Add at least one condition with a value"
    for c in conds:
        f, op = c.get("field"), c.get("op")
        if f not in FIELDS:
            return f"Unknown field {f}"
        if f in NUM_FIELDS and op not in NUM_OPS or f in TEXT_FIELDS and op not in TEXT_OPS:
            return f"{FIELDS[f]} can't use '{op}'"
        if f in NUM_FIELDS:
            try:
                [float(x) for x in (c["value"] if op == "between" else [c["value"]])]
            except (TypeError, ValueError):
                return f"{FIELDS[f]} needs a number"
    if not s["account_id"] and not s["splits"]:
        return "Pick the account"
    pcts = [float(p["pct"]) for p in s["splits"] if p.get("pct") not in (None, "")]
    if sum(pcts) > 100.001:
        return "The split adds up to more than 100%"
    if s["splits"] and not s["account_id"] and pcts and abs(sum(pcts) - 100) > 0.001 and len(pcts) == len(s["splits"]):
        return "The split must add up to 100% (or pick an account for the rest)"
    return None
