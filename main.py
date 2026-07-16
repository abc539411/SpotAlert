from __future__ import annotations

import logging
import logging.handlers
import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pytz
from environs import Env

from flightradar24api import FlightRadar24API
from store import SqliteStore
from lightroom import find_catalog

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

    # Military auto rapid-tracking — in-memory only, never persisted.
    # {registration: {"stationary_since_ts": int|None, "last_in_radius_ts": int, "arrival_id": int}}
    military_rapid_tracking: dict = field(repr=False, default_factory=dict)

    # Cancellation/diversion absence-streak tracking — in-memory only, never persisted.
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

    # Set once in main() — lets /api/force-check wake the monitor loop immediately
    # and reset its periodic timer, instead of running as a disconnected one-off task.
    check_now_event: object = field(repr=False, default=None)
    # Serializes run_monitor_rotation's scheduled arrivals check against a manual
    # force-check's own immediate one (see monitor_runner.py's module docstring) —
    # both trigger paths run the same cfg's check, never concurrently.
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


async def _backup_db(context) -> None:
    cfg: AppConfig = context.bot_data["cfg"]
    path = cfg.store.backup()
    log.info("DB backup saved: %s", path)


import asyncio
import uvicorn
from web import create_app, PER_USER_GLOBAL_SETTINGS, seed_new_airport_prefill_settings
from monitor_runner import (
    run_monitor_rotation as _run_monitor_rotation,
    run_force_check_listener as _run_force_check_listener,
    run_military_shared_loop as _run_military_shared_loop,
    run_spotting_reminder_loop as _run_spotting_reminder_loop,
)


async def _run_backup(control_store, cfgs: Dict[str, "AppConfig"]) -> None:
    """Backs up control.db plus every active airport's own DB file."""
    import system_status as _ss
    while True:
        await asyncio.sleep(86400)
        try:
            control_store_backup_path = control_store.db_path + ".bak"
            import shutil
            shutil.copy2(control_store.db_path, control_store_backup_path)
            log.info("Control DB backup saved: %s", control_store_backup_path)
            for cfg in cfgs.values():
                path = cfg.store.backup()
                log.info("DB backup saved: %s (%s)", path, cfg.airport_iata)
            _ss.record_task('db_backup', True)
        except Exception as _e:
            log.exception("DB backup failed")
            _ss.record_task('db_backup', False, str(_e))


def build_cfgs_for_watched_airports(fr_api, control_store, primary_store, data_dir: str,
                                     catalog=None) -> Dict[str, "AppConfig"]:
    """One AppConfig per active watched_airports row, each bound to its own
    SqliteStore (its own DB file) — the "one DB file per airport" architecture.
    First-ever boot (no watched_airports rows yet): registers the existing
    single-airport DB in place, in-memory, no data migration."""
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
# rather than independent per airport DB — kept in sync by hand with web.py's
# GLOBAL_INFRA_SETTINGS (these are Controller-only keys; web.py's PUT
# /api/settings fans future writes out to every airport, this just reconciles
# any drift accumulated before that write-through existed).
_GLOBAL_INFRA_SETTINGS = frozenset({
    "CHECK_INTERVAL_MINUTES", "FETCH_PAGES", "DEPARTURE_PATTERN_THRESHOLD",
    "MONITOR_CANCEL_GRACE_MINS", "MONITOR_DIVERTED_GRACE_MINS",
    "MONITOR_ABSENCE_CHECKS", "MONITOR_CONFIRM_CALL_CAP",
    "MILITARY_CHECK_INTERVAL_MINUTES", "MILITARY_RADIUS_NM",
    "MILITARY_MAX_ALT_FT", "MILITARY_RENOTIFY_HOURS", "LOGOSTREAM_API_KEY",
})


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
                % ",".join("?" * len(_GLOBAL_INFRA_SETTINGS)),
                tuple(_GLOBAL_INFRA_SETTINGS),
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


_lock_fd = None  # kept open for the process lifetime — the OS releases the flock on exit


def _acquire_single_instance_lock(data_dir: str) -> None:
    """Refuse to start if another process already holds the lock on this data
    directory. Without this, a stray/overlapping process (e.g. a manual `python
    main.py` left running over SSH, or a restart that doesn't fully wait for the
    old process to exit) would run its own independent, empty
    cfg.military_rapid_tracking against the same SQLite files as the "real"
    process — each poll cycle would see the tracked registration as "new" and
    fragment one continuous military visit into many single-point
    flight_arrivals rows, indistinguishable from the restart-fragmentation bug
    but without any restart actually happening."""
    import fcntl
    global _lock_fd
    lock_path = os.path.join(data_dir, ".spotalert.lock")
    _lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error(
            "Another SpotAlert process already holds the lock on %s — refusing to "
            "start a second instance against the same data directory.", data_dir
        )
        sys.exit(1)
    _lock_fd.write(str(os.getpid()))
    _lock_fd.flush()


def main() -> None:
    log_format = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(log_format)

    os.makedirs("logs", exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        "logs/spotalert.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(log_format)

    logging.basicConfig(level=logging.INFO, handlers=[stdout_handler, file_handler])

    data_dir = "data/"
    os.makedirs(data_dir, exist_ok=True)

    _acquire_single_instance_lock(data_dir)

    fr_api = FlightRadar24API()

    # Warm up cloudscraper session — hits the FR24 homepage so Cloudflare issues
    # a cf_clearance cookie before any API calls are made.
    try:
        from flightradar24api.request import _scraper
        _scraper.get("https://www.flightradar24.com/", timeout=10)
        log.info("Cloudflare warm-up complete")
    except Exception as _e:
        log.warning("Cloudflare warm-up failed (will retry on first API call): %s", _e)

    # primary_store is always the original data/spotalert.db — the first-ever watched
    # airport, kept at its existing path so an upgraded deployment needs zero data
    # migration. Additional airports each get their own SqliteStore(db_path) instead.
    primary_store = SqliteStore(os.path.join(data_dir, "spotalert.db"))
    primary_store.migrate_from_csv_folder(data_dir)

    import system_status as _ss
    _ss.init(primary_store)  # process-wide task/health status — bound to one store for now

    from control_store import ControlStore
    control_store = ControlStore(os.path.join(data_dir, "control.db"))

    catalog = find_catalog()
    cfgs = build_cfgs_for_watched_airports(fr_api, control_store, primary_store, data_dir, catalog)

    for cfg in cfgs.values():
        cfg.check_now_event = asyncio.Event()
        cfg.check_lock = asyncio.Lock()
        _backfilled = cfg.store.backfill_arrival_dates(cfg.airport_tz)
        if _backfilled:
            log.info("Backfilled arrival_date for %d flight_events rows (%s)", _backfilled, cfg.airport_iata)

    port = int(os.environ.get("WEB_PORT", "8088"))
    for cfg in cfgs.values():
        log.info("Monitoring %s (%s) — check every %ds", cfg.airport_name, cfg.airport_iata, cfg.check_interval)
    log.info("Web on :%d", port)

    web_app = create_app(cfgs, control_store=control_store, fr_api=fr_api, data_dir=data_dir)
    web_config = uvicorn.Config(web_app, host="0.0.0.0", port=port, log_level="warning",
                                timeout_graceful_shutdown=1)
    web_server = uvicorn.Server(web_config)

    async def _run_all():
        # The default executor (used by every asyncio.to_thread() call — the FR24/
        # adsb.fi/JetPhotos/Open-Meteo network fetches in monitor.py and military.py,
        # each blocking for real network I/O — AND by Starlette's StaticFiles for
        # every /static/* disk read) defaults to min(32, cpu_count()+4) workers. On
        # a low-core-count host that's easily exhausted by a handful of in-flight
        # airport checks running concurrently (each airport's check does several
        # sequential network calls), at which point serving a plain static file has
        # to queue behind them too — this is what made the whole web UI appear to
        # "freeze" during a check cycle even though the calls were already off the
        # event loop. A generously-sized dedicated pool keeps network I/O from ever
        # being able to starve static/file-serving threads.
        from concurrent.futures import ThreadPoolExecutor
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=48))

        tasks = [
            web_server.serve(), _run_backup(control_store, cfgs),
            _run_monitor_rotation(cfgs), _run_military_shared_loop(cfgs),
            _run_spotting_reminder_loop(cfgs),
        ]
        for cfg in cfgs.values():
            tasks.append(_run_force_check_listener(cfg))
        await asyncio.gather(*tasks)

    asyncio.run(_run_all())


if __name__ == "__main__":
    main()
