"""
End-to-end checks for the three things that hold the extracted timetable
together: the Known Trains backup/restore round trip, banking the extracted
timetable to SQLite, and the drive recorder that will label it.

These are the failure modes that lose DATA rather than produce a wrong
number, so each is checked by round trip rather than by inspection.
"""
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)


def _app(tmp):
    """Points every module's DB_PATH at a temp copy.

    NOTE: os.chdir is NOT enough and gave a false failure. Each module sets
    DB_PATH absolutely from its own __file__, so changing directory made the
    test write to a relative data/ while the app read the real one - the
    restore looked like it had imported nothing. The app was correct; the
    harness was not.
    """
    import importlib.util
    os.chdir(tmp)
    spec = importlib.util.spec_from_file_location("tswapp", os.path.join(APP, "app.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["tswapp"] = m
    try:
        spec.loader.exec_module(m)
    except SystemExit:
        pass
    return m


def run_backup_restore(m):
    """A restore that imports NOTHING and reports success is worse than one
    that fails: the old /restore read body["classes"] while the backup file
    is {"tables": {...}}, so restoring a real backup silently did nothing."""
    print("\n--- known trains backup -> wipe -> restore ---")
    tc, c = m.train_classes_db, m.app.test_client()
    op = tc.create_operator("ScotRail", "SR")
    opid = op["id"] if isinstance(op, dict) else op
    tc.create_livery(opid, "Saltire", "saltire", colour="#1e467d")
    conn = sqlite3.connect("data/train_classes.db")
    for n, seen, vis in (("DRIVEN", 5, 1), ("CATALOG", 0, 1), ("HIDDEN", 2, 0)):
        conn.execute("INSERT INTO train_classes (source_name,display_name,is_visible,"
                     "times_seen,livery_id,livery_name,imported_at,updated_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (n, n, vis, seen, opid, "saltire", "x", "x"))
    conn.commit()
    tables = ("train_classes", "operators", "operator_liveries")
    before = {t: conn.execute(f"select count(*) from {t}").fetchone()[0] for t in tables}
    conn.close()

    d = json.loads(c.post("/api/known_trains/backup").data)
    ok = d["ok"]
    backup = json.load(open(d["json_path"]))
    conn = sqlite3.connect("data/train_classes.db")
    for t in ("train_classes", "operator_liveries", "operators"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()

    res = c.post("/api/known_trains/restore", json=backup).get_json()
    conn = sqlite3.connect("data/train_classes.db")
    after = {t: conn.execute(f"select count(*) from {t}").fetchone()[0] for t in tables}
    conn.close()
    print(f"  before {before}\n  after  {after}  via {res.get('format')}")
    if before != after:
        print("  FAIL: restore did not return the database to its prior state"); ok = False
    # a junk file must be refused loudly
    bad = c.post("/api/known_trains/restore", json={"nonsense": 1})
    print(f"  junk file -> {bad.status_code}")
    if bad.status_code != 400:
        print("  FAIL: junk accepted"); ok = False
    return ok


def run_timetable_banking(m):
    """The extraction is expensive and existed only as endpoint output."""
    print("\n--- extracted timetable saved to SQLite ---")
    services = [{"start_offset": 100 * i, "first": "05:00:00", "last": "06:00:00",
                 "duration_min": 60.0, "track_points": 300, "stop_count": 140,
                 "call_count": 13,
                 "calls": [{"arrival": f"05:{k:02d}:00", "departure": f"05:{k:02d}:30",
                            "records": 11} for k in range(13)]}
                for i in range(39)]
    r = m.timetable_db.save_extracted_timetable("FifeCircle", "FCE.uexp", services)
    got = m.timetable_db.list_extracted_timetable("FifeCircle")
    print(f"  saved {r['services_saved']} services, {r['calls_saved']} calls; "
          f"read back {got['service_count']}")
    ok = True
    if got["service_count"] != 39 or r["calls_saved"] != 39 * 13:
        print("  FAIL: counts"); ok = False
    if len(got["services"][0]["calls"]) != 13:
        print("  FAIL: calls did not round trip"); ok = False
    # re-extraction must REPLACE, not accumulate two parser versions
    m.timetable_db.save_extracted_timetable("FifeCircle", "FCE.uexp", services)
    again = m.timetable_db.list_extracted_timetable("FifeCircle")
    print(f"  after re-extract: {again['service_count']} services")
    if again["service_count"] != 39:
        print("  FAIL: re-extraction accumulated"); ok = False
    return ok


def run_drive_recorder(m):
    """The recorder bridges times-without-names to names-without-positions."""
    print("\n--- drive recorder ---")
    stations = ["Leven", "Cameron Bridge", "Kirkcaldy", "Edinburgh Waverly"]
    seq = []
    for step in range(40):
        vis = [{"station_name": s, "distance_to_station_cm": (i * 4000 - step * 450) * 100,
                "platform_length": 180}
               for i, s in enumerate(stations)
               if -200000 < (i * 4000 - step * 450) * 100 < 900000]
        seq.append({"service_name": "2K05", "latitude": 56.19 + step * 0.001,
                    "longitude": -3.0, "stations": vis, "markers": [], "errors": []})
    it = iter(seq)
    m.DRIVE_RECORDER = m.drive_recorder.DriveRecorder(journey_fn=lambda: next(it, seq[-1]))
    m.DRIVE_RECORDER.POLL_SECONDS = 0.02
    c = m.app.test_client()
    c.post("/api/drive/record", json={"action": "start", "route_key": "FifeCircle"})
    time.sleep(1.2)
    stop = c.post("/api/drive/record", json={"action": "stop"}).get_json()
    ok = True
    print(f"  {stop['stations_seen']} stations from {stop['polls']} polls")
    if stop["stations_seen"] != len(stations):
        print("  FAIL: missed stations"); ok = False
    # Closest approach must be near zero. The raw value goes NEGATIVE past
    # the station, so a plain min() keeps the point furthest beyond it.
    worst = max(abs(s["closest_distance_m"]) for s in stop["sightings"])
    print(f"  worst closest approach: {worst}m")
    if worst > 1000:
        print("  FAIL: closest approach not near zero - sign handling wrong"); ok = False

    m.timetable_db.save_station_names(
        "FifeCircle", {s: ["1"] for s in stations} | {"Markinch": ["1"]}, ["1E01"])
    match = c.get("/api/drive/match?route_key=FifeCircle").get_json()
    print(f"  matched {match['matched_count']} of {match['catalogue_places']} places; "
          f"unmatched driven: {match['unmatched_driven'] or 'none'}")
    if match["matched_count"] != len(stations) or match["unmatched_driven"]:
        print("  FAIL: driven names did not match the catalogue"); ok = False
    return ok


def run_routes(m):
    """The Routes page: scan lists what exists, extract reads it.

    The two are kept separate on purpose. A scan is cheap and gets re-run
    whenever DLC is installed; an extraction is not. Rescanning must
    therefore refresh what exists WITHOUT wiping the record of what has
    already been read.
    """
    import os, shutil, sys as _sys
    print("\n--- routes: scan, list, extract ---")
    ok = True
    scan = [
        {"pak_name": "TS2Prototype-WindowsNoEditor-FifeCircle.pak",
         "pak_path": "/x/FifeCircle.pak",
         "timetables": ["a/Content/Timetable/FCE_Timetable_TT.uasset"],
         "timetables_by_name": [], "layer_datatracks": [],
         "counts": {"datatrack": 3}},
        {"pak_name": "TS2Prototype-WindowsNoEditor-BRClass158.pak",
         "pak_path": "/x/158.pak",
         "timetables": ["b/Content/Timetable/158_FCL_Timetable.uasset"],
         "timetables_by_name": [], "layer_datatracks": [],
         "counts": {"datatrack": 1}},
        # a pak that failed to open must be skipped, not stored as a route
        {"pak_name": "broken.pak", "error": "repak_not_found"},
    ]
    r = m.timetable_db.save_scanned_routes(scan)
    listing = m.timetable_db.list_scanned_routes()
    print(f"  saved {r['added']} routes; listing {listing['route_count']}, "
          f"{listing['new_count']} new")
    if listing["route_count"] != 2:
        print("  FAIL: the failed pak became a route"); ok = False
    keys = {x["route_key"] for x in listing["routes"]}
    if keys != {"FifeCircle", "BRClass158"}:
        print(f"  FAIL: route keys {keys} - platform prefix not stripped"); ok = False

    # rescanning must not duplicate
    m.timetable_db.save_scanned_routes(scan)
    if m.timetable_db.list_scanned_routes()["route_count"] != 2:
        print("  FAIL: rescan duplicated routes"); ok = False
    else:
        print("  rescan does not duplicate")

    # extraction marks the route read, and a rescan must NOT undo that
    m.timetable_db.mark_route_extracted("FifeCircle", 7, 30)
    m.timetable_db.save_scanned_routes(scan)
    after = {x["route_key"]: x for x in
             m.timetable_db.list_scanned_routes()["routes"]}
    fc = after["FifeCircle"]
    print(f"  after re-scan: FifeCircle services={fc['services_extracted']}, "
          f"is_new={fc['is_new']}")
    if fc["is_new"] or fc["services_extracted"] != 7:
        print("  FAIL: rescanning wiped the extraction record"); ok = False
    if not after["BRClass158"]["is_new"]:
        print("  FAIL: an unread route is not marked new"); ok = False

    # A HALF-EXTRACTION must fail loudly. A cooked asset keeps its data in
    # the sibling .uexp; extracting only the .uasset parses cleanly and
    # yields nothing, which showed as "0 services - read" on every route and
    # hid a broken unpack behind a success.
    import synth_ttdef, pak_tools, timetable_definition
    base, _t, _r = synth_ttdef.build("/tmp/eval_routes_asset")
    only = os.path.join("/tmp/eval_routes_only")
    os.makedirs(only, exist_ok=True)
    shutil.copy(base + ".uasset", os.path.join(only, "X.uasset"))
    half = timetable_definition.parse_timetable_definition(os.path.join(only, "X.uasset"))
    print(f"  .uasset without .uexp -> {half.get('error')}")
    if half.get("error") != "uexp_missing":
        print("  FAIL: a missing .uexp must be reported, not parsed as empty")
        ok = False

    whole = timetable_definition.parse_timetable_definition(base + ".uasset")
    if not whole.get("named_stops"):
        print("  FAIL: the complete asset should parse"); ok = False
    else:
        print(f"  complete asset -> {whole['service_count']} services")

    # A failed route must be marked failed, never left looking merely unread.
    m.timetable_db.mark_route_failed("BRClass158", "uexp_missing")
    after = {x["route_key"]: x for x in
             m.timetable_db.list_scanned_routes()["routes"]}
    if after["BRClass158"]["status"] != "failed":
        print("  FAIL: failure not recorded"); ok = False

    # CANDIDATE RANKING. RivieraLine reported "no_services_parsed" because a
    # single guess picked the wrong asset - a ServiceMode or _TT-named file -
    # while the real timetable sat beside it. The index asset (directly in a
    # Timetable/ folder) must rank first, DataTracks last, and scenario or
    # training timetables must be excluded entirely.
    route = {
        "assets": ["P/Content/ServiceMode/SM_RVL.uasset",
                   "P/Content/Timetable/DataTracks/x_MasterDataTrack.uasset",
                   "P/Content/Timetable/RVL_Timetable_TT.uasset",
                   "P/Content/Timetable/Scenarios/S1.uasset"],
        "fallback_assets": ["P/Content/Other/RVL_TT.uasset"],
    }
    order = [os.path.basename(a) for a in m._timetable_candidates(route)]
    print(f"  candidate order: {order}")
    if order[0] != "RVL_Timetable_TT.uasset":
        print("  FAIL: the index asset must be tried first"); ok = False
    if any("S1" in a for a in order):
        print("  FAIL: a scenario timetable was offered"); ok = False
    if order[-1] != "RVL_TT.uasset":
        print("  FAIL: the weak _TT name match should rank last"); ok = False
    if "x_MasterDataTrack.uasset" not in order:
        print("  FAIL: DataTracks should remain as a last resort"); ok = False

    c = m.app.test_client()
    if c.get("/pages/routes.html").status_code != 200:
        print("  FAIL: routes page missing"); ok = False
    if c.post("/api/routes/extract", json={"route_key": "Nope"}).status_code != 404:
        print("  FAIL: unknown route should 404"); ok = False
    return ok


def run_version_declared(m):
    """APP_VERSION must match what the docs claim.

    Several releases shipped with a stale version: the bump was done by
    matching the previous literal string, and once that drifted the replace
    silently did nothing - so every later bump missed too, and the app
    reported 7.60.1 while the docs said 8.0.0. A no-op edit has to be an
    error, which is what tests/bump_version.py enforces.
    """
    import re, pathlib
    print("\n--- declared version ---")
    app_src = pathlib.Path(m.__file__ if hasattr(m, "__file__") else "app.py")
    src = pathlib.Path(APP, "app.py").read_text(encoding="utf-8")
    mv = re.search(r'^APP_VERSION = "([\d.]+)"', src, re.M)
    spec = pathlib.Path(APP, "docs", "TSW_HUD_NEW_CHAT_SPEC.txt").read_text(encoding="utf-8")
    sv = re.search(r"VERSION THIS SPEC DESCRIBES: ([\d.]+)", spec)
    print(f"  app.py {mv.group(1) if mv else '?'} | spec {sv.group(1) if sv else '?'}")
    if not mv or not sv:
        print("  FAIL: could not read a version"); return False
    if mv.group(1) != sv.group(1):
        print("  FAIL: app.py and the spec disagree - a version bump was missed")
        return False
    return True


if __name__ == "__main__":
    import tempfile, shutil
    tmp = tempfile.mkdtemp()
    for d in ("data", "pages"):
        shutil.copytree(os.path.join(APP, d), os.path.join(tmp, d), dirs_exist_ok=True)
    m = _app(tmp)
    # Redirect the databases AFTER import, so nothing touches the real ones.
    for mod, fname in ((m.train_classes_db, "train_classes.db"),
                       (m.timetable_db, "timetables.db"),
                       (m.loco_profiles, "loco_profiles.db")):
        mod.DB_PATH = os.path.join(tmp, "data", fname)
    m.train_classes_db.init_db()
    m.timetable_db.init_db()
    results = [run_backup_restore(m), run_timetable_banking(m),
               run_drive_recorder(m), run_routes(m),
               run_version_declared(m)]
    print("\n" + ("ALL PASS" if all(results) else "FAILURES PRESENT"))
    sys.exit(0 if all(results) else 1)


