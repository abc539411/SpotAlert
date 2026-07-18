"""Standalone verification script for cancellation/diversion/aircraft-swap detection.

Not a pytest suite (this repo has no test framework) — run directly:
    python test_cancellation_diversion.py

Runs each scenario against a fresh scratch SQLite DB (never the live data/spotalert.db),
driving the REAL monitor.run_check() with a fake FR24 API, and asserts on the resulting
flight_arrivals / flight_departures rows. See docs/09-fr24-flight-lifecycle.md §11 for the
design this verifies, and the plan file's §6 (Verification) for what each scenario covers.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import monitor
from store import SqliteStore
import bootstrap as main_mod  # AppConfig moved from main.py to bootstrap.py
                               # when the monitor loop was split into its own
                               # process (monitor_service.py) — kept the
                               # "main_mod" alias to minimize the diff below.


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeFrApi:
    """Stand-in for FlightRadar24API — pages/lookups come from dicts the test sets directly."""

    def __init__(self):
        self.pages: dict = {}          # {page_int: {"arrivals": [...], "departures": [...]}}
        self.rego_details: dict = {}   # {registration: {...}}
        self.flight_by_number: dict = {}  # {flight_number: {"data": [...]}}

    def get_airport_details(self, code=None, page=1):
        d = self.pages.get(page, {"arrivals": [], "departures": []})
        return {"airport": {"pluginData": {"schedule": {
            "arrivals":   {"data": d.get("arrivals", [])},
            "departures": {"data": d.get("departures", [])},
        }}}}

    def get_rego_details(self, aircraft):
        return self.rego_details.get(aircraft, {"data": []})

    def get_flight_by_number(self, flight_number):
        return self.flight_by_number.get(flight_number, {"data": []})


class FakeJob:
    def __init__(self, data=None):
        self.data = data


class FakeBot:
    async def send_message(self, *a, **k):
        pass


class FakeContext:
    def __init__(self, cfg):
        self.bot_data = {"cfg": cfg}
        self.job = FakeJob()
        self.bot = FakeBot()


def make_flight(fn, reg, ac_type="B738", airline="Test Air", airline_icao="TST",
                 origin_iata="MEL", origin_name="Melbourne", dest_iata="SYD", dest_name="Sydney",
                 sched_arr=None, est_arr=None, real_arr=None,
                 sched_dep=None, est_dep=None, real_dep=None,
                 status_text="scheduled", diverted=None):
    """Build one raw arrivals/departures-page entry (the {"flight": {...}} wrapper _parse_aircraft expects)."""
    return {"flight": {
        "aircraft": {"registration": reg, "model": {"code": ac_type}},
        "identification": {"number": {"default": fn}},
        "airline": {"name": airline, "code": {"iata": airline_icao[:2], "icao": airline_icao}},
        "airport": {
            "origin":      {"code": {"iata": origin_iata}, "name": origin_name,
                             "position": {"country": {"code": "AU"}}},
            "destination": {"code": {"iata": dest_iata}, "name": dest_name},
        },
        "time": {
            "scheduled": {"arrival": sched_arr, "departure": sched_dep},
            "estimated": {"arrival": est_arr, "departure": est_dep},
            "real":      {"arrival": real_arr, "departure": real_dep},
        },
        "status": {"generic": {"status": {"text": status_text, "diverted": diverted or ""}}},
    }}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class Harness:
    def __init__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="spotalert_test_")
        self.store = SqliteStore(os.path.join(self.tmpdir, "test.db"))
        self.fr_api = FakeFrApi()
        self.cfg = main_mod.AppConfig(
            airport_code="TST", airport_name="Test Airport", airport_iata="SYD",
            airport_icao="YSSY", airport_tz="UTC", airport_lat=-33.9, airport_lon=151.2,
            fetch_pages=[1], chat_id="",
            livery_keywords=[], livery_exclude_keywords=[],
            rare_plane_min_absence_days=30,
            check_interval=1800,
            military_check_interval=900, military_radius_nm=50,
            military_max_alt_ft=5000, military_renotify_hours=4,
        )
        self.cfg.fr_api = self.fr_api
        self.cfg.store = self.store
        self.cfg.catalog = None

    def close(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def check(self):
        await monitor.run_check(FakeContext(self.cfg))

    def seed_watchlist(self, reg: str):
        """Guarantee `reg` matches Step 3's filters, so it always gets a flight_arrivals row."""
        with self.store._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO filter_regos(registration, last_notified_ts) VALUES (?, 0)",
                (reg,),
            )

    def seed_rare_plane_seen(self, airline_icao: str, aircraft_type: str):
        """Pre-seed rare_plane_cooldowns so this airline/type combo is already
        'recently seen' and won't independently match check_rare_plane. Exclusion
        no longer suppresses filter matching at all (it's a per-viewer web display
        concept now, applied in web.py — see _exclusion_owner/cluster_day_for_cache
        — never an ingestion gate), so a control aircraft needs a real reason not
        to match: rare-plane is the only filter in this suite that matches
        everything by default in a brand-new scratch DB."""
        now_ts = int(datetime.now().timestamp())
        with self.store._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO rare_plane_cooldowns"
                "(airline, aircraft_type, last_seen_ts, last_notified_ts) VALUES (?,?,?,0)",
                (airline_icao.strip(), aircraft_type.strip(), now_ts),
            )

    def seed_arrival_row(self, reg, fn, arr_ts, arr_date, current_status="Scheduled"):
        """Directly seed a flight_arrivals row, bypassing Steps 1-4 (already-trusted pipeline),
        so a scenario can start from 'this row already exists' without needing a live-matching
        fresh detection first. Sets current_status explicitly — record_filter_match() alone
        leaves it NULL, but a real row always gets it set by Step 5 on the very next check,
        and Pass 1's unresolved-rows query only ever selects 'Scheduled'/'Arriving' rows."""
        now_ts = int(datetime.now().timestamp())
        arrival_id = self.store.record_filter_match(
            reg, fn, ["Rego Watchlist"], arr_ts, now_ts,
            detail="Test Air (B738)", extra_info="", arrival_date=arr_date,
        )
        self.store.update_flight_event_status(
            reg, fn, current_status, arr_ts, arrival_date=arr_date,
        )
        return arrival_id

    def row(self, reg, fn, arr_date):
        with self.store._connect() as conn:
            return conn.execute(
                "SELECT * FROM flight_arrivals WHERE registration=? AND flight_number=? AND arrival_date=?",
                (reg, fn, arr_date),
            ).fetchone()

    def departure_row(self, arrival_id):
        with self.store._connect() as conn:
            return conn.execute(
                "SELECT * FROM flight_departures WHERE arrival_id=?", (arrival_id,),
            ).fetchone()


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

FAILURES = []

# Test config uses airport_tz="UTC" — compute test times in UTC explicitly, since a naive
# datetime.now() uses the machine's local timezone and would silently land on the wrong
# calendar day once monitor.py re-interprets the resulting timestamp in UTC.
_TODAY_DT = datetime.now(timezone.utc)
TODAY = _TODAY_DT.strftime("%Y-%m-%d")


def today_at(hour: int, minute: int = 0) -> datetime:
    """A UTC datetime on today's (UTC) date — using a fixed past calendar date would let the
    existing, unmodified 30-day retention cleanup purge test rows mid-scenario."""
    return _TODAY_DT.replace(hour=hour, minute=minute, second=0, microsecond=0)


def check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(f"{label}: {detail}")


async def scenario_explicit_cancellation():
    print("\n=== Scenario: explicit cancellation ===")
    h = Harness()
    try:
        reg, fn, date = "VH-CN1", "QF900", TODAY
        arr_ts = int(today_at(10, 0).timestamp())
        h.seed_watchlist(reg)
        arrival_id = h.seed_arrival_row(reg, fn, arr_ts, date)

        h.fr_api.pages = {1: {"arrivals": [
            make_flight(fn, reg, sched_arr=arr_ts, est_arr=arr_ts, status_text="canceled"),
        ], "departures": []}}
        await h.check()

        row = h.row(reg, fn, date)
        check("row survives (not deleted)", row is not None)
        if row:
            check("current_status = Cancelled", row["current_status"] == "Cancelled",
                  row["current_status"])
            check("arr_label = Confirmed Cancelled", row["arr_label"] == "Confirmed Cancelled",
                  row["arr_label"])
        check("no flight_departures row", h.departure_row(arrival_id) is None)
    finally:
        h.close()


async def scenario_explicit_diversion():
    print("\n=== Scenario: explicit diversion ===")
    h = Harness()
    try:
        reg, fn, date = "VH-DV1", "QF901", TODAY
        arr_ts = int(today_at(10, 0).timestamp())
        h.seed_watchlist(reg)
        h.seed_arrival_row(reg, fn, arr_ts, date)

        h.fr_api.pages = {1: {"arrivals": [
            make_flight(fn, reg, sched_arr=arr_ts, est_arr=arr_ts,
                        status_text="diverted", diverted="MEL"),
        ], "departures": []}}
        await h.check()

        row = h.row(reg, fn, date)
        check("row survives", row is not None)
        if row:
            check("current_status = Diverted", row["current_status"] == "Diverted", row["current_status"])
            check("diverted_to_iata = MEL", row["diverted_to_iata"] == "MEL", row["diverted_to_iata"])
    finally:
        h.close()


async def scenario_departure_released_to_sibling():
    print("\n=== Scenario: departure released to same-day sibling ===")
    h = Harness()
    try:
        reg = "VH-SIB1"
        fn1, fn2 = "QF910", "QF911"
        date = TODAY
        arr1_ts = int(today_at(8, 0).timestamp())
        arr2_ts = int(today_at(14, 0).timestamp())
        dep_ts  = int(today_at(20, 0).timestamp())
        h.seed_watchlist(reg)
        h.seed_arrival_row(reg, fn1, arr1_ts, date)
        h.seed_arrival_row(reg, fn2, arr2_ts, date)

        # Check 1: fn1 claims the only available departure (NZ900), fn2 still Scheduled
        h.fr_api.pages = {1: {"arrivals": [
            make_flight(fn1, reg, sched_arr=arr1_ts, est_arr=arr1_ts),
            make_flight(fn2, reg, sched_arr=arr2_ts, est_arr=arr2_ts),
        ], "departures": [
            make_flight("NZ900", reg, sched_dep=dep_ts, est_dep=dep_ts),
        ]}}
        await h.check()
        row1 = h.row(reg, fn1, date)
        dep1 = h.departure_row(row1["id"])
        check("fn1 claimed NZ900 on check 1", dep1 is not None and dep1["dep_flight"] == "NZ900",
              dep1["dep_flight"] if dep1 else None)

        # Check 2: fn1 now cancelled — same check, fn2 should claim NZ900 instead of falling to prediction
        h.fr_api.pages = {1: {"arrivals": [
            make_flight(fn1, reg, sched_arr=arr1_ts, est_arr=arr1_ts, status_text="canceled"),
            make_flight(fn2, reg, sched_arr=arr2_ts, est_arr=arr2_ts),
        ], "departures": [
            make_flight("NZ900", reg, sched_dep=dep_ts, est_dep=dep_ts),
        ]}}
        await h.check()

        row1 = h.row(reg, fn1, date)
        row2 = h.row(reg, fn2, date)
        check("fn1 now Cancelled", row1["current_status"] == "Cancelled", row1["current_status"])
        check("fn1's departure released", h.departure_row(row1["id"]) is None)
        dep2 = h.departure_row(row2["id"])
        check("fn2 claimed NZ900 in the SAME check", dep2 is not None and dep2["dep_flight"] == "NZ900",
              dep2["dep_flight"] if dep2 else None)
    finally:
        h.close()


async def scenario_cancelled_departure_repairs():
    print("\n=== Scenario: cancelled departure gets a fresh candidate ===")
    h = Harness()
    try:
        reg, fn, date = "VH-CD1", "QF920", TODAY
        arr_ts = int(today_at(8, 0).timestamp())
        dep1_ts = int(today_at(12, 0).timestamp())
        dep2_ts = int(today_at(18, 0).timestamp())
        h.seed_watchlist(reg)
        h.seed_arrival_row(reg, fn, arr_ts, date)

        # Check 1: claims real departure NZ800
        h.fr_api.pages = {1: {"arrivals": [make_flight(fn, reg, sched_arr=arr_ts, est_arr=arr_ts)],
                              "departures": [make_flight("NZ800", reg, sched_dep=dep1_ts, est_dep=dep1_ts)]}}
        await h.check()
        row = h.row(reg, fn, date)
        dep = h.departure_row(row["id"])
        check("claimed NZ800 first", dep is not None and dep["dep_flight"] == "NZ800",
              dep["dep_flight"] if dep else None)

        # Check 2: NZ800 now cancelled, NZ801 available — arrival itself untouched
        h.fr_api.pages = {1: {"arrivals": [make_flight(fn, reg, sched_arr=arr_ts, est_arr=arr_ts)],
                              "departures": [
                                  make_flight("NZ800", reg, sched_dep=dep1_ts, est_dep=dep1_ts, status_text="canceled"),
                                  make_flight("NZ801", reg, sched_dep=dep2_ts, est_dep=dep2_ts),
                              ]}}
        await h.check()
        row = h.row(reg, fn, date)
        check("arrival itself unaffected", row["current_status"] not in ("Cancelled", "Diverted"),
              row["current_status"])
        dep = h.departure_row(row["id"])
        check("re-paired with NZ801", dep is not None and dep["dep_flight"] == "NZ801",
              dep["dep_flight"] if dep else None)
    finally:
        h.close()


async def scenario_diverted_departure_noop():
    print("\n=== Scenario: diverted departure — no-op ===")
    h = Harness()
    try:
        reg, fn, date = "VH-DD1", "QF930", TODAY
        arr_ts = int(today_at(8, 0).timestamp())
        dep_ts = int(today_at(12, 0).timestamp())
        h.seed_watchlist(reg)
        h.seed_arrival_row(reg, fn, arr_ts, date)

        h.fr_api.pages = {1: {"arrivals": [make_flight(fn, reg, sched_arr=arr_ts, est_arr=arr_ts)],
                              "departures": [make_flight("NZ700", reg, sched_dep=dep_ts, est_dep=dep_ts)]}}
        await h.check()
        row = h.row(reg, fn, date)
        dep_before = h.departure_row(row["id"])

        h.fr_api.pages = {1: {"arrivals": [make_flight(fn, reg, sched_arr=arr_ts, est_arr=arr_ts)],
                              "departures": [make_flight("NZ700", reg, sched_dep=dep_ts, est_dep=dep_ts,
                                                          status_text="diverted", diverted="MEL")]}}
        await h.check()
        row = h.row(reg, fn, date)
        dep_after = h.departure_row(row["id"])
        check("arrival unaffected", row["current_status"] not in ("Cancelled", "Diverted"),
              row["current_status"])
        check("departure row unchanged", dep_after is not None and dep_after["dep_flight"] == "NZ700",
              dep_after["dep_flight"] if dep_after else None)
    finally:
        h.close()


async def scenario_aircraft_swap():
    print("\n=== Scenario: aircraft swap ===")
    h = Harness()
    try:
        reg_a, reg_b, fn, date = "VH-SWA", "VH-SWB", "QF940", TODAY
        arr_ts = int(today_at(10, 0).timestamp())
        h.seed_watchlist(reg_a)  # only A is independently interesting
        h.seed_rare_plane_seen("TST", "B738")  # B's swapped-in flight uses these defaults —
                                                 # pre-seed so it doesn't independently rare-plane-match
        h.seed_arrival_row(reg_a, fn, arr_ts, date)

        # Check: fn now shows under reg_b instead of reg_a
        h.fr_api.pages = {1: {"arrivals": [
            make_flight(fn, reg_b, sched_arr=arr_ts, est_arr=arr_ts),
        ], "departures": []}}
        await h.check()

        row_a = h.row(reg_a, fn, date)
        row_b = h.row(reg_b, fn, date)
        check("original (A) marked Swapped", row_a is not None and row_a["current_status"] == "Swapped",
              row_a["current_status"] if row_a else None)
        check("B not independently filter-matched → no new row", row_b is None)
    finally:
        h.close()


async def scenario_swap_revert():
    print("\n=== Scenario: swap revert ===")
    h = Harness()
    try:
        reg_a, reg_b, fn, date = "VH-REA", "VH-REB", "QF941", TODAY
        arr_ts = int(today_at(10, 0).timestamp())
        h.seed_watchlist(reg_a)
        h.seed_watchlist(reg_b)  # both interesting, so B gets its own row when it takes over
        h.seed_arrival_row(reg_a, fn, arr_ts, date)

        # Check 1: swap to B — B is independently filter-matched, gets a fresh row (later first_seen_ts)
        h.fr_api.pages = {1: {"arrivals": [make_flight(fn, reg_b, sched_arr=arr_ts, est_arr=arr_ts)],
                              "departures": []}}
        await h.check()
        row_a = h.row(reg_a, fn, date)
        row_b = h.row(reg_b, fn, date)
        check("A marked Swapped after check 1", row_a["current_status"] == "Swapped", row_a["current_status"])
        check("B got a successor row", row_b is not None)

        # Check 2: reverts back to A
        h.fr_api.pages = {1: {"arrivals": [make_flight(fn, reg_a, sched_arr=arr_ts, est_arr=arr_ts)],
                              "departures": []}}
        await h.check()
        row_a = h.row(reg_a, fn, date)
        row_b = h.row(reg_b, fn, date)
        check("A reopened to a live state", row_a["current_status"] != "Swapped", row_a["current_status"])
        check("B (successor) hard-deleted", row_b is None)
    finally:
        h.close()


async def scenario_silent_disappearance():
    print("\n=== Scenario: silent disappearance (both branches) + confirmation-call cap ===")
    h = Harness()
    try:
        with h.store._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('MONITOR_CANCEL_GRACE_MINS', '1')")
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('MONITOR_DIVERTED_GRACE_MINS', '1')")
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('MONITOR_ABSENCE_CHECKS', '2')")
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('MONITOR_CONFIRM_CALL_CAP', '1')")

        past_ts = int((datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp())
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        reg_sched, fn_sched = "VH-ABS1", "QF950"  # Scheduled branch — never seen airborne
        reg_arr,   fn_arr   = "VH-ABS2", "QF951"  # Arriving branch — was tracked airborne
        h.seed_watchlist(reg_sched)
        h.seed_watchlist(reg_arr)
        h.seed_arrival_row(reg_sched, fn_sched, past_ts, date, current_status="Scheduled")
        h.seed_arrival_row(reg_arr,   fn_arr,   past_ts, date, current_status="Arriving")

        # Both absent from every page — confirmation lookup returns empty for both.
        h.fr_api.pages = {1: {"arrivals": [], "departures": []}}
        h.fr_api.flight_by_number = {fn_sched: {"data": []}, fn_arr: {"data": []}}

        await h.check()  # streak=1, below threshold
        row_sched = h.row(reg_sched, fn_sched, date)
        row_arr   = h.row(reg_arr, fn_arr, date)
        check("still unresolved after 1st absence", row_sched["current_status"] == "Scheduled")

        # Rewind the in-memory tracker's first_absent_ts so the grace period reads as elapsed —
        # two real checks in a test run happen milliseconds apart, not the configured minutes.
        for key in list(h.cfg.cancel_absence_tracking.keys()):
            h.cfg.cancel_absence_tracking[key]["first_absent_ts"] -= 3600

        await h.check()  # streak=2, meets threshold + grace already elapsed — BOTH rows qualify
        row_sched = h.row(reg_sched, fn_sched, date)
        row_arr   = h.row(reg_arr, fn_arr, date)
        # Cap is 1: only one of the two eligible rows can be resolved THIS check — confirms the
        # cap is actually being enforced, not just present in config.
        still_pending = [r for r in (row_sched, row_arr) if r["current_status"] in ("Scheduled", "Arriving")]
        check("confirmation-call cap=1 defers exactly one row this check", len(still_pending) == 1,
              [r["current_status"] for r in (row_sched, row_arr)])

        # Rewind again so the deferred row's grace/streak still reads as satisfied next check.
        for key in list(h.cfg.cancel_absence_tracking.keys()):
            h.cfg.cancel_absence_tracking[key]["first_absent_ts"] -= 3600
        await h.check()  # the row the cap deferred now gets its confirmation call
        row_sched = h.row(reg_sched, fn_sched, date)
        row_arr   = h.row(reg_arr, fn_arr, date)

        check("Scheduled branch -> Presumed Cancelled",
              row_sched["current_status"] == "Cancelled" and row_sched["arr_label"] == "Presumed Cancelled",
              (row_sched["current_status"], row_sched["arr_label"]))
        check("Arriving branch -> Presumed Diverted",
              row_arr["current_status"] == "Diverted" and row_arr["arr_label"] == "Presumed Diverted",
              (row_arr["current_status"], row_arr["arr_label"]))
    finally:
        h.close()


async def scenario_confirmation_call_upgrades():
    print("\n=== Scenario: confirmation call upgrades Presumed -> Confirmed / real Arrived ===")
    h = Harness()
    try:
        with h.store._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('MONITOR_CANCEL_GRACE_MINS', '1')")
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('MONITOR_ABSENCE_CHECKS', '1')")

        past_ts = int((datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp())
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        reg, fn = "VH-CC1", "QF960"
        h.seed_watchlist(reg)
        h.seed_arrival_row(reg, fn, past_ts, date, current_status="Arriving")

        h.fr_api.pages = {1: {"arrivals": [], "departures": []}}
        real_arr = int(datetime.now().timestamp())
        h.fr_api.flight_by_number = {fn: {"data": [
            make_flight(fn, reg, real_arr=real_arr)["flight"],
        ]}}

        await h.check()  # first absence — just starts tracking, can't resolve same check
        for key in list(h.cfg.cancel_absence_tracking.keys()):
            h.cfg.cancel_absence_tracking[key]["first_absent_ts"] -= 3600
        await h.check()  # grace now reads as elapsed — confirmation call fires

        row = h.row(reg, fn, date)
        check("confirmation call found real arrival -> Arrived",
              row["current_status"] == "Arrived", row["current_status"])

        with h.store._connect() as conn:
            sighting = conn.execute(
                "SELECT * FROM rego_sightings WHERE registration=?", (reg,)
            ).fetchone()
        check("sighting backfilled", sighting is not None)
    finally:
        h.close()


async def scenario_regression_unmodified_paths():
    print("\n=== Scenario: regression — ordinary flight unaffected ===")
    h = Harness()
    try:
        reg, fn, date = "VH-REG1", "QF970", TODAY
        arr_ts = int(today_at(8, 0).timestamp())
        h.seed_watchlist(reg)

        # Fresh detection via normal Step 3/4 pipeline (not seeded directly)
        h.fr_api.pages = {1: {"arrivals": [make_flight(fn, reg, sched_arr=arr_ts, est_arr=arr_ts)],
                              "departures": []}}
        await h.check()
        row = h.row(reg, fn, date)
        check("normal flight got a row via Step 3/4", row is not None)

        # Confirmed real landing via hist_arrivals-equivalent (Step 6) + departure pairing (Pass 2)
        real_arr = arr_ts + 100
        dep_ts   = arr_ts + 3600
        h.fr_api.pages = {1: {"arrivals": [make_flight(fn, reg, sched_arr=arr_ts, real_arr=real_arr)],
                              "departures": [make_flight("NZ600", reg, sched_dep=dep_ts, est_dep=dep_ts)]}}
        await h.check()
        row = h.row(reg, fn, date)
        check("Step 5 refreshed status to Arrived", row["current_status"] == "Arrived", row["current_status"])
        dep = h.departure_row(row["id"])
        check("Pass 2 paired a departure normally", dep is not None and dep["dep_flight"] == "NZ600",
              dep["dep_flight"] if dep else None)
    finally:
        h.close()


async def main():
    scenarios = [
        scenario_explicit_cancellation,
        scenario_explicit_diversion,
        scenario_departure_released_to_sibling,
        scenario_cancelled_departure_repairs,
        scenario_diverted_departure_noop,
        scenario_aircraft_swap,
        scenario_swap_revert,
        scenario_silent_disappearance,
        scenario_confirmation_call_upgrades,
        scenario_regression_unmodified_paths,
    ]
    for s in scenarios:
        try:
            await s()
        except Exception:
            print(f"  [ERROR] {s.__name__} raised an exception:")
            traceback.print_exc()
            FAILURES.append(f"{s.__name__}: raised exception")

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("ALL SCENARIOS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
