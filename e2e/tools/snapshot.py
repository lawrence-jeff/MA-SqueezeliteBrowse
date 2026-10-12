"""Record the server's replies to a broad set of browse requests, and compare two recordings.

Use it to show that a refactor did not change what the server sends to the devices:

    python3 tools/snapshot.py record before.json
    ... change the code, deploy it ...
    python3 tools/snapshot.py compare before.json

`compare` records a fresh set (or reads a second file, if given) and lists every request whose reply
differs. Only the Python standard library is used, and no device is touched: the requests are
the JSON-RPC calls a device would make.

The server address and player id come from the E2E config (config.json next to run.py), or from
--server and --player.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
# Replies that change from one call to the next (a timestamp, a live sensor value).
DEFAULT_IGNORE = ("queue", "haextras-status")
MODES = ("artists", "albums", "tracks", "playlists", "radio", "audiobooks", "podcasts")


class Rpc:
    """The JSON-RPC interface the devices use, for one player."""

    def __init__(self, server: str, player_id: str) -> None:
        self.url = server.rstrip("/") + "/jsonrpc.js"
        self.player_id = player_id

    def call(self, *cmd: Any) -> dict[str, Any]:
        body = json.dumps(
            {"id": 1, "method": "slim.request", "params": [self.player_id, list(cmd)]}
        ).encode()
        request = urllib.request.Request(self.url, data=body)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response).get("result") or {}
        except OSError as err:
            return {"error": str(err)}


def record(rpc: Rpc) -> dict[str, Any]:
    """Return the replies to the browse requests, keyed by a name for each request."""
    out: dict[str, Any] = {}

    def ask(name: str, *cmd: Any) -> dict[str, Any]:
        out[name] = rpc.call(*cmd)
        return out[name]

    def browse(*tags: str, quantity: int = 500) -> tuple[Any, ...]:
        return ("browselibrary", "items", 0, quantity, "menu:1", *tags)

    ask("menu", "menu", 0, 100)
    for mode in (*MODES, "favorites", "favorites_all"):
        ask(f"mode-{mode}", *browse(f"mode:{mode}"))
    for mode in MODES:
        ask(f"fav-{mode}", *browse(f"mode:{mode}", "favorite_only:1"))
    ask("search-all", *browse("mode:search_all", "search:a"))
    ask("search-categories", *browse("mode:search", "search:a"))

    artist_tags = ("role_id:ALBUMARTIST", "menu_roles:ALBUMARTIST", "menu_mode:artists")
    for artist in out["mode-artists"].get("item_loop", []):
        artist_id = artist["commonParams"]["artist_id"]
        ask(f"artist-{artist_id}", *browse("mode:albums", f"artist_id:{artist_id}", *artist_tags))
        ask(f"artistinfo-{artist_id}", "artistinfo", "items", 0, 100, "menu:1", f"artist_id:{artist_id}")
    for album in out["mode-albums"].get("item_loop", [])[:6]:
        album_id = album["commonParams"]["album_id"]
        tracks = ask(f"album-{album_id}", *browse("mode:tracks", f"album_id:{album_id}"))
        ask(f"albuminfo-{album_id}", "albuminfo", "items", 0, 100, "menu:1", f"album_id:{album_id}")
        for index, track in enumerate(tracks.get("item_loop", [])[:2]):
            track_id = track["commonParams"]["track_id"]
            ask(f"trackinfo-{album_id}-{index}", "trackinfo", "items", 0, 100, "menu:1", f"track_id:{track_id}", f"play_index:{index}")
            ask(f"playcontrol-{album_id}-{index}", *browse("mode:tracks", f"album_id:{album_id}", f"xmlbrowserPlayControl:{index}"))
    for playlist in out["mode-playlists"].get("item_loop", [])[:3]:
        playlist_id = playlist["commonParams"]["playlist_id"]
        ask(f"playlist-{playlist_id}", *browse("mode:tracks", f"playlist_id:{playlist_id}"))
    for podcast in out["mode-podcasts"].get("item_loop", [])[:2]:
        podcast_id = podcast["commonParams"]["podcast_id"]
        ask(f"podcast-{podcast_id}", *browse("mode:tracks", f"podcast_id:{podcast_id}"))
    for track in out["mode-tracks"].get("item_loop", [])[:3]:
        track_id = track["commonParams"]["track_id"]
        ask(f"track-{track_id}", "trackinfo", "items", 0, 100, "menu:1", f"track_id:{track_id}")
        ask(f"contextmenu-{track_id}", "contextmenu", 0, 100, "context:playlist", f"track_id:{track_id}")
    ask("queue", "status", "-", 10, "menu:menu", "useContextMenu:1")

    # HA Extras answers only for a device with the setting ticked; otherwise it says so.
    ask("haextras-panels", "haextras", "items", 0, 100)
    for panel in ("cameras", "dashboards", "controls", "scenes", "status"):
        ask(f"haextras-{panel}", "haextras", "items", 0, 100, f"panel:{panel}")
    for row in out["haextras-controls"].get("item_loop", [])[:1]:
        entity = (row.get("actions", {}).get("go", {}).get("params") or {}).get("entity")
        if entity:
            ask("haextras-device", "haextras", "items", 0, 100, "panel:device", f"entity:{entity}")
    return out


def row_text(row: dict[str, Any]) -> str:
    return str(row.get("text", "")).split("\n", maxsplit=1)[0]


def describe(before: Any, after: Any) -> str:
    """Say in a line how two replies differ."""
    if not (isinstance(before, dict) and isinstance(after, dict)):
        return "different"
    rows_before, rows_after = before.get("item_loop", []), after.get("item_loop", [])
    if len(rows_before) != len(rows_after):
        return f"{len(rows_before)} rows -> {len(rows_after)} rows"
    changed = [row_text(b) for b, a in zip(rows_before, rows_after, strict=True) if b != a]
    if changed:
        return f"{len(changed)} row(s) differ: {', '.join(changed[:3])}"
    fields = sorted(k for k in set(before) | set(after) if k != "item_loop" and before.get(k) != after.get(k))
    return f"fields differ: {', '.join(fields)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("record", "compare"))
    parser.add_argument("first", help="record: the file to write; compare: the baseline file")
    parser.add_argument("second", nargs="?", help="compare: a second file (default: record a fresh set)")
    parser.add_argument("--config", default=str(HERE.parent / "config.json"))
    parser.add_argument("--server", help="e.g. http://192.168.0.75:9000")
    parser.add_argument("--player", help="the player id (MAC address) the requests are sent for")
    parser.add_argument("--ignore", nargs="*", default=list(DEFAULT_IGNORE), help="requests not to compare")
    args = parser.parse_args()

    config: dict[str, Any] = {}
    if Path(args.config).exists():
        config = json.loads(Path(args.config).read_text())
    server, player = args.server or config.get("server_url"), args.player or config.get("player_id")
    if not server or not player:
        parser.error("give --server and --player, or set server_url and player_id in the E2E config")

    if args.action == "record":
        data = record(Rpc(server, player))
        Path(args.first).write_text(json.dumps(data, indent=1, sort_keys=True))
        errors = [k for k, v in data.items() if isinstance(v, dict) and "error" in v]
        print(f"recorded {len(data)} replies to {args.first}; requests that failed: {errors or 'none'}")
        return 1 if errors else 0

    before = json.loads(Path(args.first).read_text())
    after = json.loads(Path(args.second).read_text()) if args.second else record(Rpc(server, player))
    names = [k for k in before if k not in args.ignore]
    differing = [k for k in names if before[k] != after.get(k)]
    missing = [k for k in names if k not in after]
    print(f"compared {len(names)} replies (ignored: {', '.join(args.ignore)}); differing: {len(differing)}")
    for name in differing:
        print(f"  {name}: {'missing in the new set' if name in missing else describe(before[name], after[name])}")
    return 1 if differing else 0


if __name__ == "__main__":
    sys.exit(main())
