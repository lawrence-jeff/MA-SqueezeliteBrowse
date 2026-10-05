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
- Optional, only if you want `reinject.sh` to also reboot a real Squeezebox (e.g. a UE Radio) test client over SSH: `sshpass`, installed yourself beforehand (`apk add sshpass` on the Home Assistant OS's own Alpine-based SSH session). `reinject.sh` deliberately does **not** install this automatically — it's your call whether to add packages to your own Home Assistant host, not something this script should decide on your behalf. If `sshpass` isn't installed, that reboot step is skipped with a warning; everything else still runs normally.

## Usage

1. SSH into Home Assistant using an add-on that gives you Docker access (Advanced SSH & Web Terminal is confirmed to work).
2. Copy this repo onto that host (e.g. `/config/LMSTest`, which is what `reinject.sh` assumes by default via `SRC`).
3. Edit `reinject.sh` and set `CONTAINER` to the name of your Music Assistant container (find it with `docker ps`).
4. If you're testing against a piCorePlayer client, `reinject.sh` includes a line that reboots it after patching:
   ```
   curl -s "http://192.168.0.241/cgi-bin/main.cgi?ACTION=reboot" > /dev/null
   ```
   Update the IP to your client, or remove the line if you don't need the client rebooted after each run.
5. If you're also testing against a real Squeezebox device (e.g. a UE Radio) on the same network, `reinject.sh` includes a second, separate best-effort step that reboots it over SSH afterward:
   ```
   sshpass -p '1234' ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 -oHostKeyAlgorithms=+ssh-rsa -oCiphers=+aes256-cbc -oMACs=+hmac-sha1 root@192.168.0.242 reboot
   ```
   Real Squeezebox firmware only offers key-exchange/host-key/cipher/MAC algorithms modern OpenSSH disables by default, which is why those four `-o` overrides are there — adjust them if a different device offers a different set (the error message when connecting without one of these tells you exactly which category and which algorithm it's missing). Update the IP/password to your own device, or remove the step if you don't have one. This step needs `sshpass` (see Requirements above) and is skipped automatically if it's not installed. Note: this device's own SSH server has been observed to report a non-zero/error exit even when the command fully succeeded — if this step prints a warning, check whether the device actually rebooted before assuming it's broken.
6. Run it:
   ```
   ./reinject.sh
   ```
   This copies the patched files into the container, verifies each patch landed (it fails loudly if a marker is missing), confirms the provider module still imports cleanly, and then restarts the container so Music Assistant picks up the changes.

## Persistence

The patch is applied directly to the container's filesystem, so it survives a plain container restart (which is what `reinject.sh` does at the end). It is **not** persistent across:

- Restarting the Music Assistant add-on/integration from the Home Assistant UI in a way that recreates the container, or
- Updating Music Assistant to a new version/image.

Either of those resets the container back to the stock image, so you'll need to run `reinject.sh` again afterward.
