"""
TSW Hud
-----------------
A small local desktop app for Train Sim World's -HTTPAPI feature.

What it does:
  - Finds/reads the game's CommAPIKey.txt (auto-detect or manual folder picker)
  - Proxies requests to the game's local API (http://localhost:31270) so your
    HTML dashboard pages never need to touch the key or worry about CORS
  - Crawls the API's /info and /list endpoints to build a full map of every
    node and endpoint the game currently exposes, and lets you copy/save it
    as plain text
  - Renders custom dashboard pages (pages/*.html) inside a real Windows
    application window, with an Exit button that fully closes the app

Run with:  python app.py
"""

import json
import math
import os
import re
import socket
import sys
import threading
import time
from datetime import datetime

import requests
from flask import Flask, jsonify, request, send_from_directory

# --------------------------------------------------------------------------
# Paths / constants
# --------------------------------------------------------------------------

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Semantic versioning: MAJOR.MINOR.PATCH
# MAJOR = a big rework; MINOR = a real new feature; PATCH = a small fix/tweak.
# Shown on the Setup page and in diagnostics so it's never ambiguous whether
# an update actually took effect (editing app.py on disk does nothing until
# the whole app is fully closed and relaunched - a page refresh alone does
# not reload Python code).
APP_VERSION = "7.39.2"
PAGES_DIR = os.path.join(APP_DIR, "pages")

# Ordering rule for the Customisation tab: add new themes ABOVE 'slate'.
# 'slate' always stays second-to-last, 'rainbow' always stays last.
THEMES = ["purple", "green", "blue", "amber", "crimson", "teal", "rose", "slate", "rainbow"]
EXPORTS_DIR = os.path.join(APP_DIR, "exports")
DIAG_DIR = os.path.join(APP_DIR, "diagnostics")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")

DEFAULT_API_BASE = "http://127.0.0.1:31270"
KEY_FILENAMES = ["CommAPIKey.txt", "DTGCommKey.txt"]  # different guides use either name

# Common install locations for the key file across TSW versions.
# %USERPROFILE%\Documents\My Games\TrainSimWorldX\Saved\Config
def candidate_folders():
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents", "My Games")
    candidates = []
    for version in ["TrainSimWorld6", "TrainSimWorld5", "TrainSimWorld4", "TrainSimWorld3",
                     "TrainSimWorld2", "TrainSimWorld"]:
        candidates.append(os.path.join(docs, version, "Saved", "Config"))
    # OneDrive-redirected Documents is common on Windows too
    onedrive_docs = os.path.join(home, "OneDrive", "Documents", "My Games")
    for version in ["TrainSimWorld6", "TrainSimWorld5", "TrainSimWorld4", "TrainSimWorld3"]:
        candidates.append(os.path.join(onedrive_docs, version, "Saved", "Config"))
    return candidates


os.makedirs(EXPORTS_DIR, exist_ok=True)
os.makedirs(DIAG_DIR, exist_ok=True)

SERVER_PORT = int(os.environ.get("TSW_HUD_PORT", "5273"))


def _run_with_timeout(fn, timeout_seconds, default):
    """Run fn() in a background thread and give up after timeout_seconds.
    Used for OS calls like getaddrinfo() that can occasionally hang for a
    long time on machines with unusual network setups (VPNs especially),
    so a single slow lookup can never freeze the whole app."""
    box = {}

    def target():
        try:
            box["value"] = fn()
        except Exception:
            pass

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout_seconds)
    return box.get("value", default)


def get_lan_ips():
    """Return all local IPv4 addresses this PC currently has, best-guess-first.

    A PC can have several interfaces at once (Wi-Fi, Ethernet, a VPN's virtual
    adapter, etc). We collect all of them rather than trusting a single OS
    'default route' guess, because that guess sometimes picks a VPN adapter
    (commonly 10.x) instead of the real home Wi-Fi/LAN (commonly 192.168.x
    or 172.16-31.x). Home-LAN-shaped addresses are sorted first.

    Note: resolving the local hostname can occasionally be slow (some VPNs
    interfere with DNS), so that lookup is time-boxed and skipped if it
    doesn't return quickly - the fast socket-based method below still runs
    either way.
    """
    ips = set()

    def hostname_lookup():
        found = set()
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                found.add(ip)
        return found

    ips |= _run_with_timeout(hostname_lookup, timeout_seconds=1.0, default=set())

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            ips.add(ip)
        s.close()
    except Exception:
        pass

    def priority(ip):
        # Lower number = more likely to be the real home LAN, shown first.
        if ip.startswith("192.168."):
            return 0
        if ip.startswith("172."):
            second = ip.split(".")[1]
            if second.isdigit() and 16 <= int(second) <= 31:
                return 1
        if ip.startswith("10."):
            return 2  # common for both home routers and VPNs - ambiguous
        return 3

    return sorted(ips, key=lambda ip: (priority(ip), ip))


def get_lan_ip():
    ips = get_lan_ips()
    return ips[0] if ips else "127.0.0.1"

# --------------------------------------------------------------------------
# Config persistence
# --------------------------------------------------------------------------

def load_config():
    cfg = {
        "config_folder": "", "api_base": DEFAULT_API_BASE, "theme": "purple",
        "other_hud_db_path": "", "other_hud_db_auto_detected": False,
        "other_hud_images_path": "", "other_hud_sync_enabled": True,
        "last_import_row_count": -1, "last_import_at": "",
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    if cfg.get("theme") not in THEMES:
        cfg["theme"] = "purple"
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


CONFIG = load_config()

# --------------------------------------------------------------------------
# Key file helpers
# --------------------------------------------------------------------------

def find_key_file(folder):
    if not folder or not os.path.isdir(folder):
        return None
    for name in KEY_FILENAMES:
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            return p
    return None


def read_api_key():
    folder = CONFIG.get("config_folder", "")
    path = find_key_file(folder)
    if not path:
        return None, None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            key = f.read().strip()
        return key, path
    except Exception:
        return None, path


def auto_detect_folder():
    for c in candidate_folders():
        if find_key_file(c):
            return c
    return None


# --------------------------------------------------------------------------
# TSW API proxy helpers
# --------------------------------------------------------------------------

SESSION = requests.Session()
DEBUG_LOG_FILE = os.path.join(DIAG_DIR, "calls.log")
HEARTBEAT_FILE = os.path.join(DIAG_DIR, "heartbeat.log")
LAST_ERROR_FILE = os.path.join(DIAG_DIR, "last_error.log")
CRASH_LOG_FILE = os.path.join(DIAG_DIR, "crash.log")
_debug_log_lock = threading.Lock()


def log_call(label, duration, outcome):
    line = f"{datetime.now().strftime('%H:%M:%S')}  {duration*1000:6.0f}ms  {outcome:20s}  {label}\n"
    try:
        with _debug_log_lock:
            lines = []
            if os.path.exists(DEBUG_LOG_FILE):
                with open(DEBUG_LOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-300:]
            lines.append(line)
            with open(DEBUG_LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines)
    except Exception:
        pass


def api_headers():
    key, _ = read_api_key()
    if not key:
        return None
    return {"DTGCommKey": key}


def resolved_api_base():
    """'localhost' can resolve to both ::1 (IPv6) and 127.0.0.1 (IPv4). On some
    Windows setups the IPv6 attempt gets silently dropped rather than refused,
    so the OS waits out the full connect timeout before falling back to IPv4 -
    adding a consistent ~2s delay to every fresh connection. Talking to
    127.0.0.1 directly skips that resolution step entirely. This normalizes
    even an old saved config that still says 'localhost'."""
    base = CONFIG.get("api_base", DEFAULT_API_BASE) or DEFAULT_API_BASE
    return base.replace("localhost", "127.0.0.1")


def api_get(path, timeout=(2, 3)):
    headers = api_headers()
    if headers is None:
        return {"error": "no_key"}, 400
    base = resolved_api_base()
    url = f"{base}/{path.lstrip('/')}"
    start = time.time()
    try:
        r = SESSION.get(url, headers=headers, timeout=timeout)
        log_call(f"GET {url}", time.time() - start, f"status {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text}
        return body, r.status_code
    except requests.exceptions.ConnectionError:
        log_call(f"GET {url}", time.time() - start, "connection_failed")
        return {"error": "connection_failed",
                "detail": "Could not reach the game. Is TSW running with the "
                           "-HTTPAPI launch option, and are you in a session?"}, 502
    except requests.exceptions.Timeout:
        log_call(f"GET {url}", time.time() - start, "timeout")
        return {"error": "timeout"}, 504
    except Exception as e:
        log_call(f"GET {url}", time.time() - start, f"error: {e}")
        return {"error": "unexpected", "detail": str(e)}, 500


def api_patch(path, params):
    headers = api_headers()
    if headers is None:
        return {"error": "no_key"}, 400
    base = resolved_api_base()
    url = f"{base}/{path.lstrip('/')}"
    start = time.time()
    try:
        r = SESSION.patch(url, headers=headers, params=params, timeout=(2, 3))
        log_call(f"PATCH {url}", time.time() - start, f"status {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text}
        return body, r.status_code
    except Exception as e:
        log_call(f"PATCH {url}", time.time() - start, f"error: {e}")
        return {"error": "unexpected", "detail": str(e)}, 500


# --------------------------------------------------------------------------
# Discovery / crawl
#
# The TSW HTTP API exposes:
#   GET /info               -> API meta + list of root HTTP routes
#   GET /list                -> top-level Nodes[] and Endpoints[]
#   GET /list/{NodePath}     -> Nodes[]/Endpoints[] under that node
#   GET /get/{NodePath}.{EndpointName}  -> the actual value
#
# We walk the node tree breadth-first, recording every node and endpoint we
# find, and build the dotted/sloshed path you'd use to call /get or /set on
# each endpoint. This is a live map of exactly what YOUR copy of the game
# currently exposes (it varies by loco/route), which is why it's more
# reliable than any static hardcoded list.
# --------------------------------------------------------------------------

MAX_NODES_TO_VISIT = 400
CRAWL_TIMEOUT_SECONDS = 45


def _get_ci(d, keys, default=None):
    """Looks up any of `keys` in dict `d`, case-insensitively. Different TSW
    builds/versions have been observed using different casing for the same
    field (e.g. 'Nodes' vs 'nodes'), which was silently causing Discovery
    scans to come back empty - not because nothing was there, but because
    we were looking for the wrong-cased key."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d:
            return d[k]
    lower_map = {k.lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in lower_map:
            return lower_map[k.lower()]
    return default


def _safe_name(entry):
    """Nodes/endpoints are documented as {"Name": "..."} objects, but be
    defensive in case a given build returns plain strings instead, or uses
    different casing for the key."""
    if isinstance(entry, dict):
        return _get_ci(entry, ["Name", "name"])
    if isinstance(entry, str):
        return entry
    return None


def crawl_api(deep_sample=False, max_deep_samples=25):
    headers = api_headers()
    if headers is None:
        return {"error": "no_key"}

    # /info is only used for cosmetic display (game name/build) - it's not
    # essential to discovery, so its failure shouldn't abort the whole scan.
    info, info_status = api_get("info")
    if info_status != 200:
        weather_log(f"Discovery: /info unavailable (status {info_status}) - continuing without it")
        info = {}

    # Retry /list once after a short pause before giving up entirely.
    root, status = api_get("list")
    if status != 200:
        time.sleep(0.5)
        root, status = api_get("list")
    if status != 200:
        return {"error": "list_failed", "detail": root}
    if not isinstance(root, dict):
        return {"error": "unexpected_list_shape", "detail": root}

    # TSW6's real /list response (confirmed against an actual game): each
    # node has NodeName/NodePath, and either an inline "Nodes" array
    # (already expanded) or a "CollapsedChildren" count meaning that branch
    # was truncated and needs its own /list call (using the node's full
    # NodePath) to expand further. There is no separate "Endpoints" or
    # "Writable" concept anywhere in this response - readable/writable
    # properties on a node (e.g. WeatherManager.Temperature) exist but
    # aren't listed here, only discoverable by trying them directly.
    tree = {}      # node_path -> {"name":..., "children": [names], "collapsed": int|None}
    leaves = []     # node_paths with no children and nothing collapsed -
                    # the best candidates for direct GET/SET property access
    errors = []
    visited = set()
    queue = [root]
    start = time.time()
    node_count = 0

    while queue and node_count < MAX_NODES_TO_VISIT:
        if time.time() - start > CRAWL_TIMEOUT_SECONDS:
            break
        current = queue.pop(0)
        if not isinstance(current, dict):
            errors.append({"node": "?", "reason": "not a JSON object"})
            continue

        node_path = _get_ci(current, ["NodePath", "nodepath"]) or "(root)"
        node_name = _get_ci(current, ["NodeName", "nodename"]) or node_path
        if node_path in visited:
            continue
        visited.add(node_path)
        node_count += 1

        children_raw = _get_ci(current, ["Nodes", "nodes"], [])
        collapsed = _get_ci(current, ["CollapsedChildren", "collapsedchildren"])
        if not isinstance(children_raw, list):
            children_raw = []

        child_names = []
        if children_raw:
            for child in children_raw:
                if not isinstance(child, dict):
                    continue
                cname = _get_ci(child, ["NodeName", "nodename"])
                if cname:
                    child_names.append(cname)
                queue.append(child)
        elif collapsed:
            # Branch was truncated - expand it with its own /list call.
            # Try the full NodePath first (as given), then a version
            # without the leading "Root/" as a fallback in case that's
            # what the game's routing actually expects.
            time.sleep(0.03)
            expanded, exp_status = api_get(f"list/{node_path}")
            if exp_status != 200:
                time.sleep(0.2)
                expanded, exp_status = api_get(f"list/{node_path}")
            if exp_status != 200 and node_path.startswith("Root/"):
                alt_path = node_path[len("Root/"):]
                expanded, exp_status = api_get(f"list/{alt_path}")
            if exp_status == 200 and isinstance(expanded, dict):
                # Fold the expanded children directly into this node rather
                # than re-queueing under the same NodePath, which would get
                # silently skipped by the visited-dedup check above.
                expanded_children = _get_ci(expanded, ["Nodes", "nodes"], [])
                if isinstance(expanded_children, list):
                    for child in expanded_children:
                        if not isinstance(child, dict):
                            continue
                        cname = _get_ci(child, ["NodeName", "nodename"])
                        if cname:
                            child_names.append(cname)
                        queue.append(child)
                collapsed = None  # successfully expanded
            else:
                errors.append({"node": node_path,
                                "reason": f"could not expand {collapsed} collapsed children (status {exp_status})"})

        tree[node_path] = {"name": node_name, "children": child_names, "collapsed": collapsed}
        if not child_names and not collapsed and node_path != "(root)":
            leaves.append(node_path)

    result = {
        "meta": info.get("Meta", {}) if isinstance(info, dict) else {},
        "nodes_visited": len(visited),
        "tree": tree,
        "leaves": leaves,
        "errors": errors,
        "truncated": node_count >= MAX_NODES_TO_VISIT or (time.time() - start) > CRAWL_TIMEOUT_SECONDS,
    }

    if deep_sample:
        samples = {}
        for leaf_path in leaves[:max_deep_samples]:
            # Root/WeatherManager -> WeatherManager, matching the dotted-
            # property GET convention we know works (e.g. WeatherManager.Data)
            short = leaf_path[len("Root/"):] if leaf_path.startswith("Root/") else leaf_path
            try:
                val, st = api_get(f"get/{short}")
                samples[leaf_path] = val if st == 200 else {"status": st}
            except Exception as ex:
                samples[leaf_path] = {"error": str(ex)}
        result["samples"] = samples

    return result


def format_crawl_as_text(result):
    lines = []
    lines.append("TSW Hud - Discovery Export")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    meta = result.get("meta", {})
    if meta:
        lines.append(f"Game: {meta.get('GameName')}  Build: {meta.get('GameBuildNumber')}  "
                      f"APIVersion: {meta.get('APIVersion')}")
    lines.append(f"Nodes visited: {result.get('nodes_visited')}  Truncated: {result.get('truncated')}")
    lines.append("")
    lines.append("Note: this game build's /list only exposes the object/node tree, not a")
    lines.append("declared list of readable/writable properties on each node. 'Leaf nodes'")
    lines.append("below are the best candidates for direct property access (e.g. via")
    lines.append("WeatherManager.Data) - exact property names still need to be tried directly.")
    lines.append("")
    lines.append("=== NODE TREE ===")
    for node_path, info in sorted(result.get("tree", {}).items()):
        lines.append(f"[{node_path}]")
        if info["children"]:
            lines.append(f"  children: {', '.join(info['children'])}")
        elif info.get("collapsed"):
            lines.append(f"  ({info['collapsed']} children not expanded)")
        else:
            lines.append("  (leaf node)")
        lines.append("")
    lines.append("=== LEAF NODES (best candidates for direct GET/SET property access) ===")
    for leaf in sorted(result.get("leaves", [])):
        lines.append(leaf)
    if result.get("errors"):
        lines.append("")
        lines.append("=== NODES SKIPPED (didn't respond as expected - harmless, rest of scan still ran) ===")
        for err in result["errors"]:
            lines.append(f"{err.get('node')}: {err.get('reason')}")
    if "samples" in result:
        lines.append("")
        lines.append("=== SAMPLE GET ATTEMPTS ON LEAF NODES ===")
        for path, val in result["samples"].items():
            lines.append(f"{path}:")
            lines.append(json.dumps(val, indent=2))
            lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Real-world weather sync
#
# Uses the game's own real-world GPS position (DriverAid.PlayerInfo.geoLocation
# - yes, TSW really exposes this) to fetch live weather from Open-Meteo (free,
# no API key needed), then writes it into WeatherManager over a smooth 2:55
# transition every 3 minutes.
#
# The exact WRITE (set/) endpoint names for weather aren't in any public
# documentation - only the READ shape is documented (WeatherManager.Data with
# Temperature/Cloudiness/Precipitation/Wetness/GroundSnow/PiledSnow/FogDensity,
# each paired with an "...Overridden" flag). So rather than hardcoding guessed
# paths, this discovers the real writable endpoint names from your own game
# at runtime via /list/WeatherManager, the same mechanism the Discovery tab
# uses, and only ever writes to fields it actually found and confirmed are
# writable. If a field isn't found, it's skipped and logged rather than
# silently failing.
# --------------------------------------------------------------------------

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_FIELDS = ["Temperature", "Cloudiness", "Precipitation", "Wetness", "GroundSnow", "PiledSnow", "FogDensity"]
FETCH_INTERVAL_SECONDS = 60    # every 60s - well within Open-Meteo's 10,000/day free limit (1,440/day)
TRANSITION_SECONDS = 59        # leaves just a 1s buffer before the next fetch

_weather_lock = threading.Lock()
WEATHER_STATE = {
    "enabled": False,
    "phase": "idle",              # idle | locating | fetching | transitioning | holding | error
    "location": None,             # {"latitude":.., "longitude":..}
    "open_meteo": None,            # raw-ish mapped values from Open-Meteo
    "start_weather": None,         # TSW values at the start of this transition
    "target_weather": None,        # TSW values we're transitioning to
    "current_weather": None,       # last values actually written
    "writable_fields": None,       # resolved once per session: {field: True/False}
    "progress": 0.0,               # 0..1 through the current transition
    "seconds_to_next_fetch": None,
    "last_error": None,
    "log": [],
}


def weather_log(msg):
    with _weather_lock:
        WEATHER_STATE["log"].append(f"{datetime.now().strftime('%H:%M:%S')}  {msg}")
        WEATHER_STATE["log"] = WEATHER_STATE["log"][-40:]


def _extract_latlon(d):
    """Looks for {latitude, longitude} under a few different possible key
    names/shapes and cases, since documentation for this has been inconsistent."""
    if not isinstance(d, dict):
        return None
    lat = _get_ci(d, ["latitude", "Latitude"])
    lon = _get_ci(d, ["longitude", "Longitude"])
    if lat is not None and lon is not None:
        return {"latitude": lat, "longitude": lon}
    for key in ("geoLocation", "playerPosition", "PlayerPosition", "GeoLocation"):
        nested = d.get(key)
        if isinstance(nested, dict):
            lat = _get_ci(nested, ["latitude", "Latitude"])
            lon = _get_ci(nested, ["longitude", "Longitude"])
            if lat is not None and lon is not None:
                return {"latitude": lat, "longitude": lon}
    return None


def get_player_location():
    body, status = api_get("get/DriverAid.PlayerInfo")
    if status == 200 and isinstance(body, dict):
        loc = _extract_latlon(body.get("Values", {})) or _extract_latlon(body)
        if loc:
            return loc
    return None


# Loco/train class identification, in order of preference:
# 1. CurrentFormation/0.Function.IS_GetVehicleInfo - a real capture from a
#    German BR442 confirmed this returns a clean class string directly
#    (e.g. "390/0") plus the unit number, in one call. Its struct field
#    names carry an FGuid hash suffix that changes between builds
#    (e.g. "class_14_DEF4FFB0...") so we match by prefix, not full key.
# 2. loco_profiles DB - if this exact loco (identified by its raw
#    ObjectClass) has EVER given a clean name before, use that even if
#    IS_GetVehicleInfo fails this time - this is what turns "sometimes
#    shows the messy fallback" into "messy once, then clean forever".
# 3. CurrentDrivableActor.ObjectClass - official DTG docs confirm this is
#    real and documented, e.g. "RVM_CJP_BNSF_ES44C4_C" - messier, but
#    reliable, and works for any loco/route. Also serves as the stable
#    key the loco_profiles DB is keyed on.
# 4. Node-name scan for a Class### pattern - last resort, UK-only naming
#    convention, kept only in case everything above fails.
CLASS_NAME_PATTERN = re.compile(r"Class[_ ]?(\d{2,4})", re.IGNORECASE)


def _find_by_key_prefix(d, prefix):
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if k.lower().startswith(prefix.lower()):
            return v
    return None


def get_loco_identity():
    """Fetches everything loco recognition needs: the raw ObjectClass
    (stable DB key), the clean class name when available, and the
    formation's max speed (DriverAid.Data.formationMaxSpeed, confirmed
    real from earlier captures) for the database's speed suggestion.
    Always attempts all three rather than stopping at first success.

    Only trusts a response when Result == "Success" - outside an active
    driving session the API still returns HTTP 200 but with
    {"Result": "Error", "Message": "..."}, which has no "Values" key.
    Without this check, body.get("Values", body) fell back to the whole
    error object and picked up "Error" or the message text as a fake
    train name, silently recording sightings while sat in the main menu."""
    raw = None
    body, status = api_get("get/CurrentDrivableActor.ObjectClass")
    if status == 200 and isinstance(body, dict) and body.get("Result") == "Success":
        values = body.get("Values")
        if isinstance(values, dict):
            for v in values.values():
                if isinstance(v, str) and v.strip():
                    raw = v.strip()
                    break

    clean = None
    body, status = api_get("get/CurrentFormation/0.Function.IS_GetVehicleInfo")
    if status == 200 and isinstance(body, dict) and body.get("Result") == "Success":
        output = _get_ci(body.get("Values", {}) if isinstance(body.get("Values"), dict) else {}, ["Output", "output"])
        cls = _find_by_key_prefix(output, "class_")
        if isinstance(cls, str) and cls.strip():
            clean = cls.strip()

    formation_max_speed_ms = None
    body, status = api_get("get/DriverAid.Data")
    if status == 200 and isinstance(body, dict) and body.get("Result") == "Success":
        values = body.get("Values")
        fms = _get_ci(values, ["formationMaxSpeed"]) if isinstance(values, dict) else None
        if isinstance(fms, dict):
            v = fms.get("value")
            if isinstance(v, (int, float)):
                formation_max_speed_ms = v

    return raw, clean, formation_max_speed_ms


def find_loco_class():
    raw, clean, formation_max_speed_ms = get_loco_identity()

    if raw or clean:
        loco_profiles.record_sighting(raw, clean_name=clean, formation_max_speed_ms=formation_max_speed_ms)
        train_classes_db.record_live_sighting(raw, clean_name=clean, formation_max_speed_ms=formation_max_speed_ms)

    if clean:
        return clean

    if raw:
        profile = loco_profiles.get_profile(raw)
        if profile and profile.get("clean_name"):
            return profile["clean_name"]

    # Fallback: scan child component names for a Class### pattern
    body, status = api_get("list/CurrentDrivableActor")
    if status == 200 and isinstance(body, dict):
        children = _get_ci(body, ["Nodes", "nodes"], [])
        if isinstance(children, list):
            for child in children:
                name = _get_ci(child, ["Name", "NodeName", "name", "nodename"]) if isinstance(child, dict) else None
                if not name:
                    continue
                m = CLASS_NAME_PATTERN.search(name)
                if m:
                    return f"Class {m.group(1)}"

    if raw:
        return raw.replace("_", " ")
    return None


def get_current_raw_object_class():
    """Just the raw key, without the display-name fallback chain - used by
    routes that need to look up/set a profile for whatever's currently
    being driven."""
    raw, _clean, _fms = get_loco_identity()
    return raw


def fetch_open_meteo(lat, lon):
    try:
        r = SESSION.get(OPEN_METEO_URL, params={
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,precipitation,rain,snowfall,cloud_cover,visibility,weather_code",
            "timezone": "auto",
        }, timeout=(3, 5))
        r.raise_for_status()
        return r.json().get("current")
    except Exception as e:
        weather_log(f"Open-Meteo fetch failed: {e}")
        return None


def map_open_meteo_to_tsw(om):
    """Best-effort mapping from real-world units to TSW's 0-1 (mostly) scales.
    These scale factors are estimates, not documented anywhere - if in-game
    weather doesn't look right, this is the function to tune."""
    temp_c = om.get("temperature_2m", 10.0)
    cloud_pct = om.get("cloud_cover", 0.0)
    precip_mm = om.get("precipitation", 0.0) or 0.0
    rain_mm = om.get("rain", 0.0) or 0.0
    snow_cm = om.get("snowfall", 0.0) or 0.0
    visibility_m = om.get("visibility", 24000.0)
    if visibility_m is None:
        visibility_m = 24000.0

    precipitation = max(0.0, min(1.0, precip_mm / 4.0))
    wetness = max(0.0, min(1.0, rain_mm / 3.0))
    ground_snow = max(0.0, min(1.0, snow_cm / 2.0)) if temp_c <= 1.5 else 0.0
    fog_density = max(0.0, min(1.0, 1.0 - (visibility_m / 20000.0)))

    return {
        "Temperature": round(temp_c, 1),
        "Cloudiness": round(max(0.0, min(1.0, cloud_pct / 100.0)), 3),
        "Precipitation": round(precipitation, 3),
        "Wetness": round(wetness, 3),
        "GroundSnow": round(ground_snow, 3),
        "PiledSnow": round(ground_snow, 3),
        "FogDensity": round(fog_density, 3),
    }


def get_current_tsw_weather():
    body, status = api_get("get/WeatherManager.Data")
    if status == 200 and isinstance(body, dict):
        values = body.get("Values", {})
        return {f: values.get(f, 0.0) for f in WEATHER_FIELDS}
    return {f: 0.0 for f in WEATHER_FIELDS}


def _patch_with_retry(path, params, attempts=2, retry_delay=0.25):
    """Real logs showed WHICH weather fields fail changes between otherwise
    identical attempts a minute apart - that's the signature of a transient
    server hiccup under load, not a wrong field name (which would fail the
    same way every time). So each write gets one retry after a short pause
    before being marked as genuinely not writable."""
    status = None
    for attempt in range(attempts):
        _, status = api_patch(path, params)
        if status == 200:
            return True
        if attempt < attempts - 1:
            time.sleep(retry_delay)
    return False


def resolve_writable_weather_fields(sample_values=None):
    """The official DTG API docs (v1.5.1) show weather fields being set
    directly - e.g. PATCH set/WeatherManager.Cloudiness?Value=0.5 - with no
    mention of writing a matching '...Overridden' field at all. That flag
    only ever appears in read responses, set automatically by the game once
    a value has been overridden - it was never something we were meant to
    write ourselves. Attempting it was very likely the real cause of the
    intermittent failures seen in testing. This now only writes the 7 real
    documented fields."""
    resolved = {}
    sample_values = sample_values or {f: 0.0 for f in WEATHER_FIELDS}
    for field in WEATHER_FIELDS:
        val = sample_values.get(field, 0.0)
        ok = _patch_with_retry(f"set/WeatherManager.{field}", {"Value": val})
        resolved[field] = field if ok else None
        time.sleep(0.03)  # small pacing delay - avoids hammering the game's embedded server

    missing = [k for k, v in resolved.items() if v is None]
    if missing:
        weather_log(f"Could not write these weather fields: {', '.join(missing)} "
                    f"(the game may use different property names for these - check a "
                    f"Discovery raw dump of WeatherManager)")
    else:
        weather_log("Confirmed all weather fields are writable.")
    return resolved


def apply_weather_values(values, writable_fields, set_overrides=False):
    for field in WEATHER_FIELDS:
        ep = writable_fields.get(field)
        if ep and field in values:
            api_patch(f"set/WeatherManager.{ep}", {"Value": values[field]})


def lerp_weather(a, b, t):
    return {f: a.get(f, 0.0) + (b.get(f, 0.0) - a.get(f, 0.0)) * t for f in WEATHER_FIELDS}


LOCATION_JUMP_THRESHOLD_METERS = 1000


def haversine_meters(a, b):
    """Real great-circle distance between two {latitude, longitude} points,
    in metres. A flat degree-difference check isn't reliable everywhere -
    a degree of longitude shrinks the further you are from the equator."""
    r = 6371000  # Earth's radius, metres
    lat1, lon1 = math.radians(a["latitude"]), math.radians(a["longitude"])
    lat2, lon2 = math.radians(b["latitude"]), math.radians(b["longitude"])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1, h)))


def location_changed_significantly(a, b, threshold_m=LOCATION_JUMP_THRESHOLD_METERS):
    """True only if the player has genuinely moved threshold_m or more.
    A missing reading (None) is treated as 'no information', not as a
    jump - a single transient failed location check was previously being
    misread as the player starting a new service, causing false restarts
    every few seconds."""
    if a is None or b is None:
        return False
    try:
        return haversine_meters(a, b) >= threshold_m
    except Exception:
        return False


def weather_sync_loop():
    while True:
        if not WEATHER_STATE["enabled"]:
            with _weather_lock:
                WEATHER_STATE["phase"] = "idle"
            time.sleep(1)
            continue

        with _weather_lock:
            WEATHER_STATE["phase"] = "locating"
        loc = get_player_location()
        if not loc:
            weather_log("Waiting for player location (are you in an active session?)")
            with _weather_lock:
                WEATHER_STATE["last_error"] = "No player location yet"
            if not _sleep_while_enabled(5):
                continue
            continue

        with _weather_lock:
            WEATHER_STATE["location"] = loc
            WEATHER_STATE["phase"] = "fetching"
        om = fetch_open_meteo(loc["latitude"], loc["longitude"])
        if not om:
            with _weather_lock:
                WEATHER_STATE["last_error"] = "Open-Meteo fetch failed"
            if not _sleep_while_enabled(10):
                continue
            continue

        target = map_open_meteo_to_tsw(om)
        current = get_current_tsw_weather()
        writable = resolve_writable_weather_fields(sample_values=current)

        with _weather_lock:
            WEATHER_STATE["open_meteo"] = om
            WEATHER_STATE["target_weather"] = target
            WEATHER_STATE["start_weather"] = current
            WEATHER_STATE["writable_fields"] = writable
            WEATHER_STATE["phase"] = "transitioning"
            WEATHER_STATE["last_error"] = None
        weather_log(f"New target from ({loc['latitude']:.3f}, {loc['longitude']:.3f}): "
                    f"{target['Temperature']}°C, cloud {target['Cloudiness']*100:.0f}%")

        # resolve_writable_weather_fields() above already set the override
        # flags as part of testing them, so no need to set them again here.
        restart_immediately = False
        for i in range(TRANSITION_SECONDS + 1):
            if not WEATHER_STATE["enabled"]:
                break
            # Cheap local check every second - if the player started a new
            # service (or otherwise jumped location) mid-transition, don't
            # keep drifting toward the now-stale target for up to a minute;
            # go fetch fresh weather for the new spot right away instead.
            new_loc = get_player_location()
            if location_changed_significantly(loc, new_loc):
                weather_log("Location changed significantly (new service?) - refetching now")
                restart_immediately = True
                break
            t = i / TRANSITION_SECONDS
            interpolated = lerp_weather(current, target, t)
            apply_weather_values(interpolated, writable, set_overrides=False)
            with _weather_lock:
                WEATHER_STATE["current_weather"] = interpolated
                WEATHER_STATE["progress"] = t
                WEATHER_STATE["seconds_to_next_fetch"] = FETCH_INTERVAL_SECONDS - i
            time.sleep(1)

        if restart_immediately:
            continue

        with _weather_lock:
            WEATHER_STATE["phase"] = "holding"
        remaining = FETCH_INTERVAL_SECONDS - TRANSITION_SECONDS
        _sleep_while_enabled(remaining)


def _sleep_while_enabled(seconds):
    """Sleeps up to `seconds`, one second at a time, so Stop takes effect
    almost immediately instead of waiting out a long sleep. Returns False if
    it was interrupted by being disabled."""
    for _ in range(seconds):
        if not WEATHER_STATE["enabled"]:
            return False
        time.sleep(1)
    return True


# --------------------------------------------------------------------------
# Flask app
# --------------------------------------------------------------------------

app = Flask(__name__, static_folder=None)

import loco_profiles
loco_profiles.init_db()

import timetable_db
timetable_db.init_db()

import train_classes_db
train_classes_db.init_db()
_dedup_count = train_classes_db.dedup_train_classes()
if _dedup_count:
    print(f"[startup] deduped {_dedup_count} duplicate train class row(s)")

import other_hud_sync


from werkzeug.exceptions import HTTPException


@app.errorhandler(Exception)
def handle_any_error(e):
    if isinstance(e, HTTPException):
        return e  # let Flask's normal 404/405/etc handling proceed untouched
    if request.path.startswith("/api/"):
        import traceback
        tb = traceback.format_exc()
        try:
            with open(LAST_ERROR_FILE, "w", encoding="utf-8") as f:
                f.write(tb)
        except Exception:
            pass
        return jsonify({"error": "server_error", "detail": str(e)}), 500
    raise e


@app.route("/")
def home():
    return send_from_directory(PAGES_DIR, "index.html")


@app.route("/images/train_classes/<path:filename>")
def train_class_image(filename):
    images_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images", "train_classes")
    return send_from_directory(images_dir, filename)


@app.route("/known_train_pictures/<path:filename>")
def known_train_pictures(filename):
    pics_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "known_train_pictures")
    return send_from_directory(pics_dir, filename)


@app.route("/company_logos/<path:filename>")
def company_logos(filename):
    logos_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "company_logos")
    return send_from_directory(logos_dir, filename)


@app.route("/api/pages", methods=["GET"])
def api_pages():
    with open(os.path.join(PAGES_DIR, "registry.json")) as f:
        return jsonify(json.load(f))


@app.route("/api/gauges", methods=["GET"])
def api_gauges():
    with open(os.path.join(PAGES_DIR, "gauges_registry.json")) as f:
        return jsonify(json.load(f))


@app.route("/pages/<path:filename>")
def pages(filename):
    # Force revalidation on every request. Without this, WebView2 (and the
    # tablet's browser) can keep serving an old cached copy of style.css or
    # dashboard.js after an update - which is why UI changes sometimes only
    # showed up after a manual refresh, and why the gauge resize didn't
    # appear until now. These files are small and requested rarely enough
    # that always-fresh is worth it over the minor bandwidth saving.
    response = send_from_directory(PAGES_DIR, filename)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ---- config -----------------------------------------------------------

@app.route("/api/config", methods=["GET"])
def get_config():
    key, key_path = read_api_key()
    return jsonify({
        "version": APP_VERSION,
        "config_folder": CONFIG.get("config_folder", ""),
        "api_base": CONFIG.get("api_base", DEFAULT_API_BASE),
        "resolved_api_base": resolved_api_base(),
        "key_found": key is not None,
        "key_file_path": key_path,
        "theme": CONFIG.get("theme", "purple"),
    })


@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.get_json(force=True, silent=True) or {}
    if "config_folder" in data:
        CONFIG["config_folder"] = data["config_folder"]
    if "api_base" in data:
        CONFIG["api_base"] = data["api_base"] or DEFAULT_API_BASE
    save_config(CONFIG)
    key, key_path = read_api_key()
    return jsonify({"ok": True, "key_found": key is not None, "key_file_path": key_path})


@app.route("/api/theme", methods=["GET"])
def get_theme():
    return jsonify({"theme": CONFIG.get("theme", "purple"), "themes": THEMES})


@app.route("/api/other_hud_sync/status", methods=["GET"])
def other_hud_sync_status():
    return jsonify({
        "enabled": CONFIG.get("other_hud_sync_enabled", True),
        "db_path": CONFIG.get("other_hud_db_path", ""),
        "db_auto_detected": CONFIG.get("other_hud_db_auto_detected", False),
        "images_path": CONFIG.get("other_hud_images_path", ""),
        "last_import_row_count": CONFIG.get("last_import_row_count", -1),
        "last_import_at": CONFIG.get("last_import_at", ""),
    })


@app.route("/api/other_hud_sync/config", methods=["POST"])
def other_hud_sync_set_config():
    """Manual override, for when auto-detect can't find the other app's
    database (or found the wrong one). Setting a path here disables
    auto-detection for future launches, matching the TSW-folder override
    pattern already used elsewhere in Settings."""
    data = request.get_json(force=True, silent=True) or {}
    if "db_path" in data:
        path = (data["db_path"] or "").strip()
        CONFIG["other_hud_db_path"] = path
        CONFIG["other_hud_db_auto_detected"] = False
    if "images_path" in data:
        CONFIG["other_hud_images_path"] = (data["images_path"] or "").strip()
    if "enabled" in data:
        CONFIG["other_hud_sync_enabled"] = bool(data["enabled"])
    save_config(CONFIG)
    return jsonify({"ok": True})


@app.route("/api/other_hud_sync/run_now", methods=["POST"])
def other_hud_sync_run_now():
    """Trigger a sync pass immediately rather than waiting for the next
    background cycle - runs inline (blocking this one request), not in a
    new thread, since it's a manual one-off action the person is actively
    waiting on."""
    db_path = other_hud_sync.get_or_detect_db_path(CONFIG, save_config)
    images_dir = CONFIG.get("other_hud_images_path") or None
    messages = []
    other_hud_sync.sync_once(db_path, images_dir, CONFIG, save_config, log_fn=messages.append)
    return jsonify({"ok": True, "log": messages, "db_path": db_path})


@app.route("/api/theme", methods=["POST"])
def set_theme():
    data = request.get_json(force=True, silent=True) or {}
    theme = data.get("theme")
    if theme not in THEMES:
        return jsonify({"ok": False, "error": "unknown_theme", "themes": THEMES}), 400
    CONFIG["theme"] = theme
    save_config(CONFIG)
    return jsonify({"ok": True, "theme": theme})


def build_diagnostics_text():
    lines = []
    lines.append("TSW Hud - Diagnostics")
    lines.append(f"App version: {APP_VERSION}")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"config_folder: {CONFIG.get('config_folder')}")
    lines.append(f"api_base (as saved): {CONFIG.get('api_base')}")
    lines.append(f"api_base (actually used for requests): {resolved_api_base()}")
    key, key_path = read_api_key()
    lines.append(f"key_found: {key is not None}  key_path: {key_path}")
    lines.append(f"lan_ips: {get_lan_ips()}")
    if os.path.exists(HEARTBEAT_FILE):
        with open(HEARTBEAT_FILE, "r", encoding="utf-8") as f:
            hb_lines = f.readlines()
        if hb_lines:
            lines.append(f"last_heartbeat: {hb_lines[-1].strip()}  "
                          f"(if this is far behind 'Generated' above, the whole app froze; "
                          f"if it's current, only the window/rendering is stuck)")
    lines.append("")
    lines.append("Recent call timings (most recent last):")
    if os.path.exists(DEBUG_LOG_FILE):
        with open(DEBUG_LOG_FILE, "r", encoding="utf-8") as f:
            lines.append(f.read())
    else:
        lines.append("(no calls logged yet)")
    if os.path.exists(LAST_ERROR_FILE):
        lines.append("")
        lines.append("Last server error:")
        with open(LAST_ERROR_FILE, "r", encoding="utf-8") as f:
            lines.append(f.read())
    if os.path.exists(CRASH_LOG_FILE):
        lines.append("")
        lines.append("Crash log (unhandled exceptions in any thread):")
        with open(CRASH_LOG_FILE, "r", encoding="utf-8") as f:
            lines.append(f.read())
    return "\n".join(lines)


@app.route("/api/diagnostics", methods=["GET"])
def diagnostics():
    return jsonify({"text": build_diagnostics_text()})


@app.route("/api/network", methods=["GET"])
def network_info():
    ips = get_lan_ips()
    candidates = [
        {"ip": ip, "dashboard_url": f"http://{ip}:{SERVER_PORT}/pages/dashboard.html",
         "home_url": f"http://{ip}:{SERVER_PORT}/"}
        for ip in ips
    ]
    best = candidates[0] if candidates else None
    return jsonify({
        "candidates": candidates,
        "lan_ip": best["ip"] if best else None,
        "network_ip": best["ip"] if best else None,
        "port": SERVER_PORT,
        "dashboard_url": best["dashboard_url"] if best else None,
        "home_url": best["home_url"] if best else None,
    })


@app.route("/api/network-info", methods=["GET"])
def network_info_simple():
    ips = get_lan_ips()
    return jsonify({
        "network_ip": ips[0] if ips else None,
    })


@app.route("/api/version", methods=["GET"])
def get_version():
    return jsonify({
        "main_version": APP_VERSION,
        "version": APP_VERSION,
    })


@app.route("/api/autodetect", methods=["POST"])
def autodetect():
    folder = auto_detect_folder()
    if folder:
        CONFIG["config_folder"] = folder
        save_config(CONFIG)
        return jsonify({"ok": True, "config_folder": folder})
    return jsonify({"ok": False, "error": "not_found",
                     "checked": candidate_folders()})


# ---- live proxy (used by dashboard pages) ------------------------------

@app.route("/api/proxy/get/<path:subpath>", methods=["GET"])
def proxy_get(subpath):
    body, status = api_get(f"get/{subpath}")
    return jsonify(body), status


@app.route("/api/proxy/set/<path:subpath>", methods=["PATCH"])
def proxy_set(subpath):
    body, status = api_patch(f"set/{subpath}", dict(request.args))
    return jsonify(body), status


@app.route("/api/proxy/raw/<path:subpath>", methods=["GET"])
def proxy_raw(subpath):
    """Call any GET route verbatim, e.g. info, list, list/CurrentDrivableActor."""
    body, status = api_get(subpath)
    return jsonify(body), status


@app.route("/api/paks/repak", methods=["GET"])
def paks_repak_status():
    """Reports whether repak is present and what its CLI supports."""
    import pak_tools
    return jsonify(pak_tools.capabilities())


@app.route("/api/paks/list", methods=["POST"])
def paks_list():
    """Lists what's inside a route pak WITHOUT extracting it.

    This is the step that answers where timetable data actually lives
    inside the archive and in what format - the one remaining unknown
    blocking the offline-timetable parser. Body:
        {"pak_path": "...", "aes_key": "optional"}
    """
    import pak_tools
    body = request.get_json(force=True, silent=True) or {}
    pak_path = (body.get("pak_path") or "").strip()
    if not pak_path:
        return jsonify({"error": "pak_path required"}), 400
    result = pak_tools.list_pak(
        pak_path,
        filter_keywords=pak_tools.TIMETABLE_KEYWORDS,
        aes_key=(body.get("aes_key") or "").strip() or None,
        path_filter=(body.get("path_filter") or "").strip() or None,
    )
    return jsonify(result)


@app.route("/api/paks/timetables", methods=["POST"])
def paks_timetables():
    """Timetable assets in ONE pak, grouped by kind. Body: {"pak_path": ...}"""
    import pak_tools
    body = request.get_json(force=True, silent=True) or {}
    pak_path = (body.get("pak_path") or "").strip()
    if not pak_path:
        return jsonify({"error": "pak_path required"}), 400
    return jsonify(pak_tools.find_timetables(
        pak_path, aes_key=(body.get("aes_key") or "").strip() or None))


@app.route("/api/paks/scan_all", methods=["POST"])
def paks_scan_all():
    """Scans EVERY pak in a folder for timetables.

    A route can have several timetables (Fife Circle has a Class 170 one and
    a Sprinter Express one) and the extras aren't necessarily in that route's
    own pak, so this is what answers "which timetables do I actually have".
    Body: {"pak_dir": "...\\Content\\DLC"} - omit to auto-detect.
    """
    import pak_tools, game_files
    body = request.get_json(force=True, silent=True) or {}
    pak_dir = (body.get("pak_dir") or "").strip()
    aes_key = (body.get("aes_key") or "").strip() or None

    if pak_dir:
        return jsonify(pak_tools.scan_all_paks(pak_dir, aes_key=aes_key))

    # Auto-detect. Paks are not all in one place - a route's own pak sits
    # under Content/DLC, but separately-sold content (Fife Circle's Sprinter
    # Express timetable, for example) ships as its own pak and may land in
    # Content/Paks instead. Scanning only one folder would silently miss
    # those, so every folder that actually contains paks gets scanned.
    scan = game_files.scan()
    target = scan.get("scanned") or {}
    content = target.get("content_dir")
    pak_dirs = []
    if content:
        for sub in ("DLC", "Paks", ""):
            candidate = os.path.join(content, sub) if sub else content
            if os.path.isdir(candidate) and any(
                f.lower().endswith(".pak") for f in os.listdir(candidate)
            ):
                pak_dirs.append(candidate)
    # Anything the install scan already spotted as holding paks.
    for i in scan.get("installs_found", []):
        for d in i.get("pak_dirs", []) or []:
            if d not in pak_dirs:
                pak_dirs.append(d)

    if not pak_dirs:
        return jsonify({"error": "could not locate any pak folder - pass pak_dir"}), 400

    combined = {"pak_dirs": pak_dirs, "paks_scanned": 0,
                "paks_with_timetables": 0, "total_timetables": 0, "results": []}
    for d in pak_dirs:
        r = pak_tools.scan_all_paks(d, aes_key=aes_key)
        if r.get("error"):
            continue
        combined["paks_scanned"] += r.get("paks_scanned", 0)
        combined["paks_with_timetables"] += r.get("paks_with_timetables", 0)
        combined["total_timetables"] += r.get("total_timetables", 0)
        combined["results"].extend(r.get("results", []))
    return jsonify(combined)


@app.route("/api/timetable/find_exports", methods=["GET", "POST"])
def timetable_find_exports():
    """Searches the machine for the other app's exported service JSON.

    Per the earlier investigation, that app can export one JSON per
    service with arrival, departure, location, latitude and longitude
    already decoded. If any exist on disk, importing them is ordinary
    work - far cheaper than reverse-engineering the Unreal binary. This
    checks before committing to the harder route.

    Files are identified by CONTENT (they must actually carry those
    fields), so an unrelated .json cannot be mistaken for an export.
    Body (optional): {"path": "folder to also search"}
    """
    import other_hud_sync
    extra = []
    if request.method == "POST":
        body = request.get_json(force=True, silent=True) or {}
        p = (body.get("path") or "").strip()
        if p:
            extra.append(p)
    try:
        return jsonify(other_hud_sync.find_timetable_exports(extra_roots=extra))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/paks/timespans", methods=["POST"])
def paks_timespans():
    """Scans an already-extracted .uexp for FTimespan-shaped values.

    The DataTrack assets carry a 'Timespan' property and 8-26 MB of .uexp
    data but no readable time strings, because Unreal stores FTimespan as
    an int64 of 100ns ticks. This looks for those directly, and reports
    clusters rather than isolated hits - a run of consecutive plausible
    times is what a stop-time table looks like, whereas scattered ones can
    be coincidental byte patterns.

    Body: {"path": "<full path to the extracted .uexp>"}  - or omit and
    pass asset_name to search the extracted folder for it.
    """
    import pak_tools
    body = request.get_json(force=True, silent=True) or {}
    path = (body.get("path") or "").strip()
    name = (body.get("asset_name") or "").strip()

    if not path and name:
        root_dir = os.path.join(APP_DIR, "extracted")
        want = os.path.splitext(name)[0].lower() + ".uexp"
        for root, _dirs, files in os.walk(root_dir):
            for f in files:
                if f.lower() == want:
                    path = os.path.join(root, f)
                    break
            if path:
                break
    if not path:
        return jsonify({"error": "path or asset_name required"}), 400
    return jsonify(pak_tools.scan_timespans(path))


@app.route("/api/paks/analyse", methods=["POST"])
def paks_analyse():
    """Dumps the bytes around each recovered time so the record layout can
    be worked out. Body: {"asset_name": "...DataTrack.uasset"} - looks for
    the matching .uexp under the app's extracted folder."""
    import pak_tools
    body = request.get_json(force=True, silent=True) or {}
    path = (body.get("path") or "").strip()
    name = (body.get("asset_name") or "").strip()
    if not path and name:
        want = os.path.splitext(os.path.basename(name))[0].lower() + ".uexp"
        for root, _dirs, files in os.walk(os.path.join(APP_DIR, "extracted")):
            for f in files:
                if f.lower() == want:
                    path = os.path.join(root, f)
                    break
            if path:
                break
    if not path:
        return jsonify({"error": "path or asset_name required"}), 400
    return jsonify(pak_tools.analyse_records(path))


@app.route("/api/paks/diff", methods=["POST"])
def paks_diff():
    """Compares consecutive fixed-size records to produce a field map.
    Body: {"asset_name": "...DataTrack.uasset", "stride": optional int}"""
    import pak_tools
    body = request.get_json(force=True, silent=True) or {}
    path = (body.get("path") or "").strip()
    name = (body.get("asset_name") or "").strip()
    if not path and name:
        want = os.path.splitext(os.path.basename(name))[0].lower() + ".uexp"
        for root, _dirs, files in os.walk(os.path.join(APP_DIR, "extracted")):
            for f in files:
                if f.lower() == want:
                    path = os.path.join(root, f)
                    break
            if path:
                break
    if not path:
        return jsonify({"error": "path or asset_name required"}), 400
    stride = body.get("stride")
    return jsonify(pak_tools.diff_records(path, stride=int(stride) if stride else None))


@app.route("/api/paks/decode", methods=["POST"])
def paks_decode():
    """Reads the time field at a known offset in every fixed-size record.
    Body: {"asset_name": "...", "stride": 2828, "offset": 0}"""
    import pak_tools
    body = request.get_json(force=True, silent=True) or {}
    path = (body.get("path") or "").strip()
    name = (body.get("asset_name") or "").strip()
    if not path and name:
        want = os.path.splitext(os.path.basename(name))[0].lower() + ".uexp"
        for root, _dirs, files in os.walk(os.path.join(APP_DIR, "extracted")):
            for f in files:
                if f.lower() == want:
                    path = os.path.join(root, f)
                    break
            if path:
                break
    if not path:
        return jsonify({"error": "path or asset_name required"}), 400
    return jsonify(pak_tools.decode_stride(
        path,
        stride=int(body.get("stride") or 2828),
        offset=int(body.get("offset") or 0)))


@app.route("/api/paks/services", methods=["POST"])
def paks_services():
    """Extracts times in file order and splits them into ascending runs,
    each a candidate service. No stride assumption - the data is a
    variable-length stream. Body: {"asset_name": "...DataTrack.uasset"}"""
    import pak_tools
    body = request.get_json(force=True, silent=True) or {}
    path = (body.get("path") or "").strip()
    name = (body.get("asset_name") or "").strip()
    if not path and name:
        want = os.path.splitext(os.path.basename(name))[0].lower() + ".uexp"
        for root, _dirs, files in os.walk(os.path.join(APP_DIR, "extracted")):
            for f in files:
                if f.lower() == want:
                    path = os.path.join(root, f)
                    break
            if path:
                break
    if not path:
        return jsonify({"error": "path or asset_name required"}), 400
    return jsonify(pak_tools.extract_time_series(path))


@app.route("/api/paks/probe", methods=["POST"])
def paks_probe():
    """Measures which Unreal serialisation an asset actually uses, by counting
    how often each name is referenced as an FName inside the .uexp.

    Tagged properties reference the property and TYPE names once per record;
    unversioned properties reference neither, while still referencing enum
    VALUE names. That asymmetry settles it without guesswork."""
    import pak_tools
    body = request.get_json(force=True, silent=True) or {}
    path = (body.get("path") or "").strip()
    name = (body.get("asset_name") or "").strip()
    if not path and name:
        want = os.path.splitext(os.path.basename(name))[0].lower() + ".uexp"
        for root, _dirs, files in os.walk(os.path.join(APP_DIR, "extracted")):
            for f in files:
                if f.lower() == want:
                    path = os.path.join(root, f)
                    break
            if path:
                break
    if not path:
        return jsonify({"error": "path or asset_name required"}), 400
    target = (body.get("window_around") or "DataType").strip()
    return jsonify(pak_tools.probe_name_references(path, window_around=target))


@app.route("/api/paks/records", methods=["POST"])
def paks_records():
    """Reads a DataTrack as Unreal TAGGED PROPERTIES and returns the records
    field by field, by name. Body: {"asset_name": "...DataTrack.uasset"}

    This supersedes /api/paks/stops for these assets. The Leven Branch name
    table contains ArrayProperty/EnumProperty/FloatProperty/IntProperty and
    field names like DataType and Distance, which means the asset is
    self-describing - so nothing needs to be inferred statistically.
    /api/paks/stops is kept for assets that really are opaque binary."""
    import pak_tools
    body = request.get_json(force=True, silent=True) or {}
    path = (body.get("path") or "").strip()
    name = (body.get("asset_name") or "").strip()
    if not path and name:
        want = os.path.splitext(os.path.basename(name))[0].lower() + ".uexp"
        for root, _dirs, files in os.walk(os.path.join(APP_DIR, "extracted")):
            for f in files:
                if f.lower() == want:
                    path = os.path.join(root, f)
                    break
            if path:
                break
    if not path:
        return jsonify({"error": "path or asset_name required"}), 400
    return jsonify(pak_tools.parse_track_records(path))


@app.route("/api/paks/stops", methods=["POST"])
def paks_stops():
    """Separates real STATION STOPS from the simulated running times around
    them, by resolving each record's FName references against the name table
    in the sibling .uasset. Body: {"asset_name": "...DataTrack.uasset"}

    This is the step after /api/paks/services: that one returns runs of
    120-151 track points per service, which is the whole route profile, not
    a timetable. Only the subset flagged StopPoint is a station call."""
    import pak_tools
    body = request.get_json(force=True, silent=True) or {}
    path = (body.get("path") or "").strip()
    name = (body.get("asset_name") or "").strip()
    if not path and name:
        want = os.path.splitext(os.path.basename(name))[0].lower() + ".uexp"
        for root, _dirs, files in os.walk(os.path.join(APP_DIR, "extracted")):
            for f in files:
                if f.lower() == want:
                    path = os.path.join(root, f)
                    break
            if path:
                break
    if not path:
        return jsonify({"error": "path or asset_name required"}), 400
    radius = int(body.get("radius") or 192)
    return jsonify(pak_tools.find_stop_points(path, radius=radius))


@app.route("/api/paks/clear_extracted", methods=["POST"])
def paks_clear_extracted():
    """Deletes the app's own 'extracted' working folder.

    Stale files here caused a real misdiagnosis: an inspection reported a
    header-only read when in fact nothing new had been extracted and the
    inspector had picked up a leftover .uasset from an earlier run. Making
    this a button avoids repeating that. Only ever touches the app's own
    folder - never the game install."""
    import shutil
    target = os.path.join(APP_DIR, "extracted")
    if not os.path.isdir(target):
        return jsonify({"ok": True, "existed": False, "path": target})
    try:
        shutil.rmtree(target)
        return jsonify({"ok": True, "existed": True, "path": target})
    except Exception as e:
        return jsonify({"error": str(e), "path": target}), 500


@app.route("/api/paks/inspect", methods=["POST"])
def paks_inspect():
    """Extracts ONE timetable asset from a pak and reports what readable
    content is inside it.

    This is the step that decides whether a parser is worth building: if
    station names, times and service codes are present as plain strings,
    it's realistic; if only property names appear, the values are packed
    binary and it's a much bigger job.

    Body: {"pak_path": "...", "asset_path": "TS2Prototype/Plugins/..."}
    """
    import pak_tools
    body = request.get_json(force=True, silent=True) or {}
    pak_path = (body.get("pak_path") or "").strip()
    asset_path = (body.get("asset_path") or "").strip()
    if not pak_path or not asset_path:
        return jsonify({"error": "pak_path and asset_path required"}), 400

    out_dir = os.path.join(APP_DIR, "extracted",
                            os.path.splitext(os.path.basename(pak_path))[0])

    # A .uasset is only the header and name table - the actual exported
    # property data lives in the .uexp sibling. Extracting just the
    # .uasset (as an earlier version did) reads names and enums but none
    # of the values, which is why the first inspection found 208 service
    # codes but zero times. Include the whole stem so .uexp/.ubulk come
    # too; the include filter is best-effort anyway since repak may not
    # support it.
    stem = os.path.splitext(asset_path)[0]
    # Glob, not a bare stem: repak's --include expects a pattern, and a
    # stem with no wildcard matches nothing. The previous attempt passed
    # the bare stem, which silently extracted nothing - the .uasset that
    # got inspected was left over from an earlier run in the same output
    # folder, so it still reported uexp_size: null.
    unpacked = pak_tools.unpack_pak(
        pak_path, out_dir,
        include=stem + "*",
        aes_key=(body.get("aes_key") or "").strip() or None,
    )
    if unpacked.get("error"):
        return jsonify(unpacked), 400

    target = None
    wanted = os.path.basename(asset_path).lower()
    for root, _dirs, files in os.walk(out_dir):
        for f in files:
            if f.lower() == wanted:
                target = os.path.join(root, f)
                break
        if target:
            break
    if not target:
        return jsonify({"error": "asset_not_found_after_unpack",
                        "unpacked_to": out_dir,
                        "files_written": unpacked.get("files_written")}), 404

    result = pak_tools.inspect_asset(target)
    result["extracted_to"] = target
    result["unpack_files_written"] = unpacked.get("files_written")

    if result.get("uexp_size") is None:
        # Distinguish "the pak has no .uexp for this asset" from "we failed
        # to extract it" - very different problems, and guessing between
        # them wasted a round trip already.
        listing = pak_tools.list_pak(pak_path, path_filter=os.path.basename(stem))
        in_pak = [e for e in (listing.get("filtered") or [])
                  if e.lower().endswith(".uexp")]
        result["uexp_in_pak"] = in_pak
        result["extraction_note"] = (
            "The .uexp IS in the pak but did not reach disk - the include "
            "filter or the output folder is the problem. Try deleting the "
            "app's 'extracted' folder and running again."
            if in_pak else
            "No .uexp for this asset exists in the pak at all, so the "
            "header/name-table really is everything this asset carries. "
            "The values likely live in the linked DataTrack assets instead."
        )
    return jsonify(result)


@app.route("/api/paks/unpack", methods=["POST"])
def paks_unpack():
    """Unpacks a pak to a working directory under this app (never into the
    game folder). Body: {"pak_path": "...", "include": "optional",
    "aes_key": "optional"}"""
    import pak_tools
    body = request.get_json(force=True, silent=True) or {}
    pak_path = (body.get("pak_path") or "").strip()
    if not pak_path:
        return jsonify({"error": "pak_path required"}), 400
    out_dir = os.path.join(APP_DIR, "extracted",
                            os.path.splitext(os.path.basename(pak_path))[0])
    result = pak_tools.unpack_pak(
        pak_path, out_dir,
        include=(body.get("include") or "").strip() or None,
        aes_key=(body.get("aes_key") or "").strip() or None,
    )
    return jsonify(result)


@app.route("/api/journey", methods=["GET"])
def journey_live():
    """Live service/next-stop info, straight from the game API.

    Two paths carry this, both confirmed from real captures of the other
    HUD's endpoint dumps rather than guessed at:

      DriverAid.PlayerInfo -> currentServiceName ("1A10"), the headcode of
                              the service currently being driven.
      DriverAid.TrackData  -> stations[] and markers[], each with
                              stationName, distanceToStationCM,
                              platformLength, markerType, markerName.

    DriverAid.TrackData was not in the original candidate list at all, and
    DriverAid.PlayerInfo only ever returned a dropped connection during
    scanning, so neither had been seen before. Together they give the live
    "what am I running / what's my next stop" half of timetable data with
    no pak extraction needed. Scheduled times still require the offline
    timetable data - see the extraction notes in game_files.py.

    Distances are in CENTIMETRES, matching distanceToSignal elsewhere in
    this API.
    """
    result = {"service_name": None, "stations": [], "markers": [], "errors": []}

    body, status = api_get("get/DriverAid.PlayerInfo", timeout=(2, 4))
    if status == 200 and isinstance(body, dict) and body.get("Result") == "Success":
        values = body.get("Values") or {}
        result["service_name"] = values.get("currentServiceName")
        geo = values.get("geoLocation") or {}
        result["latitude"] = geo.get("latitude")
        result["longitude"] = geo.get("longitude")
    else:
        result["errors"].append({"path": "DriverAid.PlayerInfo", "status": status})

    body, status = api_get("get/DriverAid.TrackData", timeout=(2, 4))
    if status == 200 and isinstance(body, dict) and body.get("Result") == "Success":
        values = body.get("Values") or {}

        def clean(entry):
            if not isinstance(entry, dict):
                return None
            cm = entry.get("distanceToStationCM")
            return {
                "station_name": entry.get("stationName"),
                "marker_name": entry.get("markerName") or None,
                "marker_type": entry.get("markerType"),
                "distance_cm": cm,
                "distance_m": round(cm / 100.0, 1) if isinstance(cm, (int, float)) else None,
                "platform_length_m": (round(entry["platformLength"] / 100.0, 1)
                                       if isinstance(entry.get("platformLength"), (int, float)) else None),
            }

        for key in ("stations", "markers"):
            raw = values.get(key)
            if isinstance(raw, list):
                result[key] = [c for c in (clean(e) for e in raw) if c]
    else:
        result["errors"].append({"path": "DriverAid.TrackData", "status": status})

    # Nearest upcoming stop, for a simple HUD readout.
    everything = [s for s in (result["stations"] + result["markers"])
                  if s.get("distance_cm") is not None]
    if everything:
        nearest = min(everything, key=lambda s: s["distance_cm"])
        result["next_stop"] = nearest
    else:
        result["next_stop"] = None

    return jsonify(result)


@app.route("/api/gamefiles/scan", methods=["GET", "POST"])
def gamefiles_scan():
    """Locates the TSW install and inventories its content files.

    Step one towards reading timetables out of the game directly rather
    than importing them from another HUD's database. Strictly read-only -
    it lists and stats files, never opens or modifies anything.

    POST {"path": "..."} to scan a specific folder if the game is
    installed somewhere the auto-detection doesn't cover.
    """
    import game_files

    extra_roots = None
    if request.method == "POST":
        body = request.get_json(force=True, silent=True) or {}
        manual = (body.get("path") or "").strip()
        if manual:
            # Accept either the library root or the game folder itself, so
            # it doesn't matter which the user pastes in.
            extra_roots = [manual, os.path.dirname(manual.rstrip("/\\"))]

    try:
        result = game_files.scan(extra_roots=extra_roots)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    # If a manual path was given and auto-detection found nothing there,
    # try treating it as a Content directory directly.
    if request.method == "POST" and not result.get("inventory"):
        body = request.get_json(force=True, silent=True) or {}
        manual = (body.get("path") or "").strip()
        if manual and os.path.isdir(manual):
            inv = game_files.inventory_content(manual)
            if not inv.get("error"):
                result["scanned"] = {"install_dir": manual, "content_dir": manual,
                                      "game_name": "(manual path)", "has_content": True}
                result["inventory"] = inv
                result.pop("conclusion", None)

    return jsonify(result)


# ---- timetable / journey discovery ------------------------------------
# Investigating whether live timetable data (service ID, destination, calling
# points, scheduled times) is exposed by the game's own API. Right now this
# app gets timetables by importing another HUD's SQLite database
# (import_from_other_hud.py) rather than from the game directly.
#
# Two-pronged scan, mirroring how the safety-system scan works:
#   1. Crawl the node tree looking for timetable-ish node names.
#   2. Probe a curated list of plausible paths directly, because this game
#      build's /list only exposes the object/node tree - NOT the readable
#      properties on each node (see format_crawl_as_text). So a property
#      like DriverAid.Data.nextStopName can exist and be readable while
#      never appearing anywhere in /list output.

_TIMETABLE_KEYWORDS = [
    "timetable", "schedule", "service", "journey", "destination",
    "station", "stop", "platform", "arrival", "departure", "calling",
    "route", "scenario", "objective", "mission", "trip", "run",
]

# Paths worth trying directly even though /list won't advertise them.
# Grouped so the results are readable; the game will simply return an error
# for anything that doesn't exist, which is harmless.
_TIMETABLE_CANDIDATE_PATHS = [
    # DriverAid already gives us speed limits/signals - it's the most likely
    # home for next-stop info too.
    "DriverAid.Data",
    "DriverAid.PlayerInfo",
    "DriverAid.Timetable",
    "DriverAid.Schedule",
    "DriverAid.NextStop",
    "DriverAid.Destination",
    "DriverAid.Data.nextStop",
    "DriverAid.Data.nextStopName",
    "DriverAid.Data.destination",
    "DriverAid.Data.serviceCode",
    "DriverAid.Data.distanceToNextStop",
    # Top-level managers, following the WeatherManager/TimeOfDay pattern.
    "TimetableManager",
    "TimetableManager.Data",
    "ScheduleManager",
    "ScheduleManager.Data",
    "ServiceManager",
    "ServiceManager.Data",
    "JourneyManager",
    "JourneyManager.Data",
    "ScenarioManager",
    "ScenarioManager.Data",
    "MissionManager",
    "MissionManager.Data",
    "ObjectiveManager",
    "ObjectiveManager.Data",
    "RouteManager",
    "RouteManager.Data",
    # Formation/actor-level - the service a given train is running.
    "CurrentFormation/0.Timetable",
    "CurrentFormation/0.Schedule",
    "CurrentFormation/0.Service",
    "CurrentFormation/0.Data",
    "CurrentDrivableActor.Timetable",
    "CurrentDrivableActor.Schedule",
    "CurrentDrivableActor.Service",
    "CurrentDrivableActor.Destination",
    # Function.* style calls, matching HUD_GetSpeed / IS_GetVehicleInfo.
    "CurrentFormation/0.Function.IS_GetTimetable",
    "CurrentFormation/0.Function.IS_GetServiceInfo",
    "CurrentFormation/0.Function.IS_GetDestination",
    "CurrentDrivableActor.Function.HUD_GetTimetable",
    "CurrentDrivableActor.Function.HUD_GetNextStop",
    "CurrentDrivableActor.Function.HUD_GetDestination",
    "CurrentDrivableActor.Function.HUD_GetServiceInfo",
]


@app.route("/api/timetable/scan", methods=["GET", "POST"])
def timetable_scan():
    """Investigates whether the game exposes live timetable data.

    GET: crawls the node tree for timetable-ish names AND probes the
    curated candidate path list above.
    POST {"test_paths": [...]}: probes specific paths, for following up on
    anything promising the automatic scan turns up.

    Must be run while in an active driving session - like everything under
    DriverAid/CurrentDrivableActor, these paths won't respond from the menu.
    """
    headers = api_headers()
    if headers is None:
        return jsonify({"error": "no_key"}), 400

    def probe(path, retries=2):
        """Probes a path, retrying on connection drops.

        The game's HTTP API drops connections when hit rapidly, which
        api_get surfaces as 502/connection_failed. That is NOT the same as
        "this path doesn't exist", and treating it as such produced false
        negatives on the first pass - including on a real top-level
        Timetable node. Three outcomes are distinguished:
          responded=True                    -> exists, returned data
          exists=False                      -> game said "not found"
          responded=False, exists=True      -> exists but no data right now
          unreachable=True                  -> connection dropped, unknown
        """
        last = None
        for attempt in range(retries + 1):
            body, status = api_get(f"get/{path}", timeout=(2, 4))
            last = (body, status)
            if status != 502:
                break
            time.sleep(0.25 * (attempt + 1))  # back off, let the API recover
        body, status = last
        msg = body.get("Message") if isinstance(body, dict) else None
        ok = (status == 200 and isinstance(body, dict)
              and body.get("Result") == "Success")
        unreachable = status == 502
        # "Node X not found" means it genuinely isn't there; "failed to
        # return valid data" means the node exists but has nothing for us.
        not_found = bool(msg and "not found" in msg.lower())
        return {
            "path": path,
            "status": status,
            "responded": ok,
            "exists": (not not_found) if not unreachable else None,
            "unreachable": unreachable,
            "values": body.get("Values") if ok else None,
            "message": msg if not ok else None,
        }

    def api_list(path):
        """/list with the same retry treatment."""
        for attempt in range(3):
            body, status = api_get(f"list/{path}" if path else "list", timeout=(2, 4))
            if status != 502:
                return body, status
            time.sleep(0.25 * (attempt + 1))
        return body, status

    if request.method == "POST":
        body = request.get_json(force=True, silent=True) or {}
        test_paths = body.get("test_paths", [])
        if not test_paths:
            return jsonify({"error": "test_paths required"}), 400
        results = [probe(p) for p in test_paths]
        return jsonify({
            "test_results": results,
            "total": len(results),
            "responding": sum(1 for r in results if r["responded"]),
        })

    # 1. Probe the curated candidates.
    candidates = [probe(p) for p in _TIMETABLE_CANDIDATE_PATHS]

    # 2. Crawl the node tree from the root for timetable-ish node names.
    matches = []
    visited = set()
    queue = [""]
    while queue and len(visited) < 250:
        path = queue.pop(0)
        if path in visited:
            continue
        visited.add(path)
        body, status = api_list(path)
        if status != 200 or not isinstance(body, dict):
            continue
        nodes = _get_ci(body, ["Nodes", "nodes"], [])
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, dict):
                continue
            child_name = _get_ci(node, ["NodeName", "nodename"])
            if not child_name:
                continue
            child_path = f"{path}.{child_name}" if path else child_name
            lower = child_name.lower().replace("_", "")
            if any(kw in lower for kw in _TIMETABLE_KEYWORDS):
                result = probe(child_path)
                result["name"] = child_name
                matches.append(result)
                # Any node matching a timetable keyword is worth expanding
                # even if it returned nothing itself - the data may sit on
                # a child. This is how the top-level Timetable node gets
                # explored rather than just noted and dropped.
                child_body, child_status = api_list(child_path)
                if child_status == 200 and isinstance(child_body, dict):
                    grandkids = _get_ci(child_body, ["Nodes", "nodes"], [])
                    if isinstance(grandkids, list):
                        for gk in grandkids:
                            if not isinstance(gk, dict):
                                continue
                            gk_name = _get_ci(gk, ["NodeName", "nodename"])
                            if not gk_name:
                                continue
                            gk_path = f"{child_path}.{gk_name}"
                            gk_result = probe(gk_path)
                            gk_result["name"] = gk_name
                            gk_result["parent"] = child_path
                            matches.append(gk_result)
            queue.append(child_path)

    everything = candidates + matches
    responding = [r for r in everything if r["responded"]]
    unreachable = [r for r in everything if r.get("unreachable")]
    exists_no_data = [r for r in everything
                      if not r["responded"] and r.get("exists") is True]

    return jsonify({
        "candidates": candidates,
        "tree_matches": matches,
        "nodes_visited": len(visited),
        "responding": responding,
        "responding_count": len(responding),
        "exists_but_no_data": exists_no_data,
        "unreachable": unreachable,
        "summary": (
            f"{len(responding)} of {len(everything)} probed paths returned data. "
            f"{len(exists_no_data)} exist but had no data right now. "
            f"{len(unreachable)} could not be reached even after retries. "
            "Run this while actually driving a timetabled service."
        ),
    })

_SAFETY_KEYWORDS = [
    "safety", "aws", "tpws", "pzb", "sifa", "vigilance", "deadman",
    "deadmanshandle", "cruisecontrol", "cruise", "afb", "afc",
    "interventionbrake", "overspeed", "ato", "atc", "avis", "device",
]


@app.route("/api/safety/scan", methods=["GET", "POST"])
def safety_scan():
    """GET: Crawls CurrentDrivableActor to find all safety-related nodes/endpoints
    by name match. Then probes each one to see which respond with Result=Success.
    
    POST with {"test_paths": ["path1", "path2", ...]} to test specific paths manually
    (useful when running a full discovery scan and finding paths manually)."""
    
    if request.method == "POST":
        body = request.get_json(force=True, silent=True) or {}
        test_paths = body.get("test_paths", [])
        if not test_paths:
            return jsonify({"error": "test_paths required"}), 400
        results = []
        for path in test_paths:
            get_body, get_status = api_get(f"get/{path}", timeout=(1, 2))
            responded = (
                get_status == 200 and isinstance(get_body, dict) and
                get_body.get("Result") == "Success"
            )
            results.append({
                "path": path,
                "status": get_status,
                "responded": responded,
                "value": get_body.get("Values") if responded else None,
            })
        return jsonify({"test_results": results, "total": len(results), "responding": sum(1 for r in results if r["responded"])})
    
    # GET: Auto-crawl for safety nodes
    # Build a full discovery tree starting from CurrentDrivableActor
    headers = api_headers()
    if headers is None:
        return jsonify({"error": "no_key"}), 400

    # Manual walk of CurrentDrivableActor tree to find safety nodes
    safety_nodes = []  # (full_path, response_status, values)
    visited = set()
    queue = [("CurrentDrivableActor", None)]  # (path, parent_response)

    for _ in range(100):  # Limit iterations
        if not queue:
            break
        path, _parent = queue.pop(0)
        if path in visited or len(visited) > 200:
            continue
        visited.add(path)

        # Try to list children
        body, status = api_get(f"list/{path}")
        if status == 200 and isinstance(body, dict):
            nodes = _get_ci(body, ["Nodes", "nodes"], [])
            if isinstance(nodes, list):
                for node in nodes:
                    if not isinstance(node, dict):
                        continue
                    child_name = _get_ci(node, ["NodeName", "nodename"])
                    if not child_name:
                        continue
                    child_path = f"{path}.{child_name}" if path else child_name
                    # Check if this node name matches safety keywords
                    lower = child_name.lower().replace("_", "")
                    if any(kw in lower for kw in _SAFETY_KEYWORDS):
                        # Try to GET this path - see if it responds
                        get_body, get_status = api_get(f"get/{child_path}", timeout=(1, 2))
                        responded = (
                            get_status == 200 and isinstance(get_body, dict) and
                            get_body.get("Result") == "Success"
                        )
                        safety_nodes.append({
                            "path": child_path,
                            "name": child_name,
                            "status": get_status,
                            "responded": responded,
                            "value": get_body.get("Values") if responded else None,
                        })
                    # Queue children
                    queue.append((child_path, status))

    return jsonify({
        "safety_nodes": safety_nodes,
        "total_found": len(safety_nodes),
        "responding": sum(1 for n in safety_nodes if n["responded"]),
    })


@app.route("/api/safety/enable_all", methods=["POST"])
def safety_enable_all():
    """Attempts to write Value=true to every safety node path that responded
    with Result=Success. Returns per-path outcomes."""
    body = request.get_json(force=True, silent=True) or {}
    paths = body.get("paths", [])
    if not paths:
        return jsonify({"error": "no paths provided"}), 400

    outcomes = []
    for path in paths:
        # Verify it still responds
        get_body, get_status = api_get(f"get/{path}", timeout=(1, 2))
        if not (get_status == 200 and isinstance(get_body, dict) and get_body.get("Result") == "Success"):
            outcomes.append({"path": path, "skipped": True, "reason": "no response to GET"})
            continue
        # Try writing true
        patch_body, patch_status = api_patch(f"set/{path}", {"Value": "true"})
        outcomes.append({
            "path": path,
            "status": patch_status,
            "ok": patch_status in (200, 204),
            "response": patch_body,
        })

    return jsonify({"outcomes": outcomes})




@app.route("/api/discover", methods=["GET"])
def discover():
    deep = request.args.get("deep", "false").lower() == "true"
    result = crawl_api(deep_sample=deep)
    return jsonify(result)


@app.route("/api/discover/text", methods=["GET"])
def discover_text():
    deep = request.args.get("deep", "false").lower() == "true"
    result = crawl_api(deep_sample=deep)
    if "error" in result:
        return jsonify(result), 400
    text = format_crawl_as_text(result)
    return jsonify({"text": text})


@app.route("/api/discover/save", methods=["POST"])
def discover_save():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text")
    if not text:
        deep = bool(data.get("deep", False))
        result = crawl_api(deep_sample=deep)
        if "error" in result:
            return jsonify(result), 400
        text = format_crawl_as_text(result)
    fname = f"tsw_api_discovery_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    fpath = os.path.join(EXPORTS_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(text)
    return jsonify({"ok": True, "path": fpath, "text": text})


# ---- real weather sync -----------------------------------------------

@app.route("/api/weather/start", methods=["POST"])
def weather_start():
    WEATHER_STATE["enabled"] = True
    weather_log("Weather sync started")
    return jsonify({"ok": True})


@app.route("/api/weather/stop", methods=["POST"])
def weather_stop():
    WEATHER_STATE["enabled"] = False
    weather_log("Weather sync stopped")
    return jsonify({"ok": True})


@app.route("/api/weather/status", methods=["GET"])
def weather_status():
    with _weather_lock:
        return jsonify(dict(WEATHER_STATE))


@app.route("/api/loco", methods=["GET"])
def loco_identity():
    name = find_loco_class()
    raw = get_current_raw_object_class()
    display_name = name  # what gets shown/returned; may be overridden below
    speedometer = "digital"  # default for unconfigured trains

    # Legacy fallback values, used only if nothing in Known Trains v2 matches
    # this loco at all.
    max_speed = loco_profiles.get_effective_max_speed_mph(raw) if raw else loco_profiles.DEFAULT_MAX_SPEED_MPH
    dial_max = max_speed * 1.2 if max_speed else None

    resolved = None

    # 1) Direct match: this raw/clean identity has its own train_classes row
    # (covers ordinary trains AND the non-destructive Variants feature,
    # since a variant row keeps its own group_id/subclass_id once attached).
    # TSW's own API is inconsistent about what it reports poll to poll, and
    # a user's configured display name is often livery-suffixed rather than
    # an exact match to either raw or clean - find_train_class_for_identity
    # tries several strategies (raw, clean, display name, and bare-class
    # prefix of a livery-suffixed display name) so the SAME row is found
    # consistently regardless of which form TSW happens to report this poll.
    own_row = train_classes_db.find_train_class_for_identity(raw, name)
    if own_row:
        group = train_classes_db.get_group(own_row["group_id"]) if own_row.get("group_id") else None
        subclass = train_classes_db.get_subclass(own_row["subclass_id"]) if own_row.get("subclass_id") else None
        resolved = train_classes_db.resolve_speeds(own_row, group=group, subclass=subclass)

        # If this row is a Variant (added via the non-destructive "Variants"
        # panel), display attributes should come from the train it's
        # attached to - it's meant to present as that train, sharing its
        # name/photo/etc, with only its own subclass (if any) able to give
        # it a different speed. The variant row's own group_id was already
        # copied from the parent when it was attached, so speed resolution
        # above is already correct either way.
        display_row = own_row
        if own_row.get("variant_of_class_id"):
            parent = train_classes_db.get_train_class(own_row["variant_of_class_id"])
            if parent:
                display_row = parent

        if display_row.get("display_name"):
            display_name = display_row["display_name"]
        if display_row.get("speedometer") in ("analogue", "digital"):
            speedometer = display_row["speedometer"]

    # 2) Old-style merge alias: the source row was deleted and future
    # sightings redirect to a target class, optionally with its own
    # subclass override. Kept for backward compatibility with data merged
    # before the non-destructive Variants feature existed.
    if resolved is None:
        alias = train_classes_db.get_alias_for_raw(raw, clean_name=name) if (raw or name) else None
        if alias:
            target = train_classes_db.get_train_class(alias["target_class_id"])
            if target:
                group = train_classes_db.get_group(target["group_id"]) if target.get("group_id") else None
                subclass = train_classes_db.get_subclass(alias["subclass_id"]) if alias.get("subclass_id") else None
                resolved = train_classes_db.resolve_speeds(target, group=group, subclass=subclass)
                if target.get("display_name"):
                    display_name = target["display_name"]
                if target.get("speedometer") in ("analogue", "digital"):
                    speedometer = target["speedometer"]

    if resolved is not None:
        if resolved.get("max_speed_mph") is not None:
            max_speed = resolved["max_speed_mph"]
        if resolved.get("dial_max_mph") is not None:
            dial_max = resolved["dial_max_mph"]

    return jsonify({
        "name": display_name,
        "raw_object_class": raw,
        "max_speed_mph": max_speed,
        "dial_max_mph": dial_max,
        "speedometer": speedometer,
    })


@app.route("/api/loco/profiles", methods=["GET"])
def loco_profiles_list():
    return jsonify({"profiles": loco_profiles.list_profiles()})


@app.route("/api/loco/max_speed", methods=["POST"])
def loco_set_max_speed():
    body = request.get_json(force=True, silent=True) or {}
    raw = (body.get("raw_object_class") or "").strip()
    mph = body.get("max_speed_mph")
    if not raw:
        return jsonify({"error": "missing_raw_object_class"}), 400
    try:
        mph = float(mph)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_max_speed_mph"}), 400
    if mph <= 0 or mph > 500:
        return jsonify({"error": "max_speed_out_of_range"}), 400
    ok = loco_profiles.set_max_speed(raw, mph)
    if not ok:
        return jsonify({"error": "unknown_loco", "detail": "This loco hasn't been seen yet - drive it once first."}), 404
    return jsonify({"ok": True, "raw_object_class": raw, "max_speed_mph": mph})


@app.route("/api/location", methods=["GET"])
def player_location():
    loc = get_player_location()
    return jsonify(loc or {"latitude": None, "longitude": None})


# ---- imported timetables (real data, via import_from_other_hud.py) -------

@app.route("/api/timetables", methods=["GET"])
def timetables_search():
    q = request.args.get("q")
    route_id = request.args.get("route_id", type=int)
    limit = min(request.args.get("limit", default=100, type=int) or 100, 500)
    offset = request.args.get("offset", default=0, type=int) or 0
    results = timetable_db.search_journeys(query=q, route_id=route_id, limit=limit, offset=offset)
    return jsonify({"journeys": results})


@app.route("/api/timetables/<int:journey_id>", methods=["GET"])
def timetable_detail(journey_id):
    journey = timetable_db.get_journey(journey_id)
    if not journey:
        return jsonify({"error": "not_found"}), 404
    return jsonify(journey)


@app.route("/api/timetables/<int:journey_id>", methods=["PATCH"])
def timetable_update(journey_id):
    body = request.get_json(force=True, silent=True) or {}
    ok = timetable_db.update_journey(journey_id, body)
    if not ok:
        return jsonify({"error": "no_editable_fields_provided"}), 400
    return jsonify(timetable_db.get_journey(journey_id))


@app.route("/api/timetables/segments/<int:segment_id>", methods=["PATCH"])
def timetable_segment_update(segment_id):
    body = request.get_json(force=True, silent=True) or {}
    ok = timetable_db.update_segment(segment_id, body)
    if not ok:
        return jsonify({"error": "no_editable_fields_provided"}), 400
    return jsonify({"ok": True})


@app.route("/api/timetables/stops/<int:stop_id>", methods=["PATCH"])
def timetable_stop_update(stop_id):
    body = request.get_json(force=True, silent=True) or {}
    ok = timetable_db.update_stop(stop_id, body)
    if not ok:
        return jsonify({"error": "no_editable_fields_provided"}), 400
    return jsonify({"ok": True})


# ---- imported train classes (real data, via import_from_other_hud.py) ----




# ---- Known Trains v2: groups, subclasses, resolved list ------------------

@app.route("/api/known_trains/groups", methods=["GET"])
def known_trains_list_groups():
    return jsonify({"groups": train_classes_db.list_groups()})


@app.route("/api/known_trains/groups", methods=["POST"])
def known_trains_create_group():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    group_id = train_classes_db.create_group(
        name, body.get("default_max_speed_mph"), body.get("default_dial_max_mph"), body.get("hud_panels")
    )
    return jsonify(train_classes_db.get_group(group_id))


@app.route("/api/known_trains/groups/<int:group_id>", methods=["GET"])
def known_trains_get_group(group_id):
    group = train_classes_db.get_group(group_id)
    if not group:
        return jsonify({"error": "not_found"}), 404
    group["subclasses"] = train_classes_db.list_subclasses(group_id)
    group["members"] = [
        tc for tc in train_classes_db.list_train_classes(visible_only=False) if tc.get("group_id") == group_id
    ]
    return jsonify(group)


@app.route("/api/known_trains/groups/<int:group_id>", methods=["PATCH"])
def known_trains_update_group(group_id):
    body = request.get_json(force=True, silent=True) or {}
    ok = train_classes_db.update_group(group_id, body)
    if not ok:
        return jsonify({"error": "no_editable_fields_provided"}), 400
    return jsonify(train_classes_db.get_group(group_id))


@app.route("/api/known_trains/groups/<int:group_id>/subclasses", methods=["POST"])
def known_trains_create_subclass(group_id):
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    subclass_id = train_classes_db.create_subclass(
        group_id, name, body.get("max_speed_override_mph"), body.get("dial_max_override_mph")
    )
    return jsonify({"id": subclass_id, "group_id": group_id, "name": name})


@app.route("/api/known_trains/subclasses/<int:subclass_id>", methods=["PATCH"])
def known_trains_update_subclass(subclass_id):
    body = request.get_json(force=True, silent=True) or {}
    ok = train_classes_db.update_subclass(subclass_id, body)
    if not ok:
        return jsonify({"error": "no_editable_fields_provided"}), 400
    return jsonify({"ok": True})


@app.route("/api/known_trains/subclasses/<int:subclass_id>", methods=["DELETE"])
def known_trains_delete_subclass(subclass_id):
    train_classes_db.delete_subclass(subclass_id)
    return jsonify({"ok": True})


@app.route("/api/known_trains/groups/<int:group_id>", methods=["DELETE"])
def known_trains_delete_group(group_id):
    train_classes_db.delete_group(group_id)
    return jsonify({"ok": True})


# ---- Class Families ("Groups" in the UI) -----------------------------------
# A family is a higher-level grouping of several Classes - e.g. Class 801,
# 802 and 805 all belonging to the "Class 8xx" family. Assigning a Class to
# a family is done via PATCH /api/known_trains/groups/<id> with family_id,
# reusing the existing Class-update endpoint above.

@app.route("/api/known_trains/families", methods=["GET"])
def known_trains_list_families():
    return jsonify({"families": train_classes_db.list_families()})


@app.route("/api/known_trains/families", methods=["POST"])
def known_trains_create_family():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    family_id = train_classes_db.create_family(name)
    return jsonify(train_classes_db.get_family(family_id))


@app.route("/api/known_trains/families/<int:family_id>", methods=["GET"])
def known_trains_get_family(family_id):
    family = train_classes_db.get_family(family_id)
    if not family:
        return jsonify({"error": "not_found"}), 404
    return jsonify(family)


@app.route("/api/known_trains/families/<int:family_id>", methods=["PATCH"])
def known_trains_update_family(family_id):
    body = request.get_json(force=True, silent=True) or {}
    ok = train_classes_db.update_family(family_id, body)
    if not ok:
        return jsonify({"error": "no_editable_fields_provided"}), 400
    return jsonify(train_classes_db.get_family(family_id))


@app.route("/api/known_trains/families/<int:family_id>", methods=["DELETE"])
def known_trains_delete_family(family_id):
    train_classes_db.delete_family(family_id)
    return jsonify({"ok": True})


# ---- Operators ------------------------------------------------------------

@app.route("/api/operators", methods=["GET"])
def operators_list():
    return jsonify({"operators": train_classes_db.list_operators()})


@app.route("/api/operators", methods=["POST"])
def operators_create():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    op_id = train_classes_db.create_operator(name, body.get("short_code"), body.get("logo_path"))
    return jsonify(train_classes_db.get_operator(op_id)), 201


@app.route("/api/operators/<int:operator_id>", methods=["GET"])
def operators_get(operator_id):
    op = train_classes_db.get_operator(operator_id)
    if not op:
        return jsonify({"error": "not_found"}), 404
    op["liveries"] = train_classes_db.list_liveries(operator_id)
    return jsonify(op)


@app.route("/api/operators/<int:operator_id>", methods=["PATCH"])
def operators_update(operator_id):
    body = request.get_json(force=True, silent=True) or {}
    train_classes_db.update_operator(operator_id, body)
    return jsonify(train_classes_db.get_operator(operator_id))


@app.route("/api/operators/<int:operator_id>", methods=["DELETE"])
def operators_delete(operator_id):
    train_classes_db.delete_operator(operator_id)
    return jsonify({"ok": True})


@app.route("/api/operators/<int:operator_id>/liveries", methods=["GET"])
def operators_list_liveries(operator_id):
    return jsonify({"liveries": train_classes_db.list_liveries(operator_id)})


@app.route("/api/operators/<int:operator_id>/liveries", methods=["POST"])
def operators_create_livery(operator_id):
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    code = (body.get("code") or "").strip()
    if not name or not code:
        return jsonify({"error": "name_and_code_required"}), 400
    livery_id = train_classes_db.create_livery(
        operator_id, name, code, body.get("colour"), body.get("is_default", 0)
    )
    return jsonify({"id": livery_id, "operator_id": operator_id, "name": name, "code": code}), 201


@app.route("/api/operators/liveries/<int:livery_id>", methods=["PATCH"])
def operators_update_livery(livery_id):
    body = request.get_json(force=True, silent=True) or {}
    train_classes_db.update_livery(livery_id, body)
    return jsonify({"ok": True})


@app.route("/api/operators/liveries/<int:livery_id>", methods=["DELETE"])
def operators_delete_livery(livery_id):
    train_classes_db.delete_livery(livery_id)
    return jsonify({"ok": True})


@app.route("/api/known_trains/list", methods=["GET"])
def known_trains_list():
    """The main list view: every train class with its resolved speeds,
    completion status, and power label already computed server-side -
    the list page just renders what it's given, no client-side resolution
    logic duplicated."""
    show_hidden = request.args.get("show_hidden") == "1"
    # Known Trains v2 is DRIVEN TRAINS ONLY (times_seen > 0). Catalog imports
    # enrich existing rows but never surface a train that has not actually
    # been driven in the game - see the spec, section 3C.
    classes = train_classes_db.list_train_classes(visible_only=not show_hidden, driven_only=True)
    groups_by_id = {g["id"]: g for g in train_classes_db.list_groups()}
    families_by_id = {f["id"]: f for f in train_classes_db.list_families()}
    operators_by_id = {}
    liveries_by_key = {}
    for op in train_classes_db.list_operators():
        try:
            op_id = int(op["id"])
        except (ValueError, TypeError):
            continue
        operators_by_id[op_id] = op
        for liv in train_classes_db.list_liveries(op_id):
            code = (liv.get("code") or "").strip().lower()
            if code:
                liveries_by_key[(op_id, code)] = liv

    results = []
    for tc in classes:
        group = groups_by_id.get(tc.get("group_id"))
        resolved = train_classes_db.resolve_speeds(tc, group=group)
        status = train_classes_db.compute_completion(tc)
        power = train_classes_db.compute_power_label(tc.get("is_steam"), tc.get("is_diesel"), tc.get("is_electric"))
        # livery_id stores the operator_id; livery_name stores the livery code.
        # Prefer the specific livery's own colour over the operator's default
        # colour, so each pill reflects the actual train livery.
        colour = None
        try:
            op_id = int(tc.get("livery_id") or 0)
        except (ValueError, TypeError):
            op_id = None
        if op_id:
            code = (tc.get("livery_name") or "").strip().lower()
            livery = liveries_by_key.get((op_id, code)) if code else None
            if livery and livery.get("colour"):
                colour = livery["colour"]
            else:
                op = operators_by_id.get(op_id)
                if op and op.get("colour"):
                    colour = op["colour"]
        family = families_by_id.get(group.get("family_id")) if group else None
        results.append({
            **tc,
            "resolved_max_speed_mph": resolved["max_speed_mph"],
            "resolved_dial_max_mph": resolved["dial_max_mph"],
            "status": status,
            "power_label": power,
            "group_name": group["name"] if group else None,
            "family_id": family["id"] if family else None,
            "family_name": family["name"] if family else None,
            "operator_colour": colour,
        })

    return jsonify({
        "classes": results,
        "needs_attention": train_classes_db.needs_attention(driven_only=True),
    })


@app.route("/api/known_trains/classes/<int:train_class_id>", methods=["GET"])
def known_trains_get(train_class_id):
    """Get a single train class."""
    tc = train_classes_db.get_train_class(train_class_id)
    if not tc:
        return jsonify({"error": "not_found"}), 404
    return jsonify(tc)


@app.route("/api/known_trains/restore", methods=["POST"])
def known_trains_restore():
    """Restore known trains data from a backup JSON."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        classes = body.get("classes", [])
        restored = 0
        for tc in classes:
            if not tc.get("source_name"):
                continue
            # Try to update existing by source_name, or insert new
            existing = train_classes_db.get_train_class_by_source_name(tc["source_name"])
            fields = {k: v for k, v in tc.items() if k not in ("id", "source_id", "times_seen", "imported_at")}
            if existing:
                train_classes_db.update_train_class(existing["id"], fields)
            else:
                train_classes_db.record_live_sighting(
                    {"source_name": tc["source_name"], "max_speed_ms": None},
                    clean_name=tc.get("display_name") or tc["source_name"]
                )
                new_tc = train_classes_db.get_train_class_by_source_name(tc["source_name"])
                if new_tc:
                    train_classes_db.update_train_class(new_tc["id"], fields)
            restored += 1
        return jsonify({"ok": True, "restored": restored})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/known_trains/wipe_all", methods=["POST"])
def known_trains_wipe_all():
    """Deletes every train class, group, subclass, operator, and livery -
    a full reset of all Known Trains data on THIS machine. Also clears the
    separate loco_profiles.db sighting cache so old classes can't reappear
    from it. Irreversible - the download/backup button should be used first
    if any of this data is worth keeping."""
    try:
        train_classes_db.clear_all()
        loco_profiles.clear_all()
        return jsonify({"ok": True})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/known_trains/classes/<int:train_class_id>", methods=["PATCH"])
def known_trains_update(train_class_id):
    """Update a single train class."""
    try:
        # Check if record exists
        tc_before = train_classes_db.get_train_class(train_class_id)
        if not tc_before:
            return jsonify({"error": "not_found"}), 404
        
        body = request.get_json(force=True, silent=True) or {}
        if not body:
            return jsonify({"error": "no_fields"}), 400
            
        applied = train_classes_db.update_train_class(train_class_id, body)
        if not applied:
            return jsonify({"error": "update_failed"}), 400
            
        tc = train_classes_db.get_train_class(train_class_id)
        if not tc:
            return jsonify({"error": "not_found"}), 404
        return jsonify(tc)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/known_trains/classes/<int:target_class_id>/variants", methods=["GET"])
def known_trains_list_variants(target_class_id):
    """Every train currently folded in as a variant of this one - the
    Operators/liveries-style list on the Edit page."""
    return jsonify({"variants": train_classes_db.list_variants_for_target(target_class_id)})


@app.route("/api/known_trains/classes/<int:target_class_id>/variants", methods=["POST"])
def known_trains_add_variant(target_class_id):
    """Adds a train as a variant of this one: it's not deleted, just hidden
    from Known Trains and tagged with this train's group + the chosen
    subclass. Fully reversible via DELETE below."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        source_class_id = body.get("source_class_id")
        subclass_id = body.get("subclass_id")
        if not source_class_id:
            return jsonify({"error": "missing_source_class_id"}), 400
        ok, error = train_classes_db.set_variant(
            int(source_class_id), target_class_id,
            int(subclass_id) if subclass_id else None,
        )
        if not ok:
            return jsonify({"error": error}), 400
        return jsonify({"ok": True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/known_trains/classes/<int:variant_class_id>/variant", methods=["DELETE"])
def known_trains_remove_variant(variant_class_id):
    """Un-hides a variant - it reappears in Known Trains as its own entry."""
    train_classes_db.remove_variant(variant_class_id)
    return jsonify({"ok": True})


@app.route("/api/known_trains/classes/<int:train_class_id>/merge_into", methods=["POST"])
def known_trains_merge(train_class_id):
    """Merges a train class into an existing target train class. The source
    row is deleted; its raw identifiers are remembered so future live
    sightings are attributed to the target. Optional subclass_id lets this
    specific variant resolve its own speed via the target's group."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        target_class_id = body.get("target_class_id")
        subclass_id = body.get("subclass_id")
        if not target_class_id:
            return jsonify({"error": "missing_target_class_id"}), 400
        ok, error = train_classes_db.merge_train_class_into(
            train_class_id, int(target_class_id),
            int(subclass_id) if subclass_id else None,
        )
        if not ok:
            return jsonify({"error": error}), 400
        return jsonify({"ok": True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/known_trains/classes/<int:train_class_id>/aliases", methods=["GET"])
def known_trains_list_aliases(train_class_id):
    """Every train currently merged into this one, so the Edit page can show
    an "Add / remove trains" list alongside the merge panel."""
    return jsonify({"aliases": train_classes_db.list_aliases_for_target(train_class_id)})


@app.route("/api/known_trains/aliases/<int:alias_id>", methods=["DELETE"])
def known_trains_delete_alias(alias_id):
    """Un-merges a previously-merged train. Future sightings of that raw
    class will create their own entry again instead of folding into the
    target."""
    train_classes_db.delete_alias(alias_id)
    return jsonify({"ok": True})


# ---- lifecycle ------------------------------------------------------------

@app.route("/api/shutdown", methods=["POST"])
def shutdown():
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# pywebview shell
# --------------------------------------------------------------------------

class JSApi:
    """Exposed to the page as window.pywebview.api.* for native dialogs."""

    def __init__(self):
        self.window = None

    def choose_folder(self):
        import webview
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if result:
            return result[0]
        return None


def install_crash_logging():
    """Write any unhandled exception, in any thread, to a file on disk.
    This runs regardless of whether the UI can respond, so it survives a
    freeze that makes the app impossible to interact with."""
    def write_crash(text):
        try:
            with open(CRASH_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.now().isoformat(timespec='seconds')} ---\n{text}\n")
        except Exception:
            pass

    def sys_hook(exc_type, exc_value, exc_tb):
        import traceback
        write_crash("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))

    def thread_hook(args):
        import traceback
        write_crash("".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook


def run_heartbeat():
    """Appends a timestamp to heartbeat.log every 2 seconds and refreshes a
    full diagnostics snapshot every 5 seconds - entirely independent of
    Flask and the window. If the app ever freezes, whatever's on disk in the
    diagnostics folder is never more than a few seconds stale, and whether
    the heartbeat itself stopped tells us if the whole process died vs just
    the window/UI."""
    tick = 0
    while True:
        try:
            with _debug_log_lock:
                lines = []
                if os.path.exists(HEARTBEAT_FILE):
                    with open(HEARTBEAT_FILE, "r", encoding="utf-8") as f:
                        lines = f.readlines()[-200:]
                lines.append(datetime.now().isoformat(timespec="seconds") + "\n")
                with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
                    f.writelines(lines)
            if tick % 5 == 0:  # full snapshot roughly every 10s
                snapshot_path = os.path.join(DIAG_DIR, "latest_snapshot.txt")
                with open(snapshot_path, "w", encoding="utf-8") as f:
                    f.write(build_diagnostics_text())
        except Exception:
            pass
        tick += 1
        time.sleep(2)


def run_flask():
    # 0.0.0.0 = listen on every network interface on this PC, so devices on
    # the same Wi-Fi/LAN (like a tablet) can reach it via this PC's LAN IP.
    # This does NOT expose it to the internet - that would require your
    # router to be configured to forward this port from the outside, which
    # nothing here does. Only devices already on your local network can connect.
    #
    # threaded=True is important: without it, this server can only handle
    # ONE request at a time, so a single slow request (e.g. waiting on the
    # game, or a tablet polling the dashboard) blocks every other request -
    # including the app's own page loads - and the whole window appears to
    # freeze ("Not Responding").
    #
    # Always plain HTTP, deliberately (spec section 3A). TLS was only ever
    # needed for Service Worker registration on other devices, and offline
    # sync is not a feature of this app.
    global SERVER_PORT
    app.run(host="0.0.0.0", port=SERVER_PORT, debug=False, use_reloader=False, threaded=True)


def run_in_system_browser():
    """Fallback mode: skip pywebview/WebView2 entirely and just open the
    app's normal default browser (Edge, Chrome, whatever is set). This
    completely sidesteps WebView2 COM interop bugs, at the cost of it being
    a browser tab instead of a dedicated app window. The Exit button still
    works (it shuts down the local server); closing the tab just leaves the
    server running quietly in this console window until Exit is clicked or
    the window is closed.
    
    If TSW_HUD_NO_BROWSER is set (parent launcher will do this), skip opening
    the browser and just run the server — the parent launcher will embed it."""
    no_browser = os.environ.get("TSW_HUD_NO_BROWSER", "").lower() == "true"
    if not no_browser:
        print("Running in browser mode (no native window) - this avoids a class")
        print("of WebView2 bugs some Windows setups hit with the native window.")
        print(f"Opening http://127.0.0.1:{SERVER_PORT}/ in your default browser...")
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{SERVER_PORT}/")
        print("You can close this window with Ctrl+C, or click Exit in the app.")
    else:
        print("Running in headless server mode (parent launcher will embed the UI).")
        print(f"TSW Hud server running on http://127.0.0.1:{SERVER_PORT}/")
        print("Press Ctrl+C to stop.")
    while True:
        time.sleep(1)


def main():
    install_crash_logging()

    heartbeat_thread = threading.Thread(target=run_heartbeat, daemon=True)
    heartbeat_thread.start()

    weather_thread = threading.Thread(target=weather_sync_loop, daemon=True)
    weather_thread.start()

    other_hud_thread = threading.Thread(
        target=other_hud_sync.other_hud_sync_loop,
        args=(CONFIG, save_config),
        kwargs={"enabled_fn": lambda: CONFIG.get("other_hud_sync_enabled", True)},
        daemon=True,
    )
    other_hud_thread.start()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    time.sleep(0.6)  # let Flask bind before the window tries to load it

    # Check if running in headless mode (parent launcher will handle UI)
    no_browser = os.environ.get("TSW_HUD_NO_BROWSER", "").lower() == "true"
    if no_browser or "--browser" in sys.argv:
        run_in_system_browser()
        return

    try:
        import webview
    except ImportError:
        print("pywebview is not installed. Run: pip install -r requirements.txt")
        print("Falling back to opening in your default browser instead.")
        run_in_system_browser()
        return

    try:
        js_api = JSApi()
        window = webview.create_window(
            "TSW Hud",
            f"http://127.0.0.1:{SERVER_PORT}/",
            width=1280,
            height=860,
            min_size=(900, 600),
            js_api=js_api,
        )
        js_api.window = window
        print("Starting native window...")
        webview.start()
        os._exit(0)  # make sure the whole process (and Flask thread) dies with the window
    except Exception as e:
        print(f"\n!!! Native window (pywebview) failed to start: {e}")
        print("Falling back to opening in your default browser instead.")
        import traceback
        traceback.print_exc()
        # Give user a moment to see the error
        time.sleep(1)
        run_in_system_browser()


if __name__ == "__main__":
    main()
