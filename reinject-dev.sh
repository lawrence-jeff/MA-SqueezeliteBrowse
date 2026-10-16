#!/bin/bash
# Put the staged patch files into the Music Assistant Developer App and restart it, in seconds.
#
# The Developer App installs the server and aioslimproto from git every time its container starts,
# which takes about 2 minutes and would also overwrite the files copied in here. So the first run
# patches the container's /app/entrypoint.sh to skip that install while /data/skip_install exists.
# That makes this a "docker restart" loop that keeps the copied files:
#
#   ./reinject-dev.sh          copy the staged files in and restart (the install is skipped)
#   ./reinject-dev.sh -reset   go back to the committed branch: remove the flag and restart, which
#                              reinstalls from git (about 2 minutes)
#
# Anything that recreates the container (changing the App options, a Supervisor restart or update,
# stopping and starting the App) also gives the committed version back, and drops the entrypoint
# patch; the next run applies it again. Runs on the Home Assistant host, like reinject.sh, with the
# same staging folders. Copy the files there first (see the README), then run:
#
#   sudo sh -c 'cd /root && ./reinject-dev.sh'
set -e

CONTAINER=app_d5369777_music_assistant_dev
FLAG=/data/skip_install
ASP_SRC=/config/ma-squeezelite-browse/aioslimproto
SQZ_SRC=/config/ma-squeezelite-browse/server
SQZ_DEST=/app/venv/lib/python3.14/site-packages/music_assistant/providers/squeezelite
ASP_DEST=/app/venv/lib/python3.14/site-packages/aioslimproto
TRANSLATIONS_DEST=/app/venv/lib/python3.14/site-packages/music_assistant/translations
ASP_FILES="cli.py client.py models.py server.py"
SQZ_FILES="provider.py browselibrary.py browseuser.py player.py constants.py strings.json haextras.py"

docker inspect $CONTAINER > /dev/null 2>&1 \
    || { echo "FAILED: $CONTAINER does not exist; start the Developer App first"; exit 1; }

wait_up() {
    echo "=== Waiting for MA's web server (port 9000) ==="
    for i in $(seq 1 60); do
        code=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/ || true)
        [ "$code" = 302 ] && { echo "up after about $((i * 3)) s"; return 0; }
        sleep 3
    done
    echo "WARNING: port 9000 did not answer in 3 minutes; check: docker logs $CONTAINER"
}

if [ "$1" = "-reset" ]; then
    docker exec $CONTAINER rm -f $FLAG
    echo "=== Flag removed: the restart installs the committed branch from git (about 2 minutes) ==="
    docker restart $CONTAINER > /dev/null
    wait_up
    exit 0
fi
[ -z "$1" ] || { echo "Unknown option: $1 (use -reset to go back to the committed version)"; exit 1; }

for f in $ASP_FILES; do
    [ -f "$ASP_SRC/$f" ] || { echo "FAILED: $ASP_SRC/$f is not staged"; exit 1; }
done
for f in $SQZ_FILES; do
    [ -f "$SQZ_SRC/$f" ] || { echo "FAILED: $SQZ_SRC/$f is not staged"; exit 1; }
done
[ -d "$SQZ_SRC/static" ] || { echo "FAILED: $SQZ_SRC/static is not staged"; exit 1; }

echo "=== Making the container skip the git install while $FLAG exists ==="
if ! docker exec $CONTAINER grep -q skip_install /app/entrypoint.sh; then
    tmp=$(mktemp)
    docker exec $CONTAINER cat /app/entrypoint.sh > "$tmp"
    # the install is the first branch of the "if [ "$build_from_source" = true ]" under Step 1
    [ "$(grep -c '^if \[ "\$build_from_source" = true \]; then$' "$tmp")" = 1 ] \
        || { echo "FAILED: entrypoint.sh has changed, patch it by hand"; rm -f "$tmp"; exit 1; }
    awk -v flag="$FLAG" '
        $0 == "if [ \"$build_from_source\" = true ]; then" {
            print "if [ -f " flag " ]; then"
            print "    echo \"" flag " exists: keeping the installed (injected) files, no git install\""
            print "elif [ \"$build_from_source\" = true ]; then"
            next
        }
        { print }' "$tmp" > "$tmp.new" && mv "$tmp.new" "$tmp"
    docker cp "$tmp" $CONTAINER:/app/entrypoint.sh
    docker exec $CONTAINER chmod +x /app/entrypoint.sh
    rm -f "$tmp"
    echo "entrypoint patched"
else
    echo "entrypoint already patched"
fi
docker exec $CONTAINER touch $FLAG

echo "=== Copying the patch files ==="
for f in $ASP_FILES; do docker cp "$ASP_SRC/$f" $CONTAINER:$ASP_DEST/$f; done
for f in $SQZ_FILES; do docker cp "$SQZ_SRC/$f" $CONTAINER:$SQZ_DEST/$f; done
# the settings labels are read from Music Assistant's own translations, which the provider's strings.json feeds
if [ -f "$SQZ_SRC/en.json" ]; then
    docker cp "$SQZ_SRC/en.json" $CONTAINER:$TRANSLATIONS_DEST/en.json
fi
docker exec $CONTAINER mkdir -p $SQZ_DEST/static
docker cp "$SQZ_SRC/static/." $CONTAINER:$SQZ_DEST/static
docker exec $CONTAINER python3 -c "import music_assistant.providers.squeezelite.provider" && echo "imports clean"

echo "=== Restarting the container (the files stay: the install is skipped) ==="
docker restart $CONTAINER > /dev/null
wait_up
echo "Done. Clients reconnect on their own; reboot a client only if its home menu did not change."
