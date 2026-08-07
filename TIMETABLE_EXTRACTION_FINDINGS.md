# Offline timetable extraction — findings

Everything below is **confirmed against a real TSW6 install**, not inferred.
Written up so this doesn't have to be rediscovered.

## Verdict

The full offline timetable data **is present and readable**. Every layer of
the problem is solved except the final one: associating times with services
and stops.

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

## 7. What remains

Associating times with services and stops: walking the `ServiceDataTracks`
records rather than scanning values in isolation. This means decoding
Unreal's property serialisation for a **custom DTG type**
(`RouteTimetableDataTrackStream`). "Stream" in the name suggests a custom
`Serialize()` writing raw binary rather than self-describing tagged
properties — so expect to reverse-engineer the record layout from the byte
patterns around known values, not to walk a standard property tree.

This is real work and should not be under-estimated. But it is now
*bounded* work: the data is confirmed present, unencrypted and decodable.

## Tooling in the app (Discovery page)

- `game_files.py` — install/pak discovery, repak detection
- `pak_tools.py` — repak wrapper, listing, asset inspection, timespan scan
- Endpoints: `/api/paks/{repak,list,timetables,scan_all,inspect,timespans,clear_extracted}`
- Extraction output goes to `<app>/extracted/` — **clear it between runs**,
  stale files caused one misdiagnosis already.
