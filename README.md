<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/koma-logo-dark.svg">
  <img src="docs/assets/koma-logo-light.svg" alt="Koma" width="320">
</picture>

# Koma

Koma adds features to the original Ricoh GR DIGITAL (2005) and connects it to a local app over USB.

<picture>
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/screens/overview-light.png">
  <img src="docs/assets/screens/overview-dark.png" alt="The Koma app's overview: the connected camera, its current look (MONO), firmware and Koma versions, and the last photo" width="800">
</picture>

## What it does

- Adds the KOMA menu tab, 21 looks and shooting options.
- Saves and restores the clock and records a battery log.
- Supports FAT32 SDHC and SDXC cards, including tested 64 GB cards.
- Provides settings, photo import, tethered shooting and webcam in the Koma app.

## Supported hardware

| Camera | Firmware | Status |
|---|---|---|
| Ricoh GR DIGITAL (2005) | 2.40 | Earlier builds tested on two cameras; hardware testing of Koma 1.0 is in progress. See [status](docs/user-guide.md#status). |

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

Set the camera's USB CONNECTION to ORIGINAL, turn it on, then connect USB. `koma --tray` keeps a tray icon; `koma doctor` checks USB. The app builds Koma on your computer from Ricoh's 2.40 update file ([download from Ricoh](https://www.ricoh-imaging.co.jp/english/r_dc/download/firmware/grd/win.html)). Setup needs an SD card of 2 GB or less.

> Flashing modified firmware can make a camera unusable. Use it at your own risk. Koma is provided under the MIT licence without warranty.

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

[Install](docs/install.md) · [User guide and status](docs/user-guide.md) · [Limitations](docs/limitations.md) · [Host tools](docs/host-tools.md) · [How it works](docs/how-it-works.md) · [Building](docs/building.md) · [Versions](docs/versioning.md) · [Internals](docs/internals/README.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md)

## Credits

See [how it works](docs/how-it-works.md) for the project’s reverse-engineering work.

Koma is not affiliated with Ricoh. Ricoh and GR are trademarks of Ricoh Imaging, used to identify the camera.

## Licence

Koma is maintained by Overscan Industries under the [MIT licence](LICENSE). Ricoh's firmware and software are not covered by it.
