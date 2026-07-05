"""One-time backfill: recompute every timeline_cache date's clusters.

Two related monitor.py bugs (both fixed alongside this script) could leave a
date's clusters_json computed with sunrise_ts=sunset_ts=0 — silently disabling
the lighting gate, so pre-sunrise/post-sunset flights were wrongly marked
"qualifying" — while its weather_json still looked perfectly fine:

1. The Open-Meteo fetch used to start at "today", one day short of the cluster
   window's "yesterday" entry, so yesterday always missed that cycle's fetch.
2. upsert_timeline_cache() only overwrites weather_json when given a non-None
   value — so on any cycle where that day's weather lookup came up empty
   (rate-limit, transient miss, anything), clusters_json still got silently
   recomputed with a sunrise/sunset of 0 while a STALE-BUT-CORRECT weather_json
   from an earlier cycle was left untouched, masking the corruption.

Because of #2, corruption isn't reliably detectable from weather_json alone —
so this reprocesses every cached date's clusters unconditionally, using each
date's existing weather_json when valid and only hitting the Open-Meteo API
for dates where it's missing/zeroed.

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
    import pytz
    tz = pytz.timezone(tz_name)
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
            # Open-Meteo returns naive local-wall-clock strings — .timestamp() on a
            # naive datetime uses the *host's* system timezone, not the airport's,
            # silently corrupting the result for any airport whose tz differs from
            # wherever this script happens to run. Must localize explicitly.
            sr = int(tz.localize(datetime.fromisoformat(sr_s)).timestamp()) if sr_s else 0
            ss = int(tz.localize(datetime.fromisoformat(ss_s)).timestamp()) if ss_s else 0
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


def backfill(db_path: str, dry_run: bool = False, force_refetch: bool = False) -> None:
    store = SqliteStore(db_path)

    with store._connect() as conn:
        rows = conn.execute("SELECT date, weather_json FROM timeline_cache ORDER BY date").fetchall()

    if not rows:
        print("timeline_cache is empty — nothing to do.")
        return

    all_dates = [row["date"] for row in rows]
    existing_weather: dict = {}
    missing_dates = []
    for row in rows:
        wj = row["weather_json"]
        w = None
        if wj:
            try:
                w = json.loads(wj)
            except Exception:
                w = None
        # --force-refetch: don't trust existing cached sunrise/sunset even if
        # present and non-zero — used after fixing the naive-datetime timezone
        # bug, whose corrupted values are wrong but still nonzero, so the normal
        # "missing or zeroed" check wouldn't have caught them.
        if w and w.get("sunrise_ts") and w.get("sunset_ts") and not force_refetch:
            existing_weather[row["date"]] = w
        else:
            missing_dates.append(row["date"])

    print(f"Reprocessing {len(all_dates)} cached date(s) ({all_dates[0]} .. {all_dates[-1]}); "
          f"{len(missing_dates)} need a fresh weather fetch.")

    lat = float(store.load_setting("_airport_lat") or 0)
    lon = float(store.load_setting("_airport_lon") or 0)
    tz_name = store.load_setting("_airport_tz") or "UTC"

    weather = dict(existing_weather)
    if missing_dates:
        if not lat or not lon:
            print("No cached airport lat/lon found in this DB — cannot fetch weather for "
                  f"{len(missing_dates)} date(s) missing it; those will be skipped.")
        else:
            fetched = _fetch_weather_range(lat, lon, tz_name, missing_dates[0], missing_dates[-1])
            weather.update(fetched)

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
    # whose departure lands on a target date despite arriving the day before.
    from datetime import timedelta
    window_start = (datetime.strptime(all_dates[0], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    window_end   = (datetime.strptime(all_dates[-1], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
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
    for date_str in all_dates:
        w = weather.get(date_str)
        if not w or not w.get("sunrise_ts") or not w.get("sunset_ts"):
            print(f"  {date_str}: no weather data available (too far in the past?) — skipped")
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

    print(f"\n{'[dry-run] would have updated' if dry_run else 'Updated'} {updated}/{len(all_dates)} date(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/spotalert.db")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-refetch", action="store_true",
                         help="Re-fetch weather for every date, ignoring existing cached values")
    args = parser.parse_args()
    backfill(args.db, dry_run=args.dry_run, force_refetch=args.force_refetch)
