"""A UI for the e2e cases that works through the resident test agent instead of the keyboard: the cases call the same methods
(press, to_top, long_press, home, open_my_music, power_on, ...), but a move is a command to the agent (no key presses, no fixed
sleeps: it waits until the window has settled), and the reply says what the screen shows, kept in .screen for checks.

Everything the agent does not cover (screenshots of the real display, reset, reboot) goes to the device's own UI object.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from harness import say

from .driver import Agent

# a key name of the keyboard UI -> what the agent does: "a:<action>" is a UI action, "s:<n>" moves the selection
KEY_TOKENS = {
    "right": "s:+1", "down": "s:+1", "left": "s:-1", "up": "s:-1", "pagedown": "s:+8", "pageup": "s:-8",
    "enter": "a:go", "esc": "a:back", "backspace": "a:back", "h": "a:go_home", "n": "a:go_home_or_now_playing",
    "space": "a:pause", "p": "a:play", "x": "a:play", "g": "a:stop", "k": "a:power_on", "i": "a:power_off", "q": "a:power",
    "u": "a:mute", "-": "a:volume_down", "=": "a:volume_up", "[": "a:go_now_playing", "]": "a:go_playlist",
    "t": "a:go_current_track_info", "f": "a:go_favorites", "b": "a:jump_fwd", "z": "a:jump_rew", "d": "a:add_end",
    "y": "a:play_next", "m": "a:create_mix", "e": "a:scanner_rew", "r": "a:scanner_fwd", "j": "a:back", "l": "a:go",
    ",": "a:shuffle_toggle", ".": "a:repeat_toggle", "/": "a:go_search",
}


class AgentUi:
    def __init__(self, base: Any, agent: Agent) -> None:
        self.base = base
        self.agent = agent
        self.kind = getattr(base, "kind", "jivelite")
        self.screen: dict = {}
        self.preset_counter = None  # not needed: entries are found by name

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def send(self, verb: str, arg: str = "") -> dict:
        try:
            reply = self.agent.send(verb, arg, timeout=45)
        except (TimeoutError, OSError, subprocess.SubprocessError):
            # the agent is gone: the device rebooted (its folder is in RAM) or its UI restarted. Install and start it again.
            say("The test agent does not answer; starting it again")
            self.agent.start()
            reply = self.agent.send(verb, arg)
        self.screen = reply.get("screen", {})
        if not reply.get("ok"):
            raise RuntimeError(f"agent: {verb} {arg}: {reply.get('note') or 'failed'} (screen: {self.screen.get('title')})")
        return reply

    # -- the keyboard UI's methods --
    def press(self, *names: str, settle: float = 0.4) -> None:
        tokens = [KEY_TOKENS[name] for name in names]
        # neighbouring moves of the selection are one move
        merged: list[str] = []
        for token in tokens:
            if token.startswith("s:") and merged and merged[-1].startswith("s:"):
                merged[-1] = f"s:{int(merged[-1][2:]) + int(token[2:])}"
            else:
                merged.append(token)
        # the caller's settle time is how long the device is given to react (a started stream, a loaded queue) before it reads it
        merged.append(f"w:{int(settle * 1000)}")
        self.send("do", " ".join(merged))

    def to_top(self) -> None:
        self.send("do", "s:-500")

    def home(self) -> None:
        self.send("home")

    go_home = home

    def select(self) -> None:
        self.send("go")

    def back(self) -> None:
        self.send("back")

    def long_press(self, name: str = "enter", seconds: float = 3.5) -> None:
        self.send("long_press")

    long_select = long_press

    def power_on(self) -> None:
        self.send("do", "a:power_on")

    def power_off(self) -> None:
        self.send("do", "a:power_off")

    def mute(self) -> None:
        self.send("do", "a:mute")

    def volume_up(self, clicks: int = 1) -> None:
        self.send("do", " ".join(["a:volume_up"] * clicks))

    def volume_down(self, clicks: int = 1) -> None:
        self.send("do", " ".join(["a:volume_down"] * clicks))

    def open_my_music(self, label: str) -> None:
        self.send("home")
        self.send("openid", "myMusicSelector")
        self.send("open", label)

    def reset(self, server_log: object, player_id: str, player_row: int = 0, player_name: str | None = None) -> None:
        """Home, with the UI controlling this player (the home screen's title is the player's name)."""
        from ui import HomeNotReached  # noqa: PLC0415

        self.send("home")
        if player_name and self.screen.get("title") != player_name:
            raise HomeNotReached(f"the home screen is titled {self.screen.get('title')!r}, not {player_name!r}")

    def screenshot(self, dest: Path, tries: int = 3) -> Path:
        """The screen as a JPEG, drawn by the player itself: no popup is left on top (the keyboard screenshot leaves one)."""
        from walkthrough.macro import bmp_to_jpeg  # noqa: PLC0415

        say(f"Taking a screenshot ({dest.name})")
        walker, folder = self.agent.walker, self.agent.folder
        walker.sh(f"rm -f {folder}/e2e_snap.bmp")
        self.send("shot", "e2e_snap")
        data = walker.sh(f"cat {folder}/e2e_snap.bmp", timeout=120)
        if not data:
            raise RuntimeError("the agent saved no screenshot")
        return bmp_to_jpeg(data, dest.with_suffix(".jpg"))

    def screen_text(self) -> list[str]:
        """What the screen shows as lines of text: the window's title, then the text of each menu row (one line per row line)."""
        self.send("dump")
        lines = [self.screen.get("title") or ""]
        for item in self.screen.get("items", []):
            if isinstance(item, dict):
                lines.extend(part for part in str(item.get("text", "")).splitlines() if part)
        return [line for line in lines if line]

    # -- what the screen shows (from the last reply) --
    def titles(self) -> list[str]:
        return [item.get("text", "") for item in self.screen.get("items", []) if isinstance(item, dict)]
