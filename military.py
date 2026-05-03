from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import List

import requests
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in nautical miles between two lat/lon points."""
    R = 3440.065
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _fetch_military() -> List[dict]:
    """Fetch all currently tracked military aircraft from adsb.fi (no auth required)."""
    resp = requests.get("https://opendata.adsb.fi/api/v2/mil", timeout=10)
    resp.raise_for_status()
    return resp.json().get("ac") or []


def _is_on_approach(ac: dict, airport_lat: float, airport_lon: float,
                    radius_nm: int, max_alt_ft: int) -> bool:
    try:
        alt = ac.get("alt_baro")
        alt = 0 if alt == "ground" else int(alt)
        if alt > max_alt_ft:
            return False
    except (TypeError, ValueError):
        return False

    lat, lon = ac.get("lat"), ac.get("lon")
    if lat is None or lon is None:
        return False

    return _haversine_nm(float(lat), float(lon), airport_lat, airport_lon) <= radius_nm


def _format_notification(ac: dict, airport_iata: str, dist_nm: float) -> str:
    registration = ac.get("r") or "N/A"
    callsign     = (ac.get("flight") or "").strip() or "N/A"
    ac_type      = ac.get("t") or "N/A"
    alt          = ac.get("alt_baro", "N/A")
    speed        = ac.get("gs", "N/A")

    return "\n".join([
        "<b>Military Aircraft Approaching</b>",
        f"  Registration: <b>{registration}</b>",
        f"  Callsign:     {callsign}",
        f"  Type:         {ac_type}",
        f"  Altitude:     {alt} ft",
        f"  Speed:        {speed} kts",
        f"  Distance:     {dist_nm:.0f} nm from {airport_iata}",
    ])


async def check_military(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    now_ts = int(datetime.now().timestamp())

    try:
        military = _fetch_military()
    except Exception as exc:
        log.warning("adsb.fi military query failed: %s", exc)
        return

    for ac in military:
        # Skip stale entries — adsb.fi may include aircraft not updated recently
        if (ac.get("seen") or 999) > 60:
            continue
        if not _is_on_approach(ac, cfg.airport_lat, cfg.airport_lon,
                               cfg.military_radius_nm, cfg.military_max_alt_ft):
            continue

        registration = (ac.get("r") or ac.get("hex") or "").strip().upper()
        if not registration:
            continue

        if not cfg.store.should_notify_military(registration, now_ts, cfg.military_renotify_hours):
            continue

        dist_nm = _haversine_nm(
            float(ac["lat"]), float(ac["lon"]),
            cfg.airport_lat, cfg.airport_lon,
        )
        message = _format_notification(ac, cfg.airport_iata, dist_nm)

        photo_url = None
        try:
            rego_details = cfg.fr_api.get_rego_details(registration)
            images = (rego_details or {}).get("aircraftImages") or []
            if images:
                photo_url = images[0]["images"]["medium"][0]["link"]
        except Exception as exc:
            log.warning("Could not fetch photo for %s: %s", registration, exc)

        try:
            if photo_url:
                await context.bot.send_photo(
                    chat_id=cfg.chat_id,
                    photo=photo_url,
                    caption=f"Aircraft Photo: {registration}",
                )
            await context.bot.send_message(chat_id=cfg.chat_id, text=message, parse_mode="HTML")
            cfg.store.mark_military_notified(registration, now_ts)
            log.info("Military notification sent: %s", registration)
        except Exception as exc:
            log.error("Failed to send military notification for %s: %s", registration, exc)
