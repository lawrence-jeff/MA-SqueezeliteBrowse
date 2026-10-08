"""Read the text on a player screenshot (needs the tesseract command and Pillow)."""

from __future__ import annotations

import difflib
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps


def _passes(image: Image.Image) -> list[Image.Image]:
    """The screenshot prepared two ways: light text on dark and dark text on light both read well."""
    grey = ImageOps.autocontrast(image.convert("L"))
    big = grey.resize((grey.width * 2, grey.height * 2), Image.LANCZOS)
    return [big, ImageOps.invert(big)]


def read_lines(path: Path) -> list[str]:
    """All the text lines tesseract finds on the screenshot, from both passes, without duplicates."""
    lines: list[str] = []
    with Image.open(path) as image:
        for prepared in _passes(image):
            with tempfile.TemporaryDirectory() as tmp:
                png = Path(tmp) / "screen.png"
                prepared.save(png)
                out = subprocess.run(
                    ["tesseract", str(png), "stdout", "--psm", "11"],
                    capture_output=True, text=True, timeout=60, check=False,
                ).stdout
            for line in out.splitlines():
                text = line.strip()
                if len(text) > 1 and text not in lines:
                    lines.append(text)
    return lines


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def contains(lines: list[str], wanted: str, threshold: float = 0.82) -> bool:
    """Whether the wanted text is on the screen, tolerating the odd OCR slip."""
    target = _norm(wanted)
    if not target:
        return True
    haystack = [_norm(line) for line in lines]
    if any(target in line for line in haystack):
        return True
    joined = " ".join(haystack)
    if target in joined:
        return True
    return any(difflib.SequenceMatcher(None, target, line).ratio() >= threshold for line in haystack)
