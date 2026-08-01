# TSW Hud — Launcher Integration Guide

## For Parent Launcher Programs

If you're embedding TSW Hud inside another launcher/application, use this environment variable to run it in headless server mode (no browser window opens).

### Launch Command

Set `TSW_HUD_NO_BROWSER=true` before running `app.py`:

```batch
set TSW_HUD_NO_BROWSER=true
python app.py
```

Or as a one-liner:

```batch
cmd /c "set TSW_HUD_NO_BROWSER=true && python app.py"
```

### What Happens

1. Flask server starts on `http://127.0.0.1:5273/`
2. No browser window opens
3. No separate pywebview window opens
4. Server runs in the background, listening for requests
5. Your parent program can embed the UI via iframe, WebView2 control, or HTTP client

### Example: Embedding in a WebView2 Control (C#/.NET)

```csharp
// Start TSW Hud server process
var psi = new ProcessStartInfo
{
    FileName = "cmd.exe",
    Arguments = "/c \"set TSW_HUD_NO_BROWSER=true && python app.py\"",
    WorkingDirectory = @"C:\path\to\TSW Hud",
    UseShellExecute = false,
    CreateNoWindow = true  // Hide console
};
var tsw_hud_process = Process.Start(psi);

// In your WebView2 control, navigate to:
webView.Source = new Uri("http://127.0.0.1:5273/");
```

### Example: Embedding in an Electron App (JavaScript)

```javascript
const { spawn } = require('child_process');
const path = require('path');

// Start TSW Hud server
const tswHudProcess = spawn('python', ['app.py'], {
  cwd: 'C:\\path\\to\\TSW Hud',
  env: {
    ...process.env,
    TSW_HUD_NO_BROWSER: 'true'
  }
});

// In your Electron window, load:
mainWindow.loadURL('http://127.0.0.1:5273/');
```

### Graceful Shutdown

The Flask server listens for `POST /api/shutdown`. Your parent program can call this to cleanly stop the server:

```javascript
fetch('http://127.0.0.1:5273/api/shutdown', { method: 'POST' })
  .then(() => console.log('TSW Hud server stopped'))
  .catch(err => console.error('Shutdown failed:', err));
```

Or kill the process directly (all threads will exit):

```javascript
tswHudProcess.kill();
```

### Environment Variables

| Variable | Value | Effect |
|----------|-------|--------|
| `TSW_HUD_NO_BROWSER` | `true` | Skip opening browser; run headless server only |

If not set or set to anything other than `true`, the app will behave normally (open browser window or native pywebview window).

### Notes

- The server binds to `127.0.0.1:5273` (localhost only, not exposed to network)
- All Flask routes, API endpoints, and pages work normally
- The app's Exit button calls `POST /api/shutdown`, which will kill the process
- Background threads (weather sync, other_hud_sync, heartbeat) run normally
- Crash logging and diagnostics still work as expected
