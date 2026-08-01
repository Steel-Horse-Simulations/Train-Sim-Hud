"""
import_from_other_hud.py

One-off (re-runnable) import: pulls real timetable/journey data and
train-class metadata out of a COPY of the other "TSW HUD" app's own
SQLite database, and populates this app's own local databases
(timetable_db.py / train_classes_db.py) so app.py can query them without
touching that other database again.

USAGE:
    python import_from_other_hud.py --db "C:\\path\\to\\copy\\tsw_hud.db"

Safe to re-run: journeys are fully replaced each run (clear_all() then
re-import — display_name/notes edits are NOT preserved across a re-run,
see timetable_db.py's clear_all() docstring). Train classes are
upserted by source_id — re-running refreshes source-derived fields but
never touches is_visible/display_name/notes, so manual edits survive.

UK DETECTION:
Confirmed join (train_classes has no direct FK to routes — it links via
matching `name` text against `formation_vehicles.class_name`):
    train_classes.name -> formation_vehicles.class_name -> formation_id
    -> route_formations.route_id -> routes.country_id -> countries.code='GB'
See determine_uk_source_ids() below. A class only appears on non-UK routes
gets imported with is_uk=0 / is_visible=0 (hidden, not discarded) per the
"if not UK, hide it for now" decision — it can be switched on later via
PATCH /api/train_classes/<id> without needing a re-import.
"""
import argparse
import sys

import timetable_db
import train_classes_db
from tsw_timetable_importer import (
    connect_readonly,
    find_timetables,
    bulk_fetch_stops,
    chain_segments,
    build_journey,
)


def determine_uk_source_ids(conn) -> set:
    """Returns the set of train_classes.id values that appear on at least
    one UK route. Confirmed join path (train_classes has no direct FK to
    routes/country — the link is via matching name text against
    formation_vehicles.class_name):

        train_classes.name = formation_vehicles.class_name
        formation_vehicles.formation_id = route_formations.formation_id
        route_formations.route_id = routes.id
        routes.country_id = countries.id
        countries.code = 'GB'
    """
    rows = conn.execute("""
        SELECT DISTINCT tc.id
        FROM train_classes tc
        JOIN formation_vehicles fv ON fv.class_name = tc.name
        JOIN route_formations rf ON rf.formation_id = fv.formation_id
        JOIN routes r ON r.id = rf.route_id
        JOIN countries c ON c.id = r.country_id
        WHERE c.code = 'GB'
    """).fetchall()
    return {r["id"] for r in rows}


def import_journeys(conn, route_names_by_id: dict) -> int:
    records = find_timetables(conn)  # all routes, real timetables only
    if not records:
        return 0
    stops_by_id = bulk_fetch_stops(conn, [r.id for r in records])
    chains = chain_segments(records)

    timetable_db.clear_all()
    count = 0
    for chain in chains:
        journey = build_journey(chain, stops_by_id)
        segments = [
            {
                "timetable_id": r.id, "service_name": r.service_name, "section_id": r.section_id,
                "start_time": r.start_time, "duration": r.duration, "bound": r.bound,
                "conductor_compatible": r.conductor_compatible, "playable": r.playable,
            }
            for r in chain
        ]
        stops = [
            {
                "location_name": s.location_name, "arrival": s.time1, "departure": s.time2,
                "latitude": s.latitude, "longitude": s.longitude,
                "source_timetable_id": s.source_timetable_id,
            }
            for s in journey.stops
        ]
        route_name = route_names_by_id.get(journey.route_id)
        timetable_db.import_journey(journey.route_id, route_name, journey.current_service_name, segments, stops)
        count += 1
    return count


def import_train_classes(conn) -> int:
    uk_ids = determine_uk_source_ids(conn)
    rows = conn.execute("""
        SELECT id, name, livery_id, typical_length_m, typical_car_count, is_electric,
               max_speed_kph, max_power_kw, manufacturer_name, engine_description,
               type_description, vehicle_category, thumbnail_path, rail_vehicle_class,
               is_drivable, powered_axle_count
        FROM train_classes
    """).fetchall()

    train_classes_db.init_db()
    for r in rows:
        train_classes_db.import_train_class(
            source_id=r["id"],
            source_name=r["name"],
            is_uk=(r["id"] in uk_ids),
            livery_id=r["livery_id"],
            typical_length_m=r["typical_length_m"],
            typical_car_count=r["typical_car_count"],
            is_electric=r["is_electric"],
            max_speed_kph=r["max_speed_kph"],
            max_power_kw=r["max_power_kw"],
            manufacturer_name=r["manufacturer_name"],
            engine_description=r["engine_description"],
            type_description=r["type_description"],
            vehicle_category=r["vehicle_category"],
            thumbnail_path=r["thumbnail_path"],
            rail_vehicle_class=r["rail_vehicle_class"],
            is_drivable=r["is_drivable"],
            powered_axle_count=r["powered_axle_count"],
        )
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to a COPY of the tsw_hud database file")
    parser.add_argument("--skip-journeys", action="store_true")
    parser.add_argument("--skip-train-classes", action="store_true")
    args = parser.parse_args()

    conn = connect_readonly(args.db)
    timetable_db.init_db()

    if not args.skip_journeys:
        route_rows = conn.execute("SELECT id, name FROM routes").fetchall()
        route_names_by_id = {r["id"]: r["name"] for r in route_rows}
        print("Importing journeys...", file=sys.stderr)
        n = import_journeys(conn, route_names_by_id)
        print(f"Imported {n} journeys into {timetable_db.DB_PATH}", file=sys.stderr)

    if not args.skip_train_classes:
        print("Importing train classes (UK routes detected via formation/route/country join)...", file=sys.stderr)
        n = import_train_classes(conn)
        print(f"Imported {n} train classes into {train_classes_db.DB_PATH} (UK ones visible, rest hidden)", file=sys.stderr)


if __name__ == "__main__":
    main()
