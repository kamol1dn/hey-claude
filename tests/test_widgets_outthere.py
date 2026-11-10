"""Offline tests for the M5b "out there" widget packs: directions / place (hey-claude's own
places tool: Nominatim, OSRM, OSM tiles), news / sports headlines (Claude Code's WebSearch
result) and the email digest (the claude.ai Gmail connector). Each pack's example
validates, its adapter turns the recorded tool result into the card's data, bad data
falls back to text, and the places tool works on canned answers (no network).

    .venv\\Scripts\\python tests\\test_widgets_outthere.py
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heyclaude import widgets as W  # noqa: E402
from heyclaude.effects import parse_result, result_text  # noqa: E402
from heyclaude.mcp_info import places as P  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "tool_results"
PACKS = ("directions", "news", "email")


def _adapter(wid):
    spec = importlib.util.spec_from_file_location(f"outthere_{wid}", ROOT / "widgets" / wid / "adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fx(name):
    fx = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return fx, parse_result(fx["content"]), result_text(fx["content"])


# ------------------------------------------------------------------ the packs
def test_examples_validate_and_bad_data_falls_back_to_text():
    reg = W.registry()
    for wid in PACKS:
        man = reg[wid]
        spec = W.normalise({"widget": wid, "data": man["example"], "text": "fallback"})
        assert spec["widget"] == wid, (wid, spec)
        assert (man.get("triggers") or {}).get("data") == "adapter", wid
        assert W.title_for(man, man["example"]) and "{" not in W.title_for(man, man["example"]), wid
    assert reg["email"]["private"] is True and reg["email"]["icon"] == "gmail"
    bad = {"directions": {"kind": "route"}, "news": {"title": "News · x", "headlines": [{"title": "t", "link": "javascript:x"}]},
           "email": {"title": "Inbox", "unread": -1, "senders": []}}
    for wid, data in bad.items():
        out = W.normalise({"widget": wid, "data": data, "text": "said instead"})
        assert out["widget"] == "text" and out["data"]["text"] == "said instead", (wid, out)
    # a map must be one of ours (state/maps/map-<hash>.png) or the bundled sample
    ex = dict(reg["directions"]["example"], map="file:///C:/Users/someone/secret.png")
    assert W.normalise({"widget": "directions", "data": ex, "text": "t"})["widget"] == "text"


def test_every_fixture_becomes_its_card():
    for f in ("info_directions.json", "info_place.json", "websearch_news.json", "websearch_sports.json",
              "gmail_search_threads.json"):
        fx, res, raw = _fx(f)
        spec = W.from_tool(fx["tool"], fx["args"], res, raw)
        assert spec and spec["widget"] == fx["expect"]["widget"], (f, spec)
        man = W.registry()[spec["widget"]]
        assert W.title_for(man, spec["data"]) == fx["expect"]["title"], (f, W.title_for(man, spec["data"]))
        assert bool(man.get("icon") or man.get("glyph")) == fx["expect"]["icon"], f


# ------------------------------------------------------------------ directions
def test_directions_adapter_maps_the_recorded_route():
    fx, res, raw = _fx("info_directions.json")
    d = _adapter("directions").adapt(fx["args"], res, raw, now=datetime(2026, 11, 10, 14, 30))
    assert d["kind"] == "route" and d["title"] == "Directions · Louvre Museum", d
    assert (d["origin"], d["dest"], d["mode"], d["how"]) == ("Eiffel Tower", "Louvre Museum", "walking", "on foot"), d
    assert (d["duration"], d["distance"], d["arrive"]) == ("50 min", "3.8 km", "15:20"), d
    assert len(d["route"]) == 42 and len(d["pins"]) == 2 and d["attribution"] == "© OpenStreetMap"
    assert all(0 <= v <= 1 for p in d["route"] for v in p)
    # the map shows only if that png really is under state/maps (here it isn't in the repo, or it is locally)
    assert "map" not in d or d["map"].startswith("file:///") and "/state/maps/map-" in d["map"]


def test_place_adapter_and_short_address():
    fx, res, raw = _fx("info_place.json")
    A = _adapter("directions")
    d = A.adapt(fx["args"], res, raw)
    assert d["kind"] == "place" and d["title"] == "Place · Brandenburg Gate" and d["place_kind"] == "gate", d
    assert d["address"] == "1 Pariser Platz, Friedrich-Wilhelm-Stadt, Berlin, Germany", d["address"]
    assert d["pins"] == [[0.5, 0.5]]
    assert A.short_address("Louvre Museum, Galerie Véro-Dodat, Quartier Les Halles, 1st Arrondissement, Paris, "
                           "Ile-de-France, Metropolitan France, 75001, France", "Louvre Museum") == \
        "Galerie Véro-Dodat, Quartier Les Halles, Ile-de-France, France"
    assert A.adapt({}, {"error": "x"}, "") is None and A.adapt({}, None, "not json") is None


def test_map_url_only_takes_our_own_maps():
    A = _adapter("directions")
    with tempfile.TemporaryDirectory() as tmp:
        st = Path(tmp) / "state"
        (st / "maps").mkdir(parents=True)
        good = st / "maps" / "map-0123abcd.png"
        good.write_bytes(b"\x89PNG")
        (st / "other.png").write_bytes(b"\x89PNG")
        (st / "maps" / "notes.png").write_bytes(b"\x89PNG")
        with mock.patch("heyclaude.config.STATE_DIR", st), mock.patch("heyclaude.config.ROOT", Path(tmp)):
            assert A.map_url(str(good)).startswith("file:///") and A.map_url(str(good)).endswith("/state/maps/map-0123abcd.png")
            assert A.map_url("state/maps/map-0123abcd.png") == good.resolve().as_uri()     # relative to the repo
            assert A.map_url(str(st / "other.png")) == ""
            assert A.map_url(str(st / "maps" / "notes.png")) == ""
            assert A.map_url(str(st / "maps" / "map-ffff.png")) == ""                     # not there
            assert A.map_url("state/maps/../other.png") == ""
            assert A.map_url(None) == "" and A.map_url("") == ""


# ------------------------------------------------------------------ the places tool, offline
def _png(colour=(240, 238, 230)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (256, 256), colour).save(b, "PNG")
    return b.getvalue()


class FakeNet:
    """Canned Nominatim, OSRM and tile answers; records every URL asked for."""

    def __init__(self):
        self.urls = []
        self.tile = _png()

    def __call__(self, url, timeout=10.0):
        self.urls.append(url)
        if "nominatim" in url:
            if "Nowhere" in url:
                return b"[]"
            if "Louvre" in url:
                return json.dumps([{"lat": "48.8611", "lon": "2.3358", "name": "Louvre Museum", "type": "museum",
                                    "display_name": "Louvre Museum, Paris, France"}]).encode()
            return json.dumps([{"lat": "48.8584", "lon": "2.2945", "name": "Eiffel Tower", "type": "attraction",
                                "display_name": "Eiffel Tower, Paris, France"}]).encode()
        if "/route/v1/" in url:
            line = [[2.2945, 48.8584], [2.3000, 48.8620], [2.3200, 48.8610], [2.3358, 48.8611]]
            return json.dumps({"code": "Ok", "routes": [{"duration": 3024.0, "distance": 3776.0,
                                                         "geometry": {"coordinates": line}}]}).encode()
        if "tile.openstreetmap.org" in url:
            return self.tile
        raise OSError("unexpected " + url)


def _tmp_state(tmp):
    """A throwaway state/ folder (named like the real one: the card only takes maps under .../state/maps/)."""
    P._geo_mem.clear()
    return mock.patch("heyclaude.config.STATE_DIR", Path(tmp) / "state")


def test_geocode_is_polite_and_cached():
    net, waits = FakeNet(), []
    with tempfile.TemporaryDirectory() as tmp, _tmp_state(tmp):
        P._last_nominatim[0] = 0.0
        a = P.geocode("Eiffel Tower", http=net, sleep=waits.append)
        b = P.geocode("Louvre Museum", http=net, sleep=waits.append)    # right after: waits ~1 s
        assert a["name"] == "Eiffel Tower" and a["kind"] == "attraction" and b["name"] == "Louvre Museum", (a, b)
        assert waits and 0.9 < waits[-1] <= 1.05, waits
        n = len(net.urls)
        assert P.geocode("  eiffel   tower ", http=net) == a and len(net.urls) == n      # cached, no request
        P._geo_mem.clear()
        assert P.geocode("Eiffel Tower", http=net) == a and len(net.urls) == n          # from state/places/geocode.json
        assert (Path(tmp) / "state" / "places" / "geocode.json").is_file()
        assert P.geocode("41.31, 69.28")["lat"] == 41.31 and len(net.urls) == n          # coordinates: no lookup
        try:
            P.geocode("Nowhere at all", http=net, sleep=lambda s: None)
            raise AssertionError("expected LookupError")
        except LookupError:
            pass
        assert all("hey-claude" in P.USER_AGENT for _ in [0])


def test_home_place_for_from_here():
    net = FakeNet()
    with tempfile.TemporaryDirectory() as tmp, _tmp_state(tmp):
        with mock.patch.dict(os.environ, {"HEYCLAUDE_INFO_CONFIG": json.dumps({"weather_location": "Eiffel Tower"})}):
            assert P.geocode("here", http=net, sleep=lambda s: None)["name"] == "Eiffel Tower"
        with mock.patch.dict(os.environ, {"HEYCLAUDE_INFO_CONFIG": json.dumps({"home": "48.85, 2.35", "weather_location": "x"})}):
            assert P.geocode("")["lat"] == 48.85
        with mock.patch.dict(os.environ, {"HEYCLAUDE_INFO_CONFIG": "{}"}):
            try:
                P.geocode("from here")
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "home" in str(e)


def test_directions_and_place_end_to_end_on_canned_answers():
    from PIL import Image
    net = FakeNet()
    with tempfile.TemporaryDirectory() as tmp, _tmp_state(tmp), mock.patch.object(P, "_http", net), \
            mock.patch.object(P.time, "sleep", lambda s: None), \
            mock.patch.dict(os.environ, {"HEYCLAUDE_INFO_CONFIG": "{}"}):
        d = P.directions("Eiffel Tower", "Louvre Museum", "walk")
        assert d["mode"] == "walking" and d["duration"] == "50 min" and d["distance"] == "3.8 km", d
        assert any("routed-foot" in u for u in net.urls), net.urls
        tiles = [u for u in net.urls if "tile.openstreetmap.org" in u]
        assert 1 <= len(tiles) <= P.MAX_TILES, tiles
        png = Path(d["map"])
        assert png.is_file() and png.parent == Path(tmp) / "state" / "maps" and png.name.startswith("map-")
        im = Image.open(png)
        assert im.size == (P.MAP_W, P.MAP_H)
        assert im.convert("L").getextrema()[1] < 200                       # darkened to the island's palette
        assert len(d["route"]) >= 2 and len(d["pins"]) == 2
        assert all(0 <= v <= 1 for p in d["route"] + d["pins"] for v in p), d
        # the tiles are cached on disk: the same map again asks for no tile
        n = len(tiles)
        P.directions("Eiffel Tower", "Louvre Museum", "walking")
        assert len([u for u in net.urls if "tile.openstreetmap.org" in u]) == n
        assert list((Path(tmp) / "state" / "tiles").rglob("*.png"))
        # the card's data from it
        card = _adapter("directions").adapt({}, d, "")
        assert card["map"].startswith("file:///") and W.normalise({"widget": "directions", "data": card})["widget"] == "directions"
        p = P.place("Louvre")
        assert p["name"] == "Louvre Museum" and p["kind"] == "museum" and p["pins"] == [[0.5, 0.5]], p
        for bad in ({"destination": ""}, {"destination": "Louvre", "mode": "teleport"}):
            try:
                P.directions(**bad)
                raise AssertionError(bad)
            except ValueError:
                pass


def test_no_map_is_still_an_answer():
    net = FakeNet()

    def no_tiles(url, timeout=10.0):
        if "tile." in url:
            raise OSError("offline")
        return net(url)
    with tempfile.TemporaryDirectory() as tmp, _tmp_state(tmp), mock.patch.object(P, "_http", no_tiles), \
            mock.patch.object(P.time, "sleep", lambda s: None):
        d = P.directions("Eiffel Tower", "Louvre Museum", "driving")
        assert d["duration"] == "50 min" and d["map"]           # a plain dark map with the ghost route
    assert P.fmt_duration(30) == "1 min" and P.fmt_duration(3600) == "1 h" and P.fmt_duration(3900) == "1 h 05 min"
    assert P.fmt_distance(850) == "850 m" and P.fmt_distance(12345) == "12.3 km" and P.fmt_distance(1609.344 * 3, "imperial") == "3.0 mi"


# ------------------------------------------------------------------ news
def test_news_adapter_on_the_recorded_searches():
    N = _adapter("news")
    fx, res, raw = _fx("websearch_sports.json")
    assert res is None and raw.startswith("Web search results for query:")      # what hey-claude receives
    d = N.adapt(fx["args"], res, raw, today=date(2026, 11, 10))
    assert d["title"] == "Sports · Premier League" and d["kind"] == "sports", d
    hs = d["headlines"]
    assert 2 <= len(hs) <= 5 and len({h["source"] for h in hs}) == len(hs), hs        # one per site
    assert "Football Web Pages" not in [h["title"] for h in hs]                      # a site name, 7 times over
    assert hs[0] == {"title": "English Premier League results & fixtures (Matchday 10)", "source": "english.ahram.org.eg",
                     "link": "https://english.ahram.org.eg/News/581763.aspx"}, hs[0]
    fx, res, raw = _fx("websearch_news.json")
    d = N.adapt(fx["args"], res, raw, today=date(2026, 11, 10))
    assert d["title"] == "News · Technology", d
    assert d["headlines"][0]["title"] == "Technology Daily | Today's Latest Technology News", d["headlines"][0]
    assert any(h.get("time") == "Jun 10, 2025" for h in d["headlines"]), d
    assert all(not h["title"].islower() for h in d["headlines"][:3])                 # slug titles go last


def test_news_only_for_news_like_searches():
    N = _adapter("news")
    raw = ('Web search results for query: "how to make sourdough bread starter"\n\nLinks: '
           '[{"title":"How To Make Sourdough Starter","url":"https://example.com/a"},'
           '{"title":"Sourdough starter in seven days","url":"https://example.org/b"}]\n\nA starter needs flour and water.')
    assert N.adapt({"query": "how to make sourdough bread starter"}, None, raw) is None
    assert W.from_tool("WebSearch", {"query": "how to make sourdough bread starter"}, None, raw) is None
    for q, k in (("latest AI news", "news"), ("Lakers score last night", "sports"), ("who won the F1 race today", "sports"),
                 ("election results", "news"), ("football boots size guide", None), ("python list comprehension", None),
                 ("champions league standings", "sports")):
        assert N.kind_of(q) == k, (q, N.kind_of(q))
    assert N.fix_text("πŸ“± Technology Daily") == "Technology Daily"
    assert N.fix_text("Café owners in Zürich") == "Café owners in Zürich"
    assert N.when("https://x.com/2026/11/07/story", "", date(2026, 11, 10)) == "Nov 7"
    assert N.when("https://x.com/premier-league/20261128", "", date(2026, 11, 10)) == ""     # a fixture page ahead
    assert N.when("https://x.com/a", "Recap: March 3, 2025 results", date(2026, 11, 10)) == "Mar 3, 2025"
    news = ('Web search results for query: "latest AI news"\n\nLinks: [{"title":"A","url":"https://a.com"}]\n\n'
            'Links: [{"title":"Chip maker unveils new AI accelerator","url":"https://news.example.com/2026/11/09/chips"},'
            '{"title":"Regulators publish draft AI rules","url":"https://example.org/ai-rules"}]\n\nSummary.')
    d = N.adapt({}, None, news, today=date(2026, 11, 10))                           # the query read from the text
    assert d["title"] == "News · AI" and len(d["headlines"]) == 2 and d["headlines"][0]["time"] == "Nov 9", d
    assert N.adapt({"query": "latest AI news"}, None, "no links here") is None


# ------------------------------------------------------------------ email
def test_email_adapter_on_the_synthetic_fixture():
    E = _adapter("email")
    fx, res, raw = _fx("gmail_search_threads.json")
    d = E.adapt(fx["args"], res, raw, now=datetime(2026, 11, 10, 12, 0))
    assert d["title"] == "Inbox" and d["unread"] == 9 and d["approx"] is True and d["threads"] == 6, d
    names = [s["name"] for s in d["senders"]]
    assert names[0] == "Priya Raman" and d["senders"][0]["count"] == 2 and d["senders"][0]["unread"] == 2, d
    assert len(names) == 4 and len(set(names)) == 4
    assert d["senders"][0]["subject"] == "Re: lab report draft"
    assert all(s["link"].startswith("https://mail.google.com/") for s in d["senders"])
    assert "snippet" not in json.dumps(d) and "Looks good overall" not in json.dumps(d)  # snippets never reach the card
    assert W.normalise({"widget": "email", "data": d})["widget"] == "email"


def test_email_edge_cases():
    E = _adapter("email")
    now = datetime(2026, 11, 10, 12, 0)
    empty = E.adapt({"query": "is:unread"}, {}, "{}", now=now)
    assert empty == {"title": "Inbox", "unread": 0, "approx": False, "threads": 0, "senders": []}, empty
    assert E.adapt({"view": "THREAD_VIEW_IDS_ONLY"}, {"threads": [{"id": "1"}]}, "", now=now) is None
    assert E.adapt({}, None, "not json") is None
    me = "you@example.com"
    t = {"threads": [{"id": "1", "viewUrl": "https://evil.example/x", "messages": [
        {"sender": "Ana Ruiz <ana@example.org>", "date": "2026-11-09T10:00:00Z", "labelIds": ["INBOX"], "subject": "Plans"},
        {"sender": me, "date": "2026-11-09T11:00:00Z", "labelIds": ["SENT"], "subject": "Re: Plans"}]}]}
    d = E.adapt({"query": "from:ana"}, t, "", now=now)
    s = d["senders"][0]
    assert s["name"] == "Ana Ruiz" and s["subject"] == "Plans" and "link" not in s, s     # who wrote, not your reply
    assert d["title"] == "“from:ana”" and d["unread"] == 0, d


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
    sys.stdout.flush()
    os._exit(0)
