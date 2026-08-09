"""
Measures the app's behaviour against a MOCK TSW API that reproduces the
game's actual failure modes:

  - it is effectively single-threaded, and returns 502 when a second request
    arrives while one is in flight (this is the documented PlayerInfo
    behaviour under rapid polling - a dropped connection, not a missing path)
  - it drops a small fraction of requests at random even when idle
  - each request takes a few milliseconds

The measurements that matter are not "does it work" but "how often does the
UI lose a value it already had". Run before and after a change to see
whether the connection handling actually improved.
"""
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PORT = 31999
DROP_RATE = float(os.environ.get("MOCK_DROP_RATE", "0.04"))  # random drops even when idle
SERVICE_MS = 6            # per-request work

_inflight = 0
_inflight_lock = threading.Lock()
stats = {"requests": 0, "concurrent_rejects": 0, "random_drops": 0, "ok": 0,
         "subscription_posts": 0, "aggregate_reads": 0}

# How the mock game handles subscriptions:
#   "supported"  - the protocol as documented in the findings
#   "absent"     - 404, i.e. an older build that never had it
#   "odd_shape"  - the endpoint works but returns a payload we cannot index
SUBSCRIPTION_MODE = os.environ.get("MOCK_SUBSCRIPTIONS", "supported")
_subscribed = set()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        stats["subscription_posts"] += 1
        if not self.path.startswith("/subscription/"):
            self.send_response(404); self.end_headers(); return
        if SUBSCRIPTION_MODE == "absent":
            self.send_response(404); self.end_headers(); return
        _subscribed.add(self.path.split("?")[0][len("/subscription/"):])
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"Result":"Success"}')

    def do_GET(self):
        global _inflight
        with _inflight_lock:
            stats["requests"] += 1
            busy = _inflight > 0
            _inflight += 1
        try:
            if busy:
                stats["concurrent_rejects"] += 1
                self.send_response(502)
                self.end_headers()
                self.wfile.write(b'{"error":"busy"}')
                return
            time.sleep(SERVICE_MS / 1000.0)
            if random.random() < DROP_RATE:
                stats["random_drops"] += 1
                self.send_response(502)
                self.end_headers()
                self.wfile.write(b'{"error":"dropped"}')
                return
            stats["ok"] += 1
            body = self._body_for(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        finally:
            with _inflight_lock:
                _inflight -= 1

    def _body_for(self, path):
        if path.startswith("/subscription/"):
            stats["aggregate_reads"] += 1
            if SUBSCRIPTION_MODE == "absent":
                return '{"Result":"Error","Message":"no such route"}'
            if SUBSCRIPTION_MODE == "odd_shape":
                # Works, but nothing identifies which path each blob is for.
                return '{"Result":"Success","Payload":[{"blob":1},{"blob":2}]}'
            vals = {}
            for p in sorted(_subscribed):
                inner = self._body_for("/get/" + p)
                import json as _j
                vals[p] = _j.loads(inner).get("Values", {})
            import json as _j
            return _j.dumps({"Result": "Success", "Values": vals})
        if "ObjectClass" in path:
            return '{"Result":"Success","Values":{"ReturnValue":"RVM_Class158_C"}}'
        if "IS_GetVehicleInfo" in path:
            return ('{"Result":"Success","Values":{"Output":'
                    '{"class_14_ABC":"158"}}}')
        if "PlayerInfo" in path:
            return ('{"Result":"Success","Values":{"geoLocation":'
                    '{"latitude":56.1953,"longitude":-3.0022}}}')
        if "DriverAid.Data" in path:
            return ('{"Result":"Success","Values":{"formationMaxSpeed":'
                    '{"value":40.2},"gradient":0.2}}')
        if "WeatherManager" in path:
            return ('{"Result":"Success","Values":{"Temperature":11.4,'
                    '"Cloudiness":0.42,"Precipitation":0.1,"FogDensity":0.05}}')
        return '{"Result":"Success","Values":{}}'


def start_mock():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def load_app():
    import importlib.util
    spec = importlib.util.spec_from_file_location("tswapp", "app.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["tswapp"] = m
    try:
        spec.loader.exec_module(m)
    except SystemExit:
        pass
    m.CONFIG["api_base"] = f"http://127.0.0.1:{PORT}"
    m.read_api_key = lambda force=False: ("testkey", "mock")
    m.api_headers = lambda: {"DTGCommKey": "testkey"}
    return m


def run(m, seconds=8):
    """Drives the same poll pattern the dashboard and map actually use."""
    c = m.app.test_client()
    results = {"loco_name": [], "location": [], "weather": []}
    stop = time.time() + seconds

    def poll(path, key, interval, extract):
        while time.time() < stop:
            try:
                d = c.get(path).get_json()
                results[key].append(extract(d))
            except Exception:
                results[key].append(None)
            time.sleep(interval)

    threads = [
        threading.Thread(target=poll, args=("/api/loco", "loco_name", 2.0,
                                            lambda d: (d or {}).get("name"))),
        threading.Thread(target=poll, args=("/api/location", "location", 2.0,
                                            lambda d: (d or {}).get("latitude"))),
        threading.Thread(target=poll, args=("/api/proxy/get/WeatherManager.Data",
                                            "weather", 5.0,
                                            lambda d: ((d or {}).get("Values") or {}).get("Temperature"))),
        threading.Thread(target=poll, args=("/api/proxy/get/CurrentDrivableActor.Function.HUD_GetSpeed",
                                            "weather", 0.3, lambda d: None)),
        threading.Thread(target=poll, args=("/api/proxy/get/DriverAid.Data",
                                            "weather", 0.3, lambda d: None)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    def dropouts(vals):
        vals = [v for v in vals if v is not None or True]
        seen_good = False
        lost = 0
        for v in vals:
            if v is not None:
                seen_good = True
            elif seen_good:
                lost += 1
        return lost, len(vals)

    print(f"\n  upstream: {stats['requests']} requests, "
          f"{stats['concurrent_rejects']} rejected as concurrent, "
          f"{stats['random_drops']} randomly dropped")
    for key in ("loco_name", "location"):
        lost, total = dropouts(results[key])
        print(f"  {key:10} {total - lost}/{total} polls returned a value"
              f"   ({lost} dropouts after first success)")
    return results


def disable_mitigations(m):
    """Turns off everything added in v7.44.0, to measure the baseline on the
    SAME mock rather than comparing against a memory of how it felt."""
    import contextlib

    class NullLock:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False

    m._upstream_lock = NullLock()
    m.READ_CACHE_SECONDS = 0.0
    m.IDENTITY_HOLD_SECONDS = 0.0
    m.LOCATION_HOLD_SECONDS = 0.0
    _orig = m.api_get
    m.api_get = lambda path, timeout=(2, 3), retries=1, use_cache=True: _orig(
        path, timeout=timeout, retries=0, use_cache=False)
    # and restore the old double-fetch in /api/loco
    m.find_loco_class = (lambda identity=None, _f=m.find_loco_class: _f())


def compare(seconds=10, drop_rate=0.35):
    """Runs both configurations against one mock and asserts the connection
    handling is measurably better. Returns True on pass.

    Deliberately compares on the SAME mock in the SAME process rather than
    trusting a before-and-after impression, because the symptoms being fixed
    here - a value flickering out for one poll - are exactly the kind of
    thing that is easy to convince yourself has improved.
    """
    global DROP_RATE
    DROP_RATE = drop_rate
    start_mock()

    m = load_app()
    disable_mitigations(m)
    for k in stats:
        stats[k] = 0
    print("\n--- baseline (mitigations off) ---")
    base = run(m, seconds=seconds)
    base_rejects = stats["concurrent_rejects"]
    base_reqs = stats["requests"]

    for k in stats:
        stats[k] = 0
    m2 = load_app()
    print("\n--- current ---")
    cur = run(m2, seconds=seconds)
    cur_rejects = stats["concurrent_rejects"]

    def lost(vals):
        seen = False
        n = 0
        for v in vals:
            if v is not None:
                seen = True
            elif seen:
                n += 1
        return n

    ok = True
    print("\n--- verdict ---")
    print(f"  concurrent rejects: {base_rejects} -> {cur_rejects}")
    if cur_rejects > 0:
        print("  FAIL: still issuing overlapping requests to the game")
        ok = False
    if base_rejects == 0:
        print("  INCONCLUSIVE: baseline saw no contention, raise the load")
    for key in ("loco_name", "location"):
        b, c = lost(base[key]), lost(cur[key])
        print(f"  {key} dropouts: {b} -> {c}")
        if c > b:
            print(f"  FAIL: {key} got worse")
            ok = False
    print("\n" + ("PASS" if ok else "FAIL"))
    return ok


def subscription_matrix(seconds=6):
    """Checks the subscription client in all three worlds it might wake up
    in. The fallback cases matter more than the happy one: the protocol has
    never been run against a real game, so the app must stay correct when it
    turns out not to work as documented.
    """
    global SUBSCRIPTION_MODE
    import json
    ok = True
    print("\n--- subscription behaviour ---")
    expected = {"supported": "active", "absent": "unsupported",
                "odd_shape": "unknown_shape"}
    for mode, want in expected.items():
        SUBSCRIPTION_MODE = mode
        _subscribed.clear()
        for k in stats:
            stats[k] = 0
        m = load_app()
        m._start_subscriptions()
        deadline = time.time() + seconds
        while time.time() < deadline:
            time.sleep(0.5)
            if m.SUBSCRIPTIONS.status()["state"] == want:
                break
        c = m.app.test_client()
        st = m.SUBSCRIPTIONS.status()
        before = stats["requests"]
        for _ in range(20):
            c.get("/api/proxy/get/DriverAid.Data")
            c.get("/api/proxy/get/CurrentDrivableActor.Function.HUD_GetSpeed")
        cost = stats["requests"] - before
        name = json.loads(c.get("/api/loco").data).get("name")
        print(f"  {mode:10} state={st['state']:14} 40 reads cost {cost:3} upstream"
              f"   loco={name}")
        if st["state"] != want:
            print(f"  FAIL: expected {want}"); ok = False
        # Whatever happens to subscriptions, the app must still answer.
        if not name:
            print("  FAIL: app stopped resolving the train"); ok = False
        if mode == "supported" and cost > 2:
            print("  FAIL: subscriptions active but reads still hitting the game")
            ok = False
        if mode != "supported" and cost == 0:
            print("  FAIL: fell back but made no requests - stale data?"); ok = False
    print("PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    if "--compare" in sys.argv:
        sys.exit(0 if compare() else 1)
    if "--subscriptions" in sys.argv:
        start_mock()
        sys.exit(0 if subscription_matrix() else 1)
    start_mock()
    m = load_app()
    if "--baseline" in sys.argv:
        disable_mitigations(m)
        print("BASELINE (mitigations disabled)")
    else:
        print("CURRENT")
    secs = 8.0
    for a in sys.argv[1:]:
        try:
            secs = float(a)
        except ValueError:
            pass
    run(m, seconds=secs)


