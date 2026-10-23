"""
Upload the Lightroom catalog to a SpotAlert instance once Studio has finished
writing to it, so that instance's Collection / Fleet / Search-Catalogue views
see the newly organized photos.

Uses SpotAlert's own routes (see web.py in the main app):
    POST /api/auth/login      -> sets the `sa_session` cookie (valid 90 days)
    POST /api/catalog/upload  -> multipart `file`, controller/pilot only;
                                 replaces that user's "My Catalog"

Only the session cookie is kept on disk (data/spotalert_sync.json) — never the
password. When the cookie expires or is invalidated (password change), the
upload gets a 401 and the user reconnects from Settings.
"""
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import requests

from config import DATA_DIR
from utils.lr_catalog import is_lightroom_running

_LINK_FILE = DATA_DIR / 'spotalert_sync.json'
_SESSION_COOKIE = 'sa_session'
_CHUNK = 1024 * 1024

_lock = threading.Lock()
_status = {'state': 'idle', 'sent': 0, 'total': 0, 'message': '', 'finished_at': None}


def _load_link() -> Optional[dict]:
    try:
        return json.loads(_LINK_FILE.read_text(encoding='utf-8'))
    except Exception:
        return None


def _save_link(link: dict):
    _LINK_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LINK_FILE.write_text(json.dumps(link), encoding='utf-8')


def _set_status(**kw):
    with _lock:
        _status.update(kw)


def status() -> dict:
    link = _load_link()
    with _lock:
        out = dict(_status)
    out['linked'] = bool(link and link.get('session'))
    out['url'] = link.get('url') if link else ''
    out['username'] = link.get('username') if link else ''
    return out


def connect(url: str, username: str, password: str) -> tuple:
    """Log in to the instance and remember the session. Returns (ok, message)."""
    url = (url or '').strip().rstrip('/')
    if not url.lower().startswith(('http://', 'https://')):
        return False, 'Instance URL must start with http:// or https://'
    if not username or not password:
        return False, 'Username and password are required'
    try:
        r = requests.post(f'{url}/api/auth/login',
                          json={'username': username, 'password': password}, timeout=15)
    except Exception as e:
        return False, f'Could not reach {url}: {e}'
    if r.status_code == 401:
        return False, 'Invalid username or password'
    if not r.ok:
        return False, f'Login failed (HTTP {r.status_code})'
    session = r.cookies.get(_SESSION_COOKIE)
    if not session:
        return False, 'Login succeeded but no session was returned — is this a SpotAlert instance?'
    try:
        role = r.json().get('role', '')
    except Exception:
        role = ''
    if role not in ('controller', 'pilot'):
        return False, f"This account's role ({role or 'unknown'}) can't upload a catalog — needs Controller or Pilot"
    _save_link({'url': url, 'username': username, 'session': session})
    _set_status(state='idle', message='', sent=0, total=0)
    return True, f'Connected to {url} as {username}'


def disconnect():
    try:
        _LINK_FILE.unlink()
    except FileNotFoundError:
        pass
    _set_status(state='idle', message='', sent=0, total=0)


class _MultipartFile:
    """Streams one file as a multipart/form-data body without loading it into
    memory (catalogs run from hundreds of MB to several GB). Has a length, so
    requests sends a normal Content-Length request rather than chunked."""

    def __init__(self, path: Path, boundary: str):
        self._path = path
        name = path.name.replace('"', '')
        self._head = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode('utf-8')
        self._tail = f'\r\n--{boundary}--\r\n'.encode('utf-8')
        self._size = path.stat().st_size

    def __len__(self):
        return len(self._head) + self._size + len(self._tail)

    def __iter__(self):
        yield self._head
        sent = 0
        with open(self._path, 'rb') as f:
            while chunk := f.read(_CHUNK):
                yield chunk
                sent += len(chunk)
                _set_status(sent=sent)
        yield self._tail


def _upload(link: dict, catalog: Path):
    boundary = uuid.uuid4().hex
    body = _MultipartFile(catalog, boundary)
    _set_status(state='uploading', sent=0, total=catalog.stat().st_size, message='', finished_at=None)
    try:
        r = requests.post(
            f"{link['url']}/api/catalog/upload",
            data=body,
            headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
            cookies={_SESSION_COOKIE: link['session']},
            timeout=(15, 900),
        )
    except Exception as e:
        _set_status(state='error', message=f'Upload failed: {e}', finished_at=time.time())
        return
    if r.status_code == 401:
        msg = 'Session expired — reconnect in Settings'
    elif r.status_code == 403:
        msg = "This account can't upload a catalog (needs Controller or Pilot)"
    elif r.status_code == 413:
        msg = 'The instance (or a proxy in front of it) rejected the catalog as too large'
    elif not r.ok:
        try:
            msg = r.json().get('detail') or f'HTTP {r.status_code}'
        except Exception:
            msg = f'HTTP {r.status_code}'
    else:
        _set_status(state='done', message=f"Uploaded to {link['url']}", finished_at=time.time())
        return
    _set_status(state='error', message=msg, finished_at=time.time())


def start_upload(catalog_path: str) -> tuple:
    """Kick off a background upload. Returns (started, message)."""
    link = _load_link()
    if not link or not link.get('session'):
        return False, 'Not connected to a SpotAlert instance'
    catalog = Path(catalog_path or '')
    if not catalog_path or not catalog.is_file():
        return False, f'Catalog not found: {catalog_path}'
    # A catalog Lightroom has open may be mid-write — don't ship a torn copy.
    if is_lightroom_running(catalog):
        return False, 'Lightroom is open — close it before uploading the catalog'
    with _lock:
        if _status['state'] == 'uploading':
            return False, 'An upload is already running'
        _status.update(state='uploading', sent=0, total=0, message='', finished_at=None)
    threading.Thread(target=_upload, args=(link, catalog), daemon=True).start()
    return True, 'Upload started'
