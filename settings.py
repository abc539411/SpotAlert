from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import pytz

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from monitor import run_check
from military import check_military

log = logging.getLogger(__name__)

# Conversation states (offset from bot.py's range to avoid collisions)
(
    CATEGORY_SELECT,
    AIRPORT_SUBMENU,
    FILTER_SUBMENU,
    ENTER_VALUE,
    MILITARY_SUBMENU,
    SUMMARY_SUBMENU,
) = range(10, 16)

_REMOVE_KB = ReplyKeyboardRemove()

_VALID_DAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}

# Top-level category keyboard
_CATEGORY_KB = ReplyKeyboardMarkup(
    [
        ["Airport & Polling"],
        ["Special Livery", "Rare Plane"],
        ["Rego Watchlist", "Type Watchlist"],
        ["Military", "Summary"],
        ["Done"],
    ],
    resize_keyboard=True,
)

# Military sub-keyboard
_MILITARY_KB = ReplyKeyboardMarkup(
    [["Check Interval", "Search Radius"], ["Max Altitude", "Re-notify Interval"], ["Back"]],
    resize_keyboard=True,
)

# Airport & Polling sub-keyboard
_AIRPORT_KB = ReplyKeyboardMarkup(
    [["Airport Code", "Check Interval"], ["Force Check Now"], ["Back"]],
    resize_keyboard=True,
)

# Summary sub-keyboard
_SUMMARY_KB = ReplyKeyboardMarkup(
    [["Morning Start", "Morning End"], ["Afternoon Start", "Afternoon End"], ["Back"]],
    resize_keyboard=True,
)

# Per-filter sub-keyboard (Special Livery, Rego/Type Watchlist)
_FILTER_KB = ReplyKeyboardMarkup(
    [["Re-notify Interval", "Active Days", "Arrival Window"], ["Back"]],
    resize_keyboard=True,
)

# Rare Plane sub-keyboard — uses different interval label
_RARE_PLANE_FILTER_KB = ReplyKeyboardMarkup(
    [["Min Absence", "Active Days", "Arrival Window"], ["Back"]],
    resize_keyboard=True,
)

# Arrival window options presented as a keyboard so the user can't enter something invalid
_ARRIVAL_WINDOW_KB = ReplyKeyboardMarkup(
    [["Always", "Daylight Only", "Off"], ["Cancel"]],
    resize_keyboard=True,
)

# Maps category name → AppConfig field names and DB setting keys
_FILTER_META = {
    "Special Livery": {
        "cfg_interval":  "livery_interval_hours",
        "cfg_days":      "livery_days",
        "cfg_window":    "livery_time_filter",
        "db_interval":   "SPECIAL_LIVERY_RENOTIFY_HOURS",
        "db_days":       "SPECIAL_LIVERY_ACTIVE_DAYS",
        "db_window":     "SPECIAL_LIVERY_ARRIVAL_WINDOW",
        "interval_unit": "hours",
    },
    "Rare Plane": {
        "cfg_interval":   "rare_plane_min_absence_days",
        "cfg_days":       "rare_plane_days",
        "cfg_window":     "rare_plane_time_filter",
        "db_interval":    "RARE_PLANE_MIN_ABSENCE_DAYS",
        "db_days":        "RARE_PLANE_ACTIVE_DAYS",
        "db_window":      "RARE_PLANE_ARRIVAL_WINDOW",
        "interval_unit":  "days",
        "interval_label": "Min Absence",
    },
    "Rego Watchlist": {
        "cfg_interval":  "rego_interval_hours",
        "cfg_days":      "rego_days",
        "cfg_window":    "rego_time_filter",
        "db_interval":   "REGO_WATCHLIST_RENOTIFY_HOURS",
        "db_days":       "REGO_WATCHLIST_ACTIVE_DAYS",
        "db_window":     "REGO_WATCHLIST_ARRIVAL_WINDOW",
        "interval_unit": "hours",
    },
    "Type Watchlist": {
        "cfg_interval":  "type_interval_hours",
        "cfg_days":      "type_days",
        "cfg_window":    "type_time_filter",
        "db_interval":   "TYPE_WATCHLIST_RENOTIFY_HOURS",
        "db_days":       "TYPE_WATCHLIST_ACTIVE_DAYS",
        "db_window":     "TYPE_WATCHLIST_ARRIVAL_WINDOW",
        "interval_unit": "hours",
    },
}

# Normalise arrival window user input → internal value
_WINDOW_INPUT_MAP = {
    "always":        "",
    "daylight only": "Daylight",
    "daylight":      "Daylight",
    "off":           "Off",
    "":              "",
}

# Human-readable label for display
_WINDOW_LABEL = {"": "Always", "Daylight": "Daylight Only", "Off": "Disabled"}


# ------------------------------------------------------------------
# Formatting helpers
# ------------------------------------------------------------------

def _next_check_str(context, cfg) -> str:
    jobs = context.application.job_queue.get_jobs_by_name("arrivals_check")
    if jobs and jobs[0].next_t:
        tz = pytz.timezone(cfg.airport_tz)
        return jobs[0].next_t.astimezone(tz).strftime("%H:%M")
    return "N/A"


def _days_label(days: list) -> str:
    return ", ".join(days) if days else "All Days"


def _window_label(window: str) -> str:
    return _WINDOW_LABEL.get(window, window)


def _overview(cfg) -> str:
    lines = [
        "<b>Current Settings</b>",
        "",
        f"<b>Airport:</b>          {cfg.airport_name} ({cfg.airport_iata}/{cfg.airport_icao})",
        f"<b>Check Interval:</b>   {cfg.check_interval // 60} min",
        "",
        "<b>Filter Settings</b>  <i>(interval · active days · arrival window)</i>",
        f"  Special Livery:   {cfg.livery_interval_hours}h renotify · {_days_label(cfg.livery_days)} · {_window_label(cfg.livery_time_filter)}",
        f"  Rare Plane:       {cfg.rare_plane_min_absence_days}d absence · {_days_label(cfg.rare_plane_days)} · {_window_label(cfg.rare_plane_time_filter)}",
        f"  Rego Watchlist:   {cfg.rego_interval_hours}h · {_days_label(cfg.rego_days)} · {_window_label(cfg.rego_time_filter)}",
        f"  Type Watchlist:   {cfg.type_interval_hours}h · {_days_label(cfg.type_days)} · {_window_label(cfg.type_time_filter)}",
        "",
        "<b>Military</b>  <i>(adsb.fi)</i>",
        f"  Check Interval:  {cfg.military_check_interval // 60} min",
        f"  Search Radius:   {cfg.military_radius_nm} nm",
        f"  Max Altitude:    {cfg.military_max_alt_ft} ft",
        f"  Re-notify:       {cfg.military_renotify_hours}h",
        "",
        "<b>Summary Periods</b>",
        f"  Morning:    {cfg.summary_morning_pre_sunrise_hours}h pre-sunrise → {cfg.summary_morning_end_hour}:00",
        f"  Afternoon:  {cfg.summary_afternoon_start_hour}:00 → {cfg.summary_afternoon_post_sunset_hours}h post-sunset",
    ]
    return "\n".join(lines)


def _filter_detail(cfg, category: str) -> str:
    m = _FILTER_META[category]
    interval = getattr(cfg, m["cfg_interval"])
    days     = getattr(cfg, m["cfg_days"])
    window   = getattr(cfg, m["cfg_window"])
    interval_label = m.get("interval_label", "Re-notify Interval")
    return (
        f"<b>{category}</b>\n\n"
        f"  {interval_label}:   {interval} {m['interval_unit']}\n"
        f"  Active Days:         {_days_label(days)}\n"
        f"  Arrival Window:      {_window_label(window)}"
    )


# ------------------------------------------------------------------
# Conversation handlers
# ------------------------------------------------------------------

async def start_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = context.bot_data["cfg"]
    await update.message.reply_html(_overview(cfg), reply_markup=_CATEGORY_KB)
    return CATEGORY_SELECT


async def handle_category_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    cfg = context.bot_data["cfg"]

    if choice == "Done":
        await update.message.reply_text("Settings closed.", reply_markup=_REMOVE_KB)
        return ConversationHandler.END

    if choice == "Airport & Polling":
        text = (
            f"<b>Airport &amp; Polling</b>\n\n"
            f"  Airport:          {cfg.airport_name} ({cfg.airport_iata}/{cfg.airport_icao})\n"
            f"  Check Interval:   {cfg.check_interval // 60} min\n"
            f"  Next Check:       {_next_check_str(context, cfg)} (local)"
        )
        await update.message.reply_html(text, reply_markup=_AIRPORT_KB)
        return AIRPORT_SUBMENU

    if choice in _FILTER_META:
        context.user_data["settings_category"] = choice
        kb = _RARE_PLANE_FILTER_KB if choice == "Rare Plane" else _FILTER_KB
        await update.message.reply_html(_filter_detail(cfg, choice), reply_markup=kb)
        return FILTER_SUBMENU

    if choice == "Military":
        context.user_data["settings_category"] = "Military"
        await update.message.reply_html(_military_detail(cfg), reply_markup=_MILITARY_KB)
        return MILITARY_SUBMENU

    if choice == "Summary":
        context.user_data["settings_category"] = "Summary"
        await update.message.reply_html(_summary_period_detail(cfg), reply_markup=_SUMMARY_KB)
        return SUMMARY_SUBMENU

    await update.message.reply_text("Please choose a category from the keyboard.")
    return CATEGORY_SELECT


async def handle_airport_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    cfg = context.bot_data["cfg"]

    if choice == "Back":
        await update.message.reply_html(_overview(cfg), reply_markup=_CATEGORY_KB)
        return CATEGORY_SELECT

    context.user_data["settings_field"] = choice

    if choice == "Airport Code":
        await update.message.reply_text(
            f"Current: {cfg.airport_name} ({cfg.airport_iata}/{cfg.airport_icao})\n\n"
            "Enter the IATA or ICAO code of the new airport (e.g. MEL or YMML)",
            reply_markup=_REMOVE_KB,
        )
        return ENTER_VALUE

    if choice == "Check Interval":
        await update.message.reply_text(
            f"Current: {cfg.check_interval // 60} min\n\n"
            "Enter the new check interval in minutes (1–120)",
            reply_markup=_REMOVE_KB,
        )
        return ENTER_VALUE

    if choice == "Force Check Now":
        for job in context.application.job_queue.get_jobs_by_name("arrivals_check"):
            job.schedule_removal()
        context.application.job_queue.run_repeating(
            run_check, interval=cfg.check_interval, first=0,
            data=cfg.chat_id, name="arrivals_check",
        )
        tz = pytz.timezone(cfg.airport_tz)
        from datetime import timedelta
        next_time = (datetime.now(tz) + timedelta(seconds=cfg.check_interval)).strftime("%H:%M")
        await update.message.reply_text(
            f"Check triggered. Next automatic check at {next_time} (local).",
            reply_markup=_AIRPORT_KB,
        )
        return AIRPORT_SUBMENU

    await update.message.reply_text("Please choose from the keyboard.")
    return AIRPORT_SUBMENU


async def handle_filter_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    cfg = context.bot_data["cfg"]
    category = context.user_data.get("settings_category")

    if choice == "Back":
        await update.message.reply_html(_overview(cfg), reply_markup=_CATEGORY_KB)
        return CATEGORY_SELECT

    if choice not in {"Re-notify Interval", "Min Absence", "Active Days", "Arrival Window"}:
        await update.message.reply_text("Please choose from the keyboard.")
        return FILTER_SUBMENU

    context.user_data["settings_field"] = choice
    m = _FILTER_META[category]

    if choice in {"Re-notify Interval", "Min Absence"}:
        current = getattr(cfg, m["cfg_interval"])
        unit = m["interval_unit"]
        label = m.get("interval_label", "Re-notify Interval")
        await update.message.reply_text(
            f"Current: {current} {unit}\n\n"
            f"Enter new value in {unit} ({label})",
            reply_markup=_REMOVE_KB,
        )

    elif choice == "Active Days":
        current_days = getattr(cfg, m["cfg_days"])
        await update.message.reply_text(
            f"Current: {_days_label(current_days)}\n\n"
            "Enter days as comma-separated abbreviations (e.g. Sat,Sun), "
            "or send a dash (-) for all days",
            reply_markup=_REMOVE_KB,
        )

    elif choice == "Arrival Window":
        current_window = getattr(cfg, m["cfg_window"])
        await update.message.reply_text(
            f"Current: {_window_label(current_window)}\n\n"
            "Choose the new arrival window",
            reply_markup=_ARRIVAL_WINDOW_KB,
        )

    return ENTER_VALUE


def _summary_period_detail(cfg) -> str:
    return (
        "<b>Summary Periods</b>\n\n"
        f"  Morning Start:    {cfg.summary_morning_pre_sunrise_hours}h before sunrise\n"
        f"  Morning End:      {cfg.summary_morning_end_hour}:00\n"
        f"  Afternoon Start:  {cfg.summary_afternoon_start_hour}:00\n"
        f"  Afternoon End:    {cfg.summary_afternoon_post_sunset_hours}h after sunset"
    )


async def handle_summary_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    cfg    = context.bot_data["cfg"]

    if choice == "Back":
        await update.message.reply_html(_overview(cfg), reply_markup=_CATEGORY_KB)
        return CATEGORY_SELECT

    if choice not in {"Morning Start", "Morning End", "Afternoon Start", "Afternoon End"}:
        await update.message.reply_text("Please choose from the keyboard.")
        return SUMMARY_SUBMENU

    context.user_data["settings_field"] = choice

    if choice == "Morning Start":
        await update.message.reply_text(
            f"Current: {cfg.summary_morning_pre_sunrise_hours}h before sunrise\n\n"
            "Enter hours before sunrise that morning begins (e.g. 1)",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Morning End":
        await update.message.reply_text(
            f"Current: {cfg.summary_morning_end_hour}:00\n\n"
            "Enter the hour (0–23) that morning ends",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Afternoon Start":
        await update.message.reply_text(
            f"Current: {cfg.summary_afternoon_start_hour}:00\n\n"
            "Enter the hour (0–23) that afternoon begins",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Afternoon End":
        await update.message.reply_text(
            f"Current: {cfg.summary_afternoon_post_sunset_hours}h after sunset\n\n"
            "Enter hours after sunset that afternoon ends (e.g. 1)",
            reply_markup=_REMOVE_KB,
        )

    return ENTER_VALUE


def _military_detail(cfg) -> str:
    return (
        "<b>Military</b>  <i>(adsb.fi — no API key needed)</i>\n\n"
        f"  Check Interval:  {cfg.military_check_interval // 60} min\n"
        f"  Search Radius:   {cfg.military_radius_nm} nm\n"
        f"  Max Altitude:    {cfg.military_max_alt_ft} ft\n"
        f"  Re-notify:       {cfg.military_renotify_hours}h"
    )


async def handle_military_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    cfg    = context.bot_data["cfg"]

    if choice == "Back":
        await update.message.reply_html(_overview(cfg), reply_markup=_CATEGORY_KB)
        return CATEGORY_SELECT

    if choice not in {"Check Interval", "Search Radius", "Max Altitude", "Re-notify Interval"}:
        await update.message.reply_text("Please choose from the keyboard.")
        return MILITARY_SUBMENU

    context.user_data["settings_field"] = choice

    if choice == "Check Interval":
        await update.message.reply_text(
            f"Current: {cfg.military_check_interval // 60} min\n\n"
            "Enter the new military check interval in minutes (1–60)",
            reply_markup=_REMOVE_KB,
        )
        return ENTER_VALUE

    if choice == "Search Radius":
        await update.message.reply_text(
            f"Current: {cfg.military_radius_nm} nm\n\n"
            "Enter search radius in nautical miles (1–250)",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Max Altitude":
        await update.message.reply_text(
            f"Current: {cfg.military_max_alt_ft} ft\n\n"
            "Enter maximum altitude in feet for approach detection",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Re-notify Interval":
        await update.message.reply_text(
            f"Current: {cfg.military_renotify_hours}h\n\n"
            "Enter re-notify interval in hours",
            reply_markup=_REMOVE_KB,
        )

    return ENTER_VALUE


async def handle_enter_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    field    = context.user_data.get("settings_field")
    category = context.user_data.get("settings_category")
    cfg      = context.bot_data["cfg"]
    store    = cfg.store

    # ----------------------------------------------------------------
    # Airport code
    # ----------------------------------------------------------------
    if field == "Airport Code":
        code = raw.upper()
        try:
            data    = cfg.fr_api.get_airport_details(code=code)
            details = data["airport"]["pluginData"]["details"]
            cfg.airport_code = code
            cfg.airport_name = details["name"]
            cfg.airport_iata = details["code"]["iata"]
            cfg.airport_icao = details["code"]["icao"]
            cfg.airport_tz   = details["timezone"]["name"]
            cfg.airport_lat  = details["position"]["latitude"]
            cfg.airport_lon  = details["position"]["longitude"]
            store.save_setting("AIRPORT_CODE", code)
            await update.message.reply_text(
                f"Updated: now monitoring {cfg.airport_name} ({cfg.airport_iata}/{cfg.airport_icao})",
                reply_markup=_AIRPORT_KB,
            )
        except Exception as exc:
            log.warning("Airport lookup failed for %s: %s", code, exc)
            await update.message.reply_text(
                f"Could not find airport '{code}' — please check the code and try again.",
                reply_markup=_REMOVE_KB,
            )
        return AIRPORT_SUBMENU

    # ----------------------------------------------------------------
    # Check interval
    # ----------------------------------------------------------------
    if field == "Check Interval":
        try:
            minutes = int(raw)
            if not 1 <= minutes <= 120:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Please enter a whole number between 1 and 120.",
                reply_markup=_REMOVE_KB,
            )
            return ENTER_VALUE

        cfg.check_interval = minutes * 60
        store.save_setting("CHECK_INTERVAL_MINUTES", str(minutes))

        # Remove the existing job and re-add it with the new interval
        for job in context.application.job_queue.get_jobs_by_name("arrivals_check"):
            job.schedule_removal()
        context.application.job_queue.run_repeating(
            run_check, interval=cfg.check_interval, first=0,
            data=cfg.chat_id, name="arrivals_check",
        )
        await update.message.reply_text(
            f"Updated: checking every {minutes} min.",
            reply_markup=_AIRPORT_KB,
        )
        return AIRPORT_SUBMENU

    # ----------------------------------------------------------------
    # Summary period settings
    # ----------------------------------------------------------------
    if field == "Morning Start":
        try:
            value = int(raw)
            if value < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a non-negative whole number.")
            return ENTER_VALUE
        cfg.summary_morning_pre_sunrise_hours = value
        store.save_setting("SUMMARY_MORNING_PRE_SUNRISE_HOURS", str(value))
        await update.message.reply_html(
            f"Updated.\n\n{_summary_period_detail(cfg)}", reply_markup=_SUMMARY_KB
        )
        return SUMMARY_SUBMENU

    if field == "Morning End":
        try:
            value = int(raw)
            if not 0 <= value <= 23:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a whole number between 0 and 23.")
            return ENTER_VALUE
        cfg.summary_morning_end_hour = value
        store.save_setting("SUMMARY_MORNING_END_HOUR", str(value))
        await update.message.reply_html(
            f"Updated.\n\n{_summary_period_detail(cfg)}", reply_markup=_SUMMARY_KB
        )
        return SUMMARY_SUBMENU

    if field == "Afternoon Start":
        try:
            value = int(raw)
            if not 0 <= value <= 23:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a whole number between 0 and 23.")
            return ENTER_VALUE
        cfg.summary_afternoon_start_hour = value
        store.save_setting("SUMMARY_AFTERNOON_START_HOUR", str(value))
        await update.message.reply_html(
            f"Updated.\n\n{_summary_period_detail(cfg)}", reply_markup=_SUMMARY_KB
        )
        return SUMMARY_SUBMENU

    if field == "Afternoon End":
        try:
            value = int(raw)
            if value < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a non-negative whole number.")
            return ENTER_VALUE
        cfg.summary_afternoon_post_sunset_hours = value
        store.save_setting("SUMMARY_AFTERNOON_POST_SUNSET_HOURS", str(value))
        await update.message.reply_html(
            f"Updated.\n\n{_summary_period_detail(cfg)}", reply_markup=_SUMMARY_KB
        )
        return SUMMARY_SUBMENU

    # ----------------------------------------------------------------
    # Filter settings
    # ----------------------------------------------------------------
    m = _FILTER_META[category]

    if field in {"Re-notify Interval", "Min Absence"}:
        try:
            value = int(raw)
            if value < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a positive whole number.")
            return ENTER_VALUE
        setattr(cfg, m["cfg_interval"], value)
        store.save_setting(m["db_interval"], str(value))
        kb = _RARE_PLANE_FILTER_KB if category == "Rare Plane" else _FILTER_KB
        await update.message.reply_html(
            f"Updated.\n\n{_filter_detail(cfg, category)}", reply_markup=kb
        )
        return FILTER_SUBMENU

    if field == "Active Days":
        if raw in ("-", ""):
            new_days = []
        else:
            new_days = [d.strip().capitalize() for d in raw.split(",") if d.strip()]
            invalid = [d for d in new_days if d not in _VALID_DAYS]
            if invalid:
                await update.message.reply_text(
                    f"Unrecognised day(s): {', '.join(invalid)}\n"
                    "Valid values: Mon Tue Wed Thu Fri Sat Sun"
                )
                return ENTER_VALUE
        setattr(cfg, m["cfg_days"], new_days)
        store.save_setting(m["db_days"], ",".join(new_days))
        kb = _RARE_PLANE_FILTER_KB if category == "Rare Plane" else _FILTER_KB
        await update.message.reply_html(
            f"Updated.\n\n{_filter_detail(cfg, category)}", reply_markup=kb
        )
        return FILTER_SUBMENU

    if field == "Arrival Window":
        kb = _RARE_PLANE_FILTER_KB if category == "Rare Plane" else _FILTER_KB
        if raw.lower() == "cancel":
            await update.message.reply_html(
                _filter_detail(cfg, category), reply_markup=kb
            )
            return FILTER_SUBMENU

        normalised = _WINDOW_INPUT_MAP.get(raw.lower())
        if normalised is None:
            await update.message.reply_text(
                "Please choose from the keyboard: Always, Daylight Only, or Off.",
                reply_markup=_ARRIVAL_WINDOW_KB,
            )
            return ENTER_VALUE
        setattr(cfg, m["cfg_window"], normalised)
        store.save_setting(m["db_window"], normalised)
        await update.message.reply_html(
            f"Updated.\n\n{_filter_detail(cfg, category)}", reply_markup=kb
        )
        return FILTER_SUBMENU

    # ----------------------------------------------------------------
    # Military settings
    # ----------------------------------------------------------------
    if field == "Check Interval" and context.user_data.get("settings_category") == "Military":
        try:
            minutes = int(raw)
            if not 1 <= minutes <= 60:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a whole number between 1 and 60.")
            return ENTER_VALUE
        cfg.military_check_interval = minutes * 60
        store.save_setting("MILITARY_CHECK_INTERVAL_MINUTES", str(minutes))
        for job in context.application.job_queue.get_jobs_by_name("military_check"):
            job.schedule_removal()
        context.application.job_queue.run_repeating(
            check_military, interval=cfg.military_check_interval, first=0,
            name="military_check",
        )
        await update.message.reply_html(
            f"Updated.\n\n{_military_detail(cfg)}", reply_markup=_MILITARY_KB
        )
        return MILITARY_SUBMENU

    if field == "Search Radius":
        try:
            value = int(raw)
            if not 1 <= value <= 250:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a whole number between 1 and 250.")
            return ENTER_VALUE
        cfg.military_radius_nm = value
        store.save_setting("MILITARY_RADIUS_NM", str(value))
        await update.message.reply_html(
            f"Updated.\n\n{_military_detail(cfg)}", reply_markup=_MILITARY_KB
        )
        return MILITARY_SUBMENU

    if field == "Max Altitude":
        try:
            value = int(raw)
            if value < 100:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a positive altitude in feet (minimum 100).")
            return ENTER_VALUE
        cfg.military_max_alt_ft = value
        store.save_setting("MILITARY_MAX_ALT_FT", str(value))
        await update.message.reply_html(
            f"Updated.\n\n{_military_detail(cfg)}", reply_markup=_MILITARY_KB
        )
        return MILITARY_SUBMENU

    if field == "Re-notify Interval":
        try:
            value = int(raw)
            if value < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a positive whole number.")
            return ENTER_VALUE
        cfg.military_renotify_hours = value
        store.save_setting("MILITARY_RENOTIFY_HOURS", str(value))
        await update.message.reply_html(
            f"Updated.\n\n{_military_detail(cfg)}", reply_markup=_MILITARY_KB
        )
        return MILITARY_SUBMENU

    # Should never reach here
    return ConversationHandler.END


async def cancel_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Settings closed.", reply_markup=_REMOVE_KB)
    return ConversationHandler.END


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

def register_settings_handlers(app: Application) -> None:
    conversation = ConversationHandler(
        entry_points=[CommandHandler("settings", start_settings)],
        states={
            CATEGORY_SELECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category_select)
            ],
            AIRPORT_SUBMENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_airport_submenu)
            ],
            FILTER_SUBMENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_filter_submenu)
            ],
            ENTER_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_enter_value)
            ],
            MILITARY_SUBMENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_military_submenu)
            ],
            SUMMARY_SUBMENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_summary_submenu)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_settings)],
        allow_reentry=True,
    )
    app.add_handler(conversation)
