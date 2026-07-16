from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Request

log = logging.getLogger(__name__)

_PBKDF2_ITERATIONS = 260_000
SESSION_COOKIE = "sa_session"
AIRPORT_COOKIE = "sa_airport"
SESSION_MAX_AGE_SECONDS = 90 * 86400  # 90 days


def _get_secret_key() -> bytes:
    """Loaded once per process. Generated on first run and persisted to
    <data_dir>/.secret_key if SPOTALERT_SECRET_KEY isn't set in the environment."""
    env_key = os.environ.get("SPOTALERT_SECRET_KEY")
    if env_key:
        return env_key.encode("utf-8")
    data_dir = os.environ.get("SPOTALERT_DATA", "data/")
    key_path = os.path.join(data_dir, ".secret_key")
    if os.path.exists(key_path):
        with open(key_path, "r") as f:
            return f.read().strip().encode("utf-8")
    os.makedirs(data_dir, exist_ok=True)
    new_key = secrets.token_hex(32)
    with open(key_path, "w") as f:
        f.write(new_key)
    log.info("Generated a new SpotAlert session secret key at %s", key_path)
    return new_key.encode("utf-8")


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt),
                                 _PBKDF2_ITERATIONS).hex()
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, iterations_s, salt, digest = stored_hash.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_s)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt),
                                         iterations).hex()
        return hmac.compare_digest(candidate, digest)
    except Exception:
        return False


def _sign(payload_b64: str) -> str:
    return hmac.new(_get_secret_key(), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def make_session_cookie(user_id: str, role: str, session_epoch: int) -> str:
    payload = {
        "uid": user_id,
        "role": role,
        "epoch": session_epoch,
        "exp": int(time.time()) + SESSION_MAX_AGE_SECONDS,
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    sig = _sign(payload_b64)
    return f"{payload_b64}.{sig}"


def _parse_session_cookie(raw: str) -> Optional[dict]:
    try:
        payload_b64, sig = raw.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(payload_b64)):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


@dataclass
class UserCtx:
    user_id: str
    username: str
    role: str
    airport_iata: Optional[str]
    language: Optional[str] = None


def set_session_cookie(response, request: Request, user_id: str, role: str, session_epoch: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        make_session_cookie(user_id, role, session_epoch),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=(request.url.scheme == "https"),
    )


def clear_auth_cookies(response) -> None:
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(AIRPORT_COOKIE)


def _resolve_session(request: Request, control_store) -> Optional[UserCtx]:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    payload = _parse_session_cookie(raw)
    if not payload:
        return None
    user_row = control_store.get_user(payload["uid"])
    if not user_row:
        return None
    if int(user_row["session_epoch"]) != int(payload["epoch"]):
        return None  # cookie invalidated by a password change/reset or explicit logout-everywhere

    airport_iata = request.cookies.get(AIRPORT_COOKIE)
    if airport_iata:
        # Never trust the cookie value blindly — re-validate against actual access,
        # unless the user is a controller (implicit access to every watched airport).
        if user_row["role"] != "controller":
            allowed = control_store.get_user_airports(user_row["user_id"])
            if airport_iata not in allowed:
                airport_iata = None

    return UserCtx(
        user_id=user_row["user_id"],
        username=user_row["username"],
        role=user_row["role"],
        airport_iata=airport_iata,
        language=user_row["language"],
    )


def get_current_user_optional(request: Request) -> Optional[UserCtx]:
    """Returns None instead of raising — used by /api/me, which must work whether or
    not the caller is logged in."""
    control_store = request.app.state.control_store
    return _resolve_session(request, control_store)


def current_user(request: Request) -> UserCtx:
    user = get_current_user_optional(request)
    if user is None:
        raise HTTPException(401, "Not authenticated")
    return user


def require_role(*roles: str):
    def _dep(user: UserCtx = Depends(current_user)) -> UserCtx:
        if user.role not in roles:
            raise HTTPException(403, "Forbidden")
        return user
    return _dep
