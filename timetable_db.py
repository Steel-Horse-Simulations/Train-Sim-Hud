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
