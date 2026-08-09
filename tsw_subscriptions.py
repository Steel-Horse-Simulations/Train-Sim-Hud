"""
TSW subscription API client.

WHAT THIS IS FOR
----------------
The app currently polls. Even after v7.44.0 made the polling well-behaved -
serialised, retried, coalesced - it is still several HTTP round trips per
second to a game engine's built-in web server, and that server drops
connections when it is busy. The subscription API replaces N polls per tick
with ONE: register the paths once, then read them all back in a single GET.

THE HONEST CAVEAT
-----------------
The exact protocol is NOT confirmed. It is recorded in
TIMETABLE_EXTRACTION_FINDINGS.md as:

    POST /subscription/<path>?Subscription=1, then one GET /subscription/

and marked "not yet implemented". Nobody has run it. In particular the shape
of the aggregate GET response is a guess.

So this module is written to EARN its place rather than assume it:

  - it probes on startup and verifies that a path it subscribed to actually
    comes back in the aggregate read;
  - if anything about that fails - endpoint missing, unexpected shape, values
    never appearing - it disables itself and the app falls back to the
    polling path, which is known to work;
  - when the shape is unrecognised it CAPTURES a sample of the raw payload so
    the real format can be read off a real run rather than guessed at again.

Failing back to something that works is the whole design. An unverified
protocol must never be able to break a working app.
"""

import threading
import time


# Paths worth subscribing to: the ones polled fastest, or polled by more than
# one page at once.
DEFAULT_PATHS = [
    "CurrentDrivableActor.Function.HUD_GetSpeed",
    "DriverAid.Data",
    "DriverAid.PlayerInfo",
    "DriverAid.TrackData",
    "WeatherManager.Data",
    "CurrentDrivableActor.ObjectClass",
    "CurrentFormation/0.Function.IS_GetVehicleInfo",
]

# How stale a subscribed value may be before callers are sent to the live
# path instead. Generous enough to cover a missed tick, short enough that
# nothing visibly lags.
MAX_VALUE_AGE = 1.5

POLL_INTERVAL = 0.25
RETRY_AFTER_FAILURE = 300.0     # re-probe support every 5 minutes
VERIFY_ATTEMPTS = 8             # aggregate reads allowed before giving up


def _looks_like_value(obj):
    """A subscription entry should carry something value-shaped."""
    return isinstance(obj, (dict, list, str, int, float))


class SubscriptionClient:
    """Owns the subscription lifecycle and the value cache.

    All HTTP goes through the callables handed in by app.py, so subscription
    traffic shares the same session, the same upstream lock and the same
    logging as everything else. This class does not open its own connections.
    """

    def __init__(self, get_fn, post_fn, paths=None, log=None):
        self._get = get_fn
        self._post = post_fn
        self._paths = list(paths or DEFAULT_PATHS)
        self._log = log or (lambda *a: None)

        self._lock = threading.Lock()
        self._values = {}            # path -> (timestamp, body)
        self._subscribed = set()
        self._state = "starting"     # starting | active | unsupported | unknown_shape | disabled
        self._detail = ""
        self._sample = None          # raw payload sample when the shape is unrecognised
        self._verified = False
        self._attempts = 0
        self._last_failure = 0.0
        self._stats = {"aggregate_reads": 0, "cache_hits": 0, "misses": 0,
                       "paths_seen": 0, "errors": 0}
        self._thread = None

    # -- public -----------------------------------------------------------

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def get(self, path):
        """A subscribed value, or None to say 'use the live path'.

        Returning None rather than an error is deliberate: this is an
        optimisation, and a miss must be indistinguishable from the
        subscription layer not existing at all.
        """
        if self._state != "active" or not self._verified:
            return None
        with self._lock:
            hit = self._values.get(path)
            if not hit:
                self._stats["misses"] += 1
                return None
            ts, body = hit
            if (time.time() - ts) > MAX_VALUE_AGE:
                self._stats["misses"] += 1
                return None
            self._stats["cache_hits"] += 1
            return body

    def status(self):
        with self._lock:
            return {
                "state": self._state,
                "detail": self._detail,
                "verified": self._verified,
                "subscribed": sorted(self._subscribed),
                "cached_paths": sorted(self._values.keys()),
                "stats": dict(self._stats),
                # Present only when the payload shape was not recognised -
                # this is the thing to send back so the real format can be
                # supported rather than guessed at.
                "unrecognised_payload_sample": self._sample,
            }

    # -- internals --------------------------------------------------------

    def _set_state(self, state, detail=""):
        with self._lock:
            self._state = state
            self._detail = detail
        self._log(f"subscriptions: {state} - {detail}")

    def _subscribe_all(self):
        ok = 0
        for path in self._paths:
            body, status = self._post(f"subscription/{path}", {"Subscription": 1})
            if status == 200:
                with self._lock:
                    self._subscribed.add(path)
                ok += 1
            elif status in (404, 405, 501):
                # The endpoint itself is not there. No amount of retrying
                # fixes that, so stop rather than hammering it.
                return -1
        return ok

    def _index(self, payload):
        """Maps an aggregate response onto {path: body}.

        Several plausible shapes are accepted because the real one is not
        known. Whichever matches, the result must contain a path that was
        actually subscribed - that is what _verify checks - so a wrong guess
        here shows up as a failed verification rather than as silently wrong
        data reaching the HUD.
        """
        out = {}
        if not isinstance(payload, dict):
            return out

        # Shape A: {"Values": {"<path>": {...}}}
        values = payload.get("Values")
        if isinstance(values, dict):
            for k, v in values.items():
                if isinstance(k, str) and _looks_like_value(v):
                    out[k] = {"Result": "Success", "Values": v} if not (
                        isinstance(v, dict) and "Values" in v) else v

        # Shape B: {"Subscriptions": [{"Path": "...", "Values": {...}}, ...]}
        for key in ("Subscriptions", "subscriptions", "Results", "Nodes"):
            entries = payload.get(key)
            if isinstance(entries, list):
                for e in entries:
                    if not isinstance(e, dict):
                        continue
                    p = e.get("Path") or e.get("path") or e.get("Name") or e.get("name")
                    if isinstance(p, str):
                        body = e.get("Values", e.get("values", e))
                        out[p] = {"Result": "Success", "Values": body}

        # Shape C: the paths sit at the top level
        for k, v in payload.items():
            if k in ("Result", "Values", "Message"):
                continue
            if isinstance(k, str) and ("." in k or "/" in k) and _looks_like_value(v):
                out.setdefault(k, {"Result": "Success", "Values": v})

        return out

    def _verify(self, indexed):
        """A subscription layer is only trusted once a path it registered
        has actually come back through it."""
        with self._lock:
            wanted = set(self._subscribed)
        return bool(wanted & set(indexed.keys()))

    def _tick(self):
        body, status = self._get("subscription/")
        self._stats["aggregate_reads"] += 1
        if status != 200 or not isinstance(body, dict):
            self._stats["errors"] += 1
            return False

        indexed = self._index(body)
        if not self._verified:
            self._attempts += 1
            if self._verify(indexed):
                self._verified = True
                self._set_state("active",
                                f"{len(indexed)} paths returned by one read")
            elif self._attempts >= VERIFY_ATTEMPTS:
                # Keep a sample so the real shape can be read off a real run.
                with self._lock:
                    self._sample = str(body)[:2000]
                self._set_state(
                    "unknown_shape",
                    "the aggregate read works but none of the subscribed "
                    "paths could be found in it - falling back to polling. "
                    "See unrecognised_payload_sample in /api/subscriptions.")
                return False
            else:
                return True

        now = time.time()
        with self._lock:
            for path, value in indexed.items():
                self._values[path] = (now, value)
            self._stats["paths_seen"] = len(self._values)
        return True

    def _loop(self):
        while True:
            if self._state in ("unsupported", "unknown_shape", "disabled"):
                # Periodically re-probe: the game may not have been running
                # when the first attempt was made.
                if (time.time() - self._last_failure) < RETRY_AFTER_FAILURE:
                    time.sleep(5)
                    continue
                self._verified = False
                self._attempts = 0
                with self._lock:
                    self._subscribed.clear()
                self._set_state("starting", "re-probing subscription support")

            if not self._subscribed:
                ok = self._subscribe_all()
                if ok < 0:
                    self._last_failure = time.time()
                    self._set_state("unsupported",
                                    "the game did not accept POST /subscription/<path> - "
                                    "polling instead")
                    continue
                if ok == 0:
                    # Nothing subscribed, most likely no active session yet.
                    time.sleep(2)
                    continue

            if not self._tick():
                if self._state == "unknown_shape":
                    self._last_failure = time.time()
                    continue
                # A failed aggregate read is not fatal on its own; the game
                # may simply be between sessions. Values age out and callers
                # fall back on their own.
                time.sleep(1)
                continue

            time.sleep(POLL_INTERVAL)
