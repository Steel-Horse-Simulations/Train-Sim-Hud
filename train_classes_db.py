"""
Imported train-class database (metadata + thumbnail references).

Populated two ways:
1. In bulk from the other TSW HUD app's own `train_classes` table (via
   import_train_class()) — real per-class data: thumbnail image path,
   livery/operator, speed, power type, manufacturer, etc. Only picks up
   whatever that other app has already catalogued, which for a
   newly-released loco can lag behind until someone runs its own
   extraction again.
2. LIVE, the moment this app's own Dashboard detects a loco via the real
   game API (via record_live_sighting()) — catches brand new content
   immediately, even before the other app's catalog has caught up, with
   whatever limited info the live API can give (name, roughly a max speed
   if available). No thumbnail/livery/manufacturer/UK-classification from
   this path — those only ever come from the real catalog import.

RECONCILIATION: a live-detected row has source_id=NULL (there's no
"other app" id for something it hasn't catalogued yet). If/when a real
catalog import later finds a matching name, import_train_class() adopts
that existing placeholder row (fills in source_id + all the rich fields)
rather than creating a duplicate — so a "new loco I just bought" entry
seamlessly turns into a fully-detailed one once the wider catalog catches
up, without ending up as two separate rows for the same loco.

UK-only for now, everything else quietly kept but hidden:
Per project decision, non-UK entries from the CATALOG import are NOT
discarded — they're imported with is_uk=0 / is_visible=0 so they're
invisible in the UI for now but available to switch on later without a
re-import. Live-detected entries are different on purpose: they default to
is_visible=1 immediately regardless of unknown UK status, since the whole
point of live detection is "let me see the new thing I just bought right
away" — there's no sensible "hide it by default" here. `is_visible` is
independently editable either way, so the user can always change either.

EDITABLE VS PROTECTED COLUMNS
Same pattern as timetable_db.py: id and source_id (the other app's
train_classes.id, kept for de-dup on re-import) are protected. times_seen
is also protected (a stat, not user data). Everything else — including
is_uk and is_visible — is editable, enforced by update_train_class() only
writing columns in EDITABLE_FIELDS.
"""
import json
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "train_classes.db")

EDITABLE_FIELDS = {
    "display_name", "livery_id", "livery_name", "typical_length_m", "typical_car_count",
    "is_electric", "is_steam", "is_diesel", "electrification_types",
    "max_speed_kph", "max_speed_mph", "max_speed_override_mph", "dial_max_override_mph",
    "max_power_kw", "manufacturer_name", "engine_description", "type_description",
    "vehicle_category", "thumbnail_path", "rail_vehicle_class",
    "is_drivable", "powered_axle_count", "is_uk", "is_visible", "notes",
    "group_id", "subclass_id",
}

# Known Trains v2: the field-completion checklist the status dot is based
# on, per the approved design (7 fields, percentage-based so it's easy to
# extend later without changing the colour thresholds themselves).
COMPLETION_FIELDS = ["display_name", "livery_name", "livery_id", "group_id", "subclass_id", "power_set", "thumbnail_path"]

# Electrification sub-options, confirmed accurate via research when this
# was designed - 6.25kV AC deliberately excluded as obsolete since the 1980s.
ELECTRIFICATION_TYPES = ["25kv_ac_overhead", "750v_dc_third_rail", "630v_dc_fourth_rail"]

KPH_TO_MPH = 0.621371
MS_TO_MPH = 2.23694


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
            CREATE TABLE IF NOT EXISTS train_classes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER UNIQUE,
                source_name TEXT NOT NULL,
                display_name TEXT,
                livery_id TEXT,
                typical_length_m REAL,
                typical_car_count INTEGER,
                is_electric INTEGER,
                max_speed_kph REAL,
                max_speed_mph REAL,
                max_power_kw REAL,
                manufacturer_name TEXT,
                engine_description TEXT,
                type_description TEXT,
                vehicle_category TEXT,
                thumbnail_path TEXT,
                rail_vehicle_class TEXT,
                is_drivable INTEGER,
                powered_axle_count INTEGER,
                is_uk INTEGER NOT NULL DEFAULT 0,
                is_visible INTEGER NOT NULL DEFAULT 0,
                times_seen INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                imported_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_train_classes_visible ON train_classes(is_visible);
            CREATE INDEX IF NOT EXISTS idx_train_classes_updated ON train_classes(updated_at);

            CREATE TABLE IF NOT EXISTS loco_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                default_max_speed_mph REAL,
                default_dial_max_mph REAL,
                hud_panels TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS loco_subclasses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL REFERENCES loco_groups(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                max_speed_override_mph REAL,
                dial_max_override_mph REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(group_id, name)
            );
            CREATE INDEX IF NOT EXISTS idx_subclasses_group ON loco_subclasses(group_id);
        """)
        conn.commit()

        # Migration: add the new Known Trains v2 columns to the EXISTING
        # train_classes table via ALTER TABLE, never CREATE/DROP - this
        # runs against a database that may already have real imported and
        # live-detected data in it, which must never be lost. Idempotent -
        # safe to call on every startup, only adds what's actually missing.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(train_classes)")}
        new_columns = {
            "livery_name": "TEXT",
            "is_steam": "INTEGER",
            "is_diesel": "INTEGER",
            "electrification_types": "TEXT",
            "max_speed_override_mph": "REAL",
            "dial_max_override_mph": "REAL",
            "group_id": "INTEGER REFERENCES loco_groups(id) ON DELETE SET NULL",
            "subclass_id": "INTEGER REFERENCES loco_subclasses(id) ON DELETE SET NULL",
        }
        for col, col_type in new_columns.items():
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE train_classes ADD COLUMN {col} {col_type}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_train_classes_group ON train_classes(group_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_train_classes_subclass ON train_classes(subclass_id)")
        conn.commit()
    finally:
        conn.close()


def clear_all():
    conn = _connect()
    try:
        conn.execute("DELETE FROM train_classes")
        conn.commit()
    finally:
        conn.close()


def import_train_class(source_id, source_name, is_uk, **fields):
    """Insert or update-in-place (keyed on source_id) one train class from
    the source database. is_visible defaults to is_uk on first import but
    is independently editable afterwards — a re-import won't silently
    re-hide something the user chose to surface.

    RECONCILIATION: if no row exists with this source_id, but a
    live-detected placeholder row exists (source_id IS NULL) with a
    matching source_name, that row is adopted — its source_id and all the
    rich catalog fields get filled in, but its existing id, times_seen,
    display_name/notes/is_visible (if the user already touched them) are
    preserved rather than creating a second, duplicate row for the same
    loco."""
    now = datetime.now().isoformat(timespec="seconds")
    max_speed_kph = fields.get("max_speed_kph")
    max_speed_mph = round(max_speed_kph * KPH_TO_MPH, 1) if max_speed_kph is not None else None

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM train_classes WHERE source_id = ?", (source_id,))
        existing = cur.fetchone()

        placeholder = None
        if not existing:
            cur.execute(
                "SELECT id FROM train_classes WHERE source_id IS NULL AND source_name = ? COLLATE NOCASE",
                (source_name,),
            )
            placeholder = cur.fetchone()

        if existing or placeholder:
            # Re-import (existing) or claim-a-placeholder (adopt): either
            # way, refresh source-derived fields but never touch
            # is_visible/display_name/notes, which the user may have
            # already hand-edited. updated_at DOES bump - this is a real
            # change to the source-derived fields, so sync needs to know.
            target_id = existing["id"] if existing else placeholder["id"]
            cur.execute(
                """UPDATE train_classes SET
                    source_id = ?, source_name = ?, livery_id = ?, typical_length_m = ?, typical_car_count = ?,
                    is_electric = ?, max_speed_kph = ?, max_speed_mph = ?, max_power_kw = ?,
                    manufacturer_name = ?, engine_description = ?, type_description = ?,
                    vehicle_category = ?, thumbnail_path = ?, rail_vehicle_class = ?,
                    is_drivable = ?, powered_axle_count = ?, is_uk = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    source_id, source_name, fields.get("livery_id"), fields.get("typical_length_m"),
                    fields.get("typical_car_count"), fields.get("is_electric"), max_speed_kph,
                    max_speed_mph, fields.get("max_power_kw"), fields.get("manufacturer_name"),
                    fields.get("engine_description"), fields.get("type_description"),
                    fields.get("vehicle_category"), fields.get("thumbnail_path"),
                    fields.get("rail_vehicle_class"), fields.get("is_drivable"),
                    fields.get("powered_axle_count"), int(bool(is_uk)), now, target_id,
                ),
            )
        else:
            cur.execute(
                """INSERT INTO train_classes
                    (source_id, source_name, display_name, livery_id, typical_length_m, typical_car_count,
                     is_electric, max_speed_kph, max_speed_mph, max_power_kw, manufacturer_name,
                     engine_description, type_description, vehicle_category, thumbnail_path,
                     rail_vehicle_class, is_drivable, powered_axle_count, is_uk, is_visible, imported_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_id, source_name, source_name, fields.get("livery_id"),
                    fields.get("typical_length_m"), fields.get("typical_car_count"),
                    fields.get("is_electric"), max_speed_kph, max_speed_mph, fields.get("max_power_kw"),
                    fields.get("manufacturer_name"), fields.get("engine_description"),
                    fields.get("type_description"), fields.get("vehicle_category"),
                    fields.get("thumbnail_path"), fields.get("rail_vehicle_class"),
                    fields.get("is_drivable"), fields.get("powered_axle_count"),
                    int(bool(is_uk)), int(bool(is_uk)), now, now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def record_live_sighting(raw_object_class, clean_name=None, formation_max_speed_ms=None):
    """Called live, every time the Dashboard detects a loco via the real
    game API (mirrors loco_profiles.record_sighting(), but feeds this
    newer catalog too). If this class has never been seen before (by
    either this live path OR a prior catalog import), creates a new,
    immediately-visible placeholder row with source_id=NULL — so a
    brand-new loco (e.g. a just-released DLC train the other app hasn't
    catalogued yet) shows up in the Train Classes page right away, not
    hidden pending a UK/catalog check the way a fresh catalog import would
    default to. Just bumps times_seen if this class is already known,
    whether that's from a prior sighting or a full catalog import."""
    name = (clean_name or raw_object_class or "").strip()
    if not name:
        return

    now = datetime.now().isoformat(timespec="seconds")
    max_speed_mph = round(formation_max_speed_ms * MS_TO_MPH, 1) if formation_max_speed_ms else None

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, times_seen FROM train_classes WHERE source_name = ? COLLATE NOCASE", (name,))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE train_classes SET times_seen = ?, updated_at = ? WHERE id = ?",
                (row["times_seen"] + 1, now, row["id"]),
            )
        else:
            cur.execute(
                """INSERT INTO train_classes
                    (source_id, source_name, display_name, max_speed_mph, is_uk, is_visible,
                     times_seen, imported_at, updated_at)
                   VALUES (NULL, ?, ?, ?, 0, 1, 1, ?, ?)""",
                (name, name, max_speed_mph, now, now),
            )
        conn.commit()
    finally:
        conn.close()


def list_train_classes(visible_only=True, query=None):
    conn = _connect()
    try:
        sql = "SELECT * FROM train_classes"
        clauses, params = [], []
        if visible_only:
            clauses.append("is_visible = 1")
        if query:
            clauses.append("(source_name LIKE ? OR display_name LIKE ?)")
            params += [f"%{query}%", f"%{query}%"]
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY display_name"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_train_class(train_class_id):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM train_classes WHERE id = ?", (train_class_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_train_class(train_class_id, fields, client_updated_at=None):
    """Only ever writes columns in EDITABLE_FIELDS — id and source_id stay
    protected so re-imports can always find the right row to update.

    client_updated_at=None (normal local edit): always applies, updated_at
    set to now. client_updated_at=<timestamp> (synced from tablet): only
    applies if newer than the row's current updated_at (last-write-wins);
    returns False if rejected as stale, same convention as timetable_db.py."""
    safe_fields = {k: v for k, v in fields.items() if k in EDITABLE_FIELDS}
    if not safe_fields:
        return False
    conn = _connect()
    try:
        if client_updated_at is not None:
            current = conn.execute("SELECT updated_at FROM train_classes WHERE id = ?", (train_class_id,)).fetchone()
            if current is None:
                return False
            if client_updated_at <= current["updated_at"]:
                return False
            new_updated_at = client_updated_at
        else:
            new_updated_at = datetime.now().isoformat(timespec="seconds")

        set_clause = ", ".join(f"{col} = ?" for col in safe_fields) + ", updated_at = ?"
        params = list(safe_fields.values()) + [new_updated_at, train_class_id]
        conn.execute(f"UPDATE train_classes SET {set_clause} WHERE id = ?", params)
        conn.commit()
        return True
    finally:
        conn.close()


def get_changes_since(since_timestamp, after_id=0, limit=300, visible_only=False):
    """For the pull side of sync. visible_only=False by default here (unlike
    list_train_classes) since a tablet that's already had a non-UK class
    manually surfaced should keep receiving its updates too.

    Paginated the same safe way as timetable_db.get_changes_since: collect
    the SET of matching ids first (cheap, using the fixed since_timestamp),
    sort it, then slice with a simple `id > after_id` cursor. NOT a single
    SQL query mixing a fixed since_timestamp with a moving id cursor via
    OR - that was tried first and had a real bug, caught by testing:
    `updated_at > since_timestamp` alone already matches every row once
    you're past the very first page, making the OR's id-cursor clause
    completely ineffective and returning the same first page forever. A
    bulk catalog import can easily give thousands of rows the exact same
    second-precision timestamp, which is exactly what exposed this.

    Returns (page_of_rows, has_more) - caller should keep calling with
    after_id set to the last id seen until has_more is False."""
    conn = _connect()
    try:
        id_sql = "SELECT id FROM train_classes WHERE updated_at > ?"
        id_params = [since_timestamp]
        if visible_only:
            id_sql += " AND is_visible = 1"
        all_ids = sorted(row["id"] for row in conn.execute(id_sql, id_params))

        page_ids = [i for i in all_ids if i > after_id][: limit + 1]
        has_more = len(page_ids) > limit
        page_ids = page_ids[:limit]

        if not page_ids:
            return [], False
        placeholders = ",".join("?" for _ in page_ids)
        rows = conn.execute(
            f"SELECT * FROM train_classes WHERE id IN ({placeholders}) ORDER BY id ASC", page_ids
        ).fetchall()
        return [dict(r) for r in rows], has_more
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Known Trains v2: groups, subclasses, speed resolution, status/power
# helpers. Builds on the existing train_classes table rather than a
# separate parallel catalog - a train_classes row can optionally belong
# to a group and (within that group) a subclass, per the approved
# hierarchy design.
# ---------------------------------------------------------------------

def create_group(name, default_max_speed_mph=None, default_dial_max_mph=None, hud_panels=None):
    now = datetime.now().isoformat(timespec="seconds")
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO loco_groups (name, default_max_speed_mph, default_dial_max_mph, hud_panels, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, default_max_speed_mph, default_dial_max_mph, json.dumps(hud_panels or []), now, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


GROUP_EDITABLE_FIELDS = {"name", "default_max_speed_mph", "default_dial_max_mph", "hud_panels"}
SUBCLASS_EDITABLE_FIELDS = {"name", "max_speed_override_mph", "dial_max_override_mph"}


def update_group(group_id, fields):
    safe = {k: v for k, v in fields.items() if k in GROUP_EDITABLE_FIELDS}
    if not safe:
        return False
    if "hud_panels" in safe:
        safe["hud_panels"] = json.dumps(safe["hud_panels"] or [])
    conn = _connect()
    try:
        safe["updated_at"] = datetime.now().isoformat(timespec="seconds")
        set_clause = ", ".join(f"{col} = ?" for col in safe)
        conn.execute(f"UPDATE loco_groups SET {set_clause} WHERE id = ?", list(safe.values()) + [group_id])
        conn.commit()
        return True
    finally:
        conn.close()


def get_group(group_id):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM loco_groups WHERE id = ?", (group_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["hud_panels"] = json.loads(result["hud_panels"] or "[]")
        return result
    finally:
        conn.close()


def list_groups():
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM loco_groups ORDER BY name").fetchall()
        results = []
        for row in rows:
            g = dict(row)
            g["hud_panels"] = json.loads(g["hud_panels"] or "[]")
            results.append(g)
        return results
    finally:
        conn.close()


def create_subclass(group_id, name, max_speed_override_mph=None, dial_max_override_mph=None):
    now = datetime.now().isoformat(timespec="seconds")
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO loco_subclasses (group_id, name, max_speed_override_mph, dial_max_override_mph, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (group_id, name, max_speed_override_mph, dial_max_override_mph, now, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_subclass(subclass_id, fields):
    safe = {k: v for k, v in fields.items() if k in SUBCLASS_EDITABLE_FIELDS}
    if not safe:
        return False
    conn = _connect()
    try:
        safe["updated_at"] = datetime.now().isoformat(timespec="seconds")
        set_clause = ", ".join(f"{col} = ?" for col in safe)
        conn.execute(f"UPDATE loco_subclasses SET {set_clause} WHERE id = ?", list(safe.values()) + [subclass_id])
        conn.commit()
        return True
    finally:
        conn.close()


def list_subclasses(group_id):
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM loco_subclasses WHERE group_id = ? ORDER BY name", (group_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def resolve_speeds(train_class_row, group=None, subclass=None):
    """Speed resolution order, per the approved design: individual override
    -> subclass override -> group default -> hardcoded fallback (100mph).
    Same chain independently for both max speed and dial max. Pass in
    already-fetched group/subclass dicts if you have them (e.g. rendering
    a list) to avoid N+1 queries; otherwise this fetches them itself."""
    if group is None and train_class_row.get("group_id"):
        group = get_group(train_class_row["group_id"])
    if subclass is None and train_class_row.get("subclass_id"):
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM loco_subclasses WHERE id = ?", (train_class_row["subclass_id"],)).fetchone()
            subclass = dict(row) if row else None
        finally:
            conn.close()

    def pick(individual_val, subclass_val, group_val, fallback):
        if individual_val is not None:
            return individual_val, "individual"
        if subclass_val is not None:
            return subclass_val, "subclass"
        if group_val is not None:
            return group_val, "group"
        return fallback, "fallback"

    max_speed, max_speed_source = pick(
        train_class_row.get("max_speed_override_mph"),
        subclass.get("max_speed_override_mph") if subclass else None,
        group.get("default_max_speed_mph") if group else None,
        train_class_row.get("max_speed_mph") or 100.0,  # catalog-sourced real speed is a reasonable final fallback before the hardcoded default
    )
    dial_max, dial_max_source = pick(
        train_class_row.get("dial_max_override_mph"),
        subclass.get("dial_max_override_mph") if subclass else None,
        group.get("default_dial_max_mph") if group else None,
        max_speed,  # if nothing set a dial max at all, just use the resolved max speed itself
    )
    return {
        "max_speed_mph": max_speed, "max_speed_source": max_speed_source,
        "dial_max_mph": dial_max, "dial_max_source": dial_max_source,
    }


def compute_power_label(is_steam, is_diesel, is_electric):
    """Bi-Mode/Tri-Mode label, computed live from the actual power-type
    selection - matches the approved edit-page preview's tested behaviour."""
    types = [bool(is_steam), bool(is_diesel), bool(is_electric)]
    count = sum(types)
    names = []
    if is_steam: names.append("Steam")
    if is_diesel: names.append("Diesel")
    if is_electric: names.append("Electric")
    if count == 0:
        return None
    if count == 1:
        return names[0]
    if count == 2:
        return "Bi-Mode (" + " + ".join(names) + ")"
    return "Tri-Mode (" + " + ".join(names) + ")"


def compute_completion(train_class_row):
    """Status dot: percentage of COMPLETION_FIELDS that are filled in,
    mapped to red/amber/yellow/green per the approved thresholds.
    "power_set" counts as filled if at least one of steam/diesel/electric
    is set, since there's no single 'power' column."""
    filled = 0
    for field in COMPLETION_FIELDS:
        if field == "power_set":
            if train_class_row.get("is_steam") or train_class_row.get("is_diesel") or train_class_row.get("is_electric"):
                filled += 1
            continue
        if train_class_row.get(field):
            filled += 1
    percent = filled / len(COMPLETION_FIELDS)
    if percent >= 1.0:
        color = "green"
    elif percent >= 0.5:
        color = "yellow"
    elif percent > 0:
        color = "amber"
    else:
        color = "red"
    return {"percent": round(percent * 100), "color": color}


def needs_attention():
    """Anything missing a display name, OR not assigned to a group, OR
    not assigned to a subclass - per the approved design, not just "never
    seen before"."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM train_classes WHERE display_name IS NULL OR display_name = '' "
            "OR group_id IS NULL OR subclass_id IS NULL "
            "ORDER BY times_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
