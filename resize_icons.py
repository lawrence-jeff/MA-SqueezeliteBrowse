#!/usr/bin/env python3
"""
resize_icons.py - generate a new size variant of every existing icon in a
directory, matching the _WxH_mode.png naming convention this project's
static/ folder uses (e.g. AlbumArtists_225x225_m.png).

Python equivalent of real LMS's own resize-icons.pl (see
slimserver-tools/resize-icons.pl) - that script batch-resizes every
icon.png it finds to a given spec and writes out a new file with the size
baked into the filename. This does the same thing, just against our own
naming convention and driven off whatever size(s) already exist on disk
rather than a fixed "icon.png" filename.

Usage:
    python3 resize_icons.py <directory> <new_size> [--mode m]

Examples:
    python3 resize_icons.py static 40x40
    python3 resize_icons.py static 40x40 --mode m

What it does:
    For every file matching <base>_<W>x<H>_<mode>.png in <directory>,
    creates <base>_<new_size>_<mode>.png (a resized copy), skipping any
    that already exist. Source files are never modified.
"""

import argparse
import re
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("This script needs Pillow: pip install Pillow", file=sys.stderr)
    sys.exit(1)

# Matches this project's naming convention exactly, e.g.
# "AlbumArtists_225x225_m.png" -> base="AlbumArtists", w=225, h=225, mode="m"
ICON_NAME_RE = re.compile(
    r"^(?P<base>.+)_(?P<w>\d+)x(?P<h>\d+)_(?P<mode>[a-zA-Z])\.png$"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory containing the source icons")
    parser.add_argument("new_size", help="Target size as WxH, e.g. 40x40")
    parser.add_argument(
        "--mode", default=None,
        help="Force this mode letter on output filenames instead of reusing "
             "each source file's own mode letter (e.g. always 'm')",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Regenerate even if the target file already exists",
    )
    args = parser.parse_args()

    m = re.match(r"^(\d+)x(\d+)$", args.new_size)
    if not m:
        parser.error(f"new_size must look like WxH, e.g. 40x40 (got {args.new_size!r})")
    new_w, new_h = int(m.group(1)), int(m.group(2))

    if not args.directory.is_dir():
        parser.error(f"{args.directory} is not a directory")

    found_any = False
    for src in sorted(args.directory.glob("*.png")):
        m = ICON_NAME_RE.match(src.name)
        if not m:
            continue  # doesn't match the naming convention - skip, not an icon we manage
        found_any = True
        base = m.group("base")
        src_w, src_h = int(m.group("w")), int(m.group("h"))
        if (src_w, src_h) == (new_w, new_h):
            continue  # already at the target size - nothing to do
        mode = args.mode or m.group("mode")
        out_name = f"{base}_{new_w}x{new_h}_{mode}.png"
        out_path = src.parent / out_name

        if out_path.exists() and not args.overwrite:
            print(f"skip (exists): {out_name}")
            continue
        if out_path == src:
            print(f"skip (same as source): {out_name}")
            continue

        with Image.open(src) as img:
            # LANCZOS: best-quality downscale filter, matches what a real
            # image tool would use for shrinking an icon cleanly.
            resized = img.resize((new_w, new_h), Image.LANCZOS)
            resized.save(out_path)
        print(f"created: {out_name}  (from {src.name})")

    if not found_any:
        print(f"No files matching '<base>_WxH_mode.png' found in {args.directory}")


if __name__ == "__main__":
    main()