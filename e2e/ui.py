"""Drive JiveLite on a real player through the vkbd helper (see tools/vkbd.c)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

# Linux input event codes
KEYS = {
    "up": 103, "down": 108, "left": 105, "right": 106, "enter": 28, "esc": 1, "backspace": 14,
    "pageup": 104, "pagedown": 109, "shift": 42,
    "a": 30, "b": 48, "d": 32, "f": 33, "h": 35, "j": 36, "k": 37, "l": 38, "n": 49, "s": 31,
    "t": 20, "p": 25, "x": 45, "z": 44,
}


class PlayerUi:
    """Send key presses and take screenshots. The password comes from E2E_PLAYER_PASSWORD."""

    def __init__(self, host: str, user: str = "tc") -> None:
        self.target = f"{user}@{host}"
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
        binary = Path(tempfile.gettempdir()) / "vkbd-arm"
        subprocess.run(
            ["zig", "cc", "-target", "arm-linux-musleabihf", "-static", "-O2", str(source), "-o", str(binary)],
            check=True,
        )
        prefix = ["sshpass", "-p", self.password] if self.password else []
        subprocess.run([*prefix, "scp", "-q", str(binary), f"{self.target}:/tmp/vkbd"], check=True)
        self._ssh("chmod +x /tmp/vkbd; sudo modprobe uinput")

    def keys(self, script: str) -> None:
        self.ensure_ready()
        self._ssh("sudo /tmp/vkbd", script)

    def press(self, *names: str, settle: float = 0.4) -> None:
        """Tap each named key in turn."""
        script = ""
        for name in names:
            code = KEYS[name]
            script += f"d {code}\ns 60\nu {code}\ns {int(settle * 1000)}\n"
        self.keys(script)

    def hold(self, name: str, seconds: float = 1.2) -> None:
        code = KEYS[name]
        self.keys(f"d {code}\ns {int(seconds * 1000)}\nu {code}\ns 400\n")

    def screenshot(self, dest: Path) -> Path:
        """Take a JiveLite screenshot (Shift+S) and copy it to dest. Retries if the file does not appear."""
        name = ""
        for _ in range(3):
            self._ssh("sudo rm -f /tmp/jivelite*.bmp")
            self.keys(f"d {KEYS['shift']}\nd {KEYS['s']}\ns 100\nu {KEYS['s']}\nu {KEYS['shift']}\ns 800\n")
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
        subprocess.run([*prefix, "scp", "-q", f"{self.target}:{name}", str(dest)], check=True)
        return dest

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
        self.press("h", "h", settle=2.0)
        self.to_top()
        self.press("right", "enter", settle=2.5)  # My Music is the second home entry
        self.to_top()
        self.press(*["right"] * self.MY_MUSIC.index(label), settle=0.2)
        self.press("enter", settle=2.0)
