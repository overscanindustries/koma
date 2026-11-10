# 02. Boot chain and the Monitor

At reset the Monitor 1.00 bootloader (OneNAND block 0) reads the main-info header, two GPIO inputs and an optional card file, then either copies the application to RAM and jumps to `0x1c000`, loads a card image into RAM without touching flash, runs its own flash updater, or drops to a UART console. The normal path checks only the header, never the body checksum, which is why a modified image with a valid header boots. On hardware only the normal flash path has run, on stock, generation 1 and generation 2 Koma firmware on both bodies. Every other path is known from executing the captured Monitor in the emulator. State as of 2026-11-08.

Tags: `HW`, `EMU`, `STATIC`, `INFERRED`, defined in [features](evidence-ledger.md). `EMU` here means the original instructions ran with supplied reset, GPIO, SD and CPU1 inputs. Addresses below `0x1c000` are Monitor addresses. Related: [01 memory map](01-hardware-and-memory-map.md), [03 image format](03-firmware-image-and-update-format.md), [04 application](04-application-architecture.md), [08 card scripts and terminal](08-card-scripts-and-service-terminal.md), [09 recovery](09-recovery-and-failure-modes.md), [10 emulator](10-emulator-and-tooling.md), [address index](address-index.md#monitor-code).

## Key addresses

| Address | Meaning | Tag |
|---|---|---|
| `0x154` | Monitor reset target (also reached after a software return) | `EMU` |
| `0x1e8..0x1ef` | `MOV r0,#0xd3; MSR CPSR_c,r0` (SVC, IRQ/FIQ masked); also where a failed load jumps (`0x3b0` -> `0x1e8`) | `STATIC`, `EMU` |
| `0x24c` | Reset poll: `0x12000024` bit 0 clear, `0x12000004` bit 0 set selects the OneNAND copy | `EMU` |
| `0x244c` | Writes GPIO direction `+0x10 = 0xe000d700` (pin 60 stays input); GPIO11 low | `STATIC` |
| `0x2524`, `0x253c` | GPIO reads; `0x2524` samples GPIO60 | `STATIC` |
| `0xb7c` | Boot control: path selection | `EMU` |
| `0xb58` -> `0x1920`/`0x189c` | Main-info validator (magic, `+0x1f0` marker, type 2 or 3); `0x1748` pre-fills `0xff` | `STATIC` |
| `0x156c` | Flash-to-RAM application loader; -1 on read failure | `STATIC` |
| `0x1518` | Sets the jump address | `STATIC` |
| `0x2090` | Parses `/ATA1/MG1MODE` | `STATIC` |
| `0xa2c` -> `0x2394` -> `0x2194` -> `0x2138`/`0x1cb0` -> `0x19ac` | Card RAM loader: scan `QRFLECT0..9`, load, validate, weighted checksum | `EMU` |
| `0x6034`, `0x60d8`, `0x613c`, `0x6140`, `0x6118` | SD mount; file open, read, stat, close | `EMU` (supplied) |
| `0x50ec` | Monitor flash updater | `EMU` |
| `0x4e54` | Update worker; `0x4558` erase info, `0x4308` program main, `0x4588` program info, `0x4658` verify main | `EMU` |
| `0x3e7c` | Full pre-write validator, skipped when `0x4e54`'s 4th argument is 1 | `STATIC` |
| `0x8540` | Logical-to-physical block resolver | `EMU` |
| `0x24b0` | Writes the handoff pointer at `0x080003c0` | `EMU` |
| `0x2b70`, `0x2bf4` | Board services, run only on the GPIO60-low valid-header branch | `EMU` |
| `0x9d0` | Console entry | `EMU` |
| `0xae0` | No-card wait (about 5000 timer ticks) before the console | `EMU` |
| `0xdb4` | Console UART setup (`0x9600` = 38,400 baud) | `EMU` |
| `0x2db4`, `0xf0ec` | Console parser; 26-entry command table | `EMU` |
| `0x3c4`, `0x3d8`, `0x41c` | Saved-return callback (restores vectors, branches to `0x1e8`); Monitor IRQ vector | `EMU` |
| `0x080000a4` vs `0x10800080` | Origin word compared with BootRAM's reset pointer; a mismatch means software return | `STATIC` |
| `0x1c054` | Application reset handler | `STATIC` |
| `0x1c1d8` | Application return to Monitor | `STATIC`, `EMU` |
| `0x28a60` (Thumb `0x28a61`) | Stock UART/INTC preparation wrapper before the return | `STATIC`, `EMU` |
| `0x1eb22` | Application update gate (in `0x1e7ac` on `MainCtrl`); updater body `0x5d18c` | `STATIC` |
| `0x281ee` | Koma loader hook: `bl 0x282cc` replaced by `bl 0x57f78` | `HW` |

## The chain

```
power-on / reset
  |  first fetch: unknown (BootRAM holds block 0 sectors 0-1; a mask ROM is not excluded)
  v
Monitor 1.00 (block 0), reset 0x154: 63 two-sector loads copy sectors 0..125 (0xfc00 B)
  |  stacks in SRAM 0x1220xxxx; UART, INTC, timer, OneNAND init; F221h = 0x40e0
  v
boot control 0xb7c: reads main info (block 0x3c), GPIO60, GPIO50, /ATA1/MG1MODE, origin word
  |-- valid header, GPIO60 low ----------> flash load 0x156c (no body checksum) -> jump 0x1c000
  |-- invalid header, either GPIO60 -----> card RAM loader -> RAM image -> jump 0x1c000
  |     (except GPIO60 high + GPIO50 low, which goes to the console)
  |-- GPIO60 high, GPIO50 high ----------> MG1MODE = VERUP_INMONITOR: flash updater 0x50ec
  |                                        otherwise RAM loader, then valid-header flash load
  |-- GPIO60 high + GPIO50 low, origin mismatch, or no card after wait --> console 0x9d0
  v
application 0x1c054 -> RTOS -> MainCtrl 0x281e8 -> 0x1e7ac -> update gate 0x1eb22
  -> 0x281ee (Koma firmware: loader 0x57f78 reads GRMOD.BIN, then returns to stock flow)
```

The normal flash path runs on every power-on of both bodies, with stock, generation 1 and generation 2 images. `HW`. All other edges are `EMU`. The loader and package are described in [11](11-modification-playbook.md).

### Established on hardware

- The normal path: Monitor flash load of the main image and start of the application. Stock 2.40, generation 1 (`Rev024M`, 2026-11-06) and generation 2 Koma firmware, build 2b (`240K2`) all boot normally on both bodies. `HW`
- The application updater (not a Monitor path): official 1.08 -> 2.40 on the spare; generation 1 and generation 2 images on both bodies; official 2.40 `QRFLECT3` over generation 1 on the spare (normal boot, JPEG tag `Rev0240`, 2026-11-07). `HW`
- A Playback-start boot on the main body reached the application (stock `SCRIPT.MG1` dump). `HW`
- The Monitor 1.00 bytes, header and checksum (captured from the main body). `HW`

Not observed on any camera: a Monitor RAM boot, the Monitor console, a Monitor reinstall, the Monitor flash updater, any GPIO60-high branch, or a restore from generation 2 to stock (that one is `EMU` only).

## Monitor 1.00

| Fact | Tag |
|---|---|
| Captured from the main body by the stock `SCRIPT.MG1` flash dump | `HW` |
| Info header: type 1, format 1, version `0100`, date `20050806`, load/entry 0, payload 63,660 B (`0xf8ac`), container 64,172 B, checksum `0x0196d9b0` | `HW` (bytes) |
| Payload SHA-256 `801a31d96df422acec9904d580eea95726e9752a3e47b568ce435abf1af86171`; the `0xfc00` window SHA-256 `e6b3286f8007a9fbf778dcc5845440f3dc69ecda37fe1c8a675115a7e55ad0c8` | `HW` (bytes) |
| Payload passes its weighted checksum, both in Python and through its own routine `0x19ac`; this is integrity, not authentication | `STATIC`, `EMU` |
| Window `0xfc00` B, non-erased, starts with ARM vector words; first sector spare tag `MON`, the other 125 `ffffff` | `HW` |
| Static analysis wraps the payload unchanged at address 0 (`monitor100.elf`, `tools/make_monitor_elf.py`) | `STATIC` |
| The 2.40 application embeds no Monitor code and only holds its version number. No official package carries a type-1 (Monitor) file | `STATIC` |
| The Monitor uses block 0 only; blocks `1..0xc` are replacement table and pool | `STATIC` |
| The Monitor has its own copy of the SD driver, separate from the application's; generation 2's SDHC changes do not reach it | `STATIC` |

## Cold reset to handoff

The model seeds OneNAND from the captured 516-byte records (only three spare metadata bytes per sector are real), exposes the first 1 KB of block 0 at address 0 and runs the original reset code. `EMU`

1. The reset code issues 63 two-sector loads covering sectors 0..125 and copies the Monitor window into RAM. A warm-SDRAM start reaches the same Monitor entry without the loads. `EMU`
2. Reset blocks at `0x24c` until `0x12000024` bit 0 reads clear and `0x12000004` bit 0 reads set: a board acknowledgement, supplied in the model. `EMU`
3. Banked stacks go to SRAM: IRQ `0x12201fc0`, FIQ `0x12201ec0`, abort `0x12201eb0`, undefined `0x12201ea0`, SVC `0x12201e90`. The application never sets SP_und or SP_abt, so its fault handlers rely on these. `EMU`, reliance `INFERRED`
4. The Monitor's timer interrupt updates SRAM counters; board services `0x2b70` run on the GPIO60-low branch. `EMU`
5. OneNAND config is `0x41e0` during the early copy and `0x40e0` (IOBE set) after init. No unlock, lock, erase or program happens on this path. `EMU`
6. From cold reset the Monitor reads main info, walks the replacement table, loads all 2,201,960 payload bytes byte-exact, and the application reaches `MainCtrl` (`0x281e8`) at 3,131,753 virtual instructions with no firmware stubs and no flash command (`tools/emulate_captured_monitor_boot.py --cold --ack --timer --stop-at application-main-task`; five verified scenarios). `EMU`

### Handoff block

| Field at application reset | Value | Tag |
|---|---|---|
| Return LR | `0x000003c4` | `EMU` |
| Four words at LR-16 | `0, 0, 0, 0x1220000c` (the last also matches the application's fallback constant) | `EMU` |
| Pointer at `0x080003c0` | `0x1220000c`, written by `0x24b0` | `EMU` |
| Word at `0x1220000c` | `0x0a780000` after a flash boot, `0x0e780000` after a card RAM boot | `EMU` |
| CPSR low byte | `0xd3` (SVC, IRQ and FIQ masked) | `EMU` |
| OneNAND config | `0x40e0` | `EMU` |
| Lock state | All 512 blocks locked | `EMU` (supplied) |

Application reset copies the four words to `0x1c044..0x1c050` and saves LR at `0x235964`; `0x1c1d8` returns through it. `STATIC`, `EMU`

## Boot control (`0xb7c`)

15 of 15 controlled decision scenarios pass; the integrated decision table is confirmed by 288 of 288 supplied-input cases. `EMU`

| Supplied conditions | Result |
|---|---|
| Origin matches, GPIO60 low, valid header | Loads the internal payload, entry `0x1c000`; never opens `MG1MODE` or the card |
| Same, body changed under an intact header | Same; the changed body runs. `0x19ac` is never called |
| GPIO60 low, invalid magic or marker | Card RAM scanner; success sets the RAM entry, failure goes to the console |
| GPIO60 low, valid header, flash read fails | Returns from the load without card recovery; no new jump address |
| GPIO60 high, no `PB_LOW` | Reads `MG1MODE`; card RAM scanner unless `VERUP`; a failed scan can fall back to the valid-header flash load |
| GPIO60 high, `MG1MODE=VERUP_INMONITOR` | Monitor flash updater `0x50ec` |
| GPIO50 low and GPIO60 high | Records `PB_LOW_DETECTED`; console before reading `MG1MODE` |
| Origin word `0x080000a4` differs from `0x10800080` | Console; skips normal boot and card selection |

Invalid main info, by input level:

| GPIO60 | GPIO50 | `MG1MODE` | Usable card | No card |
|---|---|---|---|---|
| low | either | ignored | Scanner, then application reset | Scanner, then console |
| high | high | absent or other | Scanner, then application reset | Scanner, then console |
| high | high | `VERUP_INMONITOR` | Monitor flash updater | Updater path |
| high | low | not read | Console | Console |

Integrated cold-reset runs from Monitor reset to `MainCtrl`, with the official image on a virtual card: invalid info at GPIO60 low, at GPIO60 high, and at GPIO60 low with GPIO50 low each reach `MainCtrl` at about 472.8 M instructions with flash unchanged; GPIO60 high with GPIO50 low stops at the console at 58,291 instructions; no card reaches the console at about 20.2-20.3 M. `EMU`

**Invalid** means wrong magic, wrong marker at `+0x1f0`, or a type other than 2 or 3. An erased block or an unfilled read also counts, because `0x1748` pre-fills `0xff` and `0x1920` ignores the read status (the `0xff` buffer was tested; a read failure itself was not modelled). `STATIC`

**Not a fallback trigger:** a damaged body under a valid header (no checksum on this path); a payload read failure under a valid header (`0x156c` returns -1, `0x3b0` jumps to `0x1e8`, the Monitor re-enters `0xb7c`); any crash after the application starts. `STATIC`

Ordinary startups do not depend on GPIO60: with valid info and no card, low reaches `MainCtrl` at 3.13 M instructions and high at 3.24 M. `EMU`

### GPIO60 and GPIO50

| Fact | Tag |
|---|---|
| GPIO60 is an input the Monitor cannot drive: `0x244c` leaves bit 28 clear in the direction register | `STATIC` |
| Only `0x24b0`, `0x5ac`, `0x200c`, `0x3600` and `0x588` run before the sample; none does an SPI transfer or CPU1 request, so GPIO60 is not a CPU1 reply to the Monitor | `STATIC` |
| In the application the same pin is the CPU1 link's per-byte handshake interrupt, so an application read does not reveal the Monitor-time level | `STATIC` |
| Inactive level is most likely low: `0xca548` installs the callback with `r1 = 1`, and an active line before the SPI handle exists would run the callback without a handle. The high branch looks like a service state (only low-and-valid runs board services; high reads a card mode file first) | `INFERRED` |
| GPIO50's name `PB_LOW` is the Monitor's; it is not shown to be the Playback button. The application's compare at `0x1e454` discards its result. CPU1's boot-trigger reply seeding key slot 7 (PLAY) is application key state, not evidence about GPIO50 | `STATIC`, `EMU` |
| A Playback-start boot reached the application on the main body, so it was not "GPIO60 high, GPIO50 low" | `HW`, `INFERRED` |

## Card RAM loader

Path: `0xa2c` -> scanner `0x2394` (mount `0x6034`, scan `/ATA1/QRFLECT0` to `QRFLECT9` in order) -> loader `0x2194` -> validator `0x2138`/`0x1cb0` and checksum `0x19ac` -> `0x1518` sets the jump. The payload is staged at `0x08000000 + header.load`. `STATIC`

| Property | Detail | Tag |
|---|---|---|
| Requirements per file | Full 512-byte header, successful stat, payload read equal to header length, type 2, 3 or 7, passing size, magic, format, range and weighted checksum | `STATIC` |
| Copy before validate | A rejected file can leave staged bytes in RAM without setting the entry | `STATIC` |
| Direct cases (eight) | Accepts the official image, the scanner reaching filename 3, and a modified image with recomputed checksum (runs from RAM, no flash write). Rejects stale checksum, size mismatch, truncated read, bad magic, missing files | `EMU` |
| Handoff | Exact payload in RAM, LR `0x3c4`, real handoff block, flash and spare digests unchanged; config `0x41e0` on the direct card path, `0x40e0` after a header failure | `EMU` |

These runs stop at application reset. The application then makes ordinary flash writes (store blocks every boot) and runs its own late updater; with invalid info it enters that updater.

### RAM-boot lifecycle

Eight scenarios, all reaching their endpoint. `EMU`

| Scenario | Endpoint |
|---|---|
| Normal flash boot, official type 2 on card | Update gate skips; runs past post-startup |
| Monitor RAM boot, official type 2, valid info | Same |
| Monitor RAM boot, type-7 marker image, valid info | Same |
| Same, native Monitor checksum | RAM handoff at 470,924,615 instructions; post-startup at 493,688,087 |
| Invalid info, official type 2 RAM fallback | Application enters its updater (`0x5d18c`) |
| Invalid info, type 7 | Application scanner reads the header, skips it, returns `0xffffffff` |
| Valid info, type 7, UP+TELE held with confirmation | Scanner again skips type 7 |
| Zeroed body plus invalid info, official type 2 RAM fallback | Application reinstalls main and info byte-exact (2,152 programs, 35 erases); a later cold boot without the card reaches post-startup |

The type-7 carrier is a data-only "RAM : V" marker image (eight payload bytes changed; in the model it changes 508 offscreen pixels of the version dialog). Block 0, store and extended regions stay unchanged in every scenario. `EMU`

In these runs only the SD mount and the read-only file calls (open, read, stat, close) are supplied; the delay, control helper, directory scan, payload copy, weighted checksum, entry setter and reset return all run original Monitor instructions. Instruction counts are not time. The reinstall scenario stops when the installer returns, so the firmware's own post-update restart is not covered. `EMU`

## Monitor flash updater

`/ATA1/MG1MODE` containing `VERUP_INMONITOR` (parsed by `0x2090`, read only with GPIO60 high) selects the Monitor's flash updater. `0x50ec` scans `QRFLECT0..9`, stages the file (`0x4cc8`, padded by `0x8e80`), then `0x4e54(..., 0, 1)` erases info, programs main, programs info, verifies main. No application code runs. `STATIC`

Eight isolated cases (`tools/emulate_monitor_reinstall.py`; direct entry with config `0x40e0`, all blocks initially locked; not a reset test). `EMU`

| Supplied condition | Result | Tag |
|---|---|---|
| Official type 2; corrupt main, invalid info | Returns 0; 35 erases, 2,152 programs; body and header equal the image | `EMU` |
| Payload changed, stale checksum | Returns 0 and programs it | `EMU` |
| Card read error / no files / type 7 | Returns -1; no program or erase | `EMU` |
| Erase fails at info block `0x3c` or first body block `0xd` | Replaced by physical `0xc`, table updated, image correct | `EMU` |
| Info erase and all spares fail | Returns -1 without programming main (tries `0xc` down to `0x3`) | `EMU` |

After every success `0x1920` accepts the new info, `0x156c` reloads the body byte for byte and sets the jump to `0x1c000`; block 0 is untouched. The model reconstructs only three spare metadata bytes per sector and fills the rest with `0xff`. `EMU`

**Validation differs between the two card paths.** `0x3e7c` runs only when `0x4e54`'s fourth argument is 0; `0x50ec` passes 1. The flash updater therefore programs a stale-checksum image that the RAM loader would reject, and the boot header check never notices. `STATIC`, `EMU`

### Interrupted-update recovery

With the official file on the card, all 35 cut points after the info erase recover at either GPIO60 level (Monitor RAM fallback, then the application reinstalls byte-exact). With no card the Monitor reaches the console after about 20 M instructions and the camera looks dead. GPIO60 high with GPIO50 low reaches the console even with a card. `EMU`. All of it rests on the Monitor's SD mount and file read, which has never run on hardware. Matrices are in [09](09-recovery-and-failure-modes.md).

## Container acceptance by consumer

Formats are in [03](03-firmware-image-and-update-format.md#header-types).

| Consumer | 1 | 2 | 3 | 5 | 6 | 7 | Tag |
|---|---|---|---|---|---|---|---|
| Monitor boot header check | no | yes | yes | no | no | no | `STATIC` |
| Monitor card RAM loader | no | yes | yes | no | no | yes | `EMU` |
| Monitor flash updater | untested | programs | untested | untested | untested | -1 | `EMU` |
| Application header reader `0x27400` | | returns 2 | | | | returns 7 | `STATIC` |
| Application SD scan `0x278d4`, dispatcher `0xd6dec`, gate `0x639c0` | writes Monitor window (no file exists) | flashes main | main window | text via `0xd6a62` | extended data | skipped and rejected | `EMU` |
| Direct lower programmer `0xd6c58` | yes | yes | yes | no | yes | admits it | `EMU` |

Type 7 is a barrier only in the normal SD path; the lower programmer will flash it if called directly.

## Monitor console

| Fact | Tag |
|---|---|
| UART at `0x12028000`, separate from the application's USB console. `0xdb4(0x9600)` = 38,400 baud; accepts 9600/19200/38400/57600/115200 (register values, not measured baud) | `EMU` |
| Command table `0xf0ec` (26 entries), parser `0x2db4`. `apl` mounts, scans and RAM-loads; `jr` exits, optionally selecting an entry; `j` branches; `fl`/`flb` are unsupported stubs here. Ten isolated cases pass (four commands, five accepted UART arguments, one rejected) | `EMU` |
| After a software return the console prints 346 bytes ending in `OneNand>` and waits at receive `0x1168`; no flash command | `EMU` |
| Needs warm flash config `0x40e0`; cold config stalls at `0x6ab0` | `EMU` |
| No "usb" string in the Monitor; the application's USB stack does not carry over | `STATIC` |
| Reaching the console does not start a card scan; `apl` is separate | `EMU` |

A no-card boot with invalid info waits about 5000 ticks at `0xae0`, then enters the console, with no indicator. `0x2558`, which looks like an indicator routine, is empty. `STATIC`, `EMU`

## Software entry into the Monitor

All routes below ran only in the emulator. None has a physical transport or procedure on a camera.

| Route | What runs | Tag |
|---|---|---|
| Service terminal `x 0x1c1d8 0` | Dispatcher `0x62670`, resolver `0x6188e`, handler `0x61a20`. R0 = 0 returns through `0x3c4`: vectors restored, warm reset, console `0x9d0` in 20,637 instructions. R0 non-zero copies a reset trampoline to SRAM (native reset `0x1c1e8`) | `EMU` |
| USB line to console | USB receive -> `tAdjust` -> terminal parser -> `x` -> return -> warm reset -> `OneNand>` prompt | `EMU` |
| Console to RAM application | Wrapper -> return -> console -> `apl`, `jr` -> type-7 image -> application reset -> post-startup (493,686,576 instructions in all) | `EMU` |
| Type-5 card file | In the model, one text line `x 0x28a61 0` read by the type-5 reader `0xd6a62` (non-5-character lines go to the terminal) reaches the wrapper, `0x1c1d8`, `0x3c4` and the console at 119,370,677 instructions after a full cold boot; no NAND operation, NAND and spare digest unchanged. A comment-only control reaches EOF and returns 0 through the updater at 119,321,639 without touching the Monitor | `EMU` |
| `SCRIPT.MG1` | No opcode reaches terminal `x`; no direct Monitor target among 25 handlers and 17 engine roots | `EMU`, `STATIC` |

**Interrupt window.** A bare `0x1c1d8` return leaves the application IRQ vector `0x1c214` pointing at Monitor `0x41c` before masking at `0x1ec`. The stock wrapper `0x28a60` closes it: UART shutdown `0x24d2c`, UART setup `0x248e8`, INTC mask `0xffffffff` (`0x170408`), then the return. It is not a task teardown (three kernel-object calls in it were supplied as success), and no indexed reference to it exists in the image. FIQ behaviour is not covered by the emulator's core model. `STATIC`, `EMU`

A zero return selects the console, not the card scanner; a non-zero return resets and again depends on the GPIO selection. The type-5 route's success depended on the spacing of supplied confirmation pulses (1 M-spaced pulses skipped the UI; 12 M-spaced reached the scanner), a model scheduling effect, not a physical timing. `EMU`

**`SCRIPT.MG1` scope.** `rem` skips input; `adjcom` copies five characters and dispatches upper-case engine tokens. The 75 top-level engine-table entries were executed up to their bodies; external callees and indirect callbacks are not exhausted. `EMU`, `STATIC`

**Service terminal input.** Holding RIGHT + TELE at boot selects the USB service input even with pin `0x38` high; pin `0x38` low additionally routes console output to USB. `EMU`. See [08](08-card-scripts-and-service-terminal.md).

## What the application does with boot state

| Fact | Tag |
|---|---|
| Update check runs late: reset done at about 1.8 M instructions, `0x1eb22` at 18-70 M, on `MainCtrl` (priority `0x5a`). A crash before that blocks in-application SD recovery | `EMU` |
| Gate: UP + TELE + dial value 1, 10 or 9 (`0x4fbcc(2,0)` -> `0x32540(1)`), or invalid main info (`0x273e4`). The second condition is reachable only after a Monitor RAM boot, since an invalid header never starts the flash image | `STATIC` |
| The application compares the Monitor mailbox `0x8012ff0` with `INIT_ADJVALUE_M` at `0x1e52a` and can take adjustment-load path `0x27bc0`; only the `MG1MODE` parser writes that value | `STATIC` |
| Mounted flag `0x1220003c` and mailbox `0x8012ff0` survive to `MainCtrl` and differ by branch | `EMU` |
| CPU1's boot-trigger reply (`0xf201`) seeds active-key slot 7 (PLAY) | `EMU` |
| On Koma firmware `0x281ee` calls the loader after the update gate. Generation 1 reads card `/ATA1/GRMOD.BIN` only; generation 2 tries the card first, then `/IROM/GRMOD.BIN`. A card file the stock driver cannot reach (64 GB card) leaves `/IROM` in use | `HW` |

## Open questions

- Physical entry to any non-normal Monitor branch: no button, cable or file is known to reach GPIO60 high or the console on a healthy camera.
- GPIO60 and GPIO50 levels and drivers; settling them needs board probing or CPU1 firmware.
- First fetch at reset (BootRAM versus mask ROM); reset-acknowledge bits and the low-RAM alias are supplied in every run.
- The Monitor's SD path on hardware: FAT, host driver, card compatibility (its driver copy has no SDHC support, so an SDHC/SDXC card is not expected to work there). Recovery from a flash that does not boot rests on this path. The only test is a deliberate, destructive rescue rehearsal on the spare.
- Header-valid payload read failure: one run re-enters `0xb7c`; a later run with an injected load failure did not take that path. Unresolved.
- Whether CPU1 keeps power on during the `0xae0` wait.
- UART pins, levels and adapter.
- Safety of a software return with live RTOS tasks; FIQ is not modelled.
- Whether the spare body's Monitor and extended data match the main body's (its Monitor was never dumped).
- Restore from generation 2 Koma firmware, build 2b to stock 2.40 on hardware (only generation 1 -> stock has run).

## Evidence

- [../../analysis/monitor-recovery/capture-analysis/analysis.json](../../analysis/monitor-recovery/capture-analysis/analysis.json) (Monitor header, payload and window hashes)
- [../../analysis/monitor-recovery/emulated-card-loader.json](../../analysis/monitor-recovery/emulated-card-loader.json), [../../analysis/monitor-recovery/emulated-console.json](../../analysis/monitor-recovery/emulated-console.json), [../../analysis/monitor-recovery/conclusions.json](../../analysis/monitor-recovery/conclusions.json)
- [../../analysis/captured-monitor-emulation/verification-summary.json](../../analysis/captured-monitor-emulation/verification-summary.json), [../../analysis/captured-monitor-emulation/verified-through-main-task.json](../../analysis/captured-monitor-emulation/verified-through-main-task.json), [../../analysis/captured-monitor-emulation/ram-boot-summary.json](../../analysis/captured-monitor-emulation/ram-boot-summary.json)
- [../../analysis/monitor-boot-decisions/emulated-boot-decisions.json](../../analysis/monitor-boot-decisions/emulated-boot-decisions.json) (15 cases)
- [../../analysis/session-launch/fallback-conditions.json](../../analysis/session-launch/fallback-conditions.json) (288-case matrix, integrated runs)
- [../../analysis/monitor-reinstall/report.json](../../analysis/monitor-reinstall/report.json)
- [../../analysis/ram-application-lifecycle/verification-summary.json](../../analysis/ram-application-lifecycle/verification-summary.json), [../../analysis/ram-marker/fixture-manifest-type7.json](../../analysis/ram-marker/fixture-manifest-type7.json)
- [../../analysis/ram-activation/terminal-handoff/emulated-terminal-handoff.json](../../analysis/ram-activation/terminal-handoff/emulated-terminal-handoff.json), [../../analysis/ram-activation/emulated-monitor-return.json](../../analysis/ram-activation/emulated-monitor-return.json)
- [../../analysis/script-monitor-entry/README.md](../../analysis/script-monitor-entry/README.md) (type-5 route, wrapper, pulse spacing)
- [../../analysis/flash-failure-real-monitor/README.md](../../analysis/flash-failure-real-monitor/README.md) (interrupted-update recovery, Monitor SD path never run on hardware)
- [../../analysis/sdhc/controller/README.md](../../analysis/sdhc/controller/README.md) (separate Monitor SD driver)
- [../../analysis/module-loader/README.md](../../analysis/module-loader/README.md), [../../analysis/sdhc/irom-fallback/README.md](../../analysis/sdhc/irom-fallback/README.md), [../../analysis/sdhc/driver/README.md](../../analysis/sdhc/driver/README.md) (loader hook, card then `/IROM`)
- [../../analysis/hw-tests/2026-11-07-spare.md](../../analysis/hw-tests/2026-11-07-spare.md), [../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md](../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md) (generation 2 boots on both bodies; 64 GB card falls back to `/IROM`)
- [../../archive/STATUS.md](../../archive/STATUS.md) section 1 (restore of official 2.40 over generation 1 on the spare)
- [../../analysis/usb-first-contact/README.md](../../analysis/usb-first-contact/README.md), [../../analysis/spare-usb/README.md](../../analysis/spare-usb/README.md) (official update on the spare)
- [../../tools/emulate_captured_monitor_boot.py](../../tools/emulate_captured_monitor_boot.py), [../../tools/emulate_monitor_reinstall.py](../../tools/emulate_monitor_reinstall.py), [../../tools/emulate_monitor_console.py](../../tools/emulate_monitor_console.py), [../../tools/emulate_monitor_return.py](../../tools/emulate_monitor_return.py), [../../tools/make_monitor_elf.py](../../tools/make_monitor_elf.py)
