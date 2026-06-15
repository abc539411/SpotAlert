"""
FastAPI web server for SpotAlert PWA.
Run standalone: python -m uvicorn web:app --host 0.0.0.0 --port 8080
Or create via create_app(cfg) for integration with the monitor loop.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# Config/store loader for standalone mode
# ---------------------------------------------------------------------------

def _load_standalone():
    """Load config and store the same way main.py does, for standalone uvicorn runs."""
    import math
    from environs import Env
    from storage import SqliteStore

    config_file = "config/config.env"
    if not os.path.isfile(config_file):
        raise RuntimeError(f"Config not found: {config_file}")

    env = Env()
    env.read_env(config_file)

    filters_dir = "config/filters/"
    os.makedirs(filters_dir, exist_ok=True)
    store = SqliteStore(os.path.join(filters_dir, "spotalert.db"), config_file=config_file)

    settings = {
        "AIRPORT_CODE": store.load_setting("AIRPORT_CODE") or env.str("AIRPORT_CODE", default=""),
        "CHECK_INTERVAL_MINUTES": store.load_setting("CHECK_INTERVAL_MINUTES") or env.str("CHECK_INTERVAL_MINUTES", default="30"),
        "MILITARY_CHECK_INTERVAL_MINUTES": store.load_setting("MILITARY_CHECK_INTERVAL_MINUTES") or env.str("MILITARY_CHECK_INTERVAL_MINUTES", default="15"),
    }
    return store, settings


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
            store, settings = _load_standalone()
            app.state.store = store
            app.state.settings = settings
        else:
            app.state.settings = {}

    # ── API routes ──────────────────────────────────────────────────────────

    @app.get("/api/flights")
    async def get_flights():
        store = app.state.store
        rows = store.get_tracked_flights()
        return JSONResponse([dict(r) for r in rows])

    @app.get("/api/daily")
    async def get_daily():
        store = app.state.store
        rows = store.get_daily_flights()
        return JSONResponse([dict(r) for r in rows])

    @app.get("/api/history")
    async def get_history(days: int = 7):
        store = app.state.store
        rows = store.get_notification_history(days=max(1, min(days, 30)))
        return JSONResponse([dict(r) for r in rows])

    @app.get("/api/stats")
    async def get_stats():
        store = app.state.store
        notif_stats = store.get_notification_stats()
        return JSONResponse(notif_stats)

    @app.get("/api/status")
    async def get_status():
        cfg = app.state.cfg
        now = int(time.time())
        result: dict[str, Any] = {"now_ts": now, "rapid_mode": False}
        if cfg is not None:
            result["rapid_mode"] = getattr(cfg, "rapid_mode", False)
            result["airport_name"] = cfg.airport_name
            result["airport_iata"] = cfg.airport_iata
            result["check_interval"] = cfg.check_interval
            result["military_check_interval"] = cfg.military_check_interval
        else:
            s = app.state.settings
            result["airport_code"] = s.get("AIRPORT_CODE", "")
            result["check_interval"] = float(s.get("CHECK_INTERVAL_MINUTES", 30)) * 60
        return JSONResponse(result)

    @app.get("/api/settings")
    async def get_settings():
        store = app.state.store
        with store._connect() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings ORDER BY key").fetchall()
        return JSONResponse({r["key"]: r["value"] for r in rows})

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
            "exclusion_list":  _fetch_rows("SELECT id, registration, description FROM exclusion_list ORDER BY id"),
            "rego_watchlist":  _fetch_rows("SELECT id, registration, description FROM rego_watchlist ORDER BY id"),
            "type_watchlist":  _fetch_rows("SELECT id, airline, aircraft_type FROM type_watchlist ORDER BY id"),
            "airline_watchlist": _fetch_rows("SELECT id, icao_code, entry_type, name FROM airline_watchlist ORDER BY id"),
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
            conn.execute("DELETE FROM exclusion_list WHERE registration = ?", (registration,))
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
            conn.execute("DELETE FROM rego_watchlist WHERE registration = ?", (registration,))
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
                "DELETE FROM type_watchlist WHERE airline = ? AND aircraft_type = ?",
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
                "DELETE FROM airline_watchlist WHERE icao_code = ? AND entry_type = ?",
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
