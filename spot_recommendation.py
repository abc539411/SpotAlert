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
    if cfg.rapid_mode:
        return  # user is at the airport — no rolling notifications needed

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
        lines.extend(_render_flights_with_lulls(cluster.flights, cluster.filtered, cluster.lulls, tz,
                                             window_start=cluster.start_ts, window_end=cluster.end_ts,
                                             check_date=today))
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
            cfg.store.mark_daily_flight_cluster_notified(
                [(f.registration, f.flight_number) for f in cluster.flights], now_ts
            )
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

    clusters, orphaned = _cluster_flights(
        qualifying, filtered,
        max_gap_secs=cfg.spot_rec_max_gap_hours * 3600,
        notable_lull_secs=cfg.spot_rec_notable_lull_mins * 60,
        max_lulls=cfg.spot_rec_max_lulls,
        **_lighting_kwargs(cfg, sunrise_ts, sunset_ts),
    )

    eligible = clusters[:cfg.spot_rec_max_windows]
    if not eligible:
        return

    _refresh_also_interesting_deps(orphaned, cfg, sunrise_ts, sunset_ts)

    weather = get_forecast_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz, day_offset=1)
    if cfg.spot_rec_weather_gate and weather and weather.is_severe:
        return

    day_str = tomorrow.strftime("%A %-d %b")
    header = f"Spotting Recommendation for {day_str}"
    msg = _build_clusters_message(eligible, clusters, weather, cfg.spot_rec_weather_gate,
                                  header, tz, sunrise_ts, sunset_ts, orphaned_filtered=orphaned,
                                  check_date=tomorrow)

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
    spotted_times: Optional[int] = None        # times photographed at this airport (cosmetic only)


@dataclass
class SpotCluster:
    flights: List[FlightEval]              # qualifying flights in this cluster
    filtered: List[FlightEval]             # filtered-out flights near this cluster
    start_ts: int                          # earliest event in cluster
    end_ts: int                            # latest event in cluster
    recommended_start_ts: int              # latest "be at airport by" time (all flights still catchable)
    lulls: List[Tuple[int, int]] = dc_field(default_factory=list)  # (gap_start_ts, gap_end_ts)
    alternative_windows: List[Tuple[int, int]] = dc_field(default_factory=list)  # shorter alternative windows



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


_LIGHT_EMOJI = {"low_light": "🌙", "bad_light": "☀️"}


def _lighting_quality(
    ts: int,
    sunrise_ts: int,
    sunset_ts: int,
    light_buffer_secs: int,
    bad_light_start: str,
    bad_light_end: str,
    airport_tz: str,
) -> Optional[str]:
    """Return lighting zone for a timestamp, or None if lighting is good.

    low_light: ts < sunrise + buffer  OR  ts > sunset - buffer  (🌙)
    bad_light: within the configurable midday bad-light window  (☀️)
    low_light takes priority over bad_light.
    """
    if not ts:
        return None
    if light_buffer_secs:
        if (sunrise_ts and ts < sunrise_ts + light_buffer_secs) or \
           (sunset_ts and ts > sunset_ts - light_buffer_secs):
            return "low_light"
    if bad_light_start and bad_light_end and airport_tz:
        try:
            tz = pytz.timezone(airport_tz)
            time_str = datetime.fromtimestamp(ts).astimezone(tz).strftime("%H:%M")
            if bad_light_start <= time_str <= bad_light_end:
                return "bad_light"
        except Exception:
            pass
    return None


def _flight_lighting_zone(
    f: "FlightEval",
    sunrise_ts: int, sunset_ts: int,
    light_buffer_secs: int,
    bad_light_start: str, bad_light_end: str,
    airport_tz: str,
) -> Optional[str]:
    """Return worst lighting zone across arrival and departure timestamps for a flight."""
    _PRIORITY = {"low_light": 0, "bad_light": 1}
    zones = []
    for ts in filter(None, [f.arrival_ts, f.dep_ts]):
        z = _lighting_quality(ts, sunrise_ts, sunset_ts, light_buffer_secs,
                              bad_light_start, bad_light_end, airport_tz)
        if z:
            zones.append(z)
    if not zones:
        return None
    return min(zones, key=lambda z: _PRIORITY[z])


def _dep_lighting_quality(dep_ts: int, sunrise_ts: int, sunset_ts: int, **lq_kwargs) -> Optional[str]:
    """_lighting_quality adjusted for cross-day departures.
    Shifts sunrise/sunset to the departure day before comparing."""
    day_offset = round((dep_ts - sunrise_ts) / 86400)
    return _lighting_quality(
        dep_ts,
        sunrise_ts=sunrise_ts + day_offset * 86400,
        sunset_ts=sunset_ts  + day_offset * 86400,
        **lq_kwargs,
    )


def _lighting_kwargs(cfg, sunrise_ts: int, sunset_ts: int) -> dict:
    """Build the lighting quality keyword args for _cluster_flights from cfg."""
    return dict(
        sunrise_ts=sunrise_ts,
        sunset_ts=sunset_ts,
        light_buffer_secs=cfg.spot_rec_light_buffer_mins * 60,
        bad_light_start=cfg.spot_rec_bad_light_start,
        bad_light_end=cfg.spot_rec_bad_light_end,
        airport_tz=cfg.airport_tz,
        lighting_gate=cfg.spot_rec_lighting_gate,
    )


def _refresh_also_interesting_deps(flights: list, cfg, sunrise_ts: int, sunset_ts: int) -> None:
    """Repopulate dep_ts on also_interesting flights WITHOUT the lighting gate.

    _populate_departures is called with the lighting gate on planning paths, which clears
    dep_ts for departures after sunset. For display in also_interesting we want to show the
    actual departure time regardless of lighting.
    """
    _populate_departures(flights, cfg)  # no gate
    for e in flights:
        if e.dep_ts:
            e.dep_lighting_zone = _dep_lighting_quality(
                e.dep_ts,
                sunrise_ts=sunrise_ts, sunset_ts=sunset_ts,
                light_buffer_secs=cfg.spot_rec_light_buffer_mins * 60,
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
    window_start: int = 0,
    window_end: int = 0,
    check_date=None,
) -> List[str]:
    """Render qualifying flights and lull lines sorted by sort key.

    Sort key per item:
    - Flight: earliest important event (arrival if both important, or the sole important one)
    - Lull: lull_start_ts
    Flights sort before lulls on equal timestamps.

    An event is "important" (bolded) only if it falls within [window_start, window_end]
    AND not strictly inside any lull [lull_start < ts < lull_end].
    Events outside the window are shown as plain-text context, not bolded.
    Filtered flights append at end in italics with no bolding.
    """
    def _in_lull(ts: int) -> bool:
        return any(s < ts < e for s, e in lulls)

    def _in_window(ts: int) -> bool:
        if not window_start or not window_end:
            return True   # no window provided — treat all as in-window
        return window_start <= ts <= window_end

    def _make_item(f, idx, is_filtered):
        arr_important = _in_window(f.arrival_ts) and not _in_lull(f.arrival_ts)
        dep_important = bool(f.dep_ts and _in_window(f.dep_ts) and not _in_lull(f.dep_ts))
        if arr_important:
            sort_key = f.arrival_ts
        elif dep_important and f.dep_ts:
            sort_key = f.dep_ts
        else:
            sort_key = f.arrival_ts
        return (sort_key, 0, idx, 'flight', f, arr_important, dep_important, is_filtered)

    items = []
    for idx, f in enumerate(flights):
        if now_ts > 0 and f.arrival_ts < now_ts and (not f.dep_ts or f.dep_ts < now_ts):
            continue
        items.append(_make_item(f, idx, False))

    for idx, f in enumerate(filtered):
        items.append(_make_item(f, len(flights) + idx, True))

    for idx, (lull_start, lull_end) in enumerate(lulls):
        items.append((lull_start, 1, idx, 'lull', lull_start, lull_end, None, None))

    items.sort(key=lambda x: (x[0], x[1], x[2]))

    lines = []
    for item in items:
        if item[3] == 'flight':
            _, _, _, _, f, arr_imp, dep_imp, is_filtered = item
            line = _flight_line(f, tz, include_reason=is_filtered,
                                arr_important=arr_imp, dep_important=dep_imp,
                                check_date=check_date)
            lines.append(f"<blockquote>{line}</blockquote>" if is_filtered else line)
        else:
            _, _, _, _, lull_start, lull_end, _, _ = item
            lines.append(_lull_line(lull_start, lull_end, tz))

    return lines


def _gap_cluster_raw(flights: List[FlightEval], max_gap_secs: int, valid_ts_set=None) -> List[List[FlightEval]]:
    """Greedy gap-based clustering of flights by event timestamps.

    Returns a list of clusters; each cluster is a list of FlightEval objects whose
    events (arrivals + departures) fall within the cluster's time span.
    A flight that straddles two clusters appears in both — callers use dc_replace() to copy.
    """
    if not flights:
        return []
    events = _build_events(flights, valid_ts_set)
    if not events:
        return []

    raw: List[List[Tuple[int, FlightEval]]] = []
    current = [events[0]]
    for ts, f in events[1:]:
        if ts - current[-1][0] > max_gap_secs:
            raw.append(current)
            current = [(ts, f)]
        else:
            current.append((ts, f))
    raw.append(current)

    result = []
    for group in raw:
        start, end = group[0][0], group[-1][0]
        cluster_flights = [
            f for f in flights
            if ((valid_ts_set is None or (f.registration, f.arrival_ts) in valid_ts_set)
                and start <= f.arrival_ts <= end)
            or ((f.dep_ts and (valid_ts_set is None or (f.registration, f.dep_ts) in valid_ts_set))
                and start <= f.dep_ts <= end)
        ]
        if cluster_flights:
            result.append(cluster_flights)
    return result


def _truncate_front(remaining: list) -> None:
    """Remove events from the front until the next removal would strand a flight."""
    while len(remaining) > 1:
        _, flight = remaining[0]
        if not any(f is flight for _, f in remaining[1:]):
            break
        remaining.pop(0)


def _truncate_back(remaining: list) -> None:
    """Remove events from the back until the next removal would strand a flight."""
    while len(remaining) > 1:
        _, flight = remaining[-1]
        if not any(f is flight for _, f in remaining[:-1]):
            break
        remaining.pop()


def _build_events(cluster_flights: List[FlightEval], valid_ts_set=None) -> list:
    """Build sorted event list. If valid_ts_set provided, only include (registration, ts) pairs in it."""
    events = []
    for f in cluster_flights:
        if valid_ts_set is None or (f.registration, f.arrival_ts) in valid_ts_set:
            events.append((f.arrival_ts, f))
        if f.dep_ts:
            if valid_ts_set is None or (f.registration, f.dep_ts) in valid_ts_set:
                events.append((f.dep_ts, f))
    events.sort(key=lambda x: x[0])
    return events


def _compute_window_bounds(cluster_flights: List[FlightEval], valid_ts_set=None) -> Tuple[int, int]:
    """Main window: front truncation then back (gives latest possible start)."""
    remaining = _build_events(cluster_flights, valid_ts_set)
    _truncate_front(remaining)
    _truncate_back(remaining)
    return remaining[0][0], remaining[-1][0]




def _compute_alternative_windows(
    cluster_flights: List[FlightEval],
    main_start: int,
    main_end: int,
    valid_ts_set=None,
) -> List[Tuple[int, int]]:
    """Compute back-first and alternating windows; return those with shorter duration.

    Since valid_ts_set already filters out invalid events, all events used here are
    guaranteed to be in daylight — no additional gate check needed.

    Returns up to 2 alternatives (shortest first), deduplicated.
    """
    main_dur = main_end - main_start
    seen: set = set()
    candidates = []

    def _accept(a_start, a_end):
        return a_end - a_start < main_dur and (a_start, a_end) not in seen

    # Back-first
    rem_bf = _build_events(cluster_flights, valid_ts_set)
    _truncate_back(rem_bf)
    _truncate_front(rem_bf)
    bf_start, bf_end = rem_bf[0][0], rem_bf[-1][0]
    if _accept(bf_start, bf_end):
        seen.add((bf_start, bf_end))
        candidates.append((bf_end - bf_start, bf_start, bf_end))

    # Alternating: try front then back each round until no progress
    rem_alt = _build_events(cluster_flights, valid_ts_set)
    while True:
        removed = False
        if len(rem_alt) > 1:
            _, flight = rem_alt[0]
            if any(f is flight for _, f in rem_alt[1:]):
                rem_alt.pop(0)
                removed = True
        if len(rem_alt) > 1:
            _, flight = rem_alt[-1]
            if any(f is flight for _, f in rem_alt[:-1]):
                rem_alt.pop()
                removed = True
        if not removed:
            break
    alt_start, alt_end = rem_alt[0][0], rem_alt[-1][0]
    if _accept(alt_start, alt_end):
        seen.add((alt_start, alt_end))
        candidates.append((alt_end - alt_start, alt_start, alt_end))

    candidates.sort()
    return [(s, e) for _, s, e in candidates[:2]]


def _compute_lulls(
    window_flights: List[FlightEval],
    window_start: int,
    window_end: int,
    notable_lull_secs: int,
    max_lulls: int,
) -> List[Tuple[int, int]]:
    """Find lull times within [window_start, window_end].

    For every pair (E_i, E_j) from different registrations, check whether any
    event E_k between them belongs to a THIRD registration. If so the interval
    is blocked (another plane is present). If all events between belong to
    E_i's or E_j's flight, the interval is a valid lull — the spotter only
    needs one event per flight, so same-flight events don't break the gap.

    Takes top max_lulls by duration if >= notable_lull_secs, displayed in time order.
    """
    events = []
    for f in window_flights:
        if window_start <= f.arrival_ts <= window_end:
            events.append((f.arrival_ts, f.registration))
        if f.dep_ts and window_start <= f.dep_ts <= window_end:
            events.append((f.dep_ts, f.registration))
    events.sort(key=lambda x: x[0])

    candidates = []
    n = len(events)
    for i in range(n):
        ts_i, reg_i = events[i]
        for j in range(i + 1, n):
            ts_j, reg_j = events[j]
            if reg_i == reg_j:
                continue
            # Blocked if any event between belongs to a third registration
            blocked = any(
                reg_k != reg_i and reg_k != reg_j
                for _, reg_k in events[i + 1:j]
            )
            if not blocked:
                gap = ts_j - ts_i
                if gap >= notable_lull_secs:
                    candidates.append((gap, ts_i, ts_j))

    candidates.sort(reverse=True)
    return sorted((s, e) for _, s, e in candidates[:max_lulls])


def _cluster_flights(
    qualifying: List[FlightEval],
    filtered: List[FlightEval],
    max_gap_secs: int,
    notable_lull_secs: int,
    max_lulls: int,
    sunrise_ts: int = 0,
    sunset_ts: int = 0,
    light_buffer_secs: int = 0,
    bad_light_start: str = "",
    bad_light_end: str = "",
    airport_tz: str = "",
    lighting_gate: bool = True,
) -> Tuple[List[SpotCluster], List[FlightEval]]:
    """Iterative window extraction algorithm.

    Before Phase A, individual events outside [sunrise, sunset] are dropped from the
    clustering event pool (if lighting_gate and sunrise_ts are set). Flights with no
    valid events go directly to also_interesting. This ensures window bounds and
    alternative windows are naturally valid without post-hoc gate checks.

    Phase A: Gap-cluster qualifying flights only using valid events.
    Phase B: Assign filtered flights to windows by event time range; orphans → also_interesting.
    Phase C: Compute lulls per window using all events (qualifying + filtered).
    """
    lighting_kwargs = dict(
        sunrise_ts=sunrise_ts, sunset_ts=sunset_ts,
        light_buffer_secs=light_buffer_secs,
        bad_light_start=bad_light_start, bad_light_end=bad_light_end,
        airport_tz=airport_tz,
    )
    _PRIORITY = {"low_light": 0, "bad_light": 1}

    windows: List[SpotCluster] = []
    also_interesting: List[FlightEval] = []
    seen_registrations: set = set()   # dedup also_interesting by registration

    # Build valid event set: only events within [sunrise, sunset] enter the clustering pool
    if lighting_gate and sunrise_ts and sunset_ts:
        valid_ts_set: Optional[set] = set()
        cluster_qualifying = []
        for f in qualifying:
            arr_ok = sunrise_ts <= f.arrival_ts <= sunset_ts
            dep_ok = bool(f.dep_ts and sunrise_ts <= f.dep_ts <= sunset_ts)
            if arr_ok:
                valid_ts_set.add((f.registration, f.arrival_ts))
            if dep_ok:
                valid_ts_set.add((f.registration, f.dep_ts))
            if arr_ok or dep_ok:
                cluster_qualifying.append(f)
            else:
                # No daylight events — goes to also_interesting in italics with reason
                if f.registration not in seen_registrations:
                    seen_registrations.add(f.registration)
                    reason = "no daylight events"
                    also_interesting.append(dc_replace(f, qualifying=False, reason=reason,
                                                         arr_lighting_zone=_lighting_quality(f.arrival_ts, **lighting_kwargs)))
    else:
        valid_ts_set = None
        cluster_qualifying = qualifying

    def _lighting_for(f: FlightEval, w_start: int, w_end: int) -> FlightEval:
        """Set arr/dep lighting zones on a FlightEval copy scoped to [w_start, w_end]."""
        arr_in = w_start <= f.arrival_ts <= w_end
        dep_in = bool(f.dep_ts and w_start <= f.dep_ts <= w_end)
        check_ts = ([f.arrival_ts] if arr_in else []) + ([f.dep_ts] if dep_in and f.dep_ts else [])
        zones = [z for ts in check_ts if (z := _lighting_quality(ts, **lighting_kwargs))]
        f.lighting_zone     = min(zones, key=lambda z: _PRIORITY[z]) if zones else None
        f.arr_lighting_zone = _lighting_quality(f.arrival_ts, **lighting_kwargs)
        f.dep_lighting_zone = _dep_lighting_quality(f.dep_ts, **lighting_kwargs) if f.dep_ts else None
        return f

    # Phase A: iterative extraction using only cluster_qualifying (flights with valid events)
    pool = _gap_cluster_raw(cluster_qualifying, max_gap_secs, valid_ts_set)

    while pool:
        # Pick cluster with most flights; tie-break: earliest arrival
        pool.sort(key=lambda c: (-len(c), min(f.arrival_ts for f in c)))
        best = pool[0]
        rest = pool[1:]

        if len(best) <= 1:
            # All remaining clusters are singles → stop
            for cluster in pool:
                for f in cluster:
                    if f.registration not in seen_registrations:
                        seen_registrations.add(f.registration)
                        also_interesting.append(dc_replace(f,
                            arr_lighting_zone=_lighting_quality(f.arrival_ts, **lighting_kwargs)))
            break

        window_start, window_end = _compute_window_bounds(best, valid_ts_set)
        alt_windows = _compute_alternative_windows(best, window_start, window_end, valid_ts_set)

        # Build per-window flight copies with lighting zones
        window_flights = [
            _lighting_for(dc_replace(f), window_start, window_end)
            for f in best
        ]

        windows.append(SpotCluster(
            flights=sorted(window_flights, key=lambda x: x.arrival_ts),
            filtered=[],        # filled in Phase B
            start_ts=window_start,
            end_ts=window_end,
            recommended_start_ts=window_start,
            lulls=[],           # filled in Phase C
            alternative_windows=alt_windows,
        ))

        # Release remaining clusters back to pool; deduplicate flights by identity
        seen_ids: set = set()
        remaining: List[FlightEval] = []
        for cluster in rest:
            for f in cluster:
                if id(f) not in seen_ids:
                    seen_ids.add(id(f))
                    remaining.append(f)
        pool = _gap_cluster_raw(remaining, max_gap_secs, valid_ts_set)

    # Phase B: assign filtered flights to windows or also_interesting
    for f in filtered:
        f_copy = dc_replace(f)
        f_copy.lighting_zone = _flight_lighting_zone(f_copy, **lighting_kwargs)
        f_copy.arr_lighting_zone = _lighting_quality(f_copy.arrival_ts, **lighting_kwargs)
        f_copy.dep_lighting_zone = _dep_lighting_quality(f_copy.dep_ts, **lighting_kwargs) if f_copy.dep_ts else None

        assigned = False
        for w in windows:
            if (w.start_ts <= f_copy.arrival_ts <= w.end_ts
                    or (f_copy.dep_ts and w.start_ts <= f_copy.dep_ts <= w.end_ts)):
                w.filtered.append(f_copy)
                assigned = True
                break
        if not assigned:
            also_interesting.append(f_copy)

    for w in windows:
        w.filtered.sort(key=lambda x: x.arrival_ts)

    # Phase C: compute lulls per window (qualifying + filtered together)
    for w in windows:
        w.lulls = _compute_lulls(w.flights + w.filtered, w.start_ts, w.end_ts,
                                  notable_lull_secs, max_lulls)

    also_interesting.sort(key=lambda x: x.arrival_ts)
    return windows, also_interesting


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
    check_date=None,
) -> str:
    """Build the spot check message for cluster-based display.

    eligible: windows to show (capped by max_windows); all_clusters ignored (kept for compat).
    orphaned_filtered: also_interesting from _cluster_flights — qualifying singles shown
    normally, filtered orphans shown in italics (differentiated by f.qualifying).
    """
    severe_weather = weather_gate and weather and weather.is_severe
    lines = [f"<b>{header}</b>"]

    if severe_weather:
        lines.append("Not recommended — severe weather")
    elif not eligible:
        lines.append("No qualifying sessions found.")

    lines.append("")

    if not eligible:
        lines.append("  No interesting flights found.")
    else:
        multi = len(eligible) > 1
        for i, cluster in enumerate(eligible):
            start_str = datetime.fromtimestamp(cluster.recommended_start_ts).astimezone(tz).strftime("%H:%M")
            end_str   = datetime.fromtimestamp(cluster.end_ts).astimezone(tz).strftime("%H:%M")
            n = len(cluster.flights)
            if multi:
                lines.append(f"<b>Option {i+1} — {start_str} – {end_str} · {n} flight{'s' if n != 1 else ''}</b>")
            else:
                window_str = start_str if start_str == end_str else f"{start_str} – {end_str}"
                lines.append(f"Window: {window_str}")

            lines.extend(_render_flights_with_lulls(cluster.flights, cluster.filtered, cluster.lulls, tz,
                                             now_ts=now_ts, window_start=cluster.start_ts, window_end=cluster.end_ts,
                                             check_date=check_date))
            for alt_start, alt_end in cluster.alternative_windows:
                def _dur_str(mins):
                    h, m = divmod(mins, 60)
                    return f"{h}h {m}min" if h and m else (f"{h}h" if h else f"{m}min")
                mins_earlier = (cluster.start_ts - alt_start) // 60
                main_dur_mins = (cluster.end_ts - cluster.start_ts) // 60
                alt_dur_mins = (alt_end - alt_start) // 60
                mins_shorter = main_dur_mins - alt_dur_mins
                a_str = datetime.fromtimestamp(alt_start).astimezone(tz).strftime("%H:%M")
                b_str = datetime.fromtimestamp(alt_end).astimezone(tz).strftime("%H:%M")
                lines.append(f"  💡 Also possible: {a_str} – {b_str} (start {_dur_str(mins_earlier)} earlier, {_dur_str(mins_shorter)} shorter)")
            lines.append("")

    # Also interesting: qualifying singles (shown normally) + filtered orphans (italics)
    also = orphaned_filtered or []
    if also:
        lines.append(f"Also interesting ({len(also)}):")
        for e in also:
            if e.qualifying:
                lines.append(_flight_line(e, tz, arr_important=False, dep_important=False, check_date=check_date))
            else:
                lines.append(f"<blockquote>{_flight_line(e, tz, include_reason=True, arr_important=False, dep_important=False, check_date=check_date)}</blockquote>")
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

    lookback_start = window_start - 36 * 3600  # include prev-day arrivals with daylight departures

    for record in cfg.store.get_daily_flights():
        arrival_ts = int(record["arrival_ts"])
        if arrival_ts < lookback_start or arrival_ts > window_end:
            continue

        registration  = record["registration"]

        if cfg.store.is_excluded(registration):
            continue

        notif_type    = record["notif_type"] or "Interesting"
        extra_info          = record["extra_info"] or ""
        flight_number       = record["flight_number"] or ""
        detail              = record["detail"] or ""

        # Cross-day flight (arrived before window start): only include if departure
        # falls within this window — otherwise it has nothing to offer here.
        if arrival_ts < window_start and flight_number:
            _, raw_dep_ts, _ = _lookup_departure_for_flight(cfg, flight_number, arrival_ts)
            if not raw_dep_ts or not (window_start <= raw_dep_ts <= window_end):
                continue
        # Cross-day flights (arrived before this window) are treated as fresh events —
        # Friday's arrival cluster_notified_ts must not suppress Saturday's departure cluster
        is_cross_day = arrival_ts < window_start
        raw_cluster_ts = record["cluster_notified_ts"] if "cluster_notified_ts" in record.keys() else None
        cluster_notified_ts = None if is_cross_day else raw_cluster_ts
        arr_str             = datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%H:%M")
        livery              = extra_info if notif_type == "Special Livery" else ""

        spotted = cfg.catalog.get_session_count_at_airport(registration, cfg.airport_iata) if cfg.catalog else None

        if cfg.spot_rec_max_spotted_times > 0 and spotted is not None and spotted >= cfg.spot_rec_max_spotted_times:
            results.append(FlightEval(arrival_ts, registration, notif_type, False,
                                      f"photographed {spotted} times at {cfg.airport_iata}", detail, livery, flight_number,
                                      cluster_notified_ts=cluster_notified_ts, spotted_times=spotted))
            continue

        results.append(FlightEval(arrival_ts, registration, notif_type, True, "", detail, livery, flight_number,
                                  cluster_notified_ts=cluster_notified_ts, spotted_times=spotted))

    return sorted(results, key=lambda x: x.arrival_ts)


def _evaluate_eod_flights(cfg, tomorrow, sunrise_ts: int, sunset_ts: int) -> List[FlightEval]:
    """Evaluate tomorrow's arrivals using notification_record only (no API calls)."""
    import datetime as _dt
    tz = pytz.timezone(cfg.airport_tz)
    tomorrow_start = int(tz.localize(_dt.datetime.combine(tomorrow, _dt.time(0, 0))).timestamp())
    tomorrow_end   = int(tz.localize(_dt.datetime.combine(tomorrow, _dt.time(23, 59, 59))).timestamp())
    return _evaluate_rolling_flights(cfg, tomorrow_start, tomorrow_end, sunrise_ts, sunset_ts)


def _flight_line(f: "FlightEval", tz, include_reason: bool = False,
                 scenario_a: bool = False, now_ts: int = 0,
                 arr_important: bool = True, dep_important: bool = True,
                 check_date=None) -> str:
    """Format a flight entry for display. Always shows arr and dep (if known).

    arr_important / dep_important: when True the timestamp is bolded to indicate
    the spotter must be present for that event. Non-important events are shown
    in plain text (they fall inside a lull — the spotter need not attend).
    check_date: if provided, cross-day timestamps get a day label (yesterday/tomorrow/day abbrev).
    """
    if f.livery:
        type_str = f.livery          # just the livery name — "Special Livery" prefix dropped
    else:
        type_str = f.notif_type or ""  # Rego Watchlist, Rare Plane, etc. unchanged

    def _day_label(ts: int, ref_date) -> str:
        if ref_date is None:
            return ""
        ts_date = datetime.fromtimestamp(ts).astimezone(tz).date()
        diff = (ts_date - ref_date).days
        if diff == 1:
            return " tomorrow"
        elif diff > 1:
            return " " + ts_date.strftime("%a")
        elif diff == -1:
            return " yesterday"
        elif diff < -1:
            return " " + ts_date.strftime("%a")
        return ""

    def _fmt(label: str, ts: int, zone: Optional[str], important: bool, ref_date=None) -> str:
        t = datetime.fromtimestamp(ts).astimezone(tz).strftime("%H:%M")
        day = _day_label(ts, ref_date)
        emoji = _LIGHT_EMOJI.get(zone or "", "")
        text = f"{label} {t}{day}{' ' + emoji if emoji else ''}"
        return f"<b>{text}</b>" if important else text

    times = [_fmt("arr", f.arrival_ts, f.arr_lighting_zone, arr_important, check_date)]
    if f.dep_ts:
        times.append(_fmt("dep", f.dep_ts, f.dep_lighting_zone, dep_important, check_date))
    time_str = " / ".join(times)

    flag = _registration_flag(f.registration)
    fr24_url = f"https://www.flightradar24.com/data/aircraft/{f.registration.lower()}"
    spotted_str = f" [{f.spotted_times}×]" if f.spotted_times else ""
    reg_str = f'<a href="{fr24_url}">{f.registration}</a>{spotted_str}{" " + flag if flag else ""}'
    parts = [f"  • {reg_str}"]
    if type_str:
        parts.append(type_str)
    if f.detail:
        parts.append(f.detail)
    if include_reason and f.reason:
        if f.reason.startswith("photographed"):
            parts.append("photographed")
        elif f.reason == "no daylight events":
            parts.append("no lights")
        else:
            parts.append(f.reason)
    if time_str:
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
        clusters, orphaned = _cluster_flights(
            qualifying, filtered,
            max_gap_secs=cfg.spot_rec_max_gap_hours * 3600,
            notable_lull_secs=cfg.spot_rec_notable_lull_mins * 60,
            max_lulls=cfg.spot_rec_max_lulls,
            **_lighting_kwargs(cfg, sunrise_ts, sunset_ts),
        )
        eligible = clusters[:cfg.spot_rec_max_windows]
        _refresh_also_interesting_deps(orphaned, cfg, sunrise_ts, sunset_ts)
        weather  = get_current_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz)
        header   = f"Spot Check — {now.strftime('%A %-d %b')}"
        # now_ts=0: show all flights including past — manual check is a full-day review
        msg = _build_clusters_message(eligible, clusters, weather, cfg.spot_rec_weather_gate,
                                      header, tz, sunrise_ts, sunset_ts, now_ts=0,
                                      orphaned_filtered=orphaned, check_date=target_date)

    else:  # tomorrow
        tomorrow = (now + timedelta(days=1)).date()
        sunrise_ts, sunset_ts = _sun_times(cfg, tomorrow)
        evals        = _evaluate_eod_flights(cfg, tomorrow, sunrise_ts, sunset_ts)
        qualifying   = [e for e in evals if e.qualifying]
        filtered     = [e for e in evals if not e.qualifying]
        _populate_departures(qualifying + filtered, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
        clusters, orphaned = _cluster_flights(
            qualifying, filtered,
            max_gap_secs=cfg.spot_rec_max_gap_hours * 3600,
            notable_lull_secs=cfg.spot_rec_notable_lull_mins * 60,
            max_lulls=cfg.spot_rec_max_lulls,
            **_lighting_kwargs(cfg, sunrise_ts, sunset_ts),
        )
        eligible = clusters[:cfg.spot_rec_max_windows]
        _refresh_also_interesting_deps(orphaned, cfg, sunrise_ts, sunset_ts)
        weather  = get_forecast_weather(cfg.airport_lat, cfg.airport_lon, cfg.airport_tz, day_offset=1)
        header   = f"Spot Check — {tomorrow.strftime('%A %-d %b')}"
        msg = _build_clusters_message(eligible, clusters, weather, cfg.spot_rec_weather_gate,
                                      header, tz, sunrise_ts, sunset_ts, orphaned_filtered=orphaned,
                                      check_date=tomorrow)

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
        lines.extend(_render_flights_with_lulls(cluster.flights, cluster.filtered, cluster.lulls, tz,
                                             window_start=cluster.start_ts, window_end=cluster.end_ts,
                                             check_date=today))
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
