"""Web Push notification sending — VAPID key management + pywebpush wrapper.

Kept as its own module (rather than folded into auth.py or web.py) so both
monitor.py (the trigger side, via cfg.control_store) and web.py (the
subscribe/unsubscribe endpoints) can import it without creating a circular
import."""
from __future__ import annotations

import base64
import json
import logging
import os

log = logging.getLogger(__name__)

_VAPID_CLAIMS_SUB = "mailto:admin@abc539411.duckdns.org"


def get_vapid_keys() -> tuple:
    """Returns (private_pem_str, public_key_b64url) for Web Push VAPID auth.
    Env override via VAPID_PRIVATE_KEY_PEM/VAPID_PUBLIC_KEY; otherwise generated
    once and persisted to <data_dir>/.vapid_private.pem + .vapid_public.txt,
    mirroring auth.py's _get_secret_key() generate-once-and-persist pattern."""
    env_priv = os.environ.get("VAPID_PRIVATE_KEY_PEM")
    env_pub = os.environ.get("VAPID_PUBLIC_KEY")
    if env_priv and env_pub:
        return env_priv, env_pub

    data_dir = os.environ.get("SPOTALERT_DATA", "data/")
    priv_path = os.path.join(data_dir, ".vapid_private.pem")
    pub_path = os.path.join(data_dir, ".vapid_public.txt")
    if os.path.exists(priv_path) and os.path.exists(pub_path):
        with open(priv_path, "r") as f:
            priv = f.read()
        with open(pub_path, "r") as f:
            pub = f.read().strip()
        return priv, pub

    from py_vapid import Vapid01
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    vapid = Vapid01()
    vapid.generate_keys()
    priv_pem = vapid.private_pem().decode("utf-8")
    raw_pub = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    pub_b64 = base64.urlsafe_b64encode(raw_pub).decode("utf-8").rstrip("=")

    os.makedirs(data_dir, exist_ok=True)
    with open(priv_path, "w") as f:
        f.write(priv_pem)
    with open(pub_path, "w") as f:
        f.write(pub_b64)
    log.info("Generated a new VAPID key pair for Web Push at %s", data_dir)
    return priv_pem, pub_b64


def send_push_to_user(control_store, user_id: str, title: str, body: str, data: dict = None) -> None:
    """Sends a Web Push notification to every subscription registered for
    user_id. A subscription the push service reports as gone (404/410 — the
    browser unsubscribed, or the endpoint expired) is silently removed; other
    failures are only logged, since they might be transient (network blip,
    push service hiccup) rather than a reason to drop the subscription."""
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid01

    subs = control_store.get_push_subscriptions(user_id)
    if not subs:
        return
    priv_pem, _ = get_vapid_keys()
    # pywebpush's webpush() only special-cases a str vapid_private_key as EITHER
    # a file path (os.path.isfile) OR a raw/DER key via Vapid.from_string() — PEM
    # text (what get_vapid_keys()/py_vapid actually produces) matches neither and
    # fails with a generic "Could not deserialize key data" error. Passing an
    # already-loaded Vapid01 object hits webpush()'s isinstance fast-path instead,
    # sidestepping the string-format guessing entirely.
    vapid = Vapid01.from_pem(priv_pem.encode("utf-8"))
    payload = json.dumps({"title": title, "body": body, "data": data or {}})
    for sub in subs:
        subscription_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=vapid,
                vapid_claims={"sub": _VAPID_CLAIMS_SUB},
            )
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                control_store.remove_push_subscription(sub["endpoint"])
            else:
                log.warning("Push send failed for %s: %s", user_id, exc)
        except Exception as exc:
            log.warning("Push send failed for %s: %s", user_id, exc)
