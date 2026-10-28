"""Flights the user has actually been on ("Flown", Collection tab): CSV parsing for the two supported exports
(MyFR24 / FlightDiary and Flighty) and the rules for normalising and merging rows.

Pure functions — the database side lives in ControlStore (flown_* methods) and the endpoints in web.py.

A flight is identified by (date, from, to, number): the same flight number on the same day between the same two
airports is the same flight, whichever airline code (IATA "QF" / ICAO "QFA") either export used for it.
"""
import csv
import io
import re
from typing import Dict, List, Optional, Tuple

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PAREN_RE = re.compile(r"\(([^()]*)\)\s*$")


def _clean(v) -> str:
    return (v or "").strip()


def norm_reg(v) -> str:
    return re.sub(r"\s+", "", _clean(v)).upper()


def split_flight_number(raw: str, iata_map: Optional[Dict[str, str]] = None, icao_map: Optional[Dict[str, str]] = None) -> Tuple[str, str, str]:
    """"QF465" / "QFA465" / "QFA 465" -> (iata, icao, number). Whichever airline code is unknown is left empty,
    or filled from the maps (iata->icao, icao->iata)."""
    s = re.sub(r"\s+", "", _clean(raw)).upper()
    if s.isdigit():
        return ("", "", s)
    m = re.match(r"^([A-Z]{3})(\d+[A-Z]?)$", s)          # 3 letters then digits: an ICAO code
    if m:
        icao, number = m.group(1), m.group(2)
        return ((icao_map or {}).get(icao, ""), icao, number)
    m = re.match(r"^([A-Z0-9]{2})(\d+[A-Z]?)$", s)        # 2 characters then digits: an IATA code
    if m:
        iata, number = m.group(1), m.group(2)
        return (iata, (iata_map or {}).get(iata, ""), number)
    m = re.match(r"^([A-Z]+)?(\d+[A-Z]?)$", s)
    if m:
        return ("", "", m.group(2))
    return ("", "", s)


def _airport_iata(text: str) -> str:
    """"Jinan / Yaoqiang (TNA/ZSJN)" -> TNA ; "SYD" -> SYD"""
    t = _clean(text)
    m = _PAREN_RE.search(t)
    if m:
        first = m.group(1).split("/")[0].strip().upper()
        return first if re.match(r"^[A-Z]{3}$", first) else ""
    return t.upper() if re.match(r"^[A-Za-z]{3}$", t) else ""


def _paren_pair(text: str) -> Tuple[str, str, str]:
    """"Shandong Airlines (SC/CDG)" -> ("Shandong Airlines", "SC", "CDG")"""
    t = _clean(text)
    m = _PAREN_RE.search(t)
    if not m:
        return t, "", ""
    parts = [p.strip().upper() for p in m.group(1).split("/")]
    name = t[:m.start()].strip()
    return name, (parts[0] if len(parts) > 0 and len(parts[0]) == 2 else ""), (parts[1] if len(parts) > 1 and len(parts[1]) == 3 else "")


def detect_format(header: List[str]) -> Optional[str]:
    cols = {(h or "").strip().lower() for h in header}
    if {"date", "flight number", "from", "to", "registration"} <= cols:
        return "myfr24"
    if {"date", "airline", "flight", "from", "to", "tail number"} <= cols:
        return "flighty"
    return None


def parse_csv(raw: bytes) -> Tuple[Optional[str], List[dict], int]:
    """-> (format or None, rows, skipped). Each row: date, number, airline_name, airline_iata, airline_icao, from_iata,
    to_iata, type_name, type_code, registration, seat, note."""
    text = raw.decode("utf-8-sig", errors="replace")
    rd = csv.DictReader(io.StringIO(text, newline=""))
    fmt = detect_format(rd.fieldnames or [])
    if not fmt:
        return None, [], 0
    rows: List[dict] = []
    skipped = 0
    for r in rd:
        date = _clean(r.get("Date"))[:10]
        if not _DATE_RE.match(date):
            skipped += 1
            continue
        if fmt == "myfr24":
            airline_name, a_iata, a_icao = _paren_pair(r.get("Airline"))
            iata, icao, number = split_flight_number(r.get("Flight number"), {a_iata: a_icao} if a_iata and a_icao else None)
            a_iata, a_icao = a_iata or iata, a_icao or icao
            _atext = _clean(r.get("Aircraft"))
            _tm = re.search(r"\(([A-Za-z0-9]{3,4})\)\s*$", _atext)
            tname, tcode = (_atext[:_tm.start()].strip(), _tm.group(1).upper()) if _tm else (_atext, "")
            row = {"date": date, "number": number, "airline_name": airline_name, "airline_iata": a_iata, "airline_icao": a_icao,
                   "from_iata": _airport_iata(r.get("From")), "to_iata": _airport_iata(r.get("To")),
                   "type_name": tname, "type_code": tcode if len(tcode) in (3, 4) else "", "registration": norm_reg(r.get("Registration")),
                   "seat": _clean(r.get("Seat number")), "note": _clean(r.get("Note"))}
        else:
            if _clean(r.get("Canceled")).lower() == "true":
                skipped += 1
                continue
            number = re.sub(r"\s+", "", _clean(r.get("Flight"))).upper()
            row = {"date": date, "number": number, "airline_name": "", "airline_iata": "", "airline_icao": _clean(r.get("Airline")).upper(),
                   "from_iata": _airport_iata(r.get("From")), "to_iata": _airport_iata(r.get("To")),
                   "type_name": _clean(r.get("Aircraft Type Name")), "type_code": "", "registration": norm_reg(r.get("Tail Number")),
                   "seat": _clean(r.get("Seat")), "note": _clean(r.get("Notes"))}
        if not row["number"] and not (row["from_iata"] and row["to_iata"]):
            skipped += 1
            continue
        rows.append(row)
    return fmt, rows, skipped


def fill_airline_codes(rows: List[dict], iata_to_icao: Dict[str, str], icao_to_iata: Dict[str, str]) -> None:
    """Complete each row's missing IATA / ICAO airline code from what is known (our airframes table + the rows themselves)."""
    for r in rows:
        if r["airline_iata"] and not r["airline_icao"]:
            r["airline_icao"] = iata_to_icao.get(r["airline_iata"], "")
        if r["airline_icao"] and not r["airline_iata"]:
            r["airline_iata"] = icao_to_iata.get(r["airline_icao"], "")


def display_flight_number(row: dict) -> str:
    code = row.get("airline_iata") or row.get("airline_icao") or ""
    return f"{code}{row.get('number') or ''}"


def clean_manual(data: dict, iata_to_icao: Dict[str, str], icao_to_iata: Dict[str, str]) -> Tuple[Optional[dict], Optional[str]]:
    """Validate + normalise a hand-typed flight. Returns (row, error)."""
    date = _clean(data.get("date"))[:10]
    if not _DATE_RE.match(date):
        return None, "Date must look like 2026-10-28"
    iata, icao, number = split_flight_number(data.get("flight_number"), iata_to_icao, icao_to_iata)
    if not number:
        return None, "Flight number is required"
    f, t = _clean(data.get("from_iata")).upper(), _clean(data.get("to_iata")).upper()
    for a in (f, t):
        if a and not re.match(r"^[A-Z]{3}$", a):
            return None, "Airports are 3-letter IATA codes (SYD)"
    reg = norm_reg(data.get("registration"))
    if reg and not re.match(r"^[A-Z0-9\-]{2,10}$", reg):
        return None, "That registration does not look right"
    return {"date": date, "number": number, "airline_name": _clean(data.get("airline_name"))[:60],
            "airline_iata": iata, "airline_icao": icao, "from_iata": f, "to_iata": t,
            "type_name": _clean(data.get("type_name"))[:60], "type_code": _clean(data.get("type_code")).upper()[:4],
            "registration": reg, "seat": _clean(data.get("seat"))[:8], "note": _clean(data.get("note"))[:200]}, None
