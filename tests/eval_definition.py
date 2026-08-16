"""
Checks the RouteTimetableDefinition parser against a package written from the
UE4 format itself.

The fixture is built from the format spec - package header, name map, export
table, FPropertyTag streams - not from the parser, so agreement between them
is evidence rather than circularity.

Every domain rule the user established is planted, because each one is a case
where a naive parser produces plausible-looking wrong output:
  - first stop has a DEPARTURE and no arrival
  - last stop has an ARRIVAL and no departure
  - a freight working has NO arrivals at all
  - one service PASSES two stations without calling
  - an AI service carries only SIMULATED times
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import synth_ttdef                      # noqa: E402
import timetable_definition as td       # noqa: E402
import uasset                           # noqa: E402


def run():
    base, truth, route = synth_ttdef.build("/tmp/eval_ttdef")
    ok = True

    pkg = uasset.Package(base + ".uasset")
    print(f"\n  package: {len(pkg.names)} names, {len(pkg.exports)} exports, "
          f"{len(pkg.uexp)} payload bytes")
    if not pkg.names or not pkg.exports:
        print("  FAIL: header did not parse"); return False

    r = td.parse_timetable_definition(base + ".uasset")
    if "error" in r:
        print("  FAIL:", r["error"], r.get("detail")); return False
    print(f"  services {r['service_count']} (true {len(truth)}), "
          f"named stops {r['named_stops']}")
    if r["service_count"] != len(truth):
        print("  FAIL: service count"); ok = False

    # Keyed by service NAME, not headcode: TSW splits a working across a
    # player leg and an AI continuation which SHARE a headcode, so keying on
    # the code silently compares the wrong record.
    by_name = {s["name"]: s for s in r["services"]}
    by_code = {s["headcode"]: s for s in r["services"] if not s["role"]}
    for t in truth:
        svc = by_name.get(t["service_name"])
        if not svc:
            print(f"  FAIL: lost service {t['service_name']}"); ok = False
            continue
        # Only CALLS should appear, not stations passed through.
        if svc["stop_count"] != len(t["calls"]):
            print(f"  FAIL: {t['service_name']} has {svc['stop_count']} stops, "
                  f"expected {len(t['calls'])}"); ok = False
        names = [s["station"] for s in svc["stops"]]
        want = [synth_ttdef and s.split(" Platform ")[0] for s in t["calls"]]
        if names != want:
            print(f"  FAIL: {t['service_name']} stations {names[:3]} != {want[:3]}")
            ok = False

    # platform designators split off the station name
    first = by_code["1E01"]["stops"][0]
    print(f"  first stop: {first['station']} plat {first['platform']} "
          f"arr {first['arrival']} dep {first['departure']}")
    if first["platform"] != "1" or first["station"] != "Leven":
        print("  FAIL: platform not split from the station name"); ok = False

    # DOMAIN RULES - each is correct data, not a parse failure
    if first["arrival"] is not None:
        print("  FAIL: invented an arrival on the first stop"); ok = False
    last = by_code["1E01"]["stops"][-1]
    if last["departure"] is not None:
        print("  FAIL: invented a departure on the last stop"); ok = False
    freight = by_code["6S42"]
    if any(s["arrival"] for s in freight["stops"]):
        print("  FAIL: invented arrivals on a freight working"); ok = False
    else:
        print("  freight has departures only - correct")
    ai = by_code["2K05"]
    if not any(s["arrival"] for s in ai["stops"][1:]):
        print("  FAIL: simulated times were ignored"); ok = False
    else:
        print("  AI service read from simulated times - correct")

    # times must ascend within a service
    for svc in r["services"]:
        seq = [s["arrival"] or s["departure"] for s in svc["stops"]]
        seq = [x for x in seq if x]
        if seq != sorted(seq):
            print(f"  FAIL: {svc['headcode']} times not ascending"); ok = False

    # headcodes must survive real-world name shapes: an underscore is a
    # word character, so a \\b-anchored regex matched none of 1L86_B, P1L86
    # or 1L27_1 - every service on the real file.
    for name, want in (("1L86_B", "1L86"), ("P1L86", "1L86"),
                       ("1L27_1", "1L27"), ("SVC_2K05", "2K05"),
                       ("nonsense", None)):
        got = td.derive_headcode(name)
        if got != want:
            print(f"  FAIL: headcode {name} -> {got}, expected {want}"); ok = False
    print("  headcodes derived from real-world name shapes")

    # A stop must never mix an explicit time with a simulated one: on the
    # real file that produced dwells like 05:57 -> 17:55.
    for svc in r["services"]:
        for st in svc["stops"]:
            if st["arrival"] and st["departure"]:
                a = [int(x) for x in st["arrival"].split(":")]
                dp = [int(x) for x in st["departure"].split(":")]
                dwell = (dp[0] - a[0]) * 3600 + (dp[1] - a[1]) * 60 + (dp[2] - a[2])
                if not (0 <= dwell <= 3600):
                    print(f"  FAIL: implausible dwell {st['arrival']}->{st['departure']} "
                          f"at {st['station']}"); ok = False
    print("  no implausible dwells")

    # GoTo + LoadUnload PAIRING. A GoTo names the destination; the
    # LoadUnload after it carries the times. Reading every GoTo as a stop
    # gave 3,263 "stops" on the real file with 80% having arrival ==
    # departure - the signature of times read from the wrong record.
    dwells = [s["dwell_seconds"] for svc in r["services"] for s in svc["stops"]
              if s["dwell_seconds"] is not None]
    zero = sum(1 for d in dwells if d == 0)
    print(f"  dwells recorded: {len(dwells)}, of which zero-length: {zero}")
    if dwells and zero == len(dwells):
        print("  FAIL: every dwell is zero - arrival and departure came from "
              "the same record"); ok = False

    # Pass-throughs must NOT appear as calls.
    r03 = by_code["1R03"]
    if r03["stop_count"] != 6:
        print(f"  FAIL: 1R03 has {r03['stop_count']} stops, expected 6 - "
              "pass-through GoTos are being counted as calls"); ok = False
    else:
        print("  passed stations excluded from the call list")

    # Player leg and AI continuation are separate records; label, never merge.
    roles = {s["name"]: s["role"] for s in r["services"]}
    print(f"  roles: P1L86={roles.get('P1L86')}, 1L86_B={roles.get('1L86_B')}")
    if roles.get("P1L86") != "player_leg" or roles.get("1L86_B") != "ai_continuation":
        print("  FAIL: player/AI legs not labelled"); ok = False

    print("  " + ("PASS" if ok else "FAIL"))
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
