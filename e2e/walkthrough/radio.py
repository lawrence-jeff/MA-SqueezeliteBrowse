"""Run the walkthrough steps on a Squeezebox Radio with SqueezePlay's own macro engine (the MacroPlay applet).

The macro picks every menu entry by its label inside the app, so the knob is not used at all. The steps are turned into a Lua
macro, copied into the Radio's writable settings folder together with the Macros.lua that makes MacroPlay run it at start,
and the UI is restarted so the macro runs. It saves one BMP per step (320x240, as drawn, without the wallpaper), which are
fetched and converted. Everything copied to the Radio is removed afterwards and the UI restarted once more, so the Radio is left as it was.

What a macro can use: the macro functions of MacroPlayApplet (macroHome, macroAction, macroDelay, macroScreenshot,
macroIsMenuItem, macro_fail, _macroFindWidget, macroRequire). Standard Lua functions and the widget classes are not in its
environment: get them with macroRequire("_G") and macroRequire("jive.ui.Menu").
"""

from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import say  # noqa: E402
from ui_radio import RadioUi  # noqa: E402

from .steps import FIRST, Do, Step  # noqa: E402

FOLDER = "/etc/squeezeplay/userpath"
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
-- wait for the runner's signal that the player is connected again and whatever the steps need is ready
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

local function go()
  macroAction(900, "go")
  return true
end
"""


def _pattern(label: str) -> str:
    """A Lua pattern for 'the entry whose text starts with label'."""
    return "^" + re.sub(r"([%^$().\[\]*+\-?])", r"%\1", label)


def macro_lua(steps: tuple[Step, ...]) -> str:
    """The Lua macro for these steps (step n is saved as s<nn>.bmp)."""
    parts = [HEADER.replace("@FOLDER@", FOLDER).replace("@GO@", GO)]
    for n, step in enumerate(steps, 1):
        lines = [f"-- {n}: {step.title}", "do", "  macroHome(600)", "  macroDelay(900)", "  local ok = true"]
        for entry in step.path:
            if entry == FIRST:
                lines.append("  ok = ok and first() and go()")
            elif isinstance(entry, Do):
                lines.append(f'  if ok then macroAction(1800, "{entry.action}") end')
            else:
                lines.append(f'  ok = ok and pick("{_pattern(entry)}") and go()')
        lines.append(f"  if ok then macroDelay({int(step.wait * 1000)}); macroScreenshot(200, \"s{n:02d}\")")
        lines.append(f'  else macro_fail("E2E MISSING {n}") end')
        lines.append("end")
        parts.append("\n".join(lines))
    parts.append(f'macroHome(600)\nmacroDelay(1200)\nmacroScreenshot(200, "{DONE}")\n')
    return "\n".join(parts)


class RadioWalker:
    """Deploys the macro, waits for it, fetches the screenshots, and puts the Radio back as it was."""

    def __init__(self, ui: RadioUi) -> None:
        self.ui = ui

    def _restart_ui(self) -> None:
        # detached, or the ssh session stays open for as long as the new UI process lives
        self.ui._ssh("(nohup /etc/init.d/squeezeplay restart >/dev/null 2>&1 </dev/null &); echo restarted")

    def _push(self, name: str, text: str) -> None:
        self.ui._master()
        import subprocess  # noqa: PLC0415

        subprocess.run(
            ["ssh", "-S", self.ui._socket, *self.ui._options, self.ui.target, f"cat > {FOLDER}/{name}"],
            input=text.encode(), check=True, timeout=60,
        )

    def _remove_files(self) -> None:
        self.ui._ssh(f"cd {FOLDER} && rm -f Macros.lua {MACRO_FILE} {GO} s[0-9][0-9].bmp {DONE}.bmp; true")

    def run(self, steps: tuple[Step, ...], ready: Callable[[], None] | None = None, timeout: float = 1800) -> dict[int, bytes]:
        """Run the steps; returns {step number: BMP bytes} for the screens that were captured.

        ready is called once the UI has restarted, to wait for the player to be connected and to prepare what the steps need
        (a loaded track); only then is the macro told to start.
        """
        script = macro_lua(steps)
        say(f"Radio: installing a macro of {len(steps)} steps and restarting the UI")
        try:
            self._remove_files()
            self._push("Macros.lua", MACROS_LUA)
            self._push(MACRO_FILE, script)
            started = time.time()
            self._restart_ui()
            time.sleep(25)
            if ready is not None:
                ready()
            self.ui._ssh(f"touch {FOLDER}/{GO}")
            while time.time() - started < timeout:
                try:
                    if self.ui._ssh(f"ls {FOLDER}/{DONE}.bmp 2>/dev/null; true").strip():
                        break
                    errors = self.ui._ssh("grep 'task error MacroPlay' /var/log/messages | tail -1; true").decode().strip()
                    if errors and self._error_is_new(errors, started):
                        raise RuntimeError(f"the macro failed on the Radio: {errors}")
                except OSError:
                    pass
                time.sleep(8)
            else:
                raise RuntimeError("the macro did not finish in time")
            say("Radio: the macro finished; fetching the screenshots")
            shots: dict[int, bytes] = {}
            for n in range(1, len(steps) + 1):
                data = self.ui._ssh(f"cat {FOLDER}/s{n:02d}.bmp 2>/dev/null; true")
                if data:
                    shots[n] = data
            return shots
        finally:
            say("Radio: removing the macro and restarting the UI")
            try:
                self._remove_files()
                self._restart_ui()
            except Exception as error:  # noqa: BLE001 - the cleanup must not hide the real error
                say(f"Radio: CLEANUP FAILED ({error}); remove {FOLDER}/Macros.lua and {MACRO_FILE} by hand")

    @staticmethod
    def _error_is_new(line: str, started: float) -> bool:
        match = re.match(r"(\w{3}\s+\d+\s+\d\d:\d\d:\d\d)", line)
        if not match:
            return False
        try:
            when = time.mktime(time.strptime(f"{time.localtime().tm_year} {match.group(1)}", "%Y %b %d %H:%M:%S"))
        except ValueError:
            return False
        return when >= started - 5


def bmp_to_jpeg(data: bytes, dest: Path, quality: int = 92) -> Path:
    """Convert a screenshot BMP from the Radio to a JPEG file."""
    from PIL import Image  # noqa: PLC0415

    Image.open(BytesIO(data)).convert("RGB").save(dest, "JPEG", quality=quality)
    return dest
