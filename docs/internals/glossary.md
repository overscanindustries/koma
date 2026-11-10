# Glossary

Terms used in the internals chapters and the Koma tools. Each entry gives a definition, the chapter that treats it, and one tag (`HW`, `EMU`, `STATIC`, `INFERRED`, defined in [features](evidence-ledger.md)). Addresses are firmware 2.40 runtime addresses unless stated; the [address index](address-index.md) lists them all.

Chapters: [01](01-hardware-and-memory-map.md) hardware and memory map, [02](02-boot-chain-and-monitor.md) boot chain and Monitor, [03](03-firmware-image-and-update-format.md) image and update format, [04](04-application-architecture.md) application architecture, [05](05-settings-and-adjustment-store.md) settings and adjustment store, [06](06-image-pipeline-and-colour.md) image pipeline and colour, [07](07-usb-protocol.md) USB protocol, [08](08-card-scripts-and-service-terminal.md) card scripts and service terminal, [09](09-recovery-and-failure-modes.md) recovery and failure modes, [10](10-emulator-and-tooling.md) emulator and tooling, [11](11-modification-playbook.md) modification playbook, [12](12-version-history.md) version history, [13](13-display-live-view-and-usb-streaming.md) display, live view and USB streaming.

## Koma firmware and package

| Term | Definition | Ch. | Tag |
|---|---|---|---|
| Koma | Japanese コマ, a film frame; named after Ricoh's own firmware debug string `f_dcim_new_koma`. Koma adds features to the original GR DIGITAL on firmware 2.40 | — | `STATIC` |
| Koma firmware | Ricoh 2.40 plus the Koma loader (and, in generation 2, the SD driver changes), flashed with the stock keyed update. Rarely changed; features live in the package | 03, 11 | `HW` |
| Generation 1 (`Rev024M`) | First Koma firmware (pre-release build; `flash-1` in older records): official 2.40 plus a 344-byte card-module loader (`0x57f74..0x580cb`, entry `0x57f78`), its hook at `0x281ee`, and the MakerNote marker `Rev024M`. Reads `GRMOD.BIN` from the card only, so the card must be SDSC (2 GB or less) | 11 | `HW` |
| Generation 2 (`240K2`) | Second Koma firmware (pre-release build, currently build 2b): generation 1 plus SD 2.0/SDHC/SDXC initialisation and an `/IROM/GRMOD.BIN` fallback in the loader. A card `GRMOD.BIN` wins when the loader can reach it. MakerNote marker `240K2`. Both test bodies run build 2b. The release firmware stamps `240K1.0` (see VERSIONING in `archive/design`) | 01, 11 | `HW` |
| Loader | The routine in the Dcfs island that runs after startup: waits for the card probe, opens `/ATA1/GRMOD.BIN` (generation 2: else `/IROM/GRMOD.BIN`), validates it, allocates it and calls it. It has no write path | 11 | `HW` |
| R3 curve | The tone table (`sib-R3-18c3c4`) from the firmware of Ricoh's R3 compact camera, a sibling of the GR DIGITAL. MONO uses it unchanged and it is the first CURVE option | 06 | `STATIC` |
| `GRMOD.BIN` | The package file the loader reads: 20-byte header `GRMD`, format 1, body at most 64 KiB, checksum `rol(c,1) + word`. Built files are named `*.grmod` until copied | 11 | `HW` |
| Package, module | A `GRMOD.BIN` body: an installer, records and resident code that change the running camera in RAM only. Removing the file and rebooting undoes it | 11 | `HW` |
| `/IROM` package | A `GRMOD.BIN` copied into the camera's internal memory by the one-shot installer module `irominstall.grmod`. Needed with 64 GB cards, where the loader cannot reach a card file; a card file still wins when reachable | 11 | `HW` |
| Package version | Every build stamps `KOMA V<version> <build id>` (the build id is a hash, such as `747F061F`). Released packages follow the Koma X.Y.Z scheme (the first public release is Koma 1.0, package 1.0.0); earlier builds are known by build id only | 10 | `STATIC` |
| `koma04w` | The current package build: the `koma04` feature set plus `widefs`, build id `747F061F`, 58,092 B; installed in `/IROM` on both bodies (2026-11-08) | 11 | `HW` |
| Feature | A Python description of one change (`tools/koma/features/<name>.py`) that the linker combines with others into a package | 10, 11 | `STATIC` |
| `KMOD` | The boot header of a package body: offsets of records, inits, resident segment, relocations | 10 | `STATIC` |
| Manifest | The resident block starting `KOMA`: build id, status (1 after a good install), version string, feature table | 10 | `EMU` |
| Publish slot | `0x58190`/`0x58194`: `KOMA` and the manifest address, written after install so other code can find the package | 10 | `STATIC` |
| Service table | The 48-byte table (`0x5809c`) the loader passes to a package: ABI and image version, pool, file, key funnel, LUT upload, IRQ mask, sleep. Packages do not hard-code these firmware addresses | 11 | `STATIC` |
| Static resident | The package's resident segment linked at a fixed address (`0x3dae00`) in the unused low-RAM gap above `KernPool`, instead of a pool allocation | 01, 11 | `HW` |
| Record | One installer step: `CHECK`, `DATA` (Patch), `BL` (Hook or Redirect) or `PTR` (Pointer) | 11 | `STATIC` |
| Before-bytes | The exact bytes a record expects at its address. The installer verifies all of them before writing any; one mismatch means nothing is applied | 11 | `EMU` |
| Patch | A record that writes `after` over verified `before`; code or data | 11 | `STATIC` |
| Check | A record that verifies bytes a feature depends on and writes nothing | 11 | `STATIC` |
| Hook | A record that wraps a Thumb `bl target`; several features on one site chain in build order and the original runs once | 11 | `EMU` |
| Redirect | An exclusive `bl` rewrite at any 4 bytes; no chaining | 11 | `STATIC` |
| Pointer | A record that sets a 4-aligned word to the resident address of a label | 11 | `STATIC` |
| Resident | Package parts kept until power-off: feature code, tables, manifest | 10 | `HW` |
| Transient | Package parts freed after install: installer, records with their before/after bytes, relocations, init-only features | 10 | `EMU` |
| Lint | Link-time refusals: references to flash or store writers, records on code that runs before the loader hook, stock settings writes without stock-valid values | 10, 11 | `STATIC` |
| `koma selfcheck` | Static checks plus a unit-emulated install and hook run on the real 2.40 payload | 10 | `EMU` |
| `koma smoke` | One emulator boot with the package on a virtual card and one shot; PASS, FAIL or SKIP per feature | 10 | `EMU` |
| `SMOKE_STUBS` | Firmware calls a feature's init makes that the model cannot survive, stubbed only during `smoke` | 10 | `EMU` |
| Telemetry, `KOMALOG.TXT` | A transient feature that writes one boot log per power-on: CPU mode, ticks, pool headroom, resident address and canary, an SMC check. CP15 reads are off in camera builds | 10, 11 | `HW` |
| KOMA rows | Menu rows declared as data and stored one byte each in `0x1b8b5..0x1b8cf` | 05, 11 | `EMU` |
| KOMA FEATURES | Row byte `0x1b8bc`: FULL, or ESSENTIALS (only menu5, settingsio, settingsio-menu, telemetry, tether, liveview, stallfix install) | 05 | `EMU` |
| `Rev024M`, `240K2` | MakerNote tag 2 text written by generation 1 and generation 2 Koma firmware: the `Rev%4.4x` format string at `0x112578` is changed in the body, so the tag proves which body code runs. Stock 2.40 writes `Rev0240` | 06, 12 | `HW` |
| Recipe, look | A stored combination of tone curve, contrast, key, shadow, highlight, filter or colour settings, sharpness and grain/vignette, chosen in the KOMA RECIPE row. Kept in the `RCP6` store at `0x1b900`. Not an IMG SET value: a recipe sets IMG SET to a stock value | 05, 06 | `HW` |
| SELECT | Selective-colour recipe: mono except one hue band, applied as a YUV pass hooked at `0x117382` | 06 | `HW` |
| Develop passes | The two per-shot processing passes; Koma hooks pass 1 at `0x96436` and pass 2 at `0xafd3e`, after stock register writes | 06, 11 | `HW` |
| Colour probe | A package that writes one colour-register value per shot, keyed to a shot counter, to measure hardware effects in one power-on | 11 | `HW` |
| Full-press snap | A shutter pressed straight through focuses at the SNAP distance instead of running AF. Koma `fullpress` hooks the RL1 focus handler `0x956a0` | 11 | `EMU` |
| `widefs` | Package feature that mounts SDHC/SDXC FAT32 cards at full capacity; needs generation 2 Koma firmware. 64 GB cards work on both bodies | 01, 11 | `HW` |
| `PARAM.DAT` | Card file the `settingsio` feature imports once at boot: a 4,096-byte settings block, checked (magic, checksum, Koma settings version) and copied over `0x1b000` minus per-body bytes; then consumed, with `PARAM.OK` or `PARAM.BAD` written | 05 | `HW` |
| `PARAM.CUR` | The 4 KB settings block as it is in RAM, written to the card at every boot by `settingsio`. Its stored checksum can be stale; `komaset` recomputes it before reuse | 05 | `HW` |
| `PARAM.SAV` | A copy of the current block written on demand by the SAVE TO CARD row | 05 | `EMU` |
| `KSIO` | Settings-file sub-header at `0x1b8d0`: version, firmware tag of the body that wrote the block, last-import status | 05 | `HW` |
| `tether` | Package feature that makes USB remote shooting work: a 4-byte `bl` redirect at `0x4fe44` that skips the stock state-9 force while the host holds `-CCTL remtin` with key injection on, and a second redirect at the `remtout` call `0xcd090` | 07, 11 | `HW` |
| `-LVFR` | Koma USB command that returns one live-view frame per call (modes 0..3: grey 160 x 120, JPEG 320 x 240, OSD page 360 x 240, JPEG 640 x 240). It reuses the dead `-FDWL` record (ID `0x1a`) of the command table | 07, 13 | `HW` |
| `stallfix` | Package feature with runtime-switchable fixes for the second-session tether stall (the LCD driver mutex leaked by `0x23578`); flag byte `0x1b8be`. On hardware the arm time fell from 10.41 s (off) to 0.66 s (on) | 04, 05 | `HW` |
| `-KSET`, `-KGET`, `-KSTA`, `-PEEK` | Koma USB commands (`ksettings`): write or read settings bytes live, state snapshot, RAM peek | 07 | `HW` |

## Host tools

| Term | Definition | Ch. | Tag |
|---|---|---|---|
| `tools/koma` | Host-side builder: `koma build`, `list`, `inspect`, `selfcheck`, `smoke`, `make-loader` (rebuilds the generation 1 image from the user's own 2.40 file and refuses unless the hash matches) | 10, 11 | `STATIC` |
| `komaset` | CLI for the 4 KB settings block (`dump`, `apply`, `diff`, `merge`, checksum fix) driven by the byte map `tools/komaset/settings-map.json`. Used for the cross-body settings mirror | 05 | `HW` |
| `komabridge` | Local HTTP server (`127.0.0.1:8765`, version 0.3.2) over `grdusb`: settings via `PARAM.CUR`/`PARAM.DAT`, photo transfer, tether, live view, reboot. `--lan` serves a phone with a per-run token | 07 | `HW` |
| Koma Setup | The single-file web page (`tools/komaset/web/koma-setup.html`) served by `komabridge`: review photos, shoot, remote view, looks, copy and change settings, backup, card setup | 07 | `HW` |
| `komacam` | Webcam bridge: pulls `-LVFR` frames through `komabridge` and serves MJPEG on `127.0.0.1:8770`, optionally into a virtual camera (`--vcam`). Ran an 11-minute Zoom call through OBS on hardware | 13 | `HW` |
| `grdusb` | Minimal USB client for ORIGINAL mode (`ver`, `info`, `ls`, `get`, `stage-update`) with an allowlist of commands; `komabridge` uses it | 07, 10 | `HW` |
| `stage-update` | `grdusb` command that stages official `QRFLECT3` files on the camera's card over USB, deleting every other `QRFLECT*` first; the flash itself still needs the keyed power-on | 07 | `HW` |
| `grdemu` | The boot emulator (Unicorn-based) that runs the real firmware and the captured Monitor with explicit peripheral models; CPU1 replies are invented | 10 | `EMU` |
| `grdui` | Firmware GUI runner and browser playground (OSD 4 bpp, 360 x 240) that drives the real menus through the key sink `0x51740` | 10 | `EMU` |
| `grdlook` | Tone tools: extract, compare and edit tone curves | 06, 10 | `STATIC` |
| `grdscript`, `grdpatch`, `grd_verify` | `SCRIPT.MG1` lint/build/explain; test-image builder; image checker (a PASS covers declared gates in the emulator only) | 10 | `EMU` |
| Unicorn | The CPU emulation framework under `grdemu`; its ARM core is not a verified match for the camera's | 10 | — |
| `DO_NOT_FLASH_` | File-name prefix for deliberately hanging emulator test images with valid checksums. Never flash them | 10 | — |
| REA | Reverse Engineer Anything, a static-analysis toolkit, used on the firmware through an ELF wrapper of the big-endian payload | 10 | — |

## Hardware and processors

| Term | Definition | Ch. | Tag |
|---|---|---|---|
| Main processor, GR ENGINE | The ARMv4T big-endian image SoC that runs the application under Nucleus PLUS; exact part unknown | 01 | `STATIC` |
| CPU1 | A separate microcontroller for power, RTC, battery and temperature, hard keys, zoom and reboot, reached by an SPI/GPIO handshake at `0xca524`. No firmware available; every CPU1 reply in the emulator is invented | 01, 04 | `STATIC` |
| CPU2 | The version screen's label for the main application, read from main-info `+0x12`; not a second processor | 03, 12 | `STATIC` |
| Nucleus PLUS | The RTOS (version 1.11.17 in every ARM `*rflect3` image) | 04, 12 | `STATIC` |
| HISR | Nucleus high-level interrupt service routine: deferred interrupt work scheduled by an ISR | 04, 07 | `STATIC` |
| OneNAND | The flash chip (Samsung OneNAND256 family, matched to the driver; no marking read): 64 KB blocks, all locked at power-up | 01, 09 | `STATIC` |
| `F221h`, IOBE | OneNAND system-configuration register: `0x41e0` early, `0x40e0` after Monitor init; with IOBE clear the stock boot stalls at the first flash read in the model | 01, 09 | `EMU` |
| BootRAM | OneNAND's 1 KB RAM filled from block 0 at reset; whether the SoC boots through it is unknown | 01, 02 | `STATIC` |
| Replace table (REP) | Flash structure in blocks `1..0xc` mapping failed blocks to spares; 10 entries | 03, 09 | `EMU` |
| M66592 | The Renesas USB device controller family at `0x10200000`, identified by register map | 01, 07 | `INFERRED` |
| SD host | The card controller at `0x12044000` (TMIO family by inference). Stock 2.40 initialises SD 1.x/2.0 SDSC cards; generation 2 adds SDHC/SDXC | 01 | `STATIC` |
| CC1, CC2 | Firmware names for two image-pipeline register blocks; both receive tone-table uploads, and CC2 holds the colour stage (chroma gain, matrix, coring) | 06 | `STATIC` |
| LUT, tone table | A 4096-entry big-endian u16 table (values 0..1023) uploaded to CC1/CC2 | 06 | `STATIC` |
| KernPool | The `0x70000`-byte kernel pool at `0x36adec..0x3dadec`. Largest free block on the camera with a 29.8 KB package body loaded: 3.9 KB, which is why current packages use a static resident | 01, 11 | `HW` |
| SMC | Self-modifying code; coherent on the camera | 11 | `HW` |
| CP15 | ARM system-control coprocessor; `MRC p15` hangs the camera's core, and the firmware never uses it | 11 | `HW` |
| RTC reset | After a dead battery the clock reads 2005-01-01. How the RTC is backed up (a flat backup cell or otherwise) is not established | 01 | `HW` |

## Boot chain and Monitor

| Term | Definition | Ch. | Tag |
|---|---|---|---|
| Monitor | The bootloader (1.00, 2005-08-06) in block 0: loads the application, and has a card RAM loader, a flash updater and a UART console. No release ever shipped a Monitor image | 02, 09 | `HW` |
| Application, main image | The main firmware that runs after the Monitor, loaded at `0x1c000` | 02, 04 | `STATIC` |
| Main info | The 512-byte header at block `0x3c`, sector `0x7e`. Magic, updated marker, type 2/3 and tail `R_MG1_UPDATED!!!` make the image valid. Updates erase it first and write it last | 02, 03 | `EMU` |
| Card RAM loader | Monitor scanner `0x2394` and loader `0x2194` (validator `0x2138`): runs a `QRFLECT0..9` file from RAM without writing flash when main info is invalid | 02, 09 | `EMU` |
| Monitor updater | The Monitor flash updater (`0x50ec`, worker `0x4e54`): reinstalls main firmware without the application. Emulator only; the Monitor SD path has never run on hardware | 09 | `EMU` |
| GPIO60, GPIO50 | Monitor inputs that select boot branches; GPIO50 low with GPIO60 high is logged as `PB_LOW_DETECTED`. Board meaning unknown | 02, 09 | `EMU` |
| `MG1MODE`, `VERUP_INMONITOR` | A card file the Monitor reads when GPIO60 is high; this value selects the Monitor flash updater `0x50ec` | 02, 09 | `EMU` |
| MG1 | Ricoh's internal prefix in `SCRIPT.MG1`, `MG1MODE`, `R_MG1_REFLECTOR` and the settings magic `MG1H`; meaning not established | 02, 03 | `STATIC` |
| Monitor console | The Monitor's UART command line (38,400 baud; pins unknown); reached with no usable card | 02, 09 | `EMU` |
| Monitor handoff | Values passed to the application at jump: LR `0x3c4`, words `[0,0,0,0x1220000c]`; the pointer cell `0x080003c0` holds `0x1220000c` | 02 | `EMU` |
| `MainCtrl` | The application's control task (firmware name `tAplCtrl`, queue `qAPL`): runs startup `0x1e7ac`, the update check and the loader hook | 04 | `EMU` |
| `tOpeMain`, `tCameraMain`, `tFileTask`, `tScript`, `tDcx`, `UsbShutter` | Other firmware tasks: keys and UI; camera sequence; card and file system; script interpreter; USB command protocol; CA-1 decoder | 04 | `STATIC` |
| Update gate | The `0x1eb22` check in `MainCtrl`: held keys and dial, or invalid main info, enter the update path | 03, 09 | `STATIC` |
| Point of no return | The first destructive update step: `0x63c68` erasing main info; no cancel after Yes | 03, 09 | `STATIC` |
| Pre-gate exposure | Patched bytes executed before the update gate; 0 is the requirement, so a broken change cannot block the SD updater | 11 | `EMU` |
| Dcfs island | Dead file-system self-test code at `0x56f80..0x58800` (6,272 B, 16 functions, no outside reference); the Koma loader and the generation 2 driver code live in it | 01, 11 | `STATIC` |

## Firmware image and update format

| Term | Definition | Ch. | Tag |
|---|---|---|---|
| QRFLECT, reflector | Update containers `/ATA1/QRFLECT0..9`; magic `R_MG1_REFLECTOR` XOR `0x80`; header fields at `+0x10` type, `+0x12` version, `+0x14` bounds, `+0x20` load, `+0x24` length, `+0x2c` file size, `+0x30` checksum | 03 | `STATIC` |
| `qrflect3` | Ricoh's type-2 file: the main application image (2,201,960-byte payload in 2.40) | 03 | `STATIC` |
| `qrflect7` | Ricoh's type-5 file: a text script of adjustment-engine commands that rewrites the calibration store and commits block `0x3d`. Never edit or stage it for Koma | 03, 05 | `STATIC` |
| Type 1 / 2 / 3 / 5 / 6 / 7 | Header type values: Monitor window; main; main variant; adjustment text; extended data; RAM-only carrier (the stock scanner skips type 7, the Monitor loader accepts it) | 03 | `STATIC` |
| Extend data | Flash component type 6 (blocks `0x3e..0x52`), version 1.00 dated 2005-09-23: fonts and resources; no package rewrites it | 01, 12 | `HW` |
| Weighted checksum | `sum(byte * (2 + (load + i) % 6)) mod 2^32` over the payload | 03 | `STATIC` |
| Update screens | `0x18` prompt, `0x1c` checking, `0x19` updating, `0x1a` rejected or failed, `0x1d` battery, `0x1e` no files; there is no success screen | 03 | `STATIC` |
| Keyed update | Dial on Movie, hold UP + TELE + PLAY, release, RIGHT for Yes, MENU/OK; the default answer is No. Used to install Koma firmware and to restore official 2.40 | 03, 09 | `HW` |
| `QUICKVER.UP` | A card file that triggers a separate flasher with no visible validation; keep it off every card | 03, 09 | `STATIC` |
| No anti-downgrade | Every official header has zero version bounds, so the firmware accepts any official `qrflect3` in either direction. Only the PC updater compares versions | 12 | `STATIC` |
| `FirmUpdate.exe` | Ricoh's PC updater; the only place that compares versions ("Already updated") | 07, 12 | `STATIC` |
| Version screen | Power off, dial on Movie, hold DOWN + TELE, then PLAY: shows JC1, CPU1, CPU2, MON, ADJ, EXT, SH, ST for about 20 s (builder `0x5c95c`) | 03, 12 | `STATIC` |
| Update replay | The emulator run of firmware 1.08 executing the official 2.40 update on the captured Monitor, then 2.40's first boot | 10, 12 | `EMU` |
| Type-5 stall | In every emulated two-file update the `qrflect7` step stops after about 900 bytes of the script, so no model has run the store commit | 05, 10 | `EMU` |

## Settings and adjustment store

| Term | Definition | Ch. | Tag |
|---|---|---|---|
| Settings block | 4 KB at `0x1b000` (magic `MG1H`), saved by stock at power-off. Checksum: the sum of big-endian u16 words over `0x000..0xffb`, stored as a big-endian u32 at `0x1bffc` | 05 | `HW` |
| Marker byte, marker group | A settings byte the loader `0x3d4a4` sets to `0x55` ('U') after defaulting a group of newer settings; the firmware's only settings versioning | 05, 12 | `EMU` |
| Settings tail | Bytes no stock code references; Koma keeps its state at `0x1b800..0x1bffb` | 05, 11 | `STATIC` |
| My Settings | Two stored setting sets (save `0x2978c`, load `0x298f0`, defaults `0x296a8`) | 05 | `STATIC` |
| Adjustment store (ADJ) | Flash block `0x3d`, mirrored to RAM near `0x08010000`: factory calibration with an `0xAA` magic and a sum. A torn commit loads ROM defaults and cannot be repaired by rerunning `qrflect7` | 05, 09 | `STATIC` |
| `UEEF1`, `UEE00` | `qrflect7` commands: reload the store into RAM; commit it to block `0x3d` | 05 | `STATIC` |
| `UEE52`, `UEE51`, `UEE53` | Engine tokens: flash store to `ADJROM.DAT`; `ADJRAM.DAT` to RAM; `ADJROM.DAT` to RAM then flash commit | 05, 08 | `STATIC` |
| `+0x18ed` | The store byte 2.40's `qrflect7` changed that older code reads: an AF contrast-peak threshold, 92 → 85 (sometimes misreported as `+0x18ec`) | 05, 12 | `STATIC` |
| Calibration hang | `0x08010431 = 0xFF` makes `MainCtrl` spin before the update gate; no recovery known | 09 | `EMU` |

## Card files, scripts and the service engine

| Term | Definition | Ch. | Tag |
|---|---|---|---|
| `/ATA1`, `/IROM`, `/EXT0` | The firmware's SD card path; the internal file store in flash (blocks `0x54..0x1ff`); the SD volume as seen over USB | 01, 07, 08 | `STATIC` |
| `SCRIPT.MG1` | A startup script run by the built-in interpreter from the card or `/IROM`. It runs after a No, a timeout or a rejection, and on the first boot after a flash; keep it off update cards | 08 | `STATIC` |
| `CONFIG.TST`, `CONFIG.BAK` | The script interpreter's saved state file (`/ATA1` or `/IROM`): state 1 resumes at a stored offset, state 2 reruns each boot. `CONFIG.BAK` is never read | 08 | `STATIC` |
| `adjcom` | A script command that passes a 5-character token to the service engine `0x54164`; excluded from Koma | 08 | `STATIC` |
| Service engine | The adjustment command engine at `0x54164` (letters A..Z, arbitrary memory read/write); reached by `adjcom`, the service terminal and type-5 files | 08 | `STATIC` |
| Service terminal | Text console with command table `0x17781c` (`r`, `w`, `d`, `x`, `ds`, `im`, `io`, `raw`, `lk`, `t`, `i`) via dispatcher `0x62670`; no authentication | 08 | `STATIC` |
| `qADJUSTUSB` | Message queue of the service USB input path (`0x5d4b8` posts `0x6101`; consumer `0x542b8` -> task `0x54020`) | 08 | `STATIC` |
| `UE103`, `UE104` | Service-engine tokens for flash dump and restore; one byte apart | 08, 09 | `STATIC` |
| `IROM.DAT` | The dump routine's output: 516-byte records per flash sector; the captured one holds 13,056 records | 08, 09 | `HW` |
| `GAUSS_CADJ_FILE` | Header magic of factory adjustment programs `/ATA1/RADJ/NADJ0001.ADJ`; the loader `0x5443c` reads one into `0x13000` and calls it. Unsigned native code from the card | 08 | `STATIC` |
| `PICTDATA.RDD` | `/IROM/PICTDATA.RDD`, the 160 x 120 lens-shading gain map loaded by `0x4c8f0`; applied only in WB-bracket shots | 06 | `STATIC` |
| Key funnel | `0x517dc`: the path every key event takes, including injected and CA-1 keys; runs before the update gate | 04, 11 | `STATIC` |
| Key injection, `keyset` | Script and `-CCTL` commands post `{code, action}` to the sink `0x51740`; key codes come from the table at `0x18108c` (6 POWER, 7 PLAY, 11 UP, 12 DOWN, 13 RIGHT, 14 LEFT, 15 TELE, 16 WIDE, 26 RL1, 27 RL2) | 04, 08 | `STATIC` |
| RL1, RL2 | Key codes 26 and 27, the release switch half and full press | 04, 08 | `STATIC` |
| Counters screen | LEFT + TELE at power-on (`0x1f1e0`): SHOT, REBOOT, NEAR END and HALF counts for about 20 s; display only | 04, 07 | `STATIC` |

## USB

| Term | Definition | Ch. | Tag |
|---|---|---|---|
| ORIGINAL mode | USB mode 0 (VID/PID `05ca:2218`), the vendor-protocol mode Ricoh's updater uses; every Koma host tool needs it | 07 | `HW` |
| Command table | `0x1fb400`: 35 records of 36 B (name, ID, args flag, handler). Records with ID 0 answer 501 | 07 | `HW` |
| `-VER` | First command of a Ricoh session; replies `200 9.17 VV.VV`; arms a 3 s UI timer | 07 | `HW` |
| `-CCTL` | Remote-control command (`remtin`, `remtout`, `diaset`, `keyset`, `poff`). On stock firmware it answers but does not fire the shutter; Koma `tether` makes remote shots work | 07 | `HW` |
| State 9 | The application state forced while USB is attached; its handlers ignore RL1/RL2, which is why stock remote shooting fails | 07 | `STATIC` |
| MASS STR latch | RAM byte `0x1bd240`, set by DOWN + WIDE at power-on (dial on Movie, USB mode MASS STR), that makes the card writable to the host for one boot | 07 | `STATIC` |
| CA-1 | Ricoh's cable switch, which pulses USB VBUS; the stock `UsbShutter` decoder (`0x1319e8..0x131c78`) turns the pulses into RL1/RL2 in every version | 07 | `STATIC` |
| ATTACH lock-out | A VBUS high of 500 ms or more is taken as a PC cable and leaves the CA-1 decoder dead until power-off | 07 | `EMU` |

## Image pipeline and tone

| Term | Definition | Ch. | Tag |
|---|---|---|---|
| Selector | The argument to `0x8ffc8`; `0x1e..0x22` pick the five tone tables for contrast levels 1..5 (tables `0x193b30`, `0x195b30`, `0x19bb30`, `0x197b30`, `0x199b30`) | 06 | `STATIC` |
| c1..c5 | Shorthand for the five stock tone tables by contrast level | 06 | `STATIC` |
| Selector cache | Bytes `0x1f4db0`/`0x1f4db3` remembering the last uploaded table; a custom upload must invalidate them | 06, 11 | `EMU` |
| Slot `0xe` | A sixth tone-table slot (`0x19db30`) used only by factory paths | 06, 11 | `STATIC` |
| 256 → 64 pin | Every shipped Ricoh u16 tone table outputs 64 (or 65) at input 256 | 06, 11 | `STATIC` |
| Black-clip index | The table input at which output first leaves 0: 170 on all five GR tables | 06 | `STATIC` |
| B&W mode byte | `0x1b030` image setting (3 = B&W, 6 = B&W (TE)) | 05, 06 | `HW` |
| TE, SETTING1/2 | B&W with toning (2.40, in 2.30 SEPIA's slot); two custom setting banks | 06, 12 | `STATIC` |
| Colour matrix | Rows `0x12018104/108/10c` written every shot (zero in stock); live on the camera | 06, 11 | `HW` |
| Chroma gain | `0x12018004`; 0 gives a grey frame | 06, 11 | `HW` |
| IQ stage | Hue matrix, chroma clip and chroma offset (`0x12018028/2c/30`) with no caller in 2.40; no effect when written | 06, 11 | `HW` |
| Live-view ring | Three YCbCr frames of 640 x 244 (stride 672) at `0x8470000`, rotated at 30 fps; the source for `-LVFR` and peaking | 06, 13 | `STATIC` |
| MakerNote | Ricoh JPEG metadata (`Ricoh\0\0\0`), built at `0x112120`; tag 2 is the `Rev` string | 06, 12 | `HW` |
| MakerNote `0x1001` | 48-byte shot record; byte 40 is the IMG SET value | 06 | `STATIC` |
| Camera Info | MakerNote tag `0x2001`: 22 entries; tag 2 low 16 bits is the JPEG quantisation scale | 06, 12 | `STATIC` |
| Camera Info tag 5 | Package year-month written into the store by `qrflect7` (`2007-09` for 2.40) | 12 | `HW` |

## Version history and family

| Term | Definition | Ch. | Tag |
|---|---|---|---|
| Version ladder | The seven held payloads 1.08, 1.09, 2.01, 2.02, 2.10, 2.31, 2.40; 2.20, 2.21 and 2.30 are missing | 12 | `STATIC` |
| Relocation-equivalent | Two function bodies identical once branch offsets and pointer literals are masked; over 96% of functions per hop | 12 | `STATIC` |
| fnmap | The per-version map of every 2.40 function into an older image (identical, relocated, changed, missing) | 12 | `STATIC` |
| Silent fix | A change Ricoh's notes do not mention, such as the 1.09 boot-sync flag | 12 | `STATIC` |
| Boot-sync flag | `0x1bd3a8`: set when the file task is ready; 1.09 and later wait for it before the update scan, and the loader waits for it too | 11, 12 | `STATIC` |
| SEPIA | 2.30 image setting (IMG SET value 6) that switched on a dormant fixed-tint mode; replaced by B&W (TE) in 2.40 | 12 | `STATIC` |
| CL-BKT | 2.30 colour bracket: one exposure developed three times by the WB-BKT loop | 12 | `STATIC` |
| INFO DISP, FLASH EXPO. COMP., GT-1 | 2.40 items: information display mode (`0x1b1a6`), flash exposure compensation (`0x1b1a5`), and GT-1 tele conversion lens support (`0x719bc`) | 05, 12 | `STATIC` |
| `*rflect3`, `*launch3` | Ricoh's ARM big-endian (GR DIGITAL, R3, 500G wide, GX8, R2) and Renesas M32R (R5 onward, GR DIGITAL II/III) firmware generations | 12 | `STATIC` |
| 500G wide, R3, GX8 | The GR's closest firmware relatives: 44%, 42% and 26% payload overlap; the 500G wide carries all five GR tone tables | 06, 12 | `STATIC` |
| Feature donor | A sibling model that could supply a feature as code (same ARM platform) or only as an idea (M32R models) | 12 | `INFERRED` |
| `PARAM.DAT` (Ricoh) | In the GX8 and R2, two small helpers of the `U`-group service handler save and load the 4 KB settings block to `/ATA1/PARAM.DAT`. GR 2.40 has no `PARAM.DAT` string. Its own `U`-group handler `0xd095c` is about 5.4 KB; an earlier "84-instruction stub" reading was a Ghidra artifact of an unresolved jump table. Koma's `PARAM.DAT` feature is new code modelled on the GX8 behaviour | 05, 12 | `STATIC` |

## Evidence and terms of art

| Term | Definition | Ch. | Tag |
|---|---|---|---|
| `HW`, `EMU`, `STATIC`, `INFERRED` | The four evidence tags, defined in [features](evidence-ledger.md) | — | — |
| Bounded negative | A search that found nothing within a stated boundary; not proof of absence | — | — |
| Model, supplied | Emulator terms: behaviour written for the model, or an input chosen for a run. A modelled result is not a hardware result | 10 | — |
| Fixture | A test image or routine used only in the emulator | 10 | — |
| vt (virtual time) | Instruction-driven emulator time; not a duration | 10 | — |
| The capture | The main camera's partial flash dump (13,056 records), including Monitor 1.00; kept private | 02, 09 | `HW` |
| Spare, main | The two GR DIGITAL bodies: the spare (broken LCD, tested first, treated as expendable) and the main camera. As of 2026-11-08 both run generation 2 Koma firmware, build 2b with `koma04w` in `/IROM` | 11, 12 | `HW` |
