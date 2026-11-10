# 10. Emulator and tooling

**Summary.** The build tooling is `tools/koma`: a host-side linker that turns a set of features into one `GRMOD.BIN`, plus three checks of increasing cost (`inspect`, `selfcheck`, `smoke`). `selfcheck` runs the module's installer on the real 2.40 payload under Unicorn; `smoke` boots the whole camera model with the module on a virtual card and takes one shot. Both are `EMU`: they show what the original firmware code does against peripheral models written for this project, and the hardware has already shown several places where those models were wrong (section 7). The remaining `tools/` packages are research tools (section 8): an emulator, a USB client, a card-script linter, photo-look and tone tools, a patch builder and evidence verifiers. Current as of 2026-11-08.

Tags: `HW`, `EMU`, `STATIC`, `INFERRED`, defined in [features](evidence-ledger.md).

Related: [04 Application architecture](04-application-architecture.md), [07 USB protocol](07-usb-protocol.md), [09 Recovery and failure modes](09-recovery-and-failure-modes.md), [11 Modification playbook](11-modification-playbook.md), and the user-facing [build guide](../building.md) and [install guide](../install.md).

## The `koma` tool

All commands are host-only: none touches a camera, a card or USB. The only hardware step is copying a file to a card (or flashing an image, see [11](11-modification-playbook.md)).

| Command | What it does | Runs firmware? | Evidence class of its output |
|---|---|---|---|
| `koma list` | Lists every feature (version, summary, patches, hooks, settings, options) and the named sets | No | `STATIC` |
| `koma build` | Links features into one module and a JSON manifest; runs the conflict checks and the lint | No | `STATIC` |
| `koma inspect` | Decodes a module; checks every before-byte against 2.40 and the build id | No | `STATIC` |
| `koma selfcheck` | Static disassembly checks plus a unit-emulated install, a hook run on stock code around each site, and negative cases; under a second | Installer and hook sites only | `EMU` |
| `koma smoke` | One full emulator boot with the module as `GRMOD.BIN`, one still shot, PASS/FAIL/SKIP per feature; about 1.5-2 minutes | Whole application | `EMU` |
| `koma make-loader` | Applies the 344-byte loader, its hook at `0x281ee` and the `Rev024M` marker to the user's stock 2.40 file and writes the generation 1 Koma firmware image; writes the output only if its sha256 is the known one (`107cb2b6...c995`) | No | `STATIC` |
| Unit tests (`tools/koma/tests/run_tests.py`) | Linker rules, namespacing, relocations, size limit, CLI, and the installer under Unicorn; needs `KOMA_FIRMWARE` | Installer | `EMU` |

`smoke` and `selfcheck` drive a vendored copy of the emulator under `tools/koma/emu/` (grdemu core and the grdui session layer), not the top-level `tools/grdemu`.

`make-loader` builds the generation 1 image only. There is no `koma` command for generation 2. The generation 2 Koma firmware image (stock 2.40 + loader + SDHC/SDXC driver + `/IROM` fallback + tag `240K2`) is produced by `analysis/sdhc/driver/build.py`, which starts from the generation 1 image (the `make-loader` output; same sha256) and asserts every edit's before-bytes. That script expects the generation 1 image at `analysis/flash-1-package/`, which is not in this tree. Which edits it makes, and the proofs, are in [01](01-hardware-and-memory-map.md), [03](03-firmware-image-and-update-format.md) and [analysis/sdhc/](../../analysis/sdhc/).

### What `koma build` does by default

- With no `--features`, `build` links the old default set `tone-r3,snap15,recipe-slots`. Every build stamps a version string that ends in its build id, whatever the feature list; the build id (`747F061F` for the installed package) is a hash of the body.
- `build` does not apply the camera options. `CAMERA_OPTS = {"telemetry.cp15": "off"}` is defined in `tools/koma/features/__init__.py` but nothing reads it. `telemetry.cp15` defaults to `on`, and a build whose feature list includes `telemetry` (the `koma03*` and `koma04*` sets do) then contains `MRC p15` reads, which hang the camera's core at boot (`HW`). A camera build must pass `--opt telemetry.cp15=off` explicitly.
- `koma build --features koma04 --opt telemetry.cp15=off` is not the installed package. koma04w (58,092 B, header `747F061F`) is built by `analysis/sdhc/bundle/build.py`: the `koma04` set plus the `widefs` and `widefs_init` features (full-capacity FAT32 for 64 GB cards) in a fixed init order, static resident placement at `0x3dae00`, `telemetry.cp15=off`. `widefs` is not registered in `tools/koma/features/`.
- `build` writes `OUT.grmod` and `OUT.grmod.json` (sizes, records, sha256, version string, build id). Builds are named `*.grmod` on disk; `GRMOD.BIN` is the name on the card or in `/IROM`. `QUICKVER.UP` is refused as an output name. `--resident pool|static[:ADDR]` selects the resident placement; `koma04` defaults to static.
- Every command except `make-loader` reads the user's own 2.40 file through `--firmware PATH` or `KOMA_FIRMWARE`. LLVM 20 provides the assembler (`KOMA_LLVM` overrides its location).

### Reading tool output

| Output | Class | Why |
|---|---|---|
| Byte diffs, before-byte checks, sizes, relocations | `STATIC` | Direct reading of files |
| `selfcheck` install and hook runs | `EMU` | Original instructions on a bare CPU with stubbed callees; no RTOS, no devices |
| `smoke` PASS | `EMU` | Original firmware under supplied CPU1, card, sensor and timing models |
| A behaviour seen on the camera | `HW` | Only this upgrades a feature to `HW` |

A PASS means the module installed and its code ran in the model. It says nothing about caches, real card timing, sensor output or how long things take. Instruction counts ("virtual time", vt) are not durations.

### Module layout

| Part | Contents | Lifetime |
|---|---|---|
| Boot header `KMOD` | Offsets of records, inits, resident segment, relocations | Transient |
| Installer | Thumb code that the loader calls | Transient |
| Records | `CHECK`, `DATA`, `BL`, `PTR`, each with its before-bytes | Transient |
| Init table | Feature init entry points | Transient |
| Resident segment | Manifest (`KOMA`, build id, status, version string, feature table), then each feature's code, tables and blobs | Resident |
| Relocation table | Fix-ups to runtime addresses | Transient |

The loader caps the body at 64 KiB. The installer always returns 0, so the loader frees the transient part; only feature code stays in memory.

### Installer steps

| Step | Action | Failure effect |
|---|---|---|
| 1 | Check ABI = 1 and image version `0x240` | Nothing written |
| 2 | Verify every before-byte of every record (checks, data, hook sites, publish slot) | Nothing written |
| 3 | Allocate the resident block from KernPool | Nothing written |
| 4 | Range-check every hook `bl` | Block freed; nothing written |
| 5 | Copy and relocate the resident segment | Block freed; nothing written |
| 6 | Run feature inits | - |
| 7 | Apply every record with IRQ and FIQ masked; restore the mask; set manifest status 1 | - |

After install, `0x58190` holds `KOMA` and `0x58194` the address of the resident manifest (status at `+0x0c`, version string at `+0x10`). That slot is dead code past the loader in the same island function and is reserved for Koma (`STATIC`).

### Conflict checks at link time

The linker reports every conflict at once:

| Check | Refuses |
|---|---|
| Overlapping writes | A patch overlapping another patch or hook |
| Hook targets | Two hooks on one site that disagree on the original target (same target: chained, not a conflict) |
| Depending on changed bytes | A check of bytes another feature changes, except a feature checking a site it chains on itself |
| Before-bytes | Overlapping before-bytes that disagree, or before-bytes that differ from the 2.40 image |
| Hook sites | A hook site that is not `bl target` in the image |
| Reserved bytes | Writes into the loader, its hook or the Koma publish slot |
| Settings | Two owned stores overlapping, or a write inside another feature's store (two features writing one stock field is a warning) |
| Dependencies | Missing requirements, declared conflicts, cycles |
| Size | A body over 64 KiB |

### The three lint rules

| Rule | Refuses | Why |
|---|---|---|
| Flash and store writes | Any source that loads or holds the address of a flash or store writer (settings save `0x4949c`, `UEE00` `0x27c5c`, store `0x63d9e`, updater `0x5d18c`/`0xd6dec`/`0x63c68`/`0x63b88`/`0x63c80`, NAND driver `0x63598`/`0x636dc`/`0x649a8`/`0x64f04`, whole-device erase `0x28194`, direct programmer `0xd6c58`, type-5 reader `0xd6a62`), a patch whose bytes form a `bl` to one, or a hook that wraps one | A module must never write flash; pulling the card must always undo it |
| Early sites | Any patch, hook, redirect or pointer whose 32-byte line executes before the loader hook `0x281ee` | Code fetched before the patch could sit in a stale cache line; and nothing may change what runs before the update gate |
| Stock writes without values | `Settings(owned=False)` with no list of stock-valid values | A stock field must only ever hold a value stock code accepts |

The lint cannot see computed addresses: a writer reached through a calculated pointer is not caught.

### Settings rows

Features declare menu rows as data (`ROWS`: id, label, options, settings byte, default, group). The linker checks that each byte lies in `0x1b8b5..0x1b8cf`, bytes and ids are unique, labels are at most 16 characters (warning over 14), option strings are 1-9 characters in the stock font, and the default is in range. The core init writes every default the first time (magic `KOMA` at `0x1b8b0`, version byte `0x1b8b4`, `K` written last) and afterwards resets only out-of-range bytes. Stock's own power-off save persists the block; Koma never calls a save.

## `koma selfcheck` and unit emulation

`selfcheck` places the real 2.40 payload in a Unicorn ARM big-endian address space, puts the module body at the pool address seen in emulated loader runs, supplies a service table and a stub allocator, and calls the installer exactly as the loader does. It records every write, compares the resident image with the manifest, then runs the stock instructions around each hook site through the feature's handler. Firmware callees that need devices (the colour-register writer, the LUT upload) are stubbed.

| Check | Result if it fails |
|---|---|
| Static: every record decodes; every before-byte matches 2.40; hooks decode as `bl` to the expected target | FAIL, with the record |
| Full install | Resident image and manifest status must match |
| One-byte mismatch at any record type | Must apply nothing |
| Wrong image version or ABI | Must apply nothing |
| Allocation failure | Must apply nothing and free |
| Resident block out of `bl` range | Must free it and apply nothing |
| Hook chains | Two- and three-way chains run in build order; the original runs once |
| Feature checks | Each feature's own unit expectations (register values, uploads) |

`selfcheck` is not a boot: there is no RTOS, no CPU1, no card. It catches linker, relocation and installer mistakes in under a second.

## `koma smoke`

`koma smoke OUT.grmod [--feature NAME] [--json report.json]` runs one process:

| Phase | What happens |
|---|---|
| Boot | Boots the loader image with the module on the virtual card as `GRMOD.BIN`; stops tracking install when the loader returns from the module |
| Ready | Runs to virtual time 5e8; formats the modelled internal memory through the native menu (the camera model takes a full press only after that) |
| Shot | Holds RL1 for 1.2e8, then RL2; stops right after the develop step `0x96350` that holds the hooks |
| Report | One line per feature: PASS, FAIL or SKIP |

Per-feature checks: the loader called the module; manifest status is 1; every record of the feature is in RAM (data bytes equal `after`, `bl`s decode to the resident handler); every hook handler of the feature ran during the shot; and the feature's own smoke checks pass (for example, which tone table the CC2 upload carried). Hooks the single shot cannot reach (menu, playback, SNAP mode, live view) are reported as SKIP and are covered by unit tests only.

A feature may declare `SMOKE_STUBS`: firmware calls its init makes that the model cannot survive. They are stubbed only while the loader runs the module, and each call is logged. The one in use is the key-scan period change: in the model, the key task spends most of its time in a bit-banged serial loop whose device never answers, and a faster scan starves `MainCtrl` (`EMU`; see section 7).

Starting the shot at 3e8, or skipping the format, gives no capture. Expect 1.5-2 minutes on an idle machine; wall time scales with load.

## The camera model

`smoke` drives a Unicorn-based model that runs the official 2.40 application from `0x1c000` (the payload of the user's own `QRFLECT3`, loaded into RAM). The standalone form is `tools/grdemu`.

| Modelled | How | Tag |
|---|---|---|
| CPU | Unicorn ARM big-endian; the firmware is ARMv4T Thumb/ARM | `EMU` |
| RAM | 64 MiB plus an alias window at `0x08000000` | `EMU` |
| Interrupt controller `0x12000000` | Pending and mask registers the IRQ entry reads | `EMU` |
| System timer `0x12010000` | Enough for the Nucleus scheduler; one tick per N translated-block instructions | `EMU` |
| GPIO `0x12030000` | Pin accessors; OneNAND ready line | `EMU` |
| USART `0x12024000` | Panel control only, no pixels | `EMU` |
| OneNAND `0x10800000` | OneNAND256 per its datasheet: locks, BootRAM, bad-block markers, injectable failures | `EMU` |
| OSD | 4 bits per pixel, 360x240 visible, palette registers; renders the firmware's own menus | `EMU` |
| Capture device | A line-counter stand-in that lets a still pass the sensor wait and reach develop | `EMU` |

### Supplied versus measured

"Supplied" means the value was chosen for the model; the firmware never saw a real one. Unknown MMIO returns the last value written, else 0, and is logged with per-register counters.

| Item | Status | Tag |
|---|---|---|
| CPU1 replies (power, battery, temperature, lens, boot trigger, hard keys, dial) | Fabricated from how the firmware uses them; the CPU1 firmware is not in the 2.40 image | `EMU` |
| `/ATA1` file layer | FAT, SD driver and mount are skipped; files come from a virtual card (host directory at the `0x55dc0` API) | `EMU` |
| Key events (injected at the key sink `0x51740`), mode dial position, reset acknowledgement, controller and timer behaviour | Supplied | `EMU` |
| GPIO60 | An input condition set for the run, not an established user-selectable strap | `EMU` |
| NAND spare and ECC bytes | Synthetic; flash above the captured range starts erased (`0xff`) | `EMU` |
| UI constants: `SDRAM_CAPACITY` reads 64 MiB; `VEN_RESET_VALUE` `0x000f0f0f`; DMA reset `0x1200c16c` = `0x70f`; frame interrupt every 66,667 virtual instructions | Added only after a firmware assert named the problem; physical values unknown | `EMU` |
| Virtual time | One timer tick per N translated-block instructions; instruction counts are not durations | `EMU` |

The model reproduces control flow and I/O order and records every stub. It shows which values the firmware writes to which registers. It shows no pixels: there is no sensor, lens, autofocus or computed JPEG.

### Flash model

- OneNAND256 (KFG5616x1A) per datasheet v1.1; the part is inferred from the driver and the table at `0x177978`, no marking has been read (`INFERRED`).
- Hand-off state: `F221h` = `40E0h` (IOBE set) and `F241h` = `8080h`. With IOBE clear (`40C0h`, the datasheet default) the stock boot stalls at its first flash read (`0x64524`), which supports an earlier boot stage having set IOBE (`EMU`, `INFERRED`).
- The datasheet limit of 2 partial programs per sector, and program-without-erase, are counted, not failed. Wear, read disturb, real ECC and torn writes are not modelled. Unmodelled OneNAND registers (ECC position registers `FF01h..FF04h`, `F002h`, `F24Dh`) return the last value written or 0.

### CPU model

`grdemu` accepts an allowlist of Unicorn cores (`default`, `arm926`, `arm946`, `arm1026`, `arm1136`, `arm1176`, `ti925t`); `default` is model 5, ARM1176 on the tested installation. The Monitor's `MOV r0,#0xd3; MSR CPSR_c,r0` (`0x1e8..0x1ef`) shows the difference that matters: with FIQ initially unmasked, the ARM1176 model leaves the F bit clear while the other cores set it. Controls across models agree where they were compared: cold boots under default, ARM926, ARM946 and ARM1176 all reach `MainCtrl` at 3,131,753 virtual instructions, and four Monitor-to-application reset controls reach identical hand-off fields at 1,167,353. This is a limit of the model, not evidence about the camera's CPU, and it proves nothing about FIQ races ([tools/CPU_MODELS.md](../../tools/CPU_MODELS.md)).

### Captured-Monitor harness

A separate harness executes the captured Monitor 1.00 from a cold reset (real flash driver, replacement mapping, hand-off with LR `0x3c4` and words `[0,0,0,0x1220000c]`, conditional card RAM load, reinstall). The capture it runs on (`analysis/camera-dumps`) is local-only and not in this repository; the saved reports are. Key results (`EMU`):

| Scenario | Result |
|---|---|
| Normal flash boot or Monitor RAM boot, official type 2, healthy info | Update gate skips; runs past post-startup |
| Type-7 marker image, healthy info, native checksums | RAM hand-off at instruction 470,924,615; post-startup at 493,688,087 |
| Invalid info, type 2 RAM fallback | Application selects the updater (intercepted at `0x5d18c`) |
| Zeroed main + invalid info, type 2 RAM fallback | Application reinstalls main and info byte-exact: 2,152 PROGRAM and 35 ERASE in the updater |

Ordinary startup is not read-only in the model: each healthy run does ten PROGRAM operations in IROM block `0x66` (beyond the capture, synthetic) and one ERASE in block `0x55`. These are model behaviour, not observed camera writes. Checksums are accelerated by native helpers (Monitor `0x19ac`, application `0x62cf0`; the streaming scanner `0x275cc` runs its own byte loop with the divide helper `0x157c84` accelerated only for divisor 6); accelerated runs do not preserve elapsed time, instruction count or flags.

Runs stop where the model stops, not where the camera would. The original image decoder `0x5df84` asserts in `Driver\src\sdl_dma_drv.c` line 2353 because DMA register `0x1200c16c` reads 0 where the driver needs PXIR9 = 3 or 7, which suspends `MainCtrl` (TCB `0x2be5e4`) through `0x289ec`/`0x288b0`. With a supplied latch of `0x300` or `0x700` the decoder starts and times out; completion would run IRQ entry `0x1c304`, then `0x2398c` (reads JPEG status `0x1201c020`), then HISR `camJPGhr`, and the model supplies no JPEG computation.

### Peripheral profiles

The captured-Monitor profiles take an optional `peripherals` object ([tools/PERIPHERALS.md](../../tools/PERIPHERALS.md)): `secondary_timer` (channel 2 reload, mask and write-0-to-clear behaviour with a supplied shared-clock calibration), `gpio_irq` (pending, mask and W0C registers; aggregate INTC line 11) and `gpio43_notifications` (strictly increasing virtual times, at most 64, only GPIO43). They are pending-state inputs, not motor simulations or measured edges.

### Writers driven in the model (`EMU`)

The firmware's own EXIF, MakerNote and DNG header builders were called from `tOpeMain`'s key-poll context with a synthetic capture struct: `0x936c0` (JPEG parameters), `0x84c64` (JPEG header; `0x84c88` sRGB) and `0x68a88` (DNG parameters `0x938f8`, TIFF writer `0x87830`). No firmware function was stubbed; all three returned 0 in about 0.56M instructions.

| Result | Value |
|---|---|
| JPEG header | 21,297 B; big-endian TIFF; IFD0 12 tags, EXIF IFD 28 tags, MakerNote 13,590 B with 9 tags (`Rdc`, `Rev0240`, ...), Camera Info 22 tags; preview at TIFF offset 1471; then DQT/DHT/SOF0 3264x2448/SOS |
| DNG header | 2,570 B; DNG 1.0; SubIFD 3276x2460 12-bit CFA RGGB |
| Comparison with 136 real 2.40 JPEGs | Segment order, all four tag sets, `Rev0240`, size, bpp 280, DHT and DQT length match every real file; remaining differences (no thumbnail EOI, zero DQT DC step, image setting NORMAL not B&W) follow from what was supplied |
| `Rev024M` marker (RAM `0x11257b`) | MakerNote tag 2 reads `Rev024M` in both the JPEG and DNG header (1 byte differs in each) |
| Copyright literal `0xa8bc8` (27 B, copied by `0xa8810` to `0x2d2a90` at boot) | Patching the literal changes IFD0 Copyright in both files with no code change; ImageDescription (literal `0xa8b0c` copied to `0x2d2a50`) should behave the same (`INFERRED`, not run) |

Supplied: the capture struct, the AE result struct (modal values of the real 2.40 set), WB bytes and the post-encode scan lengths; adjustment-area values (DNG colour matrices, crop margin, tag 5, Camera Info dates) are model content. No entropy-coded data or RAW strip is produced and DQT values are zero because rate control never ran. Not covered: the Adobe RGB builder `0x8564c`, the YUV/TIFF writer `0x86eb0`, the alternate string set behind `0xa88ee`, and no real DNG exists to compare. Matching values show plumbing, not metering; tag structure, offsets, strings and the `Rev` text are firmware output.

### Update replay (`EMU`)

The captured Monitor and the original 1.08 code, on the same OneNAND model, replay the 1.08 firmware running Ricoh's 2.40 update, including the update, shapes, race and type-5 plans. Details and the older-image boots are in [03](03-firmware-image-and-update-format.md) and [12](12-version-history.md).

### Display and input facts

| Fact | Tag |
|---|---|
| OSD: 4 bpp, high nibble left, 360x240 visible, stride `0xb4`; palette at `0x12020100 + i*4` | `STATIC` |
| Palette index 0 is treated as transparent | `INFERRED` |
| Menu strings are per-language pointer tables (9 languages); labels are glyph-coded (font page, glyph) pairs | `STATIC` |
| The firmware's menus, ADJ screen and six dial positions run in the model; menu order, names, defaults and remaining-shot counts at 26 MB (RAW 1, F3264 8, N3264 14, N640 277) match Ricoh's guide, except the shooting-warning default (guide ON, model OFF) and the quality list (extra N3:2; the guide text is 1.x-era) | `EMU` |
| DPUSH cannot be tapped: `0x513c0` emits it only on a hold across polls | `EMU` |
| A full press without the capture stand-in stops at the sensor wait `0x947e4` | `EMU` |

## What the model cannot see

| Blind spot | Consequence | Tag |
|---|---|---|
| CPU1 | A change that upsets real power sequencing, lens or shutter control is invisible | `EMU` |
| Caches | Unicorn is always coherent; a stale I-cache or D-cache cannot appear | `EMU` |
| Coprocessor 15 | Unicorn executes `MRC p15`; the camera's core hangs on it | `HW` |
| Timing | Races, watchdogs and real scheduler timing are not reproduced; waits on hardware end by timeout | `EMU` |
| Hardware-signalled loads | A semaphore with no modelled producer times out, and the work behind it never happens | `EMU` |
| Pool size | The model's KernPool headroom is far larger than the camera's | `HW` |
| Imaging | No CCD, AFE, lens motor, flash capacitor or real LCD; tone work ends at port writes | `EMU` |
| USB bus | The bus side of a tether session is not modelled; USB host behaviour is not exercised | `EMU` |
| Real flash | Wear, read disturb, real ECC and torn writes are not modelled | `EMU` |
| Persistent state | `/ATA1` writes and some commits live in memory only | `EMU` |
| Core differences | Unicorn's default core model is ARM1176, not a strict ARMv4T; LDR rotation, interworking and banked-register edges may differ | `EMU` |
| Reads made by Python stubs | Not seen by watchpoints; DMA is not modelled | `EMU` |
| Asserts as the only signal | The firmware suspends a task on a driver assert; a missing hardware value surfaces only when an assert fires | `EMU` |
| Entry inputs | UP+TELE, RIGHT and ENTER are modelled inputs, not verified physical procedures | `EMU` |

## Model errors found on hardware

| Model result | Hardware result | Tag |
|---|---|---|
| Menu renders showed no stock tab-header artwork | On the camera the stock header artwork (pictograms, dark active-tab panel) drew over the first menu module's header strip and clipped its labels. The model never loads that image; the likely cause is the background-image load `0x8e3d0` waiting on a semaphore at `0x2a2f4` with no producer in the model, which is an inference from the model run and disassembly. The menu feature now draws an opaque header band | `HW` (artwork); `EMU`, `STATIC` (cause) |
| A key-scan period of 20 ticks starves `MainCtrl` | Caused by the model's bit-banged serial device at `0x2cfdc` never answering; the stub exists only in `smoke`. The real device and its latency are unidentified | `INFERRED` |
| CP15 reads succeed | `MRC p15` hangs the camera's core at boot; the firmware itself contains no CP15 instruction | `HW` |
| Largest free KernPool block about 78-80 KB | 3.9 KB with the 29.8 KB Koma body still allocated (6,656 B with a smaller module); packages now link a static resident region at `0x3dae00` | `HW` |
| Self-modifying code coherent | Also coherent on the camera: a rewritten immediate ran its new value | `HW` |
| The `-VER` 3 s timer message `0xa201` never expired in the model; the tether arm had to inject it and the model's state words stayed at 2 | On the camera `0xa201` arrived in the record runner, `0xd2770` returned -1 and the runner exited (tether 1.4 fix; the injected run reproduced the -1 but not the exit) | `EMU` ([FIX-1.4](../../analysis/hw-tests/tether-debug/FIX-1.4.md)) |
| Tether scenarios run one session; the USB bus is not modelled | A second tether session started 5-12 s after the previous `remtout` stalled for about 10 s on hardware (arm 10.41 s). The cause was found by static analysis (LCD driver mutex `0x2c02ec` left taken on the capture path) and fixed by `stallfix`; arm time with the fix 0.66 s | `HW`, `STATIC` ([ROOT-CAUSE](../../analysis/stall/ROOT-CAUSE.md)) |
| The file layer is a host directory; FAT and the SD driver are skipped | On a 64 GB FAT32 card the stock `-NLST /EXT0` never completes (reader `0xb7954` sizes the root listing from the FAT16 root-entry count, 0 on FAT32). The model never runs that code | `HW`, `STATIC` |

An emulator render alone therefore does not confirm a menu or OSD change; it shows only what the firmware's text and box routines draw. Live-view and tether frame rates are measured on a camera only ([13](13-display-live-view-and-usb-streaming.md)). Check visual and timing changes on hardware.

## Research tools

All of `tools/` is in this repository. Only `grdusb`, `komaset/bridge` (komabridge) and `komacam` can touch a camera, and only when started with `--live`/`--real`/`--spawn real`; every other entry is offline. Entries marked "needs local files" read inputs that are not tracked (raw camera captures, `analysis/camera-dumps`).

| Tool | Purpose | Evidence class of output | Touches a camera |
|---|---|---|---|
| [`tools/grdemu/`](../../tools/grdemu/) | Unicorn boot model of the 2.40 firmware: core, peripherals, stubs, OneNAND model, capture-device stand-in, captured-Monitor recovery harness (`python -m grdemu SCENARIO` with `PYTHONPATH=tools`; the Monitor harnesses need local files) | `EMU` | No |
| [`tools/grdusb/`](../../tools/grdusb/README.md) | Client for Ricoh's USB vendor protocol: `list`, `ver`, `info`, `ls`, `get`, `get-many`, gated `stage-update`; `mock.py` is a simulated camera for tests ([07](07-usb-protocol.md)) | `HW` with `--live`; `--mock` and `--dry-run` produce no hardware evidence | Only with `--live` |
| [`tools/grdscript/`](../../tools/grdscript/README.md) | `lint`, `explain` and `build` for `SCRIPT.MG1` card scripts; lint runs the firmware's own parser under Unicorn and refuses dump, install, power-off and power-key constructs ([08](08-card-scripts-and-service-terminal.md)) | `EMU`; its generated macros are untested on a camera | No |
| [`tools/grdlook/`](../../tools/grdlook/README.md) | Mac photo looks and tone-curve design: `extract-curves` reads the five tone tables from the payload, `design-curve`, `apply`, `compare`, B&W tone prototype and its verifier ([06](06-image-pipeline-and-colour.md)) | Tables `STATIC`; native chain `EMU` (port writes, not pixels); looks are approximations | No |
| [`tools/grdui/`](../../tools/grdui/PLAYGROUND.md) | Runs the firmware's own GUI in the emulator; browser playground (`tools/check.py playground` or `python -m tools.grdui.playground_web`) | `EMU` | No |
| [`tools/grdpatch/`](../../tools/grdpatch/) | Declarative patch spec to `DO_NOT_FLASH_*` test images and the card-module loader assembly; never flashes | `STATIC` | No |
| [`tools/spare_test/`](../../tools/spare_test/) | Judges a tone test from files on a card (`identify`, `compare-curve`, `compare-same`, `compare-dump`); reads and prints only; thresholds were tuned on synthetic images | Measurement on camera-written files; verdicts are `INFERRED` | No |
| [`tools/check.py`](../../tools/check.py), [`tools/verify_saved_evidence.py`](../../tools/verify_saved_evidence.py) | `check.py` is the entry for `test`, `test-all`, `verify`, `playground`, `list`; `verify_saved_evidence.py` runs the 13 retained-evidence checks without replaying firmware ([VERIFYING.md](../../tools/VERIFYING.md)). `check.py test` does not run `tools/koma/tests`; those run with `run_tests.py` | Checks that saved reports are self-consistent; not camera evidence | No |
| [`tools/komaset/`](../../tools/komaset/README.md) | `komaset.py` converts the 4 KB settings block (`PARAM.CUR`/`PARAM.DAT`) to and from text, diffs and merges; `bridge/komabridge.py` is the local USB HTTP bridge; `web/koma-setup.html` is the Koma Setup page | CLI: `STATIC` on files; bridge `--real`: `HW`; `--mock`: simulated | Bridge with `--real` only |
| [`tools/komacam/`](../../tools/komacam/README.md) | GR DIGITAL as a webcam: MJPEG stream, virtual camera or frame dump; a client of komabridge | `HW` with `--spawn real`; mock/emulator spawns are simulated | Only with `--spawn real` |

Other helpers in `tools/` (emulator runners `emulate_*.py` and `probe_*.py`, `grd_lifecycle.py`, `grd_verify.py`, Ghidra export scripts, the CA-1 pulser sketch) are described in [tools/README.md](../../tools/README.md).

### Static analysis and the ELF wrapper

Ghidra and REA work on `firmware/derived/grd240.elf`, a container made by `tools/make_elf.py`: ELF32 big-endian ARM with `.text` `0x1c000..0x1a8880` (R-X), `.data` `0x1a8880..0x235968` (RW) and `.bss` `0x235968..0x35fdec`. It is not flashable or runnable. REA does not take raw big-endian ARM, so every REA result is "REA via the derived ELF wrapper" with language `ARM:BE:32:v4t` set by override, never an analysis of the vendor file. Runtime address = payload offset + `0x1c000`. `STATIC` results from the map carry error bars:

- 387 decoded blocks (42,952 B) lie outside any function; they hold about 4.4% of direct `bl` call sites (1,748 of 39,529; the call count is from the map-validation pass and was not re-derived from the retained tables). "X is never called" and "no path reaches a flash write" are therefore not safe negatives.
- At least 144 register-indirect call sites go through `_call_via_rN` thunks (`0x157ac8..0x157ad6`); 181 table-pointed entries are not functions; 41-51 KB of likely code is not in the map. Five USB handlers (`RETR` `0xcc434`, `-NLST` `0xcc970`, `-VER` `0xccb5c`, `-INFO` `0xccf14`, `-ABOR` `0xcd7c8`) are not function starts in the main map.
- False non-return: `0x6e91c` is a six-byte getter (`ldr; ldrb; bx lr`) reading byte `0x1bef92`. The first database marked it non-returning, which truncated callers and hid valid shutdown code (`0x32bec..0x32c04`); native execution disproved it. The repaired project clears 174 direct-call overrides (604 newly listed instructions, 50 caller bodies changed); 332 call-return overrides to other unproven roots remain. Firmware bytes are unchanged by the repair; inherited annotations need their own checks.

## Open questions

- The bit-banged serial device at `0x2cfdc`: what it is, and whether it slows key handling on the camera.
- A producer for the header-image semaphore `0x2a2f4` in the model, so menu renders show the stock artwork.
- Real cache behaviour on the camera (only one module class, whose patched sites first run after install, has been seen to work).
- A capture that completes in the model: the shot path stops after develop; JPEG encode and file write are not reached.
- Which display-sync and decoder completions the model needs to run shutdown to the end.
- A USB bus model and a multi-session tether scenario, so that session-to-session faults like the LCD mutex leak can be reproduced off the camera.
- Whether the camera's CPU matches any of the allowlisted Unicorn cores for FIQ-mask behaviour.

## Evidence

- [tools/koma/cli.py](../../tools/koma/cli.py), [tools/koma/features/__init__.py](../../tools/koma/features/__init__.py), [tools/koma/features/telemetry.py](../../tools/koma/features/telemetry.py): commands, default set, `CAMERA_OPTS` (unused), `telemetry.cp15` default
- [tools/koma/README.md](../../tools/koma/README.md), [tools/koma/loader.py](../../tools/koma/loader.py): command table, `make-loader`
- [analysis/sdhc/bundle/build.py](../../analysis/sdhc/bundle/build.py), [analysis/sdhc/bundle/README.md](../../analysis/sdhc/bundle/README.md): koma04w build (widefs, static resident, `cp15=off`)
- [analysis/sdhc/driver/build.py](../../analysis/sdhc/driver/build.py): generation 2 Koma firmware image builder
- [tools/CPU_MODELS.md](../../tools/CPU_MODELS.md), [tools/PERIPHERALS.md](../../tools/PERIPHERALS.md), [analysis/arm-cpu-profiles/report.json](../../analysis/arm-cpu-profiles/report.json): CPU allowlist, FIQ difference, 3,131,753 / 1,167,353 controls
- [analysis/lifecycle-integration/marker-native.json](../../analysis/lifecycle-integration/marker-native.json), [analysis/lifecycle-integration/stock-rescue.json](../../analysis/lifecycle-integration/stock-rescue.json), [analysis/captured-monitor-emulation/](../../analysis/captured-monitor-emulation/): captured-Monitor harness results
- [analysis/emu-writers/README.md](../../analysis/emu-writers/README.md): EXIF/MakerNote/DNG writers
- [analysis/emu-update-replay/README.md](../../analysis/emu-update-replay/README.md): update replay
- [analysis/map-validation/](../../analysis/map-validation/), [analysis/getter-call-repair/README.md](../../analysis/getter-call-repair/README.md): map quality, `0x6e91c` repair
- [analysis/hw-tests/tether-debug/FIX-1.4.md](../../analysis/hw-tests/tether-debug/FIX-1.4.md), [analysis/tether-feature/README.md](../../analysis/tether-feature/README.md): `0xa201` timer, USB bus not modelled
- [analysis/stall/ROOT-CAUSE.md](../../analysis/stall/ROOT-CAUSE.md), [analysis/hw-tests/RUNBOOK-2026-11-08.md](../../analysis/hw-tests/RUNBOOK-2026-11-08.md): second-session stall and its fix on hardware
- [analysis/sdhc/widefs/ROOT-NLST-64GB.md](../../analysis/sdhc/widefs/ROOT-NLST-64GB.md): 64 GB FAT32 root listing
- [tools/README.md](../../tools/README.md), [tools/VERIFYING.md](../../tools/VERIFYING.md): tool inventory and verifiers
