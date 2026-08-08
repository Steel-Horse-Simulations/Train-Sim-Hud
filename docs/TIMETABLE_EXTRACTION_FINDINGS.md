# Offline timetable extraction — findings

Everything below is **confirmed against a real TSW6 install**, not inferred.
Written up so this doesn't have to be rediscovered.

## Verdict

The full offline timetable data **is present and readable**. Times are
recovered, segmented into services, and station stops are now separated from
the simulated running times around them. What remains is writing them into
`timetables.db` as journeys and stops.

IMPORTANT CAVEAT ON THIS DOCUMENT: everything in sections 1-8 is confirmed
against a **real TSW6 install**. The StopPoint work at the end has now had ONE
real run (Leven Branch layer) - see "First real run" below. The times and
segmentation look right; the name resolution is not yet trustworthy.

## 1. The live API does NOT carry timetables

`DriverAid.Data` has speed limits and signals but no schedule. Confirmed
against real captures. What the API *does* give, with no extraction:

| Path | Gives |
|---|---|
| `DriverAid.PlayerInfo` | `currentServiceName` — the live headcode, e.g. `1A10` |
| `DriverAid.TrackData` | `stations[]` / `markers[]` with `stationName`, `distanceToStationCM`, `platformLength` |

Both are exposed by `/api/journey` (app.py). Note `DriverAid.PlayerInfo`
returns HTTP 502 under rapid polling — that is a **dropped connection, not
a missing path**. Probes must retry before concluding anything.

There is also a **subscription API** (`POST /subscription/<path>?Subscription=1`,
then one `GET /subscription/`) which would replace the current 4× 300ms
polling and likely stop the connection drops. Not yet implemented.

## 2. Install layout

```
<Steam>/steamapps/common/Train Sim World 6/
  WindowsNoEditor/TS2Prototype/Content/DLC/*.pak
```

The `WindowsNoEditor` level is easy to miss. Do not hard-code the layout —
`game_files.py` searches for folders containing `.pak`.

## 3. Extraction

Uses **repak** (github.com/trumank/repak) — handles the Oodle compression.
**The paks are NOT encrypted**; no AES key needed. This is the same tool the
"TSW HUD & Timetable Extractor" app uses (a Tauri app calling
`extractor_run_pak` etc).

## 4. Where the timetables live

**Not** in the huge route plugin. In a small sibling `*_Route_Gameplay`
plugin:

```
FifeCircle_Route_Gameplay/Content/Timetable/
  FCE_Timetable_TT.uasset                 <- INDEX ONLY, no .uexp
  DataTracks/
    FCE_Timetable_TT_MasterDataTrack.uasset      + 26 MB .uexp
    FCE_Timetable_TT_<Group>_Layer_DataTrack     + 8 MB .uexp each
  Formations/<Class>/FRM_*.uasset
```

**Loco DLCs also add timetables to existing routes** — e.g. `BRClass158.pak`
contains `158_FCL_Timetable` (Fife Circle) and `158_E2G_Timetable`
(Edinburgh–Glasgow). So "timetables for route X" means scanning **all**
paks, not just that route's. Some routes keep them under
`Scenarios/ServiceMode/` instead of `Timetable/`.

Detection must key off **folder location**, not filename:
- A `.uasset` directly in `Timetable/`, `Timetables/` or `ServiceMode/`.
- The `_TT` suffix is **not universal** (`CLI_EMKTimetable`,
  `ATS_CREMANTimetable_Su1` have none) and produces false positives
  (`ART_TT.uplugin`, `en_TT.res` = Trinidad locale data, `T_uc_TT` = a font).
- Exclude `Core/Assets/HUD/MenuScreens/Timetable/` — that is the game's
  timetable *menu UI*, 23 widgets.

Current scan: **43 paks, 72 timetables**.

## 5. The schema

```
RouteTimetableDefinition          (the _TT index asset)
RouteTimetableDataTrackStream     (the DataTrack assets — where data lives)
  └─ ServiceDataTracks → RouteTimetableTrackData
       ETimetableTrackDataType:: StopPoint | ActionPoint | GoVia
                                 ReversePoint | TrackSectionEntry/Exit
                                 MultiOccupancy
  ERouteTimetableServiceInstructionType:: GoTo | Couple | Uncouple | LoadUnload
  EInstructionScheduledTimeTypes:: Explicit | Simulated
  properties: Time, Timespan, ArrivalTime, CompletionTime,
              Destination, DestinationDisplayName, EDirectionOfTravel
```

From the index asset's name table: **208 service headcodes** (`1E01`, `1R03`,
with `_End` variants) and **88 station names with platform numbers**
(`Aberdour Platform 1`, `Edinburgh Waverley 1a`…).

## 6. Times are FTimespan int64 ticks

No readable time strings — `time_count` is always 0, and that is expected.
`FTimespan` = int64 count of 100-nanosecond ticks. Scanning for int64s that
land on whole seconds within 24h recovers them:

| Asset | .uexp | Times found | Ascending |
|---|---|---|---|
| Leven Branch layer | 8.6 MB | 133 | **86%** |
| MasterDataTrack | 26.3 MB | 406 | **78%** |

Random data sits at ~50%, so these are genuine schedule values. Recovered
times look like a real service day (05:21 → 18:00+), clustering around
morning and evening peaks.

**Note on spacing:** hits average ~65,000 bytes apart. An early version of
the cluster test required them within 128 bytes and therefore reported every
genuine time as "isolated coincidence" — nearly discarding a correct result.
The gap is now scaled to hit density, and the ascending-ratio test is the
primary evidence.

## 7. Decision: build our own parser

Confirmed direction from the user: **this app must do everything itself.**
No runtime dependency on the other app's database or its JSON exports. That
app is a reference for understanding the format only - not a data source.

The export-import path (`/api/timetable/find_exports`) therefore exists only
as a fallback/diagnostic, not the plan.

## 8. What remains

Associating times with services and stops: walking the `ServiceDataTracks`
records rather than scanning values in isolation. This means decoding
Unreal's property serialisation for a **custom DTG type**
(`RouteTimetableDataTrackStream`). "Stream" in the name suggests a custom
`Serialize()` writing raw binary rather than self-describing tagged
properties — so expect to reverse-engineer the record layout from the byte
patterns around known values, not to walk a standard property tree.

This is real work and should not be under-estimated. But it is now
*bounded* work: the data is confirmed present, unencrypted and decodable.

## Reverse-engineering the record layout

`/api/paks/analyse` (pak_tools.analyse_records) dumps the bytes around each
recovered time to work out the record structure from evidence:
  - hex/ASCII window either side of every time
  - any OTHER FTimespan within the window - a StopPoint should carry both an
    arrival and a departure, so a nearby second time is a strong signal
  - small int32s nearby, which in Unreal are usually FName table indices
    (i.e. the station name) or enum/platform values
  - the distribution of gaps between times across the whole file: a
    repeating stride means fixed-size records, which is far simpler to
    parse than a variable-length stream

Validated against synthetic records of known layout: it correctly recovered
the arrival->departure gap, the record stride, and the FName index position.

## Domain rules that constrain the parser

From the user (real TSW behaviour, not inferred):
  - The **first stop** of a service has a departure time but **no arrival** -
    the train is already there.
  - The **last stop** has an arrival but **no departure**.
  - **Freight** services often have **no scheduled arrival times** at all;
    they are not timetabled the way passenger services are.

So arrival and departure are both OPTIONAL fields. A stop with a single
time is correct data, not a parse failure - do not treat unpaired times as
a fault. The analyser also looks for 0 / -1 / int64-max values adjacent to
a time, since an absent time is likely stored as a sentinel, and that
marker is precisely what identifies first/last stops and freight workings.

## Services successfully segmented (real result)

`extract_time_series` on the Leven Branch layer returns **104 ascending
runs from 5141 times**, with this shape:

```
06:04 -> 06:55   50.6 min      12:06 -> 12:56   49.5 min
08:08 -> 08:58   49.8 min      14:08 -> 15:00   51.6 min
09:07 -> 09:58   50.6 min      17:52 -> 18:42   49.3 min
10:03 -> 10:56   52.5 min      19:07 -> 19:58   50.4 min
11:03 -> 11:56   52.4 min      20:05 -> 20:54   48.8 min
```

Roughly hourly departures, each ~50 minutes end to end. That is a real
branch-line timetable and is checkable against the game.

**The per-run counts (120-151) are TRACK POINTS, not station stops.** The
Leven branch has only a handful of stations. These are the simulated
running times along the route - consistent with
`EInstructionScheduledTimeTypes::Simulated`. Station stops will be the
subset flagged `ETimetableTrackDataType::StopPoint`.

### Approaches that were tried and FAILED - do not repeat

| Attempt | Why it failed |
|---|---|
| Cluster times within 128 bytes | Real hits average ~65,000 bytes apart |
| Require exact whole seconds | Discarded ~90% of times (most are sub-second) |
| Fixed 2828-byte stride | 2828 was only the GCD of sampled gaps. Alignment held ~20 records then drifted (1321 values, 48 distinct, 3% ascending). The type is a **Stream** = variable length |
| Read field at `k*stride+0` | Record phase is 372/2644, not 0 |
| `>=` for the ascending test | Equal values scored ~100%; a run of identical zeros looked like a perfect result. Must be strict `>` and paired with a distinct-value count |
| 4-byte scan stride | Missed 3 of every 4 values in a variable-length stream. Scan every byte |

### Next step

Associate each StopPoint with its service and write journeys+stops into
`timetables.db`. The classification itself is solved - see below.

## Finding the StopPoint marker - SOLVED (v7.38.0)

`find_stop_points()` in `pak_tools.py`, endpoint `/api/paks/stops`, button
**Find stop points** on the Discovery page.

### The approach

Deliberately does NOT start by hunting for the enum byte. The earlier record
diffs reported the type field as CONSTANT precisely because every record
sampled happened to be the same type, so "find the byte that varies" begs the
question. It starts instead from things identifiable on their own terms - the
FName table - and derives the classification:

1. Read the name table from the sibling `.uasset`; mark which entries look
   like stations and which are `ETimetableTrackDataType` members.
2. Recover the constant SHIFT between our recovered table position and the
   FName index written in the stream (the string scan starts partway through
   the header, so the two differ by an unknown constant).
3. Classify each time TWICE, independently: by station reference, and by type
   enum. Cross-check the two. Agreement is the evidence; disagreement is
   reported rather than averaged away.

Both FName encodings are tried - Unreal's `int32 index + int32 Number` pair,
and a bare index - and whichever separates better from the null model wins.
The strict form is preferred unless the loose one beats it by 25%, because a
bare index accepts every match the strict form does plus a lot of noise.

### Scoring: three measurements, and why each exists

Each was added because the previous set produced a CONFIDENT WRONG ANSWER.
That is the recurring failure mode here and it is why the scoring looks
over-engineered - every part of it is load-bearing.

| Measure | What it catches |
|---|---|
| **rate per position**, counted by distinct offset | Raw vote counts let a few thousand repeated padding values outvote a field appearing once per record |
| **unique coverage** - is the hit's offset near-unique to its record | One stray byte within radius of many times reports a hit on every record from a single shared offset, which looks exactly like a real field |
| **delta concentration** - fraction of hits in the best 32-byte distance band | The strongest of the three. A real field sits a consistent distance from its own time even in a variable-length stream; coincidences scatter uniformly |

Scored against a null model of control positions drawn from the anchors'
**immediate neighbourhood in the table**, not at random from all of it - a
random control maps to values of a different magnitude than the anchors do.

The dominant delta band is then used a second time, as a filter: a hit outside
it is a coincidental value that happened to resolve to a valid index.

### Validated against synthetic records of known layout

`tests/synth_stoppoints.py` builds a `.uasset`/`.uexp` pair with the awkward
parts of the real thing reproduced - variable-length records, a header offset
so position != index, stations on a minority of records, first stop
departure-only, last stop arrival-only, one freight service with no arrivals,
and realistic spacing. `tests/eval_stoppoints.py` scores against ground truth.

Result on the fixture: **shift recovered exactly, 41-44 stops identified out of
182 track points, 8 distinct stations, arrival/departure pairs intact, 98%
agreement between the two independent classifications.**

Two negative controls matter as much as the positive one, because every wrong
answer during development was a confident one:
  - **random bytes with planted times** - must not confirm. It doesn't.
  - **type written as a raw BYTE, not an FName** (the case section 8 predicted
    as likely) - must find the stations, report that the enum is not an FName,
    and NOT invent types. It does.

### Approaches that were tried and FAILED here - do not repeat

| Attempt | Why it failed |
|---|---|
| Vote on the shift by raw hit count | Padding values recur thousands of times; multiplied across 88 stations they outvoted the real field. Picked a shift 4 short of the truth |
| Count hits rather than distinct offsets | One stray byte position near many times scored as a field present on every record |
| Random control sample for the null model | Controls mapped to values of a different magnitude than the anchors, so any shift mapping anchors onto small numbers won on padding alone (chose -912 over -6) |
| Leaving value 0 in | Whichever position maps to 0 hits 100% of records at a unique offset every time - the exact signature being looked for. Beat the true shift twice |
| Anchoring only on the enum | When the type is a raw byte the enum resolves nowhere, so it chased a bogus shift and invented 18 StopPoints in a file containing none. Both families must be scored |
| Multiplying the station score by 4 | An unprincipled fudge to "compensate" for stations being on a minority of records. Let a bogus shift win by finding 142 stations among 182 track points. The lower coverage is the truth, not an artefact |
| Ranking candidate shifts by vote count | The true shift never made the shortlist and was never scored at all |
| Statistical scoring alone | Top two candidates differed by 0.02, and the winner was one position out - mapping the enum onto its neighbouring member. Only cross-family corroboration separates them |

### A note on the test fixture itself

Two fixture bugs each produced a misleading failure, so the fixture's realism
is part of the test:
  - Records packed ~50 bytes apart meant a +/-192 byte window spanned six
    records, so every record appeared to carry four different types. Real
    times average ~65,000 bytes apart.
  - Scaling a record's INTERNAL padding to match put the type field thousands
    of bytes from its own time, outside any sane window. Records are far
    apart; a record's own fields are not.
  - A 30-entry name table made the problem unsolvable in principle rather than
    merely hard: every small padding int was a valid index, so padding and a
    genuine reference were literally the same bytes. Real tables have
    thousands of entries.

## Tooling in the app (Discovery page)

- `game_files.py` — install/pak discovery, repak detection
- `pak_tools.py` — repak wrapper, listing, asset inspection, timespan scan
- Endpoints: `/api/paks/{repak,list,timetables,scan_all,inspect,timespans,clear_extracted}`
- Extraction output goes to `<app>/extracted/` — **clear it between runs**,
  stale files caused one misdiagnosis already.

## First real run - Leven Branch layer (v7.38.0 output)

`FCE_Timetable_TT_Leven_Branch_Layer_DataTrack.uexp`, 5141 times, 104 services.

### What looks right

The SHAPE is a real Fife Circle timetable and does not depend on the name
resolution at all:

  - stops per service: median 8, range 2-19. The longest, 15 stops over 74.8
    minutes (12:17 -> 13:32), is exactly a Leven -> Edinburgh run.
  - 137 of 297 consecutive-stop intervals are <= 90 seconds - arrival and
    departure at the same station, the pairing the domain rules predict.
  - the remaining intervals have a median of 5.6 minutes, which is
    station-to-station running time on this route.
  - 461 StopPoints selected out of 5141 times, so ~9% of track points are
    station calls. That is the right order of magnitude.

### What was WRONG - and it reported `confirmed: true`

The winning shift was **8743**, which puts `StopPoint` at FName index 8765.
**The name table has 88 entries.** There is no index 8765. The result was
impossible on its face and the tool called it confirmed.

Worse, it was a four-way tie:

| shift | score | StopPoint index | |
|---|---|---|---|
| 8743 | 0.9167 | 8765 | impossible |
| 1844 | 0.9167 | 1866 | impossible |
| 1843 | 0.9167 | 1865 | impossible |
| 1842 | 0.9167 | 1864 | impossible |
| **-6** | 0.9165 | **16** | the only plausible one - came FIFTH, by 0.0002 |

Every statistical measure was maxed out and identical (`enum_concentration`
1.0, `enum_unique_coverage` 1.0, `enum_rate` 0.1429 = exactly 1/7 across seven
enum members, which is the correct signature of a real one-per-record field).
The statistics were not wrong; they simply cannot tell these apart, and
nothing was checking whether the answer was even *possible*.

**Fixed in v7.38.1:**
  - a shift is only considered if it resolves EVERY anchor to an index that
    exists in the name table. This is a hard constraint of the format, not a
    heuristic.
  - ties are reported (`tied_shifts`) and suppress `confirmed` rather than
    being silently broken by sort order.
  - `names_sample` is returned so the table can actually be looked at.

### The real blocker now: no station names in this asset

`name_count: 88`, `station_names: 0`, `with_station: 0`. This layer's name
table has no station-shaped entries, so:
  - there is nothing to corroborate the enum classification against, which is
    exactly why the tie could not be broken;
  - the stops have times but no labels.

Section 5 records that the 88 station names and 208 headcodes came from the
**index asset** (`FCE_Timetable_TT.uasset`), not from a layer. FName indices
in a `.uexp` address that package's own name map, so a layer cannot reference
a name that is not in its own table - meaning station identity must arrive
some other way (an import/export reference, or the MasterDataTrack).

### Next step

Run `/api/paks/stops` again on the same asset and read `names_sample` - the 88
names in this layer's table are the evidence for what a StopPoint record can
possibly point at. Then inspect `FCE_Timetable_TT.uasset` and
`FCE_Timetable_TT_MasterDataTrack.uexp` the same way.

Note `name_count` is 88 here and section 5 counts 88 station names in the
index asset. Probably a coincidence - none of these 88 match the station
shape - but worth confirming rather than assuming.

## THE FORMAT IS TAGGED PROPERTIES - section 8 was wrong (v7.39.0)

`names_sample` from the second real run settles it. The Leven Branch layer's
88-entry name table contains:

```
ArrayProperty  EnumProperty  FloatProperty  IntProperty  MapProperty  NameProperty
DataType  Distance  Direction  DirectionOfTravel  InstructionIndex
GoViaIndex  ActionIndices  Location  NetworkRibbonLocation  Guid
ETimetableTrackDataType::{StopPoint, ActionPoint, GoVia, MultiOccupancy,
                          ReversePoint, TrackSectionEntry, TrackSectionExit}
EDirectionOfTravel::{Forwards, Backwards}
P2K51 ... P2K74           <- NetworkRibbonLocation values
```

Those are Unreal's **tagged property** type names. Section 8 reasoned that
"Stream" in `RouteTimetableDataTrackStream` implied a custom `Serialize()`
writing raw binary, and that the record layout would have to be
reverse-engineered from byte patterns. **That was wrong.** The asset is
self-describing: every record can be read field by field, by name, with no
inference at all.

Also settled: `names[0]` is `None` and `names[1]` is the package path, which
is Unreal's standard name-map layout. So the recovered table IS the real one -
position equals index, and the whole shift-recovery problem was never a real
problem.

### Why the statistical run picked shift -6, and why it was never going to work

Shift -6 maps `StopPoint` onto `EnumProperty` and `ActionPoint` onto
`Distance` - the property MACHINERY names. Those genuinely appear once per
record with a perfectly consistent delta, so the scoring was not
malfunctioning. It found a real per-record field; just not the one it was
looking for. No further statistics would have fixed it. This is the lesson:
**when the data is self-describing, read it - do not measure it.**

### `parse_track_records()` - the parser

`pak_tools.parse_track_records()`, endpoint `/api/paks/records`, button
**Read records (tagged properties)** on Discovery.

Walks `FPropertyTag` chains: FName Name (None terminates the record), FName
Type, int32 Size, int32 ArrayIndex, a type-specific header, an optional
HasPropertyGuid byte, then Size bytes of value. Records are found by scanning
for a readable chain rather than trusting any header offset, so no engine
version needs to be known. UE4 gained the HasPropertyGuid byte partway through
its life, so both layouts are tried and whichever parses more records wins.

Validated against `tests/synth_tagged.py`, written from the FPropertyTag spec
independently of the parser: **220/220 records and 48/48 StopPoints recovered
exactly, with and without the guid byte**, field names and values intact, and
the first stop correctly carrying a departure and no arrival. Opaque random
bytes are refused rather than turned into records.

### What this does NOT solve

Still no station names. `NetworkRibbonLocation` holds values like `P2K51`,
which are track ribbon identifiers, not station names. The stop records give
times, distances, instruction indices and ribbons - so a stop is precisely
located on the network, but not yet labelled. Mapping ribbon IDs to station
names is the remaining piece, and the index asset
(`FCE_Timetable_TT.uasset`) or `MasterDataTrack` is where to look.

### Next step

Run **Read records** on the real Leven Branch layer. `field_usage` and
`data_types` will show the actual field set - the 60-name cap has been raised,
so the full table comes back too.
