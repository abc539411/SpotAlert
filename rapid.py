from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from monitor import run_check
from military import check_military

log = logging.getLogger(__name__)

_TIMEOUT_SECS = 7200  # 2 hours


def _reschedule(context, name: str, callback, interval: int, data=None) -> None:
    for job in context.application.job_queue.get_jobs_by_name(name):
        job.schedule_removal()
    kwargs = dict(interval=interval, first=0, name=name)
    if data is not None:
        kwargs["data"] = data
    context.application.job_queue.run_repeating(callback, **kwargs)


async def _deactivate(context) -> None:
    cfg = context.bot_data["cfg"]
    cfg.rapid_mode = False
    cfg.store.reset_rapid_alerts()

    fetch_pages_count = int(cfg.store.load_setting("FETCH_PAGES") or "2")
    cfg.fetch_pages = list(range(1, fetch_pages_count + 1))

    _reschedule(context, "arrivals_check", run_check, cfg.check_interval, data=cfg.chat_id)
    _reschedule(context, "military_check", check_military, cfg.military_check_interval)

    for job in context.application.job_queue.get_jobs_by_name("rapid_timeout"):
        job.schedule_removal()


async def handle_rapid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not cfg.store.is_admin(str(update.effective_chat.id)):
        await update.message.reply_text("Admin only.")
        return

    cfg.rapid_mode = not cfg.rapid_mode

    if cfg.rapid_mode:
        cfg.fetch_pages = [1]
        interval = cfg.rapid_mode_interval
        _reschedule(context, "arrivals_check", run_check, interval, data=cfg.chat_id)
        _reschedule(context, "military_check", check_military, interval)

        context.application.job_queue.run_once(
            _rapid_timeout_job, when=_TIMEOUT_SECS, name="rapid_timeout"
        )

        mins = interval // 60
        await update.message.reply_text(
            f"🔴 Rapid Mode ON — checking every {mins} min.\n"
            f"Send /rapid again to stop."
        )
        log.info("Rapid Mode activated — interval %ds", interval)
    else:
        await _deactivate(context)
        mins = cfg.check_interval // 60
        await update.message.reply_text(f"✅ Rapid Mode OFF — back to normal ({mins} min interval).")
        log.info("Rapid Mode deactivated manually")


async def _rapid_timeout_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not cfg.rapid_mode:
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes — extend 2h", callback_data="rapid_extend"),
        InlineKeyboardButton("No — stop",       callback_data="rapid_stop"),
    ]])

    for chat_id in cfg.all_chat_ids:
        if cfg.store.is_admin(str(chat_id)):
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⏰ You've been in Rapid Mode for 2 hours.\nStill at the airport?",
                    reply_markup=keyboard,
                )
            except Exception as exc:
                log.warning("Failed to send rapid timeout prompt to %s: %s", chat_id, exc)


async def handle_rapid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    cfg = context.bot_data["cfg"]

    if query.data == "rapid_extend":
        for job in context.application.job_queue.get_jobs_by_name("rapid_timeout"):
            job.schedule_removal()
        context.application.job_queue.run_once(
            _rapid_timeout_job, when=_TIMEOUT_SECS, name="rapid_timeout"
        )
        await query.edit_message_text("✅ Extended — Rapid Mode continues for another 2 hours.")
        log.info("Rapid Mode extended by 2 hours")

    elif query.data == "rapid_stop":
        await _deactivate(context)
        mins = cfg.check_interval // 60
        await query.edit_message_text(f"✅ Rapid Mode OFF — back to normal ({mins} min interval).")
        log.info("Rapid Mode deactivated via timeout prompt")


def register_rapid_handler(app: Application) -> None:
    app.add_handler(CommandHandler("rapid", handle_rapid))
    app.add_handler(CallbackQueryHandler(handle_rapid_callback, pattern=r"^rapid_(extend|stop)$"))
