# 05. Settings and the adjustment store

The GR DIGITAL keeps two unrelated kinds of stored state. User settings live in a 4 KB RAM block at `0x1b000`, guarded by a magic and a 32-bit checksum, loaded at startup and saved at power-off. Koma keeps its own settings, recipe store and settings-file header in an unused part of that block, and adds a card import (`PARAM.DAT`) and export (`PARAM.CUR`) of the whole block, proven by a cross-body mirror on hardware. The factory adjustment store (calibration and register values) lives in flash block `0x3d`, is loaded into SDRAM at `0x08010000`, and is guarded by a magic byte and its own sum; Ricoh's `qrflect7` rewrites part of it during an official update.

Tags: `HW`, `EMU`, `STATIC`, `INFERRED`, as defined in [features](evidence-ledger.md) and the [index](README.md). Related: [03 firmware image and update format](03-firmware-image-and-update-format.md), [04 application architecture](04-application-architecture.md), [06 image pipeline and color](06-image-pipeline-and-color.md), [07 USB protocol](07-usb-protocol.md), [08 card scripts and service terminal](08-card-scripts-and-service-terminal.md), [09 recovery and failure modes](09-recovery-and-failure-modes.md), [11 modification playbook](11-modification-playbook.md), [12 version history](12-version-history.md).

Addresses are firmware 2.40 runtime addresses. `0x0801xxxx` addresses are the SDRAM copy of the adjustment store; the store offset is the low 16 bits (`0x080113f0` is offset `0x13f0`). "ADJ" means the adjustment store unless it says "ADJ button". Two checksums appear in this chapter and must not be confused: the **settings checksum** (block `0x1b000`, u32 at `0x1bffc`) and the **store sum** (block `0x3d`, word at `0x1ffc`). Unit-specific values (camera ID, MakerNote tag `0x5`, Camera Info tags 4, `0xc`, `0x14`, either checksum value) are deliberately not given.

## Key addresses

| Address | Meaning | Tag |
|---|---|---|
| `0x1b000..0x1bfff` | User settings block (4 KB). Loaded by `0x4947c(0x1b000, 0x1000)` in startup `0x1e7ac`; saved by `0x4949c(0x1b000, 0x1000)` in power-off `0x32b8c` | `STATIC` |
| `0x1b000` | Magic `MG1H` (`0x4d473148`) | `HW` |
| `0x3d9b8` | Settings checksum: sum of big-endian u16 words over `0x000..0xffb` into a 32-bit accumulator | `HW` (main body block matches) |
| `0x1bffc..0x1bfff` | Settings checksum, big-endian u32, stored by `0x4949c` | `HW` |
| `0x3d4a4` | Settings load, validation, full defaults and marker migration; compares magic and the full checksum word; contrast outside 1..5 becomes 3 | `STATIC` |
| `0x6dc18..0x6fa90` | Per-setting validators (accepted value sets and fallbacks) | `STATIC` |
| `0x5b4e4` / `0x5a260` / `0x6c4bc` | Menu commit: still page / P-A-M pages / SETUP (cursor to stored value tables) | `STATIC` |
| `0x6ef88` / `0x3e754` | RESTORE DEFAULTS / service preset | `STATIC` |
| `0x2978c` / `0x298f0` / `0x296a8` | My Settings save / load / defaults | `STATIC` |
| `0x1b030` | IMG SET | `HW main 2.40` (3 = B&W) |
| `0x1b2f8..0x1b327` | Error log, writer `0x38610` | `STATIC` |
| `0x1b338..0x1b7ff` | Free tail (no code reference, `0xff` on the main body) | `HW` |
| `0x1b8b0..0x1b8cf` | Koma settings header and rows (magic `KOMA`) | `HW` (block carried through the mirror) |
| `0x1b8d0..0x1b8ff` | Koma settings-file sub-header `KSIO` and reserve | `HW` |
| `0x1b900..0x1bbff` | Koma recipe store `RCP6` (FX table at `0x1ba00`) | `EMU` |
| flash block `0x3d` | Factory adjustment store (64 KB block, `0x2000` bytes programmed) | `STATIC` |
| `0x08010000..0x08011fff` | Store image in SDRAM; store sum word at `0x08011ffc` | `STATIC` |
| `0x175380` | Compiled default store image in ROM | `STATIC` |
| `0x176770` | Default tone-selection bytes (`0x175380 + 0x13f0`) | `STATIC` |
| `0x279ec` / `0x63e30` | Load block `0x3d` into `0x08010000` | `STATIC` |
| `0x27bc0` / `0x27bdc` | Copy ROM defaults to RAM / boot load with default fallback | `STATIC` |
| `0x27c28` / `0x27c5c` / `0x63d9e` | `UEEF1` reload / `UEE00` check-and-commit / erase and program block `0x3d` | `STATIC` |
| `0xd0e86` | `UEEF1` guard (store `0xcc` vs RAM `0xae`) | `STATIC` |
| `0xd6a62` | Type-5 (`qrflect7`) driver; brackets the file with open `0xd6888(1)` and close `0xd68cc(0)` | `STATIC` |
| `0x54164` | Adjustment command engine (`X`, `V`, `W`, `U`, …) | `STATIC` |
| `0x112120` | MakerNote builder (`0x1001` record, Camera Info) | `STATIC` |
| `0x193a80` | BCD table that renders store bytes `0x0d..0x0f` as Camera Info tag 5 | `STATIC` |

## Layers of stored state

| Layer | Holds | Written when | Survives power-off | Survives an official update | Tag |
|---|---|---|---|---|---|
| RAM only | Injected keys and virtual dial, INTERVAL, CONT. MODE, AUTO BRACKET, SELF-TIMER, MACRO (`0x1bef78`), IMAGE WITH SOUND | n/a | No | n/a | `STATIC` |
| User settings `0x1b000` | Shooting and SETUP settings, ADJ-button assignments, My Settings, rate-control bias, counters, error log, Koma area | Power-off (`0x32b8c`, up to twice); end of load | Yes | Mostly; see [Settings markers](#settings-markers-and-migration) | `STATIC`; persistence `HW` |
| Main-info header (block `0x3c`) | Firmware version (`+0x12`), validity magic, type | Last step of every update | Yes | Replaced | `STATIC` |
| Adjustment store (block `0x3d`) | Calibration, register init values, package date, unit date, identity-like bytes, tone-selection words | `qrflect7` commit (`UEE00`) or a service command | Yes | `qrflect3`: untouched. `qrflect7`: named words overwritten, others kept | `STATIC` |
| `/IROM` files | `SCRIPT.MG1`, `CONFIG.TST`, `CONFIG.BAK`; on generation 2 also `GRMOD.BIN` | Script `install`, `end`, `poff`; Koma installer module | Yes | Yes (`/IROM` is blocks `0x54..0x1ff`) | `STATIC` |
| CPU1 RTC | Clock | n/a | About a week without the main battery (Ricoh guide) | n/a | `INFERRED` |

The spare body's clock reads 2005-01-01 at every power-on (`HW spare`); a flat backup cell is the likely cause (`INFERRED`).

## User settings block

### Load, save and checksum

Startup `0x1e7ac` loads the block; `0x3d4a4` checks magic `MG1H` and the checksum and resets every setting to defaults when either fails (`STATIC`). Both 2.31 and 2.40 save the whole 4 KB at the end of load. Power-off body `0x32b8c(1)` saves it once after storing the display mode at `0x1b09c`, and again on the normal branch; the first save is skipped when a mode mask (`*(0x32df4 + 0x2c)`) equals one of two excluded values (`STATIC`).

The checksum routine `0x3d9b8` reads big-endian u16 words (`ldrh` on the big-endian core) over `0x000..0xffb` into a 32-bit register, and `0x4949c` stores the result with a 32-bit `str` at `0xffc`. So the checksum is a **big-endian u32 at `0x1bffc..0x1bfff`**; `0x3d4a4` compares the full word. All multi-byte fields in the block are big-endian. Checked on the main body's real block, rebuilt from its internal-memory capture (`IROM.DAT`, 516-byte records; the block sits at record `0x2a80`, 8 × 512 bytes): magic and checksum verify and every enum or range field holds a legal value (`HW main 2.40`). Earlier analyses called this a u16 sum; it is a u32 of u16 words.

A block written into RAM and then saved by a normal power-off persists: the cross-body mirror imported a block and the next boot's export held the imported values (`HW 2026-11-08 spare`; [Settings import and export](#settings-import-and-export)). The physical flash location of the save is not traced: two emulator harnesses show the startup commit in different `/IROM` blocks (`0x54`/`0x55` in one, `0x55`/`0x66` in the other) (`EMU`).

### Field map

Complete byte map: `tools/komaset/settings-map.json` (298 fields, 878 of 4,096 bytes; every offset 2.40 code references is in a field). Method in `analysis/settings-map/README.md`. Stored values often differ from menu cursor order; the commit tables give the mapping. Main-body values come from the real block (`HW main 2.40`).

| Address | Setting | Values | Tag |
|---|---|---|---|
| `0x1b030` | IMG SET | 0 HARD, 1 NORMAL, 2 SOFT, 3 B&W, 6 B&W(TE), 4 SETTING1, 5 SETTING2; menu order 0, 1, 2, 3, 6, 4, 5 | `HW` (main = 3) |
| `0x1b031`, `0x1b034` | Contrast, SETTING1 / SETTING2 | 1..5, default 3 | `STATIC` |
| `0x1b032`, `0x1b035` | Sharpness, SETTING1 / SETTING2 | 1..5 | `STATIC` |
| `0x1b197`, `0x1b198` | Contrast / sharpness, B&W | 1..5, default 3; EXIF reduces sharpness to 3 levels | `STATIC` |
| `0x1b1a7..0x1b1aa` | B&W(TE) contrast, sharpness, tone (1..6), density (1..5) | see [06](06-image-pipeline-and-color.md#mono-and-toning) | `STATIC` |
| `0x1b050` | PHOTOMETRY | 2 MULTI, 0 CENTER, 1 SPOT; invalid resets to 2 (`0x6e040`) | `STATIC` |
| `0x1b052` | CONT. MODE | reset to 0 at every boot | `STATIC` |
| `0x1b053` | EXPOSURE COMP. | odd 1..25 = -2.0..+2.0 EV in 1/3 steps, default 13 (`0x6dc24`) | `HW` (main = 13) |
| `0x1b054` | WHITE BALANCE | 0 AUTO, 1 OUTDOORS, 2 CLOUDY, 3 INCAND., 4 FLUORES., 5 MANUAL, 7 DETAIL (`0x6dca0`) | `HW` (main = 1) |
| `0x1b0a6` | WB DETAIL step | 0..16, default 5; index into per-body gain tables `0x080103a0`/`0x080103c4` | `STATIC` |
| `0x1b074`, `0x1b076` | Manual WB R, B gains | clamped `0x10..0x310` | `STATIC` |
| `0x1b055` | DATE IMPRINT | 0 OFF, 1 DATE, 2 TIME | `STATIC` |
| `0x1b056` | LANGUAGE | 9 languages; default from store byte `0x08010017` | `EMU` |
| `0x1b058` | FOCUS | 0 MULTI AF, 6 SPOT AF, 1 MF, 2 SNAP, 3 INFINITY (commit table `0x1bd40b`, validator `0x6decc`) | `STATIC` (main = 6, SPOT AF) |
| `0x1b059` | ISO | 0 AUTO, 1 ISO 64, 2 ISO 100, 4 ISO 200, 6 ISO 400, 7 ISO 800, 8 ISO 1600 (commit table `0x1bd41f`, validator `0x6dfbc`); RAW caps 8 to 7 | `HW` (main = 0; 1.08 frames 7, 8) |
| `0x1b05c` | AUTO BRACKET | 0 OFF, 3 ON 0.3, 1 ON 0.5, 2 WB-BKT, 4 CL-BKT; `0x6e164` refuses WB-BKT in B&W/TE; zeroed at every boot | `STATIC` |
| `0x1b060` | SHARPNESS (1.x) | 0 SHARP, 1 NORMAL, 2 SOFT; on no 2.40 menu page but still passed to capture | `STATIC` |
| `0x1b068`, `0x1b069`, `0x1b0fc` | PIC QUALITY / SIZE (one row writes all three: quality, size, 3:2) | | `STATIC`; quality change `EMU` |
| `0x1b06f` | FLASH | validator `0x6e3d8` accepts 0..3 and 17; names `INFERRED` (OFF, AUTO, RED-EYE, ON, SLOW SYNC); default from store `0x08010018` | `STATIC` |
| `0x1b047`, `0x1b048` | Per-scene flash for scene ids 5 and 8 | as `0x1b06f` | `STATIC` |
| `0x1b09c` | DISP mode | 0..4, five-step cycle; persists | `EMU` |
| `0x1b0a2` | Dirty-shutdown flag | `0x3d4a4` sets `0xffff` at boot; the clean save in `0x32b8c` clears it, so a hang leaves `0xffff` and the next boot sets `0x1a90c6`; readers `0x3b630`, `0x3cf28`, `0x11f634` pick calibration values | `STATIC`, `EMU` |
| `0x1b0d7` | USB mode (ORIGINAL / MASS STR); forced 0 when store `0x08010017` is `0xff` | see [07](07-usb-protocol.md#identity-and-descriptors) | `STATIC` |
| `0x1b0f4..0x1b0fb` | JPEG rate-control learning; bias bytes `0x1b0f6..0x1b0fb` in `0x3d..0x43` | 75 of 134 main-body shots read `0x43` | `STATIC`, `HW` |
| `0x1b010..0x1b01d`, `0x1b028..0x1b02f` | Battery tables, shot / half-press / near-end / reboot counters, release and second counters | body history | `STATIC` |
| `0x1b18c`, `0x1b18d`, `0x1b18e` | DIAL DIRECTION, ADJ SHTR, SYNCHRO MONITOR | | `STATIC` |
| `0x1b0c4`, `0x1b05d`, `0x1b191`, `0x1b192` | ADJ-button slots 1..4 | ADJ item codes: OFF 0, QUALITY 1, FOCUS 2, IMG SET 3, METERING 4, ISO 5, AUTO BKT 6, WITH SND 7, CONT 9, EXPO 10, WB 11, FLA COMP 13 | `STATIC` |
| `0x1b193` | RAW/JPEG quality | | `STATIC` |
| `0x1b1a5` | FLASH EXPO. COMP. | 3..15 = -2.0..+2.0 EV in 1/3 steps, default 9 (still row 13, table `0x1bd471`, validator `0x6fa90`, ADJ display `0x80288`) | `STATIC` |
| `0x1b1a6` | INFO DISP | 0 ON, 1 OFF | `STATIC` |
| `0x1b2f8..0x1b327` | Error log: error codes and three time stamps; shown by `0x1f1b8` (RIGHT+WIDE screen); cleared by `0x3d4a4` | | `STATIC` |
| `0x1b328..0x1b337` | Older firmware's longer log tail; two BCD time stamps on the main body, no 2.40 reference | | `HW` |

Further rows (LCD BRIGHTNESS, BEEP, VOL, LCD CONFIRM, AUTO POWER OFF, VIDEO OUT, COLOR SPACE, AF AUX, DIGITAL ZOOM, ZOOM BTN, START SETTINGS, CL-BKT B&W(TE), scene, movie and text-mode bytes, My Settings slots, PictBridge print preferences) are in the JSON. Unresolved there: flash value names, scene ids 8, 9, 11, 12, DISP step names, `0x1b080..0x1b099` (a 26-byte record copied from RAM `0x2d2a90` at reset), `0x1b09d`, `0x1b0a9`, and whether level 1 or 5 of contrast and sharpness is the stronger (`INFERRED`: the main body's B&W sharpness 4 with EXIF "hard" supports 5 = strongest).

The 2.40 data come from one body with one settings configuration (B&W, F3264, AUTO ISO, WB OUTDOORS, SPOT AF, EV index 13, CENTER metering), so value meanings rest on the commit tables and validators, not on varied hardware values.

### Persistence across power-off

Kept (Ricoh guide, Appendix D): PIC QUALITY, FOCUS, PHOTOMETRY, IMG SET, DATE IMPRINT, EXPOSURE COMP., WB, ISO, FLASH, LCD DISPLAY, SCENE MODE, ADJ MODE and every SETUP item. Reset: CONT. MODE, AUTO BRACKET, INTERVAL, IMAGE WITH SOUND, SELF-TIMER, MACRO. RESTORE DEFAULTS resets shooting-menu items only; My Settings still apply at power-on when START SETTING names them (`INFERRED` from the guide). Macro persists only through My Settings (`0x1b146`/`0x1b147`, `0x1b0d5`). Ordinary shooting also changes the block, because it holds the rate-control learning (`STATIC`). Boot combination UP + WIDE (`0x66bb8`) has a path that resets settings and reboots (`STATIC`).

Settings-block fuzzing with a valid checksum (`0xff` at each offset `0x4..0x330`, whole-block fills, random fills; 832 cases) always reached the update prompt; offsets above `0x330` were not swept (`EMU`).

### Settings markers and migration

Each release initialises groups of bytes when a marker byte is not yet `0x55` ('U'). Firing depends only on marker bytes, so the dating holds for user-modified blocks (`EMU`: each held version's own loader wrote a full-defaults block, then 2.40 cold-booted on it).

| Block written by | Markers its own loader sets | Groups that fire under 2.40 |
|---|---|---|
| 1.08 (`0x3d37c`), 1.09 (`0x3d39c`) | `0x1b038`, `0x1b0da` | `0x1b18f`, `0x1b1a1`, `0x1b1a2`, `0x1b1a3` |
| 2.01, 2.02 | + `0x1b18f` (`0x1b18c..e` = `00 01 00`, `0x1b190` = `aa`) | `0x1b1a1`, `0x1b1a2`, `0x1b1a3` |
| 2.10 | same markers; a full reset also sets `0x1b191` = 05, `0x1b192` = 00 | as 2.01 |
| 2.31 (`0x3d3f0`) | + `0x1b1a1`, `0x1b1a2`; `0x1b193` = 00 | `0x1b1a3` only |

Dating, bounded to the held versions (no 2.20 or 2.30 image): `0x1b038`/`0x1b0da` 1.08 or earlier; `0x1b18f` 2.01; `0x1b193` added in 2.20 or 2.30 inside the 2.01 group (`INFERRED` 2.20 from the changelog); `0x1b1a1`, `0x1b1a2` after 2.10 and by 2.31; `0x1b1a3` 2.40.

Bytes 2.40 writes on a 1.08 block (writer PCs in 2.40; `EMU`):

| Group | Writer | Bytes |
|---|---|---|
| `0x1b18f` | `0x3d752..0x3d764` | `0x1b18c` = 00, `0x1b18d` = 01, `0x1b18e` = 00, `0x1b193` = 00 |
| `0x1b1a1` | `0x3d770..0x3d780` | `0x1b194` = 00, `0x1b195..0x1b198` = 03 |
| `0x1b1a2` | `0x3d78a`, `0x3d7c8..0x3d7d6` | My Settings copies `0x1b199..0x1b1a0` = 03 |
| `0x1b1a3` | `0x3d7e0..0x3d804` | `0x1b1a5` = 09, `0x1b1a6` = 01, `0x1b1a7..aa` = `03 03 06 03`, `0x1b1ab..b2` = `03 03 03 03 06 06 03 03` |
| none (every boot) | `0x3d84e..0x3d986` | `0x1b05e`, `0x1b05c`, `0x1b06a`, `0x1b052`, `0x1b0ce`, `0x1b07a` = 0; `0x1b0a2` = `0xffff` |

36 bytes change in `0x1b18c..0x1b1b2` plus the checksum; a second boot changes nothing (`EMU`).

Consequences:
- Ricoh's V2.10 note: after that update ADJ settings 1 and 2 carry over, 3 and 4 read OFF, and exposure compensation and white balance assignments must be set again. In the model ADJ slots 3/4 (`0x1b191`/`0x1b192`) stay `0xff` after 1.08 → 2.40, which matches (`EMU`).
- A block saved by 2.10 reaches 2.40 with `0x1b193` = `0xff`, because its group marker is already set (`EMU`). A 1.08 → 2.40 update gets all four groups and `0x1b193` = 00.
- Markers are excluded from settings merges between bodies: a marker that is not `0x55` triggers migration defaults on the target.

### Round trip 2.40 → 2.31 → 2.40

Both versions use the same magic and checksum routine, so a 2.40 block passes 2.31's check and is kept. 2.31 runs only markers the block already has, never touches `0x1b1a3..0x1b1b2` and saves it back verbatim; 2.40 on return runs no migration. Under 2.31, IMG SET 6 means SEPIA, and the 0x30-byte error log sits at `0x1b300` instead of `0x1b2f8` (the only relocated base in the block), so each version reads the other's log out of phase (`STATIC`). Values only 2.40 has (INFO DISP, TE, 2.40-only ADJ and FLA COMP enums) are clamped by 2.31's validators where they share a byte (`INFERRED`). A downgrade has not been run.

## MakerNote `0x1001` read-back

The 48-byte record in MakerNote tag `0x1001` is built by `0x112120` from an input struct at `shot+0x178` (filled by `0x93222`, `0x4171a` AE snapshot, `0x9d980` WB, `0x10f984` rate control; record written at `0x11294a`). It mirrors several settings, so JPEGs can be read back for them (`STATIC`; values `HW main 2.40`, 136 frames, and `HW spare 1.08`, 18 frames).

| Byte | Source | Setting | Values seen | Tag |
|---|---|---|---|---|
| 0-3, 4-5, 6-12 | `shot+0x110/0x112`, `0x114`, `0x224` | width/height, nominal bpp ×100 (280 = FINE 3264), BCD capture time | | `HW main 2.40` |
| 24 | flag from `0x1b055` | 2 when DATE IMPRINT is set, else 0 | 0 on 2.40; 2 in four 1.08 frames | `STATIC`; meaning `INFERRED` |
| 32 | `0x6e3d8` | FLASH setting, not whether it fired | 0..3 on 2.40; 0, 1, 17 on 1.08 | `HW` |
| 33 | `0x6e2c0`, `0x1bef78` | Macro (disables focus values 2, 3 in `0x6decc`) | 0 | `INFERRED` |
| 34 | `0x6e090`, `0x1b060` | SHARPNESS (1.x) | 1 (NORMAL) | `STATIC` |
| 35 | `0x6decc`, `0x1b058` | FOCUS | 6 on both bodies' later frames; 0 on early 1.08 frames | `HW` |
| 36 | `0x6e040`, `0x1b050` | PHOTOMETRY | 0 on 2.40 (EXIF 2); 2 on 1.08 (EXIF 5) | `HW` |
| 37 | `0x6dc24` at `0x94e1e`, `0x1b053` | EXPOSURE COMP. index | 13 | `HW` |
| 38 | `0x6dca0`, `0x1b054` | WHITE BALANCE | 1 on 2.40; 0 or 1 on 1.08 | `HW` |
| 39 | `0x6dfbc`, `0x1b059` | ISO | 0 on 2.40; 7, 8 or 0 on 1.08 | `HW` |
| 40 | `0x6f768`, `0x1b030` | IMG SET | 3 on all 136 2.40 frames; 1 on all 18 1.08 frames | `HW` |

Contrast is recorded nowhere: no traced path copies `0x1b197`, `0x1b031`, `0x1b034` or `0x1b1a7` into the note or EXIF, and EXIF `0xa408` is never written (`STATIC`; bounded to the direct sources of `0x93222`, `0x4171a`, `0x9d980`, `0x11294a`). EXIF sharpness `0xa40a` is a 3-level reduction via `0x91268` (136 of 136 frames read 2, hard). EXIF SceneCaptureType `0xa406` mirrors `0x23b085` and reads 0 on every frame. Camera Info tag 2 carries the rate-control scale. Whether the flash fired is in Camera Info tag 6 (low bits = 2 exactly when it fired; `HW main 2.40`, 134 of 134). The 1.08 and 2.40 JPEG layouts are identical (segment order, tags, record, Camera Info set), so these bytes read the same way on 1.08 frames; MakerNote tag `0x1003` reads 1 on all 1.08 frames and 0 on all 2.40 frames (meaning unknown) (`HW`).

## Settings import and export

Koma's `settingsio` feature (transient: runs once per boot from the installer, after the loader hook `0x281ee`, the stock load of the block and the Koma core settings init) moves the whole 4 KB block to and from the card. Stock 2.40 has no settings-file code of its own: it holds no `PARAM.DAT` string, and the `U`-group handler `0xd095c` that was once read as an 84-instruction `PARAM.DAT` stub is a ~5.4 KB handler whose `tbh` jump table Ghidra did not resolve (`STATIC`). The file format and helpers follow the GX8/R2 sibling `PARAM.DAT` save; the GR code is new.

| File (card root) | Written by | Content | Tag |
|---|---|---|---|
| `PARAM.CUR` | Every boot, after the import step | The 4,096 bytes at `0x1b000` as they are in RAM | `HW both` |
| `PARAM.DAT` | The host (`tools/komaset`) | A 4,096-byte block to import once at the next boot | `HW 2026-11-08 spare` |
| `PARAM.OK` / `PARAM.BAD` | After an import attempt | `OK` or one reason line (`BAD MAGIC`, `BAD CHECKSUM`, `BAD KOMA VERSION`, `BAD REOPEN`, `BAD READ`) | `HW` (`PARAM.OK`); reasons `EMU` |
| `PARAM.SAV` | KOMA tab > OTHER > SAVE TO CARD = YES | The current block with its checksum recomputed | `EMU` (row not used on a camera; the installed package carries the `PARAM.SAV` path, `STATIC`) |

**Import.** If `/ATA1/PARAM.DAT` opens and is exactly 4,096 bytes, a first pass checks magic `MG1H`, the checksum (u32 sum of big-endian u16 words over `0x000..0xffb` against `0xffc`), and, when `KOMA` is at `0x8b0`, that the Koma settings version at `0x8b4` is 1. A different firmware tag in the file's `KSIO` header is recorded, not refused (status 3). A second pass copies the file over `0x1b000..0x1bffb`, skipping the map's `exclude_on_merge` ranges (magic, checksum, markers, body counters, calibration-derived defaults, manual WB gains, focus positions, rate-control learning, error log, Koma headers) plus the SAVE TO CARD byte and the `KSIO` sub-header. A file without a `KOMA` header copies only `0x000..0x7ff`, so a stock body's block cannot wipe the Koma area. After the copy every Koma row byte (`0x1b8b5..0x1b8cf`) at or above its option count is reset to its default. Reads go through 64-byte chunks on the init's stack; no 4 KB buffer, no pool allocation (`STATIC`; unit-emulated `EMU`).

**One-shot.** The file is then rewritten as the 4 bytes `DONE` and removed with `0x55d80`; either is enough to stop a re-import (a 4-byte or `DONE`-prefixed file is ignored). If create fails (write-protected card) the same file is imported at every boot.

**Export.** `PARAM.CUR` is written with the checksum bytes as they are in RAM, not recomputed. Koma's own boot writes (the `KSIO` header, feature inits) and stock startup writes land after the last checksum update, so the stored checksum of a `PARAM.CUR` is normally stale; this is by design (`HW`: the selftest flags it on every body). A `PARAM.CUR` must go through `komaset` (`merge` or `fix-checksum`) before it can be used as `PARAM.DAT`. The checksum becomes consistent again in the block the next stock power-off save writes.

**Persistence** is the stock power-off save `0x4949c`; Koma calls no flash or store-write routine.

**Hardware record** (`HW 2026-11-08`, both bodies on generation 2 Koma firmware, build 2b, package koma04w `747F061F` from `/IROM`): `PARAM.CUR` exported on both bodies (main from a 64 GB card). Cross-body mirror main → spare: `PARAM.DAT` = `komaset merge` of the main's `PARAM.CUR` onto the spare's, then edited (focus MF, EV 0.0, metering MULTI, ADJ and zoom-button customisations OFF, full-press snap OFF, menu layout STOCK + KOMA, B&W, ISO 400, FINE 3264, muted beeps, recipe SHINJUKU and four recipe slots). Over two boot cycles: `PARAM.OK`, `PARAM.DAT` consumed, `KSIO` status 1; the new `PARAM.CUR` differed from the sent block only in body counters, rate-control bias, flag `0x1b0ca`, recipe-store owner bytes and the stale checksum.

Not shown on a camera: the remove `0x55d80` versus the `DONE` fallback (which one consumed the file was not recorded), the `PARAM.BAD` paths, SAVE TO CARD, and stock re-validation of an imported out-of-range stock byte (the host validates every value against the map first).

## Koma area

Koma stores its state at `0x1b800..0x1bffb`, past everything stock 2.40 references (stock code touches nothing above `0x1b7ff`), so it rides on the stock power-off save. Stock fields only ever receive stock-valid values from features; each feature reads its byte at the point of use and treats an out-of-range value as the default. The core init writes every declared row default and then the magic whenever the magic is missing, and repairs out-of-range row bytes at every boot (`STATIC`, unit-emulated).

### Settings header and rows

The Tag column gives the camera evidence for the **feature** that owns the byte; the byte layout itself is `STATIC` from the code that writes it.

| Byte | Row | Values (default **bold**) | Tag |
|---|---|---|---|
| `0x1b800..0x1b8af` | Legacy `RCP1` store of the old 5-slot recipe feature; unused by current builds | | `STATIC` |
| `0x1b8b0..0x1b8b3` | Magic `KOMA` | | `HW` |
| `0x1b8b4` | Koma settings version | 1 (import refuses another version) | `HW` |
| `0x1b8b5` | SNAP DIST | 0 1 m, 1 1.5 m, 2 2 m, **3 2.5 m**, 4 5 m, 5 ∞ | `EMU` (no camera result) |
| `0x1b8b6` | FULL SNAP | **0 OFF**, 1 ON | `EMU` (no camera result) |
| `0x1b8b7` | AUTO-ISO MAX | **0 STOCK**, 1 200, 2 400, 3 800, 4 1600 | `EMU` (no camera result) |
| `0x1b8b8` | SLOW SHUTTER (slowest before ISO rises) | **0 STOCK**, 1/250 … 1/2 | `EMU` (no camera result) |
| `0x1b8b9` | HIGHLIGHT ALRT | **0 OFF**, 1 ON | `EMU` (no camera result) |
| `0x1b8ba` | LONG EXP LIMIT | **0 180 s**, 1 240 s | `EMU` (no camera result) |
| `0x1b8bb` | MENU LAYOUT | **0 5-TAB**, 1 STOCK+KOMA, 2 GR II (five-tab directory plus the GR II flag); read by menu5's init at the next boot | `EMU` |
| `0x1b8bc` | KOMA FEATURES | **0 FULL**, 1 ESSENTIALS: the installer skips every feature outside menu5, settingsio, settingsio-menu, telemetry, tether, liveview, stallfix | `EMU` |
| `0x1b8bd` | SAVE TO CARD (action) | **0 NO**, 1 YES, 2 SAVED, 3 FAILED. YES writes `PARAM.SAV`; the result is left in the byte; reset to NO at every boot; never imported | `EMU` |
| `0x1b8be` | STALL FIX FLAGS (no row; set by host `-KSET`) | high nibble `0xA` makes the low nibble the fix mask: 1 `0xa201` no-op while USB is attached, 2 no lens auto-retract timer, 4 LCD-mutex leak guard, 8 panel on at tether arm. Any other value means mask `0xd`, i.e. 173 (`0xad`) | `HW 2026-11-08`: second tether session armed in 10.41 s with 160 (all off), 0.66 s with 173 |
| `0x1b8bf` | PEAKING | **0 OFF**, 1 RED, 2 YELLOW; read once per live-view frame by the VD-task hook `0x95af4` | `EMU` (no camera result) |
| `0x1b8c0` | RECIPE: active recipe id (0 = none) | | `HW` (earlier frames; carried by the mirror) |
| `0x1b8c1..0x1b8c9` | EDIT RECIPE p0..p8: CURVE, CONTRAST, KEY, SHADOW, HIGHLIGHT, p5, p6, p7, SHARPNESS. p5..p7 are FILTER EFF., TONING, TONE LEVEL for a B&W look; SATURATION, WARMTH, TINT for a color look; COLOR, RANGE, SATURATION for SELECT (label chosen by store `+8`) | per row | `HW` (earlier frames); current build `EMU` |
| `0x1b8ca` | SAVE TO CUSTOM (action; returns to OFF) | **OFF**, CUSTOM 1..3 | `EMU` |
| `0x1b8cb` | RESET (action; returns to NO) | **NO**, YES | `EMU` |
| `0x1b8cc`, `0x1b8cd` | GRAIN, VIGNETTE of the active recipe | **OFF**, 1, 2, 3 | `EMU` (no camera result) |
| `0x1b8ce`, `0x1b8cf` | Unused inside the row range | | |

A recipe never writes a non-stock value into a stock byte: choosing one sets `0x1b030` to 1 (color looks and SELECT), 3 (B&W) or 6 (B&W(TE), toned) and stores the id in `0x1b8c0`. A recipe applies to a shot only while `0x1b030` still equals its base, so with no package loaded the camera shoots plain NORMAL, B&W or B&W(TE).

### Settings-file header `KSIO`

| Address | Field | Tag |
|---|---|---|
| `0x1b8d0..0x1b8d3` | Magic `KSIO`, written every boot | `HW` |
| `0x1b8d4` | Settings-io version 1 | `HW` |
| `0x1b8d5..0x1b8db` | 7-byte firmware tag copied from the MakerNote version format at `0x112578` (`Rev024M` on generation 1, `240K2` on generation 2) | `HW` |
| `0x1b8dc` | Last import: 0 none, 1 OK, 2 BAD, 3 OK with a different firmware tag; kept until the next attempt | `HW` (1 after the mirror) |
| `0x1b8dd..0x1b8ff` | Reserved | |

The header describes the body that wrote the block, so the import never copies it.

### Recipe store `RCP6`

| Offset from `0x1b900` | Field | Tag |
|---|---|---|
| +0 | Magic `RCP6` | `EMU` |
| +4 | Version 6 | `EMU` |
| +5 | Owner id of the edit rows | `EMU` |
| +6 | Last applied id | `EMU` |
| +7 | Record count N = 20 (10 B&W looks, SELECT, 6 color looks, 3 CUSTOM); a store with any other N is not accepted, because the FX table sits right after the records | `EMU` |
| +8 | Active type (selects the p5..p7 row variants) | `EMU` |
| +9 / +10 | SELECT color (1..6, 0 = no SELECT shot) / SELECT range, read by the selective-color pass | `EMU` |
| +11 / +12 | GRAIN / VIGNETTE for the next shot, read by the FX pass | `EMU` |
| +13 | Owner id of the GRAIN / VIGNETTE rows | `EMU` |
| +0x10 + 12·n | Record n: bytes 0..8 = p0..p8; byte 9 = kind (bits 0..1 type: 0 B&W, 1 color, 2 SELECT; bits 4..6 color look 0..5 for the ROM matrix); byte 10 = `0xa5` valid; byte 11 = XOR of bytes 0..9 with `0x5a` | `EMU` |
| `0x1ba00..0x1ba13` | FX table, one byte per recipe: `0x50 | grain | vignette << 2`; any other value reads OFF / OFF | `EMU` |

- A record applies only if valid, its XOR matches, its kind is valid and every parameter is below that type's limit; otherwise the look's default is used.
- Migration: an `RCP5` v5 store (same header and records, no FX table) keeps every record and starts with the FX table OFF. An `RCP4` v4 store (13 records) keeps its B&W records and moves CUSTOM 1..3 from ids 11..13 to 18..20. Any other header resets every record.
- Every rewrite writes the records and FX table first and the header last, and clears the active id, SAVE TO CUSTOM, RESET and the GRAIN / VIGNETTE rows.

How a recipe becomes a curve and register writes is in [06](06-image-pipeline-and-color.md#koma-recipes-and-selective-color).

## Factory adjustment store

### Layout and validation

Block `0x3d` sits between main info (`0x3c`) and extended data (`0x3e..0x52`); Monitor updates write block 0 only and do not rewrite it. Boot copies it to `0x08010000` (`0x279ec`/`0x63e30`); if byte 0 is not a valid magic or the store sum fails, `0x27bc0` copies the ROM defaults from `0x175380` instead (`STATIC`). On the main body's flash capture, byte 0 is `0xAA` and the store sum over offsets `0..0x1b00` (limit from word `0xf8`) equals the word at `0x1ffc` (`HW main 2.40`). Whether that physical block is also the logical block after bad-block replacement is not established.

Boot always reloads the store from flash (`0x1e444` → `0x27bdc`), so a RAM-only change lasts one power-on (`STATIC`). A type-5 file without `UEE00` changes the RAM store only, and the update's closing power-off loses it (`EMU`). A card `SCRIPT.MG1` with `adjcom X12C0 / V… / W…` is the only RAM-only route that reaches a shot, and it must be on the card at every power-on (`EMU`). Two stock card paths do not load store values: `0x27d0c` dumps the flash store to `/ATA1/ADJROM.DAT` (engine token `UEE52`), and the boot probe for `NADJ0001.ADJ` gates a factory code loader (up to 32 KB at `0x13000`) (`STATIC`).

### Commit chain

1. `X12C0` (twice) selects region base `0x08010000` (`0xd1e70`).
2. `UEEF1` reloads the store from flash into RAM (`0x27c28`).
3. Each `V<off>` sets an offset; each following `W<val>` writes a big-endian halfword at `0x08010000 + off` (`0xd1c30`, `0xd1d4c`).
4. `UEE00` (`0x27c5c`) requires byte 0 = `0xAA` or `0x55`, recomputes the store sum into `0x08011ffc`, then `0x63d9e` erases block `0x3d`, programs `0x2000` bytes and re-sums.

(`STATIC`.) `qrflect7` is a type-5 file: a 512-byte header then 4,486 CRLF lines (`X12C0` ×2, `UEEF1`, 2,241 `V`/`W` pairs over offsets `0x0002..0x1cd4`, `UEE00`), 31,914 bytes. It is read-modify-write, so words it does not name survive. Lines that are not five characters go to the service-terminal dispatcher `0x62670` over table `0x17781c` ([08](08-card-scripts-and-service-terminal.md#service-terminal)). `adjcom` in a card script reaches the same engine (reads go only to the debug console; writes persist); `UEE40` clears its unlock flag `0x1bd3a4`. The other `UEE` tokens are listed in [08](08-card-scripts-and-service-terminal.md#the-uee-tokens).

### Contents by layer

| Layer | Evidence | Tag |
|---|---|---|
| Words named by `qrflect7` (2,241) | Identical to the main body's capture; only 8 differ from the ROM defaults | `HW main 2.40` |
| Words outside `qrflect7` | 857 differ from ROM defaults: unit calibration lives here | `INFERRED` (one body) |
| Package version | `V0002 W0240` makes the version screen's `ADJ:` read 2.40 | `STATIC` |
| Package date | Offsets `0x0c..0x0f` (`V000C W2307`, `V000E W0907` give `23 07 09 07`); tag 5 renders `0x0d..0x0f` | `STATIC` |
| Unit date | Offsets `0x08..0x0b` feed Camera Info tag 4; not written by `qrflect7` | `STATIC` |
| Identity-like bytes | `0x20..0x2e` feed MakerNote tag `0x5`; `0x362`/`0x364` feed Camera Info tag `0x14`; not written by `qrflect7` | `STATIC` |
| Guard bytes | `0xcc`/`0xce`, compared at `0xd0e86` with RAM `0xae`; main body `0xcc` = `0x1c`, `0xce` = 1 | `HW main 2.40` |

A defaulted store would show `05 01 1f` at `0x0d..0x0f` (tag 5 = 2005-01) (`STATIC`).

### Known words

Values are `qrflect7`'s or compiled defaults, not per-unit values.

| Store address | Read by / meaning | Value | Tag |
|---|---|---|---|
| `0x08010002` | Adjustment version (`ADJ:` on the version screen) | `0240` | `STATIC` |
| `0x0801000c..0x0801000f` | Package date; tag 5 uses `0x0d..0x0f` through `0x193a80` | `23 07 09 07` | `STATIC` |
| `0x08010016..0x0801001b` | Defaults for VIDEO OUT, LANGUAGE (`0x17`), FLASH, LCD CONFIRM, AUTO POWER OFF, LCD BRIGHTNESS, applied by `0x3d4a4`; `0x17` = `0xff` also forces USB mode 0 | n/a | `STATIC` |
| `0x08010203` bit 2 | In `0x37958(0x22)`, picks the mechanical sequence table: `0x17e434` if clear, else `0x17e814` or `0x17e5f4` by `0x1f52e8` | n/a | `STATIC` |
| `0x08010267` bits 5-7 | Shutdown precondition `0x3c8dc(7)`: if bit 5 is set, calls `0x3d340(byte >> 6)` | n/a | `STATIC` |
| `0x08010312` | ISO 1600 digital gain (200%) | `0xc8` | `STATIC` |
| `0x080103a0`, `0x080103c4` | Per-body WB gain tables indexed by WB DETAIL `0x1b0a6` | n/a | `STATIC` |
| `0x080103e8` | `0xAA` selects the generic dark-frame path, else a stored defect list | `0xAA` | `STATIC` |
| `0x080103e9`, `0x080103ea`, `0x080103ec`, `0x080103ee` | Dark-frame subtract enable (0 means subtract); hot-pixel filter enable, threshold, step | 0, 1, `0x400`, `0x4ee0` | `STATIC` |
| `0x080103f6` | Debug override when `0xAA` (writes `0x50a55`, `0x124` in `0x90e0c`) | not set | `STATIC` |
| `0x080103f7` | Non-zero forces a flat lens-shading map | 0 | `STATIC` |
| `0x08010431` | Loop bound at `0x3ec14` (`V0430 Waa0e`); `0xFF` never terminates (hangs `MainCtrl`) | `0x0e` | `STATIC` |
| `0x08010a04..0x08010af7`, `0x08010a64..0x08010a8b` | Enhancer parameter tables chosen by `0x912bc` (sharpness and ISO band; no B&W branch) | n/a | `STATIC` |
| `0x08010c64`, `0x08010c66` | ISO band thresholds (ISO 400, ISO 800) | `0x20`, `0x30` | `STATIC` |
| `0x08010c68/69`, `0x08010c6c/6d` | HARD contrast/sharpness; SOFT contrast/sharpness | 4, 4; 2, 2 | `STATIC` |
| `0x08010c74/75` | B&W contrast/sharpness before 2.30 (unused since) | n/a | `STATIC` |
| `0x08010c78..0x08010c7e`, `0x08010c80..0x08010c8f` | Software chroma NR gate and offsets (color only) | `0x1a1a`, `0x20`, 0, 0 | `STATIC` |
| `0x08010de2` | Battery recovery threshold term (recovery counts down from 400 polls with thresholds raised by it) | 50 | `STATIC` |
| `0x08011304..0x08011394` | Color preset words, chroma-gain table, matrix and UV slots; see [06](06-image-pipeline-and-color.md#color-path) | n/a | `STATIC` |
| `0x080113f0..0x080113fc` | Tone selectors, luma word, `0x12018174` source; see [below](#store-bytes-that-feed-the-image-pipeline) | `V13F0 W2221`, `V13F2 W1E1F`, `V13F4 W2020`, `V13F6 W0520` | `STATIC` |
| `0x0801144c`, `0x0801145d`, `0x08011404..2e`, `0x080114d4..6` | Enhancer blend enable, lens-index term enable, CC1 `0x1201407c` sources (bits 24-25, 28) | n/a | `STATIC` |
| `0x08011b5c`, `0x08011b6e` | DNG color matrices (DNG tags `0xc621`/`0xc622` only; no effect on JPEG) | n/a | `STATIC` |
| `0x08010096..0x080100ab`, `0x8a..0x8d` | JPEG size targets (bpp ×10) and rate-control limits | 2.8 bpp FINE; ±5%, 2 fit steps, 3 corrections, at most 7 passes | `STATIC` |
| `+0x0dd2`, `+0x1520`, `+0x1522`, `+0x1526`, `+0x1528`, `+0x153c` | Boot delays taken from calibration (sleeps at `0x288f6`); `0xFF` here delays the update prompt to about `3.5e8` virtual units; `+0x0e3e`, `+0x1524` unresolved | n/a | `EMU` |

### Bytes `+0x18ec` / `+0x18ed`

`qrflect7` writes big-endian halfwords. The 2.31 → 2.40 store change at `+0x18ec` is halfword `0x405c` → `0x4055`: byte `+0x18ec` stays `0x40`, byte `+0x18ed` goes 92 → 85. Compiled defaults hold 92 (`0x5c`) in 1.08, 2.02 and 2.10 and 85 in 2.31 (`0x172f08`) and 2.40 (`0x175380`), so the 2.40 script only catches the store up to what 2.31 was built with (`STATIC`).

- `+0x18ed` has one reader: 2.40 `0xeaab2` in `0xeaa8c` (2.31 `0xe95c6` in `0xe95a0`), an AF contrast-peak comparator (`100·a < thr·b`) that uses it only when `*(0x92d1c()+3) == 1` or `0x1bf041 == 1`, otherwise `+0x0627`. `0xeaa8c` was rewritten after 2.10, the macro-AF release window.
- `+0x18ec` has 15 byte readers in each version (2.40: `0x702b6`, `0x707d8`, `0x707fa`, `0x94ffc`, `0xe69f8`, `0xe7632`, `0xe834e`, `0xe8d06`, `0xea1e6`, `0xec2fa`, `0xedc32`, `0xedcf8`, `0xee27c`, `0xee2ee`, `0xee74c`; the `0xe8ce0` 5-slot selection among them) and no writers; unchanged byte, unchanged behaviour.
- None of the 16 sites touches exposure, flash or power (`STATIC`, bounded). Earlier analyses read the change as `+0x18ec` `0x5c` → `0x55`; the changed byte is `+0x18ed`.

### Store bytes that feed the image pipeline

Stock bytes at `+0x13f0`: `22 21 1e 1f 20 20 05 20 | 40 08 99 3a | 00 00 00 80` (`STATIC`; identical on the main body's capture, `HW main 2.40`; `0x176770 - 0x175380 = 0x13f0`).

| Offset | Meaning | Read by |
|---|---|---|
| `+0x13f0` c5, `+0x13f1` c4, `+0x13f2` c1, `+0x13f3` c2, `+0x13f7` c3 | Tone-curve selector per contrast level | `0x900f4`, `0x90278`, `0x92050`; per shot via `0x9733a` |
| `+0x13f4`, `+0x13f5` | Selector when special state `0x23b085` is 1, or 3/4 | same |
| `+0x13f6` | Selector for contrast outside 1..5 (`0x05` = no upload) | `0x90278`, `0x92050` |
| `+0x13f8..0x13fb` | Word for luma-weight register `0x1201800c` (zero means `0x4008993a`; `0x99`/`0x3a` are BT.601 R/B × 512, `INFERRED`) | `0x92050` only, not per shot |
| `+0x13fc` | Word for `0x12018174` | `0x92050` |

(`STATIC`.) Only `+0x13f8..0x13fb` is the luma word; the bytes before it are selectors.

## Tone selection words

`0x92cc8` reads the contrast byte for the current mode (4 → `0x1b031`, 5 → `0x1b034`, 3 → `0x1b197`, 6 → `0x1b1a7`; routing in `0x7e626`). `0x92050` and the shot path pick a selector from `0x080113f0/f4` and pass it to `0x8ffc8` (`STATIC`).

| Contrast | Selector | Table |
|---|---|---|
| 1 | `0x1e` | `0x193b30` |
| 2 | `0x1f` | `0x195b30` |
| 3 (default) | `0x20` | `0x19bb30` |
| 4 | `0x21` | `0x197b30` |
| 5 | `0x22` | `0x199b30` |

Selectors `0x14` and `0x18` also map to `0x19bb30`; `0x0e` maps to `0x19db30` (inverted factory curve); other values upload nothing. The B&W path has no bank of its own; every mode at a given contrast reads the same table. HARD and SOFT take contrast from `0x08010c68`/`0x08010c6c`, NORMAL from a literal 3. `0x23b085` states 1, 3, 4 force selector `0x20`. Across 504 mode/detail/state combinations only modes 3, 4, 5, 6 at contrast 5 reach `0x199b30` (`EMU`). Details in [06](06-image-pipeline-and-color.md#tone-tables).

## Camera Info tag 5

| Body and firmware | Tag 5 | Frames | Tag |
|---|---|---|---|
| Main, 2.40 | `2007-09` | 136 of 136 | `HW main 2.40` |
| Spare, 1.08 (before update) | `2005-11` | 18 of 18 | `HW spare 1.08` |
| Spare, after the official 1.08 → 2.40 pair (frame shot under generation 1, which does not touch the store) | `2007-09` | 1 | `HW 2026-11-06 spare` |
| ROM-default store | `2005-01` | none | `STATIC` |

- Tag 5 is a package marker, not a unit marker: on 2.40 the bytes come from `qrflect7`'s `V000C`/`V000E` writes, so every body that applied the package shows the same value. The unit-specific date is tag 4.
- `2005-11` on the 1.08 spare matches the V1.08 package date (2005-11-16): its store held 1.08-era values and was not defaulted (`INFERRED`).
- The package date is not the firmware release date (2.40 released 2007-11-22; payload header 2007-10-24; store reads 2007-09). Reading the third byte as day of month is `INFERRED`.
- The spare reading `2007-09` after its update shows the `qrflect7` commit landed on real hardware. The store itself has not been read back.
- A damaged (defaulted) store shows tags 4 and 5 visibly changed, so a before/after JPEG comparison detects it (`INFERRED`).

## `qrflect7` commit risk

| Risk | Detail | Tag |
|---|---|---|
| Overwrite | Named words take Ricoh's values with no backup; they appear not to be per-unit (one body) | `INFERRED` |
| Torn commit | A bad store sum or magic boots on ROM defaults (`0x431 = 0x0e`, so no hang) and loses the ~857 unit words. Re-running `qrflect7` cannot repair it: `UEEF1` reloads the erased bytes, byte 0 stays `0xFF`, `UEE00` refuses, the driver logs `ERROR` to the console only. The window is one block erase plus program | `STATIC` |
| Bad value, valid sum | `0x08010431 = 0xFF` hangs `MainCtrl` before the update gate; `adjcom` from a card script could persist such a byte | `EMU` |
| Guard | A non-zero store `0xcc` that differs from RAM `0xae` (forced to `0x1c` at startup) shows screen `0x1a` and powers off, after `qrflect3` has already flashed. The main body passes | `STATIC` |
| Ordering | `0xd6dec` continues to the next `QRFLECTn` after a failed file, so `qrflect7` runs even after a failed `qrflect3` | `STATIC` |
| No input polling during the main image flash | `0xd6bcc` clears `0x2c9e48`; firmware ignores POWER and a falling cell | `STATIC` |
| Script card as update card | `SCRIPT.MG1` runs after a No, timeout, rejection, or on the first boot after a successful flash, so a card holding it is not a valid update card | `STATIC` |

`qrflect3` alone writes logical blocks `0xd..0x2e` and `0x3c` and nothing in `0x3d` (`EMU`). The emulator does not complete the store commit: the update task blocks after file byte 895 (about line 55 of 4,486) on an event the model does not supply, for both 1.08's and 2.40's updater (`EMU`). The commit has run once on hardware: the spare, 1.08 → 2.40 with Ricoh's `QRFLECT3` (2,202,472 bytes) and `QRFLECT7` (31,914 bytes) on a 2 GB card; afterwards `-VER` read `200 9.17 02.40`, the 19 internal-memory files were byte-identical, and tag 5 later read `2007-09` (`HW 2026-11-06 spare`). 1.08's update path matches 2.40's code for code apart from a boot-sync wait (`STATIC`).

## Reading state back

| Question | Answer | Tag |
|---|---|---|
| Installed firmware | USB `-VER` (`200 9.17 02.40`); MakerNote tag `0x2` reflects main-info `+0x12` only (`Rev0240` stock, `Rev024M` generation 1, `240K2` generation 2) | `HW` |
| Current settings block | `PARAM.CUR` on the card after any boot with a Koma package; decode with `komaset dump` | `HW` |
| Adjustment package in the store | Camera Info tag 5 | `HW` |
| Store intact | Tags 4 and 5 change on a defaulted store; full proof needs a block `0x3d` dump and the store sum | `INFERRED` |
| IMG SET used | MakerNote `0x1001` byte 40 | `HW` |
| Contrast used | Not recoverable from the JPEG | `STATIC` |
| Flash fired | Camera Info tag 6 low bits = 2 | `HW main 2.40` |
| Capture date on the spare | Unusable until the clock is set (it restarts at 2005-01-01) | `HW spare` |

## Open questions

- Physical flash location of the settings save `0x4949c`, and the meaning of the two mode-mask values that skip the first save.
- Whether the store sum covers offsets above `0x1b00` (`qrflect7` writes up to `0x1cd4`), and why both `0xAA` and `0x55` magics are accepted.
- Logical-to-physical mapping of block `0x3d` after bad-block replacement; the spare's store contents (never dumped).
- What the ~857 unit-specific store words outside `qrflect7` do.
- Which AF mode sets the flag that makes `0xeaa8c` read `+0x18ed` (writers of `0x1bf041` and `*(0x92d1c()+3)`).
- Whether 2.40 consumers (`0x7eb28`, `0x694a0`, `0x846aa`) clamp `0x1b193` = `0xff` on blocks saved before 2.20.
- What `0x23b085` states 1, 3, 4 mean to a user.
- Flash value names (including 17), scene ids 8, 9, 11, 12, DISP step names, `0x1b080..0x1b099`; MakerNote tag `0x1003`.
- What the emulated `qrflect7` step waits for, so a model can complete a store commit.
- On a camera: the `PARAM.DAT` remove versus `DONE` fallback, SAVE TO CARD, and stock re-validation (`0x3d4a4`) of an imported block. A stock settings reset may leave the Koma area stale (`INFERRED`); features already treat out-of-range values as defaults.

## Evidence

- [`analysis/settings-map/README.md`](../../analysis/settings-map/README.md), [`tools/komaset/settings-map.json`](../../tools/komaset/settings-map.json), [`tools/komaset/build_settings_map.py`](../../tools/komaset/build_settings_map.py): byte map, checksum, menu rows, exclusions
- [`tools/komaset/README.md`](../../tools/komaset/README.md), [`tools/komaset/komaset.py`](../../tools/komaset/komaset.py), [`tools/komaset/verify_session.py`](../../tools/komaset/verify_session.py): host side of import/export
- [`analysis/settings-io/README.md`](../../analysis/settings-io/README.md), [`tools/koma/features/settingsio.py`](../../tools/koma/features/settingsio.py), [`tools/koma/features/settingsio_menu.py`](../../tools/koma/features/settingsio_menu.py): `PARAM.DAT`/`PARAM.CUR`/`PARAM.SAV`, `KSIO`
- [`analysis/hw-tests/RUNBOOK-2026-11-08-PM.md`](../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md) (Results, blocks 1-3): `/IROM` package, exports, cross-body mirror; [`analysis/hw-tests/RUNBOOK-2026-11-08.md`](../../analysis/hw-tests/RUNBOOK-2026-11-08.md): stall-fix A/B
- [`analysis/sdhc/bundle/README.md`](../../analysis/sdhc/bundle/README.md), [`analysis/sdhc/bundle/koma04w.grmod`](../../analysis/sdhc/bundle/koma04w.grmod): installed package
- [`analysis/param-dat-sibling/README.md`](../../analysis/param-dat-sibling/README.md) (section 6): `0xd095c`, no `PARAM.DAT` in GR 2.40
- [`tools/koma/features/bwrecipes.py`](../../tools/koma/features/bwrecipes.py), [`tools/koma/features/fx.py`](../../tools/koma/features/fx.py), [`tools/koma/features/stallfix.py`](../../tools/koma/features/stallfix.py), [`tools/koma/features/peaking.py`](../../tools/koma/features/peaking.py), [`tools/koma/features/menu5.py`](../../tools/koma/features/menu5.py), [`tools/koma/features/__init__.py`](../../tools/koma/features/__init__.py), [`tools/koma/installer.py`](../../tools/koma/installer.py): Koma area layout
- [`analysis/koma-modes/README.md`](../../analysis/koma-modes/README.md), [`analysis/peaking/README.md`](../../analysis/peaking/README.md), [`analysis/stall/ROOT-CAUSE.md`](../../analysis/stall/ROOT-CAUSE.md), [`analysis/recipe-slots/README.md`](../../analysis/recipe-slots/README.md)
- [`analysis/emu-update-replay/README.md`](../../analysis/emu-update-replay/README.md): marker dating, migration writers, stalled store commit
- [`analysis/downgrade-plan/`](../../analysis/downgrade-plan/): `+0x18ec`/`+0x18ed` readers and compiled defaults, round-trip literals
- [`analysis/store-recipes/README.md`](../../analysis/store-recipes/README.md), [`analysis/tone-mode-mapping/README.md`](../../analysis/tone-mode-mapping/README.md): store bytes at `+0x13f0`, selectors, RAM-only store routes
- [`analysis/spare-usb/README.md`](../../analysis/spare-usb/README.md), [`analysis/card-metadata/`](../../analysis/card-metadata/): MakerNote and tag 5 on both bodies
- [`analysis/usb-first-contact/README.md`](../../analysis/usb-first-contact/README.md): the spare's official update
- [`analysis/shutdown-analysis-repair/README.md`](../../analysis/shutdown-analysis-repair/README.md), [`analysis/shutdown-ack-dependency/`](../../analysis/shutdown-ack-dependency/): power-off save, store bits read at shutdown
- [`archive/research/card-evidence.md`](../../archive/research/card-evidence.md): MakerNote input struct and byte sources; [`archive/research/downgrade-plan.md`](../../archive/research/downgrade-plan.md): round trip and `+0x18ed`
- [`archive/STATUS.md`](../../archive/STATUS.md) (section 1, 2026-11-06 night spare entry): spare tag 5 `2007-09` after the official update
