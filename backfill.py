#!/usr/bin/env python3
"""One-time historical backfill script — run manually after first install.

Usage (on the Steam Deck or locally):
    python backfill.py

Reads config/config.env. Set FR24_USERNAME and FR24_PASSWORD for premium access
(higher rate limits and deeper history).

Populates three tables:
  - sighting_history        last time each registration was seen at the airport
  - rare_plane_history      last time each airline+type combo visited
  - flight_departure_pattern arrival flight number -> departure flight number pairings
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

from environs import Env

from flightradar24api import FlightRadar24API
from storage import SqliteStore

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


def backfill(
    fr_api: FlightRadar24API,
    store: SqliteStore,
    airport_code: str,
    airport_iata: str,
    sleep_secs: float = 2.0,
) -> None:
    """Sweep negative pages (historical arrivals) until we hit duplicate or empty data."""
    all_sightings: dict = {}
    all_rare: dict = {}        # (airline, type) -> latest ts
    all_patterns: dict = {}    # (arr_fn, dep_fn) -> (dep_ts, sched_ts, al_name, ...)
    dep_by_rego: dict = {}     # global across all pages so arrival/departure page offsets don't break matching
    seen_registrations: set = set()

    # Pass 1: collect all pages to build global departure lookup and arrivals data
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
                fn = _safe_get(fl, "identification", "number", "default") or ""
                dep_ts = _best_ts(fl.get("time") or {}, "departure")
                if rego and fn and dep_ts:
                    sched_only = (fl.get("time") or {}).get("scheduled", {}).get("departure")
                    airline = fl.get("airline") or {}
                    airline_code = airline.get("code") or {}
                    dest = (fl.get("airport") or {}).get("destination") or {}
                    dest_code = dest.get("code") or {}
                    dep_by_rego[rego] = (
                        fn, dep_ts,
                        int(sched_only) if isinstance(sched_only, (int, float)) else None,
                        airline.get("name"), airline_code.get("iata"), airline_code.get("icao"),
                        dest.get("name"), dest_code.get("iata"), dest_code.get("icao"),
                    )
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
        log.info("Page %d: %d arrivals, %d new registrations seen", page, len(arrivals), new_this_page)

        if new_this_page == 0:
            consecutive_no_new += 1
            if consecutive_no_new >= 2:
                log.info("No new registrations for 2 pages — history exhausted, stopping")
                break
        else:
            consecutive_no_new = 0

        page -= 1
        time.sleep(sleep_secs)

    # Pass 2: process all arrivals against the complete global departure lookup
    for arr_entry in all_pages_arrivals:
        try:
            fl = arr_entry["flight"]
            rego = _safe_get(fl, "aircraft", "registration") or ""
            if not rego:
                continue

            airline_icao = _safe_get(fl, "airline", "code", "icao") or ""
            aircraft_type = _safe_get(fl, "aircraft", "model", "code") or ""
            arr_fn = _safe_get(fl, "identification", "number", "default") or ""
            arr_ts = _best_ts(fl.get("time") or {}, "arrival")

            if arr_ts:
                all_sightings[rego] = max(all_sightings.get(rego, 0), arr_ts)

            if airline_icao and aircraft_type and arr_ts:
                key = (airline_icao, aircraft_type)
                all_rare[key] = max(all_rare.get(key, 0), arr_ts)

            if arr_fn and rego in dep_by_rego:
                dep_fn, dep_ts, sched_dep_ts, al_name, al_iata, al_icao, d_name, d_iata, d_icao = dep_by_rego[rego]
                if dep_ts > (arr_ts or 0):
                    key = (arr_fn, dep_fn)
                    prev = all_patterns.get(key, (0, None, None, None, None, None, None, None))
                    all_patterns[key] = (
                        max(prev[0], dep_ts),
                        sched_dep_ts or prev[1],
                        al_name or prev[2], al_iata or prev[3], al_icao or prev[4],
                        d_name or prev[5], d_iata or prev[6], d_icao or prev[7],
                    )
        except (KeyError, TypeError):
            continue

    # Write to DB
    if all_sightings:
        store.bulk_update_sightings(all_sightings)
    for (airline, aircraft_type), ts in all_rare.items():
        store.backfill_rare_plane_seen(airline, aircraft_type, ts)
    for (arr_fn, dep_fn), (dep_ts, sched_ts, al_name, al_iata, al_icao, d_name, d_iata, d_icao) in all_patterns.items():
        store.record_departure_pattern(
            arr_fn, dep_fn, airport_iata, dep_ts,
            scheduled_dep_ts=sched_ts,
            airline_name=al_name, airline_iata=al_iata, airline_icao=al_icao,
            dest_name=d_name, dest_iata=d_iata, dest_icao=d_icao,
        )

    log.info(
        "Backfill complete — %d sightings, %d rare plane records, %d departure patterns written to DB",
        len(all_sightings), len(all_rare), len(all_patterns),
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

    filters_dir = "config/filters/"
    store = SqliteStore(os.path.join(filters_dir, "spotalert.db"), config_file=config_file)

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
