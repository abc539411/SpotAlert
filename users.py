from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

log = logging.getLogger(__name__)


async def handle_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not cfg.store.is_admin(str(update.effective_chat.id)):
        await update.message.reply_text("You don't have permission to change settings.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("/adduser <chat_id>")
        return
    chat_id = args[0].strip()
    cfg.store.upsert_user(chat_id, is_admin=False)
    await update.message.reply_text(f"User {chat_id} added.")
    log.info("Added user: %s", chat_id)


async def handle_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not cfg.store.is_admin(str(update.effective_chat.id)):
        await update.message.reply_text("You don't have permission to change settings.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("/removeuser <chat_id>")
        return
    chat_id = args[0].strip()
    removed = cfg.store.delete_user(chat_id)
    if removed:
        await update.message.reply_text(f"User {chat_id} removed.")
        log.info("Removed user: %s", chat_id)
    else:
        await update.message.reply_text(f"User {chat_id} not found (or is admin).")


def register_user_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("adduser",    handle_adduser))
    app.add_handler(CommandHandler("removeuser", handle_removeuser))
