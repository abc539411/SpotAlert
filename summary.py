from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

import pytz
from astral import LocationInfo
from astral.sun import sun
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from monitor import get_next_departure

log = logging.getLogger(__name__)

SUMMARY_DAY, SUMMARY_PERIOD = range(20, 22)

_DAY_KB = ReplyKeyboardMarkup(
    [["Today", "Tomorrow"], ["Cancel"]],
    resize_keyboard=True,
)
_PERIOD_KB = ReplyKeyboardMarkup(
    [["Morning", "Afternoon", "All Day"], ["Cancel"]],
    resize_keyboard=True,
)
_REMOVE_KB = ReplyKeyboardRemove()


async def start_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Select day:", reply_markup=_DAY_KB)
    return SUMMARY_DAY


async def handle_summary_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    if text == "Cancel":
        await update.message.reply_text("Cancelled.", reply_markup=_REMOVE_KB)
        return ConversationHandler.END
    if text not in ("Today", "Tomorrow"):
        await update.message.reply_text("Please select Today or Tomorrow.")
        return SUMMARY_DAY
    context.user_data["summary_day"] = text
    await update.message.reply_text("Select period:", reply_markup=_PERIOD_KB)
    return SUMMARY_PERIOD


async def handle_summary_period(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    period = update.message.text
    if period == "Cancel":
        await update.message.reply_text("Cancelled.", reply_markup=_REMOVE_KB)
        return ConversationHandler.END
    if period not in ("Morning", "Afternoon", "All Day"):
        await update.message.reply_text("Please select Morning, Afternoon, or All Day.")
        return SUMMARY_PERIOD

    day = context.user_data.get("summary_day", "Today")
    cfg = context.bot_data["cfg"]
    tz = pytz.timezone(cfg.airport_tz)
    today = datetime.now(tz).date()
    target_date = today if day == "Today" else today + timedelta(days=1)

    # Compute solar times for the target date
    tz_parts = cfg.airport_tz.split("/")
    location = LocationInfo(tz_parts[-1], tz_parts[0], cfg.airport_tz, cfg.airport_lat, cfg.airport_lon)
    sun_info = sun(location.observer, date=target_date, tzinfo=location.timezone)
    sunrise_ts = int(sun_info["sunrise"].timestamp())
    sunset_ts  = int(sun_info["sunset"].timestamp())

    morning_start_ts   = sunrise_ts - cfg.summary_morning_pre_sunrise_hours * 3600
    morning_end_ts     = int(datetime(target_date.year, target_date.month, target_date.day,
                                      cfg.summary_morning_end_hour, tzinfo=tz).timestamp())
    afternoon_start_ts = int(datetime(target_date.year, target_date.month, target_date.day,
                                      cfg.summary_afternoon_start_hour, tzinfo=tz).timestamp())
    afternoon_end_ts   = sunset_ts + cfg.summary_afternoon_post_sunset_hours * 3600

    records = cfg.store.get_tracked_flights()

    filtered = []
    for r in records:
        arr_ts = int(r["arrival_ts"])
        if arr_ts <= 0:
            continue
        arr_dt = datetime.fromtimestamp(arr_ts, tz)
        if arr_dt.date() != target_date:
            continue
        if period == "Morning" and not (morning_start_ts <= arr_ts < morning_end_ts):
            continue
        if period == "Afternoon" and not (afternoon_start_ts <= arr_ts <= afternoon_end_ts):
            continue
        filtered.append((arr_ts, r))

    filtered.sort(key=lambda x: x[0])

    if not filtered:
        await update.message.reply_text(
            f"No notified flights for {day} {period}.",
            reply_markup=_REMOVE_KB,
        )
        return ConversationHandler.END

    await update.message.reply_text("Generating summary...", reply_markup=_REMOVE_KB)

    def _hhmm(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz).strftime("%H%M")

    if period == "Morning":
        period_range = f"{_hhmm(morning_start_ts)}-{_hhmm(morning_end_ts)}"
    elif period == "Afternoon":
        period_range = f"{_hhmm(afternoon_start_ts)}-{_hhmm(afternoon_end_ts)}"
    else:
        period_range = "All Day"

    lines = [
        f"<b>Notified Flights — {day}, {period}</b>",
        f"{period_range} (All times are local)",
        "",
    ]
    for arr_ts, r in filtered:
        registration = r["registration"]
        notif_type   = r["notif_type"] or "Unknown"
        arr_str      = datetime.fromtimestamp(arr_ts, tz).strftime("%H:%M")

        try:
            extra_info = r["extra_info"] or ""
        except (IndexError, KeyError):
            extra_info = ""

        # If a livery name was recorded, always show this as Special Livery
        # regardless of what filter matched first
        if extra_info:
            notif_type = "Special Livery"

        airline_name = ""
        aircraft_code = ""
        dep_str = ""
        try:
            rego_details = cfg.fr_api.get_rego_details(registration)
            data = (rego_details or {}).get("data") or []
            if data:
                aircraft_code = ((data[0].get("aircraft") or {}).get("model") or {}).get("code") or ""
                airline_name  = (data[0].get("airline") or {}).get("name") or ""
            dep_time, _, _, _, _, _, dest_iata, _, _ = get_next_departure(
                rego_details, cfg.airport_iata, cfg.airport_tz
            )
            if dep_time:
                dep_str = f" → dep {dep_time.strftime('%H:%M')}"
                if dest_iata:
                    dep_str += f" to {dest_iata}"
        except Exception:
            pass

        # Strip bracket content from airline name (e.g. "Air New Zealand (All Black Livery)" → "Air New Zealand")
        clean_airline = re.sub(r'\s*\(.*?\)', '', airline_name).strip()

        if notif_type == "Special Livery" and extra_info:
            detail = f"{clean_airline} ({aircraft_code}) - {extra_info}" if aircraft_code else f"{clean_airline} - {extra_info}"
        else:
            detail = f"{clean_airline} ({aircraft_code})" if clean_airline and aircraft_code else clean_airline or aircraft_code

        line = f"<b>{notif_type}</b> — {registration}"
        if detail:
            line += f" — {detail}"
        line += f" — arr {arr_str}{dep_str}"
        lines.append(line)

    await update.message.reply_html("\n".join(lines), reply_markup=_REMOVE_KB)
    return ConversationHandler.END


async def cancel_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.", reply_markup=_REMOVE_KB)
    return ConversationHandler.END


def register_summary_handlers(app: Application) -> None:
    conversation = ConversationHandler(
        entry_points=[CommandHandler("summary", start_summary)],
        states={
            SUMMARY_DAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_summary_day)
            ],
            SUMMARY_PERIOD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_summary_period)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_summary)],
        allow_reentry=True,
    )
    app.add_handler(conversation)
