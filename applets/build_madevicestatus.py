#!/usr/bin/env python3
"""Build MaDeviceStatus.zip: the applet in MaDeviceStatus/ as the Applet Installer of SqueezePlay and JiveLite expects it.

    python3 applets/build_madevicestatus.py ../server/music_assistant/providers/squeezelite/static/applets

The files are at the root of the zip. The zip is the same for the same files, and so has the same checksum.
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
APPLET_FILES = ("MaDeviceStatusApplet.lua", "MaDeviceStatusMeta.lua", "strings.txt")
FIXED_TIME = (2026, 11, 6, 0, 0, 0)


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "MaDeviceStatus.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in APPLET_FILES:
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (HERE / "MaDeviceStatus" / name).read_bytes())
    print(f"wrote {target} ({target.stat().st_size} bytes), sha1 {hashlib.sha1(target.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
