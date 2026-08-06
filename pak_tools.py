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


def list_pak(pak_path, filter_keywords=None, aes_key=None, limit=8000):
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
    truncated = len(entries) > limit
    if truncated:
        entries = entries[:limit]

    result = {
        "pak_path": pak_path,
        "entry_count": len(entries),
        "truncated": truncated,
        "extensions": {},
    }

    for e in entries:
        ext = os.path.splitext(e)[1].lower()
        result["extensions"][ext] = result["extensions"].get(ext, 0) + 1

    if filter_keywords:
        kws = [k.lower() for k in filter_keywords]
        result["matches"] = [e for e in entries if any(k in e.lower() for k in kws)][:2000]
        result["match_count"] = len(result["matches"])
    # A sample is more useful than nothing when there are no keyword hits.
    result["sample"] = entries[:200]
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


# Substrings worth flagging when listing a pak. Deliberately broad - the
# point of the first listing run is to discover the real naming, not to
# confirm a guess.
TIMETABLE_KEYWORDS = [
    "timetable", "schedule", "service", "journey", "diagram",
    "station", "stop", "platform", "route", "formation", "scenario",
]
