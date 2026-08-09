"""
Builds a synthetic .uasset/.uexp pair shaped like a TSW timetable DataTrack,
with a layout we KNOW, so find_stop_points() can be checked against ground
truth. No real paks are needed to validate the logic.

Deliberately reproduces the awkward parts of the real thing:
  - variable-length records (it is a "Stream"), so no fixed stride
  - the name table is preceded by header strings, so table POSITION and the
    FName INDEX written into the stream differ by a constant shift
  - only a minority of records carry a station (stops), the rest are
    simulated running times
  - first stop has departure only, last stop arrival only, one service is
    freight with no arrivals at all
  - a sea of zero int32s and plenty of unrelated small ints as padding
  - realistic record spacing (thousands of bytes), because window size vs
    record spacing is itself one of the things being tested
"""
import os
import random
import struct
import sys

TICKS = 10_000_000

STATIONS = [
    "Leven 1", "Cameron Bridge 1", "Thornton 2", "Glenrothes with Thornton 1",
    "Kirkcaldy 2", "Inverkeithing 1", "Haymarket 3", "Edinburgh Waverley 1a",
]
ENUMS = ["ETimetableTrackDataType::StopPoint",
         "ETimetableTrackDataType::ActionPoint",
         "ETimetableTrackDataType::GoVia",
         "ETimetableTrackDataType::ReversePoint"]
JUNK_NAMES = ["Texture2D 2", "Material 3", "SkeletalMesh 1", "None",
              "RouteTimetableDataTrackStream", "ServiceDataTracks",
              "/Game/Timetable/FCE", "Default__RouteTimetableDefinition",
              "1A10", "2K05", "ArrivalTime", "CompletionTime"]
HEADER_STRINGS = ["/Script/CoreUObject", "PackageFileSummary", "EditorOnly",
                  "BulkDataStart", "ThumbnailTable", "AssetRegistryData"]


def _pstr(s):
    b = s.encode("ascii") + b"\x00"
    return struct.pack("<i", len(b)) + b


def build_uasset(path):
    """Header strings first, then the real name table. find_stop_points has
    to recover the constant shift between the two."""
    buf = b"\x00" * 16
    for s in HEADER_STRINGS:
        buf += _pstr(s)
    shift = -len(HEADER_STRINGS)  # index_in_table = position + shift
    # A REAL name table has thousands of entries, so the enum and station
    # indices are large numbers. An earlier version of this fixture had 30
    # names, which made the problem unsolvable in principle rather than
    # merely hard: every small padding int was a valid index, so padding and
    # a genuine station reference were literally the same bytes. Size is
    # part of the fixture being faithful.
    names = list(JUNK_NAMES[:6])
    for i in range(900):
        names.append(f"SM_Prop_{i:03d}")
    names.extend(ENUMS)
    for i in range(600):
        names.append(f"MI_Livery_{i:03d}")
    names.extend(STATIONS)
    for i in range(900):
        names.append(f"Texture2D_{i:03d}")
    names.extend(JUNK_NAMES[6:])
    for s in names:
        buf += _pstr(s)
    with open(path, "wb") as f:
        f.write(buf)
    index_of = {s: i for i, s in enumerate(names)}
    return index_of, shift


def build_uexp(path, index_of, rng):
    """Variable-length records. Ground truth returned alongside."""
    buf = bytearray()
    truth = []          # (offset_of_time, station_or_None)

    def pad(lo, hi):
        """Padding WITHIN a record - small, because a record's own fields
        sit close together."""
        n = rng.randint(lo, hi)
        for _ in range(n):
            # mostly zeros and small ints, like real Unreal padding
            buf.extend(struct.pack("<i", 0) if rng.random() < 0.6
                       else struct.pack("<i", rng.randint(1, 40)))

    def fname(s):
        buf.extend(struct.pack("<ii", index_of[s], 0))   # index + Number

    def write_time(secs):
        off = len(buf)
        buf.extend(struct.pack("<q", int(secs * TICKS)))
        return off

    services = [
        # (start_secs, stations_used, freight?)
        (6 * 3600 + 4 * 60, STATIONS, False),
        (8 * 3600 + 8 * 60, STATIONS, False),
        (9 * 3600 + 7 * 60, STATIONS[:5], False),
        (11 * 3600 + 3 * 60, STATIONS, True),      # freight: no arrivals
    ]

    def filler():
        """The gap BETWEEN records. Recovered times in the real DataTrack
        average ~65,000 bytes apart, so records are far apart even though
        each record's own fields are not. Getting this wrong in either
        direction breaks the fixture: pack records ~50 bytes apart and a
        192-byte window spans six of them, so every record looks like it
        carries four different types; scale a record's INTERNAL padding up
        to match instead and the type field ends up thousands of bytes from
        its own time, outside any sane window."""
        for _ in range(rng.randint(300, 900)):
            buf.extend(struct.pack("<i", 0) if rng.random() < 0.6
                       else struct.pack("<i", rng.randint(1, 4000)))

    for start, used, freight in services:
        t = start
        for si, st in enumerate(used):
            # a handful of simulated running-time track points before each stop
            for _ in range(rng.randint(3, 6)):
                t += rng.randint(20, 90)
                pad(1, 4)
                fname(ENUMS[rng.choice([1, 2, 3])])
                pad(1, 6)
                off = write_time(t)
                truth.append((off, None))
                pad(0, 5)
                filler()

            first, last = si == 0, si == len(used) - 1
            pad(1, 3)
            fname(ENUMS[0])                 # StopPoint
            pad(0, 4)
            fname(st)                       # the station
            pad(1, 5)
            if not first and not freight:   # arrival
                t += rng.randint(60, 200)
                off = write_time(t)
                truth.append((off, st))
                pad(0, 4)
            if not last:                    # departure
                t += rng.randint(30, 90)
                off = write_time(t)
                truth.append((off, st))
                pad(1, 6)
            filler()

    with open(path, "wb") as f:
        f.write(bytes(buf))
    return truth


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/synth"
    os.makedirs(out, exist_ok=True)
    rng = random.Random(20260808)
    base = os.path.join(out, "FCE_Timetable_TT_Leven_Layer_DataTrack")
    index_of, shift = build_uasset(base + ".uasset")
    truth = build_uexp(base + ".uexp", index_of, rng)
    print(f"built {base}.uexp  ({os.path.getsize(base + '.uexp')} bytes)")
    print(f"ground truth: {len(truth)} times, "
          f"{sum(1 for _o, s in truth if s)} of them at stations")
    print(f"true shift (index = position + shift): {shift}")
    return base, truth, shift


if __name__ == "__main__":
    main()
