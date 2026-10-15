"""Third batch: the second dashboard (Squeezebox) in the Image Viewer.

Only Up/Down/Right/Enter/h keys are used, and Enter is only pressed after reading the label of the
highlighted tile or row and finding it is the expected one (otherwise the script stops).
"""
import sys, time
from pathlib import Path
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "e2e"))
import ocr
from ui import PlayerUi

OUT = Path(__file__).parent / "shots"
u = PlayerUi("192.168.0.241")
ST, HAX = 3, 5
_n = [0]

def shot():
    _n[0] += 1
    f = OUT / f"check-{_n[0]}.jpg"
    u.screenshot(f)
    return f

def grayness(im, box):
    r, g, b = [sum(c) / max(1, len(c)) for c in zip(*Image.open(im).crop(box).convert("RGB").resize((8, 8)).getdata())]
    return -abs(r - b)  # purple background: blue well above red; the highlight box is grey

def highlighted_tile(f):
    """Return the label under the highlighted tile of a grid screen (cells 384 wide, rows 306 high from y=147)."""
    best = None
    for row in range(3):
        for col in range(5):
            x, y = col * 384, 147 + row * 306
            score = grayness(f, (x + 4, y + 4, x + 60, y + 100))   # the box's left edge area, outside the icon
            if best is None or score > best[0]:
                best = (score, x, y)
    _, x, y = best
    crop = OUT / f"cell-{_n[0]}.png"
    Image.open(f).crop((x, y + 236, x + 384, y + 300)).save(crop)
    return " ".join(ocr.read_lines(crop))

def highlighted_row(f):
    """Return the text of the highlighted row of a list screen (rows 102 high from y=147)."""
    best = None
    for k in range(9):
        y = 147 + k * 102
        score = grayness(f, (700, y + 4, 1200, y + 96))
        if best is None or score > best[0]:
            best = (score, y)
    crop = OUT / f"row-{_n[0]}.png"
    Image.open(f).crop((0, best[1], 1500, best[1] + 100)).save(crop)
    return " ".join(ocr.read_lines(crop))

def check(read, word):
    if not ocr.contains([read], word, threshold=0.6) and word.lower() not in read.lower():
        raise SystemExit(f"STOP: expected the highlight on {word!r} but it reads {read!r}")
    print("  highlight ok:", read, flush=True)

def tile(i, label, settle=3.0):
    u.to_top()
    if i:
        u.press(*["right"] * i, settle=0.25)
    check(highlighted_tile(shot()), label)
    u.press("enter", settle=settle)

def to_panel(i, label):
    u.home(); u.to_top()
    tile(ST, "Settings")
    tile(HAX, "HA Extras")
    tile(i, label, settle=4.5)

u.ensure_ready(); u.press("k", settle=2)
to_panel(1, "Dashboards")
u.to_top(); u.press("down", settle=0.4)
check(highlighted_row(shot()), "Squeezebox")
u.press("enter", settle=1.0)
time.sleep(15)   # the dashboard is rendered by Puppet, which takes a few seconds
u.screenshot(OUT / "28.jpg"); print("28", ocr.read_lines(OUT / "28.jpg")[:5], flush=True)
u.home()
