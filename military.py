from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import List, Optional

import requests
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)

# ICAO hex address ranges → country name
# Sorted by lower bound; derived from ICAO Annex 10 allocations.
_ICAO_RANGES = sorted([
    (0x010000, 0x017FFF, "Egypt"),
    (0x018000, 0x01FFFF, "Libya"),
    (0x020000, 0x027FFF, "Morocco"),
    (0x028000, 0x02FFFF, "Tunisia"),
    (0x038000, 0x03FFFF, "South Africa"),
    (0x300000, 0x33FFFF, "Italy"),
    (0x340000, 0x37FFFF, "Spain"),
    (0x380000, 0x3BFFFF, "France"),
    (0x3C0000, 0x3FFFFF, "Germany"),
    (0x400000, 0x43FFFF, "United Kingdom"),
    (0x458000, 0x45FFFF, "Denmark"),
    (0x460000, 0x467FFF, "Finland"),
    (0x468000, 0x46FFFF, "Greece"),
    (0x478000, 0x47FFFF, "Norway"),
    (0x480000, 0x487FFF, "Netherlands"),
    (0x488000, 0x48FFFF, "Poland"),
    (0x4B0000, 0x4B7FFF, "Sweden"),
    (0x4B8000, 0x4BFFFF, "Switzerland"),
    (0x4C0000, 0x4C7FFF, "Turkey"),
    (0x4D0000, 0x4D7FFF, "Ukraine"),
    (0x680000, 0x6803FF, "Bahrain"),
    (0x688000, 0x6883FF, "UAE"),
    (0x6C0000, 0x6C3FFF, "Saudi Arabia"),
    (0x710000, 0x717FFF, "Nepal"),
    (0x718000, 0x71FFFF, "Pakistan"),
    (0x750000, 0x75FFFF, "Malaysia"),
    (0x760000, 0x767FFF, "Philippines"),
    (0x768000, 0x76FFFF, "Singapore"),
    (0x770000, 0x777FFF, "Vietnam"),
    (0x780000, 0x7BFFFF, "China"),
    (0x7C0000, 0x7FFFFF, "Australia"),
    (0x800000, 0x83FFFF, "India"),
    (0x840000, 0x87FFFF, "Japan"),
    (0x880000, 0x887FFF, "Thailand"),
    (0x890000, 0x897FFF, "Myanmar"),
    (0x8A0000, 0x8A7FFF, "Indonesia"),
    (0x8B0000, 0x8B7FFF, "Papua New Guinea"),
    (0x8B8000, 0x8BFFFF, "Fiji"),
    (0xA00000, 0xAFFFFF, "United States"),
    (0xC00000, 0xC3FFFF, "Canada"),
    (0xC80000, 0xC87FFF, "New Zealand"),
    (0xE00000, 0xE3FFFF, "Argentina"),
    (0xE40000, 0xE7FFFF, "Brazil"),
    (0xE80000, 0xE8FFFF, "Chile"),
    (0xEC0000, 0xECFFFF, "Mexico"),
])


def _country_from_hex(hex_str: str) -> Optional[str]:
    """Derive country name from ICAO 24-bit hex address."""
    try:
        v = int(hex_str, 16)
        for lo, hi, country in _ICAO_RANGES:
            if lo <= v <= hi:
                return country
    except (ValueError, TypeError):
        pass
    return None


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
    desc         = ac.get("desc") or ""
    alt          = ac.get("alt_baro", "N/A")
    speed        = ac.get("gs", "N/A")
    country      = _country_from_hex(ac.get("hex", "")) or "Unknown"

    # Show full description if available, otherwise fall back to type code
    aircraft_str = f"{desc} ({ac_type})" if desc else ac_type

    return "\n".join([
        "<b>Military Aircraft Approaching</b>",
        f"  Registration: <b>{registration}</b>",
        f"  Callsign: {callsign}",
        f"  Country: {country}",
        f"  Aircraft: {aircraft_str}",
        f"  Altitude: {alt} ft",
        f"  Speed: {speed} kts",
        f"  Distance: {dist_nm:.0f} nm from {airport_iata}",
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
                try:
                    await context.bot.send_photo(
                        chat_id=cfg.chat_id,
                        photo=photo_url,
                        caption=f"Aircraft Photo: {registration}",
                    )
                except Exception as exc:
                    log.warning("Could not send photo for %s: %s", registration, exc)
            await context.bot.send_message(chat_id=cfg.chat_id, text=message, parse_mode="HTML")
            cfg.store.mark_military_notified(registration, now_ts)
            log.info("Military notification sent: %s", registration)
        except Exception as exc:
            log.error("Failed to send military notification for %s: %s", registration, exc)
