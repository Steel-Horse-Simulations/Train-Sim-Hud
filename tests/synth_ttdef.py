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


def instruction(nm, station, arrival=None, departure=None, stopping=True,
                itype="LoadUnload", simulated=False, guid_byte=7):
    body = bytearray()
    body += tag(nm, "InstructionType", "EnumProperty",
                nm.fname(f"ERouteTimetableServiceInstructionType::{itype}"),
                inner_type="ERouteTimetableServiceInstructionType")
    body += route_location(nm, station, guid_byte=guid_byte)
    if arrival is not None:
        body += timespan(nm, "SimulatedArrivalTime" if simulated else "ArrivalTime",
                         arrival)
    if departure is not None:
        body += timespan(nm, "SimulatedCompletionTime" if simulated else "CompletionTime",
                         departure)
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
    instrs += struct.pack("<i", len(stops))          # element count
    instrs += tag(nm, "Instructions", "StructProperty", b"",
                  struct_type="RouteTimetableServiceInstruction")
    for i, (st, arr, dep, stopping) in enumerate(stops):
        instrs += instruction(nm, st, arr, dep, stopping,
                              simulated=simulated, guid_byte=(i % 200) + 1)
    body += tag(nm, "Instructions", "ArrayProperty", bytes(instrs),
                inner_type="StructProperty")
    body += nm.fname("None")
    return bytes(body)


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

    for headcode, stops, sim in specs:
        services_bin += service(nm, f"SVC_{headcode}", headcode, stops, simulated=sim)
        truth.append({
            "headcode": headcode,
            "stops": [s[0] for s in stops],
            "calls": [s[0] for s in stops if s[3]],
            "simulated": sim,
        })

    export_body = bytearray()
    arr = bytearray()
    arr += struct.pack("<i", len(specs))
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
