# TSW Hud — session handover

**App version at end of session: 7.38.0**

Read `TSW_HUD_NEW_CHAT_SPEC.txt` first (the canonical spec), then this.
`TIMETABLE_EXTRACTION_FINDINGS.md` has the full detail on the timetable
work and should be read before touching any of it.

---

## How to work with me on this project

- **Results only, brief summary at the end.** No commentary or narration
  while working.
- Version bump on every change: MAJOR.MINOR.PATCH. Bug fix = patch.
- Always repackage to `TSW Hud.zip` and present the file.
- Exclusions when zipping: `config.json`, `__pycache__`, `*.pyc`,
  `extracted/`.
- I test everything on a real Windows machine with TSW6 and report back
  precisely. Treat my corrections about train/timetable behaviour as
  authoritative — I know the domain, you don't.
- **Verify before shipping.** Several bugs this session came from
  assumptions that a quick check would have caught. Render/screenshot UI
  changes, run the code against synthetic data, don't eyeball geometry.

---

## What changed in v7.38.0 - StopPoint identification

The blocker named as "next step" in the findings doc is solved.
`find_stop_points()` in `pak_tools.py`, `/api/paks/stops`, and a **Find stop
points** button next to Extract services on Discovery.

It separates real station calls from the 120-151 simulated track points per
service by resolving each record's FName references against the name table in
the sibling `.uasset`, classifying every time TWICE - once by station
reference, once by type enum - and cross-checking the two.

**Validated against synthetic records of known layout only** (`tests/`), NOT
against a real pak. On the fixture it recovers the name-table shift exactly,
finds 41-44 stops among 182 track points across 8 stations with
arrival/departure pairs intact, and the two classifications agree 98%. It also
correctly refuses two negative controls: random bytes with planted times, and
a file where the type is a raw byte rather than an FName.

**Next thing to do: run it on the real Leven Branch layer.** Extract & inspect,
then Find stop points. The number worth checking is `corroboration` - if the
two independent classifications agree on the real file the way they do on the
fixture, this is done and the remaining work is writing to `timetables.db`.

Eight failed approaches are documented in the findings doc. Every one of them
produced a confident, entirely wrong answer, which is why the scoring is as
defensive as it is - please read that table before changing any of it.

## What changed in v7.37.0 (spec-drift cleanup)

Housekeeping only, no new features.

- **Known Trains is now actually driven-only** (`times_seen > 0`). Spec 3C had
  required this since 6.x but it was never implemented — the filter was on
  variants and visibility only. `list_train_classes(driven_only=)` and
  `needs_attention(driven_only=)` both gained it; `/api/known_trains/list`
  passes True to both, and they must stay in step.
- **Removed everything spec section 3 excludes**, which had been sitting in the
  tree for several versions: `sw.js` + its Flask route, `offline-db.js`,
  `sync-client.js`, `pages/icons/`, `/api/sync/changes`, `/api/sync/push`,
  `certs/`, `HTTPS_SETUP.md`, `/api/https_cert_status`, `get_ssl_context()`,
  `get_https_cert_status()`, and `cryptography` from requirements.
  `run_flask()` is plain HTTP unconditionally now.
- `timetables_browser.html` was the last page still on the sync layer — it now
  PATCHes directly. `timetable.html` lost a dead `install-prompt.js` tag that
  404'd on every load.
- `get_changes_since()` is kept in both DB modules even though nothing calls
  it: it carries the keyset-pagination fix and costs nothing to leave.
- **Zip no longer ships `data/*.db` or `diagnostics/*.log`.** The databases
  were empty, so extracting over a real install would have wiped Known Trains.
- Spec doc updated from 6.1.3 to reality: families/operators/liveries,
  variants, the analogue speedometer angle conventions, the current route list
  (`/api/train_classes` and `train_classes.html` no longer exist), and the
  Family → Class → Subclass → Entry terminology vs the unchanged API paths.
- Nav gaps (Classes/Groups/Operators/Customisation absent from
  `registry.json`) left alone deliberately, and now documented as such.

## What changed in v7.36.0

### Known Trains / Operators pill redesign
- Pills 156px tall, 420px thumbnails, full width, image on the **right**.
- Coloured edge is a **solid layer** (`.pill-wrap` background), with the
  inner pill's `border-radius` carving the concave curve. Do NOT rebuild
  this as a hard-stopped gradient — a colour stop creates a square edge
  that no radius can fix. Left fade uses `mask-image` (opacity only, so it
  can't introduce a hard edge).
- Grey hairline border sits behind the coloured layer, masked to fade out
  in step with it.
- Pill background = livery colour at 20% opacity.
- **Pill colour comes from the LIVERY, not the operator.** Server resolves
  it per train in `/api/known_trains/list`.
- Operator logo lookup uses the operator's `short_code` (`/company_logos/
  <code>.png`) so it works regardless of livery. An earlier bug had the
  edit page reading a `logo_path` field nothing ever populated.

### Classes / Groups / Variants
- Old "Groups" page renamed **Classes** (`classes.html`); all user-facing
  text says Class. API paths still say `groups` — deliberately unchanged.
- New **Groups** page (`groups.html`) = families. Several Classes belong to
  one Group, e.g. Class 801/802/805 under "Class 8xx". Backed by a new
  `class_families` table and `family_id` on `loco_groups`.
- Known Trains groups pills by **family name** when set, else class name.
- **Variants**: attach a train as a variant of another. Non-destructive and
  fully reversible — the row is tagged `variant_of_class_id`, hidden from
  Known Trains, and reappears intact when removed. Variant dropdown lists
  only ungrouped trains. Display name/speedometer resolve to the PARENT.
- `needs_attention` / completion = display name, operator, livery, group,
  power. **Not** subclass, **not** photo.

### Analogue speedometer (dashboard)
Read the angle conventions before touching this — two long-lived bugs
lived here.

- The gauge SVG is rotated **-90° by CSS**. Everything is authored in SVG
  space and lets that rotation do the final turn.
- Value → SVG angle: `A = 224.5 + (v/dialMax)*271`, clockwise from east.
- An element drawn pointing UP (max-speed tick, needle) sits at SVG 270,
  so placing it at value v needs `rotate(A - 270)`. Using `rotate(A)` put
  the max-speed line 270° out — it looked "stuck in the same place" for
  several iterations.
- `polarPoint()` must NOT subtract an extra 90; doing so rotated the whole
  number ring a quarter turn.
- Geometry is a direct transcription of the approved mockup, scaled 50/460.
  Do not round these to "tidier" numbers.
- Numbers in 10s (5s when dial ≤50, with 1mph minor ticks).
- Digital readout is a real 7-segment display (individual bars, ghost
  segments always shown), 3 digits, no decimal point, bordered panel.
- Ring is split: normal segment up to max speed coloured by **speed limit**;
  a second segment covers only the portion **past** max speed, in red.
- Digital speedometer is disabled dashboard-wide but all its code is
  retained — `setSpeedometerMode('analogue')` is forced in `pollLoco`.

### Serving / infrastructure
- `/pages/<file>` now sends `Cache-Control: no-cache, no-store,
  must-revalidate`. Without it WebView2 served stale CSS/JS and changes
  appeared not to apply — this caused a "that didn't work" round trip.
- `pollLoco` interval 10s → 2s so train changes update without a refresh.
- `.gitattributes` added (CRLF for `.bat`/`.cmd`/`.ps1`, LF for source).

### Live journey data (works today, no extraction)
`/api/journey` returns:
- `service_name` — the live headcode, from `DriverAid.PlayerInfo`
  (`currentServiceName`, e.g. "1A10")
- `stations[]` / `markers[]` — from `DriverAid.TrackData`, each with
  `stationName`, `distanceToStationCM`, `platformLength`
- `next_stop` — nearest upcoming

**Not yet surfaced on the HUD.** Low-effort, high-value next task.

---

## Timetable extraction — state of play

**See `TIMETABLE_EXTRACTION_FINDINGS.md` for full detail.** Summary:

Goal: this app does everything itself. The other app ("TSW HUD & Timetable
Extractor") is a **reference only** — never a runtime data source.

Confirmed:
- Paks are **not encrypted**. `repak` reads them.
- Layout: `<install>/WindowsNoEditor/TS2Prototype/Content/DLC/*.pak`
- Timetables live in small `*_Route_Gameplay` plugins, and **loco DLCs add
  timetables to existing routes** (Fife Circle's Sprinter Express ships in
  `BRClass158.pak`). Scanning one route pak is not enough.
- Detect by **folder** (`Timetable/`, `Timetables/`, `ServiceMode/`), not
  by the `_TT` suffix — it isn't universal and produces false positives.
- Current inventory: **43 paks, 72 timetables.**
- Type is `RouteTimetableDataTrackStream` → `ServiceDataTracks` →
  `RouteTimetableTrackData`, with `ETimetableTrackDataType::StopPoint |
  ActionPoint | GoVia | ReversePoint | TrackSectionEntry/Exit`.
- Times are `FTimespan` = int64 of 100ns ticks. **Mostly sub-second**, so
  do not filter for whole seconds.
- The master `_TT` asset is an index with **no .uexp**. The DataTracks hold
  the data (8.6 MB / 26.3 MB .uexp).

**Achieved:** `extract_time_series` segments the Leven Branch layer into
**104 services, roughly hourly, ~50 min each** — a genuine timetable.

**Next step:** the per-service counts (120–151) are TRACK POINTS, not
stops. Find the `ETimetableTrackDataType` field to filter to `StopPoint`,
then join to the station names already recovered from the name table.

**Six approaches already failed** — the findings doc lists them with
reasons. Do not repeat them, particularly the fixed-stride assumption
(the type is a *Stream*, i.e. variable length).

### Domain rules (from me, authoritative)
- First stop: departure only, no arrival.
- Last stop: arrival only, no departure.
- Freight: often no scheduled arrivals at all.
So arrival/departure are **optional**. A single time is correct data, not
a parse failure.

---

## Tooling built (Discovery page)

| File | Does |
|---|---|
| `game_files.py` | finds the TSW install and pak folders, detects repak |
| `pak_tools.py` | repak wrapper: list, inspect assets, scan/decode times, diff records, extract services |

Endpoints: `/api/paks/{repak,list,timetables,scan_all,inspect,timespans,
analyse,diff,decode,services,unpack,clear_extracted}`,
`/api/timetable/{scan,find_exports}`, `/api/journey`, `/api/gamefiles/scan`

Extraction output goes to `<app>/extracted/`. **Clear it between runs** —
stale files caused a misdiagnosis once.

---

## Known outstanding

- Live service code / next stop not shown on the HUD yet.
- The TSW API has a **subscription** endpoint (`POST /subscription/<path>?
  Subscription=1`, then one `GET`). Would replace 4× 300ms polling and
  likely stop the dropped connections that show up as HTTP 502. **A 502 is
  a dropped connection, not a missing path** — always retry before
  concluding something doesn't exist.
- Ammeter, brake gauges, GSM-R panel: approved, not built.
