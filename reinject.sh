#!/bin/bash
set -e

# Auto-detect the Music Assistant container. `docker ps -a` finds it even if it is stopped. With
# more than one container named "music_assistant" the first match is used; set CONTAINER yourself
# above this line if that is wrong for you.
CONTAINER=$(docker ps -a --format '{{.Names}}' | grep -m1 music_assistant || true)

# Used only when no container matching "music_assistant" exists at all, which happens when
# Supervisor has torn the container down (see the recovery block below). Supervisor needs a name
# to recreate: override this with yours.
if [ -z "$CONTAINER" ]; then
    CONTAINER=app_d5369777_music_assistant
    echo "=== No music_assistant container found at all (even stopped) - probably torn down by Supervisor; attempting recovery as $CONTAINER ==="
fi
MA_SLUG=${CONTAINER#app_}
echo "=== Using Music Assistant container: $CONTAINER ==="

# Staging directories. Full dev checkouts stay on the development machine, never under /config (a
# clone there would be swept into Home Assistant's backups); this script only needs the few
# patched files below. By default every run downloads them from the branches of the two forks
# (ma-squeezelite-browse and dev-squeezelite-browse). With -local it uses what is already staged
# here instead, which is how files copied over by hand (network share, rsync) get tested.
ASP_SRC=/config/ma-squeezelite-browse/aioslimproto
SQZ_SRC=/config/ma-squeezelite-browse/server
SQZ_DEST=/app/venv/lib/python3.14/site-packages/music_assistant/providers/squeezelite
ASP_DEST=/app/venv/lib/python3.14/site-packages/aioslimproto

# -local injects the files already staged, without downloading, and fails if any is missing.
# Without it the staged files are replaced by a fresh download. -update is accepted for older
# instructions and is what happens by default.
USE_LOCAL=false
for arg in "$@"; do
    case "$arg" in
        -local) USE_LOCAL=true ;;
        -update) ;;
        *) echo "Unknown option: $arg (use -local to inject the already staged files)"; exit 1 ;;
    esac
done

ASP_RAW_BASE=https://raw.githubusercontent.com/lawrence-jeff/aioslimproto/ma-squeezelite-browse/aioslimproto
SQZ_RAW_BASE=https://raw.githubusercontent.com/lawrence-jeff/server/dev-squeezelite-browse/music_assistant/providers/squeezelite
# The icon list is hardcoded rather than read from the GitHub API, which avoids a second kind of
# network call, its rate limit and a JSON-parsing dependency. A new icon in static/ has to be
# added here, or the download won't fetch it.
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
SQZ_STATIC_FILES="$SQZ_STATIC_FILES HAExtras/account.png HAExtras/battery.png HAExtras/cctv.png HAExtras/door.png HAExtras/eye.png HAExtras/fan.png HAExtras/flash.png HAExtras/garage.png HAExtras/gauge.png HAExtras/haextras.png HAExtras/information.png HAExtras/lightbulb.png HAExtras/lock.png HAExtras/motion-sensor.png HAExtras/palette.png HAExtras/script-text.png HAExtras/speaker.png HAExtras/television-classic.png HAExtras/thermometer.png HAExtras/thermostat.png HAExtras/toggle-switch.png HAExtras/view-dashboard.png HAExtras/water-percent.png HAExtras/weather-partly-cloudy.png HAExtras/window-open.png"  # HA-EXTRAS

ASP_FILES="cli.py client.py models.py server.py"
SQZ_FILES="provider.py browselibrary.py player.py strings.json haextras.py"  # HA-EXTRAS (haextras.py, strings.json entry)

# Downloads everything into a temporary directory first and replaces the staged files only when
# all of it arrived, so a failed download never leaves a mix of old and new files.
fetch_staged_files() {
    echo "--- downloading patch files from the fork branches (lawrence-jeff/aioslimproto, lawrence-jeff/server) ---"
    mkdir -p "$ASP_SRC" "$SQZ_SRC/static"
    tmp=$(mktemp -d "$(dirname "$ASP_SRC")/.download.XXXXXX")
    mkdir -p "$tmp/aioslimproto" "$tmp/server/static"
    for f in $ASP_FILES; do
        curl -fsSL "$ASP_RAW_BASE/$f" -o "$tmp/aioslimproto/$f" \
            || { echo "FAILED: could not download $ASP_RAW_BASE/$f (nothing was changed; -local uses the staged files)"; rm -rf "$tmp"; exit 1; }
    done
    for f in $SQZ_FILES; do
        curl -fsSL "$SQZ_RAW_BASE/$f" -o "$tmp/server/$f" \
            || { echo "FAILED: could not download $SQZ_RAW_BASE/$f (nothing was changed; -local uses the staged files)"; rm -rf "$tmp"; exit 1; }
    done
    for f in $SQZ_STATIC_FILES; do
        mkdir -p "$(dirname "$tmp/server/static/$f")"  # HA-EXTRAS (static/HAExtras/)
        curl -fsSL "$SQZ_RAW_BASE/static/$f" -o "$tmp/server/static/$f" \
            || { echo "FAILED: could not download $SQZ_RAW_BASE/static/$f (nothing was changed; -local uses the staged files)"; rm -rf "$tmp"; exit 1; }
    done
    cp -R "$tmp/aioslimproto/." "$ASP_SRC/"
    cp -R "$tmp/server/." "$SQZ_SRC/"
    rm -rf "$tmp"
    echo "--- download complete ---"
}

# Music Assistant and a real LMS want the same discovery/SlimProto ports. Starting LMS while MA
# runs crashes the MA add-on: Supervisor tears the container down entirely, and `docker start`
# cannot bring it back. If that has happened, stop LMS (if it holds the ports) and start MA
# through the Supervisor API, then wait for the container to exist.
if ! docker inspect $CONTAINER > /dev/null 2>&1; then
    echo "=== $CONTAINER is gone (not just stopped) - stopping real LMS (if present) and restarting MA via Supervisor ==="
    # Best-effort, detected like the MA container; most setups have no competing LMS add-on. Falls
    # back to the dev host's LMS container name if nothing ending in "_lms" exists.
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

echo "=== Getting the patch files ==="
if $USE_LOCAL; then
    echo "--- -local: using the files already staged in $(dirname "$ASP_SRC"), not downloading ---"
    for f in $ASP_FILES; do
        [ -f "$ASP_SRC/$f" ] || { echo "FAILED: $ASP_SRC/$f is not staged - stage it, or run without -local to download"; exit 1; }
    done
    for f in $SQZ_FILES; do
        [ -f "$SQZ_SRC/$f" ] || { echo "FAILED: $SQZ_SRC/$f is not staged - stage it, or run without -local to download"; exit 1; }
    done
else
    fetch_staged_files
fi

echo ""
echo "=== Checking MA's bundled aioslimproto version (need >= 3.2.3) ==="
# Our cli.py/client.py/models.py/server.py track upstream main, which has drifted a little from the
# 3.2.3 release in the other aioslimproto files. That is fine for MA >= 2.10.5, which requires
# aioslimproto==3.2.3 and is the combination tested against real devices. An older MA may bundle
# something below that, which this patch set has not been tested against, so fail here instead of
# overlaying onto an unknown base.
docker exec $CONTAINER python3 -c "
import importlib.metadata as m
import sys
v = tuple(int(x) for x in m.version('aioslimproto').split('.')[:3])
print('Installed aioslimproto: ' + '.'.join(str(x) for x in v))
sys.exit(0 if v >= (3, 2, 3) else 1)
" || { echo "FAILED: MA's bundled aioslimproto is older than 3.2.3 - update Music Assistant to at least 2.10.5 first (see README)"; exit 1; }

echo ""
echo "=== Applying our aioslimproto patches on top of MA's own bundled copy ==="
# Only the files we patch are overlaid on MA's own aioslimproto: our cli.py/client.py/models.py/server.py on
# top of the stock 3.2.3 for everything else.
docker cp $ASP_SRC/cli.py    $CONTAINER:$ASP_DEST/cli.py
docker cp $ASP_SRC/client.py $CONTAINER:$ASP_DEST/client.py
docker cp $ASP_SRC/models.py $CONTAINER:$ASP_DEST/models.py
docker cp $ASP_SRC/server.py $CONTAINER:$ASP_DEST/server.py

echo ""
echo "=== Applying our squeezelite provider files ==="
docker cp $SQZ_SRC/provider.py      $CONTAINER:$SQZ_DEST/provider.py
docker cp $SQZ_SRC/browselibrary.py $CONTAINER:$SQZ_DEST/browselibrary.py
docker cp $SQZ_SRC/player.py        $CONTAINER:$SQZ_DEST/player.py
docker cp $SQZ_SRC/strings.json     $CONTAINER:$SQZ_DEST/strings.json  # HA-EXTRAS (its settings text)
docker cp $SQZ_SRC/haextras.py     $CONTAINER:$SQZ_DEST/haextras.py  # HA-EXTRAS

# HA-EXTRAS: Music Assistant reads setting labels from translations/en.json, which is generated from
# strings.json and does not have this one. Only this entry is added, so the rest of the file stays
# as the installed release has it.
docker exec $CONTAINER python3 -c "
import json, pathlib
root = pathlib.Path('$SQZ_DEST')
entry = json.loads((root / 'strings.json').read_text())['config_entries']['expose_ha_extras']
path = root.parent.parent / 'translations' / 'en.json'
data = json.loads(path.read_text())
for field, text in entry.items():
    data['provider.squeezelite.config_entries.expose_ha_extras.' + field] = text
path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + '\\n')
"

# The menu icons (browselibrary.py's STATIC_DIR is Path(__file__).parent / "static") must land as
# $SQZ_DEST/static next to browselibrary.py. Checked first because an empty or missing
# $SQZ_SRC/static would otherwise be hidden by the solid-color placeholder, and you would only see
# placeholders.
if [ ! -d "$SQZ_SRC/static" ] || [ -z "$(ls -A "$SQZ_SRC/static" 2>/dev/null)" ]; then
    echo "FAILED: $SQZ_SRC/static is missing or empty - populate it with the real"
    echo "        chrome icon files, one per menu tile (AlbumArtists_225x225_m.png,"
    echo "        AllArtists_225x225_m.png, Albums_225x225_m.png) before running"
    echo "        this script."
    exit 1
fi
# The trailing "/." copies the contents of $SQZ_SRC/static into $SQZ_DEST/static whether or not
# that directory already exists in the container. Without it, an existing static/ would get ours
# nested one level deeper (.../static/static/...), where nothing looks for it.
docker exec $CONTAINER mkdir -p $SQZ_DEST/static
docker cp $SQZ_SRC/static/. $CONTAINER:$SQZ_DEST/static
echo "--- contents of \$SQZ_DEST/static in the container right after copy: ---"
docker exec $CONTAINER ls -la $SQZ_DEST/static
echo "--- (end of listing) ---"

echo ""
echo "=== Verifying everything landed ==="
docker exec $CONTAINER python3 -c "import aioslimproto; print('aioslimproto package present')"
# Compare a SHA-256 of each source file with the copy in the container: proves every
# patched file landed byte-for-byte, without relying on markers inside the files.
verify_landed() {
    want=$(sha256sum "$1" | cut -d' ' -f1)
    got=$(docker exec $CONTAINER python3 -c \
        "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$2")
    [ "$want" = "$got" ] \
        || { echo "FAILED: $2 in the container differs from $1 - patch didn't land"; exit 1; }
    echo "verified: $(basename "$1")"
}
for f in cli.py client.py models.py server.py; do verify_landed "$ASP_SRC/$f" "$ASP_DEST/$f"; done
for f in provider.py browselibrary.py player.py strings.json haextras.py; do verify_landed "$SQZ_SRC/$f" "$SQZ_DEST/$f"; done  # HA-EXTRAS (haextras.py, strings.json)
# The sh -c wrapper is required: `docker exec` without a shell finds an installed package's `test`
# console script (which runs unittest) instead of the shell's file test.
docker exec $CONTAINER sh -c "test -f '$SQZ_DEST/static/AlbumArtists_225x225_m.png'" \
    || { echo "FAILED: static/AlbumArtists_225x225_m.png missing in container - static/ didn't land"; exit 1; }

echo ""
echo "=== Confirming a clean import (catches syntax/wiring errors before restart) ==="
docker exec $CONTAINER python3 -c "import music_assistant.providers.squeezelite.provider" && echo "imports clean"

echo ""
echo "=== Reset Test Client==="
# Best-effort: an unreachable test client must not abort the deploy under `set -e`. Set
# SKIP_CLIENT_REBOOT=1 to leave the clients running (enough for server-only changes: they
# reconnect).
if [ -n "${SKIP_CLIENT_REBOOT:-}" ]; then
    echo "SKIP_CLIENT_REBOOT is set - not rebooting the test client"
else
    curl -s --max-time 5 "http://192.168.0.241/cgi-bin/main.cgi?ACTION=reboot" > /dev/null \
        || echo "WARNING: could not reach test client at 192.168.0.241 - skipping its reboot, continuing"
fi

echo ""
echo "=== Reset UE Radio Test Client ==="
# A Squeezebox Radio (firmware 7.7.3) only offers legacy SSH algorithms (see the README for the
# flags) and reboots over SSH, not an HTTP endpoint like the piCorePlayer above. Best-effort,
# since other test setups have no radio at this address.
#
# sshpass is deliberately not installed automatically: this script runs on other people's Home
# Assistant hosts, and installing packages there is not its call to make. See the README for the
# one-time install.
#
# The radio's SSH server reports a non-zero exit even when the command worked, so a warning below
# does not mean the reboot failed: check the device. `timeout 15` is required: the reboot drops
# the TCP connection without a clean close, so ssh would otherwise wait forever and the script
# would never reach the restart.
if [ -z "${SKIP_CLIENT_REBOOT:-}" ] && command -v sshpass > /dev/null 2>&1; then
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

