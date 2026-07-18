"""Standalone entrypoint for the background monitor loop — arrivals checks,
military polling, spotting reminders, DB backups, and watched-airport
reconciliation. Runs as a genuinely separate OS process from the web server
(main.py spawns it via asyncio.create_subprocess_exec, see
_run_monitor_subprocess there), not just a separate asyncio task within the
same process — the two used to share one process, and a long synchronous
stretch of one airport's check (SQLite writes, CPU-bound clustering, etc.)
could starve uvicorn's ability to service web requests for as long as that
stretch took, since Python's GIL is shared across threads/tasks within one
process but NOT across processes. Splitting into two processes removes that
contention entirely rather than just reducing it (see monitor_runner.py's
module docstring for the fixes that were tried first, and how far they got).

Because this is a separate process, anything that used to be an in-process
asyncio primitive shared with the web process (an asyncio.Event for
force-check, direct dict/task mutation for runtime add/remove-airport) had to
become a cross-process signal instead — see monitor_runner.py's
run_force_check_poller and run_cfg_reconciliation_loop.

Can also be run standalone for local development (`python monitor_service.py`)
without the web server at all.
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys

from bootstrap import build_cfgs_for_watched_airports, acquire_single_instance_lock
from flightradar24api import FlightRadar24API
from store import SqliteStore
from monitor_runner import (
    run_monitor_rotation, run_military_shared_loop, run_spotting_reminder_loop,
    run_backup_loop, run_cfg_reconciliation_loop,
)

log = logging.getLogger(__name__)


def main() -> None:
    log_format = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(log_format)

    os.makedirs("logs", exist_ok=True)
    # Separate log FILE from the web process's logs/spotalert.log — two
    # RotatingFileHandlers on the same path from two different processes would
    # race on rotation. stdout is still shared (inherited from the parent
    # process, which doesn't redirect it — see main.py's _run_monitor_subprocess),
    # so `docker logs` interleaves both processes' output same as before the split.
    file_handler = logging.handlers.RotatingFileHandler(
        "logs/spotalert-monitor.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(log_format)

    logging.basicConfig(level=logging.INFO, handlers=[stdout_handler, file_handler])

    data_dir = "data/"
    os.makedirs(data_dir, exist_ok=True)

    acquire_single_instance_lock(data_dir, role="monitor")

    fr_api = FlightRadar24API()

    # Independent Cloudflare warm-up — separate process, separate fr_api
    # instance/curl_cffi session from the web process's own (see main.py).
    try:
        from flightradar24api.request import _scraper
        _scraper.get("https://www.flightradar24.com/", timeout=10)
        log.info("Cloudflare warm-up complete")
    except Exception as _e:
        log.warning("Cloudflare warm-up failed (will retry on first API call): %s", _e)

    primary_store = SqliteStore(os.path.join(data_dir, "spotalert.db"))
    primary_store.migrate_from_csv_folder(data_dir)

    import system_status as _ss
    _ss.init(primary_store)  # written here (record_task/record_api/set_next_run),
                             # read from the web process's /api/system-tasks handler —
                             # see system_status.py's module docstring for why every
                             # read/write there goes straight to the DB, no caching.

    from control_store import ControlStore
    control_store = ControlStore(os.path.join(data_dir, "control.db"))

    cfgs = build_cfgs_for_watched_airports(fr_api, control_store, primary_store, data_dir)

    for cfg in cfgs.values():
        cfg.check_lock = asyncio.Lock()
        _backfilled = cfg.store.backfill_arrival_dates(cfg.airport_tz)
        if _backfilled:
            log.info("Backfilled arrival_date for %d flight_events rows (%s)", _backfilled, cfg.airport_iata)

    for cfg in cfgs.values():
        log.info("Monitoring %s (%s) — check every %ds", cfg.airport_name, cfg.airport_iata, cfg.check_interval)

    async def _run_all():
        from concurrent.futures import ThreadPoolExecutor
        # Sized generously for the same reason as the web process's own default
        # executor (see main.py) — every asyncio.to_thread() call this process
        # makes (the bulk of monitor.py's Step 2/3/4/5/6/7a/7b DB writes, plus
        # every FR24/adsb.fi/JetPhotos/Open-Meteo network fetch) shares this pool.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=48))

        await asyncio.gather(
            run_monitor_rotation(cfgs),
            run_military_shared_loop(cfgs),
            run_spotting_reminder_loop(cfgs),
            run_backup_loop(control_store, cfgs),
            run_cfg_reconciliation_loop(cfgs, fr_api, control_store, primary_store),
        )

    asyncio.run(_run_all())


if __name__ == "__main__":
    main()
