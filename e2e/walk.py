#!/usr/bin/env python3
"""Capture the menu walkthrough on a device, and build the PDF from it.

    python3 e2e/walk.py capture --device radio --out docs/walkthrough/radio
    python3 e2e/walk.py capture --device radio --out /tmp/radio --only 3,7     # a few steps
    python3 e2e/walk.py pdf --device radio --shots docs/walkthrough/radio --out docs/walkthrough-radio.pdf

The steps are in walkthrough/steps.py and say where to go and what to capture, for every device. The wrapper for a device
(walkthrough/radio.py for a Squeezebox Radio) does it the way that device can be driven. The device comes from the "devices" of
e2e/config.json, as for run.py (--device NAME); without it the top-level player is used.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from harness import Rpc, say  # noqa: E402
from run import load_config  # noqa: E402
from walkthrough.steps import STEPS  # noqa: E402


def prepare_track(rpc: Rpc, volume: int) -> bool:
    """Load the first library track, paused, so Now Playing has something to show (at a low volume, then paused)."""
    rpc.call("mixer", "volume", volume)
    items = rpc.call("browselibrary", "items", 0, 3, "menu:1", "mode:tracks").get("item_loop", [])
    if not items:
        return False
    rpc.call("playlist", "clear")
    time.sleep(1)
    rpc.call("playlistcontrol", "cmd:load", f"track_id:{items[0]['commonParams']['track_id']}")
    for _ in range(40):
        if rpc.status().get("mode") == "play" and (rpc.status().get("time") or 0) >= 1:
            break
        time.sleep(0.5)
    time.sleep(3)
    rpc.call("pause", 1)
    return True


def capture(args: argparse.Namespace) -> int:
    import ocr  # noqa: PLC0415

    config = load_config(Path(args.config), args.device)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    only = {int(n) for n in args.only.split(",")} if args.only else None
    chosen = tuple(step for n, step in enumerate(STEPS, 1) if only is None or n in only)
    numbers = [n for n in range(1, len(STEPS) + 1) if only is None or n in only]
    if not chosen:
        sys.exit("No step selected.")

    rpc = Rpc(config["server_url"], config["player_id"])
    volume = int(config.get("test_volume", 10))
    needs_track = any("track" in step.needs for step in chosen)
    original_volume = int(rpc.call("mixer", "volume", "?").get("_mixer", 0))

    def ready() -> None:
        """Called once the UI has restarted: wait for the player, then load what the steps need (the UI restart also
        reconnects the player, and the server forgets what was loaded)."""
        for _ in range(60):
            try:
                if int(rpc.status().get("player_connected", 0)) == 1:
                    break
            except Exception:  # noqa: BLE001 - the server answers oddly while the player is away
                pass
            time.sleep(5)
        time.sleep(5)
        if needs_track:
            say(f"Preparing a paused track at volume {volume} for Now Playing")
            prepare_track(rpc, volume)

    try:
        driver = config.get("driver")
        if driver == "radio":
            from ui_radio import RadioUi  # noqa: PLC0415
            from walkthrough.radio import RadioWalker, bmp_to_jpeg  # noqa: PLC0415

            walker = RadioWalker(RadioUi(config["player_host"]))
            # macro steps are numbered from 1 in the order given, so run the chosen ones and map back to the real numbers
            shots = walker.run(chosen, ready)
            files = {numbers[i - 1]: bmp_to_jpeg(data, out / f"{numbers[i - 1]:02d}.jpg") for i, data in shots.items()}
        else:
            sys.exit(f"No walkthrough wrapper for the {driver or 'JiveLite'} driver yet; see walkthrough/radio.py for the Radio's.")
    finally:
        if needs_track:
            rpc.call("playlist", "clear")
            rpc.call("stop")
        rpc.call("mixer", "volume", original_volume)

    log = []
    for number, step in zip(numbers, chosen):
        path = files.get(number)
        if path is None:
            status, read = "missing", []
        else:
            read = ocr.read_lines(path)[:8]
            status = "ok" if not step.expect or ocr.contains(read + ocr.read_lines(path), step.expect) else "check"
        log.append({"n": number, "title": step.title, "status": status, "read": read})
        print(f"{status.upper():8} {number:2d} {step.title}")
    (out / "capture-log.json").write_text(json.dumps(log, indent=1))
    return 0


def pdf(args: argparse.Namespace) -> int:
    from walkthrough.pdf import build_pdf  # noqa: PLC0415

    config = load_config(Path(args.config), args.device)
    shots = Path(args.shots)
    log = json.loads((shots / "capture-log.json").read_text()) if (shots / "capture-log.json").exists() else []
    present = {entry["n"] for entry in log if entry.get("status") != "missing"} if log else None
    build_pdf(shots, Path(args.out), args.title or str(config.get("player_name", args.device)), args.intro, present)
    print("wrote", args.out)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(HERE / "config.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("capture", help="walk the menus on the device and save a screenshot of each step")
    c.add_argument("--device", help="an entry of the config's devices (default: the top-level player)")
    c.add_argument("--out", required=True, help="folder for NN.jpg and capture-log.json")
    c.add_argument("--only", help="comma-separated step numbers")
    p = sub.add_parser("pdf", help="build the PDF from a folder of screenshots")
    p.add_argument("--device")
    p.add_argument("--shots", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--title")
    p.add_argument("--intro", default="")
    args = parser.parse_args()
    return capture(args) if args.command == "capture" else pdf(args)


if __name__ == "__main__":
    sys.exit(main())
