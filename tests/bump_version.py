#!/usr/bin/env python3
"""Bumps APP_VERSION by regex, and FAILS LOUDLY if it does not change.

Several releases shipped with a stale version because the bump was done by
matching the previous literal string - once that drifted, the replace
silently did nothing and every later bump missed too. A no-op must be an
error, not a shrug.
"""
import re, sys, pathlib

new = sys.argv[1]
p = pathlib.Path(__file__).with_name("app.py") if len(sys.argv) < 3 else pathlib.Path(sys.argv[2])
src = p.read_text(encoding="utf-8")
out, n = re.subn(r'^APP_VERSION = "[\d.]+"', f'APP_VERSION = "{new}"', src,
                 count=1, flags=re.M)
if n != 1 or out == src:
    raise SystemExit(f"APP_VERSION not updated in {p} - pattern did not match")
p.write_text(out, encoding="utf-8")
print(f"APP_VERSION -> {new}")
