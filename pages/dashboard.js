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
let analogueTicksBuiltKey = null;       // guards against rebuilding identical ticks every 300ms poll

// Fallback headroom multiplier, used only for locos with no Known Trains v2
// entry yet (so no real dial_max_mph is available) - keeps the dial usable
// rather than pegging the needle at 100%.
const DIAL_HEADROOM_MULTIPLIER = 1.2;

let speedPollInFlight = false;

// ---------------------------------------------------------------------------
// Gauge angle conventions - read this before touching any dial geometry.
//
// The whole gauge SVG is rotated -90deg by CSS (.hud-gauge svg in style.css).
// Everything below works in *SVG space* and lets that CSS rotation do the
// final turn, exactly like the existing ring does.
//
//   - The ring is a <circle> whose stroke-dasharray starts at SVG angle 0 =
//     EAST, carrying transform="rotate(224.5 60 60)". So a value v sits at
//     SVG angle:  A = 224.5 + (v/dialMax)*271   (clockwise from east)
//   - On screen that lands at A - 90, so v=0 shows at bottom-left and the
//     scale sweeps clockwise to dialMax at bottom-right.
//   - An element drawn pointing UP in SVG (like the max-speed tick line and
//     the needle, both vertical above cy) sits at SVG angle 270 by default,
//     so to place it at value v it needs rotate(A - 270).
//
// Two long-standing bugs lived here: polarPoint() subtracted an extra 90deg
// (rotating the whole number ring a quarter turn), and the max-speed tick
// used rotate(A) instead of rotate(A - 270), putting it 270deg out.
// ---------------------------------------------------------------------------

function svgAngleForValue(v, dialMax) {
  const frac = Math.max(0, Math.min(1, v / dialMax));
  return 224.5 + frac * 271;
}

// Rotation for an element that points UP in SVG (tick line, needle).
function upElementRotationForValue(v, dialMax) {
  return svgAngleForValue(v, dialMax) - 270;
}

// Point at radius r along an SVG angle (clockwise from east) - NO extra
// offset, matching the ring's own convention.
function polarPoint(r, svgAngleDeg) {
  const rad = svgAngleDeg * Math.PI / 180;
  return { x: 60 + r * Math.cos(rad), y: 60 + r * Math.sin(rad) };
}

// Numbers always advance in clean multiples of 10, except very small dials
// (50mph or under) which use multiples of 5 so there are still enough ticks
// to read easily.
function niceTickStep(dialMax) {
  if (dialMax <= 50) return 5;
  if (dialMax <= 200) return 10;
  if (dialMax <= 400) return 20;
  return 50;
}

// Spacing between minor (unlabelled) ticks. Small dials (<=50) get a minor
// tick at every 1mph; everything else keeps the old halfway-between-majors
// spacing.
function minorTickSpacing(dialMax, majorStep) {
  return dialMax <= 50 ? 1 : majorStep / 2;
}

// ---------------------------------------------------------------------------
// Analogue face geometry. Every value below is the approved mockup's own
// number scaled by 50/460 (its arc radius 460 maps to this gauge's ring r=50),
// so the dial renders identically to the design that was signed off.
// Do not "tidy" these into round numbers - they are a direct transcription.
// ---------------------------------------------------------------------------
const AN = {
  ringWidth:  2.5,      // mockup arc_w 20 (2.1739) widened 15% per request
  tickOuter: 51.5217,   // major_out 474
  majorInner:45.5435,   // major_in  419
  minorInner:48.4783,   // minor_in  446
  labelR:    39.5652,   // label_r   364
  majorW:     0.5435,   // major stroke 5
  minorW:     0.3261,   // minor stroke 3
  fontNum:    5.6522,   // number font 52
};

// ---------------------------------------------------------------------------
// 3-digit 7-segment (LCD odometer style) readout. Built entirely from
// rounded-rect bar segments - not a font/text glyph - so it looks like a real
// 7-segment display: every segment always exists at a dim "unlit" colour
// (matching a real LCD's dead segments showing through), and the ones that
// are ON for the current digit are recoloured bright on each update.
// No decimal points - just the three digit cells.
// ---------------------------------------------------------------------------
const SEVEN_SEG_MAP = {
  '0': 'abcdef', '1': 'bc', '2': 'abged', '3': 'abgcd', '4': 'fgbc',
  '5': 'afgcd', '6': 'afgecd', '7': 'abc', '8': 'abcdefg', '9': 'abcdfg',
};

// Segment geometry for one digit cell of size (dw, dh), each segment as a
// rounded-rect bar: {x, y, w, h, rx}. Standard 7-seg layout - a=top,
// b=top-right, c=bottom-right, d=bottom, e=bottom-left, f=top-left, g=middle.
function sevenSegGeometry(dw, dh, t) {
  const gap = t * 0.35;
  return {
    a: { x: t, y: 0, w: dw - 2 * t, h: t, rx: t / 2 },
    g: { x: t, y: dh / 2 - t / 2, w: dw - 2 * t, h: t, rx: t / 2 },
    d: { x: t, y: dh - t, w: dw - 2 * t, h: t, rx: t / 2 },
    f: { x: 0, y: gap + t / 2, w: t, h: dh / 2 - t - gap, rx: t / 2 },
    b: { x: dw - t, y: gap + t / 2, w: t, h: dh / 2 - t - gap, rx: t / 2 },
    e: { x: 0, y: dh / 2 + t / 2 + gap, w: t, h: dh / 2 - t - gap, rx: t / 2 },
    c: { x: dw - t, y: dh / 2 + t / 2 + gap, w: t, h: dh / 2 - t - gap, rx: t / 2 },
  };
}

// Builds the static structure once: 3 digit cells, each with all 7 segment
// <rect>s already in the DOM (as dim "unlit" bars). Re-running just clears
// and rebuilds - cheap, and keeps this idempotent like the tick builder.
function buildDigitalReadout() {
  const group = document.getElementById('analogue-digital-readout');
  if (!group || group.childElementCount) return; // structure only needs building once

  const svgNS = 'http://www.w3.org/2000/svg';
  const dw = 5.2, dh = 9, t = 0.85, cellGap = 1.6;
  // Absolute SVG-space coordinates: the group's transform is a rotate(90 32 60)
  // - it spins the block in place around (32,60), it does NOT translate content
  // drawn at local (0,0) there. So the digits are laid out directly around
  // that same pivot point rather than around the origin.
  const pivotX = 32, pivotY = 60;
  const totalW = dw * 3 + cellGap * 2;
  const startX = pivotX - totalW / 2;
  const startY = pivotY - dh / 2;
  const geom = sevenSegGeometry(dw, dh, t);

  // Subtle border panel behind the segments, sized to the digit block plus
  // a small margin, so the readout reads as a distinct "display" rather
  // than free-floating segments.
  const pad = 1.4;
  const border = document.createElementNS(svgNS, 'rect');
  border.setAttribute('x', (startX - pad).toFixed(3));
  border.setAttribute('y', (startY - pad).toFixed(3));
  border.setAttribute('width', (totalW + pad * 2).toFixed(3));
  border.setAttribute('height', (dh + pad * 2).toFixed(3));
  border.setAttribute('rx', '1');
  border.setAttribute('fill', 'none');
  border.setAttribute('stroke', 'rgba(255,255,255,0.14)');
  border.setAttribute('stroke-width', '0.35');
  group.appendChild(border);

  for (let digit = 0; digit < 3; digit++) {
    const ox = startX + digit * (dw + cellGap);
    for (const name of 'abcdefg') {
      const seg = geom[name];
      const rect = document.createElementNS(svgNS, 'rect');
      rect.setAttribute('x', (ox + seg.x).toFixed(3));
      rect.setAttribute('y', (startY + seg.y).toFixed(3));
      rect.setAttribute('width', seg.w.toFixed(3));
      rect.setAttribute('height', seg.h.toFixed(3));
      rect.setAttribute('rx', seg.rx.toFixed(3));
      rect.setAttribute('fill', 'rgba(255,255,255,0.05)'); // dim/unlit by default
      rect.setAttribute('id', `dseg-${digit}-${name}`);
      group.appendChild(rect);
    }
  }
}

// Recolours each segment bright or dim based on the digit it belongs to.
function updateDigitalReadout(speedMph) {
  const group = document.getElementById('analogue-digital-readout');
  if (!group) return;
  buildDigitalReadout();

  const clamped = Math.min(999, Math.max(0, Math.round(speedMph || 0)));
  const digits = String(clamped).padStart(3, '0').split('');

  digits.forEach((ch, i) => {
    const on = SEVEN_SEG_MAP[ch] || '';
    for (const name of 'abcdefg') {
      const rect = document.getElementById(`dseg-${i}-${name}`);
      if (rect) rect.setAttribute('fill', on.includes(name) ? 'var(--accent-bright)' : 'rgba(255,255,255,0.05)');
    }
  });
}

// Builds the numbered ticks around the analogue face for this train's dial max.
function buildAnalogueTicks(dialMax) {
  const group = document.getElementById('analogue-ticks');
  if (!group || !dialMax) return;

  const step = niceTickStep(dialMax);
  const key = dialMax + ':' + step;
  if (key === analogueTicksBuiltKey) return;
  analogueTicksBuiltKey = key;

  const svgNS = 'http://www.w3.org/2000/svg';
  const frag = document.createDocumentFragment();

  for (let v = 0; v <= dialMax + 0.001; v += step) {
    const A = svgAngleForValue(v, dialMax);

    const outer = polarPoint(AN.tickOuter, A);
    const inner = polarPoint(AN.majorInner, A);
    const line = document.createElementNS(svgNS, 'line');
    line.setAttribute('x1', outer.x.toFixed(3)); line.setAttribute('y1', outer.y.toFixed(3));
    line.setAttribute('x2', inner.x.toFixed(3)); line.setAttribute('y2', inner.y.toFixed(3));
    line.setAttribute('stroke', 'var(--text)');
    line.setAttribute('stroke-width', AN.majorW);
    line.setAttribute('stroke-linecap', 'butt');
    frag.appendChild(line);

    const labelPos = polarPoint(AN.labelR, A);
    const text = document.createElementNS(svgNS, 'text');
    text.setAttribute('x', labelPos.x.toFixed(3));
    text.setAttribute('y', labelPos.y.toFixed(3));
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('dominant-baseline', 'central');
    // Counter-rotate +90 about its own point to cancel the SVG's -90 CSS
    // rotation, so numbers read upright on screen.
    text.setAttribute('transform', `rotate(90 ${labelPos.x.toFixed(3)} ${labelPos.y.toFixed(3)})`);
    text.setAttribute('font-size', AN.fontNum);
    text.setAttribute('font-weight', '600');
    text.setAttribute('fill', 'var(--text)');
    text.setAttribute('class', 'analogue-tick-label');
    text.textContent = Math.round(v);
    frag.appendChild(text);

    // Minor tick(s) between this major and the next, skipped past dialMax
    const minorStep = minorTickSpacing(dialMax, step);
    for (let minorV = v + minorStep; minorV < v + step && minorV < dialMax; minorV += minorStep) {
      const mA = svgAngleForValue(minorV, dialMax);
      const mOuter = polarPoint(AN.tickOuter, mA);
      const mInner = polarPoint(AN.minorInner, mA);
      const mLine = document.createElementNS(svgNS, 'line');
      mLine.setAttribute('x1', mOuter.x.toFixed(3)); mLine.setAttribute('y1', mOuter.y.toFixed(3));
      mLine.setAttribute('x2', mInner.x.toFixed(3)); mLine.setAttribute('y2', mInner.y.toFixed(3));
      mLine.setAttribute('stroke', 'var(--text-dim)');
      mLine.setAttribute('stroke-width', AN.minorW);
      mLine.setAttribute('stroke-linecap', 'butt');
      frag.appendChild(mLine);
    }
  }

  group.innerHTML = '';
  group.appendChild(frag);
}

// Shows/hides the digital number vs analogue needle+ticks based on this
// train's Known Trains "speedometer" setting. Applies unconditionally rather
// than early-returning on "no change" - a stale/cached stylesheet on the
// tablet previously left the digital readout visible over the dial, so the
// visibility is forced here with inline styles instead of relying on CSS.
function setSpeedometerMode(mode) {
  const isAnalogue = mode === 'analogue';
  if (mode !== currentSpeedometerMode) {
    currentSpeedometerMode = mode;
    analogueTicksBuiltKey = null; // force a rebuild on the next update
  }

  document.querySelectorAll('.hud-gauge').forEach(g => g.classList.toggle('analogue', isAnalogue));
  document.querySelectorAll('.hud-gauge .speed-num').forEach(el => {
    el.style.display = isAnalogue ? 'none' : '';
  });

  // The mockup's arc is far thinner than the digital gauge's ring. Without
  // this the ticks and numbers sit against a band ~4x too heavy and the whole
  // face reads wrong, so the ring is thinned to the design's width here.
  const ringWidth = isAnalogue ? AN.ringWidth : 9.2; // digital ring also widened 15% (8 -> 9.2)
  ['gauge-ring-bg', 'gauge-ring', 'gauge-ring-over'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.setAttribute('stroke-width', ringWidth);
  });

  const shown = isAnalogue ? '' : 'none';
  ['needle', 'needle-hub', 'needle-hub-dot', 'analogue-mph-label',
   'analogue-face', 'analogue-bezel', 'analogue-digital-readout'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = shown;
  });
}

function updateNeedle(dialMax) {
  const needle = document.getElementById('needle');
  if (!needle || currentSpeedometerMode !== 'analogue') return;
  const speed = currentSpeedMph || 0;
  needle.setAttribute('transform', `rotate(${upElementRotationForValue(speed, dialMax).toFixed(2)} 60 60)`);
  updateDigitalReadout(speed);
}

function updateGaugeRing() {
  const ring = document.getElementById('gauge-ring');
  const tick = document.getElementById('max-tick');
  const overMaxBadge = document.getElementById('over-max-badge');

  const dialMax = currentDialMaxMph || (currentMaxSpeedMph * DIAL_HEADROOM_MULTIPLIER);

  if (tick) {
    if (currentSpeedometerMode === 'analogue') {
      // Draw it as an exact duplicate of the major tick underneath it - same
      // endpoints, same stroke width, same cap - just red. Max speeds are
      // always multiples of 5 so this always lands exactly on a tick.
      const A = svgAngleForValue(currentMaxSpeedMph, dialMax);
      const outer = polarPoint(AN.tickOuter, A);
      const inner = polarPoint(AN.majorInner, A);
      tick.setAttribute('x1', outer.x.toFixed(3)); tick.setAttribute('y1', outer.y.toFixed(3));
      tick.setAttribute('x2', inner.x.toFixed(3)); tick.setAttribute('y2', inner.y.toFixed(3));
      tick.setAttribute('transform', '');
      tick.setAttribute('stroke-width', AN.majorW);
      tick.setAttribute('stroke-linecap', 'butt');
    } else {
      // Digital gauge: short line across the ring band, rotated into place.
      tick.setAttribute('x1', '60'); tick.setAttribute('y1', '6');
      tick.setAttribute('x2', '60'); tick.setAttribute('y2', '14');
      tick.setAttribute('transform', `rotate(${upElementRotationForValue(currentMaxSpeedMph, dialMax).toFixed(2)} 60 60)`);
      tick.setAttribute('stroke-width', '2');
      tick.setAttribute('stroke-linecap', 'butt');
    }
    tick.setAttribute('stroke', 'var(--status-red)');
  }

  if (currentSpeedometerMode === 'analogue') {
    buildAnalogueTicks(dialMax);
    updateNeedle(dialMax);
  }

  const ringOver = document.getElementById('gauge-ring-over');

  if (currentSpeedMph === null || currentSpeedMph <= 0) {
    ring.setAttribute('stroke-dasharray', '0 999');
    ring.setAttribute('stroke', 'none');
    if (ringOver) { ringOver.setAttribute('stroke-dasharray', '0 999'); ringOver.setAttribute('stroke', 'none'); }
    if (overMaxBadge) overMaxBadge.classList.remove('show');
    return;
  }

  // Normal segment: 0 up to whichever is smaller, current speed or max speed.
  // Coloured purely by speed-vs-limit (speedRingColor), same as always -
  // being over the train's max speed no longer affects this portion's colour.
  const normalUpTo = Math.min(currentSpeedMph, currentMaxSpeedMph);
  const normalFrac = Math.max(0, Math.min(1, normalUpTo / dialMax));
  const normalArcLen = normalFrac * 236.49;
  ring.setAttribute('stroke-dasharray', normalArcLen.toFixed(2) + ' 314.16');
  ring.setAttribute('stroke', speedRingColor(currentSpeedMph, currentLimitMph));

  // Over segment: only the portion from max speed up to current speed (capped
  // at the dial's own top end) - drawn as a second arc offset to start right
  // where the normal segment ends, so only speed past max shows red.
  const overMax = currentSpeedMph > currentMaxSpeedMph;
  if (ringOver) {
    if (overMax) {
      const overUpTo = Math.max(0, Math.min(currentSpeedMph, dialMax));
      const overFrac = Math.max(0, Math.min(1, overUpTo / dialMax));
      const overArcLen = Math.max(0, overFrac * 236.49 - normalArcLen);
      ringOver.setAttribute('stroke-dasharray', overArcLen.toFixed(2) + ' 999');
      ringOver.setAttribute('stroke-dashoffset', (-normalArcLen).toFixed(2));
      ringOver.setAttribute('stroke', 'var(--status-red)');
    } else {
      ringOver.setAttribute('stroke-dasharray', '0 999');
      ringOver.setAttribute('stroke', 'none');
    }
  }

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
    // Digital speedometer removed from the dashboard - analogue is always
    // shown regardless of each train's own "speedometer" setting. The
    // setting is still resolved and returned by /api/loco, and every bit of
    // digital-display code (the .speed-num markup, its CSS, and the digital
    // branch inside updateGaugeRing's max-tick handling) is left in place
    // untouched, so this can be flipped back to `data.speedometer === ...`
    // to restore per-train digital/analogue switching later.
    setSpeedometerMode('analogue');
    updateGaugeRing();
  } catch (e) {
    el.textContent = 'Missing Train Class';
    el.classList.add('missing');
  }
  el.style.display = 'block';
}

function startDashboard() {
  setWeatherIcon('overcast');
  setSpeedometerMode('analogue'); // set synchronously so digital never flashes before the first poll response
  pollSpeed();
  setInterval(pollSpeed, 300);
  pollDriver();
  setInterval(pollDriver, 300);
  pollAux();
  setInterval(pollAux, 5000);
  pollLoco();
  // 2s rather than 10s: this poll carries the speedometer type, dial max and
  // max speed, so a slow interval meant swapping trains left the wrong dial
  // (or the digital readout) on screen until a manual page refresh.
  setInterval(pollLoco, 2000);
}
