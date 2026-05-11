from __future__ import annotations

import csv
import logging
import time
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage import SqliteStore

log = logging.getLogger(__name__)

OPENSKY_CSV_URL = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
REFRESH_DAYS = 30
_BATCH_SIZE = 5000


def maybe_refresh(store: SqliteStore) -> None:
    """Download and import OpenSky CSV if the table is empty or data is stale."""
    last = store.airframe_last_updated()
    if last and (time.time() - last) < REFRESH_DAYS * 86400:
        age_days = int((time.time() - last) / 86400)
        log.info("Airframe DB up to date (%d days old, refresh at %d)", age_days, REFRESH_DAYS)
        return
    _download_and_import(store)


def _download_and_import(store: SqliteStore) -> int:
    """Stream OpenSky aircraft CSV into airframe_db. Returns number of rows imported."""
    log.info("Airframe DB: downloading OpenSky aircraft database ...")
    now_ts = int(time.time())
    count = 0
    batch = []

    try:
        req = urllib.request.Request(
            OPENSKY_CSV_URL,
            headers={"User-Agent": "SpotAlert/1.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            reader = csv.DictReader(line.decode("utf-8", errors="replace") for line in resp)
            for row in reader:
                reg = (row.get("registration") or "").strip().upper()
                if not reg:
                    continue

                built_year = None
                built_raw = (row.get("built") or "").strip()
                if built_raw and len(built_raw) >= 4 and built_raw[:4].isdigit():
                    built_year = int(built_raw[:4])

                batch.append((
                    reg,
                    (row.get("icao24") or "").strip().lower() or None,
                    (row.get("manufacturername") or "").strip() or None,
                    (row.get("serialnumber") or "").strip() or None,
                    built_year,
                    (row.get("owner") or "").strip() or None,
                    (row.get("operator") or "").strip() or None,
                    (row.get("operatoricao") or "").strip() or None,
                    (row.get("operatoriata") or "").strip() or None,
                    now_ts,
                ))
                count += 1

                if len(batch) >= _BATCH_SIZE:
                    store.bulk_upsert_airframes(batch)
                    batch.clear()

            if batch:
                store.bulk_upsert_airframes(batch)

    except Exception as exc:
        log.error("Airframe DB: download failed — %s", exc)
        return 0

    log.info("Airframe DB: imported %d rows from OpenSky", count)
    return count
