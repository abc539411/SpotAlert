from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TableView:
    columns: List[str]
    rows: List[Dict[str, Any]]


class SqliteStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        # Default busy timeout (sqlite3.connect's own default) is only 5s — a busy
        # airport's monitor check can hold the write lock for longer than that while
        # bulk-updating sightings/route-types/departure-patterns for hundreds of
        # flights, at which point every OTHER connection (including a web request
        # trying to read Feed data) hits "database is locked" and raises immediately
        # instead of just waiting. 30s gives real contention room to clear on its own.
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")

            # â”€â”€ Table renames migration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            # Rename old table names to new names if old table exists and new doesn't.
            _table_renames = [
                ("exclusion_list",          "filter_exclusions"),
                ("rego_watchlist",          "filter_regos"),
                ("type_watchlist",          "filter_types"),
                ("airline_watchlist",       "filter_airlines"),
                ("special_livery_history",  "livery_cooldowns"),
                ("rare_plane_history",      "rare_plane_cooldowns"),
                ("military_history",        "military_cooldowns"),
                ("sighting_history",        "rego_sightings"),
                ("flight_departure_pattern","departure_patterns"),
                ("route_type_history",      "route_type_tracker"),
                ("flight_events",           "flight_arrivals"),
                ("airframe_db",             "airframes"),
                ("airport_cache",           "airports"),
                ("aircraft_type_cache",     "aircraft_types"),
                ("reg_prefix_country",      "reg_prefixes"),
                ("app_settings",            "settings"),
            ]
            _existing = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            for _old, _new in _table_renames:
                if _old in _existing and _new not in _existing:
                    conn.execute(f"ALTER TABLE {_old} RENAME TO {_new}")
                    log.info("Renamed table %s -> %s", _old, _new)

            # Drop tables that are no longer used
            if "daily_flights" in _existing:
                conn.execute("DROP TABLE daily_flights")
                log.info("Dropped legacy table daily_flights")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS filter_exclusions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    airline TEXT,
                    registration TEXT NOT NULL,
                    description TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS filter_regos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    airline TEXT,
                    registration TEXT NOT NULL,
                    description TEXT,
                    last_notified_ts INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS filter_types (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    airline TEXT NOT NULL,
                    aircraft_type TEXT NOT NULL,
                    last_notified_ts INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS filter_airlines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    icao_code TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    name TEXT,
                    last_notified_ts INTEGER DEFAULT 0
                )
            """)
            # owner_user_id: multi-user retrofit â€” 'controller' is the sentinel for the
            # existing/ground-truth list; a Pilot's own list is fully independent (no
            # merge with the Controller's), matched by owner_user_id alone. Existing
            # rows backfill to 'controller' so today's lists are completely unaffected
            # until Pilot accounts actually exist (Phase 4).
            for _tbl in ("filter_exclusions", "filter_regos", "filter_types", "filter_airlines"):
                _cols = {row[1] for row in conn.execute(f"PRAGMA table_info({_tbl})").fetchall()}
                if "owner_user_id" not in _cols:
                    conn.execute(f"ALTER TABLE {_tbl} ADD COLUMN owner_user_id TEXT NOT NULL DEFAULT 'controller'")
            # Unique indexes extended to include owner_user_id so two different owners
            # (e.g. two Pilots, or a Pilot and the Controller) can each independently
            # exclude/watch the same registration/type/airline without colliding.
            # Existing indexes of the same name (pre-dating owner_user_id) must be
            # dropped first â€” CREATE INDEX IF NOT EXISTS matches by name only, so it
            # would silently keep the old, stricter (owner-less) constraint otherwise.
            conn.execute("DROP INDEX IF EXISTS idx_excl_reg")
            conn.execute("CREATE UNIQUE INDEX idx_excl_reg ON filter_exclusions(owner_user_id, registration)")
            conn.execute("DROP INDEX IF EXISTS idx_rego_reg")
            conn.execute("CREATE UNIQUE INDEX idx_rego_reg ON filter_regos(owner_user_id, registration)")
            conn.execute("DROP INDEX IF EXISTS idx_type_uniq")
            conn.execute("CREATE UNIQUE INDEX idx_type_uniq ON filter_types(owner_user_id, airline, aircraft_type)")
            conn.execute("DROP INDEX IF EXISTS idx_airline_uniq")
            conn.execute("CREATE UNIQUE INDEX idx_airline_uniq ON filter_airlines(owner_user_id, icao_code, entry_type)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS livery_cooldowns (
                    registration TEXT PRIMARY KEY,
                    last_notified_ts INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rare_plane_cooldowns (
                    airline TEXT NOT NULL,
                    aircraft_type TEXT NOT NULL,
                    last_seen_ts INTEGER NOT NULL DEFAULT 0,
                    last_notified_ts INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (airline, aircraft_type)
                )
            """)
            # Add last_seen_ts to existing tables, seeding from last_notified_ts
            rph_cols = {row[1] for row in conn.execute("PRAGMA table_info(rare_plane_cooldowns)").fetchall()}
            if "last_seen_ts" not in rph_cols:
                conn.execute("ALTER TABLE rare_plane_cooldowns ADD COLUMN last_seen_ts INTEGER NOT NULL DEFAULT 0")
                conn.execute("UPDATE rare_plane_cooldowns SET last_seen_ts = last_notified_ts WHERE last_notified_ts > 0")
            # NOTE: 'notification_record' and 'notification_log' are reserved table
            # names for future push-notification features. They are intentionally not
            # created or used anywhere in the current program.

            conn.execute("""
                CREATE TABLE IF NOT EXISTS military_cooldowns (
                    registration TEXT PRIMARY KEY,
                    last_notified_ts INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rego_sightings (
                    registration TEXT PRIMARY KEY,
                    last_seen_ts INTEGER NOT NULL,
                    prev_seen_ts INTEGER DEFAULT NULL
                )
            """)
            sh_cols = {row[1] for row in conn.execute("PRAGMA table_info(rego_sightings)").fetchall()}
            if "prev_seen_ts" not in sh_cols:
                conn.execute("ALTER TABLE rego_sightings ADD COLUMN prev_seen_ts INTEGER DEFAULT NULL")
            if "manufacturer" not in sh_cols:
                conn.execute("ALTER TABLE rego_sightings ADD COLUMN manufacturer TEXT DEFAULT NULL")
            if "airline" not in sh_cols:
                conn.execute("ALTER TABLE rego_sightings ADD COLUMN airline TEXT DEFAULT NULL")
            if "aircraft_type" not in sh_cols:
                conn.execute("ALTER TABLE rego_sightings ADD COLUMN aircraft_type TEXT DEFAULT NULL")
            if "airline_icao" not in sh_cols:
                conn.execute("ALTER TABLE rego_sightings ADD COLUMN airline_icao TEXT DEFAULT NULL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS departure_patterns (
                    arrival_flight_number   TEXT NOT NULL,
                    departure_flight_number TEXT NOT NULL,
                    airport_iata            TEXT NOT NULL,
                    count                   INTEGER NOT NULL DEFAULT 1,
                    last_seen_ts            INTEGER NOT NULL,
                    scheduled_dep_ts        INTEGER DEFAULT NULL,
                    estimated_dep_ts        INTEGER DEFAULT NULL,
                    airline_name            TEXT DEFAULT NULL,
                    airline_iata            TEXT DEFAULT NULL,
                    airline_icao            TEXT DEFAULT NULL,
                    dest_name               TEXT DEFAULT NULL,
                    dest_iata               TEXT DEFAULT NULL,
                    dest_icao               TEXT DEFAULT NULL,
                    PRIMARY KEY (arrival_flight_number, departure_flight_number, airport_iata)
                )
            """)
            fdp_cols = {row[1] for row in conn.execute("PRAGMA table_info(departure_patterns)").fetchall()}
            for col, typ in [
                ("scheduled_dep_ts",  "INTEGER"), ("estimated_dep_ts", "INTEGER"),
                ("airline_name",      "TEXT"),    ("airline_iata",     "TEXT"),
                ("airline_icao",      "TEXT"),    ("dest_name",        "TEXT"),
                ("dest_iata",         "TEXT"),    ("dest_icao",        "TEXT"),
                ("scheduled_arr_ts",  "INTEGER"), ("turnaround_secs",  "INTEGER"),
                ("actual_dep_ts",     "INTEGER"),
            ]:
                if col not in fdp_cols:
                    conn.execute(f"ALTER TABLE departure_patterns ADD COLUMN {col} {typ} DEFAULT NULL")

            # All app settings â€” set via web UI and persisted across restarts.
            # user_id dimension added for multi-user support: 'controller' is the
            # ground-truth sentinel row (what the Controller role owns, and what
            # Passengers always read); a Pilot's own user_id overrides it per-key.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    user_id TEXT NOT NULL DEFAULT 'controller',
                    key     TEXT NOT NULL,
                    value   TEXT NOT NULL,
                    PRIMARY KEY (user_id, key)
                )
            """)
            _settings_cols = {row[1] for row in conn.execute("PRAGMA table_info(settings)").fetchall()}
            if "user_id" not in _settings_cols:
                # Table predates the user_id column (single-key PK) â€” recreate with the
                # composite PK, migrating every existing row to the 'controller' sentinel.
                conn.execute("ALTER TABLE settings RENAME TO settings_old")
                conn.execute("""
                    CREATE TABLE settings (
                        user_id TEXT NOT NULL DEFAULT 'controller',
                        key     TEXT NOT NULL,
                        value   TEXT NOT NULL,
                        PRIMARY KEY (user_id, key)
                    )
                """)
                conn.execute(
                    "INSERT INTO settings (user_id, key, value) "
                    "SELECT 'controller', key, value FROM settings_old"
                )
                conn.execute("DROP TABLE settings_old")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    chat_id  TEXT PRIMARY KEY,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    language TEXT NOT NULL DEFAULT 'en'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS route_type_tracker (
                    flight_number    TEXT NOT NULL,
                    aircraft_type    TEXT NOT NULL,
                    airport_iata     TEXT NOT NULL,
                    count            INTEGER NOT NULL DEFAULT 1,
                    first_seen_ts    INTEGER NOT NULL,
                    last_seen_ts     INTEGER NOT NULL,
                    last_notified_ts INTEGER DEFAULT NULL,
                    PRIMARY KEY (flight_number, aircraft_type, airport_iata)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS airframes (
                    registration  TEXT PRIMARY KEY,
                    icao24        TEXT,
                    manufacturer  TEXT,
                    serial_number TEXT,
                    built_year    INTEGER,
                    owner         TEXT,
                    operator      TEXT,
                    operator_icao TEXT,
                    operator_iata TEXT,
                    fetched_ts    INTEGER NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_airframe_icao24 ON airframes(icao24)"
            )
            af_cols = {row[1] for row in conn.execute("PRAGMA table_info(airframes)").fetchall()}
            if "photo_url" not in af_cols:
                conn.execute("ALTER TABLE airframes ADD COLUMN photo_url TEXT DEFAULT NULL")

            # flight_arrivals is the canonical filter-match store.
            # One row per (registration, flight_number). notif_types is a JSON array of all
            # filter types that matched this flight. notified=1 when a push is sent (future use).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS flight_arrivals (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    registration  TEXT NOT NULL,
                    flight_number TEXT NOT NULL,
                    arrival_ts    INTEGER,
                    first_seen_ts INTEGER NOT NULL,
                    notif_types   TEXT DEFAULT '[]',
                    detail        TEXT DEFAULT '',
                    extra_info    TEXT DEFAULT '',
                    origin_iata   TEXT DEFAULT NULL,
                    origin_name   TEXT DEFAULT NULL,
                    notified      INTEGER DEFAULT 0
                )
            """)
            fe_cols = {row[1] for row in conn.execute("PRAGMA table_info(flight_arrivals)").fetchall()}
            for _col, _typ in [
                ("current_status", "TEXT"),
                ("dep_flight",     "TEXT"),
                ("dep_ts",         "INTEGER"),
                ("dep_dest_iata",  "TEXT"),
                ("dep_dest_name",  "TEXT"),
                ("dep_confidence", "INTEGER"),
                ("arrival_date",   "TEXT"),
                ("arr_label",      "TEXT"),
                ("airline_icao",   "TEXT"),
                ("diverted_to_iata", "TEXT"),
                ("photo_url",      "TEXT"),
                ("aircraft_type",     "TEXT"),
                ("rare_absence_days", "REAL"),
            ]:
                if _col not in fe_cols:
                    conn.execute(f"ALTER TABLE flight_arrivals ADD COLUMN {_col} {_typ} DEFAULT NULL")
            # Migrate unique index to include arrival_date so the same flight number
            # on different calendar days gets its own row (e.g. MF801 Jun 20 vs Jun 21).
            existing_idx = {row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='flight_arrivals'"
            ).fetchall()}
            if "idx_fe_reg_fn_date" not in existing_idx:
                conn.execute("DROP INDEX IF EXISTS idx_fe_reg_fn")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_fe_reg_fn_date "
                    "ON flight_arrivals(registration, flight_number, COALESCE(arrival_date,''))"
                )

            # flight_departures: one row per arrival, linked via arrival_id FK.
            # Departures are independent display items for the feed and timeline.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS flight_departures (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    arrival_id    INTEGER NOT NULL UNIQUE REFERENCES flight_arrivals(id) ON DELETE CASCADE,
                    dep_flight    TEXT,
                    dep_ts        INTEGER,
                    dep_dest_iata TEXT,
                    dep_dest_name TEXT,
                    is_prediction INTEGER NOT NULL DEFAULT 0,
                    dep_label     TEXT DEFAULT NULL
                )
            """)
            fd_cols = {row[1] for row in conn.execute("PRAGMA table_info(flight_departures)").fetchall()}
            if "dep_label" not in fd_cols:
                conn.execute("ALTER TABLE flight_departures ADD COLUMN dep_label TEXT DEFAULT NULL")
            if "dep_confidence" not in fd_cols:
                conn.execute("ALTER TABLE flight_departures ADD COLUMN dep_confidence INTEGER DEFAULT NULL")
            if "cross_day_push_sent" not in fd_cols:
                conn.execute("ALTER TABLE flight_departures ADD COLUMN cross_day_push_sent INTEGER NOT NULL DEFAULT 0")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS military_track_points (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    arrival_id INTEGER NOT NULL REFERENCES flight_arrivals(id) ON DELETE CASCADE,
                    ts         INTEGER NOT NULL,
                    lat        REAL NOT NULL,
                    lon        REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mtp_arrival ON military_track_points(arrival_id)
            """)

            rth_cols = {row[1] for row in conn.execute("PRAGMA table_info(route_type_tracker)").fetchall()}
            if "origin_iata" not in rth_cols:
                conn.execute("ALTER TABLE route_type_tracker ADD COLUMN origin_iata TEXT DEFAULT NULL")
            if "dest_iata" not in rth_cols:
                conn.execute("ALTER TABLE route_type_tracker ADD COLUMN dest_iata TEXT DEFAULT NULL")
            if "airline" not in rth_cols:
                conn.execute("ALTER TABLE route_type_tracker ADD COLUMN airline TEXT DEFAULT NULL")

            # timeline_cache: pre-computed cluster + weather JSON per calendar date.
            # Written at pull time; read instantly at query time.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS airports (
                    iata         TEXT PRIMARY KEY,
                    name         TEXT NOT NULL,
                    country_code TEXT DEFAULT '',
                    source       TEXT DEFAULT 'airportsdata',
                    city         TEXT DEFAULT ''
                )
            """)
            _airports_cols = {row[1] for row in conn.execute("PRAGMA table_info(airports)").fetchall()}
            if "city" not in _airports_cols:
                conn.execute("ALTER TABLE airports ADD COLUMN city TEXT DEFAULT ''")
            # Seed from airportsdata if table is empty
            if conn.execute("SELECT COUNT(*) FROM airports").fetchone()[0] == 0:
                try:
                    import airportsdata as _ad
                    for _iata, _a in _ad.load('IATA').items():
                        conn.execute(
                            "INSERT OR IGNORE INTO airports(iata, name, country_code, source, city) VALUES(?,?,?,?,?)",
                            (_iata, _a.get('name', _iata), _a.get('country', ''), 'airportsdata', _a.get('city', '')),
                        )
                except Exception:
                    pass
            # Backfill city for any existing rows still missing it (e.g. upgraded from an older schema)
            if conn.execute("SELECT COUNT(*) FROM airports WHERE city=''").fetchone()[0] > 0:
                try:
                    import airportsdata as _ad2
                    _by_iata = _ad2.load('IATA')
                    for row in conn.execute("SELECT iata FROM airports WHERE city=''").fetchall():
                        _a = _by_iata.get(row['iata'])
                        if _a and _a.get('city'):
                            conn.execute("UPDATE airports SET city=? WHERE iata=?", (_a['city'], row['iata']))
                except Exception:
                    pass

            conn.execute("""
                CREATE TABLE IF NOT EXISTS aircraft_types (
                    icao   TEXT PRIMARY KEY,
                    name   TEXT NOT NULL,
                    source TEXT DEFAULT 'icaolist'
                )
            """)
            at_cols = {row[1] for row in conn.execute("PRAGMA table_info(aircraft_types)").fetchall()}
            if "manufacturer" not in at_cols:
                # `name` is deliberately just the model (e.g. "A321neo") — the
                # ICAOList CSV's "MANUFACTURER, Model" column gets split on ingest
                # (see refresh_icao_type_list) so existing UI that displays `name`
                # as a type chip doesn't show a redundant manufacturer prefix. But
                # that means `name` alone can't be fed into _derive_manufacturer()
                # for sighting enrichment — store the manufacturer half separately.
                conn.execute("ALTER TABLE aircraft_types ADD COLUMN manufacturer TEXT DEFAULT NULL")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS timeline_cache (
                    date          TEXT PRIMARY KEY,
                    clusters_json TEXT NOT NULL DEFAULT '[]',
                    weather_json  TEXT,
                    computed_at   INTEGER NOT NULL
                )
            """)
            # events_json: the raw pre-exclusion, pre-clustering flight events for the
            # date (registration/ts/side/etc, no _spotted baked in). clusters_json is
            # only ever built from the Controller's own settings/catalog/exclusions â€”
            # a Pilot viewer needs the raw events to re-run clustering with their own
            # settings/catalog/exclusion list instead of inheriting the Controller's.
            tlc_cols = {row[1] for row in conn.execute("PRAGMA table_info(timeline_cache)").fetchall()}
            if "events_json" not in tlc_cols:
                conn.execute("ALTER TABLE timeline_cache ADD COLUMN events_json TEXT")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS fleet_cards (
                    icao         TEXT PRIMARY KEY,
                    iata         TEXT NOT NULL,
                    airline      TEXT NOT NULL,
                    aircraft_json TEXT NOT NULL,
                    added_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                    updated_at   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reg_prefixes (
                    prefix       TEXT PRIMARY KEY,
                    country_code TEXT NOT NULL,
                    country_name TEXT NOT NULL DEFAULT ''
                )
            """)

            fc_cols = {r[1] for r in conn.execute("PRAGMA table_info(fleet_cards)").fetchall()}
            if 'updated_at' not in fc_cols:
                import time as _t
                conn.execute("ALTER TABLE fleet_cards ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0")
                conn.execute("UPDATE fleet_cards SET updated_at=? WHERE updated_at=0", (int(_t.time()),))

    _ICAOLIST_URL      = "https://raw.githubusercontent.com/rikgale/ICAOList/main/ICAOList.csv"
    _ICAOLIST_INTERVAL = 90 * 86400  # 3 months in seconds

    def refresh_icao_type_list(self, force: bool = False) -> None:
        """Download ICAOList.csv and upsert into aircraft_types. User entries are never touched."""
        import time as _time
        last = self.load_setting('icao_list_last_update')
        if not force and last:
            try:
                if _time.time() - float(last) < self._ICAOLIST_INTERVAL:
                    return
            except ValueError:
                pass

        import csv as _csv, io as _io, urllib.request as _req
        log.info("Refreshing ICAO aircraft type list from GitHubâ€¦")
        try:
            with _req.urlopen(self._ICAOLIST_URL, timeout=30) as resp:
                text = resp.read().decode("utf-8-sig")
        except Exception as exc:
            log.warning("Failed to download ICAOList.csv: %s", exc)
            try:
                import system_status as _ss; _ss.record_api('icaolist_github', False, str(exc))
            except Exception:
                pass
            return

        reader = _csv.DictReader(_io.StringIO(text))
        inserted = updated = 0
        with self._connect() as conn:
            for row in reader:
                icao = (row.get("Aircraft TypeDesignator") or "").strip().upper()
                mfr_model = (row.get("MANUFACTURER, Model") or "").strip()
                if not icao or not mfr_model:
                    continue
                if "," in mfr_model:
                    mfr, name = (p.strip() for p in mfr_model.split(",", 1))
                else:
                    mfr, name = None, mfr_model.strip()
                existing = conn.execute(
                    "SELECT source FROM aircraft_types WHERE icao=?", (icao,)
                ).fetchone()
                if existing:
                    if existing[0] == 'user':
                        continue  # never overwrite user entries
                    conn.execute(
                        "UPDATE aircraft_types SET name=?, manufacturer=?, source='icaolist' WHERE icao=?",
                        (name, mfr, icao)
                    )
                    updated += 1
                else:
                    conn.execute(
                        "INSERT INTO aircraft_types(icao, name, manufacturer, source) VALUES(?,?,?,'icaolist')",
                        (icao, name, mfr)
                    )
                    inserted += 1

        import time as _time2
        self.save_setting('icao_list_last_update', str(_time2.time()))
        log.info("ICAO type list refreshed â€” inserted: %d  updated: %d", inserted, updated)
        try:
            import system_status as _ss; _ss.record_api('icaolist_github', True)
        except Exception:
            pass

    def upsert_aircraft_type(self, icao: str, name: str, source: str = 'user', manufacturer: str = None) -> None:
        if not icao or not name:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO aircraft_types(icao, name, source, manufacturer) VALUES(?,?,?,?) "
                "ON CONFLICT(icao) DO UPDATE SET name=excluded.name, source=excluded.source, "
                "manufacturer=excluded.manufacturer "
                "WHERE excluded.source='user' OR aircraft_types.source != 'user'",
                (icao.upper(), name, source, manufacturer),
            )

    def upsert_aircraft_types_bulk(self, rows: list) -> None:
        """Same upsert as upsert_aircraft_type, but for many rows in ONE connection/
        transaction via executemany. rows: [(icao, name, source, manufacturer), ...].
        The full aircraft_types reference table is ~2700 rows and gets copied into
        every OTHER watched airport's DB whenever one is added or reconciled — doing
        that as one open/execute/close per row (thousands of individual SQLite
        connections at startup, multiplied by however many airports are watched) was
        enough to transiently exhaust the process's file-descriptor limit (ulimit -n)
        and crash the whole app on startup once a 4th airport pushed it over ~8000
        connections in a few seconds. One connection for the whole batch avoids this
        entirely and is dramatically faster besides."""
        rows = [(icao.upper(), name, source, manufacturer) for icao, name, source, manufacturer in rows if icao and name]
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO aircraft_types(icao, name, source, manufacturer) VALUES(?,?,?,?) "
                "ON CONFLICT(icao) DO UPDATE SET name=excluded.name, source=excluded.source, "
                "manufacturer=excluded.manufacturer "
                "WHERE excluded.source='user' OR aircraft_types.source != 'user'",
                rows,
            )

    def get_aircraft_type_name(self, icao: str) -> str:
        if not icao:
            return ''
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name FROM aircraft_types WHERE icao=?", (icao.upper(),)
            ).fetchone()
            return row[0] if row else ''

    def get_aircraft_type_names(self, icao_list: list) -> dict:
        """Return {icao: name} for all codes in the list that exist in the cache."""
        if not icao_list:
            return {}
        ph = ','.join('?' * len(icao_list))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT icao, name FROM aircraft_types WHERE icao IN ({ph})",
                [c.upper() for c in icao_list],
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def get_watchlist_sets(self, owner_user_id: str) -> dict:
        """This owner's own watchlist entries across the 3 filter tables — used to
        re-derive per-viewer visibility of the shared "Watchlist Registration"/
        "Watchlist Aircraft Type"/"Watchlist Airline" tags baked into flight_arrivals/
        timeline_cache at ingestion time (monitor.py's check_*_watchlist functions
        match against ANY owner's rows, not just this one's — see
        web.py's _strip_unowned_watchlist_tags for the full story)."""
        with self._connect() as conn:
            regos = {r[0] for r in conn.execute(
                "SELECT registration FROM filter_regos WHERE owner_user_id = ?", (owner_user_id,)).fetchall()}
            types = {(r[0], r[1]) for r in conn.execute(
                "SELECT airline, aircraft_type FROM filter_types WHERE owner_user_id = ?", (owner_user_id,)).fetchall()}
            airline_icaos = {r[0] for r in conn.execute(
                "SELECT icao_code FROM filter_airlines WHERE owner_user_id = ?", (owner_user_id,)).fetchall()}
        return {"regos": regos, "types": types, "airline_icaos": airline_icaos}

    def get_aircraft_type_manufacturers(self, icao_list: list) -> dict:
        """Return {icao: manufacturer} for codes with a known manufacturer. `name` alone
        (the model, e.g. "A321neo") can't be fed into _derive_manufacturer() — this reads
        the raw CSV manufacturer half stored separately (see aircraft_types.manufacturer)."""
        if not icao_list:
            return {}
        ph = ','.join('?' * len(icao_list))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT icao, manufacturer FROM aircraft_types WHERE icao IN ({ph}) AND manufacturer IS NOT NULL",
                [c.upper() for c in icao_list],
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def upsert_airport(self, iata: str, name: str, country_code: str, source: str = 'fr24') -> None:
        """Insert or update airport info. Priority: user > fr24 > airportsdata — a 'user'
        write always wins (and can never be clobbered by a later fr24/airportsdata
        auto-population write); among the two non-user sources, fr24 still wins over
        airportsdata as before. city is never overwritten (FR24 doesn't supply it)."""
        if not iata or not name:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO airports(iata, name, country_code, source) VALUES(?,?,?,?) "
                "ON CONFLICT(iata) DO UPDATE SET name=excluded.name, country_code=excluded.country_code, "
                "source=excluded.source WHERE excluded.source='user' "
                "OR (airports.source != 'user' AND (excluded.source='fr24' OR airports.source='airportsdata'))",
                (iata.upper(), name, country_code or '', source),
            )

    def get_airport_info(self, iata: str):
        """Return (name, country_code, city) or None if not cached."""
        if not iata:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, country_code, city FROM airports WHERE iata=?", (iata.upper(),)
            ).fetchone()
            return (row['name'], row['country_code'], row['city']) if row else None

    def upsert_timeline_cache(self, date: str, clusters_json: str,
                              weather_json: Optional[str] = None,
                              events_json: Optional[str] = None) -> None:
        """Store pre-computed cluster (and optionally weather/raw-events) JSON for a
        calendar date. events_json lets a Pilot viewer re-cluster with their own
        settings/catalog/exclusions instead of the Controller's baked-in result."""
        import time as _time
        with self._connect() as conn:
            cols = ["date", "clusters_json", "computed_at"]
            vals = [date, clusters_json, int(_time.time())]
            updates = ["clusters_json=excluded.clusters_json", "computed_at=excluded.computed_at"]
            if weather_json is not None:
                cols.append("weather_json"); vals.append(weather_json)
                updates.append("weather_json=excluded.weather_json")
            if events_json is not None:
                cols.append("events_json"); vals.append(events_json)
                updates.append("events_json=excluded.events_json")
            placeholders = ",".join("?" * len(vals))
            conn.execute(
                f"INSERT INTO timeline_cache({','.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT(date) DO UPDATE SET {', '.join(updates)}",
                vals,
            )

    def get_timeline_cache(self, dates: List[str]) -> Dict[str, dict]:
        """Return {date: {clusters_json, weather_json, events_json, computed_at}} for the given dates."""
        if not dates:
            return {}
        placeholders = ",".join("?" * len(dates))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT date, clusters_json, weather_json, events_json, computed_at "
                f"FROM timeline_cache WHERE date IN ({placeholders})",
                dates,
            ).fetchall()
        return {row["date"]: dict(row) for row in rows}

    def backfill_arrival_dates(self, tz_name: str) -> int:
        """Set arrival_date for rows where it is NULL, using arrival_ts + timezone. Returns count."""
        import pytz as _pytz
        from datetime import datetime as _dt
        _tz = _pytz.timezone(tz_name)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, arrival_ts FROM flight_arrivals WHERE arrival_date IS NULL AND arrival_ts IS NOT NULL"
            ).fetchall()
            updated = 0
            for row in rows:
                try:
                    date_str = _dt.fromtimestamp(row["arrival_ts"], _tz).strftime("%Y-%m-%d")
                    conn.execute("UPDATE flight_arrivals SET arrival_date = ? WHERE id = ?",
                                 (date_str, row["id"]))
                    updated += 1
                except Exception:
                    pass
        return updated

    # ------------------------------------------------------------------
    # App settings (bot-managed, persisted across restarts)
    # ------------------------------------------------------------------

    # â”€â”€ Fleet cards â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def get_fleet_cards(self) -> list:
        import json as _json
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT icao, iata, airline, aircraft_json, updated_at FROM fleet_cards ORDER BY added_at"
            ).fetchall()
        return [{'icao': r[0], 'iata': r[1], 'airline': r[2], 'aircraft': _json.loads(r[3]), 'updated_at': r[4]} for r in rows]

    def upsert_fleet_card(self, icao: str, iata: str, airline: str, aircraft: list, updated_at: int = None) -> None:
        import json as _json, time as _time
        ts = updated_at or int(_time.time())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO fleet_cards(icao, iata, airline, aircraft_json, updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(icao) DO UPDATE SET iata=excluded.iata, airline=excluded.airline, "
                "aircraft_json=excluded.aircraft_json, updated_at=excluded.updated_at",
                (icao.upper(), iata.upper(), airline, _json.dumps(aircraft), ts),
            )

    def update_fleet_card_photos(self, icao: str, aircraft: list) -> None:
        """Replace aircraft_json with updated list (photos + last session data)."""
        import json as _json
        with self._connect() as conn:
            conn.execute("UPDATE fleet_cards SET aircraft_json=? WHERE icao=?",
                         (_json.dumps(aircraft), icao.upper()))

    def get_reg_prefix_country(self, prefix: str):
        with self._connect() as conn:
            row = conn.execute("SELECT country_code, country_name FROM reg_prefixes WHERE prefix=?",
                               (prefix.upper(),)).fetchone()
        return {'prefix': prefix.upper(), 'cc': row[0], 'name': row[1]} if row else None

    def save_reg_prefix_country(self, prefix: str, country_code: str, country_name: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO reg_prefixes(prefix, country_code, country_name) VALUES(?,?,?)",
                         (prefix.upper(), country_code.upper(), country_name))

    def delete_fleet_card(self, icao: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM fleet_cards WHERE icao=?", (icao.upper(),))

    def save_setting(self, key: str, value: str) -> None:
        """Writes the Controller-role ground-truth row. Existing call sites are
        unaffected by the multi-user settings retrofit â€” they all mean "the
        Controller's setting" until a caller explicitly scopes to a Pilot via
        set_setting()."""
        self.set_setting("controller", key, value)

    def load_setting(self, key: str) -> Optional[str]:
        """Reads the Controller-role ground-truth row (see save_setting)."""
        return self.get_setting("controller", key)

    def set_setting(self, user_id: str, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings(user_id, key, value) VALUES (?, ?, ?)",
                (user_id, key, str(value)),
            )

    def get_setting(self, user_id: str, key: str, fallback_to_controller: bool = True) -> Optional[str]:
        """Reads user_id's own row for this key; if absent and fallback_to_controller,
        falls back to the 'controller' ground-truth row (e.g. a Pilot who hasn't
        overridden this particular key yet)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE user_id = ? AND key = ?", (user_id, key)
            ).fetchone()
            if row:
                return row["value"]
            if fallback_to_controller and user_id != "controller":
                row = conn.execute(
                    "SELECT value FROM settings WHERE user_id = 'controller' AND key = ?", (key,)
                ).fetchone()
                return row["value"] if row else None
            return None

    # ------------------------------------------------------------------
    # CSV migration (one-time import from legacy)
    # ------------------------------------------------------------------

    def migrate_from_csv_folder(self, csv_folder: str) -> None:
        csv_map = {
            "filter_exclusions.csv":        ("filter_exclusions",        ["Airline", "Registration", "Description"]),
            "filter_regos.csv":        ("filter_regos",        ["Airline", "Registration", "Description", "Time"]),
            "filter_types.csv":        ("filter_types",        ["Airline", "Aircraft Type", "Time"]),
            "livery_cooldowns.csv":("livery_cooldowns",["Registration", "Time"]),
            "rare_plane_cooldowns.csv":    ("rare_plane_cooldowns",    ["Airline", "Aircraft Type", "Time"]),
        }
        with self._connect() as conn:
            for filename, (table, expected_cols) in csv_map.items():
                if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    continue
                path = os.path.join(csv_folder, filename)
                if os.path.isfile(path):
                    self._import_csv(conn, table, path, expected_cols)

    def _import_csv(self, conn: sqlite3.Connection, table: str, csv_path: str, expected_cols: List[str]) -> None:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return
            if any(c not in reader.fieldnames for c in expected_cols):
                return

            if table == "filter_exclusions":
                for r in reader:
                    rego = (r.get("Registration") or "").strip()
                    if rego:
                        conn.execute(
                            "INSERT OR IGNORE INTO filter_exclusions(airline, registration, description) VALUES (?,?,?)",
                            ((r.get("Airline") or "").strip(), rego, (r.get("Description") or "").strip()),
                        )
            elif table == "filter_regos":
                for r in reader:
                    rego = (r.get("Registration") or "").strip()
                    if rego:
                        conn.execute(
                            "INSERT OR IGNORE INTO filter_regos(airline, registration, description, last_notified_ts) VALUES (?,?,?,?)",
                            ((r.get("Airline") or "").strip(), rego, (r.get("Description") or "").strip(), _parse_int(r.get("Time"))),
                        )
            elif table == "filter_types":
                for r in reader:
                    airline = (r.get("Airline") or "").strip()
                    ac_type = (r.get("Aircraft Type") or "").strip()
                    if airline and ac_type:
                        conn.execute(
                            "INSERT OR IGNORE INTO filter_types(airline, aircraft_type, last_notified_ts) VALUES (?,?,?)",
                            (airline, ac_type, _parse_int(r.get("Time"))),
                        )
            elif table == "livery_cooldowns":
                for r in reader:
                    rego = (r.get("Registration") or "").strip()
                    ts = _parse_int(r.get("Time"))
                    if rego and ts is not None:
                        conn.execute(
                            "INSERT OR REPLACE INTO livery_cooldowns(registration, last_notified_ts) VALUES (?,?)",
                            (rego, ts),
                        )
            elif table == "rare_plane_cooldowns":
                for r in reader:
                    airline = (r.get("Airline") or "").strip()
                    ac_type = (r.get("Aircraft Type") or "").strip()
                    ts = _parse_int(r.get("Time"))
                    if airline and ac_type and ts is not None:
                        conn.execute(
                            "INSERT OR REPLACE INTO rare_plane_cooldowns(airline, aircraft_type, last_notified_ts) VALUES (?,?,?)",
                            (airline, ac_type, ts),
                        )

    # ------------------------------------------------------------------
    # List views for Telegram UI
    # ------------------------------------------------------------------

    def get_list_view(self, list_name: str) -> TableView:
        if list_name == "Exclusion List":
            cols = ["Registration", "Description"]
            rows = self._fetch(
                "SELECT registration, description FROM filter_exclusions ORDER BY id ASC"
            )
            return TableView(columns=cols, rows=[
                {"Registration": r["registration"], "Description": r["description"]}
                for r in rows
            ])
        if list_name == "Rego Watchlist":
            cols = ["Registration", "Description"]
            rows = self._fetch(
                "SELECT registration, description FROM filter_regos ORDER BY id ASC"
            )
            return TableView(columns=cols, rows=[
                {"Registration": r["registration"], "Description": r["description"]}
                for r in rows
            ])
        if list_name == "Type Watchlist":
            cols = ["Airline", "Aircraft Type"]
            rows = self._fetch(
                "SELECT airline, aircraft_type FROM filter_types ORDER BY id ASC"
            )
            return TableView(columns=cols, rows=[
                {"Airline": r["airline"], "Aircraft Type": r["aircraft_type"]}
                for r in rows
            ])
        if list_name == "Airline/Operator Watchlist":
            cols = ["ICAO Code", "Type", "Name"]
            rows = self._fetch(
                "SELECT icao_code, entry_type, name FROM filter_airlines ORDER BY id ASC"
            )
            return TableView(columns=cols, rows=[
                {"ICAO Code": r["icao_code"], "Type": r["entry_type"].capitalize(), "Name": r["name"] or ""}
                for r in rows
            ])
        raise ValueError(f"Unknown list: {list_name!r}")

    def copy_controller_settings_to_owner(self, owner_user_id: str, keys) -> None:
        """One-time seed of the Controller's current value for each key in
        `keys` (PILOT_EDITABLE_SETTINGS) into owner_user_id's own settings
        row â€” called alongside copy_controller_filters_to_owner, same timing
        and same idempotency guarantee: a no-op per key once owner_user_id
        already has a row of their own for it, so it never overwrites edits
        made after the initial seed. After this runs, that Pilot's value for
        each key is fully independent â€” no live fallback to the Controller's
        row (see web.py's _pilot_setting)."""
        with self._connect() as conn:
            for key in keys:
                if conn.execute(
                    "SELECT 1 FROM settings WHERE user_id = ? AND key = ?", (owner_user_id, key)
                ).fetchone():
                    continue
                row = conn.execute(
                    "SELECT value FROM settings WHERE user_id = 'controller' AND key = ?", (key,)
                ).fetchone()
                if row is not None:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings(user_id, key, value) VALUES (?, ?, ?)",
                        (owner_user_id, key, row["value"]),
                    )

    def copy_controller_filters_to_owner(self, owner_user_id: str) -> None:
        """One-time snapshot of the Controller's current exclusion/watchlist
        rows into owner_user_id's own rows, across all 4 tables â€” called when
        a Pilot is first set up or granted access to a new airport. Safe to
        call repeatedly: a no-op for any table owner_user_id already has rows
        in (so it never overwrites edits made after the initial copy), and
        after this runs, that Pilot's list is fully independent â€” no further
        Controller changes propagate, and an empty Pilot list stays empty."""
        with self._connect() as conn:
            if not conn.execute(
                "SELECT 1 FROM filter_exclusions WHERE owner_user_id = ? LIMIT 1", (owner_user_id,)
            ).fetchone():
                for r in conn.execute(
                    "SELECT airline, registration, description FROM filter_exclusions WHERE owner_user_id = 'controller'"
                ).fetchall():
                    conn.execute(
                        "INSERT OR IGNORE INTO filter_exclusions(airline, registration, description, owner_user_id) VALUES (?,?,?,?)",
                        (r["airline"], r["registration"], r["description"], owner_user_id),
                    )
            if not conn.execute(
                "SELECT 1 FROM filter_regos WHERE owner_user_id = ? LIMIT 1", (owner_user_id,)
            ).fetchone():
                for r in conn.execute(
                    "SELECT airline, registration, description FROM filter_regos WHERE owner_user_id = 'controller'"
                ).fetchall():
                    conn.execute(
                        "INSERT OR IGNORE INTO filter_regos(airline, registration, description, last_notified_ts, owner_user_id) VALUES (?,?,?,0,?)",
                        (r["airline"], r["registration"], r["description"], owner_user_id),
                    )
            if not conn.execute(
                "SELECT 1 FROM filter_types WHERE owner_user_id = ? LIMIT 1", (owner_user_id,)
            ).fetchone():
                for r in conn.execute(
                    "SELECT airline, aircraft_type FROM filter_types WHERE owner_user_id = 'controller'"
                ).fetchall():
                    conn.execute(
                        "INSERT OR IGNORE INTO filter_types(airline, aircraft_type, last_notified_ts, owner_user_id) VALUES (?,?,0,?)",
                        (r["airline"], r["aircraft_type"], owner_user_id),
                    )
            if not conn.execute(
                "SELECT 1 FROM filter_airlines WHERE owner_user_id = ? LIMIT 1", (owner_user_id,)
            ).fetchone():
                for r in conn.execute(
                    "SELECT icao_code, entry_type, name FROM filter_airlines WHERE owner_user_id = 'controller'"
                ).fetchall():
                    conn.execute(
                        "INSERT OR IGNORE INTO filter_airlines(icao_code, entry_type, name, last_notified_ts, owner_user_id) VALUES (?,?,?,0,?)",
                        (r["icao_code"], r["entry_type"], r["name"], owner_user_id),
                    )

    def add_exclusion(self, airline: str, registration: str, description: str,
                       owner_user_id: str = "controller") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO filter_exclusions(airline, registration, description, owner_user_id) VALUES (?,?,?,?)",
                (airline.strip(), registration.strip(), description.strip(), owner_user_id),
            )

    def add_rego_watch(self, airline: str, registration: str, description: str,
                        owner_user_id: str = "controller") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO filter_regos(airline, registration, description, last_notified_ts, owner_user_id) VALUES (?,?,?,0,?)",
                (airline.strip(), registration.strip(), description.strip(), owner_user_id),
            )

    def add_type_watch(self, airline: str, aircraft_type: str, owner_user_id: str = "controller") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO filter_types(airline, aircraft_type, last_notified_ts, owner_user_id) VALUES (?,?,0,?)",
                (airline.strip(), aircraft_type.strip(), owner_user_id),
            )

    def delete_entries_by_index(self, list_name: str, indexes: Sequence[int]) -> TableView:
        table, select_sql = _list_meta(list_name)
        ids = [int(r["id"]) for r in self._fetch(select_sql)]
        rows_to_delete = []
        for i in indexes:
            if i < 0 or i >= len(ids):
                raise IndexError(f"Index {i} out of range")
            rows_to_delete.append(ids[i])
        with self._connect() as conn:
            for row_id in rows_to_delete:
                conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
        return self.get_list_view(list_name)

    # ------------------------------------------------------------------
    # Watchlist membership checks (read-only, no side effects)
    # ------------------------------------------------------------------

    def is_on_rego_watchlist(self, registration: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM filter_regos WHERE registration = ? LIMIT 1",
                (registration.strip(),),
            ).fetchone() is not None

    def is_on_type_watchlist(self, airline: str, aircraft_type: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM filter_types WHERE airline = ? AND aircraft_type = ? LIMIT 1",
                (airline.strip(), aircraft_type.strip()),
            ).fetchone() is not None

    # ------------------------------------------------------------------
    # Exclusion check
    # ------------------------------------------------------------------

    def is_excluded(self, registration: str) -> bool:
        registration = registration.strip()
        if not registration:
            return False
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM filter_exclusions WHERE registration = ? LIMIT 1", (registration,)
            ).fetchone() is not None

    # ------------------------------------------------------------------
    # Throttle checks (read-only) + mark methods (write after successful send)
    #
    # Design: should_notify_* never writes timestamps. Instead they insert a
    # sentinel (ts=0) on first sight so we can track "seen but not yet notified".
    # The mark_*_notified methods write the real timestamp and are called only
    # after the Telegram message is confirmed sent. This prevents the DB from
    # recording a notification that was never actually delivered.
    # ------------------------------------------------------------------

    def should_notify_special_livery(self, registration: str, now_ts: int, min_hours: int) -> bool:
        registration = registration.strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_notified_ts FROM livery_cooldowns WHERE registration = ?",
                (registration,),
            ).fetchone()
            if row is None:
                # First time seen â€” insert sentinel so we don't lose track of it
                conn.execute(
                    "INSERT INTO livery_cooldowns(registration, last_notified_ts) VALUES (?,0)",
                    (registration,),
                )
                return True
            last_ts = int(row["last_notified_ts"])
            # ts=0 means a previous send attempt failed â€” always retry
            if last_ts == 0:
                return True
            return (now_ts - last_ts) / 3600 > min_hours

    def mark_special_livery_notified(self, registration: str, now_ts: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE livery_cooldowns SET last_notified_ts = ? WHERE registration = ?",
                (now_ts, registration.strip()),
            )

    def record_rare_plane_sighting(self, airline: str, aircraft_type: str, now_ts: int) -> Optional[float]:
        """Record a sighting and return how many days this combo had been absent
        BEFORE this sighting (None if never seen before â€” 'infinitely' rare).

        Always updates last_seen_ts to now_ts, unconditionally â€” the absence
        threshold is a per-viewer display concern (see web.py's per-viewer
        Rare Plane re-tagging), not an ingestion-time decision anymore. The
        caller is responsible for snapshotting the returned value onto the
        flight_arrivals row, since last_seen_ts here keeps moving forward and
        can't be used to reconstruct 'how rare was it AT THE TIME this flight
        arrived' after the fact.
        """
        airline, aircraft_type = airline.strip(), aircraft_type.strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_seen_ts FROM rare_plane_cooldowns WHERE airline = ? AND aircraft_type = ?",
                (airline, aircraft_type),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO rare_plane_cooldowns(airline, aircraft_type, last_seen_ts, last_notified_ts) VALUES (?,?,?,0)",
                    (airline, aircraft_type, now_ts),
                )
                return None
            last_seen = int(row["last_seen_ts"])
            days_absent = None if last_seen == 0 else (now_ts - last_seen) / 86400
            conn.execute(
                "UPDATE rare_plane_cooldowns SET last_seen_ts = ? WHERE airline = ? AND aircraft_type = ?",
                (now_ts, airline, aircraft_type),
            )
            return days_absent

    def get_rare_plane_last_seen(self, airline: str, aircraft_type: str) -> Optional[int]:
        """Return last_seen_ts for a rare plane combo, or None if never seen."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_seen_ts FROM rare_plane_cooldowns WHERE airline = ? AND aircraft_type = ?",
                (airline.strip(), aircraft_type.strip()),
            ).fetchone()
            return int(row["last_seen_ts"]) if row else None

    def mark_rare_plane_notified(self, airline: str, aircraft_type: str, now_ts: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE rare_plane_cooldowns SET last_notified_ts = ? WHERE airline = ? AND aircraft_type = ?",
                (now_ts, airline.strip(), aircraft_type.strip()),
            )

    def should_notify_rego_watchlist(self, registration: str, now_ts: int, min_hours: int) -> bool:
        registration = registration.strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_notified_ts FROM filter_regos WHERE registration = ?",
                (registration,),
            ).fetchone()
            if row is None:
                return False
            last_ts = row["last_notified_ts"]
            if last_ts is None or int(last_ts) == 0:
                return True
            return (now_ts - int(last_ts)) / 3600 > min_hours

    def mark_rego_notified(self, registration: str, now_ts: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE filter_regos SET last_notified_ts = ? WHERE registration = ?",
                (now_ts, registration.strip()),
            )

    def should_notify_type_watchlist(self, airline: str, aircraft_type: str, now_ts: int, min_hours: int) -> bool:
        airline, aircraft_type = airline.strip(), aircraft_type.strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_notified_ts FROM filter_types WHERE airline = ? AND aircraft_type = ?",
                (airline, aircraft_type),
            ).fetchone()
            if row is None:
                return False
            last_ts = row["last_notified_ts"]
            if last_ts is None or int(last_ts) == 0:
                return True
            return (now_ts - int(last_ts)) / 3600 > min_hours

    def mark_type_notified(self, airline: str, aircraft_type: str, now_ts: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE filter_types SET last_notified_ts = ? WHERE airline = ? AND aircraft_type = ?",
                (now_ts, airline.strip(), aircraft_type.strip()),
            )

    def add_airline_watch(self, icao_code: str, entry_type: str, name: str,
                           owner_user_id: str = "controller") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO filter_airlines(icao_code, entry_type, name, last_notified_ts, owner_user_id)"
                " VALUES (?,?,?,0,?)",
                (icao_code.strip().upper(), entry_type.strip(), name.strip(), owner_user_id),
            )

    def should_notify_airline_watchlist(self, icao_code: str, entry_type: str,
                                        now_ts: int, min_hours: int) -> bool:
        icao_code, entry_type = icao_code.strip().upper(), entry_type.strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_notified_ts FROM filter_airlines WHERE icao_code = ? AND entry_type = ?",
                (icao_code, entry_type),
            ).fetchone()
            if row is None:
                return False
            last_ts = row["last_notified_ts"]
            if last_ts is None or int(last_ts) == 0:
                return True
            return (now_ts - int(last_ts)) / 3600 > min_hours

    def mark_airline_notified(self, icao_code: str, entry_type: str, now_ts: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE filter_airlines SET last_notified_ts = ? WHERE icao_code = ? AND entry_type = ?",
                (now_ts, icao_code.strip().upper(), entry_type.strip()),
            )

    def is_on_airline_watchlist(self, icao_code: str, entry_type: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM filter_airlines WHERE icao_code = ? AND entry_type = ? LIMIT 1",
                (icao_code.strip().upper(), entry_type.strip()),
            ).fetchone() is not None

    def should_notify_military(self, registration: str, now_ts: int, min_hours: int) -> bool:
        registration = registration.strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_notified_ts FROM military_cooldowns WHERE registration = ?",
                (registration,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO military_cooldowns(registration, last_notified_ts) VALUES (?,0)",
                    (registration,),
                )
                return True
            last_ts = int(row["last_notified_ts"])
            if last_ts == 0:
                return True
            return (now_ts - last_ts) / 3600 > min_hours

    def mark_military_notified(self, registration: str, now_ts: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE military_cooldowns SET last_notified_ts = ? WHERE registration = ?",
                (now_ts, registration.strip()),
            )

    def add_military_track_point(self, arrival_id: int, ts: int, lat: float, lon: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO military_track_points(arrival_id, ts, lat, lon) VALUES (?,?,?,?)",
                (arrival_id, ts, lat, lon),
            )

    def get_military_track_points(self, arrival_ids: list) -> dict:
        """Return {arrival_id: [{ts, lat, lon}, ...]} for the given arrival ids, ordered by ts."""
        if not arrival_ids:
            return {}
        with self._connect() as conn:
            ph = ",".join("?" * len(arrival_ids))
            rows = conn.execute(
                f"SELECT arrival_id, ts, lat, lon FROM military_track_points "
                f"WHERE arrival_id IN ({ph}) ORDER BY arrival_id, ts",
                arrival_ids,
            ).fetchall()
        out: dict = {}
        for row in rows:
            out.setdefault(row["arrival_id"], []).append(
                {"ts": row["ts"], "lat": row["lat"], "lon": row["lon"]}
            )
        return out

    def get_resumable_military_visits(self, now_ts: int, exit_secs: int) -> dict:
        """Return {registration: {"arrival_id": id, "last_in_radius_ts": ts}} for the
        most recent military flight_arrivals row per registration whose latest track
        point is still within the exit grace window. Used on startup to resume
        cfg.military_rapid_tracking (an in-memory-only dict) after a process restart,
        so a restart mid-visit doesn't get treated as a brand-new approach and
        fragment one continuous visit into multiple flight_arrivals rows."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT fa.registration, fa.id AS arrival_id, MAX(mtp.ts) AS last_ts
                FROM flight_arrivals fa
                JOIN military_track_points mtp ON mtp.arrival_id = fa.id
                WHERE fa.notif_types LIKE '%"Military"%'
                GROUP BY fa.id
                """
            ).fetchall()
        latest_per_reg: dict = {}
        for row in rows:
            reg = row["registration"]
            last_ts = row["last_ts"]
            if reg not in latest_per_reg or last_ts > latest_per_reg[reg][1]:
                latest_per_reg[reg] = (row["arrival_id"], last_ts)
        return {
            reg: {"arrival_id": arrival_id, "last_in_radius_ts": last_ts}
            for reg, (arrival_id, last_ts) in latest_per_reg.items()
            if now_ts - last_ts <= exit_secs
        }

    # ------------------------------------------------------------------
    # Follow-up tracking (reminder + cancellation/diversion)
    # ------------------------------------------------------------------

    def update_flight_event_status(self, registration: str, flight_number: str,
                                    current_status: str, arrival_ts: int,
                                    arrival_date: str = None,
                                    arr_label: str = None,
                                    diverted_to_iata: str = None) -> None:
        """Refresh live status and latest arrival estimate for a flight in flight_arrivals.
        arr_label ('Arrived'|'Estimated'|'Scheduled'|...) and diverted_to_iata are only
        written when provided, never cleared to NULL.
        """
        set_cols = ["current_status = ?", "arrival_ts = ?"]
        params = [current_status, arrival_ts]
        if arr_label:
            set_cols.append("arr_label = ?")
            params.append(arr_label)
        if diverted_to_iata:
            set_cols.append("diverted_to_iata = ?")
            params.append(diverted_to_iata)

        where = "WHERE registration = ? AND flight_number = ?"
        params += [registration.strip(), flight_number]
        if arrival_date:
            where += " AND arrival_date = ?"
            params.append(arrival_date)

        with self._connect() as conn:
            conn.execute(
                f"UPDATE flight_arrivals SET {', '.join(set_cols)} {where}",
                params,
            )

    def flight_event_exists(self, registration: str, flight_number: str,
                             arrival_date: str = None) -> bool:
        """Return True if this (registration, flight_number, arrival_date) is already stored."""
        with self._connect() as conn:
            if arrival_date:
                row = conn.execute(
                    "SELECT 1 FROM flight_arrivals WHERE registration = ? AND flight_number = ? "
                    "AND arrival_date = ?",
                    (registration.strip(), flight_number, arrival_date),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM flight_arrivals WHERE registration = ? AND flight_number = ?",
                    (registration.strip(), flight_number),
                ).fetchone()
        return row is not None

    def upsert_flight_departure(
        self,
        arrival_id: int,
        dep_flight: Optional[str],
        dep_ts: Optional[int],
        dep_dest_iata: Optional[str] = None,
        dep_dest_name: Optional[str] = None,
        is_prediction: bool = False,
        dep_label: Optional[str] = None,
        dep_confidence: Optional[int] = None,
    ) -> None:
        """Insert or update the departure paired with an arrival row.

        Real data (is_prediction=False) always wins.
        A prediction never overwrites real data once real data has been stored.
        dep_label: 'Estimated' | 'Scheduled' | 'Predicted' â€” shown in the UI.
        dep_confidence: 0-100 pattern confidence, only set for predictions.
        """
        pred_int = 1 if is_prediction else 0
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO flight_departures
                    (arrival_id, dep_flight, dep_ts, dep_dest_iata, dep_dest_name,
                     is_prediction, dep_label, dep_confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(arrival_id) DO UPDATE SET
                    dep_flight     = excluded.dep_flight,
                    dep_ts         = excluded.dep_ts,
                    dep_dest_iata  = excluded.dep_dest_iata,
                    dep_dest_name  = excluded.dep_dest_name,
                    is_prediction  = excluded.is_prediction,
                    dep_label      = excluded.dep_label,
                    dep_confidence = excluded.dep_confidence
                WHERE (excluded.is_prediction = 0 OR flight_departures.is_prediction = 1)
                  AND (flight_departures.dep_label != 'Departed'
                       OR excluded.dep_label = 'Departed')
                """,
                (arrival_id, dep_flight, dep_ts, dep_dest_iata, dep_dest_name,
                 pred_int, dep_label, dep_confidence),
            )

    def try_claim_cross_day_departure_push(self, arrival_id: int) -> bool:
        """Atomically marks this arrival's cross-day-departure push as handled,
        returning True only the very first time this is called for a given
        arrival_id — False on every subsequent call (e.g. the same next-day
        departure getting reconfirmed on later check cycles). Caller decides
        whether "handled" means actually sending a push or silently consuming
        the one-time trigger (see monitor.py's run_check Step 7b: a cross-day
        departure already known the moment the arrival card was first created
        is consumed without sending, since the arrival's own push already told
        the whole story)."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE flight_departures SET cross_day_push_sent = 1 "
                "WHERE arrival_id = ? AND cross_day_push_sent = 0",
                (arrival_id,),
            )
            return cur.rowcount > 0

    def get_flight_departure(self, arrival_id: int) -> Optional[dict]:
        """Return the paired departure row for an arrival, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT dep_flight, dep_ts, dep_dest_iata, dep_dest_name, is_prediction "
                "FROM flight_departures WHERE arrival_id = ?",
                (arrival_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_departures_for_dates(self, dates: List[str]) -> List[dict]:
        """Return all flight_departures rows whose dep_ts falls on any of the given
        local dates (YYYY-MM-DD strings). Joined with flight_arrivals for rego context."""
        placeholders = ",".join("?" * len(dates))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT fd.id, fd.arrival_id, fd.dep_flight, fd.dep_ts,
                       fd.dep_dest_iata, fd.dep_dest_name, fd.is_prediction,
                       fe.registration, fe.flight_number, fe.notif_types,
                       fe.detail, fe.extra_info, fe.arrival_ts, fe.arrival_date,
                       fe.current_status
                FROM flight_departures fd
                JOIN flight_arrivals fe ON fe.id = fd.arrival_id
                WHERE fd.dep_ts IS NOT NULL
                """,
            ).fetchall()
        return [dict(r) for r in rows]

    def record_filter_match(
        self,
        registration: str,
        flight_number: str,
        notif_types: list,
        arrival_ts: int,
        first_seen_ts: int,
        detail: str = "",
        extra_info: str = "",
        origin_iata: str = None,
        origin_name: str = None,
        arrival_date: str = None,
        airline_icao: str = None,
        photo_url: str = None,
        aircraft_type: str = None,
        rare_absence_days: float = None,
    ) -> Optional[int]:
        """Store a filter-matched flight in flight_arrivals. Merges notif_types if already present.
        Returns the affected row's id. For "was this row brand new" (used to
        trigger a push notification exactly once per Feed card, not on every
        re-check of an already-known flight), see record_filter_match_ex below —
        this method is kept id-only for the many callers that don't care."""
        return self.record_filter_match_ex(
            registration, flight_number, notif_types, arrival_ts, first_seen_ts,
            detail=detail, extra_info=extra_info, origin_iata=origin_iata, origin_name=origin_name,
            arrival_date=arrival_date, airline_icao=airline_icao, photo_url=photo_url,
            aircraft_type=aircraft_type, rare_absence_days=rare_absence_days,
        )[0]

    def record_filter_match_ex(
        self,
        registration: str,
        flight_number: str,
        notif_types: list,
        arrival_ts: int,
        first_seen_ts: int,
        detail: str = "",
        extra_info: str = "",
        origin_iata: str = None,
        origin_name: str = None,
        arrival_date: str = None,
        airline_icao: str = None,
        photo_url: str = None,
        aircraft_type: str = None,
        rare_absence_days: float = None,
    ) -> "tuple":
        """Same as record_filter_match, but returns (id, is_new) — is_new is True
        only the first time this (registration, flight_number[, arrival_date])
        triple is ever recorded, False on every subsequent re-check/update."""
        if not registration or not registration.strip():
            log.warning("record_filter_match: skipping flight %s with empty registration", flight_number)
            return None, False
        with self._connect() as conn:
            if arrival_date:
                existing = conn.execute(
                    "SELECT id, notif_types, photo_url FROM flight_arrivals "
                    "WHERE registration = ? AND flight_number = ? AND arrival_date = ?",
                    (registration.strip(), flight_number, arrival_date),
                ).fetchone()
            else:
                existing = conn.execute(
                    "SELECT id, notif_types, photo_url FROM flight_arrivals WHERE registration = ? AND flight_number = ?",
                    (registration.strip(), flight_number),
                ).fetchone()
            if existing:
                try:
                    current = json.loads(existing["notif_types"] or "[]")
                except Exception:
                    current = []
                merged = list(dict.fromkeys(current + [t for t in notif_types if t not in current]))
                set_cols, params = [], []
                if merged != current:
                    set_cols.append("notif_types = ?")
                    params.append(json.dumps(merged))
                # Backfill only â€” never overwrite an existing snapshot with a possibly
                # different/newer one; this column is a frozen-at-creation display value,
                # not a live cache (airframes.photo_url is the one that keeps refreshing).
                if photo_url and not existing["photo_url"]:
                    set_cols.append("photo_url = ?")
                    params.append(photo_url)
                if set_cols:
                    params.append(existing["id"])
                    conn.execute(f"UPDATE flight_arrivals SET {', '.join(set_cols)} WHERE id = ?", params)
                return existing["id"], False
            else:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO flight_arrivals
                       (registration, flight_number, arrival_ts, first_seen_ts,
                        notif_types, detail, extra_info, origin_iata, origin_name,
                        arrival_date, airline_icao, photo_url, aircraft_type, rare_absence_days)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (registration.strip(), flight_number, arrival_ts, first_seen_ts,
                     json.dumps(notif_types), detail, extra_info, origin_iata, origin_name,
                     arrival_date, airline_icao or None, photo_url or None,
                     aircraft_type or None, rare_absence_days),
                )
                return cursor.lastrowid, cursor.rowcount > 0

    def cleanup_arrived_flights(self, now_ts: int) -> None:
        """Prune complete days older than 30 days â€” never cuts through a partial day.
        Uses arrival_date (YYYY-MM-DD) so only fully-elapsed days are removed.
        Falls back to first_seen_ts for rows with no arrival_date set."""
        try:
            import system_status as _ss; _ss.record_task('feed_cleanup', True)
        except Exception:
            pass
        import datetime as _dt
        today = _dt.date.fromtimestamp(now_ts)
        cutoff_date = (today - _dt.timedelta(days=30)).isoformat()
        cutoff_ts   = now_ts - 30 * 86400
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM flight_arrivals WHERE "
                "(arrival_date IS NOT NULL AND arrival_date < ?) OR "
                "(arrival_date IS NULL AND first_seen_ts < ?)",
                (cutoff_date, cutoff_ts),
            )
            conn.execute(
                "DELETE FROM timeline_cache WHERE date < ?",
                (cutoff_date,),
            )

    # ------------------------------------------------------------------
    # Sighting history (every registration seen in arrivals feed)
    # ------------------------------------------------------------------

    def get_notification_stats(self) -> dict:
        """Return counts of notifications sent across all filter types."""
        with self._connect() as conn:
            return {
                "special_liveries": conn.execute(
                    "SELECT COUNT(*) FROM livery_cooldowns WHERE last_notified_ts > 0"
                ).fetchone()[0],
                "military": conn.execute(
                    "SELECT COUNT(*) FROM military_cooldowns WHERE last_notified_ts > 0"
                ).fetchone()[0],
                "rego_hits": conn.execute(
                    "SELECT COUNT(*) FROM filter_regos WHERE last_notified_ts > 0"
                ).fetchone()[0],
                "type_hits": conn.execute(
                    "SELECT COUNT(*) FROM filter_types WHERE last_notified_ts > 0"
                ).fetchone()[0],
                "airline_hits": conn.execute(
                    "SELECT COUNT(*) FROM filter_airlines WHERE last_notified_ts > 0"
                ).fetchone()[0],
            }

    # ------------------------------------------------------------------
    # Departure pattern learning
    # ------------------------------------------------------------------

    def record_departure_pattern(self, arrival_fn: str, departure_fn: str,
                                  airport_iata: str, now_ts: int,
                                  scheduled_dep_ts: Optional[int] = None,
                                  estimated_dep_ts: Optional[int] = None,
                                  scheduled_arr_ts: Optional[int] = None,
                                  airline_name: Optional[str] = None,
                                  airline_iata: Optional[str] = None,
                                  airline_icao: Optional[str] = None,
                                  dest_name: Optional[str] = None,
                                  dest_iata: Optional[str] = None,
                                  dest_icao: Optional[str] = None) -> None:
        """Increment the observation count for an arrivalâ†’departure flight number pairing.

        turnaround_secs is computed from scheduled times only (not actual) so that
        day-to-day variance in real departure times doesn't corrupt the offset.
        """
        turnaround_secs: Optional[int] = None
        if scheduled_dep_ts and scheduled_arr_ts:
            turnaround_secs = scheduled_dep_ts - scheduled_arr_ts

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO departure_patterns
                    (arrival_flight_number, departure_flight_number, airport_iata, count, last_seen_ts,
                     scheduled_dep_ts, estimated_dep_ts, scheduled_arr_ts, turnaround_secs,
                     airline_name, airline_iata, airline_icao, dest_name, dest_iata, dest_icao)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(arrival_flight_number, departure_flight_number, airport_iata)
                DO UPDATE SET
                    count             = count + 1,
                    last_seen_ts      = excluded.last_seen_ts,
                    scheduled_dep_ts  = COALESCE(excluded.scheduled_dep_ts, scheduled_dep_ts),
                    estimated_dep_ts  = COALESCE(excluded.estimated_dep_ts, estimated_dep_ts),
                    scheduled_arr_ts  = COALESCE(excluded.scheduled_arr_ts, scheduled_arr_ts),
                    turnaround_secs   = COALESCE(excluded.turnaround_secs, turnaround_secs),
                    airline_name      = COALESCE(excluded.airline_name, airline_name),
                    airline_iata      = COALESCE(excluded.airline_iata, airline_iata),
                    airline_icao      = COALESCE(excluded.airline_icao, airline_icao),
                    dest_name         = COALESCE(excluded.dest_name, dest_name),
                    dest_iata         = COALESCE(excluded.dest_iata, dest_iata),
                    dest_icao         = COALESCE(excluded.dest_icao, dest_icao)
                """,
                (arrival_fn.strip(), departure_fn.strip(), airport_iata.strip(), now_ts,
                 scheduled_dep_ts, estimated_dep_ts, scheduled_arr_ts, turnaround_secs,
                 airline_name, airline_iata, airline_icao, dest_name, dest_iata, dest_icao),
            )

    def update_departure_timestamps(self, arrival_fn: str, departure_fn: str,
                                     airport_iata: str,
                                     estimated_dep_ts: Optional[int],
                                     scheduled_dep_ts: Optional[int]) -> None:
        """Refresh departure timestamps for an existing pairing â€” no count increment."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE departure_patterns
                SET estimated_dep_ts = COALESCE(?, estimated_dep_ts),
                    scheduled_dep_ts = COALESCE(?, scheduled_dep_ts)
                WHERE arrival_flight_number = ? AND departure_flight_number = ? AND airport_iata = ?
                """,
                (estimated_dep_ts, scheduled_dep_ts,
                 arrival_fn.strip(), departure_fn.strip(), airport_iata.strip()),
            )

    def backfill_rare_plane_seen(self, airline: str, aircraft_type: str, seen_ts: int) -> None:
        """Raw upsert for historical backfill â€” updates last_seen_ts without notification logic."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO rare_plane_cooldowns(airline, aircraft_type, last_seen_ts, last_notified_ts)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(airline, aircraft_type) DO UPDATE
                SET last_seen_ts = MAX(last_seen_ts, excluded.last_seen_ts)
                """,
                (airline.strip(), aircraft_type.strip(), seen_ts),
            )

    def get_predicted_dep_info(self, dep_fn: str, airport_iata: str) -> Optional[dict]:
        """Return stored departure details for a predicted flight number, or None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT scheduled_dep_ts, estimated_dep_ts, actual_dep_ts, turnaround_secs,
                       airline_name, airline_iata, airline_icao,
                       dest_name, dest_iata, dest_icao
                FROM departure_patterns
                WHERE departure_flight_number = ? AND airport_iata = ?
                ORDER BY last_seen_ts DESC LIMIT 1
                """,
                (dep_fn.strip(), airport_iata.strip()),
            ).fetchone()
            if not row:
                return None
            return {
                "scheduled_dep_ts": row["scheduled_dep_ts"],
                "estimated_dep_ts": row["estimated_dep_ts"],
                "actual_dep_ts":    row["actual_dep_ts"],
                "turnaround_secs":  row["turnaround_secs"],
                "airline_name":     row["airline_name"],
                "airline_iata":     row["airline_iata"],
                "airline_icao":     row["airline_icao"],
                "dest_name":        row["dest_name"],
                "dest_iata":        row["dest_iata"],
                "dest_icao":        row["dest_icao"],
            }

    def record_actual_departure(self, dep_fn: str, airport_iata: str, actual_dep_ts: int) -> None:
        """Update actual_dep_ts for all departure_patterns rows matching this departure flight."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE departure_patterns
                SET actual_dep_ts = ?
                WHERE departure_flight_number = ? AND airport_iata = ?
                """,
                (actual_dep_ts, dep_fn.strip(), airport_iata.strip()),
            )

    def get_predicted_departure(self, arrival_fn: str, airport_iata: str,
                                 threshold_pct: int) -> Optional[tuple]:
        """Return (departure_fn, confidence_pct, obs_count, total_count) if the most
        common departure for this arrival meets the threshold, else None."""
        arrival_fn   = arrival_fn.strip()
        airport_iata = airport_iata.strip()
        with self._connect() as conn:
            total = conn.execute(
                "SELECT SUM(count) FROM departure_patterns "
                "WHERE arrival_flight_number = ? AND airport_iata = ?",
                (arrival_fn, airport_iata),
            ).fetchone()[0] or 0
            if total == 0:
                return None
            row = conn.execute(
                "SELECT departure_flight_number, count FROM departure_patterns "
                "WHERE arrival_flight_number = ? AND airport_iata = ? "
                "ORDER BY count DESC LIMIT 1",
                (arrival_fn, airport_iata),
            ).fetchone()
            if not row:
                return None
            confidence = row["count"] / total * 100
            if confidence >= threshold_pct:
                return row["departure_flight_number"], confidence, int(row["count"]), int(total)
        return None

    def bulk_update_sightings(self, sightings: dict) -> None:
        """Record actual arrival timestamps for planes that have landed.

        sightings: {registration: {"ts": int, "manufacturer"?, "airline"?,
        "aircraft_type"?, "airline_icao"?}} or {registration: int} (legacy, no
        enrichment). Maintains last_seen_ts (most recent) and prev_seen_ts
        (previous different-day visit). The 4 enrichment fields use
        COALESCE(new, old) — a call with no enrichment data (e.g. the legacy
        int form, or a confirmation-call retry) never blanks out
        already-known values, it just leaves them as they were.
        """
        import datetime as _dt
        with self._connect() as conn:
            for reg, val in sightings.items():
                reg = reg.strip()
                if not reg:
                    continue
                is_dict = isinstance(val, dict)
                new_ts = int(val.get("ts", 0)) if is_dict else int(val)
                if not new_ts:
                    continue
                mfr = val.get("manufacturer") if is_dict else None
                airline = val.get("airline") if is_dict else None
                ac_type = val.get("aircraft_type") if is_dict else None
                airline_icao = val.get("airline_icao") if is_dict else None
                existing = conn.execute(
                    "SELECT last_seen_ts FROM rego_sightings WHERE registration = ?",
                    (reg,)
                ).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO rego_sightings(registration, last_seen_ts, manufacturer, "
                        "airline, aircraft_type, airline_icao) VALUES (?, ?, ?, ?, ?, ?)",
                        (reg, new_ts, mfr, airline, ac_type, airline_icao)
                    )
                else:
                    conn.execute(
                        "UPDATE rego_sightings SET manufacturer=COALESCE(?, manufacturer), "
                        "airline=COALESCE(?, airline), aircraft_type=COALESCE(?, aircraft_type), "
                        "airline_icao=COALESCE(?, airline_icao) WHERE registration=?",
                        (mfr, airline, ac_type, airline_icao, reg)
                    )
                    if new_ts > existing["last_seen_ts"]:
                        old_ts = existing["last_seen_ts"]
                        old_date = _dt.datetime.fromtimestamp(old_ts).date()
                        new_date = _dt.datetime.fromtimestamp(new_ts).date()
                        if new_date != old_date:
                            conn.execute(
                                "UPDATE rego_sightings SET last_seen_ts=?, prev_seen_ts=? WHERE registration=?",
                                (new_ts, old_ts, reg)
                            )
                        else:
                            conn.execute(
                                "UPDATE rego_sightings SET last_seen_ts=? WHERE registration=?",
                                (new_ts, reg)
                            )

    def get_last_seen(self, registration: str) -> Optional[int]:
        """Return the Unix timestamp of the last time this registration appeared in arrivals, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_seen_ts FROM rego_sightings WHERE registration = ?",
                (registration.strip(),),
            ).fetchone()
            return int(row["last_seen_ts"]) if row else None

    def get_flight_route(self, flight_number: str, airport_iata: str) -> Optional[dict]:
        """Return route info for a flight number from departure pattern data.

        For an arrival flight (e.g. DL41): origin=dest_iata, destination=airport_iata
        For a departure flight (e.g. DL40): origin=airport_iata, destination=dest_iata
        Returns dict with origin_iata, origin_name, dest_iata, dest_name, or None.
        """
        fn = flight_number.strip()
        iata = airport_iata.strip()
        with self._connect() as conn:
            # Check as arrival flight first
            row = conn.execute(
                "SELECT dest_iata, dest_name FROM departure_patterns "
                "WHERE arrival_flight_number = ? AND airport_iata = ? "
                "ORDER BY last_seen_ts DESC LIMIT 1",
                (fn, iata),
            ).fetchone()
            if row and row["dest_iata"]:
                return {
                    "origin_iata": row["dest_iata"],
                    "origin_name": row["dest_name"] or row["dest_iata"],
                    "dest_iata":   iata,
                    "dest_name":   iata,
                }
            # Check as departure flight
            row = conn.execute(
                "SELECT dest_iata, dest_name FROM departure_patterns "
                "WHERE departure_flight_number = ? AND airport_iata = ? "
                "ORDER BY last_seen_ts DESC LIMIT 1",
                (fn, iata),
            ).fetchone()
            if row and row["dest_iata"]:
                return {
                    "origin_iata": iata,
                    "origin_name": iata,
                    "dest_iata":   row["dest_iata"],
                    "dest_name":   row["dest_name"] or row["dest_iata"],
                }
        return None

    # ------------------------------------------------------------------
    # Route type history — feeds the Search tab's "Route Equipment" lookup
    # (/api/search/route-filters, /api/search/route in web.py). The
    # equipment-swap ALERT filter that used to also read this table was
    # removed; this table/method now exists purely for that Search feature.
    # ------------------------------------------------------------------

    def bulk_update_route_types(self, records: list) -> None:
        """Upsert (flight_number, aircraft_type, airport_iata, ts[, origin_iata, dest_iata, airline]).

        Increments count and updates last_seen_ts; preserves first_seen_ts on conflict.
        origin_iata/dest_iata/airline are filled in when provided, never cleared once set.
        """
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO route_type_tracker
                    (flight_number, aircraft_type, airport_iata, count, first_seen_ts, last_seen_ts,
                     origin_iata, dest_iata, airline)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(flight_number, aircraft_type, airport_iata) DO UPDATE SET
                    count        = CASE
                                       WHEN excluded.last_seen_ts > route_type_tracker.last_seen_ts + 14400
                                       THEN count + 1
                                       ELSE count
                                   END,
                    last_seen_ts = MAX(last_seen_ts, excluded.last_seen_ts),
                    origin_iata  = COALESCE(route_type_tracker.origin_iata, excluded.origin_iata),
                    dest_iata    = COALESCE(route_type_tracker.dest_iata,   excluded.dest_iata),
                    airline      = COALESCE(route_type_tracker.airline,     excluded.airline)
                """,
                [
                    (fn, at, iata, ts, ts,
                     r[4] if len(r) > 4 else None,
                     r[5] if len(r) > 5 else None,
                     r[6] if len(r) > 6 else None)
                    for r in records
                    for fn, at, iata, ts in [r[:4]]
                ],
            )

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    def backup(self, keep: int = 7) -> str:
        """Copy the live DB to a timestamped file in a backups/ subfolder.

        Uses SQLite's native backup API so it's safe while the DB is open.
        Prunes the oldest files once more than `keep` backups exist.
        Returns the path of the newly created backup file.
        """
        import datetime
        backup_dir = os.path.join(os.path.dirname(self.db_path), "backups")
        os.makedirs(backup_dir, exist_ok=True)

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = os.path.splitext(os.path.basename(self.db_path))[0]
        dest_path = os.path.join(backup_dir, f"{stem}_{ts}.db")

        src = sqlite3.connect(self.db_path)
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        # Prune oldest backups beyond keep count
        existing = sorted(
            f for f in os.listdir(backup_dir)
            if f.startswith(stem) and f.endswith(".db")
        )
        for old in existing[:-keep]:
            try:
                os.remove(os.path.join(backup_dir, old))
            except OSError:
                pass

        return dest_path

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def upsert_user(self, chat_id: str, is_admin: bool, language: str = "en") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users(chat_id, is_admin, language) VALUES (?,?,?) "
                "ON CONFLICT(chat_id) DO UPDATE SET is_admin=excluded.is_admin",
                (str(chat_id), 1 if is_admin else 0, language),
            )

    def get_user(self, chat_id: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT chat_id, is_admin, language FROM users WHERE chat_id = ?",
                (str(chat_id),),
            ).fetchone()
            if not row:
                return None
            return {"chat_id": row["chat_id"], "is_admin": bool(row["is_admin"]), "language": row["language"]}

    def get_all_users(self) -> List[Dict]:
        rows = self._fetch("SELECT chat_id, is_admin, language FROM users")
        return [{"chat_id": r["chat_id"], "is_admin": bool(r["is_admin"]), "language": r["language"]} for r in rows]

    def delete_user(self, chat_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM users WHERE chat_id = ? AND is_admin = 0", (str(chat_id),))
            return cur.rowcount > 0

    def set_user_language(self, chat_id: str, language: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET language = ? WHERE chat_id = ?",
                (language, str(chat_id)),
            )

    def is_admin(self, chat_id: str) -> bool:
        user = self.get_user(str(chat_id))
        return bool(user and user["is_admin"])

    def is_known_user(self, chat_id: str) -> bool:
        return self.get_user(str(chat_id)) is not None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return list(conn.execute(sql, params).fetchall())

    # ------------------------------------------------------------------
    # Airframe database (OpenSky Network)
    # ------------------------------------------------------------------

    def get_airframe(self, registration: str, icao24: str = None) -> Optional[dict]:
        """Return airframe row by registration, or by icao24 if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM airframes WHERE registration = ?",
                (registration.upper(),),
            ).fetchone()
            if row is None and icao24:
                row = conn.execute(
                    "SELECT * FROM airframes WHERE icao24 = ?",
                    (icao24.lower(),),
                ).fetchone()
        return dict(row) if row else None

    def upsert_airframe_from_fr24(
        self,
        registration: str,
        icao24: str = None,
        photo_url: str = None,
        manufacturer: str = None,
        serial_number: str = None,
        built_year: int = None,
        owner: str = None,
        operator: str = None,
        operator_icao: str = None,
        operator_iata: str = None,
    ) -> None:
        """Insert or update airframes with FR24-sourced data.

        Uses COALESCE so existing non-null values are never overwritten by nulls,
        except photo_url which is always refreshed when a new one is provided.
        """
        import time as _time
        now_ts = int(_time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO airframes
                    (registration, icao24, manufacturer, serial_number, built_year,
                     owner, operator, operator_icao, operator_iata, photo_url, fetched_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(registration) DO UPDATE SET
                    icao24        = COALESCE(airframes.icao24,        excluded.icao24),
                    manufacturer  = COALESCE(airframes.manufacturer,  excluded.manufacturer),
                    serial_number = COALESCE(airframes.serial_number, excluded.serial_number),
                    built_year    = COALESCE(airframes.built_year,    excluded.built_year),
                    owner         = COALESCE(airframes.owner,         excluded.owner),
                    operator      = COALESCE(airframes.operator,      excluded.operator),
                    operator_icao = COALESCE(airframes.operator_icao, excluded.operator_icao),
                    operator_iata = COALESCE(airframes.operator_iata, excluded.operator_iata),
                    photo_url     = COALESCE(excluded.photo_url, airframes.photo_url),
                    fetched_ts    = excluded.fetched_ts
                """,
                (registration.upper(), icao24, manufacturer, serial_number, built_year,
                 owner, operator, operator_icao, operator_iata, photo_url, now_ts),
            )



def _parse_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def _list_meta(list_name: str) -> Tuple[str, str]:
    if list_name == "Exclusion List":
        return "filter_exclusions", "SELECT id FROM filter_exclusions ORDER BY id ASC"
    if list_name == "Rego Watchlist":
        return "filter_regos", "SELECT id FROM filter_regos ORDER BY id ASC"
    if list_name == "Type Watchlist":
        return "filter_types", "SELECT id FROM filter_types ORDER BY id ASC"
    if list_name == "Airline/Operator Watchlist":
        return "filter_airlines", "SELECT id FROM filter_airlines ORDER BY id ASC"
    raise ValueError(f"Unknown list: {list_name!r}")
