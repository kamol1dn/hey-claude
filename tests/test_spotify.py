"""Offline tests for play_spotify (heyclaude/mcp_windows/spotify.py): URI and link
parsing, and the whole open -> wait -> press Play -> check flow against a fake
Spotify (window, page, media session) on a fake clock. No Spotify, no Windows
state, no real waiting.

    .venv\\Scripts\\python tests\\test_spotify.py
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from heyclaude.mcp_windows import registry, spotify  # noqa: E402
from heyclaude.mcp_windows._util import ToolError  # noqa: E402


def raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc as e:
        return e
    raise AssertionError(f"{fn.__name__} did not raise {exc.__name__}")


# == fakes ===========================================================================
class Clock:
    """Stands in for the `time` module inside spotify.py: sleep() moves the clock."""
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Button:
    def __init__(self, name, plays=None):
        self.name, self.plays = name, plays          # plays: the session it starts


class FakeSpotify:
    """The desktop app as play_spotify sees it, with the behaviour measured on the
    real one: a track URI starts by itself only while nothing is playing (else it
    opens the album with the track's row selected); other URIs only navigate."""

    PAGES = {
        "spotify:album:AAA": ("Listen to Homework - Album by Daft Punk | Spotify",
                              [Button("Play", {"title": "Daftendirekt", "artist": "Daft Punk", "album": "Homework"}),
                               Button("Play Daftendirekt by Daft Punk"),
                               Button("Play Around the World by Daft Punk")]),
        "spotify:playlist:PPP": ("Today’s Top Hits | Spotify Playlist",
                                 [Button("Choose photo"), Button("Playlists 7 playlists"),
                                  Button("Play Today’s Top Hits", {"title": "Loser", "artist": "Tame Impala",
                                                                        "album": "Deadbeat"})]),
        "spotify:artist:RRR": ("Queen | Spotify",
                               [Button("Follow"), Button("Play", {"title": "Bohemian Rhapsody", "artist": "Queen",
                                                                  "album": "A Night At The Opera"})]),
    }
    TRACK = {"title": "Around the World", "artist": "Daft Punk", "album": "Homework"}

    def __init__(self, clock, running=True, session=None, nav_s=0.8, autoplay_s=1.1, launch_s=2.0,
                 starts=True, page=None, lag_s=0.0, ignore_presses=0):
        self.clock, self.running, self.nav_s, self.autoplay_s, self.launch_s = clock, running, nav_s, autoplay_s, launch_s
        self.session = dict(session) if session else None
        self.starts, self.lag_s, self.ignore_presses = starts, lag_s, ignore_presses
        self.page = page or ("Listen to Discovery - Album by Daft Punk | Spotify",
                             [Button("Play", {"title": "One More Time", "artist": "Daft Punk", "album": "Discovery"})])
        self.selected: list = []
        self.opened, self.invoked = [], []
        self.pending = []                            # (at, fn)
        self.window_at = clock.t if running else None

    # the OS layer
    def tick(self):
        for at, fn in list(self.pending):
            if self.clock.t >= at:
                self.pending.remove((at, fn))
                fn()

    def window(self):
        self.tick()
        return 4242 if self.window_at is not None and self.clock.t >= self.window_at else None

    def session_now(self):
        self.tick()
        return dict(self.session) if self.session else None

    def open(self, uri):
        self.opened.append(uri)
        t = self.clock.t
        if not self.running:
            self.running, self.window_at = True, t + self.launch_s
            t += self.launch_s
        kind = uri.split(":")[1]
        was_playing = (self.session or {}).get("status") == "playing"
        if kind == "track":
            def nav():
                self.page = ("Listen to Homework - Album by Daft Punk | Spotify", self.PAGES["spotify:album:AAA"][1])
                self.selected = [Button("Play Around the World by Daft Punk", self.TRACK), Button("Add to Liked Songs")]
            self.pending.append((t + self.nav_s, nav))
            if not was_playing and self.starts:
                self.pending.append((t + self.autoplay_s, lambda: self._play(self.TRACK)))
        elif kind == "search":
            res = [Button("More options for One More Time"),
                   Button("Play", {"title": "One More Time", "artist": "Daft Punk", "album": "Discovery"}),
                   Button("Play Daft Punk Essentials")]
            self.pending.append((t + self.nav_s, lambda: setattr(self, "page", ("Spotify – Search", res))))
        else:
            if self.lag_s:          # the old title stays while the old page re-renders (seen on the real app)
                old_title, old_buttons = self.page
                half = (old_title, [Button("Explore it")] + old_buttons)
                self.pending.append((t + 0.3, lambda: setattr(self, "page", half)))
            self.pending.append((t + self.nav_s + self.lag_s, lambda: setattr(self, "page", self.PAGES[uri])))

    def page_now(self, hwnd, selected=False):
        self.tick()
        assert hwnd == 4242
        title, buttons = self.page
        bs = self.selected if selected else buttons
        return title, [(b.name, b) for b in bs]

    def invoke(self, b):
        self.invoked.append(b.name)
        if self.ignore_presses:
            self.ignore_presses -= 1
            return
        if b.plays and self.starts:
            self.pending.append((self.clock.t + 0.6, lambda: self._play(b.plays)))

    def _play(self, track):
        self.session = dict(track, status="playing")

    def patches(self):
        return [mock.patch.object(spotify, "time", self.clock),
                mock.patch.object(spotify, "_os_window", self.window),
                mock.patch.object(spotify, "_os_session", self.session_now),
                mock.patch.object(spotify, "_os_open", self.open),
                mock.patch.object(spotify, "_os_page", self.page_now),
                mock.patch.object(spotify, "_os_invoke", self.invoke)]


def run(fake, **kw):
    ps = fake.patches()
    for p in ps:
        p.start()
    try:
        t0 = fake.clock.t
        try:
            return spotify.play_spotify(**kw), fake.clock.t - t0
        except ToolError as e:
            e.took = fake.clock.t - t0
            raise
    finally:
        for p in reversed(ps):
            p.stop()


PAUSED = {"title": "ROYALTY", "artist": "MAXPVNK", "album": "ROYALTY", "status": "paused"}
PLAYING = {"title": "Stop The Wedding!", "artist": "Ashe", "album": "Stop The Wedding!", "status": "playing"}


# == parsing =========================================================================
def test_uri_and_links():
    u = spotify.to_uri
    assert u("spotify:track:1pKYYY0dkg23sQQXi0Q5zN") == "spotify:track:1pKYYY0dkg23sQQXi0Q5zN"
    assert u(" spotify:Album:5uRdvUR7xCnHmUW8n64n9y ") == "spotify:album:5uRdvUR7xCnHmUW8n64n9y"
    assert u("spotify:user:someone:playlist:37i9dQZF1DXcBWIGoYBM5M") == "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
    assert u("https://open.spotify.com/track/0TxMRiAvI1s0L821BJJWzx?utm_source=x&go=1&nap_web=1") == \
        "spotify:track:0TxMRiAvI1s0L821BJJWzx"
    assert u("https://open.spotify.com/intl-de/album/4m2880jivSbbyEGAKfITCa") == "spotify:album:4m2880jivSbbyEGAKfITCa"
    assert u("https://open.spotify.com/artist/1dfeR4HaWDbWqFHLkxsg1d") == "spotify:artist:1dfeR4HaWDbWqFHLkxsg1d"
    for bad in ("", "daft punk", "spotify:search:x", "spotify:nl:CAASEJo+", "https://example.com/track/abc",
                "spotify:track:", "spotify:track:abc;calc"):
        assert u(bad) == "", bad


def test_page_names_and_play_button():
    assert spotify._page_name("Listen to Homework - Album by Daft Punk | Spotify") == "Homework - Album by Daft Punk"
    assert spotify._page_name("Today’s Top Hits | Spotify Playlist") == "Today’s Top Hits"
    assert spotify._page_name("Queen | Spotify") == "Queen"
    pick = spotify._play_button
    assert pick([("Playlists 7 playlists", 1), ("Play Discovery", 2), ("Play", 3)]) == ("Play Discovery", 2)
    assert pick([("Explore Homework", 1), ("Pause", 2), ("Play", 3)]) == ("Pause", 2)
    assert pick([("Player controls", 1), ("Playback speed", 2)]) is None
    assert spotify._started(PAUSED, dict(PAUSED, status="playing"))            # resumed: counts
    assert not spotify._started(PLAYING, dict(PLAYING))                        # nothing new
    assert spotify._started(None, dict(PLAYING)) and not spotify._started(None, dict(PAUSED))


# == the flow ========================================================================
def test_track_starts_by_itself_when_spotify_is_paused():
    f = FakeSpotify(Clock(), session=PAUSED)
    r, took = run(f, uri="https://open.spotify.com/track/1pKYYY0dkg23sQQXi0Q5zN?go=1")
    assert f.opened == ["spotify:track:1pKYYY0dkg23sQQXi0Q5zN"] and f.invoked == []
    assert r["playing"] == {"title": "Around the World", "artist": "Daft Punk", "album": "Homework"}
    assert r["status"] == "playing" and r["how"] == "opened" and took < 3


def test_track_while_playing_presses_the_selected_row():
    """A track URI only navigates while something plays: the album opens with the
    track's row selected, and its own Play button (not the album's) starts it."""
    f = FakeSpotify(Clock(), session=PLAYING)
    r, took = run(f, uri="spotify:track:1pKYYY0dkg23sQQXi0Q5zN")
    assert f.invoked == ["Play Around the World by Daft Punk"]
    assert r["playing"]["title"] == "Around the World" and r["how"] == "pressed play"
    assert r["opened"] == "Homework - Album by Daft Punk" and took < 4


def test_track_that_doesnt_start_by_itself_falls_back_to_the_row():
    f = FakeSpotify(Clock(), session=PAUSED, autoplay_s=99)
    r, took = run(f, uri="spotify:track:1pKYYY0dkg23sQQXi0Q5zN")
    assert f.invoked == ["Play Around the World by Daft Punk"] and r["playing"]["title"] == "Around the World"
    assert took < spotify.AUTOPLAY_WAIT_S + 3


def test_album_waits_for_the_new_page_before_pressing():
    """The old page (Discovery) has a Play button too: never press it."""
    f = FakeSpotify(Clock(), session=PLAYING, nav_s=1.2)
    r, _ = run(f, uri="spotify:album:AAA")
    assert f.invoked == ["Play"] and r["playing"]["title"] == "Daftendirekt"
    assert r["opened"] == "Homework - Album by Daft Punk" and r["uri"] == "spotify:album:AAA"


def test_a_lagging_title_never_gets_the_old_pages_button_pressed():
    """Spotify can keep the old title for seconds while the old page re-renders:
    once anything changed, only the new title counts."""
    f = FakeSpotify(Clock(), session=PLAYING, lag_s=2.5)
    r, took = run(f, uri="spotify:album:AAA")
    assert f.invoked == ["Play"] and r["playing"]["title"] == "Daftendirekt" and took >= 2.5


def test_a_press_that_doesnt_take_is_retried_once():
    f = FakeSpotify(Clock(), session=PAUSED, ignore_presses=1)
    r, took = run(f, uri="spotify:artist:RRR")
    assert f.invoked == ["Play", "Play"] and r["playing"]["title"] == "Bohemian Rhapsody" and took < 15


def test_playlist_and_artist():
    f = FakeSpotify(Clock(), session=PAUSED)
    r, _ = run(f, uri="spotify:playlist:PPP")
    assert f.invoked == ["Play Today’s Top Hits"] and r["playing"]["artist"] == "Tame Impala"
    assert r["opened"] == "Today’s Top Hits"
    f = FakeSpotify(Clock(), session=PLAYING)
    r, _ = run(f, uri="spotify:artist:RRR")
    assert f.invoked == ["Play"] and r["playing"]["title"] == "Bohemian Rhapsody"


def test_query_searches_and_plays_the_top_result():
    f = FakeSpotify(Clock(), session=PAUSED)
    r, _ = run(f, query="  daft punk   one more time ")
    assert f.opened == ["spotify:search:daft%20punk%20one%20more%20time"]
    assert f.invoked == ["Play"] and r["playing"]["title"] == "One More Time"
    f = FakeSpotify(Clock(), session=PAUSED)
    run(f, query="AC/DC & friends")
    assert f.opened == ["spotify:search:AC/DC%20%26%20friends"]


def test_same_page_already_playing_says_so():
    page = FakeSpotify.PAGES["spotify:album:AAA"]
    f = FakeSpotify(Clock(), session=dict(PLAYING), page=(page[0], [Button("Pause")] + page[1][1:]))
    f.PAGES = dict(f.PAGES, **{"spotify:album:AAA": f.page})
    r, took = run(f, uri="spotify:album:AAA")
    assert f.invoked == [] and r["how"] == "already playing" and r["playing"]["title"] == "Stop The Wedding!"
    assert took >= spotify.SAME_PAGE_S                    # waited a moment for a page change first


def test_cold_start_launches_through_the_uri():
    f = FakeSpotify(Clock(), running=False, launch_s=6.0)
    r, took = run(f, uri="spotify:album:AAA")
    assert f.opened == ["spotify:album:AAA"] and f.invoked == ["Play"] and r["status"] == "playing"
    assert took < 15
    f = FakeSpotify(Clock(), running=False, launch_s=6.0)
    r, took = run(f, uri="spotify:track:1pKYYY0dkg23sQQXi0Q5zN")
    assert r["how"] == "opened" and f.invoked == [] and took < 15


def test_errors_are_clear_and_bounded():
    raises(ToolError, spotify.play_spotify)
    raises(ToolError, spotify.play_spotify, query="   ")
    e = raises(ToolError, spotify.play_spotify, uri="https://youtube.com/watch?v=x")
    assert "spotify" in str(e).lower()
    f = FakeSpotify(Clock(), running=False, launch_s=60)
    e = raises(ToolError, run, f, uri="spotify:album:AAA")
    assert "didn't start" in str(e) and e.took <= spotify.LAUNCH_WAIT_S + 0.5
    f = FakeSpotify(Clock(), session=PAUSED, starts=False)          # pressed, but nothing plays
    e = raises(ToolError, run, f, uri="spotify:album:AAA")
    assert "pressed Play" in str(e) and f.invoked == ["Play", "Play"] and e.took < 15     # one retry
    f = FakeSpotify(Clock(), session=PAUSED, page=("Listen to X | Spotify", [Button("Follow")]))
    f.PAGES = {"spotify:album:AAA": ("Listen to Y | Spotify", [Button("Follow")])}
    e = raises(ToolError, run, f, uri="spotify:album:AAA")             # no Play button anywhere
    assert "Play button" in str(e) and e.took < 15


def test_registered_as_a_windows_tool():
    t = registry.TOOLS["play_spotify"]
    assert (t.group, t.kind, t.allow, t.undo) == ("media", "write", True, None)
    assert "mcp__windows__play_spotify" in registry.allow_list()
    assert registry.inverse("mcp__windows__play_spotify", {}, {"playing": {}}) is None
    import inspect
    assert list(inspect.signature(t.func).parameters) == ["uri", "query"]
    assert "spotify:" in t.func.__doc__ and "query" in t.func.__doc__


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(n, "passed")
