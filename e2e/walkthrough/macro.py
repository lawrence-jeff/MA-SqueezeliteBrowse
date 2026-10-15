"""Turn the walkthrough steps into a macro for the MacroPlay applet, and run it on a device.

SqueezePlay (the Squeezebox Radio) and JiveLite (the piCorePlayer) share the applet system. MacroPlay is a UI-test applet that
plays a Lua script inside the app: it picks menu entries by their label and saves screenshots of what the app draws, so no knob or
key presses are used. This module holds what is the same for every device; radio.py and pcp.py say where the files go, how the UI
is restarted (the macro starts when the UI starts) and how to read the log.

What a macro can use: the macro functions of MacroPlayApplet (macroHome, macroAction, macroDelay, macroScreenshot,
macroIsMenuItem, macro_fail, _macroFindWidget, macroRequire). Standard Lua functions and the widget classes are not in its
environment: get them with macroRequire("_G") and macroRequire("jive.ui.Menu").
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

from harness import say

from .steps import FIRST, LONG_PRESS, Do, Nth, Step

MACRO_FILE = "e2e_walk.lua"
DONE = "zz_done"
GO = "e2e_go"

MACROS_LUA = """return {
  macros = { e2ewalk = { name = "e2e walkthrough", desc = "e2e walkthrough", file = "%s", param = {} } },
  autorun = "e2ewalk",
}
""" % MACRO_FILE

HEADER = """local Menu = macroRequire("jive.ui.Menu")
local G = macroRequire("_G")
local pcall = G.pcall

-- wait until the home menu has been built from the server's menu
for i = 1, 180 do
  local menu = _macroFindWidget(Menu)
  if menu and menu:getSize() >= 4 then break end
  macroDelay(1000)
end
macroDelay(3000)
-- wait for the runner's signal that the player is connected and whatever the steps need is ready
for i = 1, 400 do
  local f = G.io.open("@FOLDER@/@GO@", "r")
  if f then f:close() break end
  macroDelay(1000)
end
macroDelay(1000)
-- the Macro entry on the home menu exists only because this macro is installed: keep it out of the screenshots
pcall(function() G.jiveMain:removeItemById("macroPlay") end)

local function pick(pattern)
  local menu = _macroFindWidget(Menu)
  if not menu then return false end
  for i = 1, menu:getSize() do
    menu:setSelectedIndex(i)
    macroDelay(100)
    if macroIsMenuItem(pattern) then return true end
  end
  return false
end

local function first()
  local menu = _macroFindWidget(Menu)
  if not menu or menu:getSize() < 1 then return false end
  menu:setSelectedIndex(1)
  macroDelay(150)
  return true
end

local function nth(n)
  local menu = _macroFindWidget(Menu)
  if not menu or menu:getSize() < n then return false end
  menu:setSelectedIndex(n)
  macroDelay(150)
  return true
end

local function go()
  macroAction(900, "go")
  return true
end
"""


# the app's own action for each abstract one in steps.py (SlimBrowser maps its "add" action to the item's "more" menu, which is
# what a touchscreen hold opens)
ACTIONS = {"long_press": "add", "queue": "go_playlist"}


def _pattern(label: str) -> str:
    """A Lua pattern for 'the entry whose text starts with label'."""
    return "^" + re.sub(r"([%^$().\[\]*+\-?])", r"%\1", label)


def macro_lua(steps: tuple[Step, ...], folder: str) -> str:
    """The Lua macro for these steps (step n is saved as s<nn>.bmp)."""
    parts = [HEADER.replace("@FOLDER@", folder).replace("@GO@", GO)]
    for n, step in enumerate(steps, 1):
        lines = [f"-- {n}: {step.title}", "do", "  macroHome(600)", "  macroDelay(900)", "  local ok = true"]
        for index, entry in enumerate(step.path):
            # an entry that a long press follows is only highlighted: the long press acts on it, Go would open it
            then_long_press = index + 1 < len(step.path) and step.path[index + 1] == LONG_PRESS
            open_it = "" if then_long_press else " and go()"
            if entry == FIRST:
                lines.append(f"  ok = ok and first(){open_it}")
            elif isinstance(entry, Nth):
                lines.append(f"  ok = ok and nth({entry.n}){open_it}")
            elif isinstance(entry, Do):
                lines.append(f'  if ok then macroAction(1800, "{ACTIONS.get(entry.action, entry.action)}") end')
            else:
                lines.append(f'  ok = ok and pick("{_pattern(entry)}"){open_it}')
        lines.append(f'  if ok then macroDelay({int(step.wait * 1000)}); macroScreenshot(200, "s{n:02d}")')
        lines.append(f'  else macro_fail("E2E MISSING {n}") end')
        lines.append("end")
        parts.append("\n".join(lines))
    parts.append(f'macroHome(600)\nmacroDelay(1200)\nmacroScreenshot(200, "{DONE}")\n')
    return "\n".join(parts)


class MacroWalker:
    """Deploys the macro to a device, waits for it, fetches the screenshots, and puts the device back as it was.

    A subclass says how to run a command on the device (sh), where the files go (folder), how to start the UI so the macro runs
    (restart_ui), and how many macro errors the log holds (error_count). It may add to install/uninstall.
    """

    folder = ""
    startup_wait = 25.0  # seconds after the UI restart before the runner's own preparation starts
    restart_after_cleanup = False  # does the UI have to be restarted again to drop the macro's menu entry?

    def sh(self, command: str, data: bytes | None = None, timeout: int = 60) -> bytes:
        raise NotImplementedError

    def restart_ui(self) -> None:
        raise NotImplementedError

    def error_count(self) -> int:
        raise NotImplementedError

    def install(self) -> None:
        """Anything the device needs besides the macro files (the applet, where it is not part of the software)."""

    def uninstall(self) -> None:
        """Remove what install put there."""

    def _remove_files(self) -> None:
        self.sh(f"cd {self.folder} && rm -f Macros.lua {MACRO_FILE} {GO} s[0-9][0-9].bmp {DONE}.bmp; true")

    def _push(self, name: str, text: str) -> None:
        self.sh(f"cat > {self.folder}/{name}", data=text.encode())

    def run(
        self, steps: tuple[Step, ...], ready: Callable[[], None] | None = None, timeout: float = 1800
    ) -> dict[int, bytes]:
        """Run the steps; returns {step number: BMP bytes} for the screens that were captured.

        ready is called once the UI has restarted, to wait for the player to be connected and to prepare what the steps need
        (a loaded track); only then is the macro told to start.
        """
        script = macro_lua(steps, self.folder)
        say(f"{type(self).__name__}: installing a macro of {len(steps)} steps and restarting the UI")
        try:
            self._remove_files()
            self.install()
            self._push("Macros.lua", MACROS_LUA)
            self._push(MACRO_FILE, script)
            errors_before = self.error_count()
            started = time.time()
            self.restart_ui()
            time.sleep(self.startup_wait)
            if ready is not None:
                ready()
            self.sh(f"touch {self.folder}/{GO}")
            while time.time() - started < timeout:
                try:
                    if self.sh(f"ls {self.folder}/{DONE}.bmp 2>/dev/null; true").strip():
                        break
                    if self.error_count() > errors_before:
                        raise RuntimeError("the macro failed on the device; see its log for 'task error MacroPlay'")
                except OSError:
                    pass
                time.sleep(8)
            else:
                raise RuntimeError("the macro did not finish in time")
            say("the macro finished; fetching the screenshots")
            shots: dict[int, bytes] = {}
            for n in range(1, len(steps) + 1):
                data = self.sh(f"cat {self.folder}/s{n:02d}.bmp 2>/dev/null; true", timeout=120)
                if data:
                    shots[n] = data
            return shots
        finally:
            say("removing the macro")
            try:
                self._remove_files()
                self.uninstall()
                if self.restart_after_cleanup:
                    self.restart_ui()
            except Exception as error:  # noqa: BLE001 - the cleanup must not hide the real error
                say(f"CLEANUP FAILED ({error}); remove {self.folder}/Macros.lua and {MACRO_FILE} by hand")


def bmp_to_jpeg(data: bytes, dest: Path, quality: int = 90) -> Path:
    """Convert a screenshot BMP from a device to a JPEG file."""
    from PIL import Image  # noqa: PLC0415

    Image.open(BytesIO(data)).convert("RGB").save(dest, "JPEG", quality=quality)
    return dest
