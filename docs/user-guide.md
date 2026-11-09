# User guide

This covers package `koma04w` and `koma04`. Anything marked "not yet tested on a camera" has run only in the emulator.

## The KOMA tab

Press MENU. The tabs read STILL, KOMA, PLAY, CUSTOM and SETUP. LEFT goes to the page list and RIGHT comes back. KOMA cannot be reached in movie mode.

The KOMA tab has five pages. RECIPE holds the looks, described below. The rows on FOCUS, EXPOSURE, DISPLAY and OTHER are not yet tested on a camera.

- SNAP DIST sets the snap distance to 1M, 1.5M, 2M, 2.5M (stock), 5M or ∞.
- FULL SNAP ON makes a quick full press focus at the snap distance.
- AUTO-ISO MAX caps ISO AUTO at 200, 400, 800 or 1600 when the flash is off.
- SLOW SHUTTER sets the slowest shutter speed, from 1/250 to 1/2, before ISO rises.
- LONG EXP LIMIT allows 240 s exposures instead of 180 s.
- HIGHLIGHT ALRT blinks clipped areas in live view and playback.
- PEAKING marks in-focus edges in red or yellow.
- KOMA VERSION shows the package version.
- MENU LAYOUT picks 5-TAB, STK+KOMA or a GR II style layout. It applies at the next boot.
- KOMA FEATURES set to ESSENTIAL loads only the core features at the next boot.
- SAVE TO CARD writes the settings to `PARAM.SAV`.

## Looks

A look (RECIPE in the menu) is a tone curve plus settings, rendered into the JPEG. The looks follow the stock IMG SET entries. Pick one in KOMA > RECIPE, in IMG SET, or with the ADJ lever when IMG SET is assigned to it. Choosing a stock entry turns the look off.

The black-and-white looks are MONO, SHINJUKU, HICON B&W, NOIR, HARD MONO, SOFT MONO, FILM 400, FINE 100, DENSE and MATTE. The last six are not yet tested on a camera.

The colour looks are POSITIVE, NEGATIVE, BLEACH, RETRO, WARM NEG and SLIDE 50. WARM NEG and SLIDE 50 are not yet tested on a camera, and nor are the current tables for POSITIVE and BLEACH.

SELECT keeps one band of colour and turns the rest grey. Set the band with COLOUR, its width with RANGE and its strength with SATURATION. It adds about 1.7 s per shot.

EDIT RECIPE edits the active look, and each look keeps its own edits through power-off. Every look has CURVE, CONTRAST, KEY (overall brightness), SHADOW, HIGHLIGHT, SHARPNESS, GRAIN and VIGNETTE. B&W looks add FILTER EFF., TONING and TONE LEVEL. Colour looks add SATURATION, WARMTH and TINT. SAVE TO CUSTOM copies the look into CUSTOM 1, 2 or 3, and RESET restores its defaults. GRAIN and VIGNETTE add about 1.5 s per shot and are not yet tested on a camera.

The LCD shows a look's colour and toning but not its tone curve. The JPEG always gets the full look.

## Settings files

The package reads and writes these files at the card root.

| File | Purpose |
|---|---|
| `KOMALOG.TXT` | Written at every boot. If it is missing, the package did not load. |
| `PARAM.CUR` | The camera's settings, written at every boot. |
| `PARAM.DAT` | Settings to load at the next boot. The camera answers with `PARAM.OK` or `PARAM.BAD`. |
| `PARAM.SAV` | Written by SAVE TO CARD. |

Loading settings never touches calibration or counters. Make `PARAM.DAT` with `komaset` or the Koma Setup page. A raw copy of `PARAM.CUR` can be refused.

## Host tools

Set SETUP > USB CONNECTION to ORIGINAL. Turn the camera on before you plug in the cable. If the cable goes in first, the camera starts in a state that remote control cannot use. Live view drains the battery fast.

### Koma Setup page

```
.venv/bin/python tools/komaset/bridge/komabridge.py --real
```

Then open `http://localhost:8765/` for photos, shooting, remote control, looks and settings. `--mock --mock-tether` runs it without a camera. `--lan` lets a phone connect with a one-time token, and anyone with the token can read the card and press buttons. Editing looks and `--lan` are not yet tested on a camera.

### Webcam

```
.venv/bin/python tools/komacam/komacam.py --spawn real
```

This starts the bridge and serves live view at `http://127.0.0.1:8770/`. In OBS, add a Browser source with that URL at width 1280 and height 720, untick "Shutdown source when not visible", and use Transform > Fit to screen. Click Start Virtual Camera (macOS asks once to allow the camera extension), then pick OBS Virtual Camera in your video app. If the app shows only an idle frame, Start Virtual Camera was not clicked. `--mode 3` gives larger frames at about 13 fps. Don't open live view in Koma Setup at the same time. Press Ctrl-C once to stop, because a second press can leave the camera in remote mode until you power-cycle it.

### Settings CLI

```
.venv/bin/python tools/komaset/komaset.py dump PARAM.CUR -o settings.txt
.venv/bin/python tools/komaset/komaset.py apply settings.txt -o PARAM.DAT --base PARAM.CUR
.venv/bin/python tools/komaset/komaset.py merge --target other.cur --source mine.cur -o PARAM.DAT
```

`merge` copies one camera's settings onto another's. Put the output on the target camera's card as `PARAM.DAT` and boot. See [tools/komaset/README.md](../tools/komaset/README.md) for the other commands.

## Known issues

- Pressing ADJ can freeze the camera until power-off. This also happens on stock 2.40 with no card and is a contact fault of these bodies.
- Listing the card root over USB never finishes on a FAT32 card. The bridge avoids it.
- A tethered shot taken with a quick press can produce no file.
- Settings edits over USB can answer busy for about 9 s after an EV change.
- The IMG SET icon shows only the stock base of a look.
- MY SETTINGS can keep a stale look.
- The stock ADJ detail page edits values that a look ignores.
- Only 64 GB cards have been tested above 2 GB. Cards must be FAT32 with clusters of 32 KB or less.
