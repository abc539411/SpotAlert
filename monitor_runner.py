"""Runs the monitor/military polling for all watched airports.

Split out from main.py so web.py's runtime add-airport endpoint can spawn a new
airport's tasks without a circular import (main.py imports create_app from web.py).

Scheduling model: a SHARED rotation (one task each for arrivals and military
baseline checks) walks through every currently-watched airport once per
check_interval, evenly spaced (interval / N) in watch order — recomputed fresh
at the start of every cycle, so adding or removing a watched airport reflows
the whole group's spacing starting from the NEXT cycle boundary, without
disrupting whichever checks are already scheduled for the current one. This
replaces the earlier design of N independent self-timing loops, which each
locked in a stagger offset once (at startup or add-time) and never resynced —
so the group's spacing silently went stale as soon as the airport count
changed.

A brand-new airport's first-ever check is NOT part of this rotation — it's
fired immediately, out of band, by web.py's controller_add_airport right after
the airport is registered, so the Controller sees data for it right away
instead of waiting for the next cycle boundary. It's included in the rotation
from that next boundary onward like everyone else.

Force-check (/api/force-check) is likewise handled out of band, by a small
per-airport poller task (run_force_check_poller) rather than by the rotation
itself — resetting one airport's timer doesn't compose cleanly with a shared
group rotation, so a manual force-check just runs an extra check immediately
without otherwise perturbing the rotation's own schedule. The two trigger
paths are serialized per-airport via cfg.check_lock so they can never run a
check for the same airport concurrently.

The web server and this monitor loop run as two separate OS processes (see
monitor_service.py) so they no longer share a Python GIL — a long synchronous
stretch of one airport's check (DB writes, CPU-bound clustering, etc.) can no
longer starve uvicorn's ability to service web requests, which is what this
whole split was for. That separation means anything that used to be an
in-process asyncio primitive shared between the two (an asyncio.Event for
force-check, direct dict/task mutation for add/remove-airport) had to become
a cross-process signal instead — force-check is now a control_store-backed
request row polled every FORCE_CHECK_POLL_SECS (run_force_check_poller), and
add/remove-airport is discovered by periodically re-deriving the watched-
airport set from control_store and reconciling cfgs/tasks to match
(run_cfg_reconciliation_loop) rather than web.py spawning/cancelling tasks in
this process directly.

Military polling is a separate, single SHARED loop (run_military_shared_loop)
covering every watched airport with one fetch per cycle — adsb.fi's endpoint
returns global traffic, not scoped to any one airport, so N independent
per-airport pollers would just be N uncoordinated calls to the same shared
endpoint. Its interval speeds up to ~60s whenever ANY airport currently has
an active rapid-tracked visit, and returns to the (much longer) baseline
interval otherwise — see that function's own docstring for details.
"""
from __future__ import annotations

import asyncio
import logging
import time as _time

from monitor import run_check, check_spotting_reminder
from military import check_military, fetch_military_with_retry, MILITARY_RAPID_INTERVAL_SECS

log = logging.getLogger(__name__)


class _NoopBot:
    """Drop-in replacement for telegram.Bot — all sends are silent no-ops."""
    async def send_message(self, *a, **kw): pass
    async def send_photo(self, *a, **kw): pass


class _FakeJob:
    def __init__(self, data): self.data = data


class _FakeContext:
    """Minimal context stub so monitor.py / military.py run without Telegram."""
    def __init__(self, cfg):
        from datetime import datetime, timezone
        self.bot_data = {"cfg": cfg, "start_time": datetime.now(timezone.utc)}
        self.bot = _NoopBot()
        self.job = _FakeJob(cfg.chat_id)


async def _run_arrivals_check(cfg) -> None:
    """One arrivals check for cfg, with logging/system_status bookkeeping —
    shared by the rotation and the force-check listener. Serialized per-cfg via
    cfg.check_lock so the two trigger paths can never run concurrently for the
    same airport."""
    import system_status as _ss
    scope = cfg.airport_iata
    async with cfg.check_lock:
        ctx = _FakeContext(cfg)
        try:
            await run_check(ctx)
            _ss.record_task('arrivals_check', True, scope=scope)
        except Exception as _e:
            log.exception("Arrivals check failed (%s)", scope)
            _ss.record_task('arrivals_check', False, str(_e), scope=scope)


async def run_monitor_rotation(cfgs: dict) -> None:
    """Shared scheduler for every watched airport's FR24 arrivals check — see
    module docstring. cfgs is app.state.cfgs itself (same object main.py and
    web.py mutate on add/remove) — read fresh every cycle so runtime changes
    are picked up automatically."""
    import system_status as _ss
    cycle_start = _time.time()
    while True:
        airports = list(cfgs.values())
        n = len(airports)
        if n == 0:
            await asyncio.sleep(5)
            cycle_start = _time.time()
            continue
        interval = airports[0].check_interval
        slot_duration = interval / n
        for i, cfg in enumerate(airports):
            if cfg.airport_iata not in cfgs:
                continue  # removed mid-cycle — its slot is simply skipped
            scope = cfg.airport_iata
            target_ts = cycle_start + i * slot_duration
            _ss.set_next_run('arrivals_check', int(target_ts), scope=scope)
            wait_secs = target_ts - _time.time()
            if wait_secs > 0:
                await asyncio.sleep(wait_secs)
            await _run_arrivals_check(cfg)
            # Predict this airport's own next slot (same position, one interval
            # later) immediately, rather than leaving the Scheduled Tasks display
            # showing an already-past "next run" for it until the whole cycle
            # loops back around and every airport's next_ts gets recomputed at once.
            _ss.set_next_run('arrivals_check', int(target_ts + interval), scope=scope)
        cycle_start += interval


FORCE_CHECK_POLL_SECS = 2  # cheap indexed SELECT — fast enough to feel responsive
                           # from a button click without polling aggressively


async def run_force_check_poller(cfg) -> None:
    """Runs cfg's arrivals check immediately whenever /api/force-check writes a
    request row for it — out of band from run_monitor_rotation's own schedule,
    which continues undisturbed (see module docstring). Replaces the old
    asyncio.Event-based design (cfg.check_now_event) now that the web process
    (which handles the HTTP request) and this monitor process are no longer
    the same process — an in-process Event can't be set from a different OS
    process, so the trigger is a control_store row instead, polled here."""
    while True:
        await asyncio.sleep(FORCE_CHECK_POLL_SECS)
        try:
            if cfg.control_store.pop_pending_force_check(cfg.airport_iata):
                await _run_arrivals_check(cfg)
        except Exception:
            log.exception("Force-check poller failed (%s)", cfg.airport_iata)


CFG_RECONCILE_POLL_SECS = 5  # how quickly an add/remove-airport action in the
                             # web process is picked up here


async def run_cfg_reconciliation_loop(cfgs: dict, fr_api, control_store, primary_store) -> None:
    """Keeps cfgs (and this process's per-airport poller tasks) in sync with
    control_store's watched_airports table. Replaces the old design where
    web.py's controller_add_airport/controller_remove_airport directly
    mutated THIS SAME in-memory cfgs dict and spawned/cancelled tasks in this
    process — that only worked because the web server and monitor loop used
    to be one process. Now they're separate (see monitor_service.py), so
    add/remove is discovered by polling here instead.

    cfgs is pre-populated by the caller (build_cfgs_for_watched_airports) with
    whatever's already watched at startup — this loop's first iteration gives
    each of those a poller task too (nothing distinguishes "already existed at
    startup" from "found on the very first reconciliation tick" — see the
    poller_tasks bookkeeping below), then continues polling every
    CFG_RECONCILE_POLL_SECS to pick up runtime changes."""
    from bootstrap import build_config
    from store import SqliteStore
    import system_status as _ss

    poller_tasks: dict = {}  # airport_iata -> asyncio.Task (force-check poller)

    while True:
        try:
            watched_rows = control_store.get_active_watched_airports()
            watched_iatas = {row["airport_iata"] for row in watched_rows}

            for iata in set(cfgs.keys()) - watched_iatas:
                log.info("Reconciliation: %s no longer watched — stopping", iata)
                cfgs.pop(iata, None)
                task = poller_tasks.pop(iata, None)
                if task:
                    task.cancel()

            for row in watched_rows:
                iata = row["airport_iata"]
                is_new = iata not in cfgs
                if is_new:
                    store = (primary_store if row["db_path"] == primary_store.db_path
                             else SqliteStore(row["db_path"]))
                    cfg = build_config(fr_api, store, None, control_store)
                    cfg.check_lock = asyncio.Lock()
                    cfgs[iata] = cfg
                    log.info("Reconciliation: new watched airport %s — starting", iata)

                if iata not in poller_tasks:
                    cfg = cfgs[iata]
                    t = asyncio.create_task(run_force_check_poller(cfg), name=f"force-check-{iata}")
                    t.add_done_callback(log_task_result)
                    poller_tasks[iata] = t

                if is_new:
                    cfg = cfgs[iata]
                    t1 = asyncio.create_task(_run_arrivals_check(cfg), name=f"first-check-{iata}")
                    t1.add_done_callback(log_task_result)
                    t2 = asyncio.create_task(run_immediate_military_check(cfg), name=f"first-military-check-{iata}")
                    t2.add_done_callback(log_task_result)
        except Exception:
            log.exception("Cfg reconciliation loop failed")
        await asyncio.sleep(CFG_RECONCILE_POLL_SECS)


async def run_backup_loop(control_store, cfgs: dict) -> None:
    """Backs up control.db plus every active airport's own DB file. Moved here
    (from main.py) as part of the web/monitor process split — data-layer
    maintenance now lives with the monitor process, which already owns all
    per-airport write activity, rather than the web process."""
    import system_status as _ss
    while True:
        await asyncio.sleep(86400)
        try:
            control_store_backup_path = control_store.db_path + ".bak"
            import shutil
            shutil.copy2(control_store.db_path, control_store_backup_path)
            log.info("Control DB backup saved: %s", control_store_backup_path)
            for cfg in cfgs.values():
                path = cfg.store.backup()
                log.info("DB backup saved: %s (%s)", path, cfg.airport_iata)
            _ss.record_task('db_backup', True)
        except Exception as _e:
            log.exception("DB backup failed")
            _ss.record_task('db_backup', False, str(_e))


async def _process_military_for_airport(cfg, military: list) -> None:
    """Feeds one already-fetched, shared military list through cfg's own
    location-based filtering/tracking (check_military itself does no
    network I/O — see military.py's fetch_military_with_retry for the one
    shared fetch this is fed from)."""
    import system_status as _ss
    scope = cfg.airport_iata
    ctx = _FakeContext(cfg)
    try:
        await check_military(ctx, military)
        _ss.record_task('military_check', True, scope=scope)
    except Exception as _e:
        log.exception("Military check failed (%s)", scope)
        _ss.record_task('military_check', False, str(_e), scope=scope)


async def run_immediate_military_check(cfg) -> None:
    """One-off fetch + process for a single, just-added airport — mirrors
    _run_arrivals_check's role for controller_add_airport (see web.py): so
    the Controller sees military data for the new airport right away instead
    of waiting for run_military_shared_loop's next cycle. Does its own fetch
    (fetch_military_with_retry) rather than waiting to be fed one, since no
    shared-loop cycle is currently in flight to piggyback on."""
    military = await fetch_military_with_retry()
    await _process_military_for_airport(cfg, military)


async def run_military_shared_loop(cfgs: dict) -> None:
    """Single shared poller for military traffic, covering every watched
    airport — replaces the earlier design of N independent per-airport
    pollers (a baseline rotation plus a dedicated rapid-tracking task each).
    adsb.fi's /api/v2/mil endpoint returns GLOBAL traffic, not scoped to any
    one airport (see military.py's module docstring), so there's exactly one
    fetch per cycle here, and every airport just filters its own slice of
    that SAME response — N independent pollers were previously N
    uncoordinated calls to one shared endpoint, which could exceed adsb.fi's
    rate limit even though each individual airport's own polling rate was
    well under it.

    Interval is dynamic: MILITARY_RAPID_INTERVAL_SECS (60s) whenever ANY
    watched airport currently has an active rapid-tracked visit (so a visit
    anywhere still gets fast, frequent re-polling), else the much longer
    baseline military_check_interval. cfgs is app.state.cfgs itself, read
    fresh every cycle so airport add/remove and any airport's visit
    starting/ending are picked up automatically."""
    import system_status as _ss
    while True:
        airports = list(cfgs.values())
        if not airports:
            await asyncio.sleep(5)
            continue

        try:
            military = await fetch_military_with_retry()
            for cfg in airports:
                _ss.record_api('adsb_fi', True, scope=cfg.airport_iata)
        except Exception as exc:
            log.warning("adsb.fi military query failed: %s", exc)
            for cfg in airports:
                _ss.record_api('adsb_fi', False, str(exc), scope=cfg.airport_iata)
            military = None

        any_rapid = any(cfg.military_rapid_tracking for cfg in airports)
        interval = MILITARY_RAPID_INTERVAL_SECS if any_rapid else airports[0].military_check_interval
        next_ts = int(_time.time()) + interval
        for cfg in airports:
            _ss.set_next_run('military_check', next_ts, scope=cfg.airport_iata)

        if military is not None:
            for cfg in list(cfgs.values()):
                if cfg.airport_iata not in cfgs:
                    continue  # removed mid-cycle
                await _process_military_for_airport(cfg, military)

        await asyncio.sleep(interval)


async def run_spotting_reminder_loop(cfgs: dict) -> None:
    """Checks once a minute whether it's time to send the Controller's daily
    spotting-window reminder — see monitor.check_spotting_reminder for the
    actual gating/send logic. Only the currently-selected airport (per
    ControlStore.owner_last_airport) is ever checked each tick, same
    single-airport scope as every other push-notification type; iterating
    every watched airport here just means whichever one is currently selected
    gets checked regardless of which airport that happens to be."""
    while True:
        for cfg in list(cfgs.values()):
            try:
                await check_spotting_reminder(cfg)
            except Exception:
                log.exception("Spotting reminder check failed (%s)", cfg.airport_iata)
        await asyncio.sleep(60)


def log_task_result(task: "asyncio.Task") -> None:
    """add_done_callback target for tasks spawned outside the original _run_all()
    gather — an uncaught exception in a detached task is silently swallowed
    otherwise."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("Background task %s failed", task.get_name(), exc_info=exc)
