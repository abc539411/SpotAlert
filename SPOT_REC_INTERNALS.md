# Spot Recommendation — Internal Architecture

This document describes exactly how `spot_recommendation.py` works so that future changes
don't break things by misunderstanding the system.

---

## 1. Two Data Sources

Every spot check begins by producing a list of `FlightEval` objects via one of two functions:

| Function | When used | Data source |
|---|---|---|
| `_evaluate_rolling_flights(cfg, window_start, window_end, sunrise_ts, sunset_ts)` | Today (rolling check, today best, today morning/afternoon/allday, follow-up) | `notification_record` (flights the bot has already fired on) |
| `_evaluate_eod_flights(cfg, tomorrow, sunrise_ts, sunset_ts)` | Tomorrow (EOD recommendation, tomorrow best/allday/morning/afternoon) | `notification_record` scoped to tomorrow midnight→23:59 |

`_evaluate_rolling_flights` returns every tracked flight in the window. It applies:
- Exclusion list check
- Lighting gate (arrival > sunset → filtered with reason "arrives after sunset")
- Max-spotted check (already photographed N times → filtered)

`_evaluate_eod_flights` is just `_evaluate_rolling_flights` with `window_start/end` set to
midnight/23:59 on tomorrow's date. It does NOT call FR24 — it only reads `notification_record`.
**If a flight isn't in `notification_record` yet it won't appear in the EOD check.**

---

## 2. The Pipeline — Always In This Order

```
1. evaluate       → List[FlightEval]  (qualifying=True/False, dep_ts=None)
2. populate_departures → sets dep_ts, dep_fn, dep_time_label in-place
3. apply_pre_sunrise_gate → may move pre-sunrise arrivals to filtered
4. (cluster OR display)
```

Steps 2 and 3 **must not be swapped** — the gate in step 3 depends on dep_ts being set.

---

## 3. Two Display Modes

### Scenario B — Planning paths (automatic)
Used by: rolling check, EOD recommendation, follow-up, today/tomorrow **Best Time to Go**

- Goes through `_cluster_flights` → produces `SpotCluster` objects
- `_build_clusters_message` renders clusters with window times

### Scenario A — Display paths (manual /spot)
Used by: today/tomorrow **All Day / Morning / Afternoon**

- Does NOT go through `_cluster_flights`
- `_build_detail_message(scenario_a=True)` renders a flat list

### `_flight_line` — unified display (both modes)
`_flight_line` always shows **arr HH:MM [emoji] / dep HH:MM [emoji]** regardless of
mode. The `scenario_a` parameter is kept for signature compatibility but no longer
changes behaviour. `arr_lighting_zone` and `dep_lighting_zone` must be set on the
`FlightEval` — they are set in `_cluster_flights` for qualifying flights and for
filtered flights in the cluster/orphaned assignment loop.

`now_ts` is passed in but only used for:
- Window string calculation in `_build_detail_message` (skip past arrivals for "from" time)
- `_render_flights_with_lulls` (skip flights fully in the past in planning paths)
- NOT used to hide flights in the flat list — `shown_qualifying = qualifying` always

---

## 4. `_populate_departures` — Lighting Gate Behaviour

```python
_populate_departures(flights, cfg, sunset_ts=0, sunrise_ts=0)
```

- Always calls `_lookup_departure_for_flight` and sets `dep_fn`, `dep_ts`, `dep_time_label`
- **If `sunset_ts` is provided AND `cfg.spot_rec_lighting_gate` is True:**
  - Clears `dep_ts = None` if `dep_ts > sunset_ts` (after sunset) or `dep_ts < sunrise_ts` (before sunrise)
  - This is correct for **planning paths** (you don't want to include a departure that's in darkness)
  - This is **wrong for display paths** because it clears cross-day departures (a flight
    arriving today at 06:00 whose departure is tomorrow at 09:00 would have dep_ts > today's sunset)
- **Call WITHOUT sunset/sunrise for display paths** (morning/afternoon/allday):
  ```python
  _populate_departures(qualifying + filtered, cfg)  # no gate
  ```
- **Call WITH sunset/sunrise for planning paths** (best, rolling, EOD, follow-up):
  ```python
  _populate_departures(qualifying + filtered, cfg, sunset_ts=sunset_ts, sunrise_ts=sunrise_ts)
  ```

---

## 5. `_lookup_departure_for_flight` — Priority Chain

```
actual_dep_ts  (from FR24 page -1/-2, written by monitor each check)  ← highest priority
turnaround_secs (arrival_ts + turnaround — always date-correct)
estimated_dep_ts (from flight schedule, date-specific)
scheduled_dep_ts (from flight schedule, date-specific)
None
```

**Key rule:** `actual_dep_ts`, `estimated_dep_ts`, `scheduled_dep_ts` are only used if they
are **plausible** relative to `arrival_ts`:
```python
arrival_ts <= ts <= arrival_ts + 36 * 3600
```

This prevents cross-day contamination: if B-207N's stored `actual_dep_ts` is from today
(May 13 08:15) but we're evaluating tomorrow's arrival (May 14 06:15), today's timestamp
is before tomorrow's arrival → fails plausibility → falls through to turnaround.

`turnaround_secs` is computed as `scheduled_dep_ts - scheduled_arr_ts` for the departure
flight. It is **day-agnostic** — it only captures how long the aircraft sits at the airport
between touch-down and take-off, so `arrival_ts + turnaround_secs` gives the correct date
regardless of when the arrival happens.

---

## 6. `_apply_pre_sunrise_gate`

Run **after** `_populate_departures`. For qualifying flights:
- If `arrival_ts >= sunrise_ts` → keep (normal daylight arrival)
- If `arrival_ts < sunrise_ts` AND `dep_ts` is set AND `sunrise_ts <= dep_ts <= sunset_ts`
  → keep (arrives pre-dawn but departs in daylight, e.g. B-207N arr 06:15, dep 08:15)
- Otherwise → move to filtered with reason "arrives before sunrise with no confirmed daylight departure"

The gate is bypassed entirely if `lighting_gate=False` or `sunrise_ts=0`.

---

## 7. `_cluster_flights` — Key Concepts

**Clustering:** Events (arrivals + departures) are sorted by timestamp and greedy-grouped
into clusters where consecutive events are within `max_gap_secs`. Departures are included
in the event list so a flight arriving in one cluster and departing in the next pulls
them together if the gap is small enough.

**Per-cluster FlightEval copy:** Each flight is copied with `dc_replace()` so per-cluster
fields don't leak across clusters (a flight spanning two clusters gets separate copies).

**`arr_in` / `dep_in`:** For each flight in a cluster, whether its arrival/departure
falls within `cluster_start <= ts <= cluster_end`.

**`session_ts`:** The primary event for Scenario B display and ordering.
- `arrival_ts` if arrival is in this cluster
- `dep_ts` if only departure is in this cluster (departure-only entry)

**`show_dep`:** `True` only when BOTH arrival and departure are in this cluster.
Used in Scenario B to show "arr X / dep Y" on the flight line.
Used in `recommended_start_ts` calculation (departure is only a "catchable" event if `show_dep=True`).

**`cluster_end_ts`:**
```python
max(f.dep_ts if f.show_dep and f.dep_ts else f.arrival_ts for f in cluster_flights)
```
Uses departure when show_dep=True (the session ends at departure), otherwise arrival.
This is the "end" timestamp shown in the window header (e.g. "08:15 – 09:15").

**`recommended_start_ts`:** Latest time you can arrive at the airport and still catch every
flight in the cluster. Iterates over all events, checks if all flights are still catchable
(arrival hasn't passed OR show_dep departure hasn't passed), picks the latest valid time.

**Filtered flight assignment:** A filtered flight is assigned to a cluster only if
`cluster.recommended_start_ts <= f.arrival_ts <= cluster.end_ts` — i.e., within the
DISPLAYED window. Using `start_ts` (earliest raw event) would incorrectly include
flights that arrive before the recommended start and would require an earlier arrival. Flights outside all clusters go into
`orphaned_filtered` returned as the second element of the `_cluster_flights` tuple.
`_build_clusters_message` renders orphaned flights as a separate "Filtered out" section.
Rolling/EOD/follow-up callers discard orphaned flights with `clusters, _ = _cluster_flights(...)`.

**Lull detection:** For each flight, use the earliest event the user must be present for:
- If arrival is **catchable** (`arrival_ts >= recommended_start_ts` and `arr_in`): use arrival.
  Departure is excluded — the user is already at the airport for the arrival.
- If arrival is **before** `recommended_start_ts` (user misses it): use departure instead,
  since they can still catch the plane departing.
- If neither arrival nor departure applies (dep-only flight outside range): skip.

This avoids two failure modes:
1. Using departure for a flight whose arrival was caught → splits a break into two shorter ones
2. Ignoring departure for a flight whose arrival was missed → understates the break end point

---

## 8. `_render_flights_with_lulls` — Ordering

Flights are sorted by `arrival_ts`. Lulls are interleaved by inserting a lull line just
before the first flight whose `arrival_ts >= lull_end_ts`. This puts the break between
the last flight before the gap and the first flight after it.

The `now_ts` filter in this function only applies in Scenario B (planning paths), where
fully-past flights (both arrival and departure in the past) are skipped from display.
For Scenario A the filter doesn't matter because Scenario A uses `_build_detail_message`
directly, not `_render_flights_with_lulls`.

---

## 9. Path Summary Table

| Trigger | Day | Data source | `_populate_departures` gate | Display function |
|---|---|---|---|---|
| Rolling check (auto, post-check) | Today midnight→23:59 | `notification_record` | WITH gate | `_render_flights_with_lulls` per cluster |
| EOD job (auto, nightly) | Tomorrow midnight→23:59 | `notification_record` | WITH gate | `_build_clusters_message` |
| Follow-up (after Yes on EOD) | Today now+travel→sunset | `notification_record` | WITH gate | `_render_flights_with_lulls` |
| /spot Today | Today midnight→23:59 | `notification_record` | WITH gate | `_build_clusters_message` |
| /spot Tomorrow | Tomorrow midnight→23:59 | `notification_record` | WITH gate | `_build_clusters_message` |

**Rolling check per-cluster logic:**
- Clusters all of today's flights (full day window, not `now + travel_mins`)
- For each eligible cluster (≥ threshold): fires if `travel_mins ≤ notify_gap ≤ notify_window_hours`
  AND any flight in cluster has `cluster_notified_ts IS NULL`
- On send: calls `mark_cluster_notified()` for all flights in the cluster
- Re-fires if a new flight joins the cluster (its `cluster_notified_ts` will be NULL)
- No global cooldown — per-cluster tracking replaces `SPOT_REC_ROLLING_LAST_TS`
- `spot_rec_max_windows` NOT applied to rolling check (all eligible clusters fire independently)

**`cluster_notified_ts`** stored in `notification_record`, populated into `FlightEval` by
`_evaluate_rolling_flights`. Updated by `store.mark_cluster_notified(registrations, ts)`.

---

## 10. Common Failure Modes

| Symptom | Root cause | Fix |
|---|---|---|
| Pre-sunrise arrival filtered despite daylight dep | `_populate_departures` called WITH gate on display path; dep_ts cleared because cross-day departure > today's sunset | Call `_populate_departures` without gate params on display paths |
| Departure time shows wrong date (e.g. yesterday) | `actual_dep_ts` or `estimated_dep_ts` stored from a different day passes plausibility check | Plausibility check: `arrival_ts <= ts <= arrival_ts + 36h` |
| Break time shows departure mid-gap | Lull detection used dep_ts as an event, splitting the break | Lull detection uses arrival_ts only (dep_ts only for dep-only flights) |
| Window end shows arrival instead of departure | `cluster_end_ts` used `session_ts` which is always `arrival_ts` for flights with arrival in cluster | Use `dep_ts if show_dep else arrival_ts` for `cluster_end_ts` |
| Flight missing from allday display | Old filter `e.arrival_ts >= now_ts or e.dep_ts is not None` hid past arrivals with no dep data | Removed filter — all qualifying flights shown in display paths |
| Force Check crash (`NoneType has no attribute 'data'`) | `asyncio.create_task(_run_check(context))` gives context with `context.job = None` | `chat_id = context.job.data if context.job else cfg.chat_id` |
