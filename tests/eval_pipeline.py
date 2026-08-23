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


def run_timetable_hud(m):
    """The Timetable HUD: match the live service, mark progress, disambiguate.

    A headcode is NOT unique - TSW splits a working across a player leg and
    an AI continuation that share one, and 221 of 429 headcodes on the Fife
    Circle file appear more than once. So the lookup has to pick using the
    clock, and prefer the leg actually being driven.
    """
    import datetime
    print("\n--- timetable HUD ---")
    ok = True
    now = datetime.datetime.now()
    base = now.hour * 3600 + now.minute * 60

    def hms(x):
        x %= 86400
        return f"{x // 3600:02d}:{(x % 3600) // 60:02d}:{x % 60:02d}"

    stops, t = [], base - 1800
    for i, st in enumerate(["Leven", "Cameron Bridge", "Kirkcaldy",
                            "Haymarket", "Edinburgh Waverly"]):
        arr = None if i == 0 else hms(t)
        t += 60
        dep = None if i == 4 else hms(t)
        t += 600
        stops.append({"station": st, "platform": str(i + 1),
                      "arrival": arr, "departure": dep})

    m.timetable_db.save_definition_timetable("HudRoute", "H.uasset", [
        {"headcode": "2K85", "name": "P2K85", "role": "player_leg",
         "first_time": stops[0]["departure"], "last_time": stops[-1]["arrival"],
         "stops": stops},
        # same headcode, an AI leg at a different time - must NOT be chosen
        {"headcode": "2K85", "name": "2K85_B", "role": "ai_continuation",
         "first_time": "03:00:00", "last_time": "03:30:00",
         "stops": [{"station": "Depot", "arrival": "03:00:00",
                    "departure": "03:30:00"}]},
    ])

    c = m.app.test_client()
    d = c.get("/api/timetable/live?service=2K85").get_json()
    print(f"  picked {d.get('service_name')} (role {d.get('role')}), "
          f"{d.get('call_count')} stops, next index {d.get('next_index')}")
    if not d.get("found"):
        print("  FAIL: did not find the service"); return False
    if d["service_name"] != "P2K85":
        print("  FAIL: chose the AI leg over the player leg"); ok = False
    if d["alternatives"] != 1:
        print("  FAIL: the duplicate headcode was not reported"); ok = False

    calls = d["calls"]
    passed = [x for x in calls if x["passed"]]
    print(f"  {len(passed)} passed, next is {calls[d['next_index']]['station_name']}")
    if not passed:
        print("  FAIL: nothing marked passed on a service already running"); ok = False
    if d["next_index"] is None or calls[d["next_index"]]["passed"]:
        print("  FAIL: next stop is wrong"); ok = False
    # progress must be monotonic - no passed stop after an unpassed one
    seen_unpassed = False
    for x in calls:
        if not x["passed"]:
            seen_unpassed = True
        elif seen_unpassed:
            print("  FAIL: a passed stop follows an unpassed one"); ok = False
            break

    # NO HEADCODE means NO SERVICE. Searching without one matched whichever
    # service sat nearest the clock, so the HUD showed an unrelated working
    # with the game shut, and swapped away from the real one whenever a poll
    # dropped mid-journey.
    blind = c.get("/api/timetable/live").get_json()
    print(f"  with no service name: found={blind.get('found')} "
          f"({blind.get('live_error')})")
    if blind.get("found"):
        print("  FAIL: guessed a service with nothing to identify it"); ok = False

    # A supplied headcode must match exactly or return nothing - never fall
    # back to a near-time match on some other service.
    wrong = c.get("/api/timetable/live?service=9Z99").get_json()
    if wrong.get("found"):
        print("  FAIL: an unknown headcode matched a different service"); ok = False

    # No station may appear twice in a row: an unpaired LoadUnload is either
    # the origin (unnamed) or extra work at the stop just made.
    for svc_name in ("P2K85",):
        v = c.get("/api/timetable/live?name=" + svc_name).get_json()
        names = [x["station_name"] for x in (v.get("calls") or [])]
        if any(a == b for a, b in zip(names, names[1:])):
            print(f"  FAIL: {svc_name} repeats a station: {names}"); ok = False
    print("  no consecutive repeated stations")

    # THE HEADCODE MUST FOLLOW THE GAME. PlayerInfo drops often - the
    # journey reader's own notes say it "only ever returned a dropped
    # connection during scanning" - and a single failure used to leave the
    # HUD showing one service for an entire journey in another.
    state = {"mode": "drop"}
    real_get = m.api_get

    def fake_get(path, timeout=None, retries=1, use_cache=True):
        if "PlayerInfo" in path:
            if state["mode"] == "drop":
                return {"error": "connection_failed"}, 502
            return {"Values": {"currentServiceName": state["mode"]}}, 200
        return {}, 502

    m.api_get = fake_get
    m._live_headcode.__globals__["api_get"] = fake_get
    try:
        m._LAST_HEADCODE.update({"code": None, "at": 0.0})
        if c.get("/api/timetable/live").get_json().get("found"):
            print("  FAIL: found a service with the game down"); ok = False

        state["mode"] = "2K85"
        first = c.get("/api/timetable/live").get_json()
        print(f"  live headcode: {first.get('headcode')}")
        if first.get("headcode") != "2K85":
            print("  FAIL: did not read the live headcode"); ok = False

        state["mode"] = "drop"
        held = c.get("/api/timetable/live").get_json()
        if held.get("headcode") != "2K85":
            print("  FAIL: a single dropped poll lost the service"); ok = False
        else:
            print("  one dropped poll: service held")

        # The live value is FREE TEXT. Stored codes come from service names
        # ("P2K24", "1L86_B") so they are the bare four characters, and a
        # live "P2K24" or "2k24 " must still find them. A headcode is always
        # digit-letter-digit-digit.
        for spelling in ("2K85", "P2K85", " 2k85 ", "2K85_End"):
            state["mode"] = spelling
            m._LAST_HEADCODE.update({"code": None, "at": 0.0})
            v = c.get("/api/timetable/live").get_json()
            if not v.get("found"):
                print(f"  FAIL: live headcode {spelling!r} did not match the "
                      "stored service"); ok = False
        print("  live headcode matches in every spelling tried")

        # An unmatched code must say what it looked for AND what is stored -
        # "nothing is showing" cannot be acted on.
        state["mode"] = "9Z99"
        m._LAST_HEADCODE.update({"code": None, "at": 0.0})
        miss = c.get("/api/timetable/live").get_json()
        if miss.get("found"):
            print("  FAIL: matched a service that is not stored"); ok = False
        if not (miss.get("stored_headcodes") or {}).get("sample"):
            print("  FAIL: a mismatch does not report what IS stored"); ok = False
        else:
            print("  a mismatch reports the codes that are stored")

        # THE TIMETABLE IS IN GAME TIME. Judging progress against the
        # computer's clock marks stops passed that have not happened, and is
        # hours out whenever the service runs at a time of day that is not
        # the current one - which on a 24-hour timetable is most of them.
        booked = [
            {"station": "Leven", "arrival": None, "departure": "06:00:00"},
            {"station": "Cameron Bridge", "arrival": "06:15:00", "departure": "06:16:00"},
            {"station": "Kirkcaldy", "arrival": "06:30:00", "departure": "06:31:00"},
            {"station": "Haymarket", "arrival": "06:50:00", "departure": "06:51:00"},
            {"station": "Edinburgh Waverly", "arrival": "07:00:00", "departure": None},
        ]
        m.timetable_db.save_definition_timetable("GameClock", "G.uasset", [
            {"headcode": "3G01", "name": "P3G01", "role": "player_leg",
             "first_time": "06:00:00", "last_time": "07:00:00", "stops": booked}])
        game = {"iso": "2026-08-16T06:33:00"}
        real_clock = m._game_clock_seconds

        def fake_clock():
            t = game["iso"].split("T")[1].split(":")
            return int(t[0]) * 3600 + int(t[1]) * 60 + int(t[2])

        m._game_clock_seconds = fake_clock
        m._clock_seconds.__globals__["_game_clock_seconds"] = fake_clock
        try:
            state["mode"] = "3G01"
            m._LAST_HEADCODE.update({"code": None, "at": 0.0})
            g = c.get("/api/timetable/live").get_json()
            print(f"  game clock {g.get('clock')} ({g.get('clock_source')}), "
                  f"next {g['calls'][g['next_index']]['station_name']}")
            if g.get("clock_source") != "game":
                print("  FAIL: used the device clock, not the game's"); ok = False
            if g["calls"][g["next_index"]]["station_name"] != "Haymarket":
                print("  FAIL: progress not judged against game time"); ok = False
            # and it must MOVE with the game, not with real time
            game["iso"] = "2026-08-16T06:52:00"
            g2 = c.get("/api/timetable/live").get_json()
            if g2["calls"][g2["next_index"]]["station_name"] != "Edinburgh Waverly":
                print("  FAIL: progress did not follow the game clock forward")
                ok = False
            else:
                print("  progress follows the game clock")
        finally:
            m._game_clock_seconds = real_clock
            m._clock_seconds.__globals__["_game_clock_seconds"] = real_clock

        # PASSED means "no longer the next stop", not "booked time has gone".
        # Judging both from the clock dimmed the stop being served the
        # instant its booked departure ticked by - and when running late it
        # greyed out stops the train had not reached at all.
        late_stops = [
            {"station": "Leven", "arrival": None, "departure": "06:00:00"},
            {"station": "Cameron Bridge", "arrival": "06:15:00", "departure": "06:16:00"},
            {"station": "Kirkcaldy", "arrival": "06:30:00", "departure": "06:31:00"},
            {"station": "Haymarket", "arrival": "06:50:00", "departure": "06:51:00"},
        ]
        m.timetable_db.save_definition_timetable("LateRoute", "L.uasset", [
            {"headcode": "4L01", "name": "P4L01", "role": "player_leg",
             "first_time": "06:00:00", "last_time": "06:50:00",
             "stops": late_stops}])
        gclock = {"s": 6 * 3600 + 30 * 60 + 10}
        ahead = {"names": ["Kirkcaldy", "Haymarket"]}
        real_gc, real_ls = m._game_clock_seconds, m._live_station_names
        m._game_clock_seconds = lambda: gclock["s"]
        m._clock_seconds.__globals__["_game_clock_seconds"] = lambda: gclock["s"]
        m._live_station_names = lambda limit=12: ahead["names"]
        m.timetable_live.__globals__["_live_station_names"] = lambda limit=12: ahead["names"]
        try:
            state["mode"] = "4L01"
            m._LAST_HEADCODE.update({"code": None, "at": 0.0})

            # standing at Kirkcaldy, booked departure not yet reached
            v = c.get("/api/timetable/live").get_json()
            cur = v["calls"][v["next_index"]]["station_name"]
            if cur != "Kirkcaldy" or v["calls"][2]["passed"]:
                print(f"  FAIL: the current stop ({cur}) should not be passed")
                ok = False

            # still there, booked departure now BEHIND the clock
            gclock["s"] = 6 * 3600 + 55 * 60
            v = c.get("/api/timetable/live").get_json()
            cur = v["calls"][v["next_index"]]["station_name"]
            print(f"  25 min late, still at Kirkcaldy -> next {cur}")
            if cur != "Kirkcaldy":
                print("  FAIL: advanced past a stop the train had not left")
                ok = False
            if v["calls"][2]["passed"]:
                print("  FAIL: marked the current stop passed"); ok = False

            # once the game says the next station is Haymarket, it moves on
            ahead["names"] = ["Haymarket"]
            v = c.get("/api/timetable/live").get_json()
            if v["calls"][v["next_index"]]["station_name"] != "Haymarket":
                print("  FAIL: did not advance when the train left"); ok = False
            elif not v["calls"][2]["passed"]:
                print("  FAIL: the departed stop is not marked passed"); ok = False
            else:
                print("  advances only when the train actually leaves")
        finally:
            m._game_clock_seconds = real_gc
            m._clock_seconds.__globals__["_game_clock_seconds"] = real_gc
            m._live_station_names = real_ls
            m.timetable_live.__globals__["_live_station_names"] = real_ls

        # A HEADCODE IS NOT UNIQUE ACROSS ROUTES. 2K24 exists on the Fife
        # Circle AND the East Coast Main Line; searching every route showed
        # an ECML service to someone driving in Fife. The stations the game
        # says are ahead decide it.
        m.timetable_db.save_definition_timetable("ECML_test", "E.uasset", [
            {"headcode": "2K85", "name": "P2K85E", "role": "player_leg",
             "first_time": stops[0]["departure"], "last_time": stops[-1]["arrival"],
             "stops": [{"station": n, "arrival": stops[i]["arrival"],
                        "departure": stops[i]["departure"]}
                       for i, n in enumerate(["Doncaster", "Retford", "Newark",
                                              "Peterborough", "Stevenage"])]}])
        live = {"names": ["Kirkcaldy", "Haymarket"]}
        real_stations = m._live_station_names

        def fake_stations(limit=12):
            return live["names"]

        m._live_station_names = fake_stations
        m.timetable_live.__globals__["_live_station_names"] = fake_stations
        try:
            state["mode"] = "2K85"
            m._LAST_HEADCODE.update({"code": None, "at": 0.0})
            fife = c.get("/api/timetable/live").get_json()
            print(f"  stations ahead say Fife -> route {fife.get('route_key')}")
            if fife.get("route_key") == "ECML_test":
                print("  FAIL: picked the wrong route for the headcode"); ok = False

            live["names"] = ["Doncaster", "Retford"]
            m._LAST_HEADCODE.update({"code": None, "at": 0.0})
            ecml = c.get("/api/timetable/live").get_json()
            print(f"  stations ahead say ECML -> route {ecml.get('route_key')}")
            if ecml.get("route_key") != "ECML_test":
                print("  FAIL: did not follow the stations to the other route")
                ok = False
        finally:
            m._live_station_names = real_stations
            m.timetable_live.__globals__["_live_station_names"] = real_stations

        # The expiry check below needs the game to report NOTHING. The route
        # tests above leave it reporting a service, and without this the
        # expiry test reads "still found" and fails on a bug that is not
        # there.
        state["mode"] = "drop"

        # ...but the hold must EXPIRE rather than persist for the journey
        m._LAST_HEADCODE["at"] -= 1000
        expired = c.get("/api/timetable/live").get_json()
        if expired.get("found"):
            print("  FAIL: the held service never expires"); ok = False
        else:
            print("  hold expires rather than sticking")
    finally:
        m.api_get = real_get
        m._live_headcode.__globals__["api_get"] = real_get

    if c.get("/pages/timetable.html").status_code != 200:
        print("  FAIL: the HUD page is missing"); ok = False
    unknown = c.get("/api/timetable/live?service=9Z99").get_json()
    if unknown.get("found"):
        print("  FAIL: invented a service"); ok = False
    else:
        print("  unknown service reports not found, with a reason")
    svcs = c.get("/api/timetable/services").get_json()
    if not svcs.get("routes"):
        print("  FAIL: the picker has no routes"); ok = False
    return ok


def run_departure_board(m):
    """The on-foot departure board.

    Station detection is the hard part: the pak files give station NAMES and
    ribbon offsets but no coordinates, so the only place a station's position
    exists is `drive_sightings` - stations seen on a recorded drive. That is
    a real limit, and the board has to offer a manual choice rather than
    appear broken when it applies.
    """
    print("\n--- departure board (on foot) ---")
    ok = True

    def svc(hc, name, times, dest):
        return {"headcode": hc, "name": name, "role": "player_leg",
                "first_time": times[0], "last_time": times[3],
                "stops": [
                    {"station": "Leven", "arrival": None, "departure": times[0]},
                    {"station": "Kirkcaldy", "arrival": times[1],
                     "departure": times[2], "platform": "2"},
                    {"station": dest, "arrival": times[3], "departure": None}]}

    m.timetable_db.save_definition_timetable("BoardRoute", "B.uasset", [
        svc("2B01", "P2B01", ["06:00:00", "06:30:00", "06:31:00", "07:00:00"],
            "Edinburgh Waverly"),
        svc("2B02", "P2B02", ["06:10:00", "06:42:00", "06:43:00", "07:15:00"],
            "Haymarket"),
        svc("2B03", "P2B03", ["23:00:00", "23:30:00", "23:31:00", "23:59:00"],
            "Leven"),
    ])
    m.timetable_db.save_drive_sightings("BoardRoute", [
        {"station_name": "Kirkcaldy", "times_seen": 5, "closest_distance_m": 8.0,
         "latitude": 56.1128, "longitude": -3.1580}])

    # the station is found from the player's position
    near = m.timetable_db.find_nearest_station(56.1129, -3.1581)
    print(f"  nearest station to the player: {near}")
    if not near or near["station_name"] != "Kirkcaldy":
        print("  FAIL: did not locate the station from the position"); ok = False
    # ...but only when actually near it
    if m.timetable_db.find_nearest_station(51.5, -0.12) is not None:
        print("  FAIL: matched a station hundreds of miles away"); ok = False

    # the board shows trains around the game clock, in time order
    # Scoped to this route: earlier tests in this file store services calling
    # at the same stations, and an unscoped board would mix them. That is
    # correct behaviour for a real station served by several routes, but it
    # makes the assertion below meaningless.
    board = m.timetable_db.departures_at("Kirkcaldy", 6 * 3600 + 35 * 60,
                                         route_key="BoardRoute")
    deps = board["departures"]
    print(f"  {len(deps)} trains: "
          + ", ".join(f"{d['departure'][:5]} {d['destination']}" for d in deps))
    if len(deps) != 2:
        print("  FAIL: wrong number of trains in the window"); ok = False
    if [d["seconds_away"] for d in deps] != sorted(d["seconds_away"] for d in deps):
        print("  FAIL: board is not in time order"); ok = False
    if deps and deps[0]["destination"] != "Edinburgh Waverly":
        print("  FAIL: destination is not the service's last call"); ok = False

    # a train hours away must not appear
    if any(d["headcode"] == "2B03" for d in deps):
        print("  FAIL: a train 16 hours away is on the board"); ok = False

    # an unknown station must not invent departures
    empty = m.timetable_db.departures_at("Nowhere", 6 * 3600)
    if empty.get("departures"):
        print("  FAIL: invented departures for an unknown station"); ok = False

    # and the picker must offer real stations
    names = [s["station_name"] for s in
             m.timetable_db.stations_with_departures("BoardRoute")]
    if "Kirkcaldy" not in names:
        print("  FAIL: the station picker is empty"); ok = False
    else:
        print(f"  picker offers {len(names)} stations")
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
               run_timetable_hud(m), run_departure_board(m),
               run_version_declared(m)]
    print("\n" + ("ALL PASS" if all(results) else "FAILURES PRESENT"))
    sys.exit(0 if all(results) else 1)


