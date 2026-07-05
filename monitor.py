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
# Country flag helpers
# ------------------------------------------------------------------

def _flag_emoji(country_code: str) -> str:
    """Convert ISO 2-letter country code to flag emoji."""
    if len(country_code) != 2:
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country_code.upper())


# Prefix → ISO country code. Longer prefixes listed before shorter ones that
# share the same start (e.g. DQ- before D-) so startswith checks work correctly.
_REG_PREFIXES: list = [
    ("VH-", "AU"), ("VN-", "VN"), ("VT-", "IN"), ("VQ-", "GB"),
    ("HS-", "TH"), ("HZ-", "SA"),
    ("PK-", "ID"), ("PH-", "NL"), ("P2-", "PG"),
    ("A7-", "QA"), ("A6-", "AE"), ("A9C", "BH"), ("AP-", "PK"),
    ("4R-", "LK"), ("4X-", "IL"),
    ("9V-", "SG"), ("9M-", "MY"), ("9H-", "MT"), ("9G-", "GH"),
    ("ZK-", "NZ"), ("ZS-", "ZA"),
    ("CC-", "CL"),
    ("OE-", "AT"), ("OH-", "FI"), ("OK-", "CZ"), ("OM-", "SK"),
    ("OY-", "DK"), ("OD-", "LB"),
    ("LN-", "NO"), ("LX-", "LU"), ("LY-", "LT"), ("LZ-", "BG"),
    ("SE-", "SE"), ("SX-", "GR"), ("SU-", "EG"), ("SP-", "PL"), ("S2-", "BD"),
    ("EC-", "ES"), ("EI-", "IE"), ("EP-", "IR"), ("ET-", "ET"),
    ("ES-", "EE"), ("EY-", "AZ"),
    ("TC-", "TR"), ("TS-", "TN"),
    ("UR-", "UA"), ("UK-", "UZ"), ("UP-", "KZ"),
    ("RA-", "RU"), ("RF-", "RU"),
    ("RP-", "PH"),
    ("DQ-", "FJ"), ("D-",  "DE"),
    ("F-",  "FR"),
    ("G-",  "GB"),
    ("CS-", "PT"), ("CN-", "MA"),
    ("JY-", "JO"),
    ("YR-", "RO"), ("YL-", "LV"), ("YA-", "AF"),
    ("5N-", "NG"), ("5Y-", "KE"),
    ("7T-", "DZ"),
    ("XU-", "KH"),
]




def _registration_flag(registration: str) -> str:
    """Return a country flag emoji for an aircraft registration, or '' if unknown."""
    r = registration.upper().strip()

    # B- prefix: China / Hong Kong / Macau distinguished by first suffix character
    if r.startswith("B-"):
        suffix = r[2:]
        if suffix and suffix[0] in "HKLM":
            return _flag_emoji("HK")
        if suffix and suffix[0] == "0":
            return _flag_emoji("MO")
        return _flag_emoji("CN")

    # N prefix — USA (N followed by digit or letter, no dash)
    if len(r) > 1 and r[0] == "N" and r[1] != "-" and r[1].isalnum():
        return _flag_emoji("US")

    # JA (Japan) and HL (Korea) — no dash
    if r.startswith("JA"):
        return _flag_emoji("JP")
    if r.startswith("HL"):
        return _flag_emoji("KR")

    for prefix, cc in _REG_PREFIXES:
        if r.startswith(prefix):
            return _flag_emoji(cc)

    return ""


# ------------------------------------------------------------------
# Flight status / time helpers
# ------------------------------------------------------------------

def get_flight_status(flight: dict) -> str:
    """Return canonical status string matching the JS enum (Arrived/Arriving/Scheduled/N/A)."""
    try:
        real_dep = flight["time"]["real"]["departure"]
        real_arr = flight["time"]["real"]["arrival"]
        if real_arr is not None:
            return "Arrived"
        if real_dep is None:
            return "Scheduled"
        if int(real_dep) <= int(datetime.now().timestamp()):
            return "Arriving"
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


def _rego_link(rego: str, flag: str = "") -> str:
    """Return an HTML hyperlink to the FR24 aircraft page for a registration."""
    url = f"https://www.flightradar24.com/data/aircraft/{rego.lower()}"
    return f'<a href="{url}">{rego}</a>{" " + flag if flag else ""}'


def _fn_link(fn: str, flight_id: str = None) -> str:
    """Return an HTML hyperlink for a flight number.

    If flight_id is provided, links to the live flight tracker for that
    specific instance. Otherwise links to the general flights history page.
    """
    if flight_id and flight_id not in ("N/A", "N\\A"):
        url = f"https://www.flightradar24.com/{flight_id}"
    else:
        url = f"https://www.flightradar24.com/data/flights/{fn.lower()}"
    return f'<a href="{url}">{fn}</a>'


def _safe_get(d: dict, *keys, default="N/A"):
    """Walk a nested dict safely; return default on any missing key or None value."""
    for k in keys:
        try:
            d = d[k]
        except (KeyError, TypeError, IndexError):
            return default
    return d if d is not None else default


_MFR_KEYWORDS = [
    ('de havilland', 'De Havilland'),
    ('mcdonnell', 'McDonnell Douglas'),
    ('lockheed', 'Lockheed'),
    ('boeing', 'Boeing'),
    ('airbus', 'Airbus'),
    ('embraer', 'Embraer'),
    ('bombardier', 'Bombardier'),
    ('cessna', 'Cessna'),
    ('gulfstream', 'Gulfstream'),
    ('dassault', 'Dassault'),
    ('atr', 'ATR'),
    ('saab', 'Saab'),
    ('pilatus', 'Pilatus'),
    ('beechcraft', 'Beechcraft'),
    ('piper', 'Piper'),
    ('cirrus', 'Cirrus'),
    ('honda', 'Honda'),
    ('diamond', 'Diamond'),
    ('fokker', 'Fokker'),
    ('comac', 'COMAC'),
    ('antonov', 'Antonov'),
    ('sikorsky', 'Sikorsky'),
    ('bell', 'Bell'),
    ('leonardo', 'Leonardo'),
    ('bae', 'BAE Systems'),
    ('sukhoi', 'Sukhoi'),
]


def _derive_manufacturer(model_text: str):
    """Return a canonical manufacturer name from an FR24 model text string, or None."""
    if not model_text:
        return None
    lower = model_text.lower()
    for keyword, canonical in _MFR_KEYWORDS:
        if keyword in lower:
            return canonical
    return None


def _first_image_url(images: dict) -> str:
    """Pick the best available photo src from an FR24 aircraftImages 'images' block."""
    imgs = images or {}
    large = imgs.get("large") or imgs.get("medium") or []
    return large[0]["src"].replace("/640cb/", "/640/") if large else ""


def _extract_board_photos(airport_details_response: dict) -> dict:
    """get_airport_details() responses carry a top-level 'aircraftImages' array keyed by
    registration — the same photo data as get_rego_details(), free on the call monitor.py
    already makes every check. Returns {registration: photo_url}."""
    out = {}
    for item in (airport_details_response or {}).get("aircraftImages") or []:
        reg = (item.get("registration") or "").strip()
        if not reg:
            continue
        url = _first_image_url(item.get("images"))
        if url:
            out[reg] = url
    return out


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
    extra: Optional[dict] = None,
) -> str:
    lines = [f"<b>{notification_type}</b>"]

    fn_raw   = _safe_get(flight, 'identification', 'number', 'default')
    fn_id    = _safe_get(flight, 'identification', 'id', default=None)
    fn_str   = _fn_link(fn_raw, flight_id=fn_id) if fn_raw and fn_raw != "N/A" else fn_raw
    lines.append(f"  Flight: {fn_str}")

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

    rego_raw = _safe_get(aircraft, 'registration')
    rego_flag = _registration_flag(rego_raw) if rego_raw and rego_raw != "N/A" else ""
    lines.append(f"  Registration: {_rego_link(rego_raw, rego_flag) if rego_raw and rego_raw != 'N/A' else rego_raw}")

    # Route Equipment Change — show what type normally operates this route
    if notification_type == "Route Equipment Change" and extra:
        est_type  = extra.get("established_type", "")
        est_count = extra.get("established_count", 0)
        est_since = extra.get("established_since", 0)
        if est_type and est_since:
            tz_obj    = pytz.timezone(airport_tz)
            since_str = datetime.fromtimestamp(est_since).astimezone(tz_obj).strftime("%b %Y")
            lines.append(f"  Established: {est_type} × {est_count} ops since {since_str}")

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

    live_dep_shown = False
    if rego_details:
        dep_time, dep_fn, al_name, al_iata, al_icao, dest_name, dest_iata, dest_icao, dep_label = get_next_departure(
            rego_details, airport_iata, airport_tz
        )
        if dep_time:
            lines += ["", "<b>Next Departure:</b>"]
            lines.append(f"  {dep_label}: {dep_time.strftime('%a %H:%M')} (Local) — {_fn_link(dep_fn)}")
            if dest_name:
                lines.append(f"  To: {dest_name} ({dest_iata}/{dest_icao})")
            live_dep_shown = True

    if not live_dep_shown and cfg_store is not None and dep_pattern_threshold > 0:
        arrival_fn = str(_safe_get(flight, "identification", "number", "default", default=""))
        if arrival_fn and arrival_fn != "N/A":
            predicted = cfg_store.get_predicted_departure(
                arrival_fn, airport_iata, dep_pattern_threshold
            )
            if predicted:
                pred_fn, confidence, _, _ = predicted
                dep_info        = cfg_store.get_predicted_dep_info(pred_fn, airport_iata)
                sched_ts        = dep_info.get("scheduled_dep_ts") if dep_info else None
                turnaround_secs = dep_info.get("turnaround_secs")  if dep_info else None
                al_name         = dep_info.get("airline_name")     if dep_info else None
                al_iata         = dep_info.get("airline_iata")     if dep_info else None
                al_icao         = dep_info.get("airline_icao")     if dep_info else None
                dest_name       = dep_info.get("dest_name")        if dep_info else None
                dest_iata       = dep_info.get("dest_iata")        if dep_info else None
                dest_icao       = dep_info.get("dest_icao")        if dep_info else None

                now_check = int(datetime.now().timestamp())

                # a) Stored timestamp still in the future — use directly
                if sched_ts and sched_ts > now_check:
                    dep_display_ts    = sched_ts
                    dep_display_label = "Predicted"
                # b) Stale stored timestamp — derive from scheduled turnaround offset
                elif turnaround_secs:
                    arr_ts_raw     = _safe_get(flight, "time", "scheduled", "arrival", default=None)
                    arr_ts_for_dep = int(arr_ts_raw) if isinstance(arr_ts_raw, (int, float)) else None
                    dep_display_ts    = (arr_ts_for_dep + turnaround_secs) if arr_ts_for_dep else None
                    dep_display_label = "Predicted"
                else:
                    dep_display_ts    = None
                    dep_display_label = "Predicted"

                # c) No time yet — try FR24 for current day's schedule (also fills route info)
                need_fr24 = (dep_display_ts is None or not al_name or not dest_name)
                if fr_api is not None and need_fr24:
                    try:
                        fl_data = fr_api.get_flight_by_number(pred_fn)
                        if dep_display_ts is None:
                            dep_display_ts = _get_scheduled_dep_ts(fl_data, airport_iata, pred_fn)
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
                if dep_display_ts:
                    dep_time = datetime.fromtimestamp(dep_display_ts).astimezone(tz)
                    lines.append(f"  {dep_display_label}: {dep_time.strftime('%a %H:%M')} (Local) — {_fn_link(pred_fn)}")
                else:
                    lines.append(f"  Predicted: {_fn_link(pred_fn)}")
                if dest_name:
                    lines.append(f"  To: {dest_name} ({dest_iata}/{dest_icao})")
                    lines.append(f"  Confidence: {confidence:.0f}%")

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
    day = _scheduled_arrival_day(flight, tz_name)
    if not allowed_days or day is None or day not in allowed_days:
        return False
    if time_mode == "Off":
        return False
    if time_mode == "Daylight":
        if get_arrival_period(flight, tz_name, lat, lon) != "Daylight Arrival":
            return False
    return True


def _is_special_livery_airline(airline_name: str, livery_keywords: list,
                               exclude_keywords: list = None) -> bool:
    """Return True if the airline name indicates a special livery.

    Matches either a configured keyword OR a parenthetical scheme name appended
    by FR24 (e.g. 'China Southern Airlines (15th National Games)'). Short codes
    like '(CZ)' or '(CSN)' are excluded by requiring at least 5 characters inside.
    Parenthetical detection is suppressed if the text inside matches any exclude_keyword.
    """
    airline_lower = airline_name.lower()
    if any(kw.lower() in airline_lower for kw in livery_keywords):
        return True
    m = re.search(r'\((.{5,})\)', airline_name)
    if m:
        inner = m.group(1).lower()
        if exclude_keywords and any(kw.lower() in inner for kw in exclude_keywords):
            return False
        return True
    return False


def _parse_aircraft(arriving_flight: dict) -> Optional[Tuple[str, str, dict]]:
    """Extract (registration, aircraft_type_code, flight_dict) from a raw arrivals entry."""
    try:
        aircraft = arriving_flight["flight"]["aircraft"]
        if aircraft is None:
            return None
        reg = (aircraft["registration"] or "").strip()
        if not reg:
            return None
        return reg, aircraft["model"]["code"], arriving_flight["flight"]
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

    if not _is_special_livery_airline(airline_name, cfg.livery_keywords, cfg.livery_exclude_keywords):
        return None
    if cfg.store.is_excluded(registration):
        return None

    return flight, registration, lambda: None


def check_rare_plane(arriving_flight: dict, cfg) -> Optional[Tuple[dict, str, callable]]:
    flight_data = arriving_flight.get("flight") or {}

    # Prefer owner (operating carrier) ICAO; fall back to marketing airline ICAO.
    # Using owner ensures codeshare flights (e.g. Atlas Air flying as QF7554) are
    # tracked under the actual operator, not the marketing carrier.
    owner = flight_data.get("owner") or {}
    airline_icao = (
        ((owner.get("code") or {}).get("icao") or "")
        or ((flight_data.get("airline") or {}).get("code") or {}).get("icao", "")
    )

    parsed = _parse_aircraft(arriving_flight)
    if parsed is None:
        return None
    registration, aircraft_type, flight = parsed

    now_ts = int(datetime.now().timestamp())
    # Always record the sighting BEFORE any early-return guards so that special
    # livery visits and excluded regos still reset the absence clock.
    is_rare = False
    if airline_icao and aircraft_type:
        is_rare = cfg.store.update_rare_plane_seen(
            airline_icao, aircraft_type, now_ts, cfg.rare_plane_min_absence_days
        )

    # Never notify if this is a special livery aircraft (but clock was already reset above)
    airline_name = (flight_data.get("airline") or {}).get("name") or ""
    if _is_special_livery_airline(airline_name, cfg.livery_keywords, cfg.livery_exclude_keywords):
        return None

    if not airline_icao:
        return None

    if cfg.store.is_excluded(registration):
        return None

    if is_rare:
        return flight, registration, lambda: None
    return None


def check_rego_watchlist(arriving_flight: dict, cfg) -> Optional[Tuple[dict, str, callable]]:
    flight_data = arriving_flight.get("flight") or {}
    airline_name = (flight_data.get("airline") or {}).get("name") or ""
    if _is_special_livery_airline(airline_name, cfg.livery_keywords, cfg.livery_exclude_keywords):
        return None

    parsed = _parse_aircraft(arriving_flight)
    if parsed is None:
        return None
    registration, _, flight = parsed

    if cfg.store.is_excluded(registration):
        return None

    with cfg.store._connect() as _c:
        if not _c.execute("SELECT 1 FROM filter_regos WHERE registration=? LIMIT 1", (registration,)).fetchone():
            return None

    return flight, registration, lambda: None


def check_type_watchlist(arriving_flight: dict, cfg) -> Optional[Tuple[dict, str, callable]]:
    flight_data = arriving_flight.get("flight") or {}
    airline_name = (flight_data.get("airline") or {}).get("name") or ""
    if _is_special_livery_airline(airline_name, cfg.livery_keywords, cfg.livery_exclude_keywords):
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

    if cfg.store.is_excluded(registration):
        return None

    with cfg.store._connect() as _c:
        if not _c.execute("SELECT 1 FROM filter_types WHERE airline=? AND aircraft_type=? LIMIT 1",
                          (airline_icao, aircraft_type)).fetchone():
            return None

    return flight, registration, lambda: None


def check_airline_watchlist(arriving_flight: dict, cfg) -> Optional[Tuple]:
    flight_data = arriving_flight.get("flight") or {}

    airline_name = (flight_data.get("airline") or {}).get("name") or ""
    if _is_special_livery_airline(airline_name, cfg.livery_keywords, cfg.livery_exclude_keywords):
        return None

    parsed = _parse_aircraft(arriving_flight)
    if parsed is None:
        return None
    registration, _, flight = parsed

    if cfg.store.is_excluded(registration):
        return None

    airline_icao = _safe_get(flight_data, "airline", "code", "icao", default="")
    if airline_icao and airline_icao != "N/A":
        with cfg.store._connect() as _c:
            if _c.execute("SELECT 1 FROM filter_airlines WHERE icao_code=? AND entry_type='airline' LIMIT 1",
                          (airline_icao,)).fetchone():
                return flight, registration, lambda: None, "Watchlist Airline"

    owner_icao = _safe_get(flight_data, "owner", "code", "icao", default="")
    if owner_icao and owner_icao != "N/A":
        with cfg.store._connect() as _c:
            if _c.execute("SELECT 1 FROM filter_airlines WHERE icao_code=? AND entry_type='operator' LIMIT 1",
                          (owner_icao,)).fetchone():
                return flight, registration, lambda: None, "Watchlist Operator"

    return None


def check_route_type_change(arriving_flight: dict, cfg) -> Optional[Tuple]:
    """Fire when a flight number arrives with a different type than its established equipment."""
    flight_data = arriving_flight.get("flight") or {}

    # Skip special livery flights — already handled at higher priority
    airline_name = (flight_data.get("airline") or {}).get("name") or ""
    if _is_special_livery_airline(airline_name, cfg.livery_keywords, cfg.livery_exclude_keywords):
        return None

    parsed = _parse_aircraft(arriving_flight)
    if parsed is None:
        return None
    registration, aircraft_type, flight = parsed

    if cfg.store.is_excluded(registration):
        return None

    flight_number = str(_safe_get(flight, "identification", "number", "default", default=""))
    if not flight_number or flight_number == "N/A":
        return None

    established = cfg.store.get_established_route_type(
        flight_number, cfg.airport_iata,
        cfg.route_type_lookback_days,
        cfg.route_type_min_days,
        cfg.route_type_dominance_x,
    )
    if not established:
        return None

    established_type, established_count, established_since_ts = established
    if aircraft_type == established_type:
        return None  # operating as expected

    extra = {
        "established_type":  established_type,
        "established_count": established_count,
        "established_since": established_since_ts,
    }

    return flight, registration, lambda: None, extra


_FILTERS = [
    ("Special Livery",          check_special_livery),
    ("Watchlist Registration",  check_rego_watchlist),
    ("Watchlist Aircraft Type", check_type_watchlist),
    ("Watchlist Airline",       check_airline_watchlist),
    ("Rare Plane/Airline",      check_rare_plane),
    ("Route Equipment Change",  check_route_type_change),
]


def _all_matching_filters(arriving_flight: dict, cfg) -> list:
    """Run every filter; return list of (flight, registration, notification_type, on_notified, extra)
    for ALL matches (not just the first). on_notified callbacks are collected but NOT called —
    cooldown tables stay unwritten so every rotation is always detected."""
    results = []
    for notification_type, check_fn in _FILTERS:
        result = check_fn(arriving_flight, cfg)
        if result is None:
            continue
        flight, registration, on_notified = result[0], result[1], result[2]
        extra = None
        if len(result) > 3:
            if isinstance(result[3], dict):
                extra = result[3]
            else:
                notification_type = result[3]
        results.append((flight, registration, notification_type, on_notified, extra))
    return results


async def _enrich_and_store(
    flight: dict,
    registration: str,
    arrival_fn: str,
    notif_types: list,
    cfg,
    arrival_date: str = None,
) -> None:
    """Fetch photo/departure info from FR24, then write the match to flight_arrivals."""
    now_ts = int(datetime.now().timestamp())

    airframe  = cfg.store.get_airframe(registration)
    photo_url = (airframe or {}).get("photo_url") or ""
    has_dep   = bool(
        arrival_fn and arrival_fn != "N/A"
        and cfg.store.get_predicted_departure(arrival_fn, cfg.airport_iata, 1)
    )

    rego_details = None
    if not photo_url or not has_dep:
        try:
            rego_details = cfg.fr_api.get_rego_details(registration)
            _rd_data = (rego_details or {}).get("data") or []
            if _rd_data:
                _model_text = ((_rd_data[0].get("aircraft") or {}).get("model") or {}).get("text") or ""
                try:
                    _ac_country = (_rd_data[0].get("aircraft") or {}).get("country") or {}
                    _cc = (_ac_country.get("alpha2") or "").upper()
                    if _cc and "-" in registration:
                        import re as _re2
                        _pfx = (registration.split("-")[0] if "-" in registration else (_re2.match(r'^([A-Z]+)', registration.upper()) or _re2.match(r'^.', registration.upper())).group(0)).upper()
                        if _pfx and not cfg.store.get_reg_prefix_country(_pfx):
                            cfg.store.save_reg_prefix_country(_pfx, _cc, _ac_country.get("name", ""))
                except Exception:
                    pass
            else:
                _model_text = ((rego_details or {}).get("aircraftInfo") or {}).get("model", {}).get("text", "")
            _mfr = _derive_manufacturer(_model_text)
            # Re-extract every time this call already happens (not gated on photo_url being
            # empty) so a rego's cached photo keeps refreshing as FR24's own image changes,
            # instead of freezing forever after the first hit. upsert_airframe_from_fr24's
            # COALESCE keeps the old photo if this extraction comes up empty.
            _fresh_photo = ""
            images = (rego_details or {}).get("aircraftImages") or []
            if images:
                try:
                    imgs = images[0]["images"]
                    large = imgs.get("large") or imgs.get("medium") or []
                    _fresh_photo = large[0]["src"].replace("/640cb/", "/640/") if large else ""
                except (KeyError, IndexError):
                    pass
            if _fresh_photo:
                photo_url = _fresh_photo
            cfg.store.upsert_airframe_from_fr24(registration, photo_url=_fresh_photo or None, manufacturer=_mfr)
        except Exception as exc:
            log.warning("Could not fetch aircraft details for %s: %s", registration, exc)

    arr_ts = int(
        _safe_get(flight, "time", "estimated", "arrival", default=None)
        or _safe_get(flight, "time", "scheduled", "arrival", default=None)
        or 0
    )
    airline_raw   = (flight.get("airline") or {}).get("name") or (flight.get("owner") or {}).get("name") or ""
    arr_airline_icao = _safe_get(flight, "airline", "code", "icao") or ""
    aircraft_code = _safe_get(flight, "aircraft", "model", "code", default="")
    clean_airline = re.sub(r'\s*\(.*?\)', '', airline_raw).strip()
    detail = f"{clean_airline} ({aircraft_code})" if clean_airline and aircraft_code else (clean_airline or aircraft_code)

    extra_info = ""
    if "Special Livery" in notif_types:
        m = re.search(r'\((.+?)\)', airline_raw)
        extra_info = m.group(1) if m else airline_raw

    _origin     = (flight.get("airport") or {}).get("origin") or {}
    origin_iata = _safe_get(_origin, "code", "iata") or None
    origin_name = _origin.get("name") or None
    if origin_iata and origin_name:
        _origin_cc = _safe_get(_origin, "position", "country", "code") or ""
        cfg.store.upsert_airport(origin_iata, origin_name, _origin_cc, source='fr24')

    cfg.store.record_filter_match(
        registration, arrival_fn, notif_types, arr_ts, now_ts,
        detail=detail, extra_info=extra_info,
        origin_iata=origin_iata, origin_name=origin_name,
        arrival_date=arrival_date,
        airline_icao=arr_airline_icao,
        photo_url=photo_url or None,
    )


def _first_matching_filter(arriving_flight: dict, cfg) -> Optional[tuple]:
    """Run filters in priority order; stop at the first match.

    Returns (flight_dict, registration, notification_type, on_notified, extra) or None.
    extra is None for all filters except check_route_type_change which returns a dict
    with established_type/count/since for use in format_notification.
    check_airline_watchlist uses result[3] as a string override for the type label.
    """
    for notification_type, check_fn in _FILTERS:
        result = check_fn(arriving_flight, cfg)
        if result is not None:
            flight, registration, on_notified = result[0], result[1], result[2]
            # result[3] is either a string (type override) or a dict (extra data)
            extra = None
            if len(result) > 3:
                if isinstance(result[3], dict):
                    extra = result[3]
                else:
                    notification_type = result[3]  # string override (check_airline_watchlist)
            return flight, registration, notification_type, on_notified, extra
    return None


# ------------------------------------------------------------------
# Periodic arrivals check
# ------------------------------------------------------------------

async def run_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    chat_id = context.job.data if context.job else cfg.chat_id

    log.info("Checking arrivals at %s...", cfg.airport_iata)

    # ── Step 1: Pull API — positive pages (live board) + negative pages (hist) ──────────
    # all_arrivals / all_departures hold ALL flights per rego (list), not just the first.
    all_arrivals:  dict = {}  # reg → list[flight]
    all_departures: dict = {}  # reg → list[flight]
    hist_arrivals:  dict = {}  # reg → flight  (real arrival only, negative pages)
    hist_departures: dict = {}  # reg → list[flight]  (real departure only, negative pages)
    board_photos:  dict = {}  # reg → photo_url, from get_airport_details' own aircraftImages —
                              # free on the call already made below, no extra API hit

    try:
        import asyncio

        # Positive pages — current live board
        _fr24_ok = False
        for page in cfg.fetch_pages:
            try:
                data = cfg.fr_api.get_airport_details(code=cfg.airport_code, page=page)
                schedule   = data["airport"]["pluginData"]["schedule"]
                arrivals   = schedule["arrivals"]["data"]
                departures = schedule.get("departures", {}).get("data") or []
                board_photos.update(_extract_board_photos(data))
                _fr24_ok = True
            except Exception as exc:
                log.warning("Failed to fetch arrivals (page %d): %s", page, exc)
                continue

            for entry in arrivals:
                parsed = _parse_aircraft(entry)
                if parsed:
                    reg, _, flight = parsed
                    all_arrivals.setdefault(reg, []).append(flight)

            for entry in departures:
                parsed = _parse_aircraft(entry)
                if parsed:
                    reg, _, flight = parsed
                    all_departures.setdefault(reg, []).append(flight)

        import system_status as _ss
        _ss.record_api('fr24_airport', _fr24_ok)

        # Negative pages — recently rotated off, real timestamps only
        for hist_page in [-p for p in cfg.fetch_pages]:
            try:
                hist_data     = cfg.fr_api.get_airport_details(code=cfg.airport_code, page=hist_page)
                hist_schedule = hist_data["airport"]["pluginData"]["schedule"]
                board_photos.update(_extract_board_photos(hist_data))
                for entry in (hist_schedule.get("arrivals", {}).get("data") or []):
                    parsed = _parse_aircraft(entry)
                    if not parsed:
                        continue
                    reg, _, flight = parsed
                    if isinstance(_safe_get(flight, "time", "real", "arrival", default=None), (int, float)):
                        hist_arrivals.setdefault(reg, flight)
                for entry in (hist_schedule.get("departures", {}).get("data") or []):
                    parsed = _parse_aircraft(entry)
                    if not parsed:
                        continue
                    reg, _, flight = parsed
                    if isinstance(_safe_get(flight, "time", "real", "departure", default=None), (int, float)):
                        hist_departures.setdefault(reg, []).append(flight)
            except Exception as exc:
                log.debug("Failed to fetch page %d for passive updates: %s", hist_page, exc)

        # Refresh the airframes photo cache for every registration seen on the board this
        # check — free (no extra API call), and keeps photos current for consumers other
        # than Feed/Spotting (Collection, Search, etc.) that still read from airframes.
        for _reg, _url in board_photos.items():
            cfg.store.upsert_airframe_from_fr24(_reg, photo_url=_url)

        # ── Step 2: Passive DB updates (unchanged) ────────────────────────────────────────
        landed = {}   # reg → {"ts": int, "manufacturer": str, "airline": str}
        route_type_records = []
        actual_departures  = []

        def _sighting_entry(flight, ts: int) -> dict:
            return {"ts": ts}

        def _iata(flight, *keys):
            v = _safe_get(flight, *keys, default=None)
            return str(v).strip().upper() if v and str(v).strip() not in ("N/A", "N\\A", "") else None

        def _airline(flight):
            import re as _re
            raw = _safe_get(flight, "airline", "name", default="") or ""
            name = _re.sub(r'\s*\(.+?\)', '', raw).strip()
            return name or None

        for reg, flights in all_arrivals.items():
            for flight in flights:
                real_arr = _safe_get(flight, "time", "real", "arrival", default=None)
                if isinstance(real_arr, (int, float)):
                    ts = int(real_arr)
                    if ts > (landed.get(reg, {}).get("ts", 0) if isinstance(landed.get(reg), dict) else landed.get(reg, 0)):
                        landed[reg] = _sighting_entry(flight, ts)
                    fn      = str(_safe_get(flight, "identification", "number", "default", default=""))
                    ac_type = _safe_get(flight, "aircraft", "model", "code", default="")
                    if fn and fn != "N/A" and ac_type and ac_type != "N/A":
                        origin = _iata(flight, "airport", "origin", "code", "iata")
                        route_type_records.append((fn, ac_type, cfg.airport_iata, ts, origin, None, _airline(flight)))

        for reg, flight in hist_arrivals.items():
            real_arr = _safe_get(flight, "time", "real", "arrival", default=None)
            if isinstance(real_arr, (int, float)):
                ts = int(real_arr)
                if ts > (landed.get(reg, {}).get("ts", 0) if isinstance(landed.get(reg), dict) else landed.get(reg, 0)):
                    landed[reg] = _sighting_entry(flight, ts)
                fn      = str(_safe_get(flight, "identification", "number", "default", default=""))
                ac_type = _safe_get(flight, "aircraft", "model", "code", default="")
                if fn and fn != "N/A" and ac_type and ac_type != "N/A":
                    origin = _iata(flight, "airport", "origin", "code", "iata")
                    route_type_records.append((fn, ac_type, cfg.airport_iata, ts, origin, None, _airline(flight)))

        for reg, flights in all_departures.items():
            for flight in flights:
                real_dep = _safe_get(flight, "time", "real", "departure", default=None)
                if not isinstance(real_dep, (int, float)):
                    continue
                fn      = str(_safe_get(flight, "identification", "number", "default", default=""))
                ac_type = _safe_get(flight, "aircraft", "model", "code", default="")
                if fn and fn != "N/A" and ac_type and ac_type != "N/A":
                    dest = _iata(flight, "airport", "destination", "code", "iata")
                    route_type_records.append((fn, ac_type, cfg.airport_iata, int(real_dep), None, dest, _airline(flight)))
                if fn and fn not in ("N/A", "N\\A"):
                    actual_departures.append((fn, int(real_dep)))

        for reg, flights in hist_departures.items():
            for flight in flights:
                real_dep = _safe_get(flight, "time", "real", "departure", default=None)
                if not isinstance(real_dep, (int, float)):
                    continue
                fn      = str(_safe_get(flight, "identification", "number", "default", default=""))
                ac_type = _safe_get(flight, "aircraft", "model", "code", default="")
                if fn and fn != "N/A" and ac_type and ac_type != "N/A":
                    dest = _iata(flight, "airport", "destination", "code", "iata")
                    route_type_records.append((fn, ac_type, cfg.airport_iata, int(real_dep), None, dest, _airline(flight)))
                if fn and fn not in ("N/A", "N\\A"):
                    actual_departures.append((fn, int(real_dep)))

        if landed:
            cfg.store.bulk_update_sightings(landed)
        if route_type_records:
            cfg.store.bulk_update_route_types(route_type_records)
        for dep_fn, dep_ts_val in actual_departures:
            cfg.store.record_actual_departure(dep_fn, cfg.airport_iata, dep_ts_val)

        # ── Departure pattern bulk update ─────────────────────────────────────────────────
        # Only confirmed actual arrivals + actual departures count as an observation.
        # hist_arrivals / hist_departures are built from negative pages (real timestamps only),
        # so both sides are confirmed happened. The scheduled times are stored for prediction.
        _pattern_now_ts = int(datetime.now().timestamp())
        for _reg, _arr_fl in hist_arrivals.items():
            # hist_departures[reg] is a LIST (a rego can have more than one real departure in a
            # day) — pick the closest real departure strictly AFTER this arrival, same rule
            # Step 7b's live/hist pairing already uses, so the learned pattern matches what
            # pairing would actually produce for this exact visit.
            _dep_fls = hist_departures.get(_reg) or []
            if not _dep_fls:
                continue
            _arr_fn = str(_safe_get(_arr_fl, "identification", "number", "default", default="") or "")
            if not _arr_fn or _arr_fn in ("N/A", "N\\A"):
                continue
            _arr_real = _safe_get(_arr_fl, "time", "real", "arrival", default=None)
            if not isinstance(_arr_real, (int, float)):
                continue
            _arr_real = int(_arr_real)

            _dep_fl = None
            _dep_real_ts = None
            for _cand in _dep_fls:
                _cand_ts = _safe_get(_cand, "time", "real", "departure", default=None)
                if not isinstance(_cand_ts, (int, float)) or int(_cand_ts) <= _arr_real:
                    continue
                if _dep_real_ts is None or int(_cand_ts) < _dep_real_ts:
                    _dep_real_ts = int(_cand_ts)
                    _dep_fl = _cand
            if _dep_fl is None:
                continue

            _dep_fn = str(_safe_get(_dep_fl, "identification", "number", "default", default="") or "")
            if not _dep_fn or _dep_fn in ("N/A", "N\\A"):
                continue
            _sched_arr = _safe_get(_arr_fl, "time", "scheduled", "arrival",   default=None)
            _sched_dep = _safe_get(_dep_fl, "time", "scheduled", "departure", default=None)
            _est_dep   = _safe_get(_dep_fl, "time", "estimated", "departure", default=None)
            cfg.store.record_departure_pattern(
                _arr_fn, _dep_fn, cfg.airport_iata, _pattern_now_ts,
                scheduled_dep_ts = int(_sched_dep) if isinstance(_sched_dep, (int, float)) else None,
                estimated_dep_ts = int(_est_dep)   if isinstance(_est_dep,   (int, float)) else None,
                scheduled_arr_ts = int(_sched_arr) if isinstance(_sched_arr, (int, float)) else None,
                airline_name = _safe_get(_dep_fl, "airline", "name") or None,
                airline_iata = _safe_get(_dep_fl, "airline", "code", "iata") or None,
                airline_icao = _safe_get(_dep_fl, "airline", "code", "icao") or None,
                dest_name    = _safe_get(_dep_fl, "airport", "destination", "name") or None,
                dest_iata    = _safe_get(_dep_fl, "airport", "destination", "code", "iata") or None,
                dest_icao    = _safe_get(_dep_fl, "airport", "destination", "code", "icao") or None,
            )

        # ── Step 3: Filter matching → matched_regos ───────────────────────────────────────
        # Run filters on each flight per rego; union notif_types across all flights.
        matched_regos: dict = {}  # reg → {"notif_types": list, "flights": list}
        now_ts_check = int(datetime.now().timestamp())
        _tz_obj = pytz.timezone(cfg.airport_tz)

        for reg, flights in all_arrivals.items():
            for flight_entry in flights:
                # all_arrivals stores the inner flight dict from _parse_aircraft;
                # filter functions expect the outer {"flight": ...} entry format.
                matches = _all_matching_filters({"flight": flight_entry}, cfg)
                if not matches:
                    continue
                if reg not in matched_regos:
                    matched_regos[reg] = {"notif_types": [], "flights": []}
                for m in matches:
                    nt = m[2]
                    if nt not in matched_regos[reg]["notif_types"]:
                        matched_regos[reg]["notif_types"].append(nt)

        # ── Step 4: Enrich and store NEW filter-matched flights ───────────────────────────
        # Only flights that pass filters and aren't yet stored get _enrich_and_store called.
        for reg, info in matched_regos.items():
            notif_types = info["notif_types"]
            for flight in all_arrivals.get(reg, []):
                fn = str(_safe_get(flight, "identification", "number", "default", default=""))
                if not fn or fn in ("N/A", "N\\A"):
                    continue
                _real_arr  = _safe_get(flight, "time", "real",      "arrival", default=None)
                _est_arr   = _safe_get(flight, "time", "estimated", "arrival", default=None)
                _sched_arr = _safe_get(flight, "time", "scheduled", "arrival", default=None)
                if isinstance(_real_arr, (int, float)):
                    arr_ts = int(_real_arr)
                elif isinstance(_est_arr, (int, float)):
                    arr_ts = int(_est_arr)
                elif isinstance(_sched_arr, (int, float)):
                    arr_ts = int(_sched_arr)
                else:
                    continue
                arr_date = datetime.fromtimestamp(arr_ts, _tz_obj).strftime("%Y-%m-%d")
                if not cfg.store.flight_event_exists(reg, fn, arrival_date=arr_date):
                    await _enrich_and_store(flight, reg, fn, notif_types, cfg,
                                            arrival_date=arr_date)
                    await asyncio.sleep(0.5)

        # ── Step 5: Refresh arrival time + label for ALL stored flights visible on board ──
        # This is NOT gated on matched_regos — filter checks (e.g. rare_plane absence)
        # can exclude a flight on subsequent runs even though it's already stored.
        # Every flight in flight_arrivals deserves an arrival time update while FR24 shows it.
        for reg, flights in all_arrivals.items():
            for flight in flights:
                fn = str(_safe_get(flight, "identification", "number", "default", default=""))
                if not fn or fn in ("N/A", "N\\A"):
                    continue
                _real_arr  = _safe_get(flight, "time", "real",      "arrival", default=None)
                _est_arr   = _safe_get(flight, "time", "estimated", "arrival", default=None)
                _sched_arr = _safe_get(flight, "time", "scheduled", "arrival", default=None)
                if isinstance(_real_arr, (int, float)):
                    arr_ts    = int(_real_arr)
                    arr_label = "Arrived"
                elif isinstance(_est_arr, (int, float)):
                    arr_ts    = int(_est_arr)
                    arr_label = "Estimated"
                elif isinstance(_sched_arr, (int, float)):
                    arr_ts    = int(_sched_arr)
                    arr_label = "Scheduled"
                else:
                    continue
                arr_date       = datetime.fromtimestamp(arr_ts, _tz_obj).strftime("%Y-%m-%d")
                current_status = get_flight_status(flight)
                if cfg.store.flight_event_exists(reg, fn, arrival_date=arr_date):
                    cfg.store.update_flight_event_status(reg, fn, current_status, arr_ts,
                                                         arrival_date=arr_date,
                                                         arr_label=arr_label)

        # ── Step 6: Status update from hist_arrivals (landed, off positive pages) ─────────
        for reg, flight in hist_arrivals.items():
            fn = str(_safe_get(flight, "identification", "number", "default", default=""))
            if not fn:
                continue
            real_arr = _safe_get(flight, "time", "real", "arrival", default=None)
            if not isinstance(real_arr, (int, float)):
                continue
            real_arr_date = datetime.fromtimestamp(int(real_arr), _tz_obj).strftime("%Y-%m-%d")
            if not cfg.store.flight_event_exists(reg, fn, arrival_date=real_arr_date):
                continue
            cfg.store.update_flight_event_status(reg, fn, get_flight_status(flight),
                                                 int(real_arr), arrival_date=real_arr_date,
                                                 arr_label="Arrived")

        # ── Step 7a: Resolve cancellation / diversion / aircraft-swap status ──────────────
        # Runs BEFORE departure-claiming (Step 7b) and iterates unresolved DB rows directly,
        # not this check's fresh fetch — a row that's gone quiet by definition won't be in
        # the fetch. See docs/09-fr24-flight-lifecycle.md §11 for full design rationale.
        try:
            from datetime import timedelta as _td7
            now_ts = int(datetime.now().timestamp())

            def _cfg_int7(key: str, default: int) -> int:
                v = cfg.store.load_setting(key)
                if v:
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        pass
                return default

            _cancel_grace_secs   = _cfg_int7("MONITOR_CANCEL_GRACE_MINS", 90) * 60
            _diverted_grace_secs = _cfg_int7("MONITOR_DIVERTED_GRACE_MINS", 35) * 60
            _absence_checks      = _cfg_int7("MONITOR_ABSENCE_CHECKS", 3)
            _confirm_call_cap    = _cfg_int7("MONITOR_CONFIRM_CALL_CAP", 5)

            # Index this check's fresh fetch by (flight_number, arrival_date) → [(reg, flight_dict), ...]
            # across arrivals (live + hist), so a flight number can be looked up regardless of
            # which registration currently holds it.
            _fresh_by_fn_date: dict = {}
            for _reg7, _flights7 in all_arrivals.items():
                for _fl7 in _flights7:
                    _fn7 = str(_safe_get(_fl7, "identification", "number", "default", default=""))
                    if not _fn7 or _fn7 in ("N/A", "N\\A"):
                        continue
                    _a_ts7 = int(
                        _safe_get(_fl7, "time", "real",      "arrival", default=None)
                        or _safe_get(_fl7, "time", "estimated", "arrival", default=None)
                        or _safe_get(_fl7, "time", "scheduled", "arrival", default=None)
                        or 0
                    )
                    if not _a_ts7:
                        continue
                    _a_date7 = datetime.fromtimestamp(_a_ts7, _tz_obj).strftime("%Y-%m-%d")
                    _fresh_by_fn_date.setdefault((_fn7, _a_date7), []).append((_reg7, _fl7, _a_ts7))
            for _reg7, _fl7 in hist_arrivals.items():
                _fn7 = str(_safe_get(_fl7, "identification", "number", "default", default=""))
                _real_arr7 = _safe_get(_fl7, "time", "real", "arrival", default=None)
                if _fn7 and isinstance(_real_arr7, (int, float)):
                    _a_date7 = datetime.fromtimestamp(int(_real_arr7), _tz_obj).strftime("%Y-%m-%d")
                    _fresh_by_fn_date.setdefault((_fn7, _a_date7), []).append((_reg7, _fl7, int(_real_arr7)))

            # Unresolved rows from the DB — bounded to the last 3 days (generous headroom above
            # the longest grace period; keeps the query cheap, avoids reprocessing ancient rows).
            _cutoff_date7 = (datetime.now(_tz_obj).date() - _td7(days=3)).strftime("%Y-%m-%d")
            with cfg.store._connect() as conn:
                _unresolved_rows = conn.execute(
                    "SELECT id, registration, flight_number, arrival_date, arrival_ts, current_status "
                    "FROM flight_arrivals "
                    "WHERE current_status IN ('Scheduled', 'Arriving') AND arrival_date >= ?",
                    (_cutoff_date7,),
                ).fetchall()

            _confirm_call_queue: list = []  # rows needing the paid confirmation-call fallback

            for _row7 in _unresolved_rows:
                _reg_r, _fn_r, _date_r = _row7["registration"], _row7["flight_number"], _row7["arrival_date"]
                _key7 = (_reg_r, _fn_r, _date_r)
                _matches7 = _fresh_by_fn_date.get((_fn_r, _date_r), [])
                _own_match = next((m for m in _matches7 if m[0] == _reg_r), None)
                _other_matches = [m for m in _matches7 if m[0] != _reg_r]

                if _other_matches:
                    # ── Aircraft swap: this flight number now belongs to another registration ──
                    _other_reg = _other_matches[0][0]
                    with cfg.store._connect() as conn:
                        _siblings = conn.execute(
                            "SELECT id, registration, first_seen_ts FROM flight_arrivals "
                            "WHERE flight_number = ? AND arrival_date = ?",
                            (_fn_r, _date_r),
                        ).fetchall()
                    _earliest = min(_siblings, key=lambda s: s["first_seen_ts"])
                    if _earliest["id"] == _row7["id"]:
                        cfg.store.update_flight_event_status(
                            _reg_r, _fn_r, "Swapped", _row7["arrival_ts"],
                            arrival_date=_date_r, arr_label=f"Reassigned to {_other_reg}",
                        )
                        log.info("Aircraft swap: %s %s reassigned to %s", _reg_r, _fn_r, _other_reg)
                    else:
                        with cfg.store._connect() as conn:
                            conn.execute("DELETE FROM flight_departures WHERE arrival_id = ?", (_row7["id"],))
                            conn.execute("DELETE FROM flight_arrivals WHERE id = ?", (_row7["id"],))
                        log.info("Aircraft swap: %s %s successor row removed (now on %s)",
                                 _reg_r, _fn_r, _other_reg)
                    cfg.cancel_absence_tracking.pop(_key7, None)
                    continue

                if _own_match:
                    # Still visible under its own registration — clear any absence tracking,
                    # check explicit cancel/divert status, and reopen if previously Swapped.
                    cfg.cancel_absence_tracking.pop(_key7, None)
                    _fl_own, _arr_ts_own = _own_match[1], _own_match[2]
                    _status_text, _diverted_apt = _get_fr24_status(_fl_own)
                    if _status_text in ("canceled", "diverted"):
                        _new_status = "Cancelled" if _status_text == "canceled" else "Diverted"
                        cfg.store.update_flight_event_status(
                            _reg_r, _fn_r, _new_status, _arr_ts_own,
                            arrival_date=_date_r, arr_label=f"Confirmed {_new_status}",
                            diverted_to_iata=(_diverted_apt or None),
                        )
                        with cfg.store._connect() as conn:
                            conn.execute("DELETE FROM flight_departures WHERE arrival_id = ?", (_row7["id"],))
                        log.info("%s %s: %s", _reg_r, _fn_r, _new_status)
                    elif _row7["current_status"] == "Swapped":
                        cfg.store.update_flight_event_status(
                            _reg_r, _fn_r, get_flight_status(_fl_own), _arr_ts_own,
                            arrival_date=_date_r,
                        )
                        log.info("Aircraft swap reverted: %s %s reopened", _reg_r, _fn_r)
                    continue

                # ── Genuinely absent from every page this check ─────────────────────────────
                _entry7 = cfg.cancel_absence_tracking.get(_key7)
                if _entry7 is None:
                    cfg.cancel_absence_tracking[_key7] = {
                        "first_absent_ts": now_ts, "streak": 1,
                        "last_known_status": _row7["current_status"],
                    }
                    continue
                _entry7["streak"] += 1
                _grace_secs7 = (_cancel_grace_secs if _entry7["last_known_status"] == "Scheduled"
                               else _diverted_grace_secs)
                if (now_ts > _entry7["first_absent_ts"] + _grace_secs7
                        and _entry7["streak"] >= _absence_checks):
                    _confirm_call_queue.append((_entry7["first_absent_ts"], _key7, _row7, _entry7))

            # Departure-side cancellation: a previously-paired REAL (not predicted) departure
            # that itself gets cancelled needs its row cleared so Step 7b can find a genuine
            # replacement — otherwise upsert_flight_departure()'s "real data always wins" rule
            # would permanently block a fresh prediction from ever replacing it.
            with cfg.store._connect() as conn:
                _real_deps = conn.execute(
                    "SELECT fd.id AS fd_id, fd.arrival_id, fd.dep_flight, fe.registration "
                    "FROM flight_departures fd JOIN flight_arrivals fe ON fe.id = fd.arrival_id "
                    "WHERE fd.is_prediction = 0 AND fd.dep_ts > ? AND fe.arrival_date >= ?",
                    (now_ts, _cutoff_date7),
                ).fetchall()
            for _fd_row in _real_deps:
                _dep_fn_check = _fd_row["dep_flight"]
                if not _dep_fn_check:
                    continue
                for _dep_fl in all_departures.get(_fd_row["registration"], []):
                    _dfn = str(_safe_get(_dep_fl, "identification", "number", "default", default=""))
                    if _dfn != _dep_fn_check:
                        continue
                    _dstatus, _ = _get_fr24_status(_dep_fl)
                    if _dstatus == "canceled":
                        with cfg.store._connect() as conn:
                            conn.execute("DELETE FROM flight_departures WHERE id = ?", (_fd_row["fd_id"],))
                        log.info("Departure %s (arrival_id %s) cancelled — cleared for re-pairing",
                                 _dep_fn_check, _fd_row["arrival_id"])
                    break

            # Confirmation-call fallback: capped, oldest-absence-first when oversubscribed.
            _confirm_call_queue.sort(key=lambda x: x[0])
            for _first_absent_ts, _key7, _row7, _entry7 in _confirm_call_queue[:_confirm_call_cap]:
                _reg_r, _fn_r, _date_r = _key7
                _presumed_status = "Cancelled" if _entry7["last_known_status"] == "Scheduled" else "Diverted"
                _presumed_label  = f"Presumed {_presumed_status}"
                _resolved7 = False
                try:
                    _lookup = cfg.fr_api.get_flight_by_number(_fn_r)
                    for _cfl in (_lookup or {}).get("data") or []:
                        _cfn = str(_safe_get(_cfl, "identification", "number", "default", default=""))
                        if _cfn != _fn_r:
                            continue
                        _c_real_arr = _safe_get(_cfl, "time", "real", "arrival", default=None)
                        if isinstance(_c_real_arr, (int, float)):
                            cfg.store.update_flight_event_status(
                                _reg_r, _fn_r, "Arrived", int(_c_real_arr),
                                arrival_date=_date_r, arr_label="Arrived",
                            )
                            cfg.store.bulk_update_sightings({_reg_r: {"ts": int(_c_real_arr)}})
                            _c_ac_type = _safe_get(_cfl, "aircraft", "model", "code", default="")
                            if _c_ac_type and _c_ac_type not in ("N/A", "N\\A"):
                                _c_origin = _iata(_cfl, "airport", "origin", "code", "iata")
                                cfg.store.bulk_update_route_types([
                                    (_fn_r, _c_ac_type, cfg.airport_iata, int(_c_real_arr),
                                     _c_origin, None, _airline(_cfl)),
                                ])
                            log.info("Confirmation call: %s %s confirmed Arrived (board lagged)",
                                     _reg_r, _fn_r)
                            _resolved7 = True
                            break
                        _c_status, _c_diverted = _get_fr24_status(_cfl)
                        if _c_status in ("canceled", "diverted"):
                            _c_new_status = "Cancelled" if _c_status == "canceled" else "Diverted"
                            cfg.store.update_flight_event_status(
                                _reg_r, _fn_r, _c_new_status, _row7["arrival_ts"],
                                arrival_date=_date_r, arr_label=f"Confirmed {_c_new_status}",
                                diverted_to_iata=(_c_diverted or None),
                            )
                            with cfg.store._connect() as conn:
                                conn.execute("DELETE FROM flight_departures WHERE arrival_id = ?", (_row7["id"],))
                            log.info("Confirmation call: %s %s confirmed %s", _reg_r, _fn_r, _c_new_status)
                            _resolved7 = True
                            break
                        # Still legitimately in progress per this independent lookup — false trigger.
                        log.info("Confirmation call: %s %s still in progress, false trigger", _reg_r, _fn_r)
                        _resolved7 = True
                        break
                    if not _resolved7:
                        cfg.store.update_flight_event_status(
                            _reg_r, _fn_r, _presumed_status, _row7["arrival_ts"],
                            arrival_date=_date_r, arr_label=_presumed_label,
                        )
                        log.info("%s %s: %s (confirmation lookup empty)", _reg_r, _fn_r, _presumed_label)
                except Exception as _exc7:
                    log.warning("Confirmation-call lookup failed for %s %s: %s", _reg_r, _fn_r, _exc7)
                    cfg.store.update_flight_event_status(
                        _reg_r, _fn_r, _presumed_status, _row7["arrival_ts"],
                        arrival_date=_date_r, arr_label=_presumed_label,
                    )
                cfg.cancel_absence_tracking.pop(_key7, None)

        except Exception as _exc7b:
            log.warning("Step 7a (cancellation/diversion/swap resolution) failed: %s", _exc7b, exc_info=True)

        # ── Step 7b: Pair each arrival with its next departure ────────────────────────────
        # Iterate ALL arrivals visible to FR24 this check (positive + negative pages),
        # sorted by arr_ts ascending so earlier arrivals get first pick of departures.
        # Flights not in flight_arrivals (non-matched regos) are skipped. Arrivals resolved to
        # Cancelled/Diverted/Swapped above are skipped entirely — they never claim a departure.
        # Each live departure flight number can only be claimed once per rego.
        try:
            def _dep_ts_for(flight_dict: dict) -> Optional[int]:
                """Extract best available departure timestamp: real → estimated → scheduled."""
                for key in ("real", "estimated", "scheduled"):
                    v = _safe_get(flight_dict, "time", key, "departure", default=None)
                    if isinstance(v, (int, float)):
                        return int(v)
                return None

            # Build a flat list of (arr_ts, reg, fn, flight_dict, arr_date) for all visible arrivals
            all_visible: list = []
            for reg, flights in all_arrivals.items():
                for flight in flights:
                    fn = str(_safe_get(flight, "identification", "number", "default", default=""))
                    arr_ts = int(
                        _safe_get(flight, "time", "real",      "arrival", default=None)
                        or _safe_get(flight, "time", "estimated", "arrival", default=None)
                        or _safe_get(flight, "time", "scheduled", "arrival", default=None)
                        or 0
                    )
                    if fn and arr_ts:
                        arr_date = datetime.fromtimestamp(arr_ts, _tz_obj).strftime("%Y-%m-%d")
                        all_visible.append((arr_ts, reg, fn, flight, arr_date))

            for reg, flight in hist_arrivals.items():
                fn = str(_safe_get(flight, "identification", "number", "default", default=""))
                real_arr = _safe_get(flight, "time", "real", "arrival", default=None)
                if fn and isinstance(real_arr, (int, float)):
                    arr_date = datetime.fromtimestamp(int(real_arr), _tz_obj).strftime("%Y-%m-%d")
                    all_visible.append((int(real_arr), reg, fn, flight, arr_date))

            all_visible.sort(key=lambda x: x[0])  # earliest arrival first

            claimed: dict = {}  # reg → set of dep_flight strings already claimed this check

            for arr_ts, reg, fn, flight, arr_date in all_visible:
                # Skip if this rego/flight isn't in flight_arrivals (not filter-matched)
                with cfg.store._connect() as conn:
                    fe_row = conn.execute(
                        "SELECT id, current_status FROM flight_arrivals "
                        "WHERE registration = ? AND flight_number = ? AND arrival_date = ?",
                        (reg, fn, arr_date),
                    ).fetchone()
                if not fe_row:
                    continue
                # Resolved by Step 7a above — never claims a departure.
                if fe_row["current_status"] in ("Cancelled", "Diverted", "Swapped"):
                    continue
                arrival_id = fe_row["id"]
                rego_claimed = claimed.setdefault(reg, set())

                dep_fn         = None
                dep_ts         = None
                dep_dest_iata  = None
                dep_dest_name  = None
                is_pred        = False
                dep_label      = None
                dep_confidence = None

                # Source 1: live board — closest unclaimed departure after arr_ts
                live_candidates = []
                for dep_flight in all_departures.get(reg, []):
                    d_fn = str(_safe_get(dep_flight, "identification", "number", "default", default=""))
                    if not d_fn or d_fn in ("N/A", "N\\A") or d_fn in rego_claimed:
                        continue
                    # A doomed-cancelled candidate must not be claimed as "real" — that would
                    # permanently block a later prediction from replacing it (§11.4b).
                    _d_status, _ = _get_fr24_status(dep_flight)
                    if _d_status == "canceled":
                        continue
                    d_ts = _dep_ts_for(dep_flight)
                    if d_ts and d_ts > arr_ts:
                        live_candidates.append((d_ts, d_fn, dep_flight))
                live_candidates.sort(key=lambda x: x[0])
                live_best = live_candidates[0] if live_candidates else None  # (ts, fn, flight)

                # Source 2: hist_departures — all confirmed real departures per rego,
                # pick the earliest unclaimed one after arr_ts.
                hist_candidates = []
                for h_fl in hist_departures.get(reg, []):
                    h_fn = str(_safe_get(h_fl, "identification", "number", "default", default=""))
                    h_ts = _safe_get(h_fl, "time", "real", "departure", default=None)
                    if (h_fn and h_fn not in ("N/A", "N\\A")
                            and h_fn not in rego_claimed
                            and isinstance(h_ts, (int, float))
                            and int(h_ts) > arr_ts):
                        hist_candidates.append((int(h_ts), h_fn, h_fl))
                hist_candidates.sort(key=lambda x: x[0])
                hist_best = hist_candidates[0] if hist_candidates else None  # (ts, fn, flight)

                # Pick the candidate closest to arr_ts (smallest dep_ts - arr_ts).
                # Hist (confirmed departed) beats live at equal distance.
                if hist_best and live_best:
                    winner = hist_best if hist_best[0] <= live_best[0] else live_best
                elif hist_best:
                    winner = hist_best
                elif live_best:
                    winner = live_best
                else:
                    winner = None

                if winner:
                    w_ts, w_fn, w_fl = winner
                    dep_fn        = w_fn
                    dep_ts        = w_ts
                    dep_dest_iata = _safe_get(w_fl, "airport", "destination", "code", "iata") or None
                    dep_dest_name = _safe_get(w_fl, "airport", "destination", "name") or None
                    rego_claimed.add(dep_fn)
                    if winner is hist_best:
                        dep_label = "Departed"
                    else:
                        _live_real  = _safe_get(w_fl, "time", "real",      "departure", default=None)
                        _live_est   = _safe_get(w_fl, "time", "estimated", "departure", default=None)
                        _live_sched = _safe_get(w_fl, "time", "scheduled", "departure", default=None)
                        if isinstance(_live_real, (int, float)) and int(_live_real) == dep_ts:
                            dep_label = "Departed"
                        elif isinstance(_live_est, (int, float)) and int(_live_est) == dep_ts:
                            dep_label = "Estimated"
                        else:
                            dep_label = "Scheduled"
                        cfg.store.update_departure_timestamps(
                            fn, dep_fn, cfg.airport_iata,
                            int(_live_est)   if isinstance(_live_est,   (int, float)) else None,
                            int(_live_sched) if isinstance(_live_sched, (int, float)) else None,
                        )

                # Source 3: prediction fallback
                if not dep_ts and fn and fn not in ("N/A", "N\\A"):
                    pred = cfg.store.get_predicted_departure(fn, cfg.airport_iata,
                                                             cfg.departure_pattern_threshold)
                    if pred:
                        p_fn, p_conf = pred[0], round(pred[1])
                        di   = cfg.store.get_predicted_dep_info(p_fn, cfg.airport_iata) or {}
                        dep_dest_iata  = di.get("dest_iata")
                        dep_dest_name  = di.get("dest_name")
                        dep_confidence = p_conf
                        # Use scheduled time-of-day projected forward from arr_ts.
                        # The stored scheduled_dep_ts is from a past occurrence — extract
                        # HH:MM and find the next occurrence of that time after arr_ts.
                        _sched = di.get("scheduled_dep_ts")
                        if _sched:
                            _tz_p = pytz.timezone(cfg.airport_tz)
                            _sched_dt = datetime.fromtimestamp(int(_sched), _tz_p)
                            _arr_dt   = datetime.fromtimestamp(arr_ts, _tz_p)
                            _candidate = _arr_dt.replace(
                                hour=_sched_dt.hour, minute=_sched_dt.minute,
                                second=0, microsecond=0,
                            )
                            if _candidate.timestamp() <= arr_ts:
                                from datetime import timedelta as _td
                                _candidate = _candidate + _td(days=1)
                            dep_fn    = p_fn
                            dep_ts    = int(_candidate.timestamp())
                            is_pred   = True
                            dep_label = "Predicted"
                        # Turnaround fallback when no scheduled time available
                        if not dep_ts and di.get("turnaround_secs"):
                            dep_fn    = p_fn
                            dep_ts    = arr_ts + int(di["turnaround_secs"])
                            is_pred   = True
                            dep_label = "Predicted"

                if dep_ts:
                    cfg.store.upsert_flight_departure(
                        arrival_id, dep_fn, dep_ts, dep_dest_iata, dep_dest_name,
                        is_prediction=is_pred, dep_label=dep_label,
                        dep_confidence=dep_confidence,
                    )

        except Exception as _exc:
            log.warning("Step 7b (departure pairing) failed: %s", _exc, exc_info=True)

    except Exception as exc:
        log.error("Unexpected error in run_check: %s", exc, exc_info=True)

    # Prune stale flight_arrivals rows (30 days)
    cfg.store.cleanup_arrived_flights(int(datetime.now().timestamp()))

    # ── Timeline cache: pre-compute clusters for yesterday, today, future ────────────────
    # Past days (2+ days ago) are already cached and won't change — skip them.
    # Weather is fetched only for today + future (4 days).
    try:
        import json as _jc, urllib.request as _ur
        from datetime import timedelta as __timedelta
        from web import cluster_day_for_cache

        _tz_c   = pytz.timezone(cfg.airport_tz)
        _now_c  = datetime.now(_tz_c)
        _today  = _now_c.date()

        # Days to (re-)cluster: yesterday, today, +1, +2, +3
        _cluster_dates = [_today + __timedelta(days=d) for d in range(-1, 4)]

        # Fetch weather for the full cluster window (yesterday .. +3 days). This must
        # match _cluster_dates exactly — it previously started at _today, one day
        # short of _cluster_dates' "yesterday" entry, so "yesterday" always fell
        # through to sunrise_ts=sunset_ts=0 below, silently disabling the lighting
        # gate for that day. Because the result is cached permanently in
        # timeline_cache and only -1..+3 are ever re-clustered, each day was
        # permanently corrupted at the moment it passed through the "yesterday"
        # slot — see backfill_timeline_weather.py for fixing already-cached days.
        _weather: dict = {}
        _lat = getattr(cfg, 'airport_lat', 0) or 0
        _lon = getattr(cfg, 'airport_lon', 0) or 0
        if _lat and _lon:
            try:
                _tz_enc = cfg.airport_tz.replace("/", "%2F")
                _url = (f"https://historical-forecast-api.open-meteo.com/v1/forecast"
                        f"?latitude={_lat}&longitude={_lon}"
                        f"&start_date={_today - __timedelta(days=1)}&end_date={_today + __timedelta(days=3)}"
                        f"&daily=sunrise,sunset,weathercode,temperature_2m_max,temperature_2m_min"
                        f"&timezone={_tz_enc}")
                with _ur.urlopen(_url, timeout=10) as _resp:
                    _om = _jc.loads(_resp.read())
                import system_status as _sys_s; _sys_s.record_api('open_meteo', True)
                _daily = _om.get("daily", {})
                _SEVERE = {75, 82, 86, 95, 96, 99}
                for _wi, _wd in enumerate(_daily.get("time", [])):
                    try:
                        _sr_s = (_daily.get("sunrise") or [])[_wi]
                        _ss_s = (_daily.get("sunset")  or [])[_wi]
                        _wc   = int((_daily.get("weathercode") or [])[_wi] or 0)
                        # Open-Meteo returns these as naive local-wall-clock strings
                        # (per the &timezone= param) — .timestamp() on a naive
                        # datetime uses the SERVER's own system timezone, not the
                        # airport's, silently corrupting sunrise/sunset for any
                        # airport whose tz differs from the server's (invisible for
                        # the server's own home airport, wrong for every other one).
                        # Must localize to the airport's tz explicitly before
                        # converting to an epoch timestamp.
                        _sr = int(_tz_c.localize(datetime.fromisoformat(_sr_s)).timestamp()) if _sr_s else 0
                        _ss = int(_tz_c.localize(datetime.fromisoformat(_ss_s)).timestamp()) if _ss_s else 0
                        _tmax = (_daily.get("temperature_2m_max") or [])[_wi]
                        _tmin = (_daily.get("temperature_2m_min") or [])[_wi]
                        _weather[_wd] = {
                            "sunrise_ts": _sr, "sunset_ts": _ss,
                            "weather_code": _wc, "weather_severe": _wc in _SEVERE,
                            "temp_max": round(_tmax) if _tmax is not None else None,
                            "temp_min": round(_tmin) if _tmin is not None else None,
                        }
                    except Exception:
                        pass
            except Exception as _we:
                import system_status as _sys_s; _sys_s.record_api('open_meteo', False, str(_we))
                log.warning("Timeline cache: Open-Meteo fetch failed: %s", _we)

        # Catalog snapshot — one call per rego, not per flight per day
        _catalog = getattr(cfg, 'catalog', None)
        _spotted_map: dict = {}          # reg → total session count (non-livery flights)
        _livery_spotted_map: dict = {}   # (reg, livery_lower) → livery-matched session count
        if _catalog and getattr(cfg, 'spot_rec_max_spotted_times', 0):
            try:
                with cfg.store._connect() as _sc:
                    _regs = [r[0] for r in _sc.execute(
                        "SELECT DISTINCT registration FROM flight_arrivals").fetchall()]
                for _r in _regs:
                    try:
                        _spotted_map[_r] = _catalog.get_session_count_at_airport(
                            _r, cfg.airport_iata) or 0
                    except Exception:
                        pass
            except Exception:
                pass

        # Pre-fetch excluded registrations once
        _excluded = set()
        try:
            with cfg.store._connect() as _ec:
                _excluded = {r[0] for r in _ec.execute(
                    "SELECT registration FROM filter_exclusions").fetchall()}
        except Exception:
            pass

        # Read algorithm settings
        def _cfg_int(key, attr, default):
            v = cfg.store.load_setting(key)
            if v:
                try: return int(v)
                except: pass
            return int(getattr(cfg, attr, default) or default)

        _max_gap    = _cfg_int("SPOT_MAX_GAP_HOURS",     "spot_rec_max_gap_hours",     3) * 3600
        _lull_secs  = _cfg_int("SPOT_LULL_MINS",         "spot_rec_notable_lull_mins", 60) * 60
        _max_spot   = _cfg_int("SPOT_MAX_SPOTTED",       "spot_rec_max_spotted_times", 0)
        _dep_thr    = _cfg_int("DEPARTURE_PATTERN_THRESHOLD","departure_pattern_threshold", 80)
        _light_buf  = _cfg_int("SPOT_LIGHT_BUFFER_MINS", "spot_rec_light_buffer_mins", 30) * 60
        _max_lulls  = _cfg_int("SPOT_MAX_LULLS",         "spot_rec_max_lulls",         2)
        _light_gate = cfg.store.load_setting("SPOT_LIGHTING_GATE")
        _light_gate = (_light_gate.lower() == "true") if _light_gate else getattr(cfg, "spot_rec_lighting_gate", True)
        _bl_start   = cfg.store.load_setting("SPOT_BAD_LIGHT_START") or getattr(cfg, "spot_rec_bad_light_start", "") or ""
        _bl_end     = cfg.store.load_setting("SPOT_BAD_LIGHT_END")   or getattr(cfg, "spot_rec_bad_light_end",   "") or ""

        # Query all flight_arrivals rows covering the cluster date window in one shot
        _date_strs = [_cd.strftime("%Y-%m-%d") for _cd in _cluster_dates]
        _ph = ",".join("?" * len(_date_strs))
        with cfg.store._connect() as _dbc:
            _fe_rows = _dbc.execute(f"""
                SELECT fe.registration, fe.flight_number, fe.arrival_ts,
                       fe.notif_types, fe.detail, fe.extra_info, fe.airline_icao,
                       fe.origin_iata, fe.current_status, fe.arr_label,
                       fd.dep_flight, fd.dep_ts, fd.dep_dest_iata, fd.dep_dest_name,
                       fd.dep_confidence, fd.dep_label,
                       a.photo_url, a.manufacturer
                FROM flight_arrivals fe
                LEFT JOIN flight_departures fd ON fd.arrival_id = fe.id
                LEFT JOIN airframes a ON a.registration = fe.registration
                WHERE fe.arrival_date IN ({_ph})
                ORDER BY fe.arrival_ts ASC
            """, _date_strs).fetchall()

        def _get_spotted(reg: str, livery: str) -> int:
            if not _catalog or not livery:
                return _spotted_map.get(reg, 0)
            key = (reg, livery.strip().lower())
            if key not in _livery_spotted_map:
                try:
                    _livery_spotted_map[key] = _catalog.get_livery_session_count_at_airport(
                        reg, cfg.airport_iata, livery) or 0
                except Exception:
                    _livery_spotted_map[key] = _spotted_map.get(reg, 0)
            return _livery_spotted_map[key]

        # Build flat independent events keyed by their own timestamp's date.
        # Arrivals → bucketed by date(arrival_ts).
        # Departures → bucketed by date(dep_ts).
        # No cross-midnight logic needed: each event lands in the right day naturally.
        _events_by_date: dict = {}
        for _fr in _fe_rows:
            _arr_ts = _fr["arrival_ts"]
            _dep_ts = _fr["dep_ts"]
            if _dep_ts and not (_arr_ts <= _dep_ts <= _arr_ts + 36 * 3600):
                _dep_ts = None
            try:
                _nt = _jc.loads(_fr["notif_types"] or "[]")
            except Exception:
                _nt = []
            if "Military" in _nt:
                # Military flights are Feed-only: excluded from clustering/Spotting tab
                continue
            if _fr["current_status"] in ("Cancelled", "Diverted", "Swapped"):
                # Nothing to see — the tracked aircraft isn't coming, or isn't operating this
                # flight anymore. Full exclusion, not just non-qualifying/dimmed (§11.5).
                # A diverted *departure* is not filtered here — only the arrival event is built
                # from this row's own current_status, so a diverted-after-leaving departure
                # (arrival status unaffected) still participates normally.
                continue

            _common = {
                "registration":  _fr["registration"],
                "flight_number": _fr["flight_number"],
                "notif_types":   _nt,
                "detail":        _fr["detail"] or "",
                "extra_info":    _fr["extra_info"] or "",
                "airline_icao":  _fr["airline_icao"] or "",
                "photo_url":     _fr["photo_url"] or "",
                "manufacturer":  _fr["manufacturer"] or "",
                "origin_iata":   _fr["origin_iata"],
                "dep_flight":    _fr["dep_flight"],
                "dep_ts":        _dep_ts,
                "dep_dest_iata": _fr["dep_dest_iata"],
                "dep_dest_name": _fr["dep_dest_name"],
                "dep_confidence":_fr["dep_confidence"],
                "dep_label":     _fr["dep_label"],
                "current_status":_fr["current_status"],
                "arrival_ts":    _arr_ts,
                "arr_label":     _fr["arr_label"],
                "_spotted":      _get_spotted(_fr["registration"], _fr["extra_info"] or ""),
            }

            # Arrival event — belongs to date of arrival_ts
            _arr_date = datetime.fromtimestamp(_arr_ts, _tz_c).strftime("%Y-%m-%d")
            _events_by_date.setdefault(_arr_date, []).append({
                **_common, "ts": _arr_ts, "side": "arrival",
            })

            # Departure event — belongs to date of dep_ts (independent of arrival date)
            if _dep_ts:
                _dep_date = datetime.fromtimestamp(_dep_ts, _tz_c).strftime("%Y-%m-%d")
                _events_by_date.setdefault(_dep_date, []).append({
                    **_common, "ts": _dep_ts, "side": "departure",
                })

        # Fall back to each date's own previously-cached weather when this cycle's
        # fetch didn't return that date (transient API miss/rate-limit/etc) — never
        # silently fall back to sunrise_ts=sunset_ts=0, which disables the lighting
        # gate for that day. upsert_timeline_cache only overwrites weather_json when
        # given a non-None value, so a stale-but-correct weather_json can otherwise
        # sit next to a freshly-recomputed (and wrongly unqualified) clusters_json.
        _old_cache = cfg.store.get_timeline_cache(_date_strs)

        for _cd in _cluster_dates:
            _ds  = _cd.strftime("%Y-%m-%d")
            _sw  = _weather.get(_ds)
            if not _sw:
                try:
                    _sw = _jc.loads(_old_cache.get(_ds, {}).get("weather_json") or "{}")
                except Exception:
                    _sw = {}
            _sr, _ss = _sw.get("sunrise_ts", 0), _sw.get("sunset_ts", 0)

            _clusters = cluster_day_for_cache(
                _events_by_date.get(_ds, []), _sr, _ss, _tz_c,
                max_gap_secs=_max_gap, notable_lull_secs=_lull_secs,
                max_spotted=_max_spot, dep_threshold=_dep_thr,
                light_buf_secs=_light_buf, lighting_gate=_light_gate,
                bad_light_start=_bl_start, bad_light_end=_bl_end,
                max_lulls=_max_lulls, excluded_regs=_excluded,
            )

            _weather_json = _jc.dumps(_sw) if _sw else None
            cfg.store.upsert_timeline_cache(
                _ds, _jc.dumps(_clusters),
                weather_json=_weather_json,
            )
            log.debug("Timeline cache updated for %s (%d clusters)", _ds, len(_clusters))

    except Exception as _ce:
        log.warning("Timeline cache update failed: %s", _ce, exc_info=True)



async def _send_notification(
    context,
    cfg,
    chat_id: str,
    flight: dict,
    registration: str,
    notification_type: str,
    on_notified: callable,
    extra: Optional[dict] = None,
) -> None:
    log.info("Notifying: %s — %s", notification_type, registration)

    now_ts = int(datetime.now().timestamp())
    arrival_fn = str(_safe_get(flight, "identification", "number", "default", default=""))

    # Check caches before calling FR24
    airframe   = cfg.store.get_airframe(registration)
    photo_url  = (airframe or {}).get("photo_url") or ""
    has_dep_pattern = bool(
        arrival_fn and arrival_fn != "N/A"
        and cfg.store.get_predicted_departure(arrival_fn, cfg.airport_iata, 1)
    )

    rego_details = None
    if not photo_url or not has_dep_pattern:
        try:
            rego_details = cfg.fr_api.get_rego_details(registration)
            _rd_data = (rego_details or {}).get("data") or []
            if _rd_data:
                _model_text = ((_rd_data[0].get("aircraft") or {}).get("model") or {}).get("text") or ""
                try:
                    _ac_country = (_rd_data[0].get("aircraft") or {}).get("country") or {}
                    _cc = (_ac_country.get("alpha2") or "").upper()
                    if _cc and "-" in registration:
                        import re as _re2
                        _pfx = (registration.split("-")[0] if "-" in registration else (_re2.match(r'^([A-Z]+)', registration.upper()) or _re2.match(r'^.', registration.upper())).group(0)).upper()
                        if _pfx and not cfg.store.get_reg_prefix_country(_pfx):
                            cfg.store.save_reg_prefix_country(_pfx, _cc, _ac_country.get("name", ""))
                except Exception:
                    pass
            else:
                _model_text = ((rego_details or {}).get("aircraftInfo") or {}).get("model", {}).get("text", "")
            _mfr = _derive_manufacturer(_model_text)
            # Re-extract every time this call already happens (not gated on photo_url being
            # empty) so a rego's cached photo keeps refreshing as FR24's own image changes,
            # instead of freezing forever after the first hit. upsert_airframe_from_fr24's
            # COALESCE keeps the old photo if this extraction comes up empty.
            _fresh_photo = ""
            images = (rego_details or {}).get("aircraftImages") or []
            if images:
                try:
                    imgs = images[0]["images"]
                    large = imgs.get("large") or imgs.get("medium") or []
                    _fresh_photo = large[0]["src"].replace("/640cb/", "/640/") if large else ""
                except (KeyError, IndexError):
                    pass
            if _fresh_photo:
                photo_url = _fresh_photo
            cfg.store.upsert_airframe_from_fr24(registration, photo_url=_fresh_photo or None, manufacturer=_mfr)
        except Exception as exc:
            log.warning("Could not fetch aircraft details for %s: %s", registration, exc)

    # Record departure pattern for future predictions (only when fresh rego_details available)
    if arrival_fn and arrival_fn != "N/A" and rego_details:
        _, dep_fn, al_name, al_iata, al_icao, dest_name, dest_iata, dest_icao, _ = get_next_departure(
            rego_details, cfg.airport_iata, cfg.airport_tz
        )
    try:
        message = format_notification(
            flight, registration, notification_type, rego_details,
            cfg.airport_iata, cfg.airport_tz, cfg.airport_lat, cfg.airport_lon,
            catalog=cfg.catalog,
            cfg_store=cfg.store,
            dep_pattern_threshold=cfg.departure_pattern_threshold,
            fr_api=cfg.fr_api,
            extra=extra,
        )
        for dest_chat_id in cfg.all_chat_ids:
            if photo_url:
                try:
                    await context.bot.send_photo(
                        chat_id=dest_chat_id,
                        photo=photo_url,
                        caption=f'Aircraft Photo: <a href="https://www.flightradar24.com/data/aircraft/{registration.lower()}">{registration}</a>',
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    log.warning("Could not send photo for %s to %s: %s", registration, dest_chat_id, exc)
            try:
                await context.bot.send_message(chat_id=dest_chat_id, text=message, parse_mode="HTML", disable_web_page_preview=True)
            except Exception as exc:
                log.error("Failed to send notification for %s to %s: %s", registration, dest_chat_id, exc)

        # Write DB records only after confirmed primary delivery
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

        airline_raw   = (flight.get("airline") or {}).get("name") or \
                        (flight.get("owner") or {}).get("name") or ""
        aircraft_code = _safe_get(flight, "aircraft", "model", "code", default="")
        clean_airline = re.sub(r'\s*\(.*?\)', '', airline_raw).strip()
        if clean_airline and aircraft_code:
            detail = f"{clean_airline} ({aircraft_code})"
        else:
            detail = clean_airline or aircraft_code

        _origin      = (flight.get("airport") or {}).get("origin") or {}
        origin_iata  = _safe_get(_origin, "code", "iata") or None
        origin_name  = _origin.get("name") or None
        cfg.store.record_notified_flight(
            registration, flight_number, notification_type, arrival_ts, now_ts, now_ts, extra_info, detail,
            origin_iata=origin_iata, origin_name=origin_name,
        )
    except Exception as exc:
        log.error("Failed to send notification for %s: %s", registration, exc, exc_info=True)


async def _send_approach_alert(context, cfg, registration: str, record, arrival_ts: int, now_ts: int) -> None:
    tz = pytz.timezone(cfg.airport_tz)
    mins = round((arrival_ts - now_ts) / 60)
    flag = _registration_flag(registration)
    fr24_url = f"https://www.flightradar24.com/data/aircraft/{registration.lower()}"
    reg_str = f'<a href="{fr24_url}">{registration}</a>{" " + flag if flag else ""}'

    notif_type = record["notif_type"] or ""
    extra_info = record["extra_info"] or ""
    detail     = record["detail"] or ""

    type_str = f"{notif_type} ({extra_info})" if extra_info else notif_type
    parts = [f"  ✈ On approach — {reg_str} landing in ~{mins} min"]
    sub = " · ".join(filter(None, [type_str, detail]))
    if sub:
        parts.append(f"  {sub}")

    text = "\n".join(parts)
    for dest_chat_id in cfg.all_chat_ids:
        try:
            await context.bot.send_message(chat_id=dest_chat_id, text=text,
                                           parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            log.warning("Failed to send approach alert for %s to %s: %s", registration, dest_chat_id, exc)
    log.info("Approach alert sent: %s (~%d min)", registration, mins)


async def _send_departure_alert(context, cfg, registration: str, record, dep_flight: dict) -> None:
    tz = pytz.timezone(cfg.airport_tz)
    flag = _registration_flag(registration)
    fr24_url = f"https://www.flightradar24.com/data/aircraft/{registration.lower()}"
    reg_str = f'<a href="{fr24_url}">{registration}</a>{" " + flag if flag else ""}'

    notif_type = record["notif_type"] or ""
    extra_info = record["extra_info"] or ""

    dep_fn   = _safe_get(dep_flight, "identification", "number", "default", default="")
    dest_iata = _safe_get(dep_flight, "airport", "destination", "code", "iata", default="")
    dest_name = _safe_get(dep_flight, "airport", "destination", "name", default="")
    dest_str = f"{dest_iata}" if not dest_name or dest_name == "N/A" else f"{dest_name} ({dest_iata})"

    type_str = f"{notif_type} ({extra_info})" if extra_info else notif_type

    lines = [f"  🛫 Departing now — {reg_str}"]
    route = " · ".join(filter(None, [
        f"{_fn_link(dep_fn)} → {dest_str}" if dep_fn and dep_fn != "N/A" else None,
        type_str,
    ]))
    if route:
        lines.append(f"  {route}")

    text = "\n".join(lines)
    for dest_chat_id in cfg.all_chat_ids:
        try:
            await context.bot.send_message(chat_id=dest_chat_id, text=text,
                                           parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            log.warning("Failed to send departure alert for %s to %s: %s", registration, dest_chat_id, exc)
    log.info("Departure alert sent: %s", registration)


async def _send_arrival_reminder(
    context,
    cfg,
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

    aircraft = flight.get("aircraft") or {}
    aircraft_text_raw = _safe_get(aircraft, 'model', 'text')
    airline_name_raw  = (flight.get("airline") or {}).get("name", "N/A")
    flight_id = _safe_get(flight, "identification", "id", default=None)

    dep_data = None
    if flight_number and cfg.departure_pattern_threshold > 0:
        predicted = cfg.store.get_predicted_departure(flight_number, cfg.airport_iata, cfg.departure_pattern_threshold)
        if predicted:
            dep_fn, confidence, _, _ = predicted
            dep_info = cfg.store.get_predicted_dep_info(dep_fn, cfg.airport_iata)
            dep_data = {"dep_fn": dep_fn, "confidence": confidence, "dep_info": dep_info}

    flag = _registration_flag(registration)
    arrival_str = datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%a %H:%M") if arrival_ts else "N/A"
    fn_raw = _safe_get(flight, 'identification', 'number', 'default')
    fn_id  = _safe_get(flight, 'identification', 'id', default=None)
    fn_str = _fn_link(fn_raw, flight_id=fn_id) if fn_raw and fn_raw != "N/A" else fn_raw
    lines = [
        f"<b>Arriving Soon — {notification_type}</b>",
        f"  Flight: {fn_str}",
        f"  Aircraft: {aircraft_text_raw} ({_safe_get(aircraft, 'model', 'code')})",
        f"  Registration: {_rego_link(registration, flag)}",
        f"  Airline: {airline_name_raw}",
        f"  {arr_label}: {arrival_str} (Local) — in ~{hours_away}h",
    ]
    if dep_data:
        dep_fn          = dep_data["dep_fn"]
        confidence      = dep_data["confidence"]
        dep_info        = dep_data["dep_info"]
        estimated_ts    = dep_info.get("estimated_dep_ts") if dep_info else None
        sched_ts        = dep_info.get("scheduled_dep_ts") if dep_info else None
        turnaround_secs = dep_info.get("turnaround_secs")  if dep_info else None
        dest_name       = dep_info.get("dest_name")        if dep_info else None
        dest_iata       = dep_info.get("dest_iata")        if dep_info else None
        dest_icao       = dep_info.get("dest_icao")        if dep_info else None

        # a) Estimated still in the future
        if estimated_ts and estimated_ts > now_ts:
            dep_display_ts, dep_display_label = estimated_ts, "Estimated"
        # b) Scheduled still in the future
        elif sched_ts and sched_ts > now_ts:
            dep_display_ts, dep_display_label = sched_ts, "Scheduled"
        # c) Both stale — derive from turnaround offset using scheduled arrival time
        elif turnaround_secs:
            sched_arr_raw  = _safe_get(flight, "time", "scheduled", "arrival", default=None)
            sched_arr_ts   = int(sched_arr_raw) if isinstance(sched_arr_raw, (int, float)) else None
            dep_display_ts = (sched_arr_ts + turnaround_secs) if sched_arr_ts else None
            dep_display_label = "Predicted"
        else:
            dep_display_ts, dep_display_label = None, "Predicted"

        lines.append("")
        lines.append("<b>Next Departure:</b>")
        if dep_display_ts:
            dep_str = datetime.fromtimestamp(dep_display_ts).astimezone(tz).strftime("%a %H:%M")
            lines.append(f"  {dep_display_label}: {dep_str} (Local) — {_fn_link(dep_fn)}")
        else:
            lines.append(f"  Predicted: {_fn_link(dep_fn)}")
        if dest_name:
            lines.append(f"  To: {dest_name} ({dest_iata}/{dest_icao})")
        if not dep_display_ts:
            lines.append(f"  Confidence: {confidence:.0f}%")
    for dest_chat_id in cfg.all_chat_ids:
        try:
            await context.bot.send_message(chat_id=dest_chat_id, text="\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            log.error("Failed to send arrival reminder for %s to %s: %s", registration, dest_chat_id, exc)
    log.info("Sent arrival reminder for %s", registration)


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
    context, cfg, old_rego, new_rego, new_flight, flight_number, notification_type, arrival_ts,
) -> None:
    tz = pytz.timezone(cfg.airport_tz)
    arrival_str = datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%a %H:%M") if arrival_ts else "N/A"
    aircraft = new_flight.get("aircraft") or {}
    ac_type  = _safe_get(aircraft, "model", "code")
    ac_name  = _safe_get(aircraft, "model", "text")
    interesting = _classify_new_aircraft(new_flight, new_rego, cfg)
    old_flag = _registration_flag(old_rego)
    new_flag = _registration_flag(new_rego)
    fn_str = _fn_link(flight_number) if flight_number and flight_number != "N/A" else (flight_number or "N/A")
    lines = [
        f"<b>Aircraft Changed — {notification_type}</b>",
        f"  Flight: {fn_str}",
        f"  Was: {_rego_link(old_rego, old_flag)}",
        f"  Now: {_rego_link(new_rego, new_flag)} ({ac_name} / {ac_type})",
        f"  Arrival: {arrival_str} (Local)",
    ]
    if interesting:
        lines.append(f"\n  <b>{interesting}</b>")
    for dest_chat_id in cfg.all_chat_ids:
        try:
            await context.bot.send_message(chat_id=dest_chat_id, text="\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            log.error("Failed to send swap notice to %s: %s", dest_chat_id, exc)
    log.info("Sent aircraft swap notice: %s → %s on %s", old_rego, new_rego, flight_number)


async def _send_disappeared_notice(
    context, cfg, registration, flight_number, notification_type, arrival_ts,
) -> None:
    tz = pytz.timezone(cfg.airport_tz)
    arrival_str = datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%a %H:%M") if arrival_ts else "N/A"
    message = (
        f"<b>No Longer Visible — {notification_type}</b>\n"
        f"  Registration: {registration}\n"
        f"  Flight: {flight_number or 'N/A'}\n"
        f"  Was scheduled: {arrival_str} (Local)"
    )
    for dest_chat_id in cfg.all_chat_ids:
        try:
            await context.bot.send_message(chat_id=dest_chat_id, text=message, parse_mode="HTML")
        except Exception as exc:
            log.error("Failed to send disappeared notice to %s: %s", dest_chat_id, exc)
    log.info("Sent disappeared notice for %s", registration)


async def _send_cancellation_notice(
    context, cfg, registration, flight_number, notification_type, arrival_ts,
) -> None:
    tz = pytz.timezone(cfg.airport_tz)
    arrival_str = datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%a %H:%M") if arrival_ts else "N/A"
    flag = _registration_flag(registration)
    fn_str = _fn_link(flight_number) if flight_number and flight_number != "N/A" else (flight_number or "N/A")
    message = (
        f"<b>Cancelled — {notification_type}</b>\n"
        f"  Registration: {_rego_link(registration, flag)}\n"
        f"  Flight: {fn_str}\n"
        f"  Was scheduled: {arrival_str} (Local)"
    )
    for dest_chat_id in cfg.all_chat_ids:
        try:
            await context.bot.send_message(chat_id=dest_chat_id, text=message, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            log.error("Failed to send cancellation notice to %s: %s", dest_chat_id, exc)
    log.info("Sent cancellation notice for %s", registration)


async def _send_diversion_notice(
    context, cfg, registration, flight_number, notification_type, arrival_ts, diverted_airport,
) -> None:
    tz = pytz.timezone(cfg.airport_tz)
    arrival_str = datetime.fromtimestamp(arrival_ts).astimezone(tz).strftime("%a %H:%M") if arrival_ts else "N/A"
    flag = _registration_flag(registration)
    fn_str = _fn_link(flight_number) if flight_number and flight_number != "N/A" else (flight_number or "N/A")
    airport_str = f" to {diverted_airport}" if diverted_airport else ""
    message = (
        f"<b>Diverted{airport_str} — {notification_type}</b>\n"
        f"  Registration: {_rego_link(registration, flag)}\n"
        f"  Flight: {fn_str}\n"
        f"  Was scheduled: {arrival_str} (Local)"
    )
    for dest_chat_id in cfg.all_chat_ids:
        try:
            await context.bot.send_message(chat_id=dest_chat_id, text=message, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as exc:
            log.error("Failed to send diversion notice to %s: %s", dest_chat_id, exc)
    log.info("Sent diversion notice for %s → %s", registration, diverted_airport or "unknown")
