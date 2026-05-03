# SpotAlert

A Telegram bot that monitors FlightRadar24 arrivals at a chosen airport and sends notifications when interesting aircraft are detected. Designed for aircraft spotters who want advance notice of special liveries, rare airline/type combinations, watchlisted registrations, operators, and military traffic.

---

## Features

### Arrival Filters

All filters run in the following priority order — a flight only ever triggers one notification:

1. **Special Livery** — detects aircraft whose airline name contains configurable keywords (e.g. "Livery", "Sticker") and extracts the livery name from the airline field (e.g. "Air New Zealand (All Blacks Livery)" → "All Blacks Livery")
2. **Rego Watchlist** — notifies when a specific registration on your watchlist is inbound; airline is looked up automatically from FR24
3. **Type Watchlist** — notifies when a specific airline + aircraft type combination is inbound
4. **Airline/Operator Watchlist** — notifies when an aircraft from a watched airline or operator is inbound; when adding an entry you are asked whether it is an airline or an operator
5. **Rare Plane** — notifies when an airline + aircraft type combination reappears after being absent for a configurable number of days; every sighting refreshes the clock so frequent arrivals never trigger this filter

### Follow-up Notifications

- **Arrival reminder** — sends a reminder when a notified flight is within a configurable number of hours of arrival; can be disabled by setting `REMINDER_HOURS = 0`
- **Aircraft changed** — if a notified registration disappears from the board but the same flight number reappears under a different registration, an "Aircraft Changed" notice is sent; if the new aircraft matches any filter that is noted in the alert
- **Cancellation/diversion alert** — if a notified flight genuinely disappears from the arrivals board, you are alerted

### Notifications include

Each notification includes:
- Flight number, origin airport, airline, aircraft type and registration
- **Last Spotted** — the date, airport, and number of times you have photographed this aircraft (sourced from your Lightroom catalog — see below)
- **Last Seen at airport** — the last date this registration was recorded landing at the monitored airport
- Scheduled and estimated arrival times (local)
- Next scheduled departure from the monitored airport

### Military Traffic

- Monitors nearby military aircraft via the [adsb.fi](https://opendata.adsb.fi) open data API — no API key required
- Notifies when a military aircraft is on approach within a configurable radius and below a configurable altitude threshold
- Runs on its own check interval, independent of the main arrivals check

### Spot Recommendation

Automatically recommends whether it is worth heading out to spot based on the number of interesting arrivals in your planned session window.

- **Rolling check** — runs every arrivals cycle during the day; fires if enough interesting planes are arriving within your travel + session window
- **End-of-day check** — runs once at a configurable time each evening; scans the next day's full schedule to find the optimal session window and sends a recommendation with Yes/Maybe/No response buttons
  - Tapping **Yes** schedules a follow-up message at the time you need to leave, with an updated flight list
  - Tapping **No** suppresses rolling recommendations the next day
- Both checks respect configurable gates: day type (any day vs weekends/public holidays), weather (severe weather suppresses the recommendation), and lighting (flights arriving after sunset can be excluded)
- Aircraft you have photographed too many times at the airport can be excluded from the interesting count (configurable threshold)

### Registration Lookup

Type any aircraft registration directly into the chat (e.g. `VH-XQU`) to instantly retrieve:
- Last Seen at the monitored airport
- Full spotted history from your Lightroom catalog (date, time, airport — per spotting session)
- Aircraft type and operator from FR24

### Spotting Stats (`/stats`)

- Total unique aircraft photographed and total spotting sessions
- Top airports by number of trips
- Top 5 most-photographed aircraft (with operator and type)
- Top 5 aircraft spotted at the most airports
- App notification totals (special liveries, military sightings, watchlist hits)

### Telegram Bot Commands

| Command | Description |
|---|---|
| `/spot` | Check if it is recommended to go spotting today or tomorrow |
| `/summary` | View notified flights for today or tomorrow by time period |
| `/stats` | View spotting stats and notification totals |
| `/filters` | Manage watchlists and exclusion list |
| `/settings` | Configure all app settings at runtime |
| `/status` | Show host system info and next scheduled check times |

### Runtime Configuration

All settings are adjustable via `/settings` in Telegram — airport, check intervals, reminder hours, filter thresholds, arrival windows, active days, military settings, summary period definitions, and full spot recommendation configuration. Changes persist across restarts.

---

## Lightroom Catalog Integration

SpotAlert can read your Adobe Lightroom catalog to enrich notifications and lookups with your personal spotting history — showing when you last photographed an aircraft, how many times, and at which airports.

To enable this feature, place your `.lrcat` file in the `lightroom/` folder. SpotAlert opens it read-only and never modifies it.

Aircraft metadata (registration, airline, aircraft type, airport) must be tagged in Lightroom using the [AircraftMetadata Lightroom Plugin](https://github.com/aviationphoto/AircraftMetadata-Lightroom-Plugin) by [aviationphoto](https://github.com/aviationphoto). SpotAlert does not use any of that plugin's code — it reads the metadata fields the plugin creates directly from the catalog's SQLite database.

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
   git clone https://github.com/abc539411/spotalert.git
   cd spotalert
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

5. **(Optional) Lightroom integration** — copy your `.lrcat` file into the `lightroom/` folder. SpotAlert will detect it automatically on next startup.

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
| `SPOT_REC_ENABLED` | Enable the spot recommendation feature | false |
| `SPOT_REC_TRAVEL_MINS` | Minutes to travel from home to the airport | 30 |
| `SPOT_REC_SESSION_HOURS` | Typical spotting session length in hours | 5 |
| `SPOT_REC_THRESHOLD` | Minimum interesting arrivals to trigger a recommendation | 3 |
| `SPOT_REC_EOD_HOUR` | Local hour (0–23) to send the end-of-day recommendation | 20 |

All settings can also be changed at runtime via `/settings` in Telegram.

---

## Filters: Active Days & Arrival Window

Each filter supports two optional constraints:

- **Active Days** — limit notifications to specific days of the week (e.g. `Sat,Sun`)
- **Arrival Window** — `Always` (default), `Daylight Only` (between dawn and dusk at the airport), or `Off` (disabled entirely)

---

## Data Persistence

A SQLite database is created at `config/filters/spotalert.db` on first run. It stores:
- Notification history and throttle tracking for all filters
- Rego, type, and airline/operator watchlists
- Exclusion list
- Follow-up tracking (reminders, aircraft swaps, cancellations)
- Sighting history — actual landing timestamps for registrations seen at the airport
- Any settings changed via the bot

A daily backup is saved automatically to `config/filters/backups/`, keeping the last 7 copies.

---

## Utilities

### check_db.py

A standalone script to inspect the contents of the database directly — useful for debugging or reviewing what the bot has recorded.

```bash
python check_db.py
```

---

## License

This project is released under the [MIT License](LICENSE).

### Third-party code and data

**FlightRadarAPI** — The `flightradar24api/` module is a modified version of the [FlightRadarAPI](https://github.com/JeanExtreme002/FlightRadarAPI/tree/main/python) Python library by [JeanExtreme002](https://github.com/JeanExtreme002), released under the MIT License. Modifications: replaced `requests` with `cloudscraper` to bypass Cloudflare bot protection on `api.flightradar24.com`.

**FlightRadar24 data** — This project accesses FlightRadar24's unofficial API. FlightRadar24's [Terms of Service](https://www.flightradar24.com/terms-and-conditions) restrict use of their data to **personal, non-commercial purposes only**. Do not use this project in any commercial context without obtaining a proper data licence from [FlightRadar24](https://www.flightradar24.com).

**adsb.fi open data** — Military aircraft data is sourced from [opendata.adsb.fi](https://opendata.adsb.fi). This data is provided for **personal, non-commercial use only**. See [adsb.fi](https://adsb.fi) for their full terms of use.

**AircraftMetadata Lightroom Plugin** — Aircraft metadata fields read from the Lightroom catalog (registration, airline, aircraft type, airport) are created by the [AircraftMetadata Lightroom Plugin](https://github.com/aviationphoto/AircraftMetadata-Lightroom-Plugin) by [aviationphoto](https://github.com/aviationphoto). SpotAlert does not use any code from that plugin — it reads the metadata fields it creates directly from the catalog's SQLite database.
