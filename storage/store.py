from __future__ import annotations

import csv
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exclusion_list (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    airline TEXT,
                    registration TEXT NOT NULL,
                    description TEXT
                )
            """)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_excl_reg ON exclusion_list(registration)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rego_watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    airline TEXT,
                    registration TEXT NOT NULL,
                    description TEXT,
                    last_notified_ts INTEGER
                )
            """)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_rego_reg ON rego_watchlist(registration)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS type_watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    airline TEXT NOT NULL,
                    aircraft_type TEXT NOT NULL,
                    last_notified_ts INTEGER
                )
            """)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_type_uniq ON type_watchlist(airline, aircraft_type)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS airline_watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    icao_code TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    name TEXT,
                    last_notified_ts INTEGER DEFAULT 0
                )
            """)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_airline_uniq ON airline_watchlist(icao_code, entry_type)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS special_livery_history (
                    registration TEXT PRIMARY KEY,
                    last_notified_ts INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rare_plane_history (
                    airline TEXT NOT NULL,
                    aircraft_type TEXT NOT NULL,
                    last_seen_ts INTEGER NOT NULL DEFAULT 0,
                    last_notified_ts INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (airline, aircraft_type)
                )
            """)
            # Add last_seen_ts to existing tables, seeding from last_notified_ts
            rph_cols = {row[1] for row in conn.execute("PRAGMA table_info(rare_plane_history)").fetchall()}
            if "last_seen_ts" not in rph_cols:
                conn.execute("ALTER TABLE rare_plane_history ADD COLUMN last_seen_ts INTEGER NOT NULL DEFAULT 0")
                conn.execute("UPDATE rare_plane_history SET last_seen_ts = last_notified_ts WHERE last_notified_ts > 0")
            # Migrate old notification_record schema (had flight_status column) to new one
            existing = {row[1] for row in conn.execute("PRAGMA table_info(notification_record)").fetchall()}
            if existing and "flight_status" in existing:
                conn.execute("DROP TABLE notification_record")
            elif existing and "extra_info" not in existing:
                conn.execute("ALTER TABLE notification_record ADD COLUMN extra_info TEXT DEFAULT ''")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS notification_record (
                    registration      TEXT PRIMARY KEY,
                    flight_number     TEXT,
                    notif_type        TEXT,
                    original_arr_ts   INTEGER NOT NULL,
                    arrival_ts        INTEGER NOT NULL,
                    first_notified_ts INTEGER NOT NULL,
                    reminder_sent     INTEGER NOT NULL DEFAULT 0,
                    last_seen_ts      INTEGER NOT NULL,
                    extra_info        TEXT DEFAULT '',
                    detail            TEXT DEFAULT ''
                )
            """)
            nr_cols = {row[1] for row in conn.execute("PRAGMA table_info(notification_record)").fetchall()}
            if "detail" not in nr_cols:
                conn.execute("ALTER TABLE notification_record ADD COLUMN detail TEXT DEFAULT ''")
            if "cluster_notified_ts" not in nr_cols:
                conn.execute("ALTER TABLE notification_record ADD COLUMN cluster_notified_ts INTEGER DEFAULT NULL")
            if "approach_notified" not in nr_cols:
                conn.execute("ALTER TABLE notification_record ADD COLUMN approach_notified INTEGER NOT NULL DEFAULT 0")
            if "dep_notified" not in nr_cols:
                conn.execute("ALTER TABLE notification_record ADD COLUMN dep_notified INTEGER NOT NULL DEFAULT 0")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_flights (
                    registration        TEXT NOT NULL,
                    flight_number       TEXT NOT NULL,
                    notif_type          TEXT,
                    arrival_ts          INTEGER NOT NULL,
                    detail              TEXT DEFAULT '',
                    extra_info          TEXT DEFAULT '',
                    first_seen_ts       INTEGER NOT NULL,
                    last_seen_ts        INTEGER NOT NULL,
                    cluster_notified_ts INTEGER DEFAULT NULL,
                    PRIMARY KEY (registration, flight_number)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS notification_log (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    registration  TEXT NOT NULL,
                    flight_number TEXT,
                    notif_type    TEXT NOT NULL,
                    detail        TEXT DEFAULT '',
                    extra_info    TEXT DEFAULT '',
                    arrival_ts    INTEGER,
                    notified_ts   INTEGER NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS military_history (
                    registration TEXT PRIMARY KEY,
                    last_notified_ts INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sighting_history (
                    registration TEXT PRIMARY KEY,
                    last_seen_ts INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS flight_departure_pattern (
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
            fdp_cols = {row[1] for row in conn.execute("PRAGMA table_info(flight_departure_pattern)").fetchall()}
            for col, typ in [
                ("scheduled_dep_ts",  "INTEGER"), ("estimated_dep_ts", "INTEGER"),
                ("airline_name",      "TEXT"),    ("airline_iata",     "TEXT"),
                ("airline_icao",      "TEXT"),    ("dest_name",        "TEXT"),
                ("dest_iata",         "TEXT"),    ("dest_icao",        "TEXT"),
                ("scheduled_arr_ts",  "INTEGER"), ("turnaround_secs",  "INTEGER"),
                ("actual_dep_ts",     "INTEGER"),
            ]:
                if col not in fdp_cols:
                    conn.execute(f"ALTER TABLE flight_departure_pattern ADD COLUMN {col} {typ} DEFAULT NULL")

            # Persists settings changed via the Telegram bot so they survive restarts.
            # On startup, these values take precedence over config.env.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
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
                CREATE TABLE IF NOT EXISTS route_type_history (
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
                CREATE TABLE IF NOT EXISTS airframe_db (
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
                "CREATE INDEX IF NOT EXISTS idx_airframe_icao24 ON airframe_db(icao24)"
            )
            af_cols = {row[1] for row in conn.execute("PRAGMA table_info(airframe_db)").fetchall()}
            if "photo_url" not in af_cols:
                conn.execute("ALTER TABLE airframe_db ADD COLUMN photo_url TEXT DEFAULT NULL")

    # ------------------------------------------------------------------
    # App settings (bot-managed, persisted across restarts)
    # ------------------------------------------------------------------

    def save_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_settings(key, value) VALUES (?, ?)",
                (key, str(value)),
            )
        if self._config_file:
            _update_env_file(self._config_file, key, value)

    def load_setting(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    # ------------------------------------------------------------------
    # CSV migration (one-time import from legacy)
    # ------------------------------------------------------------------

    def migrate_from_csv_folder(self, csv_folder: str) -> None:
        # notification_record is not migrated — its schema changed and old CSV data is stale
        csv_map = {
            "exclusion_list.csv":        ("exclusion_list",        ["Airline", "Registration", "Description"]),
            "rego_watchlist.csv":        ("rego_watchlist",        ["Airline", "Registration", "Description", "Time"]),
            "type_watchlist.csv":        ("type_watchlist",        ["Airline", "Aircraft Type", "Time"]),
            "special_livery_history.csv":("special_livery_history",["Registration", "Time"]),
            "rare_plane_history.csv":    ("rare_plane_history",    ["Airline", "Aircraft Type", "Time"]),
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

            if table == "exclusion_list":
                for r in reader:
                    rego = (r.get("Registration") or "").strip()
                    if rego:
                        conn.execute(
                            "INSERT OR IGNORE INTO exclusion_list(airline, registration, description) VALUES (?,?,?)",
                            ((r.get("Airline") or "").strip(), rego, (r.get("Description") or "").strip()),
                        )
            elif table == "rego_watchlist":
                for r in reader:
                    rego = (r.get("Registration") or "").strip()
                    if rego:
                        conn.execute(
                            "INSERT OR IGNORE INTO rego_watchlist(airline, registration, description, last_notified_ts) VALUES (?,?,?,?)",
                            ((r.get("Airline") or "").strip(), rego, (r.get("Description") or "").strip(), _parse_int(r.get("Time"))),
                        )
            elif table == "type_watchlist":
                for r in reader:
                    airline = (r.get("Airline") or "").strip()
                    ac_type = (r.get("Aircraft Type") or "").strip()
                    if airline and ac_type:
                        conn.execute(
                            "INSERT OR IGNORE INTO type_watchlist(airline, aircraft_type, last_notified_ts) VALUES (?,?,?)",
                            (airline, ac_type, _parse_int(r.get("Time"))),
                        )
            elif table == "special_livery_history":
                for r in reader:
                    rego = (r.get("Registration") or "").strip()
                    ts = _parse_int(r.get("Time"))
                    if rego and ts is not None:
                        conn.execute(
                            "INSERT OR REPLACE INTO special_livery_history(registration, last_notified_ts) VALUES (?,?)",
                            (rego, ts),
                        )
            elif table == "rare_plane_history":
                for r in reader:
                    airline = (r.get("Airline") or "").strip()
                    ac_type = (r.get("Aircraft Type") or "").strip()
                    ts = _parse_int(r.get("Time"))
                    if airline and ac_type and ts is not None:
                        conn.execute(
                            "INSERT OR REPLACE INTO rare_plane_history(airline, aircraft_type, last_notified_ts) VALUES (?,?,?)",
                            (airline, ac_type, ts),
                        )

    # ------------------------------------------------------------------
    # List views for Telegram UI
    # ------------------------------------------------------------------

    def get_list_view(self, list_name: str) -> TableView:
        if list_name == "Exclusion List":
            cols = ["Registration", "Description"]
            rows = self._fetch(
                "SELECT registration, description FROM exclusion_list ORDER BY id ASC"
            )
            return TableView(columns=cols, rows=[
                {"Registration": r["registration"], "Description": r["description"]}
                for r in rows
            ])
        if list_name == "Rego Watchlist":
            cols = ["Registration", "Description"]
            rows = self._fetch(
                "SELECT registration, description FROM rego_watchlist ORDER BY id ASC"
            )
            return TableView(columns=cols, rows=[
                {"Registration": r["registration"], "Description": r["description"]}
                for r in rows
            ])
        if list_name == "Type Watchlist":
            cols = ["Airline", "Aircraft Type"]
            rows = self._fetch(
                "SELECT airline, aircraft_type FROM type_watchlist ORDER BY id ASC"
            )
            return TableView(columns=cols, rows=[
                {"Airline": r["airline"], "Aircraft Type": r["aircraft_type"]}
                for r in rows
            ])
        if list_name == "Airline/Operator Watchlist":
            cols = ["ICAO Code", "Type", "Name"]
            rows = self._fetch(
                "SELECT icao_code, entry_type, name FROM airline_watchlist ORDER BY id ASC"
            )
            return TableView(columns=cols, rows=[
                {"ICAO Code": r["icao_code"], "Type": r["entry_type"].capitalize(), "Name": r["name"] or ""}
                for r in rows
            ])
        raise ValueError(f"Unknown list: {list_name!r}")

    def add_exclusion(self, airline: str, registration: str, description: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO exclusion_list(airline, registration, description) VALUES (?,?,?)",
                (airline.strip(), registration.strip(), description.strip()),
            )

    def add_rego_watch(self, airline: str, registration: str, description: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO rego_watchlist(airline, registration, description, last_notified_ts) VALUES (?,?,?,0)",
                (airline.strip(), registration.strip(), description.strip()),
            )

    def add_type_watch(self, airline: str, aircraft_type: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO type_watchlist(airline, aircraft_type, last_notified_ts) VALUES (?,?,0)",
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
                "SELECT 1 FROM rego_watchlist WHERE registration = ? LIMIT 1",
                (registration.strip(),),
            ).fetchone() is not None

    def is_on_type_watchlist(self, airline: str, aircraft_type: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM type_watchlist WHERE airline = ? AND aircraft_type = ? LIMIT 1",
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
                "SELECT 1 FROM exclusion_list WHERE registration = ? LIMIT 1", (registration,)
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
                "SELECT last_notified_ts FROM special_livery_history WHERE registration = ?",
                (registration,),
            ).fetchone()
            if row is None:
                # First time seen — insert sentinel so we don't lose track of it
                conn.execute(
                    "INSERT INTO special_livery_history(registration, last_notified_ts) VALUES (?,0)",
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
                "UPDATE special_livery_history SET last_notified_ts = ? WHERE registration = ?",
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
                "SELECT last_seen_ts FROM rare_plane_history WHERE airline = ? AND aircraft_type = ?",
                (airline, aircraft_type),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO rare_plane_history(airline, aircraft_type, last_seen_ts, last_notified_ts) VALUES (?,?,?,0)",
                    (airline, aircraft_type, now_ts),
                )
                return True
            last_seen = int(row["last_seen_ts"])
            is_rare = last_seen == 0 or (now_ts - last_seen) / 86400 > min_absence_days
            conn.execute(
                "UPDATE rare_plane_history SET last_seen_ts = ? WHERE airline = ? AND aircraft_type = ?",
                (now_ts, airline, aircraft_type),
            )
            return is_rare

    def get_rare_plane_last_seen(self, airline: str, aircraft_type: str) -> Optional[int]:
        """Return last_seen_ts for a rare plane combo, or None if never seen."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_seen_ts FROM rare_plane_history WHERE airline = ? AND aircraft_type = ?",
                (airline.strip(), aircraft_type.strip()),
            ).fetchone()
            return int(row["last_seen_ts"]) if row else None

    def mark_rare_plane_notified(self, airline: str, aircraft_type: str, now_ts: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE rare_plane_history SET last_notified_ts = ? WHERE airline = ? AND aircraft_type = ?",
                (now_ts, airline.strip(), aircraft_type.strip()),
            )

    def should_notify_rego_watchlist(self, registration: str, now_ts: int, min_hours: int) -> bool:
        registration = registration.strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_notified_ts FROM rego_watchlist WHERE registration = ?",
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
                "UPDATE rego_watchlist SET last_notified_ts = ? WHERE registration = ?",
                (now_ts, registration.strip()),
            )

    def should_notify_type_watchlist(self, airline: str, aircraft_type: str, now_ts: int, min_hours: int) -> bool:
        airline, aircraft_type = airline.strip(), aircraft_type.strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_notified_ts FROM type_watchlist WHERE airline = ? AND aircraft_type = ?",
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
                "UPDATE type_watchlist SET last_notified_ts = ? WHERE airline = ? AND aircraft_type = ?",
                (now_ts, airline.strip(), aircraft_type.strip()),
            )

    def add_airline_watch(self, icao_code: str, entry_type: str, name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO airline_watchlist(icao_code, entry_type, name, last_notified_ts)"
                " VALUES (?,?,?,0)",
                (icao_code.strip().upper(), entry_type.strip(), name.strip()),
            )

    def should_notify_airline_watchlist(self, icao_code: str, entry_type: str,
                                        now_ts: int, min_hours: int) -> bool:
        icao_code, entry_type = icao_code.strip().upper(), entry_type.strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_notified_ts FROM airline_watchlist WHERE icao_code = ? AND entry_type = ?",
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
                "UPDATE airline_watchlist SET last_notified_ts = ? WHERE icao_code = ? AND entry_type = ?",
                (now_ts, icao_code.strip().upper(), entry_type.strip()),
            )

    def is_on_airline_watchlist(self, icao_code: str, entry_type: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM airline_watchlist WHERE icao_code = ? AND entry_type = ? LIMIT 1",
                (icao_code.strip().upper(), entry_type.strip()),
            ).fetchone() is not None

    def should_notify_military(self, registration: str, now_ts: int, min_hours: int) -> bool:
        registration = registration.strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_notified_ts FROM military_history WHERE registration = ?",
                (registration,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO military_history(registration, last_notified_ts) VALUES (?,0)",
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
                "UPDATE military_history SET last_notified_ts = ? WHERE registration = ?",
                (now_ts, registration.strip()),
            )

    # ------------------------------------------------------------------
    # Follow-up tracking (reminder + cancellation/diversion)
    # ------------------------------------------------------------------

    def record_notified_flight(
        self,
        registration: str,
        flight_number: str,
        notif_type: str,
        arrival_ts: int,
        first_notified_ts: int,
        now_ts: int,
        extra_info: str = "",
        detail: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO notification_record
                  (registration, flight_number, notif_type,
                   original_arr_ts, arrival_ts, first_notified_ts,
                   reminder_sent, last_seen_ts, extra_info, detail)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (registration.strip(), flight_number, notif_type,
                 arrival_ts, arrival_ts, first_notified_ts, now_ts, extra_info, detail),
            )
            conn.execute(
                """
                INSERT INTO notification_log
                  (registration, flight_number, notif_type, detail, extra_info, arrival_ts, notified_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (registration.strip(), flight_number, notif_type, detail, extra_info, arrival_ts, first_notified_ts),
            )

    def get_tracked_flights(self) -> List[sqlite3.Row]:
        return self._fetch("SELECT * FROM notification_record")

    def mark_cluster_notified(self, registrations: list, ts: int) -> None:
        """Set cluster_notified_ts for all registrations in a notified cluster."""
        if not registrations:
            return
        placeholders = ",".join("?" * len(registrations))
        with self._connect() as conn:
            conn.execute(
                f"UPDATE notification_record SET cluster_notified_ts = ? WHERE registration IN ({placeholders})",
                [ts] + list(registrations),
            )

    def update_tracked_flight(self, registration: str, last_seen_ts: int, arrival_ts: int) -> None:
        """Refresh last-seen time and current estimated arrival (may drift from original)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE notification_record SET last_seen_ts = ?, arrival_ts = ? WHERE registration = ?",
                (last_seen_ts, arrival_ts, registration.strip()),
            )

    def mark_reminder_sent(self, registration: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE notification_record SET reminder_sent = 1 WHERE registration = ?",
                (registration.strip(),),
            )

    def mark_approach_notified(self, registration: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE notification_record SET approach_notified = 1 WHERE registration = ?",
                (registration.strip(),),
            )

    def mark_dep_notified(self, registration: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE notification_record SET dep_notified = 1 WHERE registration = ?",
                (registration.strip(),),
            )

    def reset_rapid_alerts(self) -> None:
        """Reset approach_notified and dep_notified for all tracked flights (called on Rapid Mode deactivation)."""
        with self._connect() as conn:
            conn.execute("UPDATE notification_record SET approach_notified = 0, dep_notified = 0")

    def delete_tracked_flight(self, registration: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM notification_record WHERE registration = ?", (registration.strip(),))

    def cleanup_arrived_flights(self, now_ts: int) -> None:
        """Remove records for planes that arrived more than 24h ago; prune notification_log older than 7 days."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM notification_record WHERE arrival_ts > 0 AND arrival_ts < ?",
                (now_ts - 86400,),
            )
            conn.execute(
                "DELETE FROM notification_log WHERE notified_ts < ?",
                (now_ts - 7 * 86400,),
            )
            conn.execute(
                "DELETE FROM daily_flights WHERE last_seen_ts < ?",
                (now_ts - 86400,),
            )

    def upsert_daily_flight(self, registration: str, flight_number: str, notif_type: str,
                            arrival_ts: int, detail: str, extra_info: str, now_ts: int) -> None:
        """Insert or update a daily_flights row for a (registration, flight_number) pair."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_flights
                  (registration, flight_number, notif_type, arrival_ts, detail, extra_info,
                   first_seen_ts, last_seen_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(registration, flight_number) DO UPDATE SET
                  arrival_ts   = excluded.arrival_ts,
                  last_seen_ts = excluded.last_seen_ts
                """,
                (registration.strip(), flight_number, notif_type, arrival_ts,
                 detail, extra_info, now_ts, now_ts),
            )

    def get_daily_flights(self) -> List[sqlite3.Row]:
        """Return all daily_flights rows, falling back to notification_record for any
        registration not yet in daily_flights (transition period before daily_flights is populated)."""
        return self._fetch("""
            SELECT registration, flight_number, notif_type, arrival_ts, detail, extra_info,
                   first_seen_ts, last_seen_ts, cluster_notified_ts
            FROM daily_flights
            UNION
            SELECT registration, flight_number, notif_type, arrival_ts, detail, extra_info,
                   first_notified_ts AS first_seen_ts, last_seen_ts, cluster_notified_ts
            FROM notification_record
            WHERE NOT EXISTS (
                SELECT 1 FROM daily_flights df
                WHERE df.registration = notification_record.registration
            )
            ORDER BY arrival_ts
        """)

    def mark_daily_flight_cluster_notified(self, flights: list, ts: int) -> None:
        """Set cluster_notified_ts for specific (registration, flight_number) pairs."""
        if not flights:
            return
        with self._connect() as conn:
            for registration, flight_number in flights:
                conn.execute(
                    "UPDATE daily_flights SET cluster_notified_ts = ? "
                    "WHERE registration = ? AND flight_number = ?",
                    (ts, registration, flight_number),
                )

    def get_notification_history(self, days: int = 7) -> List[sqlite3.Row]:
        """Return notification_log entries from the last N days, newest first."""
        from datetime import datetime as _dt
        cutoff = int(_dt.now().timestamp()) - days * 86400
        return self._fetch(
            "SELECT * FROM notification_log WHERE notified_ts >= ? ORDER BY notified_ts DESC",
            (cutoff,),
        )

    # ------------------------------------------------------------------
    # Sighting history (every registration seen in arrivals feed)
    # ------------------------------------------------------------------

    def get_notification_stats(self) -> dict:
        """Return counts of notifications sent across all filter types."""
        with self._connect() as conn:
            return {
                "special_liveries": conn.execute(
                    "SELECT COUNT(*) FROM special_livery_history WHERE last_notified_ts > 0"
                ).fetchone()[0],
                "military": conn.execute(
                    "SELECT COUNT(*) FROM military_history WHERE last_notified_ts > 0"
                ).fetchone()[0],
                "rego_hits": conn.execute(
                    "SELECT COUNT(*) FROM rego_watchlist WHERE last_notified_ts > 0"
                ).fetchone()[0],
                "type_hits": conn.execute(
                    "SELECT COUNT(*) FROM type_watchlist WHERE last_notified_ts > 0"
                ).fetchone()[0],
                "airline_hits": conn.execute(
                    "SELECT COUNT(*) FROM airline_watchlist WHERE last_notified_ts > 0"
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
                INSERT INTO flight_departure_pattern
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
                UPDATE flight_departure_pattern
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
                INSERT INTO rare_plane_history(airline, aircraft_type, last_seen_ts, last_notified_ts)
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
                FROM flight_departure_pattern
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
        """Update actual_dep_ts for all flight_departure_pattern rows matching this departure flight."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE flight_departure_pattern
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
                "SELECT SUM(count) FROM flight_departure_pattern "
                "WHERE arrival_flight_number = ? AND airport_iata = ?",
                (arrival_fn, airport_iata),
            ).fetchone()[0] or 0
            if total == 0:
                return None
            row = conn.execute(
                "SELECT departure_flight_number, count FROM flight_departure_pattern "
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

        sightings: {registration: real_arrival_ts}
        Only updates a record if the new timestamp is more recent than the stored one,
        so repeat checks for the same flight never overwrite with stale data.
        """
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO sighting_history(registration, last_seen_ts) VALUES (?, ?)
                ON CONFLICT(registration) DO UPDATE
                SET last_seen_ts = MAX(last_seen_ts, excluded.last_seen_ts)
                """,
                [(r.strip(), ts) for r, ts in sightings.items() if r.strip()],
            )

    def get_last_seen(self, registration: str) -> Optional[int]:
        """Return the Unix timestamp of the last time this registration appeared in arrivals, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_seen_ts FROM sighting_history WHERE registration = ?",
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
                "SELECT dest_iata, dest_name FROM flight_departure_pattern "
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
                "SELECT dest_iata, dest_name FROM flight_departure_pattern "
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
        """Upsert (flight_number, aircraft_type, airport_iata, ts) for landed aircraft.

        Increments count and updates last_seen_ts; preserves first_seen_ts on conflict.
        """
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO route_type_history
                    (flight_number, aircraft_type, airport_iata, count, first_seen_ts, last_seen_ts)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(flight_number, aircraft_type, airport_iata) DO UPDATE SET
                    count        = CASE
                                       WHEN excluded.last_seen_ts > route_type_history.last_seen_ts + 14400
                                       THEN count + 1
                                       ELSE count
                                   END,
                    last_seen_ts = MAX(last_seen_ts, excluded.last_seen_ts)
                """,
                [(fn, at, iata, ts, ts) for fn, at, iata, ts in records],
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
                FROM route_type_history
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
                SELECT last_notified_ts FROM route_type_history
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
                UPDATE route_type_history SET last_notified_ts = ?
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
                FROM route_type_history
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
                "SELECT * FROM airframe_db WHERE registration = ?",
                (registration.upper(),),
            ).fetchone()
            if row is None and icao24:
                row = conn.execute(
                    "SELECT * FROM airframe_db WHERE icao24 = ?",
                    (icao24.lower(),),
                ).fetchone()
        return dict(row) if row else None

    def bulk_upsert_airframes(self, records: list) -> None:
        """INSERT OR REPLACE a batch of airframe rows."""
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO airframe_db
                    (registration, icao24, manufacturer, serial_number, built_year,
                     owner, operator, operator_icao, operator_iata, fetched_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )

    def airframe_last_updated(self) -> Optional[int]:
        """Return the most recent fetched_ts in airframe_db, or None if empty."""
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(fetched_ts) FROM airframe_db").fetchone()
        return row[0] if row and row[0] else None

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
        """Insert or update airframe_db with FR24-sourced data.

        Uses COALESCE so existing non-null values are never overwritten by nulls,
        except photo_url which is always refreshed when a new one is provided.
        """
        import time as _time
        now_ts = int(_time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO airframe_db
                    (registration, icao24, manufacturer, serial_number, built_year,
                     owner, operator, operator_icao, operator_iata, photo_url, fetched_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(registration) DO UPDATE SET
                    icao24        = COALESCE(airframe_db.icao24,        excluded.icao24),
                    manufacturer  = COALESCE(airframe_db.manufacturer,  excluded.manufacturer),
                    serial_number = COALESCE(airframe_db.serial_number, excluded.serial_number),
                    built_year    = COALESCE(airframe_db.built_year,    excluded.built_year),
                    owner         = COALESCE(airframe_db.owner,         excluded.owner),
                    operator      = COALESCE(airframe_db.operator,      excluded.operator),
                    operator_icao = COALESCE(airframe_db.operator_icao, excluded.operator_icao),
                    operator_iata = COALESCE(airframe_db.operator_iata, excluded.operator_iata),
                    photo_url     = COALESCE(excluded.photo_url, airframe_db.photo_url),
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
        return "exclusion_list", "SELECT id FROM exclusion_list ORDER BY id ASC"
    if list_name == "Rego Watchlist":
        return "rego_watchlist", "SELECT id FROM rego_watchlist ORDER BY id ASC"
    if list_name == "Type Watchlist":
        return "type_watchlist", "SELECT id FROM type_watchlist ORDER BY id ASC"
    if list_name == "Airline/Operator Watchlist":
        return "airline_watchlist", "SELECT id FROM airline_watchlist ORDER BY id ASC"
    raise ValueError(f"Unknown list: {list_name!r}")
