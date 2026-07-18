"""
FastAPI web server for SpotAlert PWA.
Run standalone: python -m uvicorn web:app --host 0.0.0.0 --port 8080
Or create via create_app(cfg) for integration with the monitor loop.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import threading
import time
from pathlib import Path
from typing import Any, List as _List, Optional
import os as _os

VERSION = "2.01"
_PROCESS_START_TS = int(time.time())


def _system_info() -> dict:
    """Return basic host system info using stdlib only."""
    info: dict[str, str] = {}
    info["hostname"] = platform.node() or "unknown"
    info["os"] = f"{platform.system()} {platform.release()}".strip()
    info["arch"] = platform.machine() or "unknown"

    # Connection type: check /sys/class/net on Linux for wireless interfaces
    conn = "Unknown"
    try:
        net_root = Path("/sys/class/net")
        if net_root.exists():
            ifaces = [p for p in net_root.iterdir() if p.name != "lo"]
            wifi = [p for p in ifaces if (p / "wireless").exists()]
            eth  = [p for p in ifaces if not (p / "wireless").exists()]
            if wifi and not eth:
                conn = "Wi-Fi"
            elif eth and not wifi:
                conn = "Ethernet"
            elif wifi and eth:
                conn = "Ethernet + Wi-Fi"
        else:
            conn = "Ethernet"  # Windows/Mac dev — assume wired
    except Exception:
        pass
    info["connection"] = conn
    return info

from fastapi import FastAPI, HTTPException, Query as _Query, Request, Response, BackgroundTasks, Depends, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from auth import current_user as _auth_current_user, require_role as _auth_require_role

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Settings keys a Pilot may edit for themselves (spotrec/livery/rare/routetype
# groups in static/app.js's SETTINGS_SCHEMA, plus the Collection Stat Keywords
# and Session Panel Tags cards — kept in sync with the UI by hand since this
# is the server-side enforcement backstop, not just a UI affordance).
# Everything else (monitoring internals, military, notification, airport
# identity, Custom Airports/Aircraft Types) is Controller-only.
# SPECIAL_LIVERY_KEYWORDS is intentionally excluded even though it's in the
# 'livery' group — it stays Controller-only (hidden from the Pilot's Settings
# UI entirely, see PILOT_HIDDEN_SETTINGS in app.js), but is still kept
# identical across every airport via PER_USER_GLOBAL_SETTINGS below (only the
# 'controller' owner id is ever used for it, since Pilots can't set it). Every
# key in this frozenset is seeded ONCE, at the moment a Pilot is set up as a
# new user or granted a new airport, but the seed source differs by key:
# COLLECTION_KW_STAT_1/2/3 and collection_session_tags (also in
# PER_USER_GLOBAL_SETTINGS) always seed from the Controller's value (see
# store.copy_controller_settings_to_owner); the SPOT_*/RARE_PLANE_MIN_ABSENCE_DAYS
# keys (in PER_AIRPORT_PREFILL_SETTINGS) seed from that SAME Pilot's own value
# on another airport they already have, falling back to the Controller's
# value only for their first-ever airport (see
# seed_new_airport_prefill_settings); SPECIAL_LIVERY_EXCLUDE_KEYWORDS is
# never seeded from anywhere — always starts blank on a new airport. All of
# this happens in controller_create_user/controller_update_user and the
# startup backfill in main.py. After the one-time seed, each key's value is
# fully independent per airport — no live fallback to the Controller's row
# (_pilot_setting), and clearing a value stays cleared.
# are additionally in PER_USER_GLOBAL_SETTINGS below.
PILOT_EDITABLE_SETTINGS = frozenset({
    "SPECIAL_LIVERY_EXCLUDE_KEYWORDS",
    "RARE_PLANE_MIN_ABSENCE_DAYS",
    "SPOT_MAX_GAP_HOURS", "SPOT_LULL_MINS", "SPOT_MAX_LULLS", "SPOT_LIGHTING_GATE",
    "SPOT_MAX_SPOTTED", "SPOT_LIGHT_BUFFER_MINS", "SPOT_BAD_LIGHT_START", "SPOT_BAD_LIGHT_END",
    "COLLECTION_KW_STAT_1", "COLLECTION_KW_STAT_2", "COLLECTION_KW_STAT_3", "collection_session_tags",
})

# GLOBAL_INFRA_SETTINGS, PER_USER_GLOBAL_SETTINGS, PER_AIRPORT_PREFILL_SETTINGS,
# and seed_new_airport_prefill_settings moved to bootstrap.py so monitor_service.py
# (the separate monitor process) can use them without importing this whole FastAPI
# module — re-imported here under the same names so every existing reference in
# this file keeps working unchanged.
from bootstrap import (
    GLOBAL_INFRA_SETTINGS, PER_USER_GLOBAL_SETTINGS, PER_AIRPORT_PREFILL_SETTINGS,
    seed_new_airport_prefill_settings,
)

# Cache for /api/live-status — one shared airport page fetch, reused for 90s
_live_status_cache: dict = {"ts": 0, "schedule": None}


def _tz_abbr(tz_name: str) -> str:
    """Real timezone abbreviation (AEST, JST, CEST, ...) for the airport-picker
    card. Deliberately resolved server-side via stdlib zoneinfo rather than the
    browser's Intl API — Intl's `timeZoneName: 'short'` falls back to a plain
    GMT+offset for most non-US zones (CLDR avoids many letter abbreviations as
    globally ambiguous), so it can't reliably produce e.g. "AEST"."""
    if not tz_name:
        return ""
    try:
        from zoneinfo import ZoneInfo
        import datetime as _dt3
        return _dt3.datetime.now(ZoneInfo(tz_name)).strftime("%Z")
    except Exception:
        return ""


def _owner_id(user) -> str:
    """Which owner's private per-user data governs this viewer's reads/writes —
    exclusion lists, fleet cards, and anything else scoped independently per
    Pilot. Controller and Passenger always share the Controller's own
    ('controller' sentinel) row/list (Passenger inherits it, never has its own)."""
    return user.user_id if user.role == "pilot" else "controller"


def _push_owner_id(user) -> str:
    """Which owner's push subscription / per-type toggle / spotting-reminder
    settings / last-selected-airport rows this user's own device state lives
    under. Deliberately NOT the same as _owner_id: _owner_id governs FILTER
    ownership and collapses Controller+Passenger onto the shared 'controller'
    sentinel (a Passenger has no filters of their own, so it inherits the
    Controller's). Push settings are a different kind of data — every real
    user has their own device, their own toggles, their own selected
    airport — so collapsing Passenger onto 'controller' here would mean a
    Passenger's phone silently shares (and can silently overwrite) the
    Controller's own push subscription and preferences. Only the Controller
    itself keeps the literal 'controller' key (preserves existing
    subscriptions/prefs from before this was multi-user); every Pilot and
    every Passenger gets their own real user_id."""
    return "controller" if user.role == "controller" else user.user_id


# For the 4 filter/watchlist tables, a Pilot always reads (and writes) their
# own rows via _owner_id(user) — no live inheritance at read time. A Pilot's
# list is seeded from the Controller's current rows ONCE, at the moment
# they're set up as a new user or granted a new airport (see
# store.copy_controller_filters_to_owner, called from
# controller_create_user/controller_update_user and the startup backfill in
# main.py); after that one-time copy, their list is fully independent
# forever — further Controller edits never propagate, and an empty Pilot
# list stays empty (never falls back to the Controller's again).


def _repatch_spotted_gate(clusters: list, catalog, max_spotted: int, airport_iata: str) -> None:
    """The background clustering pass (monitor.py) bakes 'qualifying' into
    clusters_json using the CONTROLLER's own catalog — the only "ground
    truth" a shared per-airport cache can use. A Pilot (or Controller)
    viewing with their own catalog needs the already-photographed portion
    of that flag re-evaluated against their own spotted-counts instead.
    Mutates clusters in place; does not re-derive cluster/window boundaries
    (recommended_start_local_min/end_local_min), which still reflect
    whatever was qualifying at cache-build time — a known, accepted gap.
    """
    spotted_cache: dict = {}
    def _get_spotted(reg: str, livery: str) -> int:
        key = (reg, (livery or "").strip().lower())
        if key not in spotted_cache:
            try:
                if catalog is None:
                    spotted_cache[key] = 0
                elif key[1]:
                    spotted_cache[key] = catalog.get_livery_session_count_at_airport(reg, airport_iata, livery) or 0
                else:
                    spotted_cache[key] = catalog.get_session_count_at_airport(reg, airport_iata) or 0
            except Exception:
                spotted_cache[key] = 0
        return spotted_cache[key]

    for c in clusters:
        for f in c.get("flights", []):
            # Was this flight light-qualified at cache-build time? The only
            # way to be non-qualifying with an EMPTY reason is the lighting
            # gate — over_spotted always sets reason to "spotted_N".
            light_ok = f.get("qualifying") or bool(f.get("reason"))
            spotted = _get_spotted(f.get("registration", ""), f.get("extra_info", ""))
            over_spotted = max_spotted > 0 and spotted > max_spotted
            f["qualifying"] = light_ok and not over_spotted
            f["reason"] = f"spotted_{spotted}" if over_spotted else ""


def _pilot_setting(conn, user_id: str, key: str, default: str = "") -> str:
    """This user_id's own row for a PILOT_EDITABLE_SETTINGS key. No live
    fallback to the Controller's row: a Pilot's own row is seeded from the
    Controller's current value ONCE, at setup/new-airport-grant time (see
    store.copy_controller_settings_to_owner), and is fully independent from
    then on — an empty value stays empty rather than reverting to whatever
    the Controller currently has. `default` is only used if no row exists at
    all (e.g. a key added to PILOT_EDITABLE_SETTINGS before the seed ran)."""
    row = conn.execute(
        "SELECT value FROM settings WHERE user_id = ? AND key = ?", (user_id, key)
    ).fetchone()
    return row["value"] if row is not None and row["value"] is not None else default


def _viewer_livery_exclude_keywords(conn, user) -> list:
    """This viewer's own Special Livery exclude-keyword list, lowercased.
    A Pilot's value was seeded from the Controller's current one at setup
    time (copy_controller_settings_to_owner) and is fully independent from
    then on — no live fallback. Controller/Passenger both resolve to the
    Controller's own row (via _owner_id)."""
    owner = _owner_id(user)
    raw = _pilot_setting(conn, owner, "SPECIAL_LIVERY_EXCLUDE_KEYWORDS", "")
    return [kw.strip().lower() for kw in (raw or "").split(",") if kw.strip()]


def _strip_excluded_livery_tag(notif_types: list, extra_info: str, exclude_kws: list) -> list:
    """The 'Special Livery' tag is baked into flight_arrivals once at ingestion
    time using only the Controller's own Keywords list (ingestion is a single
    shared pass — see monitor.py's _is_special_livery_airline calls, which no
    longer apply exclude-keywords at all). Each viewer's OWN Exclude Keywords
    are instead re-applied here, per-viewer, at display time — stripping the
    tag (not the whole flight; Feed/Search show every stored flight regardless
    of tags) whenever the flight's extra_info matches one of their keywords."""
    if not exclude_kws or "Special Livery" not in notif_types:
        return notif_types
    if any(kw in (extra_info or "").lower() for kw in exclude_kws):
        return [t for t in notif_types if t != "Special Livery"]
    return notif_types


def _resolve_rare_plane_tag(notif_types: list, rare_absence_days, viewer_min_days: int) -> list:
    """Same shared-ingestion-vs-per-viewer-threshold problem as Special Livery:
    monitor.py stores the flight whenever it clears the MOST PERMISSIVE
    RARE_PLANE_MIN_ABSENCE_DAYS across all owners, snapshotting the objective
    days-absent value at that moment (rare_plane_cooldowns.last_seen_ts keeps
    moving forward, so it can't be recomputed later). Each viewer's own
    threshold is re-applied here to decide whether THEY should still see the
    'Rare Plane/Airline' tag."""
    if "Rare Plane/Airline" not in notif_types:
        return notif_types
    is_rare = rare_absence_days is None or rare_absence_days > viewer_min_days
    if is_rare:
        return notif_types
    return [t for t in notif_types if t != "Rare Plane/Airline"]


def _viewer_rare_plane_min_days(conn, user) -> int:
    try:
        return int(_pilot_setting(conn, _owner_id(user), "RARE_PLANE_MIN_ABSENCE_DAYS", "7") or 7)
    except (TypeError, ValueError):
        return 7


def _viewer_watchlist_sets(store, user) -> dict:
    """This viewer's own watchlist entries — Controller/Passenger resolve to the
    Controller's own rows via _owner_id; a Pilot's watchlist is private, never
    inherited from or merged with the Controller's or another Pilot's."""
    return store.get_watchlist_sets(_owner_id(user))


def _strip_unowned_watchlist_tags(notif_types: list, registration: str, aircraft_type: str,
                                   airline_icao: str, viewer_sets: dict) -> list:
    """Same shared-ingestion-vs-per-viewer-ownership problem as Special Livery/Rare
    Plane, but worse: monitor.py's check_rego_watchlist/check_type_watchlist/
    check_airline_watchlist match against ANY owner's filter_regos/filter_types/
    filter_airlines rows, not just the Controller's (see those functions' "Exclusion
    is applied per-viewer... not here" comments — that reasoning covers exclusions,
    which are meant to suppress for everyone, but was never extended to watchlist
    INCLUSION, which should be private per owner). A Pilot's own private watchlist
    entry therefore creates a flight_arrivals row with that tag baked in for every
    viewer, including the Controller and other Pilots who never added it. Strip the
    tag here, per-viewer, unless it's present in the viewer's own watchlist.

    Known gap: "Watchlist Aircraft Type"/"Watchlist Airline" re-check against
    flight_arrivals.airline_icao (the marketing airline's ICAO code), but
    check_type_watchlist/check_airline_watchlist's operator-entry branch actually
    matched on the aircraft's OWNER/operator ICAO code, which isn't stored on the
    row separately — a wet-leased flight matched via operator (not airline) could
    still show the tag to a viewer whose watchlist only has the operator entry
    under a different code. Accepted gap, same as Rare Plane's cache-staleness
    note above."""
    out = notif_types
    if "Watchlist Registration" in out and registration not in viewer_sets["regos"]:
        out = [t for t in out if t != "Watchlist Registration"]
    if "Watchlist Aircraft Type" in out and (airline_icao, aircraft_type) not in viewer_sets["types"]:
        out = [t for t in out if t != "Watchlist Aircraft Type"]
    if "Watchlist Airline" in out and airline_icao not in viewer_sets["airline_icaos"]:
        out = [t for t in out if t != "Watchlist Airline"]
    return out


def _recluster_for_pilot(raw_events: list, sunrise_ts: int, sunset_ts: int, tz,
                          conn, user_id: str, catalog, airport_iata: str) -> list:
    """Full per-viewer re-cluster for a Pilot, using their OWN algorithm
    settings, exclusion list, and catalog — never the Controller's. Unlike
    _repatch_spotted_gate (a cheap flag patch on an already-built cache),
    exclusion removes events before clustering even runs, so a Pilot's empty
    exclusion list can only be honored by re-running cluster_day_for_cache
    from the raw (pre-exclusion) events cached alongside clusters_json."""
    def _pset_int(key, default):
        try: return int(_pilot_setting(conn, user_id, key, str(default)))
        except Exception: return default

    max_gap_secs   = _pset_int("SPOT_MAX_GAP_HOURS", 3) * 3600
    lull_secs      = _pset_int("SPOT_LULL_MINS", 60) * 60
    max_spotted    = _pset_int("SPOT_MAX_SPOTTED", 0)
    light_buf_secs = _pset_int("SPOT_LIGHT_BUFFER_MINS", 30) * 60
    max_lulls      = _pset_int("SPOT_MAX_LULLS", 2)
    lighting_gate  = _pilot_setting(conn, user_id, "SPOT_LIGHTING_GATE", "true").lower() == "true"
    bad_light_start = _pilot_setting(conn, user_id, "SPOT_BAD_LIGHT_START", "")
    bad_light_end   = _pilot_setting(conn, user_id, "SPOT_BAD_LIGHT_END", "")

    excluded_regs = {r["registration"] for r in conn.execute(
        "SELECT registration FROM filter_exclusions WHERE owner_user_id = ?", (user_id,)
    ).fetchall()}
    _exclude_kws_raw = _pilot_setting(conn, user_id, "SPECIAL_LIVERY_EXCLUDE_KEYWORDS", "")
    exclude_kws = [kw.strip().lower() for kw in _exclude_kws_raw.split(",") if kw.strip()]
    # Same query shape as SqliteStore.get_watchlist_sets — inlined since this function
    # only has a raw connection in hand, not the store object itself.
    watchlist_sets = {
        "regos": {r[0] for r in conn.execute(
            "SELECT registration FROM filter_regos WHERE owner_user_id = ?", (user_id,)).fetchall()},
        "types": {(r[0], r[1]) for r in conn.execute(
            "SELECT airline, aircraft_type FROM filter_types WHERE owner_user_id = ?", (user_id,)).fetchall()},
        "airline_icaos": {r[0] for r in conn.execute(
            "SELECT icao_code FROM filter_airlines WHERE owner_user_id = ?", (user_id,)).fetchall()},
    }

    spotted_cache: dict = {}
    def _get_spotted(reg: str, livery: str) -> int:
        key = (reg, (livery or "").strip().lower())
        if key not in spotted_cache:
            try:
                if catalog is None:
                    spotted_cache[key] = 0
                elif key[1]:
                    spotted_cache[key] = catalog.get_livery_session_count_at_airport(reg, airport_iata, livery) or 0
                else:
                    spotted_cache[key] = catalog.get_session_count_at_airport(reg, airport_iata) or 0
            except Exception:
                spotted_cache[key] = 0
        return spotted_cache[key]

    events = [{**ev, "_spotted": _get_spotted(ev.get("registration", ""), ev.get("extra_info", ""))}
              for ev in raw_events]

    return cluster_day_for_cache(
        events, sunrise_ts, sunset_ts, tz,
        max_gap_secs=max_gap_secs, notable_lull_secs=lull_secs,
        max_spotted=max_spotted, dep_threshold=0,
        light_buf_secs=light_buf_secs, lighting_gate=lighting_gate,
        bad_light_start=bad_light_start, bad_light_end=bad_light_end,
        max_lulls=max_lulls, excluded_regs=excluded_regs, exclude_kws=exclude_kws,
        watchlist_sets=watchlist_sets,
    )

# ---------------------------------------------------------------------------
# Config/store loader for standalone mode
# ---------------------------------------------------------------------------

def _load_standalone():
    """Load config and store the same way main.py does, for standalone uvicorn runs."""
    from store import SqliteStore

    data_dir = os.environ.get("SPOTALERT_DATA", "data/")
    os.makedirs(data_dir, exist_ok=True)
    store = SqliteStore(os.path.join(data_dir, "spotalert.db"))

    try:
        from lightroom import find_catalog
        catalog = find_catalog()
    except Exception:
        catalog = None

    settings = {
        "AIRPORT_CODE":                    store.load_setting("AIRPORT_CODE") or "",
        "CHECK_INTERVAL_MINUTES":          store.load_setting("CHECK_INTERVAL_MINUTES") or "30",
        "MILITARY_CHECK_INTERVAL_MINUTES": store.load_setting("MILITARY_CHECK_INTERVAL_MINUTES") or "15",
    }
    return store, settings, catalog


# ---------------------------------------------------------------------------
# Module-level clustering function — called at pull time from monitor.py
# ---------------------------------------------------------------------------

def cluster_day_for_cache(
    events: list,             # flat list of independent events — each has ts + side + display fields
    sunrise_ts: int,
    sunset_ts: int,
    tz,
    max_gap_secs: int,
    notable_lull_secs: int,
    max_spotted: int,
    dep_threshold: int,
    light_buf_secs: int,
    lighting_gate: bool,
    bad_light_start: str,
    bad_light_end: str,
    max_lulls: int,
    excluded_regs: set,
    exclude_kws: Optional[list] = None,
    watchlist_sets: Optional[dict] = None,
) -> list:
    """Return cluster list for one day.

    Each event is an independent timestamped unit — either an arrival or a departure.
    Qualification and light zone use the event's own ts against this day's sunrise/sunset.
    No cross-day checks needed: the caller buckets events by their own date.

    exclude_kws (this viewer's own Special Livery exclude-keywords, lowercased)
    is applied the same way Feed/Search apply it: an event loses its 'Special
    Livery' tag if extra_info matches a keyword, and is dropped from Spotting
    entirely if that was its only qualifying tag — so a flight hidden from
    Feed by an exclude keyword is consistently hidden from Spotting too,
    instead of only registration-based exclusions (excluded_regs) applying.

    watchlist_sets (this viewer's own filter_regos/filter_types/filter_airlines
    entries, via _viewer_watchlist_sets/get_watchlist_sets) strips the shared
    "Watchlist Registration"/"Watchlist Aircraft Type"/"Watchlist Airline" tags
    the same way — see _strip_unowned_watchlist_tags for why ingestion can't
    already scope these per-owner.
    """
    import datetime as _dt

    if not events:
        return []

    def _local_min(ts):
        dt = _dt.datetime.fromtimestamp(ts, tz)
        return dt.hour * 60 + dt.minute

    def _light_zone(ts):
        if not ts or not sunrise_ts or not sunset_ts: return None
        if ts <= sunrise_ts or ts >= sunset_ts: return None
        if ts < sunrise_ts + light_buf_secs or ts > sunset_ts - light_buf_secs: return "low_light"
        if bad_light_start and bad_light_end:
            try:
                t = _dt.datetime.fromtimestamp(ts, tz).strftime("%H:%M")
                if bad_light_start <= t <= bad_light_end: return "bad_light"
            except Exception:
                pass
        return None

    evaluated = []
    for ev in sorted(events, key=lambda x: x["ts"]):
        if ev["registration"] in excluded_regs:
            continue
        _nt = ev.get("notif_types") or []
        if exclude_kws and _nt and not _strip_excluded_livery_tag(_nt, ev.get("extra_info", ""), exclude_kws):
            continue
        if watchlist_sets and _nt and not _strip_unowned_watchlist_tags(
                _nt, ev.get("registration", ""), ev.get("aircraft_type"), ev.get("airline_icao", ""), watchlist_sets):
            continue
        over_spotted = max_spotted > 0 and ev.get("_spotted", 0) > max_spotted
        reason = f"spotted_{ev.get('_spotted', 0)}" if over_spotted else ""

        ts    = ev["ts"]
        light = _light_zone(ts)
        in_daylight = lighting_gate and sunrise_ts and sunset_ts
        qualifying  = not over_spotted and (not in_daylight or sunrise_ts <= ts <= sunset_ts)

        evaluated.append({**ev,
            "qualifying":  qualifying,
            "reason":      reason,
            "light":       light,
            "local_min":   _local_min(ts),
        })

    all_q_ts = [(f["ts"], f) for f in evaluated if f["qualifying"]]
    all_q_ts.sort(key=lambda x: x[0])

    if not all_q_ts:
        out = [{k: v for k, v in f.items() if not k.startswith("_")} for f in evaluated]
        if not out: return []
        all_ts = [f["ts"] for f in evaluated]
        return [{"start_ts": min(all_ts), "end_ts": max(all_ts),
                 "start_local_min": _local_min(min(all_ts)),
                 "end_local_min":   _local_min(max(all_ts)),
                 "recommended_start_ts":        min(all_ts),
                 "recommended_start_local_min": _local_min(min(all_ts)),
                 "alternative_windows": [], "show_window": False,
                 "flights": out, "lulls": []}]

    sections = [[all_q_ts[0]]]
    for ev in all_q_ts[1:]:
        if ev[0] - sections[-1][-1][0] > max_gap_secs: sections.append([ev])
        else: sections[-1].append(ev)
    section = max(sections, key=len)
    q_ev = section

    def _window_from_events(evts):
        if not evts: return evts[0][0], evts[-1][0]
        regs = {e[1]["registration"] for e in evts}
        end_ts = max(e[0] for e in evts)
        best_start = evts[0][0]
        for cs, _ in evts:
            if all(any(e[0] >= cs for e in evts if e[1]["registration"] == r) for r in regs):
                best_start = cs
        return best_start, end_ts

    def _regs_catchable(evts):
        return len({e[1]["registration"] for e in evts})

    total_q = _regs_catchable(q_ev)
    primary_evts = list(q_ev)
    while len(primary_evts) > 1:
        t = primary_evts[1:]
        if _regs_catchable(t) < total_q: break
        primary_evts = t
    while len(primary_evts) > 1:
        t = primary_evts[:-1]
        if _regs_catchable(t) < total_q: break
        primary_evts = t
    rec_start, c_end_ts = _window_from_events(primary_evts)
    alt_wins = []

    def _make_alt(evts):
        if not evts or _regs_catchable(evts) < total_q: return False
        a_s, a_e = _window_from_events(evts)
        if a_s == rec_start and a_e == c_end_ts: return True
        entry = {"start_ts": a_s, "end_ts": a_e,
                 "start_local_min": _local_min(a_s), "end_local_min": _local_min(a_e)}
        if entry not in alt_wins: alt_wins.append(entry)
        return True

    for _seq_dir in ["front_back", "back_front"]:
        evts = list(q_ev)
        first, second = (1, -1) if _seq_dir == "front_back" else (-1, 1)
        while len(evts) > 1:
            t = evts[first:] if first == 1 else evts[:first]
            if not _make_alt(t): break
            evts = t
        evts = list(q_ev)
        while len(evts) > 1:
            t = evts[second:] if second == 1 else evts[:second]
            if not _make_alt(t): break
            evts = t

    lo, hi, from_front = 0, len(q_ev) - 1, True
    while lo < hi:
        new_lo = lo + 1 if from_front else lo
        new_hi = hi - 1 if not from_front else hi
        t = q_ev[new_lo:new_hi + 1]
        if not _make_alt(t): break
        lo, hi, from_front = new_lo, new_hi, not from_front

    lull_pts = sorted({f["ts"] for f in evaluated if rec_start <= f["ts"] <= c_end_ts})
    lulls_all = []
    for j in range(len(lull_pts) - 1):
        gap = lull_pts[j+1] - lull_pts[j]
        if gap >= notable_lull_secs:
            lulls_all.append({"start_ts": lull_pts[j], "end_ts": lull_pts[j+1],
                               "start_local_min": _local_min(lull_pts[j]),
                               "end_local_min":   _local_min(lull_pts[j+1]), "_dur": gap})
    lulls = sorted(sorted(lulls_all, key=lambda l: -l["_dur"])[:max_lulls],
                   key=lambda l: l["start_ts"])
    for l in lulls: l.pop("_dur", None)

    out_events = sorted([{k: v for k, v in f.items() if not k.startswith("_")} for f in evaluated],
                        key=lambda f: f["ts"])
    win_count = sum(1 for f in evaluated if f["qualifying"] and rec_start <= f["ts"] <= c_end_ts)

    # Only 2 alternative-window search directions exist (front-first, back-first,
    # plus the alternating front/back walk) but they can each surface distinct
    # candidates — cap to the 2 shortest (closest to a "quick trip" alternative
    # to the main window) rather than showing every unique candidate found.
    alt_wins_capped = sorted(alt_wins, key=lambda w: w["end_ts"] - w["start_ts"])[:2]

    return [{"start_ts": min(e[0] for e in section), "end_ts": c_end_ts,
             "start_local_min": _local_min(min(e[0] for e in section)),
             "end_local_min":   _local_min(c_end_ts),
             "recommended_start_ts":        rec_start,
             "recommended_start_local_min": _local_min(rec_start),
             "alternative_windows": alt_wins_capped, "show_window": win_count >= 2,
             "flights": out_events, "lulls": lulls}]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(cfgs=None, control_store=None, fr_api=None, data_dir=None) -> FastAPI:
    """
    cfgs: dict {airport_iata: AppConfig} — one per watched airport, when running
    integrated with the monitor loop. A bare AppConfig is also accepted (wrapped
    into a single-entry dict) for backward compatibility. If None, loads a single
    config/store from disk (standalone mode, no monitor loop).
    """
    app = FastAPI(title="SpotAlert", docs_url=None, redoc_url=None)

    if cfgs is not None and not isinstance(cfgs, dict):
        cfgs = {cfgs.airport_iata: cfgs}  # single AppConfig passed directly

    # State shared across request handlers. app.state.cfg/app.state.store remain a
    # single "primary" airport's config — used as-is by routes that are either
    # explicitly airport-independent (Collection/Fleet/Search-Catalogue, per the
    # multi-airport design) or not yet rewired to resolve per-request. Routes that
    # ARE airport-aware use the _cfg_for_user()/_store_for_user() helpers below.
    _primary_cfg = next(iter(cfgs.values())) if cfgs else None
    app.state.cfg = _primary_cfg
    app.state.store = _primary_cfg.store if _primary_cfg else None
    app.state.cfgs = cfgs or {}

    _data_dir = data_dir or os.environ.get("SPOTALERT_DATA", "data/")
    app.state.data_dir = _data_dir
    app.state.fr_api = fr_api

    if control_store is not None:
        app.state.control_store = control_store
    else:
        from control_store import ControlStore
        app.state.control_store = ControlStore(os.path.join(_data_dir, "control.db"))

    def _cfg_for_user(user) -> "AppConfig":
        """Resolves the AppConfig for the airport the user currently has selected.
        Raises 400 if they haven't picked one yet (shouldn't happen — the frontend
        gates on this via /api/me before making any other call)."""
        cfg = app.state.cfgs.get(user.airport_iata)
        if cfg is None:
            raise HTTPException(400, "No airport selected")
        return cfg

    app.state.cfg_for_user = _cfg_for_user

    def _get_user_catalog(user):
        """Per-request catalog resolver — catalogs are private per user, never
        airport-scoped. Passengers have no catalog concept at all, regardless
        of any stale catalog_path (they can't upload one, but a role change
        could otherwise leave one lying around)."""
        if user.role == "passenger":
            return None
        row = app.state.control_store.get_user(user.user_id)
        path = row["catalog_path"] if row else None
        if not path:
            return None
        try:
            from lightroom import LightroomCatalog
            return LightroomCatalog(path)
        except Exception:
            return None

    app.state.get_user_catalog = _get_user_catalog

    # Gate every /api/* route behind a valid session, except the handful needed to
    # log in / check auth status in the first place. No role restrictions yet — any
    # authenticated user passes; role-based access is layered in separately.
    _AUTH_EXEMPT_PATHS = {"/api/auth/login", "/api/me", "/api/version"}

    @app.middleware("http")
    async def _require_session(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.url.path not in _AUTH_EXEMPT_PATHS:
            from auth import get_current_user_optional
            if get_current_user_optional(request) is None:
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return await call_next(request)

    def _fleet_bg_refresh_loop() -> None:
        """Refresh every owner's own fleet cards every 7 days regardless of user
        activity — fleet cards are per-user (Controller/Pilot), so this iterates
        every owner that currently has any, not one shared list."""
        import time as _time, system_status as _ss
        _FLEET_INTERVAL = 7 * 86400
        while True:
            try:
                for owner in app.state.control_store.get_fleet_card_owners():
                    cards = app.state.control_store.get_fleet_cards(owner)
                    stale = [c for c in cards if c.get('updated_at', 0) < _time.time() - _FLEET_INTERVAL]
                    for card in stale:
                        _fleet_refresh_fr24_bg(owner, card['icao'])
                _ss.record_task('fleet_update', True)
            except Exception as _e:
                _ss.record_task('fleet_update', False, str(_e))
            _time.sleep(_FLEET_INTERVAL)

    @app.on_event("startup")
    async def _startup():
        if app.state.store is None:
            store, settings, catalog = _load_standalone()
            app.state.store = store
            app.state.settings = settings
            app.state.catalog = catalog
        else:
            app.state.settings = {}
            app.state.catalog = _primary_cfg.catalog if _primary_cfg else None

        # First-boot bootstrap: create one Controller account (random logged password)
        # and register every passed-in cfg's airport in-place, so an upgraded
        # deployment lands behind a login with zero data migration. main.py already
        # does this same registration before building cfgs (it needs the airport list
        # up front) — register_airport() is INSERT OR IGNORE, so repeating it here is
        # a harmless no-op in the integrated case, and is what actually does the work
        # in standalone mode (no main.py driving things).
        cstore = app.state.control_store
        if cstore.count_users() == 0:
            import secrets as _secrets
            from auth import hash_password as _hash_password
            _bootstrap_password = _secrets.token_urlsafe(12)
            _admin_uid = cstore.create_user("admin", _hash_password(_bootstrap_password), "controller")
            log.warning(
                "No web accounts found — created initial Controller login. "
                "Username: admin  Password: %s  (change this after logging in!)",
                _bootstrap_password,
            )
        for _acfg in app.state.cfgs.values():
            # _acfg.airport_iata can be blank if this particular boot's FR24
            # lookup for this airport failed with no cache available yet
            # (build_config()'s minimal-fallback path) — never register that,
            # it would create an unrecoverable blank-code "phantom" row
            # pointing at an already-watched airport's own DB file.
            if _acfg.airport_iata and not cstore.get_watched_airport(_acfg.airport_iata):
                cstore.register_airport(
                    airport_iata=_acfg.airport_iata, airport_code=_acfg.airport_code,
                    airport_name=_acfg.airport_name, airport_icao=_acfg.airport_icao,
                    airport_tz=_acfg.airport_tz, airport_lat=_acfg.airport_lat,
                    airport_lon=_acfg.airport_lon, db_path=_acfg.store.db_path,
                )

        # One-time migration: catalogs used to be a single shared file found via
        # find_catalog() (the "lightroom/" folder). Point the first Controller
        # account at that same file so upgrading doesn't silently lose Collection/
        # Fleet/Search-Catalogue/already-photographed data — no file is moved or
        # copied, just referenced. No-op once any Controller already has a
        # catalog_path (either from this migration or a real upload).
        if not any(u["catalog_path"] for u in cstore.list_users() if u["role"] == "controller"):
            try:
                from lightroom import find_catalog as _find_legacy_catalog
                _legacy = _find_legacy_catalog()
                if _legacy is not None:
                    _first_controller = next((u for u in cstore.list_users() if u["role"] == "controller"), None)
                    if _first_controller:
                        cstore.set_catalog_path(_first_controller["user_id"], _legacy._path)
                        log.info("Migrated legacy shared catalog (%s) to Controller '%s'",
                                 _legacy._path, _first_controller["username"])
            except Exception as _cat_exc:
                log.warning("Legacy catalog migration skipped: %s", _cat_exc)

        # One-time migration: fleet cards used to live in a single shared table in
        # the (primary airport's) SqliteStore, unscoped by user. Copy them into
        # control.db under the 'controller' sentinel — the SAME owner key every
        # Controller/Passenger read/write already resolves to (see _owner_id) —
        # so upgrading doesn't silently lose the Fleet subtab's data. No-op once
        # control.db already has any fleet cards (from this migration or real
        # per-user usage).
        if not cstore.get_fleet_card_owners():
            try:
                _legacy_cards = app.state.store.get_fleet_cards() if app.state.store else []
                if _legacy_cards:
                    for _lc in _legacy_cards:
                        cstore.upsert_fleet_card(
                            "controller", _lc["icao"], _lc["iata"], _lc["airline"],
                            _lc["aircraft"], updated_at=_lc.get("updated_at"),
                        )
                    log.info("Migrated %d legacy fleet card(s) to the Controller", len(_legacy_cards))
            except Exception as _fc_exc:
                log.warning("Legacy fleet card migration skipped: %s", _fc_exc)

        # Refresh ICAO type list in background (no-op if < 90 days old)
        import threading as _thr
        _thr.Thread(target=app.state.store.refresh_icao_type_list, daemon=True).start()
        # Pre-warm collection stats cache and start periodic refresh thread
        _thr.Thread(target=_col_compute_stats, daemon=True).start()
        _col_start_bg_refresh()
        # Fleet cards: refresh any stale cards on startup, then every 7 days
        _thr.Thread(target=_fleet_bg_refresh_loop, daemon=True).start()

    # ── Auth / airport-selection routes ─────────────────────────────────────

    @app.get("/api/version")
    async def get_version():
        """Unauthenticated — the login screen shows this before any session exists."""
        return {"version": VERSION}

    @app.post("/api/auth/login")
    async def auth_login(request: Request, response: Response):
        from auth import verify_password, set_session_cookie
        body = await request.json()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        cstore = app.state.control_store
        user_row = cstore.get_user_by_username(username)
        if not user_row or not verify_password(password, user_row["password_hash"]):
            raise HTTPException(401, "Invalid username or password")
        set_session_cookie(response, request, user_row["user_id"], user_row["role"],
                            user_row["session_epoch"])
        return {"ok": True, "role": user_row["role"]}

    @app.post("/api/auth/logout")
    async def auth_logout(response: Response):
        from auth import clear_auth_cookies
        clear_auth_cookies(response)
        return {"ok": True}

    @app.post("/api/auth/change-password")
    async def auth_change_password(request: Request, user=Depends(_auth_current_user)):
        from auth import hash_password
        body = await request.json()
        cstore = app.state.control_store
        new_password = str(body.get("new_password") or "")
        if len(new_password) < 8:
            raise HTTPException(400, "New password must be at least 8 characters")
        cstore.set_password(user.user_id, hash_password(new_password))
        return {"ok": True}

    @app.get("/api/me")
    async def auth_me(request: Request):
        from auth import get_current_user_optional
        user = get_current_user_optional(request)
        if user is None:
            return {"authenticated": False}
        cstore = app.state.control_store
        if user.role == "controller":
            airports = [dict(a) for a in cstore.get_active_watched_airports()]
        else:
            allowed = set(cstore.get_user_airports(user.user_id))
            airports = [dict(a) for a in cstore.get_active_watched_airports()
                        if a["airport_iata"] in allowed]
        return {
            "authenticated": True,
            "user": {"id": user.user_id, "username": user.username, "role": user.role, "language": user.language},
            "airport": user.airport_iata,
            "airports": [{
                "iata": a["airport_iata"], "name": a["airport_name"],
                "icao": a["airport_icao"], "tz": a["airport_tz"],
                "country_code": a["country_code"] or "", "tz_abbr": _tz_abbr(a["airport_tz"]),
            } for a in airports],
        }

    @app.put("/api/me/language")
    async def set_my_language(request: Request, user=Depends(_auth_current_user)):
        body = await request.json()
        language = str(body.get("language") or "")
        if language not in ("en", "zh"):
            raise HTTPException(400, "Unsupported language")
        app.state.control_store.set_language(user.user_id, language)
        return {"ok": True}

    @app.get("/api/airports/mine")
    async def airports_mine(user=Depends(_auth_current_user)):
        cstore = app.state.control_store
        if user.role == "controller":
            airports = cstore.get_active_watched_airports()
        else:
            allowed = set(cstore.get_user_airports(user.user_id))
            airports = [a for a in cstore.get_active_watched_airports()
                        if a["airport_iata"] in allowed]
        return {"airports": [{
            "iata": a["airport_iata"], "name": a["airport_name"],
            "icao": a["airport_icao"], "tz": a["airport_tz"],
            "country_code": a["country_code"] or "", "tz_abbr": _tz_abbr(a["airport_tz"]),
        } for a in airports]}

    @app.post("/api/airport/select")
    async def airport_select(request: Request, response: Response, user=Depends(_auth_current_user)):
        from auth import AIRPORT_COOKIE
        body = await request.json()
        iata = str(body.get("airport_iata") or "").strip()
        cstore = app.state.control_store
        if not cstore.get_watched_airport(iata):
            raise HTTPException(404, "Unknown airport")
        if user.role != "controller" and iata not in cstore.get_user_airports(user.user_id):
            raise HTTPException(403, "You do not have access to this airport")
        response.set_cookie(AIRPORT_COOKIE, iata, max_age=90 * 86400,
                            httponly=False, samesite="lax",
                            secure=(request.url.scheme == "https"))
        # Each device keeps its own independent view via its own cookie (desktop
        # and mobile can genuinely show different airports at once) — but push
        # notifications only ever go to a phone, so the notification-relevant
        # "last selected airport" is scoped to mobile requests specifically,
        # never overwritten by a desktop selection. Without this, switching
        # airports on desktop after switching on mobile would silently redirect
        # notifications away from the airport actually being watched on the phone.
        import re as _re
        _ua = (request.headers.get("user-agent") or "")
        if _re.search(r"Mobile|Android|iPhone|iPod", _ua, _re.I):
            cstore.set_last_airport(_push_owner_id(user), iata)
        return {"ok": True}

    @app.post("/api/controller/airports")
    async def controller_add_airport(request: Request, user=Depends(_auth_require_role("controller"))):
        """Add a new watched airport at runtime — no process restart. Each airport
        gets its own SQLite DB file (data/airports/<iata>.db), so this never touches
        any existing airport's data."""
        body = await request.json()
        airport_code = str(body.get("airport_code") or "").strip()
        if not airport_code:
            raise HTTPException(400, "airport_code is required")
        if app.state.fr_api is None:
            raise HTTPException(400, "Only available in integrated mode")

        from store import SqliteStore
        from bootstrap import build_config, _country_code_for_iata

        new_dir = os.path.join(app.state.data_dir, "airports")
        os.makedirs(new_dir, exist_ok=True)
        new_store = SqliteStore(os.path.join(new_dir, f"{airport_code.upper()}.db"))
        new_store.save_setting("AIRPORT_CODE", airport_code)

        cfg = build_config(app.state.fr_api, new_store, app.state.catalog, app.state.control_store)
        if not cfg.airport_iata:
            # build_config()'s FR24 lookup failed with no cached fallback
            # available (rate-limited, bad code, transient network issue) —
            # _fetch_airport's minimal-fallback path returns an empty iata in
            # that case. Registering that would create an unrecoverable
            # "phantom" watched-airport row with no code at all, so refuse
            # instead and let the Controller retry.
            import glob
            for path in glob.glob(new_store.db_path + "*"):
                try:
                    os.remove(path)
                except OSError:
                    pass
            raise HTTPException(400, f"Could not fetch airport info for '{airport_code}' — try again in a moment")
        if cfg.airport_iata in app.state.cfgs:
            raise HTTPException(400, f"{cfg.airport_iata} is already being watched")

        # Seed the new airport's DB with the current global infra settings,
        # every user's PER_USER_GLOBAL_SETTINGS values (Collection Stat
        # Keywords, Session Panel Tags, and the Controller's Special Livery
        # Keywords), and Controller-only custom airports/aircraft-types, so it
        # starts in sync with every other watched airport rather than falling
        # back to build_config()'s hardcoded in-memory defaults.
        if app.state.cfgs:
            _source_store = next(iter(app.state.cfgs.values())).store
            with _source_store._connect() as _sconn:
                for _key in GLOBAL_INFRA_SETTINGS:
                    _row = _sconn.execute(
                        "SELECT value FROM settings WHERE user_id = 'controller' AND key = ?", (_key,)
                    ).fetchone()
                    if _row is not None:
                        new_store.set_setting("controller", _key, _row["value"])
                _per_user_rows = _sconn.execute(
                    "SELECT user_id, key, value FROM settings WHERE key IN (%s)"
                    % ",".join("?" * len(PER_USER_GLOBAL_SETTINGS)),
                    tuple(PER_USER_GLOBAL_SETTINGS),
                ).fetchall()
                _airport_rows = _sconn.execute(
                    "SELECT iata, name, country_code FROM airports WHERE source = 'user'"
                ).fetchall()
                # Full table, not just source='user' — the bulk 'icaolist' reference
                # rows (~2700 ICAO type-code -> manufacturer/model mappings) only ever
                # get populated by a manual GitHub refresh against one store, so a
                # brand-new airport's DB would otherwise start with zero rows and
                # silently break manufacturer resolution (see bootstrap.py's
                # _reconcile_global_settings_across_airports for the full story).
                _type_rows = _sconn.execute(
                    "SELECT icao, name, source, manufacturer FROM aircraft_types"
                ).fetchall()
            for _r in _per_user_rows:
                new_store.set_setting(_r["user_id"], _r["key"], _r["value"])
            for _r in _airport_rows:
                new_store.upsert_airport(_r["iata"], _r["name"], _r["country_code"], source='user')
            new_store.upsert_aircraft_types_bulk(
                [(_r["icao"], _r["name"], _r["source"], _r["manufacturer"]) for _r in _type_rows])

        app.state.control_store.register_airport(
            airport_iata=cfg.airport_iata, airport_code=cfg.airport_code,
            airport_name=cfg.airport_name, airport_icao=cfg.airport_icao,
            airport_tz=cfg.airport_tz, airport_lat=cfg.airport_lat, airport_lon=cfg.airport_lon,
            db_path=new_store.db_path, added_by_user_id=user.user_id,
            country_code=_country_code_for_iata(cfg.airport_iata),
        )
        app.state.cfgs[cfg.airport_iata] = cfg  # so THIS process's own requests see
                                                 # it immediately, without waiting on
                                                 # the monitor process's reconciliation

        # Seed the Controller's own per-airport SPOT_*/RARE_PLANE_MIN_ABSENCE_DAYS
        # values on this brand-new airport from their value on an airport they
        # already have, if any (their very first-ever airport has nothing to
        # seed from, so build_config()'s hardcoded defaults apply instead).
        seed_new_airport_prefill_settings(app.state.cfgs, "controller", cfg.airport_iata)

        # The monitor process (a separate OS process — see monitor_service.py)
        # discovers this new watched_airports row on its own, via
        # run_cfg_reconciliation_loop's periodic poll (every
        # monitor_runner.CFG_RECONCILE_POLL_SECS), and fires the immediate
        # first arrivals/military check itself once it does — nothing to spawn
        # here directly anymore now that the web and monitor processes don't
        # share memory.
        return JSONResponse({"ok": True, "airport_iata": cfg.airport_iata, "airport_name": cfg.airport_name})

    @app.delete("/api/controller/airports/{iata}")
    async def controller_remove_airport(iata: str, user=Depends(_auth_require_role("controller"))):
        """Hard delete, per explicit requirement: once removed, every file for
        that airport is gone for every user — not a soft active=0 flag. Removes
        the watched_airports row and any user_airport_access grants to it, then
        deletes the airport's own SQLite DB file (plus WAL/SHM/journal sidecars
        and any backups) from disk.

        Waits briefly between the DB removal and the file deletion: the monitor
        process (a separate OS process — see monitor_service.py) only notices an
        airport was removed on its next run_cfg_reconciliation_loop poll (every
        monitor_runner.CFG_RECONCILE_POLL_SECS), so deleting the files
        immediately could race a check already in flight for this airport in
        that process. The wait here is generous relative to that poll interval
        so the monitor process has stopped touching this airport's files by the
        time they're removed."""
        iata = iata.upper()
        if iata not in app.state.cfgs:
            raise HTTPException(404, "Unknown airport")
        if len(app.state.cfgs) <= 1:
            raise HTTPException(400, "Cannot delete the only watched airport")

        cfg = app.state.cfgs[iata]
        db_path = cfg.store.db_path

        app.state.cfgs.pop(iata, None)
        app.state.control_store.delete_watched_airport(iata)

        import asyncio as _asyncio
        from monitor_runner import CFG_RECONCILE_POLL_SECS
        await _asyncio.sleep(CFG_RECONCILE_POLL_SECS * 2)

        import glob
        for path in glob.glob(db_path + "*"):
            try:
                os.remove(path)
            except OSError as exc:
                log.warning("Could not remove airport DB file %s: %s", path, exc)
        backup_dir = os.path.join(os.path.dirname(db_path), "backups")
        stem = os.path.splitext(os.path.basename(db_path))[0]
        for path in glob.glob(os.path.join(backup_dir, f"{stem}_*")):
            try:
                os.remove(path)
            except OSError as exc:
                log.warning("Could not remove airport backup file %s: %s", path, exc)

        return JSONResponse({"ok": True})

    @app.get("/api/controller/users")
    async def controller_list_users(user=Depends(_auth_require_role("controller"))):
        cstore = app.state.control_store
        out = []
        for u in cstore.list_users():
            out.append({
                "id": u["user_id"], "username": u["username"], "role": u["role"],
                "airport_iatas": [] if u["role"] == "controller" else cstore.get_user_airports(u["user_id"]),
                "created_ts": u["created_ts"],
            })
        return JSONResponse({"users": out})

    @app.post("/api/controller/users")
    async def controller_create_user(request: Request, user=Depends(_auth_require_role("controller"))):
        from auth import hash_password
        body = await request.json()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        role = str(body.get("role") or "")
        airport_iatas = body.get("airport_iatas") or []
        if not username or len(password) < 8 or role not in ("pilot", "passenger"):
            # There is exactly one Controller per server (set up outside this
            # endpoint) — only Pilot/Passenger accounts can ever be created here.
            raise HTTPException(400, "username, a password of at least 8 characters, and a role of pilot or passenger are required")
        cstore = app.state.control_store
        if cstore.get_user_by_username(username):
            raise HTTPException(409, "That username is already taken")
        new_id = cstore.create_user(username, hash_password(password), role, airport_iatas)
        if role == "pilot":
            # One-time snapshot of the Controller's current exclusion/watchlist
            # rows AND pilot-editable settings values into this Pilot's own
            # rows, per airport they've just been granted — after this
            # they're fully independent, no further syncing. The per-airport
            # SPOT_*/RARE_PLANE keys instead seed from this same Pilot's own
            # value elsewhere first (see seed_new_airport_prefill_settings);
            # SPECIAL_LIVERY_EXCLUDE_KEYWORDS is never seeded at all.
            for iata in airport_iatas:
                cfg = app.state.cfgs.get(iata)
                if cfg:
                    cfg.store.copy_controller_filters_to_owner(new_id)
                    cfg.store.copy_controller_settings_to_owner(
                        new_id, PER_USER_GLOBAL_SETTINGS - {"SPECIAL_LIVERY_KEYWORDS"})
                    seed_new_airport_prefill_settings(app.state.cfgs, new_id, iata)
        return JSONResponse({"ok": True, "id": new_id})

    @app.put("/api/controller/users/{target_id}")
    async def controller_update_user(target_id: str, request: Request, user=Depends(_auth_require_role("controller"))):
        body = await request.json()
        cstore = app.state.control_store
        target = cstore.get_user(target_id)
        if not target:
            raise HTTPException(404, "Unknown user")
        role = body.get("role")
        if role is not None and role not in ("controller", "pilot", "passenger"):
            raise HTTPException(400, "Invalid role")
        if role is not None and role != target["role"]:
            # There is exactly one Controller per server, so its role can never
            # be changed here; a Pilot can never be changed either way (no
            # downgrade to Passenger, no upgrade to Controller); the only
            # allowed transition at all is Passenger -> Pilot.
            if target["role"] != "passenger" or role != "pilot":
                raise HTTPException(400, "That role change is not allowed")
        username = body.get("username")
        if username is not None:
            username = str(username).strip()
            if not username:
                raise HTTPException(400, "Username is required")
            existing = cstore.get_user_by_username(username)
            if existing and existing["user_id"] != target_id:
                raise HTTPException(409, "That username is already taken")
        airport_iatas = body.get("airport_iatas")
        effective_role = role if role is not None else target["role"]
        new_airports = []
        if effective_role == "pilot" and airport_iatas is not None:
            old_airports = set(cstore.get_user_airports(target_id))
            new_airports = [iata for iata in airport_iatas if iata not in old_airports]
        cstore.update_user(target_id, role=role, airport_iatas=airport_iatas, username=username)
        if username is not None and username != target["username"]:
            # Username changed — invalidate every existing session for this
            # account, same as a password change (set_password already does
            # this for the reset-password endpoint).
            cstore.bump_session_epoch(target_id)
        if effective_role == "pilot":
            # Snapshot the Controller's current filter/watchlist rows AND
            # pilot-editable settings values into this Pilot's own rows for
            # any newly granted airport (or, on a fresh promotion to Pilot
            # with no airport_iatas in this request, every airport they
            # already have access to) — one-time, per airport. The per-airport
            # SPOT_*/RARE_PLANE keys instead seed from this same Pilot's own
            # value on another airport they already have, if any (see
            # seed_new_airport_prefill_settings); SPECIAL_LIVERY_EXCLUDE_KEYWORDS
            # is never seeded at all.
            targets = new_airports if airport_iatas is not None else cstore.get_user_airports(target_id)
            for iata in targets:
                cfg = app.state.cfgs.get(iata)
                if cfg:
                    cfg.store.copy_controller_filters_to_owner(target_id)
                    cfg.store.copy_controller_settings_to_owner(
                        target_id, PER_USER_GLOBAL_SETTINGS - {"SPECIAL_LIVERY_KEYWORDS"})
                    seed_new_airport_prefill_settings(app.state.cfgs, target_id, iata)
        return JSONResponse({"ok": True})

    @app.post("/api/controller/users/{target_id}/reset-password")
    async def controller_reset_password(target_id: str, request: Request, user=Depends(_auth_require_role("controller"))):
        from auth import hash_password
        body = await request.json()
        new_password = str(body.get("new_password") or "")
        if len(new_password) < 8:
            raise HTTPException(400, "New password must be at least 8 characters")
        cstore = app.state.control_store
        if not cstore.get_user(target_id):
            raise HTTPException(404, "Unknown user")
        cstore.set_password(target_id, hash_password(new_password))
        return JSONResponse({"ok": True})

    @app.delete("/api/controller/users/{target_id}")
    async def controller_delete_user(target_id: str, user=Depends(_auth_require_role("controller"))):
        """Cascade-deletes the user's data everywhere: their control.db row (and,
        via ON DELETE CASCADE, user_airport_access), their catalog file, and —
        since SQLite has no cross-file foreign keys — their settings/filter rows
        in every per-airport DB they had access to. This is why the loop below is
        application code rather than a single DB constraint; missing an airport
        here would leave orphaned rows behind."""
        cstore = app.state.control_store
        target = cstore.get_user(target_id)
        if not target:
            raise HTTPException(404, "Unknown user")
        if target_id == user.user_id:
            raise HTTPException(400, "You cannot delete your own account")

        for cfg in app.state.cfgs.values():
            with cfg.store._connect() as conn:
                conn.execute("DELETE FROM settings WHERE user_id = ?", (target_id,))
                for tbl in ("filter_exclusions", "filter_regos", "filter_types", "filter_airlines"):
                    conn.execute(f"DELETE FROM {tbl} WHERE owner_user_id = ?", (target_id,))

        catalog_path = target["catalog_path"]
        if catalog_path and os.path.exists(catalog_path):
            try:
                os.remove(catalog_path)
            except OSError as exc:
                log.warning("Could not remove catalog file for deleted user %s: %s", target_id, exc)

        cstore.delete_user(target_id)
        return JSONResponse({"ok": True})

    @app.get("/api/catalog/status")
    async def catalog_status(user=Depends(_auth_require_role("controller", "pilot"))):
        row = app.state.control_store.get_user(user.user_id)
        path = row["catalog_path"] if row else None
        return JSONResponse({
            "has_catalog": bool(path),
            "filename": os.path.basename(path) if path else None,
        })

    @app.post("/api/catalog/upload")
    async def catalog_upload(file: UploadFile = File(...), user=Depends(_auth_require_role("controller", "pilot"))):
        """Streams the upload to disk in chunks (Lightroom catalogs can be
        hundreds of MB to several GB — never buffer the whole thing in memory),
        validates it actually looks like a Lightroom catalog before committing
        the new catalog_path, and only then removes the user's previous catalog
        file — so a bad upload never leaves them with no working catalog."""
        safe_name = os.path.basename(file.filename or "catalog.lrcat")
        user_dir = os.path.join(app.state.data_dir, "catalogs", f"user_{user.user_id}")
        os.makedirs(user_dir, exist_ok=True)
        new_path = os.path.join(user_dir, safe_name)
        tmp_path = new_path + ".uploading"

        try:
            with open(tmp_path, "wb") as f:
                while chunk := await file.read(1024 * 1024):
                    f.write(chunk)
        except Exception as exc:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise HTTPException(400, f"Upload failed: {exc}")

        from lightroom import LightroomCatalog
        try:
            candidate = LightroomCatalog(tmp_path)
        except Exception:
            candidate = None
        if candidate is None or candidate._reg_spec_id is None:
            os.remove(tmp_path)
            raise HTTPException(400, "That file doesn't look like a valid Lightroom catalog")

        os.replace(tmp_path, new_path)  # atomic rename, now that it's validated

        cstore = app.state.control_store
        old_row = cstore.get_user(user.user_id)
        old_path = old_row["catalog_path"] if old_row else None
        cstore.set_catalog_path(user.user_id, new_path)
        if old_path and old_path != new_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError as exc:
                log.warning("Could not remove previous catalog for %s: %s", user.user_id, exc)

        return JSONResponse({"ok": True, "filename": safe_name})

    @app.delete("/api/catalog")
    async def catalog_delete(user=Depends(_auth_require_role("controller", "pilot"))):
        cstore = app.state.control_store
        row = cstore.get_user(user.user_id)
        path = row["catalog_path"] if row else None
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError as exc:
                log.warning("Could not remove catalog file for %s: %s", user.user_id, exc)
        cstore.set_catalog_path(user.user_id, None)
        return JSONResponse({"ok": True})

    # ── API routes ──────────────────────────────────────────────────────────

    @app.post("/api/restart")
    async def restart_backend(user=Depends(_auth_require_role("controller"))):
        """Exit the process — Docker will auto-restart; on PC restart manually."""
        import asyncio, os
        async def _do_exit():
            await asyncio.sleep(0.5)
            os._exit(0)
        asyncio.create_task(_do_exit())
        return JSONResponse({"ok": True, "msg": "Restarting…"})

    @app.post("/api/refresh-fr24")
    async def refresh_fr24(user=Depends(_auth_require_role("controller"))):
        """Re-seed FR24 cookies from disk (call after copying fresh .fr24_cookies.pkl to data/)."""
        from flightradar24api.request import reload_cookies
        ok = reload_cookies()
        return JSONResponse({"ok": ok, "msg": "Cookies reloaded" if ok else "No cookie file found"})

    @app.post("/api/force-check")
    async def force_check(user=Depends(_auth_require_role("controller"))):
        cfg = _cfg_for_user(user)  # raises 400 if no airport selected
        # Write a request row for the monitor process (a separate OS process —
        # see monitor_service.py) to pick up on its next
        # run_force_check_poller tick (every monitor_runner.FORCE_CHECK_POLL_SECS)
        # instead of setting an in-process asyncio.Event, which can't cross a
        # process boundary. This also resets the airport's periodic rotation
        # timer once the monitor process runs the check, same as before.
        app.state.control_store.request_force_check(cfg.airport_iata)
        return JSONResponse({"ok": True})

    @app.get("/api/live-status/{registration}")
    async def get_live_status(registration: str, user=Depends(_auth_current_user)):
        """Check if the aircraft is currently on the ground at the local airport.
        Uses the airport schedule's ground section — the same API call the monitor uses.
        Called lazily only when stored status inference returns N/A."""
        cfg_ = _cfg_for_user(user)
        if not cfg_ or not getattr(cfg_, 'fr_api', None):
            return JSONResponse({"status": None})
        try:
            reg_upper = registration.upper().strip()
            now_ts_ = int(time.time())
            # Cache keyed per-airport — one process now serves multiple airports'
            # schedules, so a single shared cache would leak one airport's board
            # into another's live-status lookups.
            cache = _live_status_cache.setdefault(cfg_.airport_iata, {"ts": 0, "schedule": None})
            if now_ts_ - cache["ts"] > 90 or cache["schedule"] is None:
                import asyncio
                # Thread-dispatched — blocking FR24 network call, see
                # get_airforce_roundel's comment above for why.
                data = await asyncio.to_thread(cfg_.fr_api.get_airport_details,
                                                code=cfg_.airport_code, page=-1)
                cache["schedule"] = data["airport"]["pluginData"]["schedule"]
                cache["ts"] = now_ts_
            schedule = cache["schedule"]

            # Check recent arrivals: real_arr set = landed; no real_dep = still on ground
            for entry in (schedule.get("arrivals") or {}).get("data") or []:
                try:
                    fl = entry["flight"]
                    if (fl["aircraft"]["registration"] or "").upper().strip() != reg_upper:
                        continue
                    times = fl.get("time") or {}
                    real_arr = (times.get("real") or {}).get("arrival")
                    real_dep = (times.get("real") or {}).get("departure")
                    if real_arr and not real_dep:
                        return JSONResponse({"status": "On Ground"})
                    if real_arr and real_dep:
                        return JSONResponse({"status": "Departed"})
                except (KeyError, TypeError):
                    continue

            # Check recent departures: real_dep set = departed from this airport
            for entry in (schedule.get("departures") or {}).get("data") or []:
                try:
                    fl = entry["flight"]
                    if (fl["aircraft"]["registration"] or "").upper().strip() != reg_upper:
                        continue
                    real_dep = ((fl.get("time") or {}).get("real") or {}).get("departure")
                    if real_dep:
                        return JSONResponse({"status": "Departed"})
                except (KeyError, TypeError):
                    continue

        except Exception as exc:
            log.warning("Live status check failed for %s: %s", registration, exc)
        return JSONResponse({"status": None})

    @app.get("/api/aircraft/{registration}")
    async def get_aircraft(registration: str, user=Depends(_auth_current_user)):
        # Airport-specific data (flight_arrivals, departure_patterns predictions,
        # route lookup) must resolve to whichever airport the user has selected —
        # catalog data (last-spotted/sessions) is per-user regardless of airport.
        cfg   = _cfg_for_user(user)
        store = cfg.store
        airport_iata = cfg.airport_iata if cfg else (store.load_setting("AIRPORT_CODE") or "")
        result: dict = {}

        # Lightroom: last spotted + all sessions
        catalog = app.state.get_user_catalog(user)
        if catalog:
            try:
                spotted = catalog.get_last_spotted(registration)
                if spotted:
                    dt, apt, count = spotted
                    result["last_spotted_ts"]      = int(dt.timestamp())
                    result["last_spotted_airport"] = apt
                    result["spotted_count"]        = count
                sessions = catalog.get_all_sessions(registration)
                result["sessions"] = [
                    {"ts": int(dt.timestamp()), "airport": apt, "count": cnt, "notes": notes}
                    for dt, apt, cnt, notes in sessions
                ]
            except Exception as exc:
                log.warning("Lightroom lookup failed for %s: %s", registration, exc)

        # Next departure prediction — look up most recent flight_number from flight_arrivals.
        # Excludes Cancelled/Swapped rows: that arrival never actually happened under this
        # identity, so predicting a departure off it doesn't make sense (Diverted is kept —
        # the aircraft did arrive, and can still depart again later).
        if airport_iata:
            with store._connect() as conn:
                row = conn.execute(
                    "SELECT flight_number, arrival_ts FROM flight_arrivals "
                    "WHERE registration = ? AND flight_number IS NOT NULL "
                    "AND current_status NOT IN ('Cancelled', 'Swapped') "
                    "ORDER BY arrival_ts DESC LIMIT 1",
                    (registration,)
                ).fetchone()
            if row and row["flight_number"]:
                flight_number = row["flight_number"]
                arrival_ts    = row["arrival_ts"] or 0
                predicted = store.get_predicted_departure(flight_number, airport_iata, 0)
                if predicted:
                    dep_fn, confidence, _, _ = predicted
                    dep_info = store.get_predicted_dep_info(dep_fn, airport_iata) or {}
                    result["next_dep_flight"]     = dep_fn
                    result["next_dep_confidence"] = round(confidence)
                    result["next_dep_dest_iata"]  = dep_info.get("dest_iata")
                    result["next_dep_dest_name"]  = dep_info.get("dest_name")
                    result["next_dep_airline"]    = dep_info.get("airline_name")
                    # Use the first timestamp that is in the future; fall back to
                    # arrival_ts + turnaround if known; otherwise omit the time.
                    now_ts = int(time.time())
                    est  = dep_info.get("estimated_dep_ts")
                    sched = dep_info.get("scheduled_dep_ts")
                    turn  = dep_info.get("turnaround_secs")
                    actual = dep_info.get("actual_dep_ts")
                    if est and est > now_ts:
                        dep_ts, dep_label = est, "Estimated"
                    elif sched and sched > now_ts:
                        dep_ts, dep_label = sched, "Scheduled"
                    elif turn and arrival_ts:
                        predicted_ts = arrival_ts + turn
                        dep_ts = predicted_ts
                        dep_label = "Predicted" if predicted_ts > now_ts else "Departed"
                    elif actual:
                        dep_ts, dep_label = actual, "Departed"
                    elif est:
                        dep_ts, dep_label = est, "Departed"
                    elif sched:
                        dep_ts, dep_label = sched, "Departed"
                    else:
                        dep_ts, dep_label = None, None
                    result["next_dep_ts"]    = dep_ts
                    result["next_dep_label"] = dep_label
                # Also try to get origin airport via route lookup
                route = store.get_flight_route(flight_number, airport_iata)
                if route and route.get("origin_iata"):
                    result["origin_iata"] = route["origin_iata"]
                    result["origin_name"] = route.get("origin_name")

        # Backfill manufacturer from FR24 if missing in airframes
        if cfg and cfg.fr_api:
            try:
                airframe = store.get_airframe(registration.upper())
                if airframe is None or not airframe.get("manufacturer"):
                    import asyncio
                    from monitor import _derive_manufacturer
                    # Thread-dispatched — blocking FR24 network call, see
                    # get_airforce_roundel's comment above for why. This
                    # endpoint is hit on every card open, so it matters.
                    rd = await asyncio.to_thread(cfg.fr_api.get_rego_details, registration.upper())
                    _rd_data = (rd or {}).get("data") or []
                    if _rd_data:
                        _model_text = ((_rd_data[0].get("aircraft") or {}).get("model") or {}).get("text") or ""
                    else:
                        _model_text = ((rd or {}).get("aircraftInfo") or {}).get("model", {}).get("text", "")
                    _mfr = _derive_manufacturer(_model_text)
                    if _mfr:
                        store.upsert_airframe_from_fr24(registration.upper(), manufacturer=_mfr)
                        result["manufacturer"] = _mfr
            except Exception as exc:
                log.debug("Manufacturer backfill failed for %s: %s", registration, exc)

        result["airport_iata"] = airport_iata
        result["airport_name"] = (cfg.airport_name if cfg else None) or ''

        # Last Visit from rego_sightings: show prev_seen_ts when last_seen_ts is today
        import datetime as _dt
        today_start = int(_dt.datetime.combine(_dt.date.today(), _dt.time.min).timestamp())
        with store._connect() as conn:
            sh = conn.execute(
                "SELECT last_seen_ts, prev_seen_ts FROM rego_sightings WHERE registration = ?",
                (registration.upper(),)
            ).fetchone()
        if sh:
            last = sh["last_seen_ts"]
            prev = sh["prev_seen_ts"]
            if last and last < today_start:
                result["prev_seen_ts"] = last
            elif prev:
                result["prev_seen_ts"] = prev

        return JSONResponse(result)

    @app.get("/api/feed")
    async def get_feed(days: int = 30, user=Depends(_auth_current_user)):
        import datetime, json as _json, pytz

        cfg_   = _cfg_for_user(user)
        store_ = cfg_.store
        airport_iata_ = (cfg_.airport_iata if cfg_ else None) or store_.load_setting("AIRPORT_CODE") or ""
        airport_name_ = (cfg_.airport_name if cfg_ else None) or ""
        # Timezone is tied to the airport's own location — never separately
        # user-settable (WEB_TIMEZONE override removed).
        tz_name = (getattr(cfg_, 'airport_tz', None) if cfg_ else None) or "UTC"
        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = pytz.utc

        now_dt    = datetime.datetime.now(tz)
        now_ts    = int(time.time())
        cutoff_ts = now_ts - max(1, min(days, 30)) * 86400
        dep_threshold = int(
            store_.load_setting("DEPARTURE_PATTERN_THRESHOLD")
            or (getattr(cfg_, 'departure_pattern_threshold', None) if cfg_ else None)
            or 80
        )

        def _ts_local(ts):
            return datetime.datetime.fromtimestamp(ts, tz=tz)

        with store_._connect() as conn:
            rows = conn.execute("""
                SELECT fe.id AS fe_id,
                       fe.registration, fe.flight_number, fe.arrival_ts, fe.first_seen_ts,
                       fe.notif_types, fe.detail, fe.extra_info, fe.origin_iata, fe.origin_name,
                       fe.current_status, fe.arr_label, fe.airline_icao, fe.photo_url AS fe_photo_url,
                       fe.aircraft_type, fe.rare_absence_days,
                       fd.dep_flight, fd.dep_ts, fd.dep_dest_iata, fd.dep_dest_name,
                       fd.is_prediction, fd.dep_label, fd.dep_confidence,
                       a.photo_url AS af_photo_url, a.manufacturer,
                       sh.last_seen_ts AS airport_last_seen_ts,
                       ap_o.city AS origin_city, ap_d.city AS dep_dest_city
                FROM flight_arrivals fe
                LEFT JOIN flight_departures fd ON fd.arrival_id = fe.id
                LEFT JOIN airframes a        ON a.registration  = fe.registration
                LEFT JOIN rego_sightings sh  ON sh.registration = fe.registration
                LEFT JOIN airports ap_o      ON ap_o.iata = fe.origin_iata
                LEFT JOIN airports ap_d      ON ap_d.iata = fd.dep_dest_iata
                WHERE fe.first_seen_ts >= ? AND fe.flight_number IS NOT NULL
                  AND fe.registration NOT IN (SELECT registration FROM filter_exclusions WHERE owner_user_id = ?)
                ORDER BY fe.arrival_ts ASC
            """, (cutoff_ts, _owner_id(user))).fetchall()
            _livery_excl = _viewer_livery_exclude_keywords(conn, user)
            _rare_min_days = _viewer_rare_plane_min_days(conn, user)
            _watchlist_sets = _viewer_watchlist_sets(store_, user)

        events = []
        for row in rows:
            arr_ts = row["arrival_ts"]
            if not arr_ts:
                continue
            arr_dt  = _ts_local(arr_ts)
            arr_date = arr_dt.strftime("%Y-%m-%d")
            arr_local_min = arr_dt.hour * 60 + arr_dt.minute

            fn = row["flight_number"]

            # Departure info from flight_departures table (paired by monitor Step 7)
            dep_ts_val    = row["dep_ts"]
            dep_flight    = row["dep_flight"]
            dep_dest_iata = row["dep_dest_iata"]
            dep_dest_name = row["dep_dest_name"]
            dep_confidence = row["dep_confidence"]  # set for predictions, None for live data

            dep_local_min = dep_date = None
            if dep_ts_val:
                dep_dt = _ts_local(dep_ts_val)
                dep_local_min = dep_dt.hour * 60 + dep_dt.minute
                dep_date = dep_dt.strftime("%Y-%m-%d")

            try:
                notif_types = _json.loads(row["notif_types"] or "[]")
            except Exception:
                notif_types = [row["notif_types"]] if row["notif_types"] else []
            notif_types = _strip_excluded_livery_tag(notif_types, row["extra_info"], _livery_excl)
            notif_types = _resolve_rare_plane_tag(notif_types, row["rare_absence_days"], _rare_min_days)
            notif_types = _strip_unowned_watchlist_tags(
                notif_types, row["registration"], row["aircraft_type"], row["airline_icao"], _watchlist_sets)
            # If every tag this viewer would have cared about got stripped (their
            # own exclude keywords / thresholds / private watchlists), this flight
            # has no remaining reason to appear in their Feed at all — matches how
            # registration exclusion already works (drop the row for this viewer
            # entirely), and restores the pre-multi-user behavior where an excluded
            # livery never created a visible card in the first place.
            if not notif_types:
                continue

            events.append({
                "registration":         row["registration"],
                # Prefer the snapshot frozen onto the flight row at enrichment time (avoids
                # re-deriving it from airframes on every request); fall back to the airframes
                # cache for older rows recorded before this column existed.
                "photo_url":            row["fe_photo_url"] or row["af_photo_url"] or "",
                "manufacturer":         row["manufacturer"] or "",
                "detail":               row["detail"] or "",
                "extra_info":           row["extra_info"] or "",
                "airline_icao":         row["airline_icao"] or "",
                "notif_types":          notif_types,
                "airport_last_seen_ts": row["airport_last_seen_ts"],
                "arr_date":  arr_date,
                "dep_date":  dep_date,
                "flight": {
                    "fe_id":          row["fe_id"],
                    "flight_number":  fn,
                    "arrival_ts":     arr_ts,
                    "arr_local_min":  arr_local_min,
                    "origin_iata":    row["origin_iata"],
                    "origin_name":    row["origin_name"],
                    "origin_city":    row["origin_city"] or "",
                    "arr_label":      row["arr_label"] or None,
                    "dep_flight":     dep_flight,
                    "dep_local_min":  dep_local_min,
                    "dep_ts":         dep_ts_val,
                    "dep_dest_iata":   dep_dest_iata,
                    "dep_dest_name":   dep_dest_name,
                    "dep_dest_city":   row["dep_dest_city"] or "",
                    "dep_confidence":  dep_confidence,
                    "dep_label":         row["dep_label"] or ("Predicted" if row["is_prediction"] else "Scheduled"),
                    "current_status":  row["current_status"],
                },
            })

        # Group by (registration, date), with cross-midnight duplication
        day_cards: dict = {}  # date_str → {registration → card_dict}

        def _add(date_str, ev):
            day_cards.setdefault(date_str, {})
            reg = ev["registration"]
            if reg not in day_cards[date_str]:
                day_cards[date_str][reg] = {
                    "registration":         reg,
                    "photo_url":            ev["photo_url"],
                    "manufacturer":         ev["manufacturer"],
                    "detail":               ev["detail"],
                    "airline_icao":         ev["airline_icao"],
                    "extra_info":           ev["extra_info"],
                    "airport_last_seen_ts": ev["airport_last_seen_ts"],
                    "notif_types":          [],
                    "flights":              [],
                }
            card = day_cards[date_str][reg]
            for nt in ev["notif_types"]:
                if nt not in card["notif_types"]:
                    card["notif_types"].append(nt)
            existing_fns = {f["flight_number"] for f in card["flights"]}
            if ev["flight"]["flight_number"] not in existing_fns:
                card["flights"].append(ev["flight"])

        # Build a set of (registration, date) pairs that have a real flight_arrivals arrival
        # Skip cross-midnight only if this exact flight already has a real arrival
        # on dep_date — prevents the same flight appearing twice. Other flights for
        # the same rego on dep_date are fine (they're different bars in the same card).
        real_flights_on_date: set = {
            (ev["registration"], ev["flight"]["flight_number"], ev["arr_date"])
            for ev in events
        }

        for ev in events:
            _add(ev["arr_date"], ev)
            if ev["dep_date"] and ev["dep_date"] != ev["arr_date"]:
                arr_ts = ev["flight"].get("arrival_ts") or 0
                dep_ts = ev["flight"].get("dep_ts") or 0
                if dep_ts - arr_ts > 86400:
                    continue
                fn = ev["flight"].get("flight_number")
                if (ev["registration"], fn, ev["dep_date"]) in real_flights_on_date:
                    continue
                _add(ev["dep_date"], ev)

        for date_str, cards in day_cards.items():
            for card in cards.values():
                card["flights"].sort(key=lambda f: f["arrival_ts"] or 0)

        # Attach flown-path track points to military flight events
        military_fe_ids = [
            f["fe_id"]
            for cards in day_cards.values()
            for card in cards.values()
            if "Military" in card["notif_types"]
            for f in card["flights"]
        ]
        if military_fe_ids:
            track_map = store_.get_military_track_points(military_fe_ids)
            for cards in day_cards.values():
                for card in cards.values():
                    if "Military" not in card["notif_types"]:
                        continue
                    for f in card["flights"]:
                        f["track"] = track_map.get(f["fe_id"], [])

        today_local = now_dt.date()
        days_result = []
        for i in range(-3, 30):  # -3 to +3 days future, 0 = today, up to 30 days back
            d = today_local - datetime.timedelta(days=i)
            date_str = d.strftime("%Y-%m-%d")
            cards_for_day = list((day_cards.get(date_str) or {}).values())
            if not cards_for_day:
                continue
            cards_for_day.sort(
                key=lambda c: min(
                    ts for f in c["flights"]
                    for ts in (f.get("arrival_ts") or 0, f.get("dep_ts") or 0)
                    if ts
                ) if c["flights"] else 0,
                reverse=True,
            )
            if i == -1:  label = "Tomorrow"
            elif i < -1: label = f"{d.strftime('%a')}, {d.day} {d.strftime('%b')}"
            elif i == 0: label = "Today"
            elif i == 1: label = "Yesterday"
            else:        label = f"{d.strftime('%a')}, {d.day} {d.strftime('%b')}"
            days_result.append({"date": date_str, "label": label, "is_today": i == 0, "cards": cards_for_day})

        return JSONResponse({"days": days_result, "airport_iata": airport_iata_, "airport_name": airport_name_, "timezone": tz_name})

    @app.get("/api/recommendation")
    async def get_recommendation(user=Depends(_auth_current_user)):
        import datetime as _dt2, json as _json, pytz

        cfg_   = _cfg_for_user(user)
        store_ = cfg_.store

        airport_iata_ = (cfg_.airport_iata if cfg_ else None) or store_.load_setting("AIRPORT_CODE") or ""
        # Timezone is tied to the airport's own location — never separately
        # user-settable (WEB_TIMEZONE override removed).
        tz_name = (getattr(cfg_, 'airport_tz', None) if cfg_ else None) or "UTC"
        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = pytz.utc

        today_local = _dt2.datetime.now(tz).date()
        cache_dates = []
        day_meta = []  # (date_str, label, is_today, i)
        for i in range(-3, 8):
            d        = today_local - _dt2.timedelta(days=i)
            date_str = d.strftime("%Y-%m-%d")
            date_label = f"{d.strftime('%a')}, {d.day} {d.strftime('%b')}"
            if i == -2:  label = date_label
            elif i == -1: label = f"{date_label} – Tomorrow"
            elif i == 0:  label = f"{date_label} – Today"
            elif i == 1:  label = f"{date_label} – Yesterday"
            else:         label = date_label
            cache_dates.append(date_str)
            day_meta.append((date_str, label, i == 0, i))

        cached = store_.get_timeline_cache(cache_dates)

        # Passengers see the cached (Controller-baked) result as-is EXCEPT for the
        # Already-Photographed-Limit gate, which is re-patched off below since
        # Passengers have no catalog of their own to evaluate it against — that's
        # their explicitly confirmed design otherwise ("inherits the Controller's
        # settings"). A Pilot gets a full independent re-cluster: their own
        # exclusion list, their own algorithm settings, their own catalog — never
        # merged with the Controller's, and an empty Pilot exclusion list means
        # nothing is excluded for them even if the Controller has excluded that
        # registration.
        _viewer_catalog = app.state.get_user_catalog(user) if user.role in ("controller", "pilot") else None

        days_result = []
        for date_str, label, is_today, i in day_meta:
            row = cached.get(date_str)
            if not row:
                # No cache yet (first startup or no flights) — skip future days, show empty past
                if i < 0:
                    continue
                days_result.append({
                    "date": date_str, "label": label, "is_today": is_today, "is_tomorrow": i == -1,
                    "event_count": 0, "total_regs": 0,
                    "weather_code": 0, "weather_severe": False,
                    "temp_max": None, "temp_min": None,
                    "sunrise_ts": 0, "sunset_ts": 0,
                    "clusters": [],
                })
                continue

            try:
                clusters = _json.loads(row["clusters_json"] or "[]")
            except Exception:
                clusters = []

            sw = {}
            try:
                sw = _json.loads(row["weather_json"] or "{}")
            except Exception:
                pass

            # Future days with no flights: skip
            if i < 0 and not clusters:
                continue

            if user.role == "pilot":
                raw_events = None
                try:
                    raw_events = _json.loads(row["events_json"]) if row["events_json"] else None
                except Exception:
                    raw_events = None
                if raw_events is not None:
                    with store_._connect() as _pc:
                        clusters = _recluster_for_pilot(
                            raw_events, sw.get("sunrise_ts", 0), sw.get("sunset_ts", 0), tz,
                            _pc, user.user_id, _viewer_catalog, airport_iata_,
                        )
                else:
                    # Cache row predates events_json (written before the next monitor
                    # cycle runs) — best-effort fallback: just re-patch the spotted
                    # gate on the Controller-baked clusters until the next cycle.
                    with store_._connect() as _sc:
                        _max_spot = int(_pilot_setting(_sc, user.user_id, "SPOT_MAX_SPOTTED", "0") or 0)
                    _repatch_spotted_gate(clusters, _viewer_catalog, _max_spot, airport_iata_)
            elif user.role == "passenger":
                # Passengers have no catalog concept at all (_viewer_catalog is
                # already None above), so the Already-Photographed-Limit gate
                # has nothing to evaluate against for them — re-patch with
                # catalog=None to force spotted=0 for every flight, which
                # disables the spotted-count gate while still preserving the
                # Controller-baked lighting-based qualification untouched.
                # Otherwise a Passenger would inherit whatever the Controller's
                # own catalog counts happened to exclude at cache-build time.
                _repatch_spotted_gate(clusters, None, 0, airport_iata_)

            q_regs = {f["registration"] for c in clusters for f in c.get("flights", []) if f.get("qualifying")}
            days_result.append({
                "date": date_str, "label": label, "is_today": is_today, "is_tomorrow": i == -1,
                "event_count":    len(q_regs),
                "total_regs":     len(q_regs),
                "weather_code":   sw.get("weather_code", 0),
                "weather_severe": sw.get("weather_severe", False),
                "temp_max":       sw.get("temp_max"),
                "temp_min":       sw.get("temp_min"),
                "sunrise_ts":     sw.get("sunrise_ts", 0),
                "sunset_ts":      sw.get("sunset_ts", 0),
                "clusters":       clusters,
            })

        return JSONResponse({"days": days_result, "airport_iata": airport_iata_, "timezone": tz_name})

    # ── Collection tab — Lightroom catalog statistics ──────────────────────────

    import re as _re

    def _col_aircraft_manufacturer(type_name: str) -> str:
        if not type_name: return ''
        t = type_name.upper().strip()
        if _re.match(r'^AW\d{3}', t) or _re.match(r'^AB[- ]?\d{2,3}', t) or _re.match(r'^A1[0-3]\d\b', t): return 'Leonardo'
        if _re.search(r'\b(WILDCAT|LYNX|MERLIN)\b', t) or _re.match(r'^EH-101', t): return 'Leonardo'
        if _re.match(r'^A\d{3}', t): return 'Airbus'
        if _re.match(r'^7\d{2}', t) or 'DREAMLINER' in t: return 'Boeing'
        if _re.match(r'^(ERJ|E1[679]\d|E[23]\d{2}|190-|170-|175-|195-)', t): return 'Embraer'
        if _re.match(r'^(CRJ|BD-|Q[234]\d{2})', t) or 'LEARJET' in t: return 'Bombardier'
        if 'DASH 8' in t or t.startswith('DHC'): return 'De Havilland'
        if t.startswith('ATR') or _re.match(r'^(42|72)-\d', t): return 'ATR'
        if _re.match(r'^(MD|DC)-', t): return 'McDonnell Douglas'
        if _re.match(r'^(C-17|C-32|C-40|B-17|B-29|B-52|B-1\b|F-15|F/?A-18|CF-188|KC-135|KC-46|E-3\b|E-4\b|E-6\b|P-8)', t): return 'Boeing'
        if _re.match(r'^(AH-64|CH-47)', t): return 'Boeing'
        if _re.match(r'^(C-130|C-5(?!\d)|P-3|S-3\b|F-16|F-22|F-35|U-2|SR-71|TR-1)', t): return 'Lockheed Martin'
        if 'HERCULES' in t: return 'Lockheed Martin'
        if _re.match(r'^340[A-Z]?\b', t) or 'SAAB' in t: return 'Saab'
        if _re.search(r'\bHAWK\b', t) or _re.match(r'^BAE?\s', t) or _re.match(r'^146\b|^RJ[0-9]', t): return 'BAE Systems'
        if _re.match(r'^(EC\s?\d{3}|H[1-4]\d{2}|AS\s?\d{3}|MRH-?90)', t): return 'Airbus Helicopters'
        if _re.match(r'^(UH-60|MH-60|SH-60|HH-60|CH-53|S-9\d)', t) or 'SEAHAWK' in t: return 'Sikorsky'
        if _re.match(r'^(UH-1|AH-1|OH-58|V-22)', t) or t.startswith('BELL '): return 'Bell'
        if _re.match(r'^TBM\s*[789]\d{2}', t) or _re.match(r'^TB-?\s*\d{2}\b', t): return 'Daher'
        if _re.match(r'^(S-2[A-Z]?\b|TBF|TBM|B-2\b|F-14|E-2\b|C-2\b|EA-6|T-38|F-5\b)', t): return 'Northrop Grumman'
        if _re.match(r'^C919|^ARJ21', t) or 'COMAC' in t: return 'COMAC'
        if _re.match(r'^C\d{3}\b', t) or _re.search(r'CESSNA|CITATION|CARAVAN', t): return 'Cessna'
        if _re.match(r'^B[0-9]|KING AIR|BEECH', t): return 'Beechcraft'
        if _re.match(r'^G[5-8]\d{2}\b', t) or 'GULFSTREAM' in t: return 'Gulfstream'
        if _re.match(r'^PC-', t): return 'Pilatus'
        if 'FALCON' in t or 'MIRAGE' in t or 'RAFALE' in t: return 'Dassault'
        if _re.match(r'^F(27|28|50|70|100)\b', t) or 'FOKKER' in t: return 'Fokker'
        if _re.match(r'^PA-\d', t) or 'PIPER' in t: return 'Piper'
        if _re.match(r'^AN-\d', t) or 'ANTONOV' in t: return 'Antonov'
        if _re.match(r'^P-51|^B-25|^F-86|MUSTANG', t): return 'North American'
        return ''

    def _col_aircraft_family(type_name: str) -> str:
        if not type_name: return type_name
        t = type_name.strip()
        m = _re.match(r'^(A\d{3})[-\s]', t)
        if m: return m.group(1)
        m = _re.match(r'^(\d{3})-', t)
        if m: return m.group(1)
        m = _re.match(r'^([A-Z]{1,3}-\d+)', t)
        if m: return m.group(1)
        m = _re.match(r'^(E\d{3})[-\s]', t)
        if m: return m.group(1)
        m = _re.match(r'^([A-Za-z]+\s+\d+)', t)
        if m: return m.group(1)
        return t

    def _col_group_families(type_counts, limit=50, type_mfr=None, type_full_names=None):
        import collections as _col2
        totals = _col2.defaultdict(int)
        mfr: dict = {}
        full_name_map: dict = {}
        for name, cnt in (type_counts or []):
            fam = _col_aircraft_family(name or '') or name
            totals[fam] += cnt
            if fam not in mfr and name:
                _cat_mfr = (type_mfr or {}).get(name) or ''
                if _cat_mfr and (any(c.isdigit() for c in _cat_mfr)
                                 or _cat_mfr.upper() == name.upper()
                                 or _cat_mfr.upper() == fam.upper()):
                    _cat_mfr = ''
                mfr[fam] = _cat_mfr
            if fam not in full_name_map and name:
                full_name_map[fam] = (type_full_names or {}).get(name.upper(), '')
        top = sorted(totals.items(), key=lambda x: -x[1])[:limit]
        return [{'name': fam, 'full_name': full_name_map.get(fam, ''),
                 'manufacturer': mfr.get(fam, ''), 'photos': cnt} for fam, cnt in top]

    _col_airports_cache = {}
    _col_airports_icao_cache = {}

    def _col_load_airports():
        if not _col_airports_cache:
            import airportsdata
            _col_airports_cache.update(airportsdata.load('IATA'))
        return _col_airports_cache

    def _col_load_airports_icao():
        if not _col_airports_icao_cache:
            import airportsdata
            _col_airports_icao_cache.update(airportsdata.load('ICAO'))
        return _col_airports_icao_cache

    _COL_AIRPORT_OVERRIDES = {
        'NZWG': ('NZ', 'Wigram Aerodrome'),
        '12 Apostles Heliport': ('AU', '12 Apostles Heliport'),
    }
    _col_fr24_fetch_in_progress: set = set()

    def _col_country_flag(cc: str) -> str:
        if not cc or len(cc) != 2: return ''
        return chr(0x1F1E6 + ord(cc[0].upper()) - ord('A')) + \
               chr(0x1F1E6 + ord(cc[1].upper()) - ord('A'))

    def _col_fetch_airport_fr24(code: str) -> None:
        """Background FR24 fetch for an unknown airport — stores result in DB."""
        if code in _col_fr24_fetch_in_progress:
            return
        _col_fr24_fetch_in_progress.add(code)
        def _fetch():
            try:
                cfg_ = app.state.cfg
                if not cfg_ or not cfg_.fr_api:
                    return
                airport = cfg_.fr_api.get_airport(code)
                cc = getattr(airport, 'country_code', '') or ''
                name = getattr(airport, 'name', '') or ''
                if name:
                    app.state.store.upsert_airport(code, name, cc, source='fr24')
                    log.info("Airport cache: fetched %s → %s (%s)", code, name, cc)
            except Exception as exc:
                log.debug("Airport FR24 fetch failed for %s: %s", code, exc)
            finally:
                _col_fr24_fetch_in_progress.discard(code)
        import threading as _threading
        _threading.Thread(target=_fetch, daemon=True).start()

    def _col_airport_flag_and_name(code: str):
        if not code: return '', code
        if code in _COL_AIRPORT_OVERRIDES:
            cc, name = _COL_AIRPORT_OVERRIDES[code]
            return _col_country_flag(cc), name
        # DB first (covers both airportsdata seed and FR24-fetched data)
        try:
            info = app.state.store.get_airport_info(code)
            if info:
                return _col_country_flag(info[1]), info[0]
        except Exception:
            pass
        # Not in DB — trigger background FR24 fetch for next time
        if len(code) in (3, 4):
            _col_fetch_airport_fr24(code.upper())
        return '', code

    _COL_AIRLINE_COUNTRY = {
        'Qantas': 'AU', 'Jetstar Airways': 'AU', 'Jetstar': 'AU',
        'Virgin Australia Airlines': 'AU', 'Virgin Australia Regional Airlines': 'AU',
        'Virgin Australia': 'AU', 'Regional Express (REX)': 'AU', 'Rex Airlines': 'AU',
        'Alliance Airlines': 'AU', 'QantasLink': 'AU', 'Qantas Freight': 'AU',
        'Air New Zealand': 'NZ', 'Jetstar New Zealand': 'NZ', 'Air Chathams': 'NZ',
        'Cathay Pacific Airways': 'HK', 'Cathay Pacific': 'HK', 'Cathay Pacific Cargo': 'HK',
        'Hong Kong Airlines': 'HK', 'Hong Kong Express': 'HK', 'Greater Bay Airlines': 'HK',
        'Singapore Airlines': 'SG', 'Scoot': 'SG', 'SilkAir': 'SG',
        'Japan Airlines (JAL)': 'JP', 'Japan Airlines': 'JP', 'JAL': 'JP',
        'All Nippon Airways (ANA)': 'JP', 'All Nippon Airways': 'JP', 'ANA': 'JP',
        'Peach': 'JP', 'Zipair': 'JP',
        'Korean Air': 'KR', 'Asiana Airlines': 'KR', 'Jeju Air': 'KR',
        'Air China': 'CN', 'China Eastern Airlines': 'CN', 'China Southern Airlines': 'CN',
        'Hainan Airlines': 'CN', 'Sichuan Airlines': 'CN', 'Xiamen Airlines': 'CN',
        'China Airlines': 'TW', 'EVA Air': 'TW', 'Starlux Airlines': 'TW',
        'Malaysia Airlines': 'MY', 'AirAsia': 'MY', 'AirAsia X': 'MY', 'Batik Air Malaysia': 'MY',
        'Thai Airways International': 'TH', 'Thai Airways': 'TH', 'Bangkok Airways': 'TH',
        'Garuda Indonesia': 'ID', 'Lion Air': 'ID', 'Batik Air': 'ID', 'Citilink': 'ID',
        'Philippine Airlines': 'PH', 'Cebu Pacific Air': 'PH', 'Cebu Pacific': 'PH',
        'Vietnam Airlines': 'VN', 'VietJet Air': 'VN', 'Bamboo Airways': 'VN',
        'Air India': 'IN', 'IndiGo': 'IN', 'Vistara': 'IN',
        'SriLankan Airlines': 'LK', 'Nepal Airlines': 'NP', 'Royal Brunei Airlines': 'BN',
        'Fiji Airways': 'FJ', 'Air Niugini': 'PG', 'PNG Air': 'PG', 'Aircalin': 'NC',
        'Solomon Airlines': 'SB', 'Air Vanuatu': 'VU', 'Nauru Airlines': 'NR',
        'Emirates': 'AE', 'Emirates SkyCargo': 'AE', 'Etihad Airways': 'AE',
        'Qatar Airways': 'QA', 'Gulf Air': 'BH', 'Oman Air': 'OM', 'Air Arabia': 'AE',
        'flydubai': 'AE', 'FlyDubai': 'AE', 'Turkish Airlines': 'TR',
        'British Airways': 'GB', 'Virgin Atlantic Airways': 'GB', 'Virgin Atlantic': 'GB',
        'easyJet': 'GB', 'easyJet UK': 'GB', 'Jet2': 'GB', 'Jet2.com': 'GB',
        'Lufthansa': 'DE', 'Lufthansa Cargo': 'DE', 'Eurowings': 'DE', 'Condor': 'DE',
        'Air France': 'FR', 'Air France Cargo': 'FR', 'Transavia France': 'FR',
        'KLM Royal Dutch Airlines': 'NL', 'KLM': 'NL', 'KLM Cityhopper': 'NL',
        'Swiss International Air Lines': 'CH', 'Swiss': 'CH', 'Edelweiss Air': 'CH',
        'Austrian Airlines': 'AT', 'Iberia': 'ES', 'Vueling': 'ES', 'Air Europa': 'ES',
        'ITA Airways': 'IT', 'Alitalia': 'IT', 'Neos': 'IT',
        'Scandinavian Airlines (SAS)': 'SE', 'SAS': 'SE', 'Norwegian': 'NO',
        'Finnair': 'FI', 'LOT Polish Airlines': 'PL', 'Wizz Air': 'HU',
        'Ryanair': 'IE', 'Aer Lingus': 'IE', 'TAP Air Portugal': 'PT', 'Icelandair': 'IS',
        'United Airlines': 'US', 'American Airlines': 'US', 'Delta Air Lines': 'US', 'Delta': 'US',
        'Alaska Airlines': 'US', 'JetBlue': 'US', 'JetBlue Airways': 'US',
        'Southwest Airlines': 'US', 'Hawaiian Airlines': 'US', 'Spirit Airlines': 'US',
        'FedEx': 'US', 'FedEx Express': 'US', 'United Parcel Service (UPS)': 'US',
        'UPS Airlines': 'US', 'Atlas Air': 'US', 'US Air Force': 'US', 'NASA': 'US',
        'Air Canada': 'CA', 'WestJet': 'CA', 'Air Transat': 'CA', 'Porter Airlines': 'CA',
        'LATAM Airlines': 'CL', 'Azul Brazilian Airlines': 'BR', 'GOL Linhas Aéreas': 'BR',
        'Avianca': 'CO', 'Aeromexico': 'MX', 'Copa Airlines': 'PA',
        'South African Airways': 'ZA', 'Ethiopian Airlines': 'ET', 'EgyptAir': 'EG',
        'Kenya Airways': 'KE', 'Air Mauritius': 'MU',
        'Royal Australian Air Force': 'AU', 'RAAF': 'AU',
        'Royal Australian Navy': 'AU',
    }

    _COL_PLUGIN = 'ch.aviationphoto.aircraftmetadata'
    # Keyed by user_id ('controller' sentinel for the no-user background pass) —
    # catalogs are private per user, so a single shared cache would leak one
    # user's stats to everyone.
    _col_stats_cache: dict = {}

    def _resolve_catalog_path(user=None):
        """Catalogs are private per user, never a shared/airport-wide default.
        Background tasks with no specific viewer (fleet photo refresh, the
        periodic collection-stats warm cache) fall back to the Controller's
        own catalog — same "ground truth" convention used by monitor.py's
        clustering pass."""
        if user is not None:
            cat_obj = app.state.get_user_catalog(user)
            return str(cat_obj._path) if cat_obj else None
        return app.state.control_store.get_controller_catalog_path()

    def _col_catalog_path(user=None):
        return _resolve_catalog_path(user)

    def _catalog_path_for_owner(owner_user_id: str):
        """Same per-owner catalog resolution as _resolve_catalog_path, but keyed
        by owner_user_id string directly — for background fleet-card refresh
        tasks, which have no full request-time user object, only the owner id
        the cards are stored under."""
        if owner_user_id == "controller":
            return app.state.control_store.get_controller_catalog_path()
        row = app.state.control_store.get_user(owner_user_id)
        return row["catalog_path"] if row else None

    def _col_stats_ttl_secs():
        try:
            return int(app.state.store.load_setting('CHECK_INTERVAL_MINUTES') or 30) * 60
        except Exception:
            return 1800

    def _col_compute_stats(user=None):
        """Compute catalog stats for one user (or the Controller, if user is
        None — the background/no-viewer case) and store in that user's cache
        slot. Returns the data dict."""
        import time as _time, system_status as _ss
        key = user.user_id if user is not None else 'controller'
        slot = _col_stats_cache.setdefault(key, {'data': None, 'ts': 0})
        try:
            data = _col_stats_sync(user)
            slot['data'] = data
            slot['ts'] = _time.time()
            _ss.record_task('collection_stats', True)
            return data
        except Exception as _e:
            _ss.record_task('collection_stats', False, str(_e))
            return slot.get('data')

    def _col_start_bg_refresh():
        import threading, time as _time
        def _loop():
            while True:
                ttl = _col_stats_ttl_secs()
                _time.sleep(ttl)
                try:
                    # Refresh every catalog-having user's own cache slot, not
                    # just one shared one — a Pilot's Collection tab must
                    # reflect their own catalog's stats, not the Controller's.
                    for u in app.state.control_store.list_users():
                        if u["role"] in ("controller", "pilot") and u["catalog_path"]:
                            from auth import UserCtx
                            _col_compute_stats(UserCtx(u["user_id"], u["username"], u["role"], None))
                    log.info("Collection stats cache refreshed (periodic)")
                except Exception as e:
                    log.warning("Collection stats bg refresh failed: %s", e)
        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    def _col_stats_sync(user=None):
        """Run all catalog queries and return the stats dict. Called from cache layer."""
        from pathlib import Path as _Path
        from datetime import datetime as _dt, date as _date
        import sqlite3 as _sq
        cat_str = _col_catalog_path(user)
        if not cat_str:
            raise ValueError("No Lightroom catalog configured")
        cat = _Path(cat_str)
        if not cat.exists():
            raise ValueError(f"Catalog not found: {cat}")

        def prop_counts(con, key, limit=None):
            sql = """
                SELECT prop.internalValue, COUNT(*) AS cnt
                FROM AgSearchablePhotoProperty prop
                JOIN AgPhotoPropertySpec spec ON spec.id_local = prop.propertySpec
                WHERE spec.key = ? AND spec.sourcePlugin = ?
                  AND prop.internalValue IS NOT NULL AND prop.internalValue != ''
                GROUP BY prop.internalValue ORDER BY cnt DESC
            """
            if limit: sql += f' LIMIT {int(limit)}'
            return con.execute(sql, (key, _COL_PLUGIN)).fetchall()

        try:
            con = _sq.connect(str(cat))
            total_photos   = con.execute("SELECT COUNT(*) FROM Adobe_images").fetchone()[0]
            total_aircraft = con.execute("""SELECT COUNT(DISTINCT prop.internalValue)
                FROM AgSearchablePhotoProperty prop JOIN AgPhotoPropertySpec spec ON spec.id_local = prop.propertySpec
                WHERE spec.key='registration' AND spec.sourcePlugin=?""", (_COL_PLUGIN,)).fetchone()[0]
            total_airlines = con.execute("""SELECT COUNT(DISTINCT prop.internalValue)
                FROM AgSearchablePhotoProperty prop JOIN AgPhotoPropertySpec spec ON spec.id_local = prop.propertySpec
                WHERE spec.key='airline' AND spec.sourcePlugin=?""", (_COL_PLUGIN,)).fetchone()[0]
            total_airports = con.execute("""SELECT COUNT(DISTINCT prop.internalValue)
                FROM AgSearchablePhotoProperty prop JOIN AgPhotoPropertySpec spec ON spec.id_local = prop.propertySpec
                WHERE spec.key='airport_iata' AND spec.sourcePlugin=?""", (_COL_PLUGIN,)).fetchone()[0]

            raw_sessions = con.execute("""
                SELECT DATE(img.captureTime) AS session_date,
                       ap.internalValue AS airport,
                       COUNT(DISTINCT img.id_local) AS photos,
                       COUNT(DISTINCT CASE WHEN reg_spec.id_local IS NOT NULL THEN reg.internalValue END) AS aircraft
                FROM Adobe_images img
                JOIN AgSearchablePhotoProperty ap ON ap.photo = img.id_local
                JOIN AgPhotoPropertySpec ap_spec ON ap_spec.id_local = ap.propertySpec
                    AND ap_spec.key = 'airport_iata' AND ap_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty reg ON reg.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec reg_spec ON reg_spec.id_local = reg.propertySpec
                    AND reg_spec.key = 'registration' AND reg_spec.sourcePlugin = ?
                WHERE ap.internalValue IS NOT NULL AND ap.internalValue != ''
                GROUP BY session_date, airport ORDER BY session_date DESC
            """, (_COL_PLUGIN, _COL_PLUGIN)).fetchall()

            sessions, last_session = [], None
            for date_str, airport, photos, aircraft in raw_sessions:
                try:
                    d = _dt.strptime(date_str, '%Y-%m-%d')
                    date_label = d.strftime('%d %b %Y')
                except Exception:
                    d, date_label = None, date_str or '?'
                flag, airport_name = _col_airport_flag_and_name(airport or '')
                prefix = f"{flag} " if flag else ''
                sessions.append({'airport_name': airport_name or airport or '', 'flag': flag,
                                 'date_label': date_label, 'photos': photos, 'aircraft': aircraft,
                                 'date': date_str, 'airport': airport or ''})
                if last_session is None and d:
                    days_ago = (_date.today() - d.date()).days
                    last_session = {'date_label': date_label, 'date': date_str, 'airport': airport or '',
                                    'airport_name': airport_name, 'flag': flag, 'days_ago': days_ago}

            top_airlines = prop_counts(con, 'airline', 100)
            top_airports = prop_counts(con, 'airport_iata', None)
            _typ_spec_id = con.execute(
                "SELECT id_local FROM AgPhotoPropertySpec WHERE key='aircraft_type' AND sourcePlugin=?",
                (_COL_PLUGIN,)).fetchone()
            _mfr_spec_id = con.execute(
                "SELECT id_local FROM AgPhotoPropertySpec WHERE key='aircraft_manufacturer' AND sourcePlugin=?",
                (_COL_PLUGIN,)).fetchone()
            all_types_raw = con.execute("""
                SELECT typ.internalValue AS aircraft_type,
                       mfr.internalValue AS manufacturer,
                       COUNT(*) AS cnt
                FROM AgSearchablePhotoProperty typ
                LEFT JOIN AgSearchablePhotoProperty mfr
                    ON mfr.photo = typ.photo AND mfr.propertySpec = ?
                WHERE typ.propertySpec = ?
                  AND typ.internalValue IS NOT NULL AND typ.internalValue != ''
                GROUP BY typ.internalValue, mfr.internalValue
                ORDER BY typ.internalValue, cnt DESC
            """, (_mfr_spec_id[0] if _mfr_spec_id else -1,
                  _typ_spec_id[0] if _typ_spec_id else -1)).fetchall()
            # Build (type, count) for grouping + a lookup of type → most common manufacturer
            _type_mfr: dict = {}
            _type_cnt: dict = {}
            for row in all_types_raw:
                t, m, c = row[0], row[1] or '', row[2]
                _type_cnt[t] = _type_cnt.get(t, 0) + c
                if t not in _type_mfr and m:
                    _type_mfr[t] = m
            all_types = list(_type_cnt.items())
            top_aircraft = prop_counts(con, 'registration', 15)

            _rego_base = """
                SELECT reg.internalValue,
                       {metric},
                       MAX(CASE WHEN al_spec.id_local  IS NOT NULL THEN airline.internalValue END),
                       MAX(CASE WHEN typ_spec.id_local IS NOT NULL THEN atype.internalValue  END)
                FROM Adobe_images img
                JOIN AgSearchablePhotoProperty reg ON reg.photo = img.id_local
                JOIN AgPhotoPropertySpec reg_spec ON reg_spec.id_local = reg.propertySpec
                    AND reg_spec.key = 'registration' AND reg_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty airline ON airline.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec al_spec ON al_spec.id_local = airline.propertySpec
                    AND al_spec.key = 'airline' AND al_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty atype ON atype.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec typ_spec ON typ_spec.id_local = atype.propertySpec
                    AND typ_spec.key = 'aircraft_type' AND typ_spec.sourcePlugin = ?
                {extra_join}
                WHERE reg.internalValue IS NOT NULL AND reg.internalValue != ''
                GROUP BY reg.internalValue ORDER BY 2 DESC LIMIT 50"""
            top_photos_rego = con.execute(
                _rego_base.replace('{metric}', 'COUNT(DISTINCT img.id_local) AS metric')
                          .replace('{extra_join}', ''), (_COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN)).fetchall()
            top_sessions_rego = con.execute(
                _rego_base.replace('{metric}',
                    "COUNT(DISTINCT DATE(img.captureTime)||'|'||COALESCE(ap.airport,'')) AS metric")
                          .replace('{extra_join}', """
                    LEFT JOIN (SELECT ap2.photo, ap2.internalValue AS airport
                        FROM AgSearchablePhotoProperty ap2
                        JOIN AgPhotoPropertySpec aps ON aps.id_local = ap2.propertySpec
                            AND aps.key = 'airport_iata' AND aps.sourcePlugin = ?
                    ) ap ON ap.photo = img.id_local"""),
                (_COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN)).fetchall()

            raw_hoppers = con.execute("""
                SELECT reg.internalValue,
                       MAX(CASE WHEN al_spec.id_local  IS NOT NULL THEN airline.internalValue END),
                       MAX(CASE WHEN typ_spec.id_local IS NOT NULL THEN atype.internalValue  END),
                       COUNT(DISTINCT ap.internalValue), COUNT(DISTINCT img.id_local),
                       GROUP_CONCAT(DISTINCT ap.internalValue)
                FROM Adobe_images img
                JOIN AgSearchablePhotoProperty reg ON reg.photo = img.id_local
                JOIN AgPhotoPropertySpec reg_spec ON reg_spec.id_local = reg.propertySpec
                    AND reg_spec.key = 'registration' AND reg_spec.sourcePlugin = ?
                JOIN AgSearchablePhotoProperty ap ON ap.photo = img.id_local
                JOIN AgPhotoPropertySpec ap_spec ON ap_spec.id_local = ap.propertySpec
                    AND ap_spec.key = 'airport_iata' AND ap_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty airline ON airline.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec al_spec ON al_spec.id_local = airline.propertySpec
                    AND al_spec.key = 'airline' AND al_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty atype ON atype.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec typ_spec ON typ_spec.id_local = atype.propertySpec
                    AND typ_spec.key = 'aircraft_type' AND typ_spec.sourcePlugin = ?
                WHERE reg.internalValue IS NOT NULL AND ap.internalValue IS NOT NULL
                GROUP BY reg.internalValue HAVING COUNT(DISTINCT ap.internalValue) >= 2
                ORDER BY 4 DESC, 5 DESC LIMIT 50
            """, (_COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN)).fetchall()
            con.close()
        except Exception as e:
            raise HTTPException(500, str(e))

        airports_out = []
        for iata, cnt in top_airports:
            flag, full_name = _col_airport_flag_and_name(iata or '')
            airports_out.append({'iata': iata or '', 'flag': flag, 'full_name': full_name, 'photos': cnt})

        airlines_out = []
        for name, cnt in top_airlines:
            airlines_out.append({'name': name or '', 'raw_name': name, 'photos': cnt})

        _hopper_type_names = app.state.store.get_aircraft_type_names(
            list({r[2] for r in raw_hoppers if r[2]})
        )
        hoppers_out = []
        for reg, airline, atype, ap_count, photos, airports_csv in raw_hoppers:
            chips = []
            for iata in (airports_csv or '').split(','):
                iata = iata.strip()
                if iata:
                    flag, _ = _col_airport_flag_and_name(iata)
                    chips.append({'iata': iata, 'flag': flag})
            cat_mfr = _type_mfr.get(atype or '', '')
            if cat_mfr and (any(c.isdigit() for c in cat_mfr) or cat_mfr.upper() == (atype or '').upper()):
                cat_mfr = ''
            hoppers_out.append({
                'reg': reg, 'airline': airline or '', 'aircraft_type': atype or '',
                'aircraft_type_name': _hopper_type_names.get((atype or '').upper(), ''),
                'manufacturer': cat_mfr,
                'airport_count': ap_count, 'photos': photos, 'airports': chips,
            })

        _rego_type_names = app.state.store.get_aircraft_type_names(
            list({r[3] for r in list(top_photos_rego) + list(top_sessions_rego) if r[3]})
        )
        def _rego_rows(raw, metric_key):
            rows = []
            for r in raw:
                atype = r[3] or ''
                cat_mfr = ''
                try:
                    # Read manufacturer from catalog via _type_mfr (already computed)
                    cat_mfr = _type_mfr.get(atype, '')
                    if cat_mfr and (any(c.isdigit() for c in cat_mfr)
                                    or cat_mfr.upper() == atype.upper()):
                        cat_mfr = ''
                except Exception:
                    pass
                rows.append({
                    'reg': r[0], metric_key: r[1],
                    'airline': r[2] or '',
                    'aircraft_type': atype,
                    'aircraft_type_name': _rego_type_names.get(atype.upper(), ''),
                    'manufacturer': cat_mfr,
                })
            return rows

        # Keyword stat boxes — this viewer's own choice if they've set one,
        # else the Controller's, via the standard settings-inheritance
        # precedence (PER_USER_GLOBAL_SETTINGS keeps it identical across every
        # airport for a given user, so any airport's store works here).
        kw_stats = []
        try:
            _kw_con = _sq.connect(str(cat))
            _kw_store = _cfg_for_user(user).store if user is not None else app.state.store
            _kw_owner = _owner_id(user) if user is not None else "controller"
            with _kw_store._connect() as _kw_settings_conn:
                for _i in range(1, 4):
                    _kw = _pilot_setting(_kw_settings_conn, _kw_owner, f'COLLECTION_KW_STAT_{_i}', '') or ''
                    if _kw:
                        try:
                            _cnt = _kw_con.execute("""
                                SELECT COUNT(DISTINCT reg.internalValue)
                                FROM Adobe_images img
                                JOIN AgSearchablePhotoProperty reg ON reg.photo = img.id_local
                                JOIN AgPhotoPropertySpec reg_spec ON reg_spec.id_local = reg.propertySpec
                                    AND reg_spec.key = 'registration' AND reg_spec.sourcePlugin = ?
                                JOIN AgLibraryKeywordImage ki ON ki.image = img.id_local
                                JOIN AgLibraryKeyword kw ON kw.id_local = ki.tag AND kw.name = ?
                            """, (_COL_PLUGIN, _kw)).fetchone()[0]
                        except Exception:
                            _cnt = 0
                        kw_stats.append({'keyword': _kw, 'count': _cnt})
                    else:
                        kw_stats.append({'keyword': '', 'count': 0})
            _kw_con.close()
        except Exception:
            kw_stats = [{'keyword': '', 'count': 0}] * 3

        return {
            'total_photos': total_photos, 'total_aircraft': total_aircraft,
            'total_airlines': total_airlines, 'total_airports': total_airports,
            'sessions': sessions, 'last_session': last_session,
            'top_airlines': airlines_out, 'top_airports': airports_out,
            'top_types': _col_group_families(all_types, 30, _type_mfr,
                             app.state.store.get_aircraft_type_names(list(_type_cnt.keys()))),
            'top_aircraft': [{'reg': r[0], 'photos': r[1]} for r in top_aircraft],
            'airport_hoppers': hoppers_out,
            'most_photos_rego': _rego_rows(top_photos_rego, 'photos'),
            'most_sessions_rego': _rego_rows(top_sessions_rego, 'sessions'),
            'kw_stats': kw_stats,
        }

    @app.get("/api/catalog-stats")
    async def get_catalog_stats(force: bool = False, user=Depends(_auth_require_role("controller", "pilot"))):
        import time as _time
        import asyncio
        if not _col_catalog_path(user):
            return JSONResponse({"no_catalog": True})
        slot = _col_stats_cache.get(user.user_id, {'data': None, 'ts': 0})
        if not force:
            cached = slot.get('data')
            if cached is not None and (_time.time() - slot['ts']) < _col_stats_ttl_secs():
                return JSONResponse(cached)
        # Compute in thread pool so we don't block the event loop
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, _col_compute_stats, user)
        except Exception as e:
            raise HTTPException(500, str(e))
        if data is None:
            raise HTTPException(500, "Failed to compute stats")
        return JSONResponse(data)

    @app.get("/api/catalog-stats/airline")
    async def get_catalog_airline_details(airline: str = "", user=Depends(_auth_current_user)):
        from pathlib import Path as _Path
        import sqlite3 as _sq
        cat_str = _col_catalog_path(user)
        if not cat_str or not airline: return JSONResponse({'airports': [], 'types': []})
        cat = _Path(cat_str)
        if not cat.exists(): return JSONResponse({'airports': [], 'types': []})
        try:
            con = _sq.connect(str(cat))
            top_airports = con.execute("""
                SELECT ap.internalValue, COUNT(DISTINCT img.id_local) AS cnt
                FROM Adobe_images img
                JOIN AgSearchablePhotoProperty al ON al.photo = img.id_local
                JOIN AgPhotoPropertySpec al_spec ON al_spec.id_local = al.propertySpec
                    AND al_spec.key = 'airline' AND al_spec.sourcePlugin = ?
                JOIN AgSearchablePhotoProperty ap ON ap.photo = img.id_local
                JOIN AgPhotoPropertySpec ap_spec ON ap_spec.id_local = ap.propertySpec
                    AND ap_spec.key = 'airport_iata' AND ap_spec.sourcePlugin = ?
                WHERE al.internalValue = ? GROUP BY ap.internalValue ORDER BY cnt DESC LIMIT 5
            """, (_COL_PLUGIN, _COL_PLUGIN, airline)).fetchall()
            top_types = con.execute("""
                SELECT typ.internalValue,
                       MAX(CASE WHEN mfr_spec.id_local IS NOT NULL THEN mfr.internalValue END),
                       COUNT(DISTINCT img.id_local) AS cnt
                FROM Adobe_images img
                JOIN AgSearchablePhotoProperty al ON al.photo = img.id_local
                JOIN AgPhotoPropertySpec al_spec ON al_spec.id_local = al.propertySpec
                    AND al_spec.key = 'airline' AND al_spec.sourcePlugin = ?
                JOIN AgSearchablePhotoProperty typ ON typ.photo = img.id_local
                JOIN AgPhotoPropertySpec typ_spec ON typ_spec.id_local = typ.propertySpec
                    AND typ_spec.key = 'aircraft_type' AND typ_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty mfr ON mfr.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec mfr_spec ON mfr_spec.id_local = mfr.propertySpec
                    AND mfr_spec.key = 'aircraft_manufacturer' AND mfr_spec.sourcePlugin = ?
                WHERE al.internalValue = ? GROUP BY typ.internalValue ORDER BY cnt DESC LIMIT 5
            """, (_COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN, airline)).fetchall()
            con.close()
        except Exception: return JSONResponse({'airports': [], 'types': []})
        airports_out = []
        for iata, cnt in top_airports:
            flag, full_name = _col_airport_flag_and_name(iata or '')
            airports_out.append({'iata': iata, 'flag': flag, 'full_name': full_name, 'photos': cnt})
        return JSONResponse({'airports': airports_out,
            'types': [{'name': r[0], 'manufacturer': r[1] or '', 'photos': r[2]} for r in top_types]})

    @app.get("/api/catalog-stats/airport")
    async def get_catalog_airport_details(airport: str = "", user=Depends(_auth_current_user)):
        from pathlib import Path as _Path
        import sqlite3 as _sq
        cat_str = _col_catalog_path(user)
        if not cat_str or not airport: return JSONResponse({'airlines': [], 'types': []})
        cat = _Path(cat_str)
        if not cat.exists(): return JSONResponse({'airlines': [], 'types': []})
        try:
            con = _sq.connect(str(cat))
            top_airlines = con.execute("""
                SELECT al.internalValue, COUNT(DISTINCT img.id_local) AS cnt
                FROM Adobe_images img
                JOIN AgSearchablePhotoProperty ap ON ap.photo = img.id_local
                JOIN AgPhotoPropertySpec ap_spec ON ap_spec.id_local = ap.propertySpec
                    AND ap_spec.key = 'airport_iata' AND ap_spec.sourcePlugin = ?
                JOIN AgSearchablePhotoProperty al ON al.photo = img.id_local
                JOIN AgPhotoPropertySpec al_spec ON al_spec.id_local = al.propertySpec
                    AND al_spec.key = 'airline' AND al_spec.sourcePlugin = ?
                WHERE ap.internalValue = ? GROUP BY al.internalValue ORDER BY cnt DESC LIMIT 5
            """, (_COL_PLUGIN, _COL_PLUGIN, airport)).fetchall()
            top_types = con.execute("""
                SELECT typ.internalValue,
                       MAX(CASE WHEN mfr_spec.id_local IS NOT NULL THEN mfr.internalValue END),
                       COUNT(DISTINCT img.id_local) AS cnt
                FROM Adobe_images img
                JOIN AgSearchablePhotoProperty ap ON ap.photo = img.id_local
                JOIN AgPhotoPropertySpec ap_spec ON ap_spec.id_local = ap.propertySpec
                    AND ap_spec.key = 'airport_iata' AND ap_spec.sourcePlugin = ?
                JOIN AgSearchablePhotoProperty typ ON typ.photo = img.id_local
                JOIN AgPhotoPropertySpec typ_spec ON typ_spec.id_local = typ.propertySpec
                    AND typ_spec.key = 'aircraft_type' AND typ_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty mfr ON mfr.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec mfr_spec ON mfr_spec.id_local = mfr.propertySpec
                    AND mfr_spec.key = 'aircraft_manufacturer' AND mfr_spec.sourcePlugin = ?
                WHERE ap.internalValue = ? GROUP BY typ.internalValue ORDER BY cnt DESC LIMIT 5
            """, (_COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN, airport)).fetchall()
            con.close()
        except Exception: return JSONResponse({'airlines': [], 'types': []})
        airlines_out = []
        for name, cnt in top_airlines:
            cc = _COL_AIRLINE_COUNTRY.get(name or '', '')
            flag = (_col_country_flag(cc) + ' ') if cc else ''
            airlines_out.append({'name': name, 'flag': flag, 'photos': cnt})
        return JSONResponse({'airlines': airlines_out,
            'types': [{'name': r[0], 'manufacturer': r[1] or '', 'photos': r[2]} for r in top_types]})

    @app.get("/api/catalog-stats/type")
    async def get_catalog_type_details(family: str = "", user=Depends(_auth_current_user)):
        from pathlib import Path as _Path
        import sqlite3 as _sq
        cat_str = _col_catalog_path(user)
        if not cat_str or not family: return JSONResponse({'airlines': [], 'airports': []})
        cat = _Path(cat_str)
        if not cat.exists(): return JSONResponse({'airlines': [], 'airports': []})
        try:
            con = _sq.connect(str(cat))
            all_type_rows = con.execute("""
                SELECT DISTINCT prop.internalValue FROM AgSearchablePhotoProperty prop
                JOIN AgPhotoPropertySpec spec ON spec.id_local = prop.propertySpec
                    AND spec.key = 'aircraft_type' AND spec.sourcePlugin = ?
                WHERE prop.internalValue IS NOT NULL AND prop.internalValue != ''
            """, (_COL_PLUGIN,)).fetchall()
            family_types = [r[0] for r in all_type_rows if _col_aircraft_family(r[0] or '') == family]
            if not family_types:
                con.close()
                return JSONResponse({'airlines': [], 'airports': []})
            ph = ','.join('?' * len(family_types))
            top_airlines = con.execute(f"""
                SELECT al.internalValue, COUNT(DISTINCT img.id_local) AS cnt
                FROM Adobe_images img
                JOIN AgSearchablePhotoProperty typ ON typ.photo = img.id_local
                JOIN AgPhotoPropertySpec typ_spec ON typ_spec.id_local = typ.propertySpec
                    AND typ_spec.key = 'aircraft_type' AND typ_spec.sourcePlugin = ?
                JOIN AgSearchablePhotoProperty al ON al.photo = img.id_local
                JOIN AgPhotoPropertySpec al_spec ON al_spec.id_local = al.propertySpec
                    AND al_spec.key = 'airline' AND al_spec.sourcePlugin = ?
                WHERE typ.internalValue IN ({ph})
                GROUP BY al.internalValue ORDER BY cnt DESC LIMIT 5
            """, (_COL_PLUGIN, _COL_PLUGIN, *family_types)).fetchall()
            top_airports = con.execute(f"""
                SELECT ap.internalValue, COUNT(DISTINCT img.id_local) AS cnt
                FROM Adobe_images img
                JOIN AgSearchablePhotoProperty typ ON typ.photo = img.id_local
                JOIN AgPhotoPropertySpec typ_spec ON typ_spec.id_local = typ.propertySpec
                    AND typ_spec.key = 'aircraft_type' AND typ_spec.sourcePlugin = ?
                JOIN AgSearchablePhotoProperty ap ON ap.photo = img.id_local
                JOIN AgPhotoPropertySpec ap_spec ON ap_spec.id_local = ap.propertySpec
                    AND ap_spec.key = 'airport_iata' AND ap_spec.sourcePlugin = ?
                WHERE typ.internalValue IN ({ph})
                GROUP BY ap.internalValue ORDER BY cnt DESC LIMIT 5
            """, (_COL_PLUGIN, _COL_PLUGIN, *family_types)).fetchall()
            con.close()
        except Exception: return JSONResponse({'airlines': [], 'airports': []})
        airlines_out = []
        for name, cnt in top_airlines:
            cc = _COL_AIRLINE_COUNTRY.get(name or '', '')
            flag = (_col_country_flag(cc) + ' ') if cc else ''
            airlines_out.append({'name': name, 'flag': flag, 'photos': cnt})
        airports_out = []
        for iata, cnt in top_airports:
            flag, full_name = _col_airport_flag_and_name(iata or '')
            airports_out.append({'iata': iata, 'flag': flag, 'full_name': full_name, 'photos': cnt})
        return JSONResponse({'airlines': airlines_out, 'airports': airports_out})

    @app.get("/api/catalog-stats/rego")
    async def get_catalog_rego_sessions(rego: str = "", user=Depends(_auth_current_user)):
        from pathlib import Path as _Path
        import sqlite3 as _sq
        cat_str = _col_catalog_path(user)
        if not cat_str or not rego: return JSONResponse({'sessions': []})
        cat = _Path(cat_str)
        if not cat.exists(): return JSONResponse({'sessions': []})
        try:
            con = _sq.connect(str(cat))
            rows = con.execute("""
                SELECT DATE(img.captureTime), ap.internalValue, COUNT(DISTINCT img.id_local),
                       GROUP_CONCAT(DISTINCT kw.name)
                FROM Adobe_images img
                JOIN AgSearchablePhotoProperty reg ON reg.photo = img.id_local
                JOIN AgPhotoPropertySpec reg_spec ON reg_spec.id_local = reg.propertySpec
                    AND reg_spec.key = 'registration' AND reg_spec.sourcePlugin = ?
                JOIN AgSearchablePhotoProperty ap ON ap.photo = img.id_local
                JOIN AgPhotoPropertySpec ap_spec ON ap_spec.id_local = ap.propertySpec
                    AND ap_spec.key = 'airport_iata' AND ap_spec.sourcePlugin = ?
                LEFT JOIN AgLibraryKeywordImage ki ON ki.image = img.id_local
                LEFT JOIN AgLibraryKeyword kw ON kw.id_local = ki.tag AND kw.name IS NOT NULL
                WHERE reg.internalValue = ? AND ap.internalValue IS NOT NULL
                GROUP BY DATE(img.captureTime), ap.internalValue
                ORDER BY DATE(img.captureTime) DESC
            """, (_COL_PLUGIN, _COL_PLUGIN, rego)).fetchall()
            con.close()
        except Exception: return JSONResponse({'sessions': []})
        sessions = []
        for date_str, iata, photos, kw_csv in rows:
            flag, full_name = _col_airport_flag_and_name(iata or '')
            tags = [t.strip() for t in (kw_csv or '').split(',') if t.strip()]
            sessions.append({'date': date_str, 'iata': iata, 'flag': flag, 'photos': photos, 'tags': tags})
        return JSONResponse({'sessions': sessions})

    # Controller-only session-photo preview — read-only by construction: the
    # photos mount below is :ro at the Docker level (see docker-compose.yml),
    # this code path only ever reads files and writes into a SEPARATE
    # generated-thumbnail cache dir, and there is no endpoint anywhere that
    # writes to, renames, or deletes anything under either path. The RAW
    # originals on the NAS Photo share are never modified or exposed directly
    # — only a downscaled JPEG extracted from each RAW's embedded preview.
    _PHOTOS_ROOT_DEFAULT = "/app/photos"

    def _session_photos_root() -> Path:
        """Container-internal path the operator has bind-mounted their photo
        folder to — configurable via Settings > Collection > Session Photos
        Path (SESSION_PHOTOS_PATH) rather than hardcoded, since that mount
        point is something the operator chooses in their own
        docker-compose.yml, not something this app controls. Read fresh each
        call (cheap single-row lookup) rather than cached at startup, so a
        Controller changing it takes effect immediately, no restart."""
        try:
            with app.state.store._connect() as _conn:
                row = _conn.execute("SELECT value FROM settings WHERE key='SESSION_PHOTOS_PATH'").fetchone()
            path = (row[0] if row else "").strip()
        except Exception:
            path = ""
        return Path(path or _PHOTOS_ROOT_DEFAULT)

    # Every AgLibraryRootFolder.absolutePath in the catalog is recorded with
    # this literal SMB-style prefix (Lightroom itself always wrote it this
    # way, regardless of what actually reads it) — stripping it and rejoining
    # under _session_photos_root() maps a catalog-recorded path onto the
    # read-only bind mount without hardcoding each year/region subfolder name.
    _PHOTOS_CATALOG_PREFIX = "//192.168.4.100/Photo/Plane Spotting/"
    _SESSION_THUMB_DIR = Path(__file__).parent / "static" / "session_thumbs"

    def _pick_session_photo(user, rego: str, iata: str, date: str):
        """Pick ONE photo for this rego+session: whichever has the 'Featured'
        Lightroom keyword if any photo in the session has it, else a random
        photo from the session — random among ties either way, so repeat
        views of a session with multiple Featured photos (or none at all)
        don't always show the same one. Also returns the session's aggregate
        metadata (airline/type/manufacturer/notes/tags — same fields the
        Collection session-detail view shows) so the preview can display it
        alongside the photo instead of just the bare rego/airport/date."""
        cat_str = _col_catalog_path(user)
        if not cat_str or not (rego and iata and date):
            return None
        cat = Path(cat_str)
        if not cat.exists():
            return None
        import sqlite3 as _sq3
        con = _sq3.connect(f"file:{cat}?mode=ro", uri=True)
        try:
            rego_u, iata_u = rego.strip().upper(), iata.strip().upper()
            pick_row = con.execute(
                """
                SELECT img.id_local
                FROM AgSearchablePhotoProperty reg
                JOIN Adobe_images img ON img.id_local = reg.photo
                JOIN AgPhotoPropertySpec reg_spec ON reg_spec.id_local = reg.propertySpec
                    AND reg_spec.key = 'registration' AND reg_spec.sourcePlugin = ?
                JOIN AgSearchablePhotoProperty ap ON ap.photo = img.id_local
                JOIN AgPhotoPropertySpec ap_spec ON ap_spec.id_local = ap.propertySpec
                    AND ap_spec.key = 'airport_iata' AND ap_spec.sourcePlugin = ?
                LEFT JOIN AgLibraryKeywordImage ki ON ki.image = img.id_local
                LEFT JOIN AgLibraryKeyword kw ON kw.id_local = ki.tag AND kw.name = 'Featured'
                WHERE UPPER(TRIM(reg.internalValue)) = ?
                  AND UPPER(TRIM(ap.internalValue)) = ?
                  AND DATE(img.captureTime) = ?
                ORDER BY (kw.id_local IS NOT NULL) DESC, RANDOM()
                LIMIT 1
                """,
                (_COL_PLUGIN, _COL_PLUGIN, rego_u, iata_u, date),
            ).fetchone()
            if not pick_row:
                return None
            meta_row = con.execute(
                """
                SELECT MAX(CASE WHEN al_spec.id_local  IS NOT NULL THEN al.internalValue  END),
                       MAX(CASE WHEN typ_spec.id_local IS NOT NULL THEN typ.internalValue END),
                       MAX(CASE WHEN mfr_spec.id_local IS NOT NULL THEN mfr.internalValue END),
                       MAX(CASE WHEN nt_spec.id_local  IS NOT NULL THEN nt.internalValue  END),
                       GROUP_CONCAT(DISTINCT kw.name)
                FROM AgSearchablePhotoProperty reg
                JOIN Adobe_images img ON img.id_local = reg.photo
                JOIN AgPhotoPropertySpec reg_spec ON reg_spec.id_local = reg.propertySpec
                    AND reg_spec.key = 'registration' AND reg_spec.sourcePlugin = ?
                JOIN AgSearchablePhotoProperty ap ON ap.photo = img.id_local
                JOIN AgPhotoPropertySpec ap_spec ON ap_spec.id_local = ap.propertySpec
                    AND ap_spec.key = 'airport_iata' AND ap_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty al ON al.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec al_spec ON al_spec.id_local = al.propertySpec
                    AND al_spec.key = 'airline' AND al_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty typ ON typ.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec typ_spec ON typ_spec.id_local = typ.propertySpec
                    AND typ_spec.key = 'aircraft_type' AND typ_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty mfr ON mfr.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec mfr_spec ON mfr_spec.id_local = mfr.propertySpec
                    AND mfr_spec.key = 'aircraft_manufacturer' AND mfr_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty nt ON nt.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec nt_spec ON nt_spec.id_local = nt.propertySpec
                    AND nt_spec.key = 'aircraft_notes' AND nt_spec.sourcePlugin = ?
                LEFT JOIN AgLibraryKeywordImage ki ON ki.image = img.id_local
                LEFT JOIN AgLibraryKeyword kw ON kw.id_local = ki.tag AND kw.name IS NOT NULL
                    AND kw.name NOT IN ('Featured','SPTA','AircraftMetadata-RegNotFound','AircraftMetadata-WrongReg','Cleaned')
                WHERE UPPER(TRIM(reg.internalValue)) = ?
                  AND UPPER(TRIM(ap.internalValue)) = ?
                  AND DATE(img.captureTime) = ?
                """,
                (_COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN,
                 rego_u, iata_u, date),
            ).fetchone()
        finally:
            con.close()
        airline, atype, mfr, notes, kw_csv = meta_row or (None, None, None, None, None)
        tags = sorted([t.strip() for t in (kw_csv or '').split(',') if t.strip()])
        airport_flag, airport_name = _col_airport_flag_and_name(iata_u)
        return {
            'id': pick_row[0],
            'registration': rego_u, 'airport': iata_u, 'date': date,
            'airport_flag': airport_flag, 'airport_name': airport_name,
            'airline': airline or '', 'aircraft_type': atype or '',
            'manufacturer': mfr or '', 'notes': notes or '', 'tags': tags,
        }

    def _session_photo_local_path(row: dict):
        root_path = row.get("root_path") or ""
        if not root_path.startswith(_PHOTOS_CATALOG_PREFIX):
            return None
        year_region = root_path[len(_PHOTOS_CATALOG_PREFIX):]
        return _session_photos_root() / year_region / (row.get("rel_path") or "") / f"{row['base_name']}.{row['ext']}"

    @app.get("/api/session-photo-pick")
    async def get_session_photo_pick(rego: str = "", iata: str = "", date: str = "",
                                      user=Depends(_auth_require_role("controller"))):
        import asyncio
        picked = await asyncio.to_thread(_pick_session_photo, user, rego, iata, date)
        if not picked:
            return JSONResponse({"id": None})
        return JSONResponse(picked)

    @app.get("/api/session-photo-thumb/{photo_id}")
    async def get_session_photo_thumb(photo_id: int, user=Depends(_auth_require_role("controller"))):
        import asyncio
        cache_path = _SESSION_THUMB_DIR / f"{photo_id}.jpg"
        if cache_path.exists():
            return FileResponse(str(cache_path), media_type="image/jpeg",
                                 headers={"Cache-Control": "private, max-age=2592000"})

        def _extract():
            cat_str = _col_catalog_path(user)
            if not cat_str:
                return None
            cat = Path(cat_str)
            if not cat.exists():
                return None
            import sqlite3 as _sq3
            con = _sq3.connect(f"file:{cat}?mode=ro", uri=True)
            con.row_factory = _sq3.Row
            try:
                row = con.execute(
                    """
                    SELECT img.id_local AS id, root.absolutePath AS root_path,
                           folder.pathFromRoot AS rel_path,
                           file.baseName AS base_name, file.extension AS ext
                    FROM Adobe_images img
                    JOIN AgLibraryFile file ON file.id_local = img.rootFile
                    JOIN AgLibraryFolder folder ON folder.id_local = file.folder
                    JOIN AgLibraryRootFolder root ON root.id_local = folder.rootFolder
                    WHERE img.id_local = ?
                    """,
                    (photo_id,),
                ).fetchone()
            finally:
                con.close()
            if not row:
                return None
            src_path = _session_photo_local_path(dict(row))
            if not src_path or not src_path.exists():
                return None

            import subprocess as _sp
            from PIL import Image as _Img, ImageOps as _ImgOps
            import io as _io2
            # Different cameras/firmware populate different embedded-image
            # tags at different sizes — e.g. one camera's PreviewImage might
            # be a small 640px thumbnail while its JpgFromRaw is a full-size
            # JPEG, and the reverse is true for another camera. Rather than
            # guessing a fixed priority order, pull every candidate that
            # exists and keep whichever decodes to the most pixels, so the
            # preview is always the best the RAW file actually has to offer.
            preview_bytes = None
            best_pixels = 0
            for tag in ("-PreviewImage", "-JpgFromRaw", "-OtherImage", "-ThumbnailImage"):
                try:
                    result = _sp.run(
                        ["exiftool", "-b", tag, str(src_path)],
                        capture_output=True, timeout=30,
                    )
                    if result.returncode != 0 or len(result.stdout) <= 1000:
                        continue
                    probe = _Img.open(_io2.BytesIO(result.stdout))
                    pixels = probe.size[0] * probe.size[1]
                    if pixels > best_pixels:
                        best_pixels = pixels
                        preview_bytes = result.stdout
                except Exception:
                    continue
            if not preview_bytes:
                return None

            img = _Img.open(_io2.BytesIO(preview_bytes))
            img = _ImgOps.exif_transpose(img).convert("RGB")
            img.thumbnail((1920, 1920))
            _SESSION_THUMB_DIR.mkdir(parents=True, exist_ok=True)
            img.save(str(cache_path), "JPEG", quality=88)
            return cache_path

        result_path = await asyncio.to_thread(_extract)
        if not result_path:
            raise HTTPException(404, "Photo not available")
        return FileResponse(str(result_path), media_type="image/jpeg",
                             headers={"Cache-Control": "private, max-age=2592000"})

    @app.get("/api/catalog-stats/tags")
    async def get_catalog_session_tag_list(user=Depends(_auth_current_user)):
        from pathlib import Path as _Path
        import sqlite3 as _sq
        cat_str = _col_catalog_path(user)
        if not cat_str: return JSONResponse({'tags': []})
        cat = _Path(cat_str)
        if not cat.exists(): return JSONResponse({'tags': []})
        try:
            con = _sq.connect(str(cat))
            rows = con.execute("""
                SELECT DISTINCT kw.name FROM AgLibraryKeyword kw
                JOIN AgLibraryKeywordImage ki ON ki.tag = kw.id_local
                WHERE kw.name IS NOT NULL AND kw.name != ''
                ORDER BY kw.name
            """).fetchall()
            con.close()
        except Exception:
            return JSONResponse({'tags': []})
        return JSONResponse({'tags': [r[0] for r in rows]})

    @app.get("/api/catalog-stats/session")
    async def get_catalog_session_aircraft(date: str = "", airport: str = "", filter_tags: str = "",
                                            user=Depends(_auth_current_user)):
        from pathlib import Path as _Path
        import sqlite3 as _sq
        cat_str = _col_catalog_path(user)
        if not cat_str or not date or not airport: return JSONResponse({'aircraft': []})
        cat = _Path(cat_str)
        if not cat.exists(): return JSONResponse({'aircraft': []})
        PRIORITY  = {'Special Livery': 0, 'Military': 1}
        filter_set = set(t.strip() for t in filter_tags.split(',') if t.strip()) if filter_tags else None
        try:
            con = _sq.connect(str(cat))
            rows = con.execute("""
                SELECT reg.internalValue,
                       MAX(CASE WHEN al_spec.id_local  IS NOT NULL THEN al.internalValue  END),
                       MAX(CASE WHEN typ_spec.id_local IS NOT NULL THEN typ.internalValue END),
                       MAX(CASE WHEN mfr_spec.id_local IS NOT NULL THEN mfr.internalValue END),
                       MAX(CASE WHEN nt_spec.id_local  IS NOT NULL THEN nt.internalValue  END),
                       GROUP_CONCAT(DISTINCT kw.name)
                FROM Adobe_images img
                JOIN AgSearchablePhotoProperty reg ON reg.photo = img.id_local
                JOIN AgPhotoPropertySpec reg_spec ON reg_spec.id_local = reg.propertySpec
                    AND reg_spec.key = 'registration' AND reg_spec.sourcePlugin = ?
                JOIN AgSearchablePhotoProperty ap ON ap.photo = img.id_local
                JOIN AgPhotoPropertySpec ap_spec ON ap_spec.id_local = ap.propertySpec
                    AND ap_spec.key = 'airport_iata' AND ap_spec.sourcePlugin = ?
                JOIN AgLibraryKeywordImage ki ON ki.image = img.id_local
                JOIN AgLibraryKeyword kw ON kw.id_local = ki.tag AND kw.name IS NOT NULL
                LEFT JOIN AgSearchablePhotoProperty al ON al.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec al_spec ON al_spec.id_local = al.propertySpec
                    AND al_spec.key = 'airline' AND al_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty typ ON typ.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec typ_spec ON typ_spec.id_local = typ.propertySpec
                    AND typ_spec.key = 'aircraft_type' AND typ_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty mfr ON mfr.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec mfr_spec ON mfr_spec.id_local = mfr.propertySpec
                    AND mfr_spec.key = 'aircraft_manufacturer' AND mfr_spec.sourcePlugin = ?
                LEFT JOIN AgSearchablePhotoProperty nt ON nt.photo = img.id_local
                LEFT JOIN AgPhotoPropertySpec nt_spec ON nt_spec.id_local = nt.propertySpec
                    AND nt_spec.key = 'aircraft_notes' AND nt_spec.sourcePlugin = ?
                WHERE DATE(img.captureTime) = ? AND ap.internalValue = ?
                  AND reg.internalValue IS NOT NULL
                GROUP BY reg.internalValue
            """, (_COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN, _COL_PLUGIN, date, airport)).fetchall()
            con.close()
        except Exception: return JSONResponse({'aircraft': []})
        aircraft = []
        for reg, airline, atype, manufacturer, notes, kw_csv in rows:
            tags = sorted([t.strip() for t in (kw_csv or '').split(',') if t.strip()],
                          key=lambda t: (PRIORITY.get(t, 99), t))
            if not tags: continue
            if filter_set and not any(t in filter_set for t in tags): continue
            aircraft.append({'reg': reg, 'airline': airline or '', 'aircraft_type': atype or '',
                             'manufacturer': manufacturer or '', 'notes': notes or '',
                             'tags': tags, 'priority': min((PRIORITY.get(t, 99) for t in tags), default=99)})
        aircraft.sort(key=lambda a: (a['priority'], a['reg']))
        return JSONResponse({'aircraft': aircraft})

    @app.get("/api/stats")
    async def get_stats():
        store = app.state.store
        notif_stats = store.get_notification_stats()
        return JSONResponse(notif_stats)

    @app.post("/api/aircraft-types/refresh")
    async def refresh_aircraft_types():
        import threading as _thr

        def _refresh_and_fan_out():
            # Only app.state.store actually hits GitHub — every other watched
            # airport's DB starts with zero aircraft_types rows and never gets
            # its own refresh, so copy the freshly-updated table into each of
            # them afterward instead of re-fetching the same CSV N times.
            app.state.store.refresh_icao_type_list(force=True)
            with app.state.store._connect() as conn:
                rows = conn.execute("SELECT icao, name, source, manufacturer FROM aircraft_types").fetchall()
            rows_bulk = [(r["icao"], r["name"], r["source"], r["manufacturer"]) for r in rows]
            for iata, cfg in app.state.cfgs.items():
                if cfg.store is app.state.store:
                    continue
                cfg.store.upsert_aircraft_types_bulk(rows_bulk)

        _thr.Thread(target=_refresh_and_fan_out, daemon=True).start()
        return JSONResponse({'ok': True, 'message': 'Refresh started in background'})

    @app.get("/api/aircraft-types")
    async def list_aircraft_types(user=Depends(_auth_current_user)):
        with _cfg_for_user(user).store._connect() as conn:
            rows = conn.execute(
                "SELECT icao, name FROM aircraft_types WHERE source='user' ORDER BY icao"
            ).fetchall()
        return JSONResponse([{'icao': r[0], 'name': r[1]} for r in rows])

    # ── Search endpoints ──────────────────────────────────────────────────────
    _SEARCH_PLUGIN = 'ch.aviationphoto.aircraftmetadata'
    _SEARCH_SKIP_KW = {'Featured', 'SPTA', 'AircraftMetadata-RegNotFound', 'AircraftMetadata-WrongReg', 'Cleaned'}

    def _search_catalog_path(user=None):
        """NOTE: this previously referenced a bare `cfg` name that no longer
        exists in this scope (a leftover from before create_app's cfg param
        was renamed to cfgs for multi-airport support) — every call site was
        silently raising NameError since that refactor. Fixed alongside the
        per-user catalog rework."""
        return _resolve_catalog_path(user)

    def _search_mfr(type_str: str) -> str:
        from monitor import _derive_manufacturer
        return _derive_manufacturer(type_str or '') or ''

    def _search_family(type_str: str) -> str:
        """Group aircraft types into families (e.g. B738, B739 → B737)."""
        t = (type_str or '').strip().upper()
        if not t:
            return ''
        # ICAO type codes: first 3-4 chars usually identify the family
        if t.startswith('B7'):
            return t[:3] + '0' if len(t) >= 3 else t
        if t.startswith('A3') or t.startswith('A2'):
            return t[:3] + '0' if len(t) >= 3 else t
        return t[:3] if len(t) >= 3 else t

    # Each entry: (display_label, [variants to match case-insensitively])
    _ALLIANCE_LIVERIES = [
        ('Oneworld Livery',      ['Oneworld Livery']),
        ('Star Alliance Livery', ['Star Alliance Livery']),
        ('SkyTeam Livery',       ['Skyteam Livery', 'Sky Team Livery', 'SkyTeam Livery']),
    ]

    @app.get("/api/collection/livery-stats")
    async def collection_livery_stats(user=Depends(_auth_current_user)):
        cat_path = _search_catalog_path(user)
        if not cat_path or not _os.path.exists(cat_path):
            return JSONResponse({'alliances': []})
        PLUGIN = _SEARCH_PLUGIN
        try:
            import sqlite3 as _sq
            con = _sq.connect(f"file:{cat_path}?mode=ro", uri=True)
            results = []
            for label, variants in _ALLIANCE_LIVERIES:
                placeholders = ','.join('?' * len(variants))
                row = con.execute(f"""
                    SELECT COUNT(DISTINCT reg.internalValue) AS cnt
                    FROM AgSearchablePhotoProperty reg
                    JOIN AgPhotoPropertySpec reg_spec ON reg_spec.id_local = reg.propertySpec
                        AND reg_spec.key = 'registration' AND reg_spec.sourcePlugin = ?
                    JOIN AgSearchablePhotoProperty nt ON nt.photo = reg.photo
                    JOIN AgPhotoPropertySpec nt_spec ON nt_spec.id_local = nt.propertySpec
                        AND nt_spec.key = 'aircraft_notes' AND nt_spec.sourcePlugin = ?
                    WHERE LOWER(TRIM(nt.internalValue)) IN ({placeholders})
                      AND reg.internalValue IS NOT NULL AND reg.internalValue != ''
                """, [PLUGIN, PLUGIN] + [v.lower() for v in variants]).fetchone()
                results.append({'livery': label, 'count': row[0] if row else 0})
            con.close()
        except Exception as exc:
            log.warning("livery stats error: %s", exc)
            return JSONResponse({'alliances': []})
        return JSONResponse({'alliances': results})

    @app.get("/api/search/flight-filters")
    async def search_flight_filters(user=Depends(_auth_current_user)):
        store_ = _cfg_for_user(user).store
        with store_._connect() as conn:
            mfrs = [r[0] for r in conn.execute(
                "SELECT DISTINCT manufacturer FROM rego_sightings WHERE manufacturer IS NOT NULL AND manufacturer != '' ORDER BY manufacturer"
            ).fetchall()]
            airlines = [r[0] for r in conn.execute(
                "SELECT DISTINCT airline FROM rego_sightings WHERE airline IS NOT NULL AND airline != '' ORDER BY airline"
            ).fetchall()]
            types = [r[0] for r in conn.execute(
                "SELECT DISTINCT aircraft_type FROM rego_sightings WHERE aircraft_type IS NOT NULL AND aircraft_type != '' ORDER BY aircraft_type"
            ).fetchall()]
        return JSONResponse({'manufacturers': mfrs, 'airlines': airlines, 'types': types})

    @app.get("/api/search/flights")
    async def search_flights(rego: str = "", user=Depends(_auth_current_user)):
        rego = rego.strip().upper()
        cfg_ = _cfg_for_user(user)
        store_ = cfg_.store
        airport_iata_ = cfg_.airport_iata if cfg_ else ""
        pat = f'%{rego}%' if rego else '%'
        with store_._connect() as conn:
            rows = conn.execute("""
                SELECT fe.registration, fe.flight_number, fe.arrival_ts,
                       fe.origin_iata, fe.origin_name, fe.current_status, fe.detail,
                       fe.extra_info, fe.notif_types, fe.airline_icao,
                       fe.aircraft_type, fe.rare_absence_days,
                       fd.dep_flight, fd.dep_ts, fd.dep_dest_iata, fd.dep_dest_name,
                       a.manufacturer, sh.last_seen_ts,
                       ac.country_code AS origin_country_code
                FROM flight_arrivals fe
                LEFT JOIN flight_departures fd ON fd.arrival_id = fe.id
                LEFT JOIN airframes a ON a.registration = fe.registration
                LEFT JOIN rego_sightings sh ON sh.registration = fe.registration
                LEFT JOIN airports ac ON ac.iata = fe.origin_iata
                WHERE UPPER(TRIM(fe.registration)) LIKE ?
                  AND fe.registration NOT IN (SELECT registration FROM filter_exclusions WHERE owner_user_id = ?)
                ORDER BY fe.arrival_ts DESC
            """, (pat, _owner_id(user))).fetchall()
            _livery_excl = _viewer_livery_exclude_keywords(conn, user)
            _rare_min_days = _viewer_rare_plane_min_days(conn, user)
            _watchlist_sets = _viewer_watchlist_sets(store_, user)

            # Regos seen at airport but never filter-matched
            sighting_rows = conn.execute("""
                SELECT registration, last_seen_ts, manufacturer, airline, aircraft_type, airline_icao
                FROM rego_sightings
                WHERE UPPER(TRIM(registration)) LIKE ?
                ORDER BY last_seen_ts DESC
            """, (pat,)).fetchall()

        results = []
        for row in rows:
            try:
                nt = _json.loads(row["notif_types"] or "[]")
            except Exception:
                nt = []
            nt = _strip_excluded_livery_tag(nt, row["extra_info"], _livery_excl)
            nt = _resolve_rare_plane_tag(nt, row["rare_absence_days"], _rare_min_days)
            nt = _strip_unowned_watchlist_tags(
                nt, row["registration"], row["aircraft_type"], row["airline_icao"], _watchlist_sets)
            # No remaining reason for this viewer to care about this flight —
            # same "drop it, don't just de-badge it" rule as /api/feed.
            if not nt:
                continue
            results.append({
                'registration':  row["registration"],
                'flight_number': row["flight_number"] or '',
                'arrival_ts':    row["arrival_ts"],
                'origin_iata':   row["origin_iata"] or '',
                'origin_name':   row["origin_name"] or '',
                'current_status':row["current_status"] or '',
                'detail':        row["detail"] or '',
                'extra_info':    row["extra_info"] or '',
                'notif_types':   nt,
                'dep_flight':    row["dep_flight"] or '',
                'dep_ts':        row["dep_ts"],
                'dep_dest_iata': row["dep_dest_iata"] or '',
                'dep_dest_name': row["dep_dest_name"] or '',
                'manufacturer':        row["manufacturer"] or '',
                'last_seen_ts':        row["last_seen_ts"],
                'origin_country_code': row["origin_country_code"] or '',
                'airline_icao':        row["airline_icao"] or '',
            })

        # Built from the POST-filter results, not the raw rows — a registration
        # whose only match got dropped above (nothing left after this viewer's
        # own exclude keywords/thresholds) should fall through to sighting_only
        # instead of vanishing from search results entirely.
        matched_regs = {r['registration'].upper() for r in results}

        sighting_only = [
            {
                'registration':  r["registration"],
                'last_seen_ts':  r["last_seen_ts"],
                'manufacturer':  r["manufacturer"] or '',
                'airline':       r["airline"] or '',
                'aircraft_type': r["aircraft_type"] or '',
                'airline_icao':  r["airline_icao"] or '',
            }
            for r in sighting_rows
            if r["registration"].upper() not in matched_regs
        ]

        return JSONResponse({'results': results, 'sighting_only': sighting_only})

    @app.get("/api/search/route-filters")
    async def search_route_filters(user=Depends(_auth_current_user)):
        store_ = _cfg_for_user(user).store
        import re as _re2
        with store_._connect() as conn:
            # Origins: flight_arrivals (historical) UNION route_type_tracker (future)
            origin_rows = conn.execute("""
                SELECT iata, name FROM (
                    SELECT fe.origin_iata AS iata, fe.origin_name AS name
                    FROM flight_arrivals fe WHERE fe.origin_iata != '' AND fe.origin_iata IS NOT NULL
                    UNION
                    SELECT rth.origin_iata AS iata, ac.name AS name
                    FROM route_type_tracker rth
                    LEFT JOIN airports ac ON ac.iata = rth.origin_iata
                    WHERE rth.origin_iata IS NOT NULL AND rth.origin_iata != ''
                ) GROUP BY iata ORDER BY iata
            """).fetchall()
            # Dests: flight_departures (historical) UNION route_type_tracker (future)
            dest_rows = conn.execute("""
                SELECT iata, name FROM (
                    SELECT fd.dep_dest_iata AS iata, fd.dep_dest_name AS name
                    FROM flight_departures fd
                    JOIN flight_arrivals fe ON fe.id = fd.arrival_id
                    WHERE fd.dep_dest_iata != '' AND fd.dep_dest_iata IS NOT NULL
                    UNION
                    SELECT rth.dest_iata AS iata, ac.name AS name
                    FROM route_type_tracker rth
                    LEFT JOIN airports ac ON ac.iata = rth.dest_iata
                    WHERE rth.dest_iata IS NOT NULL AND rth.dest_iata != ''
                ) GROUP BY iata ORDER BY iata
            """).fetchall()
            airline_rows = conn.execute("""
                SELECT name FROM (
                    SELECT DISTINCT rth.airline AS name
                    FROM route_type_tracker rth WHERE rth.airline IS NOT NULL AND rth.airline != ''
                    UNION
                    SELECT DISTINCT fe.detail AS name
                    FROM flight_arrivals fe WHERE fe.detail != '' AND fe.detail IS NOT NULL
                )
            """).fetchall()
        def _short_airport_name(name):
            n = _re2.sub(r'\s*\bInternational\b', '', name or '', flags=_re2.IGNORECASE)
            n = _re2.sub(r'\s*\bAirports?\b', '', n, flags=_re2.IGNORECASE)
            return _re2.sub(r'\s+', ' ', n).strip()
        def _iata_label(iata, name):
            short = _short_airport_name(name)
            if short and short.upper() != iata.upper():
                return f"{iata} · {short}"
            return iata
        origins  = [_iata_label(r['iata'], r['name'] or '') for r in origin_rows]
        dests    = [_iata_label(r['iata'], r['name'] or '') for r in dest_rows]
        seen_airlines, airlines = set(), []
        for row in airline_rows:
            raw = row['name'] or ''
            m = _re2.match(r'^(.+?)\s*\(', raw)
            name = m.group(1).strip() if m else raw.strip()
            if name and name not in seen_airlines:
                seen_airlines.add(name)
                airlines.append(name)
        airlines.sort()
        cfg_ = _cfg_for_user(user)
        home_iata = (cfg_.airport_iata if cfg_ else None) or store_.load_setting("AIRPORT_CODE") or ""
        home_name = (cfg_.airport_name if cfg_ else None) or ""
        home_short = _short_airport_name(home_name)
        home_label = f"{home_iata} · {home_short}" if home_short and home_short.upper() != home_iata.upper() else home_iata
        return JSONResponse({'origins': origins, 'dests': dests, 'airlines': airlines, 'home': home_label})

    @app.get("/api/search/route")
    async def search_route(fn: str = "", origin: _List[str] = _Query(default=[]),
                           dest: _List[str] = _Query(default=[]), airline: _List[str] = _Query(default=[]),
                           user=Depends(_auth_current_user)):
        fn = fn.strip().upper()
        store_ = _cfg_for_user(user).store
        def _iata(label): return label.split('·')[0].strip().upper() if '·' in label else label.strip().upper()
        origins  = [_iata(v) for v in origin]
        dests    = [_iata(v) for v in dest]
        airlines = [v.strip() for v in airline]
        has_filter = fn or origins or dests or airlines
        if not has_filter:
            return JSONResponse({'results': []})
        fn_pat = f'%{fn}%' if fn else '%'
        import re as _re2
        def _airline_from_detail(d):
            m = _re2.match(r'^(.+?)\s*\(', d or '')
            return m.group(1).strip() if m else ''
        with store_._connect() as conn:
            rows = conn.execute("""
                SELECT DISTINCT rth.flight_number, rth.aircraft_type, rth.airport_iata,
                       rth.count, rth.first_seen_ts, rth.last_seen_ts,
                       rth.origin_iata, rth.dest_iata, rth.airline,
                       ac_o.name AS origin_name, ac_d.name AS dest_name,
                       ac_h.name AS airport_name
                FROM route_type_tracker rth
                LEFT JOIN airports ac_o ON ac_o.iata = rth.origin_iata
                LEFT JOIN airports ac_d ON ac_d.iata = rth.dest_iata
                LEFT JOIN airports ac_h ON ac_h.iata = rth.airport_iata
                WHERE UPPER(rth.flight_number) LIKE ?
                ORDER BY rth.count DESC, rth.last_seen_ts DESC
            """, (fn_pat,)).fetchall()

            if origins or dests or airlines:
                # Build lookup: flight_number → {origin_iatas, dest_iatas, airline_names}
                # from flight_arrivals (historical source)
                fe_rows = conn.execute("""
                    SELECT UPPER(fe.flight_number) AS fn, fe.origin_iata,
                           fd.dep_dest_iata, fe.detail
                    FROM flight_arrivals fe
                    LEFT JOIN flight_departures fd ON fd.arrival_id = fe.id
                """).fetchall()
                fe_origins  = {}  # fn → set of origin IATAs
                fe_dests    = {}  # fn → set of dest IATAs
                fe_airlines = {}  # fn → set of airline names
                for fe in fe_rows:
                    fn_key = fe['fn']
                    if fe['origin_iata']:
                        fe_origins.setdefault(fn_key, set()).add(fe['origin_iata'].upper())
                    if fe['dep_dest_iata']:
                        fe_dests.setdefault(fn_key, set()).add(fe['dep_dest_iata'].upper())
                    al = _airline_from_detail(fe['detail'])
                    if al:
                        fe_airlines.setdefault(fn_key, set()).add(al)

                filtered = []
                for r in rows:
                    fn_key = r['flight_number'].upper()
                    # Origin: check route_type_tracker first, fall back to flight_arrivals
                    if origins:
                        rth_origin = (r['origin_iata'] or '').upper()
                        fe_orig    = fe_origins.get(fn_key, set())
                        match_origin = (rth_origin and rth_origin in origins) or bool(fe_orig & set(origins))
                        if not match_origin:
                            continue
                    # Dest: route_type_tracker only — fe_dests (paired departure) must not be used
                    # because it conflates the arrival flight number with a different departure flight
                    if dests:
                        rth_dest = (r['dest_iata'] or '').upper()
                        if not (rth_dest and rth_dest in dests):
                            continue
                    # Airline: route_type_tracker.airline first, fall back to flight_arrivals
                    if airlines:
                        rth_al = (r['airline'] or '').strip()
                        fe_al  = fe_airlines.get(fn_key, set())
                        all_al = ({rth_al} if rth_al else set()) | fe_al
                        if not any(any(a.lower() in al.lower() for al in all_al) for a in airlines):
                            continue
                    filtered.append(r)
                rows = filtered
        results = [{'flight_number': r['flight_number'], 'aircraft_type': r['aircraft_type'],
                    'airport_iata': r['airport_iata'], 'airport_name': r['airport_name'] or '',
                    'count': r['count'], 'first_seen_ts': r['first_seen_ts'], 'last_seen_ts': r['last_seen_ts'],
                    'airline': r['airline'] or '', 'origin_iata': r['origin_iata'] or '',
                    'origin_name': r['origin_name'] or '', 'dest_iata': r['dest_iata'] or '',
                    'dest_name': r['dest_name'] or ''}
                   for r in rows]
        return JSONResponse({'results': results})

    def _search_airports_with_names(iatas):
        if not iatas:
            return []
        # Try DB first
        rows = {}
        try:
            ph = ','.join('?' * len(iatas))
            with app.state.store._connect() as conn:
                for r in conn.execute(
                    f"SELECT iata, name FROM airports WHERE iata IN ({ph})", iatas
                ).fetchall():
                    if r['name'] and r['name'] != r['iata']:
                        rows[r['iata']] = r['name']
        except Exception:
            pass
        # Fill gaps with airportsdata
        missing = [a for a in iatas if a not in rows]
        if missing:
            try:
                import airportsdata as _ad
                _ap = _ad.load('IATA')
                for a in missing:
                    info = _ap.get(a)
                    if info:
                        rows[a] = info.get('name', a)
            except Exception:
                pass
        return [{'iata': a, 'full_name': rows.get(a, a)} for a in iatas]

    @app.get("/api/search/autocomplete")
    async def search_autocomplete(user=Depends(_auth_current_user)):
        cat_path = _search_catalog_path(user)
        if not cat_path or not _os.path.exists(cat_path):
            return JSONResponse({'registrations': [], 'types': [], 'airlines': [], 'airports': [], 'keywords': []})
        PLUGIN = _SEARCH_PLUGIN
        try:
            import sqlite3 as _sq, collections as _coll
            con = _sq.connect(f"file:{cat_path}?mode=ro", uri=True)

            def _vals(key):
                return [r[0] for r in con.execute("""
                    SELECT DISTINCT prop.internalValue
                    FROM AgSearchablePhotoProperty prop
                    JOIN AgPhotoPropertySpec spec ON spec.id_local = prop.propertySpec
                        AND spec.key = ? AND spec.sourcePlugin = ?
                    WHERE prop.internalValue IS NOT NULL AND prop.internalValue != ''
                    ORDER BY prop.internalValue
                """, (key, PLUGIN)).fetchall() if r[0]]

            regos = _vals('registration')

            _type_rows = con.execute("""
                SELECT prop.internalValue, COUNT(*) AS cnt
                FROM AgSearchablePhotoProperty prop
                JOIN AgPhotoPropertySpec spec ON spec.id_local = prop.propertySpec
                    AND spec.key = 'aircraft_type' AND spec.sourcePlugin = ?
                WHERE prop.internalValue IS NOT NULL AND prop.internalValue != ''
                GROUP BY prop.internalValue ORDER BY cnt DESC
            """, (PLUGIN,)).fetchall()
            _top10 = [r[0] for r in _type_rows[:10]]
            _rest  = sorted(set(r[0] for r in _type_rows) - set(_top10))
            types  = [{'value': t, 'manufacturer': _search_mfr(t)} for t in _top10 + _rest]

            manufacturers = sorted([r[0] for r in con.execute("""
                SELECT DISTINCT prop.internalValue
                FROM AgSearchablePhotoProperty prop
                JOIN AgPhotoPropertySpec spec ON spec.id_local = prop.propertySpec
                    AND spec.key = 'aircraft_manufacturer' AND spec.sourcePlugin = ?
                WHERE prop.internalValue IS NOT NULL AND prop.internalValue != ''
                ORDER BY prop.internalValue
            """, (PLUGIN,)).fetchall() if r[0]])

            _airline_rows = con.execute("""
                SELECT prop.internalValue, COUNT(*) AS cnt
                FROM AgSearchablePhotoProperty prop
                JOIN AgPhotoPropertySpec spec ON spec.id_local = prop.propertySpec
                    AND spec.key = 'airline' AND spec.sourcePlugin = ?
                WHERE prop.internalValue IS NOT NULL AND prop.internalValue != ''
                GROUP BY prop.internalValue ORDER BY cnt DESC
            """, (PLUGIN,)).fetchall()
            _top20_airlines = [r[0] for r in _airline_rows[:20]]
            _rest_airlines  = sorted(set(r[0] for r in _airline_rows) - set(_top20_airlines))

            _airport_rows = con.execute("""
                SELECT prop.internalValue, COUNT(*) AS cnt
                FROM AgSearchablePhotoProperty prop
                JOIN AgPhotoPropertySpec spec ON spec.id_local = prop.propertySpec
                    AND spec.key = 'airport_iata' AND spec.sourcePlugin = ?
                WHERE prop.internalValue IS NOT NULL AND prop.internalValue != ''
                GROUP BY prop.internalValue ORDER BY cnt DESC
            """, (PLUGIN,)).fetchall()
            _top10_airports = [r[0] for r in _airport_rows[:10]]
            _rest_airports  = sorted(set(r[0] for r in _airport_rows) - set(_top10_airports))

            keywords = [r[0] for r in con.execute("""
                SELECT DISTINCT kw.name FROM AgLibraryKeyword kw
                WHERE kw.name IS NOT NULL AND kw.name != ''
                  AND EXISTS (SELECT 1 FROM AgLibraryKeywordImage ki WHERE ki.tag = kw.id_local)
                ORDER BY kw.name
            """).fetchall() if r[0] and r[0] not in _SEARCH_SKIP_KW]
            con.close()
        except Exception as exc:
            log.warning("search autocomplete error: %s", exc)
            return JSONResponse({'registrations': [], 'types': [], 'airlines': [], 'airports': [], 'keywords': []})

        return JSONResponse({
            'registrations': regos,
            'types': types, 'type_top_count': len(_top10),
            'manufacturers': manufacturers,
            'airlines': [{'value': n} for n in _top20_airlines + _rest_airlines],
            'airline_top_count': len(_top20_airlines),
            'airports': _search_airports_with_names(_top10_airports + _rest_airports),
            'airport_top_count': len(_top10_airports),
            'keywords': keywords,
        })

    @app.get("/api/search")
    async def search_catalog(
        rego:         str = "",
        airline:      _List[str] = _Query(default=[]),
        type:         _List[str] = _Query(default=[]),
        manufacturer: _List[str] = _Query(default=[]),
        airport:      _List[str] = _Query(default=[]),
        keyword:      _List[str] = _Query(default=[]),
        user=Depends(_auth_current_user),
    ):
        types         = [v for v in type         if v.strip()]
        airlines      = [v for v in airline      if v.strip()]
        manufacturers = [v for v in manufacturer if v.strip()]
        airports      = [v for v in airport      if v.strip()]
        keywords      = [v for v in keyword      if v.strip()]
        rego          = rego.strip()
        if not any([rego, types, airlines, manufacturers, airports, keywords]):
            return JSONResponse({'results': [], 'total': 0})

        cat_path = _search_catalog_path(user)
        if not cat_path or not _os.path.exists(cat_path):
            return JSONResponse({'results': [], 'total': 0, 'error': 'No catalog'})

        PLUGIN = _SEARCH_PLUGIN
        try:
            import sqlite3 as _sq
            con = _sq.connect(f"file:{cat_path}?mode=ro", uri=True)

            _prop_subq = lambda key: f"""(
                SELECT prop.photo, prop.internalValue FROM AgSearchablePhotoProperty prop
                JOIN AgPhotoPropertySpec spec ON spec.id_local = prop.propertySpec
                    AND spec.key = '{key}' AND spec.sourcePlugin = '{PLUGIN}'
            )"""

            where  = ["reg.internalValue IS NOT NULL", "reg.internalValue != ''"]
            params = [PLUGIN]

            kw_joins = ''
            for i, kw in enumerate(keywords):
                kw_joins += f"""
                JOIN AgLibraryKeywordImage ki{i} ON ki{i}.image = img.id_local
                JOIN AgLibraryKeyword kw{i} ON kw{i}.id_local = ki{i}.tag AND kw{i}.name = ?"""
                params.append(kw)

            if rego:
                where.append("UPPER(reg.internalValue) LIKE UPPER(?)")
                params.append(f'%{rego}%')
            if airlines:
                where.append(f"al.internalValue IN ({','.join('?'*len(airlines))})")
                params.extend(airlines)
            if types:
                where.append(f"typ.internalValue IN ({','.join('?'*len(types))})")
                params.extend(types)
            if airports:
                where.append(f"ap.internalValue IN ({','.join('?'*len(airports))})")
                params.extend(airports)
            mfr_join = ""
            if manufacturers:
                mfr_join = f"LEFT JOIN {_prop_subq('aircraft_manufacturer')} mfr ON mfr.photo = img.id_local"
                where.append(f"mfr.internalValue IN ({','.join('?'*len(manufacturers))})")
                params.extend(manufacturers)

            matched = con.execute(f"""
                SELECT DISTINCT reg.internalValue
                FROM Adobe_images img
                JOIN AgSearchablePhotoProperty reg ON reg.photo = img.id_local
                JOIN AgPhotoPropertySpec reg_spec ON reg_spec.id_local = reg.propertySpec
                    AND reg_spec.key = 'registration' AND reg_spec.sourcePlugin = ?
                LEFT JOIN {_prop_subq('airline')}       al  ON al.photo  = img.id_local
                LEFT JOIN {_prop_subq('aircraft_type')} typ ON typ.photo = img.id_local
                LEFT JOIN {_prop_subq('airport_iata')}  ap  ON ap.photo  = img.id_local
                {mfr_join}
                {kw_joins}
                WHERE {' AND '.join(where)}
            """, params).fetchall()
            regs = [r[0] for r in matched]

            rows = []
            if regs:
                p2 = [PLUGIN] + regs
                rows = con.execute(f"""
                    SELECT reg.internalValue,
                           MAX(al.internalValue)        AS airline,
                           MAX(typ.internalValue)       AS aircraft_type,
                           MAX(mfr.internalValue)       AS manufacturer,
                           MAX(nt.internalValue)        AS notes,
                           DATE(img.captureTime)        AS session_date,
                           ap.internalValue             AS airport,
                           COUNT(DISTINCT img.id_local) AS photos,
                           GROUP_CONCAT(DISTINCT kwdisp.name) AS keywords
                    FROM Adobe_images img
                    JOIN AgSearchablePhotoProperty reg ON reg.photo = img.id_local
                    JOIN AgPhotoPropertySpec reg_spec ON reg_spec.id_local = reg.propertySpec
                        AND reg_spec.key = 'registration' AND reg_spec.sourcePlugin = ?
                    LEFT JOIN {_prop_subq('airline')}                al  ON al.photo  = img.id_local
                    LEFT JOIN {_prop_subq('aircraft_type')}          typ ON typ.photo = img.id_local
                    LEFT JOIN {_prop_subq('aircraft_manufacturer')}  mfr ON mfr.photo = img.id_local
                    LEFT JOIN {_prop_subq('aircraft_notes')}         nt  ON nt.photo  = img.id_local
                    LEFT JOIN {_prop_subq('airport_iata')}           ap  ON ap.photo  = img.id_local
                    LEFT JOIN AgLibraryKeywordImage kwall ON kwall.image = img.id_local
                    LEFT JOIN AgLibraryKeyword kwdisp ON kwdisp.id_local = kwall.tag
                        AND kwdisp.name NOT IN ('Featured','SPTA','AircraftMetadata-RegNotFound','AircraftMetadata-WrongReg','Cleaned')
                    WHERE reg.internalValue IN ({','.join('?'*len(regs))})
                    GROUP BY reg.internalValue, DATE(img.captureTime), ap.internalValue
                    ORDER BY reg.internalValue, DATE(img.captureTime) DESC
                """, p2).fetchall()
            con.close()
        except Exception as exc:
            log.warning("search error: %s", exc)
            return JSONResponse({'results': [], 'total': 0, 'error': str(exc)})

        results = []
        for reg, airline_val, type_val, mfr_val, notes_val, session_date, airport_iata, photos, kw_csv in rows:
            kws = sorted([k.strip() for k in (kw_csv or '').split(',') if k.strip()])
            results.append({
                'registration':  reg,
                'airline':       airline_val or '',
                'aircraft_type': type_val or '',
                'manufacturer':  mfr_val or _search_mfr(type_val or ''),
                'notes':         notes_val or '',
                'date':          session_date or '',
                'airport':       airport_iata or '',
                'keywords':      kws,
                'photos':        photos,
            })
        return JSONResponse({'results': results, 'total': len(results)})

    _FLEET_REFRESH_SECS = 7 * 86400  # 1 week

    def _fleet_fetch_fr24(owner_user_id: str, icao: str) -> list:
        """Fetch current aircraft list for an airline from FR24. Returns list of aircraft dicts."""
        import re as _re, requests as _req
        from bs4 import BeautifulSoup as _BS
        from monitor import _derive_manufacturer as _dmfr
        cards = app.state.control_store.get_fleet_cards(owner_user_id)
        card = next((c for c in cards if c['icao'] == icao.upper()), None)
        if not card:
            return []
        fleet_url = f"https://www.flightradar24.com/data/airlines/{card['iata'].lower()}-{card['icao'].lower()}/fleet"
        hdrs = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                              '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}
        resp = _req.get(fleet_url, headers=hdrs, timeout=20)
        resp.raise_for_status()
        soup = _BS(resp.text, 'html.parser')
        rego_pat = _re.compile(r'^(?:[A-Z0-9]{1,3}-[A-Z0-9]{2,6}|N[0-9]{1,5}[A-Z]{0,2}|HL[0-9]{4}|JA[0-9A-Z]{4}|P[0-9]{3,5})$')
        skip = {'Registration', 'Aircraft', 'type', 'Serial', 'number', 'MSN', 'Age'}
        aircraft, current_type = [], None
        for s in soup.stripped_strings:
            if rego_pat.match(s):
                full = ''
                strings = list(soup.stripped_strings)
                idx = list(strings).index(s) if s in strings else -1
                if idx >= 0 and idx + 1 < len(strings):
                    nxt = strings[idx + 1]
                    if not rego_pat.match(nxt) and not nxt.isdigit():
                        full = nxt
                aircraft.append({'registration': s, 'type_code': current_type or '', 'type_full': full,
                                 'manufacturer': _dmfr(full) or '', 'photos': 0})
            elif _re.match(r'^[A-Z0-9]{3,5}$', s) and not s.isdigit() and s not in skip:
                current_type = s
        return aircraft

    def _fleet_refresh_fr24_bg(owner_user_id: str, icao: str) -> None:
        """Background task: re-fetch FR24 fleet and update DB, then refresh photo counts."""
        import time as _time
        try:
            aircraft = _fleet_fetch_fr24(owner_user_id, icao)
            if not aircraft:
                return
            _fleet_refresh_photos_bg(owner_user_id, [icao], aircraft_override={icao: aircraft})
            cards = app.state.control_store.get_fleet_cards(owner_user_id)
            card = next((c for c in cards if c['icao'] == icao), None)
            if card:
                app.state.control_store.upsert_fleet_card(owner_user_id, icao, card['iata'], card['airline'],
                                                           aircraft, updated_at=int(_time.time()))
            log.info("Fleet card %s refreshed from FR24 (%d aircraft)", icao, len(aircraft))
        except Exception as e:
            log.warning("Fleet FR24 refresh failed for %s: %s", icao, e)

    def _fleet_refresh_photos_bg(owner_user_id: str, icao_list: list, aircraft_override: dict = None) -> None:
        """Background task: update photo counts for fleet cards from LR catalog."""
        cat_path = _catalog_path_for_owner(owner_user_id)
        if not cat_path or not _os.path.exists(cat_path):
            return
        try:
            import sqlite3 as _sq
            for icao in icao_list:
                cards = app.state.control_store.get_fleet_cards(owner_user_id)
                card = next((c for c in cards if c['icao'] == icao), None)
                if not card:
                    continue
                aircraft = (aircraft_override or {}).get(icao, card['aircraft'])
                regos = [a['registration'] for a in aircraft]
                if not regos:
                    continue
                con = _sq.connect(f"file:{cat_path}?mode=ro", uri=True)
                ph2 = ','.join('?' * len(regos))
                ap_sub2 = (f"SELECT prop.photo, prop.internalValue FROM AgSearchablePhotoProperty prop "
                           f"JOIN AgPhotoPropertySpec spec ON spec.id_local=prop.propertySpec "
                           f"AND spec.key='airport_iata' AND spec.sourcePlugin='{_SEARCH_PLUGIN}'")
                rows = con.execute(
                    f"SELECT reg.internalValue, COUNT(DISTINCT img.id_local) "
                    f"FROM Adobe_images img "
                    f"JOIN AgSearchablePhotoProperty reg ON reg.photo=img.id_local "
                    f"JOIN AgPhotoPropertySpec spec ON spec.id_local=reg.propertySpec "
                    f"  AND spec.key='registration' AND spec.sourcePlugin=? "
                    f"WHERE reg.internalValue IN ({ph2}) "
                    f"GROUP BY reg.internalValue",
                    [_SEARCH_PLUGIN] + regos
                ).fetchall()
                counts = {r[0]: r[1] for r in rows}
                spotted2 = [r for r in regos if counts.get(r, 0) > 0]
                last_sess2 = {}
                if spotted2:
                    sp2 = ','.join('?' * len(spotted2))
                    sr2 = con.execute(f"""
                        SELECT reg.internalValue, DATE(img.captureTime), ap.internalValue
                        FROM Adobe_images img
                        JOIN AgSearchablePhotoProperty reg ON reg.photo=img.id_local
                        JOIN AgPhotoPropertySpec rs ON rs.id_local=reg.propertySpec
                            AND rs.key='registration' AND rs.sourcePlugin=?
                        LEFT JOIN ({ap_sub2}) ap ON ap.photo=img.id_local
                        WHERE reg.internalValue IN ({sp2})
                          AND img.captureTime=(
                              SELECT MAX(img2.captureTime) FROM Adobe_images img2
                              JOIN AgSearchablePhotoProperty r2 ON r2.photo=img2.id_local
                              JOIN AgPhotoPropertySpec s2 ON s2.id_local=r2.propertySpec
                                  AND s2.key='registration' AND s2.sourcePlugin=?
                              WHERE r2.internalValue=reg.internalValue
                          )
                        GROUP BY reg.internalValue
                    """, [_SEARCH_PLUGIN] + spotted2 + [_SEARCH_PLUGIN]).fetchall()
                    last_sess2 = {r[0]: {'date': r[1], 'airport': r[2]} for r in sr2}
                con.close()
                iatas2 = list({ls['airport'] for ls in last_sess2.values() if ls.get('airport')})
                ap_info2 = {}
                if iatas2:
                    with app.state.store._connect() as sc:
                        ap_info2 = {r[0]: {'name': r[1], 'cc': r[2]} for r in sc.execute(
                            f"SELECT iata, name, country_code FROM airports WHERE iata IN ({','.join('?'*len(iatas2))})",
                            iatas2).fetchall()}
                def _short2(n):
                    for w in ('International ','international ',' International',' international',' Airport',' airport'):
                        n = n.replace(w, ' ')
                    return n.strip()
                updated = []
                for a in aircraft:
                    r = a['registration']
                    ls2 = last_sess2.get(r)
                    ap2 = ap_info2.get((ls2 or {}).get('airport', ''), {})
                    updated.append(dict(a,
                        photos=counts.get(r, 0),
                        last_date=ls2['date'] if ls2 else '',
                        last_ap_iata=(ls2 or {}).get('airport', '') or '',
                        last_airport=_short2(ap2.get('name', (ls2 or {}).get('airport', '') or '')) if ls2 else '',
                        last_ap_cc=ap2.get('cc', '') if ls2 else ''))
                app.state.control_store.update_fleet_card_photos(owner_user_id, icao, updated)
        except Exception as e:
            log.warning("Fleet photo refresh failed: %s", e)

    @app.get("/api/fleet-cards")
    async def get_fleet_cards(background_tasks: BackgroundTasks,
                               user=Depends(_auth_require_role("controller", "pilot"))):
        import time as _time
        owner = _owner_id(user)
        cards = app.state.control_store.get_fleet_cards(owner)
        now = int(_time.time())
        for card in cards:
            if now - (card.get('updated_at') or 0) > _FLEET_REFRESH_SECS:
                background_tasks.add_task(_fleet_refresh_fr24_bg, owner, card['icao'])
        return JSONResponse(cards)

    @app.post("/api/fleet-cards")
    async def save_fleet_card(request: Request, user=Depends(_auth_require_role("controller", "pilot"))):
        import time as _time
        body = await request.json()
        icao    = (body.get('icao') or '').strip().upper()
        iata    = (body.get('iata') or '').strip().upper()
        airline = (body.get('airline') or '').strip()
        aircraft = body.get('aircraft') or []
        if not icao:
            raise HTTPException(400, "icao required")
        app.state.control_store.upsert_fleet_card(_owner_id(user), icao, iata, airline, aircraft,
                                                   updated_at=int(_time.time()))
        return JSONResponse({'ok': True})

    @app.delete("/api/fleet-cards/{icao}")
    async def delete_fleet_card(icao: str, user=Depends(_auth_require_role("controller", "pilot"))):
        app.state.control_store.delete_fleet_card(_owner_id(user), icao)
        return JSONResponse({'ok': True})

    @app.get("/api/reg-prefix-cc")
    async def reg_prefix_cc(prefix: str = "", sample: str = ""):
        """
        Return country code for a registration prefix (e.g. VH → AU / Australia).
        Checks local DB first; on cache miss uses FR24 via the sample registration.
        """
        prefix = prefix.strip().upper()
        if not prefix:
            raise HTTPException(400, "prefix required")
        cached = app.state.store.get_reg_prefix_country(prefix)
        if cached:
            return JSONResponse(cached)
        # Cache miss — look up via FR24 using the sample rego
        rego = sample.strip() or prefix + "-AAA"
        try:
            import asyncio
            fr_api = app.state.fr_api if hasattr(app.state, 'fr_api') else None
            if fr_api is None:
                from flightradar24api import FlightRadar24API
                fr_api = FlightRadar24API()
            # Thread-dispatched — blocking FR24 network call, see
            # get_airforce_roundel's comment above for why.
            data = await asyncio.to_thread(fr_api.get_rego_details, rego)
            entries = data.get('data', []) if isinstance(data, dict) else []
            country = None
            for entry in entries:
                c = (entry.get('aircraft') or {}).get('country') or {}
                if c.get('alpha2'):
                    country = c
                    break
            if not country:
                return JSONResponse({'prefix': prefix, 'cc': '', 'name': ''})
            cc   = country['alpha2'].upper()
            name = country.get('name', '')
            app.state.store.save_reg_prefix_country(prefix, cc, name)
            log.info("Reg prefix %s → %s (%s)", prefix, cc, name)
            return JSONResponse({'prefix': prefix, 'cc': cc, 'name': name})
        except Exception as e:
            log.warning("reg-prefix-cc lookup failed for %s: %s", prefix, e)
            return JSONResponse({'prefix': prefix, 'cc': '', 'name': ''})

    @app.post("/api/fleet-cards/refresh-photos")
    async def refresh_fleet_photos(background_tasks: BackgroundTasks,
                                    user=Depends(_auth_require_role("controller", "pilot"))):
        """Triggered after a catalog refresh to update photo counts for this viewer's own fleet cards."""
        owner = _owner_id(user)
        cards = app.state.control_store.get_fleet_cards(owner)
        if cards:
            background_tasks.add_task(_fleet_refresh_photos_bg, owner, [c['icao'] for c in cards])
        return JSONResponse({'ok': True})

    @app.get("/api/fleet-coverage")
    async def fleet_coverage(code: str = "", user=Depends(_auth_current_user)):
        """
        Given an IATA (2-char) or ICAO (3-char) airline code, fetch the airline's
        current fleet from FR24 and cross-reference with the LR catalog to show
        which registrations have been photographed.
        """
        import re as _re, requests as _req, asyncio
        from bs4 import BeautifulSoup as _BS
        from monitor import _derive_manufacturer as _dmfr

        code = code.strip().upper()
        if not code:
            return JSONResponse({'error': 'No code provided'})

        # ── Step 1: resolve IATA ↔ ICAO via FR24 airline list ─────────────
        # Thread-dispatched — get_airlines() is a blocking FR24 network call
        # (same reasoning as get_airforce_roundel's comment above).
        try:
            fr_api = app.state.fr_api if hasattr(app.state, 'fr_api') else None
            if fr_api is None:
                from flightradar24api import FlightRadar24API
                fr_api = FlightRadar24API()
            airlines = await asyncio.to_thread(fr_api.get_airlines)
        except Exception as e:
            return JSONResponse({'error': f'FR24 airline lookup failed: {e}'})

        match = None
        for a in airlines:
            if code == (a.get('Code') or '').upper() or code == (a.get('ICAO') or '').upper():
                match = a
                break
        if not match:
            return JSONResponse({'error': f"Airline code '{code}' not found"})

        iata = (match.get('Code') or '').upper()
        icao = (match.get('ICAO') or '').upper()
        airline_name = match.get('Name', code)

        if not iata or not icao:
            return JSONResponse({'error': f'Incomplete codes for {airline_name}: IATA={iata} ICAO={icao}'})

        # ── Step 2: fetch fleet page from FR24 ────────────────────────────
        fleet_url = f"https://www.flightradar24.com/data/airlines/{iata.lower()}-{icao.lower()}/fleet"
        hdrs = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        try:
            resp = await asyncio.to_thread(_req.get, fleet_url, headers=hdrs, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            return JSONResponse({'error': f'FR24 fleet fetch failed: {e}'})

        soup = _BS(resp.text, 'html.parser')
        rego_pat = _re.compile(r'^(?:[A-Z0-9]{1,3}-[A-Z0-9]{2,6}|N[0-9]{1,5}[A-Z]{0,2}|HL[0-9]{4}|JA[0-9A-Z]{4}|P[0-9]{3,5})$')
        skip = {'Registration', 'Aircraft', 'type', 'Serial', 'number', 'MSN', 'Age'}

        fr24_fleet = []
        current_type_code = None
        strings = list(soup.stripped_strings)
        i = 0
        while i < len(strings):
            s = strings[i]
            if rego_pat.match(s):
                full_type = strings[i + 1] if i + 1 < len(strings) else ''
                if rego_pat.match(full_type) or full_type.isdigit():
                    full_type = ''
                fr24_fleet.append({
                    'registration': s,
                    'type_code': current_type_code or '',
                    'type_full': full_type,
                    'manufacturer': _dmfr(full_type) or '',
                })
                i += 1
            elif _re.match(r'^[A-Z0-9]{3,5}$', s) and not s.isdigit() and s not in skip:
                current_type_code = s
                i += 1
            else:
                i += 1

        if not fr24_fleet:
            return JSONResponse({'error': f'No fleet data found for {airline_name} at {fleet_url}'})

        # ── Step 3: cross-reference with LR catalog ───────────────────────
        fleet_regos = [a['registration'] for a in fr24_fleet]
        photo_counts = {}
        last_session = {}   # rego → {date, airport_iata}
        cat_path = _search_catalog_path(user)
        if cat_path and _os.path.exists(cat_path):
            try:
                import sqlite3 as _sq
                con = _sq.connect(f"file:{cat_path}?mode=ro", uri=True)
                ph = ','.join('?' * len(fleet_regos))
                ap_sub = (f"SELECT prop.photo, prop.internalValue FROM AgSearchablePhotoProperty prop "
                          f"JOIN AgPhotoPropertySpec spec ON spec.id_local=prop.propertySpec "
                          f"AND spec.key='airport_iata' AND spec.sourcePlugin='{_SEARCH_PLUGIN}'")
                # Photos count
                rows = con.execute(f"""
                    SELECT reg.internalValue, COUNT(DISTINCT img.id_local)
                    FROM Adobe_images img
                    JOIN AgSearchablePhotoProperty reg ON reg.photo=img.id_local
                    JOIN AgPhotoPropertySpec spec ON spec.id_local=reg.propertySpec
                        AND spec.key='registration' AND spec.sourcePlugin=?
                    WHERE reg.internalValue IN ({ph})
                    GROUP BY reg.internalValue
                """, [_SEARCH_PLUGIN] + fleet_regos).fetchall()
                photo_counts = {r[0]: r[1] for r in rows}
                # Last session date + airport for spotted regos
                spotted_regos = [r for r in fleet_regos if photo_counts.get(r, 0) > 0]
                if spotted_regos:
                    sph = ','.join('?' * len(spotted_regos))
                    srows = con.execute(f"""
                        SELECT reg.internalValue, DATE(img.captureTime), ap.internalValue
                        FROM Adobe_images img
                        JOIN AgSearchablePhotoProperty reg ON reg.photo=img.id_local
                        JOIN AgPhotoPropertySpec rs ON rs.id_local=reg.propertySpec
                            AND rs.key='registration' AND rs.sourcePlugin=?
                        LEFT JOIN ({ap_sub}) ap ON ap.photo=img.id_local
                        WHERE reg.internalValue IN ({sph})
                          AND img.captureTime=(
                              SELECT MAX(img2.captureTime) FROM Adobe_images img2
                              JOIN AgSearchablePhotoProperty r2 ON r2.photo=img2.id_local
                              JOIN AgPhotoPropertySpec s2 ON s2.id_local=r2.propertySpec
                                  AND s2.key='registration' AND s2.sourcePlugin=?
                              WHERE r2.internalValue=reg.internalValue
                          )
                        GROUP BY reg.internalValue
                    """, [_SEARCH_PLUGIN] + spotted_regos + [_SEARCH_PLUGIN]).fetchall()
                    last_session = {r[0]: {'date': r[1], 'airport': r[2]} for r in srows}
                con.close()
            except Exception as e:
                log.warning("fleet coverage catalog lookup failed: %s", e)

        # Look up airport names + country codes from store
        iatas_needed = list({ls['airport'] for ls in last_session.values() if ls.get('airport')})
        ap_info = {}
        if iatas_needed:
            try:
                with app.state.store._connect() as sc:
                    arows = sc.execute(
                        f"SELECT iata, name, country_code FROM airports WHERE iata IN ({','.join('?'*len(iatas_needed))})",
                        iatas_needed
                    ).fetchall()
                ap_info = {r[0]: {'name': r[1], 'cc': r[2]} for r in arows}
            except Exception:
                pass

        def _short_ap(name):
            for w in ('International ', 'international ', ' International', ' international',
                      ' Airport', ' airport', 'Airport ', 'airport '):
                name = name.replace(w, ' ')
            return name.strip()

        for a in fr24_fleet:
            a['photos'] = photo_counts.get(a['registration'], 0)
            ls = last_session.get(a['registration'])
            if ls:
                ap = ap_info.get(ls['airport'], {})
                a['last_date']     = ls['date'] or ''
                a['last_ap_iata']  = ls['airport'] or ''
                a['last_airport']  = _short_ap(ap.get('name', ls['airport'] or ''))
                a['last_ap_cc']    = ap.get('cc', '')
            else:
                a['last_date'] = a['last_ap_iata'] = a['last_airport'] = a['last_ap_cc'] = ''

        # Sort: photographed first (by photo count desc), then missing alphabetically
        fr24_fleet.sort(key=lambda a: (-a['photos'], a['registration']))

        return JSONResponse({
            'airline': airline_name,
            'iata': iata,
            'icao': icao,
            'aircraft': fr24_fleet,
        })

    @app.post("/api/aircraft-types")
    async def add_aircraft_type(request: Request, user=Depends(_auth_require_role("controller"))):
        body = await request.json()
        icao = (body.get('icao') or '').strip().upper()
        name = (body.get('name') or '').strip()
        if not icao or not name:
            raise HTTPException(400, "icao and name required")
        # Global across all airports — Controller sets it once, it applies
        # everywhere (fan out to every watched airport's DB).
        for cfg in app.state.cfgs.values():
            cfg.store.upsert_aircraft_type(icao, name, source='user')
        return JSONResponse({'ok': True})

    @app.delete("/api/aircraft-types/{icao}")
    async def delete_aircraft_type(icao: str, user=Depends(_auth_require_role("controller"))):
        icao = icao.strip().upper()
        for cfg in app.state.cfgs.values():
            with cfg.store._connect() as conn:
                conn.execute("DELETE FROM aircraft_types WHERE icao=? AND source='user'", (icao,))
        return JSONResponse({'ok': True})

    def _logostream_api_key():
        with app.state.store._connect() as _conn:
            row = _conn.execute("SELECT value FROM settings WHERE key='LOGOSTREAM_API_KEY'").fetchone()
        return row[0] if row else ""

    def _baidu_translate_creds():
        with app.state.store._connect() as _conn:
            rows = _conn.execute(
                "SELECT key, value FROM settings WHERE key IN "
                "('BAIDU_TRANSLATE_APP_ID','BAIDU_TRANSLATE_SECRET_KEY')"
            ).fetchall()
        vals = {k: v for k, v in rows}
        return vals.get("BAIDU_TRANSLATE_APP_ID", ""), vals.get("BAIDU_TRANSLATE_SECRET_KEY", "")

    from functools import lru_cache as _lru_cache

    @_lru_cache(maxsize=256)
    def _country_code_for_name(name: str) -> str:
        """ISO-3166 alpha-2 code for a country NAME, via the pycountry library's
        own bundled database (not a hardcoded table) — exact lookup first, then
        pycountry's fuzzy search for less exact matches. Cached since the set of
        distinct military-operator country names seen in practice is small."""
        try:
            import pycountry
        except ImportError:
            return ""
        try:
            return pycountry.countries.lookup(name).alpha_2
        except LookupError:
            pass
        try:
            results = pycountry.countries.search_fuzzy(name)
            return results[0].alpha_2 if results else ""
        except LookupError:
            return ""

    # Countries where the World-Airforce-Insignia repo's filename is a
    # colloquial abbreviation rather than the ISO code — keyed by the
    # lowercased country name as it appears in military.py's _ICAO_RANGES,
    # valued by the (lowercased, extension-stripped) filename key it should
    # resolve to. Add an entry here if a country you know has a roundel in
    # that repo still isn't showing up.
    _ROUNDEL_NAME_OVERRIDES = {
        "united kingdom": "uk",
    }

    def _airforce_roundel_filenames():
        """List of available roundel filenames in the World-Airforce-Insignia CDN repo,
        fetched from the GitHub API once and cached to disk. No hardcoded country list —
        whatever the repo has is what we can fuzzy-match against."""
        import json as _json, requests as _req
        index_path = Path(__file__).parent / "static" / "airforce_roundels_index.json"
        if index_path.exists():
            try:
                return _json.loads(index_path.read_text())
            except Exception:
                pass
        try:
            r = _req.get(
                "https://api.github.com/repos/chaseAEd/World-Airforce-Insignia/contents/Flags",
                timeout=10,
            )
            r.raise_for_status()
            files = [item["name"] for item in r.json() if item["name"].lower().endswith(".png")]
            if files:
                index_path.write_text(_json.dumps(files))
            return files
        except Exception:
            return []

    def _fetch_airforce_roundel(country: str):
        """Fuzzy-match a country name against the roundel repo's file list, fetch from
        GitHub CDN, save to disk. Returns (Path, media_type) or (None, None)."""
        import re as _re3, difflib, requests as _req
        if not country:
            return None, None
        query = country.strip().lower()
        safe_key = _re3.sub(r'[^a-z0-9]+', '_', query).strip('_')
        cache_path = Path(__file__).parent / "static" / "airline_logos" / f"af_{safe_key}.png"
        if cache_path.exists():
            return cache_path, "image/png"

        files = _airforce_roundel_filenames()
        if not files:
            return None, None

        def _norm(s):
            s = _re3.sub(r'\s*\(.*?\)', '', s)   # strip "(1990-)" style suffixes
            s = s.rsplit('.', 1)[0]              # strip extension
            return s.strip().lower()

        norm_map = {_norm(f): f for f in files}
        match = difflib.get_close_matches(query, norm_map.keys(), n=1, cutoff=0.6)
        if not match:
            # Fall back to the country's ISO abbreviations — many roundel filenames
            # use short codes ("USA") rather than the full country name, which the
            # difflib fuzzy match above (tuned for whole-name similarity) won't
            # bridge. EXACT match only, never substring/fuzzy, on purpose: the
            # previous fallback here (`query in k or k in query`) matched ANY key
            # that was merely a substring of the query, so the 2-letter "un" key
            # (United Nations) matched every country name starting with "Un..." —
            # "United States" and even the literal string "Unknown" both showed
            # the UN roundel. A short abbreviation must match a candidate exactly.
            candidates = []
            # Colloquial abbreviation the roundel repo uses instead of the ISO
            # code for a few common countries (checked first, still an exact
            # match only) — e.g. "United Kingdom" -> "UK.png", not "GB"/"GBR"
            # (its actual ISO alpha-2/alpha-3), which is why this specific
            # country needed a manual override rather than falling out of the
            # ISO-based candidates below.
            candidates.append(_ROUNDEL_NAME_OVERRIDES.get(query, ''))
            code_a2 = _country_code_for_name(country)
            if code_a2:
                try:
                    import pycountry
                    rec = pycountry.countries.get(alpha_2=code_a2)
                    if rec:
                        candidates.append(getattr(rec, 'alpha_3', '').lower())
                        candidates.append(getattr(rec, 'common_name', '').lower())
                except Exception:
                    pass
                candidates.append(code_a2.lower())
            for cand in candidates:
                if cand and cand in norm_map:
                    match = [cand]
                    break
        if not match:
            return None, None
        filename = norm_map[match[0]]

        url = f"https://cdn.jsdelivr.net/gh/chaseAEd/World-Airforce-Insignia@master/Flags/{filename}"
        try:
            r = _req.get(url, timeout=10)
            if r.status_code == 200 and len(r.content) > 500:
                cache_path.write_bytes(r.content)
                return cache_path, "image/png"
        except Exception:
            pass
        return None, None

    def _fetch_and_cache_logo(icao: str):
        """Fetch tail logo from logostream by ICAO, save to disk. Returns (Path, media_type) or raises HTTPException."""
        import requests as _req, io as _io
        api_key = _logostream_api_key()
        if not api_key:
            raise HTTPException(503, "Logostream API key not configured")
        r = _req.get(
            f"https://airlines-api.logostream.dev/airlines/icao/{icao}",
            params={"key": api_key, "variant": "tail"},
            timeout=10
        )
        if r.status_code == 404:
            raise HTTPException(404, "Logo not found")
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "")
        is_svg = "svg" in ct
        is_png = "png" in ct
        if not is_svg and not is_png:
            raise HTTPException(404, "No logo available")
        # Reject placeholder SVGs (logostream returns ~266b text-based fallbacks)
        if is_svg and len(r.content) < 1000:
            raise HTTPException(404, "Placeholder SVG excluded")
        # For PNGs: reject if logo has almost no visible content (all-white tails).
        # A logo counts as visible content if it has either colorful (saturated)
        # pixels or dark pixels — monochrome black-on-white logos (e.g. Air New
        # Zealand's koru) have 0% saturated pixels but are clearly not blank.
        if is_png:
            try:
                from PIL import Image as _Img
                import warnings; warnings.filterwarnings("ignore")
                img = _Img.open(_io.BytesIO(r.content)).convert("RGBA")
                pixels = list(img.getdata())
                visible = [(rr, gg, bb) for rr, gg, bb, aa in pixels if aa > 30]
                if visible:
                    colorful = sum(1 for rr, gg, bb in visible if max(rr, gg, bb) - min(rr, gg, bb) > 40)
                    dark = sum(1 for rr, gg, bb in visible if max(rr, gg, bb) < 200)
                    if colorful / len(visible) < 0.03 and dark / len(visible) < 0.03:
                        raise HTTPException(404, "Effectively white logo excluded")
            except HTTPException:
                raise
            except Exception:
                pass
        ext = "svg" if is_svg else "png"
        media = "image/svg+xml" if is_svg else "image/png"
        cache_path = Path(__file__).parent / "static" / "airline_logos" / f"{icao}.{ext}"
        cache_path.write_bytes(r.content)
        import system_status as _ss; _ss.record_api('logostream', True)
        return cache_path, media

    def _cached_logo_path(icao: str):
        """Return (Path, media_type) for a cached logo, checking both .png and .svg."""
        base = Path(__file__).parent / "static" / "airline_logos"
        for ext, mt in (("png", "image/png"), ("svg", "image/svg+xml")):
            p = base / f"{icao}.{ext}"
            if p.exists():
                return p, mt
        return None, None

    @app.get("/api/airforce-roundel/{country}")
    async def get_airforce_roundel(country: str):
        import asyncio
        # Thread-dispatched — on a cache miss this makes real blocking network
        # calls (GitHub API for the file index, jsdelivr CDN for the image, up
        # to 10s timeout each) that would otherwise freeze the ENTIRE web
        # process's event loop for every other concurrent request, same class
        # of bug as the monitor-loop freezes fixed earlier tonight. Static
        # cache files under static/airline_logos/ aren't in a persistent
        # volume (see docker-compose.yml), so every container rebuild wipes
        # them and the next page load re-triggers this cold-cache burst for
        # every distinct roundel/logo shown.
        cache_path, media = await asyncio.to_thread(_fetch_airforce_roundel, country.strip())
        if not cache_path:
            raise HTTPException(404, "Roundel not found")
        from fastapi.responses import FileResponse
        return FileResponse(str(cache_path), media_type=media,
                            headers={"Cache-Control": "public, max-age=2592000"})

    @app.get("/api/country-code/{name}")
    async def get_country_code(name: str):
        """Resolve a country NAME (e.g. from military.py's ICAO-hex-block-derived
        'country' text, the same authoritative source already used for the
        airforce roundel) to its ISO-3166 alpha-2 code, via the pycountry
        library's own bundled database — not a guess from the registration
        string, which is unreliable for military serial numbers."""
        name = name.strip()
        if not name:
            raise HTTPException(404, "Not found")
        code = _country_code_for_name(name)
        if not code:
            raise HTTPException(404, "Country not found")
        return JSONResponse({"code": code})

    @app.get("/api/airline-logo/{icao}")
    async def get_airline_logo(icao: str):
        import re as _re, asyncio
        icao = icao.upper().strip()
        if not _re.match(r'^[A-Z0-9]{2,4}$', icao):
            raise HTTPException(400, "Invalid ICAO code")
        cache_path, media = _cached_logo_path(icao)
        if not cache_path:
            try:
                # Thread-dispatched — see get_airforce_roundel's comment above
                # for why a cache-miss blocking network call here can't run
                # directly on the event loop.
                cache_path, media = await asyncio.to_thread(_fetch_and_cache_logo, icao)
            except HTTPException:
                raise
            except Exception as e:
                import system_status as _ss; _ss.record_api('logostream', False, str(e))
                raise HTTPException(502, f"Logostream fetch failed: {e}")
        from fastapi.responses import FileResponse
        return FileResponse(str(cache_path), media_type=media,
                            headers={"Cache-Control": "public, max-age=2592000"})

    @app.get("/api/airline-logo-name/{name}")
    async def get_airline_logo_by_name(name: str):
        """Fuzzy search by airline name → resolve ICAO → serve tail logo."""
        import re as _re, json as _json, requests as _req, asyncio
        name = name.strip()
        if not name:
            raise HTTPException(400, "Name required")
        # Check name→icao mapping cache
        mapping_path = Path(__file__).parent / "static" / "airline_logos" / "_name_icao.json"
        mapping: dict = {}
        if mapping_path.exists():
            try:
                mapping = _json.loads(mapping_path.read_text())
            except Exception:
                mapping = {}
        name_key = name.lower()
        icao = mapping.get(name_key)
        if not icao:
            # Call logostream fuzzy search
            api_key = _logostream_api_key()
            if not api_key:
                raise HTTPException(503, "Logostream API key not configured")

            # Thread-dispatched — see get_airforce_roundel's comment above for
            # why a blocking network call here can't run directly on the event
            # loop. HTTPException raised inside still propagates correctly
            # through the await below.
            def _fetch_icao_by_name():
                r = _req.get(
                    "https://aviation-api.logostream.dev/v1/airlines",
                    params={"name": name},
                    headers={"x-api-key": api_key, "Accept": "application/json"},
                    timeout=10
                )
                r.raise_for_status()
                results = r.json().get("data", [])
                if not results:
                    raise HTTPException(404, "Airline not found")
                _icao = results[0].get("icao", "").upper()
                if not _icao:
                    raise HTTPException(404, "No ICAO in search result")
                mapping[name_key] = _icao
                mapping_path.write_text(_json.dumps(mapping))
                return _icao

            try:
                icao = await asyncio.to_thread(_fetch_icao_by_name)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(502, f"Logostream search failed: {e}")
        # Now serve the logo for the resolved ICAO
        cache_path, media = _cached_logo_path(icao)
        if not cache_path:
            try:
                cache_path, media = await asyncio.to_thread(_fetch_and_cache_logo, icao)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(502, f"Logo fetch failed: {e}")
        from fastapi.responses import FileResponse
        return FileResponse(str(cache_path), media_type=media,
                            headers={"Cache-Control": "public, max-age=2592000"})

    _TRANSLATE_CACHE_PATH = Path(__file__).parent / "static" / "translations" / "names_zh.json"
    _TRANSLATE_CACHE_LOCK = threading.Lock()

    def _load_translate_cache() -> dict:
        if not _TRANSLATE_CACHE_PATH.exists():
            return {}
        try:
            return json.loads(_TRANSLATE_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_translate_cache(cache: dict):
        # Deliberately NOT sort_keys=True — kept in insertion order (order of
        # first translation) rather than alphabetical, so newest entries land
        # at the bottom of the file and are easy to find/review after a batch
        # of new names gets translated. dict preserves insertion order (3.7+),
        # and _load_translate_cache()'s json.loads preserves the file's order
        # on read, so existing entries keep their position across restarts.
        _TRANSLATE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TRANSLATE_CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _clean_baidu_airline_name(name: str) -> str:
        """Baidu often appends a redundant '公司' ("company/corporation") suffix
        to airline names (e.g. '新西兰航空公司') that Chinese-language aviation
        convention drops (just '新西兰航空') — trim it off if present."""
        name = (name or "").strip()
        if name.endswith("公司"):
            name = name[:-2].strip()
        return name

    def _baidu_translate_batch(names: list) -> dict:
        """Translate a batch of English names to Chinese via Baidu, chunked to stay
        under Baidu's query-length cap. Returns {name: translated} for whatever
        chunks succeeded; silently omits names from any chunk that failed."""
        import hashlib, random, requests as _req
        app_id, secret = _baidu_translate_creds()
        if not app_id or not secret:
            return {}
        result = {}
        chunk: list = []
        chunk_bytes = 0
        chunks = []
        for name in names:
            b = len((name + "\n").encode("utf-8"))
            if chunk and chunk_bytes + b > 5500:
                chunks.append(chunk)
                chunk, chunk_bytes = [], 0
            chunk.append(name)
            chunk_bytes += b
        if chunk:
            chunks.append(chunk)
        def _call(url: str, q: str, domain: str = None) -> dict:
            salt = str(random.randint(32768, 65536))
            # fieldtranslate's sign includes `domain` between salt and secret
            # (per Baidu's official sample); general translate's sign doesn't
            # have a domain segment at all — this is the one difference between
            # the two endpoints' auth, everything else about the request is the same.
            sign_raw = f"{app_id}{q}{salt}{domain}{secret}" if domain else f"{app_id}{q}{salt}{secret}"
            sign = hashlib.md5(sign_raw.encode("utf-8")).hexdigest()
            params = {"q": q, "from": "en", "to": "zh", "appid": app_id, "salt": salt, "sign": sign}
            if domain:
                params["domain"] = domain
            r = _req.get(url, params=params, timeout=10)
            return r.json()

        for c in chunks:
            q = "\n".join(c)
            try:
                # Field/domain translation (aerospace-tuned terminology) — falls
                # back to the general endpoint if the account doesn't have field
                # translation enabled, the domain code is rejected, or any other
                # error comes back, so a bad/unapproved domain never breaks
                # translation outright, just loses the domain-specific tuning.
                data = _call("http://api.fanyi.baidu.com/api/trans/vip/fieldtranslate", q, domain="aerospace")
                if "trans_result" not in data:
                    data = _call("http://api.fanyi.baidu.com/api/trans/vip/translate", q)
                if "trans_result" in data:
                    for src_name, item in zip(c, data["trans_result"]):
                        result[src_name] = _clean_baidu_airline_name(item.get("dst", ""))
                    import system_status as _ss; _ss.record_api('baidu_translate', True)
                else:
                    import system_status as _ss
                    _ss.record_api('baidu_translate', False, str(data.get("error_msg", data)))
            except Exception as e:
                import system_status as _ss; _ss.record_api('baidu_translate', False, str(e))
        return result

    @app.post("/api/translate-names")
    async def translate_names(request: Request, user=Depends(_auth_current_user)):
        import asyncio
        body = await request.json()
        names = body.get("names") or []
        if not isinstance(names, list):
            raise HTTPException(400, "names must be a list")
        names = [n.strip() for n in names if isinstance(n, str) and n.strip()]
        seen = set()
        deduped = []
        for n in names:
            if n not in seen:
                seen.add(n)
                deduped.append(n)
        deduped = deduped[:200]

        # The whole cache-check + Baidu-call block is dispatched as ONE thread
        # call — this is the most frequently-hit endpoint of any of these (every
        # Feed load, every card open), and on a cache miss it makes a genuinely
        # blocking network call to Baidu while holding _TRANSLATE_CACHE_LOCK (a
        # plain threading.Lock, itself only safe to block on off the event loop
        # thread) — running that directly on the event loop froze the ENTIRE web
        # process for every other concurrent request for as long as the Baidu
        # call took. threading.Lock works the same from a worker thread, so this
        # preserves the existing cross-request serialization unchanged.
        def _resolve_translations():
            with _TRANSLATE_CACHE_LOCK:
                cache = _load_translate_cache()
                # Case-insensitive lookup — the same airline/airport name shows up in
                # different casing across data sources (FR24 payloads, Lightroom
                # catalogs, user-entered filters, etc.); without this, "Qantas" and
                # "QANTAS" would each burn a separate Baidu call and grow the cache
                # file with duplicate entries. Cache stays stored under whatever
                # casing first got translated — only the lookup is normalized —
                # and a hit is returned under the REQUESTED casing so the client's
                # own cache keys line up with what it asked for.
                cache_ci = {k.lower(): k for k in cache}
                def _cache_hit(n: str):
                    if n in cache:
                        return cache[n]
                    stored_key = cache_ci.get(n.lower())
                    return cache[stored_key] if stored_key is not None else None
                hits = {}
                misses = []
                for n in deduped:
                    v = _cache_hit(n)
                    if v is not None:
                        hits[n] = v
                    else:
                        misses.append(n)
                if not misses:
                    return hits
                new_map = _baidu_translate_batch(misses)
                if new_map:
                    cache.update(new_map)
                    _save_translate_cache(cache)
                return {**hits, **new_map}

        translations = await asyncio.to_thread(_resolve_translations)
        return JSONResponse({"translations": translations})

    @app.get("/api/airports")
    async def list_airports(user=Depends(_auth_current_user)):
        with _cfg_for_user(user).store._connect() as conn:
            rows = conn.execute(
                "SELECT iata, name, country_code, source FROM airports WHERE source='user' ORDER BY iata"
            ).fetchall()
        return JSONResponse([{'iata': r[0], 'name': r[1], 'country_code': r[2]} for r in rows])

    @app.post("/api/airports")
    async def add_airport(request: Request, user=Depends(_auth_require_role("controller"))):
        body = await request.json()
        iata = (body.get('iata') or '').strip().upper()
        name = (body.get('name') or '').strip()
        cc   = (body.get('country_code') or '').strip().upper()
        if not iata or not name:
            raise HTTPException(400, "iata and name required")
        # Global across all airports — Controller sets it once, it applies
        # everywhere (fan out to every watched airport's DB).
        for cfg in app.state.cfgs.values():
            cfg.store.upsert_airport(iata, name, cc, source='user')
        return JSONResponse({'ok': True})

    @app.delete("/api/airports/{iata}")
    async def delete_airport(iata: str, user=Depends(_auth_require_role("controller"))):
        iata = iata.strip().upper()
        for cfg in app.state.cfgs.values():
            with cfg.store._connect() as conn:
                conn.execute("DELETE FROM airports WHERE iata=? AND source='user'", (iata,))
        return JSONResponse({'ok': True})

    @app.get("/api/status")
    async def get_status(user=Depends(_auth_current_user)):
        cfg = _cfg_for_user(user)
        now = int(time.time())
        result: dict[str, Any] = {"now_ts": now, "rapid_mode": False, "version": VERSION,
                                   "runtime_secs": now - _PROCESS_START_TS, **_system_info()}
        if cfg is not None:
            result["rapid_mode"] = bool(getattr(cfg, "military_rapid_tracking", None))
            result["airport_name"] = cfg.airport_name
            result["airport_iata"] = cfg.airport_iata
            result["airport_tz"]   = getattr(cfg, "airport_tz", "")
            result["check_interval"] = cfg.check_interval
            result["military_check_interval"] = cfg.military_check_interval
        # Timezone is tied to the airport's own location — never separately
        # user-settable (WEB_TIMEZONE override removed).
        store_ = cfg.store if cfg else app.state.store
        _eff_tz = result.get("airport_tz") or "UTC"
        result["effective_tz"] = _eff_tz
        try:
            import pytz as _pytz, datetime as _dt
            _tz = _pytz.timezone(_eff_tz)
            result["current_time"] = _dt.datetime.now(_tz).strftime("%H:%M:%S")
        except Exception:
            result["current_time"] = ""
        if cfg is None:
            # Standalone mode only (no AppConfig/monitor loop) — this used to run
            # unconditionally via a try/else, which meant it silently clobbered the
            # real per-airport check_interval set above with this stale default
            # every time /api/status succeeded in integrated mode. Invisible with a
            # single airport (both usually 30 min by default); became visible as
            # soon as a second airport with a different interval existed.
            s = app.state.settings
            result["airport_code"] = s.get("AIRPORT_CODE", "")
            result["check_interval"] = float(s.get("CHECK_INTERVAL_MINUTES", 30)) * 60
        return JSONResponse(result)

    @app.get("/api/logs")
    async def get_logs(lines: int = 500):
        log_path = Path(__file__).parent / "logs" / "spotalert.log"
        if not log_path.exists():
            return JSONResponse({"text": "", "path": str(log_path)})
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail = all_lines[-max(1, min(lines, 5000)):]
        return JSONResponse({"text": "".join(tail), "path": str(log_path)})

    @app.get("/api/logs/download")
    async def download_logs():
        log_path = Path(__file__).parent / "logs" / "spotalert.log"
        if not log_path.exists():
            raise HTTPException(404, "Log file not found")
        return FileResponse(log_path, media_type="text/plain", filename="spotalert.log")

    @app.get("/api/system-tasks")
    async def get_system_tasks(user=Depends(_auth_current_user)):
        import system_status as _ss, time as _time
        cfg_  = _cfg_for_user(user)
        store_ = cfg_.store if cfg_ else app.state.store
        now   = int(_time.time())
        check_int = getattr(cfg_, 'check_interval', 1800) if cfg_ else 1800
        mil_int   = getattr(cfg_, 'military_check_interval', 900) if cfg_ else 900
        # Per-airport tasks/APIs are scoped to whichever airport is currently
        # selected — each watched airport polls FR24/adsb.fi independently
        # (staggered across the interval, see monitor_runner.py), so their
        # last-run/next-run state is tracked per airport instead of one shared
        # global slot that the last-checked airport would silently overwrite.
        airport_scope = cfg_.airport_iata if cfg_ else None

        def _t(key, scoped=False):
            return _ss.get_task(key, scope=airport_scope if scoped else None)
        def _a(key, scoped=False):
            return _ss.get_api(key, scope=airport_scope if scoped else None)

        def _entry(d, name, desc, interval=None, scoped=False, task_key=None):
            last_ts = d.get('ts')
            # Prefer the scheduler's own announced next-run time (accounts for
            # startup staggering and rapid-tracking's shortened military
            # interval) — falls back to last_ts + interval for tasks that don't
            # explicitly announce one.
            next_ts = _ss.get_next_run(task_key, scope=airport_scope) if (scoped and task_key) else None
            if next_ts is None:
                next_ts = last_ts + interval if (last_ts and interval) else None
            return {
                'name': name, 'desc': desc, 'interval': interval,
                'last_ts': last_ts, 'next_ts': next_ts,
                'ok': d.get('ok'), 'error': d.get('error'),
            }

        # ICAOList last update from DB (persisted separately)
        icao_ts = None
        if store_:
            try:
                v = store_.load_setting('icao_list_last_update')
                if v: icao_ts = int(float(v))
            except Exception:
                pass

        tasks = [
            _entry(_t('arrivals_check', scoped=True), 'Airport Scan',  'FR24 airport feed → filter matching → store flights + clusters', check_int, scoped=True, task_key='arrivals_check'),
            _entry(_t('military_check', scoped=True), 'Military Scan', 'adsb.fi query for military traffic near airport', mil_int, scoped=True, task_key='military_check'),
            _entry(_t('feed_cleanup'),      'Flight Cleanup',     'Prune flight records older than 30 days', check_int),
            _entry(_t('collection_stats'),  'Collection Stats',   'Lightroom catalog stats cache refresh', check_int),
            _entry(_t('db_backup'),         'DB Backup',          'SQLite database backup to disk', 86400),
            _entry(_t('fleet_update'),      'Fleet Update',       'Refresh all fleet card data from FR24', 7 * 86400),
            {**_entry(_t('icaolist_github'), 'ICAO List Update',  'Refresh aircraft type database', 90 * 86400),
             'last_ts': icao_ts, 'next_ts': icao_ts + 90 * 86400 if icao_ts else None,
             'ok': True if icao_ts else None},
        ]
        apis = [
            _entry(_a('fr24_airport', scoped=True), 'FR24 Airport Feed', 'Arrivals/departures board (positive + negative pages)', check_int),
            _entry(_a('open_meteo', scoped=True),   'Open-Meteo',        'Weather + sunrise/sunset for timeline clusters', check_int),
            _entry(_a('adsb_fi', scoped=True),      'adsb.fi Military',  'Military aircraft positions near airport', mil_int),
            {**_entry(_a('icaolist_github'), 'ICAOList (GitHub)', 'Aircraft type database (90-day refresh)', 90 * 86400),
             'last_ts': icao_ts, 'next_ts': icao_ts + 90 * 86400 if icao_ts else None,
             'ok': True if icao_ts else None},
            _entry(_a('logostream'),        'Logostream',         'Airline tail logos (on demand, disk-cached)', None),
        ]
        return JSONResponse({'tasks': tasks, 'apis': apis, 'now': now})

    def _owner_for(user) -> str:
        """The owner_user_id a write from this user should land under —
        Controller writes are the 'controller' ground-truth row; a Pilot's own
        writes are fully independent, keyed by their own stable token."""
        return "controller" if user.role == "controller" else user.user_id

    @app.get("/api/settings")
    async def get_settings(user=Depends(_auth_current_user)):
        store = _cfg_for_user(user).store
        with store._connect() as conn:
            result = {r["key"]: r["value"] for r in conn.execute(
                "SELECT key, value FROM settings WHERE user_id = 'controller' ORDER BY key").fetchall()}
            if user.role == "pilot":
                # Layer the pilot's own overrides on top of the Controller's
                # defaults, for whichever pilot-editable keys they've touched.
                own = conn.execute(
                    "SELECT key, value FROM settings WHERE user_id = ?", (user.user_id,)
                ).fetchall()
                own_by_key = {r["key"]: r["value"] for r in own}
                for k, v in own_by_key.items():
                    if k in PILOT_EDITABLE_SETTINGS:
                        result[k] = v
        # Passengers are read-only and must never see the raw API credential,
        # even though every other Controller-only key is fine to display
        # (read-only) for them.
        if user.role == "passenger":
            result.pop("LOGOSTREAM_API_KEY", None)
            result.pop("BAIDU_TRANSLATE_APP_ID", None)
            result.pop("BAIDU_TRANSLATE_SECRET_KEY", None)
        return JSONResponse(result)

    @app.put("/api/settings")
    async def put_settings(request: Request, user=Depends(_auth_require_role("controller", "pilot"))):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Expected JSON object")
        if user.role == "pilot":
            not_allowed = [k for k in body if k not in PILOT_EDITABLE_SETTINGS]
            if not_allowed:
                raise HTTPException(403, f"Not allowed to edit: {', '.join(not_allowed)}")
        store = _cfg_for_user(user).store
        owner = _owner_for(user)
        for key, value in body.items():
            # GLOBAL_INFRA_SETTINGS (Controller-only) and PER_USER_GLOBAL_SETTINGS
            # (this user's own value) must read identically no matter which
            # airport is selected, so the write fans out to every watched
            # airport's DB instead of just the currently selected one.
            if key in GLOBAL_INFRA_SETTINGS or key in PER_USER_GLOBAL_SETTINGS:
                for cfg in app.state.cfgs.values():
                    cfg.store.set_setting(owner, str(key), str(value))
            else:
                store.set_setting(owner, str(key), str(value))
        return JSONResponse({"ok": True})

    @app.get("/api/filters")
    async def get_filters(user=Depends(_auth_current_user)):
        store = _cfg_for_user(user).store
        # Passengers always see the Controller's list (read-only, since
        # _owner_id resolves them to the 'controller' sentinel). A Pilot
        # always reads their own rows — seeded from the Controller's list
        # once at setup time (see copy_controller_filters_to_owner), fully
        # independent afterward.
        owner = _owner_id(user)
        def _fetch_rows(sql):
            with store._connect() as conn:
                return [dict(r) for r in conn.execute(sql, (owner,)).fetchall()]
        return JSONResponse({
            "filter_exclusions":  _fetch_rows("SELECT id, registration, description FROM filter_exclusions WHERE owner_user_id = ? ORDER BY id"),
            "filter_regos":  _fetch_rows("SELECT id, registration, description FROM filter_regos WHERE owner_user_id = ? ORDER BY id"),
            "filter_types":  _fetch_rows("SELECT id, airline, aircraft_type FROM filter_types WHERE owner_user_id = ? ORDER BY id"),
            "filter_airlines": _fetch_rows("SELECT id, icao_code, entry_type, name FROM filter_airlines WHERE owner_user_id = ? ORDER BY id"),
        })

    @app.post("/api/filters/exclusion")
    async def add_exclusion(request: Request, user=Depends(_auth_require_role("controller", "pilot"))):
        body = await request.json()
        store = _cfg_for_user(user).store
        store.add_exclusion(body.get("airline", ""), body["registration"], body.get("description", ""), _owner_for(user))
        return JSONResponse({"ok": True})

    @app.delete("/api/filters/exclusion/{registration}")
    async def delete_exclusion(registration: str, user=Depends(_auth_require_role("controller", "pilot"))):
        store = _cfg_for_user(user).store
        with store._connect() as conn:
            conn.execute("DELETE FROM filter_exclusions WHERE registration = ? AND owner_user_id = ?", (registration, _owner_for(user)))
        return JSONResponse({"ok": True})

    @app.post("/api/filters/rego")
    async def add_rego(request: Request, user=Depends(_auth_require_role("controller", "pilot"))):
        body = await request.json()
        store = _cfg_for_user(user).store
        store.add_rego_watch(body.get("airline", ""), body["registration"], body.get("description", ""), _owner_for(user))
        return JSONResponse({"ok": True})

    @app.delete("/api/filters/rego/{registration}")
    async def delete_rego(registration: str, user=Depends(_auth_require_role("controller", "pilot"))):
        store = _cfg_for_user(user).store
        with store._connect() as conn:
            conn.execute("DELETE FROM filter_regos WHERE registration = ? AND owner_user_id = ?", (registration, _owner_for(user)))
        return JSONResponse({"ok": True})

    @app.post("/api/filters/type")
    async def add_type(request: Request, user=Depends(_auth_require_role("controller", "pilot"))):
        body = await request.json()
        store = _cfg_for_user(user).store
        store.add_type_watch(body["airline"], body["aircraft_type"], _owner_for(user))
        return JSONResponse({"ok": True})

    @app.delete("/api/filters/type")
    async def delete_type(request: Request, user=Depends(_auth_require_role("controller", "pilot"))):
        body = await request.json()
        store = _cfg_for_user(user).store
        with store._connect() as conn:
            conn.execute(
                "DELETE FROM filter_types WHERE airline = ? AND aircraft_type = ? AND owner_user_id = ?",
                (body["airline"], body["aircraft_type"], _owner_for(user)),
            )
        return JSONResponse({"ok": True})

    @app.post("/api/filters/airline")
    async def add_airline(request: Request, user=Depends(_auth_require_role("controller", "pilot"))):
        body = await request.json()
        store = _cfg_for_user(user).store
        store.add_airline_watch(body["icao_code"], body.get("entry_type", "airline"), body.get("name", ""), _owner_for(user))
        return JSONResponse({"ok": True})

    @app.delete("/api/filters/airline/{icao_code}")
    async def delete_airline(icao_code: str, entry_type: str = "airline", user=Depends(_auth_require_role("controller", "pilot"))):
        store = _cfg_for_user(user).store
        with store._connect() as conn:
            conn.execute(
                "DELETE FROM filter_airlines WHERE icao_code = ? AND entry_type = ? AND owner_user_id = ?",
                (icao_code.upper(), entry_type, _owner_for(user)),
            )
        return JSONResponse({"ok": True})

    # Universal — every role can subscribe, each under their own push identity
    # (_push_owner_id, never _owner_id — see that function's docstring for why
    # a Passenger must never share a row with the Controller's own).
    @app.post("/api/push/subscribe")
    async def push_subscribe(request: Request, user=Depends(_auth_current_user)):
        body = await request.json()
        keys = body.get("keys", {})
        app.state.control_store.add_push_subscription(
            user_id=_push_owner_id(user),
            endpoint=body["endpoint"],
            p256dh=keys.get("p256dh", ""),
            auth=keys.get("auth", ""),
            user_agent=request.headers.get("user-agent", ""),
            ts=int(time.time()),
        )
        return JSONResponse({"ok": True})

    @app.delete("/api/push/unsubscribe")
    async def push_unsubscribe(request: Request, user=Depends(_auth_current_user)):
        body = await request.json()
        app.state.control_store.remove_push_subscription(body["endpoint"])
        return JSONResponse({"ok": True})

    @app.get("/api/push/vapid-public-key")
    async def vapid_public_key(user=Depends(_auth_current_user)):
        from push import get_vapid_keys
        _, pub = get_vapid_keys()
        return JSONResponse({"key": pub})

    # Kept as a plain literal list (not imported from monitor.py) — same 5
    # notif_types monitor.py's _FILTERS/_PUSH_TITLE_LABELS produce, duplicated
    # here to avoid a web.py -> monitor.py import for something this small and
    # stable (these exact strings already appear as literals throughout both
    # files with no single shared constant).
    _PUSH_NOTIF_TYPES = [
        "Special Livery", "Watchlist Registration", "Watchlist Aircraft Type",
        "Watchlist Airline", "Rare Plane/Airline", "Military", "Spotting Reminder",
    ]

    _SPOTTING_REMINDER_WEATHER_GATES = {"none", "ignore_severe", "sunny_only"}

    @app.get("/api/push/notification-prefs")
    async def get_push_notification_prefs(user=Depends(_auth_current_user)):
        disabled = set(app.state.control_store.get_disabled_push_notif_types(_push_owner_id(user)))
        return JSONResponse({t: (t not in disabled) for t in _PUSH_NOTIF_TYPES})

    @app.post("/api/push/notification-prefs")
    async def set_push_notification_pref(request: Request, user=Depends(_auth_current_user)):
        body = await request.json()
        notif_type = str(body.get("notif_type") or "")
        if notif_type not in _PUSH_NOTIF_TYPES:
            raise HTTPException(400, "Unknown notification type")
        app.state.control_store.set_push_notif_enabled(_push_owner_id(user), notif_type, bool(body.get("enabled")))
        return JSONResponse({"ok": True})

    @app.get("/api/push/spotting-reminder-prefs")
    async def get_spotting_reminder_prefs(user=Depends(_auth_current_user)):
        prefs = app.state.control_store.get_spotting_reminder_prefs(_push_owner_id(user))
        return JSONResponse(prefs)

    @app.post("/api/push/spotting-reminder-prefs")
    async def set_spotting_reminder_prefs(request: Request, user=Depends(_auth_current_user)):
        import re as _re
        body = await request.json()
        kwargs = {}
        if "send_time" in body:
            send_time = str(body["send_time"] or "")
            if not _re.match(r"^([01]\d|2[0-3]):[0-5]\d$", send_time):
                raise HTTPException(400, "send_time must be HH:MM (24-hour)")
            kwargs["send_time"] = send_time
        if "weather_gate" in body:
            weather_gate = str(body["weather_gate"] or "")
            if weather_gate not in _SPOTTING_REMINDER_WEATHER_GATES:
                raise HTTPException(400, "Unknown weather_gate")
            kwargs["weather_gate"] = weather_gate
        if "min_aircraft" in body:
            try:
                min_aircraft = int(body["min_aircraft"])
            except (TypeError, ValueError):
                raise HTTPException(400, "min_aircraft must be an integer")
            if min_aircraft < 2:
                raise HTTPException(400, "min_aircraft must be at least 2")
            kwargs["min_aircraft"] = min_aircraft
        app.state.control_store.set_spotting_reminder_prefs(_push_owner_id(user), **kwargs)
        return JSONResponse({"ok": True})

    # ── Static file serving ─────────────────────────────────────────────────
    # Mount static files; index.html served at root

    _NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}

    @app.get("/static/app.js")
    async def serve_appjs():
        f = STATIC_DIR / "app.js"
        return FileResponse(str(f), media_type="application/javascript", headers=_NO_CACHE)

    @app.get("/static/index.html")
    async def serve_indexhtml():
        f = STATIC_DIR / "index.html"
        return FileResponse(str(f), media_type="text/html", headers=_NO_CACHE)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/manifest.json")
    async def manifest():
        f = STATIC_DIR / "manifest.json"
        if f.exists():
            return FileResponse(str(f), media_type="application/manifest+json")
        raise HTTPException(404)

    @app.get("/sw.js")
    async def service_worker():
        f = STATIC_DIR / "sw.js"
        if f.exists():
            return FileResponse(str(f), media_type="application/javascript",
                                headers={**_NO_CACHE, "Service-Worker-Allowed": "/"})
        raise HTTPException(404)

    @app.get("/icons/{name}")
    async def icon(name: str):
        f = STATIC_DIR / "icons" / name
        if f.exists():
            return FileResponse(str(f))
        raise HTTPException(404)

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """Serve index.html for all non-API routes (SPA client-side routing)."""
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return Response("SpotAlert web UI not built yet.", status_code=200)

    return app


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

app = create_app()
