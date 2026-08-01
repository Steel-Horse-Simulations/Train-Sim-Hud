# TSW Hud

A small Windows desktop app for Train Sim World's built-in `-HTTPAPI` feature.
It opens as its own application window, finds the game's API key for you,
shows a live cab dashboard while you drive, and can scan the whole API so you
can hand the results back to Claude to build new dashboard pages.

## 1. One-time setup

**Python.** Install Python 3.10+ from python.org if you don't have it. During
install, tick "Add python.exe to PATH".

**Enable the game's API.** In Steam: right-click Train Sim World → Properties
→ General → Launch Options, and add:

```
-HTTPAPI
```

Launch the game normally after that. The first time it starts with this
option, it creates a key file at:

```
Documents\My Games\TrainSimWorld6\Saved\Config\CommAPIKey.txt
```

(the version number in the folder name matches whichever TSW you own — 3, 4,
5 or 6). You don't need to open this file yourself — the app reads it for
you.

## 2. Running the app

Double-click **`run.bat`**. The first run will create a small local Python
environment and install the three dependencies (Flask, requests, pywebview) —
this needs an internet connection once. After that it just launches.

You'll get a normal Windows application window titled "TSW Hud"
with three tabs:

- **Setup** — click **Auto-detect** to find your key file automatically, or
  **Browse…** to pick the folder yourself if it's somewhere non-standard
  (e.g. a moved Documents folder). Shows a green/red status so you can see at
  a glance whether the game is currently reachable.
- **Dashboard** — a live readout of speed, gradient, next signal, weather and
  time of day, updating once a second while you're driving.
- **Discovery** — scans every node/endpoint the game currently exposes and
  gives you a text dump you can **Copy to clipboard** or **Save to file**
  (saved into the `exports` folder next to the app). Paste that text into a
  chat with Claude and it can build a new dashboard page tailored to exactly
  what your game session exposes — different locos and routes expose
  different endpoints, so re-scanning after picking a new train is worth it.

Click **Exit** (top right, in the app itself) to fully close it — this shuts
down the local server as well as the window, so nothing is left running in
the background.

## 3. The HUD look and Customisation tab (new in 2.0.0)

The Dashboard now renders as an actual HUD overlay - corner-anchored panels,
a circular speedometer, and a real-looking white speed-limit sign - styled
to resemble TSW's own in-game HUD (Barlow Condensed font, dark glass
panels).

The speedometer ring changes colour based on your speed vs. the current
limit, and this is fixed regardless of theme since it carries real meaning:
- **Green** - at or under the limit
- **Amber** - over the limit, but by less than 3 mph
- **Red** - 3 mph or more over the limit

The **Customisation** tab lets you pick an accent colour - Purple (default),
Green, Blue, Amber, Crimson, Teal, Rose, Slate, or Rainbow - applied across
every page. Your choice is saved and reloads automatically next time you
open the app. Connection-status colours and the speedometer's over-speed
colours never change with theme, so they stay meaningful.

## 4. Adding new dashboard pages

Every time you ask Claude for a new view (e.g. "show me the brake pressures"
or "build a page for the class 802's traction display"), it can just add a
new `.html` file into the `pages/` folder using the same pattern as
`dashboard.html`:

- Fetch live values with `fetch('/api/proxy/get/<path>')` — this is
  same-origin (no CORS issues) and the key is attached automatically.
- To trigger an action (e.g. raise a pantograph), `PATCH` to
  `/api/proxy/set/<path>?Value=...`.
- Link the new page from the top nav in `style.css`-styled pages so it's easy
  to find.

No restart is usually needed — just refresh the window, or reopen the app if
you added new server-side logic to `app.py`.

## 5. Real-world weather sync

The **Real Weather** tab syncs in-game weather to match real conditions at
your train's actual real-world position, using [Open-Meteo](https://open-meteo.com)
(free, no API key needed). Click **Start sync** and it will:

1. Read your train's real-world GPS position from the game itself.
2. Fetch current weather for that exact location.
3. Smoothly blend the in-game weather to match over 59 seconds, repeating
   every 60 seconds (1,440 calls/day, well inside Open-Meteo's free-tier
   limit of 10,000/day, 5,000/hour, 600/min -
   see [open-meteo.com/en/terms](https://open-meteo.com/en/terms)).

**Important:** open TSW's pause menu and set the **Weather** mode to
**Custom** (not Dynamic) before starting a sync. If dynamic weather is left
on, the game's own weather system can fight with these updates.

The exact real-world-to-TSW-scale mapping (e.g. how many mm/hour of rain
equals "full" precipitation) is a best-effort estimate — Dovetail doesn't
publish an official conversion. If a field looks off in-game, that's useful
feedback: send Claude a screenshot or description and it can adjust the
scaling in `map_open_meteo_to_tsw()` in `app.py`.

This feature needs an outbound internet connection (to reach Open-Meteo) in
addition to the local connection to the game — that's separate from, and
unrelated to, the LAN/tablet access described below.

## 6. Using it on a tablet or phone (same Wi-Fi only)

The **Setup** tab shows a network address (something like
`http://192.168.1.42:5273/pages/dashboard.html`) once the app is running.
To view a dashboard on a tablet:

1. Make sure the tablet is connected to the **same Wi-Fi network** as the PC
   running the app (not mobile data, not a different network).
2. Open that address in the tablet's browser.
3. The **first time** you run the app after this update, Windows will likely
   pop up a firewall prompt — tick **Private networks** and allow it. If you
   accidentally allowed only "Public networks" or blocked it, open
   **Windows Security → Firewall & network protection → Allow an app through
   firewall**, find Python (or the app), and enable it for Private networks.

This only works on your local network — nothing here opens your PC up to the
internet. That would require your router to be specifically configured to
forward this port from outside, which nothing in this app does or needs.

**Worth knowing:** there's no login on this local server, so anyone else on
your Wi-Fi (e.g. other devices in the house) could technically open the same
address, view the dashboard, or hit Exit. On a home network that's normally
fine, just worth being aware of if you're on a shared/public Wi-Fi — in that
case, stick to using it on the PC only.

## 7. Troubleshooting

- **"No key file found"** — make sure you added `-HTTPAPI` to the launch
  options (not just started the game normally), and that you've launched the
  game at least once since adding it.
- **Setup page shows a warning even though the key was found** — the game's
  API only fully responds once you're in an active session (menus and
  loading screens return little or nothing).
- **Dashboard fields stuck on "—"** — some endpoints only exist for certain
  locomotives; run a Discovery scan while sat in the cab you want to support,
  and send Claude the result so it can point the dashboard at the right
  paths for that vehicle.
- **Firewall prompt on first run** — the app listens on your local network
  so devices like a tablet can reach it; it isn't exposed to the internet.
  Allow it for private networks only.

## 8. If the app freezes or crashes: WebView2 issue vs. general hang

There are two different failure modes that look similar but have different
causes and fixes:

**A) The native window itself crashes/freezes, with errors in the console
mentioning `CoreWebView2Controller`, `QueryInterface`, `E_NOINTERFACE`, or
"can only be accessed from the UI thread".** This is a bug in the Windows
component the native window relies on (WebView2), usually caused by a
version mismatch between the WebView2 Runtime installed on your PC and what
the window-drawing library expects. It's unrelated to the game or network.

Try, in order:
1. **Update the WebView2 Runtime** — download and run the Evergreen
   Bootstrapper from
   [Microsoft's WebView2 page](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)
   even if you believe it's already installed; this forces a fresh install
   of the latest version.
2. **Update pywebview** — open Command Prompt in this folder and run:
   ```
   venv\Scripts\activate.bat
   pip install --upgrade pywebview
   ```
3. **Use browser mode instead** — double-click **`run_browser.bat`** rather
   than `run.bat`. This skips the native window entirely and opens the app
   in your normal default browser (Edge, Chrome, whatever you use) instead,
   which completely avoids this class of bug since it doesn't touch the
   WebView2 component at all. You lose the native app-window look, but every
   feature (Setup, Dashboard, Discovery, Exit) works identically. This is
   the most reliable option if 1 and 2 don't resolve it.

**B) Everything, including the browser tab or window, becomes slow or
unresponsive, with no console errors like the above.** This is more likely a
genuine network/performance issue — see the diagnostics folder guidance
below.

## 9. If the app freezes or won't respond (general)

You don't need the app to be responsive for this — it writes diagnostics to
disk continuously in the background, with no button required. Open the
**`diagnostics`** folder next to `app.py` (created automatically the first
time you run this version) and send me these files:

- **`latest_snapshot.txt`** — a full status snapshot refreshed roughly every
  10 seconds: your config, key status, network addresses, recent request
  timings, and the last heartbeat time.
- **`heartbeat.log`** — a timestamp written every 2 seconds by a thread that
  runs independently of the window and the web server. If this is still
  updating right up to "now" while the window is frozen, the problem is in
  the window/rendering layer specifically. If it stopped updating a while
  ago, the whole Python process itself has hung or crashed.
- **`crash.log`** — populated automatically if any unhandled error occurs in
  any part of the app, even ones that wouldn't otherwise show up anywhere.
- **`calls.log`** — timing (in milliseconds) of every recent request to the
  game, useful for spotting whether the game itself is slow to respond.

If the window is stuck and won't close via its own Exit button, use Windows'
Task Manager (Ctrl+Shift+Esc) → find "TSW Hud" or "python" → End
Task. That's safe; nothing is left running afterward.

## 10. Turning it into a standalone .exe (optional)

If you'd rather have a single `.exe` you can pin to the Start Menu instead of
running `run.bat`:

```
pip install pyinstaller
pyinstaller --noconsole --onefile --add-data "pages;pages" --name "TSW Hud" app.py
```

The finished file appears in `dist\TSW Hud.exe`. Copy the `pages`
folder alongside it if the `--add-data` bundling doesn't pick it up on your
system (PyInstaller's data bundling is a common source of first-run
friction — if pages don't load, check `dist` contains a `pages` folder next
to the exe, or run un-bundled via `run.bat` instead, which is more reliable).

## How it works, in short

Train Sim World, started with `-HTTPAPI`, runs a small web server on
`localhost:31270` and writes an access key to `CommAPIKey.txt`. This app:

1. Reads that key from the folder you configure.
2. Runs its own local web server (`app.py`, via Flask) that proxies requests
   to the game, attaching the key automatically, and serves the HTML pages
   in `pages/`.
3. Wraps that local server in a native window using `pywebview`, so it looks
   and behaves like a normal Windows application rather than a browser tab.
4. Exposes a discovery endpoint that walks the game's own `/info` and
   `/list` routes, which return the live list of everything currently
   available — this is why the endpoint list is always accurate for
   whatever you're driving, rather than a fixed list that goes stale.
