import sqlite3
from datetime import datetime

conn = sqlite3.connect("config/filters/spotalert.db")
conn.row_factory = sqlite3.Row

def ts(val):
    return datetime.fromtimestamp(int(val)).strftime("%Y-%m-%d %H:%M") if val else "N/A"

print("=== rare_plane_history ===")
rows = conn.execute(
    "SELECT airline, aircraft_type, last_notified_ts FROM rare_plane_history ORDER BY last_notified_ts DESC"
).fetchall()
print(f"Total: {len(rows)} entries\n")
print(f"{'Airline':<10} {'Type':<8} {'Last Notified'}")
print("-" * 38)
for r in rows:
    print(f"{r['airline']:<10} {r['aircraft_type']:<8} {ts(r['last_notified_ts'])}")

print()
print("=== special_livery_history ===")
rows2 = conn.execute(
    "SELECT registration, last_notified_ts FROM special_livery_history ORDER BY last_notified_ts DESC"
).fetchall()
print(f"Total: {len(rows2)} entries\n")
for r in rows2:
    print(f"  {r['registration']:<12} {ts(r['last_notified_ts'])}")

print()
print("=== notification_record (follow-up tracking) ===")
rows3 = conn.execute("""
    SELECT registration, flight_number, notif_type,
           original_arr_ts, arrival_ts, first_notified_ts, reminder_sent, last_seen_ts
    FROM notification_record
    ORDER BY arrival_ts ASC
""").fetchall()
print(f"Total: {len(rows3)} entries\n")
if rows3:
    print(f"{'Rego':<12} {'Flight':<8} {'Type':<24} {'Arr (orig)':<17} {'Arr (curr)':<17} {'Reminder'}")
    print("-" * 95)
    for r in rows3:
        print(
            f"{r['registration']:<12} {(r['flight_number'] or ''):<8} {(r['notif_type'] or ''):<24} "
            f"{ts(r['original_arr_ts']):<17} {ts(r['arrival_ts']):<17} {'yes' if r['reminder_sent'] else 'no'}"
        )

print()
print("=== rego_watchlist ===")
rows4 = conn.execute("SELECT airline, registration, description FROM rego_watchlist").fetchall()
print(f"Total: {len(rows4)} entries")
for r in rows4:
    print(f"  {r['airline']:<8} {r['registration']:<12} {r['description']}")

print()
print("=== type_watchlist ===")
rows5 = conn.execute("SELECT airline, aircraft_type FROM type_watchlist").fetchall()
print(f"Total: {len(rows5)} entries")
for r in rows5:
    print(f"  {r['airline']:<8} {r['aircraft_type']}")

print()
print("=== airline_watchlist ===")
rows_aw = conn.execute("SELECT icao_code, entry_type, name FROM airline_watchlist ORDER BY id").fetchall()
print(f"Total: {len(rows_aw)} entries")
for r in rows_aw:
    print(f"  {r['icao_code']:<8} {r['entry_type']:<10} {r['name'] or ''}")

print()
print("=== exclusion_list ===")
rows6 = conn.execute("SELECT airline, registration, description FROM exclusion_list").fetchall()
print(f"Total: {len(rows6)} entries")
for r in rows6:
    print(f"  {r['airline']:<8} {r['registration']:<12} {r['description']}")

print()
print("=== military_history ===")
rows8 = conn.execute(
    "SELECT registration, last_notified_ts FROM military_history ORDER BY last_notified_ts DESC"
).fetchall()
print(f"Total: {len(rows8)} entries")
for r in rows8:
    print(f"  {r['registration']:<12} {ts(r['last_notified_ts'])}")

print()
print("=== app_settings (bot-managed overrides) ===")
rows7 = conn.execute("SELECT key, value FROM app_settings ORDER BY key ASC").fetchall()
print(f"Total: {len(rows7)} entries")
for r in rows7:
    print(f"  {r['key']:<40} {r['value']}")

conn.close()
