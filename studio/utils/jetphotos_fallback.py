"""
JetPhotos aircraft metadata lookup — fallback path only, used when FR24 has no
data for a registration (military aircraft aren't tracked by FR24). Mirrors
SpotAlert's own "FR24 primary, JetPhotos fallback for military" pattern
(see monitor.py::_enrich_and_store in the main SpotAlert app).

Parsing logic mirrors AircraftMetadataProviderJP.lua from
https://github.com/aviationphoto/AircraftMetadata-Lightroom-Plugin
"""
from typing import Dict, Optional, Tuple

try:
    from curl_cffi import requests as requests
    _CURL_CFFI = True
except ImportError:
    import requests
    _CURL_CFFI = False
import sqlite3
from config import CACHE_DIR, CACHE_EXPIRY_HOURS, JETPHOTOS_BASE_URL

TOKEN_SUCCESS = '>Reg:'
TOKEN_START_REGISTRATION = '/registration/'
TOKEN_END_REGISTRATION = '"'
TOKEN_START_AIRLINE = '/airline/'
TOKEN_END_AIRLINE = '"'
TOKEN_START_AIRCRAFT = '/aircraft/'
TOKEN_END_AIRCRAFT = '"'
TOKEN_START_MANUFACTURER = '/manufacturer/'
TOKEN_END_MANUFACTURER = '/'


def _extract_metadata(payload: str, token_start: str, token_end: str) -> str:
    """
    Isolate a metadata value between two tokens — mirrors extractMetadata() in the Lua plugin.
    Returns 'not set' when the value is missing (matches plugin behaviour).
    """
    pos = payload.find(token_start)
    if pos == -1:
        return 'not set'
    after_start = payload[pos + len(token_start):]
    pos2 = after_start.find(token_end)
    if pos2 == -1:
        return 'not set'
    value = after_start[:pos2].strip()
    return value if value else 'not set'


def _slug_to_display(slug: str) -> str:
    """Convert a JetPhotos URL slug (e.g. 'air-canada') to a display name ('Air Canada')."""
    if slug == 'not set':
        return slug
    return slug.replace('-', ' ').title()


class JetPhotosFallbackLookup:
    """JetPhotos aircraft metadata lookup — used only when fr24_lookup.lookup()
    comes back empty (registration not on FR24, e.g. military aircraft)."""

    def __init__(self):
        self.base_url = JETPHOTOS_BASE_URL
        self.cache_dir = CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_db = self.cache_dir / "jetphotos_cache.db"
        self._init_cache_db()

    def _init_cache_db(self):
        conn = sqlite3.connect(self.cache_db)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS aircraft_cache (
                registration TEXT PRIMARY KEY,
                airline TEXT,
                aircraft TEXT,
                aircraft_manufacturer TEXT,
                aircraft_type TEXT,
                aircraft_url TEXT,
                timestamp DATETIME,
                last_accessed DATETIME
            )
        ''')
        conn.commit()
        conn.close()

    def _get_cached_metadata(self, registration: str) -> Optional[Dict]:
        conn = sqlite3.connect(self.cache_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM aircraft_cache
            WHERE registration = ?
            AND datetime(timestamp) > datetime('now', '-' || ? || ' hours')
        ''', (registration.upper(), CACHE_EXPIRY_HOURS))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                'registration': row['registration'],
                'airline': row['airline'],
                'aircraft': row['aircraft'],
                'aircraft_manufacturer': row['aircraft_manufacturer'],
                'aircraft_type': row['aircraft_type'],
                'aircraft_url': row['aircraft_url'],
                'cached': True
            }
        return None

    def _cache_metadata(self, registration: str, metadata: Dict):
        conn = sqlite3.connect(self.cache_db)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO aircraft_cache
            (registration, airline, aircraft, aircraft_manufacturer, aircraft_type, aircraft_url, timestamp, last_accessed)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ''', (
            registration.upper(),
            metadata.get('airline'),
            metadata.get('aircraft'),
            metadata.get('aircraft_manufacturer'),
            metadata.get('aircraft_type'),
            metadata.get('aircraft_url'),
        ))
        conn.commit()
        conn.close()

    def lookup(self, registration: str) -> Tuple[bool, Dict]:
        """
        Lookup aircraft metadata from JetPhotos.
        Parsing mirrors AircraftMetadataProviderJP.lua from the Lightroom plugin.

        Returns (success, metadata) where metadata contains:
            registration, airline, aircraft, aircraft_manufacturer, aircraft_type, aircraft_url
        On failure, metadata contains 'error' and 'reason' ('reg_not_found' | 'wrong_reg' | 'request_error').
        """
        registration = registration.upper().strip()

        cached = self._get_cached_metadata(registration)
        if cached:
            return True, cached

        try:
            url = f"{self.base_url}/{registration}"
            if _CURL_CFFI:
                response = requests.get(url, impersonate='chrome', timeout=10)
            else:
                response = requests.get(url, timeout=10)
            response.raise_for_status()
            html = response.text
        except Exception as e:
            return False, {'error': f'JetPhotos request error: {str(e)}', 'reason': 'request_error'}

        # Mirror plugin: check success token before parsing
        if TOKEN_SUCCESS not in html:
            return False, {'error': 'Registration not found on JetPhotos', 'reason': 'reg_not_found'}

        # Extract and validate registration (plugin checks returned reg matches searched reg)
        found_registration = _extract_metadata(html, TOKEN_START_REGISTRATION, TOKEN_END_REGISTRATION).upper()
        if found_registration != registration:
            return False, {
                'error': f'JetPhotos returned wrong registration: {found_registration}',
                'reason': 'wrong_reg'
            }

        # Extract raw slug values (same token boundaries as the Lua plugin)
        airline_slug = _extract_metadata(html, TOKEN_START_AIRLINE, TOKEN_END_AIRLINE)
        aircraft_slug = _extract_metadata(html, TOKEN_START_AIRCRAFT, TOKEN_END_AIRCRAFT)
        manufacturer_slug = _extract_metadata(html, TOKEN_START_MANUFACTURER, TOKEN_END_MANUFACTURER)

        # Derive aircraft type: strip manufacturer prefix by length (mirrors plugin)
        if manufacturer_slug == 'not set':
            aircraft_type_slug = aircraft_slug
        else:
            aircraft_type_slug = aircraft_slug[len(manufacturer_slug):].strip().lstrip('-')

        metadata = {
            'registration': registration,
            'airline': _slug_to_display(airline_slug),
            'aircraft': _slug_to_display(aircraft_slug),
            'aircraft_manufacturer': _slug_to_display(manufacturer_slug),
            'aircraft_type': _slug_to_display(aircraft_type_slug),
            'aircraft_url': url,
        }

        self._cache_metadata(registration, metadata)
        return True, metadata
