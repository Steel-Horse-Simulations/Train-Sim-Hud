# Offline timetable extraction — findings

Everything below is **confirmed against a real TSW6 install**, not inferred.
Written up so this doesn't have to be rediscovered.

## Verdict

**The record layout is SOLVED** (v7.48.1/7.49.0, confirmed against the real
Leven Branch layer). 12,207 fixed 707-byte records; the track-data type at a
fixed offset in every record, reproducing all six whole-file type counts
exactly; the schedule time identified by ascending behaviour. Services and
stop times are extracted by `/api/paks/timetable`.

What remains: station NAMES (not present in this asset), and writing the
result into `timetables.db`.

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
polling and likely stop the connection drops. IMPLEMENTED in v7.45.0
(`tsw_subscriptions.py`), but the protocol here was never verified against a
real game - the client probes, verifies and falls back to polling if the
endpoint is missing or the payload shape is not what was guessed. Check
`/api/subscriptions` on a real run.

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

## The format question - TAGGED PROPERTIES was ALSO wrong (v7.39.0 / 7.39.1)

READ THE CORRECTION AT THE END OF THIS SECTION BEFORE ACTING ON IT. The
tagged-property reading below is what the name table SUGGESTED; the real file
then parsed zero records, so the suggestion was not the answer.

### What the name table suggested

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

### CORRECTION: the real file has no tag chains at all

`parse_track_records()` on the real Leven Branch layer returned
`no_records_parsed` - not one chain of two consecutive valid tags anywhere in
8.6 MB. Two conclusions were wrong in two turns, from the same mistake both
times: reasoning from an indirect signal instead of measuring the thing
itself.

  - Section 8 saw "Stream" in the type name and concluded custom binary.
  - v7.39.0 saw property TYPE names in the name table and concluded tagged
    properties. But a name being IN the table does not mean it is REFERENCED
    by the record data - cooked name maps carry names from the class schema
    regardless.

### The measurement that settles it

`probe_name_references()` / `/api/paks/probe` / **Probe format** on Discovery.
It counts how often each name is actually referenced as an FName inside the
`.uexp`, which separates the two modes directly:

| | property TYPE names referenced | enum VALUE names referenced |
|---|---|---|
| Tagged properties | yes, ~once per record | yes |
| Unversioned properties (UE4.25+ cooked) | **no - none at all** | yes |

Under unversioned serialisation no tags are written; fields are identified
positionally against the class schema. Enum values still appear because an
EnumProperty value is an FName either way. So if
`ETimetableTrackDataType::StopPoint` is referenced but `DataType` and
`EnumProperty` are not, it is unversioned and there is no point looking for
tags at all.

Validated both ways in `tests/`: a tagged fixture and an unversioned fixture
built from the same records. The probe identifies each correctly, and a failed
`parse_track_records()` now carries its own probe so the "why" arrives with
the "no".

### What to run next

**Probe format** on the real Leven Branch layer. The answer decides the route:

  - **property type names referenced ~once per record** - it IS tagged and the
    tag layout differs from the UE4 one assumed here. Compare `head_hex`
    against FPropertyTag for this engine version.
  - **none referenced, enum values present** - unversioned. Tags are not
    coming. The schema then has to come from the class, or fields have to be
    located positionally - and note the statistical path already works well
    enough to segment services, so the fallback is not nothing.

### Standing lesson

Three format conclusions now, two of them wrong, and both wrong ones came from
an indirect signal. Measure the file, do not reason about the file.

## It IS tagged - the probe on the real file (v7.39.2)

`/api/paks/probe` on the real Leven Branch layer, 8.6 MB, 12,207 records:

| Name | References | |
|---|---|---|
| `DataType` | 48,830 | on every record |
| `GoViaIndex` | 48,828 | on every record |
| `InstructionIndex`, `NetworkRibbonLocation` | 36,658 each | |
| `Distance`, `RibbonLocation` | 24,414 each | = 2 x 12,207 |
| `Time`, `DirectionOfTravel`, `Guid`, `Direction`, `IntProperty`, `NameProperty`, `StructProperty`, `FloatProperty`, `ServiceDataTracks`, `Class`, `Package` | 12,207 each | **the record count** |
| `EnumProperty` | 61,036 | 5 per record |
| `MapProperty` | 1 | the outer `ServiceDataTracks` map |
| `SignalRef` | 85,522 | new - not in the first 60 names |

Property TYPE names ARE referenced, so this is **tagged property
serialisation** after all, and the v7.39.1 correction over-corrected. It is
not unversioned.

Names seen here that were not in the first 60: `SignalRef`,
`SignalPropertyReference`, `RibbonReference`, `RibbonLocation`,
`PropertyReference`, `S5K84`. The 60-name cap has been raised.

Also worth recording: `StopPoint` and `TrackSectionEntry` are referenced
**5,198 times each** - identical counts. Every StopPoint appears to be paired
with a TrackSectionEntry.

### Why nothing parsed: `longest_tag_chain: 1`

One tag reads, the next does not. That is the signature of a FIELD WIDTH
mismatch, and it fails quietly rather than loudly: if the engine writes an
FName as int64 index + int64 Number, reading it as int32 + int32 still
returns the correct index (the low half) with a Number of 0 (the high half),
so the first tag looks perfect - and every subsequent read lands mid-field.

`_read_tag()` now takes a `width`, and `parse_track_records()` tries 4 and 8
across both `guid_byte` settings, keeping whichever chains furthest. Proven
on a 16-byte-FName fixture: 112/112 records, 31/31 StopPoints, width
correctly discovered. `_MAX_PROP_SIZE` was also raised to 64 MB - the outer
`ServiceDataTracks` MapProperty is megabytes and was being rejected on size.

### A correction, and the diagnostic that should have existed first

The previous message read the first 256 bytes and claimed the file uses
64-bit fields throughout. **That was wrong** - decoded properly, bytes 0-23
look like three 8-byte values and then the pattern breaks immediately. The
head of an 8.6 MB .uexp need not be record data at all, and nothing about the
record layout can be concluded from it.

So the probe now dumps hex windows around real references to a chosen field
name (`window_around`, default `DataType`), annotating every offset in the
window that resolves to a name, and flagging whether it is consistent with an
8-byte or 16-byte FName. That reads the layout off directly:

```
8-byte layout      +0 DataType   +8  EnumProperty
16-byte layout     +0 DataType   +16 EnumProperty
```

It also reports `chain_break`: which tags the longest chain read, where it
stopped, and the next 64 bytes.

This is the diagnostic that should have been built before any of the three
format conclusions. All three were reached by reasoning from an indirect
signal - a type name, a name table's contents, a file header - rather than by
looking at the bytes around a known field.

### Next step

**Probe format** on the real Leven layer again. The window output says the
width outright; then **Read records**, which now tries both. The counts above
are a ground truth worth checking the parser against: it should find 12,207
records, with 5,198 StopPoint and 5,198 TrackSectionEntry among them. If it
does not reproduce those, it is wrong however clean the output looks.

## The name table is too small to parse against - recover the layout by REPETITION (v7.40.0)

`/api/paks/records` on the real Leven layer still returns `no_records_parsed`
at both field widths. The attached probe explains why, and it invalidates
some of what the previous section concluded.

### The 88-name table makes the FName test almost meaningless

`total_fname_references: 2,539,329` in an 8,638,791 byte file. **29% of every
byte offset in the file passes the "valid FName reference" test.** With only
88 names, any int32 in 0..87 followed by a zero looks like a name reference,
and small ints and zeros are what binary data is mostly made of.

That invalidates the diagnostics built on it:
  - the hex windows around `DataType` are mostly coincidence - window 3 shows
    `DataType` at +0 followed by `Distance` at +8, i.e. two FIELD names
    adjacent, which is not a thing FPropertyTag can produce;
  - `chain_break` read exactly one "tag": `SignalRef` / `EnumProperty` with a
    declared **size of 27**. An EnumProperty value is an 8-byte FName. Size 27
    is impossible, so that tag was noise too.

So "property TYPE names ARE referenced, therefore it is tagged" was over-read.
Some of those 61,036 `EnumProperty` hits are real; most are not.

### What IS real: sixteen names at exactly 12,207

| References | Names |
|---|---|
| 48,830 | `DataType` |
| 36,658 | `InstructionIndex`, `NetworkRibbonLocation` |
| 24,414 (= 2 x 12,207) | `Distance`, `RibbonLocation`, `FCE_..._DataTrack` |
| **12,207** | **sixteen names** - `IntProperty`, `NameProperty`, `StructProperty`, `FloatProperty`, `PropertyReference`, `SignalPropertyReference`, `RibbonReference`, `Time`, `DirectionOfTravel`, `Direction`, `Guid`, `Class`, `Package`, `ServiceDataTracks`, `Default__RouteTimetableDataTrackStream`, `S5K84` |
| 5,198 | `StopPoint` **and** `TrackSectionEntry` - identical |

Coincidence does not land on the same integer sixteen times. **12,207 is the
record count** (mean record size 708 bytes) and those are once-per-record
fields.

### `record_template()` - use the repetition, not the parse

`/api/paks/template`, button **Recover record template**.

Take a name occurring exactly once per record, treat each occurrence as a
record boundary, and ask which (relative offset, name) pairs recur ACROSS
records. A real field sits at the same offset in every record; a coincidence
does not recur. The noise cancels itself, which is exactly what parsing could
not do here.

Validated on the tagged fixture: recovers the true field ORDER
(`InstructionIndex` -> `Distance` -> `NetworkRibbonLocation`, with their
`IntProperty` / `FloatProperty` / `NameProperty` type names in the right
places), and refuses to produce a template from random bytes.

### Next step

**Recover record template** on the real Leven layer. Expect `record_count`
12,207. The `stable_fields` list is then the record layout, read from
evidence rather than assumed, and the offsets in it are what a real parser
should be built from.

### Standing lesson, now with a fourth data point

Four format conclusions, three of them wrong. Every wrong one came from
trusting a signal that had not been checked for how often it fires by chance.
The FName test firing on 29% of all offsets should have been measured BEFORE
anything was built on top of it - `total_fname_references` was in the probe
output the whole time and says exactly that.

## FIXED-LENGTH RECORDS - 12,207 x 707 bytes (v7.41.0)

`record_template()` on the real Leven Branch layer:

```
record_count        12207
record_size_min     707
record_size_median  707
record_size_max     958
anchor              Class   (one of the 16 names at exactly 12,207)
stable_fields       57 fields at 100% share
```

`12207 x 707 = 8,630,349` against a file of `8,638,791` - the entire .uexp is
records plus about 8 KB. `min == median` says the stride is 707 with a few
larger gaps, not a variable-length stream.

**The type is a `Stream` and the records are still fixed length.** Section 8's
inference from the type name was wrong in both directions.

### Why "57 fields at 100%" is NOT yet proof

Once records are fixed length, "recurs at the same offset in every record" is
true of any constant byte pattern as well as of a real field. The repetition
test that beat the noise for variable-length records stops discriminating
here, and only 24 of 12,207 records were sampled. The documented 2828-byte
stride failure is the same trap: alignment held for twenty records, then
drifted.

So the template's `stable_fields` are candidates, not findings.

### `decode_fixed_records()` - the check that does discriminate

`/api/paks/decode_fixed`, button **Decode fixed records**.

Read the type enum at one fixed offset inside every record, then compare the
result against a whole-file scan that assumed NO stride and no record
structure. Three conditions must hold:

  - **coverage** - nearly every record carries a type at that single offset.
    A wrong stride cannot produce that.
  - **within the upper bound** - no decoded count exceeds the whole-file
    count. The whole-file scan counts every genuine occurrence PLUS every
    coincidence, so it is a ceiling, never a target.
  - **rank order agrees** with the whole-file scan.

Requiring the decoded counts to EQUAL the whole-file counts was tried first
and is wrong: on a fixture whose decode matched ground truth exactly, the
whole-file reference read 284 where the truth was 263, and a correct answer
was reported as unconfirmed.

Validated on a fixed-stride fixture built with realistic payload bytes
(floats, tick counts, GUIDs - NOT small ints, which would make every offset
read as a valid FName): recovers the stride, the type offset and the time
offset exactly, reproduces the ground-truth distribution to the record, and
**refuses a stride one byte wrong**.

Time-field candidates must VARY across records - an all-zero word scored 100%
coverage and decoded to 00:00:00 in every record.

### What to expect on the real file

`Decode fixed records` on the Leven layer should report stride 707 and a type
offset where the distribution lands at or below 5,198 StopPoint / 5,198
TrackSectionEntry / 908 ReversePoint / 36 MultiOccupancy / 27 GoVia / 4
ActionPoint. If `confirmed` comes back true, the record layout is settled and
the remaining work is reading the time and distance fields at their offsets
and writing journeys into `timetables.db`.

If it comes back false, the 707 stride is coincidence and the template
offsets go in the bin - which is exactly what this endpoint exists to find
out before anything is built on them.

## The anchor bug that invalidated the v7.41.0 decode run (fixed v7.48.1)

The `decode_fixed` run on the real Leven layer reported `confirmed: false`,
stride 196 and 19% coverage - and none of that meant what it said, because it
had anchored on the wrong name.

It picked **`EnumProperty` (61,036 references)** over **`Class` (12,207)**.
Scoring was count x evenness, so five times the references beat slightly
better spacing. Every downstream number was then framed against a record
boundary that does not exist: "19% coverage" was 11,371 typed hits over
61,036 fake records, which against the real 12,207 is **93%**.

### What that run DID establish

Reading the type at a fixed offset reproduced all six whole-file counts
exactly:

```
ActionPoint 4/4 · GoVia 27/27 · MultiOccupancy 36/36
ReversePoint 908/908 · StopPoint 5198/5198 · TrackSectionEntry 5198/5198
```

`within_upper_bound: true`, `rank_agrees: true`. Six exact matches including
ActionPoint at 4, where being off by one record would show. The type field is
real and sits at a fixed offset; only the record boundary was wrong.

### The fix: score anchors by MODAL GAP DOMINANCE

What fraction of the gaps between a name's references are the same value. A
name written once per record repeats at exactly one interval and scores near
1. A name written five times per record has gaps alternating short and long,
so no single gap dominates. Coincidence scatters.

On the fixture: `Class` 0.958, `Package` 0.942, `Guid` 0.939 - all real
boundaries - against `StopPoint` 0.384 (a subset of records) and `P2K51`
0.125 (noise).

Two earlier schemes were each fooled, and the fixture now reproduces both:
  - **count x evenness** picked the five-per-record impostor;
  - **requiring an identical shared count** then latched onto a coincidental
    cluster of four rare names, because with noisy data the real anchors'
    counts get perturbed by a few coincidental hits and stop matching
    exactly. On the real file sixteen names DID land on exactly 12,207, which
    is why that scheme looked sound.

Gap dominance needs neither an exact count match nor a low reference count.

### Also fixed: two copies of the selection logic

`record_template()` and `decode_fixed_records()` each had their own anchor
picker. They drifted, and disagreed: the template picked a name with 25
coincidental references while the decoder picked the true boundary, so the
two functions were describing different records of the same file. Both now
call `_pick_anchor()`.

### Next step

Re-run **Recover record template** and then **Decode fixed records** on the
real Leven layer. Expect anchor `Class`, 12,207 records, stride 707, and a
type distribution at or below 5198/5198/908/36/27/4. If `confirmed` comes
back true this time, the record layout is settled and the remaining work is
reading the time fields and writing journeys into `timetables.db`.

## SOLVED: the record layout, confirmed on the real file (v7.49.0)

`decode_fixed_records` on the real Leven Branch layer, with the anchor bug
fixed:

```
anchor           IntProperty
record_starts    12207
stride           707        (holds for 99.71% of gaps)
type offset      +270       93% of records typed
confirmed        true
                 decoded            whole-file bound
ActionPoint            4    /    4
GoVia                 27    /   27
MultiOccupancy        36    /   36
ReversePoint         908    /  908
StopPoint           5198    / 5198
TrackSectionEntry   5198    / 5198
```

Six exact matches, including ActionPoint at 4 where being off by one record
would show. A wrong stride cannot produce a field present in 93% of records
AND land on six independent counts.

### Which field is the time

Five offsets passed "is a tick count inside a day". Two of them - `+139` and
`+140` - are one byte apart, i.e. the SAME bytes read twice, which is the
giveaway that a threshold was doing the choosing rather than any evidence.
`+233` sat near-constant at 00:52:05, a duration rather than a clock.

`+200` ascends across consecutive records (05:20:00 -> 05:20:38 over the
first twelve) and resets between services. `extract_timetable()` therefore
selects the time field on ASCENDING BEHAVIOUR, not on plausibility, and
reports the runners-up with their scores so the choice is auditable.

### Where the offsets are measured from

Offsets are relative to the ANCHOR reference, not to the record start, and
the anchor sits inside the record. On a fixture with the type at +270 and
time at +200 from the record start, the tool reports +246 and +176 because
the anchor is 24 bytes in. **The GAP between the two is the invariant** -
that is what the regression test asserts.

### Stray anchor hits have to be filtered

Even a good anchor collects a few coincidental matches, and each inserts a
phantom record whose out-of-sequence time makes the segmenter split one
service into two. On a fixture with 40 services and 161 stray hits it
reported 58. `_stride_chain()` keeps only hits a whole number of strides from
the last accepted one, which restored the record count to exact while still
tolerating the 0.3% of real gaps that are not 707.

### Service segmentation is the one HEURISTIC part

Everything above is measured. This is not. A boundary shows as the clock
going backwards OR as a large forward jump - services stored in ascending
start order never go backwards at all, and splitting only on a decrease
merged 40 fixture services into 8. The current rule is a 600s gap, adjustable
via `service_break`.

It cannot be made exact by tuning: real station-to-station running times
reach 5.6 minutes on this very layer, while consecutive services can start
minutes apart. On a fixture whose services sit ~10 minutes apart it still
merges 40 into 26.

**Cross-check the service count against `extract_time_series`, which found
104 ascending runs on this layer by a completely unrelated method.** Two
independent methods agreeing is worth more than either number alone.

### Next step

Run **Extract timetable** on the real Leven layer. Then:
  - if the service count lands near 104, segmentation is sound and the
    remaining work is writing journeys and stops into `timetables.db`;
  - if it is far off, adjust `service_break` rather than anything upstream -
    the layout beneath it is confirmed.

Station names are still absent from this asset (`NetworkRibbonLocation` holds
P2K-style track ribbon IDs, not station names), so stops will have times and
positions but no labels until the index asset is decoded.

## The chain bug: 91 records instead of 12,207 (fixed v7.49.1)

The first real `extract_timetable` run returned **91 records and 1 service**,
having stopped **0.62% into the file** - while reporting `layout_confirmed:
true` and a perfectly plausible-looking service. Everything it did report was
correct; there was just almost none of it.

`_stride_chain` required each record start to be a whole number of strides
from the LAST ACCEPTED one. Every gap it kept was an exact multiple of 707
(707, 1414, 2121, 2828, 3535 - checked). But **0.29% of the real file's gaps
are not 707**, and the first one put every subsequent offset permanently
off-grid by the remainder. From there nothing could ever match again.

A global-grid assumption turns one bad gap into a whole-file failure. The
rule is now LOCAL:

  - a stray anchor hit lands INSIDE a record, so it sits less than one stride
    from the previous genuine start; a real start is at least a stride away.
    That single comparison is the filter.
  - the offset exactly one stride on is PREFERRED when it exists, so the
    chain re-locks to the grid - purely local matching occasionally latched
    onto a stray just past the threshold and then read every field of that
    record from the wrong base, costing 7 of 2048 stops on the fixture.
    Preferred, never required.

Verified: exact record and StopPoint counts on a clean fixture, and **exact
again after injecting a 251-byte phase shift halfway through the file** -
which is the real failure reproduced deliberately. A bad gap now costs one
record instead of the remainder of the file.

### What the 91 records did confirm

The layout beneath the bug held up. Within those 91 records: stride 707, type
at +270, time at +200 chosen with a rising score of **0.9889** against 0.9444
for the `+233` duration decoy and 0.5333 for the `+139`/`+140` pair (which
also showed 35 resets, i.e. not a clock at all). 40 StopPoints and 40
TrackSectionEntry in the same 91 records - the 1:1 relationship seen in the
whole-file counts (5198 each) holds locally too.

### Next step

Re-run **Extract timetable**. Expect ~12,207 records, ~5,198 StopPoints, and
a service count to compare against `extract_time_series`' independent 104.

## Full extraction on the real file, and the segmentation question (v7.50.0)

`extract_timetable` on the real Leven Branch layer, chain bug fixed:

```
records      12207        stride 707      layout_confirmed true
type at      +270         time at +200
StopPoint 5198 · TrackSectionEntry 5198 · ReversePoint 908
MultiOccupancy 36 · GoVia 27 · ActionPoint 4
```

Every count exact against the whole-file bound. **The record layout and the
type and time fields are settled.**

The time field won on evidence, on the full file this time: `+200` scored
0.9414 rising with 32 resets, against 0.8955 for the `+233` duration and
0.4315 for `+139` - which showed **5,491 resets**, so it is not a clock at
all. The one-byte-shifted twins (`+140`, `+234`) track their partners
exactly, as they must.

### Services: 36 or 104?

The clock heuristic gave **36 services of ~152 stops each, 66-70 minutes**.
The earlier statistical `extract_time_series` gave **104 runs**. Both cannot
be right.

The clock cannot arbitrate, and this is measurable rather than a matter of
opinion: **the largest gap between any two consecutive stops in the whole
file is 396 seconds.** The 600s threshold therefore never fires - all 36
splits came from the clock going backwards. Lowering it does not help
either, since real running gaps reach 6.6 minutes and consecutive services
can start closer together than that. No threshold separates them.

### `find_service_field()` - ask the record, not the clock

A service identifier is constant for every record of one service and changes
at the boundary, so scanning each offset for a value that holds in runs
answers the question outright: **the number of runs IS the number of
services.**

Candidates are scored on runs and distinct values AGREEING. That test is
what rejects the obvious impostor: a flag alternating between two states
produces thousands of runs and two distinct values, and is not an
identifier. The fixture plants exactly such a flag to keep the test honest.

Validated on a fixture with a known ID: found at the right offset with **40
runs and 40 distinct values against 40 true services**, the alternating flag
rejected, and `extract_timetable` then segmented to exactly 40 - where the
clock heuristic on the same file gave 26.

`extract_timetable` now uses the field when one is found and reports which
method it used in `segmented_by`; the clock remains the fallback only.

### Next step

Run **Find service field** on the real Leven layer.
  - A candidate with runs ~104 confirms the statistical result and the clock
    was merging services roughly 3:1.
  - A candidate with runs ~36 confirms the clock and means the statistical
    method was over-segmenting.
  - No candidate at all means services are not identified inside the record,
    and the clock is genuinely the only signal available - in which case the
    threshold has to be chosen knowing it cannot be exact.

Then **Extract timetable**, which will use whatever it finds.

## Ground truth from the game, and station CALLS (v7.51.0)

Counted in the in-game timetable by the user - this is the yardstick
everything is now measured against:

  - **39 Leven services.** 37 run Edinburgh <-> Leven, 2 are Glenrothes -
    Leven.
  - A Leven -> Edinburgh service calls at **13 stations**, counting both
    ends.
  - A Glenrothes - Leven service calls at **2** - the two ends, nothing
    between.

### A StopPoint record is NOT a station call

`37 x 13 + 2 x 2 = 485` station calls, against **5,198 StopPoint records**.
That is **10.7 records per call**.

So every "stop count" reported before this was a record count, not a stop
list. The clock-based run reported ~152 stops per service, which against 13
real calls is 11.7 records per call - consistent with 10.7, so the extraction
was internally right all along and only the LABEL was wrong.

`extract_timetable` now collapses records into calls. The records for one
call sit seconds apart (the median gap between consecutive StopPoints is 8s)
then jump minutes to the next, so clustering on `call_gap` (default 90s)
recovers them. The call's arrival is the first time in its cluster and its
departure the last, which is exactly the arrival/departure pair the domain
rules call for.

### There is no service field in this file

`find_service_field` on the real layer returned **nothing**, and the run-count
bands show why: every offset either holds one value for the whole file or
changes on nearly every record. Nothing in between. Services are not
identified inside the record.

The tool now reports near-misses and a distribution of run counts instead of
an empty table, because "nothing found" without showing what IS there gives
no way to tell a real absence from a threshold set too tight.

It also takes `expected_runs`. Two scoring bugs were fixed along the way,
both caught by fixtures:
  - relaxing the near-miss filter let a field that changes on EVERY record
    score perfectly - its run and distinct counts are equal, so it looks
    maximally identifier-like while being the opposite. Scoring now includes
    sparsity: a service field has far fewer runs than records.
  - `extract_timetable`'s internal search, called without a target, picked
    that same field and reported 10,150 services out of 10,150 records.

### Segmentation: cut at the strongest boundaries

With no field to key off and no workable threshold - the largest gap between
consecutive stops in the whole file is 396s, while services start minutes
apart - the honest approach is to use the known count. Every transition is
ranked by how strongly it looks like a boundary (a backwards clock outranks
any forward gap, however large) and the strongest N-1 become the cuts.

Validated on a fixture built to the real shape - 39 services, 37 x 13 calls
and 2 x 2, ~11 records per call, no service id field:

```
services      39 / 39
median calls  13 / 13
short workings recovered: [2, 2]
every call carries both an arrival and a departure
```

The two Glenrothes workings are the sharpest check available: ~20 records
against ~140 is a 7:1 gap that a correct segmentation cannot miss and a wrong
one cannot fake.

### Next step

On the Discovery page enter **39** in the new Services box, then **Extract
timetable**. Expect 39 services, 37 with 13 calls and 2 with 2. If the two
short workings appear, segmentation is right and the remaining work is
writing journeys and calls into `timetables.db`.

Station NAMES remain absent from this asset - calls will have times and
positions but no labels.

## The +407 near-miss, and 3-record service headers (v7.51.1)

Running with 39 in the Services box found a field at **+407 with 37 runs**,
and because the tolerance was 10% it was accepted and used INSTEAD of the
known count. The result decomposes cleanly and shows the field is wrong in
both directions at once:

```
11 x  3 records, 0 stops, 0 duration     <- fragments
18 x ~355 records, ~154 stops, 11-13 calls  <- correct single services
 5 x ~1070 records, 33-34 calls           <- ~3 services merged each
 2 x  short (94 and 48 records)
```

Each 3-record fragment shares its start time with a full service that follows
it, so **+407 splits 11 services three records early**, and it **fails to
split 5 boundaries at all**. 18 + 5x3 + 2 = 35, not 39.

Tolerance is now **exact within 1**. A field that is nearly right about the
COUNT can be wrong about every BOUNDARY, so closeness is not evidence. With
39 given and 37 found, it now falls back to cutting at the 38 strongest
boundaries.

### The 3-record fragments are worth a look

They are not noise. Eleven of them, exactly 3 records each, no StopPoints, no
duration, each at the exact start time of the service that follows - 06:15,
06:57, 09:15, 10:01, 12:15, 13:02, 15:17, 16:04, 18:14, 19:05, 21:23, all on
the minute.

That looks like a per-service HEADER: a few records carrying the service's
identity and start time rather than a position on the track. If every service
has one, they are the real service boundary, and finding what distinguishes
them would settle segmentation without needing the count from the game at
all. They are also the most likely place for a headcode to live - which would
give the services names, something this layer otherwise lacks entirely.

Times on the exact minute are the tell: every other time in the file is a
running time to the second.

### Next step

Re-run with 39 in the Services box - it will now use the known-count cut
rather than the +407 field. Expect 37 services of ~13 calls and 2 short ones.

Then worth investigating: what is in those 3-record headers? A record
template anchored on them, compared against a normal track record, should
show which fields differ.

## The record template corroborates the time field (v7.52.0)

`record_template` on the real layer, anchored on `IntProperty`, gives 57
fields at 100% share. The one that matters:

```
+167  DataType
+175  Time          <- the property NAME
...
+200                <- the time VALUE, found independently and statistically
```

`175 + 8 (Name) + 8 (Type) + 8 (Size + ArrayIndex) + 1 (HasPropertyGuid) =
200`. Exactly Unreal's FPropertyTag header.

Two completely unrelated methods - a repetition scan that knows nothing about
Unreal, and an ascending-value test over candidate offsets - land on the same
byte. That is the strongest confirmation the time field has had.

The template also shows the record is not flat: `RibbonLocation` /
`SignalRef` / `EnumProperty` / `GoViaIndex` repeats as a block at +49, +426,
+532, +597, and `NetworkRibbonLocation` at +143, +662, +699. Those look like
arrays of sub-structures, which is consistent with ~10.7 StopPoint records
per station call.

## Segmentation with the known counts

With **39** entered and the near-miss field correctly rejected, the
known-count cut gives 39 services with 11-14 calls - the right shape. Two
faults remained, both now fixed and both measurable rather than matters of
taste:

**Fragments.** Three of the 38 cuts were spent on segments of 35-50 records,
and three real boundaries were missed as a result. A service is ~355 records,
so a cut that leaves a segment under a floor is now rejected and the next
strongest taken instead.

**Under-counted calls.** A fixed 90s clustering gap gave a median of 11 calls
against the 13 counted in the game. The gap cannot be chosen from first
principles - records within one call sit ~8s apart while runs between
stations are 2-6 minutes, but some station pairs are much closer than others,
so any fixed value under-splits somewhere.

`expected_calls` now TUNES it: the gap is swept and the value reproducing the
known call count is chosen. Critically the result reports `call_gap_curve`,
so the choice is auditable - on the fixture, 64 different gap values all give
a median of 13, a broad plateau rather than a spike. **A plateau means the
answer was found; a spike would mean it was fitted.** That distinction is the
whole reason the curve is returned.

Validated on a fixture built to the real shape: 39/39 services, median 13
calls, both 2-call Glenrothes workings recovered, no fragments.

### Next step

Re-run with **39** services and **13** calls. Expect 37 services of 13 calls
and 2 of 2, and a broad `call_gap_curve` plateau. If the plateau is narrow,
the call count is being forced rather than found and should not be trusted.

## Time clustering does NOT recover station calls (v7.53.0)

The tuning run reported a median of 13 calls at a 65s gap - the right answer.
**It was fitted, not found**, and the plateau check said so:

```
gap:  20  25  30  35  40  45  50  55  60  65  70  75  80  85  90 ...
med:  45  40  34  31  28  22  18  17  15  13  12  12  12  12  11 ...
```

Median 13 occurs at exactly ONE gap value out of 77. A real cluster
structure produces a PLATEAU, because a whole range of thresholds separates
the same groups - the fixture, built with genuinely separated calls, has 64
consecutive gap values all giving 13. The real curve is a smooth continuum
from 45 down to 1 with no flat step anywhere near 13.

**A continuum means the times carry no grouping to find.** Whatever number
came out was chosen by the threshold. Had the curve not been reported, a
median of exactly 13 would have looked like a triumph.

This is why `call_gap_curve` is returned and why the plateau, not the
answer, is the thing to check.

### What this rules out

StopPoint records are not ~11 tightly-grouped records per station call
separated by minutes of running time. Their times are spread more evenly
than that. So the ~10.7 records-per-call ratio is real arithmetic but not a
description of the layout.

### `find_call_field` - ask the records instead

`/api/paks/call_field`, button **Find call field**. Same approach that
settled the service question, applied over the StopPoint records only: a
field holding one value per call changes once per call, so its run count
across those records IS the number of station calls. The target is known -
37 x 13 + 2 x 2 = **485**.

Validated both ways on the fixture: with a call id planted it finds it at
the right offset with **485 runs, 485 distinct, 11 records per run**; asked
for a count that is not there, it returns nothing rather than the closest
thing.

### Next step

Run **Find call field** with 39 services and 13 calls entered - it will look
for 485 runs.

  - A hit means station calls are delimited in the record and the timetable
    is essentially done.
  - Nothing found means calls are not marked in these records at all. The
    next place to look is `RibbonLocation` / `NetworkRibbonLocation`, since a
    station call is a position on the network and those fields hold the P2K
    track ribbon ids - the values themselves may group the records even if no
    integer field does.

Also still worth investigating: the 3-record, on-the-minute service headers
found in v7.51.1, which remain the best candidate for where a headcode lives.

## +692: 14 distinct values, and 13 stations (v7.54.0)

`find_call_field` on the real layer found no field matching the requested 507
runs, but the candidate list contains something far more interesting than the
thing being searched for:

```
offset   runs  distinct  per-run  medrun  maxrun
 +692     429     14      12.12     10      63
 +693     429     14      12.12     10      63
 +694     429     14      12.12     10      63
 +695     429     14      12.12     10      63
```

**14 distinct values across the entire file**, and a Leven-Edinburgh service
calls at **13 stations**. Random fields do not land on 14. The four
consecutive offsets are the same int32 read at four byte positions, as
expected.

12.12 records per run also sits right on the ~10.7 records-per-call ratio
derived from the game count.

429 runs against 485 calls is explainable rather than contradictory:
consecutive runs sharing a value merge, and a service ending where the next
begins would merge across the boundary - 485 minus 38 boundaries is 447, in
the same region as 429.

Other candidates worth remembering:
  - **+696-698: 40 runs, 2 distinct** - almost exactly the 39 services.
  - **+407-410: 15 runs** - the field that was wrongly accepted in v7.51.
  - **+204: 341 runs, 153 distinct** - too many states for stations.

### A tolerance bug this exposed

`expected_calls` was sent as services x calls = 39 x 13 = **507**, but the
true total is 37 x 13 + 2 x 2 = **485**, because two of the services are
Glenrothes shuttles with 2 calls. That 4.5% overshoot against a 5% tolerance
could reject a correct field on arithmetic alone. Tolerance widened to 15%,
and the candidate list is now always returned so a near miss can be judged on
its DISTINCT count - which for a station field is the informative number, not
the run count.

### `inspect_field` - read the values, do not just count them

`/api/paks/inspect_field`, button **Inspect field**, with a **Field offset**
box.

Statistics can say a field has 14 states. Only the value sequence can say
whether those states are STATIONS, and it is decisive because a station
identifier must:

  - visit ~13 distinct values within one service, not repeat two,
  - run in the OPPOSITE ORDER on a return working,
  - show only 2 distinct values on the Glenrothes shuttles.

No coincidence produces all three, and all three are visible by printing the
sequence.

Validated on a fixture: reads back the planted call ids exactly, 13 runs and
13 distinct per service, in order.

### Next step

**Inspect field** with offset **692**, Services **39**. Then read the
sequences:

  - 13 distinct values per service, one run each -> it is the station, and
    the order gives the station ORDER along the route, which is the missing
    piece for labelling stops.
  - Two services showing only 2 distinct values -> those are the Glenrothes
    shuttles, and that is conclusive.
  - Reversed order between alternate services -> Edinburgh-bound vs
    Leven-bound, confirming direction.

Worth also inspecting **696** (40 runs, 2 distinct) - a two-state field
changing once per service looks like a direction or an up/down flag.

## +692 is a BYTE at +695 - and int32-only scanning was a blind spot (v7.55.0)

`inspect_field` on +692 returned 14 values, every one an exact multiple of
2^24:

```
0  16777216  33554432  50331648  67108864  117440512  150994944
201326592  218103808  301989888  352321536  385875968  402653184  -16777216
```

Divide by 2^24 and they are: **0, 1, 2, 3, 4, 7, 9, 12, 13, 18, 21, 23, 24,
255**.

So this is a **single byte at +695**, read as an int32 that happened to catch
it in the top byte. That also explains why +692, +693, +694 and +695 all
reported identical run and distinct counts - the same byte at four shifts.

**Every field scan in this tool read int32 only.** Any byte field in the
record was therefore either invisible or reported as four offsets of
meaningless magnitudes. The scans now cover u8, u16 and i32, de-duplicate the
same field found at several widths, and keep the reading with the most
distinct values - the one showing the field's real range rather than a
truncation of it.

### What the byte is, and is not

Not a station identifier. The value frequencies rule it out: 0 appears 2,854
times of 5,198 (55%) and 1 appears 964 (19%). A station id visited once per
call would be roughly even across its values. The per-service sequences also
alternate 0,1,0,1 rather than progressing.

Values 0-24 with 255 as a sentinel look more like a **platform number**, and
that fits the route: Edinburgh Waverley platforms run to 20, and the observed
set includes 18, 21, 23, 24. 0 would be "no platform".

### A second bug this exposed

`inspect_field` was segmenting the RUN list rather than the record list, so
its services had overlapping times and wildly uneven run counts - it was not
the same segmentation the timetable uses, so the sequences could not be
compared against it. It now segments the records exactly as
`extract_timetable` does, including the minimum-size rule.

### Next step

The station identity is still missing, and the search for it should now be
repeated with byte and 16-bit widths included - that is a genuinely new
search, not a repeat, because those widths were never scanned before.

Run **Find call field** again with 39 and 13 entered. Then **Inspect field**
on any candidate whose distinct count is near 13, selecting the right width
in the new dropdown. A station field should show ~13 distinct values spread
evenly, progressing through a service rather than alternating, and reversing
on return workings.

Also worth inspecting **+695 as a byte** to confirm the platform reading, and
**+696-698 (40 runs, 2 distinct)** which looks like a direction flag.

## +695 is a PLATFORM number, not a station (v7.56.0)

Three captures at different widths settle it:

```
+695 as u8 : 14 values - 0,1,2,3,4,7,9,12,13,18,21,23,24 and 255
+696 as u8 : 2 values - 0 (5167) and 255 (31)
+696 as u16: 2 values - 0 (5167) and 65535 (31)
```

255 appears 31 times at +695 AND 31 times at +696; 65535 - two FF bytes -
appears 31 times as a u16; and the original int32 scan showed -1 exactly 31
times. So bytes 695-698 are all FF in the same 31 records: **the field is an
int32 at +695 using -1 as a "none" sentinel**, and its real values are
0,1,2,3,4,7,9,12,13,18,21,23,24 - exactly **13** of them.

Thirteen values, and a Leven-Edinburgh service calls at thirteen stations.
Every count-based test says "station".

**It is not.** The distribution:

```
value   0: 2854 records (55%)      even share would be 8%
value   1:  964 (19%)
value   2:  447 (9%)
value   9:  304 (6%)
```

A train calls at each station on its route once, so a station field has to be
roughly EVEN. This one is dominated by 0 and 1. Values 0-24 with -1 for
"none", skewed heavily to the low numbers, is a **platform number** - and
Edinburgh Waverley's platforms run past 20, which is where 18, 21, 23 and 24
come from.

### The lesson, and `find_station_field`

Run counts and distinct counts have now pointed at the wrong field twice
(+407 for services, +695 for stations). Both times the count matched and the
BEHAVIOUR did not.

`/api/paks/station_field`, button **Find station field**, scores on
normalised Shannon entropy over the StopPoint records instead: 1.0 means
every value equally common, 0 means one dominates. A station field over 13
stations sits near 1.0; the +695 platform field would score about 0.5.
Sentinels (-1, 255, 65535) are excluded before scoring, since "none" would
otherwise drag the evenness down.

The fixture now plants BOTH an even 13-value station index and a skewed
13-value platform decoy, so the test fails unless the tool can tell them
apart. It picks the station field at evenness 0.9999 with a top share of
0.081, and ranks the decoy below it.

Also fixed: `inspect_field` reported `"last": null` on every real capture,
because the final run often carries no time. It now walks back to the last
run that has one.

### Next step

Run **Find station field**. If a field comes back with ~13 evenly spread
values, that is the station index, and the per-service sequence from
**Inspect field** will give the station ORDER - which is what stop labelling
needs.

If nothing scores above 0.85, station identity is probably not an integer in
these records at all, and the next candidates are the `RibbonLocation` /
`NetworkRibbonLocation` FName values, which name track positions directly.

## Evenness alone finds FLOATS, not indices (v7.56.1)

`find_station_field` returned +105 with 16 values at evenness 0.9915 and
called it a station field. It is not. The values:

```
64 65 66 67 68 69 70 71 72 73 74 75 76 77 78 79
```

**Contiguous, starting at 64** - that is the exponent byte of a float32 in
the range ~2 to ~5e5. It is a Distance or a coordinate.

The other high scorers are the same mistake:

| offset | values | what it is |
|---|---|---|
| +200 | 0,32,64,96,128,160,192,224 | top three bits of a mantissa byte |
| +410 | 13,14 | a low-variance float exponent |
| +474 | 64..79 | another float exponent byte |

All above 0.98, none an identifier. **A float varies smoothly, so every byte
of it is near-uniform - which is exactly what an entropy score rewards.**
Evenness was necessary to reject the platform field, but it is not
sufficient, and on its own it is actively misleading.

### Fixed

  - Values that are CONTIGUOUS and start at 32 or above are rejected as
    float exponent slices. A real index starts near zero and need not be
    contiguous.
  - Values evenly spaced by a power of two (16/32/64/128/256) are rejected
    as mantissa slices.
  - Ranking is now by closeness to the expected station count FIRST, with
    evenness as the tie-break among fields that already have the right
    shape. The count is the stronger constraint; ranking by evenness put a
    float above every genuine candidate.
  - The acceptance window narrowed from +-4 values to +-2.

The fixture now plants a float decoy alongside the station index and the
platform decoy, so the test fails unless both traps are rejected.

### Where this leaves station identity

Three searches have now come back negative, and each ruled something out:

  - **run counts** -> +407 (services) and +695 (stations): count matched,
    behaviour did not.
  - **evenness** -> float slices.
  - **+695 specifically** -> a platform number, 0-24 with -1 for none.

No integer field in the record behaves like a station index. That is a real
finding rather than a failure: it means station identity is very probably
**not an integer in these records at all**.

### Next step

Stop looking for integers. The `RibbonLocation` and `NetworkRibbonLocation`
fields hold FName references - names, not numbers - and a station call IS a
position on the network. The right question is which NAME each StopPoint
record points at, and whether consecutive records sharing a name are one
call.

That needs a scan over FName references at a fixed offset within the record,
resolved through the name table, rather than an integer histogram. The 88
names in this layer's table include the P2K/S5K ribbon ids, so the values
should be readable directly.
