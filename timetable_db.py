"""
Imported real-timetable database.

Stores journeys reconstructed from the other TSW HUD app's own SQLite
database (via tsw_timetable_importer.py's chaining logic — real
arrival/departure times, stops, and coordinates), so app.py can query a
specific service's stop list on demand without holding the ~150MB full
export in memory or re-parsing JSON per request.

EDITABLE VS PROTECTED COLUMNS
Per project decision: everything imported stays editable EXCEPT columns
that would break referential integrity or make re-importing/de-duplicating
impossible if changed. Each table's update function only accepts columns
in that table's EDITABLE_FIELDS set below — anything else is silently
ignored rather than raising, so a caller can't accidentally corrupt a key.

Protected (never editable): id, journey_id, source_timetable_id, and the
segment/stop link columns. Editable: display_name, notes, and every
imported data field (times, locations, coordinates, service metadata) —
so a wrong or approximate value from the source data can always be
hand-corrected without waiting for a re-import.
"""
import json
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "timetables.db")

# Columns callers are allowed to update. id / journey_id / *_id link columns
# are deliberately excluded so update_* calls can't corrupt relationships.
JOURNEY_EDITABLE_FIELDS = {"display_name", "notes"}
SEGMENT_EDITABLE_FIELDS = {
    "start_time", "duration", "bound", "conductor_compatible", "playable",
}
STOP_EDITABLE_FIELDS = {
    "location_name", "arrival", "departure", "latitude", "longitude",
}


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS journeys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_id INTEGER,
                route_name TEXT,
                current_service_name TEXT NOT NULL,
                display_name TEXT,
                notes TEXT,
                imported_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS journey_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                journey_id INTEGER NOT NULL REFERENCES journeys(id) ON DELETE CASCADE,
                source_timetable_id INTEGER NOT NULL,
                seq_order INTEGER NOT NULL,
                service_name TEXT,
                section_id INTEGER,
                start_time TEXT,
                duration TEXT,
                bound TEXT,
                conductor_compatible INTEGER,
                playable INTEGER,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS journey_stops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                journey_id INTEGER NOT NULL REFERENCES journeys(id) ON DELETE CASCADE,
                source_timetable_id INTEGER,
                stop_order INTEGER NOT NULL,
                location_name TEXT,
                arrival TEXT,
                departure TEXT,
                latitude REAL,
                longitude REAL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_journeys_service ON journeys(current_service_name);
            CREATE INDEX IF NOT EXISTS idx_journeys_route ON journeys(route_id);
            CREATE INDEX IF NOT EXISTS idx_segments_journey ON journey_segments(journey_id);
            CREATE INDEX IF NOT EXISTS idx_stops_journey ON journey_stops(journey_id);
            CREATE INDEX IF NOT EXISTS idx_journeys_updated ON journeys(updated_at);
            CREATE INDEX IF NOT EXISTS idx_segments_updated ON journey_segments(updated_at);
            CREATE INDEX IF NOT EXISTS idx_stops_updated ON journey_stops(updated_at);
        """)
        conn.commit()
    finally:
        conn.close()


def clear_all():
    """Wipe all imported journeys so a fresh import doesn't accumulate
    duplicates on top of a previous one. Editable display_name/notes are
    lost on re-import by design — this is a full replace, not a merge."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM journeys")
        conn.commit()
    finally:
        conn.close()


def import_journey(route_id, route_name, current_service_name, segments, stops):
    """Insert one fully-chained journey (as produced by
    tsw_timetable_importer.py's chain_segments/build_journey). `segments`
    is a list of dicts with keys matching journey_to_dict()'s "segments"
    shape; `stops` matches its "stops" shape."""
    now = datetime.now().isoformat(timespec="seconds")
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO journeys (route_id, route_name, current_service_name, display_name, imported_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (route_id, route_name, current_service_name, current_service_name, now, now),
        )
        journey_id = cur.lastrowid

        for i, seg in enumerate(segments):
            cur.execute(
                "INSERT INTO journey_segments "
                "(journey_id, source_timetable_id, seq_order, service_name, section_id, "
                " start_time, duration, bound, conductor_compatible, playable, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    journey_id, seg["timetable_id"], i, seg.get("service_name"),
                    seg.get("section_id"), seg.get("start_time"), seg.get("duration"),
                    seg.get("bound"), int(bool(seg.get("conductor_compatible"))),
                    int(bool(seg.get("playable"))), now,
                ),
            )

        for i, stop in enumerate(stops):
            cur.execute(
                "INSERT INTO journey_stops "
                "(journey_id, source_timetable_id, stop_order, location_name, arrival, departure, latitude, longitude, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    journey_id, stop.get("source_timetable_id"), i, stop.get("location_name"),
                    stop.get("arrival"), stop.get("departure"), stop.get("latitude"), stop.get("longitude"), now,
                ),
            )
        conn.commit()
        return journey_id
    finally:
        conn.close()


def search_journeys(query=None, route_id=None, limit=100, offset=0):
    conn = _connect()
    try:
        sql = "SELECT * FROM journeys"
        clauses, params = [], []
        if query:
            clauses.append("(current_service_name LIKE ? OR display_name LIKE ?)")
            params += [f"%{query}%", f"%{query}%"]
        if route_id is not None:
            clauses.append("route_id = ?")
            params.append(route_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY current_service_name LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_journey(journey_id):
    conn = _connect()
    try:
        journey = conn.execute("SELECT * FROM journeys WHERE id = ?", (journey_id,)).fetchone()
        if not journey:
            return None
        segments = conn.execute(
            "SELECT * FROM journey_segments WHERE journey_id = ? ORDER BY seq_order", (journey_id,)
        ).fetchall()
        stops = conn.execute(
            "SELECT * FROM journey_stops WHERE journey_id = ? ORDER BY stop_order", (journey_id,)
        ).fetchall()
        result = dict(journey)
        result["segments"] = [dict(s) for s in segments]
        result["stops"] = [dict(s) for s in stops]
        return result
    finally:
        conn.close()


def update_journey(journey_id, fields, client_updated_at=None):
    return _update_row("journeys", "id", journey_id, fields, JOURNEY_EDITABLE_FIELDS, client_updated_at)


def update_segment(segment_id, fields, client_updated_at=None):
    return _update_row("journey_segments", "id", segment_id, fields, SEGMENT_EDITABLE_FIELDS, client_updated_at)


def update_stop(stop_id, fields, client_updated_at=None):
    return _update_row("journey_stops", "id", stop_id, fields, STOP_EDITABLE_FIELDS, client_updated_at)


def _update_row(table, key_col, key_val, fields, allowed, client_updated_at=None):
    """Shared helper: only ever writes columns present in `allowed`,
    silently dropping anything else — this is the actual enforcement of
    the editable/protected split, not just a documentation convention.

    Two modes:
    - client_updated_at=None (normal local edit, e.g. from this app's own
      pages): always applies, sets updated_at to right now. Local edits are
      happening live against the current server state, so there's nothing
      to compare against.
    - client_updated_at=<timestamp> (a synced edit pushed from the tablet,
      made while potentially offline): last-write-wins — only applies if
      client_updated_at is later than this row's current updated_at, and
      when applied, updated_at is set to client_updated_at (preserving the
      real original edit time) rather than "now" (when it happened to sync).
      Returns False without applying if the server's copy is newer — the
      caller should treat that as "rejected, take the server's version."
    """
    safe_fields = {k: v for k, v in fields.items() if k in allowed}
    if not safe_fields:
        return False
    conn = _connect()
    try:
        if client_updated_at is not None:
            current = conn.execute(f"SELECT updated_at FROM {table} WHERE {key_col} = ?", (key_val,)).fetchone()
            if current is None:
                return False  # row doesn't exist (deleted since, or bad id) - nothing to apply to
            if client_updated_at <= current["updated_at"]:
                return False  # server's copy is same age or newer - reject, client should re-pull
            new_updated_at = client_updated_at
        else:
            new_updated_at = datetime.now().isoformat(timespec="seconds")

        set_clause = ", ".join(f"{col} = ?" for col in safe_fields) + ", updated_at = ?"
        params = list(safe_fields.values()) + [new_updated_at, key_val]
        conn.execute(f"UPDATE {table} SET {set_clause} WHERE {key_col} = ?", params)
        conn.commit()
        return True
    finally:
        conn.close()


def get_changes_since(since_timestamp, after_journey_id=0, limit=100):
    """For the pull side of sync: up to `limit` journeys (with their
    segments/stops) that changed at any level since `since_timestamp`,
    ordered by journey id so pagination is simple and correct (a plain
    integer keyset, no timestamp-tie-breaking headaches - a bulk import
    can easily give thousands of rows the exact same second-precision
    updated_at, which would break naive timestamp-only pagination).

    Two-step approach: first cheaply collect the SET of matching journey
    ids (just integers, fine even for a large sync) and sort it; THEN only
    fetch the full nested journey/segments/stops objects for one page of
    that sorted list. This is what actually bounds each HTTP response to
    a manageable size regardless of total dataset size - fetching a
    thousand fully-nested journeys in one response is what caused the
    original timeout bug, not the (cheap) id-matching step itself.

    Returns (page_of_journeys, has_more) - caller should keep calling with
    after_journey_id set to the last id seen until has_more is False.
    """
    conn = _connect()
    try:
        journey_ids = set()
        for row in conn.execute("SELECT id FROM journeys WHERE updated_at > ?", (since_timestamp,)):
            journey_ids.add(row["id"])
        for row in conn.execute("SELECT DISTINCT journey_id FROM journey_segments WHERE updated_at > ?", (since_timestamp,)):
            journey_ids.add(row["journey_id"])
        for row in conn.execute("SELECT DISTINCT journey_id FROM journey_stops WHERE updated_at > ?", (since_timestamp,)):
            journey_ids.add(row["journey_id"])

        page_ids = sorted(jid for jid in journey_ids if jid > after_journey_id)[: limit + 1]
        has_more = len(page_ids) > limit
        page_ids = page_ids[:limit]

        results = []
        for jid in page_ids:
            journey = conn.execute("SELECT * FROM journeys WHERE id = ?", (jid,)).fetchone()
            if not journey:
                continue  # deleted since - fine to just skip, tablet keeps its stale copy for now
            segments = conn.execute("SELECT * FROM journey_segments WHERE journey_id = ? ORDER BY seq_order", (jid,)).fetchall()
            stops = conn.execute("SELECT * FROM journey_stops WHERE journey_id = ? ORDER BY stop_order", (jid,)).fetchall()
            result = dict(journey)
            result["segments"] = [dict(s) for s in segments]
            result["stops"] = [dict(s) for s in stops]
            results.append(result)
        return results, has_more
    finally:
        conn.close()


def init_station_tables():
    """Tables for names extracted from the game's own files.

    Kept separate from journeys/journey_stops on purpose: this is a
    CATALOGUE of what exists on a route, read straight out of the pak, not a
    record of anything driven. Mixing the two would mean a re-import of the
    game files could disturb journey data.
    """
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS route_stations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_key TEXT NOT NULL,
                place TEXT NOT NULL,
                platform TEXT,
                raw_name TEXT NOT NULL,
                source_asset TEXT,
                imported_at TEXT NOT NULL,
                UNIQUE(route_key, raw_name)
            );

            CREATE TABLE IF NOT EXISTS route_headcodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_key TEXT NOT NULL,
                headcode TEXT NOT NULL,
                source_asset TEXT,
                imported_at TEXT NOT NULL,
                UNIQUE(route_key, headcode)
            );

            CREATE INDEX IF NOT EXISTS idx_route_stations_place
                ON route_stations(route_key, place);
        """)
        conn.commit()
    finally:
        conn.close()


def save_station_names(route_key, places, headcodes, source_asset=None):
    """Stores extracted stations and headcodes. Idempotent - re-importing
    the same asset updates rather than duplicating, so the extraction can be
    re-run freely as the parser improves."""
    init_station_tables()
    conn = _connect()
    now = datetime.now().isoformat(timespec="seconds")
    stations = headcodes_added = 0
    try:
        for place, platforms in (places or {}).items():
            for p in (platforms or [None]):
                raw = f"{place} {p}".strip() if p else place
                conn.execute(
                    "INSERT INTO route_stations "
                    "(route_key, place, platform, raw_name, source_asset, imported_at) "
                    "VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(route_key, raw_name) DO UPDATE SET "
                    "place=excluded.place, platform=excluded.platform, "
                    "source_asset=excluded.source_asset, imported_at=excluded.imported_at",
                    (route_key, place, p, raw, source_asset, now))
                stations += 1
        for hc in sorted(set(headcodes or [])):
            conn.execute(
                "INSERT INTO route_headcodes (route_key, headcode, source_asset, imported_at) "
                "VALUES (?,?,?,?) ON CONFLICT(route_key, headcode) DO UPDATE SET "
                "source_asset=excluded.source_asset, imported_at=excluded.imported_at",
                (route_key, hc, source_asset, now))
            headcodes_added += 1
        conn.commit()
    finally:
        conn.close()
    return {"route_key": route_key, "stations_saved": stations,
            "headcodes_saved": headcodes_added}


def list_route_stations(route_key=None):
    init_station_tables()
    conn = _connect()
    try:
        if route_key:
            rows = conn.execute(
                "SELECT * FROM route_stations WHERE route_key=? ORDER BY place, platform",
                (route_key,)).fetchall()
            codes = conn.execute(
                "SELECT headcode FROM route_headcodes WHERE route_key=? ORDER BY headcode",
                (route_key,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM route_stations ORDER BY route_key, place, platform").fetchall()
            codes = conn.execute(
                "SELECT headcode FROM route_headcodes ORDER BY route_key, headcode").fetchall()
        return {"stations": [dict(r) for r in rows],
                "headcodes": [r[0] for r in codes]}
    finally:
        conn.close()


def init_extracted_tables():
    """Tables for timetables read out of the game's pak files.

    Separate from journeys/journey_stops, and from route_stations, for the
    same reason as before: this is what the GAME says a route runs, derived
    from the paks. It is not a record of anything driven, and a re-extraction
    must never be able to disturb driven data.
    """
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS extracted_services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_key TEXT NOT NULL,
                source_asset TEXT NOT NULL,
                service_index INTEGER NOT NULL,
                start_offset INTEGER,
                first_time TEXT,
                last_time TEXT,
                duration_min REAL,
                track_points INTEGER,
                stop_records INTEGER,
                call_count INTEGER,
                headcode TEXT,
                service_name TEXT,
                role TEXT,
                imported_at TEXT NOT NULL,
                UNIQUE(route_key, source_asset, service_index)
            );

            CREATE TABLE IF NOT EXISTS extracted_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_id INTEGER NOT NULL
                    REFERENCES extracted_services(id) ON DELETE CASCADE,
                call_order INTEGER NOT NULL,
                arrival TEXT,
                departure TEXT,
                records INTEGER,
                platform TEXT,
                station_id INTEGER REFERENCES route_stations(id),
                station_name TEXT,
                UNIQUE(service_id, call_order)
            );

            CREATE INDEX IF NOT EXISTS idx_extracted_calls_service
                ON extracted_calls(service_id);
        """)
        # Older databases predate service_name/role. Add the columns rather
        # than requiring the file to be deleted - it holds real extractions.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(extracted_services)")}
        for col in ("service_name", "role"):
            if col not in cols:
                conn.execute(f"ALTER TABLE extracted_services ADD COLUMN {col} TEXT")
        # Older databases predate service_name/role; add them rather than
        # forcing the file to be deleted.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(extracted_services)")}
        for col in ("service_name", "role"):
            if col not in cols:
                conn.execute(f"ALTER TABLE extracted_services ADD COLUMN {col} TEXT")
        conn.commit()
    finally:
        conn.close()


def save_extracted_timetable(route_key, source_asset, services):
    """Stores the services and calls produced by pak_tools.extract_timetable.

    Replaces this asset's previous extraction rather than merging. The
    extraction is derived data - re-running it with a better parser should
    supersede the old result, not accumulate alongside it, and merging would
    leave a mix of two parser versions with no way to tell which rows came
    from which.

    station_name is left NULL: the DataTrack layers carry no station
    identity, which four separate searches confirmed. The column exists so
    labelling later is an UPDATE rather than a schema change.
    """
    init_station_tables()
    init_extracted_tables()
    conn = _connect()
    now = datetime.now().isoformat(timespec="seconds")
    try:
        old = [r[0] for r in conn.execute(
            "SELECT id FROM extracted_services WHERE route_key=? AND source_asset=?",
            (route_key, source_asset))]
        if old:
            conn.executemany("DELETE FROM extracted_calls WHERE service_id=?",
                             [(i,) for i in old])
            conn.execute(
                "DELETE FROM extracted_services WHERE route_key=? AND source_asset=?",
                (route_key, source_asset))

        n_services = n_calls = 0
        for i, svc in enumerate(services or []):
            cur = conn.execute(
                "INSERT INTO extracted_services (route_key, source_asset, "
                "service_index, start_offset, first_time, last_time, duration_min, "
                "track_points, stop_records, call_count, imported_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (route_key, source_asset, i, svc.get("start_offset"),
                 svc.get("first"), svc.get("last"), svc.get("duration_min"),
                 svc.get("track_points"), svc.get("stop_count"),
                 svc.get("call_count"), now))
            sid = cur.lastrowid
            n_services += 1
            for k, call in enumerate(svc.get("calls") or []):
                conn.execute(
                    "INSERT INTO extracted_calls (service_id, call_order, "
                    "arrival, departure, records) VALUES (?,?,?,?,?)",
                    (sid, k, call.get("arrival"), call.get("departure"),
                     call.get("records")))
                n_calls += 1
        conn.commit()
        return {"route_key": route_key, "source_asset": source_asset,
                "services_saved": n_services, "calls_saved": n_calls,
                "replaced": len(old)}
    finally:
        conn.close()


def list_extracted_timetable(route_key=None, with_calls=True, limit=200):
    init_extracted_tables()
    conn = _connect()
    try:
        if route_key:
            rows = conn.execute(
                "SELECT * FROM extracted_services WHERE route_key=? "
                "ORDER BY first_time LIMIT ?", (route_key, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM extracted_services ORDER BY route_key, first_time "
                "LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            svc = dict(r)
            if with_calls:
                svc["calls"] = [dict(c) for c in conn.execute(
                    "SELECT * FROM extracted_calls WHERE service_id=? "
                    "ORDER BY call_order", (r["id"],))]
            out.append(svc)
        return {"services": out, "service_count": len(out)}
    finally:
        conn.close()


def save_drive_sightings(route_key, sightings, service_names=None):
    """Stores station sightings captured while driving.

    These are OBSERVATIONS, kept apart from both the pak-derived catalogue
    and driven journeys. A sighting carries times_seen and a closest
    approach, so a poor capture can be judged and re-run rather than
    silently degrading the mapping - which matters because this is the
    bridge between the extracted times and the extracted names, and a wrong
    bridge would mislabel a whole timetable.
    """
    conn = _connect()
    now = datetime.now().isoformat(timespec="seconds")
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS drive_sightings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_key TEXT NOT NULL,
                station_name TEXT NOT NULL,
                times_seen INTEGER,
                closest_distance_m REAL,
                first_seen_distance_m REAL,
                platform_length REAL,
                latitude REAL,
                longitude REAL,
                service_names TEXT,
                recorded_at TEXT NOT NULL,
                UNIQUE(route_key, station_name)
            );
        """)
        svc = ", ".join(sorted(service_names or []))
        saved = 0
        for s in sightings or []:
            name = (s.get("station_name") or "").strip()
            if not name:
                continue
            conn.execute(
                "INSERT INTO drive_sightings (route_key, station_name, times_seen, "
                "closest_distance_m, first_seen_distance_m, platform_length, "
                "latitude, longitude, service_names, recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(route_key, station_name) DO UPDATE SET "
                "times_seen = COALESCE(drive_sightings.times_seen,0) + excluded.times_seen, "
                # Smallest MAGNITUDE wins - the value is negative once the
                # station is behind the train, so a plain MIN would keep the
                # reading furthest past it.
                "closest_distance_m = CASE WHEN ABS(COALESCE(excluded.closest_distance_m, 9e9)) "
                "     < ABS(COALESCE(drive_sightings.closest_distance_m, 9e9)) "
                "     THEN excluded.closest_distance_m ELSE drive_sightings.closest_distance_m END, "
                "latitude = COALESCE(excluded.latitude, drive_sightings.latitude), "
                "longitude = COALESCE(excluded.longitude, drive_sightings.longitude), "
                "service_names = excluded.service_names, "
                "recorded_at = excluded.recorded_at",
                (route_key, name, s.get("times_seen"), s.get("closest_distance_m"),
                 s.get("first_seen_distance_m"), s.get("platform_length"),
                 s.get("latitude"), s.get("longitude"), svc, now))
            saved += 1
        conn.commit()
        return {"route_key": route_key, "sightings_saved": saved}
    finally:
        conn.close()


def match_sightings_to_stations(route_key):
    """Matches driven station names against the names read from the paks.

    Reported rather than applied. The two sources spell things differently -
    the index asset contains BOTH "Edinburgh Waverley" and DTG's own
    "Edinburgh Waverly", and the live API may use either - so the safe thing
    is to show what lines up and what does not, and let a person judge the
    leftovers.
    """
    init_station_tables()
    conn = _connect()
    try:
        try:
            seen = [dict(r) for r in conn.execute(
                "SELECT * FROM drive_sightings WHERE route_key=?", (route_key,))]
        except sqlite3.OperationalError:
            return {"error": "no_sightings_recorded", "route_key": route_key}
        cat = [dict(r) for r in conn.execute("SELECT * FROM route_stations")]

        def norm(s):
            return "".join(c for c in (s or "").lower() if c.isalnum())

        by_norm = {}
        for c in cat:
            by_norm.setdefault(norm(c["place"]), []).append(c)

        matched, unmatched = [], []
        for s in seen:
            key = norm(s["station_name"])
            hit = by_norm.get(key)
            if not hit:
                # A driven name often carries a platform: "Leven 1".
                for cand_key, rows in by_norm.items():
                    if key.startswith(cand_key) and len(cand_key) > 4:
                        hit = rows
                        break
            if hit:
                matched.append({"driven": s["station_name"],
                                "place": hit[0]["place"],
                                "platforms": sorted({r["platform"] for r in hit if r["platform"]}),
                                "closest_distance_m": s.get("closest_distance_m")})
            else:
                unmatched.append(s["station_name"])
        return {
            "route_key": route_key,
            "matched": sorted(matched, key=lambda m: m["place"]),
            "matched_count": len(matched),
            "unmatched_driven": sorted(unmatched),
            "catalogue_places": len({c["place"] for c in cat}),
        }
    finally:
        conn.close()


def save_definition_timetable(route_key, source_asset, services):
    """Stores services and NAMED stops parsed from a RouteTimetableDefinition.

    Uses the same extracted_services / extracted_calls tables as the
    DataTrack extraction, with station_name now filled in - that column was
    added empty for exactly this. Replaces this asset's previous rows rather
    than merging: two parsers over the same route would otherwise leave a
    mix with no way to tell which rows came from which.
    """
    init_station_tables()
    init_extracted_tables()
    conn = _connect()
    now = datetime.now().isoformat(timespec="seconds")
    try:
        old = [r[0] for r in conn.execute(
            "SELECT id FROM extracted_services WHERE route_key=? AND source_asset=?",
            (route_key, source_asset))]
        if old:
            conn.executemany("DELETE FROM extracted_calls WHERE service_id=?",
                             [(i,) for i in old])
            conn.execute(
                "DELETE FROM extracted_services WHERE route_key=? AND source_asset=?",
                (route_key, source_asset))

        n_services = n_calls = 0
        for i, svc in enumerate(services or []):
            stops = svc.get("stops") or []
            cur = conn.execute(
                "INSERT INTO extracted_services (route_key, source_asset, "
                "service_index, first_time, last_time, call_count, headcode, "
                "service_name, role, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (route_key, source_asset, i, svc.get("first_time"),
                 svc.get("last_time"), len(stops),
                 svc.get("headcode"), svc.get("name"), svc.get("role"), now))
            sid = cur.lastrowid
            n_services += 1
            for k, st in enumerate(stops):
                conn.execute(
                    "INSERT INTO extracted_calls (service_id, call_order, "
                    "arrival, departure, platform, station_name) "
                    "VALUES (?,?,?,?,?,?)",
                    (sid, k, st.get("arrival"), st.get("departure"),
                     st.get("platform"), st.get("station")))
                n_calls += 1
        conn.commit()
        return {"route_key": route_key, "source_asset": source_asset,
                "services_saved": n_services, "calls_saved": n_calls,
                "replaced": len(old)}
    finally:
        conn.close()


def init_route_tables():
    """Routes discovered by scanning the game's pak files.

    A catalogue of what exists, kept apart from journeys and from the
    extracted timetable itself. Storing it means the route list survives a
    restart: scanning every pak takes a while, and a page that had to
    re-scan before it could show anything would be unusable.
    """
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_key TEXT NOT NULL UNIQUE,
                pak_name TEXT NOT NULL,
                pak_path TEXT NOT NULL,
                display_name TEXT,
                timetable_count INTEGER DEFAULT 0,
                index_assets TEXT,
                layer_count INTEGER DEFAULT 0,
                discovered_at TEXT NOT NULL,
                last_scanned_at TEXT,
                service_count INTEGER DEFAULT 0,
                call_count INTEGER DEFAULT 0,
                station_count INTEGER DEFAULT 0,
                scan_error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_routes_scanned
                ON routes(last_scanned_at);
        """)
        conn.commit()
    finally:
        conn.close()


def save_discovered_routes(entries):
    """Records paks that contain timetables.

    Re-discovery UPDATES rather than replaces, so a rescan of the pak folder
    never wipes the scan results already gathered for a route - those are
    the expensive part.
    """
    init_route_tables()
    conn = _connect()
    now = datetime.now().isoformat(timespec="seconds")
    added = updated = 0
    try:
        for e in entries or []:
            key = e.get("route_key") or e.get("pak_name")
            if not key:
                continue
            row = conn.execute("SELECT id FROM routes WHERE route_key=?",
                               (key,)).fetchone()
            payload = (e.get("pak_name"), e.get("pak_path"),
                       e.get("display_name") or e.get("pak_name"),
                       e.get("timetable_count") or 0,
                       json.dumps(e.get("index_assets") or []),
                       e.get("layer_count") or 0)
            if row:
                conn.execute(
                    "UPDATE routes SET pak_name=?, pak_path=?, display_name=?, "
                    "timetable_count=?, index_assets=?, layer_count=? WHERE id=?",
                    payload + (row[0],))
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO routes (pak_name, pak_path, display_name, "
                    "timetable_count, index_assets, layer_count, route_key, "
                    "discovered_at) VALUES (?,?,?,?,?,?,?,?)",
                    payload + (key, now))
                added += 1
        conn.commit()
        return {"added": added, "updated": updated}
    finally:
        conn.close()


def list_routes():
    init_route_tables()
    conn = _connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM routes ORDER BY display_name")]
        for r in rows:
            try:
                r["index_assets"] = json.loads(r.get("index_assets") or "[]")
            except Exception:
                r["index_assets"] = []
        return {"routes": rows, "route_count": len(rows),
                "scanned": sum(1 for r in rows if r.get("last_scanned_at")),
                "unscanned": sum(1 for r in rows if not r.get("last_scanned_at"))}
    finally:
        conn.close()


def record_route_scan(route_key, service_count=0, call_count=0,
                      station_count=0, error=None):
    init_route_tables()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE routes SET last_scanned_at=?, service_count=?, call_count=?, "
            "station_count=?, scan_error=? WHERE route_key=?",
            (datetime.now().isoformat(timespec="seconds"), service_count,
             call_count, station_count, error, route_key))
        conn.commit()
    finally:
        conn.close()


def init_route_tables():
    """Routes discovered by scanning pak files.

    A ROUTE here is one pak containing timetable assets. Kept separate from
    the extracted timetable itself so a rescan can refresh what exists
    without touching the services already parsed out of it - the scan is
    cheap and gets re-run often, the extraction is not.
    """
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scanned_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_key TEXT NOT NULL UNIQUE,
                pak_name TEXT NOT NULL,
                pak_path TEXT,
                display_name TEXT,
                timetable_count INTEGER DEFAULT 0,
                datatrack_count INTEGER DEFAULT 0,
                assets TEXT,
                first_seen TEXT NOT NULL,
                last_scanned TEXT,
                last_extracted TEXT,
                services_extracted INTEGER DEFAULT 0,
                calls_extracted INTEGER DEFAULT 0,
                status TEXT DEFAULT 'found'
            );
        """)
        conn.commit()
    finally:
        conn.close()


def _route_key_from_pak(pak_name):
    """A stable key from a pak filename.

    TSW names them like `TS2Prototype-WindowsNoEditor-FifeCircle.pak`, so the
    trailing segment is the route. Stripping the platform prefix keeps the
    key readable and stable across TSW versions, which rename the prefix.
    """
    base = os.path.splitext(os.path.basename(pak_name or ""))[0]
    for prefix in ("TS2Prototype-WindowsNoEditor-", "TS2Prototype-Windows-",
                   "TS2Prototype-"):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    return base or "unknown"


def save_scanned_routes(results):
    """Records routes found by a pak scan.

    Only ever ADDS or refreshes the scan fields. Extraction results
    (last_extracted, services_extracted) are left alone, so rescanning after
    installing new DLC cannot wipe the record of what has already been
    parsed.
    """
    init_route_tables()
    conn = _connect()
    now = datetime.now().isoformat(timespec="seconds")
    added = updated = 0
    try:
        for r in results or []:
            if r.get("error"):
                continue
            pak_name = r.get("pak_name") or ""
            key = _route_key_from_pak(pak_name)
            # Keep the two kinds APART. `timetables` are assets sitting in a
            # Timetable/ or ServiceMode/ folder - the real index. Those found
            # only by an `_TT` name suffix are a weaker match, and mixing
            # them meant a ServiceMode or scenario asset could be picked
            # ahead of the route's actual timetable. RivieraLine failed that
            # way: the chosen asset parsed to zero services.
            assets = list(r.get("timetables") or [])
            fallback = list(r.get("timetables_by_name") or [])
            counts = r.get("counts") or {}
            row = conn.execute(
                "SELECT id FROM scanned_routes WHERE route_key=?", (key,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE scanned_routes SET pak_name=?, pak_path=?, "
                    "timetable_count=?, datatrack_count=?, assets=?, last_scanned=? "
                    "WHERE route_key=?",
                    (pak_name, r.get("pak_path"), len(assets),
                     counts.get("datatrack", 0), json.dumps(assets), now, key))
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO scanned_routes (route_key, pak_name, pak_path, "
                    "display_name, timetable_count, datatrack_count, assets, "
                    "first_seen, last_scanned, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,'found')",
                    (key, pak_name, r.get("pak_path"), key.replace("_", " "),
                     len(assets) + len(fallback), counts.get("datatrack", 0),
                     json.dumps({"primary": assets, "fallback": fallback}),
                     now, now))
                added += 1
        conn.commit()
        return {"added": added, "updated": updated}
    finally:
        conn.close()


def list_scanned_routes():
    init_route_tables()
    conn = _connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM scanned_routes ORDER BY "
            "CASE WHEN last_extracted IS NULL THEN 0 ELSE 1 END, route_key")]
        for r in rows:
            try:
                raw = json.loads(r.get("assets") or "[]")
            except Exception:
                raw = []
            # Older rows stored a flat list; newer ones separate primary from
            # fallback. Read both so an existing database keeps working.
            if isinstance(raw, dict):
                r["assets"] = raw.get("primary") or []
                r["fallback_assets"] = raw.get("fallback") or []
            else:
                r["assets"] = raw
                r["fallback_assets"] = []
            r["is_new"] = not r.get("last_extracted")
        return {
            "routes": rows,
            "route_count": len(rows),
            "new_count": sum(1 for r in rows if r["is_new"]),
        }
    finally:
        conn.close()


def mark_route_failed(route_key, error):
    """Records why a route could not be read.

    Kept visible in the list rather than leaving the route looking merely
    unread: "not read" invites pressing the button again forever, while a
    stated reason can be acted on.
    """
    init_route_tables()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE scanned_routes SET status='failed', last_scanned=? "
            "WHERE route_key=?",
            (datetime.now().isoformat(timespec="seconds"), route_key))
        conn.commit()
    finally:
        conn.close()


def mark_route_extracted(route_key, services, calls):
    init_route_tables()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE scanned_routes SET last_extracted=?, services_extracted=?, "
            "calls_extracted=?, status='extracted' WHERE route_key=?",
            (datetime.now().isoformat(timespec="seconds"), services, calls,
             route_key))
        conn.commit()
    finally:
        conn.close()


def find_service(headcode=None, route_key=None, near_time=None,
                 service_name=None):
    """Finds a stored service, best match first.

    A headcode is NOT unique. TSW splits a working across a player leg and an
    AI continuation that share one, and a headcode can recur across the day -
    on the Fife Circle file 221 of 429 headcodes appear more than once. So a
    live headcode alone cannot pick a service, and `near_time` breaks the tie
    by choosing the one whose booked times bracket the clock.

    A player leg is preferred over a continuation when both fit: it is the
    one being driven.
    """
    init_extracted_tables()
    conn = _connect()
    try:
        sql = ("SELECT s.*, "
               "(SELECT COUNT(*) FROM extracted_calls c WHERE c.service_id=s.id) "
               "AS n_calls FROM extracted_services s WHERE 1=1")
        params = []
        if service_name:
            sql += " AND s.service_name = ?"
            params.append(service_name)
        if headcode:
            # Match the headcode OR the service name, and tolerate the forms
            # the game actually reports. `currentServiceName` is not
            # guaranteed to be a bare code: TSW stores the player leg as
            # `P2K24` and continuations as `2K24_B`, and the live value can
            # be either. An exact headcode match found nothing at all while
            # a perfectly good service sat in the table.
            code = headcode.strip()
            bare = code[1:] if (len(code) > 4 and code[0].upper() == "P"
                                and code[1].isdigit()) else code
            sql += (" AND (s.headcode = ? COLLATE NOCASE"
                    " OR s.service_name = ? COLLATE NOCASE"
                    " OR s.headcode = ? COLLATE NOCASE"
                    " OR s.service_name = ? COLLATE NOCASE)")
            params += [code, code, bare, "P" + bare]
        if route_key:
            sql += " AND s.route_key = ?"
            params.append(route_key)
        rows = [dict(r) for r in conn.execute(sql, params)]

        if not rows and headcode:
            # Last resort: the live value CONTAINS a stored code, e.g. a
            # display string like "2K24 Edinburgh - Leven". Reported to the
            # caller so a loose match is never mistaken for a clean one.
            like = f"%{headcode.strip()}%"
            rows = [dict(r) for r in conn.execute(
                sql.replace(
                    " AND (s.headcode = ? COLLATE NOCASE"
                    " OR s.service_name = ? COLLATE NOCASE"
                    " OR s.headcode = ? COLLATE NOCASE"
                    " OR s.service_name = ? COLLATE NOCASE)",
                    " AND (? LIKE '%' || s.headcode || '%'"
                    " OR s.headcode LIKE ?)"),
                [p for p in params
                 if p not in (headcode.strip(), bare, "P" + bare)]
                + [headcode.strip(), like])]
            for r in rows:
                r["loose_match"] = True
        if not rows:
            return None
        # A caller that supplied a headcode gets services with THAT headcode
        # or nothing. Falling back to a near-time match across every service
        # is how an unrelated working appeared while a real one was being
        # driven.

        def to_secs(t):
            if not t:
                return None
            try:
                h, m, s = (int(x) for x in t.split(":"))
                return h * 3600 + m * 60 + s
            except Exception:
                return None

        def score(row):
            # Lower is better.
            s = 0
            if row.get("role") == "ai_continuation":
                s += 100                     # the player is not driving this
            if not row.get("n_calls"):
                s += 500                     # nothing to show
            if near_time is not None:
                first, last = to_secs(row.get("first_time")), to_secs(row.get("last_time"))
                if first is not None and last is not None:
                    if first <= near_time <= last:
                        return s              # running now - the best answer
                    # otherwise prefer the closest start
                    s += min(abs(near_time - first), abs(near_time - last)) // 60
                else:
                    s += 300
            return s

        rows.sort(key=score)
        best = rows[0]
        best["calls"] = [dict(c) for c in conn.execute(
            "SELECT * FROM extracted_calls WHERE service_id=? ORDER BY call_order",
            (best["id"],))]
        best["alternatives"] = len(rows) - 1
        return best
    finally:
        conn.close()


def list_services(route_key=None, limit=400, with_calls=False):
    """Services for a route, for the picker when the game is not running."""
    init_extracted_tables()
    conn = _connect()
    try:
        sql = ("SELECT s.*, (SELECT COUNT(*) FROM extracted_calls c "
               "WHERE c.service_id=s.id) AS n_calls FROM extracted_services s")
        params = []
        if route_key:
            sql += " WHERE s.route_key = ?"
            params.append(route_key)
        sql += " ORDER BY s.first_time IS NULL, s.first_time LIMIT ?"
        params.append(limit)
        rows = [dict(r) for r in conn.execute(sql, params)]
        if with_calls:
            for r in rows:
                r["calls"] = [dict(c) for c in conn.execute(
                    "SELECT * FROM extracted_calls WHERE service_id=? "
                    "ORDER BY call_order", (r["id"],))]
        routes = [r[0] for r in conn.execute(
            "SELECT DISTINCT route_key FROM extracted_services ORDER BY route_key")]
        return {"services": rows, "service_count": len(rows), "routes": routes}
    finally:
        conn.close()
