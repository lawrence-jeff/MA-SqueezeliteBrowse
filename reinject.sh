#!/bin/bash
set -e

# Auto-detect the Music Assistant container - no need to find/edit this
# yourself. docker ps -a (not just ps) so this still finds it if it's
# merely stopped, not destroyed outright (see the recovery block below
# for the one case this can't help with). If you have more than one
# container with "music_assistant" in its name, this takes the first
# match - set CONTAINER yourself above this line if that's wrong for you.
CONTAINER=$(docker ps -a --format '{{.Names}}' | grep -m1 music_assistant || true)

# Fallback used only when no container matching "music_assistant" exists
# at all - including in `docker ps -a` - which happens specifically when
# Supervisor has torn the container down entirely (see the recovery block
# below). Detection can't find a name that doesn't exist anywhere, so
# Supervisor needs *some* name to recreate - this is this project's own
# dev host's container name; override it here if yours differs and you
# hit this exact edge case on a from-scratch host.
if [ -z "$CONTAINER" ]; then
    CONTAINER=app_d5369777_music_assistant
    echo "=== No music_assistant container found at all (even stopped) - probably torn down by Supervisor; attempting recovery as $CONTAINER ==="
fi
MA_SLUG=${CONTAINER#app_}
echo "=== Using Music Assistant container: $CONTAINER ==="

# The real dev checkouts (full git history, tests, etc. - several hundred MB)
# live only on whatever machine you actually develop on, never under /config -
# a full clone there would get swept into Home Assistant's own backups for no
# reason. This script only ever needs the small handful of patched files
# below. If you're developing yourself, stage them here however you like
# (drag-and-drop onto the /config network share, rsync, robocopy). If you
# just want to run this against a stock Music Assistant without doing any
# development, don't stage anything - the first run below downloads these
# same files straight from the ma-squeezelite-browse branch of the two forks
# (no git, no full clone) so SSH+this script is all you need.
ASP_SRC=/config/ma-squeezelite-browse/aioslimproto
SQZ_SRC=/config/ma-squeezelite-browse/server
SQZ_DEST=/app/venv/lib/python3.14/site-packages/music_assistant/providers/squeezelite
ASP_DEST=/app/venv/lib/python3.14/site-packages/aioslimproto

# -update re-downloads the staged files from the forks even if they're
# already present, overwriting whatever's currently staged - for someone
# who isn't developing locally and just wants to pick up the latest patch
# set. Without it, once the files are staged (downloaded or by hand),
# plain `./reinject.sh` never touches them again - it just injects
# whatever's already there, which is what a dev iterating locally wants.
FORCE_UPDATE=false
if [ "$1" = "-update" ]; then
    FORCE_UPDATE=true
fi

ASP_RAW_BASE=https://raw.githubusercontent.com/lawrence-jeff/aioslimproto/ma-squeezelite-browse/aioslimproto
SQZ_RAW_BASE=https://raw.githubusercontent.com/lawrence-jeff/server/ma-squeezelite-browse/music_assistant/providers/squeezelite
# Hardcoded rather than listed dynamically via the GitHub API - avoids a
# second kind of network call (API, not just raw-content fetches), its
# separate rate limit, and a jq/API-JSON-parsing dependency this script
# otherwise doesn't need. Downside: if a new icon is ever added to static/
# upstream, it has to be added to this list too, by hand, or -update won't
# fetch it.
SQZ_STATIC_FILES="
AlbumArtists_100x100_m.png AlbumArtists_225x225_m.png AlbumArtists_40x40_m.png AlbumArtists_41x41_m.png
Albums_100x100_m.png Albums_225x225_m.png Albums_40x40_m.png Albums_41x41_m.png
AllArtists_100x100_m.png AllArtists_225x225_m.png AllArtists_40x40_m.png AllArtists_41x41_m.png
AudioBooks.png AudioBooks_100x100_m.png AudioBooks_225x225_m.png AudioBooks_40x40_m.png AudioBooks_41x41_m.png
Playlists_100x100_m.png Playlists_225x225_m.png Playlists_40x40_m.png Playlists_41x41_m.png
browselibrary.png
favorites_100x100_m.png favorites_225x225_m.png favorites_40x40_m.png favorites_41x41_m.png
icon_favorites_remote.png icon_internet_radio_remote.png icon_ml_playlist_remote.png
playlistclear.png playlistclear_100x100_m.png playlistclear_225x225_m.png playlistclear_40x40_m.png playlistclear_41x41_m.png
playlists.png
podcasts_100x100_m.png podcasts_225x225_m.png podcasts_40x40_m.png podcasts_41x41_m.png
radio.png
radiolocal_100x100_m.png radiolocal_225x225_m.png radiolocal_40x40_m.png radiolocal_41x41_m.png
radiosearch_100x100_m.png radiosearch_225x225_m.png radiosearch_41x41_m.png
"

fetch_staged_files() {
    echo "--- downloading patch files from the ma-squeezelite-browse branches (lawrence-jeff/aioslimproto, lawrence-jeff/server) ---"
    mkdir -p "$ASP_SRC" "$SQZ_SRC/static"
    for f in cli.py models.py server.py; do
        curl -fsSL "$ASP_RAW_BASE/$f" -o "$ASP_SRC/$f" \
            || { echo "FAILED: could not download $ASP_RAW_BASE/$f"; exit 1; }
    done
    for f in provider.py browselibrary.py player.py; do
        curl -fsSL "$SQZ_RAW_BASE/$f" -o "$SQZ_SRC/$f" \
            || { echo "FAILED: could not download $SQZ_RAW_BASE/$f"; exit 1; }
    done
    for f in $SQZ_STATIC_FILES; do
        curl -fsSL "$SQZ_RAW_BASE/static/$f" -o "$SQZ_SRC/static/$f" \
            || { echo "FAILED: could not download $SQZ_RAW_BASE/static/$f"; exit 1; }
    done
    echo "--- download complete ---"
}

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
    echo "=== $CONTAINER is gone (not just stopped) - stopping real LMS (if present) and restarting MA via Supervisor ==="
    # Best-effort and auto-detected the same way as MA above - most setups
    # won't have a competing real-LMS add-on at all, which is fine; this
    # is just a courtesy for the specific case of this project's own test
    # rig (LMS and MA fighting over the same ports can crash MA's container
    # outright). Falls back to this dev host's own LMS container name only
    # if nothing matching "_lms" exists at all.
    LMS_CONTAINER=$(docker ps -a --format '{{.Names}}' | grep -m1 -i '_lms$' || echo "app_fc57b866_lms")
    LMS_SLUG=${LMS_CONTAINER#app_}
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

echo "=== Checking the staged patch files are present ==="
if $FORCE_UPDATE; then
    echo "--- -update passed: re-downloading, overwriting whatever's currently staged ---"
    fetch_staged_files
elif [ ! -f "$ASP_SRC/cli.py" ] || [ ! -f "$SQZ_SRC/provider.py" ]; then
    echo "--- staged files missing - this looks like a first run, downloading them ---"
    fetch_staged_files
else
    echo "--- staged files already present, using them as-is (pass -update to refresh) ---"
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

