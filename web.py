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
import time
from pathlib import Path
from typing import Any, List as _List
import os as _os

VERSION = "1.0.0"


def _system_info() -> dict:
    """Return basic host system info using stdlib only."""
    info: dict[str, str] = {}
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

from fastapi import FastAPI, HTTPException, Query as _Query, Request, Response, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Cache for /api/live-status — one shared airport page fetch, reused for 90s
_live_status_cache: dict = {"ts": 0, "schedule": None}

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
) -> list:
    """Return cluster list for one day.

    Each event is an independent timestamped unit — either an arrival or a departure.
    Qualification and light zone use the event's own ts against this day's sunrise/sunset.
    No cross-day checks needed: the caller buckets events by their own date.
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

    return [{"start_ts": min(e[0] for e in section), "end_ts": c_end_ts,
             "start_local_min": _local_min(min(e[0] for e in section)),
             "end_local_min":   _local_min(c_end_ts),
             "recommended_start_ts":        rec_start,
             "recommended_start_local_min": _local_min(rec_start),
             "alternative_windows": alt_wins, "show_window": win_count >= 2,
             "flights": out_events, "lulls": lulls}]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(cfg=None) -> FastAPI:
    """
    cfg: AppConfig instance (when running integrated with the monitor loop).
    If None, loads config/store from disk (standalone mode).
    """
    app = FastAPI(title="SpotAlert", docs_url=None, redoc_url=None)

    # State shared across request handlers
    app.state.cfg = cfg
    app.state.store = cfg.store if cfg else None

    @app.on_event("startup")
    async def _startup():
        if app.state.store is None:
            store, settings, catalog = _load_standalone()
            app.state.store = store
            app.state.settings = settings
            app.state.catalog = catalog
        else:
            app.state.settings = {}
            app.state.catalog = cfg.catalog if cfg else None
        # Refresh ICAO type list in background (no-op if < 90 days old)
        import threading as _thr
        _thr.Thread(target=app.state.store.refresh_icao_type_list, daemon=True).start()
        # Pre-warm collection stats cache and start periodic refresh thread
        _thr.Thread(target=_col_compute_stats, daemon=True).start()
        _col_start_bg_refresh()

    # ── API routes ──────────────────────────────────────────────────────────

    @app.post("/api/restart")
    async def restart_backend():
        """Exit the process — Docker will auto-restart; on PC restart manually."""
        import asyncio, os
        async def _do_exit():
            await asyncio.sleep(0.5)
            os._exit(0)
        asyncio.create_task(_do_exit())
        return JSONResponse({"ok": True, "msg": "Restarting…"})

    @app.post("/api/refresh-fr24")
    async def refresh_fr24():
        """Re-seed FR24 cookies from disk (call after copying fresh .fr24_cookies.pkl to data/)."""
        from flightradar24api.request import reload_cookies
        ok = reload_cookies()
        return JSONResponse({"ok": ok, "msg": "Cookies reloaded" if ok else "No cookie file found"})

    @app.post("/api/force-check")
    async def force_check():
        cfg = app.state.cfg
        if cfg is None:
            raise HTTPException(400, "Only available in integrated mode")
        import asyncio
        from datetime import datetime, timezone
        from monitor import run_check

        class _Bot:
            async def send_message(self, *a, **kw): pass
            async def send_photo(self, *a, **kw): pass

        class _Ctx:
            def __init__(self):
                self.bot_data = {"cfg": cfg, "start_time": datetime.now(timezone.utc)}
                self.bot = _Bot()
                self.job = type("J", (), {"data": cfg.chat_id})()

        asyncio.create_task(run_check(_Ctx()))
        return JSONResponse({"ok": True})

    @app.get("/api/live-status/{registration}")
    async def get_live_status(registration: str):
        """Check if the aircraft is currently on the ground at the local airport.
        Uses the airport schedule's ground section — the same API call the monitor uses.
        Called lazily only when stored status inference returns N/A."""
        cfg_ = app.state.cfg
        if not cfg_ or not getattr(cfg_, 'fr_api', None):
            return JSONResponse({"status": None})
        try:
            reg_upper = registration.upper().strip()
            now_ts_ = int(time.time())
            # Reuse cached airport page for 90s — prevents burst FR24 calls when multiple cards open
            if now_ts_ - _live_status_cache["ts"] > 90 or _live_status_cache["schedule"] is None:
                data = cfg_.fr_api.get_airport_details(code=cfg_.airport_code, page=-1)
                _live_status_cache["schedule"] = data["airport"]["pluginData"]["schedule"]
                _live_status_cache["ts"] = now_ts_
            schedule = _live_status_cache["schedule"]

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
    async def get_aircraft(registration: str):
        cfg   = app.state.cfg
        store = app.state.store
        airport_iata = (cfg.airport_iata if cfg else None) or store.load_setting("AIRPORT_CODE") or getattr(app.state, 'settings', {}).get("AIRPORT_CODE") or ""
        result: dict = {}

        # Lightroom: last spotted + all sessions
        catalog = (cfg.catalog if cfg else None) or getattr(app.state, 'catalog', None)
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

        # Next departure prediction — look up most recent flight_number from flight_arrivals
        if airport_iata:
            with store._connect() as conn:
                row = conn.execute(
                    "SELECT flight_number, arrival_ts FROM flight_arrivals "
                    "WHERE registration = ? AND flight_number IS NOT NULL "
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
                    from monitor import _derive_manufacturer
                    rd = cfg.fr_api.get_rego_details(registration.upper())
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
    async def get_feed(days: int = 30):
        import datetime, json as _json, pytz

        cfg_   = app.state.cfg
        store_ = app.state.store
        airport_iata_ = (cfg_.airport_iata if cfg_ else None) or store_.load_setting("AIRPORT_CODE") or ""
        airport_name_ = (cfg_.airport_name if cfg_ else None) or ""
        tz_name = (
            store_.load_setting("WEB_TIMEZONE")
            or (getattr(cfg_, 'airport_tz', None) if cfg_ else None)
            or store_.load_setting("_airport_tz")
            or "UTC"
        )
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
                       fe.current_status, fe.arr_label, fe.airline_icao,
                       fd.dep_flight, fd.dep_ts, fd.dep_dest_iata, fd.dep_dest_name,
                       fd.is_prediction, fd.dep_label, fd.dep_confidence,
                       a.photo_url, a.manufacturer,
                       sh.last_seen_ts AS airport_last_seen_ts
                FROM flight_arrivals fe
                LEFT JOIN flight_departures fd ON fd.arrival_id = fe.id
                LEFT JOIN airframes a        ON a.registration  = fe.registration
                LEFT JOIN rego_sightings sh  ON sh.registration = fe.registration
                WHERE fe.first_seen_ts >= ? AND fe.flight_number IS NOT NULL
                ORDER BY fe.arrival_ts ASC
            """, (cutoff_ts,)).fetchall()

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

            events.append({
                "registration":         row["registration"],
                "photo_url":            row["photo_url"] or "",
                "manufacturer":         row["manufacturer"] or "",
                "detail":               row["detail"] or "",
                "extra_info":           row["extra_info"] or "",
                "airline_icao":         row["airline_icao"] or "",
                "notif_types":          notif_types,
                "airport_last_seen_ts": row["airport_last_seen_ts"],
                "arr_date":  arr_date,
                "dep_date":  dep_date,
                "flight": {
                    "flight_number":  fn,
                    "arrival_ts":     arr_ts,
                    "arr_local_min":  arr_local_min,
                    "origin_iata":    row["origin_iata"],
                    "origin_name":    row["origin_name"],
                    "arr_label":      row["arr_label"] or None,
                    "dep_flight":     dep_flight,
                    "dep_local_min":  dep_local_min,
                    "dep_ts":         dep_ts_val,
                    "dep_dest_iata":   dep_dest_iata,
                    "dep_dest_name":   dep_dest_name,
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
    async def get_recommendation():
        import datetime as _dt2, json as _json, pytz

        cfg_   = app.state.cfg
        store_ = app.state.store

        airport_iata_ = (cfg_.airport_iata if cfg_ else None) or store_.load_setting("AIRPORT_CODE") or ""
        tz_name = (store_.load_setting("WEB_TIMEZONE")
                   or (getattr(cfg_, 'airport_tz', None) if cfg_ else None)
                   or store_.load_setting("_airport_tz") or "UTC")
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

        days_result = []
        for date_str, label, is_today, i in day_meta:
            row = cached.get(date_str)
            if not row:
                # No cache yet (first startup or no flights) — skip future days, show empty past
                if i < 0:
                    continue
                days_result.append({
                    "date": date_str, "label": label, "is_today": is_today,
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

            q_regs = {f["registration"] for c in clusters for f in c.get("flights", []) if f.get("qualifying")}
            days_result.append({
                "date": date_str, "label": label, "is_today": is_today,
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
    _col_stats_cache: dict = {'data': None, 'ts': 0}

    def _col_catalog_path():
        """Return the configured Lightroom catalog path as a string."""
        cfg_ = app.state.cfg
        # Try cfg.catalog._path first (integrated mode)
        cat_obj = getattr(cfg_, 'catalog', None) if cfg_ else None
        if cat_obj and hasattr(cat_obj, '_path'):
            return str(cat_obj._path)
        # Standalone: use find_catalog
        try:
            from lightroom import find_catalog
            cat = find_catalog()
            if cat and hasattr(cat, '_path'):
                return str(cat._path)
        except Exception:
            pass
        return None

    def _col_stats_ttl_secs():
        try:
            return int(app.state.store.load_setting('CHECK_INTERVAL_MINUTES') or 30) * 60
        except Exception:
            return 1800

    def _col_compute_stats():
        """Compute catalog stats and store in cache. Returns the data dict."""
        import time as _time
        try:
            data = _col_stats_sync()
            _col_stats_cache['data'] = data
            _col_stats_cache['ts'] = _time.time()
            return data
        except Exception:
            return _col_stats_cache.get('data')

    def _col_start_bg_refresh():
        import threading, time as _time
        def _loop():
            while True:
                ttl = _col_stats_ttl_secs()
                _time.sleep(ttl)
                try:
                    _col_compute_stats()
                    log.info("Collection stats cache refreshed (periodic)")
                except Exception as e:
                    log.warning("Collection stats bg refresh failed: %s", e)
        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    def _col_stats_sync():
        """Run all catalog queries and return the stats dict. Called from cache layer."""
        from pathlib import Path as _Path
        from datetime import datetime as _dt, date as _date
        import sqlite3 as _sq
        cat_str = _col_catalog_path()
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
                    last_session = {'date_label': date_label, 'airport': airport or '',
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

        # Keyword stat boxes
        kw_stats = []
        try:
            _kw_con = _sq.connect(str(cat))
            for _i in range(1, 4):
                _kw = app.state.store.load_setting(f'COLLECTION_KW_STAT_{_i}') or ''
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
    async def get_catalog_stats(force: bool = False):
        import time as _time
        import asyncio
        if not force:
            cached = _col_stats_cache.get('data')
            if cached is not None and (_time.time() - _col_stats_cache['ts']) < _col_stats_ttl_secs():
                return JSONResponse(cached)
        # Compute in thread pool so we don't block the event loop
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, _col_compute_stats)
        except Exception as e:
            raise HTTPException(500, str(e))
        if data is None:
            raise HTTPException(500, "Failed to compute stats")
        return JSONResponse(data)

    @app.get("/api/catalog-stats/airline")
    async def get_catalog_airline_details(airline: str = ""):
        from pathlib import Path as _Path
        import sqlite3 as _sq
        cat_str = _col_catalog_path()
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
    async def get_catalog_airport_details(airport: str = ""):
        from pathlib import Path as _Path
        import sqlite3 as _sq
        cat_str = _col_catalog_path()
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
    async def get_catalog_type_details(family: str = ""):
        from pathlib import Path as _Path
        import sqlite3 as _sq
        cat_str = _col_catalog_path()
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
    async def get_catalog_rego_sessions(rego: str = ""):
        from pathlib import Path as _Path
        import sqlite3 as _sq
        cat_str = _col_catalog_path()
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

    @app.get("/api/catalog-stats/tags")
    async def get_catalog_session_tag_list():
        from pathlib import Path as _Path
        import sqlite3 as _sq
        cat_str = _col_catalog_path()
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
    async def get_catalog_session_aircraft(date: str = "", airport: str = "", filter_tags: str = ""):
        from pathlib import Path as _Path
        import sqlite3 as _sq
        cat_str = _col_catalog_path()
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
        _thr.Thread(target=lambda: app.state.store.refresh_icao_type_list(force=True), daemon=True).start()
        return JSONResponse({'ok': True, 'message': 'Refresh started in background'})

    @app.get("/api/aircraft-types")
    async def list_aircraft_types():
        with app.state.store._connect() as conn:
            rows = conn.execute(
                "SELECT icao, name FROM aircraft_types WHERE source='user' ORDER BY icao"
            ).fetchall()
        return JSONResponse([{'icao': r[0], 'name': r[1]} for r in rows])

    # ── Search endpoints ──────────────────────────────────────────────────────
    _SEARCH_PLUGIN = 'ch.aviationphoto.aircraftmetadata'
    _SEARCH_SKIP_KW = {'Featured', 'SPTA', 'AircraftMetadata-RegNotFound', 'AircraftMetadata-WrongReg', 'Cleaned'}

    def _search_catalog_path():
        catalog = (cfg.catalog if cfg else None) or getattr(app.state, 'catalog', None)
        if catalog and hasattr(catalog, '_path'):
            return catalog._path
        return None

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
    async def collection_livery_stats():
        cat_path = _search_catalog_path()
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
    async def search_flight_filters():
        store_ = app.state.store
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
    async def search_flights(rego: str = ""):
        rego = rego.strip().upper()
        store_ = app.state.store
        pat = f'%{rego}%' if rego else '%'
        with store_._connect() as conn:
            rows = conn.execute("""
                SELECT fe.registration, fe.flight_number, fe.arrival_ts,
                       fe.origin_iata, fe.origin_name, fe.current_status, fe.detail,
                       fe.extra_info, fe.notif_types, fe.airline_icao,
                       fd.dep_flight, fd.dep_ts, fd.dep_dest_iata, fd.dep_dest_name,
                       a.manufacturer, sh.last_seen_ts,
                       ac.country_code AS origin_country_code
                FROM flight_arrivals fe
                LEFT JOIN flight_departures fd ON fd.arrival_id = fe.id
                LEFT JOIN airframes a ON a.registration = fe.registration
                LEFT JOIN rego_sightings sh ON sh.registration = fe.registration
                LEFT JOIN airports ac ON ac.iata = fe.origin_iata
                WHERE UPPER(TRIM(fe.registration)) LIKE ?
                ORDER BY fe.arrival_ts DESC
            """, (pat,)).fetchall()

            matched_regs = {row["registration"].upper() for row in rows}

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
    async def search_route_filters():
        store_ = app.state.store
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
        def _iata_label(iata, name):
            if name and name.strip() and name.upper() != iata.upper():
                return f"{iata} · {name}"
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
        cfg_ = getattr(app.state, 'cfg', None)
        home_iata = (cfg_.airport_iata if cfg_ else None) or store_.load_setting("AIRPORT_CODE") or ""
        home_name = (cfg_.airport_name if cfg_ else None) or ""
        home_label = f"{home_iata} · {home_name}" if home_name and home_name.upper() != home_iata.upper() else home_iata
        return JSONResponse({'origins': origins, 'dests': dests, 'airlines': airlines, 'home': home_label})

    @app.get("/api/search/route")
    async def search_route(fn: str = "", origin: _List[str] = _Query(default=[]),
                           dest: _List[str] = _Query(default=[]), airline: _List[str] = _Query(default=[])):
        fn = fn.strip().upper()
        store_ = app.state.store
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
                    # Dest: check route_type_tracker first, fall back to flight_arrivals
                    if dests:
                        rth_dest = (r['dest_iata'] or '').upper()
                        fe_dst   = fe_dests.get(fn_key, set())
                        match_dest = (rth_dest and rth_dest in dests) or bool(fe_dst & set(dests))
                        if not match_dest:
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
    async def search_autocomplete():
        cat_path = _search_catalog_path()
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
    ):
        types         = [v for v in type         if v.strip()]
        airlines      = [v for v in airline      if v.strip()]
        manufacturers = [v for v in manufacturer if v.strip()]
        airports      = [v for v in airport      if v.strip()]
        keywords      = [v for v in keyword      if v.strip()]
        rego          = rego.strip()
        if not any([rego, types, airlines, manufacturers, airports, keywords]):
            return JSONResponse({'results': [], 'total': 0})

        cat_path = _search_catalog_path()
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

    def _fleet_fetch_fr24(icao: str) -> list:
        """Fetch current aircraft list for an airline from FR24. Returns list of aircraft dicts."""
        import re as _re, requests as _req
        from bs4 import BeautifulSoup as _BS
        from monitor import _derive_manufacturer as _dmfr
        cards = app.state.store.get_fleet_cards()
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

    def _fleet_refresh_fr24_bg(icao: str) -> None:
        """Background task: re-fetch FR24 fleet and update DB, then refresh photo counts."""
        import time as _time
        try:
            aircraft = _fleet_fetch_fr24(icao)
            if not aircraft:
                return
            _fleet_refresh_photos_bg([icao], aircraft_override={icao: aircraft})
            cards = app.state.store.get_fleet_cards()
            card = next((c for c in cards if c['icao'] == icao), None)
            if card:
                app.state.store.upsert_fleet_card(icao, card['iata'], card['airline'], aircraft,
                                                  updated_at=int(_time.time()))
            log.info("Fleet card %s refreshed from FR24 (%d aircraft)", icao, len(aircraft))
        except Exception as e:
            log.warning("Fleet FR24 refresh failed for %s: %s", icao, e)

    def _fleet_refresh_photos_bg(icao_list: list, aircraft_override: dict = None) -> None:
        """Background task: update photo counts for fleet cards from LR catalog."""
        cat_path = _search_catalog_path()
        if not cat_path or not _os.path.exists(cat_path):
            return
        try:
            import sqlite3 as _sq
            for icao in icao_list:
                cards = app.state.store.get_fleet_cards()
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
                app.state.store.update_fleet_card_photos(icao, updated)
        except Exception as e:
            log.warning("Fleet photo refresh failed: %s", e)

    @app.get("/api/fleet-cards")
    async def get_fleet_cards(background_tasks: BackgroundTasks):
        import time as _time
        cards = app.state.store.get_fleet_cards()
        now = int(_time.time())
        for card in cards:
            if now - (card.get('updated_at') or 0) > _FLEET_REFRESH_SECS:
                background_tasks.add_task(_fleet_refresh_fr24_bg, card['icao'])
        return JSONResponse(cards)

    @app.post("/api/fleet-cards")
    async def save_fleet_card(request: Request):
        import time as _time
        body = await request.json()
        icao    = (body.get('icao') or '').strip().upper()
        iata    = (body.get('iata') or '').strip().upper()
        airline = (body.get('airline') or '').strip()
        aircraft = body.get('aircraft') or []
        if not icao:
            raise HTTPException(400, "icao required")
        app.state.store.upsert_fleet_card(icao, iata, airline, aircraft, updated_at=int(_time.time()))
        return JSONResponse({'ok': True})

    @app.delete("/api/fleet-cards/{icao}")
    async def delete_fleet_card(icao: str):
        app.state.store.delete_fleet_card(icao)
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
            fr_api = app.state.fr_api if hasattr(app.state, 'fr_api') else None
            if fr_api is None:
                from flightradar24api import FlightRadar24API
                fr_api = FlightRadar24API()
            data = fr_api.get_rego_details(rego)
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
    async def refresh_fleet_photos(background_tasks: BackgroundTasks):
        """Triggered after a catalog refresh to update photo counts for all fleet cards."""
        cards = app.state.store.get_fleet_cards()
        if cards:
            background_tasks.add_task(_fleet_refresh_photos_bg, [c['icao'] for c in cards])
        return JSONResponse({'ok': True})

    @app.get("/api/fleet-coverage")
    async def fleet_coverage(code: str = ""):
        """
        Given an IATA (2-char) or ICAO (3-char) airline code, fetch the airline's
        current fleet from FR24 and cross-reference with the LR catalog to show
        which registrations have been photographed.
        """
        import re as _re, requests as _req
        from bs4 import BeautifulSoup as _BS
        from monitor import _derive_manufacturer as _dmfr

        code = code.strip().upper()
        if not code:
            return JSONResponse({'error': 'No code provided'})

        # ── Step 1: resolve IATA ↔ ICAO via FR24 airline list ─────────────
        try:
            fr_api = app.state.fr_api if hasattr(app.state, 'fr_api') else None
            if fr_api is None:
                from flightradar24api import FlightRadar24API
                fr_api = FlightRadar24API()
            airlines = fr_api.get_airlines()
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
            resp = _req.get(fleet_url, headers=hdrs, timeout=20)
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
        cat_path = _search_catalog_path()
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
    async def add_aircraft_type(request: Request):
        body = await request.json()
        icao = (body.get('icao') or '').strip().upper()
        name = (body.get('name') or '').strip()
        if not icao or not name:
            raise HTTPException(400, "icao and name required")
        app.state.store.upsert_aircraft_type(icao, name, source='user')
        return JSONResponse({'ok': True})

    @app.delete("/api/aircraft-types/{icao}")
    async def delete_aircraft_type(icao: str):
        icao = icao.strip().upper()
        with app.state.store._connect() as conn:
            conn.execute("DELETE FROM aircraft_types WHERE icao=? AND source='user'", (icao,))
        return JSONResponse({'ok': True})

    def _logostream_api_key():
        with app.state.store._connect() as _conn:
            row = _conn.execute("SELECT value FROM settings WHERE key='LOGOSTREAM_API_KEY'").fetchone()
        return row[0] if row else ""

    def _fetch_airforce_roundel(icao: str):
        """Fetch air force roundel from GitHub CDN, save to disk. Returns (Path, media_type) or None."""
        import json as _json, requests as _req
        mapping_path = Path(__file__).parent / "static" / "airforce_roundels.json"
        if not mapping_path.exists():
            return None, None
        try:
            mapping = _json.loads(mapping_path.read_text())
        except Exception:
            return None, None
        filename = mapping.get(icao.upper())
        if not filename:
            return None, None
        cache_path = Path(__file__).parent / "static" / "airline_logos" / f"{icao}_af.png"
        if cache_path.exists():
            return cache_path, "image/png"
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
        # For PNGs: reject if logo has almost no colorful content (all-white tails)
        if is_png:
            try:
                from PIL import Image as _Img
                import warnings; warnings.filterwarnings("ignore")
                img = _Img.open(_io.BytesIO(r.content)).convert("RGBA")
                pixels = list(img.getdata())
                visible = [(rr, gg, bb) for rr, gg, bb, aa in pixels if aa > 30]
                if visible:
                    colorful = sum(1 for rr, gg, bb in visible if max(rr, gg, bb) - min(rr, gg, bb) > 40)
                    if colorful / len(visible) < 0.03:
                        raise HTTPException(404, "Effectively white logo excluded")
            except HTTPException:
                raise
            except Exception:
                pass
        ext = "svg" if is_svg else "png"
        media = "image/svg+xml" if is_svg else "image/png"
        cache_path = Path(__file__).parent / "static" / "airline_logos" / f"{icao}.{ext}"
        cache_path.write_bytes(r.content)
        return cache_path, media

    def _cached_logo_path(icao: str):
        """Return (Path, media_type) for a cached logo, checking both .png and .svg."""
        base = Path(__file__).parent / "static" / "airline_logos"
        for ext, mt in (("png", "image/png"), ("svg", "image/svg+xml")):
            p = base / f"{icao}.{ext}"
            if p.exists():
                return p, mt
        return None, None

    @app.get("/api/airline-logo/{icao}")
    async def get_airline_logo(icao: str):
        import re as _re
        icao = icao.upper().strip()
        if not _re.match(r'^[A-Z0-9]{2,4}$', icao):
            raise HTTPException(400, "Invalid ICAO code")
        # Check air force roundel mapping first
        cache_path, media = _fetch_airforce_roundel(icao)
        if not cache_path:
            cache_path, media = _cached_logo_path(icao)
        if not cache_path:
            try:
                cache_path, media = _fetch_and_cache_logo(icao)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(502, f"Logostream fetch failed: {e}")
        from fastapi.responses import FileResponse
        return FileResponse(str(cache_path), media_type=media,
                            headers={"Cache-Control": "public, max-age=2592000"})

    @app.get("/api/airline-logo-name/{name}")
    async def get_airline_logo_by_name(name: str):
        """Fuzzy search by airline name → resolve ICAO → serve tail logo."""
        import re as _re, json as _json, requests as _req
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
            try:
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
                icao = results[0].get("icao", "").upper()
                if not icao:
                    raise HTTPException(404, "No ICAO in search result")
                # Save mapping
                mapping[name_key] = icao
                mapping_path.write_text(_json.dumps(mapping))
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(502, f"Logostream search failed: {e}")
        # Now serve the logo for the resolved ICAO
        cache_path, media = _cached_logo_path(icao)
        if not cache_path:
            try:
                cache_path, media = _fetch_and_cache_logo(icao)
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(502, f"Logo fetch failed: {e}")
        from fastapi.responses import FileResponse
        return FileResponse(str(cache_path), media_type=media,
                            headers={"Cache-Control": "public, max-age=2592000"})

    @app.get("/api/airports")
    async def list_airports():
        with app.state.store._connect() as conn:
            rows = conn.execute(
                "SELECT iata, name, country_code, source FROM airports WHERE source='user' ORDER BY iata"
            ).fetchall()
        return JSONResponse([{'iata': r[0], 'name': r[1], 'country_code': r[2]} for r in rows])

    @app.post("/api/airports")
    async def add_airport(request: Request):
        body = await request.json()
        iata = (body.get('iata') or '').strip().upper()
        name = (body.get('name') or '').strip()
        cc   = (body.get('country_code') or '').strip().upper()
        if not iata or not name:
            raise HTTPException(400, "iata and name required")
        app.state.store.upsert_airport(iata, name, cc, source='user')
        return JSONResponse({'ok': True})

    @app.delete("/api/airports/{iata}")
    async def delete_airport(iata: str):
        iata = iata.strip().upper()
        with app.state.store._connect() as conn:
            conn.execute("DELETE FROM airports WHERE iata=? AND source='user'", (iata,))
        return JSONResponse({'ok': True})

    @app.get("/api/status")
    async def get_status():
        cfg = app.state.cfg
        now = int(time.time())
        result: dict[str, Any] = {"now_ts": now, "rapid_mode": False, "version": VERSION, **_system_info()}
        if cfg is not None:
            result["rapid_mode"] = getattr(cfg, "rapid_mode", False)
            result["airport_name"] = cfg.airport_name
            result["airport_iata"] = cfg.airport_iata
            result["airport_tz"]   = getattr(cfg, "airport_tz", "")
            result["check_interval"] = cfg.check_interval
            result["military_check_interval"] = cfg.military_check_interval
        # Effective timezone (WEB_TIMEZONE override or airport tz)
        store_ = app.state.store
        _eff_tz = (store_.load_setting("WEB_TIMEZONE") if store_ else None) \
                  or result.get("airport_tz") or "UTC"
        result["effective_tz"] = _eff_tz
        try:
            import pytz as _pytz, datetime as _dt
            _tz = _pytz.timezone(_eff_tz)
            result["current_time"] = _dt.datetime.now(_tz).strftime("%H:%M:%S")
        except Exception:
            result["current_time"] = ""
        else:
            s = app.state.settings
            result["airport_code"] = s.get("AIRPORT_CODE", "")
            result["check_interval"] = float(s.get("CHECK_INTERVAL_MINUTES", 30)) * 60
        return JSONResponse(result)

    @app.get("/api/settings")
    async def get_settings():
        store = app.state.store
        result = {}
        with store._connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
        for r in rows:
            result[r["key"]] = r["value"]
        return JSONResponse(result)

    @app.put("/api/settings")
    async def put_settings(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Expected JSON object")
        store = app.state.store
        for key, value in body.items():
            store.save_setting(str(key), str(value))
        return JSONResponse({"ok": True})

    @app.get("/api/filters")
    async def get_filters():
        store = app.state.store
        def _fetch_rows(sql, *args):
            with store._connect() as conn:
                return [dict(r) for r in conn.execute(sql, *args).fetchall()]
        return JSONResponse({
            "filter_exclusions":  _fetch_rows("SELECT id, registration, description FROM filter_exclusions ORDER BY id"),
            "filter_regos":  _fetch_rows("SELECT id, registration, description FROM filter_regos ORDER BY id"),
            "filter_types":  _fetch_rows("SELECT id, airline, aircraft_type FROM filter_types ORDER BY id"),
            "filter_airlines": _fetch_rows("SELECT id, icao_code, entry_type, name FROM filter_airlines ORDER BY id"),
        })

    @app.post("/api/filters/exclusion")
    async def add_exclusion(request: Request):
        body = await request.json()
        store = app.state.store
        store.add_exclusion(body.get("airline", ""), body["registration"], body.get("description", ""))
        return JSONResponse({"ok": True})

    @app.delete("/api/filters/exclusion/{registration}")
    async def delete_exclusion(registration: str):
        store = app.state.store
        with store._connect() as conn:
            conn.execute("DELETE FROM filter_exclusions WHERE registration = ?", (registration,))
        return JSONResponse({"ok": True})

    @app.post("/api/filters/rego")
    async def add_rego(request: Request):
        body = await request.json()
        store = app.state.store
        store.add_rego_watch(body.get("airline", ""), body["registration"], body.get("description", ""))
        return JSONResponse({"ok": True})

    @app.delete("/api/filters/rego/{registration}")
    async def delete_rego(registration: str):
        store = app.state.store
        with store._connect() as conn:
            conn.execute("DELETE FROM filter_regos WHERE registration = ?", (registration,))
        return JSONResponse({"ok": True})

    @app.post("/api/filters/type")
    async def add_type(request: Request):
        body = await request.json()
        store = app.state.store
        store.add_type_watch(body["airline"], body["aircraft_type"])
        return JSONResponse({"ok": True})

    @app.delete("/api/filters/type")
    async def delete_type(request: Request):
        body = await request.json()
        store = app.state.store
        with store._connect() as conn:
            conn.execute(
                "DELETE FROM filter_types WHERE airline = ? AND aircraft_type = ?",
                (body["airline"], body["aircraft_type"]),
            )
        return JSONResponse({"ok": True})

    @app.post("/api/filters/airline")
    async def add_airline(request: Request):
        body = await request.json()
        store = app.state.store
        store.add_airline_watch(body["icao_code"], body.get("entry_type", "airline"), body.get("name", ""))
        return JSONResponse({"ok": True})

    @app.delete("/api/filters/airline/{icao_code}")
    async def delete_airline(icao_code: str, entry_type: str = "airline"):
        store = app.state.store
        with store._connect() as conn:
            conn.execute(
                "DELETE FROM filter_airlines WHERE icao_code = ? AND entry_type = ?",
                (icao_code.upper(), entry_type),
            )
        return JSONResponse({"ok": True})

    @app.post("/api/push/subscribe")
    async def push_subscribe(request: Request):
        body = await request.json()
        store = app.state.store
        keys = body.get("keys", {})
        store.add_push_subscription(
            endpoint=body["endpoint"],
            p256dh=keys.get("p256dh", ""),
            auth=keys.get("auth", ""),
            user_agent=request.headers.get("user-agent", ""),
            ts=int(time.time()),
        )
        return JSONResponse({"ok": True})

    @app.delete("/api/push/unsubscribe")
    async def push_unsubscribe(request: Request):
        body = await request.json()
        store = app.state.store
        store.remove_push_subscription(body["endpoint"])
        return JSONResponse({"ok": True})

    @app.get("/api/push/vapid-public-key")
    async def vapid_public_key():
        key = os.environ.get("VAPID_PUBLIC_KEY") or ""
        if not key:
            try:
                from environs import Env
                _env = Env()
                _env.read_env("config/config.env")
                key = _env.str("VAPID_PUBLIC_KEY", default="")
            except Exception:
                pass
        return JSONResponse({"key": key})

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
                                headers={"Service-Worker-Allowed": "/"})
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
