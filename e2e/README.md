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
   volume to 0 for the run and restores it afterwards when you pass `--silent`; by default it plays at the player's current volume. Mute is not used:
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
* JiveLite has no built-in test or remote-control interface, so the UI is driven with key presses
  injected through uinput (see "Driving the player UI" below) and checked with its Screenshot applet.

## Driving the player UI (virtual keyboard)

`tools/vkbd.c` creates a uinput keyboard on the player and replays `d <code>` / `u <code>` / `s <ms>`
lines from stdin, so key presses reach JiveLite as if typed. JiveLite's character shortcuts are in
`InputToActionMap.lua` (for example `n` Now Playing, `l` go, `j` back, `h` home, Shift+S screenshot).
The screenshot lands in `/tmp/jivelite*.bmp` on the player; `PlayerUi.screenshot` copies it back and saves it as a JPEG when the destination ends in `.jpg` (about 70 KB instead of 6 MB).

    zig cc -target arm-linux-musleabihf -static -O2 -s e2e/tools/vkbd.c -o vkbd
    scp vkbd tc@<player>:/tmp/ && ssh tc@<player> 'sudo modprobe uinput'
    printf 'd 42\nd 31\ns 100\nu 31\nu 42\n' | ssh tc@<player> 'sudo /tmp/vkbd'   # Shift+S

Nothing is installed permanently: /tmp and the loaded module disappear on the player's next reboot.

`ui.py` wraps this (`PlayerUi.press`, `hold`, `screenshot`; set `E2E_PLAYER_PASSWORD` or use an ssh key).
JiveLite's own log shows only network and menu-sync events, not which screen is open. To tell which
menu the player is on, use `ServerLog.browse_requests`, which reads the `browselibrary` request
each screen sends, or take a screenshot. Arrow keys move the highlight, Enter or `l` opens an item,
`j` goes back (the Left arrow does not), `h` is home.

Navigation notes: grids and lists behave as one list. Right moves to the next entry (wrapping to the
next row), Up moves back one and stops at the first, Down is unreliable, so `PlayerUi.open_my_music`
resets with `h` and Up x12, then uses Right. E2E-17 uses it and needs `player_host` in the config
and `E2E_PLAYER_PASSWORD` in the environment (the helper and uinput are reinstalled automatically
after the player reboots). Screenshots from the run are written to `reports/screens/`.

`reports/sample/` holds an example report and the screenshots from a passing E2E-17 run. Other
runs write to `reports/` and are not tracked.

Optional Music Assistant API access: set `E2E_MA_TOKEN` (an admin token; needs `aiohttp` installed) and
cases can compare what the player shows with what the server holds. E2E-18 uses it to check that every
favorite of each type is listed, with a loadable icon. Without the token those checks are skipped.

`PlayerUi` also has `type_text` (letters, digits and common punctuation, for text fields only), `long_press`
(Enter held 3.5 s, which opens a row's long-press menu), `home`, `choose_player` and `reset`. `run.py` calls
`reset` before the cases when `player_host` is set: it checks which player the UI controls from the player id on
a browse request and switches it through Choose Player if needed (`player_row` in the config is that player's
row, default 0).

Reading the screen: `ocr.py` runs tesseract over a screenshot (light-on-dark and dark-on-light passes) and
`Ctx.check_screen(label, present=(...), absent=(...))` asserts what is and is not shown, with a tolerance for the
odd misread letter. It needs the `tesseract` command (`brew install tesseract`, `apt install tesseract-ocr`) and
Pillow. The screenshot of every check is kept in `reports/screens/<case id>-<label>.jpg`. Cases using it:
E2E-17 (list titles) and E2E-20 (the five rows of a long-press menu on the device).

E2E-21 is the long scenario: with an empty queue it adds tracks with taps and long presses on the device
(Play Next, Add to the queue), pauses so nothing ends mid-run, reads the queue back through the Music
Assistant API, skips through every track with Next, jumps with Play Now, then uses the queue screen's
long-press menu (Delete item, Move to End, Play Next) and finally Clear queue. It takes about 10 minutes.

At the end of a run the result is spoken on the player ("Testing complete with 3 issues") through Music Assistant's
announcement feature, when `E2E_MA_TOKEN` is set; `--no-announce` turns it off. Music Assistant needs a text-to-speech
engine for this (here the Home Assistant one); the announcement uses Music Assistant's own announcement volume setting (set `announce_volume` in the config to override it; Music Assistant may then leave the volume at that level).
