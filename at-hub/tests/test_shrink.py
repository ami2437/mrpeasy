"""Uploads are shrunk before they're stored: big photos -> ~2000 px JPEG, scanned PDFs re-packed; small or other files untouched."""
import io
import random

from PIL import Image
from pypdf import PdfReader

from app.services.shrink import MAX_PX, shrink


def _photo(w=4032, h=3024, fmt="JPEG", **kw):
    """A noisy, photo-like image (flat colour would compress to nothing and prove little)."""
    random.seed(1)
    small = Image.new("RGB", (w // 8, h // 8))
    small.putdata([(random.randrange(256), random.randrange(256), random.randrange(256)) for _ in range((w // 8) * (h // 8))])
    img = small.resize((w, h), Image.BICUBIC)
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def test_phone_photo_becomes_a_small_upright_jpeg():
    data = _photo(quality=95)
    name, ctype, out = shrink("IMG_0042.jpg", "image/jpeg", data)
    img = Image.open(io.BytesIO(out))
    assert name == "IMG_0042.jpg" and ctype == "image/jpeg"
    assert max(img.size) == MAX_PX and len(out) < len(data) / 3


def test_sideways_photo_is_turned_upright():
    img = Image.open(io.BytesIO(_photo(3000, 2000, quality=95)))
    exif = img.getexif()
    exif[0x0112] = 6  # camera held sideways: "rotate 90 degrees to view"
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95, exif=exif)
    _, _, out = shrink("pod.jpeg", "image/jpeg", buf.getvalue())
    w, h = Image.open(io.BytesIO(out)).size
    assert h > w                                         # portrait now, as the driver saw it


def test_iphone_heic_is_stored_as_jpeg():
    import pillow_heif  # noqa: F401  (registered by app.services.shrink)
    data = _photo(fmt="HEIF", quality=90)
    name, ctype, out = shrink("IMG_7781.HEIC", "image/heic", data)
    assert name == "IMG_7781.jpg" and ctype == "image/jpeg" and Image.open(io.BytesIO(out)).format == "JPEG"


def test_small_files_and_other_types_are_left_exactly_as_they_came():
    small = _photo(800, 600, quality=70)
    assert shrink("label.jpg", "image/jpeg", small)[2] is small
    xls = b"PK\x03\x04" + b"x" * 2_000_000
    assert shrink("prices.xlsx", None, xls)[2] is xls
    junk = b"\xff\xd8 not really a jpeg" * 50_000
    assert shrink("broken.jpg", "image/jpeg", junk)[2] is junk   # unreadable -> stored as it came


def test_screenshot_png_stays_png():
    img = Image.new("RGB", (2880, 1800), "white")
    for y in range(0, 1800, 40):
        img.paste((30, 30, 30), (100, y, 2700, y + 6))      # lines of "text"
    buf = io.BytesIO()
    img.save(buf, "PNG")
    name, ctype, out = shrink("screen.png", "image/png", buf.getvalue())
    assert name == "screen.png" and Image.open(io.BytesIO(out)).format == "PNG" and max(Image.open(io.BytesIO(out)).size) == MAX_PX


def test_scanned_pdf_shrinks_and_keeps_every_page():
    pages = [Image.open(io.BytesIO(_photo(2550, 3300, quality=98))) for _ in range(2)]
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=300)
    data = buf.getvalue()
    name, ctype, out = shrink("scan.pdf", "application/pdf", data)
    assert len(out) < len(data) * 0.85 and len(PdfReader(io.BytesIO(out)).pages) == 2


def test_upload_through_the_api_stores_the_small_copy(make, api, client, admin_headers):
    a = make.item()
    make.stock(a, 1)
    o = make.order(lines=[(a, 1, 1)])
    sh = make.ship(o)
    data = _photo(quality=95)
    r = client.post("/api/attachments/", headers=admin_headers,
                    data={"entity_type": "shipment", "entity_id": str(sh["id"]), "category": "pod"},
                    files={"files": ("delivery.heic", _photo(fmt="HEIF", quality=90), "image/heic")})
    assert r.status_code == 200, r.text
    att = r.json()[0]
    assert att["filename"] == "delivery.jpg" and att["size"] < len(data) / 3
    got = client.get(f"/api/attachments/{att['id']}/file", headers=admin_headers)
    assert got.status_code == 200 and Image.open(io.BytesIO(got.content)).format == "JPEG"
