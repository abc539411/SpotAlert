# spmonitor

A Telegram bot that monitors FlightRadar24 arrivals at a chosen airport and sends notifications when interesting aircraft are detected. Designed for aircraft spotters who want advance notice of special liveries, rare airline/type combinations, watchlisted registrations, and military traffic.

---

## Features

### Arrival Filters
- **Special Livery** — detects aircraft whose airline name contains configurable keywords (e.g. "Livery", "Sticker") and notifies with the livery name extracted from the airline field
- **Rare Plane/Airline** — notifies when an airline + aircraft type combination hasn't been seen at the airport for a configurable number of days; frequent arrivals refresh the clock so only genuinely absent combinations trigger
- **Rego Watchlist** — notifies when a specific registration on your watchlist is inbound
- **Type Watchlist** — notifies when a specific airline + aircraft type combination on your watchlist is inbound

Filter priority: Special Livery → Rego Watchlist → Type Watchlist → Rare Plane. A flight only ever triggers one notification.

### Follow-up Notifications
- **12-hour reminder** — if you were notified more than 12 hours before arrival, a reminder is sent when the flight is within 12 hours
- **Cancellation/diversion alert** — if a notified flight disappears from the arrivals board, you are alerted

### Military Traffic
- Monitors nearby military aircraft via the [adsb.fi](https://opendata.adsb.fi) open data API (no API key required)
- Notifies when a military aircraft is on approach — within a configurable radius and below a configurable altitude threshold
- Runs on its own check interval, independent of the main arrivals check

### Telegram Bot Commands
| Command | Description |
|---|---|
| `/filters` | Manage watchlists and exclusion list |
| `/settings` | Configure all app settings at runtime |
| `/summary` | View a summary of notified flights for today or tomorrow, filtered by morning or afternoon |
| `/status` | Show host system info and next scheduled check times |

### Runtime Configuration
All settings are adjustable via `/settings` in Telegram without touching any files — airport, check intervals, filter thresholds, arrival windows, active days, and summary period definitions. Changes persist across restarts.

---

## Requirements

- Python 3.10+
- A Telegram bot token ([create one via @BotFather](https://t.me/BotFather))
- Your Telegram chat ID

```
pip install -r requirements.txt
```

---

## Setup

1. **Clone the repo**
   ```bash
   git clone https://github.com/abc539411/spmonitor.git
   cd spmonitor
   ```

2. **Create your config file**
   ```bash
   cp config/config.env.example config/config.env
   ```
   Edit `config/config.env` and fill in your `TELEGRAM_BOT_TOKEN`, `CHAT_ID`, and `AIRPORT_CODE`.

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run**
   ```bash
   python main.py
   ```

---

## Configuration

All settings live in `config/config.env`. See [`config/config.env.example`](config/config.env.example) for a fully commented template.

Key settings:

| Setting | Description |
|---|---|
| `AIRPORT_CODE` | IATA or ICAO code of the airport to monitor |
| `CHECK_INTERVAL_MINUTES` | How often to poll FR24 for arrivals |
| `SPECIAL_LIVERY_KEYWORDS` | Comma-separated keywords to match against airline name |
| `RARE_PLANE_MIN_ABSENCE_DAYS` | Days a combo must be absent before being considered rare |
| `MILITARY_CHECK_INTERVAL_MINUTES` | How often to check for military traffic |
| `MILITARY_RADIUS_NM` | Search radius around the airport (nautical miles, max 250) |

All settings can also be changed at runtime via `/settings` in Telegram.

---

## Filters: Active Days & Arrival Window

Each filter supports two optional constraints:

- **Active Days** — limit notifications to specific days of the week (e.g. `Sat,Sun`)
- **Arrival Window** — `Always` (default), `Daylight Only` (between dawn and dusk at the airport), or `Off` (disabled entirely)

---

## Data Persistence

A SQLite database is created at `config/filters/spmonitor.db` on first run. It stores:
- Notification history (throttle tracking)
- Rego and type watchlists
- Exclusion list
- Follow-up tracking (reminders, cancellations)
- Any settings changed via the bot

---

## Attribution

The `flightradar24api/` module is a modified version of the [FlightRadarAPI](https://github.com/JeanExtreme002/FlightRadarAPI/tree/main/python) Python library by [JeanExtreme002](https://github.com/JeanExtreme002).

Modifications: replaced `requests` with `cloudscraper` to bypass Cloudflare bot protection on `api.flightradar24.com`. No custom headers are passed — any headers break the bypass.

Military traffic data is provided by [adsb.fi](https://opendata.adsb.fi) under their open data terms (personal, non-commercial use).
