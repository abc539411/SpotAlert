"""
_update_lr_from_fr24.py

Edits the Lightroom catalog IN PLACE — replaces JetPhotos metadata with live
FR24 data, per session with user confirmation.  All other catalog data is
untouched.  A 'Cleaned' keyword is stamped onto each confirmed session so the
script can be resumed if interrupted.

Usage:
    python _update_lr_from_fr24.py [--interval SECS] [--rego REGO] [--catalog-dir PATH]
"""

from __future__ import annotations

import sys
from pathlib import Path
# Add project root to path so monitor.py and flightradar24api are importable
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import concurrent.futures
import re
import sqlite3
import struct
import sys
import time
import uuid
import zlib
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ── Configuration ──────────────────────────────────────────────────────────────

_CATALOG_DIR     = Path(__file__).parent / "catalog"
_FR24_CACHE      = _CATALOG_DIR / "fr24_cache.json"
_USER_OVERRIDES  = _CATALOG_DIR / "user_overrides.json"
_AIRLINE_CODES   = _CATALOG_DIR / "airline_codes.json"
PLUGIN_ID        = "ch.aviationphoto.aircraftmetadata"
_LR_EPOCH_OFFSET = 978307200  # 1970-01-01 → 2001-01-01 (seconds)


_KEYWORDS_TO_CHECK  = {
    "Cargo", "Drone", "Helicopters", "Historical",
    "Military", "Police", "Private Planes", "Special Livery",
}
_KEYWORDS_TO_REMOVE = {"AircraftMetadata-RegNotFound", "AircraftMetadata-WrongReg", "SPTA"}

# Airline name aliases — treated as equivalent when comparing Current vs FR24
_AIRLINE_ALIASES: Dict[str, str] = {
    "cathay pacific airways":    "cathay pacific",
    "jetstar airways":           "jetstar",
    "virgin australia airlines": "virgin australia",
    "regional express (rex)":    "rex",
    "regional express":          "rex",
    "xiamen airlines":           "xiamen air",
    "china eastern airlines":    "china eastern",
    "thai airways international": "thai airways",
    "united parcel service (ups)": "ups",
    "united parcel service":       "ups",
    "scandinavian airlines (sas)": "sas",
    "scandinavian airlines":       "sas",
    "klm royal dutch airlines":   "klm",
}

from monitor import _derive_manufacturer as _derive_manufacturer_base

def _derive_manufacturer(model_text: str) -> str:
    return _derive_manufacturer_base(model_text) or ""

# ── Utility ────────────────────────────────────────────────────────────────────

def _uid() -> str:
    return str(uuid.uuid4()).upper()

def _lr_now() -> float:
    return time.time() - _LR_EPOCH_OFFSET

def _is_lr_running() -> bool:
    try:
        import subprocess
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq lightroom.exe", "/NH"],
            encoding="utf-8", errors="ignore", stderr=subprocess.DEVNULL,
        )
        return "lightroom.exe" in out.lower()
    except Exception:
        return False

def _fmt(v: str) -> str:
    return v if v else "(none)"

# ── FR24 ───────────────────────────────────────────────────────────────────────

def _parse_fr24(result: dict) -> Tuple[str, str, str, str]:
    """Return (airline, aircraft_type, manufacturer, livery)."""
    data = result.get("data") or []
    if data:
        airline_obj   = data[0].get("airline") or {}
        airline_raw   = airline_obj.get("name") or ""
        icao_code     = (airline_obj.get("code") or {}).get("icao") or ""
        aircraft_type = ((data[0].get("aircraft") or {}).get("model") or {}).get("code") or ""
        model_text    = ((data[0].get("aircraft") or {}).get("model") or {}).get("text") or ""
    else:
        info          = result.get("aircraftInfo") or {}
        airline_obj   = info.get("airline") or {}
        airline_raw   = airline_obj.get("name") or ""
        icao_code     = (airline_obj.get("code") or {}).get("icao") or ""
        aircraft_type = (info.get("model") or {}).get("code") or ""
        model_text    = (info.get("model") or {}).get("text") or ""

    base_airline = re.sub(r"\s*\(.+?\)", "", airline_raw).strip()
    if base_airline.lower() == "private owner":
        base_airline = "Private Owner"
    m = re.search(r"\((.+?)\)", airline_raw)
    livery = m.group(1).strip() if m else ""
    airline = f"{base_airline} ({icao_code})" if icao_code else base_airline
    return airline, aircraft_type, _derive_manufacturer(model_text), livery


def _load_user_overrides() -> Dict[str, Optional[Tuple[str, str, str, str]]]:
    try:
        if _USER_OVERRIDES.exists():
            import json as _json
            raw = _json.loads(_USER_OVERRIDES.read_text(encoding="utf-8"))
            return {k: (tuple(v) if v else None) for k, v in raw.items()}
    except Exception:
        pass
    return {}


def _save_user_overrides(overrides: Dict[str, Optional[Tuple[str, str, str, str]]]) -> None:
    try:
        import json as _json
        _USER_OVERRIDES.write_text(
            _json.dumps({k: list(v) if v else None for k, v in overrides.items()}, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"  ⚠  Could not save user overrides: {exc}")


def _load_airline_codes() -> Dict[str, str]:
    try:
        if _AIRLINE_CODES.exists():
            import json as _json
            return _json.loads(_AIRLINE_CODES.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_airline_codes(mapping: Dict[str, str]) -> None:
    try:
        import json as _json
        _AIRLINE_CODES.write_text(
            _json.dumps(dict(sorted(mapping.items())), indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"  ⚠  Could not save airline codes: {exc}")


def _load_fr24_cache() -> Dict[str, Optional[Tuple[str, str, str, str]]]:
    try:
        if _FR24_CACHE.exists():
            import json as _json
            raw = _json.loads(_FR24_CACHE.read_text(encoding="utf-8"))
            return {k: (tuple(v) if v else None) for k, v in raw.items()}
    except Exception:
        pass
    return {}


def _save_fr24_cache(cache: Dict[str, Optional[Tuple[str, str, str, str]]]) -> None:
    try:
        import json as _json
        _FR24_CACHE.write_text(
            _json.dumps({k: list(v) if v else None for k, v in cache.items()}, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"  ⚠  Could not save FR24 cache: {exc}")


def _call_fr24(fr_api, rego: str, timeout: float = 10.0) -> Optional[dict]:
    for attempt in range(2):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(fr_api.get_rego_details, rego)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                print(f"  ⏱  Timeout for {rego}")
                return None
            except Exception as exc:
                msg = str(exc)
                if "429" in msg or "too many" in msg.lower():
                    if attempt == 0:
                        print("  ⚠  Rate limited — backing off 30s …")
                        time.sleep(30)
                        continue
                    print("❌  Still rate limited. Aborting.")
                    sys.exit(1)
                print(f"  ⚠  FR24 error for {rego}: {exc}")
                return None
    return None

# ── LR catalog helpers ─────────────────────────────────────────────────────────

def _ensure_spec(conn: sqlite3.Connection, key: str, display_name: str, attrs: str) -> int:
    row = conn.execute(
        "SELECT id_local FROM AgPhotoPropertySpec WHERE key = ? AND sourcePlugin = ?",
        (key, PLUGIN_ID),
    ).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO AgPhotoPropertySpec "
        "(id_global, flattenedAttributes, key, pluginVersion, sourcePlugin, userVisibleName) "
        "VALUES (?,?,?,?,?,?)",
        (_uid(), attrs, key, 0.0, PLUGIN_ID, display_name),
    )
    return cur.lastrowid


def _ensure_keyword(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute(
        "SELECT id_local FROM AgLibraryKeyword WHERE lc_name = ?", (name.lower(),)
    ).fetchone()
    if row:
        return row[0]
    root = conn.execute(
        "SELECT id_local FROM AgLibraryKeyword WHERE parent IS NULL"
    ).fetchone()
    root_id = root[0] if root else None
    cur = conn.execute(
        "INSERT INTO AgLibraryKeyword "
        "(id_global, dateCreated, genealogy, imageCountCache, includeOnExport, "
        "includeParents, includeSynonyms, lc_name, name, parent) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (_uid(), _lr_now(), "", 0, 1, 1, 1, name.lower(), name, root_id),
    )
    own_id = cur.lastrowid
    r_s, o_s = str(root_id) if root_id else "", str(own_id)
    genealogy = (f"/{len(r_s)}{root_id}/{len(o_s)}{own_id}" if root_id
                 else f"/{len(o_s)}{own_id}")
    conn.execute("UPDATE AgLibraryKeyword SET genealogy=? WHERE id_local=?", (genealogy, own_id))
    return own_id


def _upsert_prop(conn: sqlite3.Connection, image_id: int, spec_id: int, value: str) -> None:
    """Insert or update a searchable property value for a photo."""
    lc = value.lower() if value else ""
    row = conn.execute(
        "SELECT id_local FROM AgSearchablePhotoProperty WHERE photo=? AND propertySpec=?",
        (image_id, spec_id),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE AgSearchablePhotoProperty SET internalValue=?, lc_idx_internalValue=? "
            "WHERE id_local=?",
            (value, lc, row[0]),
        )
    else:
        conn.execute(
            "INSERT INTO AgSearchablePhotoProperty "
            "(id_global, dataType, internalValue, lc_idx_internalValue, photo, propertySpec) "
            "VALUES (?,?,?,?,?,?)",
            (_uid(), None, value, lc, image_id, spec_id),
        )


def _add_keyword_to_images(conn: sqlite3.Connection, image_ids: List[int], kw_id: int) -> None:
    existing = {r[0] for r in conn.execute(
        "SELECT image FROM AgLibraryKeywordImage WHERE tag=?", (kw_id,)
    ).fetchall()}
    for iid in image_ids:
        if iid not in existing:
            conn.execute(
                "INSERT INTO AgLibraryKeywordImage (image, tag) VALUES (?,?)", (iid, kw_id)
            )


def _remove_keyword_from_images(conn: sqlite3.Connection, image_ids: List[int], kw_id: int) -> None:
    ph = ",".join("?" * len(image_ids))
    conn.execute(
        f"DELETE FROM AgLibraryKeywordImage WHERE tag=? AND image IN ({ph})",
        [kw_id] + list(image_ids),
    )


def _remove_stale_keywords(conn: sqlite3.Connection) -> None:
    ph = ",".join("?" * len(_KEYWORDS_TO_REMOVE))
    conn.execute(
        f"DELETE FROM AgLibraryKeywordImage WHERE tag IN "
        f"(SELECT id_local FROM AgLibraryKeyword WHERE name IN ({ph}))",
        list(_KEYWORDS_TO_REMOVE),
    )
    conn.commit()
    print(f"  Removed stale keyword tags: {', '.join(sorted(_KEYWORDS_TO_REMOVE))}")


_DC_TITLE_RE = re.compile(
    r"\s*<dc:title>\s*<rdf:Alt>.*?</rdf:Alt>\s*</dc:title>",
    re.DOTALL,
)

def _clear_xmp_titles(conn: sqlite3.Connection) -> None:
    """Strip dc:title from all compressed XMP blobs in Adobe_AdditionalMetadata."""
    rows = conn.execute(
        "SELECT id_local, xmp FROM Adobe_AdditionalMetadata WHERE xmp IS NOT NULL"
    ).fetchall()
    updated = 0
    for row_id, xmp_blob in rows:
        try:
            text = zlib.decompress(xmp_blob[4:]).decode("utf-8", errors="replace")
            if "<dc:title>" not in text:
                continue
            cleaned = _DC_TITLE_RE.sub("", text)
            encoded = cleaned.encode("utf-8")
            header = struct.pack("<I", len(encoded))
            new_blob = header + zlib.compress(encoded)
            conn.execute(
                "UPDATE Adobe_AdditionalMetadata SET xmp=? WHERE id_local=?",
                (new_blob, row_id),
            )
            updated += 1
        except Exception as exc:
            pass  # skip malformed blobs
    conn.commit()
    print(f"  Cleared dc:title from {updated} XMP blobs")
    # Clear title contribution from IPTC search index (LR will rebuild on next open)
    conn.execute("UPDATE AgMetadataSearchIndex SET iptcSearchIndex = ''")
    conn.commit()

# ── Load catalog data ──────────────────────────────────────────────────────────

def _load_catalog(conn: sqlite3.Connection) -> Tuple[Dict[str, int], Dict[int, dict], Dict[int, Set[str]]]:
    """
    Returns:
        spec_ids — {field_key: spec_id}
        records  — {image_id: {registration, airport_iata, airline, aircraft_type,
                               aircraft_manufacturer, livery, capture_time}}
        kw_map   — {image_id: set of keyword names}
    """
    rows = conn.execute(
        "SELECT id_local, key FROM AgPhotoPropertySpec WHERE sourcePlugin=?", (PLUGIN_ID,)
    ).fetchall()
    spec_ids = {key: sid for sid, key in rows}

    _FIELDS = ("registration", "airport_iata", "airline",
               "aircraft_type", "aircraft_manufacturer", "aircraft_notes")
    sid_to_field = {spec_ids[f]: f for f in _FIELDS if f in spec_ids}

    records: Dict[int, dict] = {}
    for iid, ct in conn.execute("SELECT id_local, captureTime FROM Adobe_images").fetchall():
        records[iid] = {
            "image_id": iid,
            "capture_time": (ct or "").split(".")[0].rstrip("Z"),
            "registration": "", "airport_iata": "", "airline": "",
            "aircraft_type": "", "aircraft_manufacturer": "", "aircraft_notes": "",
        }

    if sid_to_field:
        ph = ",".join("?" * len(sid_to_field))
        for photo_id, prop_spec, value in conn.execute(
            f"SELECT photo, propertySpec, internalValue "
            f"FROM AgSearchablePhotoProperty WHERE propertySpec IN ({ph})",
            list(sid_to_field.keys()),
        ).fetchall():
            if photo_id in records and prop_spec in sid_to_field:
                records[photo_id][sid_to_field[prop_spec]] = (value or "").strip()

    records = {iid: r for iid, r in records.items() if r["registration"]}

    kw_map: Dict[int, Set[str]] = {}
    for image_id, kw_name in conn.execute(
        "SELECT ki.image, kw.name FROM AgLibraryKeywordImage ki "
        "JOIN AgLibraryKeyword kw ON kw.id_local=ki.tag"
    ).fetchall():
        if kw_name:
            kw_map.setdefault(image_id, set()).add(kw_name)

    return spec_ids, records, kw_map

# ── Session grouping ───────────────────────────────────────────────────────────

def _group_sessions(records: Dict[int, dict]) -> Dict[str, List[List[int]]]:
    by_rego: Dict[str, List[dict]] = {}
    for r in records.values():
        by_rego.setdefault(r["registration"], []).append(r)

    result: Dict[str, List[List[int]]] = {}
    for rego, photos in by_rego.items():
        photos.sort(key=lambda p: p["capture_time"])
        sessions: List[List[int]] = []
        cur: List[int] = []
        prev_dt: Optional[datetime] = None
        prev_apt = ""
        for p in photos:
            try:
                dt = datetime.fromisoformat(p["capture_time"]).replace(tzinfo=None) if p["capture_time"] else None
            except ValueError:
                dt = None
            apt = p["airport_iata"]
            if (prev_dt is None or dt is None or apt != prev_apt
                    or (dt - prev_dt).total_seconds() > 43200):
                if cur:
                    sessions.append(cur)
                cur = [p["image_id"]]
            else:
                cur.append(p["image_id"])
            prev_dt = dt
            prev_apt = apt
        if cur:
            sessions.append(cur)
        result[rego] = sessions
    return result

# ── Prompts ────────────────────────────────────────────────────────────────────

def _prompt_metadata(
    rego: str,
    session_ids: List[int],
    records: Dict[int, dict],
    fr24: Optional[Tuple[str, str, str, str]],
    kw_map: Optional[Dict[int, Set[str]]] = None,
) -> str:
    rep = records[session_ids[0]]
    date_str = rep["capture_time"][:10] if rep["capture_time"] else "?"
    apt = rep["airport_iata"] or "?"
    n = len(session_ids)

    CW_FIELD = 14
    CW_VAL   = 34
    inner    = 2 + CW_FIELD + 2 + CW_VAL + 4 + CW_VAL + 2  # "  field  cur  →  fr24  "

    cur = rep
    fields = [
        ("Airline",      cur["airline"],               fr24[0] if fr24 else None),
        ("Type",         cur["aircraft_type"],          fr24[1] if fr24 else None),
        ("Manufacturer", cur["aircraft_manufacturer"],  fr24[2] if fr24 else None),
        ("Notes/Livery", cur["aircraft_notes"],         fr24[3] if fr24 else None),
    ]
    def _normalize_airline(v: str) -> str:
        base = _split_airline(v)[0].lower().strip()
        return _AIRLINE_ALIASES.get(base, base)

    def _fields_differ(name, cur_val, fr24_val):
        if fr24_val is None:
            return True
        if name == "Airline":
            # Ignore ICAO code + known aliases when comparing
            return _normalize_airline(cur_val) != _normalize_airline(fr24_val)
        return cur_val != fr24_val

    diff_fields = [(name, c, f) for name, c, f in fields if _fields_differ(name, c, f)]

    print()
    print(f"  ┌{'─'*inner}┐")
    hdr = f"  {rego:<10}  {date_str}  @  {apt:<4}  —  {n} photo{'s' if n != 1 else ''}"
    print(f"  │{hdr:<{inner}}│")

    if not diff_fields and fr24:
        print(f"  │{'─'*inner}│")
        same = f"  ✔  All fields match FR24 data"
        print(f"  │{same:<{inner}}│")
    elif fr24 is None:
        print(f"  │{'─'*inner}│")
        print(f"  │{'  (no FR24 data)':<{inner}}│")
    else:
        print(f"  │{'─'*inner}│")
        col_hdr = f"  {'Field':<{CW_FIELD}}  {'Current':<{CW_VAL}}  →  {'FR24':<{CW_VAL}}"
        print(f"  │{col_hdr:<{inner}}│")
        print(f"  │{'─'*inner}│")
        for name, cur_val, fr24_val in diff_fields:
            row = f"  {name:<{CW_FIELD}}  {_fmt(cur_val):<{CW_VAL}}  →  {_fmt(fr24_val):<{CW_VAL}}"
            print(f"  │{row:<{inner}}│")

    # Warn if any photo in this session is tagged Special Livery
    if kw_map:
        livery_count = sum(1 for iid in session_ids if "Special Livery" in kw_map.get(iid, set()))
        if livery_count:
            warn = f"  ⚠  {livery_count}/{len(session_ids)} photo(s) tagged 'Special Livery' — livery may have since been removed"
            print(f"  │{warn:<{inner}}│")

    print(f"  └{'─'*inner}┘")

    opts = "[Y] FR24  [n] keep  [s] skip  [c] custom  [q] quit"
    if not fr24:
        opts = "[n] keep  [s] skip  [q] quit"
    while True:
        choice = input(f"  {opts} → ").strip().lower()
        if choice in ("", "y") and fr24:
            return "y"
        if choice == "n":
            return "n"
        if choice == "s":
            return "s"
        if choice == "c":
            return "c"
        if choice == "q":
            return "q"


def _split_airline(combined: str) -> Tuple[str, str]:
    """Split 'Qantas (QFA)' → ('Qantas', 'QFA'). Returns (name, code)."""
    m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", combined)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return combined.strip(), ""


def _prompt_custom(
    records: Dict[int, dict],
    session_ids: List[int],
    fr24: Optional[Tuple[str, str, str, str]],
) -> Tuple[str, str, str, str]:
    rep = records[session_ids[0]]
    d_airline = fr24[0] if fr24 else rep["airline"]
    d_actype  = fr24[1] if fr24 else rep["aircraft_type"]
    d_mfr     = fr24[2] if fr24 else rep["aircraft_manufacturer"]
    d_livery  = fr24[3] if fr24 else rep["aircraft_notes"]

    d_name, d_code = _split_airline(d_airline)

    print("  Enter values (blank = use FR24 default):")
    name   = input(f"    Airline name [{d_name}]: ").strip() or d_name
    code   = input(f"    ICAO code    [{d_code}]: ").strip() or d_code
    airline = f"{name} ({code})" if code else name

    actype = input(f"    Type         [{d_actype}]: ").strip() or d_actype
    mfr    = input(f"    Maker        [{d_mfr}]: ").strip() or d_mfr
    _lv_raw = input(f"    Livery       [{d_livery}] (- to clear): ").strip()
    livery = "" if _lv_raw == "-" else (_lv_raw or d_livery)
    return airline, actype, mfr, livery


def _prompt_keywords(session_ids: List[int], kw_map: Dict[int, Set[str]]) -> Dict[int, Set[str]]:
    additions: Dict[int, Set[str]] = {}
    n = len(session_ids)
    for kw in sorted(_KEYWORDS_TO_CHECK):
        tagged  = [iid for iid in session_ids if kw in kw_map.get(iid, set())]
        missing = [iid for iid in session_ids if iid not in tagged]
        m = len(tagged)
        if 0 < m < n:
            ans = input(f"  '{kw}': {m}/{n} photos tagged — apply to all {n}? [Y/n] ").strip().lower()
            if ans in ("", "y"):
                for iid in missing:
                    additions.setdefault(iid, set()).add(kw)
    return additions

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Clean LR catalog metadata using FR24 data")
    parser.add_argument("--interval", type=float, default=1.5,
                        help="Seconds between FR24 API calls (default 1.5)")
    parser.add_argument("--rego", help="Process only this registration (for testing)")
    parser.add_argument("--catalog-dir", default=str(_CATALOG_DIR))
    args = parser.parse_args()

    # Locate .lrcat
    catalog_dir = Path(args.catalog_dir)
    lrcats = list(catalog_dir.glob("*.lrcat"))
    if not lrcats:
        print(f"❌  No .lrcat found in {catalog_dir}")
        sys.exit(1)
    if len(lrcats) > 1:
        print(f"❌  Multiple .lrcat files in {catalog_dir} — keep only one.")
        sys.exit(1)
    catalog_path = lrcats[0]


    print(f"Catalog: {catalog_path.name}")

    # FR24 API
    print("Initialising FR24 API …")
    from flightradar24api import FlightRadar24API
    fr_api = FlightRadar24API()

    # Open catalog read-write
    conn = sqlite3.connect(str(catalog_path))
    conn.row_factory = sqlite3.Row

    # Ensure Cleaned + Skipped keywords exist
    cleaned_kw_id = _ensure_keyword(conn, "Cleaned")
    skipped_kw_id = _ensure_keyword(conn, "Skipped")
    conn.commit()

    # One-time bulk cleanup (unconditional)
    _remove_stale_keywords(conn)
    _clear_xmp_titles(conn)

    # Load catalog
    print("Loading catalog data …")
    spec_ids, records, kw_map = _load_catalog(conn)

    print(f"  {len(records)} photos with aircraft registrations")

    sessions_by_rego = _group_sessions(records)
    cleaned_ids: Set[int] = {iid for iid, kws in kw_map.items() if "Cleaned" in kws}
    skipped_ids: Set[int] = {iid for iid, kws in kw_map.items() if "Skipped" in kws}

    # Any rego where at least one session was previously skipped → defer all its sessions
    skipped_regos: Set[str] = {
        rego for rego, sessions in sessions_by_rego.items()
        if any(bool(s) and all(iid in skipped_ids for iid in s) for s in sessions)
    }

    def _fully_done(ids: List[int]) -> bool:
        return bool(ids) and all(iid in cleaned_ids for iid in ids)

    def _is_skipped(ids: List[int]) -> bool:
        return bool(ids) and all(iid in skipped_ids for iid in ids)

    all_regos = sorted(sessions_by_rego.keys())
    if args.rego:
        target = args.rego.strip().upper()
        if target not in sessions_by_rego:
            print(f"❌  {target} not found in catalog.")
            sys.exit(1)
        regos = [target]
    else:
        regos = all_regos

    # Flatten all sessions: normal order first (chronological), skipped sessions at the end
    normal_sessions: List[Tuple[str, str, List[int]]] = []
    deferred_sessions: List[Tuple[str, str, List[int]]] = []
    for rego in regos:
        for session_ids in sessions_by_rego[rego]:
            first_dt = records[session_ids[0]]["capture_time"] if session_ids else ""
            if _is_skipped(session_ids):
                deferred_sessions.append((first_dt, rego, session_ids))
            else:
                normal_sessions.append((first_dt, rego, session_ids))
    normal_sessions.sort(key=lambda x: x[0])
    deferred_sessions.sort(key=lambda x: x[0])
    all_sessions = normal_sessions + deferred_sessions

    pending_count = sum(1 for _, _, s in all_sessions if not _fully_done(s))
    print(f"  {pending_count} sessions to process (chronological order)\n")

    # ── Mode toggle ───────────────────────────────────────────────────────────
    print("\n  How should previously entered custom values be handled?")
    print("    [1] FR24 data takes precedence  (default)")
    print("    [2] Manually entered values override FR24 data")
    _mode = input("  Select mode [1/2]: ").strip()
    override_mode = (_mode == "2")
    print(f"  Mode: {'User overrides take precedence' if override_mode else 'FR24 takes precedence'}\n")

    # Load persistent caches
    fr24_disk_cache  = _load_fr24_cache()
    user_overrides   = _load_user_overrides()
    airline_codes    = _load_airline_codes()
    print(f"  FR24 cache: {len(fr24_disk_cache)} regos  |  User overrides: {len(user_overrides)} regos")

    # Pre-fetch FR24 data for all pending regos in a background thread.
    # One request at a time, rate-limited, so the user can start answering
    # session prompts immediately while fetches run in parallel.
    pending_regos: List[str] = []
    seen_regos: Set[str] = set()
    for _dt, rego, s_ids in all_sessions:
        if rego not in seen_regos and not all(_fully_done(s) for s in sessions_by_rego[rego]):
            pending_regos.append(rego)
            seen_regos.add(rego)

    fr24_futures: Dict[str, Future] = {}

    def _fetch_rego(rego: str) -> Optional[Tuple[str, str, str, str]]:
        # In override mode: user-entered values take precedence over FR24 cache
        if override_mode and rego in user_overrides:
            return user_overrides[rego]
        if rego in fr24_disk_cache:
            return fr24_disk_cache[rego]
        result = _call_fr24(fr_api, rego, timeout=10)
        time.sleep(args.interval)
        if result is None:
            return None
        try:
            parsed = _parse_fr24(result)
            fr24_disk_cache[rego] = parsed
            _save_fr24_cache(fr24_disk_cache)
            return parsed
        except Exception as exc:
            return None

    fetch_executor = ThreadPoolExecutor(max_workers=1)
    for rego in pending_regos:
        fr24_futures[rego] = fetch_executor.submit(_fetch_rego, rego)

    done = skipped = errors = 0
    quit_early = False

    for _dt, rego, session_ids in all_sessions:
        if quit_early:
            break
        if _fully_done(session_ids):
            continue
        if rego in skipped_regos:
            continue  # entire rego deferred — will appear at end of queue on restart

        # Get FR24 data — blocks only if the background fetch isn't done yet
        fr24_data: Optional[Tuple[str, str, str, str]] = None
        if rego in fr24_futures:
            future = fr24_futures[rego]
            if not future.done():
                print(f"  ⏳ Waiting for FR24 fetch of {rego} …", flush=True)
            fr24_data = future.result()
            if fr24_data is None:
                errors += 1
            elif fr24_data[0].lower() == "private owner":
                fr24_data = ("Private Owner",) + fr24_data[1:]

        choice = _prompt_metadata(rego, session_ids, records, fr24_data, kw_map)

        if choice == "q":
            quit_early = True
            break
        if choice == "s":
            # Mark entire rego as skipped — write Skipped to ALL sessions of this rego
            skipped_regos.add(rego)
            all_rego_ids = [iid for s in sessions_by_rego[rego] for iid in s]
            _add_keyword_to_images(conn, all_rego_ids, skipped_kw_id)
            conn.commit()
            skipped += 1
            continue

        if choice == "y" and fr24_data:
            airline, actype, mfr, livery = fr24_data
        elif choice == "c":
            airline, actype, mfr, livery = _prompt_custom(records, session_ids, fr24_data)
        else:  # n — keep original
            rep0 = records[session_ids[0]]
            airline, actype, mfr, livery = (
                rep0["airline"], rep0["aircraft_type"],
                rep0["aircraft_manufacturer"], rep0["aircraft_notes"],
            )

        # If no ICAO code in the airline string, check saved mapping then prompt
        if not re.search(r'\([A-Z]{2,4}\)', airline):
            airline_name = airline.strip()
            if airline_name in airline_codes:
                code = airline_codes[airline_name]
                print(f"  ✔  Auto-applied code {code} for '{airline_name}'")
                airline = f"{airline_name} ({code})"
            else:
                code = input(f"  ⚠  No airline code for '{airline_name}' — enter ICAO code (blank to skip): ").strip().upper()
                if code:
                    airline = f"{airline_name} ({code})"
                    airline_codes[airline_name] = code
                    _save_airline_codes(airline_codes)

        if choice == "c":
            if override_mode:
                # Override mode: always save manually-entered values so they win next run
                user_overrides[rego] = (airline, actype, mfr, livery)
                _save_user_overrides(user_overrides)
            else:
                # Default mode: only fill fields FR24 left empty
                if rego in fr24_disk_cache and fr24_data is not None:
                    cached = list(fr24_disk_cache[rego])
                    if not fr24_data[0] and airline:   cached[0] = airline
                    if not fr24_data[1] and actype:    cached[1] = actype
                    if not fr24_data[2] and mfr:       cached[2] = mfr
                    fr24_disk_cache[rego] = tuple(cached)
                    _save_fr24_cache(fr24_disk_cache)
                elif fr24_data is None:
                    fr24_disk_cache[rego] = (airline, actype, mfr, livery)
                    _save_fr24_cache(fr24_disk_cache)

        # Write to catalog
        for iid in session_ids:
            _upsert_prop(conn, iid, spec_ids["airline"],               airline)
            _upsert_prop(conn, iid, spec_ids["aircraft_type"],         actype)
            if "aircraft_manufacturer" in spec_ids:
                _upsert_prop(conn, iid, spec_ids["aircraft_manufacturer"], mfr)
            if "aircraft_notes" in spec_ids:
                _upsert_prop(conn, iid, spec_ids["aircraft_notes"],    livery)

        # Keyword consistency
        kw_adds = _prompt_keywords(session_ids, kw_map)
        for iid, new_kws in kw_adds.items():
            kw_map.setdefault(iid, set()).update(new_kws)
            for kw in new_kws:
                kw_id = _ensure_keyword(conn, kw)
                _add_keyword_to_images(conn, [iid], kw_id)

        # Sync Special Livery keyword with notes field
        sl_kw_id = _ensure_keyword(conn, "Special Livery")
        if livery.strip():
            _add_keyword_to_images(conn, session_ids, sl_kw_id)
        else:
            _remove_keyword_from_images(conn, session_ids, sl_kw_id)

        # Stamp Cleaned, remove Skipped if it was previously deferred
        _remove_keyword_from_images(conn, session_ids, skipped_kw_id)
        _add_keyword_to_images(conn, session_ids, cleaned_kw_id)
        conn.commit()
        done += 1

    fetch_executor.shutdown(wait=False, cancel_futures=True)
    conn.close()
    print(f"\n✅  Done — catalog saved in place: {catalog_path.name}")
    print(f"   Confirmed: {done}   Skipped: {skipped}   FR24 errors: {errors}")


if __name__ == "__main__":
    main()
