"""
One-off script: download ICAOList.csv and seed aircraft_type_cache in the DB.
Run once; safe to re-run (existing user entries are preserved).

Usage:
    python _seed_icao_types.py
"""
import csv, io, sqlite3, urllib.request
from pathlib import Path

DB  = Path(__file__).parent.parent / "config" / "filters" / "spotalert.db"
URL = "https://raw.githubusercontent.com/rikgale/ICAOList/main/ICAOList.csv"

print(f"Downloading {URL} …")
with urllib.request.urlopen(URL, timeout=30) as resp:
    text = resp.read().decode("utf-8-sig")

conn = sqlite3.connect(str(DB))
conn.execute("""
    CREATE TABLE IF NOT EXISTS aircraft_type_cache (
        icao   TEXT PRIMARY KEY,
        name   TEXT NOT NULL,
        source TEXT DEFAULT 'icaolist'
    )
""")

reader = csv.DictReader(io.StringIO(text))
rows = list(reader)
print(f"  {len(rows)} rows in CSV")

inserted = updated = 0
for row in rows:
    icao = (row.get("Aircraft TypeDesignator") or "").strip().upper()
    mfr_model = (row.get("MANUFACTURER, Model") or "").strip()
    if not icao or not mfr_model:
        continue
    # Format: "AIRBUS, A-319NEO" → "Airbus A319neo"  (keep as-is, just title-case the mfr part)
    if "," in mfr_model:
        mfr, model = mfr_model.split(",", 1)
        name = f"{mfr.strip().title()} {model.strip()}"
    else:
        name = mfr_model.title()

    existing = conn.execute(
        "SELECT source FROM aircraft_type_cache WHERE icao=?", (icao,)
    ).fetchone()
    if existing:
        if existing[0] == 'user':
            continue  # never overwrite user entries
        conn.execute("UPDATE aircraft_type_cache SET name=?, source='icaolist' WHERE icao=?", (name, icao))
        updated += 1
    else:
        conn.execute("INSERT INTO aircraft_type_cache(icao, name, source) VALUES(?,?,'icaolist')", (icao, name))
        inserted += 1

conn.commit()
conn.close()
print(f"Done — inserted: {inserted}  updated: {updated}  user entries preserved")

# Quick check
conn2 = sqlite3.connect(str(DB))
for code in ("B738", "B789", "A333", "A320", "DH8D", "SF34"):
    r = conn2.execute("SELECT name FROM aircraft_type_cache WHERE icao=?", (code,)).fetchone()
    print(f"  {code}: {r[0] if r else '(not found)'}")
conn2.close()
