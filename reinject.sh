#!/bin/bash
set -e

CONTAINER=app_d5369777_music_assistant
SRC=/config/LMSTest
SQZ_DEST=/app/venv/lib/python3.14/site-packages/music_assistant/providers/squeezelite
ASP_DEST=/app/venv/lib/python3.14/site-packages/aioslimproto

echo "=== Downloading aioslimproto 3.2.2 fresh ==="
mkdir -p $SRC/aioslimproto_322
cd $SRC/aioslimproto_322
pip download aioslimproto==3.2.2 --no-deps -d . --no-binary :none: 2>/dev/null || pip download aioslimproto==3.2.2 --no-deps -d .
unzip -o -q aioslimproto-3.2.2-py3-none-any.whl -d extracted
cd -

echo ""
echo "=== Replacing the ENTIRE aioslimproto package with the fresh 3.2.2 install ==="
docker exec $CONTAINER rm -rf $ASP_DEST
docker cp $SRC/aioslimproto_322/extracted/aioslimproto/. $CONTAINER:$ASP_DEST

echo ""
echo "=== Now applying our patches on top of the clean 3.2.2 base ==="
docker cp $SRC/cli.py    $CONTAINER:$ASP_DEST/cli.py
docker cp $SRC/models.py    $CONTAINER:$ASP_DEST/models.py
docker cp $SRC/server.py $CONTAINER:$ASP_DEST/server.py
docker cp $SRC/provider.py      $CONTAINER:$SQZ_DEST/provider.py
docker cp $SRC/browselibrary.py $CONTAINER:$SQZ_DEST/browselibrary.py
docker cp $SRC/player.py $CONTAINER:$SQZ_DEST/player.py

# Real chrome icon files (browselibrary.py's STATIC_DIR = Path(__file__).parent
# / "static" - so this has to land as $SQZ_DEST/static, a sibling of
# browselibrary.py in the container, not anywhere else). This is a
# directory copy, not a single file - checked explicitly first since an
# empty/missing $SRC/static is an easy thing to forget locally (nothing
# about editing browselibrary.py's Python would remind you), and
# browselibrary.py's own fallback (the solid-color placeholder) would
# otherwise mask the mistake rather than erroring - you'd just see
# placeholders again and have to guess why.
if [ ! -d "$SRC/static" ] || [ -z "$(ls -A "$SRC/static" 2>/dev/null)" ]; then
    echo "FAILED: $SRC/static is missing or empty - populate it with the real"
    echo "        chrome icon files, one per menu tile (AlbumArtists_225x225_m.png,"
    echo "        AllArtists_225x225_m.png, Albums_225x225_m.png) before running"
    echo "        this script."
    exit 1
fi
# Trailing "/." on the source - same convention already used above for
# the aioslimproto extraction - copies the CONTENTS of $SRC/static into
# $SQZ_DEST/static, regardless of whether that directory already exists in
# the container. Without it, docker cp's behavior depends on whether the
# destination already exists: if music_assistant/providers/squeezelite/
# already ships its own static/ (plausible - providers often bundle a
# provider icon or similar), copying the bare directory nests ours one
# level deeper instead (.../static/static/AlbumArtists_...png) rather than
# merging, and every file genuinely lands - just not where anything looks
# for it, which is exactly the kind of silent-looking failure this
# project tries to avoid.
docker exec $CONTAINER mkdir -p $SQZ_DEST/static
docker cp $SRC/static/. $CONTAINER:$SQZ_DEST/static
echo "--- contents of \$SQZ_DEST/static in the container right after copy: ---"
docker exec $CONTAINER ls -la $SQZ_DEST/static
echo "--- (end of listing) ---"

echo ""
echo "=== Verifying everything landed ==="
docker exec $CONTAINER python3 -c "import aioslimproto; print('aioslimproto package present')"
docker exec $CONTAINER grep -c "extra_routes" $ASP_DEST/server.py \
    || { echo "FAILED: extra_routes marker missing from server.py - patch didn't land"; exit 1; }
docker exec $CONTAINER grep -c "had_handshake" $ASP_DEST/cli.py \
    || { echo "FAILED: had_handshake marker missing from cli.py - patch didn't land"; exit 1; }
docker exec $CONTAINER grep -c "BrowseLibraryHandler" $SQZ_DEST/provider.py \
    || { echo "FAILED: BrowseLibraryHandler marker missing from provider.py - patch didn't land"; exit 1; }
docker exec $CONTAINER grep -c "^# browselibrary.py v" $SQZ_DEST/browselibrary.py \
    || { echo "FAILED: version marker missing from browselibrary.py - patch didn't land"; exit 1; }
# sh -c wrapper is required here, not optional: docker exec with no shell
# does a raw PATH search for a program literally named "test" - in this
# container that apparently resolves to some installed package's "test"
# console-script (real, confirmed: it ran Python's unittest and errored
# trying to treat our file path as a test module name) rather than
# coreutils' test. Wrapping in sh -c forces the shell's own test/[
# builtin to run instead, which always means the POSIX file-test we
# actually want, regardless of what else happens to be on PATH.
docker exec $CONTAINER sh -c "test -f '$SQZ_DEST/static/AlbumArtists_225x225_m.png'" \
    || { echo "FAILED: static/AlbumArtists_225x225_m.png missing in container - static/ didn't land"; exit 1; }

echo ""
echo "=== Confirming a clean import (catches syntax/wiring errors before restart) ==="
docker exec $CONTAINER python3 -c "import music_assistant.providers.squeezelite.provider" && echo "imports clean"

echo ""
echo "=== Reset Test Client==="
curl -s "http://192.168.0.241/cgi-bin/main.cgi?ACTION=reboot" > /dev/null


echo ""
echo "=== Hard restart (direct, not via HA UI - preserves everything we just copied in) ==="
docker restart $CONTAINER

echo ""
echo "=== Waiting for it to come back up ==="

sleep 20
docker ps | grep music

echo ""
echo "Done."

