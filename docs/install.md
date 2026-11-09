# Install

Flashing modified firmware can leave a camera that does not boot, and recovery from that has not been tested. Flash only a camera you are prepared to lose.

You need a GR DIGITAL (2005) on firmware 2.40, Ricoh's 2.40 update ([`gr-240.exe`](https://www.ricoh-imaging.co.jp/english/r_dc/download/firmware/grd/win.html) from Ricoh's site, or `qrflect3` extracted from it), an SD card of 2 GB or less, a charged battery, and a computer set up as in [building](building.md). Keep an untouched copy of `qrflect3` to restore with. [firmware/README.md](../firmware/README.md) lists where to put it. `exiftool` is handy for checking the result.

To check the version, turn the camera off, set the mode dial to Movie, hold DOWN and the zoom lever at TELE, then hold PLAY. The version screen shows for about 20 s, and CPU2 is the camera firmware.

Run every command from the repository root. Each build script checks its input and output by SHA-256 and writes nothing if either is wrong.

## 1. Build the firmware

Koma 2 adds SDHC and SDXC cards and can load the package from internal memory. Use it unless you have a reason not to.

```
.venv/bin/python tools/koma/firmware2/build.py <qrflect3 | gr-240.exe> -o QRFLECT3
```

Koma 1 adds only the card loader and works with cards of 2 GB or less.

```
tools/koma/koma make-loader <qrflect3 | gr-240.exe> -o QRFLECT3
```

Reading `gr-240.exe` needs `bsdtar`.

## 2. Flash

1. Copy `QRFLECT3` to the root of an SD card of 2 GB or less. Put no other update file on it.
2. With the camera off and no USB cable, put the card in and set the mode dial to Movie. Hold UP (the flash button) and the zoom lever at TELE, then hold PLAY for about 3 s until the LCD lights. Release all three. The camera asks whether to update the firmware, with No selected. Press RIGHT for Yes, then MENU/OK.
3. The lamp blinks green for 2 to 3 minutes. Do not cut power.
4. After the restart, take a photo and run `exiftool -n -FirmwareVersion R00nnnnn.JPG`. It reads `240K2` on Koma 2 and `Rev024M` on Koma 1. Stock reads `Rev0240`. The version screen still reads `02.40`.

## 3. Build the package

Build `koma04w` for Koma 2. It adds full-size SDHC and SDXC cards.

```
KOMA_FIRMWARE=<2.40 file> .venv/bin/python tools/koma/sdhc/build_koma04w.py -o koma04w.grmod
```

Build `koma04` for Koma 1.

```
tools/koma/koma build --firmware <2.40 file> --features koma04 --opt telemetry.cp15=off -o koma04.grmod
```

Every camera build needs `--opt telemetry.cp15=off`, because reading CP15 hangs the camera at boot. `build_koma04w.py` sets it for you.

## 4. Install the package

### On the card

Copy the package to the root of a card of 2 GB or less as `GRMOD.BIN` and power on. The menu gains a KOMA tab. A card larger than 2 GB is not mounted when the loader runs, so on those cards use internal memory.

### In internal memory (Koma 2)

```
.venv/bin/python tools/koma/sdhc/irominstall.py -o OUTDIR
```

1. On a card of 2 GB or less, put `OUTDIR/irodump.grmod` at the root as `GRMOD.BIN` and your package as `KOMAPKG.BIN`.
2. Power on, wait 60 s, power off.
3. Open `IRODUMP.TXT` on the card. The last line must be `end=1`. Success is `result=installed` with `copy_rc=0` and `dst_valid=1`, or `result=same` with `dst_before_valid=1`, which means the same package was already there. `nosrc`, `badsrc` or `unmounted` means nothing was written, and `verify_fail` means the bad copy was deleted.
4. Delete `GRMOD.BIN` and `KOMAPKG.BIN` from the card. A card `GRMOD.BIN` always loads instead of the one in internal memory.

The package now loads with any card, including 64 GB cards formatted FAT32. exFAT cards are refused.

## Update

Replace the card `GRMOD.BIN`, or repeat the internal-memory install with the new package. Then power-cycle.

## Remove

Delete the card `GRMOD.BIN` or take the card out. To clear internal memory, put `irodump.grmod` on the card as `GRMOD.BIN` next to an empty file named `KOMARM.TXT`, with no `KOMAPKG.BIN`. Boot for 60 s and look for `result=removed` in `IRODUMP.TXT` (not yet tested on a camera).

If the camera hangs at power-on, take the battery out, then remove the package. A card `GRMOD.BIN` loads instead of the one in internal memory, so the internal-memory removal above still runs.

## Restore stock firmware

Flash Ricoh's untouched `qrflect3` as `QRFLECT3` the same way as in step 2. This has worked over Koma 1 and is not yet tested over Koma 2. A flash should not touch internal memory, so remove the package first if you want a clean camera.
