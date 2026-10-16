"""Drive a device through the resident test agent (e2e_agent.lua): send a command, get back what it did and what the screen shows.

    python3 e2e/agent/driver.py --device pcp start
    python3 e2e/agent/driver.py --device pcp send open "My Music"
    python3 e2e/agent/driver.py --device pcp stop
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from harness import say  # noqa: E402
from run import load_config  # noqa: E402
from walkthrough.macro import MACRO_FILE, MacroWalker  # noqa: E402,F401

AGENT_FILE = "e2e_agent.lua"
CMD, OUT = "e2e_cmd", "e2e_out"
MACROS_LUA = f"""return {{
  macros = {{ e2eagent = {{ name = "e2e agent", desc = "e2e agent", file = "{AGENT_FILE}", param = {{}} }} }},
  autorun = "e2eagent",
}}
"""


class Agent:
    """Wraps a device walker (pcp.py or radio.py) for its sh/folder/install/restart_ui."""

    def __init__(self, walker: MacroWalker) -> None:
        self.walker = walker
        self.ids = itertools.count(int(time.time()) % 100000 * 100)

    @property
    def folder(self) -> str:
        return self.walker.folder

    def start(self, timeout: float = 120) -> dict:
        w = self.walker
        w.sh(f"cd {self.folder} && rm -f {CMD} {OUT} {OUT}.tmp; true")
        w.install()
        w._push("Macros.lua", MACROS_LUA)
        w._push(AGENT_FILE, (HERE / AGENT_FILE).read_text().replace("@FOLDER@", self.folder))
        say(f"{type(w).__name__}: restarting the UI so the agent starts")
        w.restart_ui()
        time.sleep(w.startup_wait)
        end = time.time() + timeout
        while time.time() < end:
            try:
                out = self._read()
            except Exception:  # noqa: BLE001 - the device may still be restarting
                out = None
            if out and out.get("id") == "ready":
                return out
            time.sleep(2)
        raise RuntimeError("the agent did not report ready")

    def _read(self) -> dict | None:
        data = self.walker.sh(f"cat {self.folder}/{OUT} 2>/dev/null; true").decode().strip()
        return json.loads(data) if data else None

    def send(self, verb: str, arg: str = "", timeout: float = 60) -> dict:
        ident = f"c{next(self.ids)}"
        line = f"{ident} {verb} {arg}".rstrip()
        self.walker.sh(f"cat > {self.folder}/{CMD}.tmp && mv {self.folder}/{CMD}.tmp {self.folder}/{CMD}", data=(line + "\n").encode())
        end = time.time() + timeout
        while time.time() < end:
            out = self._read()
            if out and out.get("id") == ident:
                return out
            time.sleep(0.4)
        raise TimeoutError(f"no answer to: {line}")

    def eval(self, code: str) -> dict:
        """Run a piece of Lua (a chunk that may `return` a value) inside the player's UI and return the reply."""
        self.walker.sh(f"cat > {self.folder}/e2e_eval.lua", data=code.encode())
        return self.send("eval", "e2e_eval.lua")

    def stop(self) -> None:
        try:
            self.send("exit", timeout=20)
        except Exception:  # noqa: BLE001
            pass
        self.walker._remove_files()
        self.walker.sh(f"cd {self.folder} && rm -f {AGENT_FILE} {CMD} {OUT} {OUT}.tmp; true")
        self.walker.uninstall()


def make_agent(config: dict) -> Agent:
    if config.get("driver") == "radio":
        from ui_radio import RadioUi  # noqa: PLC0415
        from walkthrough.radio import RadioWalker  # noqa: PLC0415

        return Agent(RadioWalker(RadioUi(config["player_host"])))
    from ui import PlayerUi  # noqa: PLC0415
    from walkthrough.pcp import PcpWalker  # noqa: PLC0415

    return Agent(PcpWalker(PlayerUi(config["player_host"])))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(HERE.parent / "config.json"))
    parser.add_argument("--device")
    parser.add_argument("command", choices=["start", "send", "eval", "stop"])
    parser.add_argument("verb", nargs="?")
    parser.add_argument("arg", nargs="*")
    args = parser.parse_args()
    config = load_config(Path(args.config), args.device if args.device not in (None, "pcp") else None)
    agent = make_agent(config)
    if args.command == "start":
        print(json.dumps(agent.start(), indent=1))
    elif args.command == "send":
        print(json.dumps(agent.send(args.verb, " ".join(args.arg)), indent=1))
    elif args.command == "eval":
        code = Path(args.verb).read_text() if args.verb and Path(args.verb).exists() else " ".join([args.verb or "", *args.arg])
        print(json.dumps(agent.eval(code), indent=1))
    else:
        agent.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
