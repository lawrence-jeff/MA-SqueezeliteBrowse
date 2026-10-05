#!/bin/bash
set -e

CONTAINER=app_d5369777_music_assistant
MA_SLUG=d5369777_music_assistant
LMS_SLUG=fc57b866_lms
SRC=/config/LMSTest
SQZ_DEST=/app/venv/lib/python3.14/site-packages/music_assistant/providers/squeezelite
ASP_DEST=/app/venv/lib/python3.14/site-packages/aioslimproto

# Real LMS (run manually for proxy-capture comparisons against our own
# server - see the project's own notes on that) and MA both want the
# same discovery/SlimProto ports. Starting LMS while MA is running
# crashes MA's own add-on outright (Supervisor state goes to "error"
# and the container is torn down entirely, not just stopped - "docker
# start" on it afterward does nothing because there's no longer a
# container to start). If that's already happened by the time this
# script runs, recover here instead of requiring it be done by hand
# every time: stop LMS (if it's the one holding the ports) and start
# MA back up via the Supervisor API (not "docker start" - a Supervisor-
# managed add-on's container has to be recreated via Supervisor, not
# raw docker, once it's gone), then wait for the container to actually
# exist before continuing.
if ! docker inspect $CONTAINER > /dev/null 2>&1; then
    echo "=== $CONTAINER is gone (not just stopped) - stopping real LMS and restarting MA via Supervisor ==="
    docker exec hassio_cli ha apps stop $LMS_SLUG 2>&1 || true
    docker exec hassio_cli ha apps start $MA_SLUG 2>&1
    echo "=== Waiting for $CONTAINER to come back up ==="
    for i in $(seq 1 30); do
        docker inspect $CONTAINER > /dev/null 2>&1 && break
        sleep 1
    done
    docker inspect $CONTAINER > /dev/null 2>&1 \
        || { echo "FAILED: $CONTAINER still doesn't exist after restarting MA via Supervisor"; exit 1; }
fi

echo "=== Downloading aioslimproto 3.2.3 fresh ==="
mkdir -p $SRC/aioslimproto_323
cd $SRC/aioslimproto_323
pip download aioslimproto==3.2.3 --no-deps -d . --no-binary :none: 2>/dev/null || pip download aioslimproto==3.2.3 --no-deps -d .
unzip -o -q aioslimproto-3.2.3-py3-none-any.whl -d extracted
cd -

echo ""
echo "=== Replacing the ENTIRE aioslimproto package with the fresh 3.2.3 install ==="
docker exec $CONTAINER rm -rf $ASP_DEST
docker cp $SRC/aioslimproto_323/extracted/aioslimproto/. $CONTAINER:$ASP_DEST

echo ""
echo "=== Now applying our patches on top of the clean 3.2.3 base ==="
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
docker exec $CONTAINER grep -c "self.extra_routes" $ASP_DEST/cli.py \
    || { echo "FAILED: self.extra_routes marker missing from cli.py - patch didn't land"; exit 1; }
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
# Best-effort: an unreachable test client (e.g. when testing against a
# different device, like the UE Radio, that doesn't need/want a reboot)
# must not abort the rest of this script under `set -e` - the actual
# patch deploy (docker restart below) still needs to run either way.
curl -s --max-time 5 "http://192.168.0.241/cgi-bin/main.cgi?ACTION=reboot" > /dev/null \
    || echo "WARNING: could not reach test client at 192.168.0.241 - skipping its reboot, continuing"

echo ""
echo "=== Reset UE Radio Test Client ==="
# Real Squeezebox Radio firmware (7.7.3) only offers legacy SSH
# algorithms (see this project's own README for the full three-flag
# explanation) and takes a plain reboot over SSH, not an HTTP endpoint
# like the piCorePlayer client above. Best-effort for the same reason
# as that one: this project is also used against other test clients
# that don't have a UE Radio at this address at all.
#
# Deliberately does NOT auto-install sshpass (or anything else) even
# though it's missing - this script runs on other people's Home
# Assistant hosts via the repo, not just this project's own dev
# machine, and silently installing packages on someone else's system
# on their behalf is not this script's call to make. See the README
# for the one-time manual install this step needs.
#
# Real device test confirmed its embedded SSH server reports a
# non-zero exit even on a fully successful command (connected,
# authenticated, command ran and produced real output) - so a
# "FAILED"-sounding warning below doesn't necessarily mean the reboot
# didn't happen; check whether the device actually went down before
# assuming this step is broken.
if command -v sshpass > /dev/null 2>&1; then
    sshpass -p '1234' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
        -oKexAlgorithms=+diffie-hellman-group1-sha1 \
        -oHostKeyAlgorithms=+ssh-rsa \
        -oCiphers=+aes256-cbc \
        -oMACs=+hmac-sha1 \
        root@192.168.0.242 reboot \
        || echo "WARNING: ssh to UE Radio at 192.168.0.242 reported an error - this device's own SSH server is known to do this even when the reboot actually succeeded, so check the device itself before assuming it failed"
else
    echo "WARNING: sshpass not installed - skipping UE Radio reboot (see README for the one-time setup)"
fi


echo ""
echo "=== Hard restart (direct, not via HA UI - preserves everything we just copied in) ==="
docker restart $CONTAINER

echo ""
echo "=== Waiting for it to come back up ==="

sleep 20
docker ps | grep music

echo ""
echo "Done."

