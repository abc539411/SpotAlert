from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from monitor import _registration_flag, _iata_flag_with_api

log = logging.getLogger(__name__)


async def handle_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    if not cfg.store.is_admin(str(update.effective_chat.id)):
        await update.message.reply_text("You don't have permission to view stats.")
        return
    lines = ["<b>SpotAlert Stats</b>", ""]

    # ----------------------------------------------------------------
    # My Photos (Lightroom catalog)
    # ----------------------------------------------------------------
    if cfg.catalog:
        await update.message.reply_text("Crunching numbers...")
        s = cfg.catalog.get_catalog_stats()
        if s:
            lines.append("<b>My Photos</b>")
            lines.append(
                f"  {s['unique_aircraft']} unique aircraft · {s['total_sessions']} spotting sessions"
            )

            if s["top_airports"]:
                apt_parts = []
                for apt, cnt in s["top_airports"]:
                    flag = _iata_flag_with_api(apt, cfg.fr_api)
                    apt_parts.append(f"{flag} {apt} ({cnt})" if flag else f"{apt} ({cnt})")
                lines.append(f"  Top airports: {' · '.join(apt_parts)}")

            if s["top_photographed"]:
                lines.append("")
                lines.append("  Most photographed:")
                for reg, count in s["top_photographed"]:
                    airline, ac_type = cfg.catalog.get_aircraft_info(reg)
                    detail = f" — {airline} ({ac_type})" if airline and ac_type else f" — {airline or ac_type}"
                    flag = _registration_flag(reg)
                    url = f"https://www.flightradar24.com/data/aircraft/{reg.lower()}"
                    reg_str = f'<a href="{url}">{reg}</a>{" " + flag if flag else ""}'
                    lines.append(f"    {reg_str}{detail} · {count} session{'s' if count != 1 else ''}")

            if s["multi_airport"]:
                lines.append("")
                lines.append("  Spotted at multiple airports:")
                for reg, apt_count, airports in s["multi_airport"]:
                    airline, ac_type = cfg.catalog.get_aircraft_info(reg)
                    detail = f" — {airline} ({ac_type})" if airline and ac_type else f" — {airline or ac_type}"
                    flag = _registration_flag(reg)
                    url = f"https://www.flightradar24.com/data/aircraft/{reg.lower()}"
                    reg_str = f'<a href="{url}">{reg}</a>{" " + flag if flag else ""}'
                    apt_list = ", ".join(
                        f"{f} {a.strip()}" if (f := _iata_flag_with_api(a.strip(), cfg.fr_api)) else a.strip()
                        for a in airports.split(",")
                    )
                    lines.append(f"    {reg_str}{detail} · {apt_list} ({apt_count} airports)")
        else:
            lines.append("<b>My Photos</b>")
            lines.append("  No catalog data available.")
    else:
        lines.append("<b>My Photos</b>")
        lines.append("  No Lightroom catalog loaded.")

    lines.append("")

    # ----------------------------------------------------------------
    # App Notifications
    # ----------------------------------------------------------------
    n = cfg.store.get_notification_stats()
    lines.append("<b>App Notifications</b>")
    lines.append(f"  Special liveries: {n['special_liveries']}")
    lines.append(f"  Military sightings: {n['military']}")
    watchlist_total = n["rego_hits"] + n["type_hits"] + n["airline_hits"]
    lines.append(
        f"  Watchlist hits: {watchlist_total} "
        f"({n['rego_hits']} rego · {n['type_hits']} type · {n['airline_hits']} airline)"
    )

    await update.message.reply_html("\n".join(lines), disable_web_page_preview=True)


def register_stats_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("stats", handle_stats))
