"""
End-to-end test cases and media discovery.

Each case drives the player the way the device does (the same JSON-RPC requests), then checks
the server state, the artwork the client would fetch, the client log (popups arrived, no Lua
errors) and the server log. Cases are registered with @case and run by run.py.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness import FAIL, Check, Ctx, Rpc, check_icon, wait_until  # noqa: F401

MENU_ROWS = [
    "Play Now (keep queue)",
    "Play Next (keep queue)",
    "Add to the queue",
    "Play Now (replace queue)",
    "Play Next (replace queue)",
]


@dataclass
class CaseDef:
    case_id: str
    title: str
    covers: str
    needs: tuple[str, ...]
    fn: Callable[[Ctx], None]


CASES: list[CaseDef] = []


def case(case_id: str, title: str, covers: str, needs: tuple[str, ...] = ()) -> Callable:
    def register(fn: Callable[[Ctx], None]) -> Callable[[Ctx], None]:
        CASES.append(CaseDef(case_id, title, covers, needs, fn))
        return fn

    return register


# ---------------------------------------------------------------------------------------
# Media discovery: one real item of each type, found through the browse calls
# ---------------------------------------------------------------------------------------


def _first_line(text: str) -> str:
    return (text or "").splitlines()[0] if text else ""


def _rows(rpc: Rpc, mode: str, quantity: int = 40, **tags: Any) -> list[dict[str, Any]]:
    return rpc.browse(mode, 0, quantity, **tags).get("item_loop", [])


def _pick(rows: list[dict[str, Any]], needle: str | None, key: str) -> list[dict[str, Any]]:
    """Rows that carry `key`, preferred ones (name contains needle) first."""
    usable = [r for r in rows if (r.get("commonParams") or {}).get(key) is not None]
    if needle:
        usable.sort(key=lambda r: needle.lower() not in (r.get("text") or "").lower())
    return usable


def _item(row: dict[str, Any], key: str) -> dict[str, Any]:
    return {"name": _first_line(row.get("text", "")), "params": {key: row["commonParams"][key]}}


def discover(rpc: Rpc, prefer: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Pick one item of each media type from the library."""
    media: dict[str, dict[str, Any]] = {}

    tracks = _pick(_rows(rpc, "tracks"), prefer.get("track"), "track_id")
    if tracks:
        media["track"] = _item(tracks[0], "track_id")
    if len(tracks) > 1:
        media["track2"] = _item(tracks[1], "track_id")

    for row in _pick(_rows(rpc, "albums", 40), prefer.get("album"), "album_id"):
        album_id = row["commonParams"]["album_id"]
        names = [
            _first_line(t.get("text", ""))
            for t in _rows(rpc, "tracks", 100, album_id=album_id)
            if t.get("text")
        ]
        if len(names) >= 2 and "album" not in media:
            media["album"] = {**_item(row, "album_id"), "tracks": names, "count": len(names)}
        if len(names) >= 4:
            media["album_big"] = {**_item(row, "album_id"), "tracks": names, "count": len(names)}
            break

    artists = _pick(_rows(rpc, "artists"), prefer.get("artist"), "artist_id")
    if artists:
        media["artist"] = _item(artists[0], "artist_id")

    radios = _pick(_rows(rpc, "radio", 60), prefer.get("radio"), "uri")
    for i, row in enumerate(radios[:3]):
        media["radio" if i == 0 else f"radio{i + 1}"] = _item(row, "uri")

    books = _pick(_rows(rpc, "audiobooks"), prefer.get("audiobook"), "uri")
    if books:
        media["audiobook"] = _item(books[0], "uri")

    for row in _pick(_rows(rpc, "podcasts"), prefer.get("podcast"), "podcast_id"):
        episodes = _pick(
            _rows(rpc, "tracks", 5, podcast_id=row["commonParams"]["podcast_id"]), None, "uri"
        )
        if episodes:
            media["podcast_episode"] = _item(episodes[0], "uri")
            break

    for row in _pick(_rows(rpc, "playlists"), prefer.get("playlist"), "playlist_id"):
        entries = _pick(
            _rows(rpc, "tracks", 5, playlist_id=row["commonParams"]["playlist_id"]), None, "uri"
        )
        if entries:
            media["playlist_track"] = _item(entries[0], "uri")
            break

    return media


# ---------------------------------------------------------------------------------------
# Playback cases: push one item of each media type and verify it
# ---------------------------------------------------------------------------------------


def _idle_add(ctx: Ctx, key: str) -> None:
    """A single tap on an idle queue: the item is added and starts playing."""
    item = ctx.media[key]
    marker = ctx.begin()
    ctx.rpc.play_control(cmd="add", **item["params"])
    ctx.verify_playing(item["name"])
    ctx.verify_queue(length=1, current=0)
    ctx.verify_clean(marker, expect_popup=True)


@case("E2E-01", "Track: a single tap on an idle queue plays it", "Single press > Tracks", ("track",))
def track_idle(ctx: Ctx) -> None:
    _idle_add(ctx, "track")


@case(
    "E2E-02",
    "Track: a single tap while playing adds to the end",
    "Single press > Tracks (queue state: playing)",
    ("track", "track2"),
)
def track_while_playing(ctx: Ctx) -> None:
    first, second = ctx.media["track"], ctx.media["track2"]
    ctx.rpc.play_control(cmd="add", **first["params"])
    ctx.verify_playing(first["name"], progress=False)
    marker = ctx.begin()
    ctx.rpc.play_control(cmd="add", **second["params"])
    time.sleep(1.5)
    rows = ctx.verify_queue(length=2, current=0)
    ctx.check("first track still playing", ctx.rpc.status().get("mode") == "play")
    ctx.check(
        "new track is last",
        len(rows) == 2 and second["name"].lower() in rows[1]["text"].lower(),
        rows[1]["text"].splitlines()[0] if len(rows) > 1 else "(missing)",
    )
    ctx.verify_clean(marker, expect_popup=True)


@case(
    "E2E-03",
    "Album: Play All from the second track queues the album and starts there",
    "Single press > Album tracks > Play All from here",
    ("album",),
)
def album_play_all(ctx: Ctx) -> None:
    album = ctx.media["album"]
    marker = ctx.begin()
    ctx.rpc.play_control(cmd="load", sort="albumtrack", play_index=1, **album["params"])
    ctx.verify_playing(album["tracks"][1], current=1)
    ctx.verify_queue(length=album["count"], current=1)
    ctx.verify_clean(marker, expect_popup=True)


@case("E2E-04", "Radio: a single tap on an idle queue plays the station", "Single press > Radio", ("radio",))
def radio_idle(ctx: Ctx) -> None:
    _idle_add(ctx, "radio")


@case(
    "E2E-05",
    "Radio: a single tap while a track plays queues the station behind it",
    "Single press > Radio (queue state: playing)",
    ("track", "radio"),
)
def radio_while_playing(ctx: Ctx) -> None:
    track, radio = ctx.media["track"], ctx.media["radio"]
    ctx.rpc.play_control(cmd="add", **track["params"])
    ctx.verify_playing(track["name"], progress=False)
    marker = ctx.begin()
    ctx.rpc.play_control(cmd="add", **radio["params"])
    time.sleep(1.5)
    rows = ctx.verify_queue(length=2, current=0)
    ctx.check(
        "station is last and named",
        len(rows) == 2 and radio["name"].lower() in rows[1]["text"].lower(),
        rows[1]["text"].splitlines()[0] if len(rows) > 1 else "(missing)",
    )
    ctx.verify_clean(marker, expect_popup=True)


@case(
    "E2E-06",
    "Radio: three taps in a row start the first and queue the rest",
    "Single press > Radio x3",
    ("radio", "radio2", "radio3"),
)
def radio_three(ctx: Ctx) -> None:
    marker = ctx.begin()
    for key in ("radio", "radio2", "radio3"):
        ctx.rpc.play_control(cmd="add", **ctx.media[key]["params"])
        time.sleep(1.0)
    ctx.verify_playing(ctx.media["radio"]["name"], progress=False)
    ctx.verify_queue(length=3, current=0)
    ctx.verify_clean(marker, expect_popup=True)


@case(
    "E2E-07",
    "Podcast episode: a single tap plays it, with a popup and art",
    "Single press > Podcast episodes",
    ("podcast_episode",),
)
def podcast_idle(ctx: Ctx) -> None:
    _idle_add(ctx, "podcast_episode")


@case("E2E-08", "Audiobook: a single tap plays it, with a popup and art", "Single press > Audiobooks", ("audiobook",))
def audiobook_idle(ctx: Ctx) -> None:
    _idle_add(ctx, "audiobook")


@case(
    "E2E-09",
    "Playlist track: a single tap plays it",
    "Single press > Playlist tracks",
    ("playlist_track",),
)
def playlist_idle(ctx: Ctx) -> None:
    _idle_add(ctx, "playlist_track")


@case("E2E-10", "Artist: Add to the queue loads their tracks and plays", "Long press > Artists", ("artist",))
def artist_add(ctx: Ctx) -> None:
    artist = ctx.media["artist"]
    marker = ctx.begin()
    ctx.rpc.play_control(cmd="add", **artist["params"])
    ctx.verify_playing()
    _, size, _ = ctx.rpc.queue()
    ctx.check("artist tracks queued", size >= 1, f"{size} track(s)")
    ctx.verify_queue()
    ctx.verify_clean(marker, expect_popup=True)


# ---------------------------------------------------------------------------------------
# Menu and queue-management cases
# ---------------------------------------------------------------------------------------


@case(
    "E2E-11",
    "Long-press menus for a track, an album and an artist have the five MA rows",
    "Long press > Tracks, Albums, Artists",
    ("track", "album", "artist"),
)
def long_press_menus(ctx: Ctx) -> None:
    ctx.check("track menu", ctx.rpc.info_menu("trackinfo", **ctx.media["track"]["params"]) == MENU_ROWS)
    ctx.check("album menu", ctx.rpc.info_menu("albuminfo", **ctx.media["album"]["params"]) == MENU_ROWS)
    ctx.check("artist menu", ctx.rpc.info_menu("artistinfo", **ctx.media["artist"]["params"]) == MENU_ROWS)
    if "radio" in ctx.media:
        ctx.check("radio menu", ctx.rpc.info_menu("trackinfo", **ctx.media["radio"]["params"]) == MENU_ROWS)


@case(
    "E2E-12",
    "One-track queue: the track menu is not blank and Delete item clears the queue",
    "Queue view > one-track queue",
    ("track",),
)
def one_track_queue(ctx: Ctx) -> None:
    item = ctx.media["track"]
    ctx.rpc.play_control(cmd="add", **item["params"])
    ctx.verify_playing(item["name"], progress=False)
    rows = ctx.rpc.context_menu(0).get("item_loop", [])
    texts = [r.get("text") for r in rows]
    ctx.check("menu is not blank", bool(rows), str(texts))
    ctx.check("Delete item is offered", "Delete item" in texts, str(texts))
    delete = next((r for r in rows if r.get("text") == "Delete item"), None)
    if delete is None:
        return
    marker = ctx.begin()
    ctx.rpc.call(*delete["actions"]["go"]["cmd"])
    cleared = wait_until(
        lambda: ctx.rpc.status().get("mode") != "play"
        and int(ctx.rpc.status().get("playlist_tracks", 0)) == 0,
        timeout=15,
    )
    ctx.check("queue cleared and playback stopped", cleared)
    ctx.verify_clean(marker, expect_popup=False)


@case(
    "E2E-13",
    "Queue menus by row position, Move to End and Delete item",
    "Queue view > long press rows",
    ("album_big",),
)
def queue_menus(ctx: Ctx) -> None:
    album = ctx.media["album_big"]
    count = album["count"]
    ctx.rpc.play_control(cmd="load", sort="albumtrack", play_index=0, **album["params"])
    ctx.verify_playing(progress=False)
    ctx.verify_queue(length=count, current=0, icons=False)

    def texts(index: int) -> list[str]:
        return [r.get("text", "") for r in ctx.rpc.context_menu(index).get("item_loop", [])]

    playing = ctx.rpc.context_menu(0).get("item_loop", [])
    ctx.check("playing row has no actions", all("actions" not in r for r in playing) and bool(playing))
    nxt = texts(1)
    ctx.check("next row: Play Now first, no Play Next", nxt[:1] == ["Play Now"] and "Play Next" not in nxt, str(nxt))
    ctx.check(
        "next row: Move to End and Delete offered",
        {"Move to End", "Delete item"} <= set(nxt),
        str(nxt),
        warn=True,  # hidden once the player has buffered the next track for gapless playback
    )
    ctx.check("middle row", texts(2) == ["Play Now", "Play Next", "Move to End", "Delete item"], str(texts(2)))
    ctx.check("last row", texts(count - 1) == ["Play Now", "Play Next", "Delete item"], str(texts(count - 1)))

    before = [_first_line(r["text"]) for r in ctx.rpc.queue()[0]]
    move = next((r for r in ctx.rpc.context_menu(2).get("item_loop", []) if r.get("text") == "Move to End"), None)
    if move:
        ctx.rpc.call(*move["actions"]["go"]["cmd"])
        time.sleep(1.5)
        after = [_first_line(r["text"]) for r in ctx.rpc.queue()[0]]
        expected = before[:2] + before[3:] + [before[2]]
        ctx.check("Move to End reorders the queue", after == expected, f"{after[-2:]} vs {expected[-2:]}")
    last = next(
        (r for r in ctx.rpc.context_menu(count - 1).get("item_loop", []) if r.get("text") == "Delete item"),
        None,
    )
    if last:
        ctx.rpc.call(*last["actions"]["go"]["cmd"])
        time.sleep(1.5)
        ctx.verify_queue(length=count - 1, icons=False)


# ---------------------------------------------------------------------------------------
# Browse and menu cases (no playback)
# ---------------------------------------------------------------------------------------


@case("E2E-14", "Every browse list returns rows with loadable icons", "Browse listings")
def browse_lists(ctx: Ctx) -> None:
    for mode in ("artists", "albums", "tracks", "playlists", "audiobooks", "podcasts", "radio"):
        result = ctx.rpc.browse(mode, 0, 8)
        rows = result.get("item_loop", [])
        if not result.get("count"):
            ctx.skip(mode, "empty in this library")
            continue
        ctx.check(f"{mode}: rows returned", bool(rows), f"count {result.get('count')}")
        for row in [r for r in rows if r.get("icon")][:4]:
            ctx.add(check_icon(ctx.rpc.base_url, row["icon"]), prefix=f"{mode}: ")


@case("E2E-15", "Favorites lists the presets first, with icons", "Favorites")
def favorites_menu(ctx: Ctx) -> None:
    rows = ctx.rpc.browse("favorites", 0, 40).get("item_loop", [])
    labels = [r.get("text") for r in rows]
    for needed in ("All Favorites", "Artists", "Albums", "Tracks", "Playlists", "Audiobooks", "Podcasts", "Radio"):
        ctx.check(f"category '{needed}'", needed in labels, str(labels))
    for row in rows[:10]:
        ctx.add(check_icon(ctx.rpc.base_url, row.get("icon")), prefix=f"'{row.get('text')}': ")
    first_category = labels.index("All Favorites") if "All Favorites" in labels else 0
    presets = rows[:first_category]
    if presets:
        ctx.check("presets come first", all("button" in str(r.get("actions", {}).get("go", {}).get("cmd", "")) for r in presets))
    else:
        ctx.skip("presets", "none configured for this player")


@case("E2E-16", "The home menu has the Now Playing shortcut and My Music", "Home & navigation")
def home_menu(ctx: Ctx) -> None:
    rows = ctx.rpc.call("menu", 0, 100).get("item_loop", [])
    by_id = {r.get("id"): r for r in rows}
    now_playing = by_id.get("appletNowPlaying")
    ctx.check("Now Playing entry present", now_playing is not None)
    ctx.check(
        "Now Playing opens the Now Playing screen",
        bool(now_playing) and now_playing.get("nextWindow") == "nowPlaying",
    )
    ctx.check("My Music present", "myMusic" in by_id)
    ctx.check("no presets in the My Music list", not any(str(i).startswith("preset_") for i in by_id))


@case("E2E-17", "Keyboard navigation opens each My Music list", "Home & navigation > My Music")
def navigate_my_music(ctx: Ctx) -> None:
    if ctx.ui is None or ctx.server_log is None:
        ctx.skip("ui", "needs player_host in the config and the server log")
        return
    for label in ("Artists", "Albums", "Playlists", "Radio", "Favorites"):
        started = time.time()
        ctx.ui.open_my_music(label)
        requests = ctx.server_log.browse_requests(
            ctx.server_log.since(int(time.time() - started) + 3), ctx.rpc.player_id
        )
        ctx.check(f"{label}: the player requested that list", any(label.lower() in r for r in requests), str(requests))
        ctx.check_screen(f"list-{label.lower()}", present=(label,))
    ctx.ui.press("h")


FAVORITE_TYPES = {
    "artists": "artists",
    "albums": "albums",
    "tracks": "tracks",
    "playlists": "playlists",
    "audiobooks": "audiobooks",
    "podcasts": "podcasts",
    "radio": "radios",
}


@case("E2E-18", "Favorites of every type list what Music Assistant has favorited", "Favorites > each type")
def favorites_by_type(ctx: Ctx) -> None:
    if ctx.ma is None:
        ctx.skip("Music Assistant API", "set E2E_MA_TOKEN to compare against the server")
        return
    for mode, library_type in FAVORITE_TYPES.items():
        expected = [item["name"] for item in ctx.ma.favorites(library_type)]
        rows = ctx.rpc.browse(mode, 0, 200, favorite_only=1).get("item_loop", [])
        texts = [str(row.get("text", "")) for row in rows]
        if not expected:
            ctx.skip(mode, "no favorites of this type in Music Assistant")
            continue
        missing = [name for name in expected if not any(name in text for text in texts)]
        ctx.check(f"{mode}: every favorite is listed", not missing, f"missing {missing[:3]}; listed {texts[:5]}")
        ctx.check(f"{mode}: nothing extra is listed", len(texts) <= len(expected), f"{len(texts)} rows for {len(expected)} favorites")
        for row in rows[:3]:
            ctx.add(check_icon(ctx.rpc.base_url, row.get("icon")), prefix=f"{mode} '{str(row.get('text')).replace(chr(10), ' - ')}': ")


@case("E2E-19", "An empty queue offers no Play Now or Delete rows", "Queue > track info shortcut")
def empty_queue_menu(ctx: Ctx) -> None:
    ctx.rpc.clear()
    for index in (0, 1):
        rows = ctx.rpc.context_menu(index).get("item_loop", [])
        texts = [str(row.get("text", "")) for row in rows]
        actionable = [t for t in texts if t in ("Play Now", "Play Next", "Move to End", "Delete item")]
        ctx.check(f"row {index} of an empty queue has no actions", not actionable, str(texts))
        ctx.check(f"row {index} still shows something", bool(texts), "the menu would be an empty window")


@case("E2E-20", "Long press on the device shows the five MA rows for a track, album and artist", "Long press > menus")
def long_press_on_screen(ctx: Ctx) -> None:
    if ctx.ui is None:
        ctx.skip("ui", "needs player_host in the config")
        return
    for label in ("Tracks", "Albums", "Artists"):
        ctx.ui.open_my_music(label)
        ctx.ui.to_top()
        ctx.ui.long_press()
        time.sleep(2)
        ctx.check_screen(label.lower(), present=tuple(MENU_ROWS), absent=("Delete item", "Move to End"))
        ctx.check(f"{label}: the long press queued nothing", int(ctx.rpc.status().get("playlist_tracks", 0)) == 0)
        ctx.ui.home()


# -- E2E-21: build a queue with presses on the device, then skip around and manage it ------------------


def _queue_state(ctx: Ctx) -> tuple[list[str], int | None, str]:
    """The queue as Music Assistant holds it: item names in order, the current index and the state."""
    queue_id = ctx.rpc.player_id
    state = ctx.ma.call("player_queues/get", queue_id=queue_id)
    items = ctx.ma.call("player_queues/items", queue_id=queue_id, limit=100, offset=0) or []
    names = [(item.get("media_item") or {}).get("name") or item.get("name") for item in items]
    return names, state.get("current_index"), str(state.get("state"))


def _expect_queue(ctx: Ctx, label: str, expected: list[str], current: int | None = None) -> None:
    """Wait a few seconds for the queue to settle, then check its order (and the current index)."""
    deadline = time.time() + 12
    while True:
        names, index, state = _queue_state(ctx)
        if (names == expected and (current is None or index == current)) or time.time() > deadline:
            break
        time.sleep(1)
    ctx.check(f"{label}: queue order", names == expected, f"queue {names}, expected {expected}")
    if current is not None:
        ctx.check(f"{label}: current index {current}", index == current, f"index {index}, state {state}")


def _to_track(ctx: Ctx, grid_index: int) -> None:
    """Open My Music > Tracks and put the highlight on the track at this position of the grid."""
    ctx.ui.open_my_music("Tracks")
    ctx.ui.to_top()
    if grid_index:
        ctx.ui.press(*["right"] * grid_index, settle=0.3)


def _tap_track(ctx: Ctx, grid_index: int) -> None:
    _to_track(ctx, grid_index)
    ctx.ui.press("enter", settle=3.0)


def _long_press_track(ctx: Ctx, grid_index: int, label: str, choose: int) -> None:
    """Long press a track, check the menu, then pick the row at this position."""
    _to_track(ctx, grid_index)
    ctx.ui.long_press()
    time.sleep(2)
    ctx.check_screen(label, present=tuple(MENU_ROWS))
    if choose:
        ctx.ui.press(*["down"] * choose, settle=0.3)
    ctx.ui.press("enter", settle=3.0)


def _open_queue(ctx: Ctx) -> None:
    ctx.ui.home()
    ctx.ui.press("]", settle=2.5)  # JiveLite's shortcut for Current Playlist
    ctx.ui.to_top()


def _queue_row_menu(ctx: Ctx, label: str, row: int, present: tuple[str, ...], absent: tuple[str, ...], choose: str) -> None:
    """Long press a row of the queue screen, check which actions it offers, and pick one."""
    _open_queue(ctx)
    if row:
        ctx.ui.press(*["down"] * row, settle=0.3)
    ctx.ui.long_press()
    time.sleep(2)
    ctx.check_screen(label, present=present, absent=absent)
    order = [text for text in ("Play Now", "Play Next", "Move to End", "Delete item") if text in present]
    position = order.index(choose)
    if position:
        ctx.ui.press(*["down"] * position, settle=0.3)
    ctx.ui.press("enter", settle=3.0)


@case(
    "E2E-21",
    "Build a queue with taps and long presses on the device, skip around it and manage it",
    "Single press > Tracks, Long press > Tracks, Queue view, Now Playing",
)
def build_and_manage_queue(ctx: Ctx) -> None:
    if ctx.ui is None or ctx.ma is None or ctx.client_log is None:
        ctx.skip("setup", "needs player_host, E2E_MA_TOKEN and the client log")
        return
    rows = ctx.rpc.browse("tracks", 0, 6).get("item_loop", [])
    t = [str(row["text"]).splitlines()[0] for row in rows]
    if len(t) < 6:
        ctx.skip("setup", "the library needs at least 6 tracks")
        return
    marker = ctx.begin()

    _expect_queue(ctx, "start", [])

    # 1. A tap on an empty queue starts the track and does not open a menu.
    _tap_track(ctx, 0)
    _expect_queue(ctx, "tap 1 (idle)", [t[0]], current=0)
    ctx.check_screen("after-tap-1", absent=tuple(MENU_ROWS))

    # 2. A tap while playing adds to the end.
    _tap_track(ctx, 1)
    _expect_queue(ctx, "tap 2", [t[0], t[1]], current=0)
    ctx.check_screen("after-tap-2", absent=tuple(MENU_ROWS))

    # The menus below take minutes to drive; pause so the first track cannot end and shift the queue.
    ctx.ui.press("space", settle=2.0)
    ctx.check("paused after tap 2", wait_until(lambda: _queue_state(ctx)[2] == "paused", timeout=10), str(_queue_state(ctx)))

    # 3. Long press > Play Next (keep queue) puts the track right after the current one.
    _long_press_track(ctx, 2, "long-press-track-3", choose=1)
    _expect_queue(ctx, "long press 3: Play Next", [t[0], t[2], t[1]], current=0)

    # 4. Long press > Add to the queue appends.
    _long_press_track(ctx, 3, "long-press-track-4", choose=2)
    _expect_queue(ctx, "long press 4: Add to the queue", [t[0], t[2], t[1], t[3]], current=0)

    # 5. Two more taps.
    _tap_track(ctx, 4)
    _tap_track(ctx, 5)
    order = [t[0], t[2], t[1], t[3], t[4], t[5]]
    _expect_queue(ctx, "taps 5 and 6", order, current=0)

    # 6. The queue screen shows the same tracks, with a Clear queue row.
    _open_queue(ctx)
    ctx.check_screen("queue-screen", present=(*order[:5], "Clear queue"))

    # 7. Resume, then skip forward through every track with the Next key; each press must move exactly
    # one track and that track must play.
    ctx.ui.press("p", settle=2.0)
    ctx.verify_playing(order[0], progress=False, current=0)
    for index in range(1, len(order)):
        before = _queue_state(ctx)[1]
        ctx.ui.press("b", settle=1.0)
        wait_until(lambda: _queue_state(ctx)[1] != before, timeout=10, interval=0.5)
        names, current, state = _queue_state(ctx)
        ctx.check(f"Next {index}: moved exactly one track", current == before + 1, f"index {before} -> {current}")
        ctx.check(f"Next {index}: now on {order[index]}", names[current] == order[index], f"on {names[current]!r}")
        time.sleep(3)
        first = float(ctx.rpc.status().get("time", 0) or 0)
        time.sleep(3)
        second = float(ctx.rpc.status().get("time", 0) or 0)
        ctx.check(f"Next {index}: playing from the start", state == "playing" and second > first, f"{first:.0f}s -> {second:.0f}s, {state}")
    # 8. Jump back to the second track from the queue screen (Play Now).
    _open_queue(ctx)
    ctx.ui.press("down", settle=0.3)
    ctx.ui.long_press()
    time.sleep(2)
    ctx.check_screen("queue-row-1-menu", present=("Play Now",))
    ctx.ui.press("enter", settle=3.0)
    ctx.verify_playing(order[1], current=1)

    # 9. Manage the queue: delete, move to end, play next, delete again.
    _queue_row_menu(ctx, "queue-row-4-menu", 4, ("Play Now", "Play Next", "Move to End", "Delete item"), (), "Delete item")
    order = [order[0], order[1], order[2], order[3], order[5]]
    _expect_queue(ctx, "delete row 4", order, current=1)

    _queue_row_menu(ctx, "queue-row-3-menu", 3, ("Play Now", "Play Next", "Move to End", "Delete item"), (), "Move to End")
    order = [order[0], order[1], order[2], order[4], order[3]]
    _expect_queue(ctx, "move row 3 to the end", order, current=1)

    _queue_row_menu(ctx, "queue-row-4-menu-2", 4, ("Play Now", "Play Next", "Delete item"), ("Move to End",), "Play Next")
    order = [order[0], order[1], order[4], order[2], order[3]]
    _expect_queue(ctx, "play next on the last row", order, current=1)

    # 10. Clear the queue from the queue screen.
    _open_queue(ctx)
    ctx.ui.press(*["down"] * len(order), settle=0.3)
    ctx.ui.press("enter", settle=2.5)
    ctx.check_screen("clear-queue-confirm", present=("Cancel", "Clear queue"))
    ctx.ui.press("down", "enter", settle=3.0)  # the confirmation screen starts on Cancel
    _expect_queue(ctx, "clear queue", [])

    ctx.verify_clean(marker, expect_popup=True)
