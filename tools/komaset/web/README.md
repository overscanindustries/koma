# Koma Setup (web)

`koma-setup.html` is a single-file page for a GR DIGITAL's settings, photos and Koma features. It reads the 4 KB settings file the camera writes to the card (`PARAM.CUR`) and writes the file the camera loads at its next start (`PARAM.DAT`). With the bridge running it also talks to the camera over USB. Nothing is uploaded anywhere.

## Run

```
.venv/bin/python tools/komaset/bridge/komabridge.py --real                          # a camera: turn it on, then plug in USB
.venv/bin/python tools/komaset/bridge/komabridge.py --mock --mock-tether --mock-hw  # a simulated camera
```

Then open http://localhost:8765/. You can also double-click `koma-setup.html` for the file-only tasks. From `file://` it uses the settings list embedded in the page. `?bridge=PORT` points the page at a bridge on another port.

## Tasks

| Task | Does | Needs |
|---|---|---|
| Copy settings to another camera | Merges one camera's settings onto another's, keeping camera-specific items | Files or USB |
| Change settings | Edits one file in a menu-like view | Files or USB |
| Choose your Koma mode | Sets the Koma menu layout and feature set | Files or USB |
| Back up or restore | Saves the settings block, or restores a backup as `PARAM.DAT` | Files or USB |
| Review photos | Lists and downloads the card's JPEGs | USB |
| Shoot | Take a photo, focus, live view, interval shooting, exposure | USB and the `tether` feature |
| Remote | The camera's screen and buttons, for a camera with a broken screen | USB and the `tether` feature |
| Looks | Edits the Koma recipes, live or in a file | Files or USB |
| Set up a card | Checks a Koma package's SHA-256, saves it as `GRMOD.BIN`, then checks a card after a start | Nothing |

Set up a card first asks whether the camera runs Koma 1 or Koma 2. Koma 1 cameras need `koma04`. Koma 2 cameras need `koma04w`, which adds SDHC and SDXC cards. With a camera on the bridge, the page reads the firmware tag Koma keeps in the settings block and picks for you: `Rev024M` is Koma 1 and `240K2` is Koma 2. A camera without Koma's live settings cannot answer, so you pick.

The page then fetches `build/koma04.grmod` or `build/koma04w.grmod`, plus `build/SHA256SUMS`, when the bridge serves it. Build them with:

```
tools/koma/koma build --features koma04 --opt telemetry.cp15=off -o build/koma04.grmod
KOMA_FIRMWARE=<2.40 file> .venv/bin/python tools/koma/sdhc/build_koma04w.py -o build/koma04w.grmod
(cd build && shasum -a 256 koma04.grmod koma04w.grmod > SHA256SUMS)
```

Without them the page asks you to choose the file. Rerun `testdata/make_demo.py` after a rebuild so the page's built-in hashes match.

`?demo=copy`, `edit`, `mode`, `backup`, `looks` or `setup` loads demo files and opens that task. **Advanced** in the footer opens a raw field editor, the map picker, a raw diff and a hex dump.

## Phone

Run `komabridge.py --real --lan` and open the printed URL on the phone, or scan its QR code. The page can be added to the home screen. Over the plain-http LAN address it does not work offline.

## Files

`testdata/make_demo.py` rebuilds the demo blocks, embeds `../settings-map.json` in the page and refreshes the app icon and `manifest.webmanifest`. Rerun it after `settings-map.json` changes.

```
python3 tools/komaset/web/testdata/make_demo.py [--main path/to/PARAM.CUR]
```

## Tests

`test_web_parity.py` checks the page's engine (checksum, codec, merge) and embedded data against the Python tools. `test_e2e_browser.py` drives the page in headless Chrome against the mock bridge. It needs Playwright and is skipped without it. Both run in `tools/check.py test`.
