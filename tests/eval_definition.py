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

    by_code = {s["headcode"]: s for s in r["services"]}
    for t in truth:
        svc = by_code.get(t["headcode"])
        if not svc:
            print(f"  FAIL: lost service {t['headcode']}"); ok = False
            continue
        # Only CALLS should appear, not stations passed through.
        if svc["stop_count"] != len(t["calls"]):
            print(f"  FAIL: {t['headcode']} has {svc['stop_count']} stops, "
                  f"expected {len(t['calls'])}"); ok = False
        names = [s["station"] for s in svc["stops"]]
        want = [synth_ttdef and s.split(" Platform ")[0] for s in t["calls"]]
        if names != want:
            print(f"  FAIL: {t['headcode']} stations {names[:3]} != {want[:3]}")
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

    print("  " + ("PASS" if ok else "FAIL"))
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
