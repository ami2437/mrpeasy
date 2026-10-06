"""Two-step login with an authenticator app (Google / Microsoft Authenticator, Authy...): standard TOTP, RFC 6238 --
6 digits, 30-second steps, SHA-1. Opt-in per person on My Account; a super admin can switch it off for someone who
lost their phone (Users & Roles)."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

ISSUER = "AT-HUB"


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    n = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{n:06d}"


def verify(secret: str, code: str, window: int = 1, now: float = None) -> bool:
    """The code for now, or one step either side (phone clocks drift)."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if not secret or len(code) != 6:
        return False
    step = int((now or time.time()) // 30)
    return any(hmac.compare_digest(_code(secret, step + d), code) for d in range(-window, window + 1))


def uri(secret: str, username: str) -> str:
    return f"otpauth://totp/{quote(ISSUER)}:{quote(username)}?secret={secret}&issuer={quote(ISSUER)}&digits=6&period=30"


def qr_svg(text: str) -> str:
    """The setup QR code as SVG (scanned by the authenticator app)."""
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    w = qr.QrCodeWidget(text)
    x0, y0, x1, y1 = w.getBounds()
    size = 200
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(w)
    return renderSVG.drawToString(d)
