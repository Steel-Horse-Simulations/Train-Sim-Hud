"""
Known-locomotives database.

Solves two things:
1. The "sometimes shows a clean class name, sometimes shows a messy raw
   string" problem - CurrentFormation/0.Function.IS_GetVehicleInfo gives a
   clean name (e.g. "390/0") on some locos but not others; when it fails we
   fall back to the raw ObjectClass string (e.g.
   "RVM_EMK_AWC_Class390_11_DMFK_C"). This DB remembers the raw string as a
   stable key, and once we've EVER seen a clean name for it, we keep using
   that clean name even on future sightings where only the raw fallback is
   available again.
2. Per-class HUD customisation - a custom speedometer max (a fixed 100mph
   ring is useless for a Class 08 topping out at 15mph, and just as useless
   for anything genuinely fast), plus a `panel_prefs_json` column reserved
   for future per-class panel visibility once those panels (air pressure,
   ammeter, etc.) exist - schema-ready now, not wired to anything yet.
"""
import json
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "loco_profiles.db")

DEFAULT_MAX_SPEED_MPH = 100.0
MS_TO_MPH = 2.23694


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS loco_profiles (
                raw_object_class TEXT PRIMARY KEY,
                clean_name TEXT,
                max_speed_mph REAL,
                suggested_max_speed_mph REAL,
                times_seen INTEGER NOT NULL DEFAULT 0,
                first_seen_at TEXT,
                last_seen_at TEXT,
                panel_prefs_json TEXT
            );
        """)
        conn.commit()
    finally:
        conn.close()


def record_sighting(raw_object_class, clean_name=None, formation_max_speed_ms=None):
    """Called on each loco identity check. Learns a clean name the first
    time it's seen for this raw key and keeps it even if a later sighting
    only has the raw fallback again - a live clean_name=None here never
    erases a previously learned name."""
    if not raw_object_class:
        return
    now = datetime.now().isoformat(timespec="seconds")
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM loco_profiles WHERE raw_object_class = ?", (raw_object_class,))
        existing = cur.fetchone()

        suggested = None
        if formation_max_speed_ms and formation_max_speed_ms < 1e6:  # filter the FLT_MAX "unset" sentinel
            suggested = round(formation_max_speed_ms * MS_TO_MPH, 1)

        if existing is None:
            cur.execute(
                "INSERT INTO loco_profiles "
                "(raw_object_class, clean_name, suggested_max_speed_mph, times_seen, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (raw_object_class, clean_name, suggested, now, now),
            )
        else:
            new_clean = clean_name if clean_name else existing["clean_name"]
            new_suggested = suggested if suggested is not None else existing["suggested_max_speed_mph"]
            cur.execute(
                "UPDATE loco_profiles SET clean_name = ?, suggested_max_speed_mph = ?, "
                "times_seen = times_seen + 1, last_seen_at = ? WHERE raw_object_class = ?",
                (new_clean, new_suggested, now, raw_object_class),
            )
        conn.commit()
    finally:
        conn.close()


def get_profile(raw_object_class):
    if not raw_object_class:
        return None
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM loco_profiles WHERE raw_object_class = ?", (raw_object_class,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_effective_max_speed_mph(raw_object_class):
    """User override > learned suggestion (from formationMaxSpeed) > the
    old fixed default - in that priority order."""
    profile = get_profile(raw_object_class)
    if profile:
        if profile["max_speed_mph"] is not None:
            return profile["max_speed_mph"]
        if profile["suggested_max_speed_mph"] is not None:
            return profile["suggested_max_speed_mph"]
    return DEFAULT_MAX_SPEED_MPH


def set_max_speed(raw_object_class, mph):
    if not raw_object_class:
        return False
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM loco_profiles WHERE raw_object_class = ?", (raw_object_class,))
        if not cur.fetchone():
            return False
        cur.execute(
            "UPDATE loco_profiles SET max_speed_mph = ? WHERE raw_object_class = ?",
            (mph, raw_object_class),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_profiles():
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM loco_profiles ORDER BY last_seen_at DESC")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
