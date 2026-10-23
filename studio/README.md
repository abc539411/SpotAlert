# SpotAlert Studio

Local companion app to [SpotAlert](../README.md) — scans a photo inbox, looks up
registration → airline/aircraft-type metadata (FlightRadar24, with a JetPhotos
fallback for military aircraft FR24 doesn't track), organizes files into dated
airline/registration folders, and writes the metadata directly into a local
Lightroom Classic catalog.

This is the ported and rebranded successor to the old standalone "SpottingStation"
project. It runs natively on your own PC — not on SpotAlert's remote server —
because it needs direct filesystem access to your photo inbox and Lightroom
catalog, neither of which a remote server can reach.

## Setup

From the repo root, the quickest way to start Studio:

```powershell
.\studio.ps1
```

This is a thin wrapper around `studio\run.ps1` — you can also run that
directly from inside this folder:

```powershell
.\run.ps1
```

Either way, this creates a local `.venv`, installs dependencies, and starts the
app at `http://127.0.0.1:5000`. Re-run any time — it reuses the existing venv
and just updates dependencies.

## Configuration

Set these environment variables before running `run.ps1` to override the defaults
in `config.py`:

- `INBOX_PATH` — where new RAW files land (default: `C:\Users\<you>\Pictures\Planespotting Inbox`)
- `OUTPUT_PATH` — where organized files get moved to (default: the NAS share)
- `LR_CATALOG_PATH` — path to your Lightroom `.lrcat` file

## Important: close Lightroom before organizing

Writing metadata into the `.lrcat` file requires exclusive SQLite access —
Lightroom must be closed while you run the Organize step, or the write will fail
with a WAL lock conflict. `utils/lr_catalog.py::is_lightroom_running()` checks for
this and clears a stale lock file if Lightroom isn't actually running.

## Syncing the catalog to a SpotAlert instance

Settings → **SpotAlert catalog sync** links Studio to one SpotAlert instance
(URL + a Controller or Pilot login). Once linked, Studio uploads the catalog to
that instance's "My Catalog" when organising is finished — after Move All
completes, or when the last photo leaves the inbox — so its Collection / Fleet /
Search-Catalogue views pick up the new photos. The toolbar chip shows progress
and doubles as a manual upload / retry button.

Only the session cookie is stored (`data/spotalert_sync.json`, git-ignored),
never the password; it lasts 90 days, after which you reconnect. Lightroom must
be closed for the upload too, so a half-written catalog is never sent.

## Architecture notes

- `utils/spotalert_sync.py` handles the instance login and the streamed catalog
  upload (`/api/auth/login` + `/api/catalog/upload` on the SpotAlert side).

- `utils/fr24_lookup.py` is the primary registration lookup, backed by
  SpotAlert's own vendored `flightradar24api/` client (no login required).
- `utils/jetphotos_fallback.py` only fires when FR24 has no flight history for a
  registration (mainly military aircraft) — mirrors the same fallback pattern
  SpotAlert's main app uses in `monitor.py`.
- `utils/aircraft_meta.py` holds the type→manufacturer/family derivation shared
  by both the lookup and the catalog stats endpoints.
- Both lookup paths cache results in SQLite under `data/cache/` (1-week TTL by
  default — see `CACHE_EXPIRY_HOURS` in `config.py`).
