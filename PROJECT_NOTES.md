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

## Current version: 7.61.0

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


## SHIPPED in v7.43.0 - rail overlay follows the infrastructure style, minus historic lines

The overlay is no longer a plain tinted raster layer. `TintedRailLayer`
(L.GridLayer subclass, pages/map.html) composites each tile on a canvas:

  1. **OpenRailwayMap "standard" (infrastructure)** supplies every visible
     pixel - its line weights, its hollow tunnel casings, its thinner
     sidings. That artwork is the reason for using this style.
  2. **OpenRailwayMap "gauge" is used as a STENCIL only.** None of its
     colours are ever shown. It renders only track carrying gauge tagging,
     which in practice omits disused/abandoned/razed alignments, so
     `destination-in` against it drops the historic lines while leaving
     everything else untouched.
  3. **`source-in` fill** paints the livery colour through whatever alpha
     survives.

Every step is alpha-preserving, deliberately: line thickness and tunnel
translucency live entirely in the alpha channel, and flattening it would
reduce the overlay to uniform spaghetti - which is exactly what the previous
feFlood-on-the-whole-layer approach did.

**Measured on synthetic tiles** (real OpenRailwayMap is unreachable from the
dev sandbox):
  - historic alignment removed: **100%** (770 px -> 0)
  - live lines retained: **99.2%** (3015 px -> 2992)
  - line widths unchanged: 6 px running line, 3 px siding, before and after
  - tunnel/running-line alpha ratio: **0.35 before, 0.35 after** the stencil
    and the tint

**Gotchas encoded in the code:**
  - The stencil is DILATED (radius 2, diamond) before use. Gauge draws
    thinner than infrastructure, so masking straight would shave the casings
    off every line.
  - Dilation happens on a scratch canvas with `source-over`. Applying the
    offsets directly to the tile with `destination-in` multiplies alpha
    repeatedly and ERODES the lines instead of growing them.
  - If the stencil tile fails to load, the tile is drawn UNMASKED. Masking
    against a missing stencil would erase the tile completely and read as a
    broken overlay rather than a missing filter.
  - Re-tinting reuses the images already held on each tile canvas
    (`canvas._base` / `canvas._mask`). `redraw()` would refetch every tile
    from a free tile service on every livery change.

`makeRailLayer()` builds the class inside a function rather than at top
level. `L.GridLayer.extend` needs Leaflet loaded, and at top level one failed
CDN fetch threw before the rest of the script ran - the page then lost its
livery polling entirely and showed an uncoloured map with no obvious cause.
Caught by testing in a sandbox where the CDN is blocked, which turned out to
be a useful accident.

A **Historic Lines: Hidden/Shown** button sits next to the rail toggle, so
the stencil can be turned off in the field if gauge coverage turns out to be
patchy on a given route.

UNVERIFIED: openrailwaymap.org is not reachable from the dev sandbox, so the
`gauge` tile path and the assumption that gauge omits historic lines are both
from documentation, not from a real tile. If gauge tiles 404 the overlay
silently falls back to unmasked (still correct, just unfiltered).


## SHIPPED in v7.44.0 - connection stability

Symptoms: train class dropping out mid-journey, map stalling then jumping,
weather readouts flickering to em dashes. None of these were the game losing
data. They were self-inflicted, and two of the causes were diagnostics rather
than anything to do with TSW.

### Root causes found

**1. The call log was rewriting a file on every single API call.** `log_call`
read the last 300 lines off disk and rewrote the whole file, holding a global
lock. At this app's poll rates - two 300ms loops plus identity, map and
weather, roughly ten calls a second - that is ten full read-modify-writes per
second, serialised, and on Windows with a virus scanner each can stall for
tens of milliseconds. Now buffered in memory (`deque(maxlen=300)`) and
flushed by a background thread every 3s.

**2. The API key was re-read from disk on every call.** `read_api_key()` ran
`find_key_file()` - which stats several candidate folders - then opened and
read the file, for a value that changes approximately never. Now cached, with
an mtime recheck every 10s so editing the key still works without a restart.

**3. Overlapping requests to a server that cannot take them.** TSW's HTTP
server does not handle concurrency; `DriverAid.PlayerInfo` returning 502
under rapid polling is a DROPPED CONNECTION, not a missing path, and that was
already written down in the timetable findings. With dashboard, map and
weather polling independently there were routinely three or four requests in
flight. All upstream calls now go through `_upstream_lock`.

**4. No retry.** A transient 502/connection error propagated straight to the
UI. Now retried once with a short backoff.

**5. `/api/loco` fetched the identity TWICE** - `find_loco_class()` and
`get_current_raw_object_class()` each did their own three upstream reads. Six
calls every 2 seconds for one answer; now three.

**6. Sightings were written to SQLite twice every 2 seconds** for a train
that had not changed. Now on change, or every 30s.

### Holding last-known-good instead of blanking

A dropped poll is not new information. Blanking on one made a connection
hiccup look like a data problem:

  - identity is held for 20s (`IDENTITY_HOLD_SECONDS`) - this was the
    "loses the train class" symptom
  - player location is held for 30s - a train cannot teleport, so a position
    a second old beats no position, and the map now has something to
    interpolate towards instead of stalling and jumping
  - client-side `setReadout()` keeps the previous value and marks it `.stale`
    (dimmed) after 15s rather than replacing it with an em dash
  - "Missing Train Class" now only appears if a class has never been seen,
    not after a single failed poll

Identical reads within 200ms are also coalesced (`READ_CACHE_SECONDS`), so
opening a second page no longer multiplies the load on the game.

### Measured, not asserted

`tests/mock_tsw_api.py` runs a mock TSW API that reproduces the real failure
modes - 502 on concurrent requests, random drops, realistic service time -
and drives the same poll pattern the app really uses. `--compare` runs the
old and new behaviour against the SAME mock in one process:

```
concurrent rejects: 36 -> 0
location dropouts:   1 -> 0      (at a 35% random drop rate)
```

Compared in-process on purpose: a value flickering out for one poll is
exactly the kind of symptom it is easy to convince yourself has improved.


## SHIPPED in v7.45.0 - subscription API, with a verified fallback

`tsw_subscriptions.py`. Registers the hot paths once
(`POST /subscription/<path>?Subscription=1`) then reads them all back in a
single `GET /subscription/`, replacing N polls per tick with one.

Measured against the mock, 10 seconds of the app's real poll pattern:

```
subscriptions active : 37 upstream requests
polling (fallback)   : 86 upstream requests
40 proxy reads while active cost 0 upstream requests
```

### The protocol is NOT confirmed, and the code is built accordingly

TIMETABLE_EXTRACTION_FINDINGS.md records the endpoint shape and marks it
"not yet implemented". Nobody has run it against a real game, and the shape
of the aggregate response in particular is a guess. So the client earns its
place rather than assuming it:

  - it subscribes, then VERIFIES that a path it registered actually comes
    back through the aggregate read before any value is trusted;
  - `_index()` accepts several plausible response shapes, but a wrong guess
    cannot leak wrong data to the HUD - it shows up as a failed verification,
    because the indexed result must contain a path that was actually
    subscribed;
  - three outcomes, all tested: **active**, **unsupported** (endpoint 404s),
    **unknown_shape** (endpoint works, payload unrecognisable);
  - in the latter two it stands down and the polling path - which is known to
    work - carries on untouched, re-probing every 5 minutes;
  - on `unknown_shape` it CAPTURES a sample of the raw payload, exposed at
    `/api/subscriptions`, so the real format can be read off a real run
    instead of guessed at a second time.

`SubscriptionClient.get()` returns None rather than an error on a miss, so a
subscription miss is indistinguishable from the layer not existing.
Subscription traffic goes through the same session, upstream lock and logging
as everything else - it does not open its own connections.

`/api/subscriptions` reports state, detail, subscribed paths, cache hit
counts and the payload sample.

### Test

`tests/mock_tsw_api.py --subscriptions` runs the matrix:

```
supported  state=active         40 reads cost   0 upstream   loco=158
absent     state=unsupported    40 reads cost   2 upstream   loco=158
odd_shape  state=unknown_shape  40 reads cost   2 upstream   loco=158
```

The fallback rows matter more than the happy one. An unverified protocol must
never be able to break a working app, and the test asserts the train still
resolves in all three worlds.

### FIRST THING TO CHECK ON A REAL RUN

Open `/api/subscriptions` while driving. If `state` is `active`, this is
working and the polling load has dropped. If it is `unknown_shape`, send back
`unrecognised_payload_sample` - that is the real response shape and
`_index()` can then be taught it. If `unsupported`, this build of TSW does
not have the endpoint and polling remains the answer.


## FIXED in v7.45.1 - rail overlay was washed out and lost tunnels

Two separate faults, reported against a real screenshot next to
openrailwaymap.org's own rendering.

### Tunnels disappeared - a wrong assumption in v7.43.0

v7.43.0 claimed "line thickness and tunnel translucency live entirely in the
alpha channel" and recoloured with a flat `source-in` fill. **The
translucency half of that was wrong.** OpenRailwayMap does not draw a tunnel
at lower opacity - it draws it in a PALER SHADE of the same colour. Replacing
every RGB value with one flat colour threw that away, which is why a tunnel
came out identical to open track.

Now recoloured with the `'color'` blend mode, which takes hue and saturation
from the fill and keeps the LUMINOSITY of the artwork. Tunnel casings stay
pale, dark casings stay dark, and every other shade ORM uses to mean
something survives. A blend still composites source-over, so the fill also
covers the transparent parts of the tile; the tile is re-clipped to the
artwork's own alpha immediately afterwards.

Near-black casings then vanished against our dark basemap - fine on ORM's
white one, useless on ours - so a `'lighten'` pass to `LUMINANCE_FLOOR`
(#4d4d4d) runs first. `lighten` takes the per-channel maximum, so it raises
the floor without touching anything brighter and leaves the tunnel/open-track
distinction intact.

### Washed out, soft edges

Three things compounding:
  - the gauge stencil was used raw, and an anti-aliased stencil line has
    partial-alpha edge pixels. `destination-in` MULTIPLIES alpha, so every
    line got a soft translucent border.
  - layer `opacity: 0.85`
  - a `drop-shadow` glow around every line

Fixed: `buildStencil()` hardens the stencil by redrawing it on itself six
times - alpha follows 1-(1-a)^n, so 0.5 becomes 0.998 - which sharpens the
edges without pixel access, so it still works on cross-origin tiles that
would taint the canvas. Opacity back to 1, glow removed (ORM's own rendering
has clean edges, and that is the target). Dilation reduced 2px -> 1px now
that hardening handles coverage.

### Measured against a tile that encodes tunnels the way ORM really does

```
partial-alpha edge pixels:  ORM 34.1%   ours 29.5%
line width at x=200:        ORM 14px    ours 14px
tunnel luminance 204 vs open track 147  -> distinguished
historic alignment                       -> still removed
```

Our overlay now has FEWER soft edge pixels than ORM's own rendering, and
identical line widths.


## v7.45.2 - overlay diagnostic, because two fixes in a row missed

The overlay is still washed out on a real screenshot after two rounds of
changes, both of which were reasoned from what the ORM tiles PROBABLY
contain rather than measured against them. openrailwaymap.org is not
reachable from the dev sandbox, so every test so far has been against
synthetic tiles built to match a screenshot - which validates the maths and
proves nothing about the real input.

Rather than guess a third time, **Overlay: full / no-stencil / raw** cycles
the pipeline on the map page. It re-composites the images ALREADY HELD on
each tile canvas, so all three modes are the same downloaded bytes processed
differently - any difference between them is caused by our code, and
anything common to all three is not.

Reading it:
  - **raw already soft/faded** -> nothing in `compositeRailTile` is at fault.
    The cause is upstream: tile scaling at this zoom, device pixel ratio, or
    the standard style genuinely looking like that at z17.
  - **raw crisp, no-stencil faded** -> the recolour (`lighten` + `color`
    blend + re-clip) is responsible.
  - **no-stencil crisp, full faded** -> the gauge stencil is responsible,
    most likely because gauge geometry does not align with standard geometry.

The stencil tile is now always fetched even when not applied, so switching
modes never triggers a refetch - otherwise a comparison would be comparing
two different downloads.


## FIXED in v7.46.0 - overlay washed out; the luminance floor was the cause

Three screenshots from the diagnostic modes settled it, after two fixes that
were reasoned rather than measured:

  - **raw was crisp** - clean solid lines, matching openrailwaymap.org. So
    the tiles, the zoom and the tile scaling were never the problem, and the
    "probably upstream resampling" guess was wrong too.
  - **full and no-stencil were near identical**, both washed out. That
    cleared the gauge stencil.

Which left the recolour, and specifically the `lighten` pass to
`LUMINANCE_FLOOR` added in v7.45.1. It floors EVERY pixel at 30% grey before
the `color` blend. ORM's lines are mid-tone, so this washed them out and
flattened the contrast between line and casing; the `color` blend then
faithfully preserved that flattened luminosity, so the output was pale
whatever colour went in. **A dark ScotRail navy arriving as light blue was
this, not the tint.**

It was also solving a problem that does not exist here: the basemap is the
LIGHT OSM style, where near-black casings read perfectly well. The floor was
protecting against a dark basemap that is not in use.

### Changes

  - `LUMINANCE_FLOOR` now `null`. Kept as a constant with a note, for the day
    someone runs this over a dark basemap.
  - `LIFT_DARK_LIVERIES` now `false`. `tintColour()`'s 50% lightness floor was
    the other half of the same mistake - it turned #1e467d into a mid blue
    before compositing even started. Over a light basemap a dark navy is more
    legible AND more correct.
  - **Lightness re-anchoring.** `color` alone keeps ORM's luminosity, so a
    running line came out at ORM's mid-tone in the right hue - #6d95cc for a
    #1e467d navy. Hue right, lightness wrong. The tile is now scaled by
    (livery luminance / `REFERENCE_LINE_LUM`), which puts an ordinary running
    line ON the livery colour while keeping every ORM shade relative to it.
    `multiply` with a grey darkens, `screen` lightens, so either direction
    works.
  - Fourth diagnostic mode `no-floor`, which skips both the floor and the
    lift, so this can be compared directly against `raw` in future.

### Measured against a tile matched to the user's raw screenshot

```
luminance error vs raw:   floor+lift 17%   ->   no floor 5%
ScotRail #1e467d requested:
  running line -> #344862 (lum 70, requested lum 63)
  tunnel       -> #54647a (lum 98)  LIGHTER than running - preserved
  casing       -> #021125 (lum 15)  DARKER  than running - preserved
red #e05a4e requested -> running line #cc594f
```

Known limitation: a dark, highly saturated livery comes out slightly muted
(#344862 against a requested #1e467d) because the `color` blend mixes hue
with the artwork's own colour. Mid-tone liveries land almost exactly (red
#e05a4e -> #cc594f). Making this exact would mean giving up the tunnel
shading, which is the thing that was asked for.


## FIXED in v7.47.0 - rail overlay, using REAL tiles for the first time

Five attempts at this failed because every one was validated against
synthetic tiles I invented to look like a screenshot. Two real tiles from the
user's machine overturned three assumptions immediately.

### 1. The tiles are 512px, not 256 - this was the soft edges

MEASURED: OpenRailwayMap serves **512x512** images for standard 256-unit tile
coordinates (2x/retina tiles). The canvas layer created 256px tiles and drew
the artwork in with `drawImage(base, 0, 0, 256, 256)`, downsampling it, after
which Leaflet scaled it back up for display.

**That resample down-then-up is what made every line soft and doubled-looking
in every version.** It had nothing to do with the colour maths, which is why
three rounds of colour fixes changed nothing. The canvas backing store is now
`TILE_PIXELS` (512) with a `TILE_CSS_PX` (256) footprint - 1:1 with the
artwork.

### 2. ORM tiles contain LABELS, and we were recolouring them into blobs

MEASURED on the real tile: `#0000ff` blue label text (6778 px) and `#ffffff`
halos (3016 px), against `#ff8100` running lines (2687 px) and `#ffcb97`
tunnel sections (2694 px). The labels are opaque pixels sitting on the track.

Any whole-tile recolour turned them into dark smears - and those smears are
exactly the patches that had been appearing along the tracks in every
screenshot.

`recolourPixels()` now classifies pixel by pixel. Track is the orange family
(`r > 120 && r >= g >= b && r - b > 40`); labels are blue, white or black and
have no such red-over-blue bias, so they separate cleanly and are dropped
entirely. The basemap already has its own labels.

Result on the real tile, livery `#1e467d`:

```
running line #ff8100  ->  #1e467d   EXACTLY the livery colour
tunnel       #ffcb97  ->  #2b64b3   lighter, as ORM draws it
label/halo pixels surviving: 0
```

### 3. The reference luminance was NOT the problem

MEASURED 146.5 against the invented 137 - a 7% difference, nowhere near
enough to explain the near-black overlay of v7.46.0. Worth recording,
because it means the near-black output came from the blend pipeline, not the
constant, and the constant should not be blamed if darkness returns.

### Fallbacks

  - `crossOrigin='anonymous'` is what makes pixels readable, but it also
    makes the image fail to load outright if the server sends no CORS
    headers. A failed load now retries WITHOUT it: the tile still draws, is
    merely unreadable, and `compositeRailTile` falls back to the blend path.
    Losing label-stripping beats losing the overlay.
  - `recolourPixels()` returns false on a tainted canvas, and the old
    blend-mode path is kept as mode `blend` for comparison.
  - Stencil dilation raised 1px -> 4px, at 512 resolution. MEASURED:
    infrastructure lines 12-13px, gauge 8-14px, so ~3px per side is needed.

### A warning worth keeping

A test combining tiles 40848 and 40846 - DIFFERENT coordinates - produced a
completely blank overlay, because the stencil did not overlap the artwork.
In the app both are fetched at the same coords so this cannot happen, but it
shows the failure mode: if gauge and standard ever disagree, the overlay
vanishes rather than degrading. That is what the `no-stencil` mode is for.


## FIXED in v7.48.0 - Known Trains Download did nothing, and was not a backup

Two faults. The dead button was the reported one; the second was worse.

### The button did nothing in the desktop app

It built a Blob in JavaScript and called `a.click()`. **pywebview has no
download handler**, so that silently does nothing - the button worked in a
browser and appeared dead in the app.

Now `/api/known_trains/backup` writes the file SERVER SIDE, into
`<app>/backups/`, and reports the exact path. A file the server wrote and can
name is verifiable; a browser download can be blocked, ignored by the
embedded webview, or land somewhere unfindable. It also reads the file back
and compares row counts before reporting success, rather than assuming the
write worked because nothing threw. A normal download is still offered via
`/api/known_trains/export` for when the page is open in a real browser.

### It was backing up the WRONG DATA

The old code saved `/api/known_trains/list` - the DRIVEN-ONLY, RESOLVED view.
Demonstrated on a seeded database: **the old backup captured 1 of 3 trains.**
It silently omitted every catalog row with `times_seen = 0`, every hidden
row, and all variants, subclasses, families, operators, liveries and aliases.
Restoring from it after a wipe would have destroyed most of the data while
looking like a successful backup.

`export_everything()` now dumps every table by walking `sqlite_master`, so a
table added later is included without anyone remembering. A copy of the
SQLite file itself is saved alongside the JSON - restoring by putting that
file back needs no import code to be correct, which makes it the most
reliable recovery available.

### Restore, and a bug the test caught

`import_everything()` restores a dump; merges by default, `?replace=1`
overwrites.

The first restore test brought back trains and operators but **zero
liveries**. Tables were restoring in alphabetical order, which puts
`operator_liveries` before `operators`, so every livery failed its foreign
key and was dropped - silently. Import now runs parents-first, and records
the reason for every skipped row. A restore that quietly drops rows is worse
than one that fails outright, because it looks like it worked.

Verified end to end: seed, back up, DELETE every table, restore, compare.
Row counts identical across all tables, 0 skipped, 0 errors. The button was
then clicked in a real browser and the two files confirmed on disk (6 KB JSON
+ 96 KB .db).


## SHIPPED in v7.59.0 - download fixed, timetable banked, drive recorder

### 1. Known Trains download - two real bugs

**The page navigated away.** After writing the backup, the code created an
anchor to `/api/known_trains/export` and clicked it. In pywebview that does
not download - the WEBVIEW ITSELF navigates to the JSON, replacing the page.
Removed. The result now appears IN the page with the file paths, plus an
**Open backups folder** button (`/api/known_trains/reveal_backups`), which is
what "download" actually means in a desktop app.

**Restore imported nothing and said it worked.** `uploadData` posted to
`/api/known_trains/restore`, which read only `body["classes"]` - but the
backup file produced by `/api/known_trains/backup` is `{"tables": {...}}`.
Restoring a real backup therefore did NOTHING while reporting success, which
is the worst possible failure for a restore. `/restore` now accepts both
shapes, routes full backups through `import_everything()` (parents before
children), refuses an unrecognised file with a 400, and reports the actual
row counts instead of "restored successfully".

### 2. The extracted timetable is banked to SQLite

`extracted_services` and `extracted_calls` in `timetables.db`. **Extract
timetable** saves automatically; `/api/timetable/extracted` reads it back.
The Leven extraction is 39 services and 485 calls.

Re-extraction REPLACES that asset's previous rows rather than merging: this
is derived data, and a better parser should supersede the old result, not
leave a mix of two parser versions with no way to tell them apart.
`station_name` exists but is NULL, so labelling later is an UPDATE rather
than a schema change.

### 3. Drive recorder - the bridge to station names

`drive_recorder.py`, `/api/drive/record|status|match`, **Record drive** and
**Match driven names** on Discovery.

Two halves are now on disk and cannot be joined: extracted times with no
names, and 61 extracted names with no positions. The live API bridges them -
`DriverAid.TrackData` gives `stationName` with `distanceToStationCM` - so one
run along the route produces the mapping.

It reuses the app's own `/api/journey` reader rather than opening its own
connection, so recording shares the single upstream lock and cannot
reintroduce the connection drops v7.44.0 fixed.

**Closest approach compares ABSOLUTE distance.** The value goes negative once
a station is behind the train, so a plain `<` kept the point furthest PAST
each station - the first test showed closest approaches of -1800m and
recorded the train's position there rather than at the station. Now within
150m on the simulated run. The same fix was needed in the SQLite upsert.

`match_sightings_to_stations()` reports matches rather than applying them:
the index asset holds BOTH "Edinburgh Waverley" and DTG's "Edinburgh
Waverly", so the leftovers need a person's eye.

### A false failure worth recording

`tests/eval_pipeline.py` first reported the restore importing zero rows. The
APP was correct - each module sets `DB_PATH` absolutely from its own
`__file__`, so the test's `os.chdir` made it write to a relative `data/`
while the app read the real one. The harness now redirects `DB_PATH` after
import. Worth remembering: a test that changes directory does not redirect
these modules.
