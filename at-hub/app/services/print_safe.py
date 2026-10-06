"""Keep designer templates readable on a black-and-white printer.

A dark fill (navy band, charcoal table header, "Amount due" box) prints near-black, and the white / pale text on it
becomes hard to read. Every look and every saved design goes through make_safe():
- documents: a dark fill becomes a light tint of its own colour, and the text on it takes the dark colour instead --
  the look keeps its colour on screen and prints as light grey with dark text;
- labels (thermal printers can't print grey): a dark fill becomes a white box with a heavy black border, and the
  text on it turns black.
Thin dark strips (rules, accent bars) carry no text and are left alone. Safe to run again: a safe spec is unchanged."""

DARK = 0.45      # luminance below this is a dark fill
PALE = 0.6       # text lighter than this can't be read on a light fill
DEEP = 0.25      # a fill this dark is also dark enough to be the text colour; lighter ones (teal, orange) get near-black
INK = "#0f172a"
THIN = 0.12      # inches: rects this thin are rules, not fills
LABEL_MAX_W = 6.5


def _rgb(color):
    if not isinstance(color, str) or not color.startswith("#") or len(color) != 7:
        return None
    try:
        return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except ValueError:
        return None


def luminance(color) -> float:
    rgb = _rgb(color)
    if rgb is None:
        return 1.0
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def tint(color, amount=0.86) -> str:
    """The colour mixed with white -- light enough to print as pale grey."""
    r, g, b = _rgb(color)
    mix = lambda c: round((c + (1 - c) * amount) * 255)
    return "#{:02x}{:02x}{:02x}".format(mix(r), mix(g), mix(b))


def _inside(b, box) -> bool:
    cx, cy = b.get("x", 0) + b.get("w", 0) / 2, b.get("y", 0) + b.get("h", 0) / 2
    return box["x"] - 0.01 <= cx <= box["x"] + box["w"] + 0.01 and box["y"] - 0.01 <= cy <= box["y"] + box["h"] + 0.01


def _fix_blocks(blocks, label: bool) -> None:
    dark = []
    for b in blocks:
        st = b.get("style") or {}
        if b.get("type") == "rect" and luminance(st.get("bg")) < DARK and min(b.get("w", 0), b.get("h", 0)) > THIN:
            has_text = any(t.get("type") in ("text", "kv") and _inside(t, b) for t in blocks)
            if not has_text:
                continue
            ink = st["bg"] if not label else "#000000"
            if label:
                st["bg"] = None
                st["border"], st["border_color"] = max(st.get("border") or 0, 2), "#000000"
            else:
                st["bg"] = tint(ink)
            b["style"] = st
            dark.append((b, ink))
    for t in blocks:
        if t.get("type") not in ("text", "kv"):
            continue
        st = t.get("style") or {}
        for box, ink in dark:
            if _inside(t, box):
                if luminance(st.get("color")) > PALE:
                    st["color"] = ink if luminance(ink) < DEEP else INK
                if luminance(st.get("label_color")) > PALE:
                    st["label_color"] = st["color"]
                t["style"] = st
                break


def make_safe(spec: dict) -> dict:
    """Fix a template spec in place (and return it)."""
    if not isinstance(spec, dict):
        return spec
    label = (spec.get("page") or {}).get("w", 8.5) <= LABEL_MAX_W
    for band in ("header", "running", "summary", "footer"):
        if isinstance(spec.get(band), dict):
            _fix_blocks(spec[band].get("blocks") or [], label)
    st = ((spec.get("table") or {}).get("style")) or {}
    if luminance(st.get("header_bg")) < DARK:
        ink = st["header_bg"]
        st["header_bg"] = tint(ink)
        if luminance(st.get("header_color")) > PALE:
            st["header_color"] = ink if luminance(ink) < DEEP else INK
        st.setdefault("header_rule", ink)
    return spec


def make_safe_saved(db) -> int:
    """Run every saved design through make_safe (startup); returns how many changed."""
    import json
    from app.models import DocTemplate
    changed = 0
    for t in db.query(DocTemplate).all():
        before = t.spec
        after = json.dumps(make_safe(json.loads(before)))
        if after != json.dumps(json.loads(before)):
            t.spec = after
            changed += 1
    if changed:
        db.commit()
    return changed
