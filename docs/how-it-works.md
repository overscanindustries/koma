# How it works

Addresses are 2.40 runtime addresses. Status tags are defined in [features](internals/evidence-ledger.md#status-tags). The firmware itself is described in [internals](internals/README.md).

## Loader and package

A flash write is the one operation that can leave the camera unbootable, so Koma flashes only a small loader and the SD driver changes that cannot be loaded later. Everything else is in one package file, `GRMOD.BIN`, which the loader reads at each boot. A package can be replaced in seconds, and a card package is removed by pulling the card.

The stock factory ADJ loader can also run code from a card, but it needs a key combination at every power-on. Koma uses it for development only.

## Boot and the loader hook

The Monitor bootloader copies the application from flash to RAM and jumps to `0x1c000` ([internals 02](internals/02-boot-chain-and-monitor.md)). The application starts its tasks, checks for an update file, probes the SD card, and reaches a `bl 0x282cc` at `0x281ee`. Koma firmware changes that one call to `bl 0x57f78`, a loader placed in a dead code region (the Dcfs self-test island, `0x57f74..0x580cc`). The loader calls the original `0x282cc` first, then loads the package (`HW`).

The loader waits for the file task, opens the package, checks length (at most 64 KiB), header and checksum, allocates the body from the kernel pool and calls the installer inside it. With no file, a short file or a bad checksum it returns and the boot continues as stock. Nothing before the hook is changed, so the update check and the card probe run stock code. The loader cannot write flash, and the package never calls a flash routine (see [build-time lint](#build-time-lint)).

The image also rewrites the MakerNote version format at `0x112578` from `Rev%4.4x` to a fixed tag, so every JPEG records which firmware shot it. The firmware header keeps `0240`, so the version screen and USB `-VER` still read `02.40` and the stock updater still accepts the camera.

### Koma firmware images

Two images have been built and flashed. The first added only the card loader. The second adds SD 2.0/SDHC/SDXC support and the internal-memory fallback; Koma 1.0 installs it. Both were pre-release builds, so their photo tags differ from the release tag `240K1.0` ([versions](versioning.md)).

`/ATA1` is the firmware's name for the card; over USB the same volume is `/EXT0`.

| | First image (card loader) | SDHC image, build 2b |
|---|---|---|
| Changes to stock | Loader cave, hook, tag `Rev024M` | First image + SD 2.0/SDHC/SDXC driver, `/IROM` fallback, tag `240K2` |
| Package read from | `/ATA1/GRMOD.BIN` | `/ATA1/GRMOD.BIN`, else `/IROM/GRMOD.BIN` |
| Diff | 344 B cave + 4 B hook + marker | 2,052 B in 54 runs over the first image |

The SDHC image's driver initialises SD 2.0 cards (CMD8, ACMD41 with HCS, CSD v2) and uses block addressing for high-capacity cards. SDSC cards still run the stock driver bytes. The stock file system never mounts a high-capacity card on its own; the `widefs` package feature converts the FAT layer to sector numbers and mounts it. The card is not mounted when the loader runs, so the loader cannot read `GRMOD.BIN` from an SDHC/SDXC card; the package must be in internal memory. Design and proofs: [analysis/sdhc/driver](../analysis/sdhc/driver/README.md), [widefs](../analysis/sdhc/widefs/README.md).

Load order (`HW`): the loader tries `/ATA1/GRMOD.BIN` first. Only if that open fails does it mount `/IROM` (if needed) and open `/IROM/GRMOD.BIN`. A card package always wins. A firmware flash does not touch internal memory, so an installed package should survive reflashing (`INFERRED`; no reflash with a package present has been recorded).

Flash image format and the updater: [internals 03](internals/03-firmware-image-and-update-format.md).

## GRMOD.BIN

```
header     'KMOD': offsets of records, init table, resident segment, relocations
installer  Thumb code                                          (transient)
records    each with the bytes it expects to replace           (transient)
inits      init table                                          (transient)
resident   manifest ('KOMA', build id, status, version string), feature code, tables
relocs     relocation table                                    (transient)
```

A feature declares four kinds of change, each carrying its expected before-bytes:

- Patch: writes new bytes over old ones (code or data).
- Pointer: sets a 4-aligned word to the address of a resident label.
- Redirect: points one `bl` at a handler, exclusively.
- Hook: wraps a `bl`. The handler gets the original arguments and calls the next link. Several features can chain on one site, and the original runs once.

The installer verifies every before-byte first, places the resident block, range-checks every branch, copies and relocates resident code, runs the feature inits, and only then applies all records with interrupts masked. Any failure before that point writes nothing. Afterwards the loader frees the transient part. `koma04` links its resident block at a fixed address, `0x3dae00`, followed by a 16-byte canary, instead of in the kernel pool; `koma04w` keeps 41,336 B resident there (`HW`: `KOMALOG.TXT` reports `RES 003DAE00` and the canary intact).

With KOMA FEATURES = ESSENTIAL, the installer skips the inits and records of every feature outside a fixed essential list.

## Build-time lint

`koma build` refuses a package that breaks any of these rules (`tools/koma/linker.py`, `lint`):

1. No source may load, hold or call one of the flash, adjustment-store or updater routines listed in `FORBIDDEN_CALLS`. No patch may form a `bl` to one, and no hook may wrap a call to one.
2. A feature that writes a stock settings byte must declare the values it writes. The one exception is the `settingsio` import, which copies a validated `PARAM.DAT` over the block (`BULK_SETTINGS_WRITERS`).
3. No patch, hook, redirect or pointer may sit in a 32-byte code line that executes before `0x281ee`, because a stale instruction-cache line could run the old bytes. Six sites of the temporary `waitdiag` diagnostic are allowed by a checked exception: only the first halfword of the call changes, and the handler entry is placed so that old and new halfwords combined still reach valid code (`EARLY_SITE_OK`).

The linker also refuses before-byte mismatches, overlapping writes, hooks on sites that are not a `bl` to the declared target, a feature that depends on bytes another feature changes, out-of-range branches and menu rows outside the Koma byte range. It lists every problem at once. The lint cannot see addresses computed at run time.

## Settings storage

The camera keeps a 4,096-byte user-settings block at `0x1b000..0x1bfff` and saves it to flash at power-off with its own routine ([internals 05](internals/05-settings-and-adjustment-store.md)). Koma uses bytes that stock leaves unused:

| Address | Contents |
|---|---|
| `0x1b8b0` | Magic `KOMA` and a version byte (`0x1b8b4`) |
| `0x1b8b5..0x1b8cf` | One byte per menu row (the row's option index) |
| `0x1b8d0` | `KSIO` header: settings-I/O version, 7-byte firmware tag, last import status |
| `0x1b900` | Look store `RCP6`: header, then one 12-byte record per look from `0x1b910` |
| `0x1ba00` | GRAIN / VIGNETTE byte per look |

If a magic is missing, Koma writes defaults, magic last. Koma never calls the save routine; the stock power-off save persists its bytes. A look never puts a non-stock value in the stock IMG SET byte: it stores NORMAL, B&W or B&W(TE) there and keeps the look id in Koma's byte. Without the package the camera sees an ordinary stock setting.

`settingsio` exports the block to `PARAM.CUR` at every boot and imports a valid `PARAM.DAT` once (magic `MG1H`, checksum, Koma header version). The import skips a table of per-body bytes, clamps every Koma row byte to its range, and records the result in the `KSIO` status byte. It runs after every other feature's init, so the export holds their writes.

## The develop path

A still is processed in two develop passes. Koma hooks the call to the stock mono writer in pass 1 (`0x96436`) and pass 2 (`0xafd3e`), after stock has written its register values. Pass 2 rewrites white balance, chroma, matrix and mono settings, so overrides made only in pass 1 are lost; Koma writes them in both (`HW`).

- B&W looks upload a generated tone curve through the stock table upload, set the filter's luma weights and the toning word, and invalidate the selector cache so the new table is used. Sharpness comes from a hooked getter, which also feeds EXIF (`HW`).
- Color looks write the color registers below (`HW` for POSITIVE, NEGATIVE, BLEACH and RETRO on frames from an earlier build).
- SELECT and GRAIN/VIGNETTE are pixel passes on the finished master YUV image at `0x117382`, before JPEG encoding. SELECT neutralises chroma outside the chosen hue band (`HW`); GRAIN and VIGNETTE work on the Y plane (`EMU`).

| Register | Meaning | Status |
|---|---|---|
| `0x12018004` | Chroma gain, four bytes. 0 gives a grey frame; scaling all four by 1.30 gives a uniform x1.30. Drives SATURATION. | `HW` |
| `0x12018104`, `+4`, `+8` | 3x3 color matrix rows (MatR, MatG, MatB): sign-magnitude bytes (bit 7 sign), entries add, byte 0 is blue. TINT is a G-row shift. | `HW` |
| `0x12018110`, `0x12018114` | White-balance multipliers; WARMTH scales R and B on the shot's own AWB values | `HW` (earlier frames) |
| `0x120180d8` | Chroma coring: removes Cb (low field) and Cr (high field) below the field value; a dead zone, not an offset | `HW` |
| `0x12018028`, `2c`, `30` | IQ hue matrix, clip, offset: writes have no effect | `HW` |

One tone curve serves all channels, so per-channel looks such as cross processing are not possible. Live view runs Ricoh's monitor preset on every return to live view; the `preview` feature re-applies part of the look after those calls (`EMU`). Pipeline detail: [internals 06](internals/06-image-pipeline-and-color.md). Register probes: [colourprobe](../analysis/koma-0.1/colourprobe.md).

## The menu

The five-tab menu patches the stock menu: redirects, pointers and patches repoint the tab table, the header drawing and the row tables. A header band drawn by Koma covers the stock tab artwork. Koma reuses the stock list renderer and its glyph set, so labels use only the stock font's characters. Rows are data (id, label, options, byte, default, page group); the linker lays them out into pages, so a feature adds a row without touching menu code. On commit, Koma finds every changed row, applies them, then reloads every cursor from its byte, so a change that moves other rows (choosing a look, RESET) is not overwritten. The alternative layouts (STK+KOMA, GR II) are data overlays copied in at boot. Application structure: [internals 04](internals/04-application-architecture.md).

## USB

In ORIGINAL USB mode the camera speaks a Ricoh vendor protocol of text commands with replies and data frames ([internals 07](internals/07-usb-protocol.md)). Koma adds commands by repointing dead entries of the command table at `0x1fb400`: `-FDWL` becomes `-LVFR`, `-SDIS` and `-GDIS` become `-KSET` and `-KGET`, `-FUPL` becomes `-KSTA`, `-PWOF` becomes `-PEEK`. Their handlers run in the USB task.

- Tether: stock forces the USB state whenever a cable is attached, so `-CCTL keyset` presses are ignored. One exclusive redirect at `0x4fe44` in the app-state resolver lets the dial state win after `-CCTL remtin`, so keys and the shutter work while the USB session stays up. `remtout` or unplugging hands control back.
- Live view: `-LVFR <mode>,<seq>` returns one frame from the live-view ring. Mode 0 is grey 160x120, mode 1 JPEG 320x240, mode 2 the on-screen-display layer (360x240, 4 bpp with palette, for a body with a broken LCD), mode 3 JPEG 640x240.
- Settings: `-KSET` writes one settings byte in RAM within a checked window and applies it; `-KGET` reads; `-KSTA` returns a state snapshot; `-PEEK` reads RAM in `0x1a8000..0x3e5000`.
- Stall fix: the second remote session after a `remtout` stalled for about 10-20 s because an LCD mutex was left held. `stallfix` corrects it, switchable through one settings byte (`HW`; [root cause](../analysis/stall/ROOT-CAUSE.md)).

Live-view streaming internals: [internals 13](internals/13-display-live-view-and-usb-streaming.md).

## The emulator

Development runs the original 2.40 firmware under Unicorn (`tools/grdemu`) with modelled peripherals, a virtual card and a UI renderer (`tools/grdui`). It executes the real instructions, so it shows which values firmware writes and catches crashes and bad installs before a card is swapped. `koma smoke` boots the loader image with a package, takes one shot and checks every feature's hooks; `koma selfcheck` runs the installer and hooks in a smaller unit emulator.

Emulator results are tagged `EMU`; a feature is `HW` only after a camera has run it. Detail: [internals 10](internals/10-emulator-and-tooling.md).

## Emulator limits

Verified gaps ([emulator-limits](../archive/research/emulator-limits.md)):

- CPU1 is not emulated. All replies from the second processor (power, battery, keys, lens) are fabricated, so faults in that exchange (such as the ADJ timeout) do not reproduce.
- There is no sensor, AFE, lens motor, flash circuit or real LCD. Register effects on the image must be measured on a camera.
- Virtual time is coarse. Races, watchdog timeouts and real scheduler timing are not reproduced, and instruction-cache behaviour is not modelled.
- The OneNAND flash model covers locks, bad blocks and injected failures, but not torn programs on power loss, wear or ECC generation.
- The default harness serves `/ATA1` from a host folder at the file-API boundary. The SD model used for the SDHC firmware proofs is register-level, but its timing is not the real card's.
- Unmodelled registers return the last value written, so behaviour that depends on a real register value can differ.
- Renders omit images the firmware loads through hardware-signalled paths (the stock tab artwork).
- Instruction corner cases may differ between Unicorn and the real ARMv4T core.
