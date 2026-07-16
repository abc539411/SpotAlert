"""
FlightRadar24 aircraft metadata lookup — primary lookup path, replacing the
JetPhotos scraper. Falls back to utils/jetphotos_fallback.py for aircraft
FR24 doesn't track (mainly military), mirroring SpotAlert's own established
"FR24 primary, JetPhotos fallback for military" pattern
(see monitor.py::_enrich_and_store in the main SpotAlert app).

get_rego_details(reg) returns the aircraft's recent FLIGHTS (not a single
aircraft record) — data[0] is the most recent one, which gives us the
aircraft's current operator/type, the same "most recent known state" a
JetPhotos registration page shows. Verified against a live call (see
studio/README.md for the confirmed response shape):
    data[0].aircraft.model.text   -> full display name, e.g. "Airbus A321-271NY(XLR)"
    data[0].aircraft.model.code   -> ICAO type code, e.g. "A21N"
    data[0].owner.name            -> operating carrier (preferred — see below)
    data[0].airline.name          -> marketing carrier (fallback)

owner vs airline: SpotAlert's own monitor.py prefers the operating carrier
(`owner`) over the marketing carrier (`airline`) so codeshare flights are
attributed to who's actually flying the aircraft, not who sold the ticket —
same precedent applied here.
"""
from typing import Dict, Optional, Tuple
import sqlite3

from config import CACHE_DIR, CACHE_EXPIRY_HOURS
from flightradar24api.api import FlightRadar24API
from utils.aircraft_meta import aircraft_manufacturer
from utils.jetphotos_fallback import JetPhotosFallbackLookup


class FR24Lookup:
    """Aircraft metadata lookup — FR24 primary, JetPhotos fallback for
    registrations FR24 has no data for (mainly military aircraft)."""

    def __init__(self):
        self.cache_dir = CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_db = self.cache_dir / "fr24_cache.db"
        self._init_cache_db()
        self._api = FlightRadar24API()

    def _init_cache_db(self):
        conn = sqlite3.connect(self.cache_db)
        conn.execute('''
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
        row = conn.execute('''
            SELECT * FROM aircraft_cache
            WHERE registration = ?
            AND datetime(timestamp) > datetime('now', '-' || ? || ' hours')
        ''', (registration.upper(), CACHE_EXPIRY_HOURS)).fetchone()
        conn.close()
        if row:
            return {
                'registration': row['registration'],
                'airline': row['airline'],
                'aircraft': row['aircraft'],
                'aircraft_manufacturer': row['aircraft_manufacturer'],
                'aircraft_type': row['aircraft_type'],
                'aircraft_url': row['aircraft_url'],
                'cached': True,
            }
        return None

    def _cache_metadata(self, registration: str, metadata: Dict):
        conn = sqlite3.connect(self.cache_db)
        conn.execute('''
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
        Returns (success, metadata) where metadata contains:
            registration, airline, aircraft, aircraft_manufacturer, aircraft_type, aircraft_url
        On failure, metadata contains 'error' and 'reason'
        ('reg_not_found' | 'request_error').
        """
        registration = registration.upper().strip()

        cached = self._get_cached_metadata(registration)
        if cached:
            return True, cached

        aircraft_url = f"https://www.flightradar24.com/data/aircraft/{registration.lower()}"
        try:
            result = self._api.get_rego_details(registration)
            rows = (result or {}).get('data') or []
        except Exception as e:
            return False, {'error': f'FR24 request error: {str(e)}', 'reason': 'request_error'}

        if not rows:
            # FR24 has no flight history for this registration — most likely a
            # military aircraft, which FR24 doesn't track. Fall back to JetPhotos.
            success, jp_result = JetPhotosFallbackLookup().lookup(registration)
            if success:
                self._cache_metadata(registration, jp_result)
            return success, jp_result

        aircraft_info = (rows[0].get('aircraft') or {})
        model = aircraft_info.get('model') or {}
        model_text = model.get('text') or ''
        owner_name = ((rows[0].get('owner') or {}).get('name') or '').strip()
        airline_name = ((rows[0].get('airline') or {}).get('name') or '').strip()

        # aircraft_meta's regex table expects a bare model designation like
        # "A321-271NY(XLR)" or "737-800" (it was built against JetPhotos'
        # manufacturer-prefix-stripped slugs) — FR24's model.text instead reads
        # "Airbus A321-271NY(XLR)" (manufacturer name spelled out as the first
        # word). Try the full text first, then retry with the leading word
        # stripped, which covers every single-word manufacturer (Airbus,
        # Boeing, Embraer, ...); multi-word manufacturers (e.g. "De Havilland
        # Canada") fall back to '' same as before this lookup existed.
        mfr = aircraft_manufacturer(model_text)
        if not mfr and ' ' in model_text:
            mfr = aircraft_manufacturer(model_text.split(' ', 1)[1])

        metadata = {
            'registration': aircraft_info.get('registration') or registration,
            'airline': owner_name or airline_name,
            'aircraft': model_text,
            'aircraft_manufacturer': mfr,
            'aircraft_type': model.get('code') or '',
            'aircraft_url': aircraft_url,
        }

        self._cache_metadata(registration, metadata)
        return True, metadata
