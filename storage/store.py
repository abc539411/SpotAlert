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
                    extra_info        TEXT DEFAULT ''
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

            # Persists settings changed via the Telegram bot so they survive restarts.
            # On startup, these values take precedence over config.env.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

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
            cols = ["Airline", "Registration", "Description"]
            rows = self._fetch(
                "SELECT airline, registration, description FROM exclusion_list ORDER BY id ASC"
            )
            return TableView(columns=cols, rows=[
                {"Airline": r["airline"], "Registration": r["registration"], "Description": r["description"]}
                for r in rows
            ])
        if list_name == "Rego Watchlist":
            cols = ["Airline", "Registration", "Description"]
            rows = self._fetch(
                "SELECT airline, registration, description FROM rego_watchlist ORDER BY id ASC"
            )
            return TableView(columns=cols, rows=[
                {"Airline": r["airline"], "Registration": r["registration"], "Description": r["description"]}
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
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO notification_record
                  (registration, flight_number, notif_type,
                   original_arr_ts, arrival_ts, first_notified_ts,
                   reminder_sent, last_seen_ts, extra_info)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (registration.strip(), flight_number, notif_type,
                 arrival_ts, arrival_ts, first_notified_ts, now_ts, extra_info),
            )

    def get_tracked_flights(self) -> List[sqlite3.Row]:
        return self._fetch("SELECT * FROM notification_record")

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

    def delete_tracked_flight(self, registration: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM notification_record WHERE registration = ?", (registration.strip(),))

    def cleanup_arrived_flights(self, now_ts: int) -> None:
        """Remove records for planes that arrived more than 24h ago."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM notification_record WHERE arrival_ts > 0 AND arrival_ts < ?",
                (now_ts - 86400,),
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
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return list(conn.execute(sql, params).fetchall())


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
