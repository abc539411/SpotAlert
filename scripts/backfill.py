#!/usr/bin/env python3
"""One-time historical backfill script — run manually after first install or schema changes.

Usage (on the Steam Deck or locally):
    python backfill.py

Reads config/config.env. Set FR24_USERNAME and FR24_PASSWORD for premium access
(higher rate limits and deeper history).

Populates three tables:
  - rego_sightings          last time each registration was seen at the airport
  - rare_plane_cooldowns        last time each airline+type combo visited
  - departure_patterns  arrival -> departure pairings with turnaround offset
                              (scheduled_arr_ts + scheduled_dep_ts used to compute
                               turnaround_secs so future predicted departure times
                               are derived from published schedule gaps, not actuals)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from environs import Env

from flightradar24api import FlightRadar24API
from store import SqliteStore

log = logging.getLogger(__name__)


def _safe_get(d, *keys, default=None):
    for k in keys:
        try:
            d = d[k]
        except (KeyError, TypeError, IndexError):
            return default
    return d if d is not None else default


def _best_ts(times: dict, kind: str) -> Optional[int]:
    for src in ("real", "estimated", "scheduled"):
        t = (times.get(src) or {}).get(kind)
        if isinstance(t, (int, float)):
            return int(t)
    return None


def _sched_ts(times: dict, kind: str) -> Optional[int]:
    t = (times.get("scheduled") or {}).get(kind)
    return int(t) if isinstance(t, (int, float)) else None


def _est_ts(times: dict, kind: str) -> Optional[int]:
    t = (times.get("estimated") or {}).get(kind)
    return int(t) if isinstance(t, (int, float)) else None


def backfill(
    fr_api: FlightRadar24API,
    store: SqliteStore,
    airport_code: str,
    airport_iata: str,
    sleep_secs: float = 2.0,
) -> None:
    """Sweep negative pages (historical arrivals) until we hit duplicate or empty data."""
    all_sightings: dict = {}
    all_rare: dict = {}       # (airline, type) -> latest arr_ts
    all_patterns: dict = {}   # (arr_fn, dep_fn) -> pattern tuple
    all_route_types: dict = {} # (flight_number, aircraft_type) -> set of timestamps

    # dep_by_rego: rego -> (dep_fn, best_dep_ts, estimated_dep_ts, sched_dep_ts,
    #                        al_name, al_iata, al_icao, d_name, d_iata, d_icao)
    dep_by_rego: dict = {}
    seen_registrations: set = set()

    # Pass 1: collect all pages — build global departure lookup and arrivals list
    all_pages_arrivals = []
    page = -1
    consecutive_no_new = 0

    while True:
        try:
            data = fr_api.get_airport_details(code=airport_code, flight_limit=100, page=page)
            schedule = data["airport"]["pluginData"]["schedule"]
        except Exception as exc:
            log.warning("Failed to fetch page %d: %s — stopping", page, exc)
            break

        # Accumulate departures into global lookup
        for dep_entry in (schedule.get("departures") or {}).get("data") or []:
            try:
                fl = dep_entry["flight"]
                rego = _safe_get(fl, "aircraft", "registration") or ""
                fn   = _safe_get(fl, "identification", "number", "default") or ""
                times = fl.get("time") or {}
                best_dep = _best_ts(times, "departure")
                if not (rego and fn and best_dep):
                    continue

                estimated_dep_ts = _est_ts(times, "departure")
                sched_dep_ts     = _sched_ts(times, "departure")

                airline    = fl.get("airline") or {}
                al_code    = airline.get("code") or {}
                dest       = (fl.get("airport") or {}).get("destination") or {}
                dest_code  = dest.get("code") or {}

                dep_by_rego[rego] = (
                    fn, best_dep, estimated_dep_ts, sched_dep_ts,
                    airline.get("name"), al_code.get("iata"), al_code.get("icao"),
                    dest.get("name"), dest_code.get("iata"), dest_code.get("icao"),
                )

                # Also track departure flight numbers in route_type_tracker
                ac_type = _safe_get(fl, "aircraft", "model", "code") or ""
                if fn and ac_type and best_dep:
                    all_route_types.setdefault((fn, ac_type), set()).add(int(best_dep))
            except (KeyError, TypeError):
                continue

        arrivals = (schedule.get("arrivals") or {}).get("data") or []
        if not arrivals:
            log.info("Page %d: no arrivals — stopping", page)
            break

        new_this_page = sum(
            1 for e in arrivals
            if _safe_get(e.get("flight", {}), "aircraft", "registration") not in seen_registrations
            and _safe_get(e.get("flight", {}), "aircraft", "registration") not in ("", None)
        )
        for e in arrivals:
            r = _safe_get(e.get("flight", {}), "aircraft", "registration")
            if r:
                seen_registrations.add(r)

        all_pages_arrivals.extend(arrivals)
        log.info("Page %d: %d arrivals, %d new registrations", page, len(arrivals), new_this_page)

        if new_this_page == 0:
            consecutive_no_new += 1
            if consecutive_no_new >= 2:
                log.info("No new registrations for 2 pages — history exhausted, stopping")
                break
        else:
            consecutive_no_new = 0

        page -= 1
        time.sleep(sleep_secs)

    # Pass 2: process arrivals against the complete global departure lookup
    for arr_entry in all_pages_arrivals:
        try:
            fl = arr_entry["flight"]
            rego = _safe_get(fl, "aircraft", "registration") or ""
            if not rego:
                continue

            airline_icao  = _safe_get(fl, "airline", "code", "icao") or ""
            aircraft_type = _safe_get(fl, "aircraft", "model", "code") or ""
            arr_fn        = _safe_get(fl, "identification", "number", "default") or ""
            times         = fl.get("time") or {}
            arr_ts        = _best_ts(times, "arrival")
            sched_arr_ts  = _sched_ts(times, "arrival")

            if arr_ts:
                all_sightings[rego] = max(all_sightings.get(rego, 0), arr_ts)

            if airline_icao and aircraft_type and arr_ts:
                key = (airline_icao, aircraft_type)
                all_rare[key] = max(all_rare.get(key, 0), arr_ts)

            if arr_fn and aircraft_type and arr_ts:
                all_route_types.setdefault((arr_fn, aircraft_type), set()).add(int(arr_ts))

            if arr_fn and rego in dep_by_rego:
                dep_fn, best_dep, est_dep_ts, sched_dep_ts, al_name, al_iata, al_icao, d_name, d_iata, d_icao = dep_by_rego[rego]
                if best_dep > (arr_ts or 0):
                    key = (arr_fn, dep_fn)
                    prev = all_patterns.get(key)
                    if prev is None:
                        all_patterns[key] = (
                            best_dep, est_dep_ts, sched_dep_ts, sched_arr_ts,
                            al_name, al_iata, al_icao, d_name, d_iata, d_icao,
                        )
                    else:
                        all_patterns[key] = (
                            max(prev[0], best_dep),
                            est_dep_ts  or prev[1],
                            sched_dep_ts or prev[2],
                            sched_arr_ts or prev[3],
                            al_name or prev[4], al_iata or prev[5], al_icao or prev[6],
                            d_name  or prev[7], d_iata  or prev[8], d_icao  or prev[9],
                        )
        except (KeyError, TypeError):
            continue

    # Write to DB
    if all_sightings:
        store.bulk_update_sightings(all_sightings)

    for (airline, aircraft_type), ts in all_rare.items():
        store.backfill_rare_plane_seen(airline, aircraft_type, ts)

    # Write route type history with accurate per-flight counts
    if all_route_types:
        records = [
            (fn, at, airport_iata, len(tss), min(tss), max(tss))
            for (fn, at), tss in all_route_types.items()
        ]
        with store._connect() as conn:
            conn.executemany(
                """
                INSERT INTO route_type_tracker
                    (flight_number, aircraft_type, airport_iata, count, first_seen_ts, last_seen_ts)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(flight_number, aircraft_type, airport_iata) DO UPDATE SET
                    count         = MAX(count, excluded.count),
                    first_seen_ts = MIN(first_seen_ts, excluded.first_seen_ts),
                    last_seen_ts  = MAX(last_seen_ts, excluded.last_seen_ts)
                """,
                records,
            )

    for (arr_fn, dep_fn), vals in all_patterns.items():
        best_dep, est_dep_ts, sched_dep_ts, sched_arr_ts, al_name, al_iata, al_icao, d_name, d_iata, d_icao = vals
        store.record_departure_pattern(
            arr_fn, dep_fn, airport_iata, best_dep,
            scheduled_dep_ts=sched_dep_ts,
            estimated_dep_ts=est_dep_ts,
            scheduled_arr_ts=sched_arr_ts,
            airline_name=al_name, airline_iata=al_iata, airline_icao=al_icao,
            dest_name=d_name, dest_iata=d_iata, dest_icao=d_icao,
        )

    log.info(
        "Backfill complete — %d sightings, %d rare plane records, %d departure patterns, %d route type records",
        len(all_sightings), len(all_rare), len(all_patterns), len(all_route_types),
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    config_file = "config/config.env"
    if not os.path.isfile(config_file):
        log.error("Config file not found: %s", config_file)
        sys.exit(1)

    env = Env()
    env.read_env(config_file)

    airport_code = env.str("AIRPORT_CODE")
    username = env.str("FR24_USERNAME", "")
    password = env.str("FR24_PASSWORD", "")

    data_dir = "data/"
    store = SqliteStore(os.path.join(data_dir, "spotalert.db"), config_file=config_file)

    fr_api = FlightRadar24API()
    if username and password:
        try:
            fr_api.login(username, password)
            log.info("Logged in to FR24 as %s", username)
        except Exception as exc:
            log.warning("FR24 login failed: %s — continuing without auth", exc)
    else:
        log.warning("No FR24_USERNAME/FR24_PASSWORD in config — rate limits may apply")

    try:
        data = fr_api.get_airport_details(code=airport_code)
        details = data["airport"]["pluginData"]["details"]
        airport_iata = details["code"]["iata"]
        airport_name = details["name"]
        log.info("Airport: %s (%s)", airport_name, airport_iata)
    except Exception as exc:
        log.warning("Could not fetch airport details: %s — using %s as IATA", exc, airport_code)
        airport_iata = airport_code

    log.info("Starting backfill for %s — sweeping historical pages until exhausted", airport_iata)
    backfill(fr_api, store, airport_code, airport_iata)


if __name__ == "__main__":
    main()
