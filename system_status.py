"""Persistent tracker for scheduled task and external API call state.
Writes to the settings table so state survives restarts. In-memory cache for fast reads.

Entries can be scoped by airport (e.g. per-airport arrivals/military checks) so multiple
watched airports don't overwrite each other's last-run status under one shared key."""
import time as _time

_store = None
_cache: dict = {}   # key -> {'ts', 'ok', 'error'}
_next_run: dict = {}  # key -> next scheduled run ts (in-memory only, not persisted)


def init(store) -> None:
    global _store
    _store = store


def _key(name: str, scope: str = None) -> str:
    return f'{name}:{scope}' if scope else name


def _save(prefix: str, name: str, scope: str, ts: int, ok: bool, error: str) -> None:
    cache_key = f'{prefix}:{_key(name, scope)}'
    _cache[cache_key] = {'ts': ts, 'ok': ok, 'error': error}
    if _store:
        setting_key = f'_sys_{prefix}_{_key(name, scope)}'
        _store.save_setting(f'{setting_key}_ts',  str(ts))
        _store.save_setting(f'{setting_key}_ok',  '1' if ok else '0')
        _store.save_setting(f'{setting_key}_err', error or '')


def _load(prefix: str, name: str, scope: str) -> dict:
    cache_key = f'{prefix}:{_key(name, scope)}'
    if cache_key in _cache:
        return _cache[cache_key]
    if _store:
        setting_key = f'_sys_{prefix}_{_key(name, scope)}'
        ts_raw  = _store.load_setting(f'{setting_key}_ts')
        ok_raw  = _store.load_setting(f'{setting_key}_ok')
        err_raw = _store.load_setting(f'{setting_key}_err')
        if ts_raw:
            entry = {'ts': int(float(ts_raw)), 'ok': ok_raw == '1', 'error': err_raw or None}
            _cache[cache_key] = entry
            return entry
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
    task's very first run. In-memory only; doesn't need to survive a restart since the
    scheduler recomputes it fresh on every loop iteration, including the first."""
    _next_run[_key(name, scope)] = ts


def get_next_run(name: str, scope: str = None) -> int:
    return _next_run.get(_key(name, scope))
