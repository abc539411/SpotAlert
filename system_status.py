"""Persistent tracker for scheduled task and external API call state.
Writes to the settings table so state survives restarts. In-memory cache for fast reads."""
import time as _time

_store = None
_cache: dict = {}   # key -> {'ts', 'ok', 'error'}


def init(store) -> None:
    global _store
    _store = store


def _save(prefix: str, name: str, ts: int, ok: bool, error: str) -> None:
    _cache[f'{prefix}:{name}'] = {'ts': ts, 'ok': ok, 'error': error}
    if _store:
        _store.save_setting(f'_sys_{prefix}_{name}_ts',  str(ts))
        _store.save_setting(f'_sys_{prefix}_{name}_ok',  '1' if ok else '0')
        _store.save_setting(f'_sys_{prefix}_{name}_err', error or '')


def _load(prefix: str, name: str) -> dict:
    cache_key = f'{prefix}:{name}'
    if cache_key in _cache:
        return _cache[cache_key]
    if _store:
        ts_raw  = _store.load_setting(f'_sys_{prefix}_{name}_ts')
        ok_raw  = _store.load_setting(f'_sys_{prefix}_{name}_ok')
        err_raw = _store.load_setting(f'_sys_{prefix}_{name}_err')
        if ts_raw:
            entry = {'ts': int(float(ts_raw)), 'ok': ok_raw == '1', 'error': err_raw or None}
            _cache[cache_key] = entry
            return entry
    return {}


def record_task(name: str, ok: bool, error: str = None) -> None:
    _save('task', name, int(_time.time()), ok, error)


def record_api(name: str, ok: bool, error: str = None) -> None:
    _save('api', name, int(_time.time()), ok, error)


def get_task(name: str) -> dict:
    return _load('task', name)


def get_api(name: str) -> dict:
    return _load('api', name)
