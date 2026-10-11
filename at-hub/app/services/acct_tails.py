"""QuickBooks' memo pasted onto bank lines (accounting.py imports, the one-off clean of lines already in).

A QuickBooks bank rule stamps ITS OWN text -- the description of the line the rule was made from -- after the bank's
wording on every line it adds: "ONLINE TRANSFER TO M & T BANK ... CC PAYMENT BUSINESS" + "WF Direct Pay-Payment- NIRAJ
Share-Tran ID DPAAAC7JZT". That text isn't the bank's, says nothing true about the line, and fooled rules ("NIRAJ SHARE"
-> Niraj). Found by what it is, never by a fixed list (user's file, 2026-10-11, checked against the Wells Fargo
statements: 170 lines, the bank's wording on every one):

  doubled    the whole line twice ("DIRECT PAY MONTHLY BASE DIRECT PAY MONTHLY BASE")
  another    the end of the line is another line of the file (5+ days away), word for word, with a reference /
             date code in it -- "... WF Direct Pay-Payment- NIRAJ Share-Tran ID DPAAAC7JZT" (the 3/5/2025 line)
  repeated   the same ending on 2+ lines, carrying a date code far from the line's own date and none near it
             ("... WT 250113-033290 AXIS BANK ..." on a 2024 line) -- the bank's own codes are always near its date

The ending must also start like a line does (a first word some line of the file starts with: WT, ZELLE, WF ...), so the
bank's own repeated endings ("... ANUJ MITTAL ANUJ MITTAL", "Wells Fargo Bank EDI PYMNTS ...") stay."""
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

_MASK = re.compile(r"([A-WYZ#]*)X{3,}(\d+)")


def _tok(w: str) -> str:
    return re.sub(r"[^A-Z0-9*#/&,.\-]", "", w.upper())


def _same(a: str, b: str) -> bool:
    """Two words: equal, or a masked number (XXXXXXXX7437 / OWXXXXXXXX733661) whose visible part fits the other."""
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        m = _MASK.fullmatch(x)
        if m and y.startswith(m.group(1)) and y.endswith(m.group(2)) and len(y) >= len(x) - 2:
            return True
    return False


def _seq_same(a: List[str], b: List[str]) -> bool:
    return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))


def _ends_with(words: List[str], tail: List[str]) -> bool:
    return len(words) > len(tail) and _seq_same(words[-len(tail):], tail)


def _ideish(w: str) -> bool:
    w = w.strip("#")
    return len(w) >= 5 and bool(re.search(r"\d", w))


def _codes(words: List[str], near: date) -> Tuple[bool, bool]:
    """(a date code far from the line's date, one near it): 250113 / 250113-033290 (YYMMDD), ON 03/21 (MM/DD)."""
    far = close = False

    def judge(d: Optional[date]):
        nonlocal far, close
        if not d:
            return
        if abs((d - near).days) <= 10:
            close = True
        else:
            far = True
    for i, w in enumerate(words):
        m = re.fullmatch(r"(\d{2})(\d{2})(\d{2})(?:-\d+)?", w)
        if m:
            try:
                judge(date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                pass
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", w)
        if m and (i and words[i - 1] == "ON" or m.group(3)):
            mo, da = int(m.group(1)), int(m.group(2))
            yrs = [int(m.group(3)) % 100 + 2000] if m.group(3) else [near.year - 1, near.year, near.year + 1]
            best = None
            for y in yrs:
                try:
                    d = date(y, mo, da)
                except ValueError:
                    continue
                if best is None or abs((d - near).days) < abs((best - near).days):
                    best = d
            judge(best)
    return far, close


def detect(rows: List[dict]) -> List[Tuple[str, Optional[str], Optional[str]]]:
    """For each {date, description}: (the bank's wording, QuickBooks' pasted text or None, why)."""
    words = [[_tok(w) for w in (r.get("description") or "").split()] for r in rows]
    raw = [(r.get("description") or "").split() for r in rows]
    starters = Counter(w[0] for w in words if w)
    starters = {k for k, c in starters.items() if c >= 2}
    ends = defaultdict(list)  # last word -> rows, to count a repeated ending quickly
    for i, w in enumerate(words):
        if w:
            ends[w[-1]].append(i)
    by_first = defaultdict(list)
    for i, w in enumerate(words):
        if w:
            by_first[w[0]].append(i)
    out = []
    for i, (r, w) in enumerate(zip(rows, words)):
        n = len(w)
        d = r["date"]
        res = (r.get("description") or "", None, None)
        if n >= 4 and n % 2 == 0 and _seq_same(w[:n // 2], w[n // 2:]):
            out.append((" ".join(raw[i][:n // 2]), " ".join(raw[i][n // 2:]), "doubled"))
            continue
        found = None
        for k in range(2, n - 2):  # the longest ending first
            tail = w[k:]
            if len(tail) < 3:
                continue
            if any(_ideish(x) for x in tail):  # another line of the file, word for word (or its start) -- that line starts this way
                for j in by_first.get(tail[0], []):
                    if j == i or abs((rows[j]["date"] - d).days) < 5:
                        continue
                    o = words[j]
                    if len(o) >= len(tail) and _seq_same(o[:len(tail)], tail) and (len(o) == len(tail) or len(tail) >= 4):
                        found = (k, "another")
                        break
            if found:
                break
            far, close = _codes(tail, d)
            if far and not close and tail[0] in starters:
                same = sum(1 for j in ends.get(w[-1], []) if j != i and _ends_with(words[j], tail))
                if same >= 1:
                    found = (k, "repeated")
                    break
        if found:
            k, why = found
            res = (" ".join(raw[i][:k]), " ".join(raw[i][k:]), why)
        out.append(res)
    return out


def summary(rows: List[dict], res) -> Dict[str, int]:
    c = Counter(why for _c, memo, why in res if memo)
    return {"lines": sum(c.values()), **c}
