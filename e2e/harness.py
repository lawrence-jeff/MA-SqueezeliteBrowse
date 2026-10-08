"""
Helpers for the end-to-end tests: a JSON-RPC client, log readers and the shared checks.

Everything here is standard library only. The tests drive the same LMS-style JSON-RPC
interface the devices use (so they exercise the patched server code), watch the
piCorePlayer's JiveLite log for popups and Lua errors, and optionally read the Music
Assistant server log over ssh.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"

# Server log lines that are expected and not a problem.
SERVER_LOG_NOISE = (
    "No handler for alarms",
    "disconnected prematurely from stream",
    "Error unloading player",
    "Invalid command: sendspin",
)
# Client Lua errors that are known product issues: reported as warnings, not failures.
KNOWN_CLIENT_ERRORS = {
    "Player.lua:271: attempt to index": (
        "known issue: the client indexes item_loop[1] of a status push whose item_loop is empty"
    ),
}
# Server log markers that mean a handler or the icon route failed.
SERVER_LOG_ERRORS = (
    "Traceback",
    "Error handling CometD request",
    "BadStatusLine",
    "TypeError",
    "NotImplementedError",
)


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


@dataclass
class CaseResult:
    case_id: str
    title: str
    covers: str
    checks: list[Check] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0

    @property
    def status(self) -> str:
        if self.error or any(c.status == FAIL for c in self.checks):
            return FAIL
        if any(c.status == WARN for c in self.checks):
            return WARN
        if self.checks and all(c.status == SKIP for c in self.checks):
            return SKIP
        return PASS


class Rpc:
    """LMS-style JSON-RPC client for one player, the interface the devices talk to."""

    def __init__(self, base_url: str, player_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.url = self.base_url + "/jsonrpc.js"
        self.player_id = player_id

    def call(self, *cmd: Any, player: bool = True) -> dict[str, Any]:
        body = json.dumps(
            {
                "id": 1,
                "method": "slim.request",
                "params": [self.player_id if player else "", list(cmd)],
            }
        ).encode()
        req = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data.get("result") or {}

    # -- reads
    def status(self) -> dict[str, Any]:
        return self.call("status", "-", 1, "tags:acdIKlNorTuxQ")

    def queue(self) -> tuple[list[dict[str, Any]], int, int]:
        """Return (rows, queue size, current index) as the queue view would show them."""
        result = self.call("status", 0, 200, "menu:menu", "useContextMenu:1")
        rows = [r for r in result.get("item_loop", []) if "item_loop" not in r]
        return rows, int(result.get("playlist_tracks", 0)), int(result.get("playlist_cur_index", 0))

    def browse(self, mode: str, index: int = 0, quantity: int = 50, **tags: Any) -> dict[str, Any]:
        extra = [f"{key}:{value}" for key, value in tags.items()]
        return self.call("browselibrary", "items", index, quantity, "menu:1", f"mode:{mode}", *extra)

    def info_menu(self, kind: str, **tags: Any) -> list[str]:
        """Return the row texts of a long-press menu (trackinfo, albuminfo, artistinfo)."""
        extra = [f"{key}:{value}" for key, value in tags.items()]
        result = self.call(kind, "items", 0, 200, "menu:1", *extra)
        return [row.get("text", "") for row in result.get("item_loop", [])]

    def context_menu(self, index: int) -> dict[str, Any]:
        return self.call(
            "contextmenu",
            0,
            200,
            f"playlist_index:{index}",
            "menu:track",
            "context:playlist",
            "useContextMenu:1",
        )

    # -- actions
    def play_control(self, **tags: Any) -> None:
        self.call("playlistcontrol", "menu:1", *[f"{key}:{value}" for key, value in tags.items()])

    def clear(self) -> None:
        self.call("playlist", "clear")

    def mixer(self, what: str, value: Any = "?") -> Any:
        return self.call("mixer", what, value).get("_mixer")

    def players(self) -> list[dict[str, Any]]:
        return self.call("players", 0, 20, player=False).get("players_loop", [])


class ClientLog:
    """The piCorePlayer's JiveLite log, read through its web UI."""

    def __init__(self, url: str, player_id: str) -> None:
        self.url = url
        self.player_id = player_id

    def fetch(self) -> list[str]:
        cache_buster = f"&t={int(time.time() * 1000)}"
        req = urllib.request.Request(self.url + cache_buster, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = html.unescape(resp.read().decode("utf-8", errors="replace"))
        return [ln.strip() for ln in text.splitlines() if re.match(r"^\d{8} \d\d:\d\d:\d\d\.\d{3} ", ln.strip())]

    def mark(self) -> str:
        rows = self.fetch()
        return rows[-1][:23] if rows else ""

    def since(self, mark: str) -> list[str]:
        return [row for row in self.fetch() if row[:23] > mark]

    def popups(self, rows: list[str]) -> int:
        """Count the displaystatus pushes (showBriefly popups) the client received."""
        needle = f"/slim/displaystatus/{self.player_id}"
        return sum(1 for r in rows if needle in r and "_response" in r)

    @staticmethod
    def errors(rows: list[str]) -> list[str]:
        return [r for r in rows if re.match(r"^\S+ \S+ ERROR\b", r)]


class ServerLog:
    """The Music Assistant container log, read over ssh."""

    def __init__(self, ssh: str, key: str, container: str) -> None:
        self.ssh, self.key, self.container = ssh, key, container

    def since(self, seconds: int) -> list[str]:
        cmd = [
            "ssh",
            "-i",
            self.key,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            self.ssh,
            f"sudo docker logs {self.container} --since {int(seconds)}s 2>&1",
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False).stdout
        return [ln for ln in out.splitlines() if "alarms 0 99" not in ln]

    @staticmethod
    def browse_requests(lines: list[str], player_id: str) -> list[str]:
        """The menus the client opened, e.g. "mode:artists" or "item_id:album-14", oldest first.

        Each screen JiveLite opens sends one `browselibrary items` request; its mode and item_id
        say which menu it was, so the server log tells what the player is showing.
        """
        found = []
        for line in lines:
            if "Handling request" not in line or player_id not in line or "'browselibrary'" not in line:
                continue
            params = re.findall(r"'((?:mode|item_id|menu_id|sub):[^']*)'", line)
            found.append(" ".join(params) or "browselibrary")
        return found

    @staticmethod
    def problems(lines: list[str]) -> list[str]:
        found = []
        for line in lines:
            if any(noise in line for noise in SERVER_LOG_NOISE):
                continue
            if any(marker in line for marker in SERVER_LOG_ERRORS):
                found.append(line.strip()[:200])
        return found


def icon_url(base_url: str, icon: str, size: str = "225x225_m") -> str:
    """Build the artwork URL the way JiveLite does from a row's icon."""
    if re.fullmatch(r"[0-9a-fA-F-]+", icon):
        return f"{base_url}/music/{icon}/cover_{size}"
    path = re.sub(r"(.+)(\.[A-Za-z]+)$", rf"\1_{size}\2", icon)
    return f"{base_url}/{path.lstrip('/')}"


def check_icon(base_url: str, icon: str | None) -> Check:
    """Fetch one icon and report whether it is a real image."""
    if not icon:
        return Check("icon", FAIL, "blank icon (the client has nothing to fetch)")
    if icon.startswith("http"):
        return Check("icon", FAIL, f"raw URL reached the client: {icon[:90]}")
    url = icon_url(base_url, icon)
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "")
    except (urllib.error.URLError, TimeoutError, ValueError) as err:
        return Check("icon", FAIL, f"{icon}: {err}")
    if not ctype.startswith("image/") or not body:
        return Check("icon", FAIL, f"{icon}: not an image ({ctype}, {len(body)}B)")
    if ctype == "image/png" and len(body) < 400:
        return Check("icon", WARN, f"{icon}: placeholder image ({len(body)}B)")
    return Check("icon", PASS, f"{icon}: {ctype} {len(body)}B")


def wait_until(fn: Callable[[], bool], timeout: float = 25, interval: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return False


@dataclass
class Marker:
    client: str | None
    started: float


class Ctx:
    """Shared state and check helpers handed to every test case."""

    def __init__(
        self,
        rpc: Rpc,
        client_log: ClientLog | None,
        server_log: ServerLog | None,
        media: dict[str, dict[str, Any]],
    ) -> None:
        self.rpc = rpc
        self.client_log = client_log
        self.server_log = server_log
        self.media = media
        self.result: CaseResult | None = None

    # -- recording
    def check(self, name: str, ok: bool, detail: str = "", *, warn: bool = False) -> bool:
        assert self.result is not None
        status = PASS if ok else (WARN if warn else FAIL)
        self.result.checks.append(Check(name, status, detail))
        return ok

    def skip(self, name: str, detail: str) -> None:
        assert self.result is not None
        self.result.checks.append(Check(name, SKIP, detail))

    def add(self, check: Check, prefix: str = "") -> None:
        assert self.result is not None
        check.name = f"{prefix}{check.name}"
        self.result.checks.append(check)

    # -- steps
    def begin(self) -> Marker:
        mark = self.client_log.mark() if self.client_log else None
        return Marker(mark, time.time())

    def verify_clean(self, marker: Marker, *, expect_popup: bool, settle: float = 2.5) -> None:
        """Check the client log (popup arrived, no Lua errors) and the server log."""
        time.sleep(settle)
        if self.client_log and marker.client is not None:
            rows = self.client_log.since(marker.client)
            if expect_popup:
                n = self.client_log.popups(rows)
                self.check("popup reached the client", n >= 1, f"{n} displaystatus push(es)")
            errors = self.client_log.errors(rows)
            known = [e for e in errors if any(k in e for k in KNOWN_CLIENT_ERRORS)]
            other = [e for e in errors if e not in known]
            self.check("no client Lua errors", not other, "; ".join(e[:140] for e in other[:3]))
            if known:
                label = next(v for k, v in KNOWN_CLIENT_ERRORS.items() if k in known[0])
                self.check("no known client Lua errors", False, f"{len(known)}x {label}", warn=True)
        else:
            self.skip("client log checks", "client log not configured")
        if self.server_log:
            age = int(time.time() - marker.started) + 3
            problems = self.server_log.problems(self.server_log.since(age))
            self.check("no server errors", not problems, "; ".join(problems[:3]))
        else:
            self.skip("server log checks", "server log not configured")

    def verify_queue(
        self, *, length: int | None = None, current: int | None = None, icons: bool = True
    ) -> list[dict[str, Any]]:
        rows, size, cur = self.rpc.queue()
        if length is not None:
            self.check("queue length", size == length, f"expected {length}, got {size}")
        if current is not None:
            self.check("current index", cur == current, f"expected {current}, got {cur}")
        if icons:
            for row in rows[:8]:
                self.add(
                    check_icon(self.rpc.base_url, row.get("icon")),
                    prefix=f"queue row '{(row.get('text') or '')[:28].splitlines()[0]}': ",
                )
        return rows

    def verify_playing(
        self, expect_name: str | None = None, *, progress: bool = True, current: int | None = None
    ) -> None:
        playing = wait_until(lambda: self.rpc.status().get("mode") == "play", timeout=30)
        if playing and current is not None:
            # Play All starts the album and then jumps to the chosen track.
            wait_until(lambda: self.rpc.queue()[2] == current, timeout=15, interval=0.5)
        self.check("player is playing", playing, f"mode={self.rpc.status().get('mode')}")
        if not playing:
            return
        if expect_name:
            rows, _, cur = self.rpc.queue()
            text = rows[cur]["text"] if 0 <= cur < len(rows) else ""
            self.check(
                "playing the expected item",
                expect_name.lower() in text.lower(),
                f"queue row {text.splitlines()[0] if text else '(none)'!r} vs {expect_name!r}",
            )
        if progress:
            # Let the new item's position settle (the first reading can still be the
            # previous item's), then compare two readings a few seconds apart.
            time.sleep(4)
            first = float(self.rpc.status().get("time", 0) or 0)
            time.sleep(4)
            second = float(self.rpc.status().get("time", 0) or 0)
            self.check("elapsed time advances", second > first, f"{first:.1f}s -> {second:.1f}s")

    def reset(self) -> None:
        """Empty the queue and stop playback so the next case starts from idle."""
        try:
            self.rpc.clear()
            wait_until(lambda: self.rpc.status().get("mode") != "play", timeout=10)
            time.sleep(3)  # the client logs the clear's status pushes a moment later
        except (urllib.error.URLError, TimeoutError):
            pass
