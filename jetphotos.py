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


def fetch_photo_url_640(registration: str) -> str | None:
    """Same lookup, but returned in the 640px form the rest of the app stores (cdn.jetphotos.com/640/...?v=0)."""
    url = fetch_photo_url(registration)
    if not url:
        return None
    return url.replace("/400/", "/640/").split("?")[0] + "?v=0"


_FR24_PAGE_RE = re.compile(r"https://cdn\.jetphotos\.com/(?:400|640)/([^\"' )?]+)")


def fetch_photo_url_via_fr24_page(registration: str) -> str | None:
    """FR24's public aircraft page lists the aircraft's JetPhotos pictures even when its JSON feed only returns a
    generic type drawing. A plain request gets through (unlike jetphotos.com itself). 640px URL, newest first."""
    if not registration:
        return None
    try:
        import requests
        r = requests.get(f"https://www.flightradar24.com/data/aircraft/{registration.strip().lower()}",
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36", "Accept": "text/html"},
                         timeout=12)
        r.raise_for_status()
        # file names are <photo id>_<upload timestamp>.jpg — take the newest upload (the page isn't ordered by it)
        found = {m.group(1) for m in _FR24_PAGE_RE.finditer(r.text)}
        if not found:
            return None
        newest = max(found, key=lambda p: int(re.search(r"_(\d+)\.", p).group(1)) if re.search(r"_(\d+)\.", p) else 0)
        return f"https://cdn.jetphotos.com/640/{newest}?v=0"
    except Exception as exc:
        log.debug("FR24 aircraft-page photo lookup failed for %s: %s", registration, exc)
        return None


def url_alive(url: str, timeout: float = 6) -> bool:
    """True when a stored photo link still serves an image (JetPhotos removes pictures; a dead link renders as a blank tile)."""
    if not url:
        return False
    try:
        import requests
        r = requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        try:
            return r.status_code < 400 and "image" in (r.headers.get("Content-Type") or "image")
        finally:
            r.close()
    except Exception:
        return False


def pick_live_photo(registration: str, fresh_url: str, stored_url: str) -> str:
    """The photo a new card should use. FR24's fresh photo is taken as is (it was just served, so no extra request).
    Only when FR24 gave none is the aircraft's stored link used — and only if it still loads (one request); if it does
    not, the newest picture from FR24's aircraft page / JetPhotos replaces it. Same chain the Feed's repair of old cards
    uses, so a card never starts life with a dead link. Returns '' when nothing works (the caller keeps what it had)."""
    if fresh_url:
        return fresh_url
    if stored_url and url_alive(stored_url):
        return stored_url
    return fetch_photo_url_via_fr24_page(registration) or fetch_photo_url_640(registration) or ""
