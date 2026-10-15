"""Drive a Squeezebox Radio (SqueezePlay) over ssh: key presses by writing input events, screenshots from the framebuffer.

The Radio has no uinput and no screenshot hotkey, so the two pieces JiveLite's driver (ui.py) uses are replaced:

- Keys: 16-byte input_event structs written to the evdev nodes. The keypad (event0) has Home, Back, the presets and the
  transport keys, the front panel (event1) has Go (the scroll knob's push), Mute (the volume knob's push) and Power, and the knob chip (event2) turns the
  scroll knob (REL_WHEEL, code 8: -1 is clockwise, so down) and the volume knob (code 9: -1 raises the volume).
- Screenshots: /dev/fb0 holds the 240x320 RGB565 screen; one frame is copied into a file on the Radio first (fast) and
  fetched and converted afterwards.

It offers the same device actions as PlayerUi (go_home, select, back, up, down, long_select, power_on, power_off, mute,
volume_up, volume_down, screenshot, reboot, reset), so a case written with those runs on either device. Navigating My Music
by raw JiveLite keys is not supported: a case that does it asks for the JiveLite driver (Ctx.require_ui).

The Radio drops a click that comes too soon after another key, so every key waits MIN_GAP seconds after the last one, and the knob is
only used for the volume: scrolling lists by injected knob clicks is unreliable on the Radio (see walkthrough/radio.py, which
navigates with the Radio's own macro engine instead).
"""

from __future__ import annotations

import os
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from harness import say

# Key codes (linux/input-event-codes.h), measured on the Radio
KEYPAD = "event0"
FRONT = "event1"
KNOB = "event2"
KEYS = {
    "home": (KEYPAD, 102), "back": (KEYPAD, 105), "play": (KEYPAD, 207), "play_pause": (KEYPAD, 164),
    "next": (KEYPAD, 163), "previous": (KEYPAD, 165),
    "select": (FRONT, 106), "mute": (FRONT, 113), "power": (FRONT, 116),
    **{f"preset{i}": (KEYPAD, 58 + i) for i in range(1, 7)},
}
SCROLL = 8  # EV_REL code of the scroll knob
VOLUME = 9  # EV_REL code of the volume knob: only the volume_* actions may use it

WIDTH, HEIGHT = 240, 320  # the panel's native (portrait) size, RGB565, first of two pages
FRAME = WIDTH * HEIGHT * 2
MIN_GAP = 0.6
HOME_WORDS = ("Now Playing", "My Music", "Settings")


def _event(kind: int, code: int, value: int) -> bytes:
    return struct.pack("<IIHHi", 0, 0, kind, code, value)


def _printf(data: bytes) -> str:
    return "".join(f"\\{b:03o}" for b in data)






class RadioUi:
    """Send key presses and take screenshots on a Squeezebox Radio. Password: E2E_RADIO_PASSWORD (default 1234)."""

    kind = "radio"

    def __init__(self, host: str, user: str = "root") -> None:
        self.target = f"{user}@{host}"
        self.password = os.environ.get("E2E_RADIO_PASSWORD", "1234")
        self._socket = f"/tmp/e2e-radio-{host}.sock"
        self._last_key = 0.0
        self.preset_count = 0  # kept so run.py can treat both drivers alike
        self.preset_counter = None
        self._options = [
            "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=8",
            "-oKexAlgorithms=+diffie-hellman-group1-sha1", "-oHostKeyAlgorithms=+ssh-rsa",
            "-oCiphers=+aes256-cbc", "-oMACs=+hmac-sha1",
        ]

    # -- ssh (one shared connection: the Radio's old ssh takes seconds to set up)
    def _master(self) -> None:
        check = subprocess.run(["ssh", "-S", self._socket, "-O", "check", self.target], capture_output=True)
        if check.returncode != 0:
            subprocess.run(
                ["sshpass", "-p", self.password, "ssh", "-M", "-S", self._socket, "-o", "ControlPersist=300",
                 *self._options, self.target, "true"],
                check=True, timeout=30,
            )

    def _ssh(self, command: str, timeout: int = 60) -> bytes:
        self._master()
        return subprocess.run(
            ["ssh", "-S", self._socket, *self._options, self.target, command],
            capture_output=True, check=True, timeout=timeout,
        ).stdout

    # -- keys
    def _pace(self) -> None:
        wait = MIN_GAP - (time.time() - self._last_key)
        if wait > 0:
            time.sleep(wait)

    def _write(self, node: str, data: bytes, hold: float = 0.0, release: bytes = b"") -> None:
        self._pace()
        command = f"printf '{_printf(data)}' > /dev/input/{node}"
        if release:
            command += f"; usleep {int(hold * 1_000_000)}; printf '{_printf(release)}' > /dev/input/{node}"
        self._ssh(command)
        self._last_key = time.time()

    def _key(self, name: str, hold: float = 0.0) -> None:
        node, code = KEYS[name]
        down = _event(1, code, 1) + _event(0, 0, 0)
        up = _event(1, code, 0) + _event(0, 0, 0)
        if hold:
            self._write(node, down, hold, up)
        else:
            self._write(node, down + up)

    def _turn(self, code: int, value: int) -> None:
        self._write(KNOB, _event(2, code, value) + _event(0, 0, 0))

    def press(self, *names: str, settle: float = 0.4) -> None:
        """Tap each named key in turn (names as in KEYS)."""
        for name in names:
            self._key(name)
            time.sleep(settle)

    # -- device actions (the same names as PlayerUi)
    def go_home(self) -> None:
        """One Home press. On the home menu itself it opens Now Playing, so callers look at the screen after it."""
        # a key right after another is ignored for about 2 seconds
        self.press("home", settle=2.5)

    home = go_home

    def select(self) -> None:
        self.press("select", settle=2.0)

    def back(self) -> None:
        self.press("back", settle=1.0)

    def long_select(self, seconds: float = 3.5) -> None:
        say(f"Long press (select held {seconds:g}s)")
        self._key("select", hold=seconds)
        time.sleep(0.4)

    def down(self, clicks: int = 1) -> None:
        for _ in range(clicks):
            self._turn(SCROLL, -1)  # clockwise
        time.sleep(0.3)

    def up(self, clicks: int = 1) -> None:
        for _ in range(clicks):
            self._turn(SCROLL, 1)
        time.sleep(0.3)

    def to_top(self) -> None:
        self.up(12)

    def power_on(self) -> None:
        """The Radio has one power key that toggles, so the caller must know the state it is in."""
        self.press("power", settle=1.5)

    power_off = power_on

    def mute(self) -> None:
        """The mute key, which is the push of the volume knob; pressing it again unmutes."""
        self.press("mute", settle=1.0)

    def volume_up(self, clicks: int = 1) -> None:
        for _ in range(clicks):
            self._turn(VOLUME, -1)
        time.sleep(0.5)

    def volume_down(self, clicks: int = 1) -> None:
        for _ in range(clicks):
            self._turn(VOLUME, 1)
        time.sleep(0.5)

    def open_my_music(self, label: str) -> None:
        raise NotImplementedError("navigating My Music by key is only written for the JiveLite driver")

    def reboot(self) -> None:
        """Reboot the Radio (what a power cycle does); it reconnects to the server by itself."""
        try:
            self._ssh("reboot", timeout=15)
        except subprocess.SubprocessError:
            pass  # the connection drops as it goes down

    # -- screen
    def screenshot(self, dest: Path, tries: int = 3) -> Path:
        """Copy a frame of the screen (landscape, as you see it) to dest (.jpg or .png)."""
        from PIL import Image  # noqa: PLC0415

        say(f"Taking a screenshot ({dest.name})")
        name = f"e2e_{os.getpid()}"
        raw = b""
        for _ in range(tries):
            # on the Radio itself: freeze the frame first, move it afterwards
            self._ssh(f"head -c {FRAME} /dev/fb0 > /tmp/{name}.raw")
            raw = self._ssh(f"cat /tmp/{name}.raw")
            if len(raw) == FRAME:
                break
        self._ssh(f"rm -f /tmp/{name}.raw")
        if len(raw) != FRAME:
            raise RuntimeError("no screenshot came back from the Radio")
        pixels = bytearray()
        for i in range(0, FRAME, 2):
            value = raw[i] | raw[i + 1] << 8
            pixels += bytes(((value >> 11 & 31) * 255 // 31, (value >> 5 & 63) * 255 // 63, (value & 31) * 255 // 31))
        image = Image.frombytes("RGB", (WIDTH, HEIGHT), bytes(pixels)).rotate(-90, expand=True)
        if dest.suffix.lower() in (".jpg", ".jpeg"):
            image.save(dest, "JPEG", quality=90)
        else:
            image.save(dest)
        return dest








    def ensure_home(self) -> None:
        """Get to the home menu from wherever the Radio is. Home is a toggle, so look at the screen after each press."""
        import ocr  # noqa: PLC0415

        for _ in range(3):
            with tempfile.TemporaryDirectory() as tmp:
                lines = ocr.read_lines(self.screenshot(Path(tmp) / "home.png"))
            if all(ocr.contains(lines, word) for word in HOME_WORDS):
                return
            self.go_home()
        raise RuntimeError("could not get the Radio to its home menu")

    def reset(
        self, server_log: object, player_id: str, player_row: int = 0, player_name: str | None = None
    ) -> None:
        """Start from a known place: the home menu, with a check that it is this player's.

        Home is a toggle on the Radio: pressed on the home menu it opens Now Playing. So press it once, look, and press
        again only when the screen is not the home menu (the first press after a quiet spell may only wake the screen).
        """
        import ocr  # noqa: PLC0415

        lines: list[str] = []
        missing = list(HOME_WORDS)
        for attempt in (1, 2, 3):
            say(f"Navigating: Home (press {attempt})")
            self.go_home()
            with tempfile.TemporaryDirectory() as tmp:
                lines = ocr.read_lines(self.screenshot(Path(tmp) / "home.png"))
            missing = [w for w in HOME_WORDS if not ocr.contains(lines, w)]
            if not missing and (not player_name or ocr.contains(lines, player_name)):
                return
        raise RuntimeError(f"the Radio is not on its home menu for {player_name}: read {lines[:8]}, missing {missing}")
