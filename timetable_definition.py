"""
Parses a `RouteTimetableDefinition` - the timetable INDEX asset - into
services with NAMED stops and scheduled times.

This is the file that actually holds the timetable. Every earlier search
looked in the DataTrack layers and came back negative, four times over,
because station identity is not there and was never meant to be. The
DataTrack carries the simulated running profile; the schedule is here.

The structure that matters, per service:

    Instructions[]
      InstructionType          GoTo / Couple / Uncouple / LoadUnload
      Destination              -> Name      the STATION
                               -> Location  NetworkRibbonLocation (guid+offset)
      DestinationDisplayName   human-readable fallback
      ArrivalTime              Timespan, 100ns ticks
      CompletionTime           Timespan  (departure)
      SimulatedArrivalTime     Timespan  (AI services carry only these)
      SimulatedCompletionTime  Timespan
      bIsStopping              whether this is a CALL rather than a pass

Name, arrival, departure and "is this a stop" all sit on the same record, so
there is no join to reconstruct.

Domain rules that shape the output, from the user and already recorded in the
findings: the first stop of a service has a departure and no arrival, the
last has an arrival and no departure, and freight often has no scheduled
arrivals at all. A single time is therefore CORRECT data, never a parse
failure, and nothing here discards a stop for having one.
"""

import os
import re

import uasset

TICKS_PER_SECOND = 10_000_000
TICKS_PER_DAY = TICKS_PER_SECOND * 86_400

# Longest first: "Track 02" can appear inside a station name, so a shorter
# separator must not win.
LOCATION_SEPARATORS = (" Platform ", " Siding ", " Track ", " Line ")


def _hms(ticks):
    if not ticks or ticks <= 0 or ticks >= TICKS_PER_DAY:
        return None
    s = int(ticks // TICKS_PER_SECOND)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def split_location(loc):
    """'Edinburgh Waverley Platform 2' -> ('Edinburgh Waverley', 'Platform', '2')"""
    for sep in LOCATION_SEPARATORS:
        i = loc.find(sep)
        if i > 0:
            return loc[:i], sep.strip(), loc[i + len(sep):]
    return loc, "", ""


def _read_route_location(r, end):
    """A RouteLocationName struct: the station Name, plus a
    NetworkRibbonLocation giving a ribbon GUID and an offset along it.

    The ribbon pair is what makes coordinates possible without driving - it
    is a position on the network, resolvable to lat/long from the route's own
    geometry.
    """
    name, guid, offset = "", None, None
    while r.pos < end:
        t = uasset.read_tag(r)
        if t is None:
            break
        dp = r.pos
        if t.name == "Name" and t.ptype == "NameProperty":
            v = r.fname()
            if v != "None":
                name = v
        elif t.name == "Location" and t.ptype == "StructProperty":
            inner_end = dp + t.size
            while r.pos < inner_end:
                it = uasset.read_tag(r)
                if it is None:
                    break
                idp = r.pos
                if it.ptype == "StructProperty" and it.size == 16:
                    guid = r.guid().hex()
                elif it.ptype == "FloatProperty":
                    offset = round(r.f32(), 3)
                r.seek(idp + it.size)
            r.seek(inner_end)
        r.seek(dp + t.size)
    r.seek(end)
    return name, guid, offset


def _parse_instruction_array(r, end, limit=4000):
    count = r.i32()
    if count <= 0 or count > limit:
        r.seek(end)
        return []
    save = r.pos
    inner = uasset.read_tag(r)
    if inner is None or inner.ptype != "StructProperty":
        r.seek(save)
    out = []
    for _ in range(count):
        if r.pos >= end:
            break
        # Each element is a tag stream terminated by None; _parse_instruction
        # stops there and leaves the cursor ready for the next element.
        start = r.pos
        ins = _parse_instruction_until_none(r, end)
        if r.pos <= start:
            break
        out.append(ins)
    r.seek(end)
    return out


def _parse_instruction_until_none(r, limit):
    ins = {
        "type": None, "station": None, "display_name": None,
        "arrival_ticks": 0, "completion_ticks": 0,
        "sim_arrival_ticks": 0, "sim_completion_ticks": 0,
        "is_stopping": False, "waiting_seconds": None,
        "ribbon_guid": None, "ribbon_offset": None, "formation": None,
    }
    while r.pos < limit:
        t = uasset.read_tag(r)
        if t is None:
            return ins                      # the None sentinel ends this struct
        dp = r.pos
        n, p = t.name, t.ptype
        if n == "InstructionType" and p == "EnumProperty":
            ins["type"] = r.fname().split("::")[-1]
        elif n == "Destination" and p == "StructProperty":
            name, guid, off = _read_route_location(r, dp + t.size)
            ins["station"], ins["ribbon_guid"], ins["ribbon_offset"] = name, guid, off
        elif n == "DestinationDisplayName" and p == "TextProperty":
            ins["display_name"] = r.ftext(t.size)
        elif p == "StructProperty" and t.struct_type == "Timespan":
            v = r.i64()
            key = {"ArrivalTime": "arrival_ticks",
                   "CompletionTime": "completion_ticks",
                   "SimulatedArrivalTime": "sim_arrival_ticks",
                   "SimulatedCompletionTime": "sim_completion_ticks"}.get(n)
            if key:
                ins[key] = v
            elif n == "WaitingTime":
                ins["waiting_seconds"] = v // TICKS_PER_SECOND
        elif n == "bIsStopping" and p == "BoolProperty":
            ins["is_stopping"] = bool(t.bool_val)
        elif n == "FormationName" and p == "NameProperty":
            ins["formation"] = r.fname()
        r.seek(dp + t.size)
    return ins


def _build_stops(instructions):
    """Turns instructions into the stop list a passenger would recognise.

    A stop keeps whichever times it has. First stop has departure only, last
    has arrival only, freight often has neither - all correct, none dropped.
    Explicit times win over simulated ones, which AI services carry instead.
    """
    stops = []
    for ins in instructions:
        label = ins.get("station") or ins.get("display_name") or ""
        if not label:
            continue
        arrival = ins["arrival_ticks"] or ins["sim_arrival_ticks"]
        departure = ins["completion_ticks"] or ins["sim_completion_ticks"]
        place, stype, snum = split_location(label)
        stops.append({
            "station": place,
            "structure_type": stype or None,
            "platform": snum or None,
            "raw_location": label,
            "arrival": _hms(arrival),
            "departure": _hms(departure),
            "is_stopping": ins["is_stopping"],
            "instruction_type": ins["type"],
            "ribbon_guid": ins["ribbon_guid"],
            "ribbon_offset": ins["ribbon_offset"],
            "waiting_seconds": ins["waiting_seconds"],
        })
    return stops


def _parse_service(r, limit):
    svc = {"name": None, "headcode": None, "operator": None,
           "formation": None, "is_player_drivable": None,
           "instructions": []}
    while r.pos < limit:
        t = uasset.read_tag(r)
        if t is None:
            return svc
        dp = r.pos
        n, p = t.name, t.ptype
        if p == "NameProperty" and n in ("ServiceName", "Name", "HeadCode",
                                         "Headcode", "ServiceNumber",
                                         "Operator", "FormationName"):
            v = r.fname()
            key = {"ServiceName": "name", "Name": "name",
                   "HeadCode": "headcode", "Headcode": "headcode",
                   "ServiceNumber": "headcode", "Operator": "operator",
                   "FormationName": "formation"}[n]
            if v and v != "None":
                svc[key] = svc[key] or v
        elif p == "StrProperty" and n in ("ServiceName", "FriendlyName"):
            v = r.fstr()
            if v:
                svc["name"] = svc["name"] or v
        elif p == "TextProperty" and n in ("FriendlyName", "DisplayName"):
            v = r.ftext(t.size)
            if v:
                svc["name"] = svc["name"] or v
        elif p == "BoolProperty" and n in ("bIsPlayerDrivable", "IsPlayerDrivable"):
            svc["is_player_drivable"] = bool(t.bool_val)
        elif p == "ArrayProperty" and n in ("Instructions", "ServiceInstructions"):
            svc["instructions"] = _parse_instruction_array(r, dp + t.size)
        r.seek(dp + t.size)
    return svc


def _walk_top_level(r, limit):
    services = []
    while r.pos < limit:
        t = uasset.read_tag(r)
        if t is None:
            break
        dp = r.pos
        if t.ptype == "ArrayProperty" and t.name in ("Services", "ServiceDefinitions"):
            count = r.i32()
            if 0 < count < 5000:
                save = r.pos
                inner = uasset.read_tag(r)
                if inner is None or inner.ptype != "StructProperty":
                    r.seek(save)
                for _ in range(count):
                    if r.pos >= dp + t.size:
                        break
                    start = r.pos
                    services.append(_parse_service(r, dp + t.size))
                    if r.pos <= start:
                        break
        r.seek(dp + t.size)
    return services


def parse_timetable_definition(path, max_services=500):
    """Reads a RouteTimetableDefinition into services with named stops."""
    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}
    try:
        pkg = uasset.Package(path)
    except Exception as e:
        return {"error": "not_a_package", "path": path, "detail": str(e)}

    services = []
    for exp in pkg.exports:
        body = pkg.export_body(exp)
        if not body:
            continue
        r = pkg.reader_for(exp)
        found = _walk_top_level(r, len(body))
        for svc in found:
            svc["export"] = exp["object_name"]
            services.append(svc)
        if len(services) >= max_services:
            break

    out = []
    for svc in services[:max_services]:
        stops = _build_stops(svc["instructions"])
        calls = [s for s in stops if s["is_stopping"]] or stops
        times = [s["arrival"] or s["departure"] for s in calls if (s["arrival"] or s["departure"])]
        out.append({
            "name": svc["name"],
            "headcode": svc["headcode"],
            "operator": svc["operator"],
            "formation": svc["formation"],
            "is_player_drivable": svc["is_player_drivable"],
            "instruction_count": len(svc["instructions"]),
            "stop_count": len(calls),
            "first_time": times[0] if times else None,
            "last_time": times[-1] if times else None,
            "stops": calls,
        })

    named = sum(1 for s in out for st in s["stops"] if st["station"])
    return {
        "path": path,
        "package": pkg.summary(),
        "service_count": len(out),
        "services": out,
        "named_stops": named,
        "verdict": (
            f"{len(out)} services with {named} named stops, read straight from "
            "the timetable definition - no driving, no inference."
            if named else
            f"{len(out)} services parsed but no stop carries a station name. "
            "Check package.exports: this may not be the RouteTimetableDefinition, "
            "or the property names differ in this build."
        ),
    }
