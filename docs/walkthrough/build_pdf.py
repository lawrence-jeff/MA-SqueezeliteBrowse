"""Build the menu walkthrough PDF from the same slide list and screenshots as build.py."""
import re
from pathlib import Path
from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent
SHOTS = HERE / "shots"
src = (HERE / "build.py").read_text()
slides = eval(src[src.index("slides = [") + len("slides = "): src.index("]\nprs") + 1])

W, H = 960, 540   # points: 13.333 x 7.5 in, like the slides
out = HERE / "MA-SqueezeliteBrowse-menu-walkthrough.pdf"
c = canvas.Canvas(str(out), pagesize=(W, H))
c.setTitle("MA-SqueezeliteBrowse: menu walkthrough")
dark, grey = Color(.13, .13, .13), Color(.33, .33, .33)

def wrap(text, width_chars):
    words, line, lines = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width_chars:
            lines.append(line); line = w
        else:
            line = f"{line} {w}".strip()
    return lines + ([line] if line else [])

# title page
c.setFillColor(dark); c.setFont("Helvetica-Bold", 38)
c.drawString(60, 330, "MA-SqueezeliteBrowse: menu walkthrough")
c.setFont("Helvetica", 17); c.setFillColor(grey)
for i, line in enumerate(wrap("Screens of the piCorePlayer (JiveLite, 1920x1080) browsing Music Assistant 2.10.5 with the patched Squeezelite provider.", 95)):
    c.drawString(60, 280 - i * 24, line)
for i, line in enumerate(wrap("Captured 2026-10-11. The content shown is a moment in time: it follows the library, the presets and the Home Assistant labels.", 95)):
    c.drawString(60, 220 - i * 24, line)
c.showPage()

for key, title, note in slides:
    c.setFillColor(dark); c.setFont("Helvetica-Bold", 22)
    c.drawString(36, H - 40, title)
    img_h = 424; img_w = img_h * 16 / 9
    c.drawImage(str(SHOTS / f"{key}.jpg"), (W - img_w) / 2, 62, img_w, img_h)
    if note:
        c.setFillColor(grey); c.setFont("Helvetica", 11)
        for i, line in enumerate(wrap(note, 170)[:2]):
            c.drawString(36, 44 - i * 14, line)
    c.showPage()
c.save()
print(out, out.stat().st_size // 1024, "KB")
