"""Persistent tracker for scheduled task and external API call state.
Writes to the settings table so state survives restarts.

The web server and the monitor loop are separate OS processes (see
monitor_service.py) sharing the same underlying SQLite store, each with their
own copy of this module's globals — record_task/record_api/set_next_run are
mostly called from the monitor process, while get_task/get_api/get_next_run
are read from the web process's /api/system-tasks handler. Every read here
therefore always hits the DB directly rather than trusting a process-local
cache — a cache would go stale the moment the OTHER process writes a new
value, since nothing invalidates it across the process boundary.

Entries can be scoped by airport (e.g. per-airport arrivals/military checks) so multiple
watched airports don't overwrite each other's last-run status under one shared key."""
import time as _time
from typing import Optional

_store = None


def init(store) -> None:
    global _store
    _store = store


def _key(name: str, scope: str = None) -> str:
    return f'{name}:{scope}' if scope else name


def _save(prefix: str, name: str, scope: str, ts: int, ok: bool, error: str) -> None:
    if _store:
        setting_key = f'_sys_{prefix}_{_key(name, scope)}'
        _store.save_setting(f'{setting_key}_ts',  str(ts))
        _store.save_setting(f'{setting_key}_ok',  '1' if ok else '0')
        _store.save_setting(f'{setting_key}_err', error or '')


def _load(prefix: str, name: str, scope: str) -> dict:
    if not _store:
        return {}
    setting_key = f'_sys_{prefix}_{_key(name, scope)}'
    ts_raw  = _store.load_setting(f'{setting_key}_ts')
    ok_raw  = _store.load_setting(f'{setting_key}_ok')
    err_raw = _store.load_setting(f'{setting_key}_err')
    if ts_raw:
        return {'ts': int(float(ts_raw)), 'ok': ok_raw == '1', 'error': err_raw or None}
    return {}


def record_task(name: str, ok: bool, error: str = None, scope: str = None) -> None:
    _save('task', name, scope, int(_time.time()), ok, error)


def record_api(name: str, ok: bool, error: str = None, scope: str = None) -> None:
    _save('api', name, scope, int(_time.time()), ok, error)


def get_task(name: str, scope: str = None) -> dict:
    return _load('task', name, scope)


def get_api(name: str, scope: str = None) -> dict:
    return _load('api', name, scope)


def set_next_run(name: str, ts: int, scope: str = None) -> None:
    """Record when a periodic task is next expected to run — set explicitly by the
    scheduler (which knows the real, possibly-staggered interval) rather than derived
    from last_ts + interval, so the UI can show an accurate countdown even before a
    task's very first run. Persisted (not just in-memory) so the web process's
    /api/system-tasks handler — a different OS process from the scheduler — can read
    it; the scheduler recomputes and overwrites it fresh on every loop iteration
    regardless, so a stale value left over from a restart is corrected within one
    cycle either way."""
    if _store:
        _store.save_setting(f'_sys_next_{_key(name, scope)}', str(ts))


def get_next_run(name: str, scope: str = None) -> Optional[int]:
    if not _store:
        return None
    raw = _store.load_setting(f'_sys_next_{_key(name, scope)}')
    return int(float(raw)) if raw else None
