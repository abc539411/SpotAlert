from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional, Tuple

import pytz
from astral import LocationInfo
from astral.sun import sun
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)

_HOURS = 3600
_DAYS  = 86400


# ------------------------------------------------------------------
# Flight status / time helpers
# ------------------------------------------------------------------

def get_flight_status(flight: dict) -> str:
    """Return a plain-English status string derived from FR24 timestamp fields."""
    try:
        real_dep = flight["time"]["real"]["departure"]
        real_arr = flight["time"]["real"]["arrival"]
        if real_arr is not None:
            return "Landed"
        if real_dep is None:
            return "On Ground"
        if int(real_dep) <= int(datetime.now().timestamp()):
            return "In Flight"
        return "Scheduled"
    except (KeyError, TypeError):
        return "N/A"


def get_arrival_period(flight: dict, tz_name: str, lat: float, lon: float) -> str:
    """Return 'Daylight Arrival' or 'Night-time Arrival' based on local sunrise/sunset."""
    try:
        estimated = flight["time"]["estimated"]["arrival"]
        scheduled = flight["time"]["scheduled"]["arrival"]
        arrival_ts = estimated if estimated is not None else scheduled
        if arrival_ts is None:
            return "N/A"

        parts = tz_name.split("/")
        location = LocationInfo(parts[-1], parts[0], tz_name, lat, lon)
        tz = pytz.timezone(tz_name)
        arrival_date = datetime.fromtimestamp(arrival_ts, tz).date()
        sun_info = sun(location.observer, date=arrival_date, tzinfo=location.timezone)

        dawn_ts = int(sun_info["dawn"].timestamp())
        dusk_ts = int(sun_info["dusk"].timestamp())
        return "Daylight Arrival" if dawn_ts < arrival_ts < dusk_ts else "Night-time Arrival"
    except Exception:
        return "N/A"


def get_next_departure(
    rego_details: Optional[dict], airport_iata: str, tz_name: str
) -> Tuple[Optional[datetime], Optional[str], Optional[str], Optional[str]]:
    """Find the next scheduled outbound flight for this aircraft from the monitored airport.

    Returns (departure_time, dest_name, dest_iata, dest_icao), or all None if not found.
    """
    if not rego_details or not rego_details.get("data"):
        return None, None, None, None

    for flight in rego_details["data"]:
        try:
            origin_iata = flight["airport"]["origin"]["code"]["iata"]
            already_departed = flight["time"]["real"]["departure"]
            if origin_iata == airport_iata and already_departed is None:
                scheduled_dep = flight["time"]["scheduled"]["departure"]
                departure_time = (
                    datetime.fromtimestamp(scheduled_dep).astimezone(pytz.timezone(tz_name))
                    if scheduled_dep else None
                )
                dest = flight["airport"]["destination"]
                return departure_time, dest.get("name"), dest["code"]["iata"], dest["code"]["icao"]
        except (KeyError, TypeError):
            continue

    return None, None, None, None


def _safe_get(d: dict, *keys, default="N/A"):
    """Walk a nested dict safely; return default on any missing key or None value."""
    for k in keys:
        try:
            d = d[k]
        except (KeyError, TypeError, IndexError):
            return default
    return d if d is not None else default


def format_notification(
    flight: dict,
    registration: str,
    notification_type: str,
    rego_details: Optional[dict],
    airport_iata: str,
    airport_tz: str,
    airport_lat: float,
    airport_lon: float,
) -> str:
    lines = [f"<b>{notification_type}</b>"]

    lines.append(f"  Flight: {_safe_get(flight, 'identification', 'number', 'default')}")

    origin = (flight.get("airport") or {}).get("origin") or {}
    origin_name = origin.get("name") or "N/A"
    origin_iata = _safe_get(origin, "code", "iata")
    origin_icao = _safe_get(origin, "code", "icao")
    lines.append(f"  Dep. Airport: {origin_name} ({origin_iata}/{origin_icao})")

    lines.append(f"  Status: {get_flight_status(flight)}")

    aircraft = (flight.get("aircraft") or {})
    lines.append(f"  Aircraft: {_safe_get(aircraft, 'model', 'text')} ({_safe_get(aircraft, 'model', 'code')})")
    lines.append(f"  Registration: {_safe_get(aircraft, 'registration')}")

    airline = (flight.get("airline") or {})
    airline_name = airline.get("name") or "N/A"
    airline_iata = _safe_get(airline, "code", "iata")
    airline_icao = _safe_get(airline, "code", "icao")
    lines.append(f"  Airline: {airline_name} ({airline_iata}/{airline_icao})")

    lines += ["", "<b>Arrival:</b>"]
    lines.append(f"  Period: {get_arrival_period(flight, airport_tz, airport_lat, airport_lon)}")

    tz = pytz.timezone(airport_tz)
    scheduled_arr_ts = _safe_get(flight, "time", "scheduled", "arrival", default=None)
    estimated_arr_ts = _safe_get(flight, "time", "estimated", "arrival", default=None)

    if isinstance(scheduled_arr_ts, (int, float)):
        lines.append(f"  Scheduled: {datetime.fromtimestamp(scheduled_arr_ts).astimezone(tz).strftime('%a %H:%M')} (Local)")
    else:
        lines.append("  Scheduled: N/A")

    if isinstance(estimated_arr_ts, (int, float)):
        lines.append(f"  Estimated: {datetime.fromtimestamp(estimated_arr_ts).astimezone(tz).strftime('%a %H:%M')} (Local)")
    else:
        lines.append("  Estimated: N/A")

    if rego_details:
        next_dep_time, dest_name, dest_iata, dest_icao = get_next_departure(
            rego_details, airport_iata, airport_tz
        )
        if next_dep_time:
            lines += ["", "<b>Next Departure:</b>"]
            lines.append(f"  Est. Dep: {next_dep_time.strftime('%a %H:%M')} (Local)")
            if dest_name:
                lines.append(f"  To: {dest_name} ({dest_iata}/{dest_icao})")

    flight_id  = _safe_get(flight, "identification", "id", default=None)
    flight_num = _safe_get(flight, "identification", "number", "default", default=None)
    if flight_id and flight_id != "N/A":
        lines.append(f"\nhttps://www.flightradar24.com/{flight_id}")
    elif flight_num and flight_num != "N/A":
        lines.append(f"\nhttps://www.flightradar24.com/data/flights/{flight_num}")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Filter helpers
# ------------------------------------------------------------------

def _scheduled_arrival_day(flight: dict, tz_name: str) -> Optional[str]:
    """Return the 3-letter weekday (e.g. 'Sat') of the scheduled arrival in local time."""
    try:
        ts = flight["time"]["scheduled"]["arrival"]
        if ts is None:
            return None
        return datetime.fromtimestamp(ts).astimezone(pytz.timezone(tz_name)).strftime("%a")
    except (KeyError, TypeError):
        return None


def _passes_schedule_filters(
    flight: dict,
    allowed_days: list,
    time_mode: str,
    tz_name: str,
    lat: float,
    lon: float,
) -> bool:
    """Return False if this flight should be skipped due to day-of-week or time-of-day filters.

    time_mode values: "" = no filter (always pass), "Daylight" = daylight arrivals only,
    "Off" = filter entirely disabled (never pass).
    """
    if allowed_days:
        day = _scheduled_arrival_day(flight, tz_name)
        if day is None or day not in allowed_days:
            return False
    if time_mode == "Off":
        return False
    if time_mode == "Daylight":
        if get_arrival_period(flight, tz_name, lat, lon) != "Daylight Arrival":
            return False
    return True


def _parse_aircraft(arriving_flight: dict) -> Optional[Tuple[str, str, dict]]:
    """Extract (registration, aircraft_type_code, flight_dict) from a raw arrivals entry."""
    try:
        aircraft = arriving_flight["flight"]["aircraft"]
        if aircraft is None:
            return None
        return aircraft["registration"], aircraft["model"]["code"], arriving_flight["flight"]
    except (KeyError, TypeError):
        return None


# ------------------------------------------------------------------
# Filter checks
#
# Each function returns (flight_dict, registration, on_notified) or None.
# on_notified is a zero-arg callable that writes the delivery timestamp to the DB
# and MUST only be called after the Telegram message is confirmed sent — this
# ensures a failed send leaves no false record of a notification being delivered.
# ------------------------------------------------------------------

def check_special_livery(arriving_flight: dict, cfg) -> Optional[Tuple[dict, str, callable]]:
    flight_data = arriving_flight.get("flight") or {}
    airline = flight_data.get("airline")
    if not airline:
        return None
    airline_name = airline.get("name") or ""

    parsed = _parse_aircraft(arriving_flight)
    if parsed is None:
        return None
    registration, _, flight = parsed

    if not _passes_schedule_filters(
        flight, cfg.livery_days, cfg.livery_time_filter,
        cfg.airport_tz, cfg.airport_lat, cfg.airport_lon,
    ):
        return None
    if not any(keyword in airline_name for keyword in cfg.livery_keywords):
        return None
    if cfg.store.is_excluded(registration):
        return None

    now_ts = int(datetime.now().timestamp())
    if cfg.store.should_notify_special_livery(registration, now_ts, cfg.livery_interval_hours):
        def on_notified(r=registration, t=now_ts):
            cfg.store.mark_special_livery_notified(r, t)
        return flight, registration, on_notified
    return None


def check_rare_plane(arriving_flight: dict, cfg) -> Optional[Tuple[dict, str, callable]]:
    flight_data = arriving_flight.get("flight") or {}

    # Never fire if this flight qualifies as a special livery — even if the livery
    # filter is on cooldown, it takes permanent precedence over rare plane.
    airline_name = (flight_data.get("airline") or {}).get("name") or ""
    if any(keyword in airline_name for keyword in cfg.livery_keywords):
        return None

    owner = flight_data.get("owner")
    if not owner:
        return None
    try:
        airline_icao = owner["code"]["icao"]
    except (KeyError, TypeError):
        return None

    parsed = _parse_aircraft(arriving_flight)
    if parsed is None:
        return None
    registration, aircraft_type, flight = parsed

    now_ts = int(datetime.now().timestamp())
    # Always record the sighting so frequent arrivals never cross the absence threshold.
    # Returns True only if the combo hasn't been seen for longer than min_absence_days.
    is_rare = cfg.store.update_rare_plane_seen(
        airline_icao, aircraft_type, now_ts, cfg.rare_plane_min_absence_days
    )

    if not _passes_schedule_filters(
        flight, cfg.rare_plane_days, cfg.rare_plane_time_filter,
        cfg.airport_tz, cfg.airport_lat, cfg.airport_lon,
    ):
        return None
    if cfg.store.is_excluded(registration):
        return None

    if is_rare:
        def on_notified(a=airline_icao, t=aircraft_type, ts=now_ts):
            cfg.store.mark_rare_plane_notified(a, t, ts)
        return flight, registration, on_notified
    return None


def check_rego_watchlist(arriving_flight: dict, cfg) -> Optional[Tuple[dict, str, callable]]:
    flight_data = arriving_flight.get("flight") or {}
    airline_name = (flight_data.get("airline") or {}).get("name") or ""
    if any(keyword in airline_name for keyword in cfg.livery_keywords):
        return None

    parsed = _parse_aircraft(arriving_flight)
    if parsed is None:
        return None
    registration, _, flight = parsed

    if not _passes_schedule_filters(
        flight, cfg.rego_days, cfg.rego_time_filter,
        cfg.airport_tz, cfg.airport_lat, cfg.airport_lon,
    ):
        return None
    if cfg.store.is_excluded(registration):
        return None

    now_ts = int(datetime.now().timestamp())
    if cfg.store.should_notify_rego_watchlist(registration, now_ts, cfg.rego_interval_hours):
        def on_notified(r=registration, t=now_ts):
            cfg.store.mark_rego_notified(r, t)
        return flight, registration, on_notified
    return None


def check_type_watchlist(arriving_flight: dict, cfg) -> Optional[Tuple[dict, str, callable]]:
    flight_data = arriving_flight.get("flight") or {}
    airline_name = (flight_data.get("airline") or {}).get("name") or ""
    if any(keyword in airline_name for keyword in cfg.livery_keywords):
        return None

    owner = flight_data.get("owner")
    if not owner:
        return None
    try:
        airline_icao = owner["code"]["icao"]
    except (KeyError, TypeError):
        return None

    parsed = _parse_aircraft(arriving_flight)
    if parsed is None:
        return None
    registration, aircraft_type, flight = parsed

    if not _passes_schedule_filters(
        flight, cfg.type_days, cfg.type_time_filter,
        cfg.airport_tz, cfg.airport_lat, cfg.airport_lon,
    ):
        return None
    if cfg.store.is_excluded(registration):
        return None

    now_ts = int(datetime.now().timestamp())
    if cfg.store.should_notify_type_watchlist(airline_icao, aircraft_type, now_ts, cfg.type_interval_hours):
        def on_notified(a=airline_icao, t=aircraft_type, ts=now_ts):
            cfg.store.mark_type_notified(a, t, ts)
        return flight, registration, on_notified
    return None


_FILTERS = [
    ("Special Livery",          check_special_livery),
    ("Rare Plane/Airline",      check_rare_plane),
    ("Watchlist Registration",  check_rego_watchlist),
    ("Watchlist Aircraft Type", check_type_watchlist),
]


def _first_matching_filter(
    arriving_flight: dict, cfg
) -> Optional[Tuple[dict, str, str, callable]]:
    """Run filters in priority order; stop at the first match.

    Returns (flight_dict, registration, notification_type, on_notified) or None.
    Stopping at the first match is important — later filters would write their own
    DB sentinels as side-effects even if their result is never used.
    """
    for notification_type, check_fn in _FILTERS:
        result = check_fn(arriving_flight, cfg)
        if result is not None:
            flight, registration, on_notified = result
            return flight, registration, notification_type, on_notified
    return None


# ------------------------------------------------------------------
# Periodic arrivals check
# ------------------------------------------------------------------

async def run_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    chat_id = context.job.data

    log.info("Checking arrivals at %s...", cfg.airport_iata)

    # Build a map of every registration currently visible in arrivals.
    # Used by check_follow_ups to detect cancellations/diversions.
    current_arrivals: dict = {}  # registration -> flight dict (first occurrence)

    try:
        for page in cfg.fetch_pages:
            try:
                data = cfg.fr_api.get_airport_details(code=cfg.airport_code, page=page)
                arrivals = data["airport"]["pluginData"]["schedule"]["arrivals"]["data"]
            except Exception as exc:
                log.warning("Failed to fetch arrivals (page %d): %s", page, exc)
                continue

            for arriving_flight in arrivals:
                parsed = _parse_aircraft(arriving_flight)
                if parsed:
                    registration, _, flight = parsed
                    if registration not in current_arrivals:
                        current_arrivals[registration] = flight

                match = _first_matching_filter(arriving_flight, cfg)
                if match is None:
                    continue
                flight, registration, notification_type, on_notified = match
                await _send_notification(
                    context, cfg, chat_id, flight, registration, notification_type, on_notified
                )

    except Exception as exc:
        log.error("Unexpected error in run_check: %s", exc, exc_info=True)

    await check_follow_ups(context, cfg, chat_id, current_arrivals)


async def _send_notification(
    context,
    cfg,
    chat_id: str,
    flight: dict,
    registration: str,
    notification_type: str,
    on_notified: callable,
) -> None:
    log.info("Notifying: %s — %s", notification_type, registration)

    rego_details = None
    photo_url = None
    try:
        rego_details = cfg.fr_api.get_rego_details(registration)
        images = (rego_details or {}).get("aircraftImages") or []
        if images:
            photo_url = images[0]["images"]["medium"][0]["link"]
    except Exception as exc:
        log.warning("Could not fetch aircraft details for %s: %s", registration, exc)

    message = format_notification(
        flight, registration, notification_type, rego_details,
        cfg.airport_iata, cfg.airport_tz, cfg.airport_lat, cfg.airport_lon,
    )

    now_ts = int(datetime.now().timestamp())
    try:
        if photo_url:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo_url,
                caption=f"Aircraft Photo: {registration}",
            )
        await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")

        # Write DB records only after confirmed Telegram delivery
        on_notified()
        arrival_ts = int(
            _safe_get(flight, "time", "estimated", "arrival", default=None)
            or _safe_get(flight, "time", "scheduled", "arrival", default=None)
            or 0
        )
        flight_number = str(_safe_get(flight, "identification", "number", "default", default=""))
        extra_info = ""
        if notification_type == "Special Livery":
            airline_name = (flight.get("airline") or {}).get("name") or ""
            match = re.search(r'\((.+?)\)', airline_name)
            extra_info = match.group(1) if match else airline_name
        cfg.store.record_notified_flight(
            registration, flight_number, notification_type, arrival_ts, now_ts, now_ts, extra_info
        )
    except Exception as exc:
        log.error("Failed to send notification for %s: %s", registration, exc, exc_info=True)


# ------------------------------------------------------------------
# Follow-up checks: 12hr arrival reminder + cancellation/diversion
# ------------------------------------------------------------------

async def check_follow_ups(context, cfg, chat_id: str, current_arrivals: dict) -> None:
    now_ts = int(datetime.now().timestamp())
    cfg.store.cleanup_arrived_flights(now_ts)

    for record in cfg.store.get_tracked_flights():
        registration    = record["registration"]
        flight_number   = record["flight_number"] or ""
        notification_type = record["notif_type"] or ""
        original_arr_ts = int(record["original_arr_ts"])   # arrival time at point of first notification
        arrival_ts      = int(record["arrival_ts"])         # latest estimated arrival (may shift)
        first_notified_ts = int(record["first_notified_ts"])
        reminder_sent   = bool(record["reminder_sent"])
        last_seen_ts    = int(record["last_seen_ts"])

        if registration in current_arrivals:
            current_flight = current_arrivals[registration]

            # Keep the estimated arrival time up to date as delays accumulate
            current_arrival_ts = int(
                _safe_get(current_flight, "time", "estimated", "arrival", default=None)
                or _safe_get(current_flight, "time", "scheduled", "arrival", default=None)
                or arrival_ts
            )
            cfg.store.update_tracked_flight(registration, now_ts, current_arrival_ts)

            # Send a 12-hour reminder only if:
            #   • we haven't already sent one
            #   • the flight is still in the future
            #   • it's now within 12h of arrival
            #   • the original schedule was 12h+ after the initial notification
            #     (no point reminding if you were notified when it was already close)
            if (not reminder_sent
                    and current_arrival_ts > now_ts
                    and (current_arrival_ts - now_ts) <= 12 * _HOURS
                    and (original_arr_ts - first_notified_ts) > 12 * _HOURS):
                await _send_arrival_reminder(
                    context, cfg, chat_id, current_flight, registration, notification_type
                )
                cfg.store.mark_reminder_sent(registration)

        else:
            # Flight is no longer in the arrivals board
            if arrival_ts > now_ts:
                # It's still expected in the future — wait for 2 full check cycles before
                # declaring it cancelled, to avoid false alarms from transient FR24 gaps
                if now_ts - last_seen_ts > 2 * cfg.check_interval:
                    await _send_cancellation_notice(
                        context, cfg, chat_id,
                        registration, flight_number, notification_type, arrival_ts,
                    )
                    cfg.store.delete_tracked_flight(registration)


async def _send_arrival_reminder(
    context,
    cfg,
    chat_id: str,
    flight: dict,
    registration: str,
    notification_type: str,
) -> None:
    now_ts = int(datetime.now().timestamp())
    arrival_ts = int(
        _safe_get(flight, "time", "estimated", "arrival", default=None)
        or _safe_get(flight, "time", "scheduled", "arrival", default=None)
        or 0
    )
    hours_away = round((arrival_ts - now_ts) / _HOURS, 1) if arrival_ts else "?"
    tz = pytz.timezone(cfg.airport_tz)
    arrival_str = (
        datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%a %H:%M")
        if arrival_ts else "N/A"
    )

    aircraft = flight.get("aircraft") or {}
    airline_name = (flight.get("airline") or {}).get("name", "N/A")
    lines = [
        f"<b>Arriving Soon — {notification_type}</b>",
        f"  Flight: {_safe_get(flight, 'identification', 'number', 'default')}",
        f"  Aircraft: {_safe_get(aircraft, 'model', 'text')} ({_safe_get(aircraft, 'model', 'code')})",
        f"  Registration: {registration}",
        f"  Airline: {airline_name}",
        f"  Arriving: {arrival_str} (Local) — in ~{hours_away}h",
    ]
    flight_id = _safe_get(flight, "identification", "id", default=None)
    if flight_id and flight_id != "N/A":
        lines.append(f"\nhttps://www.flightradar24.com/{flight_id}")

    try:
        await context.bot.send_message(
            chat_id=chat_id, text="\n".join(lines), parse_mode="HTML"
        )
        log.info("Sent arrival reminder for %s", registration)
    except Exception as exc:
        log.error("Failed to send arrival reminder for %s: %s", registration, exc)


async def _send_cancellation_notice(
    context,
    cfg,
    chat_id: str,
    registration: str,
    flight_number: str,
    notification_type: str,
    arrival_ts: int,
) -> None:
    tz = pytz.timezone(cfg.airport_tz)
    arrival_str = (
        datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%a %H:%M")
        if arrival_ts else "N/A"
    )
    message = (
        f"<b>No Longer Arriving — {notification_type}</b>\n"
        f"  Registration: {registration}\n"
        f"  Flight: {flight_number or 'N/A'}\n"
        f"  Was scheduled: {arrival_str} (Local)\n\n"
        "This flight has disappeared from the arrivals board — "
        "likely cancelled or diverted."
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")
        log.info("Sent cancellation notice for %s", registration)
    except Exception as exc:
        log.error("Failed to send cancellation notice for %s: %s", registration, exc)
