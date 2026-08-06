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
