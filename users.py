from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from translations import SUPPORTED_LANGUAGES, t

log = logging.getLogger(__name__)


async def handle_adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    lang = cfg.language_for(update.effective_chat.id)

    if not cfg.store.is_admin(str(update.effective_chat.id)):
        await update.message.reply_text(t("permission_denied", lang))
        return

    args = context.args
    if not args:
        await update.message.reply_text("/adduser <chat_id>")
        return

    chat_id = args[0].strip()
    cfg.store.upsert_user(chat_id, is_admin=False, language="en")
    await update.message.reply_text(t("user_added", lang, chat_id=chat_id))
    log.info("Added user: %s", chat_id)


async def handle_removeuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    lang = cfg.language_for(update.effective_chat.id)

    if not cfg.store.is_admin(str(update.effective_chat.id)):
        await update.message.reply_text(t("permission_denied", lang))
        return

    args = context.args
    if not args:
        await update.message.reply_text("/removeuser <chat_id>")
        return

    chat_id = args[0].strip()
    removed = cfg.store.delete_user(chat_id)
    if removed:
        await update.message.reply_text(t("user_removed", lang, chat_id=chat_id))
        log.info("Removed user: %s", chat_id)
    else:
        await update.message.reply_text(t("user_not_found", lang, chat_id=chat_id))


async def handle_language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    lang = cfg.language_for(update.effective_chat.id)

    if not cfg.store.is_known_user(str(update.effective_chat.id)):
        await update.message.reply_text(t("permission_denied", lang))
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("English",  callback_data="lang_en"),
        InlineKeyboardButton("中文",     callback_data="lang_zh"),
    ]])
    await update.message.reply_text(t("language_prompt", lang), reply_markup=keyboard)


async def handle_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    cfg = context.bot_data["cfg"]
    chat_id = str(query.message.chat_id)

    if not cfg.store.is_known_user(chat_id):
        await query.edit_message_text(t("permission_denied", "en"))
        return

    new_lang = query.data.replace("lang_", "")
    cfg.store.set_user_language(chat_id, new_lang)
    await query.edit_message_reply_markup(reply_markup=None)

    key = "language_set_zh" if new_lang == "zh" else "language_set_en"
    await query.message.reply_text(t(key, new_lang))
    log.info("User %s set language to %s", chat_id, new_lang)


def register_user_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("adduser",    handle_adduser))
    app.add_handler(CommandHandler("removeuser", handle_removeuser))
    app.add_handler(CommandHandler("language",   handle_language_command))
    app.add_handler(CallbackQueryHandler(handle_language_callback, pattern="^lang_(en|zh)$"))
