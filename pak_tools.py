"""
pak_tools.py

Wrapper around `repak` (github.com/trumank/repak), the Unreal .pak
unpacker, for reading Train Sim World's route archives.

This is step two of the offline-timetable pipeline:

    game_files.py   find the install and its route .pak files      [done]
    pak_tools.py    list / extract what's inside those paks        [this]
    (parser)        turn extracted assets into journeys+stops      [next]
    timetable_db    store them                                     [exists]

WHY LISTING COMES BEFORE EXTRACTING
------------------------------------
A TSW route pak is large, and unpacking one wholesale is slow and fills
the disk. `repak list` prints the internal file paths WITHOUT extracting
anything, which is enough to answer the question that actually blocks the
parser: where does timetable data live inside the archive, and what
format is it in. Once that's known, only the relevant subset needs
extracting.

ON REPAK'S CLI
---------------
The exact subcommand names are verified at runtime rather than assumed -
run_help() parses `repak --help` and the code adapts to whatever that
build actually supports. Guessing at CLI flags and having them silently
fail is exactly the sort of thing that wastes a debugging cycle, so the
real stderr is always surfaced instead of being swallowed.

Nothing here writes to the game directory. Extraction output goes to a
directory the caller chooses.
"""

import os
import re
import subprocess


DEFAULT_TIMEOUT = 300  # unpacking a big route pak genuinely can take minutes


def search_locations():
    """Every folder checked for repak, in order. Returned to the UI so it
    can show the exact path to drop the binary into rather than leaving
    "next to the app" open to interpretation."""
    here = os.path.dirname(os.path.abspath(__file__))
    return {
        "app_folder": here,
        "resources_folder": os.path.join(here, "resources"),
        "tools_folder": os.path.join(here, "tools"),
        "path_entries": [p for p in os.environ.get("PATH", "").split(os.pathsep) if p][:20],
    }


def find_repak():
    """Locates the repak binary. Checks next to this app, a resources/ or
    tools/ subfolder, then PATH."""
    names = ["repak.exe", "repak"]
    here = os.path.dirname(os.path.abspath(__file__))
    dirs = [here, os.path.join(here, "resources"), os.path.join(here, "tools")]
    dirs += [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    for d in dirs:
        for n in names:
            try:
                c = os.path.join(d, n)
                if os.path.isfile(c):
                    return c
            except OSError:
                continue
    return None


def _run(args, timeout=DEFAULT_TIMEOUT):
    """Runs repak and returns (ok, stdout, stderr). Never raises on a
    non-zero exit - the caller gets the real error text to act on."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            # Don't pop a console window on Windows.
            creationflags=(subprocess.CREATE_NO_WINDOW
                           if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                           else 0),
        )
        return proc.returncode == 0, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return False, "", f"repak timed out after {timeout}s"
    except FileNotFoundError as e:
        return False, "", f"repak not runnable: {e}"
    except Exception as e:
        return False, "", f"repak failed: {e}"


def capabilities():
    """Asks repak what it can actually do, rather than assuming a CLI
    shape. Returns the help text plus the subcommands detected in it."""
    exe = find_repak()
    locations = search_locations()
    if not exe:
        return {
            "found": False,
            "put_it_here": locations["app_folder"],
            "searched": locations,
            "hint": ("repak not found. Download it from "
                     "https://github.com/trumank/repak/releases (the Windows "
                     "build), unzip it, and put repak.exe in the folder shown "
                     "above - the same folder as app.py."),
        }
    ok, out, err = _run([exe, "--help"], timeout=30)
    text = out + "\n" + err
    # Subcommands appear as indented words in the help output.
    found = set()
    for word in ["list", "unpack", "info", "get", "pack", "verify"]:
        if re.search(rf"^\s+{word}\b", text, re.MULTILINE):
            found.add(word)
    version_ok, vout, verr = _run([exe, "--version"], timeout=30)
    return {
        "found": True,
        "path": exe,
        "put_it_here": locations["app_folder"],
        "version": (vout or verr).strip().splitlines()[0] if (vout or verr).strip() else None,
        "help_ok": ok,
        "subcommands": sorted(found),
        "help_text": text.strip()[:4000],
    }


def list_pak(pak_path, filter_keywords=None, aes_key=None, limit=400, path_filter=None):
    """Lists the internal paths inside a pak without extracting.

    filter_keywords: optional list of case-insensitive substrings; when
    given, matching entries are returned separately as `matches` so
    timetable-ish assets stand out from tens of thousands of others.
    aes_key: some Unreal paks are AES-encrypted; repak accepts a key.
    """
    exe = find_repak()
    if not exe:
        return {"error": "repak_not_found"}
    if not os.path.isfile(pak_path):
        return {"error": "pak_not_found", "pak_path": pak_path}

    args = [exe, "list"]
    if aes_key:
        args += ["--aes-key", aes_key]
    args.append(pak_path)

    ok, out, err = _run(args)
    if not ok:
        return {
            "error": "repak_list_failed",
            "pak_path": pak_path,
            "stderr": err.strip()[:2000],
            "stdout": out.strip()[:1000],
            "hint": ("If this mentions encryption, the pak needs an AES key. "
                     "If it mentions an unknown/unsupported version, this "
                     "repak build may be older than the game."),
        }

    entries = [ln.strip() for ln in out.splitlines() if ln.strip()]
    total_entries = len(entries)

    # Extensions and keyword matches are computed against the FULL list.
    # Previously the list was truncated to `limit` FIRST, so on a route pak
    # with more than `limit` entries the keyword search only ever saw the
    # alphabetically-earliest paths and never reached the folders that
    # actually matter. Only the returned `sample` is truncated now.
    result = {
        "pak_path": pak_path,
        "entry_count": total_entries,
        "truncated": False,
        "extensions": {},
    }

    for e in entries:
        ext = os.path.splitext(e)[1].lower()
        result["extensions"][ext] = result["extensions"].get(ext, 0) + 1

    # Directory summary: far more useful than tens of thousands of paths
    # when the question is "where does timetable data live". Counts entries
    # per plugin and per folder beneath each plugin's Content directory.
    plugins = {}
    content_dirs = {}
    for e in entries:
        parts = e.replace("\\", "/").split("/")
        # e.g. TS2Prototype/Plugins/DLC/FifeCircle/Content/Audio/...
        if "Plugins" in parts:
            i = parts.index("Plugins")
            if len(parts) > i + 2:
                plugin = parts[i + 2]
                plugins[plugin] = plugins.get(plugin, 0) + 1
        if "Content" in parts:
            i = parts.index("Content")
            if len(parts) > i + 1:
                # Group by the folder directly under Content, scoped to its
                # plugin so two routes' folders don't get merged together.
                scope = parts[i - 1] if i > 0 else "?"
                key = f"{scope}/Content/{parts[i + 1]}"
                content_dirs[key] = content_dirs.get(key, 0) + 1

    result["plugins"] = dict(sorted(plugins.items(), key=lambda kv: -kv[1]))
    result["content_folders"] = dict(
        sorted(content_dirs.items(), key=lambda kv: -kv[1])[:120])

    # Surface the folders that actually hold scheduling data, so the answer
    # isn't buried in a 50-row folder table.
    wanted = ("timetable", "journey", "scenario", "formation",
              "routedefinition", "gameplay")
    result["promising_folders"] = {
        k: v for k, v in sorted(content_dirs.items(), key=lambda kv: -kv[1])
        if any(w in k.lower() for w in wanted)
    }

    if path_filter:
        pf = path_filter.lower()
        filtered = [e for e in entries if pf in e.lower()]
        result["path_filter"] = path_filter
        result["filtered_count"] = len(filtered)
        result["filtered"] = filtered[:3000]

    if filter_keywords:
        kws = [k.lower() for k in filter_keywords]
        matched = [e for e in entries if any(k in e.lower() for k in kws)]
        result["match_count"] = len(matched)
        # Drop .uexp/.ubulk siblings (they double every entry) and asset
        # categories that swamp the result without being scheduling data.
        primary = [m for m in matched
                   if not m.lower().endswith((".uexp", ".ubulk"))
                   and not looks_like_noise(m)]
        result["matches"] = primary[:1500]
        result["matches_shown"] = len(result["matches"])

    result["sample"] = entries[:limit]
    result["sample_truncated"] = total_entries > limit
    return result


def unpack_pak(pak_path, out_dir, include=None, aes_key=None, timeout=DEFAULT_TIMEOUT):
    """Unpacks a pak into out_dir. `include` is an optional glob/prefix
    filter if this repak build supports one; when unsupported the whole
    pak is unpacked and the caller filters afterwards."""
    exe = find_repak()
    if not exe:
        return {"error": "repak_not_found"}
    if not os.path.isfile(pak_path):
        return {"error": "pak_not_found", "pak_path": pak_path}

    os.makedirs(out_dir, exist_ok=True)
    args = [exe, "unpack"]
    if aes_key:
        args += ["--aes-key", aes_key]
    args += ["--output", out_dir]
    if include:
        args += ["--include", include]
    args.append(pak_path)

    ok, out, err = _run(args, timeout=timeout)
    if not ok and include:
        # This build may not support --include; retry without it rather
        # than reporting a failure the user can't act on.
        args = [exe, "unpack"]
        if aes_key:
            args += ["--aes-key", aes_key]
        args += ["--output", out_dir, pak_path]
        ok, out, err = _run(args, timeout=timeout)

    if not ok:
        return {"error": "repak_unpack_failed", "stderr": err.strip()[:2000],
                "stdout": out.strip()[:1000]}

    written = 0
    for _root, _dirs, files in os.walk(out_dir):
        written += len(files)
    return {"ok": True, "out_dir": out_dir, "files_written": written,
            "stdout": out.strip()[:1000]}


# Substrings worth flagging when listing a pak. Tightened after a real
# listing: the first pass matched hundreds of audio cues, station crowd
# sounds and NPC meshes because "station"/"service"/"stop" appear all over
# an Unreal route. These are the terms that actually indicate scheduling
# data, and NOISE_DIRS filters out the asset categories that swamped it.
# Keywords, tightened twice against real listings. Notes on what was
# removed and why, so it doesn't get "helpfully" re-added:
#   "/tt_", "_tt_"  -> matched thousands of Map/Tiles/TT_x-10_y-1.umap
#                      terrain tiles. TT means "terrain tile" here, not
#                      timetable.
#   "railnetwork"   -> 2293 signal/OHLE/junction assets, no scheduling.
#   "station"/"service"/"stop" -> station audio, announcements, NPCs.
# Confirmed from a real Fife Circle listing: the scheduling data lives in
# a separate small plugin, <Route>_Route_Gameplay, under Content/Timetable
# (64 entries), with Journey/, Scenarios/ and CommonFormations/ beside it.
TIMETABLE_KEYWORDS = [
    "_route_gameplay/", "/timetable/", "/journey/", "/scenarios/",
    "/commonformations/", "/routedefinition/", "/formationdesigner/",
    "servicedefinition", "servicedata", "servicelist",
]

NOISE_DIRS = [
    "/audio/", "/characters/", "/meshes/", "/textures/", "/materials/",
    "/vfx/", "/fx/", "/animation/", "/anim/", "/collectables/",
    "/editorresources/", "/enginematerials/", "/enginesky/",
    # Added after a real listing: these three alone were 39,000+ entries
    # on one route and buried everything useful.
    "/map/tiles/", "/scenery/", "/landscapematerial",
    "/passengers/", "/interactive/", "/theme/",
]


def looks_like_noise(path):
    p = path.lower()
    return any(n in p for n in NOISE_DIRS)


# ---------------------------------------------------------------------------
# Timetable asset naming - CONFIRMED from a real Fife Circle listing, not
# guessed. One timetable is a set of assets:
#
#   <Route>_Timetable_TT.uasset                  the timetable itself
#   .../DataTracks/<...>_TT_MasterDataTrack      the master track
#   .../DataTracks/<...>_TT_<Group>_Layer_DataTrack   one per service group
#                                                (Class220, Class380, LNER801,
#                                                 Leven_Branch, Trams, RHTT...)
#   .../Formations/<Class>/FRM_*.uasset          consists of what stock
#
# The "_TT" suffix marks a timetable. Scenarios and training use it too
# (FCE_Sc01_TT, FCE_RI_TT), so those are separated out below rather than
# being mistaken for the route timetable.
#
# A route can have MORE THAN ONE timetable (Fife Circle has a Class 170 one
# and a Sprinter Express one), and the extra ones are not necessarily in the
# route's own pak - hence scan_all_paks().
# ---------------------------------------------------------------------------

def classify_timetable_asset(path):
    """Buckets a pak entry into the kind of timetable asset it is, or None.

    Rewritten after a real 67-pak scan exposed three problems with keying
    off the "_TT" filename suffix:

      1. It is NOT universal. CL-Intermodal, CL-Nuclear and CreweManchester
         all have <name>_Layer_DataTrack assets whose parent timetable is
         called e.g. CLI_EMKTimetable - no _TT anywhere. Those routes came
         back with layer tracks but zero timetables.
      2. It matched non-assets: ART_TT.uplugin, ART_TT.dlc, ART_TT.locres
         (a plugin that happens to be NAMED ART_TT), and en_TT.res - ICU
         locale data for Trinidad & Tobago.
      3. It matched a font texture, T_uc_TT.uasset.

    So the primary rule is now WHERE the asset sits, not what it's called:
    a .uasset directly inside a Timetable/ or ServiceMode/ folder is a
    timetable. The _TT suffix is kept only as a fallback, and only for
    .uasset files outside obvious non-timetable folders.
    """
    p = path.replace("\\", "/")
    low = p.lower()

    # Only real assets. Kills .uplugin/.dlc/.locres/.res, and the
    # .uexp/.ubulk siblings that would double every entry.
    if not low.endswith(".uasset"):
        return None

    # Folders that contain look-alike names but never timetables.
    if any(n in low for n in ("/textures/", "/font/", "/localization/",
                              "/internationalization/", "/fonts/",
                              # Core/Assets/HUD/MenuScreens/Timetable/ is the
                              # game's own timetable MENU - 23 UI widgets
                              # (ChapterMenu, StarRatingWidget, filter bars).
                              # Real timetable data never lives under HUD/UI.
                              "/hud/", "/menuscreens/", "/widgets/", "/ui/",
                              "/core/assets/")):
        return None

    parts = p.split("/")
    parent = parts[-2].lower() if len(parts) >= 2 else ""
    grandparent = parts[-3].lower() if len(parts) >= 3 else ""
    stem = os.path.splitext(parts[-1])[0]

    if parent == "datatracks":
        if "masterdatatrack" in low:
            return "master_datatrack"
        if "_layer_datatrack" in low:
            return "layer_datatrack"
        return "datatrack"
    if parent == "formations" or grandparent == "formations":
        return "formation"

    # Directly inside a Timetable/ or ServiceMode/ folder - the reliable
    # signal. ServiceMode covers MML, whose timetable is
    # .../ServiceMode/LDN_ServiceMode_TT.uasset
    if parent in ("timetable", "timetables", "servicemode"):
        return "timetable"

    if "/scenarios/" in low and stem.lower().endswith("_tt"):
        return "scenario_timetable"
    # "training" as a path segment, not just "/training/" - the Training
    # Centre uses TrainingND24/, which the stricter test missed.
    if "training" in low and stem.lower().endswith("_tt"):
        return "training_timetable"
    if stem.lower().endswith("_tt"):
        return "timetable_by_name"
    return None


def find_timetables(pak_path, aes_key=None):
    """Lists one pak and returns only its timetable-related assets, grouped
    by kind. Much lighter to read than a full listing."""
    listing = list_pak(pak_path, aes_key=aes_key, limit=1)
    if listing.get("error"):
        return listing

    exe = find_repak()
    args = [exe, "list"]
    if aes_key:
        args += ["--aes-key", aes_key]
    args.append(pak_path)
    ok, out, err = _run(args)
    if not ok:
        return {"error": "repak_list_failed", "stderr": err.strip()[:1000]}

    groups = {}
    for line in out.splitlines():
        entry = line.strip()
        if not entry:
            continue
        kind = classify_timetable_asset(entry)
        if kind:
            groups.setdefault(kind, []).append(entry)

    return {
        "pak_path": pak_path,
        "pak_name": os.path.basename(pak_path),
        "entry_count": listing.get("entry_count"),
        "timetables": groups.get("timetable", []),
        "timetables_by_name": groups.get("timetable_by_name", []),
        "master_datatracks": groups.get("master_datatrack", []),
        "layer_datatracks": groups.get("layer_datatrack", []),
        "formations": groups.get("formation", [])[:200],
        "scenario_timetables": groups.get("scenario_timetable", []),
        "training_timetables": groups.get("training_timetable", []),
        "counts": {k: len(v) for k, v in groups.items()},
    }


def scan_all_paks(pak_dir, aes_key=None, max_paks=60):
    """Runs find_timetables over every .pak in a folder.

    A route's extra timetables (e.g. Fife Circle's Sprinter Express one)
    don't necessarily live in that route's own pak, so answering "what
    timetables do I actually have" means looking across all of them."""
    if not os.path.isdir(pak_dir):
        return {"error": "pak_dir_not_found", "pak_dir": pak_dir}

    paks = sorted(
        os.path.join(pak_dir, f) for f in os.listdir(pak_dir)
        if f.lower().endswith(".pak")
    )[:max_paks]

    results = []
    total_tt = 0
    for p in paks:
        r = find_timetables(p, aes_key=aes_key)
        if r.get("error"):
            results.append({"pak_name": os.path.basename(p), "error": r["error"]})
            continue
        n = len(r["timetables"]) + len(r.get("timetables_by_name", []))
        total_tt += n
        # Only report paks that actually contain a route timetable.
        if n or r["layer_datatracks"] or r.get("counts", {}).get("datatrack"):
            results.append({
                "pak_name": r["pak_name"],
                "pak_path": r["pak_path"],
                "timetables": r["timetables"],
                "timetables_by_name": r.get("timetables_by_name", []),
                "layer_datatracks": r["layer_datatracks"],
                "counts": r["counts"],
            })

    return {
        "pak_dir": pak_dir,
        "paks_scanned": len(paks),
        "paks_with_timetables": len(results),
        "total_timetables": total_tt,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Asset inspection
#
# Before writing any parser, the question worth answering is simply: is the
# useful content (station names, service codes, times) present as readable
# strings, or is it packed binary?
#
# Unreal stores an FName table of length-prefixed strings near the front of
# a .uasset, and property names are FNames too. So pulling the strings out
# is low-risk, version-independent, and answers the question directly -
# unlike guessing at the binary layout, which varies by engine version and
# by whether the type is a stock DataTable or a custom DTG one.
# ---------------------------------------------------------------------------

import struct
from collections import Counter, defaultdict

UASSET_MAGIC = 0x9E2A83C1


def _read_fname_strings(data, max_strings=6000):
    """Walks the file looking for Unreal's length-prefixed strings:
    int32 length, then that many bytes (ASCII, NUL-terminated) or, if the
    length is negative, that many UTF-16 code units. Returns them in file
    order. Deliberately tolerant - it scans rather than trusting header
    offsets, so it works regardless of engine version."""
    out = []
    i = 0
    n = len(data)
    while i + 4 <= n and len(out) < max_strings:
        (ln,) = struct.unpack_from("<i", data, i)
        if 2 <= ln <= 512 and i + 4 + ln <= n:
            raw = data[i + 4: i + 4 + ln]
            if raw.endswith(b"\x00"):
                try:
                    s = raw[:-1].decode("ascii")
                except UnicodeDecodeError:
                    i += 1
                    continue
                if s and all(32 <= ord(c) < 127 for c in s):
                    out.append(s)
                    i += 4 + ln
                    continue
        elif -256 <= ln <= -2 and i + 4 + (-ln * 2) <= n:
            raw = data[i + 4: i + 4 + (-ln * 2)]
            try:
                s = raw.decode("utf-16-le").rstrip("\x00")
            except UnicodeDecodeError:
                i += 1
                continue
            if s and all(32 <= ord(c) < 127 for c in s):
                out.append(s)
                i += 4 + (-ln * 2)
                continue
        i += 1
    return out


# Strings that look like they carry timetable meaning, so the report can
# lead with them rather than with 3000 engine-internal names.
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_HEADCODE_RE = re.compile(r"^[0-9][A-Z][0-9]{2}$")   # e.g. 1A10, 2K05


def inspect_asset(path):
    """Reads a .uasset (and its .uexp sibling if present) and reports what
    readable content is in it. Answers 'is this parseable' with evidence."""
    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}

    with open(path, "rb") as f:
        data = f.read()

    result = {"path": path, "size": len(data)}
    if len(data) >= 4:
        (magic,) = struct.unpack_from("<I", data, 0)
        result["magic_ok"] = (magic == UASSET_MAGIC)
        result["magic"] = hex(magic)

    # The .uexp sibling holds the actual exported data; the .uasset is
    # mostly header/name table. Both are worth reading.
    uexp = os.path.splitext(path)[0] + ".uexp"
    if os.path.isfile(uexp):
        with open(uexp, "rb") as f:
            uexp_data = f.read()
        result["uexp_size"] = len(uexp_data)
        data_all = data + uexp_data
    else:
        result["uexp_size"] = None
        data_all = data

    strings = _read_fname_strings(data_all)
    result["string_count"] = len(strings)

    times = [s for s in strings if _TIME_RE.match(s)]
    headcodes = [s for s in strings if _HEADCODE_RE.match(s)]
    props = sorted({s for s in strings
                    if any(k in s.lower() for k in
                           ("arriv", "depart", "station", "stop", "platform",
                            "service", "time", "dwell", "destination",
                            "origin", "headcode", "formation"))})

    result["times_found"] = times[:60]
    result["time_count"] = len(times)
    result["headcodes_found"] = headcodes[:60]
    result["headcode_count"] = len(headcodes)
    result["interesting_properties"] = props[:120]
    result["sample_strings"] = strings[:250]

    # A blunt, specific verdict. An earlier version said "times and/or
    # service codes" which overstated a result that had 208 codes and zero
    # times - the two cases need distinguishing because they imply very
    # different amounts of remaining work.
    if result["uexp_size"] is None:
        verdict = ("NOTE: no .uexp alongside this .uasset, so only the header "
                   "and name table were read - names, enums and identifiers "
                   "but none of the values. Extract the .uexp to see actual "
                   "times and stop data. ")
    else:
        verdict = ""

    if times and headcodes:
        verdict += ("Times AND service codes present as plain strings - a "
                    "parser is realistic.")
    elif headcodes and not times:
        verdict += (f"{len(headcodes)} service codes found but no literal "
                    "time strings. Times are almost certainly stored as "
                    "binary (DateTime/float) rather than text, which is "
                    "normal for Unreal - reading them means decoding the "
                    "property data, not just scanning strings.")
    elif times:
        verdict += "Times present as plain strings."
    elif props:
        verdict += ("Timetable-shaped names found but no values - the data "
                    "is packed binary.")
    else:
        verdict += ("No readable timetable content. Either the wrong asset, "
                    "or fully binary.")
    result["verdict"] = verdict

    # Structural fingerprints worth reporting: the UObject type and the
    # enums tell us what the schema actually is.
    result["object_types"] = sorted({s for s in strings
                                     if s.startswith("Default__")
                                     or s.startswith("/Script/")})
    result["enums"] = sorted({s.split("::")[0] for s in strings if "::" in s})
    result["stations"] = sorted({s for s in strings
                                 if " Platform " in s})[:200]
    return result


# FTimespan is an int64 count of 100-nanosecond ticks. A time-of-day
# therefore falls between 0 and 24h = 864,000,000,000 ticks. Scanning for
# int64s in that range, aligned to whole seconds, is a cheap way to find
# the schedule values without first decoding the property layout.
TICKS_PER_SECOND = 10_000_000
TICKS_PER_DAY = 86_400 * TICKS_PER_SECOND


def scan_timespans(path, max_hits=400, min_run=8):
    """Looks for FTimespan-shaped int64 values in a .uexp.

    Reports both raw hits and 'runs' - places where several plausible
    times sit close together, which is what a table of stop times looks
    like. A run is far stronger evidence than scattered hits, which can
    just be coincidental byte patterns."""
    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}
    with open(path, "rb") as f:
        data = f.read()

    hits = []
    n = len(data) - 8
    i = 0
    while i <= n:
        (val,) = struct.unpack_from("<q", data, i)
        # Tenths of a second, within a day, non-zero. NOT whole seconds:
        # requiring those discarded ~90% of real values (see the findings
        # doc's list of failed approaches). Zero is excluded because it is
        # far too common in padding to mean anything.
        if 0 < val < TICKS_PER_DAY and val % (TICKS_PER_SECOND // 10) == 0:
            secs = val // TICKS_PER_SECOND
            hits.append((i, f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"))
            i += 8
            continue
        # ONE byte, not four. This is a variable-length stream, so values
        # are not guaranteed to sit on 4-byte boundaries - a 4-byte stride
        # missed three of every four in testing.
        i += 1

    # Cluster detection, fixed. The first version required hits within 128
    # bytes of each other, but each stop-point record is a large struct -
    # on the real Leven Branch layer the 133 hits averaged 65,000 bytes
    # apart, so every genuine time was reported as "isolated". The gap is
    # now scaled to the file's own hit density.
    gap = max(4096, (len(data) // max(1, len(hits))) * 3)
    runs = []
    current = []
    for off, t in hits:
        if current and off - current[-1][0] <= gap:
            current.append((off, t))
        else:
            if len(current) >= min_run:
                runs.append(current)
            current = [(off, t)]
    if len(current) >= min_run:
        runs.append(current)

    # Stronger evidence than clustering: real schedules run forwards in
    # time, so mostly-ascending values are very unlikely to be coincidence
    # (random data sits at ~50%).
    secs = []
    for _o, t in hits:
        h, m, s = (int(x) for x in t.split(":"))
        secs.append(h * 3600 + m * 60 + s)
    ascending = sum(1 for a, b in zip(secs, secs[1:]) if b > a)
    ratio = (ascending / (len(secs) - 1)) if len(secs) > 1 else 0.0

    # Both of these were referenced in the return value but never computed,
    # so every call to this function raised NameError - the endpoint has
    # been dead. distinct guards the ascending ratio: a run of identical
    # values scores ~100% ascending under >= and looks like a perfect
    # result, which is why the test above uses strict > and is reported
    # alongside a distinct-value count.
    distinct = len(set(secs))
    offsets = [o for o, _t in hits]
    phase = Counter(o % 8 for o in offsets).most_common(1)[0][0] if offsets else None

    if ratio >= 0.7 and len(hits) >= 20:
        verdict = (f"{len(hits)} times found and {ratio:.0%} of them run "
                   "forwards - random data sits near 50%, so these are real "
                   "schedule values. A parser is realistic.")
    elif runs:
        verdict = (f"{len(runs)} clusters of times found - that is what a "
                   "stop-time table looks like.")
    elif hits:
        verdict = (f"{len(hits)} plausible values, {ratio:.0%} ascending - "
                   "not clearly distinguishable from coincidence.")
    else:
        verdict = "No plausible time values found."

    return {
        "path": path,
        "size": len(data),
        "hit_count": len(hits),
        "ascending_ratio": round(ratio, 3),
        "phase": phase,
        "distinct_times": distinct,
        "earliest": min(hits, key=lambda h: h[0])[1] if hits else None,
        "sample_times": [t for _o, t in hits[:80]],
        "run_count": len(runs),
        "largest_runs": [
            {"start_offset": r[0][0], "count": len(r),
             "times": [t for _o, t in r[:40]]}
            for r in sorted(runs, key=len, reverse=True)[:5]
        ],
        "verdict": verdict,
    }


def analyse_records(path, window=64, max_records=40):
    """Reverse-engineering aid: dumps the bytes AROUND each recovered time
    so the record layout can be worked out from real evidence.

    Reports, for each time found:
      - a hex/ASCII window either side
      - any other FTimespan within the window (a StopPoint should carry
        BOTH an arrival and a departure, so a nearby second time is a
        strong structural signal)
      - plausible int32 values, which in Unreal are usually FName table
        indices - i.e. the station name for that stop

    Plus, across all hits, the gaps between consecutive records: a
    repeating stride means fixed-size records, which makes parsing
    dramatically simpler than a variable-length stream.
    """
    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}
    with open(path, "rb") as f:
        data = f.read()

    # Reuse the same detection as scan_timespans.
    hits = []
    i, n = 0, len(data) - 8
    while i <= n:
        (val,) = struct.unpack_from("<q", data, i)
        if 0 < val < TICKS_PER_DAY and val % TICKS_PER_SECOND == 0:
            hits.append((i, val // TICKS_PER_SECOND))
            i += 8
            continue
        i += 4
    if not hits:
        return {"path": path, "error": "no_times_found"}

    def hhmmss(s):
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    records = []
    for off, secs in hits[:max_records]:
        lo = max(0, off - window)
        hi = min(len(data), off + 8 + window)
        chunk = data[lo:hi]

        # Other timespans inside the window - arrival/departure pairing.
        # NOTE: a single time is NORMAL, not a fault. The first stop of a
        # service has only a departure (the train is already there), the
        # last has only an arrival, and freight often has no scheduled
        # arrival at all. So absence of a pair is expected data, and the
        # parser must treat these fields as optional.
        neighbours = []
        sentinels = []
        for j in range(0, len(chunk) - 8, 4):
            (v,) = struct.unpack_from("<q", chunk, j)
            abs_off = lo + j
            if abs_off == off:
                continue
            if 0 < v < TICKS_PER_DAY and v % TICKS_PER_SECOND == 0:
                neighbours.append({
                    "offset_delta": abs_off - off,
                    "time": hhmmss(v // TICKS_PER_SECOND),
                })
            elif v == 0 or v == -1 or v == (1 << 63) - 1:
                # Candidate "no time set" markers. These matter: an absent
                # arrival/departure is exactly what marks a first/last stop
                # or a freight working, so they carry real meaning rather
                # than being padding to ignore.
                sentinels.append({"offset_delta": abs_off - off, "value": v})

        # Small int32s - candidate FName indices / enum values / platform nos.
        ints = []
        for j in range(0, len(chunk) - 4, 4):
            (v,) = struct.unpack_from("<i", chunk, j)
            if 0 < v < 200000:
                ints.append({"offset_delta": lo + j - off, "value": v})

        records.append({
            "offset": off,
            "time": hhmmss(secs),
            "hex_before": data[lo:off].hex(),
            "hex_after": data[off + 8:hi].hex(),
            "ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in chunk),
            "nearby_times": neighbours[:6],
            "nearby_absent_markers": sentinels[:8],
            "nearby_ints": ints[:14],
        })

    # Stride analysis across ALL hits, not just the sampled ones.
    gaps = [hits[k + 1][0] - hits[k][0] for k in range(len(hits) - 1)]
    freq = {}
    for g in gaps:
        freq[g] = freq.get(g, 0) + 1
    common = sorted(freq.items(), key=lambda kv: -kv[1])[:10]
    paired = sum(1 for r in records if r["nearby_times"])
    singles = len(records) - paired
    with_sentinels = sum(1 for r in records if r.get("nearby_absent_markers"))

    return {
        "path": path,
        "size": len(data),
        "time_count": len(hits),
        "records": records,
        "common_gaps": [{"gap_bytes": g, "count": c} for g, c in common],
        "records_with_paired_time": paired,
        "records_with_single_time": singles,
        "records_with_absent_markers": with_sentinels,
        "verdict": (
            f"{paired} paired, {singles} single. Both are expected: a stop "
            "can legitimately have only one time - the first stop of a "
            "service has just a departure, the last has just an arrival, "
            "and freight often has no scheduled arrival. "
            + (f"{with_sentinels} records have a zero/-1 value next to the "
               "time, which is likely how an absent time is stored - worth "
               "confirming, since that marker is what identifies first and "
               "last stops."
               if with_sentinels else
               "No zero/-1 markers alongside the times, so an absent time is "
               "probably encoded by the field simply not being written.")
        ),
    }


def diff_records(path, stride=None, count=6, max_report=120):
    """Compares consecutive fixed-size records byte-for-byte.

    Once a stride is known, this is the fastest route to a field map:
    bytes that are IDENTICAL across every record are structure, padding or
    type tags; bytes that VARY are the actual data. Reporting only the
    varying offsets turns 2828 opaque bytes into a short list of candidate
    fields.

    stride: bytes per record. If omitted, inferred from the most common
    gap between detected times (they were all exact multiples of 2828 on
    the real Leven Branch file).
    """
    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}
    with open(path, "rb") as f:
        data = f.read()

    # Find times, as the anchor for record boundaries.
    hits = []
    i, n = 0, len(data) - 8
    while i <= n:
        (val,) = struct.unpack_from("<q", data, i)
        if 0 < val < TICKS_PER_DAY and val % TICKS_PER_SECOND == 0:
            hits.append(i)
            i += 8
            continue
        i += 4
    if len(hits) < 3:
        return {"error": "not_enough_times", "found": len(hits)}

    if not stride:
        gaps = [hits[k + 1] - hits[k] for k in range(len(hits) - 1)]
        # The true stride is the greatest common divisor of the gaps -
        # every gap was an exact multiple of it on real data.
        from math import gcd
        g = 0
        for gap in gaps:
            g = gcd(g, gap)
        stride = g if g >= 16 else min(gaps)

    # Take records anchored at time offsets that are exactly `stride` apart.
    anchors = [hits[0]]
    for h in hits[1:]:
        if h - anchors[-1] == stride:
            anchors.append(h)
        if len(anchors) >= count:
            break
    if len(anchors) < 2:
        anchors = hits[:count]

    start = anchors[0]
    recs = []
    for a in anchors:
        lo = a - (start % stride if False else 0)
        recs.append(data[a: a + stride])
    recs = [r for r in recs if len(r) == stride]
    if len(recs) < 2:
        return {"error": "could_not_extract_records", "stride": stride}

    varying = []
    for off in range(0, stride - 4, 4):
        vals = []
        for r in recs:
            (v,) = struct.unpack_from("<i", r, off)
            vals.append(v)
        if len(set(vals)) > 1:
            varying.append({"offset": off, "values": vals[:8]})

    # Interpret the most interesting varying offsets.
    annotated = []
    for v in varying[:max_report]:
        off = v["offset"]
        note = ""
        if off % 8 == 0 and off + 8 <= stride:
            longs = []
            for r in recs:
                (lv,) = struct.unpack_from("<q", r, off)
                longs.append(lv)
            if all(0 < x < TICKS_PER_DAY and x % TICKS_PER_SECOND == 0 for x in longs):
                note = "FTimespan (time value)"
                v["as_times"] = [
                    f"{(x // TICKS_PER_SECOND) // 3600:02d}:"
                    f"{((x // TICKS_PER_SECOND) % 3600) // 60:02d}:"
                    f"{(x // TICKS_PER_SECOND) % 60:02d}" for x in longs[:8]
                ]
        floats = []
        for r in recs:
            (fv,) = struct.unpack_from("<f", r, off)
            floats.append(fv)
        if not note and all(abs(x) > 1e-6 and abs(x) < 1e9 for x in floats):
            note = "plausible float (position/distance?)"
            v["as_floats"] = [round(x, 3) for x in floats[:8]]
        v["note"] = note
        annotated.append(v)

    constant_bytes = stride - len(varying) * 4
    return {
        "path": path,
        "stride": stride,
        "records_compared": len(recs),
        "anchor_offsets": anchors[:count],
        "varying_field_count": len(varying),
        "constant_bytes_approx": constant_bytes,
        "varying_fields": annotated,
        "verdict": (
            f"Record size {stride} bytes. {len(varying)} of ~{stride // 4} "
            f"int-sized slots differ between records - the rest is identical "
            "structure. Those varying slots are the fields worth decoding."
        ),
    }


def decode_stride(path, stride=2828, offset=0, limit=4000, start=None):
    """Reads the FTimespan at a fixed offset in EVERY record.

    Motivation: on the real Leven Branch file the stride-based record count
    (8.6 MB / 2828 = 3055 records) was ~23x the number of times found by
    scanning (133). The scanner required exact whole seconds, so anything
    with sub-second precision was silently discarded. This reads the field
    directly at its known offset with NO whole-second filter, which is the
    correct way round now that the layout is known.
    """
    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}
    with open(path, "rb") as f:
        data = f.read()

    # Record boundaries are NOT at multiples of stride from byte 0. On the
    # real file the detected times sat at phase 372 and 2644 within the
    # stride, so reading at k*stride+0 sampled a different part of each
    # record and returned near-zero values that merely LOOKED like times.
    # Anchor on a genuinely detected time instead and step from there.
    if start is None:
        start = None
        i, n = 0, len(data) - 8
        while i <= n:
            (v,) = struct.unpack_from("<q", data, i)
            if 0 < v < TICKS_PER_DAY and v % TICKS_PER_SECOND == 0:
                start = i
                break
            i += 4
        if start is None:
            return {"error": "no_anchor_time_found"}

    phase = start % stride
    total = (len(data) - phase) // stride
    rows, zero, out_of_range, fractional = [], 0, 0, 0

    for k in range(min(total, limit)):
        pos = phase + k * stride + offset
        if pos + 8 > len(data):
            break
        (val,) = struct.unpack_from("<q", data, pos)
        if val == 0:
            zero += 1
            continue
        if not (0 < val < TICKS_PER_DAY):
            out_of_range += 1
            continue
        secs_total = val / TICKS_PER_SECOND
        if val % TICKS_PER_SECOND:
            fractional += 1
        s = int(secs_total)
        rows.append({
            "record": k,
            "offset": pos,
            "time": f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}",
            "fractional": round(secs_total - s, 3),
        })

    times = [r["time"] for r in rows]
    # STRICTLY increasing. An earlier version used >=, so a run of identical
    # values scored ~100% and a meaningless result looked like a strong one.
    ordered = sum(1 for a, b in zip(times, times[1:]) if b > a)
    ratio = ordered / (len(times) - 1) if len(times) > 1 else 0
    distinct = len(set(times))

    return {
        "path": path,
        "stride": stride,
        "field_offset": offset,
        "records_total": total,
        "records_read": min(total, limit),
        "valid_times": len(rows),
        "zero_value": zero,
        "out_of_range": out_of_range,
        "with_fractional_seconds": fractional,
        "ascending_ratio": round(ratio, 3),
        "phase": phase,
        "distinct_times": distinct,
        "first_times": times[:40],
        "sample_rows": rows[:40],
        "verdict": (
            f"{len(rows)} of {min(total, limit)} records carry a time at "
            f"+{offset} ({fractional} with sub-second precision). "
            + (f"{ratio:.0%} run forwards. "
               if len(times) > 1 else "")
            + (f"{distinct} distinct values. "
               + ("Looks like real running times."
                  if distinct > 20 and ratio > 0.5 else
                  "Too few distinct values to be a timetable - this offset "
                  "is probably not the time field, or the phase is wrong."))
        ),
    }


def extract_time_series(path, min_run=4, max_runs=200):
    """Extracts times in FILE ORDER and splits them into ascending runs.

    This replaces the fixed-stride approach, which was wrong: 2828 was
    merely the GCD of a sample of gaps, and stepping by it stayed aligned
    for about 20 records before drifting into garbage (1321 values but
    only 48 distinct, 3% ascending). The type is named
    RouteTimetableDataTrackStream - a STREAM, i.e. variable-length
    records - so any fixed stride was always going to fail.

    Scanning in file order needs no record size. A service runs forwards
    in time, so each ascending run is a candidate service; a drop back to
    an earlier time marks the boundary to the next one. Sub-second
    precision is allowed (it was discarding ~90% of values before).
    """
    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}
    with open(path, "rb") as f:
        data = f.read()

    times = []
    i, n = 0, len(data) - 8
    while i <= n:
        (val,) = struct.unpack_from("<q", data, i)
        # Plausible time of day, and at least whole tenths of a second -
        # arbitrary raw bytes rarely land on a clean sub-multiple.
        if 0 < val < TICKS_PER_DAY and val % (TICKS_PER_SECOND // 10) == 0:
            times.append((i, val / TICKS_PER_SECOND))
            i += 8
            continue
        # Step ONE byte, not four. In a variable-length stream the values
        # are not guaranteed to sit on 4-byte boundaries, and a 4-byte
        # stride missed three quarters of them in testing.
        i += 1

    runs, cur = [], []
    for off, secs in times:
        if cur and secs > cur[-1][1]:
            cur.append((off, secs))
        else:
            if len(cur) >= min_run:
                runs.append(cur)
            cur = [(off, secs)]
    if len(cur) >= min_run:
        runs.append(cur)

    def fmt(s):
        s = int(s)
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    runs.sort(key=len, reverse=True)
    out = []
    for r in runs[:max_runs]:
        out.append({
            "start_offset": r[0][0],
            "stop_count": len(r),
            "first": fmt(r[0][1]),
            "last": fmt(r[-1][1]),
            "duration_min": round((r[-1][1] - r[0][1]) / 60, 1),
            "times": [fmt(s) for _o, s in r[:40]],
        })

    return {
        "path": path,
        "total_times": len(times),
        "run_count": len(runs),
        "runs": out,
        "verdict": (
            f"{len(runs)} ascending runs found from {len(times)} times. "
            f"Longest has {out[0]['stop_count']} entries spanning "
            f"{out[0]['duration_min']} minutes ({out[0]['first']} to "
            f"{out[0]['last']}). Each run is a candidate service."
            if out else
            f"{len(times)} times but no ascending runs of {min_run}+ - "
            "the values may not be times, or services are interleaved."
        ),
    }


# ---------------------------------------------------------------------------
# Finding the StopPoint marker
# ---------------------------------------------------------------------------
# extract_time_series() gives us services, but each one is 120-151 TRACK
# POINTS, not station stops - the Leven branch has only a handful of
# stations. The subset we want carries
# ETimetableTrackDataType::StopPoint. This finds it.
#
# It deliberately does NOT begin by hunting for the enum. Earlier record
# diffs reported the type field as CONSTANT precisely because every sampled
# record happened to be the same type, so "find the byte that varies" begs
# the question. Instead it starts from something identifiable on its own
# terms - the STATION NAME - and derives the classification from that:
#
#   1. Read the name table; mark which entries look like station names.
#      (Already proven readable: this is where the 88 "Aberdour Platform 1"
#      names came from.)
#   2. Unreal writes an FName as an int32 index into that table. Find which
#      int32s near each time are valid indices resolving to a station.
#   3. Recover the constant SHIFT between our recovered table position and
#      the real FName index - the string scan starts partway through the
#      header, so the two are offset by an unknown constant.
#   4. A record with a station name is a StopPoint; one without is a track
#      point. Derived from the data, not assumed.
#   5. Only then look for the enum, anchored on the station field.
#
# THREE THINGS THIS GETS RIGHT THAT AN OBVIOUS IMPLEMENTATION GETS WRONG.
# All three were caught by running it against synthetic records of known
# layout, and each produced a confident, entirely wrong answer:
#
#   a) An int32 read at +4 from the time is the time's OWN high half. For
#      times in a working day that lands in 50..72 - a perfectly plausible
#      name index. It scored better than the real field. Any offset
#      overlapping the time's 8 bytes is excluded.
#   b) "The FName field should be in range on most records" is false here.
#      Only StopPoints carry a station, so the true field is in range on a
#      small MINORITY of records. Filtering on a high in-range rate throws
#      away the very field being looked for.
#   c) The field is NOT at a fixed offset from the time. This is a Stream:
#      if any variable-length data sits between the station and the time,
#      the gap moves per record. It searches for the NEAREST resolving
#      index instead, and reports how tight that distance is - which is
#      itself the evidence for whether the layout is fixed or variable.
#
# Zero is excluded from the shift vote for the same class of reason: it is
# the single most common int32 in any Unreal asset, and 88 stations x a sea
# of zeros outvotes the real signal.

# Engine-internal names that would otherwise pass the station shape test
# ("Texture2D 3", "Material 2"...).
_ENGINE_NAME_BITS = (
    "material", "texture", "mesh", "blueprint", "widget", "anim", "sound",
    "particle", "physic", "skeleton", "montage", "curve", "/game/",
    "/script/", "default__", "font", "shader", "niagara", "component",
    "property", "package", "class", "struct", "function",
)

# "Aberdour Platform 1", "Edinburgh Waverley 1a", "Kirkcaldy 2"
_STATION_SHAPE_RE = re.compile(r"^[A-Z][A-Za-z'\-\.& ]{2,40}\s\d{1,2}[a-z]?$")

# ETimetableTrackDataType members, per the schema in the findings doc.
_TRACK_DATA_TYPES = (
    "StopPoint", "ActionPoint", "GoVia", "ReversePoint",
    "TrackSectionEntry", "TrackSectionExit", "MultiOccupancy",
)


def _looks_like_station(s):
    low = s.lower()
    if any(bit in low for bit in _ENGINE_NAME_BITS):
        return False
    if "platform" in low:
        return True
    return bool(_STATION_SHAPE_RE.match(s))


def _scan_times(data, max_times=40000):
    """Times in FILE ORDER - same detection as extract_time_series: tenths
    of a second, one-byte stride. Returns [(offset, seconds_float)]."""
    times = []
    i, n = 0, len(data) - 8
    tenth = TICKS_PER_SECOND // 10
    while i <= n and len(times) < max_times:
        (val,) = struct.unpack_from("<q", data, i)
        if 0 < val < TICKS_PER_DAY and val % tenth == 0:
            times.append((i, val / TICKS_PER_SECOND))
            i += 8
            continue
        i += 1
    return times


def _sibling_uasset(path):
    """Name table lives in the .uasset, records in the .uexp."""
    if path.lower().endswith(".uexp"):
        cand = path[:-5] + ".uasset"
        if os.path.isfile(cand):
            return cand
    return None


def _fmt_hms(s):
    s = int(s)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _int32s_near(data, centre, radius, exclude_lo, exclude_hi, fname8=False):
    """Every int32 readable within `radius` bytes of `centre`, at EVERY byte
    offset - this is a variable-length stream, so values are not guaranteed
    to be 4-byte aligned.

    exclude_lo/hi masks out the time's own 8 bytes. Without that mask the
    read at +4 returns the time's high half, which for any working-day time
    lands in 50..72 - a plausible-looking name index that outscores the real
    field.

    fname8=True additionally requires the next int32 to be zero. Unreal
    writes an FName as int32 index + int32 Number, and Number is 0 for any
    name without a numeric suffix, so this filter keeps real FNames and
    drops most misaligned reads. It is offered as an alternative rather
    than imposed, because this is a custom Serialize() and may well write a
    bare index - which of the two reads more cleanly is decided by
    measurement in _recover_shift(), not assumed here.
    """
    out = {}
    lo = max(0, centre - radius)
    hi = min(len(data) - 8, centre + radius)
    for off in range(lo, hi + 1):
        if off + 4 > exclude_lo and off < exclude_hi:
            continue
        if fname8:
            idx, num = struct.unpack_from("<ii", data, off)
            if num != 0:
                continue
            v = idx
        else:
            (v,) = struct.unpack_from("<i", data, off)
        # Zero is excluded outright. It is the most common int32 in any
        # Unreal asset, and name index 0 is "None" - never a real station
        # or type reference. Left in, whichever table position maps to 0
        # appears on 100% of records at a unique offset every time - which
        # is exactly the signature a genuine field is being looked for by.
        # It beat the true shift twice.
        if v == 0:
            continue
        # Nearest-to-centre wins, so a later, further offset never displaces
        # a closer one for the same value.
        if v not in out or abs(off - centre) < abs(out[v] - centre):
            out[v] = off
    return out


def _drop_ubiquitous(value_maps, max_share=0.6):
    """Removes values that appear near almost every time.

    Generalises the zero rule. A padding constant present on 90% of records
    mimics a real field perfectly - a unique offset each time and near-total
    coverage. No genuine anchor comes close to the threshold: the type enum
    is spread across its members (~25% each) and a station appears only on
    the stop subset.
    """
    n = max(1, len(value_maps))
    freq = Counter()
    for vm in value_maps:
        for v in vm:
            freq[v] += 1
    banned = {v for v, c in freq.items() if c / n > max_share}
    if not banned:
        return value_maps, banned
    return [{v: o for v, o in vm.items() if v not in banned} for vm in value_maps], banned


def _name_table(uasset_path):
    """Names in file order, which positions look like stations, and where
    each ETimetableTrackDataType member sits."""
    with open(uasset_path, "rb") as f:
        names = _read_fname_strings(f.read())
    stations = {i: s for i, s in enumerate(names) if _looks_like_station(s)}
    types = {}
    for i, s in enumerate(names):
        bare = s.split("::")[-1]
        if bare in _TRACK_DATA_TYPES:
            types.setdefault(bare, i)
    return names, stations, types


def _anchor_stats(value_maps, centres, positions, shift, bucket=32):
    """How much a set of name-table positions behaves like a REAL FIELD at
    this shift. Returns (mean_rate, unique_coverage, delta_concentration).

    Three measurements, each added because the previous two were fooled:

      mean_rate - fraction of records referencing each position, counted by
        DISTINCT BYTE OFFSET. Raw vote counts let a few thousand repeated
        padding values outvote a field that appears once per record.

      unique_coverage - fraction of records whose hit sits at an offset used
        by almost no other record. One stray byte position within radius of
        many times otherwise reports a hit on every record from a single
        shared offset, which looks exactly like a real field.

      delta_concentration - fraction of hits falling in the single best
        {bucket}-byte band of distance from the time. This is the strongest
        of the three and the one that finally separated real from
        coincidental: a genuine field sits a roughly consistent distance
        from its own time even in a variable-length stream, while
        coincidences scatter uniformly across the window. Uniform noise
        scores about bucket/(2*radius); a real field scores far above it.
    """
    if not positions or not value_maps:
        return 0.0, 0.0, 0.0
    n = len(value_maps)
    offset_users = Counter()
    per_record = []
    deltas = []
    for vm, centre in zip(value_maps, centres):
        hits = [vm[p + shift] for p in positions if (p + shift) in vm]
        per_record.append(hits)
        for o in set(hits):
            offset_users[o] += 1
            deltas.append(o - centre)
    total = 0.0
    for p in positions:
        want = p + shift
        offsets = {vm[want] for vm in value_maps if want in vm}
        total += min(len(offsets), n) / n
    unique = sum(1 for hits in per_record
                 if any(offset_users[o] <= 2 for o in hits)) / n
    if deltas:
        buckets = Counter(d // bucket for d in deltas)
        concentration = buckets.most_common(1)[0][1] / len(deltas)
    else:
        concentration = 0.0
    return total / len(positions), unique, concentration


def _recover_shift(value_maps, centres, names, stations, type_positions,
                   radius, rng_seed=7):
    """Finds the constant offset between our recovered table position and the
    FName index written into the stream.

    _read_fname_strings scans from the start of the file and picks up header
    strings the real name table does not contain, so position != index by
    some unknown constant that has to be recovered before anything resolves.

    Candidates are generated from BOTH anchor families and each family is
    scored separately, because which one carries the signal is not known in
    advance: if the type is written as a raw byte rather than an FName - the
    likelier case per the findings doc - the enum anchors resolve nowhere and
    only the stations can recover the shift. Anchoring on the enums alone
    made this function chase a bogus shift and invent 18 StopPoints in a file
    containing no StopPoint FName at all.

    Every candidate is scored against a NULL MODEL. The control positions are
    drawn from the anchors' immediate NEIGHBOURHOOD in the table, not at
    random from the whole of it: a random control maps to values of a
    different magnitude than the anchors do, and small values are common in
    any binary, so any shift mapping the anchors onto small numbers scored an
    enormous lift on padding alone.
    """
    import random
    rnd = random.Random(rng_seed)
    enum_pos = sorted(type_positions.values())
    station_pos = sorted(stations.keys())
    banned = set(enum_pos) | set(station_pos)
    control_pool = sorted({i for a in (enum_pos + station_pos)
                           for i in range(max(0, a - 64), min(len(names), a + 65))
                           if i not in banned})
    if len(control_pool) < 8:
        control_pool = [i for i in range(len(names)) if i not in banned]
    control = rnd.sample(control_pool, min(32, len(control_pool))) if control_pool else []
    eps = 1.0 / max(1, len(value_maps))
    noise_floor = 32.0 / max(1, 2 * radius)

    # A shift is only POSSIBLE if it resolves every anchor to an index that
    # exists in the name table. This is a hard constraint of the format, not
    # a heuristic, and it was missing: on the real Leven Branch layer - an 88
    # entry table - the winning shift was 8743, which puts StopPoint at index
    # 8765. There is no index 8765. Three impossible shifts tied for first on
    # every statistical measure and the only plausible candidate came fifth,
    # losing by 0.0002.
    lo_anchor = min(enum_pos + station_pos)
    hi_anchor = max(enum_pos + station_pos)

    def possible(sh):
        return 0 <= lo_anchor + sh and hi_anchor + sh < len(names)

    candidates = defaultdict(set)
    for vm in value_maps:
        for v, o in vm.items():
            for p in enum_pos + station_pos:
                sh = v - p
                if possible(sh):
                    candidates[sh].add(o)
    if not candidates:
        return None
    shortlist = sorted(candidates, key=lambda k: len(candidates[k]), reverse=True)[:120]

    results = []
    for shift in shortlist:
        c_rate, c_uniq, c_conc = _anchor_stats(value_maps, centres, control, shift)
        e_rate, e_uniq, e_conc = _anchor_stats(value_maps, centres, enum_pos, shift)
        s_rate, s_uniq, s_conc = _anchor_stats(value_maps, centres, station_pos, shift)
        # A family only counts as found if its hits are BOTH concentrated at
        # a consistent distance and spread across records at their own
        # offsets. Either alone is reachable by padding.
        # Both families scored identically. An earlier version multiplied the
        # station score by 4 to "compensate" for stations appearing on only a
        # minority of records - an unprincipled fudge that let a bogus shift
        # win by finding 142 stations among 182 track points. A station
        # genuinely IS on a minority of records; that lower coverage is the
        # truth, not something to correct for.
        enum_score = (e_conc - max(c_conc, noise_floor)) * e_uniq
        stn_score = (s_conc - max(c_conc, noise_floor)) * s_uniq
        results.append({
            "shift": shift,
            "enum_rate": round(e_rate, 4), "enum_unique_coverage": round(e_uniq, 4),
            "enum_concentration": round(e_conc, 4), "enum_score": round(enum_score, 4),
            "station_rate": round(s_rate, 4), "station_unique_coverage": round(s_uniq, 4),
            "station_concentration": round(s_conc, 4), "station_score": round(stn_score, 4),
            "control_concentration": round(c_conc, 4),
            "control_rate": round(c_rate, 4),
            "enum_lift": round((e_rate + eps) / (c_rate + eps), 2),
            "noise_floor": round(noise_floor, 4),
            "score": round(max(enum_score, stn_score), 4),
        })
    results.sort(key=lambda r: (r["score"], r["enum_unique_coverage"]), reverse=True)
    return results


def find_stop_points(path, radius=192, max_times=8000, min_run=4):
    """Separates STATION STOPS from the running track points around them.

    extract_time_series() returns runs of 120-151 entries per service, but
    the Leven branch has only a handful of stations - the rest are
    EInstructionScheduledTimeTypes::Simulated running times. The subset we
    want is flagged ETimetableTrackDataType::StopPoint.

    Two INDEPENDENT classifications are computed and cross-checked, because
    either alone can be talked into a confident wrong answer:

      ENUM-ANCHORED    - if the type is written as an FName, "StopPoint" is
                         itself in the name table and types the record
                         directly.
      STATION-ANCHORED - a record referencing a station name is a stop.
                         Starts from something identifiable on its own terms
                         rather than hunting for "the byte that varies",
                         which begs the question: the earlier record diffs
                         called the type field constant precisely because
                         every record sampled happened to be the same type.

    Agreement between the two is the evidence. Disagreement is reported, not
    averaged away.

    Both FName encodings are tried (bare int32 index, and Unreal's index +
    Number pair) and whichever separates more cleanly from the null model is
    used - reported as `encoding`.
    """
    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}
    uasset = _sibling_uasset(path)
    if not uasset:
        return {"error": "no_sibling_uasset", "path": path,
                "detail": "Point this at the .uexp - the name table lives in "
                          "the matching .uasset next to it."}

    names, stations, type_positions = _name_table(uasset)
    if not stations and not type_positions:
        return {"error": "no_anchors", "path": path, "name_count": len(names),
                "detail": "Neither station names nor ETimetableTrackDataType "
                          "members are in this asset's name table, so there is "
                          "nothing to anchor to. Wrong asset, or the name "
                          "table did not read."}

    with open(path, "rb") as f:
        data = f.read()
    times = _scan_times(data, max_times=max_times)
    if not times:
        return {"error": "no_times", "path": path}

    # Try both FName encodings and keep whichever gives the better lift.
    attempts = {}
    for enc, fname8 in (("index+number", True), ("bare index", False)):
        near = [_int32s_near(data, off, radius, off, off + 8, fname8=fname8)
                for off, _s in times]
        near, banned = _drop_ubiquitous(near)
        ranked = _recover_shift(near, [o for o, _s in times], names, stations,
                                type_positions, radius)
        if ranked:
            attempts[enc] = (ranked, near, len(banned))

    if not attempts:
        return {"error": "no_shift_candidates", "path": path}

    # Prefer Unreal's real FName layout (int32 index + int32 Number) unless
    # dropping the Number==0 constraint scores MATERIALLY better. A bare
    # index accepts every match the strict form does plus a lot of noise, so
    # on a hairline margin it is the worse read - it beat the strict form by
    # 0.0003 on one fixture and took a spurious shift of 64624 with it.
    strict = attempts.get("index+number")
    loose = attempts.get("bare index")
    if strict and (not loose or loose[0][0]["score"] <= strict[0][0]["score"] * 1.25):
        encoding = "index+number"
    else:
        encoding = "bare index"
    ranked, near, banned_values = attempts[encoding]

    # A lift near 1 means the anchors resolve no better than random table
    # entries - i.e. nothing was found, and every "stop" below would be an
    # artefact. Say so rather than returning a confident-looking list.

    pos_to_type = {p: n for n, p in type_positions.items()}

    def classify(sh):
        out = []
        for (off, secs), vals in zip(times, near):
            station = station_delta = None
            enum_name = enum_delta = None
            for v, o in vals.items():
                p = v - sh
                d = o - off
                if p in stations and (station_delta is None or abs(d) < abs(station_delta)):
                    station, station_delta = stations[p], d
                if p in pos_to_type and (enum_delta is None or abs(d) < abs(enum_delta)):
                    enum_name, enum_delta = pos_to_type[p], d
            out.append({
                "offset": off, "seconds": secs, "time": _fmt_hms(secs),
                "station": station, "station_delta": station_delta,
                "enum": enum_name, "enum_delta": enum_delta,
            })
        return out

    def band_filter(rows, key, delta_key, bucket=32):
        """Drops hits sitting far from where that field actually lives.

        The concentration measure has already established that a real field
        sits a consistent distance from its time. Anything outside the
        dominant band is a coincidental value that happened to resolve to a
        valid index - which is exactly what the leftover false positives
        were: a "station" 180 bytes away on a record whose type says
        ReversePoint. Keeping the dominant band and one bucket either side
        allows for the variable-length wobble the stream really has, without
        accepting hits from halfway across the window.
        """
        deltas = [c[delta_key] for c in rows if c[key] is not None]
        if len(deltas) < 8:
            return rows, None
        dominant = Counter(d // bucket for d in deltas).most_common(1)[0][0]
        lo, hi = (dominant - 1) * bucket, (dominant + 2) * bucket
        for c in rows:
            if c[key] is not None and not (lo <= c[delta_key] < hi):
                c[key] = None
                c[delta_key] = None
        return rows, (lo, hi)

    def corroboration(rows):
        """Jaccard overlap between "has a station" and "typed StopPoint".

        This is the tie-break, and it is the whole design in one number: at
        the true shift those two sets are the SAME records, because a stop is
        a stop however you identify it. One position either side maps the
        enum onto its neighbouring member and the station onto the next
        station, which scores almost identically on every statistical
        measure - the top two candidates differed by 0.02 - but scatters the
        two sets across different records, so the overlap collapses.
        """
        a = {i for i, c in enumerate(rows) if c["station"]}
        b = {i for i, c in enumerate(rows) if c["enum"] == "StopPoint"}
        if not a or not b:
            return None
        return len(a & b) / len(a | b)

    # Disambiguate among the near-ties on score using corroboration between
    # the two independent families. Falls back to raw score when one family
    # is absent - e.g. when the type is a raw byte, where there is nothing to
    # corroborate against.
    tie_break = []
    for cand in ranked[:6]:
        rows = classify(cand["shift"])
        j = corroboration(rows)
        tie_break.append((cand["score"] + (j or 0), cand, rows, j))
    tie_break.sort(key=lambda x: x[0], reverse=True)
    # Report ties rather than silently taking whichever sorted first. Four
    # shifts tied to four decimal places on the real Leven layer, and the one
    # picked was impossible. If two shifts are indistinguishable, that is a
    # fact about the evidence and the caller needs to see it.
    top = tie_break[0][0]
    tied = [c["shift"] for total, c, _r, _j in tie_break if abs(total - top) < 0.005]
    _total, best, classified, corroborated = tie_break[0]
    shift = best["shift"]
    classified, station_band = band_filter(classified, "station", "station_delta")
    classified, enum_band = band_filter(classified, "enum", "enum_delta")
    corroborated = corroboration(classified)

    # Confirmed only if a family's hits are clearly more concentrated than
    # the control at the same shift. Deliberately keyed off concentration
    # rather than raw counts: every wrong answer this produced during
    # development was a CONFIDENT wrong answer with plenty of hits.
    trustworthy = best["score"] >= 0.15 and len(tied) == 1

    with_station = [c for c in classified if c["station"]]
    with_enum = [c for c in classified if c["enum"]]
    stop_by_enum = [c for c in classified if c["enum"] == "StopPoint"]
    both = [c for c in classified if c["station"] and c["enum"]]
    agree = (sum(1 for c in both if c["enum"] == "StopPoint") / len(both)) if both else None

    # Group back into services, carrying the flags through. Same ascending-run
    # segmentation as extract_time_series - no stride assumption.
    runs, cur = [], []
    for c in classified:
        if cur and c["seconds"] > cur[-1]["seconds"]:
            cur.append(c)
        else:
            if len(cur) >= min_run:
                runs.append(cur)
            cur = [c]
    if len(cur) >= min_run:
        runs.append(cur)

    services = []
    for r in sorted(runs, key=len, reverse=True)[:40]:
        stops = [c for c in r if c["station"] or c["enum"] == "StopPoint"]
        # A stop should carry BOTH an arrival and a departure - two times at
        # the same station. First stop (departure only), last stop (arrival
        # only) and freight (no arrivals at all) are CORRECT data, not parse
        # failures, so unpaired entries are counted, never dropped.
        paired = sum(1 for a, b in zip(stops, stops[1:])
                     if a["station"] and a["station"] == b["station"])
        services.append({
            "start_offset": r[0]["offset"],
            "track_points": len(r),
            "stop_count": len(stops),
            "distinct_stations": len({c["station"] for c in stops if c["station"]}),
            "first": r[0]["time"], "last": r[-1]["time"],
            "duration_min": round((r[-1]["seconds"] - r[0]["seconds"]) / 60, 1),
            "paired_times": paired,
            "unpaired": len(stops) - paired * 2,
            "stops": [{"time": c["time"], "station": c["station"], "type": c["enum"]}
                      for c in stops[:60]],
        })

    deltas = Counter(c["station_delta"] for c in with_station)
    spread = ({"distinct_deltas": len(deltas), "most_common": deltas.most_common(5),
               "fixed_layout": len(deltas) <= 3} if deltas else None)

    if len(tied) > 1:
        verdict = (f"AMBIGUOUS. {len(tied)} shifts {tied} are indistinguishable "
                   "on every measure, so which name each record refers to "
                   "cannot be decided from this asset alone. The stop TIMES "
                   "below are still real - the segmentation does not depend "
                   "on the shift - but the labels may be off by a position.")
    elif not trustworthy:
        verdict = (f"NOT CONFIRMED. At the best shift ({shift}) the anchors "
                   f"are no more consistently placed than the control "
                   f"(best {best['score']}, needs 0.15) - so the hits below "
                   "are scattered coincidences, not a field. Either the name "
                   "table did not read cleanly, or neither the type nor the "
                   "station is written as an FName in this asset.")
    elif with_station and stop_by_enum:
        verdict = (f"{len(stop_by_enum)} of {len(classified)} times are typed "
                   f"StopPoint and {len(with_station)} carry a station name; "
                   f"where both are present they agree {agree:.0%} of the time. "
                   + ("The two corroborate each other - the stop subset is "
                      "identified." if (agree or 0) >= 0.9 else
                      "They disagree too often to build on yet."))
    elif with_station:
        verdict = (f"{len(with_station)} of {len(classified)} times sit next to "
                   "a station name; those are the stops. No StopPoint FName "
                   "resolves, so the type is a raw byte - run /api/paks/diff "
                   "anchored on one of these station records to find it.")
    else:
        verdict = (f"{len(stop_by_enum)} times typed StopPoint but none carry a "
                   "station name - station text may live in the index asset "
                   "rather than this layer.")

    return {
        "path": path, "uasset": uasset,
        "name_count": len(names), "station_names": len(stations),
        "sample_stations": [stations[k] for k in list(stations)[:12]],
        "enum_members_in_table": type_positions,
        "encoding": encoding,
        "ubiquitous_values_dropped": banned_values,
        "shift": shift,
        "shift_scores": ranked[:5],
        "tied_shifts": tied,
        "names_sample": names[:400],
        "score": best["score"],
        "corroboration": round(corroborated, 3) if corroborated is not None else None,
        "confirmed": trustworthy,
        "total_times": len(classified),
        "with_station": len(with_station),
        "with_enum": len(with_enum),
        "typed_stop_point": len(stop_by_enum),
        "agreement": round(agree, 3) if agree is not None else None,
        "station_field_distance": spread,
        "station_delta_band": station_band,
        "enum_delta_band": enum_band,
        "service_count": len(runs),
        "services": services,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Tagged-property parsing - the RIGHT way to read these assets
# ---------------------------------------------------------------------------
# Section 8 of the findings doc guessed that RouteTimetableDataTrackStream,
# being named "Stream", wrote raw binary through a custom Serialize() and
# would have to be reverse-engineered byte by byte. THAT GUESS WAS WRONG, and
# the name table of the real Leven Branch layer proves it: it contains
# ArrayProperty, EnumProperty, FloatProperty, IntProperty, MapProperty and
# NameProperty alongside field names like DataType, Distance, Direction,
# InstructionIndex, GoViaIndex, ActionIndices and NetworkRibbonLocation.
#
# Those are Unreal's TAGGED PROPERTY type names. The asset is self-describing
# after all, so there is nothing to infer statistically - each record can be
# read field by field, by name.
#
# This also explains the last statistical run. It picked shift -6, which maps
# StopPoint onto "EnumProperty" and ActionPoint onto "Distance" - the property
# MACHINERY names. Those genuinely do appear once per record with a perfectly
# consistent delta, so the scoring was not malfunctioning: it found a real
# per-record field, just not the one being looked for. No amount of extra
# statistics would have fixed that. find_stop_points() is kept for assets that
# really are opaque, but for anything tagged this supersedes it.
#
# FPropertyTag layout (UE4):
#     FName  Name             (int32 index, int32 number)
#       -> "None" terminates the record
#     FName  Type
#     int32  Size             (bytes of value data that follow the header)
#     int32  ArrayIndex
#     type-specific header:
#       StructProperty  FName StructName, FGuid(16)
#       BoolProperty    uint8 value        (stored in the TAG, not the body)
#       ByteProperty    FName EnumName
#       EnumProperty    FName EnumName
#       ArrayProperty   FName InnerType
#       SetProperty     FName InnerType
#       MapProperty     FName KeyType, FName ValueType
#     uint8  HasPropertyGuid
#       if set, FGuid(16)
#     <Size bytes of value>

_PROP_SUFFIX = "Property"
_MAX_PROP_SIZE = 1 << 16


def _fname_at(data, off, names):
    """Reads an FName (int32 index + int32 Number). Returns (text, number) or
    None if the index is not in the table."""
    if off + 8 > len(data):
        return None
    idx, num = struct.unpack_from("<ii", data, off)
    if 0 <= idx < len(names) and 0 <= num < 1_000_000:
        return names[idx], num
    return None


def _read_tag(data, off, names, guid_byte=True):
    """Reads one FPropertyTag. Returns a dict, the string "None" at a record
    terminator, or None if this is not a tag."""
    nm = _fname_at(data, off, names)
    if nm is None:
        return None
    if nm[0] == "None":
        return "None"
    ty = _fname_at(data, off + 8, names)
    if ty is None or not ty[0].endswith(_PROP_SUFFIX):
        return None
    cur = off + 16
    size, array_index = struct.unpack_from("<ii", data, cur)
    if not (0 <= size <= _MAX_PROP_SIZE and 0 <= array_index < 4096):
        return None
    cur += 8
    extra = {}
    t = ty[0]
    if t == "StructProperty":
        sn = _fname_at(data, cur, names)
        if sn is None:
            return None
        extra["struct"] = sn[0]
        cur += 8 + 16
    elif t == "BoolProperty":
        extra["bool"] = data[cur] if cur < len(data) else None
        cur += 1
    elif t in ("ByteProperty", "EnumProperty", "ArrayProperty", "SetProperty"):
        en = _fname_at(data, cur, names)
        if en is None:
            return None
        extra["inner"] = en[0]
        cur += 8
    elif t == "MapProperty":
        k = _fname_at(data, cur, names)
        v = _fname_at(data, cur + 8, names)
        if k is None or v is None:
            return None
        extra["key"], extra["value_type"] = k[0], v[0]
        cur += 16
    if guid_byte:
        if cur >= len(data):
            return None
        has_guid = data[cur]
        if has_guid not in (0, 1):
            return None
        cur += 1
        if has_guid:
            cur += 16
    return {"name": nm[0], "type": t, "size": size, "array_index": array_index,
            "value_offset": cur, "end": cur + size, "tag_offset": off, **extra}


def _decode_value(data, tag, names):
    """Decodes the value types that carry timetable meaning. Anything else is
    left as raw bytes - the point is to read the schedule, not to reimplement
    Unreal."""
    t, off, size = tag["type"], tag["value_offset"], tag["size"]
    try:
        if t == "EnumProperty" or t == "NameProperty":
            v = _fname_at(data, off, names)
            return v[0] if v else None
        if t == "IntProperty" and size >= 4:
            return struct.unpack_from("<i", data, off)[0]
        if t == "FloatProperty" and size >= 4:
            return round(struct.unpack_from("<f", data, off)[0], 4)
        if t == "BoolProperty":
            return bool(tag.get("bool"))
        if t == "StructProperty" and size == 8:
            # FTimespan and FDateTime are both a bare int64 of 100ns ticks.
            (raw,) = struct.unpack_from("<q", data, off)
            if 0 <= raw < TICKS_PER_DAY:
                return {"ticks": raw, "time": _fmt_hms(raw / TICKS_PER_SECOND)}
            return raw
        if t == "StructProperty" and size == 12:
            return [round(x, 3) for x in struct.unpack_from("<fff", data, off)]
    except struct.error:
        return None
    return None


def _walk_record(data, off, names, guid_byte=True, max_props=64):
    """Reads a chain of tags up to the terminating None. Returns (props, end)
    or None if this is not a record."""
    props, cur = [], off
    for _ in range(max_props):
        tag = _read_tag(data, cur, names, guid_byte=guid_byte)
        if tag == "None":
            return props, cur + 8
        if tag is None:
            return None
        props.append(tag)
        cur = tag["end"]
    return None


def parse_track_records(path, max_records=4000, min_props=2):
    """Reads a RouteTimetableDataTrack as tagged properties and returns the
    StopPoints with their times, by name.

    No shift recovery, no statistics, no null model - the asset says what
    each field is. This supersedes find_stop_points() for tagged assets;
    that one remains for assets that really are opaque binary.

    Records are found by scanning for a readable tag chain rather than by
    trusting any header offset, so it works without knowing the engine
    version or where the export begins. A chain must terminate in None and
    hold at least min_props properties to be accepted, which is a strong
    filter: arbitrary bytes almost never produce two consecutive valid tags
    whose declared sizes chain into a terminator.
    """
    if not os.path.isfile(path):
        return {"error": "file_not_found", "path": path}
    uasset = _sibling_uasset(path)
    if not uasset:
        return {"error": "no_sibling_uasset", "path": path,
                "detail": "Point this at the .uexp - the name table lives in "
                          "the matching .uasset next to it."}
    with open(uasset, "rb") as f:
        names = _read_fname_strings(f.read())
    if not names:
        return {"error": "no_name_table", "path": uasset}
    with open(path, "rb") as f:
        data = f.read()

    # UE4 gained the HasPropertyGuid byte partway through its life. Rather
    # than assume, both are tried and whichever parses more records wins.
    best = None
    for guid_byte in (True, False):
        records, cur, n = [], 0, len(data)
        while cur < n and len(records) < max_records:
            got = _walk_record(data, cur, names, guid_byte=guid_byte)
            if got and len(got[0]) >= min_props:
                props, end = got
                records.append((cur, props))
                cur = end
                continue
            cur += 1
        if best is None or len(records) > len(best[1]):
            best = (guid_byte, records)
    guid_byte, records = best

    if not records:
        return {"error": "no_records_parsed", "path": path,
                "name_count": len(names),
                "detail": "No readable tag chains. This asset may genuinely "
                          "be opaque binary - fall back to /api/paks/stops."}

    field_use = Counter()
    types_seen = Counter()
    out = []
    for off, props in records:
        rec = {"offset": off, "fields": {}}
        for tag in props:
            field_use[tag["name"]] += 1
            val = _decode_value(data, tag, names)
            rec["fields"][tag["name"]] = val
        dt = rec["fields"].get("DataType")
        if isinstance(dt, str):
            types_seen[dt.split("::")[-1]] += 1
        out.append(rec)

    def times_of(rec):
        vals = []
        for k, v in rec["fields"].items():
            if isinstance(v, dict) and "time" in v:
                vals.append((k, v["time"]))
        return vals

    stops = [r for r in out
             if isinstance(r["fields"].get("DataType"), str)
             and r["fields"]["DataType"].endswith("StopPoint")]

    return {
        "path": path, "uasset": uasset,
        "name_count": len(names),
        "guid_byte": guid_byte,
        "records_parsed": len(out),
        "field_usage": field_use.most_common(30),
        "data_types": types_seen.most_common(),
        "stop_points": len(stops),
        "sample_records": [
            {"offset": r["offset"], "fields": r["fields"], "times": times_of(r)}
            for r in out[:8]
        ],
        "sample_stops": [
            {"offset": r["offset"], "fields": r["fields"], "times": times_of(r)}
            for r in stops[:20]
        ],
        "verdict": (
            f"{len(out)} records read as tagged properties, "
            f"{len(stops)} of them StopPoint. Field names come from the asset "
            "itself - nothing here is inferred."
            if stops else
            f"{len(out)} records read, but none carry a DataType of StopPoint. "
            "Check field_usage for what these records actually are."
        ),
    }
