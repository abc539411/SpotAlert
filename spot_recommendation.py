from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field as dc_field, replace as dc_replace
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import pytz
import requests
from astral import LocationInfo
from astral.sun import sun
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from monitor import _parse_aircraft, _safe_get, _registration_flag
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

    from monitor import _is_special_livery_airline
    if _is_special_livery_airline(airline_name, cfg.livery_keywords, cfg.livery_exclude_keywords):
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
    """Per-cluster rolling notification.

    Clusters all of today's notified flights (full day window). For each eligible cluster
    that falls within the notify window [travel_mins, notify_window_hours], fires a
    notification if any flight in the cluster has never been included in a cluster
    notification (cluster_notified_ts IS NULL). Marks all cluster flights on send.
    Re-fires if a new flight joins the cluster on a later check.
    """
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

    sunrise_ts, sunset_ts = _sun_times(cfg, today)

    # Full day window so past arrivals form accurate clusters with future departures
    import datetime as _dt
    window_start = int(tz.localize(_dt.datetime.combine(today, _dt.time(0, 0))).timestamp())
    window_end   = int(tz.localize(_dt.datetime.combine(today, _dt.time(23, 59, 59))).timestamp())

    evals = _evaluate_rolling_flights(cfg, window_start, window_end, sunrise_ts, sunset_ts)
    qualifying = [e for e in evals if e.qualifying]
    filtered   = [e for e in evals if not e.qualifying]
    _populate_departures(qualifying + filtered, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
    qualifying, filtered = _apply_pre_sunrise_gate(qualifying, filtered, sunrise_ts, sunset_ts, cfg.spot_rec_lighting_gate)

    clusters, _ = _cluster_flights(
        qualifying, filtered,
        max_gap_secs=cfg.spot_rec_max_gap_hours * 3600,
        notable_lull_secs=cfg.spot_rec_notable_lull_mins * 60,
        max_lulls=cfg.spot_rec_max_lulls,
        **_lighting_kwargs(cfg, sunrise_ts, sunset_ts),
    )

    weather = None
    travel_secs = cfg.spot_rec_travel_mins * 60
    window_secs  = cfg.spot_rec_notify_window_hours * 3600

    for cluster in clusters:
        if len(cluster.flights) < cfg.spot_rec_threshold:
            continue

        notify_gap = cluster.recommended_start_ts - now_ts
        if notify_gap < travel_secs:
            continue   # too close (or already past) — can't make it
        if notify_gap > window_secs:
            continue   # too far ahead — wait

        # Fire only if at least one flight in the cluster is new (never been cluster-notified)
        has_new = any(f.cluster_notified_ts is None for f in cluster.flights)
        if not has_new:
            continue

        if weather is None:
            weather = get_current_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz)
        if cfg.spot_rec_weather_gate and weather and weather.is_severe:
            return

        n = len(cluster.flights)
        start_str  = datetime.fromtimestamp(cluster.recommended_start_ts).astimezone(tz).strftime("%H:%M")
        end_str    = datetime.fromtimestamp(cluster.end_ts).astimezone(tz).strftime("%H:%M")
        window_str = start_str if start_str == end_str else f"{start_str} – {end_str}"

        lines = [
            f"<b>Head out now — {n} interesting flight{'s' if n != 1 else ''}</b>",
            f"Window: {window_str}",
            "",
        ]
        lines.extend(_render_flights_with_lulls(cluster.flights, cluster.filtered, cluster.lulls, tz))
        lines.append("")
        if weather:
            lines.append(f"Weather: {weather}")
        lines.append(_sun_line(sunrise_ts, sunset_ts, tz))

        sent = False
        for dest_chat_id in cfg.all_chat_ids:
            try:
                await context.bot.send_message(chat_id=dest_chat_id, text="\n".join(lines),
                                               parse_mode="HTML", disable_web_page_preview=True)
                sent = True
            except Exception as exc:
                log.error("Failed to send rolling spot recommendation to %s: %s", dest_chat_id, exc)

        if sent:
            cfg.store.mark_cluster_notified([f.registration for f in cluster.flights], now_ts)
            log.info("Sent rolling spot notification for cluster %s–%s (%d flights)",
                     start_str, end_str, n)


# ------------------------------------------------------------------
# End-of-day recommendation (scheduled job)
# ------------------------------------------------------------------

async def run_eod_recommendation(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-fired tonight for tomorrow — same pipeline as manual tomorrow spot check."""
    cfg = context.bot_data["cfg"]
    if not cfg.spot_rec_enabled:
        return

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
    _populate_departures(qualifying + filtered, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
    qualifying, filtered = _apply_pre_sunrise_gate(qualifying, filtered, sunrise_ts, sunset_ts, cfg.spot_rec_lighting_gate)

    clusters, orphaned = _cluster_flights(
        qualifying, filtered,
        max_gap_secs=cfg.spot_rec_max_gap_hours * 3600,
        notable_lull_secs=cfg.spot_rec_notable_lull_mins * 60,
        max_lulls=cfg.spot_rec_max_lulls,
        **_lighting_kwargs(cfg, sunrise_ts, sunset_ts),
    )

    eligible = [c for c in clusters if len(c.flights) >= cfg.spot_rec_threshold][:cfg.spot_rec_max_windows]
    if not eligible:
        return

    weather = get_forecast_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz, day_offset=1)
    if cfg.spot_rec_weather_gate and weather and weather.is_severe:
        return

    day_str = tomorrow.strftime("%A %-d %b")
    header = f"Spotting recommendation for {day_str}"
    msg = _build_clusters_message(eligible, clusters, weather, cfg.spot_rec_weather_gate,
                                  header, tz, sunrise_ts, sunset_ts, orphaned_filtered=orphaned)

    # Keyboard: single cluster → Yes/Maybe/No; multiple → window buttons + Maybe/No
    if len(eligible) == 1:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Yes ✓", callback_data="spot_yes"),
            InlineKeyboardButton("Maybe", callback_data="spot_maybe"),
            InlineKeyboardButton("No ✗", callback_data="spot_no"),
        ]])
    else:
        window_buttons = []
        for i, c in enumerate(eligible):
            s = datetime.fromtimestamp(c.recommended_start_ts).astimezone(tz).strftime("%H:%M")
            e = datetime.fromtimestamp(c.end_ts).astimezone(tz).strftime("%H:%M")
            window_buttons.append(InlineKeyboardButton(
                f"{s}–{e} · {len(c.flights)}✈", callback_data=f"spot_window_{i}"
            ))
        keyboard = InlineKeyboardMarkup([
            window_buttons,
            [InlineKeyboardButton("Maybe", callback_data="spot_maybe"),
             InlineKeyboardButton("No ✗", callback_data="spot_no")],
        ])

    cfg.store.save_setting("SPOT_REC_PENDING_WINDOWS", json.dumps([c.recommended_start_ts for c in eligible]))
    cfg.store.save_setting("SPOT_REC_PENDING_SESSION_TS", str(eligible[0].recommended_start_ts))

    for dest_chat_id in cfg.all_chat_ids:
        try:
            kb = keyboard if cfg.store.is_admin(dest_chat_id) else None
            await context.bot.send_message(
                chat_id=dest_chat_id, text=msg,
                parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True,
            )
        except Exception as exc:
            log.error("Failed to send EOD recommendation to %s: %s", dest_chat_id, exc)

    log.info("Sent EOD spot recommendation (%d cluster(s))", len(eligible))


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
    session_ts: Optional[int] = None # event time shown in recommendation
    show_dep: bool = False           # True when both arr and dep fall within the cluster
    lighting_zone: Optional[str] = None      # overall zone (used in scenario B / session_ts display)
    arr_lighting_zone: Optional[str] = None  # zone for arrival timestamp specifically
    dep_lighting_zone: Optional[str] = None  # zone for departure timestamp specifically
    cluster_notified_ts: Optional[int] = None  # when this flight was last included in a rolling cluster notification


@dataclass
class SpotCluster:
    flights: List[FlightEval]              # qualifying flights in this cluster
    filtered: List[FlightEval]             # filtered-out flights near this cluster
    start_ts: int                          # earliest event in cluster
    end_ts: int                            # latest event in cluster
    recommended_start_ts: int              # latest "be at airport by" time (all flights still catchable)
    lulls: List[Tuple[int, int]] = dc_field(default_factory=list)  # (gap_start_ts, gap_end_ts)



def _lookup_departure_for_flight(
    cfg, arrival_fn: str, arrival_ts: int = 0
) -> Tuple[Optional[str], Optional[int], str]:
    """Look up departure info for an arrival flight number from DB.

    Priority chain for dep_ts:
      a) estimated_dep_ts if still in the future
      b) scheduled_dep_ts if still in the future
      c) arrival_ts + turnaround_secs (derived from scheduled times, day-agnostic)
      d) None (caller shows flight number only, no time)
    """
    if not arrival_fn:
        return None, None, ""
    predicted = cfg.store.get_predicted_departure(arrival_fn, cfg.airport_iata, cfg.departure_pattern_threshold)
    if not predicted:
        return None, None, ""
    dep_fn, _, _, _ = predicted
    dep_info = cfg.store.get_predicted_dep_info(dep_fn, cfg.airport_iata)
    if not dep_info:
        return dep_fn, None, "Predicted"

    actual_ts       = dep_info.get("actual_dep_ts")
    estimated_ts    = dep_info.get("estimated_dep_ts")
    sched_ts        = dep_info.get("scheduled_dep_ts")
    turnaround_secs = dep_info.get("turnaround_secs")

    # A timestamp is only plausible if it falls after this arrival and within 36h —
    # prevents cross-day contamination when the stored timestamp is from a different date.
    def _plausible(ts):
        return bool(ts and arrival_ts and arrival_ts <= ts <= arrival_ts + 36 * 3600)

    if _plausible(actual_ts):
        return dep_fn, actual_ts, "Actual"
    if turnaround_secs and arrival_ts:
        return dep_fn, arrival_ts + turnaround_secs, "Predicted"
    if _plausible(estimated_ts):
        return dep_fn, estimated_ts, "Estimated"
    if _plausible(sched_ts):
        return dep_fn, sched_ts, "Scheduled"
    return dep_fn, None, "Predicted"


_LIGHT_EMOJI = {"too_early": "🌅", "bad_light": "☀️", "too_late": "🌇"}


def _lighting_quality(
    ts: int,
    sunrise_ts: int,
    sunset_ts: int,
    sunrise_buffer_secs: int,
    sunset_buffer_secs: int,
    bad_light_start: str,
    bad_light_end: str,
    airport_tz: str,
) -> Optional[str]:
    """Return lighting zone for a timestamp, or None if lighting is good.

    Priority: too_early > bad_light > too_late
    (too_early wins because pre-sunrise is the hardest constraint)
    """
    if not ts:
        return None
    if sunrise_ts and sunrise_buffer_secs and sunrise_ts <= ts < sunrise_ts + sunrise_buffer_secs:
        return "too_early"
    if bad_light_start and bad_light_end and airport_tz:
        try:
            tz = pytz.timezone(airport_tz)
            time_str = datetime.fromtimestamp(ts).astimezone(tz).strftime("%H:%M")
            if bad_light_start <= time_str <= bad_light_end:
                return "bad_light"
        except Exception:
            pass
    if sunset_ts and sunset_buffer_secs and ts > sunset_ts - sunset_buffer_secs:
        return "too_late"
    return None


def _flight_lighting_zone(
    f: "FlightEval",
    sunrise_ts: int, sunset_ts: int,
    sunrise_buffer_secs: int, sunset_buffer_secs: int,
    bad_light_start: str, bad_light_end: str,
    airport_tz: str,
) -> Optional[str]:
    """Return worst lighting zone across arrival and departure timestamps for a flight."""
    _PRIORITY = {"too_early": 0, "bad_light": 1, "too_late": 2}
    zones = []
    for ts in filter(None, [f.arrival_ts, f.dep_ts]):
        z = _lighting_quality(ts, sunrise_ts, sunset_ts, sunrise_buffer_secs,
                              sunset_buffer_secs, bad_light_start, bad_light_end, airport_tz)
        if z:
            zones.append(z)
    if not zones:
        return None
    return min(zones, key=lambda z: _PRIORITY[z])


def _lighting_kwargs(cfg, sunrise_ts: int, sunset_ts: int) -> dict:
    """Build the lighting quality keyword args for _cluster_flights from cfg."""
    return dict(
        sunrise_ts=sunrise_ts,
        sunset_ts=sunset_ts,
        sunrise_buffer_secs=cfg.spot_rec_sunrise_buffer_mins * 60,
        sunset_buffer_secs=cfg.spot_rec_sunset_buffer_mins * 60,
        bad_light_start=cfg.spot_rec_bad_light_start,
        bad_light_end=cfg.spot_rec_bad_light_end,
        airport_tz=cfg.airport_tz,
    )


def _lull_line(lull_start_ts: int, lull_end_ts: int, tz) -> str:
    """Format a notable gap within a cluster as a single display line."""
    duration_mins = (lull_end_ts - lull_start_ts) // 60
    h, m = divmod(duration_mins, 60)
    dur_str = f"{h}h {m}min" if h and m else (f"{h}h" if h else f"{m}min")
    start_str = datetime.fromtimestamp(lull_start_ts).astimezone(tz).strftime("%H:%M")
    end_str   = datetime.fromtimestamp(lull_end_ts).astimezone(tz).strftime("%H:%M")
    return f"  ⏸ Break time ({start_str} – {end_str}, {dur_str})"


def _render_flights_with_lulls(
    flights, filtered, lulls, tz,
    now_ts: int = 0,
) -> List[str]:
    """Render flights interleaved with lull lines in chronological order.

    Lull is inserted before the first flight whose arrival marks the end of
    the break (lull_end_ts <= flight.arrival_ts), so break lines sit between
    the last flight before the gap and the first flight after it.
    Filtered (italic) flights are appended at the end.
    """
    lines = []
    lull_iter = iter(lulls)
    next_lull = next(lull_iter, None)

    for e in flights:
        if now_ts > 0 and e.arrival_ts < now_ts and (not e.dep_ts or e.dep_ts < now_ts):
            continue
        while next_lull and next_lull[1] <= e.arrival_ts:
            lines.append(_lull_line(next_lull[0], next_lull[1], tz))
            next_lull = next(lull_iter, None)
        lines.append(_flight_line(e, tz))

    while next_lull:
        lines.append(_lull_line(next_lull[0], next_lull[1], tz))
        next_lull = next(lull_iter, None)

    for e in filtered:
        lines.append(f"<i>{_flight_line(e, tz, include_reason=True)}</i>")

    return lines


def _cluster_flights(
    qualifying: List[FlightEval],
    filtered: List[FlightEval],
    max_gap_secs: int,
    notable_lull_secs: int,
    max_lulls: int,
    sunrise_ts: int = 0,
    sunset_ts: int = 0,
    sunrise_buffer_secs: int = 0,
    sunset_buffer_secs: int = 0,
    bad_light_start: str = "",
    bad_light_end: str = "",
    airport_tz: str = "",
) -> List[SpotCluster]:
    """Group qualifying flights into natural activity clusters separated by gaps > max_gap_secs.

    Lighting quality is computed per flight and used as a soft tiebreaker
    when choosing the recommended start time within each cluster.
    """
    lighting_kwargs = dict(
        sunrise_ts=sunrise_ts, sunset_ts=sunset_ts,
        sunrise_buffer_secs=sunrise_buffer_secs, sunset_buffer_secs=sunset_buffer_secs,
        bad_light_start=bad_light_start, bad_light_end=bad_light_end,
        airport_tz=airport_tz,
    )
    if not qualifying:
        if not filtered:
            return []
        f_sorted = sorted(filtered, key=lambda x: x.arrival_ts)
        return [SpotCluster(
            flights=[],
            filtered=f_sorted,
            start_ts=f_sorted[0].arrival_ts,
            end_ts=f_sorted[-1].arrival_ts,
            recommended_start_ts=f_sorted[0].arrival_ts,
        )]

    # Build sorted event list: (timestamp, FlightEval) from qualifying flights only
    events: List[Tuple[int, FlightEval]] = []
    for f in qualifying:
        events.append((f.arrival_ts, f))
        if f.dep_ts:
            events.append((f.dep_ts, f))
    events.sort(key=lambda x: x[0])

    # Greedily group events into clusters by gap threshold
    raw_clusters: List[List[Tuple[int, FlightEval]]] = []
    current = [events[0]]
    for ts, f in events[1:]:
        if ts - current[-1][0] > max_gap_secs:
            raw_clusters.append(current)
            current = [(ts, f)]
        else:
            current.append((ts, f))
    raw_clusters.append(current)

    result: List[SpotCluster] = []

    for raw in raw_clusters:
        cluster_start = raw[0][0]
        cluster_end   = raw[-1][0]

        # Flights whose any event falls within this cluster's range.
        # Copy each FlightEval so per-cluster fields (session_ts, show_dep,
        # lighting_zone) don't clobber state when a flight spans two clusters
        # (e.g. arrives in cluster 1 but departs in cluster 2).
        cluster_flights = []
        for f in qualifying:
            if (cluster_start <= f.arrival_ts <= cluster_end
                    or (f.dep_ts and cluster_start <= f.dep_ts <= cluster_end)):
                cluster_flights.append(dc_replace(f))

        # Set session_ts, show_dep, and lighting_zone per cluster copy.
        # Lighting zone is computed only from timestamps that fall within this
        # cluster (arr_in / dep_in) so out-of-cluster departures don't dilute it.
        _PRIORITY = {"too_early": 0, "bad_light": 1, "too_late": 2}
        for f in cluster_flights:
            arr_in = cluster_start <= f.arrival_ts <= cluster_end
            dep_in = bool(f.dep_ts and cluster_start <= f.dep_ts <= cluster_end)
            f.session_ts = f.arrival_ts if arr_in else f.dep_ts
            f.show_dep   = arr_in and dep_in
            check_ts = ([f.arrival_ts] if arr_in else []) + ([f.dep_ts] if dep_in and f.dep_ts else [])
            zones = [z for ts in check_ts if (z := _lighting_quality(ts, **lighting_kwargs))]
            f.lighting_zone = min(zones, key=lambda z: _PRIORITY[z]) if zones else None
            f.arr_lighting_zone = _lighting_quality(f.arrival_ts, **lighting_kwargs) if arr_in else None
            f.dep_lighting_zone = _lighting_quality(f.dep_ts, **lighting_kwargs) if (dep_in and f.dep_ts) else None

        # Recommended start: latest time you can arrive and still catch every flight.
        # A flight is only catchable via its dep_ts when the departure is actually
        # within this cluster (show_dep=True); out-of-cluster departures don't count.
        all_events = sorted(set(
            [f.arrival_ts for f in cluster_flights] +
            [f.dep_ts for f in cluster_flights if f.dep_ts and f.show_dep]
        ))
        best_start = cluster_start
        best_good  = -1
        best_ts    = -1
        for ei in all_events:
            catchable = [
                f for f in cluster_flights
                if f.arrival_ts >= ei or (f.show_dep and f.dep_ts and f.dep_ts >= ei)
            ]
            if len(catchable) < len(cluster_flights):
                continue
            good_light = sum(1 for f in catchable if f.lighting_zone is None)
            if good_light > best_good or (good_light == best_good and ei > best_ts):
                best_good  = good_light
                best_ts    = ei
                best_start = ei
        recommended_start_ts = best_start
        # cluster_end_ts: last event the user must be present for given they arrive at
        # recommended_start_ts. Same logic as lull detection:
        # - arrival catchable (>= rec_start): window extends to arrival, NOT the departure
        # - arrival missed (< rec_start): window extends to departure (still catchable)
        _end_candidates = []
        for f in cluster_flights:
            _arr_in = cluster_start <= f.arrival_ts <= cluster_end
            _dep_in = bool(f.dep_ts and cluster_start <= f.dep_ts <= cluster_end)
            if _arr_in and f.arrival_ts >= recommended_start_ts:
                _end_candidates.append(f.arrival_ts)
            elif _dep_in and (not _arr_in or f.arrival_ts < recommended_start_ts):
                _end_candidates.append(f.dep_ts)
        cluster_end_ts = max(_end_candidates) if _end_candidates else recommended_start_ts

        # Lull detection: for each flight, use the earliest event the user must be present for.
        # - If arrival is catchable (>= recommended_start_ts): use arrival. Departure is skipped
        #   because the user is already at the airport for the arrival.
        # - If arrival is before recommended_start_ts (user misses it): use departure instead,
        #   since they can still catch the plane departing.
        lull_ts_set: set = set()
        for f in cluster_flights:
            arr_in = cluster_start <= f.arrival_ts <= cluster_end
            dep_in = bool(f.dep_ts and cluster_start <= f.dep_ts <= cluster_end)
            arrival_catchable = arr_in and f.arrival_ts >= recommended_start_ts
            if arrival_catchable:
                lull_ts_set.add(f.arrival_ts)
            elif dep_in:
                lull_ts_set.add(f.dep_ts)
        event_times = sorted(ts for ts in lull_ts_set
                             if recommended_start_ts <= ts <= cluster_end_ts)
        gaps = [
            (event_times[i+1] - event_times[i], event_times[i], event_times[i+1])
            for i in range(len(event_times) - 1)
            if event_times[i+1] - event_times[i] > notable_lull_secs
        ]
        gaps.sort(reverse=True)
        lulls = sorted((s, e) for _, s, e in gaps[:max_lulls])

        result.append(SpotCluster(
            flights=sorted(cluster_flights, key=lambda x: x.arrival_ts),
            filtered=[],
            start_ts=cluster_start,
            end_ts=cluster_end_ts,
            recommended_start_ts=recommended_start_ts,
            lulls=lulls,
        ))

    # Assign filtered flights to a cluster only if arrival falls within its time range.
    # Flights outside all clusters go into orphaned_filtered (shown as a separate section).
    orphaned_filtered: List[FlightEval] = []
    for f in filtered:
        f.lighting_zone = _flight_lighting_zone(f, **lighting_kwargs)
        f.arr_lighting_zone = _lighting_quality(f.arrival_ts, **lighting_kwargs)
        f.dep_lighting_zone = _lighting_quality(f.dep_ts, **lighting_kwargs) if f.dep_ts else None
        cluster_match = next(
            (c for c in result if c.recommended_start_ts <= f.arrival_ts <= c.end_ts), None
        )
        if cluster_match:
            cluster_match.filtered.append(f)
        else:
            orphaned_filtered.append(f)

    for c in result:
        c.filtered.sort(key=lambda x: x.arrival_ts)
    orphaned_filtered.sort(key=lambda x: x.arrival_ts)

    return result, orphaned_filtered


def _build_clusters_message(
    eligible: List[SpotCluster],
    all_clusters: List[SpotCluster],
    weather,
    weather_gate: bool,
    header: str,
    tz,
    sunrise_ts: int,
    sunset_ts: int,
    now_ts: int = 0,
    orphaned_filtered: List[FlightEval] = None,
) -> str:
    """Build the spot check message for cluster-based (Best Time to Go) mode."""
    severe_weather = weather_gate and weather and weather.is_severe
    lines = [f"<b>{header}</b>"]

    clusters_to_show = eligible if eligible else all_clusters

    if severe_weather:
        lines.append("Not recommended — severe weather")
    elif not eligible:
        lines.append("No qualifying sessions found.")

    lines.append("")

    if not clusters_to_show:
        lines.append("  No interesting flights found.")
    else:
        multi = len(clusters_to_show) > 1
        for i, cluster in enumerate(clusters_to_show):
            start_str = datetime.fromtimestamp(cluster.recommended_start_ts).astimezone(tz).strftime("%H:%M")
            end_str   = datetime.fromtimestamp(cluster.end_ts).astimezone(tz).strftime("%H:%M")
            n = len(cluster.flights)
            if multi:
                lines.append(f"<b>Option {i+1} — {start_str} – {end_str} · {n} flight{'s' if n != 1 else ''}</b>")
            else:
                window_str = start_str if start_str == end_str else f"{start_str} – {end_str}"
                lines.append(f"Window: {window_str}")

            lines.extend(_render_flights_with_lulls(cluster.flights, cluster.filtered, cluster.lulls, tz, now_ts=now_ts))
            lines.append("")

    # Collect qualifying flights from clusters not already shown above
    shown_set = set(id(c) for c in clusters_to_show)
    no_cluster_qualifying = [
        f for c in all_clusters if id(c) not in shown_set
        for f in c.flights
    ]

    no_cluster_section = no_cluster_qualifying + (orphaned_filtered or [])
    if no_cluster_section:
        lines.append(f"Also interesting ({len(no_cluster_section)}):")
        for e in no_cluster_qualifying:
            lines.append(_flight_line(e, tz))
        for e in (orphaned_filtered or []):
            lines.append(f"<i>{_flight_line(e, tz, include_reason=True)}</i>")
        lines.append("")

    lines.append(f"Weather: {weather}" if weather else "Weather: unavailable")
    if sunrise_ts and sunset_ts:
        lines.append(_sun_line(sunrise_ts, sunset_ts, tz))

    return "\n".join(lines)


def _populate_departures(flights: List["FlightEval"], cfg,
                         sunset_ts: int = 0, sunrise_ts: int = 0) -> None:
    """Populate dep_fn, dep_ts, dep_time_label on each FlightEval in-place.

    If lighting gate is enabled and sunset_ts is provided, dep_ts values outside
    daylight (before sunrise or after sunset) are cleared.
    """
    for e in flights:
        if not e.flight_number:
            continue
        dep_fn, dep_ts, dep_label = _lookup_departure_for_flight(cfg, e.flight_number, e.arrival_ts)
        if dep_fn:
            e.dep_fn = dep_fn
            e.dep_time_label = dep_label
            if dep_ts and cfg.spot_rec_lighting_gate and sunset_ts:
                if dep_ts > sunset_ts or (sunrise_ts and dep_ts < sunrise_ts):
                    dep_ts = None
            e.dep_ts = dep_ts



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

        if cfg.store.is_excluded(registration):
            continue

        notif_type    = record["notif_type"] or "Interesting"
        extra_info          = record["extra_info"] or ""
        flight_number       = record["flight_number"] or ""
        detail              = record["detail"] or ""
        cluster_notified_ts = record["cluster_notified_ts"] if "cluster_notified_ts" in record.keys() else None
        arr_str             = datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%H:%M")
        livery              = extra_info if notif_type == "Special Livery" else ""

        if cfg.spot_rec_lighting_gate and not _passes_lighting_gate(arrival_ts, sunrise_ts, sunset_ts):
            results.append(FlightEval(arrival_ts, registration, notif_type, False,
                                      f"arrives after sunset ({arr_str})", detail, livery, flight_number,
                                      cluster_notified_ts=cluster_notified_ts))
            continue

        if cfg.spot_rec_max_spotted_times > 0 and cfg.catalog:
            count = cfg.catalog.get_session_count_at_airport(registration, cfg.airport_iata)
            if count >= cfg.spot_rec_max_spotted_times:
                results.append(FlightEval(arrival_ts, registration, notif_type, False,
                                          f"photographed {count} times at {cfg.airport_iata}", detail, livery, flight_number,
                                          cluster_notified_ts=cluster_notified_ts))
                continue

        results.append(FlightEval(arrival_ts, registration, notif_type, True, "", detail, livery, flight_number,
                                  cluster_notified_ts=cluster_notified_ts))

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
    """Format a flight entry for display. Always shows arr and dep (if known)."""
    if f.livery:
        type_str = f"{f.notif_type} ({f.livery})"
    else:
        type_str = f.notif_type or ""

    times = []
    arr = datetime.fromtimestamp(f.arrival_ts).astimezone(tz).strftime("%H:%M")
    arr_emoji = _LIGHT_EMOJI.get(f.arr_lighting_zone or "", "")
    times.append(f"arr {arr}{' ' + arr_emoji if arr_emoji else ''}")
    if f.dep_ts:
        dep = datetime.fromtimestamp(f.dep_ts).astimezone(tz).strftime("%H:%M")
        dep_emoji = _LIGHT_EMOJI.get(f.dep_lighting_zone or "", "")
        times.append(f"dep {dep}{' ' + dep_emoji if dep_emoji else ''}")
    time_str = " / ".join(times)

    flag = _registration_flag(f.registration)
    fr24_url = f"https://www.flightradar24.com/data/aircraft/{f.registration.lower()}"
    reg_str = f'<a href="{fr24_url}">{f.registration}</a>{" " + flag if flag else ""}'
    parts = [f"  • {reg_str}"]
    if type_str:
        parts.append(type_str)
    if f.detail:
        parts.append(f.detail)
    if include_reason and f.reason:
        parts.append(f.reason)
    if time_str and time_str != "—":
        parts.append(time_str)
    return " — ".join(parts)






def _day_hour_ts(d, hour: int, tz) -> int:
    import datetime as _dt
    return int(tz.localize(_dt.datetime.combine(d, _dt.time(hour, 0))).timestamp())


async def _run_spot_check(send_fn, context: ContextTypes.DEFAULT_TYPE, day: str) -> None:
    """Manual spot check — clusters all notified flights for the day and displays everything."""
    cfg = context.bot_data["cfg"]
    tz = pytz.timezone(cfg.airport_tz)
    now = datetime.now(tz)
    now_ts = int(now.timestamp())

    await send_fn("Checking...")

    import datetime as _dt

    if day == "today":
        target_date = now.date()
        sunrise_ts, sunset_ts = _sun_times(cfg, target_date)
        # Full day window so past arrivals are included — gives complete cluster picture
        window_start = int(tz.localize(_dt.datetime.combine(target_date, _dt.time(0, 0))).timestamp())
        window_end   = int(tz.localize(_dt.datetime.combine(target_date, _dt.time(23, 59, 59))).timestamp())
        evals        = _evaluate_rolling_flights(cfg, window_start, window_end, sunrise_ts, sunset_ts)
        qualifying   = [e for e in evals if e.qualifying]
        filtered     = [e for e in evals if not e.qualifying]
        _populate_departures(qualifying + filtered, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
        qualifying, filtered = _apply_pre_sunrise_gate(qualifying, filtered, sunrise_ts, sunset_ts, cfg.spot_rec_lighting_gate)
        clusters, orphaned = _cluster_flights(
            qualifying, filtered,
            max_gap_secs=cfg.spot_rec_max_gap_hours * 3600,
            notable_lull_secs=cfg.spot_rec_notable_lull_mins * 60,
            max_lulls=cfg.spot_rec_max_lulls,
            **_lighting_kwargs(cfg, sunrise_ts, sunset_ts),
        )
        eligible = [c for c in clusters if len(c.flights) >= cfg.spot_rec_threshold][:cfg.spot_rec_max_windows]
        weather  = get_current_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz)
        header   = "Spot check — Today"
        # now_ts=0: show all flights including past — manual check is a full-day review
        msg = _build_clusters_message(eligible, clusters, weather, cfg.spot_rec_weather_gate,
                                      header, tz, sunrise_ts, sunset_ts, now_ts=0,
                                      orphaned_filtered=orphaned)

    else:  # tomorrow
        tomorrow = (now + timedelta(days=1)).date()
        sunrise_ts, sunset_ts = _sun_times(cfg, tomorrow)
        evals        = _evaluate_eod_flights(cfg, tomorrow, sunrise_ts, sunset_ts)
        qualifying   = [e for e in evals if e.qualifying]
        filtered     = [e for e in evals if not e.qualifying]
        _populate_departures(qualifying + filtered, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
        qualifying, filtered = _apply_pre_sunrise_gate(qualifying, filtered, sunrise_ts, sunset_ts, cfg.spot_rec_lighting_gate)
        clusters, orphaned = _cluster_flights(
            qualifying, filtered,
            max_gap_secs=cfg.spot_rec_max_gap_hours * 3600,
            notable_lull_secs=cfg.spot_rec_notable_lull_mins * 60,
            max_lulls=cfg.spot_rec_max_lulls,
            **_lighting_kwargs(cfg, sunrise_ts, sunset_ts),
        )
        eligible = [c for c in clusters if len(c.flights) >= cfg.spot_rec_threshold][:cfg.spot_rec_max_windows]
        weather  = get_forecast_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz, day_offset=1)
        header   = f"Spot check — {tomorrow.strftime('%A %-d %b')}"
        msg = _build_clusters_message(eligible, clusters, weather, cfg.spot_rec_weather_gate,
                                      header, tz, sunrise_ts, sunset_ts, orphaned_filtered=orphaned)

    await send_fn(msg, parse_mode="HTML", disable_web_page_preview=True)


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
    await query.edit_message_reply_markup(reply_markup=None)

    async def send_fn(text, **kwargs):
        await context.bot.send_message(chat_id=query.message.chat_id, text=text, **kwargs)

    await _run_spot_check(send_fn, context, day)


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
    window_end   = sunset_ts

    evals = _evaluate_rolling_flights(cfg, window_start, window_end, sunrise_ts, sunset_ts)
    qualifying = [e for e in evals if e.qualifying]
    filtered   = [e for e in evals if not e.qualifying]
    _populate_departures(qualifying + filtered, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
    qualifying, filtered = _apply_pre_sunrise_gate(qualifying, filtered, sunrise_ts, sunset_ts, cfg.spot_rec_lighting_gate)

    clusters, _ = _cluster_flights(
        qualifying, filtered,
        max_gap_secs=cfg.spot_rec_max_gap_hours * 3600,
        notable_lull_secs=cfg.spot_rec_notable_lull_mins * 60,
        max_lulls=cfg.spot_rec_max_lulls,
        **_lighting_kwargs(cfg, sunrise_ts, sunset_ts),
    )
    # Show the earliest qualifying cluster (same intent as rolling check)
    cluster = next((c for c in clusters if len(c.flights) >= cfg.spot_rec_threshold), None)

    weather = get_current_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz)

    if cluster:
        n = len(cluster.flights)
        start_str  = datetime.fromtimestamp(cluster.recommended_start_ts).astimezone(tz).strftime("%H:%M")
        end_str    = datetime.fromtimestamp(cluster.end_ts).astimezone(tz).strftime("%H:%M")
        window_str = start_str if start_str == end_str else f"{start_str} – {end_str}"
        lines = [
            f"<b>Spotting update — time to head out</b>",
            f"Window: {window_str}",
            "",
        ]
        lines.extend(_render_flights_with_lulls(cluster.flights, cluster.filtered, cluster.lulls, tz))
        lines.append("")
        if weather:
            lines.append(f"Weather: {weather}")
        lines.append(_sun_line(sunrise_ts, sunset_ts, tz))
        msg = "\n".join(lines)
    else:
        msg = _build_clusters_message([], clusters, weather, cfg.spot_rec_weather_gate,
                                      "Spotting update — time to head out", tz, sunrise_ts, sunset_ts)
    for dest_chat_id in cfg.all_chat_ids:
        try:
            await context.bot.send_message(chat_id=dest_chat_id, text=msg, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            log.error("Failed to send spot follow-up to %s: %s", dest_chat_id, exc)

    cfg.store.save_setting("SPOT_REC_ROLLING_LAST_TS", str(now_ts))
    log.info("Sent spot follow-up message")


# ------------------------------------------------------------------
# Inline keyboard callback
# ------------------------------------------------------------------

async def _schedule_spot_followup(context, cfg, session_start: int, query) -> None:
    """Schedule the spot follow-up message and reply to the user."""
    import datetime as _dt
    follow_up_ts = session_start - cfg.spot_rec_travel_mins * 60 - 1800  # 30 min buffer
    now_ts = int(datetime.now().timestamp())
    if follow_up_ts > now_ts:
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
            await _schedule_spot_followup(context, cfg, int(pending_ts), query)
        else:
            await query.message.reply_text("See you out there!")

    elif query.data.startswith("spot_window_"):
        try:
            idx = int(query.data.replace("spot_window_", ""))
            windows_raw = cfg.store.load_setting("SPOT_REC_PENDING_WINDOWS")
            windows = json.loads(windows_raw) if windows_raw else []
            session_start = int(windows[idx])
            cfg.store.save_setting("SPOT_REC_PENDING_SESSION_TS", str(session_start))
            await _schedule_spot_followup(context, cfg, session_start, query)
        except (IndexError, ValueError, TypeError):
            await query.message.reply_text("See you out there!")

    else:  # spot_maybe
        await query.message.reply_text("Maybe see you out there!")


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

def register_spot_rec_handlers(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(handle_spot_response,     pattern="^spot_(yes|maybe|no|window_\\d+)$"))
    app.add_handler(CallbackQueryHandler(handle_spot_day_callback, pattern="^spot_day_(today|tomorrow)$"))
    app.add_handler(CommandHandler("spot", handle_spot_command))
