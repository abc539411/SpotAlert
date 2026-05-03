from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)


class LightroomCatalog:
    """Read-only interface to an Adobe Lightroom catalog for aircraft spotting data."""

    _REG_KEY     = "registration"
    _APT_KEY     = "airport_iata"
    _AIRLINE_KEY = "airline"
    _TYPE_KEY    = "aircraft_type"

    def __init__(self, catalog_path: str) -> None:
        self._path = catalog_path
        self._reg_spec_id:     Optional[int] = None
        self._apt_spec_id:     Optional[int] = None
        self._airline_spec_id: Optional[int] = None
        self._type_spec_id:    Optional[int] = None
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
                    (self._REG_KEY,     "_reg_spec_id"),
                    (self._APT_KEY,     "_apt_spec_id"),
                    (self._AIRLINE_KEY, "_airline_spec_id"),
                    (self._TYPE_KEY,    "_type_spec_id"),
                ):
                    row = conn.execute(
                        "SELECT id_local FROM AgPhotoPropertySpec WHERE key = ?", (key,)
                    ).fetchone()
                    if row:
                        setattr(self, attr, row[0])
        except Exception as exc:
            log.warning("Could not load Lightroom spec IDs from %s: %s", self._path, exc)

    def get_last_spotted(self, registration: str) -> Optional[Tuple[datetime, str, int]]:
        """Return (last_capture_datetime, airport_iata, session_count) for this registration.

        A session is a group of photos taken at the same airport with no gap larger than
        12 hours between consecutive shots. Multiple photos in the same shoot count as one.
        Returns None if never photographed or catalog is unavailable.
        """
        if self._reg_spec_id is None:
            return None
        registration = registration.strip().upper()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT img.captureTime, apt.internalValue AS airport_iata
                    FROM AgSearchablePhotoProperty reg
                    JOIN Adobe_images img ON img.id_local = reg.photo
                    LEFT JOIN AgSearchablePhotoProperty apt
                        ON apt.photo = reg.photo AND apt.propertySpec = ?
                    WHERE reg.propertySpec = ?
                      AND UPPER(TRIM(reg.internalValue)) = ?
                    ORDER BY img.captureTime ASC
                    """,
                    (self._apt_spec_id, self._reg_spec_id, registration),
                ).fetchall()

            if not rows:
                return None

            # Group into sessions: new session when airport changes or gap > 12 hours
            sessions = 0
            prev_dt = None
            prev_airport = None
            for row in rows:
                dt = datetime.fromisoformat(row["captureTime"].split(".")[0].rstrip("Z"))
                airport = (row["airport_iata"] or "").strip()
                if (prev_dt is None
                        or airport != prev_airport
                        or (dt - prev_dt).total_seconds() > 43200):
                    sessions += 1
                prev_dt = dt
                prev_airport = airport

            last_dt = datetime.fromisoformat(rows[-1]["captureTime"].split(".")[0].rstrip("Z"))
            last_airport = (rows[-1]["airport_iata"] or "").strip()
            return last_dt, last_airport, sessions

        except Exception as exc:
            log.warning("Lightroom query failed for %s: %s", registration, exc)
        return None


    def get_aircraft_info(self, registration: str) -> Tuple[str, str]:
        """Return (airline, aircraft_type) from the most recent photo of this registration."""
        if self._reg_spec_id is None:
            return "", ""
        registration = registration.strip().upper()
        try:
            with self._connect() as conn:
                airline = ""
                aircraft_type = ""
                for spec_id, attr in (
                    (self._airline_spec_id, "airline"),
                    (self._type_spec_id,    "aircraft_type"),
                ):
                    if spec_id is None:
                        continue
                    row = conn.execute(
                        """
                        SELECT prop.internalValue
                        FROM AgSearchablePhotoProperty reg
                        JOIN Adobe_images img ON img.id_local = reg.photo
                        JOIN AgSearchablePhotoProperty prop
                            ON prop.photo = reg.photo AND prop.propertySpec = ?
                        WHERE reg.propertySpec = ?
                          AND UPPER(TRIM(reg.internalValue)) = ?
                        ORDER BY img.captureTime DESC LIMIT 1
                        """,
                        (spec_id, self._reg_spec_id, registration),
                    ).fetchone()
                    if row:
                        val = (row["internalValue"] or "").strip()
                        if attr == "airline":
                            airline = val
                        else:
                            aircraft_type = val
                return airline, aircraft_type
        except Exception as exc:
            log.warning("Aircraft info query failed for %s: %s", registration, exc)
            return "", ""

    def get_catalog_stats(self) -> dict:
        """Return aggregate stats across the whole catalog for the /stats command."""
        if self._reg_spec_id is None:
            return {}
        try:
            with self._connect() as conn:
                # Total unique registrations
                unique_aircraft = conn.execute(
                    "SELECT COUNT(DISTINCT UPPER(TRIM(internalValue))) FROM AgSearchablePhotoProperty WHERE propertySpec = ?",
                    (self._reg_spec_id,),
                ).fetchone()[0]

                # Total spotting trips — all photos grouped into sessions by time+airport
                # (one trip = photos at the same airport with no gap > 12h between consecutive shots)
                total_sessions = conn.execute(
                    """
                    WITH all_photos AS (
                        SELECT DISTINCT img.id_local, img.captureTime,
                               COALESCE(TRIM(apt.internalValue), '') AS airport
                        FROM AgSearchablePhotoProperty reg
                        JOIN Adobe_images img ON img.id_local = reg.photo
                        LEFT JOIN AgSearchablePhotoProperty apt
                            ON apt.photo = reg.photo AND apt.propertySpec = ?
                        WHERE reg.propertySpec = ?
                    ),
                    flagged AS (
                        SELECT captureTime, airport,
                               LAG(captureTime) OVER (ORDER BY captureTime) AS prev_time,
                               LAG(airport)     OVER (ORDER BY captureTime) AS prev_airport
                        FROM all_photos
                    )
                    SELECT COUNT(*) FROM flagged
                    WHERE prev_time IS NULL
                       OR airport != prev_airport
                       OR (julianday(captureTime) - julianday(prev_time)) * 86400 > 43200
                    """,
                    (self._apt_spec_id, self._reg_spec_id),
                ).fetchone()[0]

                # Top airports by trip count
                top_airports = conn.execute(
                    """
                    WITH all_photos AS (
                        SELECT DISTINCT img.id_local, img.captureTime,
                               TRIM(apt.internalValue) AS airport
                        FROM AgSearchablePhotoProperty reg
                        JOIN Adobe_images img ON img.id_local = reg.photo
                        JOIN AgSearchablePhotoProperty apt
                            ON apt.photo = reg.photo AND apt.propertySpec = ?
                        WHERE reg.propertySpec = ? AND TRIM(apt.internalValue) != ''
                    ),
                    flagged AS (
                        SELECT captureTime, airport,
                               LAG(captureTime) OVER (PARTITION BY airport ORDER BY captureTime) AS prev_time
                        FROM all_photos
                    )
                    SELECT airport, COUNT(*) AS trips
                    FROM flagged
                    WHERE prev_time IS NULL
                       OR (julianday(captureTime) - julianday(prev_time)) * 86400 > 43200
                    GROUP BY airport ORDER BY trips DESC LIMIT 5
                    """,
                    (self._apt_spec_id, self._reg_spec_id),
                ).fetchall()

                # Per-aircraft session counts (for top_photographed)
                session_rows = conn.execute(
                    """
                    WITH photos AS (
                        SELECT
                            UPPER(TRIM(reg.internalValue)) AS registration,
                            img.captureTime,
                            COALESCE(TRIM(apt.internalValue), '') AS airport,
                            LAG(img.captureTime) OVER (
                                PARTITION BY UPPER(TRIM(reg.internalValue))
                                ORDER BY img.captureTime
                            ) AS prev_time,
                            LAG(COALESCE(TRIM(apt.internalValue), '')) OVER (
                                PARTITION BY UPPER(TRIM(reg.internalValue))
                                ORDER BY img.captureTime
                            ) AS prev_airport
                        FROM AgSearchablePhotoProperty reg
                        JOIN Adobe_images img ON img.id_local = reg.photo
                        LEFT JOIN AgSearchablePhotoProperty apt
                            ON apt.photo = reg.photo AND apt.propertySpec = ?
                        WHERE reg.propertySpec = ?
                    ),
                    sessions AS (
                        SELECT registration,
                            SUM(CASE WHEN prev_time IS NULL
                                          OR airport != prev_airport
                                          OR (julianday(captureTime) - julianday(prev_time)) * 86400 > 43200
                                     THEN 1 ELSE 0 END) AS session_count
                        FROM photos GROUP BY registration
                    )
                    SELECT registration, session_count FROM sessions ORDER BY session_count DESC
                    """,
                    (self._apt_spec_id, self._reg_spec_id),
                ).fetchall()

                top_photographed = session_rows[:5]

                # Top 5 registrations spotted at 2+ distinct airports
                multi_airport = conn.execute(
                    """
                    SELECT reg, COUNT(DISTINCT airport) AS airport_count,
                           GROUP_CONCAT(DISTINCT airport) AS airports
                    FROM (
                        SELECT UPPER(TRIM(reg.internalValue)) AS reg,
                               TRIM(apt.internalValue) AS airport
                        FROM AgSearchablePhotoProperty reg
                        JOIN AgSearchablePhotoProperty apt
                            ON apt.photo = reg.photo AND apt.propertySpec = ?
                        WHERE reg.propertySpec = ?
                          AND TRIM(apt.internalValue) != ''
                    )
                    GROUP BY reg HAVING airport_count >= 2
                    ORDER BY airport_count DESC LIMIT 5
                    """,
                    (self._apt_spec_id, self._reg_spec_id),
                ).fetchall()

            return {
                "unique_aircraft": unique_aircraft,
                "total_sessions": total_sessions,
                "top_airports": [(r[0], r[1]) for r in top_airports],
                "top_photographed": [(r[0], r[1]) for r in top_photographed],
                "multi_airport": [(r[0], r[1], r[2]) for r in multi_airport],
            }
        except Exception as exc:
            log.warning("Catalog stats query failed: %s", exc)
            return {}

    def get_session_count_at_airport(self, registration: str, airport_iata: str) -> int:
        """Count spotting sessions for this registration at a specific airport."""
        if self._reg_spec_id is None or self._apt_spec_id is None:
            return 0
        registration = registration.strip().upper()
        airport_iata = airport_iata.strip().upper()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT img.captureTime
                    FROM AgSearchablePhotoProperty reg
                    JOIN Adobe_images img ON img.id_local = reg.photo
                    JOIN AgSearchablePhotoProperty apt
                        ON apt.photo = reg.photo AND apt.propertySpec = ?
                    WHERE reg.propertySpec = ?
                      AND UPPER(TRIM(reg.internalValue)) = ?
                      AND UPPER(TRIM(apt.internalValue)) = ?
                    ORDER BY img.captureTime ASC
                    """,
                    (self._apt_spec_id, self._reg_spec_id, registration, airport_iata),
                ).fetchall()
            if not rows:
                return 0
            sessions, prev_dt = 0, None
            for row in rows:
                dt = datetime.fromisoformat(row[0].split(".")[0].rstrip("Z"))
                if prev_dt is None or (dt - prev_dt).total_seconds() > 43200:
                    sessions += 1
                prev_dt = dt
            return sessions
        except Exception as exc:
            log.warning("Session count query failed for %s@%s: %s", registration, airport_iata, exc)
            return 0

    def get_all_sessions(self, registration: str) -> List[Tuple[datetime, str]]:
        """Return all spotting sessions as [(session_start_datetime, airport_iata), ...] newest first.

        A session is a consecutive group of photos at the same airport with no gap > 12 hours.
        """
        if self._reg_spec_id is None:
            return []
        registration = registration.strip().upper()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT img.captureTime, apt.internalValue AS airport_iata
                    FROM AgSearchablePhotoProperty reg
                    JOIN Adobe_images img ON img.id_local = reg.photo
                    LEFT JOIN AgSearchablePhotoProperty apt
                        ON apt.photo = reg.photo AND apt.propertySpec = ?
                    WHERE reg.propertySpec = ?
                      AND UPPER(TRIM(reg.internalValue)) = ?
                    ORDER BY img.captureTime ASC
                    """,
                    (self._apt_spec_id, self._reg_spec_id, registration),
                ).fetchall()

            if not rows:
                return []

            sessions: List[Tuple[datetime, str]] = []
            prev_dt = None
            prev_airport = None
            session_start = None
            for row in rows:
                dt = datetime.fromisoformat(row["captureTime"].split(".")[0].rstrip("Z"))
                airport = (row["airport_iata"] or "").strip()
                if (prev_dt is None
                        or airport != prev_airport
                        or (dt - prev_dt).total_seconds() > 43200):
                    session_start = dt
                    sessions.append((session_start, airport))
                prev_dt = dt
                prev_airport = airport

            sessions.reverse()
            return sessions

        except Exception as exc:
            log.warning("Lightroom sessions query failed for %s: %s", registration, exc)
        return []


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
