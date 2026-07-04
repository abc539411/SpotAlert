from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import time
from typing import List, Optional

log = logging.getLogger(__name__)

# Reserved sentinel used inside each per-airport DB's `settings`/filter tables to mean
# "the Controller role's ground-truth row" — never a possible secrets.token_hex(16) output.
CONTROLLER_SENTINEL = "controller"


class ControlStore:
    """Cross-cutting data that is NOT airport-specific: user accounts, sessions,
    the registry of watched airports, and per-user catalog paths/fleet cards.
    Separate from SqliteStore, which is one-instance-per-airport."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS web_users (
                    user_id       TEXT PRIMARY KEY,
                    username      TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role          TEXT NOT NULL CHECK(role IN ('controller','pilot','passenger')),
                    session_epoch INTEGER NOT NULL DEFAULT 0,
                    created_ts    INTEGER NOT NULL,
                    catalog_path  TEXT DEFAULT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_airport_access (
                    user_id      TEXT NOT NULL REFERENCES web_users(user_id) ON DELETE CASCADE,
                    airport_iata TEXT NOT NULL,
                    PRIMARY KEY (user_id, airport_iata)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS watched_airports (
                    airport_iata TEXT PRIMARY KEY,
                    airport_code TEXT NOT NULL,
                    airport_name TEXT NOT NULL,
                    airport_icao TEXT NOT NULL,
                    airport_tz   TEXT NOT NULL,
                    airport_lat  REAL NOT NULL,
                    airport_lon  REAL NOT NULL,
                    db_path      TEXT NOT NULL,
                    added_by_user_id TEXT REFERENCES web_users(user_id),
                    added_ts     INTEGER NOT NULL,
                    active       INTEGER NOT NULL DEFAULT 1
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS fleet_cards (
                    owner_user_id TEXT NOT NULL,
                    icao          TEXT NOT NULL,
                    aircraft_json TEXT NOT NULL DEFAULT '[]',
                    updated_ts    INTEGER NOT NULL,
                    PRIMARY KEY (owner_user_id, icao)
                )
            """)

            conn.execute("PRAGMA foreign_keys = ON;")

    # ── Users ──────────────────────────────────────────────────────────────

    def create_user(self, username: str, password_hash: str, role: str,
                     airport_iatas: Optional[List[str]] = None) -> str:
        """Creates a web_users row with a freshly-generated stable user_id token.
        Returns the new user_id."""
        user_id = secrets.token_hex(16)
        now_ts = int(time.time())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO web_users (user_id, username, password_hash, role, created_ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, username.strip(), password_hash, role, now_ts),
            )
            for iata in (airport_iatas or []):
                conn.execute(
                    "INSERT OR IGNORE INTO user_airport_access (user_id, airport_iata) VALUES (?, ?)",
                    (user_id, iata),
                )
        return user_id

    def get_user_by_username(self, username: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM web_users WHERE username = ?", (username.strip(),)
            ).fetchone()

    def get_user(self, user_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM web_users WHERE user_id = ?", (user_id,)
            ).fetchone()

    def list_users(self) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM web_users ORDER BY created_ts ASC").fetchall()

    def count_users(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM web_users").fetchone()[0]

    def update_user(self, user_id: str, role: Optional[str] = None,
                     airport_iatas: Optional[List[str]] = None) -> None:
        with self._connect() as conn:
            if role:
                conn.execute("UPDATE web_users SET role = ? WHERE user_id = ?", (role, user_id))
            if airport_iatas is not None:
                conn.execute("DELETE FROM user_airport_access WHERE user_id = ?", (user_id,))
                for iata in airport_iatas:
                    conn.execute(
                        "INSERT OR IGNORE INTO user_airport_access (user_id, airport_iata) VALUES (?, ?)",
                        (user_id, iata),
                    )

    def set_password(self, user_id: str, password_hash: str) -> None:
        """Also bumps session_epoch so any existing session cookie is invalidated."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE web_users SET password_hash = ?, session_epoch = session_epoch + 1 "
                "WHERE user_id = ?",
                (password_hash, user_id),
            )

    def bump_session_epoch(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE web_users SET session_epoch = session_epoch + 1 WHERE user_id = ?",
                (user_id,),
            )

    def delete_user(self, user_id: str) -> None:
        """Deletes the web_users row (cascades user_airport_access via FK) and their
        fleet_cards. Caller is responsible for cleaning up per-airport DB rows
        (settings/filter tables) and the catalog file — SQLite has no cross-file FKs."""
        with self._connect() as conn:
            conn.execute("DELETE FROM fleet_cards WHERE owner_user_id = ?", (user_id,))
            conn.execute("DELETE FROM web_users WHERE user_id = ?", (user_id,))

    def get_user_airports(self, user_id: str) -> List[str]:
        with self._connect() as conn:
            return [r["airport_iata"] for r in conn.execute(
                "SELECT airport_iata FROM user_airport_access WHERE user_id = ?", (user_id,)
            ).fetchall()]

    def set_catalog_path(self, user_id: str, catalog_path: Optional[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE web_users SET catalog_path = ? WHERE user_id = ?",
                (catalog_path, user_id),
            )

    # ── Watched airports ─────────────────────────────────────────────────────

    def register_airport(self, airport_iata: str, airport_code: str, airport_name: str,
                          airport_icao: str, airport_tz: str, airport_lat: float,
                          airport_lon: float, db_path: str,
                          added_by_user_id: Optional[str] = None) -> None:
        now_ts = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO watched_airports
                   (airport_iata, airport_code, airport_name, airport_icao, airport_tz,
                    airport_lat, airport_lon, db_path, added_by_user_id, added_ts, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (airport_iata, airport_code, airport_name, airport_icao, airport_tz,
                 airport_lat, airport_lon, db_path, added_by_user_id, now_ts),
            )

    def get_active_watched_airports(self) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM watched_airports WHERE active = 1 ORDER BY added_ts ASC"
            ).fetchall()

    def get_watched_airport(self, airport_iata: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM watched_airports WHERE airport_iata = ?", (airport_iata,)
            ).fetchone()

    def deactivate_airport(self, airport_iata: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE watched_airports SET active = 0 WHERE airport_iata = ?", (airport_iata,)
            )
