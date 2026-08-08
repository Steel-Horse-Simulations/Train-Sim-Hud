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


def run_wide_fnames():
    """16-byte FNames (int64 index + Number). The parser must discover the
    width rather than assume it - a wrong width does not fail loudly, it
    reads exactly one tag and then stops, which is what the real Leven layer
    reports."""
    base, truth = T.main_wide("/tmp/eval_wide")
    r = pak_tools.parse_track_records(base + ".uexp")
    print("\n--- tagged properties, 16-byte FName fields ---")
    if "error" in r:
        print("  FAIL:", r["error"]); return False
    true_stops = sum(1 for x in truth if x["type"] == "StopPoint")
    print(f"  fname width      {r['fname_width']} bytes per component")
    print(f"  records parsed   {r['records_parsed']}  (true {len(truth)})")
    print(f"  StopPoints       {r['stop_points']}  (true {true_stops})")
    ok = True
    if r["records_parsed"] != len(truth) or r["stop_points"] != true_stops:
        print("  FAIL: counts"); ok = False
    if r["fname_width"] != 8:
        print("  FAIL: did not detect the wide layout"); ok = False
    return ok


def run_window_diagnostic():
    """The hex window must show, around a known field name, what follows it -
    that is how the layout gets read off instead of guessed at. Both earlier
    format conclusions were reached without ever looking at these bytes."""
    base, _ = T.main("/tmp/eval_win")
    r = pak_tools.probe_name_references(base + ".uexp", window_around="DataType")
    print("\n--- diagnostic: hex window around a known field ---")
    ok = True
    if not r.get("windows"):
        print("  FAIL: no windows produced"); return False
    w = r["windows"][0]
    names = [(x["rel"], x["name"]) for x in w["resolved_names"]]
    print(f"  around DataType @ {w['centre']}: {names[:4]}")
    if not any(n == "EnumProperty" for _rel, n in names):
        print("  FAIL: did not surface the type name next to the field name"); ok = False
    cb = r.get("chain_break")
    if not cb or not cb["tags_read"]:
        print("  FAIL: no chain-break detail"); ok = False
    else:
        print(f"  chain read {len(cb['tags_read'])} tags then broke at {cb['broke_at']}")
    return ok


def run_probe():
    """The probe must tell the two serialisation modes apart, not merely
    recognise the one it was written for."""
    print("\n--- probe: distinguishing tagged from unversioned ---")
    ok = True
    base, _ = T.main("/tmp/probe_t")
    a = pak_tools.probe_name_references(base + ".uexp")
    print("  tagged fixture      :", a["verdict"][:64])
    if not a["property_type_names_referenced"] or a["longest_tag_chain"] < 2:
        print("  FAIL: did not see tags in a tagged asset"); ok = False

    base, _ = T.main_unversioned("/tmp/probe_u")
    b = pak_tools.probe_name_references(base + ".uexp")
    print("  unversioned fixture :", b["verdict"][:64])
    if b["property_type_names_referenced"]:
        print("  FAIL: saw property types where none were written"); ok = False
    if not b["enum_values_referenced"]:
        print("  FAIL: missed the enum values, which ARE present"); ok = False
    if "UNVERSIONED" not in b["verdict"]:
        print("  FAIL: did not identify unversioned serialisation"); ok = False

    # and the failing parse must carry the probe with it
    r = pak_tools.parse_track_records(base + ".uexp")
    if "probe" not in r:
        print("  FAIL: parse failure did not attach a probe"); ok = False
    else:
        print("  parse failure attaches its own probe - correct")
    return ok


if __name__ == "__main__":
    results = [run_with_guid(), run_without_guid(), run_wide_fnames(),
               run_opaque_control(), run_probe(), run_window_diagnostic()]
    print("\n" + ("ALL PASS" if all(results) else "FAILURES PRESENT"))
    sys.exit(0 if all(results) else 1)
