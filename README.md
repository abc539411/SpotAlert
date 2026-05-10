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

- **Arrival reminder** — sends a reminder when a notified flight is within a configurable number of hours of arrival; only fires if the flight was originally scheduled far enough in advance to be worth a reminder; can be disabled by setting `REMINDER_HOURS = 0`
- **Aircraft changed** — if a notified registration disappears from the board but the same flight number reappears under a different registration on the same day, an "Aircraft Changed" notice is sent; if the new aircraft matches any filter, that is noted in the alert
- **Cancellation** — triggered by the FR24 confirmed cancellation status, not by the flight disappearing from the board
- **Diversion** — triggered by the FR24 confirmed diversion status, including the divert destination airport

### Notifications include

Each notification includes:
- Flight number, origin airport, airline, aircraft type and registration with 🏳 country flag
- **Last Spotted** — the date, airport, and number of times you have photographed this aircraft (sourced from your Lightroom catalog — see below)
- **Last Seen at airport** — the last date this registration was recorded landing at the monitored airport
- Scheduled and estimated arrival times (local)
- **Next Departure** — the next outbound flight from the monitored airport, showing estimated/scheduled/predicted time, flight number, and destination; predicted departures use a three-tier lookup: stored timestamps → turnaround offset (derived from scheduled times) → FR24 API call

### Military Traffic

- Monitors nearby military aircraft via the [adsb.fi](https://opendata.adsb.fi) open data API — no API key required
- Notifies when a military aircraft is on approach within a configurable radius and below a configurable altitude threshold; aircraft already on the ground are excluded
- Notification includes country of origin (derived from ICAO hex address), registration, callsign, aircraft type, altitude, speed, distance, and a direct link to the aircraft on globe.adsb.fi
- Runs on its own check interval, independent of the main arrivals check

### Spot Recommendation

Automatically recommends whether it is worth heading out to spot based on which interesting aircraft are arriving (and departing) during the day. All recommendation data is read from the bot's own notification record — no additional FR24 API calls are made.

**Activity clustering** — flights are grouped into natural sessions based on gaps between events. A gap larger than the configured **Max Gap** threshold means "you'd go home between those flights" — separate sessions. Smaller gaps within a session can be flagged as break time notices (☕ grab a coffee). The algorithm picks the **latest viable start time** within a session so you can sleep in without missing anything.

**Lighting quality indicators** — soft indicators on each flight line (not hard gates):
- 🌅 arrives shortly after sunrise — light is still low
- ☀️ arrives in the configurable midday bad-light window — harsh overhead light
- 🌇 arrives within the sunset buffer — fading light

When choosing between otherwise equal sessions, the algorithm prefers the one with more flights in good light. Within a session, it prefers the start time that keeps the most flights out of bad-light windows.

**Automatic triggers:**
- **Rolling check** — runs after every arrivals poll during the day; fires if enough interesting flights are within the next qualifying session cluster; cooldown uses the Max Gap interval
- **End-of-day check** — runs once at a configurable hour each evening; clusters tomorrow's notified flights, finds the best session(s), and sends a recommendation
  - **Single qualifying session** → Yes/Maybe/No buttons; tapping Yes schedules a "time to leave" follow-up message
  - **Multiple qualifying sessions** → inline keyboard with one button per session + Maybe/No; tapping a session button commits to that window
  - Tapping **No** suppresses rolling recommendations for the next day

**Manual `/spot` command:**
- Choose **Today** or **Tomorrow**, then select a period:
  - **Morning / Afternoon / All Day** — shows all interesting flights in that period with both arrival and predicted departure times per aircraft
  - **Best Time to Go** — clusters all flights and shows all qualifying sessions; filtered-out flights shown as strikethrough within their session

**Filters applied to all checks:**
- **Lighting gate** (hard) — flights arriving after sunset are excluded; pre-sunrise arrivals kept only if a confirmed daylight departure exists
- **Day gate** — automatic checks only run on qualifying days (any day, weekends only, or public holidays)
- **Weather gate** — automatic checks suppressed when severe weather is forecast; manual checks always run and show weather with emoji (☀️ 🌤 🌧 ⛈ etc.)
- **Spotted times** — aircraft photographed too many times at this airport can be excluded (configurable threshold)
- **Exclusion list** — registrations on the exclusion list are never surfaced in recommendations or notifications

### Multi-User Support

Multiple Telegram users can receive notifications from the same bot instance:

- **Admin** — full access to `/settings`, `/filters`, `/stats`, and all management commands
- **Read-only users** — receive all notifications and can use `/spot` and registration lookups; cannot change settings or view stats

Users are managed via `/settings → Users`. Notifications are broadcast to all registered users simultaneously.

### Registration Lookup

Type any aircraft registration directly into the chat (e.g. `VH-XQU` or `9V-SWI`) to instantly retrieve:
- Country flag emoji derived from registration prefix
- Whether the registration is on the **Exclusion List** or **Rego Watchlist**
- Aircraft type and operator (from the bot's own records first, FR24 fallback)
- Last Seen at the monitored airport
- Full Lightroom spotting history: every session where this registration was photographed, showing date, time, and airport
- Link to the FR24 aircraft page

### Spotting Stats (`/stats`) — Admin only

- Country flags shown next to all registrations and airport codes
- Total unique aircraft photographed and total spotting sessions
- Top airports by number of trips
- Top 5 most-photographed aircraft (with operator and type)
- Top 5 aircraft spotted at the most airports
- App notification totals (special liveries, military sightings, watchlist hits)

### Telegram Bot Commands

| Command | Description |
|---|---|
| `/spot` | Check interesting flights or get a spotting recommendation — choose day and period |
| `/stats` | View spotting stats and notification totals (admin only) |
| `/filters` | Manage watchlists and exclusion list |
| `/settings` | Configure all app settings at runtime |
| `/status` | Show host system info and next scheduled check times |

### Runtime Configuration

All settings are adjustable via `/settings` in Telegram. Changes take effect immediately and persist across restarts.

---

## Lightroom Catalog Integration

SpotAlert can read your Adobe Lightroom catalog to enrich notifications and lookups with your personal spotting history — showing when you last photographed an aircraft, how many times, and at which airports.

To enable this feature, place your `.lrcat` file in the `lightroom/` folder. SpotAlert opens it read-only and never modifies it.

Aircraft metadata (registration, airline, aircraft type, airport) must be tagged in Lightroom using the [AircraftMetadata Lightroom Plugin](https://github.com/aviationphoto/AircraftMetadata-Lightroom-Plugin) by [aviationphoto](https://github.com/aviationphoto).

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
| `ARRIVALS_TO_FETCH` | How many arrivals to scan per cycle (100 per page) | 200 |
| `REMINDER_HOURS` | Hours before arrival to send a reminder; 0 = disabled | 12 |
| `SPECIAL_LIVERY_KEYWORDS` | Comma-separated keywords matched against airline name | Livery,livery,Sticker,sticker |
| `RARE_PLANE_MIN_ABSENCE_DAYS` | Days a combo must be absent before being considered rare | 7 |
| `DEPARTURE_PATTERN_THRESHOLD` | Minimum confidence % to show a predicted departure; 0 = disabled | 80 |
| `MILITARY_CHECK_INTERVAL_MINUTES` | How often to check for military traffic | 15 |
| `MILITARY_RADIUS_NM` | Search radius around the airport (nautical miles, max 250) | 50 |
| `MILITARY_MAX_ALT_FT` | Maximum altitude to consider a military aircraft "on approach" | 5000 |
| `SPOT_REC_ENABLED` | Enable the spot recommendation feature | false |
| `SPOT_REC_TRAVEL_MINS` | Minutes to travel from home to airport | 30 |
| `SPOT_REC_MAX_GAP_HOURS` | Gap (hours) between events that splits into separate sessions; also rolling cooldown | 3 |
| `SPOT_REC_THRESHOLD` | Minimum interesting flights required for a recommendation | 3 |
| `SPOT_REC_EOD_HOUR` | Local hour (0–23) to send the end-of-day recommendation | 20 |
| `SPOT_REC_SUNRISE_BUFFER_MINS` | Minutes after sunrise still flagged as poor light (🌅) | 30 |
| `SPOT_REC_SUNSET_BUFFER_MINS` | Minutes before sunset still flagged as poor light (🌇) | 30 |
| `SPOT_REC_BAD_LIGHT_START` | Local HH:MM start of midday bad-light window (☀️); empty = disabled | — |
| `SPOT_REC_BAD_LIGHT_END` | Local HH:MM end of midday bad-light window | — |

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
- **Departure patterns** — historical arrival→departure flight number pairings with observation counts, scheduled/estimated departure timestamps, turnaround offset (for predicting future departures), airline, and destination
- Registered users (admin + read-only)
- Any settings changed via the bot

A daily backup is saved automatically to `config/filters/backups/`, keeping the last 7 copies.

---

## Utilities

### check_db.py

A standalone script to inspect the contents of the database directly — useful for debugging or reviewing what the bot has recorded.

```bash
python check_db.py
```

### backfill.py

A one-time setup script that seeds the database with historical data from FR24 (requires a FR24 premium account). Run it once after first install to bootstrap the departure pattern and rare plane history before the bot has had time to learn from live traffic.

```bash
python backfill.py
```

---

## License

This project is released under the [MIT License](LICENSE).

### Third-party code and data

**FlightRadarAPI** — The `flightradar24api/` module is a modified version of the [FlightRadarAPI](https://github.com/JeanExtreme002/FlightRadarAPI/tree/main/python) Python library by [JeanExtreme002](https://github.com/JeanExtreme002), released under the MIT License. Modifications: replaced `requests` with `cloudscraper` to bypass Cloudflare bot protection on `api.flightradar24.com`.

**FlightRadar24 data** — This project accesses FlightRadar24's unofficial API. FlightRadar24's [Terms of Service](https://www.flightradar24.com/terms-and-conditions) restrict use of their data to **personal, non-commercial purposes only**. Do not use this project in any commercial context without obtaining a proper data licence from [FlightRadar24](https://www.flightradar24.com).

**adsb.fi open data** — Military aircraft data is sourced from [opendata.adsb.fi](https://opendata.adsb.fi). This data is provided for **personal, non-commercial use only**. See [adsb.fi](https://adsb.fi) for their full terms of use.

**AircraftMetadata Lightroom Plugin** — Aircraft metadata fields read from the Lightroom catalog (registration, airline, aircraft type, airport) are created by the [AircraftMetadata Lightroom Plugin](https://github.com/aviationphoto/AircraftMetadata-Lightroom-Plugin) by [aviationphoto](https://github.com/aviationphoto).
