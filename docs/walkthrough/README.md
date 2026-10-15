# Menu walkthrough

The PDFs in `docs/` show the menus a device gets from the provider, for two devices, from the same list of steps:

- `MA-SqueezeliteBrowse-menu-walkthrough-all.pdf`: both decks in one file (what the main README links to);
- `MA-SqueezeliteBrowse-menu-walkthrough.pdf`: the piCorePlayer (JiveLite) deck;
- `MA-SqueezeliteBrowse-menu-walkthrough-UE2.pdf`: the Squeezebox Radio (SqueezePlay) deck.

`pcp/` and `radio/` hold the screenshots (`NN.jpg`, numbered by step) and a `capture-log.json` of the run that made them (2026-10-14).

## How it is made

The steps are written once for every device and run by a wrapper for each device:

- `e2e/walkthrough/steps.py` says where to go and what to capture: a path of menu labels from the home menu (for example
  `My Music > Artists > the first entry > long press`), then a screenshot. It knows nothing about any device.
- `e2e/walkthrough/macro.py` turns the steps into a macro for the MacroPlay applet. SqueezePlay (the Radio) ships it; JiveLite
  (the piCorePlayer) does not, so `e2e/walkthrough/macroplay/` holds a copy (Logitech's, BSD licensed) that is installed
  temporarily. The macro runs inside the app: it picks every entry by its label, opens the menu a long press opens by firing the
  app's own action, and saves each screen as the app draws it (without the wallpaper, so the background is black).
- `e2e/walkthrough/radio.py` and `e2e/walkthrough/pcp.py` are the wrappers. Both copy the macro in, restart the UI so the
  macro runs (the Radio's UI is restarted from its init script; JiveLite is closed from its Quit tile, after reading which tile is
  highlighted, and its wrapper script starts it again), fetch the screenshots and remove everything again. Killing JiveLite's process
  instead can leave the display stuck on a black screen.
- `python3 e2e/walk.py capture [--device radio] --out docs/walkthrough/<device>` runs it, and `python3 e2e/walk.py pdf --shots
  docs/walkthrough/<device> --out <file>.pdf --title "..." --intro "..."` builds a PDF (needs reportlab and Pillow). Steps that need
  something playing (Now Playing, the queue) have a paused track, or five queued tracks, loaded first, at a low volume.

Nothing that plays something or changes a setting is selected: the steps only open menus and views.
