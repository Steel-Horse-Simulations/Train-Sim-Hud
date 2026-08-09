"""
Scores find_stop_points() against known ground truth, and against two
negative controls it MUST refuse to answer confidently.

The negative controls matter as much as the positive one. Every wrong answer
this function produced during development was a CONFIDENT wrong answer, so
"does it get the right result" is only half the test - the other half is
"does it say so when there is nothing to find".
"""
import os
import random
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pak_tools                                     # noqa: E402
import synth_stoppoints as S                         # noqa: E402


def run_positive():
    base, truth, true_shift = S.main()
    r = pak_tools.find_stop_points(base + ".uexp")
    assert "error" not in r, r
    ok = True

    print("\n--- positive case ---")
    print(f"  shift            {r['shift']}   (true {true_shift})")
    print(f"  encoding         {r['encoding']}")
    print(f"  confirmed        {r['confirmed']}")
    print(f"  typed StopPoint  {r['typed_stop_point']}   "
          f"(true {sum(1 for _o, s in truth if s)})")
    print(f"  with station     {r['with_station']}")
    print(f"  agreement        {r['agreement']}")

    if r["shift"] != true_shift:
        print("  FAIL: wrong shift"); ok = False
    if not r["confirmed"]:
        print("  FAIL: refused a case it should have solved"); ok = False
    true_stops = sum(1 for _o, s in truth if s)
    if abs(r["typed_stop_point"] - true_stops) > 2:
        print("  FAIL: stop count off by more than 2"); ok = False

    # Do the recovered stops sit at real stations, in time order?
    svc = r["services"][0]
    named = [s for s in svc["stops"] if s["station"]]
    print(f"  first service    {svc['first']} -> {svc['last']} "
          f"({svc['track_points']} points, {svc['stop_count']} stops, "
          f"{svc['distinct_stations']} distinct stations)")
    for s in named[:6]:
        print(f"      {s['time']}  {s['station']}  [{s['type']}]")
    return ok


def run_random_control():
    """Pure noise. Must NOT be confirmed."""
    d = "/tmp/synth_noise"
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, "Noise_DataTrack")
    rng = random.Random(11)
    index_of, _ = S.build_uasset(base + ".uasset")
    # Random bytes alone get refused at "no times found", which does not
    # exercise the confirm guard at all. Real-looking times are planted so it
    # reaches classification and has to decide - with no FName structure
    # anywhere for it to find.
    buf = bytearray(rng.randrange(256) for _ in range(400_000))
    t = 6 * 3600
    for k in range(120):
        t += rng.randint(30, 400)
        off = rng.randrange(0, len(buf) - 8)
        buf[off:off + 8] = struct.pack("<q", t * S.TICKS)
    with open(base + ".uexp", "wb") as f:
        f.write(bytes(buf))
    r = pak_tools.find_stop_points(base + ".uexp")
    print("\n--- negative control: random bytes ---")
    if "error" in r:
        print("  refused:", r["error"]); return True
    print(f"  confirmed        {r['confirmed']}")
    print(f"  typed StopPoint  {r['typed_stop_point']}")
    if r["confirmed"]:
        print("  FAIL: confidently classified pure noise"); return False
    print("  correctly not confirmed")
    return True


def run_raw_enum_control():
    """Types written as a raw BYTE rather than an FName - the case the
    findings doc says is likely. Stations still resolve, so it must report
    station-anchored results and say the enum is not an FName, NOT invent a
    type for every record."""
    d = "/tmp/synth_rawenum"
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, "RawEnum_DataTrack")
    rng = random.Random(5)
    index_of, _ = S.build_uasset(base + ".uasset")
    buf = bytearray()
    truth = []

    def pad(lo, hi):
        for _ in range(rng.randint(lo, hi)):
            buf.extend(struct.pack("<i", 0 if rng.random() < 0.6 else rng.randint(1, 4000)))

    def filler():
        for _ in range(rng.randint(300, 900)):
            buf.extend(struct.pack("<i", 0 if rng.random() < 0.6 else rng.randint(1, 4000)))

    # Several services, not one. An earlier version wrote a single pass over
    # 8 stations, giving 8 stop records in 44 times - far too small a sample
    # for any statistical test to separate signal from coincidence, and a
    # fixture that bears no resemblance to the real layer's 104 services.
    t = 6 * 3600
    for run in range(6):
      t = (6 + run) * 3600
      for si, st in enumerate(S.STATIONS):
          for _ in range(rng.randint(3, 6)):
              t += rng.randint(20, 90)
              pad(1, 4)
              buf.append(rng.choice([1, 2, 3]))         # raw enum byte
              pad(1, 6)
              buf.extend(struct.pack("<q", t * S.TICKS))
              truth.append(None)
              pad(0, 5); filler()
          pad(1, 3)
          buf.append(0)                                  # StopPoint, as a byte
          pad(0, 4)
          buf.extend(struct.pack("<ii", index_of[st], 0))
          pad(1, 5)
          t += rng.randint(60, 200)
          buf.extend(struct.pack("<q", t * S.TICKS))
          truth.append(st)
          pad(1, 6); filler()
    with open(base + ".uexp", "wb") as f:
        f.write(bytes(buf))

    r = pak_tools.find_stop_points(base + ".uexp")
    print("\n--- control: enum as a raw byte, not an FName ---")
    if "error" in r:
        print("  refused:", r["error"]); return False
    print(f"  shift            {r['shift']}")
    print(f"  with station     {r['with_station']}  (true {sum(1 for x in truth if x)})")
    print(f"  typed StopPoint  {r['typed_stop_point']}  (true 0 - not an FName here)")
    print(f"  verdict          {r['verdict'][:110]}")
    ok = True
    if r["typed_stop_point"] > 3:
        print("  FAIL: invented FName types that are not in the file"); ok = False
    if abs(r["with_station"] - sum(1 for x in truth if x)) > 3:
        print("  FAIL: station recall off"); ok = False
    return ok


def run_impossible_shift_control():
    """A shift that puts an FName index outside the name table is IMPOSSIBLE,
    not merely unlikely. On the real Leven Branch layer - 88 names - three
    impossible shifts (8743, 1844, 1843) tied for first place on every
    statistical measure, and the only plausible candidate came fifth by
    0.0002. This checks that such shifts are now excluded outright."""
    base = "/tmp/synth/FCE_Timetable_TT_Leven_Layer_DataTrack"
    names, stations, types = pak_tools._name_table(base + ".uasset")
    r = pak_tools.find_stop_points(base + ".uexp")
    print("\n--- control: impossible shifts must be excluded ---")
    hi = max(list(types.values()) + list(stations.keys()))
    ok = True
    for cand in r["shift_scores"]:
        idx = hi + cand["shift"]
        if not (0 <= idx < len(names)):
            print(f"  FAIL: shift {cand['shift']} resolves index {idx} in a "
                  f"{len(names)}-entry table"); ok = False
    print(f"  all {len(r['shift_scores'])} shortlisted shifts resolve in range "
          f"(table has {len(names)} names)")
    print(f"  tied_shifts      {r['tied_shifts']}")
    if len(r["tied_shifts"]) > 1 and r["confirmed"]:
        print("  FAIL: confirmed despite an unresolved tie"); ok = False
    return ok


if __name__ == "__main__":
    results = [run_positive(), run_random_control(), run_raw_enum_control(),
               run_impossible_shift_control()]
    print("\n" + ("ALL PASS" if all(results) else "FAILURES PRESENT"))
    sys.exit(0 if all(results) else 1)
