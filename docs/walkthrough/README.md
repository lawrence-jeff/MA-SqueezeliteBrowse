# Menu walkthrough

The PDFs in `docs/` show the menus a device gets from the provider:

- `MA-SqueezeliteBrowse-menu-walkthrough-all.pdf`: both decks in one file (what the main README links to);
- `MA-SqueezeliteBrowse-menu-walkthrough.pdf`: the piCorePlayer (JiveLite) deck, captured 2026-10-11;
- `MA-SqueezeliteBrowse-menu-walkthrough-UE2.pdf`: the Squeezebox Radio (SqueezePlay) deck, captured 2026-10-14.

## Squeezebox Radio deck (current method)

Written once for every device and run by a wrapper for each device:

- `e2e/walkthrough/steps.py` says where to go and what to capture (a path of menu labels from the home menu, e.g.
  `My Music > Artists > Avril Lavigne`, then a screenshot). It knows nothing about any device.
- `e2e/walkthrough/radio.py` is the Radio's wrapper. It turns the steps into a macro for SqueezePlay's own MacroPlay applet, which
  picks entries by label inside the app, so no knob or key presses are used. It copies the macro to the Radio, restarts the Radio's UI so
  it runs, fetches the screenshots (one BMP per step) and removes everything again.
- `python3 e2e/walk.py capture --device radio --out docs/walkthrough/radio` runs it; `python3 e2e/walk.py pdf --device radio
  --shots docs/walkthrough/radio --out <file>.pdf --title "Squeezebox Radio (UE2)"` builds the PDF (needs reportlab and Pillow).
  `docs/walkthrough/radio/` holds the screenshots and `capture-log.json` of the 2026-10-14 run.

The Radio's screenshots are the app's own drawing, so they have a black background instead of the wallpaper.
The piCorePlayer's JiveLite has no macro applet; its wrapper is not written yet.

## piCorePlayer deck (the first method)

`capture.py`, `capture2.py`, `capture3.py` walk the pCP's menus with `e2e/ui.py` and save one screenshot per screen into `shots/`
(`capture.py` takes menu positions as indexes, e.g. "My Music is the 2nd tile"; the later two read the highlighted label before
pressing Enter). `capture-log.json` is the last run's OCR check of each slide's title. `build.py` holds the slide list
(screenshot number, title, note) and builds a `.pptx` from it (python-pptx); `build_pdf.py` draws the PDF from the same list and
screenshots (reportlab).

Only keys that navigate are used; nothing that plays or changes a setting is selected.
