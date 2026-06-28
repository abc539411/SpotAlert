"""Run from PC to seed FR24 session cookies for the webapp container."""
import sys, os, pickle
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from flightradar24api.request import _scraper, _COOKIE_FILE

r1 = _scraper.get("https://www.flightradar24.com/", timeout=15)
print("Homepage:", r1.status_code)

r2 = _scraper.get(
    "https://api.flightradar24.com/common/v1/airport.json",
    params={"format": "json", "code": "SYD", "limit": 10, "page": 1},
    timeout=15
)
print("API:", r2.status_code)

# Serialize using jar iteration to avoid CookieConflictError from duplicate names
cookies = {c.name: c.value for c in _scraper.cookies}
os.makedirs(os.path.dirname(_COOKIE_FILE) or '.', exist_ok=True)
with open(_COOKIE_FILE, "wb") as f:
    pickle.dump(cookies, f)
print("Saved cookies:", list(cookies.keys()))
