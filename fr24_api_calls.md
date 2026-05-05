# FR24 API Call Inventory

All active FR24 API calls beyond the scheduled arrivals interval check.
"Active" means the call can fire repeatedly during normal bot operation.

---

## ~~monitor.py — `get_rego_details(registration)` — line 641~~

~~**Triggered by:** Every notification sent (special livery, rare plane, watchlist hit, etc.)~~

~~**Why it is called:**~~
~~1. **Aircraft photo** — `rego_details["aircraftImages"]` is the only source for a photo URL. The interval arrivals pull does not include photo links.~~
~~2. **Next departure (live)** — now covered by DB prediction (`flight_departure_pattern`); call is retained for the photo.~~

~~**Fine as-is** — 1 call per notification for photo. Call 2 (`get_flight_by_number`) self-eliminates as the DB populates with departure details over time.~~

---

## ~~monitor.py — `get_flight_by_number(pred_fn)` — line 264~~

~~**Triggered by:** Every notification where the DB prediction is missing details (time/airline/dest) for the predicted departure flight number.~~

~~**Why it is called:**~~
~~- Fills in gaps in the DB departure pattern (scheduled time, airline, destination).~~

~~**Fine as-is** — fires rarely once the DB is seeded; self-eliminates over time as regular routes are learned.~~

---

## ~~military.py — `get_rego_details(registration)` — line 171~~

~~**Triggered by:** Every military notification.~~

~~**Why it is called:** Aircraft photo only — but military aircraft are rarely on FR24 anyway.~~

~~**Removed** — photo fetch removed from military notifications entirely.~~

---

## ~~summary.py — `get_rego_details(registration)` — line 145~~

~~**Triggered by:** Each flight shown when a user runs `/summary`.~~

~~**Removed** — `summary.py` was dead code (never imported). File deleted.~~

---

## bot.py — `get_rego_details(registration)` — line 67

**Triggered by:** User types an aircraft registration directly in the chat (for watchlist adds).

**DB first:** Checks `notification_record` for the registration's flight number, then looks up `airline_icao` from `flight_departure_pattern`. Falls back to FR24 only if the DB has no record.

---

## lookup.py — `get_rego_details(registration)` — line 52

**Triggered by:** User types a registration in chat for a quick lookup.

**DB first:** Checks `notification_record.detail` for stored "Airline (Type)" string. Falls back to FR24 only if the registration has never been notified before.

---

## ~~settings.py — `get_airport_details(code)` — line 563~~

~~**Triggered by:** User changes the monitored airport via `/settings`.~~

~~**Why it is called:**~~
~~- Validates that the entered airport code resolves to a real airport in FR24 and retrieves its coordinates and timezone for storage.~~

~~**Could it be reduced?**~~
~~- This is a rare, deliberate user action. No meaningful reduction possible.~~

---

## Summary

| File | Call | Trigger | Reducible? |
|---|---|---|---|
| ~~monitor.py:641~~ | ~~`get_rego_details`~~ | ~~Every notification~~ | ~~Fine as-is — photo only; dep covered by DB~~ |
| ~~monitor.py:264~~ | ~~`get_flight_by_number`~~ | ~~Incomplete DB dep info~~ | ~~Fine as-is — self-eliminates as DB populates~~ |
| ~~military.py:171~~ | ~~`get_rego_details`~~ | ~~Every military notification~~ | ~~Removed — no military photos on FR24~~ |
| ~~summary.py:145~~ | ~~`get_rego_details`~~ | ~~`/summary` per flight~~ | ~~Removed — file deleted (dead code)~~ |
| bot.py:67 | `get_rego_details` | User rego lookup | DB first (flight_departure_pattern), FR24 fallback |
| lookup.py:52 | `get_rego_details` | User rego lookup | DB first (notification_record.detail), FR24 fallback |
| ~~settings.py:563~~ | ~~`get_airport_details`~~ | ~~Airport code change~~ | ~~No — fine as-is~~ |
