# SpotAlert

A self-hosted aircraft spotting assistant that monitors FlightRadar24 arrivals at a chosen airport and notifies you when interesting aircraft are detected. Features a Progressive Web App (PWA) front-end for real-time feed, spotting recommendations, collection tracking, and filter management.

---

## Features

### Arrival Filters

All filters run in priority order — a flight only ever triggers one match:

1. **Special Livery** — detects aircraft whose airline name contains configurable keywords (e.g. "Livery", "Sticker") and extracts the livery name from the airline field (e.g. "Air New Zealand (All Blacks Livery)" → "All Blacks Livery")
2. **Rego Watchlist** — notifies when a specific registration on your watchlist is inbound
3. **Type Watchlist** — notifies when a specific airline + aircraft type combination is inbound
4. **Airline/Operator Watchlist** — notifies when an aircraft from a watched airline or operator is inbound
5. **Rare Plane** — notifies when an airline + aircraft type combination reappears after being absent for a configurable number of days

### Web App (PWA)

Accessible from any browser on your local network. Installable as a home-screen app on iOS and Android.

- **Feed** — chronological day-grouped cards for every filter-matched arrival; shows flight route, status, departure prediction, and aircraft photo
- **Timeline** — spotting window recommendations clustered by time of day; shows which flights qualify, lull periods, and lighting quality indicators
- **Collection** — cross-references your Lightroom catalog with the flight feed; shows what you've photographed and what you're missing
- **Fleet** — track full airline fleets from FR24; pills show which registrations you've captured and let you add unseen aircraft to your Rego Watchlist
- **Search** — look up any registration's sighting history; browse route equipment by flight number; browse your Lightroom catalogue
- **Settings** — manage all filters, watchlists, monitoring config, spot recommendation tuning, and airports from the web UI

### Military Traffic

- Monitors nearby military aircraft via the [adsb.fi](https://opendata.adsb.fi) open data API — no API key required
- Detects aircraft on approach within a configurable radius and below a configurable altitude threshold
- Notification includes country of origin (derived from ICAO hex address), registration, callsign, aircraft type, altitude, speed, distance, and a link to globe.adsb.fi

### Spot Recommendation

Clusters the day's interesting arrivals into natural spotting sessions and recommends the best window to head out.

- **Activity clustering** — flights grouped by gaps between events; a gap larger than the Max Gap threshold = separate session
- **Lighting quality indicators** — 🌙 low/fading light around sunrise/sunset; ☀️ harsh overhead midday light
- **Departure pairing** — each arrival is matched with its outbound departure (live board → historical patterns → turnaround prediction) so you can see how long an aircraft will be on the ground
- **Weather integration** — severe weather suppresses automatic recommendations; weather always shown in the Timeline tab

### Lightroom Catalog Integration

SpotAlert reads your Adobe Lightroom catalog (read-only) to enrich the feed and Fleet tab with your personal spotting history — last photographed date, session count, airport.

Aircraft metadata (registration, airline, aircraft type, airport) must be tagged using the [AircraftMetadata Lightroom Plugin](https://github.com/aviationphoto/AircraftMetadata-Lightroom-Plugin).

---

## Requirements

- Python 3.10+
- Docker (recommended for deployment)

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

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run**
   ```bash
   python main.py
   ```
   The web app is available at `http://localhost:8088` by default.

4. **Configure via the web UI** — open the Settings tab and set at minimum `AIRPORT_CODE` and `WEB_TIMEZONE`.

5. **(Optional) Lightroom integration** — set `LR_CATALOG_PATH` in the Settings tab to the path of your `.lrcat` file. SpotAlert opens it read-only.

---

## Configuration

All settings are managed via the **Settings** tab in the web UI and stored in `data/spotalert.db`. Key settings:

| Setting | Description | Default |
|---|---|---|
| `AIRPORT_CODE` | IATA code of the airport to monitor | — |
| `WEB_TIMEZONE` | Display timezone (IANA format, e.g. `Australia/Sydney`) | — |
| `CHECK_INTERVAL_MINUTES` | How often to poll FR24 for arrivals | 30 |
| `FETCH_PAGES` | Number of pages to fetch per check (100 flights/page) | 2 |
| `SPECIAL_LIVERY_KEYWORDS` | Comma-separated keywords matched against airline name | `Livery,livery,Sticker,sticker` |
| `RARE_PLANE_MIN_ABSENCE_DAYS` | Days a combo must be absent before being considered rare | 7 |
| `DEPARTURE_PATTERN_THRESHOLD` | Minimum confidence % to show a predicted departure; 0 = off | 80 |
| `MILITARY_CHECK_INTERVAL_MINUTES` | How often to check for military traffic | 15 |
| `MILITARY_RADIUS_NM` | Search radius around the airport (nautical miles, max 250) | 50 |
| `MILITARY_MAX_ALT_FT` | Maximum altitude to consider a military aircraft "on approach" | 5000 |
| `LOGOSTREAM_API_KEY` | API key for airline tail logo fetching (Logostream) | — |
| `LR_CATALOG_PATH` | Path to your Lightroom `.lrcat` file | — |

---

## Data Persistence

A SQLite database is created at `data/spotalert.db` on first run. It stores:

- All filter-matched arrivals and their departure pairings
- Rego, type, and airline/operator watchlists + exclusion list
- Notification cooldown state for each filter
- Sighting history — last landing timestamps per registration
- Departure patterns — historical arrival→departure pairings with turnaround offsets
- Airport and aircraft type reference caches
- Fleet cards and Lightroom session cross-references
- Pre-computed spotting window clusters (timeline cache)

A daily backup is saved to `data/backups/`, keeping the last 7 copies.

---

## Utilities

### backfill.py

Seeds the database with historical FR24 data after first install. Populates sighting history, rare plane history, and departure patterns so the app has context before live traffic accumulates.

```bash
python backfill.py
```

### _seed_cookies.py

Run locally to seed Cloudflare session cookies into the webapp container when FR24 access is blocked:

```bash
python _seed_cookies.py
```

### _seed_icao_types.py

Seeds the aircraft type cache from the ICAOList.csv reference data. Run once after a DB reset:

```bash
python _seed_icao_types.py
```

---

## License

This project is released under the [MIT License](LICENSE).

### Third-party code and data

**FlightRadarAPI** — The `flightradar24api/` module is a modified version of the [FlightRadarAPI](https://github.com/JeanExtreme002/FlightRadarAPI/tree/main/python) Python library by [JeanExtreme002](https://github.com/JeanExtreme002), released under the MIT License. Modifications: replaced `requests` with `cloudscraper` to bypass Cloudflare bot protection on `api.flightradar24.com`.

**FlightRadar24 data** — This project accesses FlightRadar24's unofficial API. FlightRadar24's [Terms of Service](https://www.flightradar24.com/terms-and-conditions) restrict use of their data to **personal, non-commercial purposes only**. Do not use this project in any commercial context without obtaining a proper data licence from FlightRadar24.

**adsb.fi open data** — Military aircraft data is sourced from [opendata.adsb.fi](https://opendata.adsb.fi). This data is provided for **personal, non-commercial use only**. See [adsb.fi](https://adsb.fi) for their full terms of use.

**AircraftMetadata Lightroom Plugin** — Aircraft metadata fields read from the Lightroom catalog (registration, airline, aircraft type, airport) are created by the [AircraftMetadata Lightroom Plugin](https://github.com/aviationphoto/AircraftMetadata-Lightroom-Plugin) by [aviationphoto](https://github.com/aviationphoto).
