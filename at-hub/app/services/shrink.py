"""Shrink uploads before they're stored, so phone photos and scanned PDFs don't eat the server's disk.

Every attached file goes through shrink() (app/routes/attachments.py store_file). The original is replaced only when
the smaller copy is clearly smaller; anything that can't be read safely is kept exactly as it came.

  photos (jpg / heic / webp, and big photographic png): turned upright (EXIF), longest side cut to MAX_PX,
          saved as JPEG at QUALITY -- a 4-6 MB phone photo becomes ~300-600 KB, still sharp enough to read a label
  png screenshots / scans: longest side cut to MAX_PX and re-packed as PNG (sharp text); only a big photo-like
          png becomes JPEG
  pdf (scans over PDF_MIN_BYTES): page images over MAX_PX or stored uncompressed are re-saved as JPEG; the text
          and layout aren't touched, and the result is kept only if it opens with the same page count

    python -m app.services.shrink            what shrinking the files already stored would save (changes nothing)
    python -m app.services.shrink --apply    shrink them (back up first: Backups -> Back up now)
"""
import io
import os
from typing import Optional, Tuple

MAX_PX = 2000            # longest side of a stored photo / page image
QUALITY = 80             # JPEG quality
MIN_BYTES = 350 * 1024   # smaller images are left alone (unless huge in pixels)
PDF_MIN_BYTES = 1500 * 1024
KEEP_IF_SAVES = 0.85     # keep the new copy only if it's at most 85% of the original

PHOTO_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".webp"}

try:  # iPhone photos
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover -- without it HEIC files are stored as they came
    pillow_heif = None


def _rename(name: str, ext: str) -> str:
    stem, old = os.path.splitext(name)
    return name if old.lower() == ext else f"{stem}{ext}"


def _shrink_image(name: str, data: bytes) -> Optional[Tuple[str, str, bytes]]:
    from PIL import Image, ImageOps
    ext = os.path.splitext(name)[1].lower()
    img = Image.open(io.BytesIO(data))
    if getattr(img, "is_animated", False):
        return None
    big_px = max(img.size) > MAX_PX
    if len(data) < MIN_BYTES and not big_px:
        return None
    img = ImageOps.exif_transpose(img)
    if big_px:
        img.thumbnail((MAX_PX, MAX_PX), Image.LANCZOS)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    out = io.BytesIO()
    if ext == ".png":
        img.save(out, "PNG", optimize=True)
        packed = out.getvalue()
        # a photo saved as png (phone screenshots of a delivery, scanned photos) packs badly -- JPEG it if that's far smaller
        if not has_alpha and len(packed) > MIN_BYTES:
            j = io.BytesIO()
            img.convert("RGB").save(j, "JPEG", quality=QUALITY, optimize=True, progressive=True)
            if len(j.getvalue()) < len(packed) * 0.5:
                return _rename(name, ".jpg"), "image/jpeg", j.getvalue()
        return name, "image/png", packed
    if ext in PHOTO_EXT:
        if has_alpha:
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img, mask=img.split()[-1])
            img = bg
        img.convert("RGB").save(out, "JPEG", quality=QUALITY, optimize=True, progressive=True)
        return _rename(name, ".jpg"), "image/jpeg", out.getvalue()
    return None


def _shrink_pdf(name: str, data: bytes) -> Optional[Tuple[str, str, bytes]]:
    if len(data) < PDF_MIN_BYTES:
        return None
    from PIL import Image
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(io.BytesIO(data))
    pages = len(reader.pages)
    writer = PdfWriter(clone_from=reader)
    changed = False
    for page in writer.pages:
        for img in page.images:
            try:
                pil = img.image
                if pil is None:
                    continue
                if max(pil.size) > MAX_PX:
                    pil = pil.copy()
                    pil.thumbnail((MAX_PX, MAX_PX), Image.LANCZOS)
                if pil.mode not in ("RGB", "L"):
                    pil = pil.convert("RGB")
                img.replace(pil, quality=QUALITY)
                changed = True
            except Exception:
                continue  # an image pypdf can't re-save stays as it was
    if not changed:
        return None
    for page in writer.pages:
        try:
            page.compress_content_streams()
        except Exception:
            pass
    out = io.BytesIO()
    writer.write(out)
    new = out.getvalue()
    if len(PdfReader(io.BytesIO(new)).pages) != pages:
        return None
    return name, "application/pdf", new


def shrink(name: str, content_type: Optional[str], data: bytes) -> Tuple[str, Optional[str], bytes]:
    """(name, content type, bytes) to store: a smaller copy when it's worth it, else the file exactly as it came."""
    ext = os.path.splitext(name)[1].lower()
    try:
        if ext in PHOTO_EXT or ext == ".png":
            got = _shrink_image(name, data)
        elif ext == ".pdf":
            got = _shrink_pdf(name, data)
        else:
            got = None
    except Exception:
        got = None
    if got and len(got[2]) <= len(data) * KEEP_IF_SAVES:
        return got
    return name, content_type, data


def shrink_existing(apply: bool = False) -> dict:
    """Run shrink() over files already stored. Dry run unless apply (then the file and its Attachment row change)."""
    from pathlib import Path
    from app.config.database import SessionLocal
    from app.models import Attachment
    from app.routes.attachments import upload_root
    root, db = upload_root(), SessionLocal()
    before = after = n = 0
    try:
        for att in db.query(Attachment).all():
            path = root / att.stored_name
            if not path.exists():
                continue
            data = path.read_bytes()
            name, ctype, new = shrink(att.filename, att.content_type, data)
            before += len(data)
            after += len(new)
            if new is data:
                continue
            n += 1
            if apply:
                new_path = path.with_name(_rename(path.name, os.path.splitext(name)[1].lower()))
                new_path.write_bytes(new)
                if new_path != path:
                    path.unlink()
                att.filename, att.content_type, att.size = name, ctype, len(new)
                att.stored_name = str(Path(att.stored_name).with_name(new_path.name)).replace("\\", "/")
        if apply:
            db.commit()
    finally:
        db.close()
    return {"files_shrunk": n, "mb_before": round(before / 1e6, 1), "mb_after": round(after / 1e6, 1), "applied": apply}


if __name__ == "__main__":
    import sys
    print(shrink_existing(apply="--apply" in sys.argv))
