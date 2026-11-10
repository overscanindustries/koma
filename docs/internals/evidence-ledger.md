# Features and status

This is the one place that says what works and how it is known. Other pages reuse these tags and link here.

## Status tags

| Tag | Meaning |
|---|---|
| `HW` | Observed on a camera, or in files a camera wrote. |
| `EMU` | The original 2.40 firmware code ran in the emulator (or the unit emulator) with the feature. Says nothing about hardware. |
| `STATIC` | Read from firmware bytes or disassembly; not run. |
| `INFERRED` | Deduced from related evidence; not observed. |

A tag describes the current code unless the row says otherwise. Hardware results come from two bodies (a main camera and a spare with a broken LCD) on the dates given in the evidence. The Koma 1.0 package and firmware as built have not yet run on a camera: a `HW` row below ran on a pre-release build (most recently `koma04w`, build `747F061F`, on the `240K2` firmware).

## Camera features (Koma 1.0 package)

| Feature | What it does | Status | Evidence |
|---|---|---|---|
| Five-tab menu | STILL / KOMA / PLAY / CUSTOM / SETUP; the KOMA tab takes the movie-only slot | `HW` | [spare and main tests 2026-11-07](../../analysis/hw-tests/2026-11-07-spare.md) (main, Part A) |
| MENU LAYOUT: STK+KOMA | Mode page / KOMA / stock SETUP | `EMU` | [koma-modes](../../analysis/koma-modes/README.md) |
| MENU LAYOUT: GR II | SHOOTING / KEY CUST. / SETUP, Koma rows on SHOOTING | `EMU` | [gr2-layout](../../analysis/gr2-layout/README.md) |
| KOMA FEATURES: ESSENTIAL | At the next boot, installs only menu, settings I/O, telemetry, tether, live view and stall fix | `EMU` | [koma-modes](../../analysis/koma-modes/README.md) |
| Looks | 10 B&W looks, SELECT, 6 colour looks, CUSTOM 1-3 in IMG SET, editable per look | `HW` for the look path and for MONO, SHINJUKU, HICON B&W, NOIR, POSITIVE, NEGATIVE, BLEACH, RETRO (frames from an earlier build); `EMU` for the other looks, CUSTOM slots and the later refit of POSITIVE and BLEACH | [test02](../../analysis/colour-research/test02/README.md), [bwrecipes notes](../../analysis/koma-0.1/bwrecipes.md) |
| Single IMG SET picker | One 21-entry list in GR III order (STANDARD = stock NORMAL, other stock entries hidden, CUSTOM 1-3 in place of SETTING1/2); RIGHT opens the look editor; the RECIPE and EDIT RECIPE rows are gone | `EMU` | [look-ia](../../analysis/look-ia/README.md) |
| ADJ LEVER per look | Editor row choosing which looks the ADJ lever steps through | `EMU` | [look-ia](../../analysis/look-ia/README.md) |
| SELECT | Mono except one hue band | `HW` | [selcolour1](../../analysis/colour-research/selcolour1/README.md) |
| GRAIN, VIGNETTE | Per-look film grain and corner falloff | `EMU` | [fx](../../tools/koma/features/fx.py) |
| Live-view look preview | LCD shows the active look's colour, toning and filter | `EMU` | [koma-0.3-preview](../../analysis/koma-0.3-preview) |
| SNAP DIST, FULL SNAP | Snap distance 1 m to infinity; quick full press focuses at it | `EMU` | [snapdist](../../tools/koma/features/snapdist.py), [fullpress](../../tools/koma/features/fullpress.py) |
| AUTO-ISO MAX, SLOW SHUTTER | ISO-auto ceiling and slowest shutter before ISO rises | `EMU` | [autoiso](../../tools/koma/features/autoiso.py) |
| LONG EXP LIMIT | 180 s or 240 s | `EMU` | [longexp](../../tools/koma/features/longexp.py) |
| HIGHLIGHT ALRT | Blinking clipped areas in live view, histogram display and playback | `EMU` | [hlalert notes](../../analysis/koma-0.1/hlalert.md) |
| PEAKING | Focus peaking on the LCD in live view | `EMU` | [peaking](../../analysis/peaking/README.md) |
| Faster keys | Key scan every 20 ticks instead of 50, faster auto-repeat | `EMU` | [fastkeys](../../tools/koma/features/fastkeys.py) |
| Boot log | `KOMALOG.TXT`: build id, install stage, resident block, pool | `HW` | [koma-0.1/hw](../../analysis/koma-0.1/hw), [2026-11-07 tests](../../analysis/hw-tests/2026-11-07-spare.md) |
| Clock keeper | Restores a lost clock at power-on from `KOMATIME.TXT`; saves the running clock there at every power-off (not with USB attached or a flat battery) | Power-off save and restore of a lost clock: `EMU`. Loads and leaves a running clock alone: `HW` (spare, 2026-11-07, earlier build, whose boot-time save found the clock not yet read) | [clockkeep](../../tools/koma/features/clockkeep.py), [clockkeep notes](../../analysis/koma-0.1/clockkeep.md), [spare test 3](../../analysis/hw-tests/2026-11-07-spare.md) |
| Photo firmware tag (`fwtag`) | Writes `240K1.0` into the photo MakerNote firmware tag and `PARAM.CUR` at every start, so the tag names the running package | `EMU` (JPEG and DNG headers, on the 1.0 and the `240K2` firmware) | [fwtag](../../tools/koma/features/fwtag.py) |
| Battery log | Writes the battery check's raw readings, level and thresholds to `KOMABATT.TXT` at power-off | `HW` (one complete log, spare, 2026-11-07, earlier build) | [battery notes](../../analysis/koma-0.1/battery.md), [spare test 3](../../analysis/hw-tests/2026-11-07-spare.md) |
| Settings export | `PARAM.CUR` (4,096 B settings block) written every boot | `HW` | [2026-11-07 tests](../../analysis/hw-tests/2026-11-07-spare.md) (1b) |
| Settings import | `PARAM.DAT` imported once at boot, then `PARAM.OK` or `PARAM.BAD` | `HW` | [2026-11-07 tests](../../analysis/hw-tests/2026-11-07-spare.md) (2), [runbook 2026-11-08 PM](../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md) (block 3) |
| SAVE TO CARD | Writes `PARAM.SAV` from the menu | `EMU` | [settingsio_menu](../../tools/koma/features/settingsio_menu.py) |
| USB tether | Shutter over USB while the USB session stays up | `HW` | [runbook 2026-11-08](../../analysis/hw-tests/RUNBOOK-2026-11-08.md) (Results) |
| USB live view (`-LVFR`) | Mode 0 grey 160x120, 1 JPEG 320x240, 2 on-screen-display layer, 3 JPEG 640x240 | `HW` (modes 0-2 22-29 fps depending on mode and body, mode 3 about 12.9 fps) | [runbook 2026-11-08](../../analysis/hw-tests/RUNBOOK-2026-11-08.md) |
| USB settings access | `-KSET` / `-KGET` live byte edit, `-KSTA` state snapshot | `HW` (EV round trip, `-KSTA`); `-PEEK` RAM read unit-tested only (`EMU`) | [ksettings](../../analysis/ksettings/README.md) |
| Second-session stall fix | Removes a 10-20 s delay arming remote control a second time | `HW` (10.41 s off, 0.66 s on) | [stall root cause](../../analysis/stall/ROOT-CAUSE.md), [runbook 2026-11-08](../../analysis/hw-tests/RUNBOOK-2026-11-08.md) |
| Wait-ring diagnostic | Temporary: reports long task waits through `-KSTA` | `HW` (answers) | [waitdiag](../../tools/koma/features/waitdiag.py) |
| Full-capacity FAT32 (widefs) | Mounts SDHC/SDXC cards at full size; needs the Koma firmware's SDHC driver | `HW` (64 GB, both bodies) | [widefs](../../analysis/sdhc/widefs/README.md), [2026-11-07 tests](../../analysis/hw-tests/2026-11-07-spare.md) |
| Card-root listing on FAT32 (`rootlist`) | `-NLST /EXT0` completes on FAT32 cards (stock sizes it by the FAT16 root count, 0 on FAT32) | `EMU` | [ROOT-NLST-64GB](../../analysis/sdhc/widefs/ROOT-NLST-64GB.md), [look-ia](../../analysis/look-ia/README.md) |

The user-facing description of each row is in the [user guide](../user-guide.md).

## Firmware

| Item | What it is | Status | Evidence |
|---|---|---|---|
| First Koma firmware image (pre-release) | Stock 2.40 + 344 B loader for card `GRMOD.BIN`, MakerNote tag `Rev024M` | `HW` (flashed on both bodies 2026-11-06; both later moved to the SDHC image) | [record](../../archive/STATUS.md), [module loader](../../analysis/module-loader/README.md) |
| Koma 1.0 firmware | The build 2b image below with the tag `240K1.0` (5 bytes differ). Built on your computer from Ricoh's 2.40 file by `koma make-firmware` or the app, which refuse any result but SHA-256 `ab52104c…649e` | `STATIC` (byte diff against build 2b); not yet flashed | [versions](../versioning.md), [loader.py](../../tools/koma/loader.py) |
| Koma firmware, SDHC image build 2b (pre-release) | First image + SDHC/SDXC driver + `/IROM/GRMOD.BIN` fallback, tag `240K2` (the release image stamps `240K1.0`) | `HW` (installed on both bodies) | [driver](../../analysis/sdhc/driver/README.md), [runbook 2026-11-08 PM](../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md) |
| SD 2.0 / SDHC / SDXC init | CMD8, ACMD41 with HCS, block addressing | `HW` (CMD8 probe; 64 GB cards on both bodies); other SDHC sizes `EMU` | [SDPROBE log](../../analysis/sdhc/probe/hw/SDPROBE-spare-2026-11-07.TXT), [driver](../../analysis/sdhc/driver/README.md) |
| Package load order | Card `GRMOD.BIN` first, then `/IROM/GRMOD.BIN` | `HW` | [2026-11-07 tests](../../analysis/hw-tests/2026-11-07-spare.md) |
| `/IROM` installer | Card module that copies a package into internal memory; the app's setup uses it when Koma goes "In the camera" | `HW` (both bodies, first run 2026-11-07; 58,092 B package on 2026-11-08). With the 61,784 B 1.0 package, and through the app: not yet run on a camera | [diag modules](../../analysis/sdhc/diag/README.md), [runbook 2026-11-08 PM](../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md) |
| Restore stock over the first image | Official `QRFLECT3` by the keyed update | `HW` (once, spare, 2026-11-07) | [record](../../archive/research/handoff.md) |
| Restore stock over the SDHC image | Same procedure | `EMU` (byte-exact install/restore cycle on an earlier SDHC build) | [driver](../../analysis/sdhc/driver/README.md) section 8.5 |
| Recovery from a flash that does not boot | | Unknown | [limitations](../limitations.md) |

## Host software

| Tool | What it does | Status | Evidence |
|---|---|---|---|
| `komabridge` | Local HTTP bridge to the camera over USB | `HW` (selftest 9/9 on both bodies) | [runbook 2026-11-08](../../analysis/hw-tests/RUNBOOK-2026-11-08.md), [bridge README](../../tools/komaset/bridge/README.md) |
| Big-card listing workaround (bridge 0.3.1) | Never lists the card root | `HW` | [runbook 2026-11-08 PM](../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md) (block 1) |
| Shot-settle wait (bridge) | No card commands until the camera is idle after a shot | Not run on a camera | [bridge README](../../tools/komaset/bridge/README.md) |
| Koma app (`koma`) | Settings, looks, photos, shooting, webcam, and the first-run setup that builds the firmware and installs the package | Not yet run on a camera as a whole; setup tested against a simulated camera only. Installed with `pipx install koma-gr` or Homebrew | [app](../../tools/komaapp), [install](../install.md) |
| Koma Setup page (pre-1.0, repository only) | Photos, Shoot, Remote, settings editing and copying in a browser; not in the installed app | `HW` for photo review, Shoot, Remote and settings writes; Looks live editing and phone mode (`komabridge --lan`) not run on a camera | [write audit](../../analysis/hw-tests/WEBAPP-WRITE-AUDIT.md), [runbook 2026-11-08 PM](../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md) |
| Settings mirror | Copy one body's settings to another through `PARAM.DAT` | `HW` (main to spare) | [runbook 2026-11-08 PM](../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md) (block 3) |
| `komaset` CLI | Settings block to text and back, diff, merge, check | `HW` (used for the mirror) | [komaset](../../tools/komaset/README.md) |
| `komacam` | Webcam: MJPEG stream, OBS route, virtual camera | `HW` (11-minute Zoom call through OBS); self-healing OBS page and the pyvirtualcam route to Zoom not run | [runbook 2026-11-08 PM](../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md) (block 4) |
| `grdusb` | Read-only USB client (`ver`, `info`, `ls`, `get`) | `HW` | [grdusb](../../tools/grdusb/README.md) |
| `tools/koma` | Builds and checks packages | Host only | [building](../building.md) |
| Emulator (`grdemu`, `grdui`) | Runs the 2.40 firmware under Unicorn | Host only | [how it works](../how-it-works.md#the-emulator) |

## Not in this tree

The ADJ guard and the low-battery debounce (`battguard`) are not in this tree or in Koma 1.0. One spare test ran the ADJ guard and it failed (`analysis/hw-tests/2026-11-07-spare.md`, test 3).
