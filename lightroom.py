from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional, Tuple

log = logging.getLogger(__name__)


class LightroomCatalog:
    """Read-only interface to an Adobe Lightroom catalog for aircraft spotting data."""

    _REG_KEY = "registration"
    _APT_KEY = "airport_iata"

    def __init__(self, catalog_path: str) -> None:
        self._path = catalog_path
        self._reg_spec_id: Optional[int] = None
        self._apt_spec_id: Optional[int] = None
        self._load_spec_ids()

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self._path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_spec_ids(self) -> None:
        try:
            with self._connect() as conn:
                for key, attr in (
                    (self._REG_KEY, "_reg_spec_id"),
                    (self._APT_KEY, "_apt_spec_id"),
                ):
                    row = conn.execute(
                        "SELECT id_local FROM AgPhotoPropertySpec WHERE key = ?", (key,)
                    ).fetchone()
                    if row:
                        setattr(self, attr, row[0])
        except Exception as exc:
            log.warning("Could not load Lightroom spec IDs from %s: %s", self._path, exc)

    def get_last_spotted(self, registration: str) -> Optional[Tuple[datetime, str]]:
        """Return (capture_datetime, airport_iata) of the most recent photo for this registration.

        Returns None if never photographed or catalog is unavailable.
        """
        if self._reg_spec_id is None:
            return None
        registration = registration.strip().upper()
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT img.captureTime, apt.internalValue AS airport_iata
                    FROM AgSearchablePhotoProperty reg
                    JOIN Adobe_images img ON img.id_local = reg.photo
                    LEFT JOIN AgSearchablePhotoProperty apt
                        ON apt.photo = reg.photo AND apt.propertySpec = ?
                    WHERE reg.propertySpec = ?
                      AND UPPER(TRIM(reg.internalValue)) = ?
                    ORDER BY img.captureTime DESC
                    LIMIT 1
                    """,
                    (self._apt_spec_id, self._reg_spec_id, registration),
                ).fetchone()
                if row and row["captureTime"]:
                    dt = datetime.fromisoformat(row["captureTime"].split(".")[0].rstrip("Z"))
                    airport = (row["airport_iata"] or "").strip()
                    return dt, airport
        except Exception as exc:
            log.warning("Lightroom query failed for %s: %s", registration, exc)
        return None


def find_catalog(folder: str = "lightroom") -> Optional[LightroomCatalog]:
    """Return a LightroomCatalog for the first .lrcat file found in folder, or None."""
    try:
        for name in os.listdir(folder):
            if name.endswith(".lrcat"):
                path = os.path.join(folder, name)
                log.info("Using Lightroom catalog: %s", path)
                return LightroomCatalog(path)
    except OSError:
        pass
    log.info("No Lightroom catalog found in '%s' — spotted info will be omitted.", folder)
    return None
