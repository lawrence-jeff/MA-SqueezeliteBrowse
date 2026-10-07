#!/bin/bash
set -e

CONTAINER=app_d5369777_music_assistant
MA_SLUG=d5369777_music_assistant
LMS_SLUG=fc57b866_lms
# The working copy of the source lives in the two fork checkouts, not here -
# this repo now only holds reinject.sh itself and the README. Both checkouts
# are on their own ma-squeezelite-browse branch, based on upstream main (not
# the 3.2.3 release aioslimproto is pinned to pre-this-project) - that's
# deliberate: we want to know if something upstream changes and breaks this,
# and per-fix PRs branch off main separately anyway, so there's no benefit
# to the daily dev copy tracking an older release instead.
ASP_SRC=/config/ma-squeezelite-browse/aioslimproto/aioslimproto
SQZ_SRC=/config/ma-squeezelite-browse/server/music_assistant/providers/squeezelite
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

echo "=== Checking out the two fork branches this depends on (first run only) ==="
# These are our own forks, set up specifically for this project - cloning
# them automatically (unlike the sshpass case below, which is a system
# package on someone else's host) is just finishing the one-time setup a
# fresh checkout of this repo needs. Shallow + single-branch since we only
# ever need the current state of ma-squeezelite-browse, not history - the
# server repo especially is a full MA checkout, no reason to pull all of it.
if [ ! -f "$ASP_SRC/cli.py" ]; then
    echo "--- /config/ma-squeezelite-browse/aioslimproto missing or incomplete, cloning ma-squeezelite-browse ---"
    rm -rf /config/ma-squeezelite-browse/aioslimproto
    git clone --depth 1 --branch ma-squeezelite-browse --single-branch \
        https://github.com/lawrence-jeff/aioslimproto.git /config/ma-squeezelite-browse/aioslimproto
fi
if [ ! -f "$SQZ_SRC/provider.py" ]; then
    echo "--- /config/ma-squeezelite-browse/server missing or incomplete, cloning ma-squeezelite-browse ---"
    rm -rf /config/ma-squeezelite-browse/server
    git clone --depth 1 --branch ma-squeezelite-browse --single-branch \
        https://github.com/lawrence-jeff/server.git /config/ma-squeezelite-browse/server
fi

echo ""
echo "=== Checking MA's bundled aioslimproto version (need >= 3.2.3) ==="
# Our cli.py/models.py/server.py track upstream main, which has real,
# confirmed drift in the OTHER aioslimproto files (client.py especially)
# versus the 3.2.3 release - checked directly via a real diff, not
# assumed. That's fine for MA >= 2.10.5, whose own squeezelite provider
# already requires aioslimproto==3.2.3 (confirmed directly against a
# stock container run of that image, not assumed) - this exact
# combination is what's actually been tested against real devices this
# whole project. An older MA could still be bundling something below
# that (this project's own history: the full-package-replace this step
# used to do was built specifically because MA's default was 3.2.1 at
# the time), which this patch set has never been tested against - so
# fail loudly here instead of silently overlaying onto an unknown base.
docker exec $CONTAINER python3 -c "
import importlib.metadata as m
import sys
v = tuple(int(x) for x in m.version('aioslimproto').split('.')[:3])
print('Installed aioslimproto: ' + '.'.join(str(x) for x in v))
sys.exit(0 if v >= (3, 2, 3) else 1)
" || { echo "FAILED: MA's bundled aioslimproto is older than 3.2.3 - update Music Assistant to at least 2.10.5 first (see README)"; exit 1; }

echo ""
echo "=== Applying our aioslimproto patches on top of MA's own bundled copy ==="
# Just overlay the files we actually patch - no need to download or
# replace the whole package. This exact combination (our cli.py/models.py/
# server.py, tracking upstream main, layered over MA's stock 3.2.3 for
# everything else) is what's actually
# been tested against real devices (UE Radio, picoreplayer) this whole
# project - not a new, unverified combination.
docker cp $ASP_SRC/cli.py    $CONTAINER:$ASP_DEST/cli.py
docker cp $ASP_SRC/models.py $CONTAINER:$ASP_DEST/models.py
docker cp $ASP_SRC/server.py $CONTAINER:$ASP_DEST/server.py

echo ""
echo "=== Applying our squeezelite provider files ==="
docker cp $SQZ_SRC/provider.py      $CONTAINER:$SQZ_DEST/provider.py
docker cp $SQZ_SRC/browselibrary.py $CONTAINER:$SQZ_DEST/browselibrary.py
docker cp $SQZ_SRC/player.py        $CONTAINER:$SQZ_DEST/player.py

# Real chrome icon files (browselibrary.py's STATIC_DIR = Path(__file__).parent
# / "static" - so this has to land as $SQZ_DEST/static, a sibling of
# browselibrary.py in the container, not anywhere else). This is a
# directory copy, not a single file - checked explicitly first since an
# empty/missing $SQZ_SRC/static would otherwise be masked by
# browselibrary.py's own fallback (the solid-color placeholder) rather than
# erroring - you'd just see placeholders again and have to guess why.
if [ ! -d "$SQZ_SRC/static" ] || [ -z "$(ls -A "$SQZ_SRC/static" 2>/dev/null)" ]; then
    echo "FAILED: $SQZ_SRC/static is missing or empty - populate it with the real"
    echo "        chrome icon files, one per menu tile (AlbumArtists_225x225_m.png,"
    echo "        AllArtists_225x225_m.png, Albums_225x225_m.png) before running"
    echo "        this script."
    exit 1
fi
# Trailing "/." on the source - same convention already used above for
# the aioslimproto copy - copies the CONTENTS of $SQZ_SRC/static into
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
docker cp $SQZ_SRC/static/. $CONTAINER:$SQZ_DEST/static
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
#
# `timeout 15` wraps the whole ssh call - NOT optional, found via a
# real run that hung the entire script indefinitely without it: the
# device reboot itself succeeded, but the abrupt reboot appears to
# kill the TCP connection without a clean close/FIN, so the local ssh
# client just sat waiting forever for a graceful shutdown that was
# never coming - well past this script's own `docker restart` step
# ever running. `timeout 15` guarantees this step always ends on its
# own, successful reboot or not.
if command -v sshpass > /dev/null 2>&1; then
    timeout 15 sshpass -p '1234' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
        -oKexAlgorithms=+diffie-hellman-group1-sha1 \
        -oHostKeyAlgorithms=+ssh-rsa \
        -oCiphers=+aes256-cbc \
        -oMACs=+hmac-sha1 \
        root@192.168.0.242 reboot \
        || echo "WARNING: ssh to UE Radio at 192.168.0.242 reported an error (or timed out after 15s) - this device's own SSH server is known to do this even when the reboot actually succeeded, so check the device itself before assuming it failed"
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

