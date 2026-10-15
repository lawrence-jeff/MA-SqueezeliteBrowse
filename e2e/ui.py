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
    "-": 12, "=": 13,
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

    def reboot(self) -> None:
        """Reboot the player (what a power cycle does); it reconnects to the server by itself."""
        self._ssh("sudo reboot")

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

    def screenshot(self, dest: Path, tries: int = 3) -> Path:
        """Take a JiveLite screenshot (Shift+S) and copy it to dest. Retries if the file does not appear."""
        say(f"Taking a screenshot ({dest.name})")
        name = ""
        for _ in range(tries):
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

    def start_from_home(self, expect: tuple[str, ...] = HOME_WORDS) -> list[str]:
        """
        Start a session from the home screen, and stop with the evidence if that does not work.

        Screenshot, wait 3 seconds, k (power on), wait 2 seconds, h, wait 1 second, h, screenshot, then OCR checks that the home
        screen is showing. If it is not, the two screenshots and what was read from them are saved
        in reports/screens/ (reset-failure.json) and HomeNotReached is raised.
        """
        import json  # noqa: PLC0415

        import ocr  # noqa: PLC0415

        folder = Path(__file__).resolve().parent / "reports" / "screens"
        folder.mkdir(parents=True, exist_ok=True)
        say("Start: taking a screenshot of where the device was left")
        start = folder / "reset-start.jpg"
        start.unlink(missing_ok=True)
        try:
            start_lines = ocr.read_lines(self.screenshot(start, tries=1))
            say(f"Screen before going home: {start_lines[:6]}")
        except RuntimeError:
            # A powered-off player ignores every key, the screenshot key included.
            start_lines = []
            say("No screenshot came back (the player may be powered off); carrying on with the power-on key")
        say("Waiting 3 seconds, then k (power on), 2 seconds, h, 1 second, h")
        time.sleep(3.0)
        # A player that is soft powered off shows a screensaver that ignores every key but the power ones;
        # k is JiveLite's "power on" and does nothing when the player is already on.
        self.press("k", settle=2.0)
        self.press("h", settle=1.0)
        self.press("h", settle=0.5)
        end = self.screenshot(folder / "reset-end.jpg")
        end_lines = ocr.read_lines(end)
        if all(ocr.contains(end_lines, word) for word in expect):
            say("On the home screen")
            return end_lines
        record = {
            "when": time.strftime("%Y-%m-%d %H:%M:%S"),
            "expected_on_screen": list(expect),
            "start_screenshot": str(start) if start.exists() else None,
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

    def reset(
        self, server_log: object, player_id: str, player_row: int = 0, player_name: str | None = None
    ) -> None:
        """Start from a known place: home, with the UI controlling this player.

        The player the UI controls survives reboots, so it is checked and switched through Choose
        Player if it is the wrong one. With player_name the check reads the title of the home
        screen, which names the player (no navigation). Without it, the player id on the request
        a browse sends is used instead.
        """
        import ocr  # noqa: PLC0415

        lines = self.start_from_home()
        if player_name:
            for _ in range(2):
                if ocr.contains(lines, player_name):
                    return
                say(f"The home screen does not show '{player_name}'; choosing it in Choose Player")
                self.choose_player(player_row)
                self.home()
                with tempfile.TemporaryDirectory() as tmp:
                    lines = ocr.read_lines(self.screenshot(Path(tmp) / "home.jpg"))
            if ocr.contains(lines, player_name):
                return
            raise RuntimeError(f"the UI does not control {player_name}: the home screen reads {lines[:6]}")
        for _ in range(3):
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

    # Home grid geometry at 1920x1080: tiles 384 wide and 306 high from y=147, five to a row.
    def highlighted_tile_label(self) -> str:
        """The label under the highlighted tile of a grid screen (the box around it is grey on the purple wallpaper)."""
        import ocr  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            shot = self.screenshot(Path(tmp) / "tiles.jpg")
            image = Image.open(shot).convert("RGB")

            def grayness(box: tuple[int, int, int, int]) -> float:
                pixels = list(image.crop(box).resize((8, 8)).getdata())
                red = sum(p[0] for p in pixels) / len(pixels)
                blue = sum(p[2] for p in pixels) / len(pixels)
                return -abs(red - blue)

            best = max(
                ((grayness((c * 384 + 4, 147 + r * 306 + 4, c * 384 + 60, 147 + r * 306 + 100)), c, r) for r in range(3) for c in range(5)),
            )
            _, col, row = best
            crop = Path(tmp) / "label.png"
            image.crop((col * 384, 147 + row * 306 + 236, col * 384 + 384, 147 + row * 306 + 300)).save(crop)
            return " ".join(ocr.read_lines(crop))

    def quit_jivelite(self) -> None:
        """Close JiveLite from the Quit tile of the home menu (its wrapper script starts it again).

        Moves along the tiles until the highlighted one reads Quit and only then presses Enter, so no other tile is ever chosen.
        """
        say("Closing JiveLite from its Quit tile")
        self.home()
        self.to_top()
        for _ in range(7):
            label = self.highlighted_tile_label()
            if "quit" in label.lower():
                self.press("enter", settle=1.0)
                return
            self.press("right", settle=0.5)
        raise RuntimeError("could not find the Quit tile on the home menu")

    def to_top(self) -> None:
        self.press(*["up"] * 12, settle=0.1)

    # -- Device actions shared with the Squeezebox Radio driver (ui_radio.RadioUi). A case that only needs these
    # works on either device; the raw key names above are JiveLite's own and are used by the cases that
    # navigate lists. On JiveLite each action is a hotkey.
    kind = "jivelite"

    def go_home(self) -> None:
        self.home()

    def select(self) -> None:
        self.press("enter", settle=2.0)

    def back(self) -> None:
        self.press("esc", settle=1.0)

    def long_select(self) -> None:
        self.long_press("enter")

    def power_on(self) -> None:
        self.press("k", settle=1.0)

    def power_off(self) -> None:
        self.press("i", settle=1.0)

    def mute(self) -> None:
        """The mute key; pressing it again unmutes."""
        self.press("u", settle=1.0)

    def volume_up(self, clicks: int = 1) -> None:
        for _ in range(clicks):
            self.press("=", settle=0.5)

    def volume_down(self, clicks: int = 1) -> None:
        for _ in range(clicks):
            self.press("-", settle=0.5)

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
