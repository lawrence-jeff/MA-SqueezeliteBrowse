# MA-SqueezeliteBrowse

This patches the [Music Assistant](https://music-assistant.io/) Squeezelite provider so a Squeezelite client (e.g. a Logitech Media Server-style client, or [piCorePlayer](https://www.picoreplayer.org/)) can **browse the Music Assistant media library directly from the client**, instead of only playing whatever Music Assistant pushes to it.

It works by injecting patched provider files and a patched `aioslimproto` package straight into the running `music_assistant` container's Python environment.

The intent is to let you quickly test these changes against your own real Music Assistant setup and real Squeezebox-style clients, without needing a full Music Assistant development environment. If you'd rather develop that way - editing the real source, running the test suite, etc. - use the forked repositories directly (see below). This script is for the "I just want to try it" path: from a stock Home Assistant host with Music Assistant already running, you can be testing in a few minutes.

## Where the actual code lives

The patched source itself isn't in this repo - it lives in two forks, each on a `ma-squeezelite-browse` branch based on upstream `main`:

- [lawrence-jeff/aioslimproto](https://github.com/lawrence-jeff/aioslimproto/tree/ma-squeezelite-browse) - the `aioslimproto` package (`cli.py`, `models.py`, `server.py`).
- [lawrence-jeff/server](https://github.com/lawrence-jeff/server/tree/ma-squeezelite-browse) - `music_assistant/providers/squeezelite/` (`provider.py`, `browselibrary.py`, `player.py`, and the `static/` menu icons).

This repo holds only `reinject.sh` and this README. `reinject.sh` downloads the files it needs from those two branches itself (see Usage below) - there's nothing to clone or set up by hand.

## How it works

- `reinject.sh` - downloads the patched `aioslimproto` files and the squeezelite provider files (plus `static/`) from the two forks above into `/config/ma-squeezelite-browse`, copies them into the running Music Assistant container, verifies the patches landed, and restarts the container so they take effect.

## Requirements

- Music Assistant **2.10.5 or later**, due to a dependency on `aioslimproto` 3.2.3.
- Home Assistant running Music Assistant as an add-on/container.
- SSH access to Home Assistant **with Docker access** — the [Advanced SSH & Web Terminal](https://github.com/hassio-addons/addon-ssh) add-on works well for this.
- `docker` and `curl` available in that SSH session.
- A writable `/config` directory - only needs to hold the handful of downloaded patch files (well under 2MB total).
- Optional, only if you want `reinject.sh` to also reboot a real Squeezebox (e.g. a UE Radio) test client over SSH: `sshpass`, installed yourself beforehand (`apk add sshpass` on the Home Assistant OS's own Alpine-based SSH session). `reinject.sh` deliberately does **not** install this automatically — it's your call whether to add packages to your own Home Assistant host, not something this script should decide on your behalf. If `sshpass` isn't installed, that reboot step is skipped with a warning; everything else still runs normally.

## Usage

1. SSH into Home Assistant using an add-on that gives you Docker access (Advanced SSH & Web Terminal is confirmed to work).
2. Download `reinject.sh` (it uses absolute paths throughout, so it doesn't matter where you put it - your home directory is fine):
   ```
   curl -fsSL https://raw.githubusercontent.com/lawrence-jeff/MA-SqueezeliteBrowse/main/reinject.sh -o reinject.sh
   chmod +x reinject.sh
   ```
3. If you're testing against a piCorePlayer client, `reinject.sh` includes a line that reboots it after patching:
   ```
   curl -s "http://192.168.0.241/cgi-bin/main.cgi?ACTION=reboot" > /dev/null
   ```
   Update the IP to your client, or remove the line if you don't need the client rebooted after each run.
4. If you're also testing against a real Squeezebox device (e.g. a UE Radio) on the same network, `reinject.sh` includes a second, separate best-effort step that reboots it over SSH afterward. The device must have **Remote Access** (sometimes called SSH/remote support access) enabled in its own on-device Settings menu first, or the SSH connection will simply fail/time out regardless of the credentials/algorithm overrides below.
   ```
   sshpass -p '1234' ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 -oHostKeyAlgorithms=+ssh-rsa -oCiphers=+aes256-cbc -oMACs=+hmac-sha1 root@192.168.0.242 reboot
   ```
   Real Squeezebox firmware only offers key-exchange/host-key/cipher/MAC algorithms modern OpenSSH disables by default, which is why those four `-o` overrides are there — adjust them if a different device offers a different set (the error message when connecting without one of these tells you exactly which category and which algorithm it's missing). Update the IP/password to your own device, or remove the step if you don't have one. This step needs `sshpass` (see Requirements above) and is skipped automatically if it's not installed. Note: this device's own SSH server has been observed to report a non-zero/error exit even when the command fully succeeded — if this step prints a warning, check whether the device actually rebooted before assuming it's broken.
5. Run it:
   ```
   ./reinject.sh
   ```
   `reinject.sh` auto-detects your Music Assistant container (no need to find/set its name yourself). On first run, since nothing's downloaded yet, it fetches the patch files straight from the `ma-squeezelite-browse` branch of the two forks (no git, no full clone) into `/config/ma-squeezelite-browse`, copies them into the container, verifies each patch landed (it fails loudly if a marker is missing), confirms the provider module still imports cleanly, and restarts the container so Music Assistant picks up the changes.
6. Run `./reinject.sh -update` any time later to re-download and pick up newer patches. Plain `./reinject.sh` never re-downloads on its own - once the files are there, it just re-injects whatever's already downloaded (faster, and works offline).

   Consider disabling Music Assistant's Auto Update while you're testing this. An auto-update - or any restart of the app that recreates the container - reverts back to stock (see below) and means running `reinject.sh` again.

## Reverting back to stock

The patch is applied directly to the container's filesystem, so it survives a plain container restart (which is what `reinject.sh` does at the end). It is **not** persistent across:

- Restarting the Music Assistant add-on/integration from the Home Assistant UI in a way that recreates the container, or
- Updating Music Assistant to a new version/image.

Either of those resets the container back to the stock image, so you'll need to run `reinject.sh` again afterward.
