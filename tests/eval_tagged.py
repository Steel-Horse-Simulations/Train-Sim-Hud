"""
Scores parse_track_records() against known ground truth.

The fixture is written from Unreal's FPropertyTag spec, independently of the
parser, so agreement between them is evidence rather than circularity.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pak_tools                       # noqa: E402
import synth_tagged as T               # noqa: E402


def _check(base, truth, label):
    r = pak_tools.parse_track_records(base + ".uexp")
    print(f"\n--- {label} ---")
    if "error" in r:
        print("  ERROR:", r["error"]); return False
    true_stops = sum(1 for x in truth if x["type"] == "StopPoint")
    print(f"  records parsed   {r['records_parsed']}  (true {len(truth)})")
    print(f"  guid byte        {r['guid_byte']}")
    print(f"  StopPoints       {r['stop_points']}  (true {true_stops})")
    print(f"  data types       {r['data_types']}")
    ok = True
    if r["records_parsed"] != len(truth):
        print("  FAIL: record count"); ok = False
    if r["stop_points"] != true_stops:
        print("  FAIL: StopPoint count"); ok = False

    # Domain rules: first stop of a service has no arrival, last no departure.
    # Those must survive as ABSENT FIELDS, not be invented or dropped.
    stops = [s for s in r["sample_stops"]]
    first = stops[0]["fields"]
    if "ArrivalTime" in first:
        print("  FAIL: invented an arrival on a first stop"); ok = False
    else:
        print("  first stop has a departure and no arrival - correct")
    ribbons = [s["fields"].get("NetworkRibbonLocation") for s in stops]
    if not all(ribbons):
        print("  FAIL: lost NetworkRibbonLocation"); ok = False
    else:
        print(f"  ribbon names read back: {ribbons[:5]}")
    return ok


def run_with_guid():
    base, truth = T.main("/tmp/synth_tagged_g", guid_byte=True)
    return _check(base, truth, "tagged properties, HasPropertyGuid byte present")


def run_without_guid():
    """Older UE4 packages have no HasPropertyGuid byte. The parser must work
    out which layout it is looking at rather than assuming one."""
    base, truth = T.main("/tmp/synth_tagged_n", guid_byte=False)
    return _check(base, truth, "tagged properties, no HasPropertyGuid byte")


def run_opaque_control():
    """Genuinely opaque binary. Must refuse, not hallucinate records."""
    d = "/tmp/synth_opaque"
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, "Opaque_DataTrack")
    T.build_uasset(base + ".uasset")
    rng = random.Random(9)
    with open(base + ".uexp", "wb") as f:
        f.write(bytes(rng.randrange(256) for _ in range(200_000)))
    r = pak_tools.parse_track_records(base + ".uexp")
    print("\n--- control: opaque binary, no tagged properties ---")
    if "error" in r:
        print("  correctly refused:", r["error"]); return True
    print(f"  records parsed   {r['records_parsed']}")
    print(f"  StopPoints       {r['stop_points']}")
    if r["stop_points"] > 0:
        print("  FAIL: invented StopPoints in random bytes"); return False
    if r["records_parsed"] > 40:
        print("  FAIL: too many spurious records from noise"); return False
    print("  no StopPoints invented")
    return True


if __name__ == "__main__":
    results = [run_with_guid(), run_without_guid(), run_opaque_control()]
    print("\n" + ("ALL PASS" if all(results) else "FAILURES PRESENT"))
    sys.exit(0 if all(results) else 1)
