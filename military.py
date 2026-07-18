from __future__ import annotations

import asyncio
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

# adsb.fi's /api/v2/mil returns GLOBAL military traffic, not scoped to any one
# airport — every watched airport just filters the SAME response client-side
# via _is_on_approach(). So there's exactly ONE fetch per cycle, shared by
# every airport (see monitor_runner.run_military_shared_loop), rather than
# each airport independently polling the same endpoint — N independent
# pollers would just be N uncoordinated calls to one shared endpoint, easily
# exceeding adsb.fi's ~1 req/sec limit even though each individual airport's
# own polling rate is well under it.

# ICAO hex address ranges → country name
# Sourced from the ATDB ICAO 24-bit allocation table (aerotransport.org),
# itself derived from ICAO Annex 10 Volume III — replaces an earlier hand-
# maintained table that had drifted badly out of sync with the real
# allocations. E.g. the old table labeled the WHOLE span 0x890000-0x897FFF
# "Myanmar", when that span actually belongs to five different countries
# (Yemen, Bahrain, Brunei, UAE, Solomon Islands) and Myanmar's real block is
# 0x704000-0x704FFF; roughly half the old table's ~45 entries were similarly
# shifted/scrambled relative to their real countries (e.g. "Switzerland" and
# "Turkey" were swapped, "Philippines"/"Pakistan"/"Vietnam" each pointed at a
# neighboring country's real block, "Mexico" pointed at a reserved/unassigned
# block). This table has ~190 entries instead of ~45, since the old one only
# bothered covering countries this app happened to have already seen.
_ICAO_RANGES = sorted([
    (0x004000, 0x0043FF, "Zimbabwe"),
    (0x006000, 0x006FFF, "Mozambique"),
    (0x008000, 0x00FFFF, "South Africa"),
    (0x010000, 0x017FFF, "Egypt"),
    (0x018000, 0x01FFFF, "Libya"),
    (0x020000, 0x027FFF, "Morocco"),
    (0x028000, 0x02FFFF, "Tunisia"),
    (0x030000, 0x0303FF, "Botswana"),
    (0x032000, 0x032FFF, "Burundi"),
    (0x034000, 0x034FFF, "Cameroon"),
    (0x035000, 0x0353FF, "Comoros"),
    (0x036000, 0x036FFF, "Congo"),
    (0x038000, 0x038FFF, "Côte d'Ivoire"),
    (0x03E000, 0x03EFFF, "Gabon"),
    (0x040000, 0x040FFF, "Ethiopia"),
    (0x042000, 0x042FFF, "Equatorial Guinea"),
    (0x044000, 0x044FFF, "Ghana"),
    (0x046000, 0x046FFF, "Guinea"),
    (0x048000, 0x0483FF, "Guinea-Bissau"),
    (0x04A000, 0x04A3FF, "Lesotho"),
    (0x04C000, 0x04CFFF, "Kenya"),
    (0x050000, 0x050FFF, "Liberia"),
    (0x054000, 0x054FFF, "Madagascar"),
    (0x058000, 0x058FFF, "Malawi"),
    (0x05A000, 0x05A3FF, "Maldives"),
    (0x05C000, 0x05CFFF, "Mali"),
    (0x05E000, 0x05E3FF, "Mauritania"),
    (0x060000, 0x0603FF, "Mauritius"),
    (0x062000, 0x062FFF, "Niger"),
    (0x064000, 0x064FFF, "Nigeria"),
    (0x068000, 0x068FFF, "Uganda"),
    (0x06A000, 0x06A3FF, "Qatar"),
    (0x06C000, 0x06CFFF, "Central African Republic"),
    (0x06E000, 0x06EFFF, "Rwanda"),
    (0x070000, 0x070FFF, "Senegal"),
    (0x074000, 0x0743FF, "Seychelles"),
    (0x076000, 0x0763FF, "Sierra Leone"),
    (0x078000, 0x078FFF, "Somalia"),
    (0x07A000, 0x07A3FF, "Swaziland"),
    (0x07C000, 0x07CFFF, "Sudan"),
    (0x080000, 0x080FFF, "Tanzania"),
    (0x084000, 0x084FFF, "Chad"),
    (0x088000, 0x088FFF, "Togo"),
    (0x08A000, 0x08AFFF, "Zambia"),
    (0x08C000, 0x08CFFF, "D R Congo"),
    (0x090000, 0x090FFF, "Angola"),
    (0x094000, 0x0943FF, "Benin"),
    (0x096000, 0x0963FF, "Cape Verde"),
    (0x098000, 0x0983FF, "Djibouti"),
    (0x09A000, 0x09AFFF, "Gambia"),
    (0x09C000, 0x09CFFF, "Burkina Faso"),
    (0x09E000, 0x09E3FF, "Sao Tome"),
    (0x0A0000, 0x0A7FFF, "Algeria"),
    (0x0A8000, 0x0A8FFF, "Bahamas"),
    (0x0AA000, 0x0AA3FF, "Barbados"),
    (0x0AB000, 0x0AB3FF, "Belize"),
    (0x0AC000, 0x0ACFFF, "Colombia"),
    (0x0AE000, 0x0AEFFF, "Costa Rica"),
    (0x0B0000, 0x0B0FFF, "Cuba"),
    (0x0B2000, 0x0B2FFF, "El Salvador"),
    (0x0B4000, 0x0B4FFF, "Guatemala"),
    (0x0B6000, 0x0B6FFF, "Guyana"),
    (0x0B8000, 0x0B8FFF, "Haiti"),
    (0x0BA000, 0x0BAFFF, "Honduras"),
    (0x0BC000, 0x0BC3FF, "St.Vincent + Grenadines"),
    (0x0BE000, 0x0BEFFF, "Jamaica"),
    (0x0C0000, 0x0C0FFF, "Nicaragua"),
    (0x0C2000, 0x0C2FFF, "Panama"),
    (0x0C4000, 0x0C4FFF, "Dominican Republic"),
    (0x0C6000, 0x0C6FFF, "Trinidad and Tobago"),
    (0x0C8000, 0x0C8FFF, "Suriname"),
    (0x0CA000, 0x0CA3FF, "Antigua & Barbuda"),
    (0x0CC000, 0x0CC3FF, "Grenada"),
    (0x0D0000, 0x0D7FFF, "Mexico"),
    (0x0D8000, 0x0DFFFF, "Venezuela"),
    (0x100000, 0x1FFFFF, "Russia"),
    (0x201000, 0x2013FF, "Namibia"),
    (0x202000, 0x2023FF, "Eritrea"),
    (0x300000, 0x33FFFF, "Italy"),
    (0x340000, 0x37FFFF, "Spain"),
    (0x380000, 0x3BFFFF, "France"),
    (0x3C0000, 0x3FFFFF, "Germany"),
    (0x400000, 0x43FFFF, "United Kingdom"),
    (0x440000, 0x447FFF, "Austria"),
    (0x448000, 0x44FFFF, "Belgium"),
    (0x450000, 0x457FFF, "Bulgaria"),
    (0x458000, 0x45FFFF, "Denmark"),
    (0x460000, 0x467FFF, "Finland"),
    (0x468000, 0x46FFFF, "Greece"),
    (0x470000, 0x477FFF, "Hungary"),
    (0x478000, 0x47FFFF, "Norway"),
    (0x480000, 0x487FFF, "Netherlands"),
    (0x488000, 0x48FFFF, "Poland"),
    (0x490000, 0x497FFF, "Portugal"),
    (0x498000, 0x49FFFF, "Czech Republic"),
    (0x4A0000, 0x4A7FFF, "Romania"),
    (0x4A8000, 0x4AFFFF, "Sweden"),
    (0x4B0000, 0x4B7FFF, "Switzerland"),
    (0x4B8000, 0x4BFFFF, "Turkey"),
    (0x4C0000, 0x4C7FFF, "Yugoslavia"),
    (0x4C8000, 0x4C83FF, "Cyprus"),
    (0x4CA000, 0x4CAFFF, "Ireland"),
    (0x4CC000, 0x4CCFFF, "Iceland"),
    (0x4D0000, 0x4D03FF, "Luxembourg"),
    (0x4D2000, 0x4D23FF, "Malta"),
    (0x4D4000, 0x4D43FF, "Monaco"),
    (0x500000, 0x5004FF, "San Marino"),
    (0x501000, 0x5013FF, "Albania"),
    (0x501C00, 0x501FFF, "Croatia"),
    (0x502C00, 0x502FFF, "Latvia"),
    (0x503C00, 0x503FFF, "Lithuania"),
    (0x504C00, 0x504FFF, "Moldova"),
    (0x505C00, 0x505FFF, "Slovakia"),
    (0x506C00, 0x506FFF, "Slovenia"),
    (0x507C00, 0x507FFF, "Uzbekistan"),
    (0x508000, 0x50FFFF, "Ukraine"),
    (0x510000, 0x5103FF, "Belarus"),
    (0x511000, 0x5113FF, "Estonia"),
    (0x512000, 0x5123FF, "Macedonia"),
    (0x513000, 0x5133FF, "Bosnia & Herzegovina"),
    (0x514000, 0x5143FF, "Georgia"),
    (0x515000, 0x5153FF, "Tajikistan"),
    (0x600000, 0x6003FF, "Armenia"),
    (0x600800, 0x600BFF, "Azerbaijan"),
    (0x601000, 0x6013FF, "Kyrgyzstan"),
    (0x601800, 0x601BFF, "Turkmenistan"),
    (0x680000, 0x6803FF, "Bhutan"),
    (0x681000, 0x6813FF, "Micronesia"),
    (0x682000, 0x6823FF, "Mongolia"),
    (0x683000, 0x6833FF, "Kazakhstan"),
    (0x684000, 0x6843FF, "Palau"),
    (0x700000, 0x700FFF, "Afghanistan"),
    (0x702000, 0x702FFF, "Bangladesh"),
    (0x704000, 0x704FFF, "Myanmar"),
    (0x706000, 0x706FFF, "Kuwait"),
    (0x708000, 0x708FFF, "Laos"),
    (0x70A000, 0x70AFFF, "Nepal"),
    (0x70C000, 0x70C3FF, "Oman"),
    (0x70E000, 0x70EFFF, "Cambodia"),
    (0x710000, 0x717FFF, "Saudi Arabia"),
    (0x718000, 0x71FFFF, "Korea (South)"),
    (0x720000, 0x727FFF, "Korea (North)"),
    (0x728000, 0x72FFFF, "Iraq"),
    (0x730000, 0x737FFF, "Iran"),
    (0x738000, 0x73FFFF, "Israel"),
    (0x740000, 0x747FFF, "Jordan"),
    (0x748000, 0x74FFFF, "Lebanon"),
    (0x750000, 0x757FFF, "Malaysia"),
    (0x758000, 0x75FFFF, "Philippines"),
    (0x760000, 0x767FFF, "Pakistan"),
    (0x768000, 0x76FFFF, "Singapore"),
    (0x770000, 0x777FFF, "Sri Lanka"),
    (0x778000, 0x77FFFF, "Syria"),
    (0x780000, 0x7BFFFF, "China"),
    (0x7C0000, 0x7FFFFF, "Australia"),
    (0x800000, 0x83FFFF, "India"),
    (0x840000, 0x87FFFF, "Japan"),
    (0x880000, 0x887FFF, "Thailand"),
    (0x888000, 0x88FFFF, "Viet Nam"),
    (0x890000, 0x890FFF, "Yemen"),
    (0x894000, 0x894FFF, "Bahrain"),
    (0x895000, 0x8953FF, "Brunei"),
    (0x896000, 0x896FFF, "United Arab Emirates"),
    (0x897000, 0x8973FF, "Solomon Islands"),
    (0x898000, 0x898FFF, "Papua New Guinea"),
    (0x899000, 0x8993FF, "Taiwan (unofficial)"),
    (0x8A0000, 0x8A7FFF, "Indonesia"),
    (0x900000, 0x9003FF, "Marshall Islands"),
    (0x901000, 0x9013FF, "Cook Islands"),
    (0x902000, 0x9023FF, "Samoa"),
    (0xA00000, 0xAFFFFF, "United States"),
    (0xC00000, 0xC3FFFF, "Canada"),
    (0xC80000, 0xC87FFF, "New Zealand"),
    (0xC88000, 0xC88FFF, "Fiji"),
    (0xC8A000, 0xC8A3FF, "Nauru"),
    (0xC8C000, 0xC8C3FF, "Saint Lucia"),
    (0xC8D000, 0xC8D3FF, "Tonga"),
    (0xC8E000, 0xC8E3FF, "Kiribati"),
    (0xC90000, 0xC903FF, "Vanuatu"),
    (0xE00000, 0xE3FFFF, "Argentina"),
    (0xE40000, 0xE7FFFF, "Brazil"),
    (0xE80000, 0xE80FFF, "Chile"),
    (0xE84000, 0xE84FFF, "Ecuador"),
    (0xE88000, 0xE88FFF, "Paraguay"),
    (0xE8C000, 0xE8CFFF, "Peru"),
    (0xE90000, 0xE90FFF, "Uruguay"),
    (0xE94000, 0xE94FFF, "Bolivia"),
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


async def fetch_military_with_retry() -> List[dict]:
    """Called once per cycle by monitor_runner.run_military_shared_loop, not
    per-airport. On a 429 (rate limited), wait 30s and retry once before
    giving up — a transient rate-limit shouldn't cost every rapid-tracking
    airport a full cycle's worth of track updates. Gives up (re-raises)
    after 2 total attempts."""
    for attempt in range(2):
        try:
            return await asyncio.to_thread(_fetch_military)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 429 and attempt == 0:
                log.warning("adsb.fi rate limited (429) — retrying once in 30s")
                await asyncio.sleep(30)
                continue
            raise
    raise AssertionError("unreachable")


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


async def check_military(context: ContextTypes.DEFAULT_TYPE, military: List[dict]) -> None:
    """Processes ONE cfg's (airport's) view of an already-fetched, shared
    military list — the actual adsb.fi fetch happens once per cycle in
    monitor_runner.run_military_shared_loop, not here (see that function and
    fetch_military_with_retry for why: the endpoint is global, not scoped to
    any one airport)."""
    cfg = context.bot_data["cfg"]
    now_ts = int(datetime.now().timestamp())

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
                    photo_url = await asyncio.to_thread(jetphotos.fetch_photo_url, registration)
                    if photo_url:
                        cfg.store.upsert_airframe_from_fr24(registration, photo_url=photo_url)
                except Exception as exc:
                    log.debug("JetPhotos photo fetch failed for %s: %s", registration, exc)

            visit_flight_number = f"{callsign or registration}#{now_ts}"
            arrival_id, _is_new_visit = cfg.store.record_filter_match_ex(
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

            # Push notification — fires once per genuinely new Feed card (this
            # visit's flight_number is unique, so _is_new_visit is effectively
            # always True here), NOT on the Telegram cooldown below — every
            # other push type fires once per new card, not on a renotify timer,
            # and Military shouldn't be the odd one out. Fans out to every
            # eligible user (Controller + every Pilot/Passenger granted this
            # airport — see monitor._iter_push_recipients), each gated on
            # their own subscription/selected-airport/enabled-types, same as
            # every other push type. No per-owner content re-filtering is
            # needed here (unlike monitor.py's filter-match push) — there's no
            # per-Pilot military watchlist, so every recipient sees the same
            # detection. Tapping it deep-links to this registration's Feed
            # card — Military is already a first-class notif_type there.
            if _is_new_visit and cfg.control_store:
                from monitor import _iter_push_recipients, _push_recipient_lang
                import push as _push
                for _push_owner_id, _role in _iter_push_recipients(cfg):
                    try:
                        if cfg.control_store.get_last_airport(_push_owner_id) != cfg.airport_iata:
                            continue
                        _disabled = set(cfg.control_store.get_disabled_push_notif_types(_push_owner_id))
                        if "Military" in _disabled:
                            continue
                        _lang = _push_recipient_lang(cfg.control_store, _push_owner_id, _role)
                        _title = "军机接近中" if _lang == "zh" else "Military Aircraft Approaching"
                        # Country + bare type code (no dash) reads better than the
                        # manufacturer/model string here — e.g. "Australia (PC21)"
                        # instead of "Pilatus Pc-21 (PC-21)".
                        _type_code = (ac_type or "").replace("-", "")
                        _mid = f"{country} ({_type_code})" if _type_code else country
                        _dist_label = f"距{cfg.airport_iata} {dist_nm:.0f}海里" if _lang == "zh" else f"{dist_nm:.0f}nm from {cfg.airport_iata}"
                        _body = f"{registration} · {_mid} · {_dist_label}"
                        _push.send_push_to_user(
                            cfg.control_store, _push_owner_id, title=_title, body=_body,
                            data={"registration": registration},
                        )
                    except Exception as exc:
                        log.warning("Military push notification failed for %s (owner=%s): %s",
                                   registration, _push_owner_id, exc)

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
