"""
game_files.py

Locates the Train Sim World install on disk and inventories the content
files, as the first step towards reading timetable data straight out of
the game rather than importing it from another HUD's database.

HOW THE OTHER APP DOES IT (confirmed from its own source, not guessed)
----------------------------------------------------------------------
"TSW HUD & Timetable Extractor" is a Tauri app (Rust backend, HTML/JS
frontend). Its Extraction page calls these Rust commands:

    extractor_autodetect_tsw_root   find the install
    extractor_find_repak            locate the repak binary
    extractor_list_routes           enumerate route .pak files
    extractor_run_pak {pak_path}    unpack + parse ONE pak
    extractor_mark_completed        remember which paks are done
    extractor_rebuild_train_classes rebuild class metadata
    extractor_rebuild_thumbnails    decode class thumbnails from paks

The key line from its UI, verbatim:

    "Bundled repak.exe handles Oodle-compressed TSW6 paks. Drop it into
     hud/resources/repak.exe, next to hud.exe, or anywhere on PATH."
     -> https://github.com/trumank/repak

So the pipeline is:
    TSW install -> route .pak files -> repak unpacks them (handling Oodle
    compression) -> parse the unpacked assets -> SQLite

That settles the earlier open question: the timetable data is NOT in the
live HTTP API, and it IS inside .pak archives that need a real Unreal pak
unpacker. Its TSW path setting placeholder is:

    D:/SteamLibrary/steamapps/common/Train Sim World 6

i.e. the install ROOT, with the extractor finding route paks beneath it -
which is why hard-coding a Content subpath was the wrong approach.

WHAT THE LIVE API *DOES* GIVE (no extraction required)
-------------------------------------------------------
From real endpoint captures:
    DriverAid.PlayerInfo -> currentServiceName, e.g. "1A10"
    DriverAid.TrackData  -> stations[]/markers[] with stationName,
                            distanceToStationCM, platformLength
That covers "which service am I on" and "what's my next stop", but not
scheduled times or the full calling list - those need the pak route.

This module handles install/pak discovery only. It is strictly READ-ONLY:
it stats and lists files, never opens paks and never writes to the game
directory.
"""

import os
import platform


# The game's Unreal project is named TS2Prototype internally, so content
# sits under <install>/TS2Prototype/Content/... rather than under a folder
# named after the game. Both are checked, since this differs across
# versions and is the kind of detail worth verifying rather than assuming.
_CONTENT_SUBPATHS = [
    os.path.join("TS2Prototype", "Content"),
    "Content",
]

_GAME_DIR_NAMES = [
    "Train Sim World 6",
    "Train Sim World 5",
    "Train Sim World 4",
    "Train Sim World 3",
    "Train Sim World 2",
    "Train Sim World",
    "TrainSimWorld6",
    "TrainSimWorld5",
    "TrainSimWorld4",
    "TrainSimWorld3",
    "TrainSimWorld2",
]

# Extensions worth flagging. Unreal ships cooked content in .pak (plus
# .utoc/.ucas for IoStore builds); loose .uasset/.json/.csv/.xml would be
# far easier to read if any exist.
_INTERESTING_EXTS = {
    ".pak": "Unreal content archive",
    ".utoc": "IoStore table of contents",
    ".ucas": "IoStore container",
    ".sig": "pak signature",
    ".uasset": "loose Unreal asset",
    ".umap": "loose Unreal map",
    ".json": "loose JSON",
    ".csv": "loose CSV",
    ".xml": "loose XML",
    ".db": "SQLite database",
    ".sqlite": "SQLite database",
}

# Filename fragments that would suggest timetable/service content.
_TIMETABLE_HINTS = [
    "timetable", "schedule", "service", "journey", "scenario",
    "route", "station", "stop", "formation",
]


def _steam_library_roots():
    """Every plausible Steam library root. Steam can install games on any
    drive via library folders, so this checks the default location on
    each drive letter as well as the standard Program Files paths."""
    roots = []
    system = platform.system()

    if system == "Windows":
        for base in [
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("ProgramFiles", r"C:\Program Files"),
        ]:
            if base:
                roots.append(os.path.join(base, "Steam", "steamapps", "common"))
        # Secondary drives - a very common setup for large game installs.
        for letter in "CDEFGHIJKL":
            roots.append(rf"{letter}:\SteamLibrary\steamapps\common")
            roots.append(rf"{letter}:\Steam\steamapps\common")
            roots.append(rf"{letter}:\Games\Steam\steamapps\common")
        # Epic and Microsoft Store variants.
        for letter in "CDEFGHIJKL":
            roots.append(rf"{letter}:\Program Files\Epic Games")
            roots.append(rf"{letter}:\XboxGames")
    else:
        home = os.path.expanduser("~")
        roots += [
            os.path.join(home, ".steam", "steam", "steamapps", "common"),
            os.path.join(home, ".local", "share", "Steam", "steamapps", "common"),
            os.path.join(home, "Library", "Application Support", "Steam",
                         "steamapps", "common"),
        ]
    return roots


def _find_content_dirs(install_dir, max_depth=4):
    """Finds the real Content directory (or directories) by looking, rather
    than assuming a fixed layout.

    The first version of this guessed at <install>/TS2Prototype/Content and
    <install>/Content. On a real TSW5/TSW6 install both were absent, so the
    scan found the game and then reported nothing useful. This walks the
    install a few levels deep and identifies directories either named
    "Content" or containing .pak/.utoc files, which is what actually
    matters regardless of what the folders are called."""
    content_dirs = []
    pak_dirs = []
    install_dir = os.path.abspath(install_dir)
    base_depth = install_dir.rstrip("\\/").count(os.sep)

    for dirpath, dirnames, filenames in os.walk(install_dir):
        depth = dirpath.rstrip("\\/").count(os.sep) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
            continue
        # Skip obvious noise so the walk stays fast.
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in {"engine", "binaries", "saved",
                                            "intermediate", "_commonredist"}]
        base = os.path.basename(dirpath)
        if base.lower() == "content":
            content_dirs.append(dirpath)
        if any(f.lower().endswith((".pak", ".utoc", ".ucas")) for f in filenames):
            pak_dirs.append(dirpath)

    return content_dirs, pak_dirs


def describe_layout(install_dir, max_depth=3, max_entries=400):
    """Returns the actual directory tree under an install, so an unexpected
    layout can be seen rather than guessed at."""
    entries = []
    install_dir = os.path.abspath(install_dir)
    base_depth = install_dir.rstrip("\\/").count(os.sep)
    for dirpath, dirnames, filenames in os.walk(install_dir):
        depth = dirpath.rstrip("\\/").count(os.sep) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
            continue
        rel = os.path.relpath(dirpath, install_dir)
        big = [f for f in filenames
               if f.lower().endswith((".pak", ".utoc", ".ucas", ".uasset",
                                      ".json", ".csv", ".xml", ".db", ".sqlite"))]
        entries.append({
            "dir": "." if rel == "." else rel,
            "subdirs": sorted(dirnames)[:30],
            "file_count": len(filenames),
            "notable_files": sorted(big)[:20],
        })
        if len(entries) >= max_entries:
            break
    return entries


def find_game_installs(extra_roots=None):
    """Returns every TSW install found, each with its resolved Content
    directory if one exists. Cheap - only stats directories."""
    found = []
    seen = set()
    roots = list(_steam_library_roots())
    if extra_roots:
        roots = list(extra_roots) + roots

    for root in roots:
        try:
            if not os.path.isdir(root):
                continue
        except OSError:
            continue
        for name in _GAME_DIR_NAMES:
            install = os.path.join(root, name)
            key = os.path.normcase(os.path.abspath(install))
            if key in seen:
                continue
            try:
                if not os.path.isdir(install):
                    continue
            except OSError:
                continue
            seen.add(key)

            # Try the known layouts first (fast), then fall back to
            # actually searching the install for real content.
            content_dir = None
            for sub in _CONTENT_SUBPATHS:
                candidate = os.path.join(install, sub)
                if os.path.isdir(candidate):
                    content_dir = candidate
                    break

            content_dirs, pak_dirs = ([], [])
            if content_dir is None:
                content_dirs, pak_dirs = _find_content_dirs(install)
                if content_dirs:
                    content_dir = content_dirs[0]
                elif pak_dirs:
                    # No folder called Content, but we found the paks -
                    # that's the directory that actually matters.
                    content_dir = pak_dirs[0]

            found.append({
                "install_dir": install,
                "game_name": name,
                "content_dir": content_dir,
                "has_content": content_dir is not None,
                "all_content_dirs": content_dirs,
                "pak_dirs": pak_dirs,
            })
    return found


def inventory_content(content_dir, max_files=4000):
    """Walks a Content directory and summarises what's there: a count and
    total size per file extension, the largest archives, and any file
    whose name hints at timetable data.

    Read-only. Capped at max_files so an enormous install can't hang the
    request - `truncated` reports whether the cap was hit."""
    if not content_dir or not os.path.isdir(content_dir):
        return {"error": "content_dir_not_found", "content_dir": content_dir}

    by_ext = {}
    biggest = []
    hinted = []
    total_files = 0
    truncated = False

    for dirpath, _dirnames, filenames in os.walk(content_dir):
        for fn in filenames:
            total_files += 1
            if total_files > max_files:
                truncated = True
                break
            ext = os.path.splitext(fn)[1].lower()
            full = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0

            entry = by_ext.setdefault(ext, {"count": 0, "bytes": 0})
            entry["count"] += 1
            entry["bytes"] += size

            rel = os.path.relpath(full, content_dir)
            if ext in _INTERESTING_EXTS:
                biggest.append({"path": rel, "bytes": size, "ext": ext})

            lower = fn.lower()
            if any(h in lower for h in _TIMETABLE_HINTS):
                hinted.append({"path": rel, "bytes": size, "ext": ext})
        if truncated:
            break

    biggest.sort(key=lambda e: e["bytes"], reverse=True)
    hinted.sort(key=lambda e: e["bytes"], reverse=True)

    ext_summary = [
        {
            "ext": ext or "(no extension)",
            "description": _INTERESTING_EXTS.get(ext, ""),
            "count": v["count"],
            "bytes": v["bytes"],
            "mb": round(v["bytes"] / (1024 * 1024), 1),
        }
        for ext, v in sorted(by_ext.items(), key=lambda kv: kv[1]["bytes"], reverse=True)
    ]

    return {
        "content_dir": content_dir,
        "total_files": total_files,
        "truncated": truncated,
        "by_extension": ext_summary,
        "largest_content_files": biggest[:40],
        "timetable_name_matches": hinted[:60],
    }


def find_repak():
    """Looks for the repak binary - the Unreal pak unpacker the other
    extractor uses (github.com/trumank/repak). Checks next to this app,
    a resources/ subfolder, and anywhere on PATH, mirroring where that
    app says to put it."""
    names = ["repak.exe", "repak"]
    here = os.path.dirname(os.path.abspath(__file__))
    search_dirs = [
        here,
        os.path.join(here, "resources"),
        os.path.join(here, "tools"),
    ]
    search_dirs += [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]

    for d in search_dirs:
        for n in names:
            try:
                candidate = os.path.join(d, n)
                if os.path.isfile(candidate):
                    return {"found": True, "path": candidate}
            except OSError:
                continue
    return {
        "found": False,
        "path": None,
        "hint": ("repak not found. It's the pak unpacker needed to read timetable "
                 "data out of the game's .pak files (it handles the Oodle "
                 "compression TSW uses). Download from "
                 "https://github.com/trumank/repak/releases and put repak.exe "
                 "next to this app, in a resources/ folder, or on PATH."),
    }


def scan(extra_roots=None, max_files=4000):
    """Full scan: locate installs, inventory content, and always report the
    actual directory layout so an unexpected structure is diagnosable
    rather than just producing 'not found'."""
    installs = find_game_installs(extra_roots=extra_roots)
    with_content = [i for i in installs if i["has_content"]]

    result = {
        "platform": platform.system(),
        "installs_found": installs,
        "install_count": len(installs),
        "repak": find_repak(),
    }

    if not installs:
        result["conclusion"] = (
            "No Train Sim World install found in any of the usual Steam/Epic "
            "locations. If it's installed somewhere unusual, paste the folder "
            "in the box and this will scan it directly."
        )
        return result

    # Always show what's actually inside the installs - this is what makes
    # an unexpected layout debuggable instead of a dead end.
    result["layouts"] = {
        i["install_dir"]: describe_layout(i["install_dir"])
        for i in installs[:2]
    }

    if not with_content:
        result["conclusion"] = (
            "Found the game but couldn't identify a content/pak directory, even "
            "after searching the install. The 'layouts' section shows the real "
            "folder structure - that will say where the data actually lives."
        )
        return result

    target = with_content[0]
    result["scanned"] = target
    result["inventory"] = inventory_content(target["content_dir"], max_files=max_files)
    return result
