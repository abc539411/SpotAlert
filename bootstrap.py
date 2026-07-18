"""Process-agnostic startup/config helpers shared by main.py (web server) and
monitor_service.py (background monitor loop) — split into its own module so
monitor_service.py can build its own AppConfig/store/control_store objects
without importing web.py's FastAPI app (a genuinely separate OS process needs
its own copies of these, not a shared in-memory object — see monitor_service.py's
module docstring for why the two processes exist at all).

Every object built here (SqliteStore, ControlStore, LightroomCatalog, AppConfig)
is a plain object with no cross-process singleton assumptions — safe to
construct twice, once per process, pointed at the same SQLite files (which are
opened in WAL mode, supporting concurrent multi-process access natively).
"""
from __future__ import annotations

import logging
import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from flightradar24api import FlightRadar24API
from store import SqliteStore

log = logging.getLogger(__name__)


@dataclass
class AppConfig:
    # Target airport
    airport_code: str
    airport_name: str
    airport_iata: str
    airport_icao: str
    airport_tz: str
    airport_lat: float
    airport_lon: float
    fetch_pages: List[int]      # page numbers to request from FR24 (100 flights/page)

    # Telegram
    chat_id: str

    # Special Livery filter
    livery_keywords: List[str]
    livery_exclude_keywords: List[str]

    # Rare Plane filter
    rare_plane_min_absence_days: int

    # Seconds between each arrivals check; used by follow-up logic to detect missed flights
    check_interval: int

    # Military filter (adsb.fi open data — no API key required)
    military_check_interval: int  # seconds between each military check
    military_radius_nm: int
    military_max_alt_ft: int
    military_renotify_hours: int

    # Spot recommendation (active settings — Telegram-only fields removed)
    spot_rec_lighting_gate: bool = True
    spot_rec_max_spotted_times: int = 0
    spot_rec_max_gap_hours: int = 3
    spot_rec_notable_lull_mins: int = 60
    spot_rec_max_lulls: int = 2
    spot_rec_light_buffer_mins: int = 30
    spot_rec_bad_light_start: str = ""
    spot_rec_bad_light_end: str = ""
    departure_pattern_threshold: int = 80  # min % confidence to show a predicted departure

    # Rapid mode — in-memory only, never persisted
    rapid_mode: bool = field(repr=False, default=False)

    # Military auto rapid-tracking — in-memory only, never persisted. Only ever
    # populated/consumed in the monitor process (military polling doesn't run
    # in the web process) — a web-process AppConfig's copy stays permanently
    # empty, which is fine since nothing there depends on it being live.
    # {registration: {"stationary_since_ts": int|None, "last_in_radius_ts": int, "arrival_id": int}}
    military_rapid_tracking: dict = field(repr=False, default_factory=dict)

    # Cancellation/diversion absence-streak tracking — in-memory only, never
    # persisted. Monitor-process-only, same reasoning as military_rapid_tracking.
    # {(registration, flight_number, arrival_date): {"first_absent_ts": int, "streak": int,
    #                                                 "last_known_status": str}}
    cancel_absence_tracking: dict = field(repr=False, default_factory=dict)

    # Dependencies — excluded from repr/comparison
    fr_api: object = field(repr=False, default=None)
    store: object = field(repr=False, default=None)
    catalog: object = field(repr=False, default=None)  # legacy single shared catalog — kept
                                                        # only for the dev-only standalone path;
                                                        # the monitor loop resolves the Controller's
                                                        # own catalog fresh each cycle via control_store
                                                        # instead (catalogs are per-user, not per-cfg).
    control_store: object = field(repr=False, default=None)

    # In-process-only serialization lock for the monitor process: the shared
    # rotation's scheduled check for this airport and a manual force-check
    # (see monitor_runner.run_force_check_poller) both run cfg's check, and
    # this ensures they never run concurrently. Meaningless (and unset/None)
    # in the web process — force-check itself is now a cross-process signal
    # via control_store.request_force_check(), not an in-process primitive
    # (asyncio.Event can't cross a process boundary — see monitor_service.py).
    check_lock: object = field(repr=False, default=None)

    @property
    def all_chat_ids(self) -> List[str]:
        """All registered user chat IDs (admin + secondary)."""
        return [u["chat_id"] for u in self.store.get_all_users()]


def _fetch_airport(fr_api: FlightRadar24API, code: str, store=None, retries: int = 3) -> dict:
    _CACHE_KEYS = ("_airport_name", "_airport_iata", "_airport_icao", "_airport_tz",
                   "_airport_lat", "_airport_lon")
    for attempt in range(1, retries + 1):
        try:
            data = fr_api.get_airport_details(code=code)
            details = data["airport"]["pluginData"]["details"]
            info = {
                "name": details["name"],
                "iata": details["code"]["iata"],
                "icao": details["code"]["icao"],
                "tz":   details["timezone"]["name"],
                "lat":  details["position"]["latitude"],
                "lon":  details["position"]["longitude"],
            }
            if store:
                for k, v in zip(_CACHE_KEYS, info.values()):
                    store.save_setting(k, str(v))
            return info
        except Exception as exc:
            log.warning("Airport fetch attempt %d/%d failed: %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(5)
    # Fall back to cached values if available
    if store:
        cached = [store.load_setting(k) for k in _CACHE_KEYS]
        if all(cached):
            log.warning("Using cached airport info for '%s'", code)
            return {
                "name": cached[0], "iata": cached[1], "icao": cached[2],
                "tz": cached[3], "lat": float(cached[4]), "lon": float(cached[5]),
            }
    log.error("Could not fetch airport info for '%s' — using minimal fallback", code)
    return {"name": code, "iata": code, "icao": code, "tz": "UTC", "lat": 0.0, "lon": 0.0}


def _country_code_for_iata(iata: str) -> str:
    """FR24's airport-details API (_fetch_airport above) doesn't return a
    country code at all, so this is resolved separately via the airportsdata
    package already used elsewhere in the codebase for reference-table
    seeding (store.py). Best-effort — an empty string is a harmless fallback
    (just means no flag shown for that airport in the picker UI)."""
    try:
        import airportsdata
        return (airportsdata.load('IATA').get(iata.upper()) or {}).get('country', '') or ''
    except Exception:
        return ''


def _s(store: SqliteStore, key: str, default: str = "") -> str:
    """Return DB-saved value, or default if not set."""
    return store.load_setting(key) or default


def _si(store: SqliteStore, key: str, default: str = "0") -> int:
    return math.ceil(float(_s(store, key, default=default)))


def _sl(store: SqliteStore, key: str) -> list:
    raw = _s(store, key, default="")
    return [v.strip() for v in raw.split(",") if v.strip()] if raw else []


def build_config(fr_api: FlightRadar24API, store: SqliteStore, catalog=None, control_store=None) -> AppConfig:
    airport_code = _s(store, "AIRPORT_CODE")
    airport = _fetch_airport(fr_api, airport_code, store=store)

    fetch_pages_count = _si(store, "FETCH_PAGES", default="2")
    fetch_pages = list(range(1, fetch_pages_count + 1))

    return AppConfig(
        airport_code=airport_code,
        airport_name=airport["name"],
        airport_iata=airport["iata"],
        airport_icao=airport["icao"],
        airport_tz=airport["tz"],
        airport_lat=airport["lat"],
        airport_lon=airport["lon"],
        fetch_pages=fetch_pages,
        chat_id="",
        livery_keywords=_sl(store, "SPECIAL_LIVERY_KEYWORDS"),
        livery_exclude_keywords=_sl(store, "SPECIAL_LIVERY_EXCLUDE_KEYWORDS"),
        rare_plane_min_absence_days=_si(store, "RARE_PLANE_MIN_ABSENCE_DAYS", default="7"),
        check_interval=math.ceil(float(_s(store, "CHECK_INTERVAL_MINUTES", default="30")) * 60),
        military_check_interval=math.ceil(float(_s(store, "MILITARY_CHECK_INTERVAL_MINUTES", default="15")) * 60),
        military_radius_nm=_si(store, "MILITARY_RADIUS_NM", default="50"),
        military_max_alt_ft=_si(store, "MILITARY_MAX_ALT_FT", default="5000"),
        military_renotify_hours=_si(store, "MILITARY_RENOTIFY_HOURS", default="4"),
        spot_rec_lighting_gate=_s(store, "SPOT_LIGHTING_GATE", default="true").lower() == "true",
        spot_rec_max_spotted_times=_si(store, "SPOT_MAX_SPOTTED", default="0"),
        spot_rec_max_gap_hours=_si(store, "SPOT_MAX_GAP_HOURS", default="3"),
        spot_rec_notable_lull_mins=_si(store, "SPOT_LULL_MINS", default="60"),
        spot_rec_max_lulls=_si(store, "SPOT_MAX_LULLS", default="2"),

        spot_rec_light_buffer_mins=_si(store, "SPOT_LIGHT_BUFFER_MINS", default="30"),
        spot_rec_bad_light_start=_s(store, "SPOT_BAD_LIGHT_START", default=""),
        spot_rec_bad_light_end=_s(store, "SPOT_BAD_LIGHT_END", default=""),
        departure_pattern_threshold=_si(store, "DEPARTURE_PATTERN_THRESHOLD", default="80"),
        fr_api=fr_api,
        store=store,
        catalog=catalog,
        control_store=control_store,
    )


def build_cfgs_for_watched_airports(fr_api, control_store, primary_store, data_dir: str,
                                     catalog=None, resume_military: bool = True) -> Dict[str, "AppConfig"]:
    """One AppConfig per active watched_airports row, each bound to its own
    SqliteStore (its own DB file) — the "one DB file per airport" architecture.
    First-ever boot (no watched_airports rows yet): registers the existing
    single-airport DB in place, in-memory, no data migration.

    resume_military=False skips _resume_military_tracking (only the monitor
    process needs working military_rapid_tracking state — the web process's
    own copy is never read for anything functional, just wasted startup work)."""
    watched = control_store.get_active_watched_airports()
    cfgs: Dict[str, "AppConfig"] = {}

    if not watched:
        cfg = build_config(fr_api, primary_store, catalog, control_store)
        control_store.register_airport(
            airport_iata=cfg.airport_iata, airport_code=cfg.airport_code,
            airport_name=cfg.airport_name, airport_icao=cfg.airport_icao,
            airport_tz=cfg.airport_tz, airport_lat=cfg.airport_lat,
            airport_lon=cfg.airport_lon, db_path=primary_store.db_path,
            country_code=_country_code_for_iata(cfg.airport_iata),
        )
        cfgs[cfg.airport_iata] = cfg
        if resume_military:
            _resume_military_tracking(cfg)
        return cfgs

    for row in watched:
        store = primary_store if row["db_path"] == primary_store.db_path else SqliteStore(row["db_path"])
        cfg = build_config(fr_api, store, catalog, control_store)
        cfgs[row["airport_iata"]] = cfg
        if not row["country_code"]:
            control_store.set_airport_country_code(
                row["airport_iata"], _country_code_for_iata(row["airport_iata"])
            )
        if resume_military:
            _resume_military_tracking(cfg)

    _reconcile_global_settings_across_airports(cfgs)
    _backfill_pilot_filter_copies(cfgs, control_store)
    return cfgs


def _resume_military_tracking(cfg: "AppConfig") -> None:
    """Reconstruct in-memory military rapid-tracking state from the DB on startup.
    cfg.military_rapid_tracking lives only in memory (never persisted), so a process
    restart mid-visit would otherwise be indistinguishable from a brand-new approach
    on the next scan cycle — fragmenting one continuous visit into multiple
    flight_arrivals rows, each with only the few track points seen before the next
    restart. Resume any visit whose last track point is still within the exit grace
    window instead of starting fresh."""
    import military as _military
    now_ts = int(time.time())
    resumed = cfg.store.get_resumable_military_visits(now_ts, _military.MILITARY_STATIONARY_EXIT_SECS)
    for reg, info in resumed.items():
        cfg.military_rapid_tracking[reg] = {
            "stationary_since_ts": None,  # unknown at restart; errs toward continuing to track
            "last_in_radius_ts": info["last_in_radius_ts"],
            "arrival_id": info["arrival_id"],
        }
    if resumed:
        log.info("Resumed %d military visit(s) for %s after restart: %s",
                  len(resumed), cfg.airport_iata, ", ".join(resumed))


# Settings/tables that must read identically across every watched airport
# rather than independent per airport DB — kept in sync by hand with
# GLOBAL_INFRA_SETTINGS below (these are Controller-only keys; the fan-out on
# write lives in web.py's PUT /api/settings, this just reconciles any drift
# accumulated before that write-through existed). NOTE: this set and
# GLOBAL_INFRA_SETTINGS have drifted slightly (the latter also includes the
# Baidu translate keys) — pre-existing, not touched here.
_RECONCILE_INFRA_SETTINGS = frozenset({
    "CHECK_INTERVAL_MINUTES", "FETCH_PAGES", "DEPARTURE_PATTERN_THRESHOLD",
    "MONITOR_CANCEL_GRACE_MINS", "MONITOR_DIVERTED_GRACE_MINS",
    "MONITOR_ABSENCE_CHECKS", "MONITOR_CONFIRM_CALL_CAP",
    "MILITARY_CHECK_INTERVAL_MINUTES", "MILITARY_RADIUS_NM",
    "MILITARY_MAX_ALT_FT", "MILITARY_RENOTIFY_HOURS", "LOGOSTREAM_API_KEY",
})

# Controller-only settings that must be identical across every watched airport
# rather than independent per airport-DB file — the Controller sets one, and
# web.py's PUT /api/settings fans the write out to every airport instead of
# just the selected one. These are unreachable for Pilots (never in
# web.py's PILOT_EDITABLE_SETTINGS), so no per-user variation is possible.
GLOBAL_INFRA_SETTINGS = frozenset({
    "CHECK_INTERVAL_MINUTES", "FETCH_PAGES", "DEPARTURE_PATTERN_THRESHOLD",
    "MONITOR_CANCEL_GRACE_MINS", "MONITOR_DIVERTED_GRACE_MINS",
    "MONITOR_ABSENCE_CHECKS", "MONITOR_CONFIRM_CALL_CAP",
    "MILITARY_CHECK_INTERVAL_MINUTES", "MILITARY_RADIUS_NM",
    "MILITARY_MAX_ALT_FT", "MILITARY_RENOTIFY_HOURS", "LOGOSTREAM_API_KEY",
    "BAIDU_TRANSLATE_APP_ID", "BAIDU_TRANSLATE_SECRET_KEY", "SESSION_PHOTOS_PATH",
})

# Settings whose value must be identical across every airport a given user
# accesses — unlike other settings (which are genuinely independent per
# airport DB), these fan out to every airport's DB at save time, keyed by the
# writing user's own owner id (Controller's or a Pilot's own), so the value
# follows that user everywhere they go. SPECIAL_LIVERY_KEYWORDS is
# Controller-only (not in web.py's PILOT_EDITABLE_SETTINGS), so in practice
# only the 'controller' owner id is ever used for it here.
PER_USER_GLOBAL_SETTINGS = frozenset({
    "COLLECTION_KW_STAT_1", "COLLECTION_KW_STAT_2", "COLLECTION_KW_STAT_3", "collection_session_tags",
    "SPECIAL_LIVERY_KEYWORDS",
})

# Genuinely per-airport settings (each airport keeps its own independent
# value, e.g. because sunrise/sunset/lighting are physically airport-specific)
# that are still seeded ONCE when a user gains a new airport — but the seed
# source depends on whether this user already has a value anywhere else:
#   - Existing user gaining an additional airport: copy THEIR OWN value from
#     any airport they already have access to (not the Controller's).
#   - Brand-new user's very first airport (or the Controller's own value when
#     a brand-new airport is added to the server): fall back to the
#     Controller's value on the new airport itself.
# SPECIAL_LIVERY_EXCLUDE_KEYWORDS is deliberately NOT in this set — per user
# feedback it should never be prefilled from anywhere, always starting blank
# on a new airport grant.
PER_AIRPORT_PREFILL_SETTINGS = frozenset({
    "SPOT_MAX_GAP_HOURS", "SPOT_LULL_MINS", "SPOT_MAX_LULLS", "SPOT_LIGHTING_GATE",
    "SPOT_MAX_SPOTTED", "SPOT_LIGHT_BUFFER_MINS", "SPOT_BAD_LIGHT_START", "SPOT_BAD_LIGHT_END",
    "RARE_PLANE_MIN_ABSENCE_DAYS",
})


def seed_new_airport_prefill_settings(cfgs: dict, owner_user_id: str, new_iata: str) -> None:
    """Called whenever owner_user_id (a Pilot, or the Controller via a
    brand-new watched airport) gains access to new_iata. For each key in
    PER_AIRPORT_PREFILL_SETTINGS, seeds new_iata's own row for owner_user_id
    from wherever a value already exists for them: their own row on any
    OTHER airport in cfgs first, falling back to the Controller's row on
    new_iata itself only if they have no value anywhere yet. No-op per key
    once owner_user_id already has their own row on new_iata (never
    overwrites an edit made after the seed) — safe to call repeatedly."""
    new_cfg = cfgs.get(new_iata)
    if not new_cfg:
        return
    other_cfgs = [cfg for iata, cfg in cfgs.items() if iata != new_iata]
    with new_cfg.store._connect() as new_conn:
        for key in PER_AIRPORT_PREFILL_SETTINGS:
            if new_conn.execute(
                "SELECT 1 FROM settings WHERE user_id = ? AND key = ?", (owner_user_id, key)
            ).fetchone():
                continue
            value = None
            for other_cfg in other_cfgs:
                with other_cfg.store._connect() as oconn:
                    row = oconn.execute(
                        "SELECT value FROM settings WHERE user_id = ? AND key = ?", (owner_user_id, key)
                    ).fetchone()
                if row is not None:
                    value = row["value"]
                    break
            if value is None:
                row = new_conn.execute(
                    "SELECT value FROM settings WHERE user_id = 'controller' AND key = ?", (key,)
                ).fetchone()
                value = row["value"] if row is not None else None
            if value is not None:
                new_conn.execute(
                    "INSERT OR REPLACE INTO settings(user_id, key, value) VALUES (?, ?, ?)",
                    (owner_user_id, key, value),
                )


def _reconcile_global_settings_across_airports(cfgs: Dict[str, "AppConfig"]) -> None:
    """One-time-effect (but safe to re-run every boot — becomes a no-op once
    every airport already matches) sync of the Controller-only global infra
    settings, Controller-only custom airports, and the aircraft_types table,
    using the earliest-added watched airport as the source of truth. Resolves
    drift that accumulated while these were still independent per airport DB.

    aircraft_types is copied in full (every source, not just 'user') — unlike
    `airports`, whose non-user rows accumulate organically per airport from
    that airport's own traffic and don't need syncing, aircraft_types' bulk
    'icaolist' rows (~2700 ICAO type-code -> manufacturer/model mappings) only
    ever get populated by a manual GitHub CSV refresh against ONE store
    (app.state.store — see web.py's /api/aircraft-types/refresh). A newly
    added airport's own DB starts with zero rows and never gets that refresh
    on its own, silently breaking manufacturer resolution (Search tab's
    Manufacturer filter, mfrBadge, etc.) for every airport except whichever
    one happens to be app.state.store. upsert_aircraft_type() itself already
    guards against a non-user row ever clobbering an existing 'user' one, so
    copying every row here is safe regardless of copy order."""
    if len(cfgs) < 2:
        return
    canonical = next(iter(cfgs.values())).store
    others = list(cfgs.values())[1:]
    with canonical._connect() as conn:
        infra_rows = {
            r["key"]: r["value"] for r in conn.execute(
                "SELECT key, value FROM settings WHERE user_id = 'controller' AND key IN (%s)"
                % ",".join("?" * len(_RECONCILE_INFRA_SETTINGS)),
                tuple(_RECONCILE_INFRA_SETTINGS),
            ).fetchall()
        }
        airport_rows = conn.execute(
            "SELECT iata, name, country_code FROM airports WHERE source = 'user'"
        ).fetchall()
        type_rows = conn.execute(
            "SELECT icao, name, source, manufacturer FROM aircraft_types"
        ).fetchall()
    type_rows_bulk = [(r["icao"], r["name"], r["source"], r["manufacturer"]) for r in type_rows]
    for cfg in others:
        for key, value in infra_rows.items():
            cfg.store.set_setting("controller", key, value)
        for r in airport_rows:
            cfg.store.upsert_airport(r["iata"], r["name"], r["country_code"], source='user')
        cfg.store.upsert_aircraft_types_bulk(type_rows_bulk)


def _backfill_pilot_filter_copies(cfgs: Dict[str, "AppConfig"], control_store) -> None:
    """One-time backfill for Pilots created before the exclusion/watchlist
    tables AND pilot-editable settings switched from live default-to-
    Controller-if-unset inheritance to a one-time copy at setup time —
    treats "now" as that setup moment for any (Pilot, airport) pair that
    doesn't yet have its own independent rows/values. Safe to re-run every
    boot: both copy_controller_*_to_owner() calls are no-ops per table/key
    once the Pilot has a row of their own, so this only ever fires once per
    (Pilot, airport, table-or-key) in practice."""
    for u in control_store.list_users():
        if u["role"] != "pilot":
            continue
        for iata in control_store.get_user_airports(u["user_id"]):
            cfg = cfgs.get(iata)
            if cfg:
                cfg.store.copy_controller_filters_to_owner(u["user_id"])
                cfg.store.copy_controller_settings_to_owner(
                    u["user_id"], PER_USER_GLOBAL_SETTINGS - {"SPECIAL_LIVERY_KEYWORDS"})
                seed_new_airport_prefill_settings(cfgs, u["user_id"], iata)


_lock_fds: Dict[str, object] = {}  # role -> open file handle, kept for the process lifetime


def acquire_single_instance_lock(data_dir: str, role: str) -> None:
    """Refuse to start if another process already holds the lock for this
    role in this data directory. Role-scoped (not one lock for the whole data
    dir) because the web server and monitor loop are now separate processes
    that legitimately run concurrently against the same data dir — this must
    only prevent TWO instances of the SAME role (e.g. two monitor processes),
    which is the actual failure mode it guards against: a stray/duplicate
    monitor process would run its own independent, empty
    cfg.military_rapid_tracking against the same SQLite files as the "real"
    one — each poll cycle would see the tracked registration as "new" and
    fragment one continuous military visit into many single-point
    flight_arrivals rows."""
    import fcntl
    global _lock_fds
    lock_path = os.path.join(data_dir, f".spotalert.{role}.lock")
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error(
            "Another SpotAlert '%s' process already holds the lock on %s — refusing to "
            "start a second instance of this role against the same data directory.",
            role, data_dir,
        )
        sys.exit(1)
    fd.write(str(os.getpid()))
    fd.flush()
    _lock_fds[role] = fd
