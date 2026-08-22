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


# British headcode classes. The first character says what a working IS, and
# it explains output that would otherwise look like a parse failure: on the
# real Fife Circle file 90 of 91 class-5 services have NO passenger calls,
# because class 5 is empty coaching stock - a positioning move with nowhere
# to call. Reporting that as "0 stops" without saying why invites someone to
# go looking for a bug that is not there.
HEADCODE_CLASSES = {
    "1": "express passenger",
    "2": "stopping passenger",
    "3": "parcels or empty",
    "4": "freight",
    "5": "empty coaching stock",
    "6": "freight",
    "7": "freight",
    "8": "freight",
    "9": "passenger",
    "0": "light engine",
}


def classify_headcode(headcode):
    """('class digit', 'what it is') for a headcode, or (None, None)."""
    if not headcode or not headcode[0].isdigit():
        return None, None
    return headcode[0], HEADCODE_CLASSES.get(headcode[0])


def expects_calls(headcode):
    """Whether a working of this class should be calling anywhere.

    Empty stock and light engine moves legitimately have none. This is why
    the parser reports a service class rather than treating an empty stop
    list as a failure.
    """
    c, _ = classify_headcode(headcode)
    return c not in ("0", "5")


CONTINUATION_SUFFIX = re.compile(
    r"(_End|_\d+_[A-Z]|_[A-Z]|-[A-Z]|[A-Z])$")


def _classify_role(name, headcode):
    """Player leg, AI continuation, or a plain service.

    TSW splits one working across several records and marks the
    continuations with a suffix - but the suffix takes several forms, and
    only checking `_B` and `_End` missed most of them. On the real Fife
    Circle file that left 145 services looking "unexpectedly empty" when
    they were ordinary continuation legs: `_B` (34), `_1_B` (8), `_C`, `_A`,
    and bare or hyphenated forms like `2P02-B` and `2G16B` (101 between
    them).

    A trailing capital is only treated as a continuation when the name is
    otherwise the headcode, so a genuine service name ending in a capital is
    not swept up.
    """
    if not name:
        return None
    if headcode and name == "P" + headcode:
        return "player_leg"
    if headcode:
        rest = name[len(headcode):] if name.startswith(headcode) else None
        if rest and CONTINUATION_SUFFIX.fullmatch(rest):
            return "ai_continuation"
    if name.endswith("_End") or re.search(r"(_\d+)?_[A-Z]$", name):
        return "ai_continuation"
    return None


def _hms(ticks, allow_next_day=True):
    """A Timespan as a clock time.

    A service that runs past midnight keeps counting: a call at ten past
    midnight on a train that left at 23:50 is stored as 24:10, not 00:10,
    because a Timespan is an elapsed duration from the start of the service
    day rather than a wall clock.

    Rejecting anything at or beyond 24 hours - which the first version did -
    silently dropped exactly those calls. On the real Fife Circle file it
    cost the last two stops of P2K85 and six of P2K86, both terminating at
    Leven after 23:50. The times were there; they were being thrown away.

    Values past midnight are wrapped for display and flagged by the caller,
    so 24:10 shows as 00:10 while the ORDER stays correct.
    """
    if not ticks or ticks <= 0:
        return None
    if ticks >= TICKS_PER_DAY:
        if not allow_next_day or ticks >= TICKS_PER_DAY * 2:
            return None
        ticks -= TICKS_PER_DAY
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


# No \b anchors: an underscore is a word character, so \b never matches
# between "P" and "1" in "P1L86", and every real service name failed.
# Anchor on the surrounding DIGITS instead, so a letter prefix or an
# _B / _1 suffix does not block the match.
HEADCODE_RE = re.compile(r"(?<![0-9])(\d[A-Z]\d{2})(?![0-9])")


def derive_headcode(name):
    """Pulls the British headcode out of a service name.

    The real asset does not carry a HeadCode property - service names look
    like `1L86_B`, `P1L86`, `1L27_1`, where the code is embedded with a
    prefix or suffix. All 500 services parsed had headcode None until this,
    which would have left the HUD with nothing to label a service by.
    """
    m = HEADCODE_RE.search(name or "")
    return m.group(1) if m else None


def _to_seconds(hms):
    """Clock text back to seconds, for recomputing a dwell after two
    LoadUnload records at one platform are merged."""
    if not hms:
        return None
    try:
        h, m, s = (int(x) for x in hms.split(":"))
        return h * 3600 + m * 60 + s
    except Exception:
        return None


def _build_stops(instructions):
    """Turns instructions into the stop list a passenger would recognise.

    THE PAIRING RULE. A station call is a **GoTo followed by a LoadUnload**.
    The GoTo names the destination; the LoadUnload that follows it carries
    the arrival and completion times and the dwell. A GoTo with no LoadUnload
    after it is a routing waypoint the train passes through, NOT a call.

    Reading every GoTo as a stop - which is what the first version did -
    produced 3,263 "stops" on the real Fife Circle file where 80% had
    arrival equal to departure and every instruction came back as GoTo with
    is_stopping true. Both were signs that the times were being read from
    the wrong record.

    Simulated times are used ONLY as a fallback and never in preference to
    real ones. The reference implementation warns that SimulatedArrivalTime
    is frequently unset or non-monotonic, and will happily produce a stop at
    12:01 sitting before an earlier stop at 10:08 - so a real time always
    wins, and a missing time stays missing.

    Domain rules from the user, all preserved: first stop has a departure
    and no arrival, last has an arrival and no departure, freight often has
    no arrivals at all. A single time is CORRECT data. Nothing is dropped
    for having one.
    """
    stops = []
    pending_departure = 0
    i = 0
    n = len(instructions)
    while i < n:
        ins = instructions[i]
        itype = (ins.get("type") or "").split("::")[-1]

        if itype in ("Couple", "Uncouple"):
            i += 1
            continue

        if itype != "GoTo":
            if itype == "LoadUnload" and stops:
                # An unpaired LoadUnload AFTER calls have begun is extra
                # station work at the stop just made - a second door release,
                # or a longer stand. It is NOT another call.
                #
                # Treating it as one produced the trailing duplicate seen on
                # the real file: P1L24_1 ended "Edinburgh Waverly 18:04,
                # Edinburgh Waverly 18:09". Its times are folded into the
                # call before it, which is what they describe: the earliest
                # arrival and the latest departure at that platform.
                last = stops[-1]
                arr = ins["arrival_ticks"] or ins["sim_arrival_ticks"]
                dep = ins["completion_ticks"] or ins["sim_completion_ticks"]
                a_new, d_new = _hms(arr), _hms(dep)
                if a_new and (not last["arrival"] or a_new < last["arrival"]):
                    last["arrival"] = a_new
                if d_new and (not last["departure"] or d_new > last["departure"]):
                    last["departure"] = d_new
                if last["arrival"] and last["departure"]:
                    a = _to_seconds(last["arrival"])
                    d = _to_seconds(last["departure"])
                    last["dwell_seconds"] = max(0, d - a) if (a is not None and d is not None) else None
                i += 1
                continue
            if itype == "LoadUnload":
                # A leading LoadUnload is the service standing at its
                # starting platform. It carries a booked departure but NO
                # destination of its own.
                #
                # Borrowing the name from the following GoTo - which the
                # first version did - names it after the NEXT station
                # instead, because a GoTo is where the train is going, not
                # where it is. On the real file that produced 331 services
                # with a duplicated station: P1L86 read "Kirkcaldy 18:01,
                # Kirkcaldy 18:10" when it starts at Kirkcaldy and its first
                # call is also Kirkcaldy... no. It starts SOMEWHERE ELSE and
                # the 18:10 Kirkcaldy is the real first call.
                #
                # There is nothing in the record that names the origin, so
                # it is not invented. The departure is attached to the first
                # real call instead, which is where a reader would look for
                # it, and the origin is reported as unknown.
                pending_departure = (ins["completion_ticks"]
                                     or ins["sim_completion_ticks"])
                i += 1
                continue

        nxt = instructions[i + 1] if i + 1 < n else None
        nxt_type = (nxt.get("type") or "").split("::")[-1] if nxt else None
        if nxt_type == "LoadUnload":
            stop = _make_stop(ins, nxt)
            if pending_departure and not stops:
                # The booked departure from the origin belongs on the first
                # call, which is where anyone reading a stop list expects to
                # find "this service leaves at".
                stop["origin_departure"] = _hms(pending_departure)
                pending_departure = 0
            stops.append(stop)
            i += 2                       # the pair is one call
            continue

        # GoTo with no LoadUnload after it: passed through, not called at.
        i += 1

    return stops


def _make_stop(goto_ins, time_ins, starts_here=False):
    label = (goto_ins.get("station") or goto_ins.get("display_name")
             or time_ins.get("station") or time_ins.get("display_name") or "")
    place, stype, snum = split_location(label)

    # Real times only, with simulated as a fallback - never mixed, since a
    # simulated value can sit out of order against real ones.
    arrival = time_ins["arrival_ticks"] or time_ins["sim_arrival_ticks"]
    departure = time_ins["completion_ticks"] or time_ins["sim_completion_ticks"]
    if starts_here:
        arrival = 0                       # the train is already there

    # A dwell of zero with both times present is what an unpaired read looks
    # like; report the dwell so it can be judged rather than hidden.
    # A dwell needs BOTH times. Reporting one from waiting_seconds alone
    # produced a stop showing "no arrival, no departure, dwell 30s", which
    # is self-contradictory and would read on a HUD as a timed call.
    dwell = None
    if arrival and departure and departure >= arrival:
        dwell = int((departure - arrival) // TICKS_PER_SECOND)

    return {
        "station": place,
        # True when this call falls after midnight. The HUD needs to know:
        # 00:10 sorts before 23:50 as a string, so a stop list ordered by
        # displayed time would put the end of the journey at the top.
        "next_day": bool(arrival >= TICKS_PER_DAY or departure >= TICKS_PER_DAY),
        "structure_type": stype or None,
        "platform": snum or None,
        "raw_location": label,
        "arrival": _hms(arrival),
        "departure": _hms(departure),
        "dwell_seconds": dwell,
        "waiting_seconds": time_ins.get("waiting_seconds"),
        "instruction_type": (time_ins.get("type") or "").split("::")[-1],
        "ribbon_guid": goto_ins.get("ribbon_guid"),
        "ribbon_offset": goto_ins.get("ribbon_offset"),
        "used_simulated": bool(
            not time_ins["arrival_ticks"] and time_ins["sim_arrival_ticks"]),
    }


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


# Array names that hold the service list. `Services` is what the Fife Circle
# definition uses; the others are added as they are found. Anything not here
# is REPORTED rather than silently ignored - guessing a name and returning
# nothing is how NorthLondonLine came back as "no_named_stops" with no clue
# which property it should have been reading.
SERVICE_ARRAY_NAMES = ("Services", "ServiceDefinitions", "TimetableServices",
                       "RouteServices", "ServiceList", "Timetables")


def _walk_top_level(r, limit, seen_properties=None):
    """Finds the service array in an export, recording every top-level
    property it passes so an unrecognised layout can be diagnosed."""
    services = []
    while r.pos < limit:
        t = uasset.read_tag(r)
        if t is None:
            break
        dp = r.pos
        if seen_properties is not None:
            seen_properties.append({
                "name": t.name, "type": t.ptype, "size": t.size,
                "inner": t.inner_type or t.struct_type or None,
            })
        if t.ptype == "ArrayProperty" and t.name in SERVICE_ARRAY_NAMES:
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


def parse_timetable_definition(path, max_services=5000):
    """max_services is a guard against a runaway parse, not a page size. The
    first real run hit a 500 cap silently, reporting 500 services as though
    that were the answer."""
    """Reads a RouteTimetableDefinition into services with named stops."""
    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}
    try:
        pkg = uasset.Package(path)
    except Exception as e:
        return {"error": "not_a_package", "path": path, "detail": str(e)}

    if getattr(pkg, "uexp_missing", False):
        return {
            "error": "uexp_missing",
            "path": path,
            "detail": "The .uasset was found but its .uexp is not beside it. "
                      "A cooked asset keeps all its data in the .uexp, so "
                      "extract BOTH files - the .uasset alone parses cleanly "
                      "and yields nothing, which looks like an empty route.",
            "package": pkg.summary(),
        }

    services = []
    seen_properties = []
    for exp in pkg.exports:
        body = pkg.export_body(exp)
        if not body:
            continue
        r = pkg.reader_for(exp)
        found = _walk_top_level(r, len(body), seen_properties)
        for svc in found:
            svc["export"] = exp["object_name"]
            services.append(svc)
        if len(services) >= max_services:
            break

    out = []
    for svc in services[:max_services]:
        # _build_stops already returns only real calls - a GoTo without a
        # following LoadUnload is a pass-through and never reaches here.
        calls = _build_stops(svc["instructions"])
        times = [s["arrival"] or s["departure"] for s in calls if (s["arrival"] or s["departure"])]
        # TSW splits one working across a player leg (P<code>) and an AI
        # continuation (<code>_B). Both are genuine records in the file - the
        # findings doc records this - so they are LABELLED, not merged and
        # not discarded. Merging would hide a real distinction; discarding
        # would lose half the timetable.
        nm_ = svc["name"] or ""
        code = svc["headcode"] or derive_headcode(nm_)
        role = _classify_role(nm_, code)

        hc_class, hc_meaning = classify_headcode(code)
        out.append({
            "name": svc["name"],
            "role": role,
            "headcode": code,
            "headcode_class": hc_class,
            "service_class": hc_meaning,
            "expects_calls": expects_calls(code),
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
    # An empty stop list is only a concern for a working that SHOULD call
    # somewhere. Counting them apart keeps a real fault visible instead of
    # being buried under legitimate empty-stock moves.
    # A continuation leg legitimately carries no calls: it is the tail of a
    # working whose passenger calls belong to the leg before it. Counting
    # those as unexplained hid the real number - 145 of them on the Fife
    # Circle file, of which 122 share a headcode with a service that does
    # have stops.
    empty_expected = [s for s in out
                      if not s["stops"] and s["expects_calls"] and not s["role"]]
    empty_ok = [s for s in out
                if not s["stops"] and (not s["expects_calls"] or s["role"])]
    return {
        "truncated": len(services) >= max_services,
        "path": path,
        "package": pkg.summary(),
        "service_count": len(out),
        "services": out,
        "named_stops": named,
        "services_without_calls": len(empty_expected) + len(empty_ok),
        "empty_stock_or_light_engine": len(empty_ok),
        "unexpectedly_empty": len(empty_expected),
        # What the asset actually contains, so an unrecognised layout can be
        # read off the result instead of guessed at. Only included when
        # nothing was found - it is diagnostic, not routine output.
        "top_level_properties": (seen_properties[:60] if not named else None),
        "array_properties": ([p for p in seen_properties
                              if p["type"] == "ArrayProperty"][:20]
                             if not named else None),
        "service_array_names_tried": (list(SERVICE_ARRAY_NAMES) if not named
                                      else None),
        "verdict": (
            f"{len(out)} services with {named} named stops, read straight from "
            "the timetable definition - no driving, no inference. "
            f"{len(empty_ok)} carry no calls because they are empty stock, "
            f"light engine or continuation legs; {len(empty_expected)} are "
            "unexpectedly empty."
            if named else
            f"{len(out)} services parsed and no stop carries a station name. "
            "The arrays actually present in this asset are listed in "
            "array_properties - if one of them is the service list, its name "
            "needs adding to SERVICE_ARRAY_NAMES."
        ),
    }
