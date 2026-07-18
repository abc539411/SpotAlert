"""
_clean_dirty_airlines.py

For all photos tagged 'Dirty' in the Lightroom catalog, groups them by their
current airline value, confirms the correct airline name + ICAO code with the
user, updates the airline field, then removes the 'Dirty' keyword.

All other metadata is left untouched.

Usage:
    python _clean_dirty_airlines.py [--catalog-dir PATH]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ── Configuration ──────────────────────────────────────────────────────────────

_CATALOG_DIR     = Path(__file__).parent / "catalog"
_FR24_CACHE      = _CATALOG_DIR / "fr24_cache.json"
_AIRLINE_CODES   = _CATALOG_DIR / "airline_codes.json"
PLUGIN_ID        = "ch.aviationphoto.aircraftmetadata"
_LR_EPOCH_OFFSET = 978307200  # 1970-01-01 → 2001-01-01 (seconds)

# ── Utility ────────────────────────────────────────────────────────────────────

def _uid() -> str:
    return str(uuid.uuid4()).upper()

def _lr_now() -> float:
    return time.time() - _LR_EPOCH_OFFSET

def _split_airline(combined: str) -> Tuple[str, str]:
    """Split 'Qantas (QFA)' → ('Qantas', 'QFA'). Returns (name, code)."""
    m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", combined)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return combined.strip(), ""

def _fmt(v: str) -> str:
    return v if v else "(none)"

# ── FR24 ───────────────────────────────────────────────────────────────────────

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


def _load_airline_codes() -> Dict[str, str]:
    try:
        if _AIRLINE_CODES.exists():
            return json.loads(_AIRLINE_CODES.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_airline_codes(mapping: Dict[str, str]) -> None:
    try:
        _AIRLINE_CODES.write_text(
            json.dumps(dict(sorted(mapping.items())), indent=2), encoding="utf-8"
        )
    except Exception as exc:
        print(f"  ⚠  Could not save airline codes: {exc}")



def _parse_fr24_airline(result: dict) -> Tuple[str, str]:
    """Return (airline_with_code, icao_code) from a FR24 rego lookup."""
    data = result.get("data") or []
    if data:
        airline_obj = data[0].get("airline") or {}
    else:
        info = result.get("aircraftInfo") or {}
        airline_obj = info.get("airline") or {}

    airline_raw = airline_obj.get("name") or ""
    icao_code   = (airline_obj.get("code") or {}).get("icao") or ""
    # Greedy — see monitor.py's _enrich_and_store for why (nested-parens livery names).
    base_name   = re.sub(r"\s*\(.*\)", "", airline_raw).strip()
    # Some airlines' FR24 name field bakes a "Sticker(s)"/"Livery/Liveries"
    # qualifier into the visible name itself — see monitor.py's _clean_airline_name.
    base_name   = re.sub(r"\s*(liveries|livery|stickers?)\s*$", "", base_name, flags=re.IGNORECASE).strip()
    if base_name.lower() == "private owner":
        base_name = "Private Owner"
    airline = f"{base_name} ({icao_code})" if icao_code else base_name
    return airline, icao_code

# ── LR catalog helpers ─────────────────────────────────────────────────────────

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


def _remove_keyword_from_images(conn: sqlite3.Connection, image_ids: List[int], kw_id: int) -> None:
    ph = ",".join("?" * len(image_ids))
    conn.execute(
        f"DELETE FROM AgLibraryKeywordImage WHERE tag=? AND image IN ({ph})",
        [kw_id] + list(image_ids),
    )

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Fix airline values on Dirty-tagged photos")
    parser.add_argument("--catalog-dir", default=str(_CATALOG_DIR))
    args = parser.parse_args()

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

    conn = sqlite3.connect(str(catalog_path))
    conn.row_factory = sqlite3.Row

    # Find the 'Dirty' keyword
    dirty_row = conn.execute(
        "SELECT id_local FROM AgLibraryKeyword WHERE lc_name = 'dirty'"
    ).fetchone()
    if not dirty_row:
        print("✅  No 'Dirty' keyword found in catalog — nothing to do.")
        conn.close()
        return
    dirty_kw_id = dirty_row[0]

    # Find all image_ids tagged Dirty
    dirty_image_ids: Set[int] = {
        r[0] for r in conn.execute(
            "SELECT image FROM AgLibraryKeywordImage WHERE tag=?", (dirty_kw_id,)
        ).fetchall()
    }
    if not dirty_image_ids:
        print("✅  No photos tagged 'Dirty' — nothing to do.")
        conn.close()
        return

    print(f"  {len(dirty_image_ids)} photos tagged 'Dirty'")

    # Load spec_ids for airline and registration
    spec_rows = conn.execute(
        "SELECT key, id_local FROM AgPhotoPropertySpec WHERE sourcePlugin=?", (PLUGIN_ID,)
    ).fetchall()
    spec_ids = {r[0]: r[1] for r in spec_rows}

    if "airline" not in spec_ids:
        print("❌  Could not find 'airline' property spec in catalog.")
        conn.close()
        sys.exit(1)
    airline_spec_id = spec_ids["airline"]
    rego_spec_id    = spec_ids.get("registration")

    ph = ",".join("?" * len(dirty_image_ids))
    dirty_list = list(dirty_image_ids)

    # Get current airline value for each Dirty photo
    airline_by_image: Dict[int, str] = {}
    for photo_id, value in conn.execute(
        f"SELECT photo, internalValue FROM AgSearchablePhotoProperty "
        f"WHERE propertySpec=? AND photo IN ({ph})",
        [airline_spec_id] + dirty_list,
    ).fetchall():
        airline_by_image[photo_id] = (value or "").strip()

    # Get registration for each Dirty photo (to query FR24)
    rego_by_image: Dict[int, str] = {}
    if rego_spec_id:
        for photo_id, value in conn.execute(
            f"SELECT photo, internalValue FROM AgSearchablePhotoProperty "
            f"WHERE propertySpec=? AND photo IN ({ph})",
            [rego_spec_id] + dirty_list,
        ).fetchall():
            rego_by_image[photo_id] = (value or "").strip()

    # Group image_ids by distinct airline value; pick one rego per group
    by_airline: Dict[str, List[int]] = {}
    sample_rego: Dict[str, str] = {}
    for iid in dirty_image_ids:
        val = airline_by_image.get(iid, "")
        by_airline.setdefault(val, []).append(iid)
        if val not in sample_rego and rego_by_image.get(iid):
            sample_rego[val] = rego_by_image[iid]

    print(f"  {len(by_airline)} distinct airline value(s) to review")

    # Init FR24
    print("Initialising FR24 API …")
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from flightradar24api import FlightRadar24API
    fr_api = FlightRadar24API()
    fr24_cache   = _load_fr24_cache()
    airline_codes = _load_airline_codes()
    print(f"  FR24 cache: {len(fr24_cache)} regos  |  Airline codes: {len(airline_codes)} entries\n")

    done = skipped = 0

    for airline_val in sorted(by_airline.keys(), key=str.lower):
        image_ids = by_airline[airline_val]
        n = len(image_ids)
        cur_name, cur_code = _split_airline(airline_val)

        # Pull FR24 data for a sample rego to get a suggested airline name + code
        rego = sample_rego.get(airline_val, "")
        fr24_name, fr24_code = "", ""
        if rego:
            if rego in fr24_cache and fr24_cache[rego]:
                cached = fr24_cache[rego]
                fr24_name, fr24_code = _split_airline(cached[0])
            else:
                print(f"  Fetching FR24 data for {rego} …", flush=True)
                result = _call_fr24(fr_api, rego)
                if result:
                    fr24_airline, fr24_code = _parse_fr24_airline(result)
                    fr24_name, fr24_code = _split_airline(fr24_airline)
                    # Store in cache (merge into existing entry if present)
                    if rego in fr24_cache and fr24_cache[rego]:
                        entry = list(fr24_cache[rego])
                        entry[0] = fr24_airline
                        fr24_cache[rego] = tuple(entry)
                    else:
                        fr24_cache[rego] = (fr24_airline, "", "", "")
                    _save_fr24_cache(fr24_cache)

        # Step 1: exact match cur_name against airline_codes.json
        if cur_name in airline_codes:
            ac_match, ac_code = cur_name, airline_codes[cur_name]
        else:
            ac_match, ac_code = "", ""

        # Step 2: resolve final defaults — airline_codes wins over FR24
        def_name = ac_match or fr24_name or cur_name
        def_code = ac_code or fr24_code or cur_code

        W = 60
        print(f"  ┌{'─'*W}┐")
        print(f"  │  {'Current:':<16} {_fmt(airline_val):<{W-20}}│")
        if ac_match:
            ac_display = f"{ac_match} → {ac_code}" if ac_code else f"{ac_match} → (no code)"
            print(f"  │  {'Codes.json:':<16} {ac_display:<{W-20}}│")
        if rego:
            fr24_display = f"{fr24_name} ({fr24_code})" if fr24_code else (fr24_name or "(no data)")
            fr24_label   = f"FR24 ({rego}):"
            print(f"  │  {fr24_label:<16} {fr24_display:<{W-20}}│")
        print(f"  │  {'Photos:':<16} {n:<{W-20}}│")
        print(f"  └{'─'*W}┘")

        while True:
            new_name = input(f"  Airline name [{def_name}]: ").strip() or def_name
            _code_raw = input(f"  ICAO code    [{def_code}] (- to clear): ").strip().upper()
            new_code  = "" if _code_raw == "-" else (_code_raw or def_code)
            new_airline = f"{new_name} ({new_code})" if new_code else new_name

            print(f"  → Will write: {new_airline}")
            confirm = input("  [Y] confirm  [r] re-enter  [s] skip  [q] quit → ").strip().lower()

            if confirm in ("", "y"):
                for iid in image_ids:
                    _upsert_prop(conn, iid, airline_spec_id, new_airline)
                _remove_keyword_from_images(conn, image_ids, dirty_kw_id)
                cleaned_kw_id = _ensure_keyword(conn, "Cleaned")
                for iid in image_ids:
                    conn.execute(
                        "INSERT OR IGNORE INTO AgLibraryKeywordImage (image, tag) VALUES (?,?)",
                        (iid, cleaned_kw_id),
                    )
                conn.commit()
                # Save confirmed name → code back to airline_codes.json
                if new_name:
                    airline_codes[new_name] = new_code
                    _save_airline_codes(airline_codes)
                print(f"  ✔  Updated {n} photo(s)  →  {new_airline}\n")
                done += 1
                break
            elif confirm == "r":
                continue
            elif confirm == "s":
                print(f"  ↷  Skipped\n")
                skipped += 1
                break
            elif confirm == "q":
                conn.close()
                print(f"\n  Stopped early — confirmed: {done}  skipped: {skipped}")
                return

    conn.close()
    print(f"\n✅  Done — catalog saved in place: {catalog_path.name}")
    print(f"   Confirmed: {done}   Skipped: {skipped}")


if __name__ == "__main__":
    main()
