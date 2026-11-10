"""directions() and place(): a route or a place with a small map (ROADMAP M5b), keyless.

- Places: OpenStreetMap's Nominatim (https://nominatim.org). Its usage policy: a real
  User-Agent naming the app, at most one request a second, results cached. Lookups are
  remembered in state/places/geocode.json (places don't move), so asking again never
  hits the network.
- Routes: OSRM on the FOSSGIS servers openstreetmap.org itself uses
  (routing.openstreetmap.de: car, foot and bike profiles), falling back to the OSRM
  demo server for driving.
- The map: the few OpenStreetMap tiles that cover the route (tile.openstreetmap.org,
  at most 12 per map, cached under state/tiles/ for 30 days, never prefetched), darkened
  to the island's palette, with a faint ghost of the route, saved as a PNG under
  state/maps/. The result carries its path and the route and pins in the image's own
  0..1 coordinates, so the card (widgets/directions) draws the line on and drops the pins.

"From here" / an empty origin is the home place: `info.home` (a place name or
"lat, lon"), else `info.weather_location` (weather.location passed through).
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from heyclaude import mcp_info

PROMPT = ("directions(origin, destination, mode) gives travel time, distance and a map (\"how long to walk to the "
          "Louvre\", \"directions from the station to the airport\"; leave origin empty for \"from here\"); "
          "place(query) finds a place and shows it on a map (\"where is the Brandenburg Gate\").")

USER_AGENT = "hey-claude/0.5 (voice assistant; +https://github.com/kamol1dn/hey-claude)"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
ROUTERS = {
    "driving": ["https://routing.openstreetmap.de/routed-car/route/v1/driving/",
                "https://router.project-osrm.org/route/v1/driving/"],
    "walking": ["https://routing.openstreetmap.de/routed-foot/route/v1/foot/"],
    "cycling": ["https://routing.openstreetmap.de/routed-bike/route/v1/bike/"],
}
MODES = {"driving": "driving", "drive": "driving", "car": "driving", "walking": "walking", "walk": "walking",
         "foot": "walking", "cycling": "cycling", "cycle": "cycling", "bike": "cycling", "bicycle": "cycling"}
HERE = {"", "here", "home", "my place", "current location", "my location", "from here"}
MAP_W, MAP_H = 612, 228          # px (1.5x the card's 408 x 152 map box)
PAD = 30                         # px kept around the route
MAX_TILES = 12
TILE_TTL_S = 30 * 86400
KEEP_MAPS = 40
ATTRIBUTION = "© OpenStreetMap"


def _state() -> Path:
    from heyclaude.config import STATE_DIR
    return STATE_DIR


# ---------------------------------------------------------------- network
_lock = threading.Lock()
_last_nominatim = [0.0]


def _http(url: str, timeout: float = 10.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _fetch_json(url: str, params: dict, http=None) -> object:
    raw = (http or _http)(url + ("?" + urllib.parse.urlencode(params) if params else ""))
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------- places
_geo_mem: dict[str, dict] = {}


def _geo_file() -> Path:
    return _state() / "places" / "geocode.json"


def _geo_cache() -> dict:
    if not _geo_mem:
        try:
            _geo_mem.update(json.loads(_geo_file().read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return _geo_mem


def _geo_save():
    try:
        f = _geo_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        items = list(_geo_mem.items())[-500:]
        f.write_text(json.dumps(dict(items), ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


_LATLON = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


def home() -> str:
    c = mcp_info.config()
    return str(c.get("home") or c.get("weather_location") or "").strip()


def _short(display: str, name: str) -> str:
    """Nominatim's long display_name -> the place's name, else its first part."""
    return (name or display.split(",")[0]).strip()


def geocode(query: str, http=None, sleep=time.sleep) -> dict:
    """{"name", "address", "lat", "lon", "kind"} for a place name or "lat, lon"."""
    q = re.sub(r"\s+", " ", str(query or "")).strip()
    if q.lower() in HERE:
        q = home()
        if not q:
            raise ValueError("no home place set (info.home, or weather.location in Settings); say where from")
    m = _LATLON.match(q)
    if m:
        lat, lon = float(m[1]), float(m[2])
        if not (-85 <= lat <= 85 and -180 <= lon <= 180):
            raise ValueError(f"{q!r} isn't a valid latitude, longitude")
        return {"name": f"{lat:.4f}, {lon:.4f}", "address": "", "lat": lat, "lon": lon, "kind": "point"}
    key = q.lower()
    cache = _geo_cache()
    if key in cache:
        return cache[key]
    with _lock:                                          # Nominatim: one request a second, at most
        wait = 1.05 - (time.monotonic() - _last_nominatim[0])
        if wait > 0:
            sleep(wait)
        try:
            res = _fetch_json(NOMINATIM, {"q": q, "format": "jsonv2", "limit": 1, "addressdetails": 0}, http)
        finally:
            _last_nominatim[0] = time.monotonic()
    if not isinstance(res, list) or not res:
        raise LookupError(f"couldn't find a place called {q!r}")
    r = res[0]
    display = str(r.get("display_name") or q)
    out = {"name": _short(display, str(r.get("name") or "")), "address": display,
           "lat": round(float(r["lat"]), 6), "lon": round(float(r["lon"]), 6),
           "kind": str(r.get("type") or r.get("category") or "").replace("_", " ")}
    cache[key] = out
    _geo_save()
    return out


# ---------------------------------------------------------------- routes
def _route(a: dict, b: dict, mode: str, http=None) -> dict:
    coords = f"{a['lon']},{a['lat']};{b['lon']},{b['lat']}"
    err = None
    for base in ROUTERS[mode]:
        try:
            r = _fetch_json(base + coords, {"overview": "full", "geometries": "geojson", "steps": "false"}, http)
        except (OSError, ValueError) as e:
            err = e
            continue
        if isinstance(r, dict) and r.get("code") == "Ok" and r.get("routes"):
            rt = r["routes"][0]
            return {"duration_s": float(rt["duration"]), "distance_m": float(rt["distance"]),
                    "line": [(float(lon), float(lat)) for lon, lat in rt["geometry"]["coordinates"]]}
        err = LookupError(str((r or {}).get("message") or (r or {}).get("code") or "no route"))
    raise LookupError(f"no {mode} route found ({err})")


# ---------------------------------------------------------------- map
def _px(lon: float, lat: float, z: int) -> tuple[float, float]:
    """Web Mercator pixel coordinates at zoom z (256 px tiles)."""
    n = 256 * 2 ** z
    lat = max(-85.0511, min(85.0511, lat))
    s = math.sin(math.radians(lat))
    return (lon + 180) / 360 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def _zoom(points, w: int, h: int, zmax: int = 17) -> int:
    for z in range(zmax, 1, -1):
        xs, ys = zip(*(_px(lon, lat, z) for lon, lat in points))
        if max(xs) - min(xs) <= w - 2 * PAD and max(ys) - min(ys) <= h - 2 * PAD:
            return z
    return 2


def _tile(z: int, x: int, y: int, http=None) -> bytes | None:
    n = 2 ** z
    x %= n
    if not 0 <= y < n:
        return None
    f = _state() / "tiles" / str(z) / str(x) / f"{y}.png"
    try:
        if f.is_file() and time.time() - f.stat().st_mtime < TILE_TTL_S:
            return f.read_bytes()
    except OSError:
        pass
    try:
        data = (http or _http)(TILE_URL.format(z=z, x=x, y=y))
    except OSError:
        return f.read_bytes() if f.is_file() else None          # a stale tile beats none
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    except OSError:
        pass
    return data


def _simplify(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker, iterative."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (x1, y1), (x2, y2) = pts[i], pts[j]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy) or 1e-9
        best, at = 0.0, -1
        for k in range(i + 1, j):
            d = abs(dy * (pts[k][0] - x1) - dx * (pts[k][1] - y1)) / L
            if d > best:
                best, at = d, k
        if best > tol and at > 0:
            keep[at] = True
            stack += [(i, at), (at, j)]
    return [p for p, k in zip(pts, keep) if k]


def _dark(img):
    """OSM's light tiles -> the island's near-black: invert the lightness, keep a warm tint."""
    from PIL import Image, ImageOps
    g = ImageOps.grayscale(img.convert("RGB"))
    g = ImageOps.invert(g)
    g = g.point(lambda v: int(14 + (max(0, v - 18) / 237) ** 1.35 * 120))
    warm = Image.merge("RGB", (g.point(lambda v: min(255, int(v * 1.06))), g, g.point(lambda v: int(v * 0.9))))
    return warm


def render_map(points: list[tuple[float, float]], route: list[tuple[float, float]] | None, http=None) -> dict:
    """The map PNG for some (lon, lat) points (and a route line): {"path", "route", "pins", "zoom"}.
    route and pins are in the image's own 0..1 coordinates."""
    from PIL import Image, ImageDraw
    pts = list(route or []) + list(points)
    if len(pts) == 1:
        z = 15
    else:
        z = _zoom(pts, MAP_W, MAP_H)
    xs, ys = zip(*(_px(lon, lat, z) for lon, lat in pts))
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    x0, y0 = cx - MAP_W / 2, cy - MAP_H / 2
    tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int((x0 + MAP_W) // 256), int((y0 + MAP_H) // 256)
    tiles = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
    if len(tiles) > MAX_TILES:
        raise ValueError("map too large")
    base = Image.new("RGB", (MAP_W, MAP_H), (17, 17, 17))
    got = 0
    for tx, ty in tiles:
        data = _tile(z, tx, ty, http)
        if not data:
            continue
        try:
            t = Image.open(io.BytesIO(data))
            base.paste(_dark(t), (int(tx * 256 - x0), int(ty * 256 - y0)))
            got += 1
        except (OSError, ValueError):
            continue
    def rel(lon, lat):
        px, py = _px(lon, lat, z)
        return (px - x0) / MAP_W, (py - y0) / MAP_H
    line = []
    if route:
        line = _simplify([((px - x0), (py - y0)) for px, py in (_px(lon, lat, z) for lon, lat in route)], 0.9)
        if len(line) > 160:
            line = _simplify(line, 2.5)
        # a faint ghost of the route under the line the card draws on
        over = Image.new("RGBA", base.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        d.line(line, fill=(0, 0, 0, 150), width=9, joint="curve")
        d.line(line, fill=(217, 119, 87, 70), width=4, joint="curve")
        base = Image.alpha_composite(base.convert("RGBA"), over).convert("RGB")
    out_dir = _state() / "maps"
    out_dir.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(json.dumps([z, round(x0), round(y0), line[:3], len(line)]).encode()).hexdigest()[:12]
    f = out_dir / f"map-{h}.png"
    base.save(f, "PNG", optimize=True)
    for old in sorted(out_dir.glob("map-*.png"), key=lambda p: p.stat().st_mtime)[:-KEEP_MAPS]:
        try:
            old.unlink()
        except OSError:
            pass
    return {"path": str(f), "zoom": z, "tiles": got,
            "route": [[round(x / MAP_W, 4), round(y / MAP_H, 4)] for x, y in line],
            "pins": [[round(v, 4) for v in rel(lon, lat)] for lon, lat in points]}


# ---------------------------------------------------------------- formatting
def fmt_duration(s: float) -> str:
    m = max(1, int(round(s / 60)))
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min" if m else f"{h} h"


def fmt_distance(m: float, units: str = "metric") -> str:
    if units == "imperial":
        mi = m / 1609.344
        return f"{mi:.1f} mi" if mi >= 0.2 else f"{int(round(m * 3.28084 / 10) * 10)} ft"
    if m < 1000:
        return f"{int(round(m / 10) * 10)} m"
    return f"{m / 1000:.1f} km" if m < 100_000 else f"{m / 1000:.0f} km"


# ---------------------------------------------------------------- tools
def directions(origin: str = "", destination: str = "", mode: str = "driving") -> dict:
    """Travel time, distance and a small route map between two places (OpenStreetMap data).

    origin: a place name, address or "lat, lon"; empty or "here" = the user's home place.
    destination: a place name, address or "lat, lon".
    mode: "driving", "walking" or "cycling".
    The island shows the route card from this result by itself: just say the time and distance."""
    if not str(destination or "").strip():
        raise ValueError("say where to")
    md = MODES.get(str(mode or "driving").strip().lower())
    if md is None:
        raise ValueError("mode must be driving, walking or cycling")
    a, b = geocode(origin), geocode(destination)
    r = _route(a, b, md)
    units = str(mcp_info.config().get("units") or "metric").lower()
    out = {"mode": md, "from": a["name"], "to": b["name"], "from_address": a["address"], "to_address": b["address"],
           "duration_s": round(r["duration_s"]), "distance_m": round(r["distance_m"]),
           "duration": fmt_duration(r["duration_s"]), "distance": fmt_distance(r["distance_m"], units),
           "source": "OSRM · OpenStreetMap", "attribution": ATTRIBUTION}
    try:
        m = render_map([(a["lon"], a["lat"]), (b["lon"], b["lat"])], r["line"])
        out.update(map=m["path"], route=m["route"], pins=m["pins"])
    except (OSError, ValueError, ImportError) as e:      # no map is still an answer
        out["map_error"] = str(e)
    return out


def place(query: str) -> dict:
    """Find a place (a landmark, an address, a business) and show it on a small map (OpenStreetMap data).

    query: what to look for, e.g. "Brandenburg Gate" or "Amir Timur Square, Tashkent".
    The island shows the place card from this result by itself: just say what and where it is."""
    if not str(query or "").strip():
        raise ValueError("say which place")
    p = geocode(query)
    out = {"name": p["name"], "address": p["address"], "kind": p["kind"], "lat": p["lat"], "lon": p["lon"],
           "source": "Nominatim · OpenStreetMap", "attribution": ATTRIBUTION}
    try:
        m = render_map([(p["lon"], p["lat"])], None)
        out.update(map=m["path"], pins=m["pins"])
    except (OSError, ValueError, ImportError) as e:
        out["map_error"] = str(e)
    return out


TOOLS = [directions, place]
