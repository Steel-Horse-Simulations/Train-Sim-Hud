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
    "group_id", "subclass_id", "photo_override", "speedometer",
}

# Known Trains v2: the field-completion checklist the status dot is based
# on, per the approved design (7 fields, percentage-based so it's easy to
# extend later without changing the colour thresholds themselves).
COMPLETION_FIELDS = ["display_name", "livery_name", "livery_id", "group_id", "power_set"]

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

            -- "Group" here is the higher-level family that several Classes
            -- (loco_groups rows) belong to - e.g. Class 801, 802 and 805 all
            -- belonging to the "Class 8xx" family. Deliberately a separate
            -- table from loco_groups (which is the Class-level entity, shown
            -- to the user as "Classes") to avoid disturbing the existing
            -- class/subclass logic.
            CREATE TABLE IF NOT EXISTS class_families (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        conn.commit()

        # Migration: add family_id to loco_groups so an existing Class can be
        # assigned into a Group (family) without disturbing existing rows.
        existing_group_cols = {row["name"] for row in conn.execute("PRAGMA table_info(loco_groups)")}
        if "family_id" not in existing_group_cols:
            conn.execute("ALTER TABLE loco_groups ADD COLUMN family_id INTEGER REFERENCES class_families(id) ON DELETE SET NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_groups_family ON loco_groups(family_id)")

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

        # Operators tables
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS operators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                short_code TEXT,
                logo_path TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS operator_liveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operator_id INTEGER NOT NULL REFERENCES operators(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                code TEXT NOT NULL,
                colour TEXT,
                is_default INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            );
        """)

        # Migration: add colour to operators table
        existing_op_cols = {row["name"] for row in conn.execute("PRAGMA table_info(operators)")}
        if "colour" not in existing_op_cols:
            conn.execute("ALTER TABLE operators ADD COLUMN colour TEXT")

        # Migration: add photo_override and speedometer to train_classes
        existing_cols2 = {row["name"] for row in conn.execute("PRAGMA table_info(train_classes)")}
        extra_cols = {"photo_override": "TEXT", "speedometer": "TEXT"}
        for col, col_type in extra_cols.items():
            if col not in existing_cols2:
                conn.execute(f"ALTER TABLE train_classes ADD COLUMN {col} {col_type}")

        # Aliases: an ungrouped train class can be merged into an existing
        # (target) train class. The merged row is deleted; this table
        # remembers its raw identifiers so future live sightings are
        # attributed to the target instead of recreating a stray row.
        # subclass_id (optional) lets this specific variant resolve its own
        # speed via the target's group/subclass even though every other
        # attribute (name, livery, photo, etc.) comes from the target.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS train_class_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT,
                source_name TEXT,
                target_class_id INTEGER NOT NULL REFERENCES train_classes(id) ON DELETE CASCADE,
                subclass_id INTEGER REFERENCES loco_subclasses(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_alias_source_id ON train_class_aliases(source_id);
            CREATE INDEX IF NOT EXISTS idx_alias_source_name ON train_class_aliases(source_name);
        """)

        conn.commit()
    finally:
        conn.close()


# ---- Operator CRUD --------------------------------------------------------

def list_operators():
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM operators ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_operator(operator_id):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM operators WHERE id = ?", (operator_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_operator(name, short_code=None, logo_path=None):
    now = datetime.now().isoformat(timespec="seconds")
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO operators (name, short_code, logo_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (name, short_code, logo_path, now, now)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_operator(operator_id, fields):
    allowed = {"name", "short_code", "logo_path", "colour"}
    safe = {k: v for k, v in fields.items() if k in allowed}
    if not safe:
        return False
    safe["updated_at"] = datetime.now().isoformat(timespec="seconds")
    conn = _connect()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in safe)
        conn.execute(f"UPDATE operators SET {set_clause} WHERE id = ?", list(safe.values()) + [operator_id])
        conn.commit()
        return True
    finally:
        conn.close()


def delete_operator(operator_id):
    conn = _connect()
    try:
        conn.execute("DELETE FROM operators WHERE id = ?", (operator_id,))
        conn.commit()
    finally:
        conn.close()


def list_liveries(operator_id):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM operator_liveries WHERE operator_id = ? ORDER BY is_default DESC, name",
            (operator_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_livery(operator_id, name, code, colour=None, is_default=0):
    now = datetime.now().isoformat(timespec="seconds")
    conn = _connect()
    try:
        if is_default:
            conn.execute("UPDATE operator_liveries SET is_default = 0 WHERE operator_id = ?", (operator_id,))
        cur = conn.execute(
            "INSERT INTO operator_liveries (operator_id, name, code, colour, is_default, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (operator_id, name, code, colour, 1 if is_default else 0, now, now)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_livery(livery_id, fields):
    allowed = {"name", "code", "colour", "is_default"}
    safe = {k: v for k, v in fields.items() if k in allowed}
    if not safe:
        return False
    safe["updated_at"] = datetime.now().isoformat(timespec="seconds")
    conn = _connect()
    try:
        if safe.get("is_default"):
            row = conn.execute("SELECT operator_id FROM operator_liveries WHERE id = ?", (livery_id,)).fetchone()
            if row:
                conn.execute("UPDATE operator_liveries SET is_default = 0 WHERE operator_id = ?", (row["operator_id"],))
        set_clause = ", ".join(f"{k} = ?" for k in safe)
        conn.execute(f"UPDATE operator_liveries SET {set_clause} WHERE id = ?", list(safe.values()) + [livery_id])
        conn.commit()
        return True
    finally:
        conn.close()


def delete_livery(livery_id):
    conn = _connect()
    try:
        conn.execute("DELETE FROM operator_liveries WHERE id = ?", (livery_id,))
        conn.commit()
    finally:
        conn.close()


# ---- Group delete ---------------------------------------------------------

def delete_group(group_id):
    conn = _connect()
    try:
        conn.execute("DELETE FROM loco_groups WHERE id = ?", (group_id,))
        conn.commit()
    finally:
        conn.close()


def delete_subclass(subclass_id):
    conn = _connect()
    try:
        conn.execute("DELETE FROM loco_subclasses WHERE id = ?", (subclass_id,))
        conn.commit()
    finally:
        conn.close()


def clear_all():
    """Wipes every piece of Known Trains data: sighted/imported train
    classes, groups, subclasses, families, operators, and liveries - a full
    reset. Also resets AUTOINCREMENT counters so new records start at id 1
    again."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM train_classes")
        conn.execute("DELETE FROM loco_subclasses")
        conn.execute("DELETE FROM loco_groups")
        conn.execute("DELETE FROM class_families")
        conn.execute("DELETE FROM operator_liveries")
        conn.execute("DELETE FROM operators")
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name IN "
            "('train_classes','loco_groups','loco_subclasses','class_families','operators','operator_liveries')"
        )
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
    whether that's from a prior sighting or a full catalog import.

    Deduplication searches by BOTH raw_object_class AND clean_name so that
    a train first recorded via its raw API key ("RVM_BR_Class170_C") and
    later seen again with a resolved clean name ("Class 170") does not
    create two separate rows in the database."""
    name = (clean_name or raw_object_class or "").strip()
    raw = (raw_object_class or "").strip()
    if not name:
        return

    now = datetime.now().isoformat(timespec="seconds")
    max_speed_mph = round(formation_max_speed_ms * MS_TO_MPH, 1) if formation_max_speed_ms else None

    conn = _connect()
    try:
        cur = conn.cursor()

        # Alias check: if this raw/clean name was merged into another train
        # class, redirect the sighting there instead of recreating a row.
        alias = None
        if raw:
            cur.execute("SELECT * FROM train_class_aliases WHERE source_name = ? COLLATE NOCASE", (raw,))
            alias = cur.fetchone()
        if not alias and name and name != raw:
            cur.execute("SELECT * FROM train_class_aliases WHERE source_name = ? COLLATE NOCASE", (name,))
            alias = cur.fetchone()
        if alias:
            cur.execute(
                "UPDATE train_classes SET times_seen = times_seen + 1, updated_at = ? WHERE id = ?",
                (now, alias["target_class_id"]),
            )
            conn.commit()
            return

        # Search by raw key first (most stable), then by clean name as fallback.
        # This prevents a second row being created when TSW returns a clean name
        # on a later poll for a train whose first sighting only had the raw key.
        row = None
        if raw:
            cur.execute("SELECT id, times_seen FROM train_classes WHERE source_name = ? COLLATE NOCASE", (raw,))
            row = cur.fetchone()
        if not row and name and name != raw:
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


def dedup_train_classes():
    """Merges duplicate train_classes rows. Two passes:
    1. Same display_name (case-insensitive) → keep highest times_seen row, sum counts.
    2. Raw-API-key rows (source_name has underscores, no spaces, display_name == source_name,
       meaning no human name was ever set) → delete if ANY clean-name row exists, since they
       are earlier sightings of the same loco before TSW returned a readable class name.
    Safe to call at startup — no-op if already clean."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, source_name, display_name, times_seen FROM train_classes ORDER BY id"
        ).fetchall()

        to_delete = []

        # Pass 1: same display_name
        seen_display = {}  # lower display_name -> primary id
        for row in rows:
            display = (row["display_name"] or row["source_name"] or "").strip()
            key = display.lower()
            if not key:
                continue
            if key in seen_display:
                to_delete.append((seen_display[key], row["id"], row["times_seen"]))
            else:
                seen_display[key] = row["id"]

        # Pass 2: raw-API-key rows where display_name was never set to anything readable
        # (source_name == display_name AND no spaces, has underscores → still raw)
        # Delete these if there are any other (clean-name) rows in the DB.
        non_deleted_ids = {r["id"] for r in rows} - {d[1] for d in to_delete}
        has_clean_rows = any(
            " " in (r["display_name"] or "") or
            (r["display_name"] and r["display_name"] != r["source_name"])
            for r in rows if r["id"] in non_deleted_ids
        )
        if has_clean_rows:
            for row in rows:
                if row["id"] not in non_deleted_ids:
                    continue
                sn = (row["source_name"] or "")
                dn = (row["display_name"] or "")
                # Raw key: no spaces, contains underscores, display_name still equals source_name
                if "_" in sn and " " not in sn and dn == sn:
                    to_delete.append((None, row["id"], row["times_seen"]))

        for primary_id, dupe_id, dupe_times in to_delete:
            if primary_id is not None:
                conn.execute(
                    "UPDATE train_classes SET times_seen = times_seen + ? WHERE id = ?",
                    (dupe_times, primary_id)
                )
            conn.execute("DELETE FROM train_classes WHERE id = ?", (dupe_id,))

        if to_delete:
            conn.commit()
        return len(to_delete)
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


def get_train_class_by_source_name(source_name):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM train_classes WHERE source_name = ?", (source_name,)).fetchone()
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


GROUP_EDITABLE_FIELDS = {"name", "default_max_speed_mph", "default_dial_max_mph", "hud_panels", "family_id"}
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


def get_subclass(subclass_id):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM loco_subclasses WHERE id = ?", (subclass_id,)).fetchone()
        return dict(row) if row else None
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


# ---------------------------------------------------------------------
# Class Families ("Groups" in the UI): a higher-level grouping that several
# Classes (loco_groups rows) belong to - e.g. Class 801, 802 and 805 all
# belonging to the "Class 8xx" family. A Class's own subclass logic is
# untouched; family_id on loco_groups just says which family (if any) that
# Class sits in.
# ---------------------------------------------------------------------

def create_family(name):
    now = datetime.now().isoformat(timespec="seconds")
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO class_families (name, created_at, updated_at) VALUES (?, ?, ?)",
            (name, now, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


FAMILY_EDITABLE_FIELDS = {"name"}


def update_family(family_id, fields):
    safe = {k: v for k, v in fields.items() if k in FAMILY_EDITABLE_FIELDS}
    if not safe:
        return False
    conn = _connect()
    try:
        safe["updated_at"] = datetime.now().isoformat(timespec="seconds")
        set_clause = ", ".join(f"{col} = ?" for col in safe)
        conn.execute(f"UPDATE class_families SET {set_clause} WHERE id = ?", list(safe.values()) + [family_id])
        conn.commit()
        return True
    finally:
        conn.close()


def delete_family(family_id):
    """Deletes the family. Member Classes are NOT deleted - their family_id
    is set to NULL automatically via ON DELETE SET NULL, so they just become
    ungrouped again."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM class_families WHERE id = ?", (family_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def get_family(family_id):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM class_families WHERE id = ?", (family_id,)).fetchone()
        if not row:
            return None
        family = dict(row)
        classes = conn.execute(
            "SELECT * FROM loco_groups WHERE family_id = ? ORDER BY name", (family_id,)
        ).fetchall()
        family["classes"] = [dict(c) for c in classes]
        return family
    finally:
        conn.close()


def list_families():
    """Every family, each with its member Classes nested in - the Groups
    page renders straight from this without extra per-family requests."""
    conn = _connect()
    try:
        families = [dict(r) for r in conn.execute("SELECT * FROM class_families ORDER BY name").fetchall()]
        classes = conn.execute("SELECT * FROM loco_groups WHERE family_id IS NOT NULL ORDER BY name").fetchall()
        by_family = {}
        for c in classes:
            c = dict(c)
            by_family.setdefault(c["family_id"], []).append(c)
        for f in families:
            f["classes"] = by_family.get(f["id"], [])
        return families
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
    """Anything missing one or more of the COMPLETION_FIELDS - i.e. anything
    that would not show a green completion dot on the Known Trains list."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM train_classes ORDER BY times_seen DESC").fetchall()
        result = []
        for r in rows:
            row = dict(r)
            status = compute_completion(row)
            if status["percent"] < 100:
                result.append(row)
        return result
    finally:
        conn.close()


# ---- Merge / aliasing ------------------------------------------------------

def merge_train_class_into(source_class_id, target_class_id, subclass_id=None):
    """Merges any train class into an existing (target) class - the source
    doesn't need to be ungrouped. The source row's raw identifiers are
    remembered in train_class_aliases so future live sightings are
    attributed to the target instead of recreating a stray row. The source
    row is then deleted; its historic times_seen count is folded into the
    target. subclass_id (optional) lets this specific variant resolve its
    own speed via the target's group even though every other attribute
    comes from the target."""
    if source_class_id == target_class_id:
        return False, "cannot merge a train into itself"

    conn = _connect()
    try:
        source = conn.execute("SELECT * FROM train_classes WHERE id = ?", (source_class_id,)).fetchone()
        target = conn.execute("SELECT * FROM train_classes WHERE id = ?", (target_class_id,)).fetchone()
        if not source:
            return False, "source train not found"
        if not target:
            return False, "target train not found"

        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO train_class_aliases (source_id, source_name, target_class_id, subclass_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(source["source_id"]) if source["source_id"] is not None else None,
             source["source_name"], target_class_id, subclass_id, now),
        )
        conn.execute(
            "UPDATE train_classes SET times_seen = times_seen + ?, updated_at = ? WHERE id = ?",
            (source["times_seen"] or 0, now, target_class_id),
        )
        conn.execute("DELETE FROM train_classes WHERE id = ?", (source_class_id,))
        conn.commit()
        return True, None
    finally:
        conn.close()


def list_aliases_for_target(target_class_id):
    """Every raw train that's currently merged into this target - i.e. what
    shows under "Merged into this train" on the Edit page, with a Remove
    button per row to un-merge it."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT a.*, s.name AS subclass_name FROM train_class_aliases a "
            "LEFT JOIN loco_subclasses s ON s.id = a.subclass_id "
            "WHERE a.target_class_id = ? ORDER BY a.source_name",
            (target_class_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_alias(alias_id):
    """Un-merges a previously-merged train. The alias link is removed so a
    future live sighting of that raw class creates its own train_classes row
    again instead of being folded into the target. Historic times_seen that
    was already folded into the target is not split back out."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM train_class_aliases WHERE id = ?", (alias_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def get_alias_for_raw(raw_object_class, clean_name=None):
    """Looks up whether the currently-detected loco is an alias merged into
    another train class. Returns the alias row (with target_class_id and
    optional subclass_id) or None. Used live so the HUD can resolve this
    specific variant's speed via its assigned subclass while every other
    attribute is drawn from the target class."""
    raw = (raw_object_class or "").strip()
    name = (clean_name or "").strip()
    if not raw and not name:
        return None
    conn = _connect()
    try:
        row = None
        if raw:
            row = conn.execute(
                "SELECT * FROM train_class_aliases WHERE source_name = ? COLLATE NOCASE", (raw,)
            ).fetchone()
        if not row and name and name != raw:
            row = conn.execute(
                "SELECT * FROM train_class_aliases WHERE source_name = ? COLLATE NOCASE", (name,)
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
