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


_IATA_COUNTRY: dict = {
    # Australia & Pacific
    "SYD": "AU", "MEL": "AU", "BNE": "AU", "PER": "AU", "ADL": "AU",
    "CBR": "AU", "OOL": "AU", "CNS": "AU", "HBA": "AU", "DRW": "AU",
    "TSV": "AU", "MKY": "AU", "LST": "AU", "HTI": "AU",
    "AKL": "NZ", "CHC": "NZ", "WLG": "NZ", "ZQN": "NZ", "DUD": "NZ",
    "NAN": "FJ", "SUV": "FJ",
    "POM": "PG", "LAE": "PG",
    "HIR": "SB", "VLI": "VU",
    "PPT": "PF",
    # East & Southeast Asia
    "SIN": "SG",
    "HKG": "HK",
    "MFM": "MO",
    "NRT": "JP", "HND": "JP", "KIX": "JP", "ITM": "JP", "NGO": "JP", "CTS": "JP",
    "ICN": "KR", "GMP": "KR", "PUS": "KR", "CJU": "KR",
    "PEK": "CN", "PKX": "CN", "PVG": "CN", "SHA": "CN", "CAN": "CN",
    "CTU": "CN", "SZX": "CN", "XIY": "CN", "KMG": "CN", "CSX": "CN",
    "WUH": "CN", "NKG": "CN", "HGH": "CN", "TNA": "CN", "CKG": "CN",
    "TPE": "TW", "TSA": "TW", "KHH": "TW",
    "MNL": "PH", "CEB": "PH", "DVO": "PH",
    "CGK": "ID", "DPS": "ID", "SUB": "ID", "UPG": "ID", "MDC": "ID",
    "KUL": "MY", "BKI": "MY", "KCH": "MY", "PEN": "MY",
    "BKK": "TH", "DMK": "TH", "CNX": "TH", "HKT": "TH", "HDY": "TH",
    "HAN": "VN", "SGN": "VN", "DAD": "VN",
    "RGN": "MM",
    "PNH": "KH", "REP": "KH",
    "VTE": "LA",
    # South Asia
    "DEL": "IN", "BOM": "IN", "MAA": "IN", "BLR": "IN", "CCU": "IN",
    "HYD": "IN", "AMD": "IN", "COK": "IN",
    "CMB": "LK",
    "DAC": "BD", "CGP": "BD",
    "KTM": "NP",
    "KHI": "PK", "LHE": "PK", "ISB": "PK",
    "MLE": "MV",
    # Middle East & Central Asia
    "DXB": "AE", "AUH": "AE", "SHJ": "AE",
    "DOH": "QA",
    "BAH": "BH",
    "MCT": "OM",
    "KWI": "KW",
    "AMM": "JO", "AQJ": "JO",
    "BEY": "LB",
    "RUH": "SA", "JED": "SA", "DMM": "SA",
    "TLV": "IL",
    "IST": "TR", "SAW": "TR", "AYT": "TR",
    "GYD": "AZ",
    "TAS": "UZ",
    "ALA": "KZ",
    # Africa
    "JNB": "ZA", "CPT": "ZA", "DUR": "ZA",
    "NBO": "KE", "MBA": "KE",
    "ADD": "ET",
    "LOS": "NG", "ABV": "NG",
    "ACC": "GH",
    "CMN": "MA", "RAK": "MA",
    "ALG": "DZ",
    "CAI": "EG", "HRG": "EG",
    # Europe
    "LHR": "GB", "LGW": "GB", "MAN": "GB", "EDI": "GB", "STN": "GB",
    "CDG": "FR", "ORY": "FR", "NCE": "FR",
    "FRA": "DE", "MUC": "DE", "DUS": "DE", "BER": "DE",
    "AMS": "NL", "EIN": "NL",
    "MAD": "ES", "BCN": "ES", "AGP": "ES",
    "FCO": "IT", "MXP": "IT", "VCE": "IT",
    "ZRH": "CH", "GVA": "CH",
    "VIE": "AT",
    "BRU": "BE",
    "ARN": "SE", "GOT": "SE",
    "CPH": "DK",
    "HEL": "FI",
    "OSL": "NO", "BGO": "NO",
    "LIS": "PT", "OPO": "PT",
    "OTP": "RO",
    "WAW": "PL", "KRK": "PL",
    "PRG": "CZ",
    "BUD": "HU",
    "ATH": "GR", "SKG": "GR",
    "SVO": "RU", "DME": "RU", "LED": "RU",
    "KBP": "UA",
    # Americas
    "LAX": "US", "SFO": "US", "JFK": "US", "ORD": "US", "MIA": "US",
    "SEA": "US", "HNL": "US", "DFW": "US", "ATL": "US", "DEN": "US",
    "LAS": "US", "PHX": "US", "IAH": "US", "BOS": "US", "EWR": "US",
    "YVR": "CA", "YYZ": "CA", "YUL": "CA", "YYC": "CA", "YEG": "CA",
    "GRU": "BR", "GIG": "BR", "BSB": "BR", "FOR": "BR",
    "SCL": "CL",
    "BOG": "CO",
    "LIM": "PE",
    "MEX": "MX", "CUN": "MX",
    "EZE": "AR", "AEP": "AR",
}


def _iata_flag(iata: str) -> str:
    """Return a country flag emoji for an airport IATA code, or '' if unknown."""
    cc = _IATA_COUNTRY.get((iata or "").upper().strip(), "")
    return _flag_emoji(cc) if cc else ""


_IATA_COUNTRY_CACHE: dict = {}  # runtime cache for API-resolved airport countries


def _tz_to_country_code(tz_name: str) -> str:
    """Return ISO country code from timezone name using pytz, or ''."""
    import pytz as _pytz
    for code, tzs in _pytz.country_timezones.items():
        if tz_name in tzs:
            return code
    return ""


def _iata_flag_with_api(iata: str, fr_api) -> str:
    """Return country flag emoji for an IATA code.

    Checks the static dict first, then falls back to a FR24 API call
    (deriving country from the airport's timezone). Results are cached
    in memory so each unknown code is only resolved once per run.
    """
    iata = (iata or "").upper().strip()
    if not iata:
        return ""

    flag = _iata_flag(iata)
    if flag:
        return flag

    if iata in _IATA_COUNTRY_CACHE:
        cc = _IATA_COUNTRY_CACHE[iata]
        return _flag_emoji(cc) if cc else ""

    try:
        data    = fr_api.get_airport_details(code=iata)
        details = data["airport"]["pluginData"]["details"]
        tz_name = (details.get("timezone") or {}).get("name", "")
        cc      = _tz_to_country_code(tz_name) if tz_name else ""
    except Exception:
        cc = ""

    _IATA_COUNTRY_CACHE[iata] = cc
    _IATA_COUNTRY[iata] = cc  # persist into static dict for this session
    return _flag_emoji(cc) if cc else ""


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


def _is_special_livery_airline(airline_name: str, livery_keywords: list,
                               exclude_keywords: list = None) -> bool:
    """Return True if the airline name indicates a special livery.

    Matches either a configured keyword OR a parenthetical scheme name appended
    by FR24 (e.g. 'China Southern Airlines (15th National Games)'). Short codes
    like '(CZ)' or '(CSN)' are excluded by requiring at least 5 characters inside.
    Parenthetical detection is suppressed if the text inside matches any exclude_keyword.
    """
    if any(kw in airline_name for kw in livery_keywords):
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
    if not _is_special_livery_airline(airline_name, cfg.livery_keywords, cfg.livery_exclude_keywords):
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
    if _is_special_livery_airline(airline_name, cfg.livery_keywords, cfg.livery_exclude_keywords):
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
    if _is_special_livery_airline(airline_name, cfg.livery_keywords, cfg.livery_exclude_keywords):
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

    if not _passes_schedule_filters(
        flight, cfg.route_type_days, cfg.route_type_time_filter,
        cfg.airport_tz, cfg.airport_lat, cfg.airport_lon,
    ):
        return None
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

    now_ts = int(datetime.now().timestamp())
    if not cfg.store.should_notify_route_type_change(
        flight_number, aircraft_type, cfg.airport_iata, cfg.route_type_renotify_days
    ):
        return None

    extra = {
        "established_type":  established_type,
        "established_count": established_count,
        "established_since": established_since_ts,
    }

    def on_notified(fn=flight_number, at=aircraft_type, iata=cfg.airport_iata, ts=now_ts):
        cfg.store.mark_route_type_notified(fn, at, iata, ts)

    return flight, registration, on_notified, extra


_FILTERS = [
    ("Special Livery",          check_special_livery),
    ("Watchlist Registration",  check_rego_watchlist),
    ("Watchlist Aircraft Type", check_type_watchlist),
    ("Watchlist Airline",       check_airline_watchlist),
    ("Rare Plane/Airline",      check_rare_plane),
    ("Route Equipment Change",  check_route_type_change),
]


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

        # Fetch pages -1 and -2 for passive DB updates (arrivals and departures).
        # These are flights with real timestamps that may have rotated off the
        # positive pages — never fed into the notification pipeline.
        hist_arrivals: dict = {}   # registration → flight (real arrival only)
        hist_departures: dict = {} # registration → flight (real departure only)
        for hist_page in [-p for p in cfg.fetch_pages]:
            try:
                hist_data = cfg.fr_api.get_airport_details(code=cfg.airport_code, page=hist_page)
                hist_schedule = hist_data["airport"]["pluginData"]["schedule"]
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
                        hist_departures.setdefault(reg, flight)
            except Exception as exc:
                log.debug("Failed to fetch page %d for passive updates: %s", hist_page, exc)

        # Record sightings — real arrivals from positive pages and page -1
        landed = {}
        for reg, flight in current_arrivals.items():
            real_arr = _safe_get(flight, "time", "real", "arrival", default=None)
            if isinstance(real_arr, (int, float)):
                landed[reg] = int(real_arr)
        for reg, flight in hist_arrivals.items():
            real_arr = int(_safe_get(flight, "time", "real", "arrival"))
            landed[reg] = max(landed.get(reg, 0), real_arr)
        if landed:
            cfg.store.bulk_update_sightings(landed)

        # Record route type history for arrivals and departures (passive learning)
        route_type_records = []
        for reg, flight in {**current_arrivals, **hist_arrivals}.items():
            real_arr = _safe_get(flight, "time", "real", "arrival", default=None)
            if not isinstance(real_arr, (int, float)):
                continue
            fn      = str(_safe_get(flight, "identification", "number", "default", default=""))
            ac_type = _safe_get(flight, "aircraft", "model", "code", default="")
            if fn and fn != "N/A" and ac_type and ac_type != "N/A":
                route_type_records.append((fn, ac_type, cfg.airport_iata, int(real_arr)))
        actual_departures = []  # (dep_fn, actual_dep_ts)
        for reg, flight in {**current_departures, **hist_departures}.items():
            real_dep = _safe_get(flight, "time", "real", "departure", default=None)
            if not isinstance(real_dep, (int, float)):
                continue
            fn      = str(_safe_get(flight, "identification", "number", "default", default=""))
            ac_type = _safe_get(flight, "aircraft", "model", "code", default="")
            if fn and fn != "N/A" and ac_type and ac_type != "N/A":
                route_type_records.append((fn, ac_type, cfg.airport_iata, int(real_dep)))
            if fn and fn not in ("N/A", "N\\A"):
                actual_departures.append((fn, int(real_dep)))
        if route_type_records:
            cfg.store.bulk_update_route_types(route_type_records)
        for dep_fn, dep_ts in actual_departures:
            cfg.store.record_actual_departure(dep_fn, cfg.airport_iata, dep_ts)

        # Pass 2: run filters and send notifications
        # Skip registrations already tracked in notification_record (still pending arrival)
        # to avoid double-notifying long-haul flights that appear in the schedule 12+ hours early.
        already_tracked = {r["registration"] for r in cfg.store.get_tracked_flights()}

        import asyncio
        for arriving_flight in all_arriving_flights:
            match = _first_matching_filter(arriving_flight, cfg)
            if match is None:
                continue
            flight, registration, notification_type, on_notified, extra = match
            if registration in already_tracked:
                continue
            await _send_notification(
                context, cfg, chat_id, flight, registration, notification_type, on_notified,
                extra=extra,
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
            if not photo_url:
                images = (rego_details or {}).get("aircraftImages") or []
                if images:
                    try:
                        photo_url = images[0]["images"]["medium"][0]["link"]
                        cfg.store.upsert_airframe_from_fr24(registration, photo_url=photo_url)
                    except (KeyError, IndexError):
                        pass
        except Exception as exc:
            log.warning("Could not fetch aircraft details for %s: %s", registration, exc)

    # Record departure pattern for future predictions (only when fresh rego_details available)
    if arrival_fn and arrival_fn != "N/A" and rego_details:
        _, dep_fn, al_name, al_iata, al_icao, dest_name, dest_iata, dest_icao, _ = get_next_departure(
            rego_details, cfg.airport_iata, cfg.airport_tz
        )
        if dep_fn:
            sched_dep_ts = _get_scheduled_dep_ts(rego_details, cfg.airport_iata, dep_fn)
            # Scheduled arrival time — used with scheduled_dep_ts to compute turnaround_secs
            sched_arr_ts_raw = _safe_get(flight, "time", "scheduled", "arrival", default=None)
            sched_arr_ts = int(sched_arr_ts_raw) if isinstance(sched_arr_ts_raw, (int, float)) else None
            # Estimated departure time
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
                scheduled_arr_ts=sched_arr_ts,
                airline_name=al_name, airline_iata=al_iata, airline_icao=al_icao,
                dest_name=dest_name, dest_iata=dest_iata, dest_icao=dest_icao,
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
                await _send_cancellation_notice(context, cfg, registration, flight_number, notification_type, arrival_ts)
                cfg.store.delete_tracked_flight(registration)
                continue
            elif status_text == "diverted":
                await _send_diversion_notice(context, cfg, registration, flight_number, notification_type, arrival_ts, diverted_airport)
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
                await _send_arrival_reminder(context, cfg, current_flight, registration, notification_type, flight_number)
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
                        await _send_aircraft_swap_notice(context, cfg, registration, new_rego, new_flight, flight_number, notification_type, arrival_ts)
                    # If no confirmed status and no swap, silently remove — likely landed early or FR24 data gap
                    cfg.store.delete_tracked_flight(registration)


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
