"""Runs the monitor/military polling loops for a single AppConfig (one airport).

Split out from main.py so web.py's runtime add-airport endpoint can spawn a new
airport's tasks without a circular import (main.py imports create_app from web.py).
"""
from __future__ import annotations

import asyncio
import logging

from monitor import run_check
from military import check_military, MILITARY_RAPID_INTERVAL_SECS

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


async def run_monitor(cfg) -> None:
    import system_status as _ss
    ctx = _FakeContext(cfg)
    event = cfg.check_now_event
    while True:
        # Wait for the interval to elapse, or for /api/force-check to wake us early.
        # Either way the timer resets from here — a manual check counts as "just ran".
        try:
            await asyncio.wait_for(event.wait(), timeout=cfg.check_interval)
        except asyncio.TimeoutError:
            pass
        event.clear()
        try:
            await run_check(ctx)
            _ss.record_task('arrivals_check', True)
        except Exception as _e:
            log.exception("Arrivals check failed (%s)", getattr(cfg, "airport_iata", "?"))
            _ss.record_task('arrivals_check', False, str(_e))


async def run_military(cfg) -> None:
    import system_status as _ss
    ctx = _FakeContext(cfg)
    while True:
        try:
            await check_military(ctx)
            _ss.record_task('military_check', True)
        except Exception as _e:
            log.exception("Military check failed (%s)", getattr(cfg, "airport_iata", "?"))
            _ss.record_task('military_check', False, str(_e))
        interval = MILITARY_RAPID_INTERVAL_SECS if cfg.military_rapid_tracking else cfg.military_check_interval
        await asyncio.sleep(interval)


def log_task_result(task: "asyncio.Task") -> None:
    """add_done_callback target for tasks spawned outside the original _run_all()
    gather — an uncaught exception in a detached task is silently swallowed
    otherwise."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("Background task %s failed", task.get_name(), exc_info=exc)
