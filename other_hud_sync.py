"""
other_hud_sync.py

Finds the other "TSW HUD" app's own database and image folder automatically
(same auto-detect-with-manual-override pattern as this app's own TSW install
detection), and keeps this app's imported timetable/train-class data fresh
without requiring the manual copy-the-db-file-then-run-a-script workflow
from v3.0.0.

Runs in a background thread on startup (see other_hud_sync_loop), so it
never blocks the app opening - same shape as the existing WEATHER_STATE
background thread in app.py. Re-checks periodically (not just once at
startup) since the other app may keep running and gain new data.

SAFETY: only ever opens the other app's database read-only (mode=ro via
tsw_timetable_importer.connect_readonly), even while that app is running -
SQLite's WAL mode is specifically designed to let readers work safely
alongside an active writer. This assumption is believed correct but NOT
YET CONFIRMED against a real concurrent-write scenario - see PROJECT_NOTES.md.

IMAGES FOLDER: guessed as a sibling of the db/ folder
(".../resources/images/train_classes/", since the db lives at
".../resources/db/tsw_hud.db") - NOT YET CONFIRMED, see PROJECT_NOTES.md.
If this guess is wrong, image sync will simply find nothing and skip
silently rather than erroring - confirm and adjust IMAGES_SUBPATH below
once verified.
"""
import json
import glob
import os
import shutil
import time
from datetime import datetime

import timetable_db
import train_classes_db
from tsw_timetable_importer import connect_readonly, find_timetables, bulk_fetch_stops, chain_segments, build_journey
from import_from_other_hud import determine_uk_source_ids

DB_SUBPATH = os.path.join("resources", "db", "tsw_hud.db")
IMAGES_SUBPATH = os.path.join("resources", "images", "train_classes")  # NOT YET CONFIRMED - see docstring

# How often to re-check for new data once the app is running, in addition to
# the check on startup. Not just a one-shot, since the other app may still
# be running and gain new data during this app's own session too.
RECHECK_INTERVAL_SECONDS = 15 * 60

THIS_APP_IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images", "train_classes")


def _candidate_roots():
    home = os.path.expanduser("~")
    return [
        os.path.join(home, "Downloads"),
        os.path.join(home, "Desktop"),
        os.path.join(home, "OneDrive", "Desktop"),
        os.path.join(home, "OneDrive", "Downloads"),
        os.path.join(home, "Documents"),
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("APPDATA", ""),
    ]


def auto_detect_other_hud_db(max_depth_seconds=8):
    """Scan common folders for the other app's database. Bounded by a rough
    time budget rather than a fixed depth, since folder trees vary wildly in
    size - a slow/huge Downloads folder shouldn't hang app startup."""
    start = time.time()
    for root in _candidate_roots():
        if not root or not os.path.isdir(root):
            continue
        pattern = os.path.join(root, "**", DB_SUBPATH)
        try:
            for match in glob.iglob(pattern, recursive=True):
                if os.path.isfile(match):
                    return match
                if time.time() - start > max_depth_seconds:
                    return None
        except Exception:
            continue
        if time.time() - start > max_depth_seconds:
            return None
    return None


def images_path_for_db(db_path):
    """Derive the guessed images folder from a known db path - both live
    under the same resources/ root."""
    if not db_path:
        return None
    resources_dir = os.path.dirname(os.path.dirname(db_path))  # .../resources/db/x.db -> .../resources
    guess = os.path.join(resources_dir, "images", "train_classes")
    return guess if os.path.isdir(guess) else None


def get_or_detect_db_path(config, save_config_fn):
    """config is the app's normal CONFIG dict (already loaded from
    configuration.json). Returns a usable path, or None if nothing is set
    and nothing could be auto-detected - never raises."""
    saved = config.get("other_hud_db_path", "")
    if saved and os.path.isfile(saved):
        return saved

    detected = auto_detect_other_hud_db()
    if detected:
        config["other_hud_db_path"] = detected
        config["other_hud_db_auto_detected"] = True
        save_config_fn(config)
        return detected

    return None


def sync_images(images_dir):
    """Copy any new/changed images from the other app's folder into this
    app's own images/train_classes/. Skips files that already exist with
    the same size and mtime, so this isn't a full re-copy every run.
    Silently does nothing if images_dir is None or doesn't exist - this is
    a nice-to-have, never a hard requirement."""
    if not images_dir or not os.path.isdir(images_dir):
        return 0
    os.makedirs(THIS_APP_IMAGES_DIR, exist_ok=True)
    copied = 0
    for name in os.listdir(images_dir):
        if not name.lower().endswith(".png"):
            continue
        src = os.path.join(images_dir, name)
        dst = os.path.join(THIS_APP_IMAGES_DIR, name)
        try:
            if os.path.exists(dst):
                s_src, s_dst = os.stat(src), os.stat(dst)
                if s_src.st_size == s_dst.st_size and int(s_src.st_mtime) <= int(s_dst.st_mtime):
                    continue
            shutil.copy2(src, dst)
            copied += 1
        except Exception:
            continue  # one bad file shouldn't stop the rest
    return copied


def sync_once(db_path, images_dir, config, save_config_fn, log_fn=print):
    """One full check-and-import pass. Cheap to call repeatedly - skips the
    expensive full import entirely if the source row count hasn't changed
    since last time."""
    if not db_path or not os.path.isfile(db_path):
        log_fn("other_hud_sync: no database path available, skipping")
        return

    try:
        conn = connect_readonly(db_path)
    except Exception as e:
        log_fn(f"other_hud_sync: could not open database ({e}), skipping")
        return

    try:
        current_count = conn.execute(
            "SELECT COUNT(*) FROM timetables WHERE source = 'Timetable'"
        ).fetchone()[0]
    except Exception as e:
        log_fn(f"other_hud_sync: could not query database ({e}), skipping")
        return

    last_count = config.get("last_import_row_count", -1)
    if current_count == last_count:
        log_fn(f"other_hud_sync: no change ({current_count} rows), skipping full import")
    else:
        log_fn(f"other_hud_sync: row count changed ({last_count} -> {current_count}), importing...")
        try:
            route_rows = conn.execute("SELECT id, name FROM routes").fetchall()
            route_names_by_id = {r["id"]: r["name"] for r in route_rows}

            records = find_timetables(conn)
            stops_by_id = bulk_fetch_stops(conn, [r.id for r in records])
            chains = chain_segments(records)

            timetable_db.clear_all()
            for chain in chains:
                journey = build_journey(chain, stops_by_id)
                segments = [
                    {"timetable_id": r.id, "service_name": r.service_name, "section_id": r.section_id,
                     "start_time": r.start_time, "duration": r.duration, "bound": r.bound,
                     "conductor_compatible": r.conductor_compatible, "playable": r.playable}
                    for r in chain
                ]
                stops = [
                    {"location_name": s.location_name, "arrival": s.time1, "departure": s.time2,
                     "latitude": s.latitude, "longitude": s.longitude, "source_timetable_id": s.source_timetable_id}
                    for s in journey.stops
                ]
                route_name = route_names_by_id.get(journey.route_id)
                timetable_db.import_journey(journey.route_id, route_name, journey.current_service_name, segments, stops)

            uk_ids = determine_uk_source_ids(conn)
            tc_rows = conn.execute("""
                SELECT id, name, livery_id, typical_length_m, typical_car_count, is_electric,
                       max_speed_kph, max_power_kw, manufacturer_name, engine_description,
                       type_description, vehicle_category, thumbnail_path, rail_vehicle_class,
                       is_drivable, powered_axle_count
                FROM train_classes
            """).fetchall()
            for r in tc_rows:
                train_classes_db.import_train_class(
                    source_id=r["id"], source_name=r["name"], is_uk=(r["id"] in uk_ids),
                    livery_id=r["livery_id"], typical_length_m=r["typical_length_m"],
                    typical_car_count=r["typical_car_count"], is_electric=r["is_electric"],
                    max_speed_kph=r["max_speed_kph"], max_power_kw=r["max_power_kw"],
                    manufacturer_name=r["manufacturer_name"], engine_description=r["engine_description"],
                    type_description=r["type_description"], vehicle_category=r["vehicle_category"],
                    thumbnail_path=r["thumbnail_path"], rail_vehicle_class=r["rail_vehicle_class"],
                    is_drivable=r["is_drivable"], powered_axle_count=r["powered_axle_count"],
                )

            config["last_import_row_count"] = current_count
            config["last_import_at"] = datetime.now().isoformat(timespec="seconds")
            save_config_fn(config)
            log_fn(f"other_hud_sync: imported {len(chains)} journeys, {len(tc_rows)} train classes")
        except Exception as e:
            log_fn(f"other_hud_sync: import failed ({e}), will retry next cycle")

    if images_dir is None:
        images_dir = images_path_for_db(db_path)
    copied = sync_images(images_dir)
    if copied:
        log_fn(f"other_hud_sync: copied {copied} new/changed image(s)")

    conn.close()


def other_hud_sync_loop(config, save_config_fn, enabled_fn=lambda: True, log_fn=print):
    """Background thread entry point. Checks on startup, then every
    RECHECK_INTERVAL_SECONDS thereafter, for as long as the app runs.
    enabled_fn lets this be turned off from Settings without restarting."""
    while True:
        if enabled_fn():
            db_path = get_or_detect_db_path(config, save_config_fn)
            images_dir = config.get("other_hud_images_path") or None
            sync_once(db_path, images_dir, config, save_config_fn, log_fn=log_fn)
        time.sleep(RECHECK_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Exported-JSON discovery
#
# Separate from the database above. The other app can EXPORT one JSON file
# per service, in a documented shape: a "PackageService" wrapper with route
# and formation metadata, containing "TimetableRow" entries each carrying
# arrival, departure, location, latitude and longitude.
#
# That export is by far the cheapest route to full offline timetables - the
# data is already decoded, so importing it is ordinary work rather than
# reverse-engineering Unreal's binary format. If the user has ever run that
# app's extractor, these files may already be sitting on disk.
#
# Detection is by CONTENT, not filename: a file is only accepted if it
# actually has the expected fields, so an unrelated .json can't be mistaken
# for an export.
# ---------------------------------------------------------------------------

EXPORT_SIGNATURE_KEYS = ("arrival", "departure", "location")


def _looks_like_timetable_export(path, max_bytes=400_000):
    """Reads the head of a JSON file and decides whether it is one of the
    other app's service exports. Returns a short description, or None."""
    try:
        if os.path.getsize(path) > max_bytes:
            return None
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    except Exception:
        return None

    def rows_of(obj):
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            for key in ("rows", "timetableRows", "TimetableRows",
                        "stops", "Stops", "service", "Service"):
                v = obj.get(key)
                if isinstance(v, list):
                    return v
            for v in obj.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v
        return None

    rows = rows_of(data)
    if not rows or not isinstance(rows[0], dict):
        return None
    keys = {k.lower() for k in rows[0].keys()}
    if not any(sig in k for k in keys for sig in EXPORT_SIGNATURE_KEYS):
        return None

    return {
        "path": path,
        "row_count": len(rows),
        "row_keys": sorted(rows[0].keys()),
        "has_coords": any("lat" in k for k in keys),
        "sample_row": rows[0],
    }


def find_timetable_exports(extra_roots=None, max_seconds=20, max_hits=40):
    """Searches the usual folders for the other app's exported service JSON.
    Time-bounded so it can't hang on a huge Downloads folder."""
    start = time.time()
    hits = []
    seen = set()
    roots = list(extra_roots or []) + list(_candidate_roots())

    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            for match in glob.iglob(os.path.join(root, "**", "*.json"),
                                    recursive=True):
                if time.time() - start > max_seconds or len(hits) >= max_hits:
                    return {"exports": hits, "timed_out": True,
                            "searched_roots": roots}
                key = os.path.normcase(match)
                if key in seen:
                    continue
                seen.add(key)
                info = _looks_like_timetable_export(match)
                if info:
                    hits.append(info)
        except Exception:
            continue

    return {"exports": hits, "timed_out": False, "searched_roots": roots}
