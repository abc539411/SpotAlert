from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import pytz
import requests
from astral import LocationInfo
from astral.sun import sun
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from monitor import _parse_aircraft, _safe_get
from weather import get_current_weather, get_forecast_weather

log = logging.getLogger(__name__)

# In-memory public holiday cache: {country: {year: set_of_date_strings}}
_holiday_cache: dict = {}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _tz_to_country(tz_name: str) -> Optional[str]:
    for code, tzs in pytz.country_timezones.items():
        if tz_name in tzs:
            return code
    return None


def _is_public_holiday(d, country: str) -> bool:
    year = d.year
    if country not in _holiday_cache:
        _holiday_cache[country] = {}
    if year not in _holiday_cache[country]:
        try:
            r = requests.get(
                f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country}",
                timeout=5,
            )
            dates = {h["date"] for h in r.json()}
        except Exception as exc:
            log.warning("Public holiday fetch failed for %s/%s: %s", country, year, exc)
            dates = set()
        _holiday_cache[country][year] = dates
    return d.isoformat() in _holiday_cache[country][year]


def _is_qualifying_day(d, day_type: str, tz_name: str) -> bool:
    if day_type == "Any":
        return True
    if d.weekday() >= 5:
        return True
    country = _tz_to_country(tz_name)
    return bool(country and _is_public_holiday(d, country))


def _sun_times(cfg, date) -> Tuple[int, int]:
    """Return (sunrise_ts, sunset_ts) for the given date at the airport location."""
    tz = pytz.timezone(cfg.airport_tz)
    parts = cfg.airport_tz.split("/")
    location = LocationInfo(parts[-1], parts[0], cfg.airport_tz, cfg.airport_lat, cfg.airport_lon)
    info = sun(location.observer, date=date, tzinfo=location.timezone)
    return int(info["sunrise"].timestamp()), int(info["sunset"].timestamp())


def _sun_line(sunrise_ts: int, sunset_ts: int, tz) -> str:
    rise = datetime.fromtimestamp(sunrise_ts).astimezone(tz).strftime("%H:%M")
    sset = datetime.fromtimestamp(sunset_ts).astimezone(tz).strftime("%H:%M")
    return f"Sunrise: {rise} · Sunset: {sset}"


def _passes_lighting_gate(arrival_ts: int, sunrise_ts: int, sunset_ts: int) -> bool:
    """Return False if the arrival is after sunset (exclude). Before sunrise is allowed
    since the plane will still be on the ground after sunrise."""
    return arrival_ts <= sunset_ts


def _interesting_label(arriving_flight: dict, cfg) -> Optional[str]:
    """Read-only filter check. Returns notification type label or None."""
    flight_data = arriving_flight.get("flight") or {}
    airline_name = (flight_data.get("airline") or {}).get("name") or ""

    if any(kw in airline_name for kw in cfg.livery_keywords):
        return "Special Livery"

    parsed = _parse_aircraft(arriving_flight)
    if parsed is None:
        return None
    registration, aircraft_type, _ = parsed

    if cfg.store.is_excluded(registration):
        return None

    # Skip planes already well-photographed at this airport
    if cfg.spot_rec_max_spotted_times > 0 and cfg.catalog:
        count = cfg.catalog.get_session_count_at_airport(registration, cfg.airport_iata)
        if count >= cfg.spot_rec_max_spotted_times:
            return None

    if cfg.store.is_on_rego_watchlist(registration):
        return "Watchlist Registration"

    owner = flight_data.get("owner") or {}
    try:
        airline_icao = owner["code"]["icao"]
        if cfg.store.is_on_type_watchlist(airline_icao, aircraft_type):
            return "Watchlist Aircraft Type"
    except (KeyError, TypeError):
        pass

    al_icao = _safe_get(flight_data, "airline", "code", "icao", default="")
    if al_icao and al_icao != "N/A" and cfg.store.is_on_airline_watchlist(al_icao, "airline"):
        return "Watchlist Airline"

    ow_icao = _safe_get(flight_data, "owner", "code", "icao", default="")
    if ow_icao and ow_icao != "N/A" and cfg.store.is_on_airline_watchlist(ow_icao, "operator"):
        return "Watchlist Operator"

    # Rare plane — read-only, no DB write
    try:
        airline_icao = owner["code"]["icao"]
        last_seen = cfg.store.get_rare_plane_last_seen(airline_icao, aircraft_type)
        now_ts = int(datetime.now().timestamp())
        if last_seen is None or (now_ts - last_seen) / 86400 > cfg.rare_plane_min_absence_days:
            return "Rare Plane"
    except (KeyError, TypeError):
        pass

    return None


# ------------------------------------------------------------------
# Rolling recommendation (called after each arrivals check)
# ------------------------------------------------------------------

async def check_rolling_recommendation(context: ContextTypes.DEFAULT_TYPE, cfg, chat_id: str) -> None:
    if not cfg.spot_rec_enabled:
        return

    tz = pytz.timezone(cfg.airport_tz)
    now = datetime.now(tz)
    today = now.date()
    now_ts = int(now.timestamp())

    if not _is_qualifying_day(today, cfg.spot_rec_day_type, cfg.airport_tz):
        return

    suppressed = cfg.store.load_setting("SPOT_REC_SUPPRESSED_DATE")
    if suppressed == today.isoformat():
        return

    last_ts = cfg.store.load_setting("SPOT_REC_ROLLING_LAST_TS")
    if last_ts and (now.timestamp() - float(last_ts)) < cfg.spot_rec_session_hours * 3600:
        return

    sunrise_ts, sunset_ts = _sun_times(cfg, today)

    # Don't send after sunset
    if now_ts >= sunset_ts:
        return

    window_start = now_ts + cfg.spot_rec_travel_mins * 60
    window_end = min(window_start + cfg.spot_rec_session_hours * 3600, sunset_ts)

    evals = _evaluate_rolling_flights(cfg, window_start, window_end, sunrise_ts, sunset_ts)
    interesting = [e for e in evals if e.qualifying]
    filtered    = [e for e in evals if not e.qualifying]

    if len(interesting) < cfg.spot_rec_threshold:
        return

    weather = get_current_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz)
    if cfg.spot_rec_weather_gate and weather and weather.is_severe:
        return

    first_arr = datetime.fromtimestamp(interesting[0].arrival_ts).astimezone(tz).strftime("%H:%M")
    last_arr  = datetime.fromtimestamp(interesting[-1].arrival_ts).astimezone(tz).strftime("%H:%M")
    window_str = first_arr if first_arr == last_arr else f"{first_arr} – {last_arr}"

    lines = [
        f"<b>Head out now — {len(interesting)} interesting arrival{'s' if len(interesting) != 1 else ''}</b>",
        f"Window: {window_str}",
        "",
    ]
    for e in interesting:
        lines.append(_flight_line(e, tz))

    if filtered:
        lines.append("")
        lines.append(f"Also of note — filtered out ({len(filtered)}):")
        for e in filtered:
            lines.append(_flight_line(e, tz, include_reason=True))

    lines.append("")
    if weather:
        lines.append(f"Weather: {weather}")
    lines.append(_sun_line(sunrise_ts, sunset_ts, tz))

    try:
        await context.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
        cfg.store.save_setting("SPOT_REC_ROLLING_LAST_TS", str(now_ts))
        log.info("Sent rolling spot recommendation (%d flights)", len(interesting))
    except Exception as exc:
        log.error("Failed to send rolling spot recommendation: %s", exc)


# ------------------------------------------------------------------
# End-of-day recommendation (scheduled job)
# ------------------------------------------------------------------

async def run_eod_recommendation(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not cfg.spot_rec_enabled:
        return

    chat_id = cfg.chat_id
    tz = pytz.timezone(cfg.airport_tz)
    tomorrow = (datetime.now(tz) + timedelta(days=1)).date()

    if not _is_qualifying_day(tomorrow, cfg.spot_rec_day_type, cfg.airport_tz):
        return

    sunrise_ts, sunset_ts = _sun_times(cfg, tomorrow)

    try:
        evals = _evaluate_eod_flights(cfg, tomorrow, sunrise_ts, sunset_ts)
    except Exception as exc:
        log.error("EOD recommendation check failed: %s", exc, exc_info=True)
        return

    qualifying = [e for e in evals if e.qualifying]
    filtered   = [e for e in evals if not e.qualifying]
    if not qualifying:
        return

    # Slide a session_hours window to find peak concentration
    session_secs = cfg.spot_rec_session_hours * 3600
    best_qualifying, best_start = [], None
    for e in qualifying:
        in_win = [q for q in qualifying if e.arrival_ts <= q.arrival_ts <= e.arrival_ts + session_secs]
        if len(in_win) > len(best_qualifying):
            best_qualifying = in_win
            best_start = e.arrival_ts

    if len(best_qualifying) < cfg.spot_rec_threshold:
        return

    weather = get_forecast_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz, day_offset=1)
    if cfg.spot_rec_weather_gate and weather and weather.is_severe:
        return

    # Filtered flights within the best window
    best_filtered = [e for e in filtered if best_start and best_start <= e.arrival_ts <= best_start + session_secs]

    first_str = datetime.fromtimestamp(best_qualifying[0].arrival_ts).astimezone(tz).strftime("%H:%M")
    last_str  = datetime.fromtimestamp(best_qualifying[-1].arrival_ts).astimezone(tz).strftime("%H:%M")
    window_str = first_str if first_str == last_str else f"{first_str} – {last_str}"
    day_str = tomorrow.strftime("%A %-d %b")

    lines = [
        f"<b>Spotting recommendation for {day_str}</b>",
        f"Best window: {window_str} ({len(best_qualifying)} interesting arrival{'s' if len(best_qualifying) != 1 else ''})",
        "",
    ]
    for e in best_qualifying:
        lines.append(_flight_line(e, tz))

    if best_filtered:
        lines.append("")
        lines.append(f"Also of note — filtered out ({len(best_filtered)}):")
        for e in best_filtered:
            lines.append(_flight_line(e, tz, include_reason=True))

    lines.append("")
    lines.append(f"Weather tomorrow: {weather}" if weather else "Weather tomorrow: unavailable")
    lines.append(_sun_line(sunrise_ts, sunset_ts, tz))

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes ✓", callback_data="spot_yes"),
        InlineKeyboardButton("Maybe", callback_data="spot_maybe"),
        InlineKeyboardButton("No ✗", callback_data="spot_no"),
    ]])

    try:
        await context.bot.send_message(
            chat_id=chat_id, text="\n".join(lines),
            parse_mode="HTML", reply_markup=keyboard,
        )
        cfg.store.save_setting("SPOT_REC_PENDING_SESSION_TS", str(best_qualifying[0].arrival_ts))
        log.info("Sent EOD spot recommendation (%s–%s, %d flights)", first_str, last_str, len(best_qualifying))
    except Exception as exc:
        log.error("Failed to send EOD recommendation: %s", exc)


# ------------------------------------------------------------------
# Manual /spot command — detailed evaluation with full reasoning
# ------------------------------------------------------------------

@dataclass
class FlightEval:
    arrival_ts: int
    registration: str
    notif_type: str        # filter label (Special Livery, Rego Watchlist, etc.)
    qualifying: bool
    reason: str            # why filtered out, if not qualifying
    detail: str = ""       # "Airline (Type)" for all types
    livery: str = ""       # livery name for Special Livery only


def _get_airline_detail(cfg, registration: str) -> str:
    """Return 'Airline (Type)' string via FR24 API."""
    try:
        rego_details = cfg.fr_api.get_rego_details(registration)
        data = (rego_details or {}).get("data") or []
        if data:
            import re
            aircraft_code = ((data[0].get("aircraft") or {}).get("model") or {}).get("code") or ""
            airline_name  = (data[0].get("airline") or {}).get("name") or ""
            clean_airline = re.sub(r'\s*\(.*?\)', '', airline_name).strip()
            if clean_airline and aircraft_code:
                return f"{clean_airline} ({aircraft_code})"
            return clean_airline or aircraft_code
    except Exception:
        pass
    return ""


def _airline_detail_from_flight(flight_data: dict) -> str:
    """Extract 'Airline (Type)' from already-fetched flight data — no API call needed."""
    import re
    airline_name  = (flight_data.get("airline") or {}).get("name") or ""
    aircraft_code = _safe_get(flight_data, "aircraft", "model", "code", default="")
    clean_airline = re.sub(r'\s*\(.*?\)', '', airline_name).strip()
    if clean_airline and aircraft_code:
        return f"{clean_airline} ({aircraft_code})"
    return clean_airline or aircraft_code


def _evaluate_rolling_flights(cfg, window_start: int, window_end: int,
                               sunrise_ts: int, sunset_ts: int,
                               current_arrivals: dict = None,
                               arrivals_by_fn: dict = None) -> List[FlightEval]:
    """Evaluate all tracked flights in the window, categorising each with a reason.

    current_arrivals: {registration: flight} — if provided, used to detect cancellations/swaps.
    arrivals_by_fn:   {flight_number: (registration, flight)} — for swap detection.
    """
    results = []
    tz = pytz.timezone(cfg.airport_tz)

    for record in cfg.store.get_tracked_flights():
        arrival_ts = int(record["arrival_ts"])
        if arrival_ts < window_start or arrival_ts > window_end:
            continue

        registration = record["registration"]
        notif_type = record["notif_type"] or "Interesting"
        extra_info = record["extra_info"] or ""
        arr_str = datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%H:%M")
        detail = _get_airline_detail(cfg, registration)
        livery = extra_info if notif_type == "Special Livery" else ""

        # Cancellation / swap check (only when current arrivals data is provided)
        if current_arrivals is not None and registration not in current_arrivals:
            flight_number = record["flight_number"] or ""
            if flight_number and arrivals_by_fn and flight_number in arrivals_by_fn:
                new_rego, _ = arrivals_by_fn[flight_number]
                if new_rego != registration:
                    results.append(FlightEval(arrival_ts, registration, notif_type, False,
                                              f"aircraft changed to {new_rego}", detail, livery))
                    continue
            results.append(FlightEval(arrival_ts, registration, notif_type, False,
                                      "cancelled or diverted", detail, livery))
            continue

        if cfg.spot_rec_lighting_gate and not _passes_lighting_gate(arrival_ts, sunrise_ts, sunset_ts):
            results.append(FlightEval(arrival_ts, registration, notif_type, False,
                                      f"arrives after sunset ({arr_str})", detail, livery))
            continue

        if cfg.spot_rec_max_spotted_times > 0 and cfg.catalog:
            count = cfg.catalog.get_session_count_at_airport(registration, cfg.airport_iata)
            if count >= cfg.spot_rec_max_spotted_times:
                results.append(FlightEval(arrival_ts, registration, notif_type, False,
                                          f"photographed {count} times at {cfg.airport_iata}", detail, livery))
                continue

        results.append(FlightEval(arrival_ts, registration, notif_type, True, "", detail, livery))

    return sorted(results, key=lambda x: x.arrival_ts)


def _evaluate_eod_flights(cfg, tomorrow, sunrise_ts: int, sunset_ts: int) -> List[FlightEval]:
    """Fetch tomorrow's arrivals and evaluate each with a reason."""
    tz = pytz.timezone(cfg.airport_tz)
    results = []

    for page in cfg.fetch_pages:
        try:
            data = cfg.fr_api.get_airport_details(code=cfg.airport_code, page=page)
            arrivals = data["airport"]["pluginData"]["schedule"]["arrivals"]["data"]
        except Exception as exc:
            log.warning("Manual EOD eval: failed to fetch page %d: %s", page, exc)
            continue

        for arriving_flight in arrivals:
            parsed = _parse_aircraft(arriving_flight)
            if not parsed:
                continue
            registration, _, flight = parsed

            scheduled_ts = _safe_get(flight, "time", "scheduled", "arrival", default=None)
            estimated_ts = _safe_get(flight, "time", "estimated", "arrival", default=None)
            arrival_ts = estimated_ts or scheduled_ts
            if not isinstance(arrival_ts, (int, float)):
                continue
            if datetime.fromtimestamp(arrival_ts).astimezone(tz).date() != tomorrow:
                continue

            arr_str = datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%H:%M")

            # Exclusion list
            if cfg.store.is_excluded(registration):
                flight_data = arriving_flight.get("flight") or {}
                detail = _airline_detail_from_flight(flight_data)
                results.append(FlightEval(int(arrival_ts), registration, "", False, "on exclusion list", detail))
                continue

            # Not interesting by any filter — skip silently
            label = _interesting_label(arriving_flight, cfg)
            if label is None:
                continue

            # Extract detail from flight data — no extra API call needed
            flight_data = arriving_flight.get("flight") or {}
            detail = _airline_detail_from_flight(flight_data)
            if label == "Special Livery":
                import re as _re
                airline_raw = (flight_data.get("airline") or {}).get("name") or ""
                m = _re.search(r'\((.+?)\)', airline_raw)
                livery = m.group(1) if m else ""
            else:
                livery = ""

            # Lighting gate
            if cfg.spot_rec_lighting_gate and not _passes_lighting_gate(int(arrival_ts), sunrise_ts, sunset_ts):
                results.append(FlightEval(int(arrival_ts), registration, label, False,
                                          f"arrives after sunset ({arr_str})", detail, livery))
                continue

            # Max spotted
            if cfg.spot_rec_max_spotted_times > 0 and cfg.catalog:
                count = cfg.catalog.get_session_count_at_airport(registration, cfg.airport_iata)
                if count >= cfg.spot_rec_max_spotted_times:
                    results.append(FlightEval(int(arrival_ts), registration, label, False,
                                              f"photographed {count} times at {cfg.airport_iata}", detail, livery))
                    continue

            results.append(FlightEval(int(arrival_ts), registration, label, True, "", detail, livery))

    return sorted(results, key=lambda x: x.arrival_ts)


def _flight_line(f: "FlightEval", tz, include_reason: bool = False) -> str:
    """Format a flight entry: type (livery if special) — airline (type) — arr HH:MM."""
    arr = datetime.fromtimestamp(f.arrival_ts).astimezone(tz).strftime("%H:%M")
    if f.livery:
        type_str = f"{f.notif_type} ({f.livery})"
    else:
        type_str = f.notif_type or ""
    detail_str = f" — {f.detail}" if f.detail else ""
    reason_str = f" — {f.reason}" if include_reason and f.reason else ""
    return f"  • {f.registration} — {type_str}{detail_str}{reason_str} — arr {arr}"


def _build_detail_message(
    qualifying: List["FlightEval"],
    filtered: List["FlightEval"],
    threshold: int,
    weather,
    weather_gate: bool,
    header: str,
    tz,
    sunrise_ts: int = 0,
    sunset_ts: int = 0,
    show_verdict: bool = True,
) -> str:
    """Build the detailed manual check message, tightening window to first→last arrival."""
    severe_weather = weather_gate and weather and weather.is_severe

    # Tighten window to first → last qualifying arrival
    if qualifying:
        first_str = datetime.fromtimestamp(qualifying[0].arrival_ts).astimezone(tz).strftime("%H:%M")
        last_str  = datetime.fromtimestamp(qualifying[-1].arrival_ts).astimezone(tz).strftime("%H:%M")
        window_str = first_str if first_str == last_str else f"{first_str} – {last_str}"
    else:
        window_str = None

    # Verdict (only for Best Time to Go)
    if show_verdict:
        if severe_weather:
            verdict = f"Not recommended — severe weather ({weather})"
        elif len(qualifying) >= threshold:
            verdict = f"Recommended — {len(qualifying)} qualifying arrival{'s' if len(qualifying) != 1 else ''}"
        else:
            verdict = f"Not recommended — {len(qualifying)} qualifying arrival{'s' if len(qualifying) != 1 else ''} (need {threshold})"
        lines = [f"<b>{header}</b>", verdict]
    else:
        count_str = f"{len(qualifying)} interesting arrival{'s' if len(qualifying) != 1 else ''}"
        lines = [f"<b>{header}</b>", count_str]
    if window_str:
        lines.append(f"Window: {window_str}")
    lines.append("")

    if not qualifying and not filtered:
        lines.append("  No interesting arrivals in this window.")
    else:
        # Qualifying section
        if qualifying:
            section = "Would have qualified:" if severe_weather else f"Qualifying ({len(qualifying)}):"
            lines.append(section)
            for f in qualifying:
                lines.append(_flight_line(f, tz))

        # Filtered section
        if filtered:
            if qualifying:
                lines.append("")
            lines.append(f"Filtered out ({len(filtered)}):")
            for f in filtered:
                lines.append(_flight_line(f, tz, include_reason=True))

    lines.append("")
    lines.append(f"Weather: {weather}" if weather else "Weather: unavailable")
    if sunrise_ts and sunset_ts:
        lines.append(_sun_line(sunrise_ts, sunset_ts, tz))

    return "\n".join(lines)


_PERIOD_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("Morning",          callback_data="spot_period_morning"),
     InlineKeyboardButton("Afternoon",        callback_data="spot_period_afternoon")],
    [InlineKeyboardButton("All Day",          callback_data="spot_period_allday"),
     InlineKeyboardButton("Best Time to Go",  callback_data="spot_period_best")],
])

_PERIOD_LABELS = {
    "morning":   "Morning",
    "afternoon": "Afternoon",
    "allday":    "All Day",
    "best":      "Best Time to Go",
}


def _day_hour_ts(d, hour: int, tz) -> int:
    import datetime as _dt
    return int(tz.localize(_dt.datetime.combine(d, _dt.time(hour, 0))).timestamp())


async def _run_spot_check(send_fn, context: ContextTypes.DEFAULT_TYPE,
                          day: str, period: str) -> None:
    cfg = context.bot_data["cfg"]
    tz = pytz.timezone(cfg.airport_tz)
    now = datetime.now(tz)
    show_verdict = (period == "best")

    if day == "today":
        await send_fn("Checking...")
        now_ts = int(now.timestamp())
        target_date = now.date()
        sunrise_ts, sunset_ts = _sun_times(cfg, target_date)

        if period == "best":
            import datetime as _dt
            midnight = int(tz.localize(_dt.datetime.combine(target_date, _dt.time(23, 59, 59))).timestamp())
            window_start = now_ts + cfg.spot_rec_travel_mins * 60
            window_end   = min(window_start + cfg.spot_rec_session_hours * 3600, midnight)
        elif period == "morning":
            window_start = sunrise_ts - cfg.summary_morning_pre_sunrise_hours * 3600
            window_end   = _day_hour_ts(target_date, cfg.summary_morning_end_hour, tz)
        elif period == "afternoon":
            window_start = _day_hour_ts(target_date, cfg.summary_afternoon_start_hour, tz)
            window_end   = sunset_ts + cfg.summary_afternoon_post_sunset_hours * 3600
        else:  # allday — full day, no session window constraint
            window_start = _day_hour_ts(target_date, 0, tz)
            window_end   = _day_hour_ts(target_date, 23, tz) + 3599

        evals      = _evaluate_rolling_flights(cfg, window_start, window_end, sunrise_ts, sunset_ts)
        qualifying = [e for e in evals if e.qualifying]
        filtered   = [e for e in evals if not e.qualifying]
        weather    = get_current_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz)
        header     = f"Spot check — Today ({_PERIOD_LABELS[period]})"
        msg = _build_detail_message(qualifying, filtered, cfg.spot_rec_threshold,
                                    weather, cfg.spot_rec_weather_gate,
                                    header, tz, sunrise_ts, sunset_ts, show_verdict=show_verdict)

    else:  # tomorrow
        await send_fn("Fetching tomorrow's arrivals...")
        tomorrow   = (now + timedelta(days=1)).date()
        sunrise_ts, sunset_ts = _sun_times(cfg, tomorrow)
        evals         = _evaluate_eod_flights(cfg, tomorrow, sunrise_ts, sunset_ts)
        all_qualifying = [e for e in evals if e.qualifying]
        all_filtered   = [e for e in evals if not e.qualifying]

        if period == "best":
            session_secs = cfg.spot_rec_session_hours * 3600
            best_qualifying, best_start = [], None
            for e in all_qualifying:
                in_win = [q for q in all_qualifying if e.arrival_ts <= q.arrival_ts <= e.arrival_ts + session_secs]
                if len(in_win) > len(best_qualifying):
                    best_qualifying = in_win
                    best_start = e.arrival_ts
            qualifying = best_qualifying
            filtered   = [f for f in all_filtered
                          if best_start and best_start <= f.arrival_ts <= best_start + session_secs] \
                         if best_start else all_filtered
        elif period == "allday":
            qualifying = all_qualifying
            filtered   = all_filtered
        else:
            if period == "morning":
                ws = sunrise_ts - cfg.summary_morning_pre_sunrise_hours * 3600
                we = _day_hour_ts(tomorrow, cfg.summary_morning_end_hour, tz)
            else:  # afternoon
                ws = _day_hour_ts(tomorrow, cfg.summary_afternoon_start_hour, tz)
                we = sunset_ts + cfg.summary_afternoon_post_sunset_hours * 3600
            qualifying = [e for e in all_qualifying if ws <= e.arrival_ts <= we]
            filtered   = [e for e in all_filtered   if ws <= e.arrival_ts <= we]

        weather = get_forecast_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz, day_offset=1)
        header  = f"Spot check — {tomorrow.strftime('%A %-d %b')} ({_PERIOD_LABELS[period]})"
        msg = _build_detail_message(qualifying, filtered, cfg.spot_rec_threshold,
                                    weather, cfg.spot_rec_weather_gate,
                                    header, tz, sunrise_ts, sunset_ts, show_verdict=show_verdict)

    await send_fn(msg, parse_mode="HTML")


async def handle_spot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Today",    callback_data="spot_day_today"),
        InlineKeyboardButton("Tomorrow", callback_data="spot_day_tomorrow"),
    ]])
    await update.message.reply_text("Which day?", reply_markup=keyboard)


async def handle_spot_day_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    day = query.data.replace("spot_day_", "")
    context.user_data["spot_day"] = day
    await query.edit_message_text("Which period?", reply_markup=_PERIOD_KB)


async def handle_spot_period_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    period = query.data.replace("spot_period_", "")
    day    = context.user_data.get("spot_day", "today")
    await query.edit_message_reply_markup(reply_markup=None)

    async def send_fn(text, **kwargs):
        await context.bot.send_message(chat_id=query.message.chat_id, text=text, **kwargs)

    await _run_spot_check(send_fn, context, day, period)


# ------------------------------------------------------------------
# Follow-up message (scheduled when user taps Yes on EOD recommendation)
# ------------------------------------------------------------------

async def _send_spot_followup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-evaluate today's window and send an updated message — time to head out."""
    cfg = context.bot_data["cfg"]
    tz = pytz.timezone(cfg.airport_tz)
    now = datetime.now(tz)
    now_ts = int(now.timestamp())
    today = now.date()

    sunrise_ts, sunset_ts = _sun_times(cfg, today)
    window_start = now_ts + cfg.spot_rec_travel_mins * 60
    window_end = min(window_start + cfg.spot_rec_session_hours * 3600, sunset_ts)

    # Fetch current arrivals to detect cancellations and swaps since the EOD recommendation
    current_arrivals: dict = {}
    arrivals_by_fn: dict = {}
    try:
        for page in cfg.fetch_pages:
            try:
                data = cfg.fr_api.get_airport_details(code=cfg.airport_code, page=page)
                page_arrivals = data["airport"]["pluginData"]["schedule"]["arrivals"]["data"]
            except Exception:
                continue
            for af in page_arrivals:
                parsed = _parse_aircraft(af)
                if parsed:
                    reg, _, flight = parsed
                    current_arrivals[reg] = flight
                    fn = str(_safe_get(flight, "identification", "number", "default", default=""))
                    if fn and fn not in arrivals_by_fn:
                        arrivals_by_fn[fn] = (reg, flight)
    except Exception as exc:
        log.warning("Follow-up: could not fetch current arrivals: %s", exc)

    evals = _evaluate_rolling_flights(
        cfg, window_start, window_end, sunrise_ts, sunset_ts,
        current_arrivals if current_arrivals else None,
        arrivals_by_fn if arrivals_by_fn else None,
    )
    qualifying = [e for e in evals if e.qualifying]
    filtered = [e for e in evals if not e.qualifying]

    weather = get_current_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz)

    msg = _build_detail_message(
        qualifying, filtered, cfg.spot_rec_threshold,
        weather, cfg.spot_rec_weather_gate,
        "Spotting update — time to head out", tz, sunrise_ts, sunset_ts,
    )

    try:
        await context.bot.send_message(chat_id=cfg.chat_id, text=msg, parse_mode="HTML")
        cfg.store.save_setting("SPOT_REC_ROLLING_LAST_TS", str(now_ts))
        log.info("Sent spot follow-up message")
    except Exception as exc:
        log.error("Failed to send spot follow-up: %s", exc)


# ------------------------------------------------------------------
# Inline keyboard callback
# ------------------------------------------------------------------

async def handle_spot_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    cfg = context.bot_data["cfg"]
    tz = pytz.timezone(cfg.airport_tz)
    tomorrow = (datetime.now(tz) + timedelta(days=1)).date().isoformat()

    await query.edit_message_reply_markup(reply_markup=None)

    if query.data == "spot_no":
        cfg.store.save_setting("SPOT_REC_SUPPRESSED_DATE", tomorrow)
        await query.message.reply_text("Got it — no rolling spotting recommendations tomorrow.")

    elif query.data == "spot_yes":
        pending_ts = cfg.store.load_setting("SPOT_REC_PENDING_SESSION_TS")
        if pending_ts:
            session_start = int(pending_ts)
            follow_up_ts = session_start - cfg.spot_rec_travel_mins * 60 - 1800  # 30 min buffer
            now_ts = int(datetime.now().timestamp())
            if follow_up_ts > now_ts:
                import datetime as _dt
                follow_up_dt = _dt.datetime.fromtimestamp(follow_up_ts, tz=pytz.utc)
                # Cancel any existing follow-up before scheduling a new one
                for job in context.application.job_queue.get_jobs_by_name("spot_followup"):
                    job.schedule_removal()
                context.application.job_queue.run_once(
                    _send_spot_followup, when=follow_up_dt, name="spot_followup",
                )
                tz_obj = pytz.timezone(cfg.airport_tz)
                followup_str = _dt.datetime.fromtimestamp(follow_up_ts).astimezone(tz_obj).strftime("%H:%M")
                await query.message.reply_text(f"See you out there! I'll send you an update at {followup_str}.")
            else:
                await query.message.reply_text("See you out there!")
        else:
            await query.message.reply_text("See you out there!")

    else:
        await query.message.reply_text("Maybe see you out there!")


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

def register_spot_rec_handlers(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(handle_spot_response,       pattern="^spot_(yes|maybe|no)$"))
    app.add_handler(CallbackQueryHandler(handle_spot_day_callback,   pattern="^spot_day_(today|tomorrow)$"))
    app.add_handler(CallbackQueryHandler(handle_spot_period_callback, pattern="^spot_period_(morning|afternoon|allday|best)$"))
    app.add_handler(CommandHandler("spot", handle_spot_command))
