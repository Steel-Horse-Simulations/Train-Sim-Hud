# TSW Hud — session handover

**App version at end of session: 8.0.3**

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

## What changed in v7.60.1 - the parser works on the REAL file

FCE_Timetable_TT.uasset: 2,328 names, 21.3 MB payload -> **500 services,
2,083 named stops, 42 stations**, in correct geographic order with plausible
intervals (Kirkcaldy > Kinghorn > Burntisland > Aberdour > ... > Waverly).

Three faults the real run exposed, all fixed:
  - explicit and simulated times were mixed INDEPENDENTLY, pairing an
    explicit arrival with a simulated completion -> dwells like 05:57->17:55.
    Now one source supplies both halves of a pair.
  - 0 headcodes of 500: the asset has no HeadCode property, the code is in
    the service name (1L86_B, P1L86). derive_headcode() extracts it - and the
    regex must NOT use \b, since an underscore is a word character and every
    real name failed.
  - the 500 cap silently truncated and reported 500 as the answer. Now 5,000
    with a `truncated` flag.

**Next: ribbon GUID + offset -> lat/long** from the route definition asset's
geometry, putting every stop on the map without driving.

## What changed in v8.0.3 - diagnose an unrecognised timetable layout

NorthLondonLine still fails: `NL_Timetable_378.uasset -> no_named_stops`, and
only ONE candidate exists so there is nothing to fall through to.

The service array name is a GUESS - the parser only accepted `Services` or
`ServiceDefinitions`, taken from the Fife Circle asset. If this route names it
something else, the parse returns nothing and says nothing useful.

The parser now records every top-level property it walks past and, when it
finds no named stops, returns `array_properties`, `top_level_properties` and
`service_array_names_tried`. The Routes page carries the array names into the
failure line: `NL_Timetable_378.uasset -> no_named_stops (contains: ...)`.

SERVICE_ARRAY_NAMES is now a named constant - add to it when a real one turns
up.

**Next: re-run NorthLondonLine and read the "contains:" list.** If one of
those arrays is the service list, its name goes in the constant and the route
parses. If the list is empty or obviously not a timetable, that asset is a
loco-DLC timetable fragment rather than the route index, and the route's real
timetable is in another pak.

## What changed in v8.0.2 - RivieraLine: pick the RIGHT timetable asset

`no_services_parsed - it may be a DataTrack layer rather than the timetable
index`. It was not a DataTrack; the extractor made ONE guess at which asset to
read and picked wrong.

Two causes:
  - `save_scanned_routes` merged `timetables` (found by FOLDER - the real
    index) with `timetables_by_name` (found only by an `_TT` suffix - a weak
    match), so a ServiceMode or oddly-named asset could sort ahead of the
    route's actual timetable. They are now stored separately.
  - the extractor took the first non-DataTrack asset and gave up if it
    yielded nothing.

Now `_timetable_candidates()` RANKS them - asset directly in Timetable/ first,
then other folder matches, then `_TT` name matches, DataTracks last, scenario
and training timetables excluded entirely - and extraction TRIES each in turn
until one produces named stops. A route only fails once every candidate has
been tried, and the error lists what was tried and why each failed.

## What changed in v8.0.1 - two bugs the Routes page exposed

**The version had been stale since 7.61.** Every bump was done by matching the
PREVIOUS literal string; once that drifted the replace silently did nothing,
and every later bump missed too. The app reported 7.60.1 while the docs
claimed 8.0.0. Fixed by regex, and `tests/bump_version.py` now FAILS if the
edit is a no-op. `eval_pipeline.run_version_declared()` asserts app.py and the
spec agree - it caught the spec still saying 7.60.1 the moment it was added.

**Every route read as "0 services - read".** A cooked asset keeps its data in
a sibling `.uexp`, and extraction was unpacking only the `.uasset` (include=
the exact file). The `.uasset` alone parses CLEANLY and yields nothing - no
error - so the route was marked read on a total failure.

Three changes, because the bug had three chances to be caught and took none:
  - unpack the DIRECTORY, not the single file, so the .uexp comes too;
  - `uasset.Package` flags `uexp_missing` when a cooked header has no payload,
    and the parser returns `uexp_missing` rather than an empty result;
  - an empty parse is treated as a FAILURE - the route is marked `failed`
    with the reason shown, never "read".

## What changed in v8.0.0 - the Routes page

New page pages/routes.html (in the nav). Find routes with timetables -> lists
every pak containing one, as pills like Known Trains. Per-route "Scan
timetable", plus "Scan all new routes".

SCAN and EXTRACT are separate on purpose: a scan is cheap and re-run on every
DLC install, an extraction is not. save_scanned_routes() only refreshes SCAN
fields, never last_extracted/services_extracted - so a rescan cannot wipe the
record of what was already read. Regression test asserts it.

Points to remember:
  - route_key strips the TS2Prototype-WindowsNoEditor- prefix (TSW renames it
    between versions; the key must stay stable)
  - extraction prefers a NON-DataTrack asset - DataTrack has the running
    profile with no station names, the index asset has the schedule
  - /api/routes/scan DELEGATES to /api/paks/scan_all rather than duplicating
    its folder detection (paks land in both Content/DLC and Content/Paks)
  - after unpack the asset is found by NAME - some repak builds ignore
    --include and unpack everything
  - failed paks are skipped, not stored; failures shown in the page

New table: scanned_routes in timetables.db.

## What changed in v7.62.1 - midnight confirmed, last 145 explained

Real re-run: P2K85 now 23:03 -> 00:09:30 with Cameron Bridge 00:04:30 and
Leven 00:09:30. named_stops 2,663 -> 3,043; 10 past-midnight stops recovered.

The 145 "unexpectedly empty" were CONTINUATION LEGS. Names: 1L86_B,
1L30_1_B, 2P02-B, 2G16B, 1L96_C - the suffix takes several forms and only _B
and _End were recognised (44 caught, 101 missed). 122 of the 145 share a
headcode with a service that HAS stops, which is what a continuation is.
_classify_role() now handles _End, _B, _1_B, -B and a bare trailing capital
(the last only when the rest of the name IS the headcode).

Final state: 820 services, 3,043 named stops, arr==dep on 10 of 3,044.

## What changed in v7.62.0 - both open questions resolved

**Untimed tails = a MIDNIGHT bug in my code.** Only 3 of 585 services, and
they were the last two of the day terminating at Leven. A Timespan keeps
counting past midnight - 00:10 is stored as 24:10 - and _hms() rejected
anything >= 24h, discarding those calls. Now wrapped for display with a
next_day flag. The flag matters: "00:10" sorts before "23:50" as text, so a
list ordered by displayed time would invert the journey. The TEST also
compared strings, which is why it flagged a correct midnight service.

**235 zero-stop services are CORRECT.** By headcode class: class 5 (EMPTY
COACHING STOCK) is 90 empty of 91. A positioning move has nowhere to call.
Parser now reports headcode_class / service_class / expects_calls and splits
the count into empty_stock_or_light_engine vs unexpectedly_empty.

Confirming check: instruction-to-stop ratio median EXACTLY 2.00, which is
what a pure GoTo+LoadUnload timetable gives - the pairing consumes
instructions correctly rather than skipping them.

## What changed in v7.61.1 - pairing confirmed on real data, two fixes

Real re-run: arrival == departure on 10 of 3,035 stops (was 80%), 2,630
dwells with a median of 30s. P2K85 reads as a proper timetable - Haymarket
23:06:30/23:07:00, Edinburgh Gateway, Dalmeny, ... Kirkcaldy, all p2, 30s
dwells.

Fixed: (a) 382 NAMELESS stops - a service starting at a platform stores a
LoadUnload with no destination followed by the GoTo that names it; the
location now comes from that GoTo. (b) dwell reported without times - a
dwell now requires BOTH arrival and departure.

STILL OPEN: 235 services with 0 stops (mostly _B AI continuations with 1-2
instructions - likely repositioning legs, but that is inference not
measurement), and the final stops of a service carrying no times.

## What changed in v7.61.0 - the GoTo/LoadUnload pairing rule

First real run: 820 services, 3,263 named stops, real stations in correct
geographic order. Approach confirmed. Two bugs found.

**A call is a GoTo PAIRED with the following LoadUnload.** GoTo is routing and
carries no times; the LoadUnload after it carries ArrivalTime/CompletionTime.
Reading every GoTo as a stop gave 80% of stops with arrival == departure, all
instructions typed GoTo, 99% is_stopping. Now: GoTo+LoadUnload = one call,
lone GoTo = passed through, leading LoadUnload = service starts here,
Couple/Uncouple = yard work.

**The 263 single-stop "fragments" were NOT a bug** - 1L86_B is the AI
continuation of P1L86. TSW splits a working across player and AI legs sharing
a headcode. Now LABELLED via `role`, never merged or dropped. service_name +
role stored, with a column migration for existing DBs.

Fixture rewritten to store times where the GAME does (on the LoadUnload) -
it previously put them on the GoTo, which let the broken parser pass. Test
also keyed on headcode, which compares the wrong record once two share one;
keyed by name now.

**Next: re-run Read timetable (named stops).** Expect fewer, better stops with
real dwells; the check is arrival no longer equalling departure.

## What changed in v7.60.0 - THE TIMETABLE PARSER

The user supplied github.com/hcfairbanks/tsw_projects, which settles it: the
other app parses the **RouteTimetableDefinition** (the INDEX asset), never the
DataTrack layers. Each Instruction carries Destination.Name (the STATION),
ArrivalTime, CompletionTime and bIsStopping on the SAME record - there was
never a join to find, which is why four searches for one came back negative.

NEW: `uasset.py` - real package reader (header, name map, exports, .uexp).
NEW: `timetable_definition.py` - Services -> Instructions -> named stops.
NEW: `/api/paks/timetable_definition` + **Read timetable (named stops)**.
Stored in extracted_services/extracted_calls with station_name + platform.

Validated against tests/synth_ttdef.py, which writes a REAL .uasset/.uexp
from the format spec (not from the parser). All domain rules planted and
respected: first stop departure-only, last arrival-only, freight no arrivals,
passed stations excluded from the call list, AI services read from
SimulatedArrival/CompletionTime.

The DataTrack work is not wrong, just aimed at the running profile rather
than the schedule. Kept, but no longer the route to a timetable.

**Next: run Read timetable (named stops) on the real FCE_Timetable_TT.uasset.**
Then ribbon GUID + offset -> lat/long, which needs the route definition
asset's geometry and would map every stop without driving.

## What changed in v7.59.0 - download fixed, timetable banked, recorder built

**Known Trains download, two bugs.** (a) After backing up it clicked an
anchor to /api/known_trains/export - in pywebview the WEBVIEW navigates to
the JSON and the page disappears. Removed; paths now shown in-page with an
"Open backups folder" button. (b) uploadData posted to /restore which read
only body["classes"], but the backup is {"tables":{...}} - so restoring a
real backup imported NOTHING and reported success. /restore now takes both
shapes, routes full backups through import_everything, 400s on junk, and
reports row counts.

**Timetable banked.** extracted_services / extracted_calls in timetables.db;
Extract timetable saves automatically (39 services, 485 calls);
/api/timetable/extracted reads back. Re-extraction REPLACES rather than
merges - derived data, a better parser should supersede. station_name column
exists but NULL so labelling is an UPDATE later.

**Drive recorder.** drive_recorder.py + /api/drive/record|status|match +
Record drive / Match driven names buttons. Reuses /api/journey so it shares
the upstream lock. Closest approach must compare ABSOLUTE distance - the
value goes negative past a station, and a plain < kept the point furthest
PAST it (-1800m in the first test). Matching is REPORTED not applied, since
the asset holds both "Edinburgh Waverley" and "Edinburgh Waverly".

**New test: tests/eval_pipeline.py** - backup/wipe/restore round trip,
timetable banking, recorder + matching. Note its first failure was the
HARNESS: modules set DB_PATH absolutely from __file__, so os.chdir does not
redirect them.

**Next: drive the Leven branch with Record drive on**, then Match driven
names. That produces the station-to-position mapping that labels the stops.

## What changed in v7.58.1 - REAL STATION NAMES EXTRACTED

FCE_Timetable_TT.uasset -> 2,329 names -> **61 stations, 40 track features,
416 headcodes**, saved to route_stations / route_headcodes.

Real: Leven 1/2, Cameron Bridge 1/2, Glenrothes with Thornton 1/2, Kirkcaldy,
Haymarket 0-4, Edinburgh Waverly (DTG's spelling) with 18 platforms including
1a/1b/2a/2b/10a/11b. Headcodes 1E01, 1R03, 1B61 with _End variants.

First pass returned 102 "places" - 41 wrong, in three ways, all now fixed and
locked in by a regression test built from the REAL names:
  - engine identifiers (JunctionID, YardManager) - rejected by CamelCase shape
  - track features (Portal - ..., Up Fife Line, Passenger Loop, Siding, Depot,
    operators like Avanti West Coast) - kept SEPARATELY as `infrastructure`,
    not discarded, since a stop's position may need them
  - classification ran BEFORE the platform split, so "Eastfield Through
    Siding 5" got through; it now runs after
  - platforms sorted as strings (10a before 2a); now numeric with letter
    tiebreak, which matters as Waverley's 1a/1b/2a/2b are distinct platforms

**Next: join names to stop times.** Likely key is Distance - the stop records
carry it and the live API's DriverAid.TrackData gives stationName with
distanceToStationCM. One drive along the branch produces the mapping.

## What changed in v7.58.0 - station names read from the game files, and stored

find_name_fields on the real layer: only TWO offsets in the whole 707-byte
record resolve to names, both EDirectionOfTravel (+369, +410). No place names
in this layer. Station identity is NOT there - confirmed four ways now (run
counts, evenness, +695 platform field, name references).

Oddity for later: +410 has 15 runs over 5,198 stop records. A direction flag
across 39 services should change ~39 times.

`read_station_names()` / `/api/paks/stations` / **Extract station names**
reads the INDEX asset (no .uexp - the name table IS the payload) and
classifies three kinds of name: headcodes (1E01 form, _End variants),
stations (place-like, usually with a platform), machinery. Platform
designators are split so "Aberdour Platform 1/2" collapse to one place.

Stored in NEW tables `route_stations` / `route_headcodes` in timetables.db,
deliberately separate from journeys so re-importing game files cannot disturb
driven data. Idempotent via ON CONFLICT DO UPDATE.

Validated: 13 places / 25 entries / 208 headcodes exact, machinery excluded,
re-import does not duplicate.

**Next: point the asset box at FCE_Timetable_TT.uasset** (Content/Timetable/,
NOT DataTracks/) and press Extract station names. Expect ~88 entries and 208
headcodes. Then the join to stop times - Distance is the likely key, since
both the records and the live API carry it.

## What changed in v7.57.0 - the name scan

Three integer searches negative (run counts -> wrong fields, evenness ->
float slices, +695 -> platform number). So stop treating fields as integers.

`find_name_fields()` / `/api/paks/name_fields` / **Find name fields** resolves
FName REFERENCES at each record offset through the name table and asks which
NAME a StopPoint record points at. Right question because a call is a network
position and the record's fields are literally called RibbonLocation /
NetworkRibbonLocation.

Index + Number(0) pairing is required, which is what separates a real
reference from a small integer - with 88 names, 29% of offsets pass the index
test alone. Ranked by place-like share so machinery names (Class, Guid,
DataType) cannot win.

Tested BOTH ways, since a negative is the likely answer: finds planted ribbon
references (13 names, 100% resolve, 100% place-like), and reports nothing when
they are stripped out.

**Next: Find name fields, 13 in the calls box.** Place-like names = the track
positions, which settles call grouping AND gives labels. Nothing found =
station identity is not in this layer at all; fall back to the index asset or
the live API's DriverAid.TrackData stationName.

## What changed in v7.56.1 - evenness finds FLOATS, not indices

find_station_field picked +105: 16 values, evenness 0.9915. The values are
64..79 CONTIGUOUS - the exponent byte of a float32. It is a Distance. Other
high scorers: +200 = {0,32,64,...,224} (mantissa top bits), +410 = {13,14}
(low-variance exponent), +474 = another exponent byte. All above 0.98, none
an identifier.

A float varies smoothly so every byte of it is near-uniform - exactly what
entropy rewards. Evenness was needed to reject the platform field but is not
sufficient alone.

Fixed: reject contiguous runs starting >=32 (float exponents) and values
spaced by a power of two (mantissa slices); rank by closeness to the expected
station count FIRST with evenness as tie-break; window +-4 -> +-2. Fixture
now plants a float decoy too.

**Station identity is probably NOT an integer in these records.** Three
searches negative: run counts (+407, +695), evenness (floats), and +695
specifically (platform, 0-24 with -1 sentinel).

**Next: stop looking for integers.** RibbonLocation / NetworkRibbonLocation
hold FName references, and a station call IS a network position. Needs an
FName scan at a fixed record offset resolved through the name table, not an
integer histogram.

## What changed in v7.56.0 - +695 is a PLATFORM number

Three width captures settle it: 255 appears 31x at +695 and 31x at +696,
65535 appears 31x as u16, int32 showed -1 31x. Bytes 695-698 are all FF in
the same 31 records, so it is an int32 at +695 with -1 as a "none" sentinel.
Real values: 0,1,2,3,4,7,9,12,13,18,21,23,24 - exactly 13, matching the 13
stations on a Leven-Edinburgh run.

NOT a station. 0 takes 55% of records, 1 takes 19%, where an even share is
8%. A train calls at each station once, so a station field must be even.
0-24 skewed low with -1 for none = PLATFORM (Waverley runs past 20, hence
18/21/23/24).

Count-based searches have now picked the wrong field twice (+407 services,
+695 stations) - count matched, behaviour did not.

`find_station_field()` / `/api/paks/station_field` / **Find station field**
scores normalised Shannon entropy over StopPoint records. Station ~1.0,
platform ~0.5. Sentinels excluded. Fixture plants BOTH an even station index
and a skewed platform decoy; tool picks station at 0.9999 / top share 0.081.

Fixed: inspect_field reported "last": null on every capture (final run has no
time); now walks back to the last run that has one.

**Next: Find station field.** >0.85 evenness with ~13 values = the station
index, and Inspect field then gives station ORDER. Nothing above 0.85 means
station identity is not an integer - try RibbonLocation FName values.

## What changed in v7.55.0 - int32-only scanning was a blind spot

+692's 14 values are all multiples of 2^24. Divided out: 0,1,2,3,4,7,9,12,13,
18,21,23,24,255. It is a single BYTE at +695 caught in the top byte of an
int32 - which is also why +692..+695 all reported identical runs/distinct.

EVERY field scan read int32 only, so byte fields were invisible or reported
as four meaningless offsets. Scans now cover u8/u16/i32, de-duplicate a field
found at several widths, and keep the reading with the most distinct values.
New width dropdown on Inspect field.

The byte is NOT a station id: 0 appears 55% of the time and 1 another 19%,
and sequences alternate 0,1,0,1. Values 0-24 with 255 sentinel look like
PLATFORM NUMBERS (Waverley runs to 20; observed set includes 18,21,23,24).

Also fixed: inspect_field segmented the RUN list not the record list, giving
services with overlapping times that could not be compared against the
timetable. Now segments records exactly as extract_timetable does.

**Next: re-run Find call field** - with byte/16-bit widths this is a genuinely
new search. Then Inspect field on any candidate with ~13 distinct values.
Also inspect +695 as a byte (platform?) and +696 (40 runs, 2 distinct -
direction?).

## What changed in v7.54.0 - +692 has 14 distinct values; 13 stations

find_call_field found no 507-run field, but the CANDIDATE LIST holds the
find: **+692-695, 429 runs, 14 DISTINCT values**, 12.12 records per run. A
Leven-Edinburgh service calls at 13 stations. Random fields do not land on
14, and 12.12 matches the ~10.7 records-per-call ratio.

429 vs 485 is explainable: runs sharing a value merge, including across the
38 service boundaries (485 - 38 = 447).

Also: +696-698 has 40 runs / 2 distinct - almost exactly the 39 services,
likely a direction flag.

Bug found: expected_calls was sent as 39x13 = 507, true total 485 (two
Glenrothes shuttles have 2 calls). A 4.5% overshoot against a 5% tolerance
could reject a correct field. Widened to 15%; candidates always returned.

`inspect_field()` / `/api/paks/inspect_field` / **Inspect field** + a Field
offset box dumps a field's VALUES per service. Statistics say 14 states; only
the sequence says whether they are stations - it must visit 13 per service,
reverse on return workings, and show 2 on the shuttles.

**Next: Inspect field, offset 692, services 39.** 13 distinct per service in
order = station identity AND station order, which is what stop labelling
needs.

## What changed in v7.53.0 - time clustering does NOT give station calls

The tuned run reported median 13 calls at a 65s gap. It was FITTED. The
plateau check caught it: median 13 occurs at exactly ONE of 77 gap values,
and the curve is a smooth continuum 45->1 with no flat step. The fixture,
built with real separation, has a 64-value plateau. A continuum means the
times carry no grouping, so the number came from the threshold.

Had call_gap_curve not been reported, "median 13" would have looked like a
triumph. Check the PLATEAU, never the answer.

Rules out: StopPoint records are not tight groups of ~11 per call separated
by running time. The 10.7 ratio is real arithmetic but not a layout.

`find_call_field()` / `/api/paks/call_field` / **Find call field** applies the
service-field approach over StopPoint records only, targeting 485 runs
(37x13 + 2x2). Validated both ways: finds a planted call id (485 runs, 485
distinct, 11 per run), refuses a count that is not there.

**Next: Find call field with 39/13 entered.** If nothing, try
RibbonLocation/NetworkRibbonLocation values as the grouping - a call is a
position on the network. Also still open: the 3-record on-the-minute service
headers from v7.51.1, best candidate for a headcode.

## What changed in v7.52.0 - call-gap tuning, fragment floor

record_template independently corroborates the time field: it places the
"Time" property NAME at +175, and 175 + 8 + 8 + 8 + 1 = 200, exactly Unreal's
FPropertyTag header and exactly the time VALUE offset found statistically.
Two unrelated methods, same byte.

Known-count cut with 39 gives the right shape (11-14 calls). Two fixes:
  - FRAGMENTS: 3 of 38 cuts went on 35-50 record segments while 3 real
    boundaries were missed. Cuts leaving a segment below a floor are now
    rejected and the next strongest taken.
  - CALLS: fixed 90s gap gave median 11 vs the 13 counted in game.
    `expected_calls` now sweeps the gap and picks the value reproducing the
    known count, returning `call_gap_curve` so the choice is auditable -
    a broad plateau (64 values on the fixture) means found, a spike means
    fitted.

New "Station calls per service" box next to Services on Discovery.

**Next: re-run with 39 services / 13 calls.** Check the plateau is broad.

## What changed in v7.51.1 - reject near-miss service fields

With 39 entered, a field at +407 with 37 runs was accepted (10% tolerance) and
used instead of the known count. It was wrong in both directions: split 11
services 3 records early (leaving 3-record fragments) and failed to split 5
boundaries at all, giving blocks of ~1070 points where a service is ~355.
18 + 5x3 + 2 = 35, not 39. Tolerance is now exact-within-1.

DISCOVERY worth following up: those 11 fragments are exactly 3 records, no
stops, no duration, each at the exact start time of the service that follows,
all ON THE MINUTE while every other time in the file is to the second. That
looks like a per-service HEADER - and the most likely home for a headcode,
which would give services names this layer otherwise lacks.

## What changed in v7.51.0 - station CALLS, and ground truth from the game

User counted the in-game timetable: 39 Leven services, 37 Edinburgh<->Leven
calling at 13 stations, 2 Glenrothes-Leven calling at 2. That is 485 calls
against 5,198 StopPoint records = **10.7 records per call**. So a StopPoint
is NOT a station call, and every "stop count" reported before this was a
record count. extract_timetable now clusters records into calls (call_gap,
default 90s), arrival = first time in the cluster, departure = last.

find_service_field on the real file found NOTHING - services are not
identified inside the record. It now reports near-misses and run-count bands
rather than an empty table, and takes expected_runs.

Segmentation therefore uses the KNOWN count: rank every transition by
boundary strength (backwards clock beats any forward gap) and cut at the
strongest N-1. New "Services" box on the Discovery page feeds it.

Validated on a fixture built to the real shape: 39/39 services, median 13
calls, both 2-call Glenrothes workings recovered, every call with an arrival
and a departure.

**Next: enter 39 in the Services box, Extract timetable.** Two short services
appearing among 37 long ones is the check.

## What changed in v7.50.0 - full extraction, and structural segmentation

Real Leven layer: 12,207 records, stride 707, type +270, time +200, and ALL
six type counts exact (5198/5198/908/36/27/4). Layout and fields are settled.

Open question: the clock heuristic says 36 services of ~152 stops; the earlier
statistical method said 104. The clock CANNOT arbitrate - the largest gap
between consecutive stops in the whole file is 396s, so the 600s threshold
never fires, and lowering it hits real 6.6-minute running gaps.

`find_service_field()` / `/api/paks/service_field` / **Find service field**
scans every offset for a value that holds in runs. Runs = services. Scored on
runs and distinct values AGREEING, which rejects alternating flags. Validated:
40 runs / 40 distinct against 40 true services on a fixture, where the clock
gave 26. extract_timetable uses it when found and reports `segmented_by`.

**Next: Find service field on the real layer.** ~104 runs vindicates the
statistical result; ~36 vindicates the clock; nothing found means the clock is
genuinely all there is.

## What changed in v7.49.1 - chain bug: 91 records instead of 12,207

First real extract_timetable run stopped 0.62% into the file - 91 records, 1
service - while reporting layout_confirmed: true. _stride_chain required
alignment to a GLOBAL grid; 0.29% of real gaps are not 707, and the first one
put everything after permanently off-grid.

Now LOCAL: a stray sits less than one stride from the previous genuine start,
a real start is at least a stride away. The exact-stride position is
PREFERRED (so the chain re-locks and stays exact) but never required (so a
bad gap costs one record, not the rest of the file).

Verified exact on a clean fixture AND after injecting a 251-byte phase shift
mid-file - the real failure reproduced deliberately.

The 91 records did confirm the layout: time at +200 scored 0.9889 rising
against 0.9444 for the +233 duration decoy and 0.5333 for the +139/+140 pair
(35 resets - not a clock). 40 StopPoints and 40 TrackSectionEntry, the same
1:1 seen whole-file at 5198 each.

**Next: re-run Extract timetable.** Expect ~12,207 records, ~5,198 stops,
service count to compare against extract_time_series' independent 104.

## What changed in v7.49.0 - RECORD LAYOUT SOLVED, timetable extracted

The real Leven layer decoded and CONFIRMED: 12,207 records, stride 707
(99.71% of gaps), type at +270, 93% typed, and all six whole-file type counts
reproduced exactly (4/27/36/908/5198/5198).

`extract_timetable()` / `/api/paks/timetable` / **Extract timetable** button
now produces services with their stop times. The time field is chosen by
ASCENDING behaviour, not plausibility - five offsets passed "is a tick count",
including +139/+140 which are the same bytes read one byte apart, and +233
which is a constant duration. +200 ascends and resets; that is the schedule.

Two things worth remembering:
  - offsets are relative to the ANCHOR (inside the record), not the record
    start, so the GAP between type and time is the invariant;
  - stray anchor hits must be filtered to the stride grid or phantom records
    split services in two (40 -> 58 on a fixture before `_stride_chain`).

Service segmentation is the ONLY heuristic left (600s gap, adjustable). It
cannot be exact - real running times reach 5.6 min while services start
minutes apart. CROSS-CHECK against extract_time_series' independent 104 runs.

**Next: Extract timetable on the real Leven layer.** Near 104 services means
segmentation is sound and the rest is writing to timetables.db. Station names
are still missing from this asset entirely.

## What changed in v7.48.1 - anchor selection fixed (timetable work)

The v7.41.0 decode run's "confirmed: false, stride 196, 19% coverage" was
meaningless: it anchored on `EnumProperty` (61,036 refs) instead of `Class`
(12,207), because scoring was count x evenness and 5x the references beat
better spacing. The 19% was 11,371 hits over 61,036 fake records - against the
real 12,207 that is 93%.

That run DID prove the type field is real: all six whole-file counts
reproduced exactly from a fixed offset (4/27/36/908/5198/5198), including
ActionPoint at 4.

Anchors are now scored by MODAL GAP DOMINANCE - what fraction of gaps between
a name's references are identical. Once-per-record names score ~0.95;
five-per-record impostors and coincidences do not. Also consolidated two
duplicate anchor pickers that had drifted and disagreed.

**Next: Recover record template, then Decode fixed records, on the real Leven
layer.** Expect anchor Class, 12,207 records, stride 707.

## What changed in v7.48.0 - Download button fixed, and it was never a backup

Reported as "the download button does nothing". It built a Blob and called
a.click(), which pywebview ignores entirely - worked in a browser, dead in
the app.

MUCH more serious: it was saving /api/known_trains/list, the DRIVEN-ONLY
resolved view. On a seeded DB the old backup captured 1 of 3 trains, omitting
never-driven catalog rows, hidden rows, variants, subclasses, families,
operators, liveries and aliases. Restoring it after a wipe would have lost
most of the data while appearing to succeed.

Now: /api/known_trains/backup writes JSON + a copy of the .db server-side to
<app>/backups/, verifies by reading back and comparing row counts, and reports
the paths. /api/known_trains/export serves a normal download for browsers.
/api/known_trains/import restores (merge by default, ?replace=1 to overwrite).

Import must run PARENTS FIRST - the first test restored trains and operators
but zero liveries, because alphabetical order put operator_liveries before
operators and every FK failed silently. Skipped rows now record their reason.

Verified: seed -> backup -> DELETE all tables -> restore -> identical counts,
0 skipped.

## What changed in v7.47.0 - overlay fixed with REAL tile measurements

Five attempts failed because all were validated against synthetic tiles. Two
real tiles from the user overturned three assumptions at once:

1. **Tiles are 512px, not 256.** The canvas drew them into a 256 canvas and
   Leaflet scaled back up - that resample was the soft, doubled edges all
   along, not the colour maths. Now TILE_PIXELS=512 backing / TILE_CSS_PX=256
   footprint.
2. **ORM tiles contain labels** (#0000ff text, #ffffff halos) which the
   whole-tile recolour turned into the dark smears seen in every screenshot.
   recolourPixels() now classifies per pixel - orange family = track,
   everything else dropped.
3. **REFERENCE_LINE_LUM was nearly right** (real 146.5 vs guessed 137), so it
   was NOT the cause of the near-black output. Don't blame it if darkness
   returns.

Measured on the real tile with livery #1e467d: running line -> #1e467d
exactly, tunnel -> #2b64b3 (lighter), 0 label pixels surviving.

crossOrigin retries without CORS on failure (tile still draws, blend path
used). Stencil dilation 1px -> 4px at 512, measured from real line widths.

## What changed in v7.46.0 - overlay fixed; the luminance floor was the cause

Diagnostic screenshots: raw CRISP, full and no-stencil both washed out and
near identical. So not the tiles, not the zoom, not the stencil - the
recolour, and specifically v7.45.1's `lighten` pass to LUMINANCE_FLOOR, which
floored every pixel at 30% grey before the `color` blend. That washed the
mid-tone lines out and flattened line-vs-casing contrast, and the pale result
was then faithfully preserved. It also explained the "colour has gone light"
report - the livery was fine, the luminosity under it was destroyed.

LUMINANCE_FLOOR -> null, LIFT_DARK_LIVERIES -> false (tintColour's 50%
lightness floor was the other half of the same mistake). Both kept as
constants for a dark basemap someday.

Added lightness RE-ANCHORING: scale by (livery luminance /
REFERENCE_LINE_LUM) so an ordinary running line lands ON the livery colour
while tunnels stay lighter and casings darker.

Measured: luminance error vs raw 17% -> 5%. Tunnel and casing ordering
preserved. Caveat: dark saturated liveries come out slightly muted
(#1e467d -> #344862); mid-tone ones land almost exactly.

## What changed in v7.45.2 - overlay diagnostic

Overlay still washed out after two fixes, both reasoned rather than measured
(ORM is unreachable from the sandbox; all testing has been against synthetic
tiles). Added **Overlay: full / no-stencil / raw** on the map, which
re-composites the same already-downloaded tile bytes three ways so the
responsible stage can be identified instead of guessed at.

raw faded -> not our compositing (zoom/DPR/style upstream).
raw crisp + no-stencil faded -> the recolour.
no-stencil crisp + full faded -> the gauge stencil misaligning.

## What changed in v7.45.1 - overlay washed out / tunnels missing

v7.43.0's claim that "tunnel translucency lives in the alpha channel" was
WRONG. ORM draws tunnels as a PALER SHADE, not lower opacity, so the flat
`source-in` recolour erased the distinction. Now uses the `'color'` blend
mode (hue+sat from the fill, luminosity from the artwork), re-clipped to the
artwork's alpha afterwards, with a `'lighten'` pass to LUMINANCE_FLOOR first
so near-black casings stay visible on our dark basemap.

Washed-out look was three things: the raw anti-aliased stencil softening every
edge via destination-in alpha multiplication, layer opacity 0.85, and a
drop-shadow glow. buildStencil() now hardens the stencil (redraw on itself 6x,
alpha 1-(1-a)^n), opacity is 1, glow gone, dilation 2px -> 1px.

Measured: partial-alpha edge pixels ORM 34.1% vs ours 29.5%, widths identical
at 14px, tunnel luminance 204 vs open track 147.

## What changed in v7.45.0 - subscription API

`tsw_subscriptions.py` + `/api/subscriptions`. Subscribes the hot paths once,
then reads them all in one GET. Mock measurement over 10s of the real poll
pattern: 37 upstream requests with subscriptions active vs 86 polling, and 40
proxy reads cost 0 upstream calls.

The protocol has never been run against a real game - the findings doc records
it as unimplemented and the aggregate response shape is a guess. So the client
verifies that a path it subscribed to actually comes back before trusting any
value, and stands down to polling otherwise. Three outcomes, all tested:
active / unsupported (404) / unknown_shape. On unknown_shape it captures the
raw payload sample at /api/subscriptions so the real shape can be supported
rather than guessed at again.

**FIRST THING TO CHECK ON A REAL RUN:** open /api/subscriptions while driving.
`active` means it worked. `unknown_shape` - send back
`unrecognised_payload_sample`. `unsupported` - this TSW build lacks the
endpoint.

## What changed in v7.44.0 - connection stability

Train class dropping out, map stalling then jumping, weather flickering. None
of it was the game losing data.

Two of the causes were the app's own diagnostics: `log_call` rewrote a 300
line file on EVERY api call under a global lock (~10/sec), and
`read_api_key()` re-stat'd folders and re-read the key file on every call.
Both now cached/buffered.

The rest: overlapping requests to a server that cannot take them (TSW returns
502 on concurrency - documented in the timetable findings and then not acted
on), no retry, `/api/loco` fetching identity twice (6 upstream calls per poll,
now 3), and sighting rows written to SQLite every 2s.

Everything now holds last-known-good rather than blanking: identity 20s,
location 30s, and client readouts keep their value and dim after 15s.
`.stale` class added to style.css.

Measured with `tests/mock_tsw_api.py --compare`, which reproduces the game's
failure modes and runs old vs new in one process: concurrent rejects 36 -> 0,
location dropouts 1 -> 0 at a 35% drop rate.

## What changed in v7.43.0 - overlay follows infrastructure style, historic lines stencilled out

`TintedRailLayer` composites each tile on a canvas: infrastructure style
supplies the pixels (line weights, hollow tunnel casings), the GAUGE style is
used purely as a stencil via `destination-in` to drop historic alignments, and
`source-in` paints the livery colour through the surviving alpha. Alpha is
preserved at every step because thickness and tunnel translucency live there.

Measured on synthetic tiles: historic removed 100%, live lines retained 99.2%,
widths unchanged (6px/3px), tunnel alpha ratio 0.35 before and after.

The stencil is dilated (radius 2) because gauge draws thinner than
infrastructure. Dilation must be a source-over union on a scratch canvas -
repeated destination-in erodes instead. A missing stencil falls back to
unmasked rather than erasing the tile. Re-tint reuses cached tile images
rather than refetching.

New **Historic Lines: Hidden/Shown** button next to the rail toggle.

UNVERIFIED: openrailwaymap.org is unreachable from the dev sandbox, so the
`gauge` tile path and the claim that gauge omits historic lines come from
documentation, not a real tile.

## What changed in v7.42.0 - map zoom + livery-coloured rail overlay

Zoom 15 -> 17. Rail overlay and player dot now take the driven train's livery
colour via a new `livery_colour` field on `/api/loco` and a shared
`train_classes_db.resolve_livery_colour()`.

Tinting uses an SVG `feFlood` + `SourceAlpha` filter for an exact colour. CSS
`hue-rotate` was tried and rejected - it is a matrix approximation and turned
blue into cyan on test tiles. Lightness is floored at 50% (hue preserved) so
dark liveries stay visible on a dark basemap.

Caveat: Leaflet comes from unpkg, unreachable from the dev sandbox, so the map
never initialises there. Colour resolution, the tint filter and the lightness
floor were tested directly; the live map at zoom 17 was not.

## What changed in v7.41.0 - FIXED-LENGTH records, 12,207 x 707 bytes

`record_template()` on the real Leven layer: 12,207 records, min AND median
gap 707 bytes, 57 fields at 100% share, anchored on `Class`. 12,207 x 707 =
8,630,349 against a file of 8,638,791 - the whole .uexp is records plus ~8 KB.
So the records are FIXED length despite the type being called a "Stream".

But 57 fields at 100% is not proof: with fixed-length records, "same offset in
every record" is equally true of a constant byte pattern, and only 24 of
12,207 records were sampled. The old 2828-byte stride failure held alignment
for twenty records before drifting.

`decode_fixed_records()` / `/api/paks/decode_fixed` / **Decode fixed records**
is the check that does discriminate: read the type at one fixed offset in
every record and test the result against a whole-file scan that assumed no
stride at all - coverage near-total, no count exceeding the whole-file bound,
rank order agreeing. Validated on a fixed-stride fixture: recovers stride,
type offset and time offset exactly, matches ground truth to the record, and
refuses a stride one byte wrong.

**Next: Decode fixed records on the real Leven layer.** Expect stride 707 and
a type distribution at or below 5198/5198/908/36/27/4. If confirmed, the
layout is settled and the rest is reading times and writing to timetables.db.
If not, the 707 stride is coincidence - which is what this exists to find out
before anything gets built on it.

## What changed in v7.40.0 - the name table is too small to parse against

Records still do not parse at either field width, and the probe says why:
**29% of every byte offset in the 8.6 MB file passes the "valid FName" test**
(2,539,329 hits). With 88 names, any int32 in 0..87 followed by a zero looks
like a name reference. That invalidates the hex windows AND the previous
turn's "it IS tagged" conclusion - chain_break's one tag was
SignalRef/EnumProperty with size 27, and an EnumProperty value is 8 bytes, so
it was noise.

What survives is the count structure: sixteen names referenced EXACTLY 12,207
times each. That is the record count (mean 708 bytes/record). StopPoint and
TrackSectionEntry are 5,198 each - identical.

`record_template()` / `/api/paks/template` / **Recover record template** uses
that: anchor on a once-per-record name, and keep only what recurs at the same
relative offset across records. Noise does not recur; real fields do.
Validated on the fixture (recovers true field order) and refuses random bytes.

**Next: Recover record template on the real Leven layer.** Expect record_count
12,207; stable_fields is then the layout to build a parser from.

## What changed in v7.39.2 - field width made a parameter

The probe on the real Leven layer settles it: property TYPE names ARE
referenced (EnumProperty 61,036, IntProperty/NameProperty/StructProperty/
FloatProperty 12,207 each), so it is tagged serialisation and v7.39.1
over-corrected.

`longest_tag_chain: 1` was the real tell - the signature of a FIELD WIDTH
mismatch, which fails quietly: read a 64-bit FName as 32-bit and the first tag
still looks perfect (right index in the low half, zero in the high half) while
every later read lands mid-field. `_read_tag()` now takes a width and the
parser tries 4 and 8 against both guid_byte settings. Proven on a
16-byte-FName fixture (112/112 records, 31/31 stops, width discovered).
`_MAX_PROP_SIZE` raised to 64 MB - the outer ServiceDataTracks MapProperty is
megabytes and was rejected on size.

The probe now dumps annotated hex windows around real references to a field
name (default DataType) and reports where the tag chain broke. That reads the
layout off directly instead of inferring it - `+8 EnumProperty` means 8-byte
FNames, `+16` means 16-byte.

**Ground truth to check any parser against**, from the probe: 12,207 records,
5,198 StopPoint and 5,198 TrackSectionEntry (identical counts - every stop
appears paired with a section entry). If a parse does not reproduce those, it
is wrong however tidy it looks.

**Next: Probe format, then Read records, on the real Leven layer.**

## What changed in v7.39.1 - the over-correction

`parse_track_records()` on the real Leven Branch layer found zero tag chains -
not one pair of consecutive valid tags in 8.6 MB. So v7.39.0's conclusion was
wrong too, and for the same reason section 8 was: reasoning from an indirect
signal rather than measuring. A name being present in the name table does not
mean the record data references it.

Added `probe_name_references()` / `/api/paks/probe` / **Probe format**, which
counts how often each name is actually referenced as an FName in the .uexp.
That separates tagged from unversioned (UE4.25+) serialisation directly:
unversioned writes no tags, so property TYPE names are referenced ZERO times
while enum VALUE names still appear. Validated against fixtures built both
ways. A failed parse now attaches its own probe.

**Next: Probe format on the real Leven Branch layer.** If property type names
come back with counts, the tag layout just differs from the UE4 one assumed
here. If they come back at zero with enum values present, it is unversioned
and tags are never coming - at which point the statistical path
(/api/paks/stops) is the fallback, and it already segments services usefully.

## What changed in v7.39.0 - what the name table suggested

The name table from the second real run settles the format question, and
section 8 of the findings doc was wrong. The Leven layer's 88 names include
ArrayProperty / EnumProperty / FloatProperty / IntProperty / MapProperty /
NameProperty and field names DataType, Distance, DirectionOfTravel,
InstructionIndex, GoViaIndex, ActionIndices, NetworkRibbonLocation. That is
Unreal's tagged-property serialisation - the asset is self-describing, so
nothing needs to be inferred.

`parse_track_records()` / `/api/paks/records` / **Read records (tagged
properties)** walks FPropertyTag chains and reads each record field by field
by name. Validated against a fixture written from the format spec: 220/220
records, 48/48 StopPoints, with and without the HasPropertyGuid byte, first
stop correctly departure-only. Random bytes are refused.

This explains the earlier statistical result rather than contradicting it:
shift -6 maps StopPoint onto "EnumProperty" and ActionPoint onto "Distance",
i.e. the property machinery names, which really do appear once per record with
a consistent delta. The scoring found a real field, just the wrong one. No
further statistics would have helped. `find_stop_points()` is kept for assets
that genuinely are opaque.

**Still unsolved: station names.** NetworkRibbonLocation holds P2K51-style
track ribbon IDs, not station names. Stops are precisely located but unlabelled.

**Next: run Read records on the real Leven Branch layer.**

## What changed in v7.38.1 - first real run, and a false confirmation

Ran against the real Leven Branch layer. The times and segmentation look
genuinely right (median 8 stops per service, a 15-stop 74.8-minute
Leven -> Edinburgh run, 137 of 297 intervals under 90 seconds = arrival/
departure pairs). But it reported `confirmed: true` on an IMPOSSIBLE answer:
the winning shift put `StopPoint` at FName index 8765 in an 88-entry name
table, and three other impossible shifts tied with it, with the only plausible
candidate coming fifth by 0.0002.

Fixed: shifts must now resolve every anchor to an index that exists in the
name table, ties are reported and suppress `confirmed`, and `names_sample` is
returned. Full write-up in the findings doc.

**The real blocker is now clear: this layer's name table has no station names
at all** (88 names, 0 station-shaped). So there is nothing to corroborate the
enum against - which is why the tie was unbreakable - and the stops have times
but no labels. Station identity has to come from somewhere else; the index
asset is the place to look next.

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
