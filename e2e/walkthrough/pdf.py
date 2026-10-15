"""Build the walkthrough PDF from the steps and the screenshots a capture saved (needs reportlab and Pillow)."""

from __future__ import annotations

from pathlib import Path

from .steps import STEPS

W, H = 960, 540  # points: 13.333 x 7.5 in


def _wrap(text: str, width_chars: int) -> list[str]:
    words, line, lines = text.split(), "", []
    for word in words:
        if len(line) + len(word) + 1 > width_chars:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + ([line] if line else [])


def build_pdf(
    shots: Path, out: Path, title: str, intro: str = "", present: set[int] | None = None
) -> list[int]:
    """One title page, then a page for every step that has a screenshot. Returns the step numbers used."""
    from PIL import Image  # noqa: PLC0415
    from reportlab.lib.colors import Color  # noqa: PLC0415
    from reportlab.pdfgen import canvas  # noqa: PLC0415

    dark, grey = Color(0.13, 0.13, 0.13), Color(0.33, 0.33, 0.33)
    sheet = canvas.Canvas(str(out), pagesize=(W, H))
    sheet.setTitle(f"MA-SqueezeliteBrowse: menu walkthrough ({title})")

    sheet.setFillColor(dark)
    sheet.setFont("Helvetica-Bold", 38)
    sheet.drawString(60, 340, "MA-SqueezeliteBrowse: menu walkthrough")
    sheet.setFont("Helvetica-Bold", 24)
    sheet.drawString(60, 296, title)
    sheet.setFont("Helvetica", 17)
    sheet.setFillColor(grey)
    for i, line in enumerate(_wrap(intro, 95)):
        sheet.drawString(60, 250 - i * 24, line)
    sheet.showPage()

    used: list[int] = []
    for number, step in enumerate(STEPS, 1):
        image = shots / f"{number:02d}.jpg"
        if not image.exists() or (present is not None and number not in present):
            continue
        with Image.open(image) as picture:
            aspect = picture.width / picture.height
        sheet.setFillColor(dark)
        sheet.setFont("Helvetica-Bold", 22)
        sheet.drawString(36, H - 40, step.title)
        height = 424.0
        width = height * aspect
        if width > W - 72:
            width = W - 72
            height = width / aspect
        sheet.drawImage(str(image), (W - width) / 2, 62 + (424 - height) / 2, width, height)
        if step.note:
            sheet.setFillColor(grey)
            sheet.setFont("Helvetica", 11)
            for i, line in enumerate(_wrap(step.note, 170)[:2]):
                sheet.drawString(36, 44 - i * 14, line)
        sheet.showPage()
        used.append(number)
    sheet.save()
    return used
