<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/koma-logo-dark.svg">
  <img src="docs/assets/koma-logo-light.svg" alt="Koma" width="320">
</picture>

[English](README.md) · [日本語](README.ja.md)

# Koma

Koma gives the original Ricoh GR DIGITAL (2005) the shooting features of the GRs that came after it, fixes two problems it has had for 21 years, and connects it to a desktop app over USB. Shoot tethered with live view, or use it as the webcam on your next Zoom call.

<picture>
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/screens/overview-light.png">
  <img src="docs/assets/screens/overview-dark.png" alt="The Koma app's overview: the connected camera, its current look (MONO), firmware and Koma versions, and the last photo" width="800">
</picture>

## What it does

### Shooting features from later GRs

- Snap focus at six distances, 1 m to infinity.
- Full-press snap: press the shutter straight down and the camera fires at the snap distance without hunting for focus.
- Auto-ISO ceiling and slowest-shutter limit.
- Highlight alert and focus peaking on the LCD.
- Long exposures up to 240 s.
- A KOMA tab in the camera's own menu, with an optional GR II menu layout.

### 21 looks

- Ten black-and-white looks, six color looks, STANDARD, SELECT (one color kept, the rest mono) and three custom slots, all in IMG SET.
- Hard and Soft Monotone from the GR III. Bleach Bypass built from a GR DIGITAL IV's.
- Every look is editable: eight tone curves, contrast, key, shadow, highlight, B&W filter, toning, saturation, warmth, tint, sharpness, grain and vignette.
- Put the looks you use on the ADJ lever.

### Fixes for the 2005 firmware

- The camera remembers the date and time. A stock GR DIGITAL forgets them when the battery has been out too long; Koma puts them back.
- SDHC and SDXC cards work. Stock stops at 2 GB; Koma has been tested with 64 GB.
- A battery log is written to the card.

### The Koma app

- Tethered shooting with live view.
- Webcam, through OBS Virtual Camera.
- Photo import.
- Look editing on your last photo, sent to the camera.
- Backup and restore of every camera setting, and copying them to another GR DIGITAL.

## Supported hardware

| Camera | Firmware | Status |
|---|---|---|
| Ricoh GR DIGITAL (2005) | 2.40 | Tested on real hardware. |

## Install

macOS:

```sh
brew install overscanindustries/koma/koma
```

Linux and Windows, with [pipx](https://pipx.pypa.io):

```sh
pipx install koma-gr
```

Linux and Windows need one extra USB step: see [installation](docs/install.md).

## Run

```sh
koma
```

Set the camera's USB CONNECTION to ORIGINAL, turn it on, then connect USB. `koma --tray` keeps a tray icon; `koma doctor` checks USB.

Setup builds Koma on your computer from Ricoh's 2.40 update file ([download from Ricoh](https://www.ricoh-imaging.co.jp/english/r_dc/download/firmware/grd/win.html)) and needs an SD card of 2 GB or less.

> Flashing modified firmware can make a camera unusable.

## Screens

| | |
|---|---|
| <img src="docs/assets/screens/looks.png" alt="Looks: the 21 looks drawn on the last photo, with MONO selected and on the ADJ lever" width="400"> | <img src="docs/assets/screens/look-editor.png" alt="Look editor for SHINJUKU: curve, contrast, filter, toning, sharpness and grain controls beside a preview" width="400"> |
| Pick a look. The camera shows it in IMG SET. | Edit a look, then apply it to the camera. |
| <img src="docs/assets/screens/shoot.png" alt="Shoot: live view from the camera with exposure, ISO and white balance, a shutter button and the photos taken this session" width="400"> | <img src="docs/assets/screens/photos.png" alt="Photos: the camera's photos grouped by day, with an Import button" width="400"> |
| Shoot tethered with live view. | Import new photos from the card. |
| <img src="docs/assets/screens/settings.png" alt="Settings: the camera's menu rows with two pending changes and Review and Apply buttons" width="400"> | <img src="docs/assets/screens/webcam.png" alt="Webcam: the camera's picture shown as OBS Virtual Camera, with steps for the call app" width="400"> |
| Change camera settings and back them up. | Use the camera as a webcam. |

## Documentation

[Install](docs/install.md) · [User guide](docs/user-guide.md) · [Limitations](docs/limitations.md) · [Host tools](docs/host-tools.md) · [How it works](docs/how-it-works.md) · [Building](docs/building.md) · [Versions](docs/versioning.md) · [Internals](docs/internals/README.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md)

## Credits

The reverse-engineering work is described in [how it works](docs/how-it-works.md).

Koma is not affiliated with Ricoh. Ricoh and GR are trademarks of Ricoh Imaging, used to identify the camera.

## Licence

Koma is maintained by Overscan Industries under the [MIT licence](LICENSE). Ricoh's firmware and software are not covered by it.
