from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from translations import tr_airline, tr_aircraft

log = logging.getLogger(__name__)


async def handle_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data["cfg"]
    lang = cfg.language_for(update.effective_chat.id)
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
                apt_str = " · ".join(f"{apt} ({cnt})" for apt, cnt in s["top_airports"])
                lines.append(f"  Top airports: {apt_str}")

            if s["top_photographed"]:
                lines.append("")
                lines.append("  Most photographed:")
                for reg, count in s["top_photographed"]:
                    airline, ac_type = cfg.catalog.get_aircraft_info(reg)
                    al = tr_airline(airline, lang) if airline else ""
                    ac = tr_aircraft(ac_type, lang) if ac_type else ""
                    detail = f" — {al} ({ac})" if al and ac else f" — {al or ac}"
                    lines.append(f"    {reg}{detail} · {count} session{'s' if count != 1 else ''}")

            if s["multi_airport"]:
                lines.append("")
                lines.append("  Spotted at multiple airports:")
                for reg, apt_count, airports in s["multi_airport"]:
                    airline, ac_type = cfg.catalog.get_aircraft_info(reg)
                    al = tr_airline(airline, lang) if airline else ""
                    ac = tr_aircraft(ac_type, lang) if ac_type else ""
                    detail = f" — {al} ({ac})" if al and ac else f" — {al or ac}"
                    apt_list = ", ".join(a.strip() for a in airports.split(","))
                    lines.append(f"    {reg}{detail} · {apt_list} ({apt_count} airports)")
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

    await update.message.reply_html("\n".join(lines))


def register_stats_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("stats", handle_stats))
