"""
Writes a genuine UE4 `.uasset` + `.uexp` pair containing a
RouteTimetableDefinition, so the parser can be checked against known values.

Written from the FORMAT - package header, name map, export table, FPropertyTag
streams - not from the parser, so agreement between the two is evidence
rather than circularity. Everything the parser has to cope with on the real
file is reproduced: services with instructions, stations carrying platform
designators, first stop with no arrival, last with no departure, a freight
working with no arrivals at all, non-stopping passes, and AI services that
carry only simulated times.
"""

import os
import struct

UASSET_MAGIC = 0x9E2A83C1
TICKS = 10_000_000


class NameMap:
    def __init__(self):
        self.names = []
        self.index = {}

    def add(self, s):
        if s not in self.index:
            self.index[s] = len(self.names)
            self.names.append(s)
        return self.index[s]

    def fname(self, s):
        return struct.pack("<ii", self.add(s), 0)


def fstr(s):
    b = s.encode("ascii") + b"\x00"
    return struct.pack("<i", len(b)) + b


def tag(nm, name, ptype, payload, struct_type=None, inner_type=None,
        bool_val=None):
    """One FPropertyTag plus its value bytes."""
    out = bytearray()
    out += nm.fname(name)
    out += nm.fname(ptype)
    out += struct.pack("<ii", len(payload), 0)      # Size, ArrayIndex
    if ptype == "StructProperty":
        out += nm.fname(struct_type or "None")
        out += b"\x00" * 16                          # struct guid
    elif ptype == "BoolProperty":
        out += bytes([1 if bool_val else 0])
    elif ptype in ("ByteProperty", "EnumProperty", "ArrayProperty", "SetProperty"):
        out += nm.fname(inner_type or "None")
    out += b"\x00"                                   # HasPropertyGuid
    out += payload
    return bytes(out)


def timespan(nm, name, seconds):
    return tag(nm, name, "StructProperty", struct.pack("<q", int(seconds * TICKS)),
               struct_type="Timespan")


def route_location(nm, station, guid_byte=7, offset=1234.5):
    """A Destination: RouteLocationName carrying Name + NetworkRibbonLocation."""
    inner = bytearray()
    inner += tag(nm, "Name", "NameProperty", nm.fname(station))
    loc = bytearray()
    loc += tag(nm, "RibbonGuid", "StructProperty", bytes([guid_byte]) * 16,
               struct_type="Guid")
    loc += tag(nm, "Offset", "FloatProperty", struct.pack("<f", offset))
    loc += nm.fname("None")
    inner += tag(nm, "Location", "StructProperty", bytes(loc),
                 struct_type="NetworkRibbonLocation")
    inner += nm.fname("None")
    return tag(nm, "Destination", "StructProperty", bytes(inner),
               struct_type="RouteLocationName")


def starts_here(nm, station, departure, guid_byte=7, waiting=120):
    """A service beginning at a platform: a LoadUnload with NO destination,
    followed by the GoTo that names where it is.

    The real file stores it this way, and reading the location from the
    LoadUnload gives a nameless stop at the head of most services - 382 of
    them on the Fife Circle file.
    """
    out = bytearray()
    out += instruction(nm, "", departure=departure, itype="LoadUnload",
                       guid_byte=0, waiting=waiting, no_destination=True)
    out += instruction(nm, station, itype="GoTo", guid_byte=guid_byte)
    return bytes(out)


def call_pair(nm, station, arrival=None, departure=None, simulated=False,
              guid_byte=7, waiting=60):
    """A station CALL as the real file stores it: a GoTo naming the
    destination, followed by a LoadUnload carrying the times.

    The first version of the fixture put times on the GoTo, which let a
    parser that read every GoTo as a stop pass the test - and that parser
    then produced 3,263 stops on the real file with 80% having arrival equal
    to departure. The fixture has to store times where the game does.
    """
    out = bytearray()
    out += instruction(nm, station, itype="GoTo", guid_byte=guid_byte)
    out += instruction(nm, station, arrival=arrival, departure=departure,
                       itype="LoadUnload", simulated=simulated,
                       guid_byte=guid_byte, waiting=waiting)
    return bytes(out)


def pass_through(nm, station, guid_byte=7):
    """A GoTo with NO LoadUnload after it - a routing waypoint the train
    passes without calling. Must not appear in the stop list."""
    return instruction(nm, station, itype="GoTo", guid_byte=guid_byte)


def instruction(nm, station, arrival=None, departure=None, stopping=True,
                itype="LoadUnload", simulated=False, guid_byte=7, waiting=None,
                no_destination=False):
    body = bytearray()
    body += tag(nm, "InstructionType", "EnumProperty",
                nm.fname(f"ERouteTimetableServiceInstructionType::{itype}"),
                inner_type="ERouteTimetableServiceInstructionType")
    if not no_destination:
        body += route_location(nm, station, guid_byte=guid_byte)
    if arrival is not None:
        body += timespan(nm, "SimulatedArrivalTime" if simulated else "ArrivalTime",
                         arrival)
    if departure is not None:
        body += timespan(nm, "SimulatedCompletionTime" if simulated else "CompletionTime",
                         departure)
    if waiting is not None:
        body += timespan(nm, "WaitingTime", waiting)
    body += tag(nm, "bIsStopping", "BoolProperty", b"", bool_val=stopping)
    body += nm.fname("None")                        # ends this struct
    return bytes(body)


def service(nm, name, headcode, stops, simulated=False):
    """stops: list of (station, arrival|None, departure|None, stopping)"""
    body = bytearray()
    body += tag(nm, "ServiceName", "NameProperty", nm.fname(name))
    body += tag(nm, "HeadCode", "NameProperty", nm.fname(headcode))
    body += tag(nm, "bIsPlayerDrivable", "BoolProperty", b"", bool_val=True)

    instrs = bytearray()
    n_instr = sum(2 if st[3] else 1 for st in stops)   # start pair is also 2
    instrs += struct.pack("<i", n_instr)             # element count
    instrs += tag(nm, "Instructions", "StructProperty", b"",
                  struct_type="RouteTimetableServiceInstruction")
    for i, (st, arr, dep, stopping) in enumerate(stops):
        if i == 0 and arr is None and dep is not None and stopping:
            instrs += starts_here(nm, st, dep, guid_byte=(i % 200) + 1)
            continue
        if stopping:
            instrs += call_pair(nm, st, arr, dep, simulated=simulated,
                                guid_byte=(i % 200) + 1)
        else:
            instrs += pass_through(nm, st, guid_byte=(i % 200) + 1)
    body += tag(nm, "Instructions", "ArrayProperty", bytes(instrs),
                inner_type="StructProperty")
    body += nm.fname("None")
    return bytes(body)


def write_package(base, nm, export_body):
    """Writes a .uasset header + .uexp payload around an export body.

    Split out of build() so a test can construct an asset with a DIFFERENT
    internal layout - an unrecognised service array name, say - without
    duplicating the header maths.
    """
    name_blob = bytearray()
    for s in nm.names:
        name_blob += fstr(s)
        name_blob += b"\x00" * 4
    head = bytearray()
    head += struct.pack("<I", UASSET_MAGIC)
    head += struct.pack("<i", -4)
    head += struct.pack("<iii", 0, 0, 0)
    head_size_pos = len(head); head += struct.pack("<i", 0)
    head += fstr("/Game/Timetable"); head += struct.pack("<i", 0)
    name_off_pos = len(head); head += struct.pack("<ii", len(nm.names), 0)
    head += struct.pack("<ii", 0, 0)
    exp_off_pos = len(head); head += struct.pack("<ii", 1, 0)
    head += struct.pack("<ii", 0, 0); head += b"\x00" * 32
    name_offset = len(head); head += name_blob
    export_offset = len(head); total_header = export_offset + 100
    exp = bytearray()
    exp += struct.pack("<i", 0); exp += struct.pack("<ii", 0, 0)
    exp += struct.pack("<i", 0); exp += nm.fname(os.path.basename(base))
    exp += struct.pack("<i", 0); exp += struct.pack("<q", len(export_body))
    exp += struct.pack("<q", total_header); exp += b"\x00" * 60
    head += exp
    struct.pack_into("<i", head, head_size_pos, total_header)
    struct.pack_into("<ii", head, name_off_pos, len(nm.names), name_offset)
    struct.pack_into("<ii", head, exp_off_pos, 1, export_offset)
    with open(base + ".uasset", "wb") as f:
        f.write(bytes(head))
    with open(base + ".uexp", "wb") as f:
        f.write(bytes(export_body))
    return base


def build(out="/tmp/synth_ttdef"):
    os.makedirs(out, exist_ok=True)
    base = os.path.join(out, "FCE_Timetable_TT")
    nm = NameMap()
    nm.add("None")

    route = ["Leven Platform 1", "Cameron Bridge Platform 1",
             "Glenrothes with Thornton Platform 2", "Markinch Platform 1",
             "Kirkcaldy Platform 2", "Inverkeithing Platform 1",
             "Haymarket Platform 3", "Edinburgh Waverley Platform 12"]

    truth = []
    services_bin = bytearray()
    specs = []

    # A normal passenger working: first stop departure-only, last arrival-only.
    t = 5 * 3600 + 20 * 60
    stops = []
    for i, st in enumerate(route):
        arr = None if i == 0 else t
        t += 90
        dep = None if i == len(route) - 1 else t
        t += 480
        stops.append((st, arr, dep, True))
    specs.append(("1E01", stops, False))

    # A working that PASSES two stations without calling.
    t = 7 * 3600
    stops = []
    for i, st in enumerate(route):
        stopping = i not in (2, 5)
        arr = None if i == 0 else t
        t += 60
        dep = None if i == len(route) - 1 else t
        t += 400
        stops.append((st, arr, dep, stopping))
    specs.append(("1R03", stops, False))

    # Freight: no arrivals at all, which is CORRECT data.
    t = 9 * 3600
    stops = []
    for st in route[:4]:
        t += 600
        stops.append((st, None, t, True))
    specs.append(("6S42", stops, False))

    # A service whose final platform carries a SECOND LoadUnload - extra
    # station work at the same stop, not another call. On the real file this
    # produced "Edinburgh Waverly 18:04, Edinburgh Waverly 18:09".
    t = 14 * 3600
    stops = []
    for i, st in enumerate(route[:3]):
        arr = None if i == 0 else t
        t += 60
        dep = t
        t += 480
        stops.append((st, arr, dep, True))
    specs.append(("1D55", stops, False))
    extra_tail = ("1D55", route[2], t + 300, t + 360)

    # A service crossing MIDNIGHT. Timespans keep counting past 24h, so a
    # call at 00:10 is stored as 24:10 - and rejecting anything past a day
    # silently dropped the last stops of the real late-night Leven services.
    t = 23 * 3600 + 40 * 60
    stops = []
    for i, st in enumerate(route[:4]):
        arr = None if i == 0 else t
        t += 60
        dep = None if i == 3 else t
        t += 900                       # pushes the last calls past 24:00
        stops.append((st, arr, dep, True))
    specs.append(("2N99", stops, False))

    # An AI service carrying only simulated times.
    t = 11 * 3600
    stops = []
    for i, st in enumerate(route[:5]):
        arr = None if i == 0 else t
        t += 70
        dep = t
        t += 300
        stops.append((st, arr, dep, True))
    specs.append(("2K05", stops, True))

    # TSW splits one working across a player leg (P<code>) and an AI
    # continuation (<code>_B). Both are real records in the file - this is
    # in the findings doc - so the fixture carries the pattern and the
    # parser must LABEL them rather than merge or discard either.
    specs.append(("1L86", [(route[0], None, 6*3600, True),
                           (route[1], 6*3600+300, 6*3600+400, True)], False))
    names = {"1L86": "P1L86"}

    for headcode, stops, sim in specs:
        svc_name = names.get(headcode, f"SVC_{headcode}")
        services_bin += service(nm, svc_name, headcode, stops, simulated=sim)
        truth.append({
            "service_name": svc_name,
            # A first stop with a departure and no arrival is the ORIGIN,
            # written as a LoadUnload with no destination - the file does not
            # name where a service begins.
            "has_origin": bool(stops and stops[0][1] is None and stops[0][2]),
            "headcode": headcode,
            "stops": [s[0] for s in stops],
            "calls": [s[0] for s in stops if s[3]],
            "simulated": sim,
        })

    # the AI continuation of 1L86
    services_bin += service(nm, "1L86_B", "1L86",
                            [(route[2], 6*3600+900, 6*3600+1000, True)], simulated=False)
    truth.append({"service_name": "1L86_B", "headcode": "1L86", "has_origin": False,
                  "stops": [route[2]], "calls": [route[2]], "simulated": False})
    specs.append(("1L86", [(route[2], None, None, True)], False))

    export_body = bytearray()
    arr = bytearray()
    arr += struct.pack("<i", len(truth))
    arr += tag(nm, "Services", "StructProperty", b"",
               struct_type="RouteTimetableService")
    arr += services_bin
    export_body += tag(nm, "Services", "ArrayProperty", bytes(arr),
                       inner_type="StructProperty")
    export_body += nm.fname("None")

    nm.add("RouteTimetableDefinition")
    nm.add("FCE_Timetable_TT")

    # --- header ---
    name_blob = bytearray()
    for s in nm.names:
        name_blob += fstr(s)
        name_blob += b"\x00" * 4                     # two uint16 hashes

    head = bytearray()
    head += struct.pack("<I", UASSET_MAGIC)
    head += struct.pack("<i", -4)                    # LegacyFileVersion
    head += struct.pack("<i", 0)                     # FileVersionUE4
    head += struct.pack("<i", 0)                     # FileVersionLicenseeUE4
    head += struct.pack("<i", 0)                     # CustomVersion count
    head_size_pos = len(head)
    head += struct.pack("<i", 0)                     # TotalHeaderSize (patched)
    head += fstr("/Game/Timetable")                  # FolderName
    head += struct.pack("<i", 0)                     # PackageFlags
    name_off_pos = len(head)
    head += struct.pack("<ii", len(nm.names), 0)     # NameCount, NameOffset
    head += struct.pack("<ii", 0, 0)                 # GatherableText
    exp_off_pos = len(head)
    head += struct.pack("<ii", 1, 0)                 # ExportCount, ExportOffset
    head += struct.pack("<ii", 0, 0)                 # ImportCount, ImportOffset
    head += b"\x00" * 32

    name_offset = len(head)
    head += name_blob
    export_offset = len(head)

    total_header = export_offset + 100
    exp = bytearray()
    exp += struct.pack("<i", 0)                      # ClassIndex
    exp += struct.pack("<ii", 0, 0)                  # Super, Template
    exp += struct.pack("<i", 0)                      # OuterIndex
    exp += nm.fname("FCE_Timetable_TT")              # ObjectName
    exp += struct.pack("<i", 0)                      # ObjectFlags
    exp += struct.pack("<q", len(export_body))       # SerialSize
    exp += struct.pack("<q", total_header)           # SerialOffset (absolute)
    exp += b"\x00" * 60
    head += exp

    struct.pack_into("<i", head, head_size_pos, total_header)
    struct.pack_into("<ii", head, name_off_pos, len(nm.names), name_offset)
    struct.pack_into("<ii", head, exp_off_pos, 1, export_offset)

    with open(base + ".uasset", "wb") as f:
        f.write(bytes(head))
    with open(base + ".uexp", "wb") as f:
        f.write(bytes(export_body))

    print(f"built {base}.uasset + .uexp - {len(specs)} services, "
          f"{len(nm.names)} names, {len(export_body)} bytes of payload")
    return base, truth, route


if __name__ == "__main__":
    build()
