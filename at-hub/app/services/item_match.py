"""Rank our items against a vendor's (or customer's) description of a part.

Fastener descriptions are written many ways -- "BOLT_HH_5/8-11x1-1/4_A307A_HDG" vs
"5/8"-11 x 1-1/4" Hex Bolt A307 Galv" -- so plain text similarity misses. This pulls out
the attributes that identify a fastener (diameter, threads, length, type, grade, finish),
normalises synonyms, and scores weighted overlap. Runs locally; no AI call needed.
"""
import difflib
import re
from typing import Dict, List, Optional

# words that mean the same thing on fastener paperwork
SYNONYMS = {
    "hh": "hex", "hx": "hex", "hexagon": "hex", "hvy": "heavy", "hv": "heavy", "hd": "heavy",
    "galvanized": "galv", "galvanised": "galv", "galvd": "galv",
    "zp": "zinc", "zn": "zinc", "znc": "zinc", "yz": "yellowzinc", "pln": "plain", "blk": "plain", "black": "plain",
    "ss": "stainless", "ss304": "304", "ss316": "316", "316l": "316",
    "nuts": "nut", "bolts": "bolt", "washers": "washer", "studs": "stud", "screws": "screw",
    "fw": "flatwasher", "flat": "flatwasher", "hardened": "hardened", "hrdn": "hardened", "hrd": "hardened",
    "gr": "grade", "grd": "grade", "fully": "", "threaded": "threaded", "thd": "threaded", "rod": "rod",
    "w": "", "with": "", "and": "", "x": "", "the": "", "of": "", "in": "", "inch": "",
}
TYPES = {"bolt", "nut", "washer", "flatwasher", "stud", "screw", "rod", "clamp", "collar", "pin", "anchor"}
FINISH = {"galv", "hdg", "mechgalv", "zinc", "yellowzinc", "plain", "stainless", "304", "316"}
# Hot-dip and mechanical galvanizing are different finishes; plain "galv" matches either.
FINISH_PHRASES = [(r"hot[\s-]*dip(?:ped)?[\s-]*galv\w*", " hdg "), (r"mech(?:anical(?:ly)?)?\.?[\s-]*galv\w*", " mechgalv ")]
GRADE_RE = re.compile(r"^(a\d{3}[a-z]?|f\d{3,4}|b7m?|2hm?|gr\d|grade\d|[258])$")
SIZE_RE = re.compile(r"(?<![\d/.-])(\d+(?:-\d+)?/\d+|\d+(?:\.\d+)?)\s*\"?\s*-\s*(\d+)(?![\d/])\s*(?:unc|unf|un)?\"?(?:\s*(?:x|\*)\s*(\d+(?:-\d+)?(?:/\d+)?(?:\.\d+)?))?", re.I)
# diameter x length with no thread count: 7/8 x 2-1/4, 3/4" x 3"
DIA_LEN_RE = re.compile(r"(?<![\d/-])(\d+(?:-\d+)?/\d+|\d*\.\d+|\d+)\s*\"?\s*(?:x|\*)\s*(\d+(?:-\d+)?(?:/\d+)?(?:\.\d+)?)", re.I)
BARE_DIA_RE = re.compile(r"(?<![\d/-])(\d+-\d+/\d+|\d+/\d+|\d*\.\d+)\s*(?:\"|in\b|inch\b)?")
WEIGHTS = {"dia": 4.0, "tpi": 2.0, "len": 3.0, "type": 2.5, "grade": 2.0, "finish": 1.5, "word": 0.5}
# the nut that goes WITH a bolt ("... w/ A194-2H HEX NUT") describes its companion, not what the line is
NUT_GRADE_RE = re.compile(r"\b(a194|a563|f436|sae\s*j995)[\s-]*(gr(?:ade)?\s*)?(2hm?|dh3?|dh|[a-z]|8|5)(?![a-z0-9])")
COMPANION_RE = re.compile(r"(?:\bw/|\bw\s|\bwith\b)\s*(?:nutgrade\S*\s*)?(?:(?:heavy|hvy|hex|hh|heavy-hex|finish|fin|jam)\s*)*nuts?\b")
NO_NUT_RE = re.compile(r"\b(?:no|less|w/o|without)[\s-]*nuts?\b")
# what an item IS comes from its group, not from words in its title (a "BOLT ... w/ HEX NUT" in group Bolt is a bolt)
GROUP_TYPES = {"bolt": {"bolt"}, "nut": {"nut"}, "washer": {"washer", "flatwasher"}, "stud": {"stud"}, "clamp": {"clamp"}, "rod": {"rod"},
               "screw": {"screw"}, "pin": {"pin"}, "anchor": {"anchor"}}
SPECIFIC_FINISH = {"hdg", "mechgalv", "zinc", "yellowzinc", "plain", "stainless", "304", "316"}


def features(text: Optional[str], group: Optional[str] = None) -> Dict[str, set]:
    """group: the item's product group -- decides its type (a bolt is a bolt even when its title names its nut)."""
    t = (text or "").lower().replace("_", " ").replace("”", '"').replace("″", '"')
    t = re.sub(r"(?<=\d) (\d+/\d+)(?![\d/])", r"-\1", t)  # "2 1/4" is 2-1/4, not 2 and a stray 1/4
    t = re.sub(r"(?<=[a-z0-9])w/", " w/", t)  # "HDGw/A194" -> "HDG w/A194"
    t = NUT_GRADE_RE.sub(lambda m: f" nutgrade{m.group(1).replace(' ', '')}{m.group(3)} ", t)  # A194-2H: the nut's grade, not a size
    no_nut = bool(NO_NUT_RE.search(t))
    t = NO_NUT_RE.sub(" nonut ", t)
    with_nut = bool(COMPANION_RE.search(t))
    t = COMPANION_RE.sub(" withnut ", t)
    for pattern, repl in FINISH_PHRASES:
        t = re.sub(pattern, repl, t)
    f = {k: set() for k in WEIGHTS}
    for dia, tpi, length in SIZE_RE.findall(t):
        f["dia"].add(dia)
        f["tpi"].add(tpi)
        if length:
            f["len"].add(length)
    t = SIZE_RE.sub(" ", t)
    if not f["dia"]:
        for dia, length in DIA_LEN_RE.findall(t):
            f["dia"].add(dia)
            f["len"].add(length)
        t = DIA_LEN_RE.sub(" ", t)
    # a size with no thread pitch (washers, "3/4in bolt"): the first bare fraction / inch figure is the diameter
    if not f["dia"]:
        m = BARE_DIA_RE.search(t)
        if m:
            f["dia"].add(m.group(1))
            t = t[:m.start()] + " " + t[m.end():]
    for raw in re.split(r"[^a-z0-9/.-]+", t):
        w = SYNONYMS.get(raw, raw).strip(".-")
        if not w or len(w) < 2 and not w.isdigit():
            continue
        if w in TYPES:
            f["type"].add(w)
        elif w in FINISH:
            f["finish"].add(w)
        elif GRADE_RE.match(w):
            w = re.sub(r"^(a\d{3})[a-z]$", r"\1", w)  # A307A / A307B -> A307
            f["grade"].add(w.replace("grade", "gr"))
        elif not w.isdigit():
            f["word"].add(w)
    if f["finish"] & {"hdg", "mechgalv"}:
        f["finish"].add("galv")
    if "flatwasher" in f["type"]:
        f["type"].add("washer")
    if no_nut or with_nut:
        f["type"].discard("nut")  # "bolt NO NUT" / "bolt w/ hex nut": the line is the bolt
        if not f["type"]:
            f["type"].add("bolt")
    nut_grades = {w[len("nutgrade"):] for w in f["word"] if w.startswith("nutgrade")}
    f["word"] = {w for w in f["word"] if not w.startswith("nutgrade") and w not in ("withnut", "nonut")}
    g = (group or "").strip().lower()
    for key, types in GROUP_TYPES.items():
        if g == key or g.startswith(key):
            f["type"] = set(types)
            break
    if nut_grades and "nut" in f["type"] and not with_nut:
        f["grade"] |= nut_grades  # on a NUT, A194-2H is its own grade
    return f


def score(a: Dict[str, set], b: Dict[str, set]) -> float:
    """Weighted overlap of the attributes both sides mention; a conflicting size or type costs more than a missing one."""
    got = possible = 0.0
    for k, w in WEIGHTS.items():
        if not a[k] and not b[k]:
            continue
        possible += w
        if k == "finish":
            fa, fb = a[k] & SPECIFIC_FINISH, b[k] & SPECIFIC_FINISH
            if fa and fb and not (fa & fb):  # hot-dip vs mechanical galv vs zinc vs plain: a different part
                got -= w * 1.5
                continue
        if a[k] & b[k]:
            got += w * len(a[k] & b[k]) / max(len(a[k]), len(b[k]))
        elif a[k] and b[k] and k in ("dia", "len", "type"):
            got -= w * 0.5  # 3/4 vs 5/8 is a different part
    return max(0.0, got / possible) if possible else 0.0


def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class ItemMatcher:
    """Build once per request (features of every item are precomputed)."""

    def __init__(self, items, vendor_codes: Optional[Dict[str, int]] = None, learned: Optional[Dict[str, dict]] = None):
        # TEST data items (TEST-...) never compete with real ones
        self.items = [i for i in items if not str(getattr(i, "code", "") or "").upper().startswith("TEST-")]
        self.by_id = {i.id: i for i in self.items}
        self.feats = {i.id: features(f"{i.title} {i.code}", getattr(i, "category", None)) for i in self.items}
        self.titles = {}  # an item's own description, word for word (only when one item has it)
        for i in self.items:
            if len(_norm(i.title)) >= 8:
                self.titles.setdefault(_norm(i.title), []).append(i.id)
        self.titles = {k: v[0] for k, v in self.titles.items() if len(v) == 1}
        self.codes = {}
        for i in self.items:
            for c in (i.code, getattr(i, "barcode", None)):
                if _norm(c):
                    self.codes[_norm(c)] = i.id
        self.vendor_codes = {_norm(k): v for k, v in (vendor_codes or {}).items()}
        # learned from earlier saves (item_alias.for_party): {"code"/"desc": {key: (item_id, hits)}}
        self.learned = {k: {key: v for key, v in d.items() if v[0] in self.by_id} for k, d in (learned or {}).items()}

    def rank(self, codes: List[Optional[str]], description: Optional[str], top: int = 5, only_ids=None) -> List[dict]:
        """Best candidates first: [{item_id, code, title, score, why}]. A known vendor part # or
        our own code is a certain match; otherwise attributes from the description decide."""
        out = {}
        def remembered(kind, text, what):
            hit = self.learned.get(kind, {}).get(_norm(text)) if text else None
            if hit:  # picked by the user before: certain after 2 saves, near-certain after 1
                iid, hits = hit
                out[iid] = max(out.get(iid, (0, "")), (1.0 if hits >= 2 else 0.99, f"learned: you picked this for that {what} before ({hits}x)"))
        for c in codes:
            remembered("code", c, "part #")
        remembered("desc", description, "description")
        for c in codes:
            key = _norm(c)
            if key in self.learned.get("code", {}):
                continue
            if key and key in self.vendor_codes:
                out[self.vendor_codes[key]] = (1.0, "vendor part #")
            elif key and key in self.codes:
                out[self.codes[key]] = (1.0, "our item #")
            elif len(key) >= 6:
                # the vendor's # with a suffix dropped or added (87C225A32G vs 87C225A32G/NND): near-certain
                for known, iid in self.vendor_codes.items():
                    if known.startswith(key) or key.startswith(known):
                        out[iid] = max(out.get(iid, (0, "")), (0.95, f"vendor part # (close: {key.upper()})"))
        if description:
            # "15440 BOLT_HH_3/4-10x2-3/4..." -- our own item # written in front of the description
            first = _norm((description.strip().split() or [""])[0])
            if len(first) >= 4 and any(ch.isdigit() for ch in first) and first in self.codes:
                iid = self.codes[first]
                out[iid] = max(out.get(iid, (0, "")), (1.0, "our item # at the start of the description"))
            same = self.titles.get(_norm(description)) or self.titles.get(_norm(" ".join(description.strip().split()[1:])))
            if same:
                out[same] = max(out.get(same, (0, "")), (0.989, "our item's own description, word for word"))  # your own past pick (learned) still wins
            want = features(description)  # part numbers aren't descriptions: "87C225A32G" isn't a size
            text = _norm(description)
            for iid, f in self.feats.items():
                if only_ids is not None and iid not in only_ids:
                    continue
                s = score(want, f)
                # plain text likeness only helps when the description gives no size to compare -- it must never
                # lift a part whose diameter or length is different ("5/8 x 1-3/4" for "5/8 x 2-1/4")
                if s < 0.5 and not (want["dia"] or want["len"]):
                    s = max(s, 0.9 * difflib.SequenceMatcher(None, text, _norm(self.by_id[iid].title)).ratio())
                if s > out.get(iid, (0, ""))[0] and not (out.get(iid, (0, ""))[0] >= 0.97):  # a number / exact match keeps its reason
                    shared = [x for k in ("dia", "tpi", "len", "type", "grade", "finish") for x in sorted(want[k] & f[k])]
                    out[iid] = (s, "matches " + ", ".join(shared) if shared else "similar text")
        ranked = sorted(out.items(), key=lambda kv: -kv[1][0])[:top]
        return [{"item_id": iid, "code": self.by_id[iid].code, "title": self.by_id[iid].title,
                 "score": round(s, 2), "why": why} for iid, (s, why) in ranked if s >= 0.25]


def pick(candidates: List[dict]) -> Optional[int]:
    """Auto-select only when the top candidate is certain or clearly ahead; otherwise the user chooses."""
    if not candidates:
        return None
    top = candidates[0]
    runner = candidates[1]["score"] if len(candidates) > 1 else 0
    # certain = a NUMBER matched (ours, the vendor's linked part #, one picked before) -- a perfect attribute score
    # isn't proof on its own (two items can share size, grade and finish); then it must be clearly ahead
    by_number = not str(top.get("why", "")).startswith(("matches", "similar"))
    if (top["score"] >= 0.97 and by_number and top["score"] - runner > 0.005) or (top["score"] >= 0.98 and top["score"] - runner >= 0.08) \
            or (top["score"] >= 0.8 and top["score"] - runner >= 0.15):
        return top["item_id"]
    return None
