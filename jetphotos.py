"""JetPhotos.com photo lookup by registration — used for military aircraft, which FR24
doesn't return photos for. Chrome TLS impersonation via curl_cffi is required to get past
JetPhotos' bot detection; plain requests gets blocked.
"""
import logging
import re

try:
    from curl_cffi import requests as _requests
except ImportError:
    _requests = None

log = logging.getLogger(__name__)

_PHOTO_RE = re.compile(r'class="result__photo"[^>]*\bsrc="([^"]+)"')
_PHOTO_RE_ALT = re.compile(r'<img src="([^"]+)"[^>]*class="result__photo"')


def fetch_photo_url(registration: str) -> str | None:
    """Return the most recent JetPhotos photo URL for a registration, or None."""
    if not _requests or not registration:
        return None
    reg = registration.strip().upper()
    try:
        r = _requests.get(
            f"https://www.jetphotos.com/registration/{reg}",
            impersonate="chrome", timeout=10,
        )
        r.raise_for_status()
        html = r.text
    except Exception as exc:
        log.debug("JetPhotos request failed for %s: %s", reg, exc)
        return None

    m = _PHOTO_RE.search(html) or _PHOTO_RE_ALT.search(html)
    if not m:
        return None
    src = m.group(1)
    if src.startswith("//"):
        src = "https:" + src
    return src
