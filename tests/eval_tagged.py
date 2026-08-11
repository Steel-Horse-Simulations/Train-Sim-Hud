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


def run_template_recovery():
    """Layout recovery by repetition must find the real field order, and must
    NOT be fooled by a tiny name table where most 'FName references' are
    coincidences. On the real Leven layer 29% of all byte offsets read as a
    valid FName.

    Two anchors are checked, because they answer different questions:
      - the DEFAULT anchor should be a genuine record boundary, and the
        template then holds the fields present on EVERY record;
      - anchoring explicitly on StopPoint should surface the fields carried
        only by stops. Those appear on 48 of 220 records, so they correctly
        fall below the share threshold under the default anchor - that is
        the template working, not failing.
    """
    print("\n--- record template recovered by repetition ---")
    base, truth = T.main("/tmp/eval_tmpl")
    ok = True

    auto = pak_tools.record_template(base + ".uexp")
    if "error" in auto:
        print("  FAIL:", auto["error"]); return False
    fields = [n for _rel, n in auto["stable_fields"]]
    print(f"  default anchor   {auto['anchor']} ({auto['record_count']} records)")
    print(f"  every-record fields {fields[:5]}")
    if "DataType" not in fields:
        print("  FAIL: missed DataType, which is on every record"); ok = False
    if auto["record_count"] != len(truth):
        print(f"  FAIL: record count {auto['record_count']} != {len(truth)}"); ok = False

    stops = pak_tools.record_template(
        base + ".uexp", anchor="ETimetableTrackDataType::StopPoint")
    sfields = [n for _rel, n in stops["stable_fields"]]
    print(f"  anchored on StopPoint: {sfields[:6]}")
    for want in ("InstructionIndex", "Distance", "NetworkRibbonLocation"):
        if want not in sfields:
            print(f"  FAIL: missed {want} on stop records"); ok = False
    try:
        if not (sfields.index("InstructionIndex") < sfields.index("Distance")
                < sfields.index("NetworkRibbonLocation")):
            print("  FAIL: field order wrong"); ok = False
        else:
            print("  stop-record field order matches the fixture")
    except ValueError:
        ok = False

    # noise control: random bytes must not yield a confident template
    d = "/tmp/eval_tmpl_noise"
    os.makedirs(d, exist_ok=True)
    nb = os.path.join(d, "Noise_DataTrack")
    T.build_uasset(nb + ".uasset")
    rng = random.Random(3)
    with open(nb + ".uexp", "wb") as f:
        f.write(bytes(rng.randrange(256) for _ in range(300_000)))
    n = pak_tools.record_template(nb + ".uexp")
    stable = [] if "error" in n else n.get("stable_fields", [])
    print(f"  noise control    {'refused' if 'error' in n else str(len(stable)) + ' stable fields'}")
    if len(stable) > 3:
        print("  FAIL: invented a template from random bytes"); ok = False
    return ok


def run_fixed_stride():
    """Fixed-stride decoding, as the real Leven layer appears to be (707-byte
    records, 12,207 of them). The decoded type distribution must match ground
    truth, and a WRONG stride must be rejected - the documented 2828-byte
    failure held alignment for twenty records before drifting, so 'it looked
    right at first' is not evidence."""
    from collections import Counter
    print("\n--- fixed-stride records ---")
    base, truth, stride, type_at, time_at = T.main_fixed("/tmp/eval_fixed")
    r = pak_tools.decode_fixed_records(base + ".uexp")
    ok = True
    if "error" in r:
        print("  FAIL:", r["error"]); return False
    print(f"  anchor {r['anchor']}  stride {r['stride']}  type offset +{r['type_field_offset']}")
    print(f"  coverage {r['coverage']}  confirmed {r['confirmed']}")
    if r["stride"] != stride:
        print(f"  FAIL: stride {r['stride']} != {stride}"); ok = False
    if r["type_field_offset"] != type_at:
        print(f"  FAIL: type offset {r['type_field_offset']} != {type_at}"); ok = False
    if r["type_distribution"] != dict(Counter(truth).most_common()):
        print("  FAIL: distribution does not match ground truth"); ok = False
    else:
        print("  decoded distribution matches ground truth exactly")
    if not r["confirmed"]:
        print("  FAIL: refused a correct decode"); ok = False

    # A wrong stride must not be confirmed.
    bad = pak_tools.decode_fixed_records(base + ".uexp", stride=stride + 1)
    print(f"  wrong stride ({stride + 1}): confirmed={bad.get('confirmed')}")
    if bad.get("confirmed"):
        print("  FAIL: confirmed a wrong stride"); ok = False
    return ok


def run_anchor_impostor():
    """The anchor must be the record BOUNDARY, not whichever name is
    referenced most.

    Reproduces the real failure: on the Leven layer `EnumProperty` had 61,036
    references against `Class`'s 12,207, and count-based scoring picked it -
    giving a stride of 196 instead of 707 and framing every downstream
    measurement against a record boundary that does not exist. The fixture
    writes a name five times per record to recreate exactly that.
    """
    from collections import Counter
    print("\n--- anchor selection with a high-count impostor ---")
    base, truth, stride, type_at, time_at = T.main_fixed("/tmp/eval_anchor")
    ok = True

    tpl = pak_tools.record_template(base + ".uexp")
    dec = pak_tools.decode_fixed_records(base + ".uexp")
    print(f"  template anchor {tpl['anchor']}  stride {tpl['record_size_median']}")
    print(f"  decode   anchor {dec['anchor']}  stride {dec['stride']}  type +{dec['type_field_offset']}")

    if tpl["anchor"] == "EnumProperty" or dec["anchor"] == "EnumProperty":
        print("  FAIL: picked the five-per-record impostor"); ok = False
    if tpl["anchor"] != dec["anchor"]:
        print("  FAIL: the two functions disagree on the record boundary"); ok = False
    for label, got in (("template", tpl["record_size_median"]), ("decode", dec["stride"])):
        if got != stride:
            print(f"  FAIL: {label} stride {got} != {stride}"); ok = False
    if dec["type_field_offset"] != type_at:
        print(f"  FAIL: type offset {dec['type_field_offset']} != {type_at}"); ok = False
    if dec["type_distribution"] != dict(Counter(truth).most_common()):
        print("  FAIL: type distribution does not match ground truth"); ok = False
    else:
        print("  type distribution matches ground truth exactly")
    if not dec["confirmed"]:
        print("  FAIL: refused a correct decode"); ok = False
    return ok


def run_timetable_extraction():
    """End-to-end extraction on a fixture built to the CONFIRMED real layout:
    707-byte records, type at +270, time at +200, plus the decoys the real
    file carries - a tick-sized value at +139 with its one-byte-shifted twin
    at +140, and a near-constant duration at +233.

    The decoys are the point. All of them pass a plain "is this a tick count
    inside a day" test, so anything choosing the time field on that basis
    alone picks wrong; only ascending-within-a-service separates them.
    """
    print("\n--- timetable extraction (real layout + real decoys) ---")
    base, truth, stride, type_at, time_at = T.main_leven("/tmp/eval_leven")
    r = pak_tools.extract_timetable(base + ".uexp")
    if "error" in r:
        print("  FAIL:", r["error"]); return False
    ok = True
    true_records = sum(s["points"] for s in truth)
    true_stops = sum(s["stops"] for s in truth)
    print(f"  records    {r['record_count']} (true {true_records})")
    print(f"  StopPoints {r['type_counts'].get('StopPoint')} (true {true_stops})")
    print(f"  services   {r['service_count']} (true {len(truth)})")
    print(f"  time field +{r['time_field_offset']}, chosen from "
          f"{[(c['offset'], c['rising']) for c in r['time_field_candidates'][:3]]}")

    if r["stride"] != stride:
        print(f"  FAIL: stride {r['stride']} != {stride}"); ok = False
    if r["record_count"] != true_records:
        print("  FAIL: record count - stray anchor hits not filtered"); ok = False
    if r["type_counts"].get("StopPoint") != true_stops:
        print("  FAIL: StopPoint count"); ok = False

    # The chosen time field must be the ascending one, not a decoy. Offsets
    # are relative to the ANCHOR, which sits inside the record, so compare
    # the gap between the two fields rather than absolute positions.
    gap = r["type_field_offset"] - r["time_field_offset"]
    if gap != (type_at - time_at):
        print(f"  FAIL: type/time gap {gap} != {type_at - time_at} - a decoy was chosen")
        ok = False
    else:
        print(f"  type/time gap {gap} matches the fixture - real time field chosen")
    if r["time_field_candidates"][0]["rising"] < 0.9:
        print("  FAIL: winning field does not ascend"); ok = False

    # Segmentation is a heuristic and known to merge closely-spaced services,
    # so this is a sanity band, not an equality check.
    if not (len(truth) * 0.5 <= r["service_count"] <= len(truth) * 1.5):
        print(f"  FAIL: service count wildly off"); ok = False
    if not (20 <= (r["median_duration_min"] or 0) <= 180):
        print(f"  FAIL: implausible median duration {r['median_duration_min']}"); ok = False
    return ok


def run_phase_shift_recovery():
    """A gap that is not a whole stride must cost ONE record, not the file.

    This is the bug that made the real Leven run report 91 records instead of
    12,207 while still looking confident: the chain required alignment to a
    global grid, 0.29% of real gaps are not 707, and the first one put
    everything after it permanently off-grid. Extraction stopped 0.62% into
    the file.
    """
    import shutil
    print("\n--- record chain survives a phase shift ---")
    base, truth, stride, type_at, time_at = T.main_leven("/tmp/eval_shift")
    true_records = sum(s["points"] for s in truth)
    true_stops = sum(s["stops"] for s in truth)
    ok = True

    clean = pak_tools.extract_timetable(base + ".uexp")
    print(f"  clean    records {clean['record_count']}/{true_records}  "
          f"stops {clean['type_counts'].get('StopPoint')}/{true_stops}")
    if clean["record_count"] != true_records:
        print("  FAIL: clean record count"); ok = False
    if clean["type_counts"].get("StopPoint") != true_stops:
        print("  FAIL: clean StopPoint count"); ok = False

    raw = bytearray(open(base + ".uexp", "rb").read())
    cut = len(raw) // 2
    raw[cut:cut] = b"\x00" * 251          # knock everything after off the grid
    shifted = "/tmp/eval_shift/shifted"
    with open(shifted + ".uexp", "wb") as f:
        f.write(bytes(raw))
    shutil.copy(base + ".uasset", shifted + ".uasset")

    after = pak_tools.extract_timetable(shifted + ".uexp")
    print(f"  shifted  records {after['record_count']}/{true_records}  "
          f"stops {after['type_counts'].get('StopPoint')}/{true_stops}")
    if after["record_count"] < true_records * 0.95:
        print("  FAIL: chain did not recover - the whole-file bug is back"); ok = False
    else:
        print("  recovered - a bad gap costs one record, not the remainder")
    return ok


def run_service_field():
    """Segmentation must be STRUCTURAL where a service identifier exists.

    The clock cannot settle it on the real file: the largest gap between
    consecutive stops is 396s, so a 600s threshold never fires, and the
    answer swings between 36 services (clock) and 104 (statistical). A field
    holding one value per service decides it. The fixture also plants a
    two-value flag that must NOT be mistaken for an identifier.
    """
    print("\n--- structural service segmentation ---")
    base, truth, stride, type_at, time_at = T.main_leven("/tmp/eval_svc")
    ok = True
    f = pak_tools.find_service_field(base + ".uexp")
    if "error" in f or not f.get("best"):
        print("  FAIL: no service field found"); return False
    b = f["best"]
    print(f"  field at +{b['offset']}: {b['runs']} runs, {b['distinct']} distinct "
          f"(true services {len(truth)})")
    if b["runs"] != len(truth):
        print("  FAIL: run count != service count"); ok = False
    if b["distinct"] != len(truth):
        print("  FAIL: distinct values != service count"); ok = False

    r = pak_tools.extract_timetable(base + ".uexp")
    print(f"  extract: {r['service_count']} services via {r['segmented_by']}")
    if r["service_count"] != len(truth):
        print("  FAIL: extraction did not use it"); ok = False
    if "service id" not in r["segmented_by"]:
        print("  FAIL: fell back to the clock despite a field being present"); ok = False
    if r["type_counts"].get("StopPoint") != sum(s["stops"] for s in truth):
        print("  FAIL: StopPoint count changed"); ok = False
    return ok


def run_fife_shape():
    """The real Leven timetable, as counted in the game: 39 services - 37
    calling at 13 stations, 2 Glenrothes workings calling at only 2 - with
    ~11 StopPoint RECORDS per station call and no service identifier
    anywhere in the record.

    The record-to-call ratio is the point. 5,198 StopPoint records against
    485 real calls is 10.7 records per call, so a StopPoint count is not a
    stop list and must not be reported as one. The two short workings are
    the sharpest check available: ~20 records against ~140, a 7:1 gap that
    a correct segmentation cannot miss and a wrong one cannot fake.
    """
    print("\n--- real Fife Circle shape: 39 services, 37x13 + 2x2 calls ---")
    base, truth, stride, type_at, time_at = T.main_fife("/tmp/eval_fife")
    ok = True

    # There is no service field in this file, and the search must say so
    # rather than inventing one.
    f = pak_tools.find_service_field(base + ".uexp", expected_runs=39)
    print(f"  service field: {f['best']}   (correctly absent)")
    if f["best"] is not None:
        print("  FAIL: invented a service field where none exists"); ok = False

    r = pak_tools.extract_timetable(base + ".uexp", expected_services=39)
    calls = sorted(s["call_count"] for s in r["services"])
    print(f"  services {r['service_count']} (true 39) via {r['segmented_by']}")
    print(f"  call counts: {calls[:4]} ... {calls[-3:]}, median {r['median_calls']}")
    if r["service_count"] != 39:
        print("  FAIL: service count"); ok = False
    if r["median_calls"] != 13:
        print(f"  FAIL: median calls {r['median_calls']} != 13"); ok = False
    if sorted(c for c in calls if c <= 4) != [2, 2]:
        print("  FAIL: the two Glenrothes workings were not recovered"); ok = False
    else:
        print("  both 2-call Glenrothes workings recovered")

    # every call must carry an arrival and a departure
    svc = next(s for s in r["services"] if s["call_count"] == 13)
    if not all(c["arrival"] and c["departure"] for c in svc["calls"]):
        print("  FAIL: a call is missing arrival or departure"); ok = False
    return ok


def run_near_miss_rejection():
    """A field with NEARLY the right number of runs must be rejected, not
    used.

    On the real Leven layer, with 39 services known from the game, a 10%
    tolerance accepted a field at +407 with 37 runs and preferred it to the
    known count. That field was wrong in both directions at once: it split 11
    services three records early - leaving 3-record fragments with no stops -
    and failed to split 5 boundaries at all, giving blocks of ~1,070 points
    where a service is ~355. Being close on the COUNT says nothing about the
    BOUNDARIES.
    """
    print("\n--- a nearly-right service field must be rejected ---")
    base, truth, _s, _t, _tm = T.main_leven("/tmp/eval_near")
    ok = True

    exact = pak_tools.extract_timetable(base + ".uexp", expected_services=40)
    print(f"  told 40, field has 40: {exact['service_count']} via {exact['segmented_by']}")
    if "service id" not in exact["segmented_by"] or exact["service_count"] != 40:
        print("  FAIL: an exactly-matching field should be used"); ok = False

    near = pak_tools.extract_timetable(base + ".uexp", expected_services=45)
    print(f"  told 45, field has 40: {near['service_count']} via {near['segmented_by']}")
    if "service id" in near["segmented_by"]:
        print("  FAIL: used a field whose run count does not match"); ok = False
    if near["service_count"] != 45:
        print("  FAIL: did not fall back to the known count"); ok = False
    return ok


def run_call_gap_tuning():
    """The call clustering gap is TUNED to a known call count, not guessed.

    It cannot be picked from first principles: records within one call sit
    ~8s apart and the run to the next station is 2-6 minutes, but some
    station pairs are far closer than others, so any fixed value
    under-splits somewhere. The real run with a fixed 90s gave a median of
    11 calls against the 13 counted in the game.

    Also checks the minimum-service-size rule: without it the boundary
    ranking spent 3 of its 38 cuts on fragments of 35-50 records, and
    correspondingly missed 3 real boundaries.
    """
    print("\n--- call gap tuned to a known call count ---")
    base, truth, _s, _t, _tm = T.main_fife("/tmp/eval_tune")
    ok = True
    r = pak_tools.extract_timetable(base + ".uexp", expected_services=39,
                                    expected_calls=13)
    calls = sorted(s["call_count"] for s in r["services"])
    print(f"  chosen gap {r['call_gap_seconds']}s, median calls {r['median_calls']} (true 13)")
    print(f"  call counts {calls[:4]} ... {calls[-3:]}")
    if r["median_calls"] != 13:
        print("  FAIL: tuning did not reach the known call count"); ok = False
    if sorted(c for c in calls if c <= 4) != [2, 2]:
        print("  FAIL: the two short workings were lost"); ok = False
    if min(s["track_points"] for s in r["services"]) < 20:
        print("  FAIL: a fragment survived the minimum-size rule"); ok = False

    # The chosen gap must sit on a plateau, not a spike - a spike would mean
    # the answer was fitted rather than found.
    band = [g for g, m in (r.get("call_gap_curve") or []) if m == 13]
    print(f"  gaps giving median 13: {len(band)} values")
    if len(band) < 4:
        print("  FAIL: the answer is a sharp spike, not a stable plateau"); ok = False
    return ok


def run_call_field():
    """A station-call identifier must be found when present and refused when
    absent.

    This exists because time clustering does NOT work on the real file. The
    tuning curve there slides smoothly from 45 calls to 1 with no flat step,
    and a continuum means the times carry no grouping - the 13 it produced
    was set by the threshold, not discovered. The plateau check caught that,
    which is the whole reason the curve is returned.
    """
    print("\n--- station call identifier ---")
    expected = 37 * 13 + 2 * 2          # 485, from the in-game timetable
    base, truth, _s, _t, _tm = T.main_fife("/tmp/eval_callf")
    ok = True
    r = pak_tools.find_call_field(base + ".uexp", expected_calls=expected)
    b = r.get("best")
    print(f"  with a call field planted: {b['offset'] if b else None} -> "
          f"{b['runs'] if b else '-'} runs (expected {expected})")
    if not b or b["runs"] != expected:
        print("  FAIL: did not find the planted call field"); ok = False
    elif b["distinct"] != expected:
        print("  FAIL: runs and distinct values disagree"); ok = False

    # and it must not invent one when the grouping is absent
    r2 = pak_tools.find_call_field(base + ".uexp", expected_calls=expected * 3)
    print(f"  asked for {expected * 3}: {r2.get('best')}")
    if r2.get("best") is not None:
        print("  FAIL: invented a field for a count that is not there"); ok = False
    return ok


def run_byte_field():
    """Single-byte fields must be found AS BYTES.

    Scanning int32 only was a real blind spot. On the real Leven layer the
    field at +695 is one byte taking the values 0,1,2,3,4,7,9,12,13,18,21,
    23,24,255 - but read as an int32 it appeared at four offsets with values
    like 16777216 and 402653184, every one a multiple of 2^24, and was never
    recognisable for what it was.
    """
    print("\n--- byte-width fields ---")
    base, truth, _s, _t, _tm = T.main_fife("/tmp/eval_byte")
    ok = True
    r = pak_tools.find_call_field(base + ".uexp")
    widths = {c["width"] for c in r["candidates"]}
    print(f"  widths scanned: {sorted(widths)}")
    if "u8" not in widths:
        print("  FAIL: byte fields not scanned"); ok = False

    # read the planted byte field directly and check the values are small
    ins = pak_tools.inspect_field(base + ".uexp", 480, field_width="u8",
                                  expected_services=39)
    vals = [v for v, _n in ins["value_frequency"]]
    print(f"  byte field values: {sorted(vals)[:8]}... max {max(vals)}")
    if max(vals) > 255:
        print("  FAIL: byte read did not stay in range"); ok = False
    if any(v % (1 << 24) == 0 and v for v in vals):
        print("  FAIL: values look like shifted int32 reads"); ok = False
    else:
        print("  values are plain small integers, not 2^24 multiples")
    return ok


if __name__ == "__main__":
    results = [run_with_guid(), run_without_guid(), run_wide_fnames(),
               run_opaque_control(), run_probe(), run_window_diagnostic(),
               run_template_recovery(), run_fixed_stride(),
               run_anchor_impostor(), run_timetable_extraction(),
               run_phase_shift_recovery(), run_service_field(),
               run_fife_shape(), run_near_miss_rejection(),
               run_call_gap_tuning(), run_call_field(), run_byte_field()]
    print("\n" + ("ALL PASS" if all(results) else "FAILURES PRESENT"))
    sys.exit(0 if all(results) else 1)
