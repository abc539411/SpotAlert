from __future__ import annotations

import logging
import platform
import re
from datetime import datetime, timezone
from typing import Optional, Tuple

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from storage import SqliteStore

log = logging.getLogger(__name__)

# Conversation state IDs
FILTER_CHOICE, OP_CHOICE, ADD_ENTRY, ADD_ENTRY_FROM_NOTIFICATION, DELETE_ENTRY, SELECT_AIRLINE_TYPE = range(6)

_FILTER_KEYBOARD = ReplyKeyboardMarkup(
    [["Exclusion List", "Rego Watchlist"], ["Type Watchlist", "Airline/Operator Watchlist"]],
    resize_keyboard=True, one_time_keyboard=True,
)

_AIRLINE_TYPE_KB = ReplyKeyboardMarkup(
    [["Airline", "Operator"], ["Cancel"]],
    resize_keyboard=True,
)
_OP_KEYBOARD = ReplyKeyboardMarkup(
    [["Add Entry", "Delete Entry", "Exit"]],
    resize_keyboard=True, one_time_keyboard=True,
)
_OP_KEYBOARD_EMPTY = ReplyKeyboardMarkup(
    [["Add Entry", "Exit"]],
    resize_keyboard=True, one_time_keyboard=True,
)
_REMOVE_KEYBOARD = ReplyKeyboardRemove()

_VALID_FILTER_NAMES = {"Exclusion List", "Rego Watchlist", "Type Watchlist", "Airline/Operator Watchlist"}


def _get_store(context: ContextTypes.DEFAULT_TYPE) -> SqliteStore:
    return context.bot_data["cfg"].store


def _render_list_html(title: str, columns: list, rows: list, show_index: bool) -> Tuple[str, int]:
    if not rows:
        return f"<b>{title} is empty.</b>", 0
    parts = [f"<b>{title}</b>"]
    for idx, row in enumerate(rows):
        parts.append(f"\n{'Index: ' + str(idx) if show_index else ''}")
        for col in columns:
            parts.append(f"{col}: {row.get(col, '')}")
    return "\n".join(parts), len(rows)


def _lookup_airline_icao(context: ContextTypes.DEFAULT_TYPE, registration: str) -> str:
    """Return airline ICAO code from FR24 rego details, or empty string if unavailable."""
    try:
        cfg = context.bot_data["cfg"]
        details = cfg.fr_api.get_rego_details(registration)
        data = (details or {}).get("data") or []
        if data:
            airline = (data[0].get("airline") or {})
            return (airline.get("code") or {}).get("icao") or ""
    except Exception as exc:
        log.warning("Airline lookup failed for %s: %s", registration, exc)
    return ""


def _parse_fields_from_notification(text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract registration, airline ICAO, and aircraft type from a pasted notification message."""
    rego_match    = re.search(r"Registration:\s*(\S+)", text)
    airline_match = re.search(r"Airline:.*?\((\w+)/\w+\)", text)
    type_match    = re.search(r"Aircraft:.*?\((\w+)\)", text)
    return (
        rego_match.group(1)    if rego_match    else None,
        airline_match.group(1) if airline_match else None,
        type_match.group(1)    if type_match    else None,
    )


# ------------------------------------------------------------------
# Conversation handlers
# ------------------------------------------------------------------

async def start_filter_management(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Choose the filter to modify:", reply_markup=_FILTER_KEYBOARD
    )
    return FILTER_CHOICE


async def handle_filter_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    selected = update.message.text
    if selected not in _VALID_FILTER_NAMES:
        await update.message.reply_text("Please select a filter from the keyboard.")
        return FILTER_CHOICE

    context.user_data["selected_filter"] = selected
    view = _get_store(context).get_list_view(selected)
    text, count = _render_list_html(selected, view.columns, view.rows, show_index=True)
    await update.message.reply_html(text)

    keyboard = _OP_KEYBOARD_EMPTY if count == 0 else _OP_KEYBOARD
    await update.message.reply_text("What would you like to do?", reply_markup=keyboard)
    return OP_CHOICE


async def prompt_add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    selected = context.user_data.get("selected_filter")
    if selected == "Type Watchlist":
        prompt = (
            "Enter airline ICAO and aircraft type separated by a comma (e.g. QFA,B744).\n"
            "Or paste a notification message to auto-fill."
        )
    elif selected == "Rego Watchlist":
        prompt = (
            "Enter registration (e.g. VH-OEJ) — airline will be looked up automatically.\n"
            "Or paste a notification message to auto-fill."
        )
    elif selected == "Airline/Operator Watchlist":
        prompt = "Enter the ICAO code, optionally followed by a name (e.g. QFA,Qantas)."
    else:  # Exclusion List
        prompt = (
            "Enter registration (e.g. VH-OEJ).\n"
            "Or paste a notification message to auto-fill."
        )
    await update.message.reply_text(prompt, reply_markup=_REMOVE_KEYBOARD)
    return ADD_ENTRY


async def receive_add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    selected = context.user_data.get("selected_filter")
    store = _get_store(context)

    # Multi-line input → user pasted a notification message
    if "\n" in text:
        registration, airline, aircraft_type = _parse_fields_from_notification(text)
        if registration and airline:
            context.user_data["parsed_entry"] = {
                "registration": registration,
                "airline": airline,
                "aircraft_type": aircraft_type or "",
            }
            await update.message.reply_text(
                f"Detected — Rego: {registration} | Airline: {airline} | Type: {aircraft_type or 'N/A'}\n"
                "Enter a description (or type - to skip):"
            )
            return ADD_ENTRY_FROM_NOTIFICATION
        await update.message.reply_text("Could not parse the notification. Please enter manually.")
        return ADD_ENTRY

    # Airline/Operator Watchlist — single-line, then ask airline vs operator
    if selected == "Airline/Operator Watchlist":
        fields = [f.strip() for f in text.split(",")]
        icao_code = fields[0].upper()
        name = fields[1] if len(fields) >= 2 else ""
        if not icao_code:
            await update.message.reply_text("Please enter an ICAO code.")
            return ADD_ENTRY
        context.user_data["parsed_entry"] = {"icao_code": icao_code, "name": name}
        await update.message.reply_text(
            f"Adding {icao_code}{f' ({name})' if name else ''}\n\nIs this an Airline or Operator?",
            reply_markup=_AIRLINE_TYPE_KB,
        )
        return SELECT_AIRLINE_TYPE

    # Single-line manual entry
    registration = text.split(",")[0].strip().upper()
    if not registration:
        await update.message.reply_text("Please enter a registration.")
        return ADD_ENTRY

    if selected == "Type Watchlist":
        fields = [f.strip() for f in text.split(",")]
        if len(fields) == 2:
            store.add_type_watch(fields[0], fields[1])
            return await _complete_with_updated_list(update, context, selected)
        await update.message.reply_text("Invalid format. Expected: AIRLINE,AIRCRAFT_TYPE")
        return ADD_ENTRY

    if selected == "Rego Watchlist":
        airline_icao = _lookup_airline_icao(context, registration)
        store.add_rego_watch(airline_icao, registration, "")
        label = f" (airline: {airline_icao})" if airline_icao else ""
        await update.message.reply_text(f"Added {registration}{label}.")
        return await _complete_with_updated_list(update, context, selected)

    # Exclusion List — ask for description as a follow-up step
    context.user_data["parsed_entry"] = {"registration": registration, "airline": "", "aircraft_type": ""}
    await update.message.reply_text(
        f"Registration: {registration}\nEnter a description (or - to skip):",
        reply_markup=_REMOVE_KEYBOARD,
    )
    return ADD_ENTRY_FROM_NOTIFICATION


async def receive_add_entry_from_notification(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    description = update.message.text.strip()
    if description == "-":
        description = ""

    selected = context.user_data.get("selected_filter")
    parsed = context.user_data.get("parsed_entry", {})
    store = _get_store(context)

    registration  = parsed.get("registration", "")
    airline       = parsed.get("airline", "")
    aircraft_type = parsed.get("aircraft_type", "")

    if selected == "Exclusion List":
        store.add_exclusion(airline, registration, description)
    elif selected == "Rego Watchlist":
        store.add_rego_watch(airline, registration, description)
    elif selected == "Type Watchlist":
        store.add_type_watch(airline, aircraft_type)

    return await _complete_with_updated_list(update, context, selected)


async def receive_airline_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip()
    if choice == "Cancel":
        await update.message.reply_text("Cancelled.", reply_markup=_REMOVE_KEYBOARD)
        return ConversationHandler.END

    if choice not in ("Airline", "Operator"):
        await update.message.reply_text("Please choose Airline or Operator.", reply_markup=_AIRLINE_TYPE_KB)
        return SELECT_AIRLINE_TYPE

    parsed = context.user_data.get("parsed_entry", {})
    icao_code = parsed.get("icao_code", "")
    name      = parsed.get("name", "")
    entry_type = choice.lower()

    _get_store(context).add_airline_watch(icao_code, entry_type, name)
    return await _complete_with_updated_list(update, context, "Airline/Operator Watchlist")


async def prompt_delete_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Enter the index(es) to delete, separated by commas (e.g. 0,2,4):",
        reply_markup=_REMOVE_KEYBOARD,
    )
    return DELETE_ENTRY


async def receive_delete_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    selected = context.user_data.get("selected_filter")
    store = _get_store(context)

    try:
        indexes = [int(x.strip()) for x in update.message.text.split(",")]
    except ValueError:
        await update.message.reply_text("Invalid input. Please enter numeric index(es).")
        return DELETE_ENTRY

    try:
        view_before = store.get_list_view(selected)
        deleted_rows = [view_before.rows[i] for i in indexes]
        deleted_text, _ = _render_list_html("Deleted", view_before.columns, deleted_rows, show_index=False)
        await update.message.reply_html(deleted_text)

        view_after = store.delete_entries_by_index(selected, indexes)
        updated_text, _ = _render_list_html(
            f"Updated {selected}", view_after.columns, view_after.rows, show_index=True
        )
        await update.message.reply_html(updated_text)
        await update.message.reply_text("Done!", reply_markup=_REMOVE_KEYBOARD)
    except IndexError:
        await update.message.reply_text("Index out of range. Please try again.")
        return DELETE_ENTRY

    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.", reply_markup=_REMOVE_KEYBOARD)
    return ConversationHandler.END


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

async def _complete_with_updated_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE, filter_name: str
) -> int:
    view = _get_store(context).get_list_view(filter_name)
    text, _ = _render_list_html(f"Updated {filter_name}", view.columns, view.rows, show_index=True)
    await update.message.reply_html(text)
    await update.message.reply_text("Done!", reply_markup=_REMOVE_KEYBOARD)
    return ConversationHandler.END


# ------------------------------------------------------------------
# Status command
# ------------------------------------------------------------------

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]

    # Uptime
    start_time = context.bot_data.get("start_time")
    if start_time:
        delta = datetime.now(timezone.utc) - start_time
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m = rem // 60
        uptime = f"{h}h {m}m"
    else:
        uptime = "N/A"

    # Next job times
    def next_job(name: str) -> str:
        jobs = context.application.job_queue.get_jobs_by_name(name)
        if jobs and jobs[0].next_t:
            import pytz
            tz = pytz.timezone(cfg.airport_tz)
            return jobs[0].next_t.astimezone(tz).strftime("%H:%M")
        return "N/A"

    lines = [
        "<b>Status</b>",
        "",
        "<b>Host</b>",
        f"  Hostname:   {platform.node()}",
        f"  OS:         {platform.system()} {platform.release()}",
        f"  Arch:       {platform.machine()}",
        f"  Python:     {platform.python_version()}",
        "",
        "<b>Bot</b>",
        f"  Uptime:         {uptime}",
        f"  Monitoring:     {cfg.airport_name} ({cfg.airport_iata})",
        f"  Next Arrivals:  {next_job('arrivals_check')} (local)",
        f"  Next Military:  {next_job('military_check')} (local)",
    ]
    await update.message.reply_html("\n".join(lines))


# ------------------------------------------------------------------
# Handler registration
# ------------------------------------------------------------------

def register_handlers(app: Application) -> None:
    conversation = ConversationHandler(
        entry_points=[CommandHandler("filters", start_filter_management)],
        states={
            FILTER_CHOICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_filter_selection)
            ],
            OP_CHOICE: [
                MessageHandler(filters.Regex("^Add Entry$"),    prompt_add_entry),
                MessageHandler(filters.Regex("^Delete Entry$"), prompt_delete_entry),
                MessageHandler(filters.Regex("^Exit$"),         cancel_conversation),
            ],
            ADD_ENTRY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_add_entry)
            ],
            ADD_ENTRY_FROM_NOTIFICATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_add_entry_from_notification)
            ],
            DELETE_ENTRY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_delete_entry)
            ],
            SELECT_AIRLINE_TYPE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_airline_type)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True,
    )
    app.add_handler(conversation)
    app.add_handler(CommandHandler("status", status))
