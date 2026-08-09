"""
Builds a .uasset/.uexp pair using Unreal's REAL tagged-property layout, with
the exact name table observed in the real Leven Branch layer - None at index
0, the package path at 1, the ETimetableTrackDataType members, and the field
names DataType / Distance / Direction / InstructionIndex / GoViaIndex /
ActionIndices / NetworkRibbonLocation.

This is the fixture for parse_track_records(). It is written from the format
spec, not from the parser, so agreement between the two is evidence rather
than circular.
"""
import os
import random
import struct
import sys

TICKS = 10_000_000

NAMES = [
    "None",
    "/FifeCircle_Route_Gameplay/Timetable/DataTracks/FCE_Test_Layer_DataTrack",
    "/Script/CoreUObject",
    "/Script/TS2Prototype",
    "5B42",
    "ActionIndices",
    "ArrayProperty",
    "ArrivalTime",
    "Class",
    "CompletionTime",
    "DataType",
    "Default__RouteTimetableDataTrackStream",
    "Direction",
    "DirectionOfTravel",
    "Distance",
    "EDirectionOfTravel",
    "EDirectionOfTravel::Backwards",
    "EDirectionOfTravel::Forwards",
    "EnumProperty",
    "ETimetableTrackDataType",
    "ETimetableTrackDataType::ActionPoint",
    "ETimetableTrackDataType::GoVia",
    "ETimetableTrackDataType::MultiOccupancy",
    "ETimetableTrackDataType::ReversePoint",
    "ETimetableTrackDataType::StopPoint",
    "ETimetableTrackDataType::TrackSectionEntry",
    "ETimetableTrackDataType::TrackSectionExit",
    "FloatProperty",
    "GoViaIndex",
    "Guid",
    "InstructionIndex",
    "IntProperty",
    "Location",
    "NameProperty",
    "NetworkRibbonLocation",
    "StructProperty",
    "Time",
    "Timespan",
    "Package",
    "PropertyReference",
    "ServiceDataTracks",
    "SignalRef",
] + [f"P2K{n}" for n in range(50, 60)]   # NetworkRibbonLocation values, as in the real table
IDX = {n: i for i, n in enumerate(NAMES)}


def _pstr(s):
    b = s.encode("ascii") + b"\x00"
    return struct.pack("<i", len(b)) + b


def build_uasset(path):
    buf = bytearray()
    for n in NAMES:
        buf += _pstr(n)
    with open(path, "wb") as f:
        f.write(bytes(buf))


def _fname(n):
    return struct.pack("<ii", IDX[n], 0)


def _tag(name, type_name, value, extra=b"", guid_byte=True):
    """One FPropertyTag plus its value bytes, per the UE4 layout."""
    out = bytearray()
    out += _fname(name)
    out += _fname(type_name)
    out += struct.pack("<ii", len(value), 0)     # Size, ArrayIndex
    out += extra                                  # type-specific header
    if guid_byte:
        out += b"\x00"                            # HasPropertyGuid
    out += value
    return bytes(out)


def enum_prop(name, enum_type, member, guid_byte=True):
    return _tag(name, "EnumProperty", _fname(member), _fname(enum_type), guid_byte)


def int_prop(name, v, guid_byte=True):
    return _tag(name, "IntProperty", struct.pack("<i", v), b"", guid_byte)


def float_prop(name, v, guid_byte=True):
    return _tag(name, "FloatProperty", struct.pack("<f", v), b"", guid_byte)


def timespan_prop(name, secs, guid_byte=True):
    return _tag(name, "StructProperty", struct.pack("<q", int(secs * TICKS)),
                _fname("Timespan") + b"\x00" * 16, guid_byte)


def name_prop(name, value_name, guid_byte=True):
    return _tag(name, "NameProperty", _fname(value_name), b"", guid_byte)


def build_uexp(path, rng, guid_byte=True):
    """Ground truth returned alongside: one entry per record."""
    buf = bytearray()
    truth = []
    t = 6 * 3600

    for service in range(8):
        t = (6 + service) * 3600
        n_stops = rng.randint(3, 9)
        for i in range(n_stops):
            # a few non-stop track points between calls
            for _ in range(rng.randint(2, 5)):
                t += rng.randint(20, 90)
                kind = rng.choice(["GoVia", "ActionPoint", "TrackSectionEntry"])
                buf += enum_prop("DataType", "ETimetableTrackDataType",
                                 f"ETimetableTrackDataType::{kind}", guid_byte)
                buf += float_prop("Distance", rng.uniform(0, 40000), guid_byte)
                buf += enum_prop("DirectionOfTravel", "EDirectionOfTravel",
                                 "EDirectionOfTravel::Forwards", guid_byte)
                buf += timespan_prop("Time", t, guid_byte)
                buf += _fname("None")
                truth.append({"type": kind, "arrival": None})

            first, last = i == 0, i == n_stops - 1
            buf += enum_prop("DataType", "ETimetableTrackDataType",
                             "ETimetableTrackDataType::StopPoint", guid_byte)
            buf += int_prop("InstructionIndex", rng.randint(0, 200), guid_byte)
            buf += float_prop("Distance", rng.uniform(0, 40000), guid_byte)
            buf += name_prop("NetworkRibbonLocation", f"P2K{50 + i}", guid_byte)
            arrival = departure = None
            # Domain rules: first stop has no arrival, last has no departure.
            if not first:
                t += rng.randint(60, 200)
                arrival = t
                buf += timespan_prop("ArrivalTime", t, guid_byte)
            if not last:
                t += rng.randint(30, 90)
                departure = t
                buf += timespan_prop("CompletionTime", t, guid_byte)
            buf += _fname("None")
            truth.append({"type": "StopPoint", "arrival": arrival,
                          "departure": departure, "ribbon": f"P2K{50 + i}"})

    with open(path, "wb") as f:
        f.write(bytes(buf))
    return truth


def main(out="/tmp/synth_tagged", guid_byte=True):
    os.makedirs(out, exist_ok=True)
    base = os.path.join(out, "FCE_Test_Layer_DataTrack")
    rng = random.Random(4242)
    build_uasset(base + ".uasset")
    truth = build_uexp(base + ".uexp", rng, guid_byte=guid_byte)
    print(f"built {base}.uexp ({os.path.getsize(base + '.uexp')} bytes)")
    print(f"ground truth: {len(truth)} records, "
          f"{sum(1 for r in truth if r['type'] == 'StopPoint')} StopPoints")
    return base, truth


if __name__ == "__main__":
    main()


def build_uexp_unversioned(path, rng):
    """UE4.25+ UNVERSIONED property serialisation: no property tags at all.

    Fields are identified positionally against the class schema, so the
    property NAMES and TYPE names never appear in the stream. Enum values
    still do, because an EnumProperty value is an FName either way. That
    asymmetry is exactly what probe_name_references() keys off, so this
    fixture exists to prove the probe can tell the two modes apart rather
    than only recognising the one it was written for.
    """
    buf = bytearray()
    truth = []
    for service in range(8):
        t = (6 + service) * 3600
        n_stops = rng.randint(3, 9)
        for i in range(n_stops):
            for _ in range(rng.randint(2, 5)):
                t += rng.randint(20, 90)
                kind = rng.choice(["GoVia", "ActionPoint", "TrackSectionEntry"])
                buf += struct.pack("<H", 0x0007)          # unversioned header
                buf += _fname(f"ETimetableTrackDataType::{kind}")
                buf += struct.pack("<f", rng.uniform(0, 40000))
                buf += _fname("EDirectionOfTravel::Forwards")
                buf += struct.pack("<q", int(t * TICKS))
                truth.append({"type": kind})
            buf += struct.pack("<H", 0x001F)
            buf += _fname("ETimetableTrackDataType::StopPoint")
            buf += struct.pack("<i", rng.randint(0, 200))
            buf += struct.pack("<f", rng.uniform(0, 40000))
            buf += _fname(f"P2K{50 + i}")
            t += rng.randint(60, 200)
            buf += struct.pack("<q", int(t * TICKS))
            truth.append({"type": "StopPoint"})
    with open(path, "wb") as f:
        f.write(bytes(buf))
    return truth


def main_unversioned(out="/tmp/synth_unversioned"):
    os.makedirs(out, exist_ok=True)
    base = os.path.join(out, "FCE_Test_Layer_DataTrack")
    rng = random.Random(4242)
    build_uasset(base + ".uasset")
    truth = build_uexp_unversioned(base + ".uexp", rng)
    print(f"built {base}.uexp ({os.path.getsize(base + '.uexp')} bytes) - UNVERSIONED")
    print(f"ground truth: {len(truth)} records, "
          f"{sum(1 for r in truth if r['type'] == 'StopPoint')} StopPoints")
    return base, truth


def build_uexp_wide(path, rng):
    """Tagged properties with 16-byte FNames (int64 index + int64 Number).

    Exists because the real Leven layer reports longest_tag_chain = 1, which
    is the exact signature of a width mismatch: the first tag reads fine
    (a 64-bit index's low half is the right value and its high half is
    zeros), then the second lands mid-field and fails. Whether that is what
    the real file does is not yet known - this fixture proves the parser can
    HANDLE it, which is a different claim and the only one being made here.
    """
    def fname(n):
        return struct.pack("<qq", IDX[n], 0)

    def tag(name, type_name, value, extra=b""):
        out = bytearray()
        out += fname(name)
        out += fname(type_name)
        out += struct.pack("<ii", len(value), 0)
        out += extra
        out += b"\x00"
        out += value
        return bytes(out)

    buf = bytearray()
    truth = []
    for service in range(6):
        t = (6 + service) * 3600
        for i in range(rng.randint(3, 7)):
            for _ in range(rng.randint(2, 4)):
                t += rng.randint(20, 90)
                kind = rng.choice(["GoVia", "ActionPoint"])
                buf += tag("DataType", "EnumProperty",
                           fname(f"ETimetableTrackDataType::{kind}"),
                           fname("ETimetableTrackDataType"))
                buf += tag("Distance", "FloatProperty", struct.pack("<f", rng.uniform(0, 4e4)))
                buf += fname("None")
                truth.append({"type": kind})
            buf += tag("DataType", "EnumProperty",
                       fname("ETimetableTrackDataType::StopPoint"),
                       fname("ETimetableTrackDataType"))
            buf += tag("InstructionIndex", "IntProperty", struct.pack("<i", rng.randint(0, 200)))
            t += rng.randint(60, 200)
            buf += tag("ArrivalTime", "StructProperty", struct.pack("<q", int(t * TICKS)),
                       fname("Timespan") + b"\x00" * 16)
            buf += fname("None")
            truth.append({"type": "StopPoint"})
    with open(path, "wb") as f:
        f.write(bytes(buf))
    return truth


def main_wide(out="/tmp/synth_wide"):
    os.makedirs(out, exist_ok=True)
    base = os.path.join(out, "FCE_Test_Layer_DataTrack")
    rng = random.Random(4242)
    build_uasset(base + ".uasset")
    truth = build_uexp_wide(base + ".uexp", rng)
    print(f"built {base}.uexp ({os.path.getsize(base + '.uexp')} bytes) - 16-byte FNames")
    print(f"ground truth: {len(truth)} records, "
          f"{sum(1 for r in truth if r['type'] == 'StopPoint')} StopPoints")
    return base, truth


def build_uexp_fixed(path, rng, stride=707, n_records=600):
    """FIXED-stride records, like the real Leven layer (707 bytes, 12,207 of
    them). Padding is random rather than zeros, so the file is full of values
    that read as valid FName references - which is the actual difficulty:
    with an 88-name table, 29% of all offsets in the real file pass that test.

    The type distribution is deliberately lopsided (many StopPoint and
    TrackSectionEntry, a handful of ActionPoint) to match the real counts, so
    that reproducing it from a fixed offset is a real test and not a
    coin-flip.
    """
    TYPE_AT, TIME_AT, ANCHOR_AT = 120, 300, 0   # five anchors at 0,8,16,24,32
    weights = [("StopPoint", 42), ("TrackSectionEntry", 42), ("ReversePoint", 8),
               ("MultiOccupancy", 4), ("GoVia", 3), ("ActionPoint", 1)]
    pool = [t for t, w in weights for _ in range(w)]
    buf = bytearray()
    truth = []
    t = 5 * 3600
    for i in range(n_records):
        # Padding must look like real record data - floats, tick counts,
        # GUIDs - NOT small ints. An earlier version filled records with
        # ints in 1..90, which on an 88-name table meant every offset read
        # as a valid FName reference and the whole-file reference counts
        # came out at double the truth. Real payload bytes rarely produce a
        # small-int-followed-by-zero pair, which is why on the actual Leven
        # layer sixteen names land on exactly 12,207.
        rec = bytearray()
        while len(rec) < stride:
            r = rng.random()
            if r < 0.35:
                rec += struct.pack("<i", 0)
            elif r < 0.75:
                rec += struct.pack("<f", rng.uniform(-4e4, 4e4))
            else:
                rec += struct.pack("<i", rng.randint(1 << 20, (1 << 31) - 1))
        rec = bytearray(rec[:stride])
        kind = rng.choice(pool)
        # The real layer has SIXTEEN names referenced exactly once per
        # record, which is how the record count was found at all. One
        # anchor is not a faithful fixture.
        for k, nm in enumerate(("Class", "Package", "Guid", "ServiceDataTracks",
                                "PropertyReference")):
            rec[ANCHOR_AT + k * 8:ANCHOR_AT + k * 8 + 8] = _fname(nm)
        rec[TYPE_AT:TYPE_AT + 8] = _fname(f"ETimetableTrackDataType::{kind}")
        t += rng.randint(20, 200)
        rec[TIME_AT:TIME_AT + 8] = struct.pack("<q", int(t * TICKS))
        buf += rec
        truth.append(kind)
    with open(path, "wb") as f:
        f.write(bytes(buf))
    return truth, stride, TYPE_AT, TIME_AT


def main_fixed(out="/tmp/synth_fixed"):
    os.makedirs(out, exist_ok=True)
    base = os.path.join(out, "FCE_Test_Layer_DataTrack")
    rng = random.Random(1234)
    build_uasset(base + ".uasset")
    truth, stride, type_at, time_at = build_uexp_fixed(base + ".uexp", rng)
    from collections import Counter
    print(f"built {base}.uexp - {len(truth)} fixed {stride}-byte records")
    print(f"  type at +{type_at}, time at +{time_at}")
    print(f"  true distribution: {dict(Counter(truth).most_common())}")
    return base, truth, stride, type_at, time_at
