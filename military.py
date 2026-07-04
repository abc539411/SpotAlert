from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import List, Optional

import requests
from telegram.ext import ContextTypes

import jetphotos


log = logging.getLogger(__name__)

# Auto rapid-tracking: once a military aircraft is detected, poll adsb.fi at this
# cadence until it leaves the radius or goes stationary (landed + slow) for this long.
MILITARY_RAPID_INTERVAL_SECS = 60
MILITARY_STATIONARY_EXIT_SECS = 600   # 10 min
MILITARY_MOVING_GS_THRESHOLD = 5      # knots; below this while on the ground counts as stopped

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
        if alt == "ground":
            return False
        alt = int(alt)
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

    aircraft_str = f"{desc} ({ac_type})" if desc else ac_type

    hex_code = (ac.get("hex") or "").lower()
    map_url  = f"https://globe.adsb.fi/?icao={hex_code}" if hex_code else ""
    rego_str = f'<a href="{map_url}">{registration}</a>' if map_url else f"<b>{registration}</b>"

    return "\n".join(filter(None, [
        "<b>Military Aircraft Approaching</b>",
        f"  Registration: {rego_str}",
        f"  Callsign: {callsign}",
        f"  Country: {country}",
        f"  Aircraft: {aircraft_str}",
        f"  Altitude: {alt} ft",
        f"  Speed: {speed} kts",
        f"  Distance: {dist_nm:.0f} nm from {airport_iata}",
    ]))


async def check_military(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    now_ts = int(datetime.now().timestamp())

    try:
        military = _fetch_military()
        import system_status as _ss; _ss.record_api('adsb_fi', True)
    except Exception as exc:
        import system_status as _ss; _ss.record_api('adsb_fi', False, str(exc))
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

        dist_nm = _haversine_nm(
            float(ac["lat"]), float(ac["lon"]),
            cfg.airport_lat, cfg.airport_lon,
        )

        callsign    = (ac.get("flight") or "").strip()
        ac_type     = ac.get("t") or ""
        desc        = ac.get("desc") or ""
        country     = _country_from_hex(ac.get("hex", "")) or "Unknown"
        alt         = ac.get("alt_baro", "?")
        speed       = ac.get("gs", "?")
        detail      = f"{desc} ({ac_type})" if desc else ac_type
        extra_info  = f"{country} · {alt}ft · {speed}kts · {dist_nm:.0f}nm from {cfg.airport_iata}"

        # Fresh approach — start (or resume, if it dropped out and came back) a rapid-tracked visit.
        # Recorded regardless of the Telegram renotify cooldown below, which only gates notifications.
        if registration not in cfg.military_rapid_tracking:
            # FR24 doesn't have photos for military tails — fall back to JetPhotos
            existing_frame = cfg.store.get_airframe(registration)
            if not existing_frame or not existing_frame.get("photo_url"):
                try:
                    photo_url = jetphotos.fetch_photo_url(registration)
                    if photo_url:
                        cfg.store.upsert_airframe_from_fr24(registration, photo_url=photo_url)
                except Exception as exc:
                    log.debug("JetPhotos photo fetch failed for %s: %s", registration, exc)

            visit_flight_number = f"{callsign or registration}#{now_ts}"
            arrival_id = cfg.store.record_filter_match(
                registration, visit_flight_number, ["Military"], now_ts, now_ts,
                detail=detail, extra_info=extra_info,
            )
            # First track point is written by the exit-check pass below (it runs over every
            # currently-tracked aircraft, including this one, later in this same cycle) —
            # adding it here too would double up the same timestamp.
            cfg.military_rapid_tracking[registration] = {
                "stationary_since_ts": None,  # airborne by definition — _is_on_approach() excludes "ground"
                "last_in_radius_ts": now_ts,
                "arrival_id": arrival_id,
            }
            log.info("Military visit started: %s", registration)

        if not cfg.store.should_notify_military(registration, now_ts, cfg.military_renotify_hours):
            continue

        try:
            message = _format_notification(ac, cfg.airport_iata, dist_nm)
            for dest_chat_id in cfg.all_chat_ids:
                await context.bot.send_message(chat_id=dest_chat_id, text=message, parse_mode="HTML",
                                               disable_web_page_preview=True)
            cfg.store.mark_military_notified(registration, now_ts)
            log.info("Military notification sent: %s", registration)
        except Exception as exc:
            log.error("Failed to send military notification for %s: %s", registration, exc)

    # Track points + exit check for everything currently being rapid-tracked. Runs over the
    # full unfiltered `military` list, not the _is_on_approach()-gated loop above — a departing
    # aircraft will by definition stop passing that filter.
    if cfg.military_rapid_tracking:
        by_reg = {}
        for ac in military:
            reg = (ac.get("r") or ac.get("hex") or "").strip().upper()
            if reg and (ac.get("seen") or 999) <= 60:
                by_reg[reg] = ac

        for reg, entry in list(cfg.military_rapid_tracking.items()):
            ac = by_reg.get(reg)

            # A fast military jet doing circuits will routinely swing outside a tight radius
            # (e.g. MILITARY_RADIUS_NM=10) mid-pattern and be back within a lap or two — that's
            # NOT the aircraft leaving for good. So "outside radius this cycle" gets the exact
            # same grace window as a reception gap, instead of ending the visit immediately.
            # Only a real, sustained absence from the radius (10+ min) or confirmed-stationary
            # (landed + slow, 10+ min) ends the visit — one unified rule for both.
            if ac is not None:
                lat, lon = ac.get("lat"), ac.get("lon")
                if lat is not None and lon is not None:
                    dist_nm = _haversine_nm(float(lat), float(lon), cfg.airport_lat, cfg.airport_lon)
                    if dist_nm <= cfg.military_radius_nm:
                        entry["last_in_radius_ts"] = now_ts
                        cfg.store.add_military_track_point(entry["arrival_id"], now_ts, float(lat), float(lon))

                        alt = ac.get("alt_baro")
                        on_ground = (alt == "ground")
                        if not on_ground:
                            moving = True  # airborne at any altitude — including a hover — counts as active
                        else:
                            gs = ac.get("gs")
                            try:
                                moving = gs is not None and float(gs) > MILITARY_MOVING_GS_THRESHOLD
                            except (TypeError, ValueError):
                                moving = False
                        if moving:
                            entry["stationary_since_ts"] = None
                        elif entry["stationary_since_ts"] is None:
                            entry["stationary_since_ts"] = now_ts
                    # else: outside radius this cycle — last_in_radius_ts/stationary_since_ts left
                    # untouched, no track point added; handled by the grace-window checks below.

            # ac is None (a brief reception gap — adsb.fi coverage regularly drops out for a
            # minute or two) falls through the same way: state is left untouched this cycle.
            exit_tracking = False
            if entry["stationary_since_ts"] is not None and \
               (now_ts - entry["stationary_since_ts"]) > MILITARY_STATIONARY_EXIT_SECS:
                exit_tracking = True  # confirmed on the ground + slow, continuously, for 10 min
            elif (now_ts - entry["last_in_radius_ts"]) > MILITARY_STATIONARY_EXIT_SECS:
                exit_tracking = True  # not confirmed within radius for 10 min — presumed gone for good

            if exit_tracking:
                del cfg.military_rapid_tracking[reg]
                log.info("Military visit ended: %s", reg)
