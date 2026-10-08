"""Drive JiveLite on a real player through the vkbd helper (see tools/vkbd.c)."""

from __future__ import annotations

import os
import subprocess
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

    def keys(self, script: str) -> None:
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
        self._ssh("sudo rm -f /tmp/jivelite*.bmp")
        self.keys(f"d {KEYS['shift']}\nd {KEYS['s']}\ns 100\nu {KEYS['s']}\nu {KEYS['shift']}\ns 1500\n")
        name = self._ssh("ls -t /tmp/jivelite*.bmp | head -1").strip()
        prefix = ["sshpass", "-p", self.password] if self.password else []
        subprocess.run([*prefix, "scp", "-q", f"{self.target}:{name}", str(dest)], check=True)
        return dest
