[日本語](ja/manual-install.md)

# Manual installation

For app-guided setup, see [installation](install.md). You need a GR DIGITAL on firmware 2.40, Ricoh's 2.40 update file (`qrflect3` or `gr-240.exe`, from [Ricoh](https://www.ricoh-imaging.co.jp/english/r_dc/download/firmware/grd/win.html); hashes in [installation](install.md#koma-on-the-camera)), a charged battery, a card reader, an SD card of 2 GB or less and the tools in [building](building.md). Keep an untouched copy of Ricoh's update for restoration.

## Build and flash the firmware

```sh
tools/koma/koma make-firmware /path/to/qrflect3 -o QRFLECT3
```

Reading `gr-240.exe` needs `bsdtar`.

1. Copy `QRFLECT3` to the root of the small card. Put no other update file there; never copy `QRFLECT7`.
2. Set the dial to Movie. Hold UP and TELE, then hold PLAY for about 3 seconds until the LCD lights. Release, wait about 5 seconds, press RIGHT, then MENU/OK.
3. Do not cut power while the lamp blinks green.
4. After restart, the service version screen still reads `02.40`. A new photo's MakerNote firmware tag reads `240K1.0`; check with `exiftool -n -FirmwareVersion R00nnnnn.JPG`.

## Install Koma

Rebuild the Koma 1.0 package from Ricoh's file (the release ships it as `dist-release/koma-1.0.0.grmod.delta`, without Ricoh's bytes):

```sh
tools/koma/koma make-package /path/to/qrflect3 -o GRMOD.BIN
```

Or build your own package as described in [building](building.md). For an SD card of 2 GB or less, copy the package to the card root as `GRMOD.BIN` and power on. KOMA appears in the menu. For larger cards, install it in internal memory:

1. Put the [installer](../analysis/sdhc/diag/README.md) on the small card as `GRMOD.BIN` and the package as `KOMAPKG.BIN`.
2. Power on, wait 60 seconds, then power off.
3. Read `IRODUMP.TXT` on the card. Success is `result=installed` or `result=same`, with `dst_valid=1`, `copy_rc=0` and a final line `end=1`. `result=unmounted` or `badsrc` means nothing was written; `verify_fail` means the bad copy was deleted.
4. Delete `GRMOD.BIN` and `KOMAPKG.BIN` from the card. A card copy of `GRMOD.BIN` takes precedence over the internal copy.

## Update, remove or restore

- Update a card installation by replacing `GRMOD.BIN`. For internal memory, repeat the installer steps with the new package. Power-cycle the camera.
- Remove a card installation by deleting `GRMOD.BIN` or removing the card.
- To remove the internal copy, put the installer on a small card as `GRMOD.BIN` beside an empty `KOMARM.TXT`, with no `KOMAPKG.BIN`. Boot for 60 seconds and check for `result=removed`. This removal path has not been tested on a camera.
- To restore Ricoh firmware, flash the untouched `qrflect3` as `QRFLECT3` using the steps above. Restoration over the current Koma firmware has not been tested on a camera. An internal copy may remain after a flash; remove it first if internal memory needs to be clean.

If the menu is unchanged, check the file name, card root and firmware tag. SDHC and SDXC cards need the internal installation. If the camera hangs, remove the battery and card. Koma settings remain in the camera after removal but are ignored by stock firmware.
