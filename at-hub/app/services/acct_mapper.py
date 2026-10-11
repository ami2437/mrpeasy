"""Which account a bank line belongs to, learned from the lines already put in an account (accounting.py).

A bank description is two things: WHO the money went to / came from (the payee: "ZELLE TO SHAH NIRAJ", a wire's
/BNF=, an ACH company, an online transfer's bank) and the NOTE typed with it ("MAALIKS", "SALARY", "GEN WHOLESALE RENT").
The same payee often means different accounts (a Zelle to Niraj is his share, a salary, meals or Richard's share), so
the note words decide between them. Every line someone picks teaches it; nothing is hard-coded per company.

suggest() returns (account_id, confidence, why): confidence "sure" is put in the account by itself, "guess" is shown
as a one-click suggestion, None means a person picks."""
import math
import re
from collections import Counter, defaultdict
from typing import Iterable, List, Optional, Tuple

PATTERNS = [
    (r"ZELLE (?:TO|FROM) ([A-Z][A-Z .'&-]+?)(?: ON \d|$)", "zelle"),
    (r"/BNF=([A-Z0-9 .,&'-]+?)(?: SRF#| TRN#|$)", "wire"),
    (r"BUSINESS TO BUSINESS ACH ([A-Z0-9 .,&'*-]+?)(?: \d{6}|$)", "ach"),
    (r"ONLINE TRANSFER (?:TO|FROM) ([A-Z0-9 .&'-]+?)(?: CHK| REF| BUSINESS| EVERYDAY| WELLS|$)", "transfer"),
    (r"WF DIRECT PAY-PAYMENT- ?([A-Z0-9 .&'%-]+?)(?:-TRAN ID|$)", "directpay"),
    (r"CASH APP\*([A-Z .]+?)(?: [A-Z]+ [A-Z]{2} S\d|$)", "cashapp"),
    (r"^([A-Z][A-Z0-9 .&'*-]+?) (?:PAYMENTS?|PYMNTS|TRANSFER|EPAY|CRCARDPMT|ONLINE PMT|MOBILE PMT|WEB PYMT|AUTOPAY\w*|INT SALE|RENTAL)\b", "payee"),
]
STOP = set("""ON TO FROM REF THE AND FOR OF IN AT BY WITH A AN OR CHK CHECKING EVERYDAY BUSINESS WELLS FARGO WORK CHECKIN
ACH PAYMENT PAYMENTS TRAN ID SRF TRN RFB WT FED LLC INC CO LTD AMERICAN TRADERS MITTAL ANUJ""".split())


def clean(desc: str) -> str:
    return re.sub(r"\s+", " ", (desc or "").upper()).strip()


def payee(desc: str) -> str:
    """'zelle:SHAH NIRAJ', 'wire:AKBARALI ENTERPRISES', 'text:WIRE TRANS SVC' ... -- the part that names who."""
    d = clean(desc)
    for pat, kind in PATTERNS:
        m = re.search(pat, d)
        if m:
            p = re.sub(r"\b\w*\d\w*\b", "", m.group(1))
            p = re.sub(r"\s+", " ", p).strip(" .,-*")
            if p:
                return f"{kind}:{p[:40]}"
    words = [w for w in re.sub(r"[^A-Z ]", " ", d).split() if len(w) > 1]
    return "text:" + " ".join(words[:3])


def payee_label(desc: str) -> str:
    """The payee for people to read: 'Shah Niraj (Zelle)'."""
    k = payee(desc)
    kind, _, name = k.partition(":")
    nice = {"zelle": "Zelle", "wire": "Wire", "ach": "ACH", "transfer": "Transfer", "directpay": "Direct Pay", "cashapp": "Cash App", "payee": "", "text": ""}[kind]
    name = name.title()
    return f"{name} ({nice})" if nice else name


def words(desc: str) -> set:
    """The note words: everything that isn't a number, a reference code or filler. REF #RP0YM68H4P and dates go."""
    d = clean(desc)
    d = re.sub(r"REF ?#\s?\S+|#\S+|\bS\d{10,}\b", " ", d)
    return {w for w in re.sub(r"[^A-Z ]", " ", d).split() if len(w) > 2 and w not in STOP and not re.search(r"\d", w)}


class Mapper:
    """Built from the lines already in an account: (description, amount, account_id[, day of month, weight]). Cheap --
    rebuilt per request. weight 2 = a person corrected AT-HUB's pick on that line (counts double)."""

    def __init__(self, examples: Iterable[tuple]):
        self.by_payee = defaultdict(list)  # payee -> [(note words, sign, account, amount, day, weight)]
        raw = defaultdict(list)
        for e in examples:
            desc, amount, acc = e[0], e[1], e[2]
            day = e[3] if len(e) > 3 else None
            wt = e[4] if len(e) > 4 else 1.0
            if acc:
                raw[payee(desc)].append((words(desc), 1 if amount >= 0 else -1, acc, abs(amount or 0.0), day, wt))
        self.word_acc = defaultdict(Counter)  # note word -> accounts it was seen with (any payee)
        df = Counter()
        for p, ex in raw.items():
            frame = self._frame(p, [e[0] for e in ex])
            for w, sign, acc, amt, day, wt in ex:
                note = w - frame
                self.by_payee[p].append((note, sign, acc, amt, day, wt))
                for x in note:
                    self.word_acc[x][acc] += wt
                df.update(note)
        self.frames = {p: self._frame(p, [e[0] for e in ex]) for p, ex in raw.items()}
        n = sum(len(v) for v in raw.values())
        self.idf = {w: math.log((n + 1) / (c + 0.5)) for w, c in df.items()}

    @staticmethod
    def _frame(p: str, notes: List[set]) -> set:
        """Words that come with this payee's lines every time (ZELLE, the payee's own name, the bank's wording):
        they say who, not what for."""
        own = set(p.partition(":")[2].split()) | {"ZELLE", "ONLINE", "TRANSFER", "DIRECT", "PAY", "MONEY", "AUTHORIZED", "CASH", "APP", "CARD", "WIRE", "BNF"}
        if len(notes) < 3:
            return own
        c = Counter(x for n in notes for x in n)
        return own | {x for x, k in c.items() if k / len(notes) >= 0.6}

    def suggest(self, desc: str, amount: float, rules=None, day: Optional[int] = None) -> Tuple[Optional[int], Optional[str], str]:
        """rules: not used here any more (accounting.py runs the rules first); kept so old callers still work."""
        p = payee(desc)
        who = payee_label(desc)
        sign = 1 if amount >= 0 else -1
        amt = abs(amount or 0.0)
        ex = [e for e in self.by_payee.get(p, []) if e[1] == sign] or self.by_payee.get(p, [])
        note = words(desc) - self.frames.get(p, self._frame(p, []))
        score = defaultdict(float)
        support = Counter()  # same-payee lines sharing a note word, per account
        same_amt = Counter()  # same-payee lines with the very same amount (rent, a loan payment), per account
        use_amt = amt >= 20 and (not note or amt % 50 > 0.009)  # round $500s say little when a note says more
        for ew, _s, acc, eamt, eday, wt in ex:  # lines from the same payee: the closer the note, the more it counts
            common = note & ew
            if common:
                score[acc] += wt * sum(self.idf.get(x, 1.0) for x in common)
                support[acc] += wt
            elif not note and not ew:
                score[acc] += wt * 1.0
            if use_amt and abs(eamt - amt) <= max(0.01, amt * 0.002):
                score[acc] += wt * 1.5
                same_amt[acc] += wt
                if day and eday and min(abs(day - eday), 31 - abs(day - eday)) <= 2:  # and about the same day of the month
                    score[acc] += wt * 0.5
        for x in note:  # a note word seen with an account on other payees' lines too (MTB LOAN -> M & T)
            seen = self.word_acc.get(x)
            if seen and len(seen) <= 3:
                tot = sum(seen.values())
                for acc, k in seen.items():
                    score[acc] += 0.6 * self.idf.get(x, 1.0) * k / tot
        accs = Counter()
        for e in ex:
            accs[e[2]] += e[5]
        if not score:
            if not accs:
                return None, None, "new payee"
            acc, n = accs.most_common(1)[0]
            if len(accs) == 1:
                return acc, ("sure" if n >= 2 and not note else "guess"), f"{who}: {_n(n)} before"
            share = n / sum(accs.values())
            return acc, ("guess" if share >= 0.5 else None), f"{who}: usually this ({_n(n)} of {_n(sum(accs.values()))})"
        ranked = sorted(score.items(), key=lambda x: -x[1])
        best, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        if len(accs) == 1 and best in accs and accs[best] >= 2 and top >= 2 * second:
            return best, "sure", f"{who}: {_n(accs[best])} before"
        if len(accs) == 1 and best in accs and note and not p.startswith("text:") and any(e[0] == note for e in ex):
            return best, "sure", f"{who}: same as before"  # the same payee and the very same note words
        if note and support[best] >= 2 and top >= 3 * max(second, 0.5):
            return best, "sure", f"{who} + the note words"
        if not note and same_amt[best] >= 2 and len([a for a in same_amt if same_amt[a]]) == 1 and top >= 2 * max(second, 0.5):
            return best, "sure", f"{who}: the same amount as before"
        if same_amt[best]:
            return best, "guess", f"{who}: the same amount as before"
        return best, "guess", (f"{who}: the note words point here" if note else f"{who}: usually this")


def _n(x: float) -> str:
    return str(int(round(x)))
