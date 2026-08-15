"""
Records station sightings while driving, to join the extracted timetable to
the extracted station names.

WHY THIS EXISTS
---------------
Two halves of the timetable are now on disk and cannot be joined:

  - `extracted_services` / `extracted_calls` - real arrival and departure
    times, read from the pak, but with NO station labels. Four separate
    searches confirmed the DataTrack layers carry no station identity in any
    form (run counts, evenness, the +695 platform field, and FName
    references, which found only EDirectionOfTravel enums).
  - `route_stations` - 61 real station names with platforms, read from the
    timetable index asset, but with no positions.

The live API bridges them. `DriverAid.TrackData` gives `stationName` with
`distanceToStationCM` while driving, so one run along the route produces a
name-to-position mapping. After that the join is data rather than inference.

WHAT IT DOES NOT DO
-------------------
It does not guess. A sighting is only recorded when the game actually
reports a station name, and each is stored with the time and position it was
seen at, so a bad run can be identified and discarded rather than quietly
corrupting the mapping.
"""

import threading
import time
from datetime import datetime


class DriveRecorder:
    """Polls the live journey endpoint and accumulates station sightings.

    Deliberately reuses the app's own `/api/journey` reader rather than
    opening its own connection, so recording shares the single upstream lock
    and cannot add to the connection drops that v7.44.0 was written to fix.
    """

    POLL_SECONDS = 2.0

    def __init__(self, journey_fn, log=None):
        self._journey = journey_fn
        self._log = log or (lambda *a: None)
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._route_key = None
        self._started_at = None
        self._polls = 0
        self._errors = 0
        # (station name) -> best sighting seen so far
        self._sightings = {}
        self._service_names = set()
        self._track = []          # ordered positions, for a route profile

    # -- control ---------------------------------------------------------

    def start(self, route_key):
        with self._lock:
            if self._running:
                return {"ok": False, "error": "already_recording",
                        "route_key": self._route_key}
            self._route_key = route_key
            self._started_at = datetime.now().isoformat(timespec="seconds")
            self._running = True
            self._polls = self._errors = 0
            self._sightings.clear()
            self._service_names.clear()
            self._track.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return {"ok": True, "route_key": route_key, "started_at": self._started_at}

    def stop(self):
        with self._lock:
            self._running = False
        return self.status()

    def status(self):
        with self._lock:
            return {
                "recording": self._running,
                "route_key": self._route_key,
                "started_at": self._started_at,
                "polls": self._polls,
                "errors": self._errors,
                "stations_seen": len(self._sightings),
                "service_names": sorted(self._service_names),
                "sightings": sorted(
                    self._sightings.values(),
                    key=lambda s: (s["first_seen_distance_m"] is None,
                                   -(s["first_seen_distance_m"] or 0))),
                "track_points": len(self._track),
            }

    # -- internals -------------------------------------------------------

    def _record(self, data):
        service = data.get("service_name")
        if service:
            self._service_names.add(service)

        lat, lon = data.get("latitude"), data.get("longitude")
        if lat is not None and lon is not None:
            self._track.append({"t": time.time(), "lat": lat, "lon": lon})

        for st in (data.get("stations") or []):
            name = (st.get("station_name") or "").strip()
            if not name:
                continue
            dist_cm = st.get("distance_to_station_cm")
            dist_m = round(dist_cm / 100.0, 1) if isinstance(dist_cm, (int, float)) else None
            hit = self._sightings.get(name)
            if hit is None:
                self._sightings[name] = {
                    "station_name": name,
                    "times_seen": 1,
                    "first_seen_at": datetime.now().isoformat(timespec="seconds"),
                    "first_seen_distance_m": dist_m,
                    "closest_distance_m": dist_m,
                    "platform_length": st.get("platform_length"),
                    "latitude": lat,
                    "longitude": lon,
                    "closest_at": None,
                }
                continue
            hit["times_seen"] += 1
            # Keep the CLOSEST approach and the position at that moment. A
            # station reported 4km ahead and one reported 20m away are the
            # same name at very different positions, and only the closest
            # approach locates it.
            # Compare ABSOLUTE distance. The value goes negative once the
            # station is behind the train, so a plain "<" comparison keeps
            # the most negative reading - the point furthest PAST the
            # station - and records the train's position there rather than
            # at the station. The closest approach is the smallest
            # magnitude, either side of zero.
            if dist_m is not None and (hit["closest_distance_m"] is None
                                       or abs(dist_m) < abs(hit["closest_distance_m"])):
                hit["closest_distance_m"] = dist_m
                if lat is not None:
                    hit["latitude"], hit["longitude"] = lat, lon
                    hit["closest_at"] = datetime.now().isoformat(timespec="seconds")

    def _loop(self):
        while True:
            with self._lock:
                if not self._running:
                    return
            try:
                data = self._journey()
                with self._lock:
                    self._polls += 1
                    if isinstance(data, dict) and not data.get("errors"):
                        self._record(data)
                    elif isinstance(data, dict) and data.get("stations"):
                        # Partial results are still useful - PlayerInfo drops
                        # under load far more often than TrackData does.
                        self._record(data)
                    else:
                        self._errors += 1
            except Exception as e:
                with self._lock:
                    self._errors += 1
                self._log(f"drive recorder: {e}")
            time.sleep(self.POLL_SECONDS)
