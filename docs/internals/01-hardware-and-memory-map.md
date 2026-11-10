# 01. Hardware and memory map

The original GR DIGITAL (2005) pairs a main SoC (Ricoh "GR ENGINE", ARMv4T big-endian, Nucleus PLUS RTOS) that runs the application with a separate microcontroller, CPU1, that owns power, keys, zoom and the RTC. Firmware lives in a 32 MiB Samsung OneNAND split into Monitor, bad-block pool, main image, adjustment store, extended data and the `/IROM` file store. The application loads at `0x1c000` and runs code, data, stacks and the kernel pool below `0x422800`; image buffers start at `0x08000000` and peripherals sit at `0x10xxxxxx`-`0x12xxxxxx`. Most register meanings come from how the firmware uses them; only OneNAND (datasheet) and USB (M66592 register map) have external documentation. State as of 2026-11-08.

Tags: `HW`, `EMU`, `STATIC`, `INFERRED`, defined in [features](evidence-ledger.md). `HW` rows are 2.40 unless stated. Addresses are application runtime addresses unless stated. Related: [02 boot chain](02-boot-chain-and-monitor.md), [03 image format](03-firmware-image-and-update-format.md), [04 application](04-application-architecture.md), [05 settings and store](05-settings-and-adjustment-store.md), [07 USB](07-usb-protocol.md), [10 emulator](10-emulator-and-tooling.md), [address index](address-index.md).

## Key addresses

| Address | Meaning | Tag |
|---|---|---|
| `0x00000000..0xfbff` | Monitor window in RAM | `EMU` |
| `0x1b000..0x1bfff` | 4 KB user-settings block | `STATIC` |
| `0x1c000` | Application load address; reset handler `0x1c054` | `STATIC` |
| `0x1c000..0x1a8880` | `.text` | `STATIC` |
| `0x1a8880..0x235968` | `.data` (executes in place) | `STATIC` |
| `0x235968..0x35fdec` | `.bss` | `STATIC` |
| `0x36adec..0x3dadec` | Nucleus `KernPool` (`0x70000` B) | `STATIC` |
| `0x3dae00` | Koma package static resident block (above `KernPool`) | `HW` |
| `0x422800` | Application RAM ceiling used by the validator | `STATIC` |
| `0x08000000` | Image SDRAM base | `STATIC` |
| `0x08010000` | Adjustment store image in SDRAM | `STATIC` |
| `0x10200000` | USB controller (M66592 family) | `STATIC` |
| `0x10800000` | OneNAND BootRAM/DataRAM window; registers at `0x1081e000` | `STATIC` |
| `0x12000000` | Interrupt controller | `STATIC` |
| `0x12010000` | System timer; `+0x04` counts on hardware | `HW 2026-11-06 main` |
| `0x12018000` | Image engine CC2 (colour stage) | `HW 2026-11-06 main` |
| `0x12030000` | GPIO | `STATIC` |
| `0x12044000` | SD host controller (TMIO family) | `STATIC`, family `INFERRED` |
| Block `0x3c` / `0x3d` | Main-info header / adjustment store | `HW` (capture) |

## Flash layout

OneNAND blocks are 64 KiB = 128 sectors of 512 B (64 pages x 2 sectors). 512 x 64 x 2 x 512 B = 32 MiB = 256 Mb; one geometry entry describes 65,536 sectors. `STATIC`

| Block(s) | Holds | Tag |
|---|---|---|
| `0`, sectors `0..0x7d` | Monitor window, `0xfc00` B (126 sectors); Monitor 1.00 payload 63,660 B (`0xf8ac`); spare tag `MON` on the first sector | `HW` (capture) |
| `0`, sector `0x7e` | Monitor info: 512-byte header, type 1, format 1, version `0100`, date `20050806`, checksum `0x0196d9b0`; spare tag `INF` | `HW` (capture) |
| `0`, first 1 KB | Copied by the chip into 1 KB BootRAM at power-up, then always locked | `STATIC` (datasheet) |
| `1..0xc` | Replacement table (scanned in `1..0xb`, spare tag `REP`) and spare pool (allocated from `0xc` down) | `STATIC`; block 1 `HW` (erased, `REP`) |
| `0xd..0x3b` | Main firmware body, logical blocks through the replacement table; at most 47 blocks = `0x2f0000` B | `STATIC` |
| `0xd..0x2e` | Where the stock 2,201,960-byte image lands with no bad blocks (34 blocks); byte-identical to official 2.40 | `HW` (capture) |
| `0x3c` | Main info, 512-byte header at sector `0x7e`; marks the main image valid; erased first and programmed last by an update | `STATIC`, order `EMU` |
| `0x3d` | Adjustment store (one block); byte 0 `0xAA`, stored sum `0x0118adb3` verified | `HW` (capture, main body) |
| `0x3e..0x52` | Extended data, 21 blocks (1,376,256 B); directory at relative `0x146e00` (in block `0x52`) lists `ricoh.res` (79,872 B at `0x200`) and nine more resources | `HW` (capture) |
| `0x53` | No reader or writer found | `STATIC` |
| `0x54..0x1ff` | `/IROM` file store: sectors `0x2a00..0xffff` = 28,049,408 B (Ricoh: "26 MB"). Holds the Koma package `/IROM/GRMOD.BIN` on both bodies | `STATIC`; package `HW` |
| `0x55`, `0x66` | Written on every modelled boot (ten programs in `0x66`, one erase in `0x55`) | `EMU` |
| `0x66..0x1ff` | Not captured; the only dump ends after sector `0x32ff` | `HW` |

Firmware flashes do not touch `/IROM`: a `/IROM/GRMOD.BIN` survives a firmware update. `STATIC`; internal memory unchanged across the spare's official update `HW`.

## CPU address space

| Range | Meaning | Tag |
|---|---|---|
| `0x00000000..0xfbff` | Monitor window, loaded by Monitor reset in 63 two-sector loads; low RAM is assumed to alias `0x08000000` | `EMU`, alias `INFERRED` |
| `0x000003c4` | Monitor return address handed to the application; saved at `0x235964` | `EMU` |
| `0x00000080..0x9c` | Vector pointer block written by application reset: undef `0x174984`, IRQ `0x1c214`, FIQ `0x1c4ec`, reset `0x1c054`. The Monitor owns the real vectors `0x00..0x1c` | `STATIC` |
| `0x1b000..0x1bfff` | Settings block, read into RAM by `0x4947c` from a flash-backed store; not part of the payload | `STATIC` |
| `0x1c044..0x1c050` | Copy of the four Monitor handoff words | `EMU` |
| `0x1c000..0x1a8880` | `.text`, 1,624,192 B | `STATIC` |
| `0x1a8880..0x235968` | `.data`, 577,768 B; the copy loop is skipped because source equals destination | `STATIC` |
| `0x235968..0x35fdec` | `.bss`, 1,221,764 B, zeroed by reset | `STATIC` |
| `0x35fdec..0x366de8` | Stacks: SVC `0x3ffc`, IRQ `0x1000`, FIQ `0x2000`. SP_und and SP_abt are never set by the application | `STATIC` |
| `0x366dec..0x36adec` | A further `0x4000` recorded at `0x1a8a1c`/`0x1a8a20`; user unknown | `STATIC` |
| `0x36adec..0x3dadec` | `KernPool`, `0x70000` B; pool object `0x2bf81c`, allocator `0x15f024`, free `0x15f084` | `STATIC` |
| `0x3c7d64..0x3d1984` | Where early module resident blocks landed (inside `KernPool`) | `HW 2026-11-06 main` |
| `0x0038f1a8` | Stack pointer of the task running the card loader | `HW 2026-11-06 main` |
| `0x3dadec..0x422800` | Gap of `0x47a14` B; no stock access found | `STATIC` (bounded search), unused `INFERRED` |
| `0x3dae00..0x3e4f78` | koma04w static resident block (41,336 B), canary `0x3e4f78..0x3e4f88`. Boots with the canary intact on both bodies | `HW 2026-11-07 spare`, `HW 2026-11-08 main` |
| `0x422800` | Validator ceiling: `load + size - 1 < 0x422800` (`0x276d8`, `0x27738`) | `STATIC` |
| `0x08000000..` | Image SDRAM; SDL DMA asserts require buffers at or above it. `0x0a839d00`/`0x0c000000` constants suggest 64 MB | `STATIC`, size `INFERRED` |
| `0x08010000..` | Calibration/adjustment store in SDRAM, rewritten by `qrflect7` (2,241 `V`/`W` pairs, offsets `0x0002..0x1cd4`); ADJ version at `0x08010002/3` | `STATIC` |
| `0x08422800`, `0x0842f280` | OSD pages, 360 x 240 visible, 4 bpp, stride `0xb4` | `STATIC` |
| `0x08b84000`, `0x08c00000` | Fixed buffers used by the updater (`0x279d8`) and by the `/ATA1/REFLECT3` test routine (`0x58350`) | `STATIC` |
| `0x12200000` area | Monitor SRAM: stacks IRQ `0x12201fc0`, FIQ `0x12201ec0`, abort `0x12201eb0`, undef `0x12201ea0`, SVC `0x12201e90`; handoff pointer `0x1220000c`; SD-mounted flag `0x1220003c` | `EMU`; SRAM `INFERRED` |

### Two views of memory

The Monitor runs in `[0, 0xfc00)` with its stacks in SRAM at `0x1220xxxx`. It copies the application to `0x1c000` (flash load `0x156c`) or stages a card image at `0x08000000 + header.load` (RAM loader). The application then uses the run view above. Monitor boot control compares the origin word `0x080000a4` with BootRAM's reset pointer `0x10800080`; a mismatch is a software return and goes to the Monitor console. See [02](02-boot-chain-and-monitor.md#handoff-block).

## Peripheral bases

| Base | Device and registers | Tag |
|---|---|---|
| `0x10200000` | USB controller, Renesas M66592 family. Details below | `STATIC`, exact part `INFERRED` |
| `0x10800000` | OneNAND BootRAM (1 KB, reset pointer `0x10800080`); DataRAM at `0x10800400` | `STATIC` |
| `0x1081e000..` | OneNAND registers: manufacturer `e000`, device ID `e002`, version `e004`, block `e200`, FPA `e20e`, buffer `e400`, command `e440`, config `e442` (`F221h`), status `e480`, interrupt `e482`, start block `e498`, ECC `ffe00..` | `STATIC` (driver vs datasheet) |
| `0x11000004` | SDRAM-capacity register an assert reads; value unknown | `STATIC` |
| `0x12000000` | Interrupt controller: `+0x04` write 1 = soft reset path, `+0x08` mask (1 = masked), `+0x0c`, `+0x10` pending. Lines: 0 system timer, 2 display frame, 3 CC1 frame, 5 JPEG, 11 GPIO aggregate, 12 resize | `STATIC` |
| `0x12000004`, `0x12000024` | Monitor reset polls: `+0x24` bit 0 must read clear and `+0x04` bit 0 set to take the OneNAND-copy branch | `EMU` |
| `0x12010000` | System timer: `+0x00` reload (reset writes `0xfd20`), `+0x10` mask (reset writes `0xe`), `+0x14` pending, write-0-to-clear. Channel 2: reload `+0x08`, mask bit 2, pending bit 2 | `STATIC` |
| `0x12010004` | Free-running counter: advanced across a busy loop on two of four logged boots, read 0 on the other two | `HW 2026-11-06 main` |
| `0x1200c000` | Display/DMA: OSD base `0x1200c080`, stride/4 `0x1200c088`, enable `0x1200c00c` bit 2; DMA `0x1200c150` (written 7), `0x1200c16c` (LNIR/PXIR9 fields) | `STATIC` |
| `0x12014000` | CC1 / capture timing: `0x1201401c` status (shutter wait `0x947e4` on bit 31, then bits 23:16), `0x12014024` `vcycle`, `0x12014040`/`44` frame ack and enable, `0x1201407c` (fields unknown), `0x12014088/8c` clip levels | `STATIC` |
| `0x12018000` | CC2 colour stage: chroma gain `0x12018004`, luma weights `0x1201800c`, mono `0x12018038`, WB gains `0x12018110/114`, colour matrix `0x12018104/108/10c`, chroma coring `0x120180d8`. Full map in [address index](address-index.md#isp-registers) | `STATIC`; liveness `HW` |
| `0x1201c000` | JPEG block: control `+0x00`, mask `+0x1c`, status `+0x20` | `STATIC` |
| `0x12020000` | VEN (video/OSD): enable `+0x04` bit 1, `0x12020010` reset value, mask `+0x14`, status `+0x18`, palette `0x12020100` (Y, signed Cb, signed Cr) | `STATIC` |
| `0x12024000`, `0x12024200` | USART: LCD panel control and audio codec; IRQ `0x23ef4`, send `0x240fc`. Carries no pixels | `STATIC` |
| `0x12028000` | Monitor console UART; init writes register sets for 9600/19200/38400/57600/115200 | `EMU` |
| `0x12030000` | GPIO: data `+0x20` (pins 0..31), `+0x24` (32..63); pending `+0x2c`, `+0x30`; masks `+0x34`, `+0x38`; polarity `+0x1c` | `STATIC` |
| `0x12034000` | Resize engine: control `+0x00`, status `+0x04` | `STATIC` |
| `0x12044000` | SD host: 16-bit registers at a 4-byte stride, matching the Toshiba TMIO map; only `0x26420` writes the command register; busy flag is bit 30 (`CMD_BUSY`). The Monitor has its own copy of the driver | `STATIC`, family `INFERRED` |

### Interrupts and timers

| Fact | Tag |
|---|---|
| IRQ entry `0x1c214` dispatches bits 1..17, then bit 0, of `pending & ~mask`; reset writes mask `0x3fffe`/`0x3ffff`; `0x1c208` writes INTC `+0x04 = 1` (soft reset path) | `STATIC` |
| Timer channel 0 raises INTC line 0 (handler `0x1f36c`). Channel 2 (reload `+0x08`, mask bit 2 at `+0x10`, write-0-to-clear pending bit 2 at `+0x14`, cancel `0x1f740`) is used by the native camera sequence | `STATIC` |
| With channel 2 modelled, the camera sequence takes 2,088 channel-2 IRQs and then waits at `0x122dd8` for event mask `8022`; `CameraMain` completion stays pending | `EMU` |
| GPIO pending/mask registers `+0x2c/+0x30/+0x34/+0x38` feed INTC line 11 through dispatcher `0x1c3dc` -> `0x2598a`. A supplied GPIO43 pending bit (`+0x30` bit 11) reaches callback `0x38d1c`, which changes a counter from 1033 to 1031; masked and no-event controls leave it at 1033 | `STATIC`, `EMU` |
| JPEG completion: IRQ call site `0x1c304` -> `0x2398c` reads status `0x1201c020` and activates HISR `camJPGhr` (`0x239c0`); status bit 1 -> `0x5df54`, error bits `0x7c` -> `0x5df6c` | `STATIC` |
| DMA driver asserts `sdl_dma_drv.c:2353` (PXIR9 must be 3 or 7 when LNIR is 7 or 15) on register `0x1200c16c`. Latched values `0x300`/`0x700` let the decoder start at `0x163db0` and wait on semaphore `0x2bfccc` (timeout 2000); neither is a measured reset value | `STATIC`, `EMU` |

The emulator's peripheral models and their limits are in [10](10-emulator-and-tooling.md).

### USB controller

| Fact | Tag |
|---|---|
| Offsets used from `0x10200000`: `0x00, 02, 04, 06, 0a, 10, 1e, 20, 22, 24, 2a, 30, 32, 36, 38, 3a, 40, 46, 48, 4a, 50, 54, 56, 58, 5a, 5c, 5e, 60, 64, 66, 68, 6a, 6c, 70..7c`; every one is a named register in the Linux M66592 header. The M66592 manual itself was not read | `STATIC` |
| `0x60100`: `PINCFG \|= 0x8100` (LDRV, BIGEND: external-chip bits) and `SYSCFG \|= 0x180` (HSE, ATCKM) | `STATIC` |
| `0x5f730`: `CFIFOSEL` MBW_16 `0x400`; FRDY bit 13, BVAL bit 15; 16-bit PIO tail writes | `STATIC` |
| `0x5fda8`: Configured state test `INTSTS0 & 0x70 == 0x30` | `STATIC` |
| `0x606c8`: SETUP dispatcher, reads request registers `0x54..0x5a` and routes vendor requests | `STATIC` |
| `0x60530`: clock-down suspend sequence; `0x60628`: `TESTMODE` | `STATIC` |
| Pipes: 1 = `0x81` bulk IN, 2 = `0x02` bulk OUT, 7 = `0x83` interrupt IN | `STATIC` |

The command protocol, descriptor modes and enumeration results are in [07](07-usb-protocol.md).

### Image engine liveness

Results from register writes in both develop passes on the main body, judged from the JPEG.

| Register | Write | Result | Tag |
|---|---|---|---|
| `0x12018004` chroma gain | 0 | True grey frame (chroma 0.0) | `HW 2026-11-06 main` |
| `0x12018104` MatR | byte 3 = `0x3F` | Frame turns red; matrix is live | `HW 2026-11-06 main` |
| `0x12018108`/`0x1201810c` MatG/MatB | byte 3 = `0x3F` | Live; drive G and B output from the R input | `HW 2026-11-06 main` |
| `0x120180d8` chroma coring (`pUVoffset`) | low field 31; high field 31 | Live; each field cores its channel (Cb low, Cr high) by 31 after the tone curve. A dead zone, not an offset | `HW 2026-11-06 main` |
| `0x12018104` MatR | `BF000000`; `20909000`; MatR + MatB `0000003F` | Sign-magnitude (`BF` pushes cyan); entries add; byte 0 reads blue | `HW 2026-11-06 main` |
| `0x12018004` chroma gain | all four bytes x1.30 | Uniform x1.30 chroma | `HW 2026-11-06 main` |
| `0x12018028/2c/30` IQ hue matrix, clip, offset | several values, also with `0x12018100` bit 6 | No change: dead stage | `HW 2026-11-06 main` |

Byte roles, scale and clamp of the matrix are in [06 image pipeline](06-image-pipeline-and-colour.md); register list in the [address index](address-index.md#isp-registers).

## GPIO pins

Pin roles come from code. The 11 pins whose direction was checked match `+0x0c`/`+0x10` as direction registers: inputs 3, 4, 5, 8, 50, 51, 57; outputs 11, 61, 62, 63. `STATIC`, register meaning `INFERRED`

| Pin | Role | Tag |
|---|---|---|
| 60 | Sampled by the Monitor at `0x2524` (boot path select). In the application, the per-byte handshake line of the CPU1 link (callback `0xca60c`, installed by `0xca548`). Probably low on a normal boot | `STATIC`, level `INFERRED` |
| 50 | Monitor input named `PB_LOW`; not shown to be the Playback button. The application compares it at `0x1e454` and discards the result | `STATIC` |
| 57 | OneNAND INT line (`0x64524` polls `+0x24` bit 25) | `INFERRED` |
| 43 | Pending bit `+0x30` bit 11 (INTC line 11) reaches callback `0x38d1c`; physical function unknown | `STATIC`, `EMU` |
| 11 | Request line to CPU1; held low by the Monitor (`0x244c`), raised by the application once the SPI handle exists | `STATIC` |
| 61, 62, 63 | Outputs; Monitor helpers `0x25b4`/`0x258c` shift words on them | `STATIC` |
| 8, 5, 4 | Inputs read by Monitor helpers `0x255c`, `0x261c`, `0x2634`. Pin 8 high closes the script gate; its USB meaning is unverified | `STATIC`, meaning `INFERRED` |
| 3, 51 | Inputs by direction-register pattern | `INFERRED` |
| `0x38` (56) | VBUS sense (bank 2 bit 24). Service-mode input at boot (RIGHT+TELE plus pin low routes the console to USB). Input of a CA-1 pulse decoder (init `0x1319e8`, classifier `0x131a78`) derived from code and run in the emulator; never driven on hardware | VBUS `INFERRED`; service mode `EMU`; decoder `STATIC`, `EMU` |
| `0x30`, `0x31` | RL1/RL2 shutter inputs (`HKEY_RL1`/`HKEY_RL2` HISRs, `0x50c94`) | `STATIC` |

## Keys and the CPU1 link

Hard keys are read by CPU1 (`REQ_GET_HKEY_STATUS`), not by the SoC directly. `STATIC`

| Fact | Tag |
|---|---|
| Key table `0x18108c`: 36-byte records (type, byte, mask, polarity, name `OPE_HKEY_NAME_*`); record index = event code. 6 POWER, 7 PLAY, 8 USB, 15 TELE, 16 WIDE, 19 MENU, 23 ENTER, 26 RL1, 27 RL2; codes 0, 1, 2, 42 are control codes. Physical labels unconfirmed | `STATIC` |
| Key submit `0x324e6` posts message `0x7103` | `STATIC` |
| Update-entry keys: UP (index `0xb`) and TELE (`0xf`) plus dial value 1, 10 or 9 (`0x4fbcc(2,0)` -> `0x32540(1)`) | `STATIC` |
| `-CCTL diaset,N` accepts dial values 0..8; `diaset,9` returns `451` | `HW` (spare) |
| CPU1 exchange `0xca524` installs `0xca60c` as the GPIO60 callback and advances one SPI transfer per interrupt; request table `0x181676`; 55 indexed `0xca76a` call sites | `STATIC` |
| `REQ_SET_CFG_BOOT` (index `0x10`, register `0xf114`) has no caller among those 55 sites (indirect use not excluded). `REQ_SET_CFG_PORT_*` have no known mapping to GPIO60 | `STATIC` |
| `REQ_GET_BOOT_TRIGGER` (register `0xf201`, index `0x3f` at `0x51a64`) is requested after the Monitor handoff. Of 256 supplied reply bytes only bits 3, 5 and 2 matter (decoded 8, 1, 2); the reply seeds active-key slot 7 (PLAY) | `EMU` |

## Components

No chip marking has been read and no body has been opened. The Ricoh board photo shows no legible `SAMSUNG`, `KFG`, SoC or RAM marking (a sticker covers the largest package). "Matched" means firmware or published figures fit a part.

| Component | What is known | Tag |
|---|---|---|
| Main SoC | Ricoh "GR ENGINE", image processing a version-up of the GX8's. ARMv4T big-endian, Thumb-heavy (37,422 Thumb vs 2,107 ARM direct calls). Nucleus PLUS 1.11.17 (string `0x17517f`). Driver modules `sdl_cc1`, `cc2`, `ven`, `jpg`, `rsz`, `dma`, `mem`, `aud`, `tmr`. Part number unknown; likely an in-house ASIC | `STATIC`, ASIC `INFERRED` |
| Core behaviour | Runs module code in SVC mode (CPSR `0x80000013`). Executing `MRC p15` hangs the core at boot. Self-modifying code is coherent: a CPU store that rewrote an immediate ran the new value, and freshly written code at a new address executed. A tight Thumb loop ran about 25 M instructions/s if a tick is 1 ms | `HW 2026-11-06 main`; speed `INFERRED` |
| CPU1 | Separate MCU: power, RTC, ADC (battery, temperature, strobe, backlight), hard keys, zoom, DAC, reboot, DC-DC config. Reached over SPI plus GPIO handshake (`0xca524`, request table `0x181676`). Firmware not in the image | `STATIC` |
| Flash | Samsung OneNAND256: `KFG5616Q1A` (1.8 V, device ID `0x14`) or `KFG5616D1A/U1A` (ID `0x15`). Driver matches the A-die datasheet (KFG5616x1A Ver 1.1, 2005-08-12): register map, commands, status bits, timing (`tBERS1` 2/3 ms, `tPGM2` 220/750 µs). M-die datasheet not obtained, so M-die is not excluded | `STATIC` (driver vs datasheet) |
| RAM | Type and size unknown; strings assert `SDRAM_BASE_ADDR` and `sdramCapa` | `STATIC`, 64 MB `INFERRED` |
| Sensor | Sony ICX476 series 1/1.8", 2.22 µm, 3340 x 2486 total, 3288 x 2472 effective (matched on Ricoh's figures). ICX456 (2/3", 2.7 µm) rejected on those figures. Firmware line is 3320 px (strides `0xcf8`, `0x19f0`, `0x1038`), CCD window 3320 x 2475, print size 3264 x 2448, horizontal crop `0x24`; this geometry fits both ICX476 and ICX456, so it does not discriminate | `INFERRED` (match), `STATIC` (geometry) |
| AFE and TG | Separate TG and AFE up to 36 MHz, band-pass CDS; parts unknown | `STATIC` (Ricoh text) |
| AF | External AF element; firmware also has CCD AF and a CPU1 `AF_TEMP` channel | `STATIC` (Ricoh text, strings) |
| Oscillator | Metal can reads about `27.000` on the board photo (low confidence) | `INFERRED` |
| USB | Renesas M66592 family, external at `0x10200000` | `STATIC` |
| LCD | 2.5" a-Si TFT, about 210 k dots, per-unit VCOM over USART; OSD 360 x 240, 4 bpp | `STATIC` |
| SD | `/ATA1` file layer over the TMIO-family host at `0x12044000`. Stock 2.40 initialises SD 1.x/2.0 standard-capacity cards only: it never sends CMD8, so an SDHC/SDXC card fails init and the drive is marked failed. A 2 GB SD 2.0 card works on stock and Koma firmware. CMD8 probe on the spare: command register `0x0408`, arg `0x1AA` returns R7 `0x1AA`; ACMD41 `0x40100000` returns OCR `0x80FF8000` (CCS 0). Generation 2 Koma firmware initialises SD 2.0, SDHC and SDXC cards (CMD8, ACMD41 HCS, CCS latch, CSD v2); 64 GB cards work with the widefs module on both bodies | Stock `STATIC`; probe `HW 2026-11-07 spare`; generation 2 `HW` |
| Audio | Codec on USART2; AVI audio is mu-law mono 8 kHz, video 320 x 240 MJPG at 30 fps (33,333 µs per frame) | `HW` (files) |
| Power | Sub PCB; battery DB-60 Li-ion; voltages read through CPU1 | `STATIC` |
| RTC | Through CPU1 (`REQ_GET_RTC_XSTP`); Ricoh states the date survives about one week without batteries. The spare body's clock reads a 2005-01-01 date. It kept running across a 1-minute battery pull and the firmware treats it as user-set, so a flat backup cell is not established | Ricoh `STATIC`; spare `HW 2026-11-07`; cause `INFERRED` |
| Lens | 6 elements in 5 groups, 2 aspheric, element 1 low-dispersion; DC-motor front group, pulse-motor focus group; 7-blade 12-step iris with ND between elements 3 and 4; `FOCUS PI` home-sensor strings | `STATIC` (Ricoh text, strings) |
| Board | Main PCB plus power sub PCB, eight-layer all-layer IVH, BGA both sides | `STATIC` (Ricoh text) |

### Version-screen labels

The non-update version screen (`0x5c95c`) shows `JC1`, `CPU1`, `CPU2`, `MON`, `ADJ`, `EXT`, `SH`, `ST`.

| Label | Meaning | Tag |
|---|---|---|
| CPU2 | The application (payload at `0x1c000`); version from main-info `+0x12` | `STATIC` |
| CPU1 | The MCU, via `REQ_GET_VERSION` | `STATIC` |
| MON | Monitor version from a RAM table (`0x1a8c08/0c`, filled via `0x1f09c`); the application contains no Monitor code | `STATIC` |
| ADJ | Adjustment-store version at `0x08010002/3` | `STATIC` |
| JC1, EXT, SH, ST | Not decoded | `STATIC` |

## OneNAND driver

### Identification and commands

`0x6462c` calls `0x64614`, which reads `0x1081e000`, `e002`, `e004` and compares only the device ID against `+0x02` of each 0x1c-byte entry of the geometry table `0x177978`. Manufacturer and version are read and ignored. The result is cached at `0x1bec28`; no match returns `0xf000000`. `STATIC`

| Fact | Tag |
|---|---|
| Commands issued: Load `00h`, Load spare `13h`, Program spare `1Ah`, Unlock `23h`, Lock `2Ah`, Program `80h`, Erase `94h`. Never lock-tight (`2Ch`) or unlock-all | `STATIC` |
| Status bits used: Lock bit 14, Error bit 10 (driver masks `0x4000`/`0x400`). Program/erase on a locked block returns `0x5400`/`0x4C00` | `STATIC` (datasheet p.46) |
| `F24Dh` (reserved on A-die) is written with the block number before each lock/unlock, a scheme other dies use for continuous lock: the driver was not written only for this die | `STATIC`, `INFERRED` |
| `F221h` config: `0x41e0` during the early cold copy, `0x40e0` after Monitor init (IOBE set). With IOBE clear (`0x40c0`) the first flash read stalls | `EMU` |

### Geometry table `0x177978`

| Offset | Value | Meaning | Tag |
|---|---|---|---|
| `+0x00` | 1 | Valid entry / terminator (0 ends) | `STATIC` |
| `+0x02` | `0x14` / `0x15` | Device ID | `STATIC` |
| `+0x06` | `0x200` | Block-count bound (512) | `STATIC` |
| `+0x08` | `0x40` | Pages per block | `STATIC` |
| `+0x0a` | 2 | Sectors per page | `STATIC` |
| `+0x04`, `+0x0c..0x12` | `0xe`; 1, `0xa`, 2, `0x810` | No reader | `STATIC` |
| `+0x14..0x1a` | 50, 350, 2000, 50 | No reader; not timeouts (the only timeout is computed from the system timer in `0x64524`). 2000 = typical block erase; 350 = typical `tPGM2` before datasheet rev 1.0 | `STATIC`, origin `INFERRED` |

Sector size is hard-coded at 512; the block register is masked with `0x1ff`.

### Lock state and block ownership

The datasheet locks every block after reset, individually; lock-tight clears only on reset; block 0 is guaranteed valid; at least 502 of 512 blocks are valid. `STATIC` (datasheet)

The application unlocks one target per operation (`0x647c8`, `0x64d24`, `0x64f04`) and relocks it. The Monitor's own updater also unlocks and relocks its targets. Neither lock-tights, and the same driver can unlock block 0. Lock protection is therefore no defence against patched or runaway code. No physical lock state has been measured. `STATIC`

| Blocks | Writers | Tag |
|---|---|---|
| `0` | `0x639e2`/`0x63ac0` (type 1: Monitor and Monitor info) | `STATIC` |
| `0xd..0x3b`, `0x3c` | `0x63b88`, `0x63c68`, `0x63c80` (type 2) | `STATIC` |
| `0x3d` | `0x63d9e` (erase plus `0x2000`-byte program) | `STATIC` |
| `0x3e..0x52` | `0x63e9c`, `0x63f94` (type 6) | `STATIC` |
| `1..0xc` | `0x6325c`, `0x631a4`, `0x63598`, `0x63620`, `0x636dc` | `STATIC` |
| `0x54..0x1ff` | `0x670d0`/`0x67134` (guard `0x678cc`); self-test `0x64228` | `STATIC` |
| `1..0x1ff` | Near-whole-device erase `0x28194` (from opcode interpreter `0xd17d4`); restore `0x674bc` skips block 0 | `STATIC` |

A stock type-2 update issues no program or erase below block `0xd` unless a block fails. `EMU`

### Bad blocks, replacement table, ECC

| Fact | Tag |
|---|---|
| `0x64b1c` checks only the first page's sector-0 spare word against `0xFFFF` and returns `0xd000000` for a marked block. A marker on the second page (allowed by the datasheet) is missed | `STATIC` |
| ECC status `0x6490c` -> `0x645c0` flags uncorrectable errors only (`0x2000000`); single-bit correction is left to the chip | `STATIC` |
| Logical-to-physical mapping `0x63364`, at most 10 entries (`0x62e38`); pool allocation from `0xc` down (`0x62fbe`); table scan `0x63030` (blocks `1..0xb`), write `0x631a4` | `STATIC` |
| Erase failure `0x63598` records type 2; program failure `0x636dc` records type 3 and copies earlier sectors via `0x63620`; verify `0x638e8`. An exhausted pool returns -1 with info already erased | `STATIC` |
| The Monitor honours remaps of main and info (logical `0xd` and `0x3c` resolved to physical `0xc`) | `EMU` |
| The application read path never allocates a replacement: no read-time self-healing | `STATIC` |

### Dump record format

`0x67434` reads sectors `0..0xffff` through `0x64d80` (physical, no table translation) and writes 516 bytes per sector to `/ATA1/IROM.DAT`: 512 main bytes, 3 packed metadata bytes (from `0x6470e`) and 1 unchanged byte. It is not raw spare or ECC. A full file is 33,816,576 B. Record offset = (block x 128 + sector) x 516; block `0xd` starts at record `0x680`. `STATIC`

The one real dump (main body, stock `SCRIPT.MG1` at a Playback start) is 6,736,896 B = 13,056 records, ending after sector `0x32ff`. `HW`. An uncorrectable read and a failed card write each reproduce exactly that length, so the file cannot say which stopped it. `EMU`

## Address bases

| Artifact | Rule | Example | Tag |
|---|---|---|---|
| Vendor `qrflect3` (2,202,472 B with 512-byte header) | runtime = file offset + `0x1be00` | `0x5cab8` at file `0x40cb8` | `STATIC` |
| Header-stripped payload `firmware/derived/grd240-armbe.bin` (2,201,960 B) | runtime = payload offset + `0x1c000` | `0x62cf0` -> `0x46cf0` | `STATIC` |
| ELF wrapper `grd240.elf` for Ghidra (`ARM:BE:32:v4t`) | `.text` at file `0x1000`; `.data` at file `0x18d880` | `0x62cf0` -> `0x47cf0`; `0x1a8880` -> `0x18d880` | `STATIC` |
| Monitor payload (`monitor100.elf` wraps it unchanged at 0, `tools/make_monitor_elf.py`) | Load and entry 0; offsets equal addresses | `0x2524` | `STATIC` |
| `IROM.DAT` dump | (block x 128 + sector) x 516 | | `STATIC` |
| Main image in flash | From physical block `0xd`, 128 sectors per block; info at block `0x3c` sector `0x7e` | | `HW` |

Three bases (`+0x1be00`, `+0x1c000`, ELF `+0x1000`) appear in tools and notes; check which one a number uses before patching. A numeric address query against the ELF can silently fall back to a file-offset match (`0x54020` once returned `FUN_0006ef88`); confirm the selected function by name.

## Rules for code that runs on the camera

| Rule | Why | Tag |
|---|---|---|
| Never execute a CP15 instruction | `MRC p15` hangs the core. The firmware contains none (four aligned matches are all in `.data`) | `HW 2026-11-06 main` |
| Patching `.text` at runtime works without cache maintenance | Self-modifying code test passed; a menu module patched sites that first ran after the write | `HW 2026-11-06 main` |
| Budget `KernPool` in kilobytes | Largest free block: 76,800 B with only a small probe module, 6,656 B with an earlier, smaller body, 3,904 B with the 29.8 KB Koma body resident (KernPool-resident builds). With the package resident at `0x3dae00` instead, 23,296 B. The emulator's figures are not representative | `HW 2026-11-06 main`; static resident `HW 2026-11-07 spare` |
| Allocate through the kernel | `0x15f024(0x2bf81c, &ptr, size, 0)`; the wrapper `0x6128a` is fatal on an empty pool | `STATIC` |
| Do not grow the flash image | Added bytes land in `.bss` (zeroed at `0x1c0d8`); `tools/grd_firmware.py pack` refuses a size change; size refusal `0x2f0000` comes after the info erase | `STATIC` |
| Use known caves only | Dcfs test group `0x56f80..0x58800`. Generation 1 uses `0x57f74..0x58197` (loader at `0x57f78`, 344 B; hook `0x281ee`); generation 2 also uses `0x57274..0x57ac0` and extends the loader tail to `0x58104`. Zero padding is not free space | Layout `STATIC`; runs `HW` |

## Trusting the call graph

| Use | Verdict |
|---|---|
| Byte facts through the ELF wrapper | Safe: all 2,201,960 payload bytes match |
| Locating a function from a table or call target | Useful: 25/25 script handlers, the checksum `0x62cf0`, 21/26 USB handlers are function starts |
| Counts and sizes | About 4% of decoded code and 4-5% more of the image (41-51 KB of likely code) lie outside the map. Unknown regions: 10,260 regions, 931,850 B, about 68% "other data" |
| "Never called" / "no path reaches flash write" | Not safe as a negative: 4.4% of `bl` sites sit in unowned code; 181 table-pointed entries are not functions; 144 `_call_via_rN` sites (`0x157ac8..0x157ad6`) plus three `mov lr,pc; bx rN` and one `ldr pc,[r0]` are indirect |
| `gap`-origin functions | Not safe: 533 functions, 510 without callers, about a third data |
| "Does not return" annotations | Suspect: getter `0x6e91c` was wrongly flagged and hid code after 174 call sites (see [04](04-application-architecture.md#non-return-annotations)); 9,535 function no-return flags and 332 other-root overrides remain unproven |

Five USB handlers (`RETR` `0xcc434`, `-NLST` `0xcc970`, `-VER` `0xccb5c`, `-INFO` `0xccf14`, `-ABOR` `0xcd7c8`) are not function starts in the main project. Decompiler spot checks: seven functions agreed with Capstone; three emulated functions matched 9/9, 9/9 and 12/12; the decompiler reports 3 failures in 8,792 functions. `STATIC`, `EMU`

## Hardware observations

| Observation | Tag |
|---|---|
| Main body `-VER` = `200 9.17 02.40`; enumerates as "GR DIGITAL" `05ca:2218` (ORIGINAL mode), no volume | `HW` |
| Captured flash: main payload equals official 2.40; Monitor 1.00 window complete and checksum-valid; block `0x3d` sum matches | `HW` (main body) |
| Spare body before its update: JPEGs tagged `Rev0108` (18/18); `-VER` `01.08` | `HW` (spare body) |
| Spare body updated 1.08 -> 2.40 with the official pair; internal memory unchanged; Camera Info tag 5 then read `2007-09` (store commit landed) | `HW` (spare body) |
| The generation 1 image (generation 1: loader plus `Rev024M` marker) installed on both bodies; JPEGs carry `Rev024M` | `HW 2026-11-06 spare and main` |
| Official 2.40 `QRFLECT3` flashed over generation 1 on the spare: normal boot, JPEG tag `Rev0240` | `HW 2026-11-07 spare` |
| Generation 2 Koma firmware build 2b (tag `240K2`, image sha256 `75425a70…9680`) on both bodies, with package koma04w (build `747F061F`) in `/IROM` | `HW 2026-11-08 both` |
| SD CMD8 answered on the spare; 64 GB cards mounted at full capacity under generation 2 + widefs | `HW 2026-11-07` |
| Module code runs in SVC mode; SMC coherent; `MRC p15` hangs; pool headroom as above; `0x12010004` counts | `HW 2026-11-06 main` |
| Colour matrix and chroma coring live, IQ stage dead | `HW 2026-11-06 main` |

## Open questions

- SoC part, foundry, and whether a mask ROM runs before the Monitor.
- RAM type and size; the low-RAM alias; whether anything else uses `0x3dadec..0x422800`.
- Fitted ARM core, cache configuration and FIQ behaviour. Coherent SMC was seen for one pattern only; why `MRC p15` hangs (undefined-instruction trap with no abort stack, or a core without CP15) is unknown.
- Why `0x12010004` read 0 on two of four boots.
- CPU1 part and firmware; every CPU1 reply in the emulator is fabricated.
- OneNAND variant and die; physical lock state after Monitor boot.
- Blocks `0x66..0x1ff`, raw spare/ECC, factory-bad map. Why the dump stopped at sector `0x32ff`: a dump on the spare body would split read error from card-write error, and would also give the spare's Monitor and extended data for comparison.
- Meaning of block `0x53` and geometry fields `+0x04`, `+0x0c..+0x1a`.
- The Monitor's own SD driver and FAT path have never run on hardware; the Monitor's copy has no SDHC support added by generation 2.
- Physical roles and levels of GPIO 60, 50, 57, 43, 8, `0x38`; only 57 and 11 have support beyond code reading, and 60 and 50 gate Monitor paths.
- Real values of `0x1200c16c`, `0x12020010`, `0x11000004`, `0x1201401c`, `0x1201407c` (emulator values were chosen to pass asserts).
- Sensor marking and readout modes; LCD panel, audio codec, TG/AFE, PMIC, RTC parts.
- Byte roles b1 vs b2 of `MatR/G/B` (both read G).

## Evidence

- [../../analysis/monitor-recovery/capture-analysis/analysis.json](../../analysis/monitor-recovery/capture-analysis/analysis.json) (flash capture: Monitor window, main comparison, extended data, record format)
- [../../analysis/map-validation/](../../analysis/map-validation/) (`unknown-classification-summary.json`, `table-targets-not-in-map.json`, `callvia_sites.json`, `emu-check.json`)
- [../../analysis/koma-0.1/hw/](../../analysis/koma-0.1/hw/) (`KOMALOG-boot2.TXT`, `KOMALOG-v04-boot.TXT`, `KOMALOG-crash1.TXT`: SVC mode, SP, pool, SMC, CP15, timer counter) and [../../analysis/koma-0.1/telemetry.md](../../analysis/koma-0.1/telemetry.md)
- [../../analysis/colour-research/probe2/KOMALOG.TXT](../../analysis/colour-research/probe2/KOMALOG.TXT) (76,800 B pool, resident `0x3c7d64`)
- [../../analysis/koma-0.1/colourprobe.md](../../analysis/koma-0.1/colourprobe.md) (image engine liveness)
- [../../analysis/module-loader/README.md](../../analysis/module-loader/README.md) (cave, loader, hook)
- [../../analysis/sdhc/controller/README.md](../../analysis/sdhc/controller/README.md) (SD host, stock init)
- [../../analysis/sdhc/probe/README.md](../../analysis/sdhc/probe/README.md), [../../analysis/sdhc/probe/hw/SDPROBE-spare-2026-11-07.TXT](../../analysis/sdhc/probe/hw/SDPROBE-spare-2026-11-07.TXT) (CMD8 probe)
- [../../analysis/sdhc/driver/README.md](../../analysis/sdhc/driver/README.md) (generation 2 Koma firmware, build 2b, island usage)
- [../../analysis/sdhc/bundle/README.md](../../analysis/sdhc/bundle/README.md) (static resident block)
- [../../analysis/hw-tests/2026-11-07-spare.md](../../analysis/hw-tests/2026-11-07-spare.md) (64 GB, static resident, RTC battery pull), [../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md](../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md) (both bodies on generation 2 + koma04w)
- [../../archive/STATUS.md](../../archive/STATUS.md) section 1 (restore of official 2.40 on the spare)
- [../../analysis/usb-first-contact/README.md](../../analysis/usb-first-contact/README.md), [../../analysis/spare-usb/README.md](../../analysis/spare-usb/README.md) (USB IDs, `-VER`, `diaset`, spare JPEG tags, AVI format)
- [../../analysis/liveview/usb/README.md](../../analysis/liveview/usb/README.md) (USB controller use)
- [../../analysis/vbus-shutter/README.md](../../analysis/vbus-shutter/README.md) (pin `0x38`, CA-1 decoder)
- [../../analysis/secondary-camera-timer/verification-summary.json](../../analysis/secondary-camera-timer/verification-summary.json), [../../analysis/gpio43-irq-contract/verification-summary.json](../../analysis/gpio43-irq-contract/verification-summary.json)
- [../../analysis/resize-completion/](../../analysis/resize-completion/) (JPEG/DMA completion chain)
- [../../archive/research/hardware-components.md](../../archive/research/hardware-components.md) (datasheet comparison, components, sensor match)
- [../../tools/grdemu/periph.py](../../tools/grdemu/periph.py), [../../tools/secondary_camera_timer.py](../../tools/secondary_camera_timer.py), [../../tools/supplied_gpio_irq.py](../../tools/supplied_gpio_irq.py), [../../tools/make_monitor_elf.py](../../tools/make_monitor_elf.py), [../../tools/grd_firmware.py](../../tools/grd_firmware.py), [../../tools/koma/linker.py](../../tools/koma/linker.py)
