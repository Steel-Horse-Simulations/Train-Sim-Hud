"""
game_files.py

Locates the Train Sim World install on disk and inventories the content
files, as the first step towards reading timetable data straight out of
the game rather than importing it from another HUD's database.

WHY THIS EXISTS / WHAT IS AND ISN'T KNOWN
------------------------------------------
Confirmed from this project's own code: the app currently gets timetables
via import_from_other_hud.py, i.e. out of a different application's
SQLite database. That other application builds its database by reading
the game's files. Doing that ourselves means answering, in order:

  1. Where is the game installed?              <- this module, solved
  2. What content files are actually there?    <- this module, inventory
  3. What format is the timetable data in?     <- NOT yet answered
  4. Can we parse it?                          <- depends on 3

Steps 1 and 2 are deterministic and are implemented here. Step 3 is not
guessed at anywhere in this file, deliberately: TSW is an Unreal Engine
title whose content ships as .pak archives, and exactly how (and whether)
timetables are readable varies by game version and by whether the pak is
encrypted. Rather than hard-code assumptions that might be wrong, this
scanner reports what is genuinely on disk so the format question can be
answered from real evidence.

The scan is strictly READ-ONLY. It stats and lists files. It never opens
paks, never writes to the game directory, and never modifies anything.
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

            content_dir = None
            for sub in _CONTENT_SUBPATHS:
                candidate = os.path.join(install, sub)
                if os.path.isdir(candidate):
                    content_dir = candidate
                    break
            found.append({
                "install_dir": install,
                "game_name": name,
                "content_dir": content_dir,
                "has_content": content_dir is not None,
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


def scan(extra_roots=None, max_files=4000):
    """Full scan: locate installs, then inventory the first one that has a
    Content directory."""
    installs = find_game_installs(extra_roots=extra_roots)
    with_content = [i for i in installs if i["has_content"]]

    result = {
        "platform": platform.system(),
        "installs_found": installs,
        "install_count": len(installs),
    }

    if not installs:
        result["conclusion"] = (
            "No Train Sim World install found in any of the usual Steam/Epic "
            "locations. If it's installed somewhere unusual, pass the folder "
            "manually and this will scan it."
        )
        return result

    if not with_content:
        result["conclusion"] = (
            "Found the game folder but no Content directory inside it. The "
            "install may be in an unexpected layout - the folder listing "
            "above shows what was found."
        )
        return result

    target = with_content[0]
    result["scanned"] = target
    result["inventory"] = inventory_content(target["content_dir"], max_files=max_files)
    return result
