# Changelog

## 2.10 — since 2.01

### Added
- **Board** — arrivals board for regular flight numbers, showing the assigned aircraft before it lands. Day cards (today centred at the current time), Older + month picker, "No info yet" rows for flights not yet on FR24's schedule (placed at the same flight's scheduled time on the most recent same weekday).
- **Board for every role** — Passengers follow the Controller's hidden list; Controllers and Pilots can hide flights and add one-off "Manual" flights. Settings → Board: Regular Flight Threshold, Drop After, Placeholder Grace, and **Open on the Board** (home page; on phones the Feed becomes the pull-down page).
- **Feed Older** — earlier weeks grouped by year and week; **Spotting** gets Older and a month picker too.
- **Refresh with confirmation** — every refresh button (Feed, Spotting, Board, Collection, Search) asks twice and shows when the data was last updated (Collection and Search also show the latest photo date). Search refreshes the Collection.
- **Take a tour** — guided walk through every tab, offered at each sign-in and on every version change, replayable from Settings → General; starts on the airport page, uses the Controller's first airport for example data, English and Chinese, with upgrade notes for Passengers.
- **Automatic photo repair** — missing or dead aircraft photo links are refilled (FR24, then JetPhotos) and saved to the card.
- **Mute for everyone** — Passengers get their own notification mute list; the airport selector in user management has an **All** pill.
- Spotting-day pill (days you photographed at the airport, from your own catalog).

### Changed
- Flight history, the Spotting timeline cache and Board rows are kept for **3 years** (was 30 days).
- Phone detail cards rise to 80% of the screen; the Board's day list no longer scrolls past its first or last flight.
- Service worker is network-first, so the page and its script always come from the same release.
- Chinese wording: Board is 大屏; Arrivals/Departures are 进港/离港; and several smaller label fixes.

### Notes
- Everything is additive: new columns on the user table (`board_default`, `tour_seen`, `tour_version`), no destructive migrations.
