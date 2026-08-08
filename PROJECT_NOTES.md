# TSW Hud — Project Notes

Living reference document for continuity between chats. Last updated after v7.37.0.
If you're a new Claude chat reading this for the first time: welcome, and please read
this whole file before doing anything else.

## Cleanup instruction (read this first)

This file and the `design_previews/` folder are development-only scaffolding — the
running app never loads or references either of them. **Once every "IN PROGRESS"
item below has actually been built and shipped for real** (not just previewed),
delete this file and `design_previews/` in that same update, since their content
will be fully superseded by the real code by that point.

**Do not delete them before then.** They carry design decisions that took many
back-and-forth rounds with the user to nail down (data model, speed-resolution
order, exact colour thresholds, etc.) — deleting them early, just because some
time or one update has passed, would lose real context before it's ever been used.
If asked to "clean up unneeded files" while items below are still unbuilt, leave
this file and `design_previews/` alone and explain why, rather than removing them.

## What this is

Windows desktop HUD companion app for Train Sim World 6, built around TSW's
`-HTTPAPI` launch flag (`http://127.0.0.1:31270`, key in `CommAPIKey.txt`, header
`DTGCommKey`). Flask backend (`app.py`) + HTML/JS/CSS pages served locally, runnable
standalone in a browser or via `pywebview`. Launch scripts: `run.bat` (pywebview),
`run_browser.bat` (plain browser).

## Ground rules established over many sessions (please keep following these)

1. **Aesthetic/visual changes → preview first, get explicit approval, THEN code.**
   Never skip straight to implementing a UI change. Preview workflow specifics:
   build the mockup as a real standalone HTML file (same pattern as
   `design_previews/`), actually render it and take a screenshot to check it looks
   right and measure alignment/sizing objectively — but the screenshot is for this
   internal check only, NOT something to hand over; deliver just the `.html` file
   itself so the person can open it and click around. Wait for explicit approval on
   the preview before writing any of it into the real app files.
   roll a random accent colour from the theme set (purple, green, blue, amber,
   crimson, teal, rose, slate, rainbow), excluding whichever 2-3 were used most
   recently, unless the panel needs fixed real-world colours instead (e.g. the
   GSM-R radio replica, which intentionally ignores the theme system to look like
   the real hardware); match the existing dark-glass visual language unless asked
   to deliberately break from it; actually render and screenshot the mockup (measure
   alignment/sizing objectively) before showing it, rather than trusting the code
   looks right; letterbox to the real device frame if it's meant for a specific
   phone screen rather than stretching to fill.
2. **Functional/non-aesthetic changes → discuss the plan first**, implement only once
   the person says they're ready.
3. **Test everything against real captured data before shipping.** This project has a
   strong track record of finding real bugs (wrong field names, sizing quirks, CSS
   selector collisions) by actually rendering/measuring/testing rather than assuming
   code is correct. Keep doing this — it's caught genuine bugs repeatedly.
4. **Never guess at API field shapes.** Every endpoint used in this app was confirmed
   against a real capture before being wired in. If a new endpoint is needed, ask the
   person to grab a raw response from the Discovery page first.
5. Mini-HUD previews on the Customisation tab → leave alone unless explicitly asked.
6. Dashboard HUDs (Desktop + Tablet) → always treated as an identical pair, share
   `dashboard.js`.
7. **Shipping a real update** (not a preview): bump `APP_VERSION` in `app.py` following
   this versioning convention (confirmed with the user): **first number** = a brand new
   feature (e.g. 3.x.x -> 4.0.0); **middle number** = an update/addition to an existing
   feature (e.g. 4.0.0 -> 4.1.0, as the live-detection-reconciliation addition to the
   Train Classes catalog was); **last number** = pure bug fixes or cosmetic changes with
   no new capability (e.g. 4.1.0 -> 4.1.1). Run a full regression check first (Python
   compile check on every `.py` file, JS syntax check on every page's inline `<script>`,
   and load every page/route) before packaging; create any new folders the update needs
   directly in the project (following the pattern of `design_previews/`, `data/`,
   `images/`) rather than asking the person to create them manually; zip the whole
   project folder as "TSW Hud"; update this file to reflect whatever changed, per the
   cleanup rule above.

## Architecture

```
TSW Hud/
  app.py                    - Flask backend, all API routes. Current version: 6.0.2
  loco_profiles.py          - Known-locomotives DB (SQLite) - self-healing class
                               names + per-loco speed customisation (the ORIGINAL,
                               simple loco DB - separate from train_classes_db.py)
  timetable_db.py           - Local DB for imported real timetable journeys
                               (data/timetables.db) - journeys/segments/stops tables,
                               hard-coded EDITABLE_FIELDS allow-list per table
  train_classes_db.py       - Local DB for imported real train-class metadata +
                               thumbnails (data/train_classes.db) - UK-visible by
                               default, non-UK imported but hidden (not discarded)
  import_from_other_hud.py  - One-off/re-runnable import script, pulls from a copy
                               of the other "TSW HUD" app's own SQLite database
  tsw_timetable_importer.py - Standalone chaining/export logic reused by the
                               import script above (also usable on its own via CLI)
  other_hud_sync.py         - Background auto-discovery + sync: finds the other
                               app's database/images automatically, imports new
                               data on startup and every ~15 min after. Started as
                               a daemon thread in app.py's main(), same pattern as
                               the weather sync thread.
  requirements.txt
  run.bat / run_browser.bat
  data/                      - SQLite DBs live here, recreated on first run via
                               each module's init_db()
  images/
    train_classes/            - Loco thumbnail images, NOT bundled with the app -
                               copy your own files here matching thumbnail_path
                               values from train_classes_db (served at
                               /images/train_classes/<filename>)
  pages/
    style.css, theme.js       - shared theme system (9 themes: purple, green, blue,
                               amber, crimson, teal, rose, slate, rainbow)
    dashboard.html/.js        - Desktop + Tablet HUD (shared JS)
    dashboard_tablet.html
    timetable.html            - color-theme placeholder only, NOT the same thing as
                               the real timetables_browser.html below - still no
                               live in-game timetable HUD panel exists yet. Earmarked
                               by the user for a different, not-yet-built part of the
                               timetable feature. Has its own home-screen icon as of
                               v5.2.0 (manifest-timetable-placeholder.json, clock-face
                               glyph, deliberately distinct from timetables_browser's
                               document icon)
    timetables_browser.html   - NEW v3.0.0: admin page, search real imported
                               journeys, click to expand/edit stop list inline
    map.html                  - Leaflet map, optional real-world rail overlay toggle
                               (OpenRailwayMap)
    explorer.html              - raw API Discovery tool (list/get browser)
    weather.html                - Real Weather sync (Open-Meteo -> game)
    hud_lab.html                 - NEW v5.0.0: sandbox for testing new gauge/panel ideas
                               against live data before they touch the real Dashboard;
                               includes a continuous live API path watcher (300ms poll,
                               same rate as the real Dashboard)
    customisation.html          - theme picker + "Known Trains" panel (the
                               ORIGINAL simple loco_profiles.py list, NOT the new
                               train_classes admin page)
    classes.html                - Classes (individual train classes + subclasses).
                               Was called "Groups" before v7.x; API paths still
                               say groups, deliberately unchanged.
    groups.html                 - Groups = families. Several Classes belong to one
                               Group (e.g. Class 801/802/805 under "Class 8xx").
    operators.html              - Operators + their liveries (colour per livery,
                               logo by operator short_code)
    known_trains.html            - Known Trains v2 main list, grouped by family name
                               (falling back to class name), status dots,
                               needs-attention section. DRIVEN TRAINS ONLY.
    known_trains_edit.html       - Known Trains v2 individual edit page (incl. variants)
    known_trains_group.html      - Known Trains v2 class settings page
  design_previews/            - APPROVED but NOT YET BUILT design mockups for the
                               Known Trains v2 hierarchy (see below) - static HTML,
                               safe to open directly in a browser. Not wired into
                               the real app.
```

## Current version: 7.42.0

## Shipped features (working, tested against real data)

- Dashboard: speed gauge (300ms poll), gradient, signal + aspect, weather chip,
  loco class label, upcoming speed limits panel, route/service panel
- Map HUD: live position tracking, smooth interpolated movement, optional real-world
  rail infrastructure overlay (OpenRailwayMap tiles)
- Real Weather sync: Open-Meteo -> `WeatherManager.*` fields, temperature-capped
  snow logic, 60s cycle with per-second interpolation
- Discovery/Explorer: raw `/list` and `/get` browser against the live API
- 9-theme system across all HUDs
- **Known-locomotives DB (`loco_profiles.py`)**: solves "loco class sometimes shows
  clean, sometimes shows messy raw string" - records the raw `ObjectClass` string as
  a stable key, remembers the clean name (from `IS_GetVehicleInfo`) forever once
  learned, even if a later session only gives the raw fallback again. Also stores a
  per-loco custom speedometer max. Dashboard's speed gauge ring scales against this
  instead of a fixed 100mph. This is the ORIGINAL simple loco DB, separate from the
  newer `train_classes_db.py` below - as of v4.1.0, live detection feeds BOTH databases
  side by side (see v4.1.0 below), but they remain two separate databases/pages, not
  merged into one - `loco_profiles.py` still only ever has the simple fields it always
  had (clean name, max speed), `train_classes_db.py` is the one that also gets thumbnails/
  livery/manufacturer whenever the wider catalog catches up with something.
- "Known Trains" panel on Customisation page: lists every recorded loco (from
  `loco_profiles.py`), times seen, editable max speed. Still the *simple* original
  version - the real "Known Trains v2" hierarchy (`known_trains.html` etc., shipped
  v6.0.0) is a genuinely separate system built on `train_classes_db.py`, not a
  replacement for this panel. User deliberately chose NOT to rewire the live
  Dashboard (which still reads from `loco_profiles.py`) to the new hierarchy in the
  v6.0.0 build - that remains a distinct, not-yet-done future step.

### v3.0.0 - Real Timetables + Train Classes (shipped this update)

Solved the two longest-standing open investigations (timetable export data, and
loco images) by finding that the *other* "TSW HUD" app (a native Tauri desktop app,
separate from this project) keeps its own real, structured SQLite database
(`tsw_hud.db`, ~3.8GB, WAL mode) with genuine parsed timetable services and
train-class metadata including thumbnail image paths - no OCR, pak-parsing, or
repak needed, just reading that database directly.

- **`tsw_timetable_importer.py`** - standalone chaining/export logic. TSW splits
  some services (e.g. on routes like Fife Circle) across a player-driven leg and
  an AI-driven continuation, stored as separate DB rows sharing a `section_id` -
  this reconstructs the full journey by chaining segments whose times line up
  within a 2-minute tolerance. Validated against ~40 real chained services with
  consistently correct, realistic stopping patterns. `--export-json` mode dumps
  every real timetable (all routes) to a JSON snapshot in a few seconds (~147MB
  for the whole game).
- **`timetable_db.py` / `train_classes_db.py`** - new local app databases
  following the `loco_profiles.py` pattern (SQLite in `data/`). Each table has a
  hard-coded `EDITABLE_FIELDS` allow-list enforced in the DB layer itself - IDs
  and foreign keys can never be edited via the update functions; everything else
  (names, times, locations, coordinates, speeds, livery, visibility, notes) is
  freely editable. `train_classes_db` detects UK train classes via a confirmed
  join (`train_classes.name` = `formation_vehicles.class_name` -> `formation_id`
  -> `route_formations.route_id` = `routes.id` -> `routes.country_id` =
  `countries.id`, code='GB') - non-UK entries import hidden (`is_visible=0`), not
  discarded, switchable per-entry later without a re-import.
- **`import_from_other_hud.py`** - the actual one-off/re-runnable import script,
  reuses `tsw_timetable_importer.py`'s functions rather than duplicating logic.
- **New API routes** in `app.py`: `GET/PATCH /api/timetables`, `GET
  /api/timetables/<id>`, `PATCH /api/timetables/segments/<id>` and `/stops/<id>`,
  `GET /api/train_classes`, `GET/PATCH /api/train_classes/<id>`, `GET
  /images/train_classes/<filename>` (serves from `images/train_classes/`, not
  bundled - copy your own image files there).
- **Two new admin pages**: `pages/timetables_browser.html` and
  `pages/train_classes.html` - see the Architecture section above for exactly what
  each does and does NOT do (the train classes page is a flat editor, not yet the
  full Known Trains v2 hierarchy).
- Tested end-to-end against synthetic databases mimicking the real schema before
  shipping (unit-test-style scripts, `node --check` for JS) - NOT yet tested by the
  user against their actual real database/app in production use.

## REMOVED features (do not resurrect without a new conversation)

- OCR-based timetable import (screenshot -> text -> structured stops) - built,
  shipped, then explicitly removed by the user (v2.16.0) once the real
  database-driven import (see v3.0.0 above) became the better path forward. All
  OCR/pyautogui code was deleted. Don't rebuild this unless explicitly asked again.

## SHIPPED in v6.0.0 + v6.0.1 + v6.0.2 - "Known Trains v2" hierarchy, built for real

**Deliberately NOT done in this build, a clear future step when picked up:** rewiring the
live Dashboard (speed gauge scaling, the max-speed tick's `DIAL_HEADROOM_MULTIPLIER`
placeholder) to pull from this new hierarchy's `resolve_speeds()` instead of the old
`loco_profiles.py` system. User explicitly chose to scope this build to just the new
catalog/admin system. When this does get picked up, it directly resolves the placeholder
multiplier noted back in the max-speed-tick work - the real two-value (max speed + dial
max) resolution this hierarchy provides is exactly what that placeholder was standing in
for.


Full build of the design approved earlier (all 3 mockups: list/edit/group-settings).
User explicitly chose NOT to rewire the live Dashboard (speed gauge, max-speed tick) to
this new hierarchy in this pass - that stays on the old `loco_profiles.py` system for
now, a deliberate, separate future step. This build is the catalog/admin system only.

### Schema (extends train_classes_db.py in place, does not replace it)
Two new tables - `loco_groups` (name, default_max_speed_mph, default_dial_max_mph,
hud_panels as a JSON array) and `loco_subclasses` (belongs to a group, name, its own
optional max_speed_override_mph/dial_max_override_mph). The existing `train_classes`
table gained new columns via a safe ALTER-TABLE migration (never CREATE/DROP, checked via
PRAGMA table_info so it's idempotent and safe to run on every startup): `group_id`,
`subclass_id`, `livery_name` (distinct from the existing `livery_id`, which turned out to
already be doing double duty as "company/operator" with real imported data like "SCR"/
"EWS" - kept as-is rather than renamed, just documented), `is_steam`, `is_diesel` (joining
the existing `is_electric`), `electrification_types` (JSON array, client-side serialized),
`max_speed_override_mph`, `dial_max_override_mph` (kept separate from the existing
`max_speed_kph`/`max_speed_mph`, which stay as catalog-sourced reference data and now
double as a sensible fallback in the speed-resolution chain, below).

**Migration safety was the top priority here, given the user has real production data in
this database now** - tested explicitly against a simulated pre-existing database (old
schema, real row with a custom display_name and a real times_seen count) and confirmed:
all existing data survives completely intact, new columns get sensible NULL defaults, and
running the migration twice in a row is a safe no-op (idempotent, matches how `init_db()`
actually gets called on every app startup).

### Core logic (train_classes_db.py)
- `resolve_speeds()` - the four-tier chain from the approved design: individual override
  -> subclass override -> group default -> catalog-sourced real speed -> hardcoded 100mph
  last resort. Tested at every tier independently, including confirming the catalog
  fallback returns the REAL converted catalog value (not a coincidental match with the
  hardcoded default - deliberately tested with a non-round conversion to be sure).
- `compute_power_label()` - Bi-Mode/Tri-Mode, tested across every combination.
- `compute_completion()` - the 7-field percentage-based status dot, tested at 0%/14%/57%/
  100% to confirm all four colour thresholds (red/amber/yellow/green) trigger correctly.
- `needs_attention()` - missing display name OR group OR subclass, tested to correctly
  include/exclude the right entries.
- Full group/subclass CRUD functions.

### API routes
`/api/known_trains/groups` (list/create), `/api/known_trains/groups/<id>` (get with
subclasses+members included, patch), `/api/known_trains/groups/<id>/subclasses` (create),
`/api/known_trains/subclasses/<id>` (patch), `/api/known_trains/list` (the main list view
- resolves speeds/status/power server-side per row, so the page itself does zero
resolution logic, just renders what it's given). All tested end-to-end through the real
Flask test client before any UI was built on top of them.

### Three real pages, all built and tested against real seeded data + real save round-trips
- **`known_trains.html`** - the list, grouped by group name (ungrouped entries get their
  own bucket), status dots, thumbnails (falls back to a "no photo" placeholder on load
  error), needs-attention section that auto-hides when empty. Confirmed correct against a
  realistic 3-entry scenario (one fully in a group+subclass, one in a group only, one
  totally unassigned) - right grouping, right dot colours, right attention-panel contents.
- **`known_trains_edit.html`** - ported faithfully from the approved preview, all its
  tested interactivity intact: live field-completion tracking, live Bi-Mode/Tri-Mode
  badge, electrification sub-options only shown when Electric is checked, and the
  custom-vs-default speed caption/reset behaviour, now driven by REAL resolved defaults
  (fetches the selected group's detail to know its actual default/subclass overrides,
  not hardcoded preview numbers). Changing the Group dropdown live-refetches that group's
  subclasses. Full save round-trip tested: edited every field, saved, confirmed every
  single value persisted correctly in the database afterward.
- **`known_trains_group.html`** - group name and default speeds (save on blur, not every
  keystroke), the same tested add-subclass interaction from the approved preview now
  actually creating real subclasses, per-subclass speed override editing, HUD panel
  toggles (persisted as the `hud_panels` JSON array - "Speed gauge" shown as always-on/
  disabled since it's the one panel that actually exists; Air Pressure/Ammeter toggle
  real preference storage even though those panels aren't built yet), and a real members
  list linking each one to its edit page. Every interaction tested end-to-end: renamed a
  group, added a subclass through the real form, toggled a panel - all three confirmed
  actually persisted in the database afterward, not just updating the page's own view of
  itself.

New shared CSS variable added: `--status-yellow` (`pages/style.css`) - the existing shared
stylesheet only had green/amber/red for status colours, but this design needs a 4th tier.

### Nav
All pages sharing the topbar updated with a "Known Trains" link, using the same careful
unique-anchor insertion approach (and duplicate-count verification) as previous nav
additions, avoiding the prose-corruption bug from the v3.2.0 build.

### v6.0.1 - fixed a real Service Worker caching bug found via real device testing
User reported the tablet still showed the old version after the v6.0.0 update, even
though the server had genuinely been updated. Real bug, confirmed and fixed, not assumed:
`pages/sw.js`'s `CACHE_NAME` was a hardcoded string (`'tsw-hud-shell-v1'`) that never
changed between releases. Browsers only re-check a Service Worker for updates when its
OWN script bytes change - since `sw.js`'s content never changed across v3.2.0 through
v6.0.0 (only the pages it cached did), already-installed devices kept using their
original worker instance and its original cache indefinitely, regardless of what the
server was actually serving. This is a well-known class of PWA bug, not specific to this
app, but it was genuinely present here.

**Fixed by making `sw.js` version-aware**: it's now served dynamically by a new Flask
route (`/pages/sw.js` in `app.py`, registered ahead of the generic static-file route)
rather than as a static file, substituting the real running `APP_VERSION` into
`CACHE_NAME` on every request (`tsw-hud-shell-6.0.1`, etc.) via a `__APP_VERSION__`
placeholder in the source file. Response sent with `Cache-Control: no-cache` so the
script itself always gets revalidated too. This means every real version bump now
automatically changes the Service Worker's own bytes, which is what actually triggers
browsers to notice, install the new worker, and (via the existing `activate` handler)
clean up the old cache - no separate manual step needed going forward, it rides on the
same `APP_VERSION` bump already required for every release.

Also added the 3 new Known Trains v2 pages to the pre-cached shell list, since they
existed after `sw.js` was originally written and hadn't been added.

**Tested properly, not just theorized**: a first naive test used two separate fresh
browser profiles for "before" and "after," which didn't actually validate anything (a
fresh profile has no old cache to bust in the first place) - caught and corrected. The
real test used one persistent browser profile across a genuine simulated update (real
version bump, real page content change): confirmed the old cache name existed after
initial install, and confirmed - after one reload, the same way a real returning device
would behave - the new content was correctly served and the old cache was gone.



### Max-speed tick (update to the existing speed gauge feature)
Built into the real `dashboard.js`/`dashboard.html`/`dashboard_tablet.html` (both HUDs
updated identically, per the "always treated as a pair" rule) - a tick mark at the loco's
max-speed position, ring turns `--status-red` (not themed, matches the existing
over-limit red) once actual speed exceeds it, plus an "Over max speed" badge. Exceeding
the loco's own max speed takes visual priority over the normal track-speed-limit colour
logic - judged a more fundamental "this shouldn't be physically possible" state, not just
"a bit over the posted limit" (this priority ordering wasn't separately re-confirmed with
the user beyond being proposed at preview time - flag if it should work differently).

**The `DIAL_HEADROOM_MULTIPLIER = 1.2` placeholder from the preview remains in place and
is now live** - the real "speedometer dial max" field from Known Trains v2 still doesn't
exist yet, so the gauge still fakes headroom above the one stored max-speed value rather
than using a real independently-set dial-max. Clearly commented in `dashboard.js` with a
`# search for this comment` pointer for whoever replaces it once Known Trains v2 ships
its real field.

Tested with a real Playwright browser against MOCKED live API responses (not just unit
logic) - intercepted `/api/loco` and the game's `HUD_GetSpeed` proxy endpoint to drive the
gauge through well-under/just-under/exactly-at/over-max speed states, confirming tick
position, ring colour (including that `var(--status-red)` actually resolves correctly
when set via JS `setAttribute`, not just assumed), and badge visibility all update
correctly - on both the desktop AND tablet dashboards separately.

### HUD Lab (new page in v5.0.0, upgraded to a multi-pill grid in v5.1.0)
`pages/hud_lab.html`, linked in every page's nav. A sandbox for trying new gauge/panel
ideas against live game data before anything touches the real Dashboard - nothing built
here can affect production. **v5.1.0 update:** the original single-value watcher became a
**centered, auto-reflowing grid of independent pills** (flexbox wrap + centered, so adding
or removing a pill naturally re-centers everything else with no manual layout math) -
add any number of CommAPI paths at once, each gets its own pill (label + live value +
path), polling independently at the same 300ms rate as the real Dashboard. Click a pill to
expand its raw JSON response inline. Visual style matches the existing speed/limit pills
(dark rounded chip), per the user's request.

Tested thoroughly: 3 simultaneous pills with different mocked live values, confirmed each
updates completely independently (changing one pill's value doesn't affect the others),
click-to-expand raw detail works, and - the trickiest part - removing one pill correctly
shrinks the grid AND the remaining pills keep polling correctly afterward (no broken
interval references left behind).

Useful immediately for the still-open Brake Pipe / Main Reservoir investigation - can now
watch `HUD_GetBrakeGauge_1` and `HUD_GetBrakeGauge_2` side by side in their own pills to
compare which is which, whenever the user is next at their computer.

### Ammeter panel (approved design, NOT built)
`design_previews/preview_ammeter_panel.html` - green accent (rolled, excluding rainbow/
crimson/purple as recently used), needle gauge matching the real 0-8 kA scale and colour
bands confirmed from the in-cab photo used earlier to solve the ÷1000 scaling question.
Built on the real HUD frame markup, letterboxed to the real 16:9 desktop frame. Tested via
real rotation-matrix math across 4 demo states (idle/normal/heavy/red-zone), confirming
the needle sweeps monotonically and the red-zone threshold triggers at the right value.

**Two things flagged as open/provisional, not yet resolved:**
- **Position** - shown in the bottom-left corner for the preview, but that's the same slot
  the upcoming-limits panel uses when there's a limit ahead. Needs a real placement
  decision before this gets built for real.
- **Needle sweep angle range (-50° to +50°)** is hand-picked to look right, not measured
  against the real gauge. Worth confirming against the actual dial via the HUD Lab
  watcher once the user is back at their computer.

### Also discussed this session, NOT yet acted on
- **Brake pipe / main reservoir gauges**: two real CommAPI endpoints were already
  confirmed to exist in earlier Discovery captures this project - `HUD_GetBrakeGauge_1`
  and `HUD_GetBrakeGauge_2` - but which gauge is which, and the correct psi scaling, still
  needs one real captured reading (ideally alongside a photo of the physical gauges for
  comparison, same approach as the Ammeter). Needs the user at their computer with the
  game running - the new HUD Lab watcher above is specifically built to make that step
  easy once they are.
- **Per-instrument configurable values in Known Trains v2**: user's idea, agreed as the
  right direction - every future instrument (ammeter, brake gauges, etc.) will need its
  own definition of what's configurable and what range means (mirroring how speed already
  has two independent values). Not fully spec'd yet - to be defined as part of the Known
  Trains v2 design work itself, once that's picked up, rather than guessed at per-gauge
  before any of them exist for real.

### v6.0.2 - repointed the existing home-screen icon to Known Trains
User's existing "TSW Train Classes" home-screen icon (added back in v3.2.0/v5.2.0) was
still correctly pointing at the OLD flat `train_classes.html` page - that page was never
touched and still works exactly as it always has, so this wasn't a bug in the icon
itself. The real gap: Known Trains v2 (shipped v6.0.0) never got its own installable
identity at all - no manifest link, no icon - so there was nothing to add to a home
screen for it in the first place. User asked to repoint the EXISTING icon at Known
Trains instead of building a separate new one.

- `manifest-train-classes.json`: `start_url` changed to `/pages/known_trains.html`.
  `scope` widened from an exact single-page match to `/pages/` - necessary because Known
  Trains is 3 linked pages (list/edit/group settings), not one self-contained page like
  the old Train Classes was, and a narrow scope would have caused navigation between them
  to potentially break out of standalone mode into regular browser chrome.
- `pages/train_classes.html` (the old page): removed its `<link rel="manifest">` and
  `install-prompt.js` - it no longer has its own install identity now that its former
  manifest points elsewhere, and leaving them in would have meant visiting that page and
  trying to "Add to Home Screen" would silently produce a Known Trains icon, which would
  have been confusing. The page itself is otherwise completely unchanged.
- `pages/known_trains.html`: gained the manifest link + `install-prompt.js` it was
  missing - necessary for the browser to actually pick up the icon/name/start_url when
  installing from that page directly, since a manifest file existing elsewhere doesn't
  help unless the current page references it.
- `install.html` hub page: card updated to link to and describe Known Trains.

Confirmed via direct testing (not assumed): the manifest's `start_url`/`scope` resolve
correctly, `train_classes.html` no longer references the manifest, `known_trains.html`
now does, and all affected pages still load correctly.



User reported Train Classes always showed "Syncing..." and didn't work offline on the
real tablet, after completing HTTPS setup. Two real bugs found and fixed - not
theorized, both reproduced and confirmed fixed with real tests, not just assumed.

### Bug 1: sync pull had no pagination, so a first-ever sync against a large real
dataset would try to return everything in one response
The client's fetch timeout was 4 seconds; a first-ever sync has to pull the ENTIRE
dataset (every journey and every train class that's ever existed), which - given the
known real scale (the earlier full timetable export alone was ~147MB) - could easily
take longer than that. This meant: the sync kept timing out and retrying forever
(matching "always showing syncing"), and since it never once completed, IndexedDB never
got populated with real data at all (matching "doesn't work offline" - there was nothing
cached to fall back to).

**Fixed with proper pagination**, not just a bigger timeout number (which would only
have delayed the same problem at a larger dataset size): `get_changes_since()` in both
`timetable_db.py` and `train_classes_db.py` now return one bounded page at a time (100
journeys / 300 train classes per call) plus a cursor and `has_more` flag; `/api/sync/
changes` accepts `after_journey_id`/`after_train_class_id` params; the client's `pull()`
now loops through pages until both are fully caught up, so no single request/response is
ever large regardless of total dataset size.

**A real bug was found and fixed WHILE building this fix**: the first pagination attempt
for `train_classes_db.get_changes_since()` used a single SQL query mixing a fixed
`since_timestamp` with a moving `id` cursor via `OR` - this looked reasonable but was
actually broken: once `updated_at > since_timestamp` alone already matches every row
(true for essentially all rows on a first-ever sync), the `OR`'s id-cursor clause became
completely ineffective, and every "page" returned the exact same first 300 rows forever
in an infinite loop. Caught immediately by testing with 2500 seeded rows sharing
identical timestamps (deliberately chosen to mirror a real bulk catalog import, where
`other_hud_sync.py` sets `updated_at=now()` for potentially thousands of rows in one
pass) - the test asserted no duplicate ids and failed on the very first duplicate.
Fixed by switching to the same safe two-step pattern already used (correctly) for
journeys: collect the cheap SET of matching ids first using the fixed `since_timestamp`,
sort it, then slice with a simple `id > after_id` cursor - avoiding the flawed
single-query OR approach entirely.

Final validation: seeded 700 train classes + 250 journeys (deliberately larger than
likely caused the original real-world failure) and confirmed via a real Playwright
browser test that a fresh sync completes fully and correctly - `pendingCount` reaches 0,
`syncing` becomes `false`, and IndexedDB ends up with the exact expected counts (700 and
250) - in about 0.3 seconds, comfortably within the timeout with room to spare even as
the real dataset grows further.

### Bug 2: tablet edit timestamps used UTC, server timestamps used local time
`TSWOfflineDB.queueChange()` generated edit timestamps via `new Date().toISOString()`
(always UTC), while the server's `datetime.now().isoformat()` uses naive LOCAL time.
Since the UK is UTC+1 during BST, a tablet edit could appear up to an hour "older" than
it really was relative to the server's own timestamps, silently skewing the
last-write-wins comparison. Fixed by having the client generate timestamps in local wall-
clock time in the same shape the server uses, instead of UTC - correct for this app's
actual real-world setup (one user, both devices physically in the same place/timezone,
which the whole last-write-wins design already assumed).




Solves the manual copy-a-file-then-run-a-script workflow from v3.0.0. New module
`other_hud_sync.py`, started as a daemon thread in `app.py`'s `main()` (same
pattern as the weather sync thread) - checks on startup, then every ~15 minutes
for as long as the app runs, so it also picks up new data if the other app is
left running alongside this one.

- **Auto-detects the other app's database** by scanning common folders (Downloads,
  Desktop, OneDrive variants, Documents, `%LOCALAPPDATA%`, `%APPDATA%`) for a
  `resources/db/tsw_hud.db` match, bounded by a time budget so a huge Downloads
  folder can't hang startup. Falls back to a manual path field in Settings (new
  panel on the Setup page) if auto-detect can't find it, or found the wrong one -
  never a hard failure, matches the existing TSW-install-folder pattern.
- **Only re-imports when something's actually changed** - compares the source
  `timetables` row count against what was seen last time, skips the expensive full
  import entirely if nothing's different.
- **Copies new/changed images too** (skips files with the same size+mtime already
  present), guessing the images live at `resources/images/train_classes/` - a
  sibling of the `db/` folder, since `thumbnail_path` values start with
  `/images/train_classes/`.
- **New Settings panel** on the Setup page (`index.html`): shows current sync
  status (auto-detected vs manual, last synced when), a manual override path
  field, and a "Sync now" button to trigger an immediate pass rather than waiting
  for the next 15-minute cycle.
- **New API routes**: `GET /api/other_hud_sync/status`, `POST
  /api/other_hud_sync/config` (manual override), `POST
  /api/other_hud_sync/run_now` (immediate one-off sync, blocking).
- Full regression check performed before packaging (compile check on every `.py`
  file, JS syntax check on every page including the new Settings panel JS, every
  page/route loaded and confirmed 200, new API endpoints tested for real behavior
  including the graceful no-database-configured case, and the background thread
  verified to start and complete its first pass without error exactly as `main()`
  invokes it) - all passed.

**Still NOT confirmed against the user's real setup (both were "likely fine"
assumptions going into this build, not yet proven):**
1. **The WAL concurrent-read assumption.** Reading the other app's database
   read-only while that app is actively running and writing to it has not been
   tested for real - only the theory (SQLite WAL mode is designed for this) and
   the code path in isolation (against a static test file) have been checked. If
   this doesn't hold up in practice, `sync_once()` already fails safely (catches
   the exception, logs it, retries next cycle) rather than crashing the app - but
   it might mean sync just silently never succeeds while the other app is open.
2. **The `resources/images/train_classes/` guess for where image files live** -
   user was going to check this next time they were at their computer, hadn't
   confirmed yet as of this build. If wrong, image sync will just find nothing and
   silently do nothing (not break) - but won't actually copy pictures until the
   real path is confirmed and, if different, `IMAGES_SUBPATH` in
   `other_hud_sync.py` gets corrected.

**Next step:** user to actually run this against their real setup (both app open
and closed) and report back what happens - particularly whether auto-detect finds
the database at all, whether the WAL-concurrent-read assumption holds, and whether
the images guess was correct.

## SHIPPED in v3.2.0 + v5.2.0 - Tablet home-screen app icons + "Add to Home Screen" hub page

### What shipped
- Three PWA icon sets (`pages/icons/*-192.png`, `*-512.png`), rendered from the exact
  approved glyphs (speed-gauge for Dashboard, three text-rows for Timetables, loco
  silhouette for Train Classes), fixed purple (`#a06bf0`) regardless of active theme -
  confirmed with the user that dynamic icon recoloring isn't practical (Android reads a
  manifest icon once when added to the home screen, doesn't re-check it afterwards).
- Three manifest files (`pages/manifest-dashboard.json`, `-timetables.json`,
  `-train-classes.json`), each with its own `start_url`/`scope` so they install as three
  separate home-screen apps, not one. Dashboard's manifest points at
  `dashboard_tablet.html` specifically (not the desktop version) since this was built for
  the user's tablet; `display: standalone` on all three so they open with no browser
  address bar once added.
- `<link rel="manifest">` + theme-color meta + `pages/install-prompt.js` wired into
  `dashboard_tablet.html`, `timetables_browser.html`, and `train_classes.html`.
  `install-prompt.js` shows a real one-tap Install button when the browser's automatic
  `beforeinstallprompt` fires, and falls back to plain "tap ⋮ → Add to Home screen"
  instructions after 2.5s if it doesn't - **note: without a Service Worker (which this
  app doesn't have yet - that's part of the still-unbuilt offline sync piece below),
  not every Android/Chrome version reliably fires the automatic prompt, hence the
  fallback rather than assuming it always works.**
- New hub page `pages/install.html` ("Add to Home Screen" in every page's nav) - shows
  all three as tappable cards; tapping one navigates to that real page, where the actual
  install prompt/instructions live (a single hub page can't remotely trigger installing a
  *different* page's manifest - that's a real web-platform limitation, not a shortcut we
  chose not to take).
- Nav bar updated on all pages sharing the topbar to link to the new hub page.
- Train Classes was confirmed NOT to need a separate "static placeholder" page - the real
  page (shipped in v3.0.0) already handles "no data imported yet" with a clean empty
  state, so adding it to the home screen now, before running a sync, already works as
  intended.

### Testing performed before packaging
Full regression check: Python compile check on every `.py` file, JSON validity check on
all three new manifest files, JS syntax check on every inline and external script
(including the new `install-prompt.js` and `install.html`), every page/route/asset loaded
including the new manifests/icons/install page (all 200), and a real Playwright render of
the install hub confirming all three icon images actually loaded (not just that the page
returned 200). One real bug caught and fixed during this build: the nav-link insertion
script initially also matched and broke a sentence inside a prose paragraph on the Setup
page (a `Train Classes` link used in body text, not just the nav) - caught by grepping for
duplicate occurrences before packaging, not just trusting the script ran once cleanly.

### v5.2.0 update - real-device testing found two real problems, both fixed
User tested on the actual tablet (in Chrome, after learning DuckDuckGo doesn't support
proper PWA install - see below) and reported the icons looked "a little small."

- **Real bug found while investigating: icon corners were solid white (255,255,255), not
  transparent.** The icon-generation script rendered a rounded-rect div without an
  explicit transparent page background, so Playwright's screenshot filled the corners
  with the browser's default white rather than nothing - would have looked wrong as a
  white square-ish blob behind the rounded shape on any platform that doesn't itself
  crop to the rounded corners. Fixed by setting the page background to transparent and
  using `omit_background=True` on the screenshot capture; corner pixels confirmed now
  properly `(0,0,0,0)` (RGBA transparent) for all icons.
- **The real cause of "too small": no maskable icon variant existed.** Confirmed via
  research - Android wraps a non-maskable PWA icon in an extra white circle and shrinks
  it to fit, rather than letting it fill the adaptive-icon shape. Added a proper
  `-maskable` variant for all icons (full-bleed opaque square, no rounded corners, glyph
  sized to comfortably fit the standard 80%-safe-zone), registered as a separate
  `"purpose": "maskable"` manifest entry alongside the existing `"purpose": "any"` one
  (kept as two separate files/entries rather than combined `"any maskable"`, which is
  explicitly discouraged and renders inconsistently). Glyph size also increased for both
  variants (any: 280px -> 300px; maskable: up to 340px within its larger safe canvas) for
  a further, more direct sizing improvement on top of the maskable fix itself.
- **New 4th icon**: `pages/timetable.html` (the old colour-theme placeholder page, user
  confirmed this is NOT the real Timetables page - it's earmarked for a different, not-
  yet-built part of the timetable feature) now has its own manifest + icon set too,
  following the same any+maskable pattern as the other three. A clock-face glyph,
  deliberately different from the document-with-lines icon already used for the real
  Timetables page, so the two don't look identical on the home screen. Approved via a
  preview showing all 4 icons together before any code was added, per the established
  preview-first workflow. Install hub page (`install.html`) updated with a 4th card.
- **Also confirmed via real testing + research: DuckDuckGo's Android browser doesn't
  support proper PWA installation** - "Add to Home Screen" there produces a plain website
  shortcut (full browser chrome, no standalone mode), not a true install. Chrome (or
  other Chromium-based browsers - Brave, Edge) is required. Not a bug in this app.

Full regression re-run after these changes: compile check, JSON validity on all 4
manifests, JS syntax on every page, every page/route/asset loaded (200) including all 16
icon files (4 apps x 2 sizes x 2 purposes), and a real Playwright render of the install
hub confirming all 4 cards now show with correctly loaded images.

### Confirmed on the real tablet after this update
User re-tested on the actual Tab A11 in Chrome. **Icon sizing fix confirmed working for
real** - no longer reported as too small. **Still shows the Chrome badge and doesn't open
full-screen** - confirmed this is exactly the already-documented HTTPS/Service-Worker
dependency, not a new problem: since `HTTPS_SETUP.md` hasn't been completed yet, Chrome
still can't register a Service Worker, so it still can't meet the criteria for a true
full install and keeps falling back to a basic shortcut. No code fix applies here - this
resolves itself once HTTPS is set up, which was already the one piece of this whole
session that could only be verified at the user's computer. Re-adding the icons should
NOT be necessary once HTTPS is working - only the install method itself needs to change
(Chrome should offer a proper "Install" prompt once the Service Worker can register).

### Real-world finding, confirmed via testing on the actual tablet
**"Add to Home Screen" needs Chrome (or another Chromium-based browser like Brave/Edge) -
it does NOT work properly in DuckDuckGo's Android browser.** Confirmed via a real report
of the same issue: DuckDuckGo's "Add to Home Screen" produces a plain website shortcut
rather than a properly installed standalone app, and doesn't fire the automatic
`beforeinstallprompt` event `install-prompt.js` listens for - it simply lacks the same PWA
install machinery Chromium-based browsers implement. Not a bug in this app; a genuine
browser limitation. Worth mentioning to the user again if this comes up on a different
device in future.

## SHIPPED in v4.0.0 + v5.2.1 - Offline data on the tablet with two-way sync + HTTPS support

Full build of the design approved earlier (`design_previews/preview_tablet_icons_sync_ui.html`'s
sync-banner half - the home-screen-icons half shipped separately in v3.2.0). User's tablet
is a Samsung Galaxy Tab A11 (8.7", 800x1340), always on and connected to the same Wi-Fi as
the PC, with a fixed/reserved LAN IP already set up in the router.

### Important finding that changed the plan, confirmed via real research (not assumed)
Service Workers - which offline "cold launch with zero connection" requires - only work
over a secure context (HTTPS), with a single exception: `localhost`/`127.0.0.1` on the
SAME device. A tablet reaching the PC over its real LAN IP (e.g. `192.168.1.50`) does NOT
qualify, confirmed via a real bug report of someone hitting exactly this scenario. Plain
HTTP would make Service Worker registration fail outright on the tablet, even though it'd
appear to work fine if only ever tested from the PC itself. **User chose to add HTTPS**
(via a self-signed certificate from `mkcert`) rather than settle for the weaker
IndexedDB-only-no-cold-launch fallback option.

### HTTPS support (code-ready, certificate itself not yet generated - see below)
- `app.py`: `get_ssl_context()` checks for `certs/cert.pem` + `certs/key.pem`; if both
  exist, `run_flask()` automatically serves over HTTPS with zero further config; if not,
  falls back to plain HTTP exactly as before - confirmed via test that nothing breaks in
  the meantime. The moment a real certificate is dropped into `certs/`, HTTPS just starts
  working on next launch.
- `HTTPS_SETUP.md` (new file, project root) - full step-by-step walkthrough for the user to
  follow at their own computer: installing mkcert via Chocolatey, generating a certificate
  for the PC's actual LAN IP directly into `certs/cert.pem`/`certs/key.pem` (using mkcert's
  `-cert-file`/`-key-file` flags so no renaming is needed), and trusting the certificate
  authority on the Android tablet (Settings -> Security -> Install a certificate -> CA
  certificate). Confirmed real, current mkcert commands via research rather than relying on
  possibly-stale memory. Noted honestly: this is a ~30-45 minute one-time setup (per
  device/router config), with the certificate itself needing regeneration after ~2 years,
  and Android will show a persistent "Network may be monitored" notice for any manually
  trusted CA - expected and correct, not a sign of a problem.
- **User still needs to actually run through `HTTPS_SETUP.md` at their computer** - the
  code is ready and waiting, but no real certificate exists yet, so the app is still
  running over plain HTTP as of this build. Once they do, HTTPS (and therefore the
  Service-Worker-backed cold-launch-while-offline capability) activates with no further
  code changes.

### Data layer: `updated_at` tracking + last-write-wins
- `timetable_db.py` / `train_classes_db.py`: added `updated_at` to every editable table
  (journeys, journey_segments, journey_stops, train_classes), set on every insert and edit.
- `update_journey`/`update_segment`/`update_stop`/`update_train_class` all gained an
  optional `client_updated_at` parameter: `None` (the normal case - a local edit made
  directly in this app) always applies and sets `updated_at` to right now, no comparison
  needed. A real timestamp (an edit pushed from the tablet, made whenever the person
  actually tapped save there) triggers last-write-wins: only applies if newer than the
  row's current `updated_at`, and preserves the client's original edit time rather than
  the time it happened to sync - confirmed behaving correctly via direct tests (stale
  synced edits correctly rejected, newer ones correctly applied and preserved).
- New `get_changes_since(timestamp)` on both modules, for the pull side of sync.

### New API endpoints
- `GET /api/sync/changes?since=<timestamp>` - returns every journey/train-class changed
  since that point, plus the server's own current time to use as the next baseline
  (deliberately NOT the caller's own clock, since the two devices' clocks may not agree).
- `POST /api/sync/push` - applies a batch of edits from another device, each with its own
  `updated_at`; still filtered through the exact same `EDITABLE_FIELDS` allow-lists as any
  other update, so a pushed edit can no more touch a protected column than a local one can.
  Returns which edits were applied vs rejected as stale, per item.

### Offline data layer (tablet-side JS, works over plain HTTP - no secure-context
requirement, unlike Service Worker)
- `pages/offline-db.js` - IndexedDB wrapper: `journeys` and `train_classes` object stores
  (each journey document embeds its own stops/segments, matching the API response shape),
  a `pending_changes` queue for edits made offline, and `applyOptimisticEdit()` which
  writes an edit into the local cache immediately - including the fiddly case of a stop or
  segment edit, which has to be found by scanning cached journeys for the one containing
  that nested id (documented as worth indexing separately if this ever grows to caching
  the whole game's data, not just what's relevant to routes actually played).
- `pages/sync-client.js` (`TSWSync`) - orchestrates pull-then-push, exposes
  `getStatus()`/`onStatusChange()`/`queueEdit()`/`sync()` for pages to build against.
  Auto-syncs on load and every 30s while a page stays open.
- `pages/sw.js` - Service Worker caching the app shell (pages/CSS/JS/icons/manifests, NOT
  `/api/` routes - those go through the IndexedDB layer instead so they can be properly
  merged, not just served stale). Registered from pages that opt in; harmless no-op if
  registration fails (expected over plain HTTP - see above).

### Real bug found and fixed during testing (not just assumed correct)
`queueEdit()` fires `sync()` without awaiting it, and the original `sync()` silently
dropped any call that arrived while one was already running, with no follow-up scheduled.
Firing several edits in quick succession (e.g. saving a journey's name plus multiple stops
at once) meant some of them could get queued AFTER the in-flight sync's snapshot was taken,
then sit stranded until the next 30-second timer rather than syncing immediately. Caught by
actually testing a real multi-edit save and watching the pending count fail to reach zero,
not by assuming the code was correct. Fixed with a "rerun requested" flag: any sync() call
arriving mid-flight schedules exactly one follow-up run right after the current one
finishes. Re-verified against the original scenario AND a harder stress test (5 fully
concurrent, unsequenced edits fired via `Promise.all`) - both settle to 0 pending
immediately and every edit lands correctly.

### UI changes
`pages/timetables_browser.html` and `pages/train_classes.html`: added the sync status
banner (green dot "Synced HH:MM:SS" / amber dot "Offline - showing cached data from
<time>" + a pending-count chip), matching the approved preview design. `load()` on both
pages now falls back to querying IndexedDB directly (with equivalent client-side
search/filter logic) when the live API is unreachable. Saving an edit on either page now
always goes through `TSWSync.queueEdit()` rather than a direct PATCH - applies locally
immediately (instant-feeling UI, works identically whether online or offline) and attempts
a live sync right away if reachable, falling back to the queue otherwise. Both pages
register the Service Worker (silently no-ops if it can't, e.g. no HTTPS yet).

### Testing performed
Full regression check (compile, JSON validity, JS syntax on every page/script including
all 3 new sync files, every page/route/asset loaded). Beyond that, extensive real-browser
(Playwright) testing of the sync logic itself, not just page loads: pull correctly
populates IndexedDB; a queued edit applies optimistically and instantly, including the
nested-stop case; going offline (all `/api/` calls blocked) correctly flips status to
offline while keeping cached data fully readable; reconnecting correctly flushes the queue
and the edit is confirmed present server-side afterward; the race-condition fix confirmed
via both the original failure scenario and a harder concurrent-edit stress test.

### Certificate expiry notification
`get_https_cert_status()` in `app.py` reads `certs/cert.pem` (via the `cryptography`
library, added as a new dependency in `requirements.txt`) and reports days remaining
until expiry. New `GET /api/https_cert_status` endpoint; new panel on the Setup page
(`index.html`) showing one of four states (no cert / valid / expiring within 30 days /
expired), colour-coded, prompting the user to "ask Claude for help" when relevant.

**Important limitation, surfaced honestly in the UI itself, not just here:** once a
certificate is ACTUALLY expired, browsers refuse the TLS handshake outright and show
their own native warning page before this app's HTML/JS ever loads - so this can only
ever function as a heads-up *before* expiry (the "expiring soon" state, within 30 days),
not a reliable after-the-fact alert. The "expired" state is still implemented and tested
for completeness (e.g. useful if checked from a device that's still on the old cached
page, or from the PC itself if only the tablet-facing cert broke), but it's not the
primary value of this feature - the point is to catch it BEFORE it breaks anything.

Tested against four real certificates generated on the fly with known expiry dates (not
guessed at) - 400 days out (fine), 14 days out (expiring soon), already expired, and no
certificate present at all - all four confirmed rendering distinct, correct messages via
real Playwright browser tests, not just unit-testing the backend function in isolation.


1. **The real HTTPS certificate hasn't been generated yet** - user needs to work through
   `HTTPS_SETUP.md` at their computer. Everything above was tested over plain HTTP in this
   session; the HTTPS code path itself (and therefore genuine Service-Worker-backed
   cold-launch-while-offline) is unverified against a real certificate + real device.
2. **Never tested on the actual Tab A11** - all testing this session used a desktop headless
   browser simulating requests/offline states, not the real tablet hardware/Chrome for
   Android/real Wi-Fi conditions.
3. The WAL-concurrent-read assumption from `other_hud_sync.py` (v3.1.0) remains separately
   unverified against the user's real setup too - unrelated feature, same "not yet proven
   on real hardware" caveat applies.

## SHIPPED in v4.1.0 - Live-detected locos now feed the new Train Classes catalog too

Closes a real gap: `pages/train_classes.html`/`train_classes_db.py` (the new catalog,
shipped v3.0.0) previously only ever got new entries from a batch import against the
other app's own database - so a just-released, brand new loco (which that other app
hasn't catalogued yet, since it needs its own manual extraction run first) wouldn't show
up until someone happened to run that. Meanwhile the OLD system (`loco_profiles.py` /
Customisation page's "Known Trains" panel) has always auto-added anything the live game
API detects, immediately. User wanted that same immediacy for the new catalog too.

- `find_loco_class()` in `app.py` now calls BOTH `loco_profiles.record_sighting()` (old,
  unchanged) AND the new `train_classes_db.record_live_sighting()` every time a loco is
  detected live. The two systems remain genuinely separate databases/pages (not merged
  into one) - this just makes sure both get fed from the same live detection event.
- `train_classes_db.record_live_sighting(raw_object_class, clean_name, formation_max_speed_ms)`
  - brand new loco: creates a row immediately with `source_id=NULL` (there's no "other
    app" id for something it hasn't catalogued yet) and **`is_visible=1` right away** -
    a deliberate difference from a normal catalog import (which defaults non-UK entries
    to hidden) - the whole point here is "let me see the new thing I just bought", so
    there's no sensible reason to hide it by default.
  - already-known loco (whether from a prior live sighting OR a full catalog import):
    just bumps a new `times_seen` counter (added to the schema, mirroring the old
    system's stat), no duplicate row.
- **Reconciliation, the important part:** `import_train_class()` (the batch catalog
  import) now also checks for an existing `source_id IS NULL` placeholder row matching by
  name before falling back to a plain insert - if the other app's catalog later catches
  up with a loco that was already live-detected, that existing row gets adopted (real
  `source_id`, thumbnail, livery, manufacturer, speeds all filled in) rather than creating
  a second, duplicate row for the same loco. Confirmed via a full real test: live-detect a
  new loco -> re-sight it (times_seen increments, no duplicate) -> user edits its display
  name -> catalog import later finds the same loco by name -> reconciles into the SAME
  row (thumbnail/livery/is_uk now populated, source_id now set) while preserving the
  user's custom display name AND the accumulated times_seen stat. Also regression-tested
  the original by-source_id re-import path to confirm the refactor didn't break it.



- Loco class detection tries `IS_GetVehicleInfo` (clean name) -> DB-remembered
  clean name -> `ObjectClass` raw fallback -> node-name regex scan, in that order.
- Weather sync fixed to stop attempting undocumented `...Overridden` field writes
  (official DTG docs confirm these are read-only, set by the game automatically).
- The `WEATHER_STATE` background-thread pattern in `app.py` is the reference
  example for any future long-running task needing live UI feedback.
- v3.0.0: `update_journey`/`update_segment`/`update_stop` functions in
  `timetable_db.py` were initially missing `return` statements, silently making
  every PATCH API call report failure even when the underlying SQL succeeded -
  caught via Flask test-client testing before shipping, fixed. Reminder to always
  test the actual return path of DB helper functions, not just that the SQL runs.


## SHIPPED in v7.37.0 - Spec-drift cleanup

Housekeeping pass, no new features. Brings the tree back in line with the spec.

**Known Trains v2 is now genuinely driven-only (spec section 3C).** This was
specified but had never actually been implemented - the filter was on
`variant_of_class_id IS NULL` and `is_visible`, never on `times_seen`, so a
catalog import could surface trains that had never been driven.
`list_train_classes()` gained a `driven_only` argument and `needs_attention()`
the same; `/api/known_trains/list` passes True for both. The two filters must
agree - a needs-attention entry with no corresponding visible row would be an
item the person cannot go and fix. Everything else that calls
`list_train_classes()` (the group-members list on the Classes page) leaves it
False and still sees the whole catalog.

**Removed everything spec section 3 excludes.** These were all shipped in the
v3.2.0-v4.1.0 era, before the decision to drop PWA/offline/HTTPS, and had
simply never been taken back out:
  - `pages/sw.js` (+ its dynamic Flask route), `offline-db.js`,
    `sync-client.js`, `pages/icons/` (16 PNGs)
  - `/api/sync/changes` and `/api/sync/push`
  - `certs/`, `HTTPS_SETUP.md`, `/api/https_cert_status`, `get_ssl_context()`,
    `get_https_cert_status()`; `run_flask()` is now plain HTTP unconditionally
  - `cryptography` dropped from `requirements.txt` (only the cert reader used it)

`timetables_browser.html` was the one page still wired to the sync layer - it
now saves via direct `PATCH` to `/api/timetables/<id>` and
`/api/timetables/stops/<id>`, as the spec says it should. Its offline banner,
IndexedDB fallbacks and Service Worker registration are gone. `timetable.html`
lost its stale `install-prompt.js` tag (that file itself was already absent, so
the tag was a guaranteed 404 on every page load).

Note: `get_changes_since()` in `timetable_db.py` and `train_classes_db.py` is
kept. Nothing calls it now, but it carries the hard-won keyset-pagination fix
(collect ids, sort, slice - never a `since OR id >` clause) and costs nothing
to leave in place.

**Packaging.** `data/*.db` and `diagnostics/*.log` are no longer in the zip.
The databases shipped empty, so extracting over a real install would have
replaced live Known Trains data with nothing.

**Docs.** `TSW_HUD_NEW_CHAT_SPEC.txt` had drifted a long way - it still
described v6.1.3, still listed `train_classes.html` and `/api/train_classes`
(both removed at some point since), and knew nothing about families, operators
and liveries, variants or the analogue speedometer. Updated, and the regression
checklist's page/route list along with it.


## SHIPPED in v7.38.0 - StopPoint identification

`find_stop_points()` in `pak_tools.py`, `/api/paks/stops`, and a **Find stop
points** button on Discovery. Separates real station calls from the simulated
running times around them by resolving FName references against the name table
in the sibling `.uasset`.

Validated against synthetic records of known layout (`tests/`), NOT yet against
a real pak. Full write-up, including eight failed approaches and three fixture
bugs that each produced a misleading failure, is in
`docs/TIMETABLE_EXTRACTION_FINDINGS.md`. Read that before touching the scoring.


## SHIPPED in v7.42.0 - map zoom and livery-coloured rail overlay

Default map zoom 15 -> 17 (two notches closer).

The rail overlay now takes the colour of the train being driven, as does the
player dot. `/api/loco` gained `livery_colour`, resolved by a new shared
`train_classes_db.resolve_livery_colour()` - specific livery colour first,
operator colour as fallback. That logic previously lived only inside the
known_trains list endpoint; sharing it stops the map and the pills drifting
apart. A variant takes its PARENT's colour, matching how it takes the
parent's name.

**Tinting method, and why not the obvious one.** OpenRailwayMap serves
pre-rendered raster tiles, so the line colour cannot be set the way a vector
layer's could. A CSS `hue-rotate` was tried first and is wrong: it is a
linear matrix approximation, not a true hue rotation, and it drifts badly on
bright colours - tested against synthetic tiles, it turned blue into cyan and
red into yellow. The shipped version uses an SVG filter, `feFlood` painted
through `SourceAlpha`, which replaces every non-transparent pixel with an
EXACT colour while preserving anti-aliasing.

`tintColour()` floors lightness at 50% while keeping hue, because these are
dark thin lines on a dark basemap - ScotRail navy #1e3f8f tinted straight is
nearly invisible, and comes out #2c5dd3. Bright liveries pass through
untouched. A small drop-shadow glow lifts the lines off the terrain.

`applyLiveryColour()` is called on livery change AND after `initMap()`.
Neither the tile layer nor the marker exists before the map is built, so an
earlier version left a stale flood colour whenever the overlay was off and
never coloured the dot at all if the first livery poll landed before the map.

NOT verified in a browser here: Leaflet is loaded from unpkg, which the dev
sandbox cannot reach, so the map itself never initialises there. The tint
filter, the lightness floor and the colour resolution were all tested
directly; the map's own rendering at zoom 17 was not.
