# MA-SqueezeliteBrowse

This patches the [Music Assistant](https://music-assistant.io/) Squeezelite provider so a Squeezelite client (e.g. a Logitech Media Server-style client, or [piCorePlayer](https://www.picoreplayer.org/)) can **browse the Music Assistant media library directly from the client**, instead of only playing whatever Music Assistant pushes to it.

It works by injecting patched provider files and a patched `aioslimproto` package straight into the running `music_assistant` container's Python environment.

## Where the actual code lives

The patched source itself isn't in this repo - it lives in two forks, each on a `ma-squeezelite-browse` branch based on upstream `main`:

- [lawrence-jeff/aioslimproto](https://github.com/lawrence-jeff/aioslimproto/tree/ma-squeezelite-browse) - the `aioslimproto` package.
- [lawrence-jeff/server](https://github.com/lawrence-jeff/server/tree/ma-squeezelite-browse) - `music_assistant/providers/squeezelite/` (`provider.py`, `browselibrary.py`, `player.py`, and the `static/` menu icons).

This repo holds only `reinject.sh` and this README, living in a directory named `ma-squeezelite-browse` (matching the branch name both forks use) rather than a project-specific name of its own.

Editing the actual behavior - the browse menus, queue sync, seek, anything in `provider.py`/`browselibrary.py`/`player.py`/`cli.py`/`models.py`/`server.py` - means cloning those two forks **somewhere on your own machine** (not under `/config`) and editing them there. Keep the full clones local to whatever you develop on: `server` especially is a full Music Assistant checkout, several hundred MB even shallow-cloned, and `/config` gets swept into Home Assistant's own backups - there's no reason for that history/size to live there.

`reinject.sh` itself only ever reads six small files (`cli.py`/`models.py`/`server.py` from the `aioslimproto` fork, `provider.py`/`browselibrary.py`/`player.py`/`static/` from the `server` fork) staged into `/config/ma-squeezelite-browse/aioslimproto` and `/config/ma-squeezelite-browse/server` as flat directories (not git checkouts). How you get them there from your own machine onto the `/config` network share is up to you and your OS - drag-and-drop in Finder/Explorer, `rsync`, `robocopy`, whatever - `reinject.sh` doesn't care, it just reads whatever's currently staged there each time it runs.

## How it works

- `reinject.sh` - copies the staged `aioslimproto` patch files and the squeezelite provider files (plus `static/`) into the running Music Assistant container, verifies the patches landed, and restarts the container so they take effect.

## Requirements

- Music Assistant **2.10.5 or later**. That's the version this project has confirmed ships `aioslimproto==3.2.3` by default (checked directly against a stock container, not assumed) - our patched `cli.py`/`models.py`/`server.py` track upstream `aioslimproto` `main`, which has real, confirmed differences from 3.2.3 in the files we don't overlay (`client.py` especially), so an older MA bundling something below 3.2.3 is a combination this project has never tested. `reinject.sh` checks the installed version itself and fails loudly rather than silently overlaying onto an unknown base.
- Home Assistant running Music Assistant as an add-on/container.
- SSH access to Home Assistant **with Docker access** — the [Advanced SSH & Web Terminal](https://github.com/hassio-addons/addon-ssh) add-on works well for this.
- `docker` and `curl` available in that SSH session (`git` is no longer needed on the Home Assistant host itself - the real checkouts live on your own dev machine).
- A writable `/config` directory - only needs to hold the handful of staged files below (well under 2MB total), not full clones.
- Optional, only if you want `reinject.sh` to also reboot a real Squeezebox (e.g. a UE Radio) test client over SSH: `sshpass`, installed yourself beforehand (`apk add sshpass` on the Home Assistant OS's own Alpine-based SSH session). `reinject.sh` deliberately does **not** install this automatically — it's your call whether to add packages to your own Home Assistant host, not something this script should decide on your behalf. If `sshpass` isn't installed, that reboot step is skipped with a warning; everything else still runs normally.

## Usage

**If you just want to run this, no development:**

1. SSH into Home Assistant using an add-on that gives you Docker access (Advanced SSH & Web Terminal is confirmed to work).
2. Download `reinject.sh` into `/config/ma-squeezelite-browse`:
   ```
   mkdir -p /config/ma-squeezelite-browse && cd /config/ma-squeezelite-browse
   curl -fsSL https://raw.githubusercontent.com/lawrence-jeff/MA-SqueezeliteBrowse/main/reinject.sh -o reinject.sh
   chmod +x reinject.sh
   ```
3. Edit it and set `CONTAINER` to the name of your Music Assistant container (find it with `docker ps`).
4. Run it: `./reinject.sh`. On first run, since nothing's staged yet, it downloads the patched files itself straight from the `ma-squeezelite-browse` branch of the two forks (no git, no full clone - see "Where the actual code lives" above) into `/config/ma-squeezelite-browse/{aioslimproto,server}`, then injects them.
5. Run `./reinject.sh -update` any time later to re-download and pick up newer patches. Plain `./reinject.sh` never re-downloads on its own - once the files are staged, it just injects whatever's already there.

**If you're developing changes yourself:**

1. Clone the two forks somewhere on your own machine (not under `/config`) and check out their `ma-squeezelite-browse` branch:
   ```
   git clone --branch ma-squeezelite-browse https://github.com/lawrence-jeff/aioslimproto.git
   git clone --branch ma-squeezelite-browse https://github.com/lawrence-jeff/server.git
   ```
2. Copy this repo onto the Home Assistant host as `/config/ma-squeezelite-browse`, which is what `reinject.sh` assumes by default.
3. Stage the patched files from your checkouts onto the `/config` network share, into flat directories alongside `reinject.sh` (no subfolders, no `.git`) - this is what `reinject.sh` will keep injecting from, instead of downloading its own copy:
   - `/config/ma-squeezelite-browse/aioslimproto/` ← `aioslimproto/aioslimproto/{cli.py,models.py,server.py}`
   - `/config/ma-squeezelite-browse/server/` ← `server/music_assistant/providers/squeezelite/{provider.py,browselibrary.py,player.py,static/}`

   This is a plain file copy onto a mounted network drive - Finder, Explorer, `rsync`, `robocopy`, whatever your OS makes easiest. Nothing here requires a Unix shell, `tar`, or SSH piping, so this works the same way on Windows as on Mac/Linux. Don't pass `-update` once you're iterating this way - it would overwrite your staged edits with the forks' current contents.
4. SSH into Home Assistant using an add-on that gives you Docker access (Advanced SSH & Web Terminal is confirmed to work).
5. Edit `reinject.sh` and set `CONTAINER` to the name of your Music Assistant container (find it with `docker ps`).
6. If you're testing against a piCorePlayer client, `reinject.sh` includes a line that reboots it after patching:
   ```
   curl -s "http://192.168.0.241/cgi-bin/main.cgi?ACTION=reboot" > /dev/null
   ```
   Update the IP to your client, or remove the line if you don't need the client rebooted after each run.
7. If you're also testing against a real Squeezebox device (e.g. a UE Radio) on the same network, `reinject.sh` includes a second, separate best-effort step that reboots it over SSH afterward. The device must have **Remote Access** (sometimes called SSH/remote support access) enabled in its own on-device Settings menu first, or the SSH connection will simply fail/time out regardless of the credentials/algorithm overrides below.
   ```
   sshpass -p '1234' ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 -oHostKeyAlgorithms=+ssh-rsa -oCiphers=+aes256-cbc -oMACs=+hmac-sha1 root@192.168.0.242 reboot
   ```
   Real Squeezebox firmware only offers key-exchange/host-key/cipher/MAC algorithms modern OpenSSH disables by default, which is why those four `-o` overrides are there — adjust them if a different device offers a different set (the error message when connecting without one of these tells you exactly which category and which algorithm it's missing). Update the IP/password to your own device, or remove the step if you don't have one. This step needs `sshpass` (see Requirements above) and is skipped automatically if it's not installed. Note: this device's own SSH server has been observed to report a non-zero/error exit even when the command fully succeeded — if this step prints a warning, check whether the device actually rebooted before assuming it's broken.
8. Run it:
   ```
   ./reinject.sh
   ```
   This copies the staged patch files into the container, verifies each patch landed (it fails loudly if a marker is missing), confirms the provider module still imports cleanly, and then restarts the container so Music Assistant picks up the changes. After you make further edits in your own checkouts, re-stage (step 3) and re-run this.

## Persistence

The patch is applied directly to the container's filesystem, so it survives a plain container restart (which is what `reinject.sh` does at the end). It is **not** persistent across:

- Restarting the Music Assistant add-on/integration from the Home Assistant UI in a way that recreates the container, or
- Updating Music Assistant to a new version/image.

Either of those resets the container back to the stock image, so you'll need to run `reinject.sh` again afterward.
