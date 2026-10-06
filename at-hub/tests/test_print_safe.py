"""Every printed look stays readable on a black-and-white printer: no text on a dark fill (2026-10-06)."""
import copy
from app.services import template_starters
from app.services.print_safe import make_safe, luminance, DARK, THIN, _inside

DOC_TYPES = ("invoice", "quote", "purchase_order", "packing_list", "box_label", "address_label")


def _dark_text_boxes(spec):
    bad = []
    for band in ("header", "running", "summary", "footer"):
        blocks = (spec.get(band) or {}).get("blocks") or []
        for b in blocks:
            st = b.get("style") or {}
            if b.get("type") == "rect" and luminance(st.get("bg")) < DARK and min(b["w"], b["h"]) > THIN \
                    and any(t.get("type") in ("text", "kv") and _inside(t, b) for t in blocks):
                bad.append((band, b["id"], st.get("bg")))
    if luminance(((spec.get("table") or {}).get("style") or {}).get("header_bg")) < DARK:
        bad.append(("table", "header", spec["table"]["style"]["header_bg"]))
    return bad


def test_no_look_puts_text_on_a_dark_fill():
    for dt in DOC_TYPES:
        for key, spec in template_starters.starters(dt):
            assert not _dark_text_boxes(spec), (dt, key, _dark_text_boxes(spec))


def test_saving_a_dark_design_makes_it_light_and_is_stable():
    spec = {"page": {"w": 8.5, "h": 11}, "header": {"blocks": [
        {"id": "r", "type": "rect", "x": 0, "y": 0, "w": 7, "h": 1, "style": {"bg": "#1b2a4a"}},
        {"id": "t", "type": "text", "x": 0.2, "y": 0.2, "w": 3, "h": 0.3, "text": "INVOICE", "style": {"color": "#ffffff"}}]},
        "table": {"style": {"header_bg": "#1e293b", "header_color": "#ffffff"}}}
    safe = make_safe(copy.deepcopy(spec))
    assert not _dark_text_boxes(safe)
    assert luminance(safe["header"]["blocks"][1]["style"]["color"]) < 0.3
    assert make_safe(copy.deepcopy(safe)) == safe
