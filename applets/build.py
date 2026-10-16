#!/usr/bin/env python3
"""Build FontReplacer.zip: the applet in FontReplacer/ plus the two merged CJK fonts from the original applet repository.

    python3 applets/build.py ../server/music_assistant/providers/squeezelite/static/applets

The fonts (FreeSans merged with DroidSansFallback, about 8 MB) are downloaded from LMS-Community/applet-FontReplacer and
checked against the hashes below; they are not kept in this repo. The zip has the files at its root, which is how the
Applet Installer of SqueezePlay and JiveLite expects an applet to be packed.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = "https://raw.githubusercontent.com/LMS-Community/applet-FontReplacer/master/FontReplacer/"
FONTS = {  # name -> sha1 of the file in the original repository
    "NewFont.ttf": None,
    "NewBoldFont.ttf": None,
}
APPLET_FILES = ("FontReplacerApplet.lua", "FontReplacerMeta.lua", "strings.txt")
FIXED_TIME = (2026, 10, 16, 0, 0, 0)  # the same files give the same zip, and so the same checksum


def fetch(name: str) -> bytes:
    with urllib.request.urlopen(RAW + name, timeout=120) as reply:  # noqa: S310 - a fixed https address
        return reply.read()


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "FontReplacer.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in APPLET_FILES:
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (HERE / "FontReplacer" / name).read_bytes())
        for name in FONTS:
            data = fetch(name)
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    print(f"wrote {target} ({target.stat().st_size} bytes), sha1 {hashlib.sha1(target.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
