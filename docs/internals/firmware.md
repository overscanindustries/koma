# Firmware internals

How the Ricoh GR DIGITAL (2005) is built, how it boots, how its firmware file is laid out, and how the 2.40 application is organised. Addresses are 2.40 application runtime addresses unless stated. Addresses below `0x1c000` belong to the Monitor (the small boot program in flash block 0).

Related: [settings-and-image.md](settings-and-image.md), [usb.md](usb.md), [writing-features.md](writing-features.md), [addresses.md](addresses.md).

## Hardware

| Part | What is known |
|---|---|
| Main SoC | Ricoh "GR ENGINE". ARMv4T big-endian, mostly Thumb. RTOS is Nucleus PLUS 1.11.17. Driver modules `sdl_cc1`, `cc2`, `ven`, `jpg`, `rsz`, `dma`, `mem`, `aud`, `tmr` |
| CPU1 | Separate microcontroller for power, clock, ADC (battery, temperature, strobe, backlight), hard keys, zoom and reboot. Reached over SPI with a per-byte handshake on GPIO 60. Its firmware is not in the image |
| Flash | Samsung OneNAND256 (KFG5616), 32 MiB |
| RAM | Size unknown, 64 MB likely. Image SDRAM starts at `0x08000000` |
| Sensor | Sony 1/1.8" CCD, 3340 x 2486 total pixels |
| USB | Renesas M66592-family controller at `0x10200000` |
| SD | `/ATA1` file layer over a Toshiba TMIO-style host at `0x12044000`. Stock 2.40 never sends CMD8, so SDHC and SDXC cards fail to initialise and a 2 GB SD card works. Koma 2 adds SD 2.0, SDHC and SDXC init |
| LCD | 2.5" TFT. OSD is 360 x 240 at 4 bits per pixel |

Module code runs in SVC mode. Self-modifying code needs no cache maintenance. `MRC p15` hangs the core, and the firmware contains no CP15 instruction.

### Flash layout

OneNAND blocks are 64 KiB (128 sectors of 512 bytes). There are 512 blocks.

| Block | Holds |
|---|---|
| `0`, sectors `0..0x7d` | Monitor window, `0xfc00` bytes (Monitor 1.00 payload is 63,660 bytes) |
| `0`, sector `0x7e` | Monitor info record (512-byte container header) |
| `1..0xc` | Replacement table (scanned in `1..0xb`) and spare pool (allocated from `0xc` down) |
| `0xd..0x3b` | Main application body, at most 47 blocks (`0x2f0000` bytes). Stock 2.40 (2,201,960 bytes) lands on `0xd..0x2e` |
| `0x3c` | Main info record at sector `0x7e`. Marks the main image valid. Erased first and programmed last by an update |
| `0x3d` | Adjustment store (`0x2000` bytes used). Byte 0 is `0xAA` or `0x55` |
| `0x3e..0x52` | Extended data (21 blocks). Its directory lists `ricoh.res` (79,872 bytes) and nine more resources |
| `0x53` | No reader or writer found |
| `0x54..0x1ff` | `/IROM` internal file store, about 26 MB. Holds `/IROM/GRMOD.BIN` |

Firmware updates never touch `/IROM`, so the package survives them. A stock type-2 update writes only blocks `0x3c` and `0xd` upward, unless a block fails.

### Memory map

| Range | Meaning |
|---|---|
| `0x0..0xfbff` | Monitor window. The Monitor owns the real exception vectors |
| `0x1b000..0x1bfff` | 4 KB settings block (read by `0x4947c`, saved by `0x4949c`) |
| `0x1c000..0x1a8880` | `.text`. Load address `0x1c000`, reset handler `0x1c054` |
| `0x1a8880..0x235968` | `.data`, executes in place |
| `0x235968..0x35fdec` | `.bss`, zeroed by reset |
| `0x35fdec..0x366de8` | Stacks (SVC `0x3ffc`, IRQ `0x1000`, FIQ `0x2000` bytes) |
| `0x36adec..0x3dadec` | `KernPool`, the kernel heap (`0x70000` bytes) |
| `0x3dadec..0x422800` | Unused by stock code. Koma's resident block sits at `0x3dae00..0x3e4f78` |
| `0x422800` | Application RAM ceiling: `load + size - 1` must be below it |
| `0x08000000..` | Image SDRAM. DMA buffers must be at or above it |
| `0x08010000..` | Adjustment store copy (ADJ version at `0x08010002/3`) |
| `0x08422800`, `0x0842f280` | OSD pages (stride `0xb4`) |
| `0x12200000` area | Monitor SRAM: banked stacks (IRQ `0x12201fc0`, FIQ `0x12201ec0`, abort `0x12201eb0`, undefined `0x12201ea0`, SVC `0x12201e90`) and the handoff pointer `0x1220000c` |

The application never sets the abort and undefined stack pointers, so its fault handlers use the Monitor's.

### Peripheral bases

| Base | Device |
|---|---|
| `0x10200000` | USB controller. Pipes: 1 = `0x81` bulk IN, 2 = `0x02` bulk OUT, 7 = `0x83` interrupt IN |
| `0x12000000` | Interrupt controller: `+0x08` mask (1 = masked), `+0x10` pending. Lines: 0 timer, 2 display frame, 3 CC1 frame, 5 JPEG, 11 GPIO, 12 resize. IRQ entry `0x1c214` |
| `0x12014000` | CC1 capture timing (`0x1201401c` status) |
| `0x12018000` | CC2 colour stage (see [addresses.md](addresses.md)) |
| `0x12030000` | GPIO (data `+0x20` pins 0..31, `+0x24` pins 32..63) |
| `0x12044000` | SD host |

Timers raise INTC line 0. GPIO 60 is the CPU1 handshake in the application.

### Rules for code that runs on the camera

Never execute a CP15 instruction, and allocate with `0x15f024(0x2bf81c, &ptr, size, 0)` (wrapper `0x6128a` is fatal on an empty pool). The rest is in [writing-features.md](writing-features.md).

## Boot chain

The Monitor runs at every power-on and decides what to start. The normal path loads the application from flash after checking only a header. The card-load and updater paths were run only in an emulator, never on a camera. Do not rely on them as recovery.

```
power-on
  Monitor 1.00, reset 0x154: 63 two-sector loads copy sectors 0..125 (0xfc00 bytes) to RAM
  init stacks, UART, interrupts, timer, OneNAND (F221h = 0x40e0)
  boot control 0xb7c reads main info (block 0x3c), GPIO60, GPIO50, /ATA1/MG1MODE, origin word
    valid header, GPIO60 low       -> flash load 0x156c (no body checksum), jump 0x1c000
    invalid header                 -> card RAM loader, jump 0x1c000
    GPIO60 high, GPIO50 high       -> MG1MODE = VERUP_INMONITOR: flash updater 0x50ec
                                      otherwise RAM loader, then valid-header flash load
    GPIO60 high + GPIO50 low, origin mismatch, or no card after a wait -> console 0x9d0
  application reset 0x1c054 -> MainCtrl 0x281e8 -> 0x1e7ac -> update gate 0x1eb22 -> 0x281ee
```

The normal flash path runs on every power-on of the test cameras with stock, Koma 1 and Koma 2 images.

With invalid info and no card, the Monitor waits about 5000 timer ticks, then enters a UART console at 38,400 baud (pins unknown). The camera looks dead.

### Header validation is the only check

The validator (`0xb58` -> `0x1920`) accepts a container with the right magic, the right `+0x1f0` tail marker and type 2 or 3. Neither the Monitor nor the application recomputes the body checksum on the flash path. A modified body under an intact header boots. Koma relies on this.

None of these triggers the card fallback: a damaged body under a valid header, a flash read failure under a valid header, or a crash after the application starts. An erased or unreadable info block counts as invalid.

### Handoff

| Item | Value |
|---|---|
| Return LR | `0x000003c4` (saved at `0x235964`) |
| Four words at LR-16 | `0, 0, 0, 0x1220000c`, copied to `0x1c044..0x1c050` |
| Pointer at `0x080003c0` | `0x1220000c` |
| CPSR | `0xd3` (SVC, interrupts masked) |
| Lock state | All 512 blocks locked |

### Monitor 1.00

Container type 1, format 1, version `0100`, date `20050806`, load and entry 0, payload 63,660 bytes (`0xf8ac`), checksum `0x0196d9b0`. The Monitor has its own SD driver with no SDHC support, so Koma 2's SD changes do not reach it.

### Card loading and Monitor updates

Seen only in an emulator. The card RAM loader scans `/ATA1/QRFLECT0` to `QRFLECT9`, copies the payload to `0x08000000 + header.load`, validates, then jumps. It accepts types 2, 3 and 7. If `/ATA1/MG1MODE` holds `VERUP_INMONITOR`, `0x50ec` flashes instead and skips the full validator `0x3e7c`, so a stale-checksum file is programmed. An update interrupted after the info erase leaves the image invalid, so treat it as possibly unrecoverable.

## Firmware image and update file

### Package

Ricoh ships `gr-240.exe`, a self-extractor (bsdtar opens it). It holds two container files and a Windows tool that only stages files on the card. The camera flashes them itself.

| File | Size |
|---|---|
| `qrflect3` (application, type 2) | 2,202,472 bytes |
| `qrflect7` (adjustment script, type 5) | 31,914 bytes |

The digit in a file name is not the type. The scanner opens `QRFLECT0..9` by name and dispatches on the type byte. See [settings-and-image.md](settings-and-image.md) for the `qrflect7` script.

### Container header

512 bytes, big-endian. Payload follows at `+0x200`.

| Offset | Size | Field | `qrflect3` | `qrflect7` |
|---|---|---|---|---|
| `+0x00` | 16 | Magic `"R_MG1_REFLECTOR\0"`, each byte XOR `0x80` | same | same |
| `+0x10` | 1 | Type | `02` | `05` |
| `+0x11` | 1 | Format (type 2 needs 1, type 5 needs 3) | `01` | `03` |
| `+0x12` | 2 | Version, BCD | `0240` | `0001` |
| `+0x14`, `+0x16` | 2+2 | Target-version bounds, zero = no restriction | `0` | `0` |
| `+0x18` | 4 | Date, BCD `yyyymmdd`, then 4 time bytes at `+0x1c` | `20071024` | `20071024` |
| `+0x20` | 4 | Load address | `0x1c000` | `0` |
| `+0x24` | 4 | Payload length | `0x219968` | `0x7aaa` |
| `+0x28` | 4 | Entry address | `0x1c000` | `0` |
| `+0x2c` | 4 | File size (header + payload), compared with the real size | `0x219b68` | `0x7caa` |
| `+0x30` | 4 | Weighted checksum | `0x346f08ed` | `0x0065c71d` |
| `+0x34..0x1ef` | | `0xff` filler | | |
| `+0x1f0` | 16 | Tail `"R_MG1_UPDATED!!!"`, each byte XOR `0x80` | present | present |

The update scanner ignores the `+0x1f0` tail, but the boot-time validity test checks it. An image with a wrong tail would flash, then read back as invalid.

### Header types

| Type | Destination window | Role |
|---|---|---|
| 1 | `[0, 0xfc00)` | Monitor. No file exists. Keep off the card |
| 2 | `[0x1c000, 0x406800)` | Application. Accepted by the scanner, RAM loader and Monitor updater |
| 3 | as type 2 | Second application type, no known file |
| 5 | none | Adjustment text script |
| 6 | `[0, 0x1000000)` | Extended data, no GR file |
| 7 | as type 2 | RAM-loader carrier. The stock scanner rejects it |

Types 0, 4, 7 and above count as present but not accepted, and one such file rejects the whole card.

### Checksum

```
checksum = sum over i of payload[i] * (2 + (load_address + i) % 6)   mod 2^32
```

It covers the payload only and is stored big-endian at `+0x30`. The weight cycles 2 to 7 with its phase set by the load address (phase 0 for `qrflect7`). The firmware's copies are `0x62cf0` and the streaming check `0x275cc`. It is an integrity check, not authentication. A flipped bit makes the stock updater show an error screen and boot normally with no flash write.

### Repacking an image

- Start from the official `qrflect3` and keep its size. A size change hits the `0x2f0000` refusal after the info erase, and added bytes would land in `.bss`.
- Change only payload bytes and the 4 checksum bytes at `+0x30`. The rest of the header must match the official file.
- Keep `+0x14`/`+0x16` zero and the `+0x1f0` tail intact.
- Put the image on an SDSC card (2 GB) as `QRFLECT3` alone. With `QRFLECT7` also present, `0xd6dec` runs the store script after the image, even when the image failed.
- Never ship `QUICKVER.UP` or an edited type-5 file. The first is an unvalidated flasher and the second reaches the adjustment store.

The flashing procedure and its risks are in [writing-features.md](writing-features.md).

### Application updater

Runs on `MainCtrl` inside `0x1e7ac`. The gate (`0x1eb22`) opens on UP + TELE + dial value 1, 10 or 9, or on invalid main info. `0x278d4` scans `QRFLECT0..9` and checks each by type, and one bad file rejects the card. `0xd6dec` flashes each accepted file. The update ends with power-off (`0x32b74`).

A type-2 file needs format 1, a passing version gate (`0x27648`), `+0x20 >= 0x1c000`, `+0x20 + +0x24 - 1 < 0x422800`, a real file size equal to `+0x2c`, and the streaming checksum equal to `+0x30`. The version gate rejects only when `+0x14` or `+0x16` is non-zero and the installed version is outside them, so reinstalls and downgrades are accepted. A file `/ATA1/QUICKVER.UP` runs a separate flasher (`0x26e04`) with no visible validation. Never ship it.

### Flash write order

| Step | Routine | Action |
|---|---|---|
| 1 | `0x63c68` | Erase main-info block `0x3c`. No cancel exists after this point |
| 2 | `0x63b88` | Walk logical blocks from `0xd` (at most 47), map, erase (`0x63598`), program (`0x636dc`) |
| 3 | per block | Read-back compare while streaming. Not a second checksum |
| 4 | `0x63c80` | Program the info record at block `0x3c`, sector `0x7e` |

An interruption between steps 1 and 4 leaves the image invalid. `0x63b88` refuses a body over `0x2f0000` bytes, but only after the info block is erased, so a size change bricks the image. Keep the official size, change only payload bytes and the 4 checksum bytes at `+0x30`, and put the image on the card as `QRFLECT3` alone.

### Version markers

| Place | Value |
|---|---|
| Main info `+0x12` | `0240`, copied from the container at flash time |
| USB `-VER` | `200 9.17 02.40`, from the info header |
| MakerNote tag 2 | `Rev%4.4x` from the format string at `0x112578`: `Rev0240` stock, `Rev024M` Koma 1, `240K2` Koma 2 |
| Version screen (DOWN + TELE, `0x5c95c`) | CPU1, CPU2 (the application, from info `+0x12`), MON, ADJ (`0x08010002/3`) |

Koma images keep `+0x12` at `0240`, so the MakerNote tag is how to tell builds apart.

## Application structure

Reset (`0x1c054`) enters C init (`0x1cf58`), which creates `KernPool` and the tasks. `MainCtrl` (`0x281e8`) calls `0x1e7ac` once (settings load, boot keys, update gate), then `0x281ee` and `0x282cc` (`post_startup`). Koma's hook sits at `0x281ee`.

### Tasks

Priority is lower-runs-first. The firmware's task table is at `0x1a8acc` (0x1c bytes per task).

| Task | Function | Priority | Role |
|---|---|---|---|
| MainCtrl | `0x281e8` | 90 | Startup, update gate, requests, shutdown |
| tFileTask | `0x849c4` | 120 | File system, script-gate check |
| tOpeMain | `0x8a3b8` | 80 | Keys, dial, UI messages |
| tCameraMain | `0x34038` | 82 | Shooting-state commands |
| RecRL1TASK | `0x95f98` | 70 | Half-press recording, runs the full-press capture |
| tDcx | `0x53734` | 80 | USB command task |
| tAdjust | `0x54020` | 110 | Adjustment engine and service terminal |
| tScript | `0x65220` | 80 | Script interpreter |

Sixteen tasks exist in all. `tScript` and `tOpeMain` share priority 80, so script key events arrive only when the script blocks.

### Messages and queues

Queue entries are `0x8c` bytes: a 12-byte header (length at `+0`, routing at `+2` and `+4`, message id at `+6`) and up to 128 bytes of payload.

| Primitive | Address |
|---|---|
| Queue send `(queue, msg, size, timeout)` | `0x5d7a8` |
| Queue receive `(queue, buf, 0x8c, timeout)`, -1 on timeout | `0x5d878` |
| Semaphore wait / release | `0x5d978` / `0x5d9a4` |
| Sleep / tick getter | `0x288f6` / `0x5df20` |
| Event-group set / wait | `0x15ec8c` / `0x15ecbc` |
| Generic sender (type at `+6`, payload at `+12`) | `0x5d4b8` |

| Id range | Use |
|---|---|
| `0x1xxx` | File task and startup sync (`0x1102` to the file task, `0x1202` to MainCtrl) |
| `0x21xx`, `0x31xx` | `tCameraMain` commands. `0x3101` is shutdown |
| `0x2201` | Shutdown acknowledgement to MainCtrl |
| `0x5xxx` | Requests to MainCtrl |
| `0x6101` | Terminal input to `tAdjust` |
| `0x71xx` | `tOpeMain` UI messages. `0x7103` is a key or dial event `{code, action}` |
| `0x720d` | Power-off request (POWER press, long PLAY hold) |

Queue handles: `0x2c9e28`/`0x2c9e2c` (`tOpeMain` input), `0x1b96cc` (`tCameraMain`), `0x1a8ad8` (MainCtrl requests), `0x1bd394` (terminal input).

### Dial and state

`0x51158` maps CPU1 MODE bits to a dial position. With injection on, `0x51228` returns the virtual position `0x2b9d39`. `0x4fd1e` maps position to application state.

| Dial position | 1 | 2 | 3 | 4 | 6 | 7 | 8 | other |
|---|---|---|---|---|---|---|---|---|
| State | 3 | 4 | 5 | 2 | 10 | 12 | 13 | 6 (playback) |

Record states are 2, 3, 4, 5, 10, 12 and 13. `0x4fd1e` forces state 9 when `0x23a888 == 1` and `0x23a88c == 0`. State 9 ignores RL1 and RL2, and on stock 2.40 in ORIGINAL USB mode `-CCTL` produced no files at any dial position.

### Key path and injection

The key scan fills `0x2b9d84` from CPU1 status bytes (byte and mask per key in table `0x18108c`, 36-byte records, record index = event code) or GPIO. `0x50eb0` overlays the injected array `0x2b9dd8[code]`. A PLAY hold over 300 polls posts `0x720d`.

Events reach `tOpeMain` as `0x7103 {code, action}` from the scanner, script commands and USB `-CCTL keyset`. The injection sink is `0x51740`:

| Code | Behaviour |
|---|---|
| 0 | Commit the virtual dial. Emit `(0,4)` if changed |
| 1 | Set the virtual dial to the real dial |
| 2, 42 | Injection off, clear injected keys, virtual dial back to 9 |
| other | Injection on, `0x2b9dd8[code] = action` (no upper bound) |

Actions: 0 stop overriding, 1 hold, 3 tap, 2 ignored. The first injected key turns injection on, which reads the dial as 9 and the state as 6, so key sequences start with `keyset 0 1`.

Key codes are 6 POWER, 7 PLAY, 11 UP, 12 DOWN, 13 RIGHT, 14 LEFT, 15 TELE, 16 WIDE, 20 TRASH, 22 DISP, 23 ENTER (likely MENU/OK), 26 RL1 (half press), 27 RL2 (full press), 36 DPUSH and 41 HEADUP. Codes 8 and 19 cannot be injected. The physical function of each code is inferred. Codes 28 to 35 are dial-wheel levels and cannot be injected. Injecting code 6 (POWER) powers the camera off.

### Power-off

POWER press -> `0x513c0` -> `0x52152` -> `0x720d` -> `0x32b74` -> `0x32b8c(1)`. The body waits on two flags, saves the settings block (`0x4949c(0x1b000, 0x1000)`), shows the shutdown image, waits for `0x2201` from `tCameraMain` (lens retract), saves settings again, sends CPU1 specials `0xf6` and `0xfd`, calls `0x4fcaa(2 or 1)`, then `sleep(1000)` forever.

### Capture path

RL1 (code 26) then RL2 (code 27). A full press arrives at `0x973e4` inside `0x9733a`, then `0x947e4` polls the CC1 status, then `0x9670c` waits on semaphore `[0x1f4dd8]`. JPEG completion is INTC line 5 (ISR `0x2398c`). Resize completion is line 12 (ISR `0x23a30`). Develop hooks are `0x96350`, `0x96436`, `0xafd20`, `0xafd3e` and `0x9665e`.

### Where hooks go

Run code once after startup at `0x281ee`. Everything else is in [writing-features.md](writing-features.md).

## Service entry points

These key combinations act when held at power-on.

| Keys | Effect |
|---|---|
| UP + TELE, dial 1/10/9 | Update gate |
| RIGHT + TELE, dial 1/10/9 | Service terminal and USB console (INFERRED by analogy with the update gate, never tried on hardware) |
| DOWN + TELE | Version screen |
| LEFT + TELE | Counters screen |
| DOWN + WIDE | Writable mass storage for one boot |
| UP + WIDE | Factory flow on script start, can reset settings and reboot |

A text script runs at power-on from `/ATA1/SCRIPT.MG1`, or `/IROM/SCRIPT.MG1` if the card open fails. The file task sets gate `0x1a90e4`, and `MainCtrl` reads it once (`0x2821e`) and calls `0x65108`, which starts `tScript`. A high level on pin 8 (mode byte `0x1b9866`) closes the gate. No key combination is needed. A script needs CRLF on every line including the last, `#` comments only at line start, hex as `0x`, labels with `@` (`@main` is the start) and a file under 65,536 bytes. Scripts have no read command and no feedback.

## Firmware versions

Ricoh shipped ten releases between 2005-11 and 2007-11. Seven are recovered (2.20, 2.21 and 2.30 are missing). Addresses in this document are 2.40. Earlier versions are relocated.

| Version | Date | Payload bytes |
|---|---|---|
| 1.08 | 2005-11-16 | 2,163,704 |
| 1.09 | 2006-02-10 | 2,164,232 |
| 2.01 | 2006-03-24 | 2,175,768 |
| 2.02 | 2006-04-27 | 2,175,840 |
| 2.10 | 2006-07-28 | 2,179,812 |
| 2.31 | 2007-04-20 | 2,187,168 |
| 2.40 | 2007-11-22 | 2,201,960 |

The settings block has no format version. IMG SET value 6 is SEPIA in 2.31 and B&W (TE) in 2.40. The GR DIGITAL shares its ARM code base with the Caplio R3 and the 500G wide, not with the GR DIGITAL II and later, which use Renesas M32R.
