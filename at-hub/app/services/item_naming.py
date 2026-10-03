"""Name new items from a customer's PO line, so a scanned order can create what's missing in one click.

Chart / Hudson describe a bolt in one string, e.g.
    BOLT_HH_3/4-10x2-1/2_A325_TYPE1_HDG_w/A194-2H HEX NUT
    <type>_<head>_<thread>x<length>_<grade>_<type/finish>_w/<the nut>
We keep that as the bolt's title (their item # is the code), and the matching $0 nut is
<bolt code>-NUT, titled from inside the bolt's own description:
    <thread> <nut spec> HEX NUT [special treatment]   ->   3/4-10 A194-2H HEX NUT
When the description doesn't name the nut, the bolt grade decides it (A325 -> A194-2H, A307 -> A563 GR A);
other grades are flagged for a person to check.
"""
import re
from typing import Any, Dict, Optional

# Chart joins fields with "_", which regex counts as a word character -- so boundaries here are "not a letter/digit".
_B, _E = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
# "3/4-10x2-1/2", "1-8x14-1/2", "5/16x1-1/8" (eye bolts print no pitch), "3/4-10 X 4 1/2" -> "3/4-10" / "1-8" / "5/16"
# also "1-1/2-8x10", '5/8"-11x3', "5/8-11UNCx2", "1.1/4-7x6" (a "." typed for "-")
THREAD = re.compile(r"(?<![\d/.])(\d+(?:[-.]\d+/\d+|/\d+)?)\"?(?:-(\d+))?\s*(?:UNC|UNF)?\s*x\s*\d", re.I)
WITH = re.compile(r"w/\s*(.*)$", re.I)
NUT_WORD = re.compile(r"(?<![A-Za-z])NUTS?(?![A-Za-z])", re.I)
ASSEMBLED = re.compile(r"assembl", re.I)
TWO = re.compile(r"\(\s*2\s*\)|(?<![A-Za-z0-9])2\s*(?:HVY\s*)?(?:HEX\s*)?NUTS", re.I)
# nut grades, as we title them
NUT_GRADES = [
    (re.compile(_B + r"(?:A?\s*194\s*[-_ ]?\s*)?2H" + _E, re.I), "A194-2H"),
    (re.compile(_B + r"(?:SA|A)?\s*194\s*[-_ ]?\s*(?:GR\s*)?4/7" + _E, re.I), "A194 GR 4/7"),
    (re.compile(_B + r"A563\s*[-_ ]?\s*(?:GR\s*[-_ ]?\s*)?(DH|[A-D])" + _E, re.I), "A563 GR {0}"),
    (re.compile(_B + r"F594\w*", re.I), "F594 SS"),
    (re.compile(_B + r"(?:SS|316L?|304L?|STAINLESS)" + _E, re.I), "SS"),
]
# the bolt's grade -> its usual nut, when the description doesn't name one
GRADE_NUT = [
    (re.compile(_B + r"(?:A325|F3125)" + _E, re.I), "A194-2H", "high"),
    (re.compile(_B + r"A307A?" + _E, re.I), "A563 GR A", "high"),
    (re.compile(_B + r"A320\s*[-_ ]?\s*L7" + _E, re.I), "A194 GR 4/7", "check"),
    (re.compile(_B + r"J429\s*GR\s*5" + _E, re.I), "GR 5", "check"),
    (re.compile(_B + r"A489" + _E, re.I), "A563 GR A", "check"),  # eye bolts
    (re.compile(_B + r"(?:316L?|304L?|SS)" + _E, re.I), "SS", "check"),
]
# eye bolts print "5/16x1-1/8" with no pitch: the nut needs one -- standard coarse thread (UNC)
UNC = {"1/4": 20, "5/16": 18, "3/8": 16, "7/16": 14, "1/2": 13, "9/16": 12, "5/8": 11, "3/4": 10, "7/8": 9,
       "1": 8, "1-1/8": 7, "1-1/4": 7, "1-3/8": 6, "1-1/2": 6}
SPECIAL = [(re.compile(r"WAX", re.I), "WAX DIPPED")]


def _nut_grade(text: str) -> Optional[str]:
    for pattern, name in NUT_GRADES:
        m = pattern.search(text)
        if m:
            return name.format(*(g.upper() for g in m.groups()))
    return None


def nut_title(bolt_description: str) -> Dict[str, Any]:
    """The matching nut's title, from inside the bolt's description:  <thread> <nut grade> [HVY] HEX NUT [WAX DIPPED].
    Returns {"title", "confidence": "high"|"check", "why", "per_bolt"}; title None when the nut comes assembled."""
    desc = re.sub(r"\s+", " ", bolt_description or "").strip()
    if ASSEMBLED.search(desc):
        return {"title": None, "confidence": "high", "why": "the nut comes assembled on the bolt", "per_bolt": 0}
    m = THREAD.search(desc)
    thread = None
    if m:
        size = m.group(1).replace(".", "-")
        thread = f"{size}-{m.group(2)}" if m.group(2) else size
    pitch_added = False
    if thread and "-" not in thread and thread in UNC:
        thread, pitch_added = f"{thread}-{UNC[thread]}", True
    w = WITH.search(desc)
    nut_text = w.group(1) if w and NUT_WORD.search(w.group(1)) else None
    if nut_text:  # only up to the word NUT: what follows is notes ("w/MECH NUT. 3888 STOCK FOR SS")
        nut_text = nut_text[:NUT_WORD.search(nut_text).end()]
    if nut_text is None:  # no "w/": the nut may still be described, e.g. "..., A563 GR A HEX NUT"
        n = NUT_WORD.search(desc)
        nut_text = desc[max(0, desc.rfind("_", 0, n.start()) if "_" in desc[:n.start()] else desc.rfind(",", 0, n.start())):] if n else None

    grade, confidence, why = None, "high", ""
    if nut_text:
        grade = _nut_grade(nut_text)
        why = "nut named in the description"
    if not grade:
        for pattern, nut, conf in GRADE_NUT:
            if pattern.search(desc):
                grade, confidence = nut, conf
                why = f"usual nut for a {pattern.search(desc).group(0).replace('_', ' ').strip()} bolt" + ("" if conf == "high" else ": check it")
                break
    if not grade:
        confidence, why = "check", "the description doesn't say which nut: check it"
    if not thread:
        confidence, why = "check", why + "; no thread size found"
    elif pitch_added:
        why += f"; pitch {thread} is standard coarse thread (not printed)"
    heavy = bool(nut_text and re.search(r"HVY|HEAVY", nut_text, re.I))
    specials = [label for pattern, label in SPECIAL if pattern.search(desc)]
    title = " ".join(p for p in [thread, grade, "HVY HEX NUT" if heavy else "HEX NUT", *specials] if p)
    return {"title": title, "confidence": confidence, "why": why, "per_bolt": 2 if TWO.search(desc) else 1}


def nut_code(bolt_code: str) -> str:
    return f"{bolt_code.strip()}-NUT"


def item_category(description: str) -> Optional[str]:
    """Bolt / Stud / Washer / Nut ... from the first word Chart prints (BOLT_HH_..., WASHER_FLAT_...)."""
    first = re.split(r"[_\s]", (description or "").strip(), maxsplit=1)[0].upper()
    # a cap screw is a bolt here: same group, and it gets a matching nut like one
    return {"BOLT": "Bolt", "SCREW": "Bolt", "STUD": "Stud", "WASHER": "Washer", "NUT": "Nut", "ROD": "Rod",
            "ANCHOR": "Anchor", "PIN": "Pin"}.get(first)


# Structural bolt grades we always sell with a nut, even when the line doesn't say "w/ nut" (our history:
# 30745, 30747, 39165, 39222, 69473... all have a -NUT). Studs, eye bolts and specialty grades aren't assumed.
USUALLY_WITH_NUT = re.compile(r"(?<![A-Za-z0-9])(A325|F3125|A307A?|A490)(?![A-Za-z0-9])", re.I)
NUT_HEADS = re.compile(r"^(BOLT_(HH|HEX|HVY|CAR)|SCREW_CAP)", re.I)


def usually_with_nut(description: str) -> bool:
    d = (description or "").strip()
    return bool(NUT_HEADS.match(d) and USUALLY_WITH_NUT.search(d))
