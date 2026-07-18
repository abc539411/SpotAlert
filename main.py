from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys

import uvicorn

from bootstrap import build_cfgs_for_watched_airports, acquire_single_instance_lock
from flightradar24api import FlightRadar24API
from store import SqliteStore
from lightroom import find_catalog
from web import create_app

log = logging.getLogger(__name__)


async def _run_monitor_subprocess(data_dir: str) -> None:
    """Launches monitor_service.py as a genuinely separate OS process (not just
    an asyncio task) so its GIL is independent of the web server's — a long
    synchronous stretch in one airport's check can no longer starve uvicorn's
    ability to accept/serve requests, which is the whole reason for this split
    (see monitor_service.py's module docstring for the full story). Restarts
    it with capped exponential backoff if it ever exits, and terminates it
    cleanly if THIS (the web) process is stopped (e.g. Docker sending SIGTERM).

    stdout/stderr are inherited (not piped) so the child's log lines interleave
    directly into `docker logs` alongside the web process's own, same as
    before the split when everything was one process's stdout."""
    backoff = 2
    while True:
        started_at = asyncio.get_event_loop().time()
        log.info("Starting monitor subprocess...")
        proc = await asyncio.create_subprocess_exec(sys.executable, "-u", "monitor_service.py")
        try:
            rc = await proc.wait()
            ran_for = asyncio.get_event_loop().time() - started_at
            log.error("Monitor subprocess exited with code %s after %.0fs — restarting in %ds",
                      rc, ran_for, backoff)
            # A sustained run before dying resets backoff — only a tight crash
            # loop should back off further; a process that ran fine for a
            # while and then died once shouldn't be punished with a long wait.
            backoff = 2 if ran_for > 120 else min(backoff * 2, 60)
        except asyncio.CancelledError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            raise
        await asyncio.sleep(backoff)


def main() -> None:
    log_format = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(log_format)

    os.makedirs("logs", exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        "logs/spotalert.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(log_format)

    logging.basicConfig(level=logging.INFO, handlers=[stdout_handler, file_handler])

    data_dir = "data/"
    os.makedirs(data_dir, exist_ok=True)

    acquire_single_instance_lock(data_dir, role="web")

    fr_api = FlightRadar24API()

    # Warm up cloudscraper session — hits the FR24 homepage so Cloudflare issues
    # a cf_clearance cookie before any API calls are made. The monitor process
    # does its own independent warm-up too (separate process, separate fr_api
    # instance, separate curl_cffi session) — a small amount of duplicated
    # startup work in exchange for not sharing HTTP client state cross-process.
    try:
        from flightradar24api.request import _scraper
        _scraper.get("https://www.flightradar24.com/", timeout=10)
        log.info("Cloudflare warm-up complete")
    except Exception as _e:
        log.warning("Cloudflare warm-up failed (will retry on first API call): %s", _e)

    # primary_store is always the original data/spotalert.db — the first-ever watched
    # airport, kept at its existing path so an upgraded deployment needs zero data
    # migration. Additional airports each get their own SqliteStore(db_path) instead.
    primary_store = SqliteStore(os.path.join(data_dir, "spotalert.db"))
    primary_store.migrate_from_csv_folder(data_dir)

    import system_status as _ss
    _ss.init(primary_store)  # process-wide task/health status — bound to one store for now

    from control_store import ControlStore
    control_store = ControlStore(os.path.join(data_dir, "control.db"))

    catalog = find_catalog()
    # resume_military=False: this cfg copy is read-only display use in the web
    # process (Feed/Search/Settings) — military polling only ever runs in the
    # monitor process, so reconstructing military_rapid_tracking here would
    # just be wasted startup work with nothing ever reading it.
    cfgs = build_cfgs_for_watched_airports(fr_api, control_store, primary_store, data_dir,
                                            catalog, resume_military=False)

    for cfg in cfgs.values():
        _backfilled = cfg.store.backfill_arrival_dates(cfg.airport_tz)
        if _backfilled:
            log.info("Backfilled arrival_date for %d flight_events rows (%s)", _backfilled, cfg.airport_iata)

    port = int(os.environ.get("WEB_PORT", "8088"))
    for cfg in cfgs.values():
        log.info("Serving %s (%s)", cfg.airport_name, cfg.airport_iata)
    log.info("Web on :%d", port)

    web_app = create_app(cfgs, control_store=control_store, fr_api=fr_api, data_dir=data_dir)
    web_config = uvicorn.Config(web_app, host="0.0.0.0", port=port, log_level="warning",
                                timeout_graceful_shutdown=1)
    web_server = uvicorn.Server(web_config)

    async def _run_all():
        # The default executor is used by every asyncio.to_thread() call this
        # process makes (web.py's own FR24/photo lookups, e.g.) AND by
        # Starlette's StaticFiles for every /static/* disk read. Sized
        # generously so a burst of either can't queue behind the other.
        from concurrent.futures import ThreadPoolExecutor
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=48))

        await asyncio.gather(
            web_server.serve(),
            _run_monitor_subprocess(data_dir),
        )

    asyncio.run(_run_all())


if __name__ == "__main__":
    main()
