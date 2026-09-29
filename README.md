# MA-SqueezeliteBrowse

This patches the [Music Assistant](https://music-assistant.io/) Squeezelite provider so a Squeezelite client (e.g. a Logitech Media Server-style client, or [piCorePlayer](https://www.picoreplayer.org/)) can **browse the Music Assistant media library directly from the client**, instead of only playing whatever Music Assistant pushes to it.

It works by injecting patched provider files and a patched `aioslimproto` package straight into the running `music_assistant` container's Python environment.

## How it works

- `provider.py`, `browselibrary.py`, `player.py` — patched Squeezelite provider for Music Assistant, adding a `BrowseLibraryHandler` that serves the Music Assistant library over the Squeezelite/SlimProto protocol.
- `cli.py`, `models.py`, `server.py` — patched into a clean copy of the `aioslimproto` package (pinned to 3.2.3) to add extra HTTP routes and playlist metadata fixes the browse UI needs.
- `static/` — the menu icon assets (album art placeholders, artists, playlists, etc.) the browse UI serves to the client.
- `reinject.sh` — downloads a fresh `aioslimproto` 3.2.3 wheel, copies it and all the patched files into the running Music Assistant container, verifies the patches landed, and restarts the container so they take effect.

## Requirements

- Home Assistant running Music Assistant as an add-on/container.
- SSH access to Home Assistant **with Docker access** — the [Advanced SSH & Web Terminal](https://github.com/hassio-addons/addon-ssh) add-on works well for this.
- `docker`, `pip`, `unzip`, and `curl` available in that SSH session.

## Usage

1. SSH into Home Assistant using an add-on that gives you Docker access (Advanced SSH & Web Terminal is confirmed to work).
2. Copy this repo onto that host (e.g. `/config/LMSTest`, which is what `reinject.sh` assumes by default via `SRC`).
3. Edit `reinject.sh` and set `CONTAINER` to the name of your Music Assistant container (find it with `docker ps`).
4. If you're testing against a piCorePlayer client, `reinject.sh` includes a line that reboots it after patching:
   ```
   curl -s "http://192.168.0.241/cgi-bin/main.cgi?ACTION=reboot" > /dev/null
   ```
   Update the IP to your client, or remove the line if you don't need the client rebooted after each run.
5. Run it:
   ```
   ./reinject.sh
   ```
   This copies the patched files into the container, verifies each patch landed (it fails loudly if a marker is missing), confirms the provider module still imports cleanly, and then restarts the container so Music Assistant picks up the changes.

## Persistence

The patch is applied directly to the container's filesystem, so it survives a plain container restart (which is what `reinject.sh` does at the end). It is **not** persistent across:

- Restarting the Music Assistant add-on/integration from the Home Assistant UI in a way that recreates the container, or
- Updating Music Assistant to a new version/image.

Either of those resets the container back to the stock image, so you'll need to run `reinject.sh` again afterward.
