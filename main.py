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
from monitor import run_check
from military import check_military, MILITARY_RAPID_INTERVAL_SECS
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
    livery_interval_hours: int
    livery_days: List[str]
    livery_time_filter: str     # "" = always | "Daylight" = daylight only | "Off" = disabled

    # Rare Plane filter
    rare_plane_min_absence_days: int
    rare_plane_days: List[str]
    rare_plane_time_filter: str

    # Rego Watchlist filter
    rego_interval_hours: int
    rego_days: List[str]
    rego_time_filter: str

    # Type Watchlist filter
    type_interval_hours: int
    type_days: List[str]
    type_time_filter: str

    # Airline/Operator Watchlist filter
    airline_interval_hours: int
    airline_days: List[str]
    airline_time_filter: str

    # Seconds between each arrivals check; used by follow-up logic to detect missed flights
    check_interval: int
    reminder_hours: int  # hours before arrival to send a reminder; 0 = disabled


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
    route_type_min_days: int = 7           # min days of history before filter fires
    route_type_dominance_x: int = 3        # dominant type must be >= N× next type count
    route_type_lookback_days: int = 90     # observation window in days
    route_type_renotify_days: int = 30     # cooldown per (flight, type) pairing
    route_type_days: List[str] = field(default_factory=list)
    route_type_time_filter: str = ""

    # Approach alert (Rapid Mode only)
    approach_alert_mins: int = 30     # loaded from APPROACH_ALERT_MINS; 0 = disabled

    # Rapid mode — in-memory only, never persisted
    rapid_mode: bool = field(repr=False, default=False)
    rapid_mode_interval: int = 120    # seconds, loaded from RAPID_MODE_INTERVAL_MINS

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
    catalog: object = field(repr=False, default=None)

    # Set once in main() — lets /api/force-check wake the monitor loop immediately
    # and reset its periodic timer, instead of running as a disconnected one-off task.
    check_now_event: object = field(repr=False, default=None)

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


def _s(store: SqliteStore, key: str, default: str = "") -> str:
    """Return DB-saved value, or default if not set."""
    return store.load_setting(key) or default


def _si(store: SqliteStore, key: str, default: str = "0") -> int:
    return math.ceil(float(_s(store, key, default=default)))


def _sl(store: SqliteStore, key: str) -> list:
    raw = _s(store, key, default="")
    return [v.strip() for v in raw.split(",") if v.strip()] if raw else []


def build_config(fr_api: FlightRadar24API, store: SqliteStore, catalog=None) -> AppConfig:
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
        livery_interval_hours=_si(store, "SPECIAL_LIVERY_RENOTIFY_HOURS"),
        livery_days=_sl(store, "SPECIAL_LIVERY_ACTIVE_DAYS"),
        livery_time_filter=_s(store, "SPECIAL_LIVERY_ARRIVAL_WINDOW"),
        rare_plane_min_absence_days=_si(store, "RARE_PLANE_MIN_ABSENCE_DAYS", default="7"),
        rare_plane_days=_sl(store, "RARE_PLANE_ACTIVE_DAYS"),
        rare_plane_time_filter=_s(store, "RARE_PLANE_ARRIVAL_WINDOW"),
        rego_interval_hours=_si(store, "REGO_WATCHLIST_RENOTIFY_HOURS"),
        rego_days=_sl(store, "REGO_WATCHLIST_ACTIVE_DAYS"),
        rego_time_filter=_s(store, "REGO_WATCHLIST_ARRIVAL_WINDOW"),
        type_interval_hours=_si(store, "TYPE_WATCHLIST_RENOTIFY_HOURS"),
        type_days=_sl(store, "TYPE_WATCHLIST_ACTIVE_DAYS"),
        type_time_filter=_s(store, "TYPE_WATCHLIST_ARRIVAL_WINDOW"),
        airline_interval_hours=_si(store, "AIRLINE_WATCHLIST_RENOTIFY_HOURS"),
        airline_days=_sl(store, "AIRLINE_WATCHLIST_ACTIVE_DAYS"),
        airline_time_filter=_s(store, "AIRLINE_WATCHLIST_ARRIVAL_WINDOW"),
        check_interval=math.ceil(float(_s(store, "CHECK_INTERVAL_MINUTES", default="30")) * 60),
        reminder_hours=_si(store, "REMINDER_HOURS", default="12"),
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
        route_type_min_days=_si(store, "ROUTE_TYPE_MIN_DAYS", default="7"),
        route_type_dominance_x=_si(store, "ROUTE_TYPE_DOMINANCE_X", default="3"),
        route_type_lookback_days=_si(store, "ROUTE_TYPE_LOOKBACK_DAYS", default="90"),
        route_type_renotify_days=_si(store, "ROUTE_TYPE_RENOTIFY_DAYS", default="30"),
        route_type_days=_sl(store, "ROUTE_TYPE_ACTIVE_DAYS"),
        route_type_time_filter=_s(store, "ROUTE_TYPE_ARRIVAL_WINDOW"),
        approach_alert_mins=_si(store, "APPROACH_ALERT_MINS", default="30"),
        rapid_mode_interval=_si(store, "RAPID_MODE_INTERVAL_MINS", default="2") * 60,
        fr_api=fr_api,
        store=store,
        catalog=catalog,
    )


async def _backup_db(context) -> None:
    cfg: AppConfig = context.bot_data["cfg"]
    path = cfg.store.backup()
    log.info("DB backup saved: %s", path)


import asyncio
import uvicorn
from web import create_app


class _NoopBot:
    """Drop-in replacement for telegram.Bot — all sends are silent no-ops."""
    async def send_message(self, *a, **kw): pass
    async def send_photo(self, *a, **kw): pass


class _FakeJob:
    def __init__(self, data): self.data = data


class _FakeContext:
    """Minimal context stub so monitor.py / military.py run without Telegram."""
    def __init__(self, cfg):
        from datetime import datetime, timezone
        self.bot_data = {"cfg": cfg, "start_time": datetime.now(timezone.utc)}
        self.bot = _NoopBot()
        self.job = _FakeJob(cfg.chat_id)


async def _run_monitor(cfg: AppConfig) -> None:
    import system_status as _ss
    ctx = _FakeContext(cfg)
    event = cfg.check_now_event
    while True:
        # Wait for the interval to elapse, or for /api/force-check to wake us early.
        # Either way the timer resets from here — a manual check counts as "just ran".
        try:
            await asyncio.wait_for(event.wait(), timeout=cfg.check_interval)
        except asyncio.TimeoutError:
            pass
        event.clear()
        try:
            await run_check(ctx)
            _ss.record_task('arrivals_check', True)
        except Exception as _e:
            log.exception("Arrivals check failed")
            _ss.record_task('arrivals_check', False, str(_e))


async def _run_military(cfg: AppConfig) -> None:
    import system_status as _ss
    ctx = _FakeContext(cfg)
    while True:
        try:
            await check_military(ctx)
            _ss.record_task('military_check', True)
        except Exception as _e:
            log.exception("Military check failed")
            _ss.record_task('military_check', False, str(_e))
        interval = MILITARY_RAPID_INTERVAL_SECS if cfg.military_rapid_tracking else cfg.military_check_interval
        await asyncio.sleep(interval)


async def _run_backup(store) -> None:
    import system_status as _ss
    while True:
        await asyncio.sleep(86400)
        try:
            path = store.backup()
            log.info("DB backup saved: %s", path)
            _ss.record_task('db_backup', True)
        except Exception as _e:
            log.exception("DB backup failed")
            _ss.record_task('db_backup', False, str(_e))


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

    fr_api = FlightRadar24API()

    # Warm up cloudscraper session — hits the FR24 homepage so Cloudflare issues
    # a cf_clearance cookie before any API calls are made.
    try:
        from flightradar24api.request import _scraper
        _scraper.get("https://www.flightradar24.com/", timeout=10)
        log.info("Cloudflare warm-up complete")
    except Exception as _e:
        log.warning("Cloudflare warm-up failed (will retry on first API call): %s", _e)

    store = SqliteStore(os.path.join(data_dir, "spotalert.db"))
    store.migrate_from_csv_folder(data_dir)

    import system_status as _ss
    _ss.init(store)

    catalog = find_catalog()
    cfg = build_config(fr_api, store, catalog)
    cfg.check_now_event = asyncio.Event()

    _backfilled = store.backfill_arrival_dates(cfg.airport_tz)
    if _backfilled:
        log.info("Backfilled arrival_date for %d flight_events rows", _backfilled)

    port = int(os.environ.get("WEB_PORT", "8088"))
    log.info(
        "Monitoring %s (%s) — check every %ds, web on :%d",
        cfg.airport_name, cfg.airport_iata, cfg.check_interval, port,
    )

    web_app = create_app(cfg)
    web_config = uvicorn.Config(web_app, host="0.0.0.0", port=port, log_level="warning",
                                timeout_graceful_shutdown=1)
    web_server = uvicorn.Server(web_config)

    async def _run_all():
        await asyncio.gather(
            web_server.serve(),
            _run_monitor(cfg),
            _run_military(cfg),
            _run_backup(store),
        )

    asyncio.run(_run_all())


if __name__ == "__main__":
    main()
