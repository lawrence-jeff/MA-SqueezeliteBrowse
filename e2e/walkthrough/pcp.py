"""The walkthrough on a piCorePlayer, with the MacroPlay applet copied in from SqueezePlay.

JiveLite does not ship MacroPlay but loads applets from its user folder, so the applet (macroplay/, Logitech's, BSD licensed) is
copied there together with the macro and Macros.lua. pCP's filesystem is in RAM, so nothing survives a reboot, and everything is
removed at the end anyway. The macro starts when JiveLite starts: JiveLite is closed from its own Quit tile on the home menu (the
highlighted tile is read before Enter is pressed) and its wrapper script starts it again. Killing the process instead can leave
the display stuck on a black screen.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui import PlayerUi  # noqa: E402

from .macro import MacroWalker, bmp_to_jpeg  # noqa: E402,F401

APPLET_FILES = ("MacroPlayApplet.lua", "MacroPlayMeta.lua", "strings.txt", "pass.png", "fail.png")
LOG = "/var/log/pcp_jivelite.log"


class PcpWalker(MacroWalker):
    folder = "/home/tc/.jivelite/userpath-vis"
    startup_wait = 12.0
    restart_after_cleanup = False  # the macro hides its own home entry, and the applet stays idle without a Macros.lua

    def __init__(self, ui: PlayerUi) -> None:
        self.ui = ui

    def sh(self, command: str, data: bytes | None = None, timeout: int = 60) -> bytes:
        env = {**os.environ, "SSHPASS": self.ui.password}
        return subprocess.run(
            ["sshpass", "-e", "ssh", "-o", "StrictHostKeyChecking=no", "-o", "PubkeyAuthentication=no", "-o", "ConnectTimeout=10",
             self.ui.target, f"sudo sh -c {shlex.quote(command)}"],
            input=data, capture_output=True, check=True, timeout=timeout, env=env,
        ).stdout

    def install(self) -> None:
        applet = f"{self.folder}/applets/MacroPlay"
        self.sh(f"mkdir -p {applet}")
        source = Path(__file__).resolve().parent / "macroplay"
        for name in APPLET_FILES:
            self.sh(f"cat > {applet}/{name}", data=(source / name).read_bytes())

    def uninstall(self) -> None:
        self.sh(f"rm -rf {self.folder}/applets/MacroPlay")

    def restart_ui(self) -> None:
        self.ui.quit_jivelite()

    def error_count(self) -> int:
        out = self.sh(f"grep -ac 'task error MacroPlay' {LOG}; true").decode().strip()
        return int(out or 0)
