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
    FILTER_CATEGORY_SUBMENU,
    SPOT_REC_SUBMENU,
    USER_SUBMENU,
    LIGHTING_SUBMENU,
    SESSIONS_SUBMENU,
) = range(10, 20)

_REMOVE_KB = ReplyKeyboardRemove()

_VALID_DAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}

# Top-level category keyboard — grouped to keep it manageable
_CATEGORY_KB = ReplyKeyboardMarkup(
    [
        ["Monitoring", "Filters"],
        ["Military"],
        ["Spot Recommendation"],
        ["Users"],
        ["Done"],
    ],
    resize_keyboard=True,
)

_USER_KB = ReplyKeyboardMarkup(
    [["Add User", "Remove User"], ["Back"]],
    resize_keyboard=True,
)

# Filter category keyboard (lists individual filters)
_FILTER_CATEGORY_KB = ReplyKeyboardMarkup(
    [
        ["Special Livery", "Rare Plane"],
        ["Rego Watchlist", "Type Watchlist"],
        ["Airline/Op Watchlist"],
        ["Route Type"],
        ["Back"],
    ],
    resize_keyboard=True,
)

_ROUTE_TYPE_FILTER_KB = ReplyKeyboardMarkup(
    [["Cooldown", "Active Days", "Arrival Window"],
     ["Min History", "Dominance", "Lookback"],
     ["Back"]],
    resize_keyboard=True,
)

# Spot recommendation sub-keyboard
_SPOT_REC_KB = ReplyKeyboardMarkup(
    [
        ["Enabled", "Day Type"],
        ["Travel Time", "Threshold"],
        ["Notify Window", "EOD Hour"],
        ["Weather Gate", "Max Spotted Times"],
        ["Lighting →", "Sessions →"],
        ["Back"],
    ],
    resize_keyboard=True,
)

_LIGHTING_KB = ReplyKeyboardMarkup(
    [
        ["Lighting Gate"],
        ["Light Buffer"],
        ["Bad Light Start", "Bad Light End"],
        ["Back"],
    ],
    resize_keyboard=True,
)

_SESSIONS_KB = ReplyKeyboardMarkup(
    [
        ["Max Gap", "Max Windows"],
        ["Notable Lull", "Max Lulls"],
        ["Back"],
    ],
    resize_keyboard=True,
)

_ON_OFF_KB = ReplyKeyboardMarkup(
    [["On", "Off"], ["Cancel"]], resize_keyboard=True,
)

_DAY_TYPE_KB = ReplyKeyboardMarkup(
    [["Any", "Weekends & Holidays"], ["Cancel"]], resize_keyboard=True,
)

# Military sub-keyboard
_MILITARY_KB = ReplyKeyboardMarkup(
    [["Check Interval", "Search Radius"], ["Max Altitude", "Re-notify Interval"], ["Back"]],
    resize_keyboard=True,
)

# Airport & Polling sub-keyboard
_AIRPORT_KB = ReplyKeyboardMarkup(
    [["Airport Code", "Check Interval"], ["Reminder Hours", "Pages"], ["Dep. Pattern Threshold"], ["Rapid Interval", "Approach Alert"], ["Force Check Now"], ["Back"]],
    resize_keyboard=True,
)


# Per-filter sub-keyboard (Special Livery, Rego/Type Watchlist)
_FILTER_KB = ReplyKeyboardMarkup(
    [["Re-notify Interval", "Active Days", "Arrival Window"], ["Back"]],
    resize_keyboard=True,
)

# Special Livery sub-keyboard — includes Exclude Keywords
_SPECIAL_LIVERY_KB = ReplyKeyboardMarkup(
    [["Re-notify Interval", "Active Days", "Arrival Window"], ["Exclude Keywords"], ["Back"]],
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
    "Airline/Operator Watchlist": {
        "cfg_interval":  "airline_interval_hours",
        "cfg_days":      "airline_days",
        "cfg_window":    "airline_time_filter",
        "db_interval":   "AIRLINE_WATCHLIST_RENOTIFY_HOURS",
        "db_days":       "AIRLINE_WATCHLIST_ACTIVE_DAYS",
        "db_window":     "AIRLINE_WATCHLIST_ARRIVAL_WINDOW",
        "interval_unit": "hours",
    },
    "Route Type": {
        "cfg_interval":   "route_type_renotify_days",
        "cfg_days":       "route_type_days",
        "cfg_window":     "route_type_time_filter",
        "db_interval":    "ROUTE_TYPE_RENOTIFY_DAYS",
        "db_days":        "ROUTE_TYPE_ACTIVE_DAYS",
        "db_window":      "ROUTE_TYPE_ARRIVAL_WINDOW",
        "interval_unit":  "days",
        "interval_label": "Cooldown",
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
    reminder = f"{cfg.reminder_hours}h" if cfg.reminder_hours > 0 else "disabled"
    lines = [
        "<b>Current Settings</b>",
        "",
        f"<b>Monitoring:</b> {cfg.airport_name} ({cfg.airport_iata}) · {cfg.check_interval // 60} min · reminder {reminder}",
        "",
        "<b>Filters</b> <i>(interval · days · window)</i>",
        f"  Special Livery: {cfg.livery_interval_hours}h · {_days_label(cfg.livery_days)} · {_window_label(cfg.livery_time_filter)}",
        f"  Rare Plane: {cfg.rare_plane_min_absence_days}d · {_days_label(cfg.rare_plane_days)} · {_window_label(cfg.rare_plane_time_filter)}",
        f"  Rego Watchlist: {cfg.rego_interval_hours}h · {_days_label(cfg.rego_days)} · {_window_label(cfg.rego_time_filter)}",
        f"  Type Watchlist: {cfg.type_interval_hours}h · {_days_label(cfg.type_days)} · {_window_label(cfg.type_time_filter)}",
        f"  Airline/Op: {cfg.airline_interval_hours}h · {_days_label(cfg.airline_days)} · {_window_label(cfg.airline_time_filter)}",
        "",
        f"<b>Military:</b> {cfg.military_check_interval // 60} min · {cfg.military_radius_nm} nm · {cfg.military_max_alt_ft} ft · renotify {cfg.military_renotify_hours}h",
        "",
        f"<b>Spot Rec:</b> {'enabled' if cfg.spot_rec_enabled else 'disabled'}",
    ]
    return "\n".join(lines)


def _spot_rec_detail(cfg) -> str:
    max_s = str(cfg.spot_rec_max_spotted_times) if cfg.spot_rec_max_spotted_times > 0 else "off"
    return (
        "<b>Spot Recommendation</b>\n\n"
        f"  Enabled: {'Yes' if cfg.spot_rec_enabled else 'No'}\n"
        f"  Day Type: {cfg.spot_rec_day_type}\n"
        f"  Travel Time: {cfg.spot_rec_travel_mins} min\n"
        f"  Threshold: {cfg.spot_rec_threshold} planes\n"
        f"  Notify Window: {cfg.spot_rec_notify_window_hours}h\n"
        f"  EOD Check: {cfg.spot_rec_eod_hour:02d}:00 local\n"
        f"  Weather Gate: {'On' if cfg.spot_rec_weather_gate else 'Off'}\n"
        f"  Lighting Gate: {'On' if cfg.spot_rec_lighting_gate else 'Off'}\n"
        f"  Max Spotted Times: {max_s}\n"
        f"  Max Gap: {cfg.spot_rec_max_gap_hours}h\n"
        f"  Notable Lull: {cfg.spot_rec_notable_lull_mins} min\n"
        f"  Max Lulls: {cfg.spot_rec_max_lulls}\n"
        f"  Max Windows: {cfg.spot_rec_max_windows}\n"
        f"  🌙 Light Buffer: {cfg.spot_rec_light_buffer_mins} min\n"
        f"  ☀️ Bad Light: {cfg.spot_rec_bad_light_start or 'off'} – {cfg.spot_rec_bad_light_end or 'off'}"
    )


def _filter_detail(cfg, category: str) -> str:
    m = _FILTER_META[category]
    interval = getattr(cfg, m["cfg_interval"])
    days     = getattr(cfg, m["cfg_days"])
    window   = getattr(cfg, m["cfg_window"])
    interval_label = m.get("interval_label", "Re-notify Interval")
    base = (
        f"<b>{category}</b>\n\n"
        f"  {interval_label}: {interval} {m['interval_unit']}\n"
        f"  Active Days: {_days_label(days)}\n"
        f"  Arrival Window: {_window_label(window)}"
    )
    if category == "Route Type":
        base += (
            f"\n  Min History: {cfg.route_type_min_days} days"
            f"\n  Dominance: {cfg.route_type_dominance_x}×"
            f"\n  Lookback: {cfg.route_type_lookback_days} days"
        )
    return base


# ------------------------------------------------------------------
# Conversation handlers
# ------------------------------------------------------------------

async def start_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = context.bot_data["cfg"]
    if not cfg.store.is_admin(str(update.effective_chat.id)):
        await update.message.reply_text("You don't have permission to change settings.")
        return ConversationHandler.END
    await update.message.reply_html(_overview(cfg), reply_markup=_CATEGORY_KB)
    return CATEGORY_SELECT


async def handle_category_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    cfg = context.bot_data["cfg"]

    if choice == "Done":
        await update.message.reply_text("Settings closed.", reply_markup=_REMOVE_KB)
        return ConversationHandler.END

    if choice == "Monitoring":
        reminder = f"{cfg.reminder_hours}h" if cfg.reminder_hours > 0 else "disabled"
        approach = f"{cfg.approach_alert_mins} min" if cfg.approach_alert_mins > 0 else "disabled"
        text = (
            f"<b>Monitoring</b>\n\n"
            f"  Airport: {cfg.airport_name} ({cfg.airport_iata}/{cfg.airport_icao})\n"
            f"  Check Interval: {cfg.check_interval // 60} min\n"
            f"  Rapid Interval: {cfg.rapid_mode_interval // 60} min\n"
            f"  Pages: {len(cfg.fetch_pages)} per check\n"
            f"  Reminder: {reminder}\n"
            f"  Approach Alert: {approach}\n"
            f"  Next Check: {_next_check_str(context, cfg)} (local)"
        )
        await update.message.reply_html(text, reply_markup=_AIRPORT_KB)
        return AIRPORT_SUBMENU

    if choice == "Filters":
        await update.message.reply_text("Choose a filter:", reply_markup=_FILTER_CATEGORY_KB)
        return FILTER_CATEGORY_SUBMENU

    if choice == "Military":
        context.user_data["settings_category"] = "Military"
        await update.message.reply_html(_military_detail(cfg), reply_markup=_MILITARY_KB)
        return MILITARY_SUBMENU

    if choice == "Spot Recommendation":
        context.user_data["settings_category"] = "Spot Recommendation"
        await update.message.reply_html(_spot_rec_detail(cfg), reply_markup=_SPOT_REC_KB)
        return SPOT_REC_SUBMENU

    if choice == "Users":
        await update.message.reply_html(_user_detail(cfg), reply_markup=_USER_KB)
        return USER_SUBMENU

    await update.message.reply_text("Please choose a category from the keyboard.")
    return CATEGORY_SELECT


async def handle_filter_category_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    cfg = context.bot_data["cfg"]

    if choice == "Back":
        await update.message.reply_html(_overview(cfg), reply_markup=_CATEGORY_KB)
        return CATEGORY_SELECT

    # Normalise "Airline/Op Watchlist" button back to full name
    if choice == "Airline/Op Watchlist":
        choice = "Airline/Operator Watchlist"

    if choice in _FILTER_META:
        context.user_data["settings_category"] = choice
        if choice == "Rare Plane":
            kb = _RARE_PLANE_FILTER_KB
        elif choice == "Route Type":
            kb = _ROUTE_TYPE_FILTER_KB
        elif choice == "Special Livery":
            kb = _SPECIAL_LIVERY_KB
        else:
            kb = _FILTER_KB
        await update.message.reply_html(_filter_detail(cfg, choice), reply_markup=kb)
        return FILTER_SUBMENU

    await update.message.reply_text("Please choose from the keyboard.")
    return FILTER_CATEGORY_SUBMENU


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

    if choice == "Reminder Hours":
        current = f"{cfg.reminder_hours}h" if cfg.reminder_hours > 0 else "disabled"
        await update.message.reply_text(
            f"Current: {current}\n\n"
            "Enter hours before arrival to send a reminder (e.g. 12), or 0 to disable",
            reply_markup=_REMOVE_KB,
        )
        return ENTER_VALUE

    if choice == "Approach Alert":
        current = f"{cfg.approach_alert_mins} min" if cfg.approach_alert_mins > 0 else "disabled"
        await update.message.reply_text(
            f"Current: {current}\n\n"
            "Minutes before landing to send an approach alert (Rapid Mode only). "
            "Enter 0 to disable, or a number between 1 and 120.",
            reply_markup=_REMOVE_KB,
        )
        return ENTER_VALUE

    if choice == "Rapid Interval":
        await update.message.reply_text(
            f"Current: {cfg.rapid_mode_interval // 60} min\n\n"
            "Check interval (minutes) to use during Rapid Mode (1–10).",
            reply_markup=_REMOVE_KB,
        )
        return ENTER_VALUE

    if choice == "Pages":
        await update.message.reply_text(
            f"Current: {len(cfg.fetch_pages)} pages\n\n"
            "Number of pages fetched per check (each page = 100 flights). "
            "Applied to both arrivals and recent departures (e.g. 2 = pages 1,2 and -1,-2).",
            reply_markup=_REMOVE_KB,
        )
        return ENTER_VALUE

    if choice == "Dep. Pattern Threshold":
        await update.message.reply_text(
            f"Current: {cfg.departure_pattern_threshold}%\n\n"
            "Minimum confidence % to show a predicted next departure (0 to disable, e.g. 80)",
            reply_markup=_REMOVE_KB,
        )
        return ENTER_VALUE

    if choice == "Force Check Now":
        import asyncio
        from monitor import run_check as _run_check
        # Run the check immediately in the background
        asyncio.create_task(_run_check(context))
        # Reset the repeating schedule from now
        for job in context.application.job_queue.get_jobs_by_name("arrivals_check"):
            job.schedule_removal()
        context.application.job_queue.run_repeating(
            _run_check, interval=cfg.check_interval, first=cfg.check_interval,
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
        await update.message.reply_text("Choose a filter:", reply_markup=_FILTER_CATEGORY_KB)
        return FILTER_CATEGORY_SUBMENU

    _ROUTE_TYPE_EXTRA = {"Min History", "Dominance", "Lookback"}
    _STANDARD_FIELDS  = {"Re-notify Interval", "Min Absence", "Cooldown", "Active Days", "Arrival Window", "Exclude Keywords"}

    if choice not in _STANDARD_FIELDS | _ROUTE_TYPE_EXTRA:
        await update.message.reply_text("Please choose from the keyboard.")
        return FILTER_SUBMENU

    context.user_data["settings_field"] = choice
    m = _FILTER_META[category]

    # Route Type extra settings
    if choice == "Min History":
        await update.message.reply_text(
            f"Current: {cfg.route_type_min_days} days\n\n"
            "Minimum days of history before the filter can fire (prevents false positives on thin data).",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Dominance":
        await update.message.reply_text(
            f"Current: {cfg.route_type_dominance_x}×\n\n"
            "Dominant type count must be ≥ N× the next most common type. Default 3.",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Lookback":
        await update.message.reply_text(
            f"Current: {cfg.route_type_lookback_days} days\n\n"
            "How many days of history to consider when determining the established type.",
            reply_markup=_REMOVE_KB,
        )
    elif choice in {"Re-notify Interval", "Min Absence", "Cooldown"}:
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

    elif choice == "Exclude Keywords":
        current = ", ".join(cfg.livery_exclude_keywords) if cfg.livery_exclude_keywords else "(none)"
        await update.message.reply_text(
            f"Current: {current}\n\n"
            "Keywords that suppress the auto-parenthetical detection. If the text inside "
            "parentheses in an airline name contains one of these words, the flight is NOT "
            "flagged as a special livery.\n\n"
            "Enter as comma-separated keywords (e.g. operated by,wet lease), or send a dash (-) to clear.",
            reply_markup=_REMOVE_KB,
        )

    return ENTER_VALUE




def _military_detail(cfg) -> str:
    return (
        "<b>Military</b> <i>(adsb.fi — no API key needed)</i>\n\n"
        f"  Check Interval: {cfg.military_check_interval // 60} min\n"
        f"  Search Radius: {cfg.military_radius_nm} nm\n"
        f"  Max Altitude: {cfg.military_max_alt_ft} ft\n"
        f"  Re-notify: {cfg.military_renotify_hours}h"
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
        return ENTER_VALUE

    if choice == "Max Altitude":
        await update.message.reply_text(
            f"Current: {cfg.military_max_alt_ft} ft\n\n"
            "Enter maximum altitude in feet for approach detection",
            reply_markup=_REMOVE_KB,
        )
    if choice == "Re-notify Interval":
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

    # Route spot rec fields to dedicated handler
    if category == "Spot Recommendation":
        return await _handle_spot_rec_value(update, context, raw, cfg, store)

    # ----------------------------------------------------------------
    # User management
    # ----------------------------------------------------------------
    if field == "Add User":
        chat_id = raw.strip()
        cfg.store.upsert_user(chat_id, is_admin=False, language="en")
        await update.message.reply_html(
            f"User <code>{chat_id}</code> added (read-only, English by default).\n"
            f"They can use /language to change their language.\n\n"
            + _user_detail(cfg),
            reply_markup=_USER_KB,
        )
        return USER_SUBMENU

    if field == "Remove User":
        chat_id = raw.strip()
        removed = cfg.store.delete_user(chat_id)
        if removed:
            msg = f"User <code>{chat_id}</code> removed."
        else:
            msg = f"User <code>{chat_id}</code> not found (or is admin — cannot remove admin)."
        await update.message.reply_html(f"{msg}\n\n" + _user_detail(cfg), reply_markup=_USER_KB)
        return USER_SUBMENU

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
    if field == "Check Interval" and category != "Military":
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

    if field == "Reminder Hours":
        try:
            value = int(raw)
            if value < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a non-negative whole number (0 to disable).")
            return ENTER_VALUE
        cfg.reminder_hours = value
        store.save_setting("REMINDER_HOURS", str(value))
        label = f"{value}h" if value > 0 else "disabled"
        await update.message.reply_text(f"Updated: reminder {label}.", reply_markup=_AIRPORT_KB)
        return AIRPORT_SUBMENU

    if field == "Approach Alert":
        try:
            value = int(raw)
            if not 0 <= value <= 120:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter 0 (disabled) or a number between 1 and 120.")
            return ENTER_VALUE
        cfg.approach_alert_mins = value
        store.save_setting("APPROACH_ALERT_MINS", str(value))
        label = f"{value} min" if value > 0 else "disabled"
        await update.message.reply_text(f"Updated: approach alert {label}.", reply_markup=_AIRPORT_KB)
        return AIRPORT_SUBMENU

    if field == "Rapid Interval":
        try:
            value = int(raw)
            if not 1 <= value <= 10:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a number between 1 and 10.")
            return ENTER_VALUE
        cfg.rapid_mode_interval = value * 60
        store.save_setting("RAPID_MODE_INTERVAL_MINS", str(value))
        await update.message.reply_text(f"Updated: Rapid Mode interval {value} min.", reply_markup=_AIRPORT_KB)
        return AIRPORT_SUBMENU

    if field == "Pages":
        try:
            value = int(raw)
            if not 1 <= value <= 5:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a number between 1 and 5.")
            return ENTER_VALUE
        cfg.fetch_pages = list(range(1, value + 1))
        store.save_setting("FETCH_PAGES", str(value))
        await update.message.reply_text(
            f"Updated: fetching {value} page{'s' if value > 1 else ''} per check "
            f"(arrivals {list(range(1, value + 1))}, departures {[-p for p in range(1, value + 1)]}).",
            reply_markup=_AIRPORT_KB,
        )
        return AIRPORT_SUBMENU

    if field == "Dep. Pattern Threshold":
        try:
            value = int(raw)
            if not 0 <= value <= 100:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a number between 0 and 100 (0 to disable).")
            return ENTER_VALUE
        cfg.departure_pattern_threshold = value
        store.save_setting("DEPARTURE_PATTERN_THRESHOLD", str(value))
        label = f"{value}%" if value > 0 else "disabled"
        await update.message.reply_text(f"Updated: departure pattern threshold {label}.", reply_markup=_AIRPORT_KB)
        return AIRPORT_SUBMENU

    # ----------------------------------------------------------------
    # Military settings
    # ----------------------------------------------------------------
    if field == "Check Interval" and category == "Military":
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

    if field == "Re-notify Interval" and category == "Military":
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

    # ----------------------------------------------------------------
    # Filter settings
    # ----------------------------------------------------------------
    if category not in _FILTER_META:
        await update.message.reply_text("Please choose from the keyboard.")
        return CATEGORY_SELECT

    m = _FILTER_META[category]

    def _filter_kb(cat):
        if cat == "Rare Plane":
            return _RARE_PLANE_FILTER_KB
        if cat == "Route Type":
            return _ROUTE_TYPE_FILTER_KB
        return _FILTER_KB

    # Route Type extra fields
    if field == "Min History":
        try:
            value = int(raw)
            if value < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a positive number of days.")
            return ENTER_VALUE
        cfg.route_type_min_days = value
        store.save_setting("ROUTE_TYPE_MIN_DAYS", str(value))
        await update.message.reply_html(
            f"Updated.\n\n{_filter_detail(cfg, category)}", reply_markup=_ROUTE_TYPE_FILTER_KB
        )
        return FILTER_SUBMENU

    if field == "Dominance":
        try:
            value = int(raw)
            if value < 2:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a whole number of 2 or more.")
            return ENTER_VALUE
        cfg.route_type_dominance_x = value
        store.save_setting("ROUTE_TYPE_DOMINANCE_X", str(value))
        await update.message.reply_html(
            f"Updated.\n\n{_filter_detail(cfg, category)}", reply_markup=_ROUTE_TYPE_FILTER_KB
        )
        return FILTER_SUBMENU

    if field == "Lookback":
        try:
            value = int(raw)
            if value < 7:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a number of days (minimum 7).")
            return ENTER_VALUE
        cfg.route_type_lookback_days = value
        store.save_setting("ROUTE_TYPE_LOOKBACK_DAYS", str(value))
        await update.message.reply_html(
            f"Updated.\n\n{_filter_detail(cfg, category)}", reply_markup=_ROUTE_TYPE_FILTER_KB
        )
        return FILTER_SUBMENU

    if field in {"Re-notify Interval", "Min Absence", "Cooldown"}:
        try:
            value = int(raw)
            if value < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a positive whole number.")
            return ENTER_VALUE
        setattr(cfg, m["cfg_interval"], value)
        store.save_setting(m["db_interval"], str(value))
        kb = _filter_kb(category)
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
        kb = _filter_kb(category)
        await update.message.reply_html(
            f"Updated.\n\n{_filter_detail(cfg, category)}", reply_markup=kb
        )
        return FILTER_SUBMENU

    if field == "Arrival Window":
        kb = _filter_kb(category)
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

    if field == "Exclude Keywords" and category == "Special Livery":
        if raw.strip() in ("-", ""):
            keywords = []
        else:
            keywords = [k.strip().lower() for k in raw.split(",") if k.strip()]
        cfg.livery_exclude_keywords = keywords
        store.save_setting("SPECIAL_LIVERY_EXCLUDE_KEYWORDS", ",".join(keywords))
        label = ", ".join(keywords) if keywords else "(none)"
        await update.message.reply_html(
            f"Updated: exclude keywords — {label}", reply_markup=_SPECIAL_LIVERY_KB
        )
        return FILTER_SUBMENU

    # Should never reach here
    return ConversationHandler.END


# ------------------------------------------------------------------
# Spot Recommendation submenu
# ------------------------------------------------------------------

_SPOT_REC_FIELDS = {
    "Enabled", "Day Type", "Travel Time", "Threshold",
    "Notify Window", "EOD Hour", "Weather Gate", "Max Spotted Times",
}

_LIGHTING_FIELDS = {
    "Lighting Gate", "Light Buffer",
    "Bad Light Start", "Bad Light End",
}

_SESSIONS_FIELDS = {
    "Max Gap", "Max Windows", "Notable Lull", "Max Lulls",
}


async def handle_spot_rec_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    cfg = context.bot_data["cfg"]

    if choice == "Back":
        await update.message.reply_html(_overview(cfg), reply_markup=_CATEGORY_KB)
        return CATEGORY_SELECT

    if choice == "Lighting →":
        context.user_data["settings_category"] = "Spot Recommendation"
        await update.message.reply_html(_spot_rec_detail(cfg), reply_markup=_LIGHTING_KB)
        return LIGHTING_SUBMENU

    if choice == "Sessions →":
        context.user_data["settings_category"] = "Spot Recommendation"
        await update.message.reply_html(_spot_rec_detail(cfg), reply_markup=_SESSIONS_KB)
        return SESSIONS_SUBMENU

    if choice not in _SPOT_REC_FIELDS:
        await update.message.reply_text("Please choose from the keyboard.")
        return SPOT_REC_SUBMENU

    context.user_data["settings_field"] = choice
    context.user_data["settings_category"] = "Spot Recommendation"

    if choice == "Enabled":
        current = "On" if cfg.spot_rec_enabled else "Off"
        await update.message.reply_text(
            f"Current: {current}\n\nEnable or disable the spot recommendation feature?",
            reply_markup=_ON_OFF_KB,
        )
    elif choice == "Day Type":
        await update.message.reply_text(
            f"Current: {cfg.spot_rec_day_type}\n\nChoose which days to recommend spotting:",
            reply_markup=_DAY_TYPE_KB,
        )
    elif choice == "Travel Time":
        await update.message.reply_text(
            f"Current: {cfg.spot_rec_travel_mins} min\n\nEnter travel time to the airport in minutes:",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Threshold":
        await update.message.reply_text(
            f"Current: {cfg.spot_rec_threshold}\n\nMinimum interesting arrivals needed to recommend:",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Notify Window":
        await update.message.reply_text(
            f"Current: {cfg.spot_rec_notify_window_hours}h\n\n"
            "Outer bound for rolling notifications — notify when a qualifying cluster starts within this many hours (e.g. 4).",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "EOD Hour":
        await update.message.reply_text(
            f"Current: {cfg.spot_rec_eod_hour:02d}:00\n\nHour (0–23) to send the end-of-day recommendation:",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Weather Gate":
        current = "On" if cfg.spot_rec_weather_gate else "Off"
        await update.message.reply_text(
            f"Current: {current}\n\nBlock recommendations during severe weather?",
            reply_markup=_ON_OFF_KB,
        )
    elif choice == "Max Spotted Times":
        current = str(cfg.spot_rec_max_spotted_times) if cfg.spot_rec_max_spotted_times > 0 else "off"
        await update.message.reply_text(
            f"Current: {current}\n\n"
            "If a plane has been photographed at the airport this many times or more, "
            "it won't count as interesting. Enter a number (0 to disable):",
            reply_markup=_REMOVE_KB,
        )

    return ENTER_VALUE


async def handle_lighting_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    cfg = context.bot_data["cfg"]

    if choice == "Back":
        await update.message.reply_html(_spot_rec_detail(cfg), reply_markup=_SPOT_REC_KB)
        return SPOT_REC_SUBMENU

    if choice not in _LIGHTING_FIELDS:
        await update.message.reply_text("Please choose from the keyboard.")
        return LIGHTING_SUBMENU

    context.user_data["settings_field"] = choice
    context.user_data["settings_category"] = "Spot Recommendation"
    context.user_data["spot_rec_origin"] = "lighting"

    if choice == "Lighting Gate":
        current = "On" if cfg.spot_rec_lighting_gate else "Off"
        await update.message.reply_text(
            f"Current: {current}\n\nExclude flights arriving after sunset?",
            reply_markup=_ON_OFF_KB,
        )
    elif choice == "Light Buffer":
        await update.message.reply_text(
            f"Current: {cfg.spot_rec_light_buffer_mins} min\n\n"
            "🌙 Minutes around sunrise and sunset marked as poor light. "
            "Flights within this window before sunset or after sunrise are flagged but not excluded.",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Bad Light Start":
        current = cfg.spot_rec_bad_light_start or "off"
        await update.message.reply_text(
            f"Current: {current}\n\n"
            "☀️ Start of midday bad light window (HH:MM e.g. 11:00). Send - to disable.",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Bad Light End":
        current = cfg.spot_rec_bad_light_end or "off"
        await update.message.reply_text(
            f"Current: {current}\n\n"
            "☀️ End of midday bad light window (HH:MM e.g. 14:00). Send - to disable.",
            reply_markup=_REMOVE_KB,
        )

    return ENTER_VALUE


async def handle_sessions_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    cfg = context.bot_data["cfg"]

    if choice == "Back":
        await update.message.reply_html(_spot_rec_detail(cfg), reply_markup=_SPOT_REC_KB)
        return SPOT_REC_SUBMENU

    if choice not in _SESSIONS_FIELDS:
        await update.message.reply_text("Please choose from the keyboard.")
        return SESSIONS_SUBMENU

    context.user_data["settings_field"] = choice
    context.user_data["settings_category"] = "Spot Recommendation"
    context.user_data["spot_rec_origin"] = "sessions"

    if choice == "Max Gap":
        await update.message.reply_text(
            f"Current: {cfg.spot_rec_max_gap_hours}h\n\n"
            "Gap between events (hours) that splits activity into separate sessions.",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Max Windows":
        await update.message.reply_text(
            f"Current: {cfg.spot_rec_max_windows}\n\n"
            "Maximum number of session options shown in EOD and manual spot checks (max 3).",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Notable Lull":
        await update.message.reply_text(
            f"Current: {cfg.spot_rec_notable_lull_mins} min\n\n"
            "Gap within a session (minutes) worth flagging as a break time.",
            reply_markup=_REMOVE_KB,
        )
    elif choice == "Max Lulls":
        await update.message.reply_text(
            f"Current: {cfg.spot_rec_max_lulls}\n\n"
            "Maximum number of break time notices shown per session (longest gaps first).",
            reply_markup=_REMOVE_KB,
        )

    return ENTER_VALUE


async def _handle_spot_rec_value(update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str, cfg, store) -> int:
    """Handle ENTER_VALUE responses for spot rec fields."""
    field = context.user_data.get("settings_field")

    if field == "Enabled":
        if raw.lower() == "cancel":
            await update.message.reply_html(_spot_rec_detail(cfg), reply_markup=_SPOT_REC_KB)
            return SPOT_REC_SUBMENU
        val = raw.lower() in ("on", "yes", "true")
        cfg.spot_rec_enabled = val
        store.save_setting("SPOT_REC_ENABLED", "true" if val else "false")
        # Schedule or remove EOD job
        jobs = context.application.job_queue.get_jobs_by_name("eod_rec")
        if val and not jobs:
            import datetime as _dt
            from spot_recommendation import run_eod_recommendation
            eod_time = _dt.time(cfg.spot_rec_eod_hour, 0, tzinfo=pytz.timezone(cfg.airport_tz))
            context.application.job_queue.run_daily(run_eod_recommendation, time=eod_time, name="eod_rec")
        elif not val:
            for job in jobs:
                job.schedule_removal()

    elif field == "Day Type":
        if raw.lower() == "cancel":
            await update.message.reply_html(_spot_rec_detail(cfg), reply_markup=_SPOT_REC_KB)
            return SPOT_REC_SUBMENU
        val = "WeekendPublicHoliday" if "weekend" in raw.lower() or "holiday" in raw.lower() else "Any"
        cfg.spot_rec_day_type = val
        store.save_setting("SPOT_REC_DAY_TYPE", val)

    elif field == "Travel Time":
        try:
            val = int(raw)
            if val < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a non-negative number of minutes.")
            return ENTER_VALUE
        cfg.spot_rec_travel_mins = val
        store.save_setting("SPOT_REC_TRAVEL_MINS", str(val))

    elif field == "Threshold":
        try:
            val = int(raw)
            if val < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a positive whole number.")
            return ENTER_VALUE
        cfg.spot_rec_threshold = val
        store.save_setting("SPOT_REC_THRESHOLD", str(val))

    elif field == "Notify Window":
        try:
            val = int(raw)
            if val < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a positive number of hours (e.g. 4).")
            return ENTER_VALUE
        cfg.spot_rec_notify_window_hours = val
        store.save_setting("SPOT_REC_NOTIFY_WINDOW_HOURS", str(val))

    elif field == "EOD Hour":
        try:
            val = int(raw)
            if not 0 <= val <= 23:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a whole number between 0 and 23.")
            return ENTER_VALUE
        cfg.spot_rec_eod_hour = val
        store.save_setting("SPOT_REC_EOD_HOUR", str(val))
        # Reschedule EOD job
        for job in context.application.job_queue.get_jobs_by_name("eod_rec"):
            job.schedule_removal()
        if cfg.spot_rec_enabled:
            import datetime as _dt
            from spot_recommendation import run_eod_recommendation
            eod_time = _dt.time(val, 0, tzinfo=pytz.timezone(cfg.airport_tz))
            context.application.job_queue.run_daily(run_eod_recommendation, time=eod_time, name="eod_rec")

    elif field == "Weather Gate":
        if raw.lower() == "cancel":
            await update.message.reply_html(_spot_rec_detail(cfg), reply_markup=_SPOT_REC_KB)
            return SPOT_REC_SUBMENU
        val = raw.lower() == "on"
        cfg.spot_rec_weather_gate = val
        store.save_setting("SPOT_REC_WEATHER_GATE", "true" if val else "false")

    elif field == "Lighting Gate":
        if raw.lower() == "cancel":
            await update.message.reply_html(_spot_rec_detail(cfg), reply_markup=_LIGHTING_KB)
            return LIGHTING_SUBMENU
        val = raw.lower() == "on"
        cfg.spot_rec_lighting_gate = val
        store.save_setting("SPOT_REC_LIGHTING_GATE", "true" if val else "false")

    elif field == "Max Spotted Times":
        try:
            val = int(raw)
            if val < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter 0 (disabled) or a positive number.")
            return ENTER_VALUE
        cfg.spot_rec_max_spotted_times = val
        store.save_setting("SPOT_REC_MAX_SPOTTED_TIMES", str(val))

    elif field == "Max Gap":
        try:
            val = int(raw)
            if val < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a positive number of hours.")
            return ENTER_VALUE
        cfg.spot_rec_max_gap_hours = val
        store.save_setting("SPOT_REC_MAX_GAP_HOURS", str(val))

    elif field == "Notable Lull":
        try:
            val = int(raw)
            if val < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a positive number of minutes.")
            return ENTER_VALUE
        cfg.spot_rec_notable_lull_mins = val
        store.save_setting("SPOT_REC_NOTABLE_LULL_MINS", str(val))

    elif field == "Max Lulls":
        try:
            val = int(raw)
            if val < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter 0 or a positive number.")
            return ENTER_VALUE
        cfg.spot_rec_max_lulls = val
        store.save_setting("SPOT_REC_MAX_LULLS", str(val))

    elif field == "Max Windows":
        try:
            val = int(raw)
            if not 1 <= val <= 3:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a number between 1 and 3.")
            return ENTER_VALUE
        cfg.spot_rec_max_windows = val
        store.save_setting("SPOT_REC_MAX_WINDOWS", str(val))

    elif field == "Light Buffer":
        try:
            val = int(raw)
            if val < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Please enter a non-negative number of minutes.")
            return ENTER_VALUE
        cfg.spot_rec_light_buffer_mins = val
        store.save_setting("SPOT_REC_LIGHT_BUFFER_MINS", str(val))

    elif field == "Bad Light Start":
        import re as _re
        if raw.strip() in ("-", "off", ""):
            cfg.spot_rec_bad_light_start = ""
            store.save_setting("SPOT_REC_BAD_LIGHT_START", "")
        elif _re.match(r"^\d{2}:\d{2}$", raw.strip()):
            cfg.spot_rec_bad_light_start = raw.strip()
            store.save_setting("SPOT_REC_BAD_LIGHT_START", raw.strip())
        else:
            await update.message.reply_text("Please enter a time in HH:MM format (e.g. 11:00), or - to disable.")
            return ENTER_VALUE

    elif field == "Bad Light End":
        import re as _re
        if raw.strip() in ("-", "off", ""):
            cfg.spot_rec_bad_light_end = ""
            store.save_setting("SPOT_REC_BAD_LIGHT_END", "")
        elif _re.match(r"^\d{2}:\d{2}$", raw.strip()):
            cfg.spot_rec_bad_light_end = raw.strip()
            store.save_setting("SPOT_REC_BAD_LIGHT_END", raw.strip())
        else:
            await update.message.reply_text("Please enter a time in HH:MM format (e.g. 14:00), or - to disable.")
            return ENTER_VALUE

    origin = context.user_data.get("spot_rec_origin")
    if origin == "lighting":
        await update.message.reply_html(f"Updated.\n\n{_spot_rec_detail(cfg)}", reply_markup=_LIGHTING_KB)
        return LIGHTING_SUBMENU
    elif origin == "sessions":
        await update.message.reply_html(f"Updated.\n\n{_spot_rec_detail(cfg)}", reply_markup=_SESSIONS_KB)
        return SESSIONS_SUBMENU
    else:
        await update.message.reply_html(f"Updated.\n\n{_spot_rec_detail(cfg)}", reply_markup=_SPOT_REC_KB)
        return SPOT_REC_SUBMENU


def _user_detail(cfg) -> str:
    users = cfg.store.get_all_users()
    lines = ["<b>Users</b>\n"]
    for u in users:
        role = "Admin" if u["is_admin"] else "Read-only"
        lang = "English" if u["language"] == "en" else "中文"
        lines.append(f"  {u['chat_id']} — {role} — {lang}")
    if not users:
        lines.append("  No users configured.")
    return "\n".join(lines)


async def handle_user_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    cfg = context.bot_data["cfg"]

    if choice == "Back":
        await update.message.reply_html(_overview(cfg), reply_markup=_CATEGORY_KB)
        return CATEGORY_SELECT

    if choice == "Add User":
        context.user_data["settings_field"] = "Add User"
        await update.message.reply_text(
            "Enter the chat ID of the user to add:\n(They can get their chat ID by messaging @userinfobot)",
            reply_markup=_REMOVE_KB,
        )
        return ENTER_VALUE

    if choice == "Remove User":
        context.user_data["settings_field"] = "Remove User"
        await update.message.reply_text(
            "Enter the chat ID of the user to remove:",
            reply_markup=_REMOVE_KB,
        )
        return ENTER_VALUE

    await update.message.reply_html(_user_detail(cfg), reply_markup=_USER_KB)
    return USER_SUBMENU


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
            FILTER_CATEGORY_SUBMENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_filter_category_submenu)
            ],
            SPOT_REC_SUBMENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_spot_rec_submenu)
            ],
            USER_SUBMENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_submenu)
            ],
            LIGHTING_SUBMENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lighting_submenu)
            ],
            SESSIONS_SUBMENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sessions_submenu)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_settings)],
        allow_reentry=True,
    )
    app.add_handler(conversation)
