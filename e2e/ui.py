"""Drive JiveLite on a real player through the vkbd helper (see tools/vkbd.c)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from harness import say

# Linux input event codes (linux/input-event-codes.h)
KEYS = {
    "up": 103, "down": 108, "left": 105, "right": 106, "enter": 28, "esc": 1, "backspace": 14,
    "pageup": 104, "pagedown": 109, "shift": 42, "tab": 15, "space": 57, "[": 26, "]": 27,
}
_ROWS = {"qwertyuiop": 16, "asdfghjkl": 30, "zxcvbnm": 44}
for _letters, _first in _ROWS.items():
    for _offset, _letter in enumerate(_letters):
        KEYS[_letter] = _first + _offset
for _offset, _digit in enumerate("1234567890"):
    KEYS[_digit] = 2 + _offset

# Printable characters that are not letters or digits: character -> (key name, needs shift)
_PUNCTUATION = {
    " ": ("space", False), "[": (26, False), "]": (27, False), "-": (12, False), "=": (13, False), ",": (51, False), ".": (52, False),
    "/": (53, False), ";": (39, False), "'": (40, False), "_": (12, True), "+": (13, True),
    "?": (53, True), ":": (39, True), '"': (40, True), "!": (2, True), "@": (3, True), "&": (8, True),
}


def key_script(text: str, hold_ms: int = 40, gap_ms: int = 60) -> str:
    """Turn text into vkbd lines: a press for each character, with Shift for capitals and symbols."""
    lines = []
    for char in text:
        shifted = char.isupper()
        lower = char.lower()
        if lower in KEYS and (char.isalnum()):
            code = KEYS[lower]
        elif char in _PUNCTUATION:
            name, shifted = _PUNCTUATION[char]
            code = KEYS[name] if isinstance(name, str) else name
        else:
            raise ValueError(f"cannot type {char!r}")
        if shifted:
            lines.append(f"d {KEYS['shift']}")
        lines += [f"d {code}", f"s {hold_ms}", f"u {code}"]
        if shifted:
            lines.append(f"u {KEYS['shift']}")
        lines.append(f"s {gap_ms}")
    return "\n".join(lines) + "\n"


def _to_jpeg(source: Path, dest: Path, quality: int = 80) -> None:
    """Compress a BMP screenshot to a JPEG (about 6 MB down to under 100 KB), with Pillow or macOS sips."""
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        subprocess.run(
            ["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality), str(source), "--out", str(dest)],
            check=True, capture_output=True,
        )
        return
    with Image.open(source) as image:
        image.convert("RGB").save(dest, "JPEG", quality=quality, optimize=True)


class HomeNotReached(RuntimeError):
    """The device did not end up on the home screen when a session started."""


class PlayerUi:
    """Send key presses and take screenshots. The password comes from E2E_PLAYER_PASSWORD."""

    def __init__(self, host: str, user: str = "tc") -> None:
        self.target = f"{user}@{host}"
        self._shot_at = 0.0  # when the last screenshot key was sent
        self.preset_count = 0  # presets are the first entries of My Music, ahead of Favorites
        self.preset_counter = None  # optional callable that reads the current number of presets
        self.password = os.environ.get("E2E_PLAYER_PASSWORD", "")

    def _ssh(self, command: str, stdin: str | None = None) -> str:
        prefix = ["sshpass", "-p", self.password] if self.password else []
        out = subprocess.run(
            [*prefix, "ssh", "-o", "StrictHostKeyChecking=no", self.target, command],
            input=stdin, capture_output=True, text=True, timeout=60, check=False,
        )
        return out.stdout

    def ensure_ready(self) -> None:
        """Install the helper and load uinput if the player has been rebooted since (both live in RAM)."""
        if "ok" in self._ssh("test -x /tmp/vkbd && test -e /dev/uinput && echo ok"):
            return
        source = Path(__file__).resolve().parent / "tools" / "vkbd.c"
        binary = source.with_name("vkbd-arm")
        if not binary.exists() or binary.stat().st_mtime < source.stat().st_mtime:
            subprocess.run(
                ["zig", "cc", "-target", "arm-linux-musleabihf", "-static", "-O2", "-s", str(source), "-o", str(binary)],
                check=True,
            )
        prefix = ["sshpass", "-p", self.password] if self.password else []
        subprocess.run([*prefix, "scp", "-q", str(binary), f"{self.target}:/tmp/vkbd"], check=True)
        self._ssh("chmod +x /tmp/vkbd; sudo modprobe uinput")

    # JiveLite drops key presses for a couple of seconds while it saves a screenshot.
    SETTLE_AFTER_SHOT = 3.0

    def keys(self, script: str) -> None:
        wait = self.SETTLE_AFTER_SHOT - (time.time() - self._shot_at)
        if wait > 0:
            time.sleep(wait)
        self.ensure_ready()
        self._ssh("sudo /tmp/vkbd", script)

    def press(self, *names: str, settle: float = 0.4) -> None:
        """Tap each named key in turn."""
        script = ""
        for name in names:
            code = KEYS[name]
            script += f"d {code}\ns 60\nu {code}\ns {int(settle * 1000)}\n"
        self.keys(script)

    def type_text(self, text: str) -> None:
        """Type into a text entry field. Outside one, letters trigger JiveLite's own shortcuts."""
        self.keys(key_script(text))

    def long_press(self, name: str = "enter", seconds: float = 3.5) -> None:
        """A long press on the device: the key held for more than 3 seconds."""
        say(f"Long press ({name} held {seconds:g}s)")
        self.hold(name, seconds)

    def hold(self, name: str, seconds: float = 1.2) -> None:
        code = KEYS[name]
        self.keys(f"d {code}\ns {int(seconds * 1000)}\nu {code}\ns 400\n")

    def screenshot(self, dest: Path) -> Path:
        """Take a JiveLite screenshot (Shift+S) and copy it to dest. Retries if the file does not appear."""
        say(f"Taking a screenshot ({dest.name})")
        name = ""
        for _ in range(3):
            self._ssh("sudo rm -f /tmp/jivelite*.bmp")
            self.keys(f"d {KEYS['shift']}\nd {KEYS['s']}\ns 100\nu {KEYS['s']}\nu {KEYS['shift']}\ns 800\n")
            self._shot_at = time.time()
            for _ in range(8):
                name = self._ssh("ls -t /tmp/jivelite*.bmp 2>/dev/null | head -1").strip()
                if name:
                    time.sleep(0.5)  # let the write finish
                    break
                time.sleep(0.5)
            if name:
                break
        if not name:
            raise RuntimeError("no screenshot appeared on the player")
        prefix = ["sshpass", "-p", self.password] if self.password else []
        raw = dest.with_suffix(".bmp")
        subprocess.run([*prefix, "scp", "-q", f"{self.target}:{name}", str(raw)], check=True)
        if dest.suffix.lower() in (".jpg", ".jpeg"):
            _to_jpeg(raw, dest)
            raw.unlink()
        return dest

    def home(self) -> None:
        """Go to the home screen: h, a second's wait, h again (h also dismisses a screensaver)."""
        self.press("h", settle=1.0)
        self.press("h", settle=0.5)

    HOME_WORDS = ("My Music", "Choose Player", "Quit")

    def start_from_home(self, expect: tuple[str, ...] = HOME_WORDS) -> None:
        """
        Start a session from the home screen, and stop with the evidence if that does not work.

        Screenshot, wait 3 seconds, h, wait 1 second, h, screenshot, then OCR checks that the home
        screen is showing. If it is not, the two screenshots and what was read from them are saved
        in reports/screens/ (reset-failure.json) and HomeNotReached is raised.
        """
        import json  # noqa: PLC0415

        import ocr  # noqa: PLC0415

        folder = Path(__file__).resolve().parent / "reports" / "screens"
        folder.mkdir(parents=True, exist_ok=True)
        say("Start: taking a screenshot of where the device was left")
        start = self.screenshot(folder / "reset-start.jpg")
        start_lines = ocr.read_lines(start)
        say(f"Screen before going home: {start_lines[:6]}")
        say("Waiting 3 seconds, then h, 1 second, h")
        time.sleep(3.0)
        self.press("h", settle=1.0)
        self.press("h", settle=0.5)
        end = self.screenshot(folder / "reset-end.jpg")
        end_lines = ocr.read_lines(end)
        if all(ocr.contains(end_lines, word) for word in expect):
            say("On the home screen")
            return
        record = {
            "when": time.strftime("%Y-%m-%d %H:%M:%S"),
            "expected_on_screen": list(expect),
            "start_screenshot": str(start),
            "start_text": start_lines,
            "end_screenshot": str(end),
            "end_text": end_lines,
        }
        (folder / "reset-failure.json").write_text(json.dumps(record, indent=2))
        raise HomeNotReached(
            f"did not reach the home screen; saved {folder / 'reset-failure.json'} with the start and end screenshots"
        )

    def choose_player(self, row: int) -> None:
        """Home > Choose Player > the player in this row (the list order is fixed by JiveLite)."""
        say(f"Navigating: Home > Choose Player > row {row + 1}")
        self.home()
        self.to_top()
        self.press("right", "right", "enter", settle=2.5)  # Choose Player is the third home entry
        self.to_top()
        self.press(*["down"] * row, settle=0.4) if row else None
        self.press("enter", settle=3.0)

    def reset(self, server_log: object, player_id: str, player_row: int = 0) -> None:
        """Start from a known place: home, with the UI controlling this player.

        The player the UI controls survives reboots, so it is checked by the player id on the
        requests a browse sends, and switched through Choose Player if it is the wrong one.
        """
        self.start_from_home()
        for attempt in range(3):
            self.open_my_music("Favorites")
            seen = server_log.browse_player(server_log.since(25))  # type: ignore[attr-defined]
            self.home()
            if seen == player_id:
                return
            self.choose_player(player_row)
        raise RuntimeError(f"the UI still controls {seen}, not {player_id}")

    # Grids behave as one list: Right moves to the next entry (wrapping to the next row) and Up
    # moves back one, stopping at the first entry. So Up x N resets the selection to the top.
    MY_MUSIC = [
        "Favorites", "Artists", "Albums", "Tracks", "Playlists",
        "Audiobooks", "Podcasts", "Radio", "Search", "Switch Library",
    ]

    def to_top(self) -> None:
        self.press(*["up"] * 12, settle=0.1)

    def open_my_music(self, label: str) -> None:
        """From anywhere: go home, open My Music, then open the entry called label."""
        say(f"Navigating: Home > My Music > {label}")
        self.press("h", "h", settle=2.0)
        self.to_top()
        self.press("right", "enter", settle=2.5)  # My Music is the second home entry
        self.to_top()
        presets = self.preset_counter() if self.preset_counter else self.preset_count
        self.press(*["right"] * (presets + self.MY_MUSIC.index(label)), settle=0.2)
        self.press("enter", settle=2.0)
