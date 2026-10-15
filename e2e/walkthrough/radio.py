"""The walkthrough on a Squeezebox Radio, with SqueezePlay's own macro engine (the MacroPlay applet, part of the Radio's software).

The macro goes in the Radio's writable settings folder together with the Macros.lua that makes MacroPlay run it at start, and the
Radio's UI is restarted (not the Radio) so it runs. The Radio keeps the macro's home menu entry until the UI is restarted again, so
that is done once more after the files are removed. See macro.py for the macro itself.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui_radio import RadioUi  # noqa: E402

from .macro import MacroWalker, bmp_to_jpeg  # noqa: E402,F401


class RadioWalker(MacroWalker):
    folder = "/etc/squeezeplay/userpath"
    startup_wait = 25.0
    restart_after_cleanup = True

    def __init__(self, ui: RadioUi) -> None:
        self.ui = ui

    def sh(self, command: str, data: bytes | None = None, timeout: int = 60) -> bytes:
        if data is None:
            return self.ui._ssh(command, timeout=timeout)
        self.ui._master()
        return subprocess.run(
            ["ssh", "-S", self.ui._socket, *self.ui._options, self.ui.target, command],
            input=data, capture_output=True, check=True, timeout=timeout,
        ).stdout

    def restart_ui(self) -> None:
        # detached, or the ssh session stays open for as long as the new UI process lives
        self.sh("(nohup /etc/init.d/squeezeplay restart >/dev/null 2>&1 </dev/null &); echo restarted")

    def error_count(self) -> int:
        out = self.sh("grep -c 'task error MacroPlay' /var/log/messages; true").decode().strip()
        return int(out or 0)
