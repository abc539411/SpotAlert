from __future__ import annotations

import logging
import logging.handlers
import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import List

import pytz
from environs import Env
from telegram import BotCommand
from telegram.ext import Application

from flightradar24api import FlightRadar24API
from storage import SqliteStore
from monitor import run_check
from military import check_military
from bot import register_handlers
from settings import register_settings_handlers
from summary import register_summary_handlers
from lightroom import find_catalog
from lookup import register_lookup_handler
from stats import register_stats_handlers
from spot_recommendation import register_spot_rec_handlers, run_eod_recommendation

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

    # Summary command period definitions
    summary_morning_pre_sunrise_hours: int   # X: morning starts X hours before sunrise
    summary_morning_end_hour: int            # Y: morning ends at Y:00 local
    summary_afternoon_start_hour: int        # Z: afternoon starts at Z:00 local
    summary_afternoon_post_sunset_hours: int # M: afternoon ends M hours after sunset

    # Military filter (adsb.fi open data — no API key required)
    military_check_interval: int  # seconds between each military check
    military_radius_nm: int
    military_max_alt_ft: int
    military_renotify_hours: int

    # Spot recommendation
    spot_rec_enabled: bool = False
    spot_rec_day_type: str = "Any"       # "Any" or "WeekendPublicHoliday"
    spot_rec_travel_mins: int = 30
    spot_rec_session_hours: int = 5
    spot_rec_threshold: int = 3
    spot_rec_eod_hour: int = 20
    spot_rec_weather_gate: bool = True
    spot_rec_lighting_gate: bool = True
    spot_rec_max_spotted_times: int = 0   # 0 = disabled

    # Dependencies — excluded from repr/comparison
    fr_api: object = field(repr=False, default=None)
    store: object = field(repr=False, default=None)
    catalog: object = field(repr=False, default=None)


def _fetch_airport(fr_api: FlightRadar24API, code: str, retries: int = 3) -> dict:
    for attempt in range(1, retries + 1):
        try:
            data = fr_api.get_airport_details(code=code)
            details = data["airport"]["pluginData"]["details"]
            return {
                "name": details["name"],
                "iata": details["code"]["iata"],
                "icao": details["code"]["icao"],
                "tz":   details["timezone"]["name"],
                "lat":  details["position"]["latitude"],
                "lon":  details["position"]["longitude"],
            }
        except Exception as exc:
            log.warning("Airport fetch attempt %d/%d failed: %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(30)
    raise RuntimeError(f"Could not fetch airport info for '{code}' after {retries} attempts.")


def _s(store: SqliteStore, env: Env, key: str, default: str = "") -> str:
    """Return DB-saved value if one exists, otherwise fall back to config.env."""
    return store.load_setting(key) or env.str(key, default=default)


def _si(store: SqliteStore, env: Env, key: str, default: str = "0") -> int:
    return math.ceil(float(_s(store, env, key, default=default)))


def _sl(store: SqliteStore, env: Env, key: str) -> list:
    raw = _s(store, env, key, default="")
    return [v.strip() for v in raw.split(",") if v.strip()] if raw else []


def build_config(env: Env, fr_api: FlightRadar24API, store: SqliteStore, catalog=None) -> AppConfig:
    airport_code = _s(store, env, "AIRPORT_CODE")
    airport = _fetch_airport(fr_api, airport_code)

    arrivals_to_fetch = math.ceil(float(_s(store, env, "ARRIVALS_TO_FETCH", default="200")))
    fetch_pages = list(range(1, math.ceil(arrivals_to_fetch / 100) + 1))

    return AppConfig(
        airport_code=airport_code,
        airport_name=airport["name"],
        airport_iata=airport["iata"],
        airport_icao=airport["icao"],
        airport_tz=airport["tz"],
        airport_lat=airport["lat"],
        airport_lon=airport["lon"],
        fetch_pages=fetch_pages,
        chat_id=env.str("CHAT_ID"),
        livery_keywords=_sl(store, env, "SPECIAL_LIVERY_KEYWORDS"),
        livery_interval_hours=_si(store, env, "SPECIAL_LIVERY_RENOTIFY_HOURS"),
        livery_days=_sl(store, env, "SPECIAL_LIVERY_ACTIVE_DAYS"),
        livery_time_filter=_s(store, env, "SPECIAL_LIVERY_ARRIVAL_WINDOW"),
        rare_plane_min_absence_days=_si(store, env, "RARE_PLANE_MIN_ABSENCE_DAYS", default="7"),
        rare_plane_days=_sl(store, env, "RARE_PLANE_ACTIVE_DAYS"),
        rare_plane_time_filter=_s(store, env, "RARE_PLANE_ARRIVAL_WINDOW"),
        rego_interval_hours=_si(store, env, "REGO_WATCHLIST_RENOTIFY_HOURS"),
        rego_days=_sl(store, env, "REGO_WATCHLIST_ACTIVE_DAYS"),
        rego_time_filter=_s(store, env, "REGO_WATCHLIST_ARRIVAL_WINDOW"),
        type_interval_hours=_si(store, env, "TYPE_WATCHLIST_RENOTIFY_HOURS"),
        type_days=_sl(store, env, "TYPE_WATCHLIST_ACTIVE_DAYS"),
        type_time_filter=_s(store, env, "TYPE_WATCHLIST_ARRIVAL_WINDOW"),
        airline_interval_hours=_si(store, env, "AIRLINE_WATCHLIST_RENOTIFY_HOURS"),
        airline_days=_sl(store, env, "AIRLINE_WATCHLIST_ACTIVE_DAYS"),
        airline_time_filter=_s(store, env, "AIRLINE_WATCHLIST_ARRIVAL_WINDOW"),
        check_interval=math.ceil(float(_s(store, env, "CHECK_INTERVAL_MINUTES", default="30")) * 60),
        reminder_hours=_si(store, env, "REMINDER_HOURS", default="12"),
        summary_morning_pre_sunrise_hours=_si(store, env, "SUMMARY_MORNING_PRE_SUNRISE_HOURS", default="1"),
        summary_morning_end_hour=_si(store, env, "SUMMARY_MORNING_END_HOUR", default="12"),
        summary_afternoon_start_hour=_si(store, env, "SUMMARY_AFTERNOON_START_HOUR", default="12"),
        summary_afternoon_post_sunset_hours=_si(store, env, "SUMMARY_AFTERNOON_POST_SUNSET_HOURS", default="1"),
        military_check_interval=math.ceil(float(_s(store, env, "MILITARY_CHECK_INTERVAL_MINUTES", default="15")) * 60),
        military_radius_nm=_si(store, env, "MILITARY_RADIUS_NM", default="50"),
        military_max_alt_ft=_si(store, env, "MILITARY_MAX_ALT_FT", default="5000"),
        military_renotify_hours=_si(store, env, "MILITARY_RENOTIFY_HOURS", default="4"),
        spot_rec_enabled=_s(store, env, "SPOT_REC_ENABLED", default="false").lower() == "true",
        spot_rec_day_type=_s(store, env, "SPOT_REC_DAY_TYPE", default="Any"),
        spot_rec_travel_mins=_si(store, env, "SPOT_REC_TRAVEL_MINS", default="30"),
        spot_rec_session_hours=_si(store, env, "SPOT_REC_SESSION_HOURS", default="5"),
        spot_rec_threshold=_si(store, env, "SPOT_REC_THRESHOLD", default="3"),
        spot_rec_eod_hour=_si(store, env, "SPOT_REC_EOD_HOUR", default="20"),
        spot_rec_weather_gate=_s(store, env, "SPOT_REC_WEATHER_GATE", default="true").lower() == "true",
        spot_rec_lighting_gate=_s(store, env, "SPOT_REC_LIGHTING_GATE", default="true").lower() == "true",
        spot_rec_max_spotted_times=_si(store, env, "SPOT_REC_MAX_SPOTTED_TIMES", default="0"),
        fr_api=fr_api,
        store=store,
        catalog=catalog,
    )


async def _backup_db(context) -> None:
    cfg: AppConfig = context.bot_data["cfg"]
    path = cfg.store.backup()
    log.info("DB backup saved: %s", path)


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

    config_file = "config/config.env"
    if not os.path.isfile(config_file):
        log.error("Config file not found: %s", config_file)
        sys.exit(1)

    env = Env()
    env.read_env(config_file)

    filters_dir = "config/filters/"
    os.makedirs(filters_dir, exist_ok=True)

    fr_api = FlightRadar24API()
    store = SqliteStore(os.path.join(filters_dir, "spotalert.db"), config_file=config_file)
    store.migrate_from_csv_folder(filters_dir)

    catalog = find_catalog()
    cfg = build_config(env, fr_api, store, catalog)
    log.info(
        "Monitoring %s (%s) — checking every %s min",
        cfg.airport_name, cfg.airport_iata,
        env.str("CHECK_INTERVAL_MINUTES"),
    )

    async def _set_commands(application: Application) -> None:
        await application.bot.set_my_commands([
            BotCommand("spot",     "Check if recommended to go spotting today or tomorrow"),
            BotCommand("summary",  "View notified flights by day & period"),
            BotCommand("stats",    "View spotting stats and notification totals"),
            BotCommand("filters",  "Manage watchlists & exclusion list"),
            BotCommand("settings", "Configure filter intervals, days & windows"),
            BotCommand("status",   "Show bot status and next check times"),
        ])

    token = env.str("TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).post_init(_set_commands).build()
    from datetime import datetime, timezone
    app.bot_data["cfg"] = cfg
    app.bot_data["start_time"] = datetime.now(timezone.utc)

    register_handlers(app)
    register_settings_handlers(app)
    register_summary_handlers(app)
    register_lookup_handler(app)
    register_stats_handlers(app)
    register_spot_rec_handlers(app)
    # Named so settings.py can reschedule it when the interval changes
    app.job_queue.run_repeating(
        run_check, interval=cfg.check_interval, first=10,
        data=cfg.chat_id, name="arrivals_check",
    )
    app.job_queue.run_repeating(
        check_military, interval=cfg.military_check_interval, first=15,
        name="military_check",
    )
    app.job_queue.run_repeating(
        _backup_db, interval=86400, first=60,
        name="daily_backup",
    )
    if cfg.spot_rec_enabled:
        import datetime as _dt
        eod_time = _dt.time(
            hour=cfg.spot_rec_eod_hour, minute=0,
            tzinfo=pytz.timezone(cfg.airport_tz),
        )
        app.job_queue.run_daily(run_eod_recommendation, time=eod_time, name="eod_rec")
        log.info("Spot recommendation enabled — EOD check at %02d:00 local", cfg.spot_rec_eod_hour)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
