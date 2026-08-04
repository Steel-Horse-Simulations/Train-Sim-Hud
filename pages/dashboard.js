// Shared logic for both Dashboard HUD variants (Desktop + Tablet). Both
// pages include this file as-is, so any behavioural change here applies to
// both automatically - only their CSS/sizing differs between the two files.

const mps_to_mph = v => (v * 2.23694).toFixed(1);

// TSW's distanceToSignal is in CENTIMETRES, not metres - confirmed by
// comparing a real in-game reading (81 yards) against the raw API value
// (7379), which only lines up when treated as cm (7379cm = 73.79m = ~81yd).
function fmtDistance(rawCm) {
  if (rawCm === undefined || rawCm === null) return '—';
  const meters = rawCm / 100;
  if (meters > 999) return (meters * 0.000621371).toFixed(2) + ' mi';
  return Math.round(meters) + ' m';
}

function signalColor(aspect) {
  if (!aspect) return '#555';
  const a = aspect.toLowerCase();
  if (a.includes('clear') || a.includes('green')) return '#5fd07a';
  if (a.includes('caution') || a.includes('yellow') || a.includes('amber')) return '#e8b74a';
  if (a.includes('danger') || a.includes('red') || a.includes('stop')) return '#e05a4e';
  return '#4f8fc0';
}

// Speed ring colour: fixed meaning regardless of theme.
//   at/under limit -> green
//   over limit but less than 3 mph over -> amber
//   3 mph or more over -> red
function speedRingColor(speedMph, limitMph) {
  if (limitMph === null || limitMph === undefined) return '#5fd07a';
  const over = speedMph - limitMph;
  if (over >= 3) return '#e05a4e';
  if (over > 0) return '#e8b74a';
  return '#5fd07a';
}

// Weather icon paths, same set shown in the Customisation preview - keyed
// by a short condition name, resolved from Open-Meteo's WMO weather_code.
const WEATHER_ICONS = {
  clear: '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M4.2 4.2l1.5 1.5M18.3 18.3l1.5 1.5M2.5 12h2.2M19.3 12h2.2M4.2 19.8l1.5-1.5M18.3 5.7l1.5-1.5"/>',
  partlyCloudy: '<circle cx="8.5" cy="9" r="3.2"/><path d="M8.5 3.3v1.5M3.3 9h1.5M13.5 5.6l-1 1M4.1 14.5l1-1"/><path d="M11 19a3.6 3.6 0 0 1-.3-7.18A5 5 0 0 1 20 9.3 4 4 0 0 1 19.4 19H11Z"/>',
  overcast: '<path d="M6.5 19a4 4 0 0 1-.4-7.98A5.5 5.5 0 0 1 16.6 8.5 4.5 4.5 0 0 1 16 19H6.5Z"/>',
  fog: '<path d="M6.5 13a4 4 0 0 1-.4-7.98A5.5 5.5 0 0 1 16.6 2.5 4.5 4.5 0 0 1 16 13H6.5Z"/><path d="M4 17h16M4 20h16"/>',
  rain: '<path d="M6.5 12a4 4 0 0 1-.4-7.98A5.5 5.5 0 0 1 16.6 1.5 4.5 4.5 0 0 1 16 12H6.5Z"/><path d="M8 15.5 6.7 18M12 15.5 10.7 18M16 15.5 14.7 18"/>',
  snow: '<path d="M6.5 11a4 4 0 0 1-.4-7.98A5.5 5.5 0 0 1 16.6 .5 4.5 4.5 0 0 1 16 11H6.5Z"/><path d="M8 15v6M5.5 16.5l5 3M10.5 16.5l-5 3M16 15v6M13.5 16.5l5 3M18.5 16.5l-5 3"/>',
  thunderstorm: '<path d="M6.5 12a4 4 0 0 1-.4-7.98A5.5 5.5 0 0 1 16.6 1.5 4.5 4.5 0 0 1 16 12H6.5Z"/><path d="M12.5 14.5 10 19h3l-1.8 4"/>',
};

const WEATHER_LABELS = {
  clear: 'Clear', partlyCloudy: 'Partly cloudy', overcast: 'Overcast',
  fog: 'Foggy', rain: 'Rain', snow: 'Snow', thunderstorm: 'Thunderstorm',
};

// Gradient direction icons - simple triangle outlines, same stroke style as
// the weather icons: apex-up for uphill, apex-down for downhill, flat bar for level.

// WMO weather_code -> our icon key. https://open-meteo.com/en/docs
function weatherCodeToKey(code) {
  if (code === 0 || code === 1) return 'clear';
  if (code === 2) return 'partlyCloudy';
  if (code === 3) return 'overcast';
  if (code === 45 || code === 48) return 'fog';
  if ([51,53,55,56,57,61,63,65,66,67,80,81,82].includes(code)) return 'rain';
  if ([71,73,75,77,85,86].includes(code)) return 'snow';
  if ([95,96,99].includes(code)) return 'thunderstorm';
  return 'overcast';
}

function setWeatherIcon(key) {
  const el = document.getElementById('weather-icon');
  el.innerHTML = WEATHER_ICONS[key] || WEATHER_ICONS.overcast;
}

async function proxyGet(path) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), 4000);
  try {
    const res = await fetch('/api/proxy/get/' + path, { signal: controller.signal });
    if (!res.ok) throw new Error('bad status ' + res.status);
    return await res.json();
  } finally {
    clearTimeout(id);
  }
}

function updateUpcomingLimits(nextSpeedLimits, currentLimitMph) {
  const panel = document.getElementById('limits-panel');
  const row = document.getElementById('limits-row');
  if (!panel || !row) return;

  const items = [];
  if (Array.isArray(nextSpeedLimits)) {
    let lastMph = (currentLimitMph !== null && currentLimitMph !== undefined) ? Math.round(currentLimitMph) : null;
    for (const entry of nextSpeedLimits) {
      const rawMs = entry && entry.value && entry.value.value;
      // Same sanity check as the current limit - guards against the
      // Unreal "unset float" sentinel value showing up as a real number.
      if (typeof rawMs !== 'number' || !isFinite(rawMs) || rawMs < 0 || rawMs >= 112) continue;
      const mph = Math.round(parseFloat(mps_to_mph(rawMs)));
      if (mph === lastMph) continue; // skip repeated track-segment markers with the same limit
      lastMph = mph;
      items.push({ mph, distText: fmtDistance(entry.distanceToNextSpeedLimit) });
      if (items.length >= 3) break;
    }
  }

  if (items.length === 0) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';
  row.innerHTML = items.map(it => `
    <div class="limit-item">
      <div class="chip">${it.mph}</div>
      <div class="dist">${it.distText}</div>
    </div>
  `).join('');
}

let currentSpeedMph = null;
let currentLimitMph = null;
let currentMaxSpeedMph = 100;
let currentDialMaxMph = null; // real per-train speedometer dial max from Known Trains v2, via /api/loco
let currentSpeedometerMode = 'digital'; // 'digital' or 'analogue', from Known Trains v2 per train
let analogueTicksBuiltForDialMax = null; // dialMax the analogue face was last built for, so it's only rebuilt when it actually changes

// Fallback headroom multiplier, used only for locos with no Known Trains v2
// entry yet (so no real dial_max_mph is available) - keeps the dial usable
// rather than pegging the needle at 100%.
const DIAL_HEADROOM_MULTIPLIER = 1.2;

let speedPollInFlight = false;

// Picks a "nice" major tick step (5/10/15/20/25/50/100) aiming for roughly
// 6-10 numbered ticks around the dial, whatever the train's own dial max is.
function niceTickStep(dialMax) {
  const rough = dialMax / 8;
  const candidates = [5, 10, 15, 20, 25, 50, 100];
  for (const c of candidates) {
    if (rough <= c) return c;
  }
  return 100;
}

function angleForValue(v, dialMax) {
  const frac = Math.max(0, Math.min(1, v / dialMax));
  return 224.5 + frac * 271;
}

function polarPoint(r, deg) {
  const rad = (deg - 90) * Math.PI / 180;
  return { x: 60 + r * Math.cos(rad), y: 60 + r * Math.sin(rad) };
}

// Builds the numbered ticks around the analogue face for this train's dial
// max. Only re-run when dialMax actually changes (different train / edited
// setting) - not on every 300ms speed poll.
function buildAnalogueTicks(dialMax) {
  const group = document.getElementById('analogue-ticks');
  if (!group || !dialMax || dialMax === analogueTicksBuiltForDialMax) return;
  analogueTicksBuiltForDialMax = dialMax;

  const step = niceTickStep(dialMax);
  const svgNS = 'http://www.w3.org/2000/svg';
  const frag = document.createDocumentFragment();

  for (let v = 0; v <= dialMax + 0.001; v += step) {
    const deg = angleForValue(v, dialMax);
    const outer = polarPoint(50, deg);
    const inner = polarPoint(42, deg);
    const line = document.createElementNS(svgNS, 'line');
    line.setAttribute('x1', outer.x); line.setAttribute('y1', outer.y);
    line.setAttribute('x2', inner.x); line.setAttribute('y2', inner.y);
    line.setAttribute('stroke', 'var(--text)');
    line.setAttribute('stroke-width', '1.6');
    line.setAttribute('stroke-linecap', 'round');
    frag.appendChild(line);

    const labelPos = polarPoint(34, deg);
    const text = document.createElementNS(svgNS, 'text');
    text.setAttribute('x', labelPos.x);
    text.setAttribute('y', labelPos.y);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('dominant-baseline', 'middle');
    // Counter-rotate 90deg around its own point: the whole SVG is rotated
    // -90deg via CSS (.hud-gauge svg), so every text element needs this to
    // stay upright/readable rather than rendering sideways on screen.
    text.setAttribute('transform', `rotate(90 ${labelPos.x} ${labelPos.y})`);
    text.setAttribute('font-size', '9');
    text.setAttribute('fill', 'var(--text)');
    text.setAttribute('class', 'analogue-tick-label');
    text.textContent = Math.round(v);
    frag.appendChild(text);

    // Minor tick at the halfway point to the next major, skipped past dialMax
    const minorV = v + step / 2;
    if (minorV < dialMax) {
      const mdeg = angleForValue(minorV, dialMax);
      const mOuter = polarPoint(50, mdeg);
      const mInner = polarPoint(45, mdeg);
      const mLine = document.createElementNS(svgNS, 'line');
      mLine.setAttribute('x1', mOuter.x); mLine.setAttribute('y1', mOuter.y);
      mLine.setAttribute('x2', mInner.x); mLine.setAttribute('y2', mInner.y);
      mLine.setAttribute('stroke', 'var(--text-dim)');
      mLine.setAttribute('stroke-width', '1');
      mLine.setAttribute('stroke-linecap', 'round');
      frag.appendChild(mLine);
    }
  }

  group.innerHTML = '';
  group.appendChild(frag);
}

// Shows/hides the digital number vs analogue needle+ticks based on this
// train's Known Trains "speedometer" setting. Cheap to call every poll -
// only actually touches the DOM when the mode has changed.
function setSpeedometerMode(mode) {
  if (mode === currentSpeedometerMode) return;
  currentSpeedometerMode = mode;
  const isAnalogue = mode === 'analogue';
  document.querySelectorAll('.hud-gauge').forEach(g => g.classList.toggle('analogue', isAnalogue));
  const display = isAnalogue ? '' : 'none';
  const needle = document.getElementById('needle');
  const hub = document.getElementById('needle-hub');
  const hubDot = document.getElementById('needle-hub-dot');
  const mphLabel = document.getElementById('analogue-mph-label');
  if (needle) needle.style.display = display;
  if (hub) hub.style.display = display;
  if (hubDot) hubDot.style.display = display;
  if (mphLabel) mphLabel.style.display = display;
}

function updateNeedle(dialMax) {
  const needle = document.getElementById('needle');
  if (!needle || currentSpeedometerMode !== 'analogue') return;
  const speed = currentSpeedMph || 0;
  const rotation = angleForValue(speed, dialMax);
  needle.setAttribute('transform', `rotate(${rotation} 60 60)`);
}

function updateGaugeRing() {
  const ring = document.getElementById('gauge-ring');
  const tick = document.getElementById('max-tick');
  const overMaxBadge = document.getElementById('over-max-badge');

  const dialMax = currentDialMaxMph || (currentMaxSpeedMph * DIAL_HEADROOM_MULTIPLIER);
  if (tick) {
    const tickFrac = Math.max(0, Math.min(1, currentMaxSpeedMph / dialMax));
    const tickRotation = 224.5 + (tickFrac * 271);
    tick.setAttribute('transform', `rotate(${tickRotation} 60 60)`);
    tick.setAttribute('stroke', 'var(--status-red)');
  }

  if (currentSpeedometerMode === 'analogue') {
    buildAnalogueTicks(dialMax);
    updateNeedle(dialMax);
  }

  if (currentSpeedMph === null || currentSpeedMph <= 0) {
    ring.setAttribute('stroke-dasharray', '0 999');
    ring.setAttribute('stroke', 'none');
    if (overMaxBadge) overMaxBadge.classList.remove('show');
    return;
  }
  const frac = Math.max(0, Math.min(1, currentSpeedMph / dialMax));
  const arcLength = frac * 236.49;
  ring.setAttribute('stroke-dasharray', arcLength.toFixed(2) + ' 314.16');
  const overMax = currentSpeedMph > currentMaxSpeedMph;
  ring.setAttribute('stroke', overMax ? 'var(--status-red)' : speedRingColor(currentSpeedMph, currentLimitMph));
  if (overMaxBadge) overMaxBadge.classList.toggle('show', overMax);
}

// Last-resort fallback: recursively find the first numeric value anywhere
// in an object. Used only if none of the known field-name shapes match, so
// something still shows rather than nothing - and we log what we found.
function findFirstNumber(obj, depth) {
  if (depth > 3 || obj === null || obj === undefined) return undefined;
  if (typeof obj === 'number') return obj;
  if (typeof obj === 'object') {
    for (const key of Object.keys(obj)) {
      const v = findFirstNumber(obj[key], depth + 1);
      if (v !== undefined) return v;
    }
  }
  return undefined;
}

async function pollSpeed() {
  if (speedPollInFlight) return;
  speedPollInFlight = true;
  const debugEl = document.getElementById('speed-debug');
  try {
    const speed = await proxyGet('CurrentDrivableActor.Function.HUD_GetSpeed');
    // TSW's Function.* endpoints return their result under "ReturnValue"
    // (confirmed from other Function.Get* calls in the game's own API) -
    // "Speed (ms)" was an earlier guess that turned out wrong, which is why
    // speed was showing blank. Checking a few shapes here for safety.
    let speedMs = (speed.Values && speed.Values.ReturnValue !== undefined) ? speed.Values.ReturnValue
                 : (speed.ReturnValue !== undefined) ? speed.ReturnValue
                 : speed['Speed (ms)'];
    if (speedMs === undefined && debugEl) {
      // None of our guesses matched - fall back to finding any number in the
      // response so something still shows, and surface the raw response so
      // it can be copied straight to Claude instead of guessing a 4th time.
      speedMs = findFirstNumber(speed, 0);
      debugEl.style.display = 'block';
      debugEl.textContent = 'Speed field not recognised - raw response: ' + JSON.stringify(speed);
    } else if (debugEl) {
      debugEl.style.display = 'none';
    }
    currentSpeedMph = (speedMs !== undefined && speedMs !== null) ? parseFloat(mps_to_mph(speedMs)) : null;
    document.getElementById('v-speed').textContent = (currentSpeedMph !== null) ? currentSpeedMph.toFixed(0) : '—';
    updateGaugeRing();
  } catch (e) {
    // a single missed fast poll isn't worth surfacing - the next one 300ms later will likely succeed
  } finally {
    speedPollInFlight = false;
  }
}

let driverPollInFlight = false;
let auxPollInFlight = false;

async function pollDriver() {
  if (driverPollInFlight) return;
  driverPollInFlight = true;
  const statusEl = document.getElementById('poll-status');
  try {
    const aid = await proxyGet('DriverAid.Data');
    const v = aid.Values || {};
    if (v.gradient !== undefined) {
      document.getElementById('v-gradient').textContent = v.gradient.toFixed(1) + ' %';
    } else {
      document.getElementById('v-gradient').textContent = '—';
    }
    const rawLimitMs = v.speedLimit && v.speedLimit.value;
    const limitIsSane = typeof rawLimitMs === 'number' && isFinite(rawLimitMs) && rawLimitMs >= 0 && rawLimitMs < 112;
    currentLimitMph = limitIsSane ? parseFloat(mps_to_mph(rawLimitMs)) : null;
    document.getElementById('v-limit').textContent = (currentLimitMph !== null) ? currentLimitMph.toFixed(0) : '—';
    document.getElementById('v-dist').textContent = fmtDistance(v.distanceToSignal);
    document.getElementById('v-signal-text').textContent = v.signalAspectClass || '—';
    updateUpcomingLimits(v.nextSpeedLimits, currentLimitMph);
    document.getElementById('signal-lamp').style.background = signalColor(v.signalAspectClass);
    updateGaugeRing();
    if (statusEl) statusEl.textContent = '· connected';
  } catch (e) {
    if (statusEl) statusEl.textContent = '· waiting for game…';
  } finally {
    driverPollInFlight = false;
  }
}

async function pollAux() {
  if (auxPollInFlight) return;
  auxPollInFlight = true;
  try {
    const weather = await proxyGet('WeatherManager.Data');
    const w = weather.Values || weather;
    document.getElementById('w-temp').textContent = (w.Temperature !== undefined) ? w.Temperature.toFixed(1) + ' °C' : '—';
    document.getElementById('w-precip').textContent = (w.Precipitation !== undefined) ? (w.Precipitation * 100).toFixed(0) + ' %' : '—';
    document.getElementById('w-cloud').textContent = (w.Cloudiness !== undefined) ? (w.Cloudiness * 100).toFixed(0) + ' %' : '—';
    document.getElementById('w-fog').textContent = (w.FogDensity !== undefined) ? (w.FogDensity * 100).toFixed(0) + ' %' : '—';

    try {
      const wsResponse = await fetch('/api/weather/status');
      const ws = await wsResponse.json();
      if (ws.open_meteo && ws.open_meteo.temperature_2m !== undefined) {
        const key = weatherCodeToKey(ws.open_meteo.weather_code);
        setWeatherIcon(key);
        document.getElementById('w-hud-text').textContent =
          ws.open_meteo.temperature_2m.toFixed(0) + '°C · ' + WEATHER_LABELS[key];
        document.getElementById('w-hud-source').textContent = 'Current weather';
      } else if (w.Temperature !== undefined) {
        const key = (w.Cloudiness || 0) > 0.6 ? 'overcast' : (w.Cloudiness || 0) > 0.2 ? 'partlyCloudy' : 'clear';
        setWeatherIcon(key);
        document.getElementById('w-hud-text').textContent = w.Temperature.toFixed(0) + '°C · ' + WEATHER_LABELS[key];
        document.getElementById('w-hud-source').textContent = 'In-game weather';
      } else {
        document.getElementById('w-hud-text').textContent = '—';
      }
    } catch (e) {
      document.getElementById('w-hud-text').textContent = '—';
    }

    const tod = await proxyGet('TimeOfDay.data');
    const t = tod.Values || tod;
    document.getElementById('t-local').textContent = t.LocalTimeISO8601 ? t.LocalTimeISO8601.substr(11, 8) : '—';
    document.getElementById('t-sunrise').textContent = t.SunriseTime || '—';
    document.getElementById('t-sunset').textContent = t.SunsetTime || '—';
  } catch (e) {
    // aux data not critical
  } finally {
    auxPollInFlight = false;
  }
}

function exitApp() {
  fetch('/api/shutdown', {method: 'POST'});
}

// Temporary Full Screen toggle - placeholder implementation, will likely be
// replaced later. Toggles the whole page in/out of the browser's fullscreen mode.
function toggleFullScreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(() => {});
  } else {
    document.exitFullscreen().catch(() => {});
  }
}

async function pollLoco() {
  const el = document.getElementById('v-loco');
  if (!el) return;
  try {
    const res = await fetch('/api/loco');
    const data = await res.json();
    if (data && data.name) {
      el.textContent = data.name;
      el.classList.remove('missing');
    } else {
      el.textContent = 'Missing Train Class';
      el.classList.add('missing');
    }
    if (data && typeof data.max_speed_mph === 'number' && data.max_speed_mph > 0) {
      currentMaxSpeedMph = data.max_speed_mph;
    }
    if (data && typeof data.dial_max_mph === 'number' && data.dial_max_mph > 0) {
      currentDialMaxMph = data.dial_max_mph;
    } else {
      currentDialMaxMph = null;
    }
    setSpeedometerMode(data && data.speedometer === 'analogue' ? 'analogue' : 'digital');
    updateGaugeRing();
  } catch (e) {
    el.textContent = 'Missing Train Class';
    el.classList.add('missing');
  }
  el.style.display = 'block';
}

function startDashboard() {
  setWeatherIcon('overcast');
  pollSpeed();
  setInterval(pollSpeed, 300);
  pollDriver();
  setInterval(pollDriver, 300);
  pollAux();
  setInterval(pollAux, 5000);
  pollLoco();
  setInterval(pollLoco, 10000);
}
