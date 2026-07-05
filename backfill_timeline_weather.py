"""One-time backfill: recompute timeline_cache entries whose cached sunrise/sunset
was silently zeroed out by a date-range bug in monitor.py's weather fetch (fixed
alongside this script — see monitor.py's Open-Meteo call). Any date processed while
it sat in the "yesterday" slot of the cluster window got permanently cached with
sunrise_ts=sunset_ts=0, which disables the lighting gate (pre-sunrise/post-sunset
flights were incorrectly marked "qualifying") — and since only the last 4 days are
ever re-clustered, the bad entry for a given calendar date never self-heals.

Usage: python backfill_timeline_weather.py [--db path/to/spotalert.db] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import datetime

from store import SqliteStore
from web import cluster_day_for_cache
from lightroom import find_catalog


def _fetch_weather_range(lat: float, lon: float, tz_name: str, start_date: str, end_date: str) -> dict:
    tz_enc = tz_name.replace("/", "%2F")
    url = (f"https://historical-forecast-api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}&start_date={start_date}&end_date={end_date}"
           f"&daily=sunrise,sunset,weathercode,temperature_2m_max,temperature_2m_min&timezone={tz_enc}")
    with urllib.request.urlopen(url, timeout=20) as resp:
        om = json.loads(resp.read())
    daily = om.get("daily", {})
    out = {}
    for i, date_str in enumerate(daily.get("time", [])):
        try:
            sr_s = (daily.get("sunrise") or [])[i]
            ss_s = (daily.get("sunset") or [])[i]
            wc = int((daily.get("weathercode") or [])[i] or 0)
            sr = int(datetime.fromisoformat(sr_s).timestamp()) if sr_s else 0
            ss = int(datetime.fromisoformat(ss_s).timestamp()) if ss_s else 0
            tmax = (daily.get("temperature_2m_max") or [])[i]
            tmin = (daily.get("temperature_2m_min") or [])[i]
            SEVERE = {75, 82, 86, 95, 96, 99}
            out[date_str] = {
                "sunrise_ts": sr, "sunset_ts": ss,
                "weather_code": wc, "weather_severe": wc in SEVERE,
                "temp_max": round(tmax) if tmax is not None else None,
                "temp_min": round(tmin) if tmin is not None else None,
            }
        except Exception:
            pass
    return out


def backfill(db_path: str, dry_run: bool = False) -> None:
    store = SqliteStore(db_path)

    with store._connect() as conn:
        rows = conn.execute("SELECT date, weather_json FROM timeline_cache ORDER BY date").fetchall()

    corrupted_dates = []
    for row in rows:
        wj = row["weather_json"]
        if not wj:
            corrupted_dates.append(row["date"])
            continue
        try:
            w = json.loads(wj)
            if not w.get("sunrise_ts") or not w.get("sunset_ts"):
                corrupted_dates.append(row["date"])
        except Exception:
            corrupted_dates.append(row["date"])

    if not corrupted_dates:
        print("No corrupted timeline_cache entries found — nothing to do.")
        return

    print(f"Found {len(corrupted_dates)} corrupted date(s): {corrupted_dates[0]} .. {corrupted_dates[-1]}"
          f" ({len(corrupted_dates)} total)")

    lat = float(store.load_setting("_airport_lat") or 0)
    lon = float(store.load_setting("_airport_lon") or 0)
    tz_name = store.load_setting("_airport_tz") or "UTC"
    if not lat or not lon:
        print("No cached airport lat/lon found in this DB — cannot fetch weather. Aborting.")
        return

    weather = _fetch_weather_range(lat, lon, tz_name, corrupted_dates[0], corrupted_dates[-1])

    import pytz
    tz = pytz.timezone(tz_name)

    max_gap    = int(store.load_setting("SPOT_MAX_GAP_HOURS") or 3) * 3600
    lull_secs  = int(store.load_setting("SPOT_LULL_MINS") or 60) * 60
    max_spot   = int(store.load_setting("SPOT_MAX_SPOTTED") or 0)
    dep_thr    = int(store.load_setting("DEPARTURE_PATTERN_THRESHOLD") or 80)
    light_buf  = int(store.load_setting("SPOT_LIGHT_BUFFER_MINS") or 30) * 60
    max_lulls  = int(store.load_setting("SPOT_MAX_LULLS") or 2)
    light_gate_raw = store.load_setting("SPOT_LIGHTING_GATE")
    light_gate = (light_gate_raw.lower() == "true") if light_gate_raw else True
    bl_start   = store.load_setting("SPOT_BAD_LIGHT_START") or ""
    bl_end     = store.load_setting("SPOT_BAD_LIGHT_END") or ""

    with store._connect() as conn:
        excluded = {r[0] for r in conn.execute("SELECT registration FROM filter_exclusions").fetchall()}

    # Match the live code path's per-registration "already photographed" counts
    # (monitor.py's _spotted_map) — needed so re-clustering doesn't silently
    # re-qualify flights that should still be excluded by SPOT_MAX_SPOTTED.
    catalog = find_catalog() if max_spot else None
    spotted_map: dict = {}
    if catalog:
        with store._connect() as conn:
            regs = [r[0] for r in conn.execute("SELECT DISTINCT registration FROM flight_arrivals").fetchall()]
        airport_iata = store.load_setting("_airport_iata") or store.load_setting("AIRPORT_CODE") or ""
        for reg in regs:
            try:
                spotted_map[reg] = catalog.get_session_count_at_airport(reg, airport_iata) or 0
            except Exception:
                pass

    # Build events_by_date exactly like monitor.py: arrivals bucketed by their own
    # arrival date, departures bucketed by their own (possibly different) departure
    # date — a +/-1 day buffer on the query window catches overnight turnarounds
    # whose departure lands on a corrupted date despite arriving the day before.
    from datetime import timedelta
    window_start = (datetime.strptime(corrupted_dates[0], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    window_end   = (datetime.strptime(corrupted_dates[-1], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    with store._connect() as conn:
        fe_rows = conn.execute("""
            SELECT fe.registration, fe.flight_number, fe.arrival_ts,
                   fe.notif_types, fe.detail, fe.extra_info, fe.airline_icao,
                   fe.origin_iata, fe.current_status, fe.arr_label,
                   fd.dep_flight, fd.dep_ts, fd.dep_dest_iata, fd.dep_dest_name,
                   fd.dep_confidence, fd.dep_label,
                   a.photo_url, a.manufacturer
            FROM flight_arrivals fe
            LEFT JOIN flight_departures fd ON fd.arrival_id = fe.id
            LEFT JOIN airframes a ON a.registration = fe.registration
            WHERE fe.arrival_date BETWEEN ? AND ?
            ORDER BY fe.arrival_ts ASC
        """, (window_start, window_end)).fetchall()

    events_by_date: dict = {}
    for fr in fe_rows:
        arr_ts = fr["arrival_ts"]
        dep_ts = fr["dep_ts"]
        if dep_ts and not (arr_ts <= dep_ts <= arr_ts + 36 * 3600):
            dep_ts = None
        try:
            nt = json.loads(fr["notif_types"] or "[]")
        except Exception:
            nt = []
        if "Military" in nt or fr["current_status"] in ("Cancelled", "Diverted", "Swapped"):
            continue
        common = {
            "registration": fr["registration"], "flight_number": fr["flight_number"],
            "notif_types": nt, "detail": fr["detail"] or "", "extra_info": fr["extra_info"] or "",
            "airline_icao": fr["airline_icao"] or "", "photo_url": fr["photo_url"] or "",
            "manufacturer": fr["manufacturer"] or "", "origin_iata": fr["origin_iata"],
            "dep_flight": fr["dep_flight"], "dep_ts": dep_ts,
            "dep_dest_iata": fr["dep_dest_iata"], "dep_dest_name": fr["dep_dest_name"],
            "dep_confidence": fr["dep_confidence"], "dep_label": fr["dep_label"],
            "current_status": fr["current_status"], "arrival_ts": arr_ts,
            "arr_label": fr["arr_label"], "_spotted": spotted_map.get(fr["registration"], 0),
        }
        arr_date = datetime.fromtimestamp(arr_ts, tz).strftime("%Y-%m-%d")
        events_by_date.setdefault(arr_date, []).append({**common, "ts": arr_ts, "side": "arrival"})
        if dep_ts:
            dep_date = datetime.fromtimestamp(dep_ts, tz).strftime("%Y-%m-%d")
            events_by_date.setdefault(dep_date, []).append({**common, "ts": dep_ts, "side": "departure"})

    updated = 0
    for date_str in corrupted_dates:
        w = weather.get(date_str)
        if not w or not w.get("sunrise_ts") or not w.get("sunset_ts"):
            print(f"  {date_str}: no weather data available from API (too far in the past?) — skipped")
            continue

        events = events_by_date.get(date_str, [])
        clusters = cluster_day_for_cache(
            events, w["sunrise_ts"], w["sunset_ts"], tz,
            max_gap_secs=max_gap, notable_lull_secs=lull_secs,
            max_spotted=max_spot, dep_threshold=dep_thr,
            light_buf_secs=light_buf, lighting_gate=light_gate,
            bad_light_start=bl_start, bad_light_end=bl_end,
            max_lulls=max_lulls, excluded_regs=excluded,
        )

        print(f"  {date_str}: recomputed with sunrise={w['sunrise_ts']} sunset={w['sunset_ts']} "
              f"({len(events)} events -> {len(clusters)} clusters)")
        if not dry_run:
            store.upsert_timeline_cache(date_str, json.dumps(clusters), weather_json=json.dumps(w))
        updated += 1

    print(f"\n{'[dry-run] would have updated' if dry_run else 'Updated'} {updated}/{len(corrupted_dates)} date(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/spotalert.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    backfill(args.db, dry_run=args.dry_run)
