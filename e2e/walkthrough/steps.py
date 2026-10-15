"""The menu walkthrough, written once for every device: where to go and what to capture.

Each step starts from the home menu, opens the entries in `path` one after the other, waits for the screen to settle and
takes a screenshot. Nothing here knows how a device is driven: a device wrapper (radio.py, pcp.py) turns a step into the
presses or macro that does it.

A path entry is
  - a label: the entry whose text starts with it (case matters, as on screen);
  - FIRST: the first entry of the menu that is open (for "any item": a list of albums, a podcast ...); Nth(n): the n-th;
  - Do("go"): a device action instead of a menu entry; LONG_PRESS is the long press that opens an entry's menu and QUEUE opens the
    queue (the current playlist).

Nothing that plays something or changes a setting is ever chosen: the steps only open menus and views.
"""

from __future__ import annotations

from dataclasses import dataclass

FIRST = "<first entry>"


@dataclass(frozen=True)
class Nth:
    """The n-th entry (counting from 1) of the menu that is open, e.g. the second row of the queue."""

    n: int


@dataclass(frozen=True)
class Do:
    """A device action (a name from the device's action set, e.g. "go")."""

    action: str


@dataclass(frozen=True)
class Step:
    title: str  # the slide title
    path: tuple = ()  # labels, FIRST and Do(...) to open from the home menu
    note: str = ""  # the slide note
    needs: tuple = ()  # what the server must have ready first: "track" (a paused track) or "queue" (five tracks queued)
    wait: float = 1.45  # seconds to let the last screen settle before the screenshot (long text starts to scroll about a second after it opens)
    optional: bool = False  # not an error when the entry is not there (a setting that is off, nothing in the library)
    expect: str = ""  # text the screen should show, read with OCR as a check; empty to skip it


# A long press on the highlighted entry: opens its menu (play now, play next, add ...). Each device does it its own way.
LONG_PRESS = Do("long_press")
# open the current playlist (the queue) from wherever the device is
QUEUE = Do("queue")

STEPS: tuple[Step, ...] = (
    Step("Main Menu", (), "Home screen. Settings holds the HA Extras entry when it is enabled for the device.", expect="Settings"),
    Step(
        "Now Playing",
        ("Now Playing",),
        "Shows the current track from the Music Assistant queue.",
        needs=("track",),
    ),
    Step(
        "Queue (Current Playlist)",
        (QUEUE,),
        "The player's queue as Music Assistant has it.",
        needs=("queue",),
    ),
    Step(
        "Long press on a queue item",
        (QUEUE, Nth(4), LONG_PRESS),
        "The menu of a queue item (not the one playing or the next): Play Now, Play Next, Move to End and Delete item, as in the Music Assistant app.",
        needs=("queue",),
        wait=3.0,
        optional=True,
    ),
    Step(
        "My Music",
        ("My Music",),
        "The preset entries come from the player's settings in Music Assistant (favorited playlists and radio stations).",
        expect="Artists",
    ),
    Step("My Music - Favorites", ("My Music", "Favorites"), "All Favorites, then one entry per type."),
    Step("My Music - Artists", ("My Music", "Artists"), expect="Artists"),
    Step(
        "My Music - Artists - Avril Lavigne",
        ("My Music", "Artists", "Avril Lavigne"),
        "An artist who is only on one track (a featured artist). The view lists albums, then tracks not on those albums, "
        "then matching radio stations.",
        optional=True,
    ),
    Step(
        "Long press on an artist",
        ("My Music", "Artists", FIRST, LONG_PRESS),
        "The menu of an artist: play everything by them, add it to the queue, play it next.",
        optional=True,
    ),
    Step("My Music - Albums", ("My Music", "Albums"), expect="Albums"),
    Step(
        "My Music - Albums - an album",
        ("My Music", "Albums", FIRST),
        "Tracks of one album. Tapping a track adds it (plays it when the queue is idle); long press for Play Album from here.",
    ),
    Step(
        "Long press on an album",
        ("My Music", "Albums", FIRST, LONG_PRESS),
        "The menu of an album: play it, play it next, add it to the queue.",
        optional=True,
    ),
    Step("My Music - Tracks", ("My Music", "Tracks"), expect="Tracks"),
    Step(
        "Long press on a track",
        ("My Music", "Tracks", FIRST, LONG_PRESS),
        "The menu of a track: play now, play next, add to the queue, and more.",
        optional=True,
    ),
    Step("My Music - Playlists", ("My Music", "Playlists"), expect="Playlists"),
    Step("My Music - Audiobooks", ("My Music", "Audiobooks"), expect="Audiobooks", optional=True),
    Step("My Music - Podcasts", ("My Music", "Podcasts"), expect="Podcasts"),
    Step("My Music - Podcasts - a podcast", ("My Music", "Podcasts", FIRST), "Episodes of one podcast.", optional=True),
    Step("My Music - Radio", ("My Music", "Radio"), expect="Radio"),
    Step(
        "Long press on a radio station",
        ("My Music", "Radio", FIRST, LONG_PRESS),
        "The menu of a radio station.",
        optional=True,
    ),
    Step("My Music - Search", ("My Music", "Search"), "The search text entry."),
    Step("My Music - Switch Library", ("My Music", "Switch Library"), expect="Libraries"),
    Step("Settings", ("Settings",), "Includes Alarm Clock (says alarms are not supported) and HA Extras.", expect="Settings"),
    Step(
        "Settings - Alarm Clock",
        ("Settings", "Alarm Clock"),
        "Alarms are not supported; Home Assistant can trigger them with an automation.",
        optional=True,
    ),
    Step(
        "Settings - HA Extras",
        ("Settings", "HA Extras"),
        "Experimental. Needs the device setting 'Expose HA Entities to Device (HA Extras)' and the HA Extras label on the entities.",
        optional=True,
    ),
    Step(
        "Settings - HA Extras - Cameras",
        ("Settings", "HA Extras", "Cameras"),
        "Choosing a camera shows a snapshot full screen in the Image Viewer, refreshed about every 10 seconds.",
        optional=True,
    ),
    Step(
        "Settings - HA Extras - Cameras - a camera",
        ("Settings", "HA Extras", "Cameras", FIRST),
        "The camera view in the Image Viewer. It fetches a new snapshot about every 10 seconds until a key is pressed.",
        wait=12.0,
        optional=True,
    ),
    Step(
        "Settings - HA Extras - Dashboards",
        ("Settings", "HA Extras", "Dashboards"),
        "Entities whose state is the address of a picture of a dashboard. Shown full screen like the cameras.",
        optional=True,
    ),
    Step(
        "Settings - HA Extras - Dashboards - a dashboard",
        ("Settings", "HA Extras", "Dashboards", FIRST),
        "A dashboard in the Image Viewer. A screenshot service renders the picture; it is fetched again about every 10 seconds.",
        wait=12.0,
        optional=True,
    ),
    Step(
        "Settings - HA Extras - Controls",
        ("Settings", "HA Extras", "Controls"),
        "Choosing a row opens Turn On / Turn Off / Cancel.",
        optional=True,
    ),
    Step(
        "Settings - HA Extras - Controls - a control",
        ("Settings", "HA Extras", "Controls", FIRST),
        "Choosing a control opens its menu: the current state, Turn On, Turn Off and Cancel.",
        optional=True,
    ),
    Step(
        "Settings - HA Extras - Scenes and Scripts",
        ("Settings", "HA Extras", "Scenes"),
        "Choosing one opens Run / Cancel.",
        optional=True,
    ),
    Step(
        "Settings - HA Extras - Scenes and Scripts - a scene",
        ("Settings", "HA Extras", "Scenes", FIRST),
        "Choosing a scene opens Run and Cancel.",
        optional=True,
    ),
    Step("Settings - HA Extras - Status", ("Settings", "HA Extras", "Status"), "Read-only values.", optional=True),
)
