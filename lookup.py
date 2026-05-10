from __future__ import annotations

import logging
import re
from datetime import datetime

import pytz
from telegram import Update
from telegram.ext import Application, ConversationHandler, ContextTypes, MessageHandler, filters

from monitor import _registration_flag

log = logging.getLogger(__name__)

# Matches a bare registration — must contain at least one hyphen or digit to avoid
# matching plain words like "Done", "Cancel", "Today" from keyboard menus
_REGO_RE = re.compile(r"^(?=.*[-\d])[A-Z][A-Z0-9\-]{2,8}$", re.IGNORECASE)


def _extract_registration(text: str) -> str | None:
    text = text.strip()
    if _REGO_RE.match(text):
        return text.upper()
    return None


def _format_seen(last_seen_ts: int, airport_iata: str, airport_tz: str) -> str:
    tz = pytz.timezone(airport_tz)
    now_ts = int(datetime.now().timestamp())
    days_ago = (now_ts - last_seen_ts) // 86400
    date_str = datetime.fromtimestamp(last_seen_ts).astimezone(tz).strftime("%d %b %Y")
    if days_ago == 0:
        return f"{date_str} (today)"
    if days_ago == 1:
        return f"{date_str} (yesterday)"
    if days_ago <= 7:
        return f"{date_str} ({days_ago} days ago)"
    return date_str


async def handle_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    registration = _extract_registration(text)
    if not registration:
        return

    # Skip if user is inside an active ConversationHandler
    for handler_group in context.application.handlers.values():
        for h in handler_group:
            if isinstance(h, ConversationHandler):
                try:
                    key = h._get_key(update)
                    if h._conversations.get(key) is not None:
                        return
                except Exception:
                    pass

    log.info("Lookup: text=%r → registration=%r", text, registration)

    cfg = context.bot_data["cfg"]

    flag = _registration_flag(registration)
    header = f"{registration}{' ' + flag if flag else ''}"
    lines = [f"<b>Lookup: {header}</b>", ""]

    # --- Aircraft details: DB first, FR24 fallback ---
    aircraft_str = ""
    operator_str = ""

    # Check notification_record for stored detail ("Airline (Type)")
    try:
        for record in cfg.store.get_tracked_flights():
            if record["registration"] == registration and record["detail"]:
                import re as _re
                detail = record["detail"]
                m = _re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", detail)
                if m:
                    operator_str = m.group(1).strip()
                    aircraft_str = m.group(2).strip()
                else:
                    operator_str = detail
                break
    except Exception as exc:
        log.warning("DB detail lookup failed for %s: %s", registration, exc)

    # Fallback to FR24 if DB had nothing
    if not aircraft_str and not operator_str:
        try:
            rego_details = cfg.fr_api.get_rego_details(registration)
            data = (rego_details or {}).get("data") or []
            if data:
                aircraft_code = ((data[0].get("aircraft") or {}).get("model") or {}).get("code") or ""
                aircraft_name = ((data[0].get("aircraft") or {}).get("model") or {}).get("text") or ""
                airline_name  = (data[0].get("airline") or {}).get("name") or ""
            else:
                info = (rego_details or {}).get("aircraftInfo") or {}
                aircraft_code = (info.get("model") or {}).get("code") or ""
                aircraft_name = (info.get("model") or {}).get("text") or ""
                airline_name  = (info.get("airline") or {}).get("name") or ""
            aircraft_str = f"{aircraft_name} ({aircraft_code})" if aircraft_name else aircraft_code
            operator_str = airline_name
        except Exception as exc:
            log.warning("FR24 lookup failed for %s: %s", registration, exc)

    if aircraft_str:
        lines.append(f"Aircraft: {aircraft_str}")
    if operator_str:
        lines.append(f"Operator: {operator_str}")

    lines.append("")

    # --- Last Seen at airport (sighting_history) ---
    last_seen_ts = cfg.store.get_last_seen(registration)
    if last_seen_ts:
        lines.append(f"Last Seen at {cfg.airport_iata}: {_format_seen(last_seen_ts, cfg.airport_iata, cfg.airport_tz)}")
    else:
        lines.append(f"Last Seen at {cfg.airport_iata}: No record")

    lines.append("")

    # --- Spotting sessions (Lightroom catalog) ---
    if cfg.catalog:
        sessions = cfg.catalog.get_all_sessions(registration)
        if sessions:
            n = len(sessions)
            lines.append(f"Spotted {n} time{'s' if n != 1 else ''}:")
            for dt, apt in sessions:
                apt_str = f" — {apt}" if apt else ""
                lines.append(f"  • {dt.strftime('%d %b %Y, %H:%M')}{apt_str}")
        else:
            lines.append("Not yet photographed")

    lines.append(f"\nhttps://www.flightradar24.com/data/aircraft/{registration.lower()}")

    await update.message.reply_html("\n".join(lines))


def register_lookup_handler(app: Application) -> None:
    # Group 1 — runs after ConversationHandlers (group 0) so it never
    # intercepts keyboard replies from /filters, /settings, or /summary.
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lookup),
        group=1,
    )
