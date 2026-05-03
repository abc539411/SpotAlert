# spmonitor

A Telegram bot that monitors FlightRadar24 arrivals at a chosen airport and sends notifications when interesting aircraft are detected. Designed for aircraft spotters who want advance notice of special liveries, rare airline/type combinations, watchlisted registrations, operators, and military traffic.

---

## Features

### Arrival Filters

All filters run in the following priority order — a flight only ever triggers one notification:

1. **Special Livery** — detects aircraft whose airline name contains configurable keywords (e.g. "Livery", "Sticker") and extracts the livery name from the airline field (e.g. "Air New Zealand (All Blacks Livery)" → "All Blacks Livery")
2. **Rego Watchlist** — notifies when a specific registration on your watchlist is inbound; airline is looked up automatically from FR24
3. **Type Watchlist** — notifies when a specific airline + aircraft type combination is inbound
4. **Airline/Operator Watchlist** — notifies when an aircraft from a watched airline or operator is inbound; when adding an entry you are asked whether it is an airline or an operator (an airline can have multiple operators flying on its behalf)
5. **Rare Plane** — notifies when an airline + aircraft type combination reappears after being absent for a configurable number of days; every sighting refreshes the clock so frequent arrivals never trigger this filter

### Follow-up Notifications

- **Arrival reminder** — sends a reminder when a notified flight is within a configurable number of hours of arrival (only if it was first notified more than that many hours before arrival); can be disabled by setting `REMINDER_HOURS = 0`
- **Aircraft changed** — if a notified registration disappears from the board but the same flight number reappears under a different registration, an "Aircraft Changed" notice is sent instead of a false cancellation alert; if the new aircraft matches any filter (special livery, watchlist, etc.) that is included in the notice
- **Cancellation/diversion alert** — if a notified flight genuinely disappears from the arrivals board (not a swap), you are alerted

### Military Traffic

- Monitors nearby military aircraft via the [adsb.fi](https://opendata.adsb.fi) open data API — no API key required
- Notifies when a military aircraft is on approach within a configurable radius and below a configurable altitude threshold
- Runs on its own check interval, independent of the main arrivals check

### Telegram Bot Commands

| Command | Description |
|---|---|
| `/filters` | Manage watchlists and exclusion list |
| `/settings` | Configure all app settings at runtime |
| `/summary` | View a summary of notified flights for today or tomorrow, filtered by morning or afternoon |
| `/status` | Show host system info (OS, Python version) and next scheduled check times |

### Runtime Configuration

All settings are adjustable via `/settings` in Telegram — airport, check intervals, reminder hours, filter thresholds, arrival windows, active days, military settings, and summary period definitions. Changes persist across restarts and are written back to `config/config.env` automatically.

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

| Setting | Description | Default |
|---|---|---|
| `AIRPORT_CODE` | IATA or ICAO code of the airport to monitor | — |
| `CHECK_INTERVAL_MINUTES` | How often to poll FR24 for arrivals | 30 |
| `REMINDER_HOURS` | Hours before arrival to send a reminder; 0 = disabled | 12 |
| `SPECIAL_LIVERY_KEYWORDS` | Comma-separated keywords matched against airline name | Livery,livery,Sticker,sticker |
| `RARE_PLANE_MIN_ABSENCE_DAYS` | Days a combo must be absent before being considered rare | 7 |
| `MILITARY_CHECK_INTERVAL_MINUTES` | How often to check for military traffic | 15 |
| `MILITARY_RADIUS_NM` | Search radius around the airport (nautical miles, max 250) | 50 |
| `MILITARY_MAX_ALT_FT` | Maximum altitude to consider a military aircraft "on approach" | 5000 |

All settings can also be changed at runtime via `/settings` in Telegram.

---

## Filters: Active Days & Arrival Window

Each filter supports two optional constraints:

- **Active Days** — limit notifications to specific days of the week (e.g. `Sat,Sun`)
- **Arrival Window** — `Always` (default), `Daylight Only` (between dawn and dusk at the airport), or `Off` (disabled entirely)

---

## Data Persistence

A SQLite database is created at `config/filters/spmonitor.db` on first run. It stores:
- Notification history and throttle tracking for all filters
- Rego, type, and airline/operator watchlists
- Exclusion list
- Follow-up tracking (reminders, aircraft swaps, cancellations)
- Any settings changed via the bot

---

## Utilities

### check_db.py

A standalone script to inspect the contents of the database directly — useful for debugging or reviewing what the bot has recorded.

```bash
python check_db.py
```

It prints every table: rare plane and special livery history, notification records, all watchlists, the exclusion list, military history, and current app settings overrides.

---

## License

This project is released under the [MIT License](LICENSE).

### Third-party code and data

**FlightRadarAPI** — The `flightradar24api/` module is a modified version of the [FlightRadarAPI](https://github.com/JeanExtreme002/FlightRadarAPI/tree/main/python) Python library by [JeanExtreme002](https://github.com/JeanExtreme002), released under the MIT License.

Modifications made for this project: replaced `requests` with `cloudscraper` to bypass Cloudflare bot protection on `api.flightradar24.com`. No custom headers are passed — any headers break the Cloudflare bypass and result in 403 errors.

**FlightRadar24 data** — This project accesses FlightRadar24's unofficial API. FlightRadar24's [Terms of Service](https://www.flightradar24.com/terms-and-conditions) restrict use of their data to **personal, non-commercial purposes only**. Do not use this project in any commercial context without obtaining a proper data licence from [FlightRadar24](https://www.flightradar24.com).

**adsb.fi open data** — Military aircraft data is sourced from [opendata.adsb.fi](https://opendata.adsb.fi). This data is provided for **personal, non-commercial use only**. See [adsb.fi](https://adsb.fi) for their full terms of use. If you intend to use this project commercially, you must arrange separate licensing with adsb.fi.
