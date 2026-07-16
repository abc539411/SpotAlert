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
per-airport listener task (run_force_check_listener) rather than by the
rotation itself — resetting one airport's timer doesn't compose cleanly with a
shared group rotation, so a manual force-check just runs an extra check
immediately without otherwise perturbing the rotation's own schedule. The two
trigger paths are serialized per-airport via cfg.check_lock so they can never
run a check for the same airport concurrently.

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


async def run_force_check_listener(cfg) -> None:
    """Runs cfg's arrivals check immediately whenever /api/force-check sets its
    check_now_event — out of band from run_monitor_rotation's own schedule,
    which continues undisturbed (see module docstring)."""
    event = cfg.check_now_event
    while True:
        await event.wait()
        event.clear()
        await _run_arrivals_check(cfg)


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
