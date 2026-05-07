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


def _apply_pre_sunrise_gate(
    qualifying: List["FlightEval"],
    filtered: List["FlightEval"],
    sunrise_ts: int,
    sunset_ts: int,
    lighting_gate: bool,
) -> tuple:
    """Second lighting gate pass — run after _populate_departures so dep_ts is known.

    Pre-sunrise arrivals are only kept if dep_ts is known and falls in daylight.
    If dep_ts is unknown or also outside daylight, the flight is moved to filtered.
    Returns (new_qualifying, new_filtered).
    """
    if not lighting_gate or not sunrise_ts:
        return qualifying, filtered

    still_qualifying = []
    newly_filtered = list(filtered)
    for e in qualifying:
        if e.arrival_ts < sunrise_ts:
            if e.dep_ts and sunrise_ts <= e.dep_ts <= sunset_ts:
                still_qualifying.append(e)
            else:
                e.reason = "arrives before sunrise with no confirmed daylight departure"
                newly_filtered.append(e)
        else:
            still_qualifying.append(e)
    return still_qualifying, newly_filtered


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
    _populate_departures(interesting, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
    interesting, filtered = _apply_pre_sunrise_gate(interesting, filtered, sunrise_ts, sunset_ts, cfg.spot_rec_lighting_gate)
    for e in interesting:
        arr_in = window_start <= e.arrival_ts <= window_end
        dep_in = bool(e.dep_ts and window_start <= e.dep_ts <= window_end)
        e.session_ts = e.arrival_ts if arr_in else (e.dep_ts or e.arrival_ts)
        e.show_dep = arr_in and dep_in

    if len(interesting) < cfg.spot_rec_threshold:
        return

    weather = get_current_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz)
    if cfg.spot_rec_weather_gate and weather and weather.is_severe:
        return

    first_arr = datetime.fromtimestamp(interesting[0].arrival_ts).astimezone(tz).strftime("%H:%M")
    last_arr  = datetime.fromtimestamp(interesting[-1].arrival_ts).astimezone(tz).strftime("%H:%M")
    window_str = first_arr if first_arr == last_arr else f"{first_arr} – {last_arr}"

    n = len(interesting)
    lines = [
        f"<b>Head out now — {n} interesting flight{'s' if n != 1 else ''}</b>",
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

    sent = False
    for dest_chat_id in cfg.all_chat_ids:
        try:
            await context.bot.send_message(chat_id=dest_chat_id, text="\n".join(lines), parse_mode="HTML")
            sent = True
        except Exception as exc:
            log.error("Failed to send rolling spot recommendation to %s: %s", dest_chat_id, exc)

    if sent:
        cfg.store.save_setting("SPOT_REC_ROLLING_LAST_TS", str(now_ts))
        log.info("Sent rolling spot recommendation (%d flights)", len(interesting))


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

    _populate_departures(qualifying, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
    qualifying, filtered = _apply_pre_sunrise_gate(qualifying, filtered, sunrise_ts, sunset_ts, cfg.spot_rec_lighting_gate)
    if not qualifying:
        return

    session_secs = cfg.spot_rec_session_hours * 3600
    best_qualifying = _best_session_window(qualifying, session_secs)

    if len(best_qualifying) < cfg.spot_rec_threshold:
        return

    weather = get_forecast_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz, day_offset=1)
    if cfg.spot_rec_weather_gate and weather and weather.is_severe:
        return

    # Filtered flights within the best window
    best_start = min(e.session_ts for e in best_qualifying)
    best_end   = best_start + session_secs
    best_filtered = [e for e in filtered
                     if best_start <= e.arrival_ts <= best_end
                     or (e.dep_ts and best_start <= e.dep_ts <= best_end)]

    first_str = datetime.fromtimestamp(min(e.session_ts for e in best_qualifying)).astimezone(tz).strftime("%H:%M")
    last_str  = datetime.fromtimestamp(max(e.session_ts for e in best_qualifying)).astimezone(tz).strftime("%H:%M")
    window_str = first_str if first_str == last_str else f"{first_str} – {last_str}"
    day_str = tomorrow.strftime("%A %-d %b")
    n = len(best_qualifying)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes ✓", callback_data="spot_yes"),
        InlineKeyboardButton("Maybe", callback_data="spot_maybe"),
        InlineKeyboardButton("No ✗", callback_data="spot_no"),
    ]])

    lines = [
        f"<b>Spotting recommendation for {day_str}</b>",
        f"Best window: {window_str} ({n} interesting flight{'s' if n != 1 else ''})",
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

    for dest_chat_id in cfg.all_chat_ids:
        try:
            kb = keyboard if cfg.store.is_admin(dest_chat_id) else None
            await context.bot.send_message(
                chat_id=dest_chat_id, text="\n".join(lines),
                parse_mode="HTML", reply_markup=kb,
            )
        except Exception as exc:
            log.error("Failed to send EOD recommendation to %s: %s", dest_chat_id, exc)

    cfg.store.save_setting("SPOT_REC_PENDING_SESSION_TS", str(best_qualifying[0].arrival_ts))
    log.info("Sent EOD spot recommendation (%s–%s, %d flights)", first_str, last_str, n)


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
    flight_number: str = ""          # arrival flight number (for departure lookup)
    dep_fn: Optional[str] = None     # predicted departure flight number
    dep_ts: Optional[int] = None     # departure timestamp
    dep_time_label: str = ""         # "Predicted", "Scheduled", "Estimated"
    session_ts: Optional[int] = None # time used for Scenario B window optimisation
    show_dep: bool = False           # True when both arr and dep fall within the session window



def _lookup_departure_for_flight(cfg, arrival_fn: str) -> Tuple[Optional[str], Optional[int], str]:
    """Look up departure info for an arrival flight number from DB.
    Returns (dep_fn, dep_ts, dep_time_label) or (None, None, '').
    Precedence for dep_ts: estimated → scheduled → None (predicted, no time).
    """
    if not arrival_fn:
        return None, None, ""
    predicted = cfg.store.get_predicted_departure(arrival_fn, cfg.airport_iata, cfg.departure_pattern_threshold)
    if not predicted:
        return None, None, ""
    dep_fn, _, _, _ = predicted
    dep_info = cfg.store.get_predicted_dep_info(dep_fn, cfg.airport_iata)
    if dep_info:
        if dep_info.get("estimated_dep_ts"):
            return dep_fn, dep_info["estimated_dep_ts"], "Estimated"
        if dep_info.get("scheduled_dep_ts"):
            return dep_fn, dep_info["scheduled_dep_ts"], "Scheduled"
    return dep_fn, None, "Predicted"


def _populate_departures(flights: List["FlightEval"], cfg,
                         sunset_ts: int = 0, sunrise_ts: int = 0) -> None:
    """Populate dep_fn, dep_ts, dep_time_label on each FlightEval in-place.

    If lighting gate is enabled and sunset_ts is provided, dep_ts values outside
    daylight (before sunrise or after sunset) are cleared.
    """
    for e in flights:
        if not e.flight_number:
            continue
        dep_fn, dep_ts, dep_label = _lookup_departure_for_flight(cfg, e.flight_number)
        if dep_fn:
            e.dep_fn = dep_fn
            e.dep_time_label = dep_label
            if dep_ts and cfg.spot_rec_lighting_gate and sunset_ts:
                if dep_ts > sunset_ts or (sunrise_ts and dep_ts < sunrise_ts):
                    dep_ts = None
            e.dep_ts = dep_ts


def _best_session_window(qualifying: List["FlightEval"], session_secs: int) -> List["FlightEval"]:
    """Find the session window maximising qualifying aircraft count.

    Each aircraft contributes arr_ts and dep_ts (if available) as candidate window starts.
    Tiebreaker: minimise span across ALL in-window events (both arr and dep counted).
    Per aircraft: session_ts = arr if arr in window, else dep.
    show_dep set True when both arr and dep fall within the chosen window.
    """
    if not qualifying:
        return []

    candidates: set = set()
    for e in qualifying:
        candidates.add(e.arrival_ts)
        if e.dep_ts:
            candidates.add(e.dep_ts)

    best_flights: List["FlightEval"] = []
    best_span = float("inf")
    best_ws: Optional[int] = None
    best_we: Optional[int] = None

    for ws in sorted(candidates):
        we = ws + session_secs
        in_window: List["FlightEval"] = []
        event_times: List[int] = []  # all events (arr + dep) in window for span calculation
        for e in qualifying:
            arr_in = ws <= e.arrival_ts <= we
            dep_in = bool(e.dep_ts and ws <= e.dep_ts <= we)
            if arr_in or dep_in:
                in_window.append(e)
                if arr_in:
                    event_times.append(e.arrival_ts)
                if dep_in:
                    event_times.append(e.dep_ts)

        if not in_window:
            continue

        span = max(event_times) - min(event_times) if len(event_times) > 1 else 0
        if len(in_window) > len(best_flights) or (
            len(in_window) == len(best_flights) and span < best_span
        ):
            best_flights = in_window
            best_span = span
            best_ws = ws
            best_we = we

    # Assign session_ts and show_dep for the best window
    if best_ws is not None:
        for e in best_flights:
            arr_in = best_ws <= e.arrival_ts <= best_we
            dep_in = bool(e.dep_ts and best_ws <= e.dep_ts <= best_we)
            e.session_ts = e.arrival_ts if arr_in else e.dep_ts
            e.show_dep = arr_in and dep_in

    return best_flights


def _evaluate_rolling_flights(cfg, window_start: int, window_end: int,
                               sunrise_ts: int, sunset_ts: int) -> List[FlightEval]:
    """Evaluate tracked flights in the window using notification_record only (no API calls).

    Cancelled/swapped flights are already removed from the record by the monitor's
    check_follow_ups before this runs, so no explicit cancellation check is needed.
    """
    results = []
    tz = pytz.timezone(cfg.airport_tz)

    for record in cfg.store.get_tracked_flights():
        arrival_ts = int(record["arrival_ts"])
        if arrival_ts < window_start or arrival_ts > window_end:
            continue

        registration  = record["registration"]
        notif_type    = record["notif_type"] or "Interesting"
        extra_info    = record["extra_info"] or ""
        flight_number = record["flight_number"] or ""
        detail        = record["detail"] or ""
        arr_str       = datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%H:%M")
        livery        = extra_info if notif_type == "Special Livery" else ""

        if cfg.spot_rec_lighting_gate and not _passes_lighting_gate(arrival_ts, sunrise_ts, sunset_ts):
            results.append(FlightEval(arrival_ts, registration, notif_type, False,
                                      f"arrives after sunset ({arr_str})", detail, livery, flight_number))
            continue

        if cfg.spot_rec_max_spotted_times > 0 and cfg.catalog:
            count = cfg.catalog.get_session_count_at_airport(registration, cfg.airport_iata)
            if count >= cfg.spot_rec_max_spotted_times:
                results.append(FlightEval(arrival_ts, registration, notif_type, False,
                                          f"photographed {count} times at {cfg.airport_iata}", detail, livery, flight_number))
                continue

        results.append(FlightEval(arrival_ts, registration, notif_type, True, "", detail, livery, flight_number))

    return sorted(results, key=lambda x: x.arrival_ts)


def _evaluate_eod_flights(cfg, tomorrow, sunrise_ts: int, sunset_ts: int) -> List[FlightEval]:
    """Evaluate tomorrow's arrivals using notification_record only (no API calls)."""
    import datetime as _dt
    tz = pytz.timezone(cfg.airport_tz)
    tomorrow_start = int(tz.localize(_dt.datetime.combine(tomorrow, _dt.time(0, 0))).timestamp())
    tomorrow_end   = int(tz.localize(_dt.datetime.combine(tomorrow, _dt.time(23, 59, 59))).timestamp())
    return _evaluate_rolling_flights(cfg, tomorrow_start, tomorrow_end, sunrise_ts, sunset_ts)


def _flight_line(f: "FlightEval", tz, include_reason: bool = False,
                 scenario_a: bool = False, now_ts: int = 0) -> str:
    """Format a flight entry for display.

    Scenario A (manual): show arr and/or dep times; hide arrival if already passed.
    Scenario B (automatic): show whichever session_ts was chosen (arr or dep).
    """
    if f.livery:
        type_str = f"{f.notif_type} ({f.livery})"
    else:
        type_str = f.notif_type or ""
    detail_str = f" — {f.detail}" if f.detail else ""
    reason_str = f" — {f.reason}" if include_reason and f.reason else ""

    if scenario_a:
        arr_passed = now_ts > 0 and f.arrival_ts < now_ts
        times = []
        if not arr_passed:
            arr = datetime.fromtimestamp(f.arrival_ts).astimezone(tz).strftime("%H:%M")
            times.append(f"arr {arr}")
        if f.dep_ts:
            dep = datetime.fromtimestamp(f.dep_ts).astimezone(tz).strftime("%H:%M")
            times.append(f"dep {dep}")
        time_str = " / ".join(times) if times else "—"
    else:
        ts = f.session_ts if f.session_ts is not None else f.arrival_ts
        t = datetime.fromtimestamp(ts).astimezone(tz).strftime("%H:%M")
        if f.show_dep and f.dep_ts:
            dep_t = datetime.fromtimestamp(f.dep_ts).astimezone(tz).strftime("%H:%M")
            time_str = f"arr {t} / dep {dep_t}"
        elif f.dep_ts and f.session_ts == f.dep_ts and f.session_ts != f.arrival_ts:
            time_str = f"dep {t}"
        else:
            time_str = f"arr {t}"

    parts = [f"  • {f.registration}"]
    if type_str:
        parts.append(type_str)
    if f.detail:
        parts.append(f.detail)
    if include_reason and f.reason:
        parts.append(f.reason)
    if time_str and time_str != "—":
        parts.append(time_str)
    return " — ".join(parts)


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
    scenario_a: bool = False,
    now_ts: int = 0,
    show_window: bool = True,
) -> str:
    """Build the detail message for a spot check.

    scenario_a=True: Scenario A (manual /spot) — show both arr and dep times per aircraft.
    scenario_a=False: Scenario B (automatic) — show whichever session_ts was chosen.
    """
    severe_weather = weather_gate and weather and weather.is_severe

    # Window string — use session_ts for Scenario B, all event times for Scenario A
    if qualifying:
        if scenario_a:
            all_times = []
            for e in qualifying:
                if not (now_ts > 0 and e.arrival_ts < now_ts):
                    all_times.append(e.arrival_ts)
                if e.dep_ts:
                    all_times.append(e.dep_ts)
            if all_times:
                first_str = datetime.fromtimestamp(min(all_times)).astimezone(tz).strftime("%H:%M")
                last_str  = datetime.fromtimestamp(max(all_times)).astimezone(tz).strftime("%H:%M")
                window_str = first_str if first_str == last_str else f"{first_str} – {last_str}"
            else:
                window_str = None
        else:
            times = [e.session_ts if e.session_ts is not None else e.arrival_ts for e in qualifying]
            first_str = datetime.fromtimestamp(min(times)).astimezone(tz).strftime("%H:%M")
            last_str  = datetime.fromtimestamp(max(times)).astimezone(tz).strftime("%H:%M")
            window_str = first_str if first_str == last_str else f"{first_str} – {last_str}"
    else:
        window_str = None

    n = len(qualifying)
    flight_word = "flight" if n == 1 else "flights"

    # Verdict (only for Best Time to Go)
    if show_verdict:
        if severe_weather:
            verdict = f"Not recommended — severe weather ({weather})"
        elif n >= threshold:
            verdict = f"Recommended — {n} qualifying {flight_word}"
        else:
            verdict = f"Not recommended — {n} qualifying {flight_word} (need {threshold})"
        lines = [f"<b>{header}</b>", verdict]
    else:
        lines = [f"<b>{header}</b>", f"{n} interesting {flight_word}"]
    if window_str and show_window:
        lines.append(f"Window: {window_str}")
    lines.append("")

    if not qualifying and not filtered:
        lines.append("  No interesting flights in this window.")
    else:
        shown_qualifying = qualifying
        if scenario_a and now_ts > 0:
            shown_qualifying = [e for e in qualifying
                                if e.arrival_ts >= now_ts or e.dep_ts is not None]

        if show_verdict:
            section = "Would have qualified:" if severe_weather else f"Qualifying ({len(shown_qualifying)}):"
        else:
            section = f"Qualifying ({len(shown_qualifying)}):" if shown_qualifying else None

        if shown_qualifying and section:
            lines.append(section)
            for e in shown_qualifying:
                lines.append(_flight_line(e, tz, scenario_a=scenario_a, now_ts=now_ts))

        if filtered:
            if shown_qualifying:
                lines.append("")
            lines.append(f"Filtered out ({len(filtered)}):")
            for e in filtered:
                lines.append(_flight_line(e, tz, include_reason=True, scenario_a=scenario_a, now_ts=now_ts))

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
    now_ts = int(now.timestamp())
    show_verdict = (period == "best")
    scenario_a   = (period != "best")  # Scenario A for set periods, Scenario B for Best Time to Go

    if day == "today":
        await send_fn("Checking...")
        target_date = now.date()
        sunrise_ts, sunset_ts = _sun_times(cfg, target_date)

        if period == "best":
            import datetime as _dt
            midnight     = int(tz.localize(_dt.datetime.combine(target_date, _dt.time(23, 59, 59))).timestamp())
            window_start = now_ts + cfg.spot_rec_travel_mins * 60
            window_end   = midnight  # evaluate full remaining day; _best_session_window picks the optimal sub-window
        elif period == "morning":
            window_start = sunrise_ts - cfg.summary_morning_pre_sunrise_hours * 3600
            window_end   = _day_hour_ts(target_date, cfg.summary_morning_end_hour, tz)
        elif period == "afternoon":
            window_start = _day_hour_ts(target_date, cfg.summary_afternoon_start_hour, tz)
            window_end   = sunset_ts + cfg.summary_afternoon_post_sunset_hours * 3600
        else:  # allday
            window_start = _day_hour_ts(target_date, 0, tz)
            window_end   = _day_hour_ts(target_date, 23, tz) + 3599

        evals         = _evaluate_rolling_flights(cfg, window_start, window_end, sunrise_ts, sunset_ts)
        all_qualifying = [e for e in evals if e.qualifying]
        all_filtered   = [e for e in evals if not e.qualifying]

        if period == "best":
            session_secs = cfg.spot_rec_session_hours * 3600
            _populate_departures(all_qualifying, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
            all_qualifying, all_filtered = _apply_pre_sunrise_gate(all_qualifying, all_filtered, sunrise_ts, sunset_ts, cfg.spot_rec_lighting_gate)
            qualifying   = _best_session_window(all_qualifying, session_secs)
            best_start   = min((e.session_ts for e in qualifying), default=None)
            filtered     = [f for f in all_filtered
                            if best_start and best_start <= f.arrival_ts <= best_start + session_secs]
            _populate_departures(filtered, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
        else:
            qualifying = all_qualifying
            filtered   = all_filtered
            _populate_departures(qualifying + filtered, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
            qualifying, filtered = _apply_pre_sunrise_gate(qualifying, filtered, sunrise_ts, sunset_ts, cfg.spot_rec_lighting_gate)

        weather = get_current_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz)
        header  = f"Spot check — Today ({_PERIOD_LABELS[period]})"
        msg = _build_detail_message(qualifying, filtered, cfg.spot_rec_threshold,
                                    weather, cfg.spot_rec_weather_gate,
                                    header, tz, sunrise_ts, sunset_ts, show_verdict=show_verdict,
                                    scenario_a=scenario_a, now_ts=now_ts, show_window=(period == "best"))

    else:  # tomorrow
        await send_fn("Checking...")
        tomorrow = (now + timedelta(days=1)).date()
        sunrise_ts, sunset_ts = _sun_times(cfg, tomorrow)
        evals         = _evaluate_eod_flights(cfg, tomorrow, sunrise_ts, sunset_ts)
        all_qualifying = [e for e in evals if e.qualifying]
        all_filtered   = [e for e in evals if not e.qualifying]

        if period == "best":
            session_secs = cfg.spot_rec_session_hours * 3600
            _populate_departures(all_qualifying, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
            all_qualifying, all_filtered = _apply_pre_sunrise_gate(all_qualifying, all_filtered, sunrise_ts, sunset_ts, cfg.spot_rec_lighting_gate)
            qualifying   = _best_session_window(all_qualifying, session_secs)
            best_start   = min((e.session_ts for e in qualifying), default=None)
            filtered     = [f for f in all_filtered
                            if best_start and best_start <= f.arrival_ts <= best_start + session_secs]
            _populate_departures(filtered, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
        elif period == "allday":
            qualifying = all_qualifying
            filtered   = all_filtered
            _populate_departures(qualifying + filtered, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
            qualifying, filtered = _apply_pre_sunrise_gate(qualifying, filtered, sunrise_ts, sunset_ts, cfg.spot_rec_lighting_gate)
        else:
            if period == "morning":
                ws = sunrise_ts - cfg.summary_morning_pre_sunrise_hours * 3600
                we = _day_hour_ts(tomorrow, cfg.summary_morning_end_hour, tz)
            else:  # afternoon
                ws = _day_hour_ts(tomorrow, cfg.summary_afternoon_start_hour, tz)
                we = sunset_ts + cfg.summary_afternoon_post_sunset_hours * 3600
            qualifying = [e for e in all_qualifying if ws <= e.arrival_ts <= we]
            filtered   = [e for e in all_filtered   if ws <= e.arrival_ts <= we]
            _populate_departures(qualifying + filtered, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
            qualifying, filtered = _apply_pre_sunrise_gate(qualifying, filtered, sunrise_ts, sunset_ts, cfg.spot_rec_lighting_gate)

        weather = get_forecast_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz, day_offset=1)
        header  = f"Spot check — {tomorrow.strftime('%A %-d %b')} ({_PERIOD_LABELS[period]})"
        msg = _build_detail_message(qualifying, filtered, cfg.spot_rec_threshold,
                                    weather, cfg.spot_rec_weather_gate,
                                    header, tz, sunrise_ts, sunset_ts, show_verdict=show_verdict,
                                    scenario_a=scenario_a, now_ts=now_ts, show_window=(period == "best"))

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

    evals = _evaluate_rolling_flights(cfg, window_start, window_end, sunrise_ts, sunset_ts)
    qualifying = [e for e in evals if e.qualifying]
    filtered   = [e for e in evals if not e.qualifying]
    _populate_departures(qualifying, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
    qualifying, filtered = _apply_pre_sunrise_gate(qualifying, filtered, sunrise_ts, sunset_ts, cfg.spot_rec_lighting_gate)

    weather = get_current_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz)
    msg = _build_detail_message(
        qualifying, filtered, cfg.spot_rec_threshold,
        weather, cfg.spot_rec_weather_gate,
        "Spotting update — time to head out", tz, sunrise_ts, sunset_ts,
    )
    for dest_chat_id in cfg.all_chat_ids:
        try:
            await context.bot.send_message(chat_id=dest_chat_id, text=msg, parse_mode="HTML")
        except Exception as exc:
            log.error("Failed to send spot follow-up to %s: %s", dest_chat_id, exc)

    cfg.store.save_setting("SPOT_REC_ROLLING_LAST_TS", str(now_ts))
    log.info("Sent spot follow-up message")


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
