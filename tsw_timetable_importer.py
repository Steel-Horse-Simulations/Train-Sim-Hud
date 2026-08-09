"""
tsw_timetable_importer.py

PROTOTYPE / DRAFT — not yet wired into app.py.

Reads real timetable data out of the "TSW HUD" app's own SQLite database
(tsw_hud) and reconstructs full, ordered stop-by-stop journeys, including
stitching together journeys that TSW splits across multiple `timetables`
rows (e.g. routes like Fife Circle where the player only drives part of
the service and the AI takes over for the rest).

USAGE (read-only against a COPY of the database — never the live file):
    python tsw_timetable_importer.py --db "C:\\path\\to\\copy\\tsw_hud" --list-services "Edinburgh Waverley"
    python tsw_timetable_importer.py --db "C:\\path\\to\\copy\\tsw_hud" --service-name "1R05: Edinburgh Waverley - Glasgow Queen Street" --route-id 107

Known open questions / things to validate against more real examples
before trusting this in production (see project notes):
  - The chaining rule below (same route_id + current_service_name, end
    time of one segment lining up with start time of the next) is a
    heuristic based on ONE confirmed real example (Fife Circle 1R05).
    It may need refinement once tested against more services.
  - We don't yet have the full meaning of every `action_id` in
    timetable_actions — we're using "location_id IS NOT NULL" as a proxy
    for "this is a real passenger stop", which matched every case seen
    so far, but hasn't been checked against every action_id value.
  - `sections` mixes real timetables AND one-off scenarios under the
    same route; we sidestep this by filtering `timetables.source =
    'Timetable'` rather than trying to interpret section names.
"""

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Stop:
    location_name: Optional[str]
    time1: Optional[str]       # observed as arrival in confirmed real stops
    time2: Optional[str]       # observed as departure in confirmed real stops
    latitude: Optional[float]
    longitude: Optional[float]
    sort_order: int
    source_timetable_id: int   # which underlying `timetables.id` this came from


@dataclass
class TimetableRecord:
    id: int
    route_id: int
    section_id: Optional[int]
    service_name: Optional[str]
    current_service_name: Optional[str]
    start_time: Optional[str]
    duration: Optional[str]
    bound: Optional[str]
    conductor_compatible: bool
    playable: bool


@dataclass
class Journey:
    route_id: int
    current_service_name: str
    segment_ids: list = field(default_factory=list)   # timetables.id values, in order
    stops: list = field(default_factory=list)          # list[Stop], fully stitched


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def connect_readonly(db_path: str) -> sqlite3.Connection:
    """Open the database strictly read-only. Never write to the live file —
    always point this at a COPY made after closing the TSW HUD app.

    Built via pathlib's as_uri() rather than a naive f-string, since SQLite's
    URI mode requires forward slashes and percent-encoded spaces — a raw
    Windows path like "C:\\Users\\...\\Hud Stuff\\tsw_hud" is not a valid
    file: URI as-is and will fail with "unable to open database file"."""
    from pathlib import Path

    resolved = Path(db_path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Database file not found: {resolved}")
    uri = resolved.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def parse_hms(value: Optional[str]) -> Optional[timedelta]:
    """Parse an HH:MM:SS (or H:MM:SS) string into a timedelta. Returns None
    for blank/None values rather than raising, since many columns are
    legitimately empty."""
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    parts = value.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return None
    while len(parts) < 3:
        parts.append(0)
    h, m, s = parts[:3]
    return timedelta(hours=h, minutes=m, seconds=s)


def parse_coord(value: Optional[str]) -> Optional[float]:
    """latitude/longitude are stored as TEXT; blank or unparsable -> None."""
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Core queries
# ---------------------------------------------------------------------------

def find_timetables(
    conn: sqlite3.Connection,
    service_name_like: Optional[str] = None,
    route_id: Optional[int] = None,
    include_scenarios: bool = False,
) -> list:
    """Find candidate `timetables` rows. By default restricted to real
    timetables (source='Timetable'), excludes Scenario/Training unless
    include_scenarios is set."""
    query = """
        SELECT id, route_id, section_id, service_name, current_service_name,
               start_time, duration, bound, conductor_compatible, playable
        FROM timetables
    """
    clauses = []
    params: list = []
    if not include_scenarios:
        clauses.append("source = 'Timetable'")
    if service_name_like:
        clauses.append("current_service_name LIKE ?")
        params.append(f"%{service_name_like}%")
    if route_id is not None:
        clauses.append("route_id = ?")
        params.append(route_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY route_id, current_service_name, start_time"

    rows = conn.execute(query, params).fetchall()
    return [
        TimetableRecord(
            id=r["id"],
            route_id=r["route_id"],
            section_id=r["section_id"],
            service_name=r["service_name"],
            current_service_name=r["current_service_name"],
            start_time=r["start_time"],
            duration=r["duration"],
            bound=r["bound"],
            conductor_compatible=bool(r["conductor_compatible"]),
            playable=bool(r["playable"]),
        )
        for r in rows
    ]


def bulk_fetch_stops(conn: sqlite3.Connection, timetable_ids: list) -> dict:
    """Fetch real stops for MANY timetable_ids in one pass (batched, since
    SQLite has a limit of ~999 bound parameters per query), rather than one
    query per timetable_id — this matters once you're processing the whole
    database rather than a single service. Returns {timetable_id: [Stop,...]}."""
    result: dict = {tid: [] for tid in timetable_ids}
    BATCH = 500
    ids = list(timetable_ids)
    for i in range(0, len(ids), BATCH):
        batch = ids[i : i + BATCH]
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""
            SELECT te.timetable_id, l.name AS location_name, te.time1, te.time2,
                   te.latitude, te.longitude, te.sort_order
            FROM timetable_entries te
            JOIN locations l ON l.id = te.location_id
            WHERE te.timetable_id IN ({placeholders}) AND te.location_id IS NOT NULL
            ORDER BY te.timetable_id, te.sort_order
            """,
            batch,
        ).fetchall()
        for r in rows:
            result[r["timetable_id"]].append(
                Stop(
                    location_name=r["location_name"],
                    time1=r["time1"],
                    time2=r["time2"],
                    latitude=parse_coord(r["latitude"]),
                    longitude=parse_coord(r["longitude"]),
                    sort_order=r["sort_order"],
                    source_timetable_id=r["timetable_id"],
                )
            )
    return result


def fetch_stops(conn: sqlite3.Connection, timetable_id: int) -> list:
    """Fetch real passenger stops (rows with a location) for one
    `timetables.id`, in order. Non-stop action rows (start markers,
    movement/signal markers with no location) are excluded."""
    rows = conn.execute(
        """
        SELECT l.name AS location_name, te.time1, te.time2,
               te.latitude, te.longitude, te.sort_order
        FROM timetable_entries te
        JOIN locations l ON l.id = te.location_id
        WHERE te.timetable_id = ? AND te.location_id IS NOT NULL
        ORDER BY te.sort_order
        """,
        (timetable_id,),
    ).fetchall()
    return [
        Stop(
            location_name=r["location_name"],
            time1=r["time1"],
            time2=r["time2"],
            latitude=parse_coord(r["latitude"]),
            longitude=parse_coord(r["longitude"]),
            sort_order=r["sort_order"],
            source_timetable_id=timetable_id,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Chaining logic
# ---------------------------------------------------------------------------

CHAIN_TOLERANCE = timedelta(minutes=2)


def segment_end_time(record: TimetableRecord) -> Optional[timedelta]:
    start = parse_hms(record.start_time)
    dur = parse_hms(record.duration)
    if start is None or dur is None:
        return None
    return start + dur


def chain_segments(records: list) -> list:
    """Group same-route/same-service records into ordered chains, where a
    later segment's start time lines up (within CHAIN_TOLERANCE) with the
    previous segment's computed end time. This models the confirmed
    Fife Circle case (player-driven leg -> AI-driven leg) but is a
    heuristic — validate against more real services before relying on it
    broadly."""
    by_key: dict = {}
    for rec in records:
        key = (rec.route_id, rec.current_service_name)
        by_key.setdefault(key, []).append(rec)

    chains: list = []
    for key, recs in by_key.items():
        recs = sorted(recs, key=lambda r: parse_hms(r.start_time) or timedelta())
        current_chain = [recs[0]]
        for prev, nxt in zip(recs, recs[1:]):
            prev_end = segment_end_time(prev)
            nxt_start = parse_hms(nxt.start_time)
            if (
                prev_end is not None
                and nxt_start is not None
                and abs(nxt_start - prev_end) <= CHAIN_TOLERANCE
            ):
                current_chain.append(nxt)
            else:
                chains.append(current_chain)
                current_chain = [nxt]
        chains.append(current_chain)
    return chains


def dedupe_boundary_stops(stops: list) -> list:
    """When stitching chained segments together, the last stop of one
    segment and the first stop of the next can refer to the same physical
    location (e.g. the handoff point). Collapse an exact adjacent
    duplicate location name into a single stop, preferring the entry that
    has both time1 and time2 populated."""
    if not stops:
        return stops
    result = [stops[0]]
    for stop in stops[1:]:
        prev = result[-1]
        if stop.location_name and stop.location_name == prev.location_name:
            # Same station at a segment boundary -- keep whichever has more data.
            if not prev.time2 and stop.time2:
                result[-1] = stop
            continue
        result.append(stop)
    return result


def build_journey(chain: list, stops_by_id: dict) -> Journey:
    first = chain[0]
    all_stops: list = []
    for rec in chain:
        all_stops.extend(stops_by_id.get(rec.id, []))
    all_stops = dedupe_boundary_stops(all_stops)
    return Journey(
        route_id=first.route_id,
        current_service_name=first.current_service_name or "",
        segment_ids=[r.id for r in chain],
        stops=all_stops,
    )


def journey_to_dict(journey: Journey, chain: list) -> dict:
    """Serialize a Journey (plus its originating segment metadata) into a
    plain dict suitable for JSON export."""
    return {
        "route_id": journey.route_id,
        "current_service_name": journey.current_service_name,
        "segments": [
            {
                "timetable_id": r.id,
                "service_name": r.service_name,
                "section_id": r.section_id,
                "start_time": r.start_time,
                "duration": r.duration,
                "bound": r.bound,
                "conductor_compatible": r.conductor_compatible,
                "playable": r.playable,
            }
            for r in chain
        ],
        "stops": [
            {
                "location_name": s.location_name,
                "arrival": s.time1,
                "departure": s.time2,
                "latitude": s.latitude,
                "longitude": s.longitude,
                "sort_order": s.sort_order,
                "source_timetable_id": s.source_timetable_id,
            }
            for s in journey.stops
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to a COPY of the tsw_hud database file")
    parser.add_argument("--service-name", help="Substring to match against current_service_name")
    parser.add_argument("--route-id", type=int, help="Restrict to a specific route_id")
    parser.add_argument(
        "--list-services",
        metavar="SUBSTRING",
        help="Just list matching services (id, route_id, current_service_name, start_time, duration) and exit",
    )
    parser.add_argument(
        "--include-scenarios",
        action="store_true",
        help="Also include one-off Scenario/Training records (default: real Timetables only)",
    )
    parser.add_argument(
        "--export-json",
        metavar="PATH",
        help="Export ALL matching journeys (across every route unless --route-id is given) "
             "to this JSON file, instead of printing to the console",
    )
    args = parser.parse_args()

    conn = connect_readonly(args.db)

    if args.list_services:
        records = find_timetables(conn, service_name_like=args.list_services, include_scenarios=args.include_scenarios)
        for r in records:
            print(f"{r.id}\t{r.route_id}\t{r.section_id}\t{r.start_time}\t{r.duration}\t{r.current_service_name}")
        return

    records = find_timetables(
        conn,
        service_name_like=args.service_name,
        route_id=args.route_id,
        include_scenarios=args.include_scenarios,
    )
    if not records:
        print("No matching timetables found.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(records)} timetable record(s). Fetching stops...", file=sys.stderr)
    stops_by_id = bulk_fetch_stops(conn, [r.id for r in records])

    chains = chain_segments(records)
    print(f"Grouped into {len(chains)} journey chain(s).", file=sys.stderr)

    if args.export_json:
        journeys = [journey_to_dict(build_journey(chain, stops_by_id), chain) for chain in chains]
        with open(args.export_json, "w", encoding="utf-8") as f:
            json.dump(journeys, f, indent=2, ensure_ascii=False)
        print(f"Wrote {len(journeys)} journeys to {args.export_json}", file=sys.stderr)
        return

    for chain in chains:
        journey = build_journey(chain, stops_by_id)
        print(f"\n=== {journey.current_service_name} (route {journey.route_id}, "
              f"segments {journey.segment_ids}) ===")
        for i, stop in enumerate(journey.stops, start=1):
            print(f"  [{i:>3}] (orig sort_order {stop.sort_order:>3}) {stop.location_name:<30} "
                  f"arr={stop.time1 or '--':<10} dep={stop.time2 or '--':<10} "
                  f"lat={stop.latitude} lon={stop.longitude}")


if __name__ == "__main__":
    main()
