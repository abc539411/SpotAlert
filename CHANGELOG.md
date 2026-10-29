# Changelog

Versioning: a **major** release is x.y0 (2.10, 2.20...) and re-offers the in-app tour once; point releases (2.11, 2.12...) are fixes and polish and do not.

## 2.20 — since 2.13

### Added
- **Flown** (Collection → Flown) — the flights you have been on. Import a **MyFR24** (FlightDiary) or **Flighty** CSV, or add flights by hand; a separate **Edit** column holds Import CSV, Add flight and Done. Flights are grouped by year and Aircraft by airline (both fold), side by side on PC and swipeable on phones. Re-importing never duplicates or overwrites what you typed. **Look up on FR24** fills a recent flight (about two weeks of FR24 history) from its date and flight number. The airline name and aircraft type (ICAO) are editable. Controllers and Pilots only; your records stay in your own account.
- **Flown aircraft are marked everywhere** — a registration you have flown on is green with a small plane after it (Feed, Spotting, Board, Search, photo pop-ups); hover for how many times.
- **Board: Possible Equipment** shows an `Nx` pill for how many sessions you spotted each aircraft in, lists the aircraft assigned to the flight itself, and a right-aligned "Past 3 Years" note. The Spotted section moved to the Feed card.
- **Feed card** — Last Visit sits under the status pill; a Schedule heading; Spotted sessions are rows (airport name on PC, code on phones) with the total session count at the right; the total reads "N Sessions".
- **Photo pop-ups** share one layout (type pills under the registration, airline tail and name bottom-left, livery line above the place and date bottom-right). The Board's Possible Equipment pop-up shows the aircraft's JetPhotos picture with a JetPhotos pill.

### Changed
- **One card style** across Spotting, Board, Feed, Collection (Summary, Flown, Fleet) and Search: shaded headers, faint-line rows, one divider strength, matching group headers and count pills at the right. Fleet now uses Flown's footprint and rows (a green dot for photographed aircraft); Flown on phones has the same footprint as a Fleet card.
- **Sharper detail photos** — Feed, Spotting and Board photos use the full-size JetPhotos image with its black logo strip cropped off, cut once and cached on the server. The Spotting pop-up on PC keeps the plain 640px photo.
- Dates: Feed Last Visit and Fleet read `28 Dec '22`; Catalogue sessions read `9 Jul 2025`.
- Search → Last Seen single-row cards have no header shading; Catalogue cards on phones got a taller header with the sessions pill at its right.
- Alitalia and Cathay Dragon tail logos are bundled (`static/logo_overrides/`); the airline code for Cathay Dragon is completed for Flown.

### Fixed
- Collection keyword tiles no longer fall back to "not set" after the background refresh.
- The date field in the Flown form no longer overruns its neighbour on iPhone.

## 2.13 — since 2.12

### Added
- **Sunrise / sunset lines on the Board** — a glowing dot and the word "Sunrise" / "Sunset", centred inside each day's flight list at the right time of day, in Spotting's colours. The same glowing dot now sits on Spotting's sunrise/sunset markers. Days the weather cache doesn't cover are worked out from the airport's position.
- **Previous equipment on Rare Plane flights** — the Board card shows "Previous Equipment - B78X" (此前机型) and the Spotting card shows "Was B38M" (原为).

## 2.12 — since 2.10

### Added
- **Board: today + 3 days ahead** always show, and "No info yet" rows cover all four days; each fills with real flights as the normal checks reach it.
- **Fleet marker** — an aircraft that is in one of your Fleet cards but not yet photographed gets a small gold camera before its flag on the Board (any row with a rego). Those flights, and aircraft on your Exclusion List, still show when their flight number is hidden (Passengers see them too, without the icon).
- **Rare Plane cards** show the flight's previous equipment type in grey where a livery name would go ("Previous Equipment - B38M" / "此前机型 - B38M").
- Feed: no **Older** button for an airport with no history beyond the 7 days shown (a newly added airport).

### Changed
- Hiding flights on the Board updates immediately instead of waiting for a full reload.
- Board day cards swipe like Spotting's (carousel behaviour, no swipe blocking, no rebuild under a finger).
- "Possible aircraft" is now **Possible Equipment**.
- Photos: a new card checks that its saved photo link still loads (falling back to FR24's aircraft page, then JetPhotos), and the main Feed now repairs missing and broken photos the way the older-weeks view does — with no extra requests when FR24 supplies a photo.
- The tour's "What's new" step is shown for major releases only.

### Fixed
- Creating a brand-new airport database no longer fails on a one-off migration.

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
