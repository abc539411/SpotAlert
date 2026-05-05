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


def _get_fr24_status(flight: dict) -> tuple:
    """Return (status_text, diverted_airport) from the FR24 status field.

    status_text: 'canceled', 'diverted', or '' if unknown.
    diverted_airport: IATA code (e.g. 'NBO') or '' for non-diversions.
    """
    try:
        generic = flight["status"]["generic"]["status"]
        text = (generic.get("text") or "").lower()
        diverted = (generic.get("diverted") or "").upper()
        return text, diverted
    except (KeyError, TypeError):
        return "", ""


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


def _extract_dep_fields(fl: dict, tz_name: str) -> tuple:
    """Extract departure fields from a rego_details flight entry.

    Returns (dep_time, dep_fn, airline_name, airline_iata, airline_icao, dest_name, dest_iata, dest_icao, time_label).
    """
    dep_ts, label = _best_time(fl, "departure")
    dep_time = (
        datetime.fromtimestamp(dep_ts).astimezone(pytz.timezone(tz_name))
        if dep_ts else None
    )
    dep_fn = ((fl.get("identification") or {}).get("number") or {}).get("default")
    airline = fl.get("airline") or {}
    airline_name = airline.get("name")
    airline_code = airline.get("code") or {}
    airline_iata = airline_code.get("iata")
    airline_icao = airline_code.get("icao")
    dest = fl["airport"]["destination"]
    return dep_time, dep_fn, airline_name, airline_iata, airline_icao, dest.get("name"), dest["code"]["iata"], dest["code"]["icao"], label


def get_next_departure(rego_details: Optional[dict], airport_iata: str, tz_name: str) -> tuple:
    """Find the next outbound flight for this aircraft from the monitored airport.

    Returns (dep_time, dep_fn, airline_name, airline_iata, airline_icao, dest_name, dest_iata, dest_icao, time_label).
    All values may be None if no upcoming departure is found.
    """
    _empty = (None,) * 8 + ("",)
    if not rego_details or not rego_details.get("data"):
        return _empty

    for flight in rego_details["data"]:
        try:
            origin_iata = flight["airport"]["origin"]["code"]["iata"]
            already_departed = flight["time"]["real"]["departure"]
            if origin_iata == airport_iata and already_departed is None:
                return _extract_dep_fields(flight, tz_name)
        except (KeyError, TypeError):
            continue

    return _empty


def _lookup_flight_by_number(rego_details: Optional[dict], flight_number: str, tz_name: str) -> tuple:
    """Search rego_details for a flight matching flight_number.

    Returns (dep_time, dep_fn, airline_name, airline_iata, airline_icao, dest_name, dest_iata, dest_icao, time_label).
    """
    _empty = (None,) * 8 + ("",)
    if not rego_details or not rego_details.get("data"):
        return _empty
    for fl in rego_details["data"]:
        try:
            fn = ((fl.get("identification") or {}).get("number") or {}).get("default")
            if fn != flight_number:
                continue
            return _extract_dep_fields(fl, tz_name)
        except (KeyError, TypeError):
            continue
    return _empty


def _best_time(flight: dict, kind: str) -> Tuple[Optional[float], str]:
    """Return (timestamp, label) using the best available time for 'arrival' or 'departure'.

    Priority: real → estimated → scheduled.
    Returns (None, '') if no time is available.
    """
    times = (flight.get("time") or {})
    for src, label in (("real", "Actual"), ("estimated", "Estimated"), ("scheduled", "Scheduled")):
        ts = (times.get(src) or {}).get(kind)
        if isinstance(ts, (int, float)):
            return ts, label
    return None, ""


def _get_scheduled_dep_ts(rego_details: Optional[dict], airport_iata: str, dep_fn: str) -> Optional[int]:
    """Extract the scheduled departure time for dep_fn departing from airport_iata."""
    for fl in (rego_details or {}).get("data") or []:
        try:
            fn = ((fl.get("identification") or {}).get("number") or {}).get("default")
            origin = fl["airport"]["origin"]["code"]["iata"]
            if fn == dep_fn and origin == airport_iata:
                ts = (fl.get("time") or {}).get("scheduled", {}).get("departure")
                if isinstance(ts, (int, float)):
                    return int(ts)
        except (KeyError, TypeError):
            continue
    return None


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
    catalog=None,
    cfg_store=None,
    dep_pattern_threshold: int = 0,
    fr_api=None,
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

    airline = (flight.get("airline") or {})
    airline_name = airline.get("name") or "N/A"
    airline_iata = _safe_get(airline, "code", "iata")
    airline_icao = _safe_get(airline, "code", "icao")
    lines.append(f"  Airline: {airline_name} ({airline_iata}/{airline_icao})")

    lines.append(f"  Registration: {_safe_get(aircraft, 'registration')}")
    lines.append("")

    if catalog is not None:
        spotted = catalog.get_last_spotted(registration)
        if spotted:
            dt, apt, count = spotted
            apt_str = f" at {apt}" if apt else ""
            times_str = f"({count} time{'s' if count != 1 else ''})"
            lines.append(f"  Last Spotted: {dt.strftime('%d %b %Y')}{apt_str} {times_str}")
        else:
            lines.append("  Last Spotted: Not yet photographed")

    last_seen_ts = cfg_store.get_last_seen(registration) if cfg_store is not None else None
    if last_seen_ts:
        now_ts = int(datetime.now().timestamp())
        days_ago = (now_ts - last_seen_ts) // 86400
        tz = pytz.timezone(airport_tz)
        seen_date = datetime.fromtimestamp(last_seen_ts).astimezone(tz).strftime("%d %b %Y")
        if days_ago == 0:
            seen_str = f"{seen_date} (today)"
        elif days_ago == 1:
            seen_str = f"{seen_date} (yesterday)"
        elif days_ago <= 7:
            seen_str = f"{seen_date} ({days_ago} days ago)"
        else:
            seen_str = seen_date
        lines.append(f"  Last Seen at {airport_iata}: {seen_str}")

    lines += ["", "<b>Arrival:</b>"]
    lines.append(f"  Period: {get_arrival_period(flight, airport_tz, airport_lat, airport_lon)}")

    tz = pytz.timezone(airport_tz)
    arr_ts, arr_label = _best_time(flight, "arrival")
    if arr_ts:
        lines.append(f"  {arr_label}: {datetime.fromtimestamp(arr_ts).astimezone(tz).strftime('%a %H:%M')} (Local)")
    else:
        lines.append("  Arrival: N/A")

    if rego_details:
        dep_time, dep_fn, al_name, al_iata, al_icao, dest_name, dest_iata, dest_icao, dep_label = get_next_departure(
            rego_details, airport_iata, airport_tz
        )
        if dep_time:
            lines += ["", "<b>Next Departure:</b>"]
            lines.append(f"  {dep_label}: {dep_time.strftime('%a %H:%M')} (Local) — {dep_fn}")
            if dest_name:
                lines.append(f"  To: {dest_name} ({dest_iata}/{dest_icao})")
        elif cfg_store is not None and dep_pattern_threshold > 0:
            arrival_fn = str(_safe_get(flight, "identification", "number", "default", default=""))
            if arrival_fn and arrival_fn != "N/A":
                predicted = cfg_store.get_predicted_departure(
                    arrival_fn, airport_iata, dep_pattern_threshold
                )
                if predicted:
                    pred_fn, confidence, _, _ = predicted
                    # Step 1: all details from our own DB
                    dep_info = cfg_store.get_predicted_dep_info(pred_fn, airport_iata)
                    sched_ts  = dep_info.get("scheduled_dep_ts") if dep_info else None
                    al_name   = dep_info.get("airline_name")     if dep_info else None
                    al_iata   = dep_info.get("airline_iata")     if dep_info else None
                    al_icao   = dep_info.get("airline_icao")     if dep_info else None
                    dest_name = dep_info.get("dest_name")        if dep_info else None
                    dest_iata = dep_info.get("dest_iata")        if dep_info else None
                    dest_icao = dep_info.get("dest_icao")        if dep_info else None
                    # Step 2: fill any missing fields from FR24 by flight number
                    if fr_api is not None and (sched_ts is None or not al_name or not dest_name):
                        try:
                            fl_data = fr_api.get_flight_by_number(pred_fn)
                            if sched_ts is None:
                                sched_ts = _get_scheduled_dep_ts(fl_data, airport_iata, pred_fn)
                            if not al_name or not dest_name:
                                _, _, al_name2, al_iata2, al_icao2, dest_name2, dest_iata2, dest_icao2, _ = _lookup_flight_by_number(
                                    fl_data, pred_fn, airport_tz
                                )
                                al_name   = al_name   or al_name2
                                al_iata   = al_iata   or al_iata2
                                al_icao   = al_icao   or al_icao2
                                dest_name = dest_name or dest_name2
                                dest_iata = dest_iata or dest_iata2
                                dest_icao = dest_icao or dest_icao2
                        except Exception:
                            pass
                    tz = pytz.timezone(airport_tz)
                    lines += ["", "<b>Next Departure:</b>"]
                    if sched_ts:
                        dep_time = datetime.fromtimestamp(sched_ts).astimezone(tz)
                        lines.append(f"  Predicted: {dep_time.strftime('%a %H:%M')} (Local) — {pred_fn}")
                    else:
                        lines.append(f"  Predicted: {pred_fn}")
                    if dest_name:
                        lines.append(f"  To: {dest_name} ({dest_iata}/{dest_icao})")
                    lines.append(f"  Confidence: {confidence:.0f}%")

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


def check_airline_watchlist(arriving_flight: dict, cfg) -> Optional[Tuple]:
    flight_data = arriving_flight.get("flight") or {}

    airline_name = (flight_data.get("airline") or {}).get("name") or ""
    if any(keyword in airline_name for keyword in cfg.livery_keywords):
        return None

    parsed = _parse_aircraft(arriving_flight)
    if parsed is None:
        return None
    registration, _, flight = parsed

    if not _passes_schedule_filters(
        flight, cfg.airline_days, cfg.airline_time_filter,
        cfg.airport_tz, cfg.airport_lat, cfg.airport_lon,
    ):
        return None
    if cfg.store.is_excluded(registration):
        return None

    now_ts = int(datetime.now().timestamp())

    airline_icao = _safe_get(flight_data, "airline", "code", "icao", default="")
    if airline_icao and airline_icao != "N/A":
        if cfg.store.should_notify_airline_watchlist(airline_icao, "airline", now_ts, cfg.airline_interval_hours):
            def on_notified(code=airline_icao, t=now_ts):
                cfg.store.mark_airline_notified(code, "airline", t)
            return flight, registration, on_notified, "Watchlist Airline"

    owner_icao = _safe_get(flight_data, "owner", "code", "icao", default="")
    if owner_icao and owner_icao != "N/A":
        if cfg.store.should_notify_airline_watchlist(owner_icao, "operator", now_ts, cfg.airline_interval_hours):
            def on_notified(code=owner_icao, t=now_ts):
                cfg.store.mark_airline_notified(code, "operator", t)
            return flight, registration, on_notified, "Watchlist Operator"

    return None


_FILTERS = [
    ("Special Livery",          check_special_livery),
    ("Watchlist Registration",  check_rego_watchlist),
    ("Watchlist Aircraft Type", check_type_watchlist),
    ("Watchlist Airline",       check_airline_watchlist),
    ("Rare Plane/Airline",      check_rare_plane),
]


def _first_matching_filter(
    arriving_flight: dict, cfg
) -> Optional[Tuple[dict, str, str, callable]]:
    """Run filters in priority order; stop at the first match.

    Returns (flight_dict, registration, notification_type, on_notified) or None.
    check_airline_watchlist returns an optional 4th element to override the type label.
    """
    for notification_type, check_fn in _FILTERS:
        result = check_fn(arriving_flight, cfg)
        if result is not None:
            flight, registration, on_notified = result[0], result[1], result[2]
            override_type = result[3] if len(result) > 3 else notification_type
            return flight, registration, override_type, on_notified
    return None


# ------------------------------------------------------------------
# Periodic arrivals check
# ------------------------------------------------------------------

async def run_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    chat_id = context.job.data

    log.info("Checking arrivals at %s...", cfg.airport_iata)

    # Build maps of currently visible arrivals and departures for follow-up checks.
    current_arrivals: dict = {}             # registration  → flight dict
    arrivals_by_flight_number: dict = {}    # flight_number → (registration, flight dict)
    current_departures: dict = {}           # registration  → departure flight dict

    try:
        # Pass 1: collect all arrivals and departures from every page
        all_arriving_flights = []
        for page in cfg.fetch_pages:
            try:
                data = cfg.fr_api.get_airport_details(code=cfg.airport_code, page=page)
                schedule = data["airport"]["pluginData"]["schedule"]
                arrivals   = schedule["arrivals"]["data"]
                departures = schedule.get("departures", {}).get("data") or []
            except Exception as exc:
                log.warning("Failed to fetch arrivals (page %d): %s", page, exc)
                continue

            for arriving_flight in arrivals:
                parsed = _parse_aircraft(arriving_flight)
                if parsed:
                    registration, _, flight = parsed
                    if registration not in current_arrivals:
                        current_arrivals[registration] = flight
                    fn = str(_safe_get(flight, "identification", "number", "default", default=""))
                    if fn and fn not in arrivals_by_flight_number:
                        arrivals_by_flight_number[fn] = (registration, flight)
                all_arriving_flights.append(arriving_flight)

            for dep_flight in departures:
                parsed = _parse_aircraft(dep_flight)
                if parsed:
                    registration, _, flight = parsed
                    if registration not in current_departures:
                        current_departures[registration] = flight

        # Record actual arrivals — only planes that have landed, using their real arrival time
        landed = {
            reg: int(_safe_get(flight, "time", "real", "arrival"))
            for reg, flight in current_arrivals.items()
            if isinstance(_safe_get(flight, "time", "real", "arrival", default=None), (int, float))
        }
        if landed:
            cfg.store.bulk_update_sightings(landed)

        # Pass 2: run filters and send notifications
        import asyncio
        for arriving_flight in all_arriving_flights:
            match = _first_matching_filter(arriving_flight, cfg)
            if match is None:
                continue
            flight, registration, notification_type, on_notified = match
            await _send_notification(
                context, cfg, chat_id, flight, registration, notification_type, on_notified
            )
            await asyncio.sleep(1)  # avoid Telegram rate limits when sending many at once

    except Exception as exc:
        log.error("Unexpected error in run_check: %s", exc, exc_info=True)

    await check_follow_ups(context, cfg, chat_id, current_arrivals, arrivals_by_flight_number, current_departures)

    from spot_recommendation import check_rolling_recommendation
    await check_rolling_recommendation(context, cfg, chat_id)


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
        catalog=cfg.catalog,
        cfg_store=cfg.store,
        dep_pattern_threshold=cfg.departure_pattern_threshold,
        fr_api=cfg.fr_api,
    )

    now_ts = int(datetime.now().timestamp())

    # Record departure pattern for future predictions
    arrival_fn = str(_safe_get(flight, "identification", "number", "default", default=""))
    if arrival_fn and arrival_fn != "N/A" and rego_details:
        _, dep_fn, al_name, al_iata, al_icao, dest_name, dest_iata, dest_icao, _ = get_next_departure(
            rego_details, cfg.airport_iata, cfg.airport_tz
        )
        if dep_fn:
            sched_dep_ts = _get_scheduled_dep_ts(rego_details, cfg.airport_iata, dep_fn)
            # Also capture estimated departure time if available
            estimated_dep_ts = None
            for fl in (rego_details or {}).get("data") or []:
                try:
                    fn = ((fl.get("identification") or {}).get("number") or {}).get("default")
                    origin = fl["airport"]["origin"]["code"]["iata"]
                    if fn == dep_fn and origin == cfg.airport_iata:
                        est = (fl.get("time") or {}).get("estimated", {}).get("departure")
                        if isinstance(est, (int, float)):
                            estimated_dep_ts = int(est)
                except (KeyError, TypeError):
                    continue
            cfg.store.record_departure_pattern(
                arrival_fn, dep_fn, cfg.airport_iata, now_ts,
                scheduled_dep_ts=sched_dep_ts,
                estimated_dep_ts=estimated_dep_ts,
                airline_name=al_name, airline_iata=al_iata, airline_icao=al_icao,
                dest_name=dest_name, dest_iata=dest_iata, dest_icao=dest_icao,
            )
    try:
        if photo_url:
            try:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_url,
                    caption=f"Aircraft Photo: {registration}",
                )
            except Exception as exc:
                log.warning("Could not send photo for %s: %s", registration, exc)

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

        airline_raw   = (flight.get("airline") or {}).get("name") or ""
        aircraft_code = _safe_get(flight, "aircraft", "model", "code", default="")
        clean_airline = re.sub(r'\s*\(.*?\)', '', airline_raw).strip()
        if clean_airline and aircraft_code:
            detail = f"{clean_airline} ({aircraft_code})"
        else:
            detail = clean_airline or aircraft_code

        cfg.store.record_notified_flight(
            registration, flight_number, notification_type, arrival_ts, now_ts, now_ts, extra_info, detail
        )
    except Exception as exc:
        log.error("Failed to send notification for %s: %s", registration, exc, exc_info=True)


# ------------------------------------------------------------------
# Follow-up checks: 12hr arrival reminder + cancellation/diversion
# ------------------------------------------------------------------

async def check_follow_ups(context, cfg, chat_id: str, current_arrivals: dict,
                           arrivals_by_flight_number: dict = None,
                           current_departures: dict = None) -> None:
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

            # Refresh departure timestamps from current departures schedule
            if current_departures and registration in current_departures and flight_number:
                dep_flight = current_departures[registration]
                dep_fn = str(_safe_get(dep_flight, "identification", "number", "default", default=""))
                if dep_fn and dep_fn != "N/A":
                    estimated_dep_ts = _safe_get(dep_flight, "time", "estimated", "departure", default=None)
                    scheduled_dep_ts = _safe_get(dep_flight, "time", "scheduled", "departure", default=None)
                    estimated_dep_ts = int(estimated_dep_ts) if isinstance(estimated_dep_ts, (int, float)) else None
                    scheduled_dep_ts = int(scheduled_dep_ts) if isinstance(scheduled_dep_ts, (int, float)) else None
                    cfg.store.update_departure_timestamps(
                        flight_number, dep_fn, cfg.airport_iata, estimated_dep_ts, scheduled_dep_ts
                    )

            # Check FR24 status for confirmed cancellation or diversion
            status_text, diverted_airport = _get_fr24_status(current_flight)
            if status_text == "canceled":
                await _send_cancellation_notice(
                    context, cfg, chat_id, registration, flight_number, notification_type, arrival_ts
                )
                cfg.store.delete_tracked_flight(registration)
                continue
            elif status_text == "diverted":
                await _send_diversion_notice(
                    context, cfg, chat_id, registration, flight_number, notification_type, arrival_ts, diverted_airport
                )
                cfg.store.delete_tracked_flight(registration)
                continue

            # Send a 12-hour reminder only if:
            #   • we haven't already sent one
            #   • the flight is still in the future
            #   • it's now within 12h of arrival
            #   • the original schedule was 12h+ after the initial notification
            #     (no point reminding if you were notified when it was already close)
            if (not reminder_sent
                    and cfg.reminder_hours > 0
                    and current_arrival_ts > now_ts
                    and (current_arrival_ts - now_ts) <= cfg.reminder_hours * _HOURS
                    and (original_arr_ts - first_notified_ts) > cfg.reminder_hours * _HOURS):
                await _send_arrival_reminder(
                    context, cfg, chat_id, current_flight, registration, notification_type, flight_number
                )
                cfg.store.mark_reminder_sent(registration)

        else:
            # Flight is no longer in the arrivals board
            if arrival_ts > now_ts:
                # Wait for 2 full check cycles to rule out transient FR24 gaps
                if now_ts - last_seen_ts > 2 * cfg.check_interval:
                    # Check if the flight number reappeared under a different registration
                    # on the same day — ignores tomorrow's scheduled flights with the same number
                    swap = arrivals_by_flight_number.get(flight_number) if arrivals_by_flight_number and flight_number else None
                    if swap and swap[0] != registration:
                        tz = pytz.timezone(cfg.airport_tz)
                        orig_date = datetime.fromtimestamp(arrival_ts, tz).date()
                        new_arr_ts = _safe_get(swap[1], "time", "scheduled", "arrival", default=None) \
                                     or _safe_get(swap[1], "time", "estimated", "arrival", default=None)
                        swap_date = datetime.fromtimestamp(new_arr_ts, tz).date() if isinstance(new_arr_ts, (int, float)) else None
                        if swap_date != orig_date:
                            swap = None
                    if swap and swap[0] != registration:
                        new_rego, new_flight = swap
                        await _send_aircraft_swap_notice(
                            context, cfg, chat_id,
                            registration, new_rego, new_flight,
                            flight_number, notification_type, arrival_ts,
                        )
                    else:
                        await _send_disappeared_notice(
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
    flight_number: str = "",
) -> None:
    now_ts = int(datetime.now().timestamp())
    arr_ts, arr_label = _best_time(flight, "arrival")
    arrival_ts = int(arr_ts) if arr_ts else 0
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
        f"  {arr_label}: {arrival_str} (Local) — in ~{hours_away}h",
    ]

    # Next departure — DB lookup: estimated → scheduled → predicted
    if flight_number and cfg.departure_pattern_threshold > 0:
        predicted = cfg.store.get_predicted_departure(flight_number, cfg.airport_iata, cfg.departure_pattern_threshold)
        if predicted:
            dep_fn, confidence, _, _ = predicted
            dep_info = cfg.store.get_predicted_dep_info(dep_fn, cfg.airport_iata)
            estimated_ts = dep_info.get("estimated_dep_ts") if dep_info else None
            sched_ts     = dep_info.get("scheduled_dep_ts") if dep_info else None
            dest_name    = dep_info.get("dest_name") if dep_info else None
            dest_iata    = dep_info.get("dest_iata") if dep_info else None
            dest_icao    = dep_info.get("dest_icao") if dep_info else None
            lines.append("")
            lines.append("<b>Next Departure:</b>")
            if estimated_ts:
                dep_str = datetime.fromtimestamp(estimated_ts).astimezone(tz).strftime("%a %H:%M")
                lines.append(f"  Estimated: {dep_str} (Local) — {dep_fn}")
            elif sched_ts:
                dep_str = datetime.fromtimestamp(sched_ts).astimezone(tz).strftime("%a %H:%M")
                lines.append(f"  Scheduled: {dep_str} (Local) — {dep_fn}")
            else:
                lines.append(f"  Predicted: {dep_fn}")
            if dest_name:
                lines.append(f"  To: {dest_name} ({dest_iata}/{dest_icao})")
            if not estimated_ts and not sched_ts:
                lines.append(f"  Confidence: {confidence:.0f}%")

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


def _classify_new_aircraft(flight: dict, registration: str, cfg) -> Optional[str]:
    """Read-only filter check on the new aircraft. Returns a label if interesting, else None."""
    airline_name = (flight.get("airline") or {}).get("name") or ""

    if any(kw in airline_name for kw in cfg.livery_keywords):
        match = re.search(r'\((.+?)\)', airline_name)
        livery = match.group(1) if match else airline_name
        return f"Special Livery — {livery}"

    if cfg.store.is_on_rego_watchlist(registration):
        return "Watchlist Registration"

    try:
        owner = flight.get("owner") or {}
        airline_icao = owner["code"]["icao"]
        aircraft_type = _safe_get(flight, "aircraft", "model", "code", default="")
        if aircraft_type and cfg.store.is_on_type_watchlist(airline_icao, aircraft_type):
            return "Watchlist Aircraft Type"
    except (KeyError, TypeError):
        pass

    al_icao = _safe_get(flight.get("airline") or {}, "code", "icao", default="")
    if al_icao and al_icao != "N/A" and cfg.store.is_on_airline_watchlist(al_icao, "airline"):
        return "Watchlist Airline"

    ow_icao = _safe_get(flight.get("owner") or {}, "code", "icao", default="")
    if ow_icao and ow_icao != "N/A" and cfg.store.is_on_airline_watchlist(ow_icao, "operator"):
        return "Watchlist Operator"

    return None


async def _send_aircraft_swap_notice(
    context,
    cfg,
    chat_id: str,
    old_rego: str,
    new_rego: str,
    new_flight: dict,
    flight_number: str,
    notification_type: str,
    arrival_ts: int,
) -> None:
    tz = pytz.timezone(cfg.airport_tz)
    arrival_str = (
        datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%a %H:%M")
        if arrival_ts else "N/A"
    )
    aircraft = new_flight.get("aircraft") or {}
    ac_type  = _safe_get(aircraft, "model", "code")
    ac_name  = _safe_get(aircraft, "model", "text")

    lines = [
        f"<b>Aircraft Changed — {notification_type}</b>",
        f"  Flight: {flight_number or 'N/A'}",
        f"  Was: {old_rego}",
        f"  Now: {new_rego} ({ac_name} / {ac_type})",
        f"  Arrival: {arrival_str} (local)",
    ]
    interesting = _classify_new_aircraft(new_flight, new_rego, cfg)
    if interesting:
        lines.append(f"\n  <b>{interesting}</b>")

    try:
        await context.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
        log.info("Sent aircraft swap notice: %s → %s on %s", old_rego, new_rego, flight_number)
    except Exception as exc:
        log.error("Failed to send swap notice for %s: %s", flight_number, exc)


async def _send_disappeared_notice(
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
        f"<b>No Longer Visible — {notification_type}</b>\n"
        f"  Registration: {registration}\n"
        f"  Flight: {flight_number or 'N/A'}\n"
        f"  Was scheduled: {arrival_str} (Local)\n\n"
        "This flight has dropped off the arrivals board."
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")
        log.info("Sent disappeared notice for %s", registration)
    except Exception as exc:
        log.error("Failed to send disappeared notice for %s: %s", registration, exc)


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
        f"<b>Cancelled — {notification_type}</b>\n"
        f"  Registration: {registration}\n"
        f"  Flight: {flight_number or 'N/A'}\n"
        f"  Was scheduled: {arrival_str} (Local)"
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")
        log.info("Sent cancellation notice for %s", registration)
    except Exception as exc:
        log.error("Failed to send cancellation notice for %s: %s", registration, exc)


async def _send_diversion_notice(
    context,
    cfg,
    chat_id: str,
    registration: str,
    flight_number: str,
    notification_type: str,
    arrival_ts: int,
    diverted_airport: str,
) -> None:
    tz = pytz.timezone(cfg.airport_tz)
    arrival_str = (
        datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%a %H:%M")
        if arrival_ts else "N/A"
    )
    airport_str = f" to {diverted_airport}" if diverted_airport else ""
    message = (
        f"<b>Diverted{airport_str} — {notification_type}</b>\n"
        f"  Registration: {registration}\n"
        f"  Flight: {flight_number or 'N/A'}\n"
        f"  Was scheduled: {arrival_str} (Local)"
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")
        log.info("Sent diversion notice for %s → %s", registration, diverted_airport or "unknown")
    except Exception as exc:
        log.error("Failed to send diversion notice for %s: %s", registration, exc)
