# End-to-end tests

Drives a real player through the same LMS-style JSON-RPC the devices use, then checks what the
server did and what the client would show. Standard library only (Python 3.10+).

## Why JSON-RPC and not the Music Assistant API

Music Assistant's own websocket API needs a login token. The JSON-RPC on the Squeezelite port
(9000) does not, and it is the exact path the devices take (`browselibrary`, `playlistcontrol`,
`status`, `contextmenu`, ...), so the tests exercise the patched code, not a parallel route.

## Setup

1. Copy `config.example.json` to `config.json` and fill it in (it is git-ignored):
   server URL, the player id and name, the piCorePlayer's JiveLite log URL, and (optional) ssh
   details for the Music Assistant container log.
2. `python3 e2e/run.py --dry-run` finds one item of each media type and shows the plan. It does
   not change anything.
3. `python3 e2e/run.py` runs the cases. It refuses to start unless the configured player is
   idle with an empty queue (or you pass `--force`), only ever touches that one player, sets its
   volume to 0 for the run and restores it afterwards (`--audible` to hear it). Mute is not used:
   Squeezelite treats the protocol's mute as an output off switch that the next stream undoes.

`--list` shows the cases, `--only E2E-04,E2E-07` runs a few, `-v` shows passing checks.
Reports (markdown and json) go to `e2e/reports/`.

## What each push checks

* the player reaches `play` and its elapsed time advances
* the queue length, current index and the playing item's name
* every queue row's icon is fetchable the way JiveLite fetches it, and is a real image (a
  small placeholder PNG is a warning, a blank icon or a raw URL is a failure)
* a showBriefly popup reached the client (a `/slim/displaystatus/<player>` push in its log)
* no Lua errors in the client log since the step started
* no tracebacks or handler errors in the server log since the step started

Other cases check the long-press menus, the one-track queue menu and Delete item, queue menus
by row position, Move to End, every browse list's icons, the Favorites screen and the home menu.

## Limits

* A popup push in the client log shows the server sent it and the client handled it without an
  error. It cannot prove what was drawn on screen.
* The client caches failed artwork fetches for the whole session. The tests fetch each icon
  fresh, so they will not reproduce a device stuck on a failed fetch from an earlier start.
* JiveLite has no built-in test or remote-control interface. Its Screenshot applet (hold Pause
  and Rew) saves a `.bmp` in `/tmp` on the device but needs key input, so it is not used here.

## Driving the player UI (virtual keyboard)

`tools/vkbd.c` creates a uinput keyboard on the player and replays `d <code>` / `u <code>` / `s <ms>`
lines from stdin, so key presses reach JiveLite as if typed. JiveLite's character shortcuts are in
`InputToActionMap.lua` (for example `n` Now Playing, `l` go, `j` back, `h` home, Shift+S screenshot).
The screenshot lands in `/tmp/jivelite*.bmp` on the player.

    zig cc -target arm-linux-musleabihf -static -O2 e2e/tools/vkbd.c -o vkbd
    scp vkbd tc@<player>:/tmp/ && ssh tc@<player> 'sudo modprobe uinput'
    printf 'd 42\nd 31\ns 100\nu 31\nu 42\n' | ssh tc@<player> 'sudo /tmp/vkbd'   # Shift+S

Nothing is installed permanently: /tmp and the loaded module disappear on the player's next reboot.
