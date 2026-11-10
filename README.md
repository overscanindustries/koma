<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/koma-logo-dark.svg">
  <img src="docs/assets/koma-logo-light.svg" alt="Koma" width="320">
</picture>

# Koma

Koma adds features to the original Ricoh GR DIGITAL (2005) and connects it to a local app over USB.

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

Set the camera's USB CONNECTION to ORIGINAL, turn it on, then connect USB. `koma --tray` keeps a tray icon; `koma doctor` checks USB. The app builds Koma on your computer from your own Ricoh 2.40 update file. Setup needs an SD card of 2 GB or less.

> Flashing modified firmware can make a camera unusable. Use it at your own risk. Koma is provided under the MIT licence without warranty.

## Documentation

[Install](docs/install.md) · [User guide and status](docs/user-guide.md) · [Limitations](docs/limitations.md) · [Host tools](docs/host-tools.md) · [How it works](docs/how-it-works.md) · [Building](docs/building.md) · [Versions](docs/versioning.md) · [Internals](docs/internals/README.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md)

## Credits

See [how it works](docs/how-it-works.md) for the project’s reverse-engineering work.

Koma is not affiliated with Ricoh. Ricoh and GR are trademarks of Ricoh Imaging, used to identify the camera.

## Licence

Koma is maintained by Overscan Industries under the [MIT licence](LICENSE). Ricoh's firmware and software are not covered by it.
