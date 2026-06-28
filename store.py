from __future__ import annotations

import csv
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TableView:
    columns: List[str]
    rows: List[Dict[str, Any]]


class SqliteStore:
    def __init__(self, db_path: str, config_file: str = ""):
        self.db_path = db_path
        self._config_file = config_file
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")

            # ── Table renames migration ───────────────────────────────────────
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
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_excl_reg ON filter_exclusions(registration)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS filter_regos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    airline TEXT,
                    registration TEXT NOT NULL,
                    description TEXT,
                    last_notified_ts INTEGER
                )
            """)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_rego_reg ON filter_regos(registration)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS filter_types (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    airline TEXT NOT NULL,
                    aircraft_type TEXT NOT NULL,
                    last_notified_ts INTEGER
                )
            """)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_type_uniq ON filter_types(airline, aircraft_type)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS filter_airlines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    icao_code TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    name TEXT,
                    last_notified_ts INTEGER DEFAULT 0
                )
            """)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_airline_uniq ON filter_airlines(icao_code, entry_type)"
            )
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

            # Persists settings changed via the Telegram bot so they survive restarts.
            # On startup, these values take precedence over config.env.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
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

            conn.execute("""
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint     TEXT NOT NULL UNIQUE,
                    p256dh       TEXT NOT NULL,
                    auth         TEXT NOT NULL,
                    user_agent   TEXT DEFAULT '',
                    created_ts   INTEGER NOT NULL
                )
            """)

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
                    source       TEXT DEFAULT 'airportsdata'
                )
            """)
            # Seed from airportsdata if table is empty
            if conn.execute("SELECT COUNT(*) FROM airports").fetchone()[0] == 0:
                try:
                    import airportsdata as _ad
                    for _iata, _a in _ad.load('IATA').items():
                        conn.execute(
                            "INSERT OR IGNORE INTO airports(iata, name, country_code, source) VALUES(?,?,?,?)",
                            (_iata, _a.get('name', _iata), _a.get('country', ''), 'airportsdata'),
                        )
                except Exception:
                    pass

            conn.execute("""
                CREATE TABLE IF NOT EXISTS aircraft_types (
                    icao   TEXT PRIMARY KEY,
                    name   TEXT NOT NULL,
                    source TEXT DEFAULT 'icaolist'
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS timeline_cache (
                    date          TEXT PRIMARY KEY,
                    clusters_json TEXT NOT NULL DEFAULT '[]',
                    weather_json  TEXT,
                    computed_at   INTEGER NOT NULL
                )
            """)

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
        log.info("Refreshing ICAO aircraft type list from GitHub…")
        try:
            with _req.urlopen(self._ICAOLIST_URL, timeout=30) as resp:
                text = resp.read().decode("utf-8-sig")
        except Exception as exc:
            log.warning("Failed to download ICAOList.csv: %s", exc)
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
                    name = mfr_model.split(",", 1)[1].strip()
                else:
                    name = mfr_model.strip()
                existing = conn.execute(
                    "SELECT source FROM aircraft_types WHERE icao=?", (icao,)
                ).fetchone()
                if existing:
                    if existing[0] == 'user':
                        continue  # never overwrite user entries
                    conn.execute(
                        "UPDATE aircraft_types SET name=?, source='icaolist' WHERE icao=?",
                        (name, icao)
                    )
                    updated += 1
                else:
                    conn.execute(
                        "INSERT INTO aircraft_types(icao, name, source) VALUES(?,?,'icaolist')",
                        (icao, name)
                    )
                    inserted += 1

        import time as _time2
        self.save_setting('icao_list_last_update', str(_time2.time()))
        log.info("ICAO type list refreshed — inserted: %d  updated: %d", inserted, updated)

    def upsert_aircraft_type(self, icao: str, name: str, source: str = 'user') -> None:
        if not icao or not name:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO aircraft_types(icao, name, source) VALUES(?,?,?) "
                "ON CONFLICT(icao) DO UPDATE SET name=excluded.name, source=excluded.source "
                "WHERE excluded.source='user' OR aircraft_types.source != 'user'",
                (icao.upper(), name, source),
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

    def upsert_airport(self, iata: str, name: str, country_code: str, source: str = 'fr24') -> None:
        """Insert or update airport info. FR24 data always wins over airportsdata."""
        if not iata or not name:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO airports(iata, name, country_code, source) VALUES(?,?,?,?) "
                "ON CONFLICT(iata) DO UPDATE SET name=excluded.name, country_code=excluded.country_code, "
                "source=excluded.source WHERE excluded.source='fr24' OR airports.source='airportsdata'",
                (iata.upper(), name, country_code or '', source),
            )

    def get_airport_info(self, iata: str):
        """Return (name, country_code) or None if not cached."""
        if not iata:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, country_code FROM airports WHERE iata=?", (iata.upper(),)
            ).fetchone()
            return (row['name'], row['country_code']) if row else None

    def upsert_timeline_cache(self, date: str, clusters_json: str,
                              weather_json: Optional[str] = None) -> None:
        """Store pre-computed cluster (and optionally weather) JSON for a calendar date."""
        import time as _time
        with self._connect() as conn:
            if weather_json is not None:
                conn.execute(
                    "INSERT INTO timeline_cache(date, clusters_json, weather_json, computed_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(date) DO UPDATE SET "
                    "clusters_json=excluded.clusters_json, weather_json=excluded.weather_json, "
                    "computed_at=excluded.computed_at",
                    (date, clusters_json, weather_json, int(_time.time())),
                )
            else:
                conn.execute(
                    "INSERT INTO timeline_cache(date, clusters_json, computed_at) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(date) DO UPDATE SET "
                    "clusters_json=excluded.clusters_json, computed_at=excluded.computed_at",
                    (date, clusters_json, int(_time.time())),
                )

    def get_timeline_cache(self, dates: List[str]) -> Dict[str, dict]:
        """Return {date: {clusters_json, weather_json, computed_at}} for the given dates."""
        if not dates:
            return {}
        placeholders = ",".join("?" * len(dates))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT date, clusters_json, weather_json, computed_at "
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

    # ── Fleet cards ──────────────────────────────────────────────────────────
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
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                (key, str(value)),
            )

    def load_setting(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

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

    def add_exclusion(self, airline: str, registration: str, description: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO filter_exclusions(airline, registration, description) VALUES (?,?,?)",
                (airline.strip(), registration.strip(), description.strip()),
            )

    def add_rego_watch(self, airline: str, registration: str, description: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO filter_regos(airline, registration, description, last_notified_ts) VALUES (?,?,?,0)",
                (airline.strip(), registration.strip(), description.strip()),
            )

    def add_type_watch(self, airline: str, aircraft_type: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO filter_types(airline, aircraft_type, last_notified_ts) VALUES (?,?,0)",
                (airline.strip(), aircraft_type.strip()),
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
                # First time seen — insert sentinel so we don't lose track of it
                conn.execute(
                    "INSERT INTO livery_cooldowns(registration, last_notified_ts) VALUES (?,0)",
                    (registration,),
                )
                return True
            last_ts = int(row["last_notified_ts"])
            # ts=0 means a previous send attempt failed — always retry
            if last_ts == 0:
                return True
            return (now_ts - last_ts) / 3600 > min_hours

    def mark_special_livery_notified(self, registration: str, now_ts: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE livery_cooldowns SET last_notified_ts = ? WHERE registration = ?",
                (now_ts, registration.strip()),
            )

    def update_rare_plane_seen(self, airline: str, aircraft_type: str, now_ts: int, min_absence_days: int) -> bool:
        """Record a sighting and return True if the combo was absent long enough to be considered rare.

        Updates last_seen_ts on every call so regular arrivals never trigger a rare notification.
        Only returns True when the gap since the last sighting exceeds min_absence_days.
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
                return True
            last_seen = int(row["last_seen_ts"])
            is_rare = last_seen == 0 or (now_ts - last_seen) / 86400 > min_absence_days
            conn.execute(
                "UPDATE rare_plane_cooldowns SET last_seen_ts = ? WHERE airline = ? AND aircraft_type = ?",
                (now_ts, airline, aircraft_type),
            )
            return is_rare

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

    def add_airline_watch(self, icao_code: str, entry_type: str, name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO filter_airlines(icao_code, entry_type, name, last_notified_ts)"
                " VALUES (?,?,?,0)",
                (icao_code.strip().upper(), entry_type.strip(), name.strip()),
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

    # ------------------------------------------------------------------
    # Follow-up tracking (reminder + cancellation/diversion)
    # ------------------------------------------------------------------

    def update_flight_event_status(self, registration: str, flight_number: str,
                                    current_status: str, arrival_ts: int,
                                    arrival_date: str = None,
                                    arr_label: str = None) -> None:
        """Refresh live status and latest arrival estimate for a flight in flight_arrivals.
        arr_label ('Arrived'|'Estimated'|'Scheduled') is only written when provided,
        never cleared to NULL.
        """
        with self._connect() as conn:
            if arr_label:
                if arrival_date:
                    conn.execute(
                        "UPDATE flight_arrivals SET current_status = ?, arrival_ts = ?, arr_label = ? "
                        "WHERE registration = ? AND flight_number = ? AND arrival_date = ?",
                        (current_status, arrival_ts, arr_label,
                         registration.strip(), flight_number, arrival_date),
                    )
                else:
                    conn.execute(
                        "UPDATE flight_arrivals SET current_status = ?, arrival_ts = ?, arr_label = ? "
                        "WHERE registration = ? AND flight_number = ?",
                        (current_status, arrival_ts, arr_label,
                         registration.strip(), flight_number),
                    )
            else:
                if arrival_date:
                    conn.execute(
                        "UPDATE flight_arrivals SET current_status = ?, arrival_ts = ? "
                        "WHERE registration = ? AND flight_number = ? AND arrival_date = ?",
                        (current_status, arrival_ts, registration.strip(), flight_number, arrival_date),
                    )
                else:
                    conn.execute(
                        "UPDATE flight_arrivals SET current_status = ?, arrival_ts = ? "
                        "WHERE registration = ? AND flight_number = ?",
                        (current_status, arrival_ts, registration.strip(), flight_number),
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
        dep_label: 'Estimated' | 'Scheduled' | 'Predicted' — shown in the UI.
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
    ) -> None:
        """Store a filter-matched flight in flight_arrivals. Merges notif_types if already present."""
        if not registration or not registration.strip():
            log.warning("record_filter_match: skipping flight %s with empty registration", flight_number)
            return
        with self._connect() as conn:
            if arrival_date:
                existing = conn.execute(
                    "SELECT id, notif_types FROM flight_arrivals "
                    "WHERE registration = ? AND flight_number = ? AND arrival_date = ?",
                    (registration.strip(), flight_number, arrival_date),
                ).fetchone()
            else:
                existing = conn.execute(
                    "SELECT id, notif_types FROM flight_arrivals WHERE registration = ? AND flight_number = ?",
                    (registration.strip(), flight_number),
                ).fetchone()
            if existing:
                try:
                    current = json.loads(existing["notif_types"] or "[]")
                except Exception:
                    current = []
                merged = list(dict.fromkeys(current + [t for t in notif_types if t not in current]))
                if merged != current:
                    conn.execute(
                        "UPDATE flight_arrivals SET notif_types = ? WHERE id = ?",
                        (json.dumps(merged), existing["id"]),
                    )
            else:
                conn.execute(
                    """INSERT OR IGNORE INTO flight_arrivals
                       (registration, flight_number, arrival_ts, first_seen_ts,
                        notif_types, detail, extra_info, origin_iata, origin_name,
                        arrival_date, airline_icao)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (registration.strip(), flight_number, arrival_ts, first_seen_ts,
                     json.dumps(notif_types), detail, extra_info, origin_iata, origin_name,
                     arrival_date, airline_icao or None),
                )

    def cleanup_arrived_flights(self, now_ts: int) -> None:
        """Prune flight_arrivals rows older than 30 days."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM flight_arrivals WHERE first_seen_ts < ?",
                (now_ts - 30 * 86400,),
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
    # Web Push subscriptions
    # ------------------------------------------------------------------

    def add_push_subscription(self, endpoint: str, p256dh: str, auth: str,
                               user_agent: str, ts: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO push_subscriptions
                   (endpoint, p256dh, auth, user_agent, created_ts)
                   VALUES (?, ?, ?, ?, ?)""",
                (endpoint, p256dh, auth, user_agent, ts),
            )

    def remove_push_subscription(self, endpoint: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))

    def get_push_subscriptions(self) -> List[sqlite3.Row]:
        return self._fetch("SELECT * FROM push_subscriptions")

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
        """Increment the observation count for an arrival→departure flight number pairing.

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
        """Refresh departure timestamps for an existing pairing — no count increment."""
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
        """Raw upsert for historical backfill — updates last_seen_ts without notification logic."""
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

        sightings: {registration: {"ts": int}} or {registration: int} (legacy).
        Maintains last_seen_ts (most recent) and prev_seen_ts (previous different-day visit).
        """
        import datetime as _dt
        with self._connect() as conn:
            for reg, val in sightings.items():
                reg = reg.strip()
                if not reg:
                    continue
                new_ts = int(val) if isinstance(val, (int, float)) else int(val.get("ts", 0))
                if not new_ts:
                    continue
                existing = conn.execute(
                    "SELECT last_seen_ts FROM rego_sightings WHERE registration = ?",
                    (reg,)
                ).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO rego_sightings(registration, last_seen_ts) VALUES (?, ?)",
                        (reg, new_ts)
                    )
                elif new_ts > existing["last_seen_ts"]:
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
    # Route type history
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

    def get_established_route_type(
        self,
        flight_number: str,
        airport_iata: str,
        lookback_days: int,
        min_days: int,
        dominance_x: int,
    ) -> Optional[tuple]:
        """Return (established_type, count, first_seen_ts) if one type clearly dominates.

        Returns None if there is insufficient history or no single dominant type.
        Dominant type must have count >= dominance_x * count of the next most common type,
        and must have first been seen at least min_days ago.
        """
        import time as _time
        now_ts = int(_time.time())
        cutoff_ts = now_ts - lookback_days * 86400
        min_age_ts = now_ts - min_days * 86400

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT aircraft_type, count, first_seen_ts
                FROM route_type_tracker
                WHERE flight_number = ? AND airport_iata = ? AND last_seen_ts >= ?
                ORDER BY count DESC
                """,
                (flight_number.strip(), airport_iata.strip(), cutoff_ts),
            ).fetchall()

        if not rows:
            return None

        dominant = rows[0]
        dom_type       = dominant["aircraft_type"]
        dom_count      = dominant["count"]
        dom_first_seen = dominant["first_seen_ts"]

        # Must have enough history (established for at least min_days)
        if dom_first_seen > min_age_ts:
            return None

        # Must clearly dominate — count >= dominance_x × next type's count
        if len(rows) > 1:
            second_count = rows[1]["count"]
            if dom_count < dominance_x * second_count:
                return None

        return dom_type, dom_count, dom_first_seen

    def should_notify_route_type_change(
        self,
        flight_number: str,
        aircraft_type: str,
        airport_iata: str,
        renotify_days: int,
    ) -> bool:
        """Return True if this (flight, type) pairing hasn't been notified within the cooldown."""
        import time as _time
        cutoff_ts = int(_time.time()) - renotify_days * 86400
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT last_notified_ts FROM route_type_tracker
                WHERE flight_number = ? AND aircraft_type = ? AND airport_iata = ?
                """,
                (flight_number.strip(), aircraft_type.strip(), airport_iata.strip()),
            ).fetchone()
        if not row:
            return True
        last_ts = row["last_notified_ts"]
        return last_ts is None or last_ts < cutoff_ts

    def mark_route_type_notified(
        self,
        flight_number: str,
        aircraft_type: str,
        airport_iata: str,
        now_ts: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE route_type_tracker SET last_notified_ts = ?
                WHERE flight_number = ? AND aircraft_type = ? AND airport_iata = ?
                """,
                (now_ts, flight_number.strip(), aircraft_type.strip(), airport_iata.strip()),
            )

    def get_route_type_history(
        self,
        flight_number: str,
        airport_iata: str,
        lookback_days: int,
    ) -> list:
        """Return all type records for a flight number sorted by count desc."""
        import time as _time
        cutoff_ts = int(_time.time()) - lookback_days * 86400
        with self._connect() as conn:
            return list(conn.execute(
                """
                SELECT aircraft_type, count, first_seen_ts, last_seen_ts
                FROM route_type_tracker
                WHERE flight_number = ? AND airport_iata = ? AND last_seen_ts >= ?
                ORDER BY count DESC
                """,
                (flight_number.strip(), airport_iata.strip(), cutoff_ts),
            ).fetchall())

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


def _update_env_file(config_file: str, key: str, value: str) -> None:
    """Update a KEY = value line in the env file, or append if the key isn't found."""
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        updated = False
        for i, line in enumerate(lines):
            if re.match(rf"^\s*{re.escape(key)}\s*=", line):
                lines[i] = f"{key} = {value}\n"
                updated = True
                break
        if not updated:
            lines.append(f"{key} = {value}\n")
        with open(config_file, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError:
        pass


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
