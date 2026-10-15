# Menu walkthrough

How `docs/MA-SqueezeliteBrowse-menu-walkthrough.pdf` was made (October 2026, piCorePlayer only):

1. `capture.py`, `capture2.py`, `capture3.py` walk the pCP's menus with `e2e/ui.py` and save one screenshot per screen into `shots/`
   (`capture.py` takes menu positions as indexes, e.g. "My Music is the 2nd tile"; the later two read the highlighted label before
   pressing Enter). `capture-log.json` is the last run's OCR check of each slide's title.
2. `build.py` holds the slide list (screenshot number, title, note) and builds a `.pptx` from it (python-pptx);
   `build_pdf.py` draws the PDF from the same list and screenshots (reportlab).

Only keys that navigate are used; nothing that plays or changes a setting is selected.
