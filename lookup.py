from __future__ import annotations

import logging
import re
from datetime import datetime

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, ConversationHandler,
    ContextTypes, MessageHandler, filters,
)

from monitor import _registration_flag

log = logging.getLogger(__name__)

# Registration: must contain hyphen or digit, can start with digit (9V-, 4R-), ends with letter or digit
_REGO_RE = re.compile(r"^(?=.*[-\d])[A-Z0-9][A-Z0-9\-]{2,8}$", re.IGNORECASE)

# Flight number: exactly 2 alphanumeric IATA code + 1-4 digits, nothing else
_FN_RE = re.compile(r"^[A-Z0-9]{2}\d{1,4}$", re.IGNORECASE)


def _is_registration(text: str) -> bool:
    """Unambiguously a registration: has hyphen, or ends with a letter."""
    t = text.upper().strip()
    return "-" in t or (t[-1].isalpha() and not _FN_RE.match(t))


def _classify(text: str):
    """Return ('rego', text), ('fn', text), ('ambiguous', text), or (None, None)."""
    t = text.strip().upper()
    is_rego = bool(_REGO_RE.match(t))
    is_fn   = bool(_FN_RE.match(t))

    if not is_rego and not is_fn:
        return None, None

    # Unambiguous rego: has hyphen, or ends with letter and can't be flight number
    if is_rego and ("-" in t or (t[-1].isalpha())):
        return "rego", t

    # Unambiguous flight number: matches FN but not rego
    if is_fn and not is_rego:
        return "fn", t

    # Both match (e.g. HL7732 — Korean registration that also looks like a flight number)
    if is_fn and is_rego:
        return "ambiguous", t

    if is_rego:
        return "rego", t
    return "fn", t


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


def _in_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True if the user is inside an active ConversationHandler."""
    for handler_group in context.application.handlers.values():
        for h in handler_group:
            if isinstance(h, ConversationHandler):
                try:
                    key = h._get_key(update)
                    if h._conversations.get(key) is not None:
                        return True
                except Exception:
                    pass
    return False


async def _do_rego_lookup(registration: str, update, context) -> None:
    cfg = context.bot_data["cfg"]
    flag = _registration_flag(registration)
    header = f"{registration}{' ' + flag if flag else ''}"
    lines = [f"<b>Lookup: {header}</b>", ""]

    aircraft_str = ""
    operator_str = ""

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

    tags = []
    if cfg.store.is_excluded(registration):
        tags.append("⛔ Exclusion List")
    if cfg.store.is_on_rego_watchlist(registration):
        tags.append("👁 Rego Watchlist")
    if tags:
        lines.append(f"Status: {' · '.join(tags)}")

    lines.append("")

    last_seen_ts = cfg.store.get_last_seen(registration)
    if last_seen_ts:
        lines.append(f"Last Seen at {cfg.airport_iata}: {_format_seen(last_seen_ts, cfg.airport_iata, cfg.airport_tz)}")
    else:
        lines.append(f"Last Seen at {cfg.airport_iata}: No record")

    lines.append("")

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

    await update.reply_html("\n".join(lines))


async def _do_fn_lookup(flight_number: str, update, context) -> None:
    cfg = context.bot_data["cfg"]
    rows = cfg.store.get_route_type_history(
        flight_number, cfg.airport_iata, cfg.route_type_lookback_days
    )

    lines = [f"<b>Flight {flight_number} at {cfg.airport_iata}</b>", ""]

    if not rows:
        lines.append("No equipment history recorded yet.")
        lines.append("History builds automatically as flights arrive.")
        await update.reply_html("\n".join(lines))
        return

    total = sum(r["count"] for r in rows)
    lines.append(f"Equipment history (last {cfg.route_type_lookback_days} days):")

    tz = pytz.timezone(cfg.airport_tz)
    for r in rows:
        pct = round(r["count"] / total * 100)
        since_str = datetime.fromtimestamp(r["first_seen_ts"]).astimezone(tz).strftime("%b %Y")
        last_str  = datetime.fromtimestamp(r["last_seen_ts"]).astimezone(tz).strftime("%-d %b")
        lines.append(f"  🛫 {r['aircraft_type']} — {r['count']} ops ({pct}%) · since {since_str} · last {last_str}")

    # Show established type if one exists
    established = cfg.store.get_established_route_type(
        flight_number, cfg.airport_iata,
        cfg.route_type_lookback_days,
        cfg.route_type_min_days,
        cfg.route_type_dominance_x,
    )
    if established:
        est_type, est_count, _ = established
        pct = round(est_count / total * 100)
        lines.append("")
        lines.append(f"Established: {est_type} ({pct}% of ops)")
    elif len(rows) > 1:
        lines.append("")
        lines.append("No established type — route is in transition or shows regular variation.")

    await update.reply_html("\n".join(lines))


async def handle_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    kind, value = _classify(text.strip())
    if kind is None:
        return

    if _in_conversation(update, context):
        return

    if kind == "rego":
        log.info("Lookup: rego=%r", value)
        await _do_rego_lookup(value, update.message, context)

    elif kind == "fn":
        log.info("Lookup: flight_number=%r", value)
        await _do_fn_lookup(value, update.message, context)

    elif kind == "ambiguous":
        log.info("Lookup: ambiguous=%r", value)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✈ Registration {value}", callback_data=f"lookup_reg_{value}"),
            InlineKeyboardButton(f"🔢 Flight {value}",      callback_data=f"lookup_fn_{value}"),
        ]])
        await update.message.reply_text(
            f"Did you mean registration or flight number?",
            reply_markup=keyboard,
        )


async def handle_lookup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    data = query.data  # e.g. "lookup_reg_HL7732" or "lookup_fn_HL7732"
    if data.startswith("lookup_reg_"):
        value = data.replace("lookup_reg_", "").upper()
        log.info("Lookup (disambiguated): rego=%r", value)
        await _do_rego_lookup(value, query.message, context)
    elif data.startswith("lookup_fn_"):
        value = data.replace("lookup_fn_", "").upper()
        log.info("Lookup (disambiguated): flight_number=%r", value)
        await _do_fn_lookup(value, query.message, context)


def register_lookup_handler(app: Application) -> None:
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lookup),
        group=1,
    )
    app.add_handler(
        CallbackQueryHandler(handle_lookup_callback, pattern=r"^lookup_(reg|fn)_.+$"),
        group=1,
    )
