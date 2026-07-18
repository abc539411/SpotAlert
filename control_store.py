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
        # See store.py's _connect() for why 30s — same "default 5s busy timeout is
        # too short under real write contention" reasoning applies here too.
        conn = sqlite3.connect(self.db_path, timeout=30.0)
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
            _wu_cols = {row[1] for row in conn.execute("PRAGMA table_info(web_users)").fetchall()}
            if "language" not in _wu_cols:
                conn.execute("ALTER TABLE web_users ADD COLUMN language TEXT DEFAULT NULL")

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
                    active       INTEGER NOT NULL DEFAULT 1,
                    country_code TEXT DEFAULT ''
                )
            """)
            _wa_cols = {row[1] for row in conn.execute("PRAGMA table_info(watched_airports)").fetchall()}
            if "country_code" not in _wa_cols:
                conn.execute("ALTER TABLE watched_airports ADD COLUMN country_code TEXT DEFAULT ''")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS fleet_cards (
                    owner_user_id TEXT NOT NULL,
                    icao          TEXT NOT NULL,
                    iata          TEXT NOT NULL DEFAULT '',
                    airline       TEXT NOT NULL DEFAULT '',
                    aircraft_json TEXT NOT NULL DEFAULT '[]',
                    updated_ts    INTEGER NOT NULL,
                    PRIMARY KEY (owner_user_id, icao)
                )
            """)
            fc_cols = {row[1] for row in conn.execute("PRAGMA table_info(fleet_cards)").fetchall()}
            if "iata" not in fc_cols:
                conn.execute("ALTER TABLE fleet_cards ADD COLUMN iata TEXT NOT NULL DEFAULT ''")
            if "airline" not in fc_cols:
                conn.execute("ALTER TABLE fleet_cards ADD COLUMN airline TEXT NOT NULL DEFAULT ''")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      TEXT NOT NULL,
                    endpoint     TEXT NOT NULL UNIQUE,
                    p256dh       TEXT NOT NULL,
                    auth         TEXT NOT NULL,
                    user_agent   TEXT DEFAULT '',
                    created_ts   INTEGER NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS owner_last_airport (
                    owner_user_id TEXT PRIMARY KEY,
                    airport_iata  TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS push_notification_prefs (
                    owner_user_id TEXT NOT NULL,
                    notif_type    TEXT NOT NULL,
                    enabled       INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (owner_user_id, notif_type)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS spotting_reminder_prefs (
                    owner_user_id TEXT PRIMARY KEY,
                    send_time     TEXT NOT NULL DEFAULT '18:00',
                    weather_gate  TEXT NOT NULL DEFAULT 'none',
                    min_aircraft  INTEGER NOT NULL DEFAULT 2,
                    last_sent_date TEXT
                )
            """)

            # Cross-process signal: the web process and monitor process are separate
            # OS processes (see monitor_service.py), so /api/force-check can't just
            # set an in-process asyncio.Event anymore — it writes a row here instead,
            # which the monitor process's force-check poller (monitor_runner.py)
            # picks up on its next poll tick and deletes once handled.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS force_check_requests (
                    airport_iata  TEXT PRIMARY KEY,
                    requested_ts  INTEGER NOT NULL
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

    def get_controller_catalog_path(self) -> Optional[str]:
        """Catalogs are per-user, but the background monitor loop's shared
        clustering pass (one result per airport, not per viewer) needs a single
        "ground truth" catalog for its cached already-photographed gate — the
        Controller's own. Multiple Controller accounts are technically allowed;
        this picks the first one (by creation order) that actually has a
        catalog uploaded."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT catalog_path FROM web_users WHERE role = 'controller' "
                "AND catalog_path IS NOT NULL ORDER BY created_ts ASC LIMIT 1"
            ).fetchone()
            return row["catalog_path"] if row else None

    def update_user(self, user_id: str, role: Optional[str] = None,
                     airport_iatas: Optional[List[str]] = None,
                     username: Optional[str] = None) -> None:
        with self._connect() as conn:
            if username:
                conn.execute("UPDATE web_users SET username = ? WHERE user_id = ?", (username.strip(), user_id))
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
        """Deletes the web_users row (cascades user_airport_access via FK), their
        fleet_cards, and every push-related row (subscriptions, per-type prefs,
        spotting-reminder prefs, last-selected-airport) — none of those 4 tables
        have an FK to web_users (each owner_user_id is a free-form string, since
        it's also used for the literal 'controller' sentinel), so without this
        they'd silently become orphaned rows every time a Pilot/Passenger account
        is removed. Caller is responsible for cleaning up per-airport DB rows
        (settings/filter tables) and the catalog file — SQLite has no cross-file FKs."""
        with self._connect() as conn:
            conn.execute("DELETE FROM fleet_cards WHERE owner_user_id = ?", (user_id,))
            conn.execute("DELETE FROM push_subscriptions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM push_notification_prefs WHERE owner_user_id = ?", (user_id,))
            conn.execute("DELETE FROM spotting_reminder_prefs WHERE owner_user_id = ?", (user_id,))
            conn.execute("DELETE FROM owner_last_airport WHERE owner_user_id = ?", (user_id,))
            conn.execute("DELETE FROM web_users WHERE user_id = ?", (user_id,))

    def get_user_airports(self, user_id: str) -> List[str]:
        with self._connect() as conn:
            return [r["airport_iata"] for r in conn.execute(
                "SELECT airport_iata FROM user_airport_access WHERE user_id = ?", (user_id,)
            ).fetchall()]

    def get_users_with_airport_access(self, airport_iata: str) -> List[sqlite3.Row]:
        """Every Pilot/Passenger explicitly granted this airport — used to fan
        out push notifications to every eligible user, not just the
        Controller. The Controller isn't in this table at all (implicit
        access to every airport) and must be handled separately by the
        caller via the literal 'controller' sentinel."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT u.user_id, u.role FROM user_airport_access a "
                "JOIN web_users u ON u.user_id = a.user_id "
                "WHERE a.airport_iata = ?",
                (airport_iata,),
            ).fetchall()

    def set_catalog_path(self, user_id: str, catalog_path: Optional[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE web_users SET catalog_path = ? WHERE user_id = ?",
                (catalog_path, user_id),
            )

    def set_language(self, user_id: str, language: Optional[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE web_users SET language = ? WHERE user_id = ?",
                (language, user_id),
            )

    def get_language(self, user_id: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT language FROM web_users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return row["language"] if row else None

    def get_controller_language(self) -> Optional[str]:
        """Push notifications for the Controller role are sent under the
        literal 'controller' owner sentinel (see monitor._push_owner_id),
        not a real web_users.user_id, so there's no single row to look up a
        language preference from. Same fallback reasoning as
        get_controller_catalog_path: picks the first Controller account (by
        creation order) that has actually set a language."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT language FROM web_users WHERE role = 'controller' "
                "AND language IS NOT NULL ORDER BY created_ts ASC LIMIT 1"
            ).fetchone()
            return row["language"] if row else None

    # ── Fleet cards ──────────────────────────────────────────────────────────
    # User-scoped (not airport-scoped) — same as catalogs, the Fleet subtab must
    # look the same regardless of which airport is selected. owner_user_id is
    # the 'controller' sentinel for Controller/Passenger, or a Pilot's own
    # stable token — never merged between owners.

    def get_fleet_cards(self, owner_user_id: str) -> List[dict]:
        import json as _json
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT icao, iata, airline, aircraft_json, updated_ts FROM fleet_cards "
                "WHERE owner_user_id = ? ORDER BY updated_ts", (owner_user_id,)
            ).fetchall()
        return [{"icao": r["icao"], "iata": r["iata"], "airline": r["airline"],
                 "aircraft": _json.loads(r["aircraft_json"]), "updated_at": r["updated_ts"]} for r in rows]

    def upsert_fleet_card(self, owner_user_id: str, icao: str, iata: str, airline: str,
                           aircraft: list, updated_at: Optional[int] = None) -> None:
        import json as _json, time as _time
        ts = updated_at or int(_time.time())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO fleet_cards(owner_user_id, icao, iata, airline, aircraft_json, updated_ts) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(owner_user_id, icao) DO UPDATE SET iata=excluded.iata, airline=excluded.airline, "
                "aircraft_json=excluded.aircraft_json, updated_ts=excluded.updated_ts",
                (owner_user_id, icao.upper(), iata.upper(), airline, _json.dumps(aircraft), ts),
            )

    def update_fleet_card_photos(self, owner_user_id: str, icao: str, aircraft: list) -> None:
        import json as _json
        with self._connect() as conn:
            conn.execute(
                "UPDATE fleet_cards SET aircraft_json = ? WHERE owner_user_id = ? AND icao = ?",
                (_json.dumps(aircraft), owner_user_id, icao.upper()),
            )

    def delete_fleet_card(self, owner_user_id: str, icao: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM fleet_cards WHERE owner_user_id = ? AND icao = ?",
                (owner_user_id, icao.upper()),
            )

    def get_fleet_card_owners(self) -> List[str]:
        """Distinct owners that currently have any fleet cards — for the
        periodic background refresh loop, which has no per-request viewer."""
        with self._connect() as conn:
            return [r[0] for r in conn.execute("SELECT DISTINCT owner_user_id FROM fleet_cards").fetchall()]

    # ── Web Push subscriptions ───────────────────────────────────────────────
    # Per-user (not per-airport, not per-browser-instance) — a user's push
    # subscription applies regardless of which airport they currently have
    # selected. user_id is the same owner sentinel/token used everywhere else
    # ('controller' for the Controller/Passenger, a Pilot's own stable token).

    def add_push_subscription(self, user_id: str, endpoint: str, p256dh: str,
                               auth: str, user_agent: str = "", ts: Optional[int] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO push_subscriptions(user_id, endpoint, p256dh, auth, user_agent, created_ts) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, "
                "auth=excluded.auth, user_agent=excluded.user_agent",
                (user_id, endpoint, p256dh, auth, user_agent, ts or int(time.time())),
            )

    def remove_push_subscription(self, endpoint: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))

    def get_push_subscriptions(self, user_id: str) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ?", (user_id,)
            ).fetchall()
        return [{"endpoint": r["endpoint"], "p256dh": r["p256dh"], "auth": r["auth"]} for r in rows]

    # ── Last-selected airport ─────────────────────────────────────────────────
    # Same owner_user_id scoping as push_subscriptions/fleet_cards — tracked
    # server-side (not just the sa_airport cookie set by /api/airport/select)
    # so a background task with no HTTP request in hand (the push-notification
    # trigger in monitor.py's _enrich_and_store) can still know which airport an
    # owner currently has selected, to only notify for that one.

    def set_last_airport(self, owner_user_id: str, airport_iata: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO owner_last_airport(owner_user_id, airport_iata) VALUES (?,?) "
                "ON CONFLICT(owner_user_id) DO UPDATE SET airport_iata=excluded.airport_iata",
                (owner_user_id, airport_iata),
            )

    def get_last_airport(self, owner_user_id: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT airport_iata FROM owner_last_airport WHERE owner_user_id = ?", (owner_user_id,)
            ).fetchone()
        return row["airport_iata"] if row else None

    # ── Push notification per-filter preferences ─────────────────────────────
    # Default is "everything on" — a row only ever gets written when a user
    # explicitly disables a type, so an unwritten type reads as enabled without
    # needing to pre-populate all 5 rows for every owner up front.

    def set_push_notif_enabled(self, owner_user_id: str, notif_type: str, enabled: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO push_notification_prefs(owner_user_id, notif_type, enabled) VALUES (?,?,?) "
                "ON CONFLICT(owner_user_id, notif_type) DO UPDATE SET enabled=excluded.enabled",
                (owner_user_id, notif_type, 1 if enabled else 0),
            )

    def get_disabled_push_notif_types(self, owner_user_id: str) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT notif_type FROM push_notification_prefs WHERE owner_user_id = ? AND enabled = 0",
                (owner_user_id,),
            ).fetchall()
        return [r["notif_type"] for r in rows]

    # ── Spotting reminder preferences ─────────────────────────────────────────
    # send_time is "HH:MM" in the owner's currently-selected airport's local
    # time (re-evaluated live against cfg.airport_tz each check, not fixed to
    # whichever airport was selected when the time was saved). weather_gate is
    # one of 'none' / 'ignore_severe' / 'sunny_only'. last_sent_date (the
    # airport-local date the reminder last actually fired) guards against
    # sending twice in the same day if the check loop runs more than once
    # inside the same minute.

    _SPOTTING_REMINDER_DEFAULTS = {
        "send_time": "18:00", "weather_gate": "none", "min_aircraft": 2, "last_sent_date": None,
    }

    def get_spotting_reminder_prefs(self, owner_user_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT send_time, weather_gate, min_aircraft, last_sent_date "
                "FROM spotting_reminder_prefs WHERE owner_user_id = ?",
                (owner_user_id,),
            ).fetchone()
        if not row:
            return dict(self._SPOTTING_REMINDER_DEFAULTS)
        return dict(row)

    def set_spotting_reminder_prefs(self, owner_user_id: str, send_time: str = None,
                                     weather_gate: str = None, min_aircraft: int = None) -> None:
        current = self.get_spotting_reminder_prefs(owner_user_id)
        if send_time is not None: current["send_time"] = send_time
        if weather_gate is not None: current["weather_gate"] = weather_gate
        if min_aircraft is not None: current["min_aircraft"] = min_aircraft
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO spotting_reminder_prefs(owner_user_id, send_time, weather_gate, min_aircraft) "
                "VALUES (?,?,?,?) ON CONFLICT(owner_user_id) DO UPDATE SET "
                "send_time=excluded.send_time, weather_gate=excluded.weather_gate, min_aircraft=excluded.min_aircraft",
                (owner_user_id, current["send_time"], current["weather_gate"], current["min_aircraft"]),
            )

    def set_spotting_reminder_last_sent(self, owner_user_id: str, date_str: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO spotting_reminder_prefs(owner_user_id, last_sent_date) VALUES (?,?) "
                "ON CONFLICT(owner_user_id) DO UPDATE SET last_sent_date=excluded.last_sent_date",
                (owner_user_id, date_str),
            )

    # ── Watched airports ─────────────────────────────────────────────────────

    def register_airport(self, airport_iata: str, airport_code: str, airport_name: str,
                          airport_icao: str, airport_tz: str, airport_lat: float,
                          airport_lon: float, db_path: str,
                          added_by_user_id: Optional[str] = None,
                          country_code: str = '') -> None:
        now_ts = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO watched_airports
                   (airport_iata, airport_code, airport_name, airport_icao, airport_tz,
                    airport_lat, airport_lon, db_path, added_by_user_id, added_ts, active,
                    country_code)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (airport_iata, airport_code, airport_name, airport_icao, airport_tz,
                 airport_lat, airport_lon, db_path, added_by_user_id, now_ts, country_code),
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

    def set_airport_country_code(self, airport_iata: str, country_code: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE watched_airports SET country_code = ? WHERE airport_iata = ?",
                (country_code, airport_iata),
            )

    def delete_watched_airport(self, airport_iata: str) -> None:
        """Hard delete: the airport row and every user's access grant to it are
        removed outright (not a soft active=0 flag) — the caller is responsible
        for also deleting the airport's own SQLite DB file from disk, since
        that lives outside control.db entirely."""
        with self._connect() as conn:
            conn.execute("DELETE FROM watched_airports WHERE airport_iata = ?", (airport_iata,))
            conn.execute("DELETE FROM user_airport_access WHERE airport_iata = ?", (airport_iata,))

    # ── Force-check cross-process signal ─────────────────────────────────────

    def request_force_check(self, airport_iata: str) -> None:
        """Called by /api/force-check (web process). Upsert rather than insert-only
        so a second click before the first request is picked up just refreshes the
        timestamp instead of erroring on the PRIMARY KEY."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO force_check_requests(airport_iata, requested_ts) VALUES (?, ?) "
                "ON CONFLICT(airport_iata) DO UPDATE SET requested_ts=excluded.requested_ts",
                (airport_iata, int(time.time())),
            )

    def pop_pending_force_check(self, airport_iata: str) -> bool:
        """Called by the monitor process's force-check poller. Returns True (and
        deletes the row) if a check was requested for this airport since the last
        poll — the DELETE-if-exists happens in one connection so two pollers could
        never both claim the same request, though in practice there's only ever
        one monitor process per the role-scoped instance lock in bootstrap.py."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM force_check_requests WHERE airport_iata = ?", (airport_iata,)
            ).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM force_check_requests WHERE airport_iata = ?", (airport_iata,))
            return True
