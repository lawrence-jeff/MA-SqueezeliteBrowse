"""Walk the pCP menus and screenshot each screen. Only Up/Right/Enter/h keys are used (nothing is selected that acts)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "e2e"))
from ui import PlayerUi
import ocr

OUT = Path(__file__).parent / "shots"
u = PlayerUi("192.168.0.241")

def tile(i, settle=3.0):
    u.to_top()
    if i:
        u.press(*["right"] * i, settle=0.25)
    u.press("enter", settle=settle)

MM, ST, HAX = 1, 3, 5   # home: My Music is the 2nd tile, Settings the 4th; Settings: HA Extras the 6th
steps = [
    ("Main Menu", "jeffpicoreplayer-2", []),
    ("My Music", "Music Assistant", [MM]),
    ("My Music - Favorites", "Favorites", [MM, 2]),
    ("My Music - Artists", "Artists", [MM, 3]),
    ("My Music - Albums", "Albums", [MM, 4]),
    ("My Music - Albums - first album", "", [MM, 4, 0]),
    ("My Music - Tracks", "Tracks", [MM, 5]),
    ("My Music - Playlists", "Playlists", [MM, 6]),
    ("My Music - Audiobooks", "Audiobooks", [MM, 7]),
    ("My Music - Podcasts", "Podcasts", [MM, 8]),
    ("My Music - Podcasts - first podcast", "", [MM, 8, 0]),
    ("My Music - Radio", "Radio", [MM, 9]),
    ("My Music - Search", "Search", [MM, 10]),
    ("My Music - Switch Library", "Switch Library", [MM, 11]),
    ("Settings", "Settings", [ST]),
    ("Settings - HA Extras", "HA Extras", [ST, HAX]),
    ("Settings - HA Extras - Cameras", "Cameras", [ST, HAX, 0]),
    ("Settings - HA Extras - Dashboards", "Dashboards", [ST, HAX, 1]),
    ("Settings - HA Extras - Controls", "Controls", [ST, HAX, 2]),
    ("Settings - HA Extras - Scenes and Scripts", "Scenes", [ST, HAX, 3]),
    ("Settings - HA Extras - Status", "Status", [ST, HAX, 4]),
]
def shot_with_print_key(dest):
    import subprocess, time
    u._ssh("sudo rm -f /tmp/jivelite*.bmp")
    u.keys("d 99\ns 100\nu 99\ns 800\n")
    name = ""
    for _ in range(10):
        name = u._ssh("ls -t /tmp/jivelite*.bmp 2>/dev/null | head -1").strip()
        if name:
            time.sleep(0.5)
            break
        time.sleep(0.5)
    if not name:
        raise RuntimeError("no screenshot with the print key either")
    raw = dest.with_suffix(".bmp")
    subprocess.run(["sshpass", "-p", u.password, "scp", "-q", f"{u.target}:{name}", str(raw)], check=True)
    from ui import _to_jpeg
    _to_jpeg(raw, dest); raw.unlink()
    u._shot_at = time.time()

only = set(sys.argv[1:])
u.ensure_ready(); u.press("k", settle=2)
log = []
for n, (name, title, path) in enumerate(steps, 1):
    if only and str(n) not in only:
        continue
    if name.endswith("Switch Library"):
        u.press("esc", "esc", settle=1.5)  # leave the Search text entry, which swallows the h key
    u.home(); u.to_top()
    for i, idx in enumerate(path):
        tile(idx, settle=4.5 if i == len(path) - 1 else 3.0)
    if not name.endswith("Search"):
        u.to_top()  # the cursor goes back to the first item, so every slide looks the same way
    f = OUT / f"{n:02d}.jpg"
    if name.endswith("Search"):
        # a text entry types letters, so the Shift+S shortcut cannot be used there: use the Print Screen key
        shot_with_print_key(f)
    else:
        u.screenshot(f)
    lines = ocr.read_lines(f)
    ok = (not title) or ocr.contains(lines, title)
    log.append({"n": n, "name": name, "ok": ok, "read": lines[:6]})
    print(("OK  " if ok else "CHECK"), n, name, lines[:4], flush=True)
(Path(__file__).parent / "capture-log.json").write_text(json.dumps(log, indent=1))
