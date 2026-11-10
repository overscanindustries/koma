# Address index

A lookup table of addresses, tables, variables, flash blocks, registers and constants with a known meaning in GR DIGITAL firmware 2.40, the Monitor 1.00 and the Koma additions. Find an address here, then follow the chapter or evidence link for context. Rows without a link are covered by the chapter named in the section text or the [chapter list](README.md).

Tags: `HW` (seen on a camera or in files a camera wrote), `EMU` (original firmware code ran in the emulator; says nothing about hardware), `STATIC` (read from firmware bytes or disassembly, including datasheet reading), `INFERRED` (deduced, not observed). A row that bundles several addresses carries the weakest tag that applies.

**Address spaces.** Unless a row says otherwise, an address is a 2.40 application runtime address (image loaded at `0x1c000`; `qrflect3` file offset = runtime - `0x1be00`, payload offset = runtime - `0x1c000`). Values below `0x10000` in [Monitor code](#monitor-code) are Monitor addresses. `0x08xxxxxx` is SDRAM, `0x10xxxxxx`-`0x12xxxxxx` MMIO, `+0x..` a header or store offset. Flash blocks are 64 KiB. Names are descriptive, not firmware symbols. Addresses from other firmware versions appear only in [Same function across versions](#same-function-across-versions) or with the version named.

Chapters: [01](01-hardware-and-memory-map.md) hardware and memory map, [02](02-boot-chain-and-monitor.md) boot chain, [03](03-firmware-image-and-update-format.md) image and update format, [04](04-application-architecture.md) application, [05](05-settings-and-adjustment-store.md) settings and adjustment store, [06](06-image-pipeline-and-color.md) image pipeline and color, [07](07-usb-protocol.md) USB protocol, [08](08-card-scripts-and-service-terminal.md) card scripts and service terminal.

## Monitor code

| Address | Meaning | Tag |
|---|---|---|
| `0x0..0xfc00` | Monitor window: block 0 sectors `0..0x7d` (spare tag `MON`); type-1 destination | `STATIC` |
| `0x154` | Reset target; also reached after a software return | `EMU` |
| `0x1e8..0x1ef` | `MOV r0,#0xd3; MSR CPSR_c,r0`; target of a failed load (`0x3b0`) | `STATIC`, `EMU` |
| `0x1ec` | CPU-masking point; before it the application IRQ vector `0x1c214` already points at `0x41c` | `EMU` |
| `0x24c` | Reset poll of `0x12000024` bit 0 | `EMU` |
| `0x3c4` | Return address handed to the application | `EMU` |
| `0x3d8` | Saved-return callback: restores vectors, branches to `0x1e8` | `EMU` |
| `0x41c` | Monitor IRQ vector | `EMU` |
| `0x9d0` | Console entry | `EMU` |
| `0xa2c` | Card RAM path entry | `STATIC` |
| `0xae0` | No-card wait (about 5000 timer ticks), then console | `EMU` |
| `0xb58` | Main-info check entry | `STATIC` |
| `0xb7c` | Boot control (path selection) | `EMU` |
| `0xdb4` | Console UART setup; `0xdb4(0x9600)` = 38,400 baud | `EMU` |
| `0x1518` | Set jump address | `STATIC` |
| `0x156c` | Flash-to-RAM application loader; -1 on read failure | `STATIC` |
| `0x189c`, `0x1920` | Main-info validator; `0x1920` ignores the read status | `STATIC` |
| `0x19ac` | Weighted checksum; never called on the normal boot path | `EMU` |
| `0x1cb0`, `0x2138` | Card-file validator | `STATIC` |
| `0x2090` | `/ATA1/MG1MODE` parser | `STATIC` |
| `0x2194` | Card RAM loader (types 2, 3, 7) | `STATIC` |
| `0x2394` | Card scanner, `/ATA1/QRFLECT0..9` in order | `STATIC` |
| `0x244c` | GPIO direction write `+0x10 = 0xe000d700` (pin 60 input); GPIO11 low | `STATIC` |
| `0x24b0` | Writes the handoff pointer at `0x080003c0` | `EMU` |
| `0x2524`, `0x253c` | GPIO reads; `0x2524` samples GPIO60 | `STATIC` |
| `0x255c`, `0x261c`, `0x2634` | Read GPIO 8, 5, 4 | `STATIC` |
| `0x258c`, `0x25b4` | Shift words out on GPIO 61-63 | `STATIC` |
| `0x2b70`, `0x2bf4` | Board services, GPIO60-low valid-info branch only | `EMU` |
| `0x2db4`, `0xf0ec` | Console parser; 26-entry command table (`apl`, `jr`, `j`, ...) | `EMU` |
| `0x3e7c` | Full pre-write validator; skipped when `0x4e54`'s 4th argument is 1 | `STATIC` |
| `0x4308`, `0x4558`, `0x4588`, `0x4658` | Updater: program main, erase info, program info, verify main | `EMU` |
| `0x4cc8`, `0x8e80` | Stage a found file; pad helper | `EMU` |
| `0x4e54` | Update worker | `EMU` |
| `0x50ec` | Monitor flash updater (GPIO60 high + `VERUP_INMONITOR`) | `EMU` |
| `0x6034` | SD mount (sets `0x1220003c`) | `EMU` |
| `0x60d8`, `0x613c`, `0x6140`, `0x6118` | File open, read, stat, close | `EMU` |
| `0x8540` | Logical-to-physical block resolver | `EMU` |
| `0x080000a4` | Origin word; mismatch with `0x10800080` sends boot to the console | `STATIC` |
| `0x080003c0` | Handoff pointer cell; holds the pointer `0x1220000c` ([02](02-boot-chain-and-monitor.md)) | `EMU` |
| `0x8012ff0` | Monitor mailbox; compared with `INIT_ADJVALUE_M` by the application at `0x1e52a` | `STATIC` |
| `0x10800080` | BootRAM reset pointer | `STATIC` |
| `0x1220000c` | Fourth handoff word: `0x0a780000` after a flash boot, `0x0e780000` after a card RAM boot; matches the application's fallback constant ([02](02-boot-chain-and-monitor.md)) | `EMU` |
| `0x1220003c` | SD-mounted flag | `EMU` |
| `0x12201e90..0x12201fc0` | Monitor stacks: SVC `e90`, undef `ea0`, abort `eb0`, FIQ `ec0`, IRQ `fc0` | `EMU` |

## Application: boot and update

| Address | Meaning | Tag |
|---|---|---|
| `0x1c000` | Load and entry address | `STATIC` |
| `0x1c0ac..0x1c0bc` | `.data` copy loop in the reset code (skipped: source equals destination) ([fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |
| `0x1c054..0x1c184` | Reset code: SVC, IRQ, FIQ stacks only | `STATIC` |
| `0x1c1d8` | Return to the Monitor; R0 = 0 through `0x235964`, else reset trampoline (`0x1c1e8`) | `STATIC`, `EMU` |
| `0x1c214` | IRQ entry: reads INTC pending `+0x10`, mask `+0x08`; dispatches bits 1..17 then 0 | `STATIC` |
| `0x1c208` | Writes INTC `+0x04` = 1 ([01](01-hardware-and-memory-map.md)) | `STATIC` |
| `0x1c304` | JPEG IRQ call site (-> `0x2398c`) ([01](01-hardware-and-memory-map.md)) | `STATIC` |
| `0x1c3dc` | GPIO pending dispatcher (-> `0x2598a`) feeding INTC line 11 ([01](01-hardware-and-memory-map.md)) | `STATIC` |
| `0x122dd8` | Native camera sequence waits here for event mask 8022 ([01](01-hardware-and-memory-map.md)) | `EMU` |
| `0x26420` | Only writer of the SD command register ([01](01-hardware-and-memory-map.md)) | `STATIC` |
| `0x1cf58`, `0x1c530`, `0x60f5c` | C init; `KernPool` creation chain (`0x70000`) | `STATIC` |
| `0x1cfb4` | Task create (priorities from arguments) | `STATIC` |
| `0x1dea4`, `0x28800` | Nucleus task create; task-create path for `tScript` / `tOpeMain` (priority `0x50`) ([fn table](../../analysis/sibling-arm-diff/feature-table.md), [master firmware](../../analysis/master-firmware/README.md)) | `STATIC` |
| `0x1e3ac` | Boot banner: header version and build date ([code map](../../analysis/feature-code-map/README.md)) | `STATIC` |
| `0x1e444` | Reads main-info `+0x12` into `0x1a8c00/04` | `STATIC` |
| `0x1e468..0x1e490` | Allocates a 128 KB block headed by `0x1a8bfc` | `STATIC` |
| `0x1e7ac` | Startup function on `MainCtrl`: settings load, service keys, update gate, script wait. Forces RAM `0xae` to `0x1c` | `STATIC` |
| `0x1e98c` | Sets `0x23a88c`/`0x23a888` if RIGHT + TELE and pin `0x38` low for 25 polls | `STATIC` |
| `0x1ea6e` | DOWN + WIDE test: stores 1 in `0x1bd240` (store `0x1ea7c`, literal pool `0x1ebc4`) ([stock options](../../analysis/stock-options/README.md)) | `STATIC` |
| `0x1ea82`, `0x1ea8e` | Service-key test; `0x53b0c(1)` | `STATIC` |
| `0x1eae8` | Waits on event mask 2 or 4 | `STATIC` |
| `0x1eb22` | Update gate: UP + TELE + dial 1/10/9, or invalid info | `STATIC` |
| `0x1eb3e`, `0x1eb40` | Wait for `0x1bd3a8`, up to 6000 x `sleep(10)` | `STATIC` |
| `0x1eb74` | `/ATA1/QUICKVER.UP` check | `STATIC` |
| `0x1ec12` | After a successful update: flags, then `0x32b74` | `STATIC` |
| `0x1ec74` | Skip path; other key combinations | `STATIC` |
| `0x1ede0` | Waits for the camera task's `0x2200`/`0x2209` acknowledgement during startup ([power-on diag](../../analysis/spare-poweron-diag/README.md)) | `STATIC` |
| `0x1ef90`, `0x293f4` | Special-start classifier (tests `0x4fbcc(2)`, `(3)`, `(1)`); camera-state setter, `0x293f4(2)` starts the lens ([power-on diag](../../analysis/spare-poweron-diag/README.md), [stall](../../analysis/stall/ROOT-CAUSE.md)) | `STATIC` |
| `0x1effa` | `NADJ0001.ADJ` probe (called at `0x1e88c`, `0x1ea32`): unless `0x3e8ec()` returns 1, opens and closes `/ATA1/RADJ/NADJ0001.ADJ`, sets `0x1a8c1a`, then `0x31a14(0x8000, n)` sends `0x1102` to the file task ([04](04-application-architecture.md), [store recipes](../../analysis/store-recipes/README.md)) | `STATIC` |
| `0x1f090` | Predicate; console goes to USB only if it is not 1 | `STATIC` |
| `0x1f09c` | Version screen (DOWN + TELE); sets the Monitor version datum `0x1a8c08/0c` | `STATIC` |
| `0x1f1b8` | RIGHT + WIDE 20 s screen | `STATIC` |
| `0x1f1e0` | Counters screen (LEFT + TELE); strings `0x1f26c`/`0x1f28c` | `STATIC` |
| `0x26e04`, `0x26f38` | `QUICKVER.UP` flasher; second reflector driver (`programMonitor`) | `STATIC` |
| `0x273e4` | Main-info validity check (calls `0x62c42`) | `STATIC` |
| `0x27400` | Main header reader (returns type) | `EMU` |
| `0x275cc` | Streaming weighted checksum (the updater's gate) | `STATIC` |
| `0x27648` | Version gate; callers `0x276ec`, `0x277a0`, `0x27838` | `STATIC` |
| `0x27688`, `0x27738`, `0x27824` | Per-type checks, types 1, 3, 6 | `STATIC` |
| `0x276d8` | Per-type check, type 2 | `STATIC` |
| `0x277ec` | Per-type check, type 5 | `STATIC` |
| `0x278d4` | `QRFLECT0..9` scanner (template `0x177968`) | `STATIC` |
| `0x281e8` | `MainCtrl` (firmware task `tAplCtrl`) entry; calls `0x1e7ac` at `0x281ea` | `EMU` |
| `0x281ee` | After startup returns; the generation 1 image hook (`bl 0x282cc` -> `bl 0x57f78`) | `HW` |
| `0x2821e`, `0x2822c` | Script-gate read; `0x65108` call | `STATIC` |
| `0x282cc` | `post_startup` | `EMU` |
| `0x28794` | Wait for a named message (`0x2201`, policy 2) | `STATIC` |
| `0x28b28`, `0x5d330`, `0x5d434` | Update prompt; screen helper; `0x5d330(0x18)` defaults to No | `STATIC` |
| `0x28bdc` | MainCtrl request handler (`0x5xxx`, `0x720d`) | `STATIC` |
| `0x32430` | `0x32430(1,0)`; failure powers off without a screen | `STATIC` |
| `0x32540` | Two-key + dial test: `(1)` UP + TELE, `(0)` RIGHT + TELE | `STATIC` |
| `0x32a22` | Battery check (`0x2b9e92` must be 3 or 2) | `STATIC` |
| `0x32b74` | Power-off wrapper (end of update) | `STATIC` |
| `0x32b8c..0x32de1` | Shutdown body: settings save, CPU1 specials, `0x4fcaa` | `STATIC` |
| `0x4fbcc` | Key-combination wrapper (`n` = 1 service, 2 update, 4 shutdown, 5 UP + WIDE) | `STATIC` |
| `0x52900`, `0x5290c`, `0x521dc` | Update failure path; battery poll for the update gate; battery voltage read from CPU1 register `0x1e` ([fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |
| `0x5d18c` | Update orchestration; returns 1 when no store write happened | `STATIC` |
| `0x5ceac` | Calls `0x32430(1,0)`: no screen, power-off path ([03](03-firmware-image-and-update-format.md)) | `STATIC` |
| `0x62bbc` | Magic compare (literal `0x177894`) | `STATIC` |
| `0x62c42` | Main-info validity body | `STATIC` |
| `0x62cf0` | Weighted checksum routine | `EMU` |
| `0x62d76` | Header validator; windows type 1 `[0,0xfc00)`, 2/3/7 `[0x1c000,0x406800)`, 6 `[0,0x1000000)` | `STATIC` |
| `0x639c0` | Type admission (1, 2, 3, 6); no direct caller | `STATIC` |
| `0x63574` | Block-fits-buffer (`0x10000`) check | `STATIC` |
| `0x63b88` | Program main; refuses > `0x2f0000` after the info erase | `STATIC` |
| `0x63c68` | Erase main info (first destructive step) | `STATIC` |
| `0x63c80` | Program main info (last step) | `STATIC` |
| `0xd6888`, `0xd68cc` | Lamp request patterns around the type-5 file | `STATIC` |
| `0xd6a62` | Type-5 driver: 5-char lines to the engine, others to the terminal | `STATIC` |
| `0xd6bcc` | Stops key and battery polling before flashing | `STATIC` |
| `0xd6c58` | Lower main-image programmer; admits type 7 if called directly | `STATIC`, `EMU` |
| `0xd6dec` | Updater loop; continues after a failed file | `STATIC` |

## Service engine, scripts, terminal

| Address | Meaning | Tag |
|---|---|---|
| `0x24a80` | Console ring writer (ring `0x237f40`, limit `0x400`) | `STATIC` |
| `0x26b04`, `0x26b10` | Console to USB; console router (flag `0x1a9028`) | `STATIC` |
| `0x27e38`, `0x27f50` | Dumps to `FPROM.DAT` / `IFPROM.DAT` (read-only to flash) | `STATIC` |
| `0x28a60` | UART/INTC preparation wrapper before a Monitor return | `STATIC`, `EMU` |
| `0x3dfe8` | `/ATA1/TEMPLATE` voice-memo probe at init (-> `0x3e1e0`) ([hidden modes](../../analysis/hidden-modes/README.md)) | `STATIC` |
| `0x534be`, `0x534f4`, `0x5317a` | USB console writer chain; drops output while `0x1bd294 == 0` | `STATIC` |
| `0x53564` | Sets `0x1bd294`; sends a 12-byte `c1 20` notification; called from `0xd9366` ([fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |
| `0x538bc` | USB event router: to `0xca8a4` when `0x1bd2c8 == 0`, else to `0xcda50` ([live-view USB](../../analysis/liveview/usb/README.md)) | `STATIC` |
| `0x53734` | tDcx entry; stores selector in `0x1bd2c8` | `STATIC` |
| `0x53b0c` | Creates tDcx | `STATIC` |
| `0x54020` | tAdjust / terminal task; gets qADJUSTUSB into `0x1bd394` | `STATIC` |
| `0x542b8`, `0x54320` | `qADJUSTUSB` consumer (-> task `0x54020`); writer of the input selector `0x1bd39c` ([08](08-card-scripts-and-service-terminal.md)) | `STATIC` |
| `0x54164` | Adjustment command engine: A..Z, 17 letter handlers, arbitrary memory read/write | `STATIC` |
| `0x5443c`, `0x544bc`, `0x544ec` | Factory code loader chain (`GAUSS_CADJ_FILE`, body to `0x13000`) | `STATIC` |
| `0x55844` (file `0x39a44`) | Script gate check | `EMU` |
| `0x55864` | Stores card-probe flag `0x1bd3a8` | `STATIC` |
| `0x558ac` | Tries `/IROM/SCRIPT.MG1`, then `/ATA1/SCRIPT.MG1` | `STATIC` |
| `0x5d5d0` | USB session start (`0x800`) | `STATIC` |
| `0x5d4b8` | Message sender (type at `+6`, payload at `+12`); posts message `0x6101` to `qADJUSTUSB` ([04](04-application-architecture.md), [08](08-card-scripts-and-service-terminal.md)) | `STATIC` |
| `0x61a20`, `0x6188e` | Terminal `x` handler; address resolver | `EMU` |
| `0x61b6c`, `0x61c88`, `0x61cf8` | Terminal `ds`, `w`, `r` handlers; `r` reads one resolved word and rejects 0 ([08](08-card-scripts-and-service-terminal.md)) | `STATIC` |
| `0x62670` | Terminal line dispatcher (table `0x17781c`); no authentication | `STATIC` |
| `0x6470e` | Flash read used by the dump (516-byte records) | `STATIC` |
| `0x65108` | Script task starter (buffer `0x10000`, stack `0x4000`, priority `0x50`) | `STATIC` |
| `0x65202`, `0x65220` | `tScript` task body and exit handling (return code -2 skips a line, -1 stops) ([08](08-card-scripts-and-service-terminal.md)) | `STATIC` |
| `0x6515c` | Script load: `/ATA1` first, then `/IROM` | `STATIC` |
| `0x65260` | 64 KiB script buffer | `STATIC` |
| `0x652fc` | `CONFIG.TST` resume into `0x2c2d28` | `STATIC` |
| `0x653c0`, `0x654ec` | `CONFIG` rotation (always removes `CONFIG.TST`); state save to `/ATA1` or `/IROM/CONFIG.TST`, used by `end` and `poff` ([08](08-card-scripts-and-service-terminal.md)) | `STATIC` |
| `0x661b0`, `0x66ab4` | `poff`: save state, arm reboot flag `0x2b9e5d`, POWER pulse | `STATIC` |
| `0x66878` | `adjcom`: upper-cases a 5-char token, calls `0x54164` | `STATIC` |
| `0x66988` | `install`: delete `/IROM/SCRIPT.MG1`, copy `/ATA1/SCINST.TST` to it | `STATIC` |
| `0x66a30` | `uninstall`: delete script and `CONFIG` files | `STATIC` |
| `0x66bb8` | Reads UP + WIDE on script start | `STATIC` |
| `0x67434` | Flash dump to `/ATA1/IROM.DAT` (read-only to flash) | `STATIC`, `HW` |
| `0x674bc` | Flash restore (blocks >= 1, skips block 0) | `STATIC` |
| `0x8df30` | `ricoh.res` loader (limit `0x1fc00`) ([04](04-application-architecture.md)) | `STATIC` |
| `0xb62b4`, `0xb62be` | Dump entry (`UE103`) / restore entry (`UE104`) | `STATIC` |
| `0x138ae8` | Sub-dispatcher: `0x54` dump, `0x55` restore | `STATIC` |
| `0xcda50`, `0xcdbb0` | Second USB line handler (state `0x1bd2cc`); CR line assembler | `STATIC` |
| `0xd0e86..0xd0f08` | `UEEF1` guard (screen `0x1a`, power-off) | `STATIC` |
| `0xd095c` | Service-engine `U`-group handler, called by `0x54164`. Sub-commands `0xb4..0xfe` go through a `tbh` table at `0xd0a1c` to targets `0xd0cae..0xd240e`, so the handler is about 5.4 KB. Earlier analyses called it an 84-instruction stub; that figure was a Ghidra artifact. GR 2.40 has no `PARAM.DAT` string; the GX8 equivalent is `0xb2b68` ([PARAM.DAT](../../analysis/param-dat-sibling/README.md)) | `STATIC` |
| `0xd0d5a`, `0xd0dde`, `0xd0ee0` | Engine command handlers; `0xd0dde` is the flash-dump case reached for engine byte `0xe1` through `0xd0a1c`; `0xd0ee0` leads to the opcode interpreter `0xd17d4` ([08](08-card-scripts-and-service-terminal.md), [fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |
| `0xd1ab0`, `0xd1abe`, `0xd1ac2`, `0xd1cec` | Engine read/write primitives and helpers ([fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |
| `0xd1c30`, `0xd1d4c`, `0xd1e70` | Engine letter handlers `V`, `W`, `X` (`X` selects the region base, `X12C0` -> `0x08010000`) ([08](08-card-scripts-and-service-terminal.md)) | `STATIC` |
| `0xd17d4` | Opcode interpreter (`0x5c` dump, `0x5d` erase, `0x5e` dump); `UEE` second-byte dispatch | `STATIC` |
| `0x115148` | memset that can run `0x4000` B past an allocation | `STATIC` |
| `0x17781c` | Terminal table (`r`, `w`, `d`, `x`, `ds`, `im`, `io`, `raw`, `lk`, `t`, `i`) | `STATIC` |

## USB and file I/O

| Address | Meaning | Tag |
|---|---|---|
| `0x29b28` | Capture entry (record states only) | `STATIC` |
| `0x4fc78` | Stores 1 at `0x1bd1b8` (`remtin`) | `STATIC` |
| `0x4fc90`, `0x2833e` | Only reader of `0x1bd1b8`; needs `0x23a870 == 6` in state 9 | `STATIC` |
| `0x4fd1e` | Dial position -> app state; forces state 9 | `STATIC` |
| `0x4fe44..0x4fe63` | State-9 force (file `0x34044`); Koma `tether` redirects `0x4fe44`, see [Koma](#koma-firmware-and-package) | `STATIC` |
| `0x6d670` | State-9 runner; calls `0x5d5d0(0x800)` | `STATIC` |
| `0x6d6f0`, `0x6d840` | State-9 handlers; ignore RL1/RL2; key 24/36 -> `0xc4b64` | `STATIC` |
| `0x6d80c` (read `0x6d88a`) | Drops `0x5104` state requests while the mass-storage latch is set | `STATIC` |
| `0x55dc0`, `0x56024`, `0x55f0d` | `/ATA1` file open, read, close | `STATIC` |
| `0x5f688` | Bulk IN send (needs state `0x30`) | `STATIC` |
| `0x5f730` | Zero-length packet after an exact packet multiple | `STATIC` |
| `0x5f3f0`, `0x5f500` | Device-descriptor patcher; PID selector by USB mode ([07](07-usb-protocol.md)) | `STATIC` |
| `0x5fda8` | Configured-state check (`0x70 == 0x30`) | `STATIC` |
| `0x606c8` | SETUP dispatcher | `STATIC` |
| `0x60100`, `0x60530`, `0x60628` | M66592-family controller setup (`PINCFG \|= 0x8100`, `SYSCFG \|= 0x180`); USB clock-down suspend; USB `TESTMODE` ([01](01-hardware-and-memory-map.md), [live-view USB](../../analysis/liveview/usb/README.md)) | `STATIC` |
| `0xb2560` | File-system sector function (ops 1-3 read, 4-6 write) | `INFERRED` |
| `0xb7954` | Directory reader (from readdir `0x5644c`); its root end tests at `0xb7992` and `0xb79ca` use the FAT16 `RootEntCnt`, which is 0 on FAT32 ([root -NLST](../../analysis/sdhc/widefs/ROOT-NLST-64GB.md)) | `STATIC` |
| `0xc4acc`, `0x3d4a4` | USB mode from `0x1b0d7`; `0x08010017 == 0xff` forces mode 0 | `STATIC` |
| `0xc4b64` | Switch to PTP mode | `STATIC` |
| `0xcaa5c`, `0xcac64`, `0xcb0d4` | Frame send helpers (ZLP / divide convention); `0xcaa5c` sends nothing for length 0, which is why root `-NLST /EXT0` hangs on a FAT32 card ([live-view USB](../../analysis/liveview/usb/README.md), [root -NLST](../../analysis/sdhc/widefs/ROOT-NLST-64GB.md)) | `HW` |
| `0xcb6c4` | Command-protocol transfer state machine ([07](07-usb-protocol.md), [live-view USB](../../analysis/liveview/usb/README.md)) | `STATIC` |
| `0xcbd64` | `-CCTL` argument parser | `STATIC` |
| `0xcc384` | `CWD` | `STATIC` |
| `0xcc434` | `RETR` | `HW` |
| `0xcc598` | `-STOR` (writes a file) | `STATIC` |
| `0xcc628` | `DELE` | `HW` |
| `0xcc6a8` | `RMD` | `STATIC` |
| `0xcc728` | `MKD` (no table entry) | `STATIC` |
| `0xcc7c0` | `PWD` | `HW` |
| `0xcc86c`, `0xcc8ec` | `RNFR`, `RNTO` | `STATIC` |
| `0xcc970` | `-NLST` | `HW` |
| `0xccb5c` | `-VER` (`200 9.17 VV.VV`; arms a 3 s timer) | `HW` |
| `0xccbea`, `0xccd98` | `-PRET`, `-SRET` | `STATIC` |
| `0xccf14` | `-INFO` | `HW` |
| `0xcd000` | `-CCTL` (`remtin`, `remtout`, `diaset`, `keyset`, `poff`); answers 200, shutter not fired | `HW` |
| `0xcd27c` | `-DCAP` | `HW` |
| `0xcd398`, `0xcd39a` | `-SDIS`, `-GDIS` stubs | `STATIC` |
| `0xcd39c`, `0xcd39e` | `-FUPL`, `-FDWL` (bare return) | `STATIC` |
| `0xcd3a0`, `0xcd43e`, `0xcd51c` | `-GCID`, `-GSID`, `-TIME` | `STATIC` |
| `0xcd660` | `-GTPN` (uninitialised state is fatal) | `STATIC` |
| `0xcd6cc`, `0xcd73c` | `-RTST`, `-FDAT` | `HW` |
| `0xcd7c8` | `-ABOR` | `STATIC` |
| `0x131b3a` | `UsbShutter` task: posts `(8,1)` | `STATIC` |
| `0xd8c54`, `0xd8ca2` | Descriptor-request handler; descriptor selection ([07](07-usb-protocol.md), [fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |
| `0xd931e`, `0xd9366`, `0xd9372` | Vendor request `0x22`: handler; notification call (`0x53564`); stage-5 routing target ([07](07-usb-protocol.md), [fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |
| `0x1fb400` | USB command table: 35 records of 36 B | `STATIC` |
| `0x1fb910` | `-CCTL` sub-command table | `STATIC` |
| `0x235184` | "USB MASS STORAGE" | `STATIC` |

## Flash driver

| Address | Meaning | Tag |
|---|---|---|
| `0x28194` | Erase blocks `1..0x1ff` except bad | `STATIC` |
| `0x62e38`, `0x62fbe`, `0x63030`, `0x631a4`, `0x63364` | Replacement table: size 10, pool from `0xc` down, scan, write, map | `STATIC` |
| `0x6325c`, `0x63620` | Table/pool writer; copy earlier sectors | `STATIC` |
| `0x6339c` | Take a spare after an erase failure | `STATIC` |
| `0x63598`, `0x636dc`, `0x638e8`, `0x63886` | Erase (type 2 on fail), program (type 3), verify | `STATIC` |
| `0x639e2`, `0x63ac0` | Monitor and Monitor-info writers (block 0) | `STATIC` |
| `0x63d9e` | Store writer: erase `0x3d`, program `0x2000` B | `STATIC` |
| `0x63e30`, `0x279ec` | Readers of block `0x3d` into `0x08010000` | `STATIC` |
| `0x63e9c`, `0x63f94` | Extended-data writers | `STATIC` |
| `0x64524` | Status/IO: polls GPIO57; tests the error bit first | `STATIC` |
| `0x64228` | `/IROM` self-test ([01](01-hardware-and-memory-map.md)) | `STATIC` |
| `0x641ec` | The firmware's only `rand()`-like routine (not a grain source) ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x645c0`, `0x6490c` | ECC status (uncorrectable only) | `STATIC` |
| `0x64614`, `0x6462c` | Chip-ID read; device ID compare only; cache `0x1bec28` | `STATIC` |
| `0x646a4`, `0x64764`, `0x647c8` | Range check; lock one block; unlock one block (one target block per operation) ([fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |
| `0x64b1c` | Bad-block marker (first page only) | `STATIC` |
| `0x64d24`, `0x64d5c`, `0x64d6e`, `0x64f04`, `0x64f70` | Block operations (never lock-tight) | `STATIC` |
| `0x64d80` | Raw physical read (no table translation) | `STATIC` |
| `0x64838`, `0x649a8`, `0x64b88` | OneNAND program setup; erase ([03](03-firmware-image-and-update-format.md)) | `STATIC` |
| `0x64930`, `0x64a74` | DataRAM / spare-area transfer ([03](03-firmware-image-and-update-format.md)) | `STATIC` |
| `0x64c1c`, `0x64c58`, `0x64cc4` | Readers of the geometry table `0x177978` ([fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |
| `0x670d0`, `0x67134`, `0x678cc` | `/IROM` writers; guard | `STATIC` |
| `0x8a790` | Polling gate, skipped when `0x2c9e48 == 0` | `STATIC` |
| `0x177978` | Geometry table: `+0` valid, `+2` device ID, `+6` 512 blocks, `+8` 64 pages, `+0xa` 2 sectors | `STATIC` |

## Image pipeline, tone, capture, JPEG

| Address | Meaning | Tag |
|---|---|---|
| `0x23950` | CC1 line-3 frame ISR | `EMU` |
| `0x2398c`, `0x239c0` | JPEG ISR (line 5); HISR `camJPGhr` | `EMU` |
| `0x23a30`, `0x5e834` | Resize ISR (line 12); HISR callback releasing `0x1bd9d0` | `EMU` |
| `0x54bd0`, `0x54d84`, `0x54f3c`, `0x5509e` | Capture-message builders by drive type; store mode in `cp+0x22` (also `0x334cc`, a caller of `0x6e588`) ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x5df84` | Image decoder wrapper (semaphore `0x2bfccc`, timeout 2000) | `EMU` |
| `0x5df54`, `0x5df6c` | JPEG success / error callbacks | `EMU` |
| `0x5e7ce`, `0x1714e4` | Resize wait; resize start | `STATIC` |
| `0x68ab0`, `0x69354` | CCD window 3320 x 2475; RAW packer chooser (`0xde6d8`, `0x94484`, `0xde12c`) ([pixel access](../../analysis/pixel-access/README.md)) | `STATIC` |
| `0x6e164` | Bracket getter/validator | `STATIC` |
| `0x6e588` | Mono mode getter: 0 color, 1 B&W, 2 TE | `STATIC` |
| `0x8e3d0` | Stock image load (menu header art, shutdown image `(5)`); waits on `0x2a2f4` | `STATIC`; art `HW` |
| `0x8ff0e` | Mono/TE writer of `0x12018038`; TE table in 2.40 | `STATIC` |
| `0x8ffc8` | Tone-table upload `(selector, arg)`; contrast 1..5 -> `0x1e..0x22` | `STATIC`, `HW` |
| `0x90554`, `0x90604`, `0x906cc`, `0x907b4`, `0x90938`, `0x90ac0`, `0x90ba8`, `0x90c74`, `0x90d70`, `0x90e48`, `0x91032`, `0x910d4`, `0x91184` | Enhancer parameter selectors (store groups `0x08010b04..` to registers; `0x90e48` knee via `0x16d8e8`) ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x91634` | Lens-index term added to the ENH gain ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x900f4` | Loads the CC1 LUT when `cp+0x40` = 1 | `STATIC` |
| `0x90278`, `0x90408` | CC2 LUT load; capture upload step | `STATIC`, `EMU` |
| `0x90322` | Selector-cache compare | `STATIC` |
| `0x9035c` | CC1 selector path | `STATIC` |
| `0x9049c` | CC1 limits (changed by ISO setting 8) | `STATIC` |
| `0x91268` | Sharpness getter by mode (B&W `0x1b198`, TE `0x1b1a8`) | `STATIC` |
| `0x912bc`, `0x91e78` | Filter-parameter choice; apply (YH, ENH, LowPass, knee, ColorNR, RGB, interpolation) | `STATIC` |
| `0x91a30` | Blend (enable word `0x0801144c`) | `STATIC` |
| `0x92050` | Preview tone route (runs before the update gate) | `EMU` |
| `0x92200`, `0x922bc` | Per-mode word; chroma-gain writer | `STATIC` |
| `0x92420` | Contrast-compensation percent | `STATIC` |
| `0x92598` | Matrix and UV-offset writer (every shot) | `STATIC` |
| `0x92cc8` | Contrast getter: reads `0x080113f0/f4`, picks the selector | `STATIC` |
| `0x92e24` | Stores `0x0a839d00`, `0x0c000000` (64 MB hint) | `STATIC` |
| `0x943f0` | Line period (`0x81` vs `0x408`) | `STATIC` |
| `0x947e4` | Shutter wait on `0x1201401c` | `EMU` |
| `0x94858` | Second `0x1201401c` poller | `STATIC` |
| `0x955ec`, `0x956a0`, `0x95780` | RL2-pending setter; RL1 focus handler; type mapping | `STATIC` |
| `0x95738`, `0x95780` | Koma `fullpress` hook sites (`bl 0x70280`): external-sensor branch; RL2-pending branch ([fullpress.py](../../tools/koma/features/fullpress.py)) | `STATIC` |
| `0x960b4` | DNG re-develop path entry | `STATIC` |
| `0x96242`, `0x9b54c` | WB gain writers | `STATIC` |
| `0x96350` | Still develop pass 1; CCD to SDRAM 16-bit raw | `EMU` |
| `0x96436` | Pass-1 per-shot hook (`bl 0x8ff0e`) | `HW 2026-11-06 main` |
| `0x964b8` | WB-reset branch (`0x9b7e0`) after the pass-1 hook ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x96bc8`, `0x9fb20`, `0x9fcf4`, `0xa0144` | Callers of defect repair `0xdd888` ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x9665e` | `bl 0x91e78`; enhancer override point | `STATIC` |
| `0x9670c` | Semaphore wait on `[0x1f4dd8]` (timeout 500) | `EMU` |
| `0x9733a` | Normal shot; full-press call at `0x973e4` | `EMU` |
| `0x97694`, `0x9e3dc` | VD wait loop; per-iteration wait. `0x97694` and `0xa0650` are further callers of the upload path `0x90278` ([code map](../../analysis/feature-code-map/README.md)) | `STATIC` |
| `0x97aee` | Long-exposure dark frame (two frames when `cp+0xa` > 4); second capture `0x1199b4` (DMA subtract `0x16e4e4`), hot-pixel pass `0x119e18` ([06](06-image-pipeline-and-color.md), [pixel access](../../analysis/pixel-access/README.md)) | `STATIC` |
| `0x99988` | Fills the live-view ring pointer tables (Y `0x2cf640`, CbCr `0x2cf658`) ([06](06-image-pipeline-and-color.md), [live-view frames](../../analysis/liveview/frames/README.md)) | `STATIC` |
| `0x9af40` | Manual WB R/B clamp, `0x10..0x310` (`0x1b074`, `0x1b076`) ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x34004` | Post-shot resume dispatch into `0x95cd0` ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x825c8` | Stock histogram; reads the live-view ring ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x985a0`, `0x33670` | Callers: `0x33670` -> `0x985a0` -> `0x92050` -> `0x8ffc8(0x22)` | `EMU` |
| `0x9dfb5`, `0x9e058`, `0x9e0ec` | Frame callback; clear / set `0x12014044` bit 0 with INTC bit 3 | `STATIC` |
| `0xa0840` | WB/CL-BKT one-exposure, three-develop loop | `STATIC` |
| `0xa0650..0xa1056` | AE-bracket block holding the CL-BKT loop `0xa0840` ([code map](../../analysis/feature-code-map/README.md)) | `STATIC` |
| `0x40518` | AE-bracket step setter ([code map](../../analysis/feature-code-map/README.md)) | `STATIC` |
| `0xa32dc`, `0xa343c`, `0xa3644`, `0xa39bc` | Lens shading: apply, upsample, fallback, WB-bracket gate | `STATIC` |
| `0xafd20` | Second develop pass; rewrites color registers | `STATIC`, `HW` |
| `0xafd3e` | Pass-2 hook (-> `0x8ff0e`) | `HW 2026-11-06 main` |
| `0xde6d8`, `0xde12c` | RAW packers (16 -> 12 bit ARM; `KizRawArrange`) | `STATIC` |
| `0xdf10c`, `0x690f8` | DNG re-develop (`RIMG0001.DNG` + `.PRM`) | `STATIC` |
| `0x10f984` | JPEG rate control (adaptive scale, floor `0x14`) | `STATIC` |
| `0x10f1dc`, `0x10f2ec`, `0x10f3cc`, `0x10f5d4` | Rate-control helpers; `0x10f2ec` maps (size, quality) to bits per pixel x 10 from the store; `0x10f5d4` nominal bpp ([JPEG quality](../../analysis/jpeg-quality/README.md), [fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |
| `0x10fb50`, `0x110110` | JPEG scale-floor sites ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x110464`, `0x1104d8` | JPEG fast-path test; header-size producers ([06](06-image-pipeline-and-color.md), [writers](../../analysis/emu-writers/README.md)) | `STATIC` |
| `0x11194c`, `0x111bf6`, `0x201ec8` | Post-encode size setters; flag ([writers](../../analysis/emu-writers/README.md)) | `STATIC` |
| `0x112120` | MakerNote builder | `STATIC` |
| `0x1120e4`, `0x11294a` | MakerNote globals init; tag `0x1001` 48-byte record builder (IMG SET into byte 40 via `0x6f768`) ([fn table](../../analysis/sibling-arm-diff/feature-table.md), [06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x112746` | Zero-length memset that leaves Camera Info entry `0x10` unwritten ([fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |
| `0x112578` | `Rev%4.4x` formatter | `STATIC` |
| `0x11257b` (file `0xf677b`) | Marker patch site (`Rev024M`) | `HW` |
| `0x112b00` | Playback resize (decode, resize, re-encode) | `STATIC` |
| `0x116dbc`, `0x116fb8` | Software chroma NR; its gate (`cp+0x22` = 0 only) | `STATIC` |
| `0x11703c` | Master YUV (`CamStillRecGenMasterYUV`); shading `0x11705c`, gate veneer `0x117052`, date imprint `0x117372`; Koma SELECT pass hook `0x117382` (`HW`) ([ISP](../../analysis/isp-capabilities/README.md), [06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x117dc0` | JPEG dimension helper (aspect 2 latent) | `STATIC` |
| `0x1181ba` | Second date-imprint call (path `0x118074`, gated at `0x118192`) ([pixel access](../../analysis/pixel-access/README.md)) | `STATIC` |
| `0x13f0b4`, `0x13f8a0` | Date-imprint entry; glyph writer into Y/UV | `STATIC` |
| `0x163db0`, `0x1636b6` | Decoder start; DMA check | `EMU` |
| `0x166334`, `0x166436` | CC1 LUT loop | `STATIC` |
| `0x166598` | `MAX_COUNT_V` assert | `STATIC` |
| `0x16d31c` | Final LUT upload loop | `STATIC` |
| `0x16e4e4` | DMA calc engine (CalcType 1 applies shading) | `STATIC` |
| `0x170a30`, `0x170a3c` | `0x1201401c` read / write | `STATIC` |
| `0x170b14` | `0x12014040` writer | `STATIC` |
| `0x193a80` | Source of the second MakerNote date | `STATIC` |
| `0x936c0`, `0x93718` | JPEG parameter block builder ([writers](../../analysis/emu-writers/README.md)) | `STATIC` |
| `0x93128`, `0x93222`, `0x9355a` | EXIF part (date, AE copy); MakerNote part; finaliser ([writers](../../analysis/emu-writers/README.md)) | `STATIC` |
| `0x84c64`, `0x84c88`, `0x8564c`, `0x1c3264` | JPEG header builder; APP1/MakerNote length builder; Adobe RGB variant; end pointer ([writers](../../analysis/emu-writers/README.md), [06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x86180`, `0x86778` | EXIF writers on shot paths ([fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |
| `0x86eb0` | YUV/TIFF writer ([writers](../../analysis/emu-writers/README.md)) | `STATIC` |
| `0x89d30`, `0x89ed4` | UserComment; PrintIM ([writers](../../analysis/emu-writers/README.md)) | `STATIC` |
| `0x68a88`, `0x938f8`, `0x87830` | DNG parameters; DNG/TIFF writer ([writers](../../analysis/emu-writers/README.md)) | `STATIC` |
| `0x9d980` | Camera Info tag 1 WB source ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x8909c` | AVI writer ([code map](../../analysis/feature-code-map/README.md)) | `STATIC` |
| `0x719bc` | GT-1 lens correction, slot-1 path (flag `0x23a8a0`) ([code map](../../analysis/feature-code-map/README.md)) | `STATIC` |

## ISP registers

CC2 color stage at `0x12018000`. Liveness from register writes in both develop passes, judged from JPEGs.

| Register | Meaning | Tag |
|---|---|---|
| `0x12018000` | `SxUV` (`0x2038` in all modes) | `STATIC` |
| `0x12018004` | Chroma gain, 4 x u8 (`0x80` = 1.0); 0 gives a grey frame | `HW 2026-11-06 main` |
| `0x1201800c` | Luma weights `0x4008993a` (0.299/0.114 x 512); written at monitor start only | `STATIC` |
| `0x12018010`, `0x12018018`, `0x1201801c` | YH offset; ENH compensation; ENH gain | `STATIC` |
| `0x12018028` | IQ hue matrix (s8 x 4); no writer in 2.40; writes have no effect | `HW 2026-11-06 main` |
| `0x1201802c` | IQ chroma clip; no effect | `HW 2026-11-06 main` |
| `0x12018030` | IQ chroma offset; no effect | `HW 2026-11-06 main` |
| `0x12018038` | Mono: bit 24 fixMode, fixed `cfr`/`cfb` (s10) | `STATIC` |
| `0x12018078` | Chroma clip B (0 at monitor default) | `STATIC` |
| `0x12018100` | Control; bit 6 does not enable the IQ stage | `HW 2026-11-06 main` |
| `0x120180d8` | chroma coring (`pUVoffset`), two 6-bit fields (`& 0x3f3f`): low field cores Cb, high field cores Cr, after the tone curve; word = (Cr << 8) \| Cb | `HW 2026-11-06 main` |
| `0x120180dc`, `0x120180e4`, `0x120180e8`, `0x120180ec` | YH limit; RGB filter; ENH filter 1, 2 | `STATIC` |
| `0x12018104`, `0x12018108`, `0x1201810c` | `MatR`, `MatG`, `MatB` (mask `0xbfbfbfbf`, 0 in stock). Live; works on 8-bit gamma values; sign-magnitude (bit 7 sign, bit 6 ignored, bits 5..0 magnitude); byte 3 = R input, bytes 2 and 1 G-like, byte 0 = B; entries add; linear in magnitude | `HW 2026-11-06 main` |
| `0x12018110`, `0x12018114` | WB gains R, B | `STATIC` |
| `0x12018120`, `0x12018124`, `0x12018128`, `0x1201812c` | ColorNR; YH threshold; YH gain 1, 2 | `STATIC` |
| `0x12018130..0x1201814c` | Knee registers (via `0x16d8e8`) ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x12018150`, `0x12018154`, `0x12018170` | LowPass 1, 2; interpolation | `STATIC` |
| `0x12018160..0x1201816c` | Four Q14 unity gains (`0x4000`) | `STATIC` |
| `0x12018174` | From store `0x080113fc` at monitor start | `STATIC` |
| `0x12014088`, `0x1201408c` | CC1 clip levels (`0x0fff0fff`) | `STATIC` |

| Setter | Register | Tag |
|---|---|---|
| `0x171924`, `0x171958`, `0x171d80` | Chroma gain, Y weights, mono tint | `STATIC` |
| `0x171a00`, `0x171b74`, `0x171cb4` | IQ hue matrix, clip, offset (no caller in 2.40) | `STATIC` |
| `0x172080` | Chroma clip B | `STATIC` |
| `0x17231c` | Chroma coring (UV) | `STATIC` |

## UI, keys, power

| Address | Meaning | Tag |
|---|---|---|
| `0x256c2`, `0x256d6` | GPIO read `+0x20` / `+0x24` | `STATIC` |
| `0x256ea` | Takes a GPIO number | `STATIC` |
| `0x2571e` | Pin read (true when the data bit is 0) used by the VBUS decoder ([07](07-usb-protocol.md)) | `STATIC` |
| `0x1f36c`, `0x1f740` | System-timer channel-0 IRQ handler; channel-2 cancel ([01](01-hardware-and-memory-map.md)) | `STATIC` |
| `0x23ef4`, `0x240fc` | USART receive store; send ([01](01-hardware-and-memory-map.md)) | `STATIC` |
| `0x29910`, `0x8ba7c` | Shutdown save call; config copy ([04](04-application-architecture.md), [shutdown](../../analysis/shutdown-analysis-repair/README.md)) | `STATIC` |
| `0x324e6` | Posts `0x7103` to qOPE | `STATIC` |
| `0x3386c` | `tCameraMain` shutdown handler; queues `0x2201` | `STATIC` |
| `0x34038` | `tCameraMain` loop | `STATIC` |
| `0x35ac8`, `0x17e464` | Mechanical sequence engine; its table | `STATIC` |
| `0x38940`, `0x39014` | Secondary-timer sequence callbacks | `STATIC` |
| `0x3c8dc`, `0x3c9dc`, `0x3cfc8` | Shutdown mechanics; event wait (`0x2d1bd0`) | `STATIC` |
| `0x3d340` | Called before the mechanical shutdown when store `0x08010267` bit 5 is set ([04](04-application-architecture.md)) | `STATIC` |
| `0x3eb58`, `0xf1428` | Shutdown image: wait on `0x1b9890`; image step ([04](04-application-architecture.md)) | `STATIC` |
| `0x32cae` | Return site of the `0x2201` wait in shutdown ([04](04-application-architecture.md)) | `EMU` |
| `0x228e8` | LCD mutex blocking take (10,001 ticks) ([04](04-application-architecture.md), [stall](../../analysis/stall/ROOT-CAUSE.md)) | `STATIC` |
| `0x23578` | Capture-path try-take that leaks the LCD mutex ([04](04-application-architecture.md), [stall](../../analysis/stall/ROOT-CAUSE.md)) | `STATIC` |
| `0x22905` | Saved LR of a `MainCtrl` blocked on the LCD mutex (tether stall) ([04](04-application-architecture.md), [stall](../../analysis/stall/ROOT-CAUSE.md)) | `HW` |
| `0x5d978` | Semaphore wait; stores the caller's LR at `TCB+0xd0` ([04](04-application-architecture.md), [stall](../../analysis/stall/ROOT-CAUSE.md)) | `STATIC` |
| `0x5e774`, `0x5e7e4` | Creates resize semaphore `0x1bd9d0`; convert wait ([04](04-application-architecture.md)) | `STATIC`, `EMU` |
| `0x16fcf0`, `0x16fcfc` | DMA accessor; validator ([04](04-application-architecture.md)) | `STATIC` |
| `0x1615a0`, `0x170534` | `sdl_ven` asserts ([04](04-application-architecture.md)) | `STATIC` |
| `0x96716` | CCD2 thumbnail-timeout print ([04](04-application-architecture.md)) | `EMU` |
| `0x3ec14` | Calibration loop; hangs `MainCtrl` if `0x08010431` = `0xff` | `EMU` |
| `0x4947c`, `0x4949c` | Settings block load / save `(0x1b000, 0x1000)` | `STATIC` |
| `0x4fcaa` | Final power control | `STATIC` |
| `0x50c94` | RL1/RL2 HISRs (GPIO `0x30`/`0x31`) | `STATIC` |
| `0x50eb0` | Key poll with injection overlay | `STATIC` |
| `0x5110c` | Fills key array `0x2b9d84` | `STATIC` |
| `0x51158` | MODE bits -> dial value | `STATIC` |
| `0x511ec` | Dial transitional-position filter ([04](04-application-architecture.md)) | `STATIC` |
| `0x51228` | Dial reader (virtual while injecting) | `STATIC` |
| `0x5123c` | Virtual-dial writer (`dialset`, `0x2b9d39`) ([fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |
| `0x5129c` | PLAY long hold -> power-off | `STATIC` |
| `0x513c0` | Key event emitter (nothing while two keys are down) | `STATIC` |
| `0x51740` | Key injection sink (control codes 0, 1, 2, 42); sole caller `0x8a8be` in `0x8a8b8` ([fn table](../../analysis/sibling-arm-diff/feature-table.md), [tether](../../analysis/tether-feature/README.md)) | `STATIC` |
| `0x517dc` | Key funnel (scanned, injected, CA-1 keys; message `0x7202`) | `STATIC` |
| `0x51a64` | `REQ_GET_BOOT_TRIGGER` read of CPU1 register `0xf201` ([04](04-application-architecture.md)) | `STATIC` |
| `0xf114` | CPU1 `REQ_SET_CFG_BOOT` register (table index `0x10`, no caller) ([01](01-hardware-and-memory-map.md)) | `STATIC` |
| `0x520c8` | CPU1 power-off sequence | `STATIC` |
| `0x52152` | POWER press -> `0x720d` | `STATIC` |
| `0x5c95c` | Version screen builder | `STATIC` |
| `0x5c2f4` | RIGHT + WIDE diagnostic-log display (log `0x1b2f8`) ([hidden modes](../../analysis/hidden-modes/README.md)) | `STATIC` |
| `0x5b7e4`, `0x5b89c` | Detail screen load (first field `0x1c2e2e`) and store, B&W and TE ([tone modes](../../analysis/tone-mode-mapping/README.md)) | `STATIC` |
| `0x7e626`, `0x79346` | Mode -> detail-screen dispatcher; B&W/TE detail route ([tone modes](../../analysis/tone-mode-mapping/README.md), [06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x7e714..0x7e780` (`0x7e738`) | DIAL DIRECTION wrappers ([code map](../../analysis/feature-code-map/README.md)) | `STATIC` |
| `0x7fa48` | OSD size label; special-cases aspect flag 1 only ([frontier 2](../../analysis/emu-frontier-2/README.md)) | `STATIC` |
| `0x80740` | OSD element name list (`APLCAP_OSD_OVER_*`) ([code map](../../analysis/feature-code-map/README.md)) | `STATIC` |
| `0x5d7a8`, `0x5d878`, `0x5d914`, `0x5d9a4` | Queue send, receive, depth; semaphore release | `STATIC` |
| `0x5de6c` | Error logger | `EMU` |
| `0x6bdea`, `0x6c4bc` | SETUP load / commit | `STATIC` |
| `0x6e91c` | Getter of byte `0x1bef92` (once mis-flagged non-returning) | `STATIC` |
| `0x8a3b8` | `tOpeMain` loop; spin at `0x8a3e6` | `STATIC` |
| `0x8a74c` | Key polling in `tOpeMain` every 50 ticks while the queue is idle ([04](04-application-architecture.md)) | `STATIC` |
| `0x8a678` | OPE message builder and gate | `STATIC` |
| `0x8aa5e` | `0x710d` handler (-> `0x520c8`) | `STATIC` |
| `0xc5724` | Update-screen renderer | `STATIC` |
| `0xe3698` | SETUP action rows dispatched on hard-coded row numbers ([menu redesign](../../analysis/menu-redesign/README.md)) | `STATIC` |
| `0xca524` | CPU1 link | `STATIC` |
| `0xca548`, `0xca60c` | GPIO60 callback install; callback | `STATIC` |
| `0xca6b8`, `0xca76a`, `0xca808` | CPU1 bit command; table command writer; register read | `STATIC` |
| `0xd6fc4`, `0xd7014` | Kernel `*** STOP ***` printer | `STATIC` |
| `0x157b2c`, `0x157bac`, `0x157c84` | memset, memcpy, divide | `STATIC` |
| `0x15ec8c`, `0x15ecbc` | Event-group set / wait | `STATIC` |
| `0x15f024`, `0x15f084` | `KernPool` alloc / free | `STATIC`, `HW` |
| `0x1c5b4` | IRQ mask set (ARM); service table `+0x28` ([master firmware](../../analysis/master-firmware/README.md)) | `STATIC` |
| `0x174a00..0x175174` | Thumb -> ARM veneer table (linker-generated) ([code map](../../analysis/feature-code-map/README.md)) | `STATIC` |
| `0x174984`, `0x17496c`, `0x174978` | Undefined / abort handlers | `STATIC` |

## Code caves and patch sites

| Address | Meaning | Tag |
|---|---|---|
| `0x56f80..0x58800` | Dcfs test group: candidate code island (6,272 B in all GR images and two siblings) | `STATIC` |
| `0x57f74..0x58197` | Loader cave (548 B) used by generation 1 and generation 2 | `HW` |
| `0x57f78` | Card-module loader entry; the loader block `0x57f74..0x580cb` is 344 B (guard, loader, `/ATA1/GRMOD.BIN`, service table) ([loader.py](../../tools/koma/loader.py)) | `HW` |
| `0x5809c` | Loader service table (48 B: file open/read/close, alloc/free, key funnel, LUT upload) | `STATIC` |
| `0x58190`, `0x58194` | Publish slot: `KOMA` and the manifest address, written after install ([firmware.py](../../tools/koma/firmware.py), [linker.py](../../tools/koma/linker.py)) | `STATIC` |
| `0xb958c..0xb9974`, `0xd9e14..0xda1d8`, `0xa9934..0xa9d2a`, `0xbdaf4..0xbe17a` | Further cave candidates | `INFERRED` |
| `0x3c83c`, `0x3c840`, `0x3c850` | SNAP routine and its two load sites; `0x36ff0` re-applies SNAP ([snapdist.py](../../tools/koma/features/snapdist.py)) | `STATIC` |
| `0x46ef4`, `0x47074`, `0x4699c`, `0x47538` | Exposure-count chain | `STATIC` |
| `0x457c8`, `0x459ac` | 10-bit count packing into `0x1fb184[31:30]`, `0x1fb180[7:0]` (wraps above 1023) | `EMU` |
| `0x6f42c`, `0x6f498` | M index clamp (42); aperture clamp | `STATIC` |
| `0x6f6c4`, `0x6f6d0` | Aspect store / getter | `STATIC` |
| `0x701c0`, `0x70280`, `0x70b8c`, `0x712a4` | Focus type getter; focus by type; chooser (calibration `0x080106e0`); preset reader ([frontier 2](../../analysis/emu-frontier-2/README.md)) | `STATIC` |
| `0x72a82` | INFO DISP | `STATIC` |
| `0x7b374` | CL-BKT shot count | `STATIC` |
| `0x846aa` | Per-shot size estimate (shots remaining) | `STATIC` |
| `0x11318c` (`0x1131e0`) | Rate-control call | `STATIC` |

## Data tables and strings

| Address | Meaning | Tag |
|---|---|---|
| `0x53d18..0x53f78`, `0xcd0f0..0xcd248` | `-CCTL` reply strings (`200`, `451`, `501`) | `STATIC` |
| `0x84860`, `0x8485c` | FINE size-estimate literals | `STATIC` |
| `0x905ae..0x911de` | CC1/CC2 error strings | `STATIC` |
| `0xa8810`, `0xa88ee` | Boot copy of header strings to RAM; alternate set branch | `STATIC` |
| `0xa8b0c`, `0xa8bc8` (file `0x8cdc8`) | ImageDescription and Copyright literals | `STATIC` |
| `0xa8b90` | `Rdc\0` (MakerNote tag 1) | `STATIC` |
| `0x17517f` | Nucleus PLUS 1.11.17 | `STATIC` |
| `0x175380` | ROM defaults of the adjustment store | `STATIC` |
| `0x175416` | ROM-default FINE byte | `STATIC` |
| `0x176770` | Compiled default detail bytes (level 5 -> `0x199b30`) | `STATIC` |
| `0x177968` | `QRFLECTn` name template | `STATIC` |
| `0x177894` | Reflector magic literal | `STATIC` |
| `0x177aa4` | `SCRIPT.MG1` command table | `STATIC` |
| `0x17750f` | ND flag per aperture index | `STATIC` |
| `0x17eb60`, `0x17ef34` | ISO table; Tv table | `STATIC` |
| `0x17ec3c`, `0x17ec92` | Tv table (M); EXIF exposure table | `STATIC` |
| `0x17fe86` | Long-exposure frame counts; `0x17fe96` (file `0x164096`) = 180 s (5395) ([frontier 2](../../analysis/emu-frontier-2/README.md)) | `STATIC` |
| `0x18108c` | Key table (42 x `0x24` B) | `STATIC` |
| `0x181676` | CPU1 `REQ_*` name table | `STATIC` |
| `0x182774` | `REQ_SET_POWER_OFF` descriptor | `STATIC` |
| `0x18d270` | Menu-text pointer rows (9 languages) | `STATIC` |
| `0x193b30` | Tone table, contrast 1 (selector `0x1e`); 4096 u16, 12-bit index, 10-bit value | `STATIC` |
| `0x195b30` | Tone table, contrast 2 (`0x1f`) | `STATIC` |
| `0x197b30` | Tone table, contrast 4 (`0x21`) | `STATIC` |
| `0x199b30` (file `0x17dd30`) | Tone table, contrast 5 (`0x22`) | `STATIC` |
| `0x900c4`, `0x90404`, `0x90498` | Literals pointing at the contrast-5 table `0x199b30` ([03](03-firmware-image-and-update-format.md)) | `STATIC` |
| `0x19bb30` | Tone table, contrast 3 (`0x20`) | `STATIC` |
| `0x19db30` | Sixth table (selector `0xe`, inverted curve) | `STATIC` |
| `0x1a911c` | State table (capture entry only for record states) | `STATIC` |
| `0x1a91ac..0x1a91f0` | Still-page row-index map | `STATIC` |
| `0x1bd3e4`, `0x1bd3ef`, `0x1bd3fa`, `0x1bd3f5` | Quality menu tables; N3:2 flag | `STATIC` |
| `0x1bd42b` | AUTO BRACKET cursor -> value | `STATIC` |
| `0x1bd452` | IMG SET cursor -> value `00 01 02 03 06 04 05` (2.31: `0x1bacda`) ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x1b1670` | u8 table of 32,768 entries, companding-like; role unknown ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x1c1ec4` | Nine tab-name words | `EMU` |
| `0x1c224c` + 12k | SETUP rows (k = 0..29) | `STATIC` |
| `0x1c23b9` | Menu tab byte | `STATIC` |
| `0x1dee53` | Built-in shutdown image | `STATIC` |
| `0x1f4c88` | TE (Cb, Cr) table, 6 tones x 5 densities | `STATIC` |
| `0x1f4cb8` | TE tint table | `STATIC` |
| `0x1fb400` | USB command table | `STATIC` |
| `0x1fbaf0`, `0x202cac` | Font offsets/widths; page-1 glyphs | `STATIC` |
| `0x1fe084`, `0x1ff084` | Image tables | `STATIC` |
| `0x1ff7a8` | Update-screen element tables (not decoded) | `STATIC` |
| `0x21a8ec`, `0x22fee1` | Image data | `STATIC` |
| `0xa4a70..0xa5530` | External-flash serial driver `ExtFlashDrvRxTx` (GR-only) ([master firmware](../../analysis/master-firmware/README.md)) | `STATIC` |

## RAM state

| Address | Meaning | Tag |
|---|---|---|
| `0x1a8880`, `0x235968` | `.data` start; `.bss` start | `STATIC` |
| `0x1a8acc` | Firmware task table (`0x1c` B per task) ([04](04-application-architecture.md)) | `STATIC` |
| `0x1a8ad4`, `0x1a8ad8` | `tAplCtrl` (`MainCtrl`) TCB pointer; queue `qAPL` ([04](04-application-architecture.md)) | `STATIC` |
| `0x1a8bfc` | Work pointer heading a 128 KB block (= template base `0x1a90b4`) | `STATIC` |
| `0x1a8c00`, `0x1a8c04` | Version words for `Rev%4.4x` | `STATIC` |
| `0x1a8c08`, `0x1a8c0c` | Monitor version datum | `STATIC` |
| `0x1a8c1a` | `NADJ0001.ADJ` found | `STATIC` |
| `0x1a9028` | Console destination flag | `STATIC` |
| `0x1a9072` | Non-zero: VID/PID from the rebrand table `0x080116ba` | `STATIC` |
| `0x1a909d` | Cleared on successful startup | `STATIC` |
| `0x1a90e4` | Script gate | `STATIC` |
| `0x1a918c` | Operating-state index for battery thresholds | `STATIC` |
| `0x1b9866` | Boot/script mode byte (from pin 8); in mode 1 `tFileTask` `0x55734` mounts `/IROM` only if SD init fails ([generation 2 driver](../../analysis/sdhc/driver/README.md)) | `HW` |
| `0x1b98fa` | Slow-mode flag | `STATIC` |
| `0x1bd1b8`, `0x1bd1c0` | `remtin` flag; stored app state | `STATIC` |
| `0x1bd20c` | Card state (must not be 2 for writable mass storage) | `INFERRED` |
| `0x1bd240` | Mass-storage write latch (one boot) | `STATIC` |
| `0x1bd241` | USB mode: 0 ORIGINAL, 1 PTP, 2 MASS STR | `STATIC` |
| `0x1bd294` | USB console ready | `STATIC` |
| `0x1bd2c8`, `0x1bd2cc` | tDcx selector; line-handler state | `STATIC` |
| `0x1bd2ec` | Remote-in flag (zeroed by a USB session event) | `STATIC` |
| `0x1bd394`, `0x1bd39c` | qADJUSTUSB id; input selector | `STATIC` |
| `0x1bd3a8` | Card-probe done (file task); loader and update path wait on it | `STATIC`, `HW` |
| `0x1bd9a4` | Update prompt answer (`0xe` = Yes) | `STATIC` |
| `0x1bd9b8`, `0x1bd9d0` | Decoder and resize semaphores | `EMU` |
| `0x2bfc94` | Resize semaphore object observed in the emulator ([04](04-application-architecture.md)) | `EMU` |
| `0x1bec28` | Cached flash geometry entry | `STATIC` |
| `0x1bef92` | Display-mode byte read by `0x6e91c` | `STATIC` |
| `0x1f4db0`, `0x1f4db3` | LUT selector cache; must be cleared after a module upload | `STATIC`, `HW` |
| `0x1f4dd8` | Thumbnail CCD2 wait semaphore (producer unknown) | `EMU` |
| `0x1f4e7a`, `0x1f4e7b` | Focus type; RL2 state (2 = full press pending at focus start) ([frontier 2](../../analysis/emu-frontier-2/README.md)) | `STATIC` |
| `0x1f5074`, `0x1f5078` | WB and light-source bytes read by the EXIF writer ([writers](../../analysis/emu-writers/README.md)) | `STATIC` |
| `0x1f502f`, `0x1f501c`, `0x1f511c` | Monitor (live view) running; display slot; VD counter ([liveview.py](../../tools/koma/features/liveview.py), [peaking.py](../../tools/koma/features/peaking.py)) | `STATIC` |
| `0x1fb200` | `/IROM` mounted flag ([/IROM fallback](../../analysis/sdhc/irom-fallback/README.md)) | `EMU` |
| `0x1bf028`, `0x1bf02a` | Focus-state byte (stock writer `0x70994`); stored distance ([fullpress.py](../../tools/koma/features/fullpress.py), [frontier 2](../../analysis/emu-frontier-2/README.md)) | `STATIC` |
| `0x2023f0`, `0x2023f4`, `0x2023f6` | CA-1 decoder last edge, state, busy | `STATIC` |
| `0x235964` | Saved Monitor return address | `STATIC` |
| `0x2358dc` | System tick counter | `HW 2026-11-06 main` |
| `0x237f40` | Console ring | `STATIC` |
| `0x23a870` | State value; 3 on battery END | `STATIC` |
| `0x23a880` | AV-jack state | `STATIC` |
| `0x23a888` | USB attached (follows pin `0x38`) | `INFERRED` |
| `0x23a88c` | Console-to-USB enable | `STATIC` |
| `0x23a890` | Shutdown reason word | `EMU` |
| `0x23a8a0` | GT-1 lens-correction flag | `STATIC` |
| `0x23b068` | `cp` shooting parameters; `cp+0x1d` special state, `cp+0x22` mode, `cp+0x40` CC1 select | `STATIC` |
| `0x23b085` | Special state: 1, 3, 4 force selector `0x20` | `STATIC` |
| `0x23b08a` | Color / B&W / TE (0/1/2) | `STATIC` |
| `0x2b9d30`, `0x2b9d34`, `0x2b9d38`, `0x2b9d39` | Dial state; `0x2b9d39` virtual dial | `STATIC` |
| `0x2b9d84`, `0x2b9dae` | Key state array; copy tested by `0x32540(0)` | `STATIC` |
| `0x2b9dd8` | Injected key actions (no upper bound) | `STATIC` |
| `0x2b9e38` | Overwritten by `defkey` code `0x60` | `EMU` |
| `0x2b9e5c` | Update abort flag (only a clear at `0x52022`; no setter) ([03](03-firmware-image-and-update-format.md)) | `STATIC` |
| `0x2b9e5d`, `0x2b9e60` | Reboot flag and value (`poff`) | `STATIC` |
| `0x2b9e92` | Battery state | `STATIC` |
| `0x2bf81c` | `KernPool` object | `STATIC` |
| `0x2bfccc` | Decoder semaphore | `EMU` |
| `0x2bd9d8`, `0x2bda18` | Rate-control quantiser RAM ([writers](../../analysis/emu-writers/README.md)) | `STATIC` |
| `0x2c0394` | Timer semaphore held by `0x35ac8` | `EMU` |
| `0x2c2d28`, `0x2c2d30` | `CONFIG.TST` target; `adjcom` log flag | `STATIC` |
| `0x2c9e28`..`0x2c9e38` | OPE queues | `STATIC` |
| `0x2c9e40` | OPE gate flag | `STATIC` |
| `0x2c9e48` | Key/battery polling enable | `STATIC` |
| `0x2c9e3c` | Key-funnel condition word (checked at `0x517e6`) ([stall](../../analysis/stall/ROOT-CAUSE.md)) | `STATIC` |
| `0x2cf640`, `0x2cf658` | Live-view ring pointer tables, Y and CbCr ([live-view frames](../../analysis/liveview/frames/README.md)) | `STATIC` |
| `0x2d1bd0` | Shutdown event group | `STATIC` |
| `0x2d24fc` | Stored focus target (1024 / distance) | `EMU` |
| `0x2d2768` | AE result struct, filled at `0x41704` ([writers](../../analysis/emu-writers/README.md)) | `STATIC` |
| `0x2de650`, `0x35d23c` | `qPTPTRAN` queue; `UsbShutter` HISR control block ([VBUS](../../analysis/vbus-shutter/README.md)) | `STATIC` |
| `0x1a8d14`, `0x2c02ec` | LCD driver mutex handle; mutex object. Leaked by `0x23578` on the capture path when the panel is off: the cause of the second-session tether stall ([04](04-application-architecture.md), [stall](../../analysis/stall/ROOT-CAUSE.md)) | `HW` |
| `0x2d2a34`, `0x2d2a40`, `0x2d2a50`, `0x2d2a90` | RAM Make, Model, ImageDescription, Copyright | `STATIC` |
| `0x2f8498` + n x `0xa8c0` | OSD layers (1 icons, 2 menus, 3 dialogs) | `STATIC` |
| `0x35fdec..0x366de8` | Stacks (SVC `0x3ffc`, IRQ `0x1000`, FIQ `0x2000`) | `STATIC` |
| `0x36adec..0x3dadec` | `KernPool` | `STATIC` |
| `0x3c7d64..0x3d1984` | Module resident blocks observed in KernPool (before the static resident) | `HW 2026-11-06 main` |
| `0x3dadec..0x422800` | Low-RAM gap (`0x47a14` B) with no stock use; Koma links its resident segment here ([01](01-hardware-and-memory-map.md)) | `STATIC` |
| `0x3dae00..0x3e4f78` | koma04w static resident segment (41,336 B; canary to `0x3e4f88`); `KOMALOG` reports `RES 003DAE00` ([koma04w](../../analysis/sdhc/bundle/README.md), [linker.py](../../tools/koma/linker.py), [01](01-hardware-and-memory-map.md)) | `HW` |
| `0x422800` | Application RAM ceiling | `STATIC` |

## Settings block (`0x1b000`)

Byte meanings and values in [05](05-settings-and-adjustment-store.md); the machine-readable map is [settings map](../../tools/komaset/settings-map.json).

| Address | Meaning | Tag |
|---|---|---|
| `0x1b000` | Magic `MG1H` ([05](05-settings-and-adjustment-store.md)) | `HW` |
| `0x1b012`..`0x1b01c` | Shutter-life counts: HALF/NEAR END/END table, SHOT `0x1b016`, HALF `0x1b018`, NEAR END `0x1b01a`, REBOOT `0x1b01c` ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b030` | IMG SET value (3 = B&W, 6 = B&W(TE)) ([05](05-settings-and-adjustment-store.md)) | `HW` |
| `0x1b031`, `0x1b034`, `0x1b197`, `0x1b1a7` | First detail byte: custom 1, custom 2, B&W, TE ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b038`, `0x1b0da` | Marker bytes of two pre-1.08 groups ([1.x diff](../../analysis/version-diff/1x/README.md)) | `STATIC` |
| `0x1b047`, `0x1b048` | Per-scene flash for scene ids 5 and 8 ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b050` | PHOTOMETRY ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b053` | EXPOSURE COMP. (odd 1..25, default 13) ([05](05-settings-and-adjustment-store.md)) | `HW` |
| `0x1b054` | WHITE BALANCE (1 = OUTDOORS, 7 = DETAIL) ([05](05-settings-and-adjustment-store.md)) | `HW` |
| `0x1b055` | DATE IMPRINT ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b056` | Language ([05](05-settings-and-adjustment-store.md)) | `EMU` |
| `0x1b058` | FOCUS: 0 MULTI AF, 6 SPOT AF, 1 MF, 2 SNAP, 3 INFINITY ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b059` | ISO: 0 AUTO, 1 64, 2 100, 4 200, 6 400, 7 800, 8 1600 ([05](05-settings-and-adjustment-store.md)) | `HW` |
| `0x1b05c` | AUTO BRACKET (zeroed every boot) ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b05d`, `0x1b0c4`, `0x1b191`, `0x1b192` | ADJ-button slots 2, 1, 3, 4 ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b060` | SHARPNESS (1.x item, still passed to capture) ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b068` | Quality ([05](05-settings-and-adjustment-store.md)) | `EMU` |
| `0x1b06f` | FLASH mode ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b074`, `0x1b076` | Manual WB R, B (clamped `0x10..0x310` at `0x9af40`) ([06](06-image-pipeline-and-color.md)) | `STATIC` |
| `0x1b080` | Copy of the copyright literal (not read by the writers) ([writers](../../analysis/emu-writers/README.md)) | `STATIC` |
| `0x1b09c` | Display mode (advanced by DISP) ([05](05-settings-and-adjustment-store.md)) | `EMU` |
| `0x1b0a6` | WB DETAIL step (index into `0x080103a0`/`0x080103c4`) ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b0c5` | M index ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b0d7` | USB mode ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b0f6..0x1b0fb` | JPEG quant bias ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b0fc` | Aspect ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b18c`, `0x1b18d`, `0x1b18e` | DIAL DIRECTION, ADJ SHTR BTN SET, SYNCHRO MONITOR ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b18f`, `0x1b190` | 2.01 marker group (`'U'`, `0xAA`) ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b193` | RAW+JPEG quality ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b194` | CL-BKT third frame (0 = ON) ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b195`, `0x1b196` | 2.30 sepia contrast/sharpness (orphaned) ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b198` | B&W sharpness ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b199..0x1b1a0`, `0x1b1ab..0x1b1b2` | My-Settings copies ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b1a1`, `0x1b1a2`, `0x1b1a3` | 2.30 group; 2.30/2.31 fix-up; 2.40 group ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b1a5` | FLASH EXPO. COMP. (validator `0x6fa90`, ADJ display `0x80288`) ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b1a6` | INFO DISP ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b1a8`, `0x1b1a9`, `0x1b1aa` | TE sharpness; TE tone (1..6); TE density (1..5) ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b1b3..0x1b2f7` | Unreferenced bytes ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b2f8..0x1b327` | Lens-error log (3 x `0x10`; writer `0x38610`; tail memset start, `0x1b308` in 2.10, `0x1b300..0x1b32f` in 2.31) ([2.x diff](../../analysis/version-diff/2x/README.md), [caves](../../analysis/menu-redesign/caves/README.md)) | `STATIC` |
| `0x1b328..0x1b337` | Older firmware's longer log tail (two BCD time stamps on the main body) ([05](05-settings-and-adjustment-store.md)) | `HW` |
| `0x1b338..0x1b7ff` | Free tail (no code reference; `0xff` on the main body) ([05](05-settings-and-adjustment-store.md)) | `HW` |
| `0x1b800..0x1b8af` | Legacy Koma `RCP1` store; unused by current builds ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x1b8b0`, `0x1b8b4` | Koma magic `KOMA`; Koma settings version (1) ([05](05-settings-and-adjustment-store.md)) | `HW` |
| `0x1b8b5..0x1b8cf` | Koma menu rows, one byte each (SNAP DIST `0x1b8b5` ... LONG EXP LIMIT `0x1b8ba`) ([05](05-settings-and-adjustment-store.md)) | `EMU` |
| `0x1b8bb`, `0x1b8bc` | MENU LAYOUT; KOMA FEATURES (FULL / ESSENTIALS) ([05](05-settings-and-adjustment-store.md)) | `EMU` |
| `0x1b8bd` | SAVE TO CARD action (writes `PARAM.SAV`) ([05](05-settings-and-adjustment-store.md)) | `EMU` |
| `0x1b8be` | STALL FIX FLAGS (no row; set by host `-KSET`; high nibble `0xA` validates the fix mask) ([stallfix.py](../../tools/koma/features/stallfix.py)) | `HW` |
| `0x1b8bf` | PEAKING (read per live-view frame by the VD-task hook `0x95af4`) ([05](05-settings-and-adjustment-store.md)) | `EMU` |
| `0x1b8c0`, `0x1b8c1..0x1b8c9` | Active recipe id; EDIT RECIPE p0..p8 ([05](05-settings-and-adjustment-store.md)) | `HW` |
| `0x1b8ca`, `0x1b8cb`, `0x1b8cc`, `0x1b8cd` | SAVE TO CUSTOM; RESET; GRAIN; VIGNETTE ([05](05-settings-and-adjustment-store.md)) | `EMU` |
| `0x1b8d0..0x1b8dc` | Settings-file header `KSIO`: version `0x1b8d4`, firmware tag `0x1b8d5..0x1b8db`, last-import status `0x1b8dc` ([05](05-settings-and-adjustment-store.md), [settingsio.py](../../tools/koma/features/settingsio.py)) | `HW` |
| `0x1b900..0x1bbff` | Koma recipe store `RCP6` v6 (header, 20 records of 12 B from `0x1b910`; SELECT color / range `0x1b909` / `0x1b90a`) ([05](05-settings-and-adjustment-store.md), [bwrecipes.py](../../tools/koma/features/bwrecipes.py)) | `EMU` |
| `0x1ba00..0x1ba13` | `RCP6` FX table: one byte per recipe, `0x50 \| grain \| vignette << 2` ([05](05-settings-and-adjustment-store.md)) | `EMU` |
| `0x1bffc..0x1bfff` | Checksum: big-endian u32, the sum of big-endian u16 words over `0x000..0xffb` (computed by `0x3d9b8`, stored by `0x4949c`) ([05](05-settings-and-adjustment-store.md)) | `HW` |

Earlier analyses described the checksum as a u16 sum; it is the u32 above.

| Routine | Meaning | Tag |
|---|---|---|
| `0x3d4a4` | Settings load, validation, defaults and marker migration ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x3d9b8` | Checksum over `0x000..0xffb` ([05](05-settings-and-adjustment-store.md)) | `HW` |
| `0x3d5fe` | Defaults ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x3d752..0x3d804` | 2.40 migration writers for groups `0x1b18f`, `0x1b1a1`, `0x1b1a2`, `0x1b1a3` ([05](05-settings-and-adjustment-store.md)) | `EMU` |
| `0x3d84e..0x3d986` | Always-reset items ([05](05-settings-and-adjustment-store.md)) | `EMU` |
| `0x28630`, `0x3d90c`, `0x32b8c`, `0xce010` | Save triggers: power-off, end of load, update, service ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x2978c`, `0x298f0`, `0x296a8` | My Settings save, load, defaults ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x6dc18..0x6fa90` | Per-setting validators ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x5b4e4`, `0x5a260`, `0x6c4bc` | Menu commit: still page, P-A-M pages, SETUP ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x6ef88`, `0x3e754` | RESTORE DEFAULTS; service preset ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x55d80` | File remove (used by the `PARAM.DAT` one-shot) ([05](05-settings-and-adjustment-store.md)) | `STATIC` |

## Adjustment store (`0x08010000`)

| Address | Meaning | Tag |
|---|---|---|
| `0x08010002/3` | ADJ version | `STATIC` |
| `0x08010008..0x0801000b` | Factory date (BCD) | `STATIC` |
| `0x0801000c..0x0801000f` | Package date (MakerNote tag 5 source via `0x193a80`) ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x08010016..1b` | Calibration-derived setting defaults; `0x08010017` language (`0xff` forces USB mode 0) | `STATIC` |
| `0x08010020` | MakerNote tag 5 source (16 B) | `STATIC` |
| `0x08010096..0ab` | FINE/NORMAL JPEG targets | `STATIC` |
| `0x08010312` | ISO setting 8 gain (200%) | `STATIC` |
| `0x080103a0`, `0x080103c4` | Per-body WB gain tables indexed by WB DETAIL ([05](05-settings-and-adjustment-store.md)) | `STATIC` |
| `0x080106e0` | Focus-type calibration read by `0x70b8c` ([frontier 2](../../analysis/emu-frontier-2/README.md)) | `STATIC` |
| `0x080103e8` | Defect list valid (`0xAA`) | `STATIC` |
| `0x08010431` | Calibration byte (default `0x0e`; `0xff` hangs boot) | `EMU` |
| `0x08010892`, `0x08010894` | SNAP distance (410 = 1024/2.5 m; also read by `0x36ff0`, `0x712a4`); infinity preset ([snapdist.py](../../tools/koma/features/snapdist.py), [frontier 2](../../analysis/emu-frontier-2/README.md)) | `STATIC` |
| `0x08010a04..0x08010af7` | Filter parameter table | `STATIC` |
| `0x08010b04..0x08010c54` | YH, ENH, knee, ColorNR, RGB filter, interpolation, LowPass groups | `STATIC` |
| `0x08010c74`, `0x08010c75` | Pre-2.30 B&W contrast/sharpness | `STATIC` |
| `0x08010c78..0x08010c7e` | Color NR thresholds | `STATIC` |
| `0x08010de2` | Battery calibration | `STATIC` |
| `0x08011304` | Live-view color preset | `EMU` |
| `0x08011308`, `0x08011330` | Per-mode color words; chroma-gain table | `STATIC` |
| `0x0801137c/d` | Chroma coring source (0) | `STATIC` |
| `0x08011380..94` | Matrix rows (0 in stock) | `STATIC` |
| `0x080113f0..f7` | Tone-curve selectors | `STATIC` |
| `0x080113f8` | Luma word for `0x1201800c` | `STATIC` |
| `0x080113fc` | Value for `0x12018174` | `STATIC` |
| `0x08011ffc` | Recomputed sum | `STATIC` |
| `+0x18ed` | AF contrast-peak threshold (92 -> 85 in 2.31/2.40); one reader `0xeaab2` in comparator `0xeaa8c` (2.31 `0xe95c6` in `0xe95a0`), used when `*(0x92d1c()+3) == 1` or `0x1bf041 == 1`. The neighbouring `+0x18ec` is unchanged and has 15 readers (`0x702b6` ... `0xee74c`) ([05](05-settings-and-adjustment-store.md)) | `STATIC` |

| Routine | Meaning | Tag |
|---|---|---|
| `0x27af4`, `0x27b7c` | Store <-> file copy helpers | `STATIC` |
| `0x27bc0` | ROM defaults to RAM (`UEEAB`); used on a bad sum | `STATIC` |
| `0x27bdc` | Boot store load (via `0x1e444`) | `STATIC` |
| `0x27c28` | `UEEF1` reload | `STATIC` |
| `0x27c5c` | `UEE00` commit | `STATIC` |
| `0x27cbc`, `0x27d54`, `0x27da0` | `UEE50`, `UEE51`, `UEE53` | `STATIC` |
| `0x27d0c` | `UEE52`: writes the flash store to `/ATA1/ADJROM.DAT` (not a loader) | `STATIC` |
| `0xdc7d0`, `0xd0be0` | Callers passing selector `0xe`: factory adjustment (guard flag `0x1ffe3e`; writes `0x08010162/163`), console command ([06](06-image-pipeline-and-color.md), [frontier 2](../../analysis/emu-frontier-2/README.md)) | `STATIC` |

## Image SDRAM

| Address | Meaning | Tag |
|---|---|---|
| `0x08000000` | Image SDRAM base | `STATIC` |
| `0x08422800`, `0x0842f280` | OSD pages | `STATIC` |
| `0x8470000` | Live-view ring: 3 frames of 640 x 244, stride 672; Y slots `+0`, `+0x2aa80`, `+0x55500`, CbCr `+0x7ff80`, `+0xaaa00`, `+0xd5480` ([live-view frames](../../analysis/liveview/frames/README.md)) | `STATIC` |
| `0x8570000`, `0xb45f500` | Playback resize Y / UV | `STATIC` |
| `0x8605f00`, `0x8f40e00` | Master Y / UV (stride `0xd00`) | `STATIC` |
| `0x987bd00`, `0x987bd20` | RAW RGB (stride `0x19f0`); upsampled shading map | `STATIC` |
| `0x08b84000`, `0x08c00000` | Updater buffer; `REFLECT3` test buffer | `STATIC` |
| `0xbffb3bc` | 160 x 120 shading source map | `STATIC` |

## Flash blocks

| Block / sector | Meaning | Tag |
|---|---|---|
| `0` | Monitor (`MON`) and info sector `0x7e` (`INF`) | `HW` |
| `1..0xc` | Replacement table and spare pool (`REP`) | `STATIC` |
| `0xd..0x3b` | Main body (logical) | `STATIC` |
| `0xd..0x2e` | Stock image; 35 erases and 2,152 programs per install | `HW`, `EMU` |
| `0x3c` | Main info at sector `0x7e`; erased first, programmed last | `STATIC` |
| `0x3d` | Adjustment store; `0xAA`, sum over `0..0x1b00` = `0x0118adb3` (main body) | `HW` |
| `0x3e..0x52` | Extended data | `HW` |
| `0x53` | Unused | `STATIC` |
| `0x54..0x1ff` | `/IROM` store; `0x55`, `0x66` written every boot | `STATIC`, `EMU` |
| sector `0x3300` | Where the dump stops | `HW` |

## MMIO registers

| Address | Meaning | Tag |
|---|---|---|
| `0x10200000` | USB controller (M66592 family) | `STATIC` |
| `0x10800000` | OneNAND window | `STATIC` |
| `0x1081e000`..`0x1081e498` | OneNAND registers (see [01](01-hardware-and-memory-map.md#peripheral-bases)) | `STATIC` |
| `F221h` | OneNAND config: `0x41e0` early, `0x40e0` after init, `0x40c0` stalls | `EMU` |
| `F24Dh`, `F24Eh` | Block number before lock; interrupt/lock (never read) | `STATIC` |
| `0x11000004` | SDRAM capacity | `STATIC` |
| `0x12000000` | INTC: `+0x04` soft reset, `+0x08` mask, `+0x10` pending; reset mask `0x3fffe`/`0x3ffff` | `STATIC` |
| `0x12000024` | Polled by Monitor `0x24c` | `EMU` |
| `0x12010000` | System timer: reload `+0x00` (`0xfd20`), `+0x10` mask, `+0x14` ack; channel 2 reload `+0x08` | `STATIC` |
| `0x12010004` | Free-running counter (counted on two of four boots) | `HW 2026-11-06 main` |
| `0x1201000c` | Second timer word (read 0) | `HW 2026-11-06 main` |
| `0x1200c080`, `0x1200c088`, `0x1200c00c` | OSD base, stride, enable | `STATIC` |
| `0x1200c150`, `0x1200c16c` | DMA; DMA latch (LNIR/PXIR9) | `STATIC` |
| `0x1201401c` | Capture timing status: bit 31 valid, bits 23:16 window | `EMU` |
| `0x12014024` | `vcycle` (low 12 bits) | `STATIC` |
| `0x12014040`, `0x12014044` | Frame ack; enable (with INTC bit 3) | `STATIC` |
| `0x1201407c` | Fields unknown | `STATIC` |
| `0x12018000..` | CC2 color stage, see [ISP registers](#isp-registers) | |
| `0x1201c000`, `0x1201c01c`, `0x1201c020` | JPEG control, mask, status | `STATIC` |
| `0x12020004`, `0x12020010`, `0x12020014`, `0x12020018` | VEN enable; reset value; mask; status | `STATIC` |
| `0x12020100` | Palette (63 entries) | `STATIC` |
| `0x12024000`, `0x12024200` | USART (panel, codec) | `STATIC` |
| `0x12028000` | Monitor UART | `EMU` |
| `0x12030000` | GPIO: `+0x1c` polarity, `+0x20`/`+0x24` data, `+0x2c`/`+0x30` pending, `+0x34`/`+0x38` mask | `STATIC` |
| `0x12034000`, `0x12034004` | Resize control; status | `STATIC` |
| `0x12044000` | SD host controller (TMIO family `INFERRED`) ([01](01-hardware-and-memory-map.md)) | `STATIC` |

## Header offsets and tags

| Offset / tag | Meaning | Tag |
|---|---|---|
| `+0x00` | Magic `R_MG1_REFLECTOR` XOR `0x80` | `STATIC` |
| `+0x10` | Type: 1 Monitor, 2 main, 3 main, 5 adjustment text, 6 extended data, 7 RAM carrier | `STATIC` |
| `+0x11` | Format (1 for type 2, 3 for type 5) | `STATIC` |
| `+0x12..+0x17` | Version and bounds; official `02 40 00 00 00 00` | `STATIC` |
| `+0x18`, `+0x1c` | Date (BCD), time bytes | `STATIC` |
| `+0x20`, `+0x24`, `+0x28` | Load, length, entry | `STATIC` |
| `+0x2c` | Total file size (`0x219b68` for the official `qrflect3`) ([03](03-firmware-image-and-update-format.md)) | `STATIC` |
| `+0x30` | Weighted checksum | `STATIC` |
| `+0x1f0` | Tail `R_MG1_UPDATED!!!` (checked at boot) | `STATIC` |
| `0x200` | Header size | `STATIC` |
| MakerNote `0x1`, `0x2`, `0x3`, `0x6` | `Rdc\0`; `Rev%4.4x`; LONG 1 (8); reserved 0 | `STATIC` |
| MakerNote `0x1001` | 48-byte shot record; byte 40 = IMG SET | `STATIC` |
| MakerNote `0x1002` | Offset of `0x1001` data | `STATIC` |
| MakerNote `0x1003` | LONG from `shot+0x15`; 1 on 1.08, 0 on 2.40 | `HW` |
| MakerNote `0x2001` | Camera Info (22 entries; tag 2 low 16 = JPEG quant scale) | `STATIC` |
| Camera Info `0xa`, `0xb`, `0x10` | Share one offset; `0x10` never written (zero-length memset at `0x112746`) ([fn table](../../analysis/sibling-arm-diff/feature-table.md)) | `STATIC` |

## Constants

| Value | Meaning | Tag |
|---|---|---|
| `05ca:2218` | ORIGINAL USB mode (bulk IN `0x81`, bulk OUT `0x02`, interrupt IN `0x83`) | `HW` |
| `05ca:0331`, `05ca:0109` | PTP; MASS STR | `STATIC` |
| `0x1e..0x22` | Tone selectors for contrast 1..5; `0xe` sixth slot | `STATIC` |
| `0x18`, `0x19`, `0x1a`, `0x1c`, `0x1d`, `0x1e` | Update screens: prompt, updating, rejected, checking, battery, no file | `STATIC` |
| `0xAA`, `0x55` | Store magic values accepted by `UEE00` | `STATIC` |
| `0x54`, `0x55` | Engine dump / restore sub-codes | `STATIC` |
| `0x5c`, `0x5d`, `0x5e` | Opcode interpreter: dump, erase, dump | `STATIC` |
| `0x406800`, `0x422800`, `0x2f0000` | Size limits: header window, range check, program refusal | `STATIC` |
| `0x5400`, `0x4c00` | OneNAND status for program / erase on a locked block (datasheet) ([01](01-hardware-and-memory-map.md)) | `STATIC` |

## CA-1 VBUS decoder

| Address | Meaning | Tag |
|---|---|---|
| `0x1319e8` | Init (HISR `UsbShutter`, pin `0x38`) | `STATIC` |
| `0x13191c`, `0x131960` | ISR A (asserted), ISR B (released) | `STATIC` |
| `0x1319a4`, `0x131c78` | HISR body; dispatcher on state | `STATIC` |
| `0x131a78`, `0x131b3a` | Pulse classifier; state machine and key emitter | `STATIC` |
| `0xc49bc`, `0xc5088` | tPtpTran start-up; dispatch of message `0x20` | `STATIC` |

## Stock boot options

| Address | Meaning | Tag |
|---|---|---|
| `0x13f010` (`0x13f066`, read `0x13f06e`) | MASS STR attach hook; `0x131cb4(latch ? 2 : 1)` | `STATIC` |
| `0x144f78`, `0x145474`, `0x1446b0`, `0x15722c` | MODE SENSE (write-protect `0x80` unless writable); write/format; SCSI dispatch; write-protect source | `STATIC` |
| `0x235200` | RW/RO mode (latched per boot) | `STATIC` |
| `0x3d9d0` | UI mode setup after the counters screen (stores `0x1b97d8..e0` only) | `STATIC` |
| `0x4c8f0`, `0x4c95c` | `/IROM/PICTDATA.RDD` loader; path | `STATIC` |

## Koma firmware and package

Generation 1 (the generation 1 image, tag `Rev024M`) adds the loader and hook to 2.40. Generation 2 Koma firmware, build 2b (tag `240K2`) adds SDHC/SDXC initialisation and the `/IROM/GRMOD.BIN` fallback ([generation 2 driver](../../analysis/sdhc/driver/README.md)). The package `GRMOD.BIN` patches RAM only.

| Address | Meaning | Tag |
|---|---|---|
| `0x281ee` | Loader hook: `bl 0x282cc` -> `bl 0x57f78`; the loader calls `0x282cc` first ([loader.py](../../tools/koma/loader.py)) | `HW` |
| `0x57f78` | Loader entry; see [Code caves](#code-caves-and-patch-sites) | `HW` |
| `0x112578` | `Rev%4.4x` format slot rewritten to `Rev024M` (generation 1) or `240K2` (generation 2) | `HW` |
| `0x57fa4` | Loader call of `open_both` ([generation 2 driver](../../analysis/sdhc/driver/README.md)) | `STATIC` |
| `0x580cc..0x58104` | Generation 2 `open_both` tail (56 B): open card `GRMOD.BIN`; if that fails, mount drive 0 (`0x49384(0)`) and open `/IROM/GRMOD.BIN` ([generation 2 driver](../../analysis/sdhc/driver/README.md)) | `HW` |
| `0x49384` | Mount drive; `0x49384(0)` from the loader mounts `/IROM` ([generation 2 driver](../../analysis/sdhc/driver/README.md)) | `HW` |
| `0x57f90` | Generation 2 loader wait: sleep(10) -> sleep(40) per poll, about 2.8 s -> 10 s ([generation 2 driver](../../analysis/sdhc/driver/README.md)) | `STATIC` |
| `0x57288`, `0x573f2` | Generation 2 SD init redirects (`t_stock`, `t_common`): re-initialise SDHC cards with CMD8 and ACMD41 HCS=1 ([generation 2 driver](../../analysis/sdhc/driver/README.md)) | `HW` |
| `0x57320` | ACMD41 retry limit 17 -> 25 ([generation 2 driver](../../analysis/sdhc/driver/README.md)) | `STATIC` |
| `0x57274..0x57ac0` | Generation 2 code in the Dcfs island: SD init shims (`0x578b8..0x579c4`) and loader helpers (`0x57a08..0x57ac0`); runs on hardware ([01](01-hardware-and-memory-map.md), [generation 2 driver](../../analysis/sdhc/driver/README.md)) | `HW` |
| `0x4fe44` | `tether`: exclusive `bl` redirect over `ldr r0, =0x23a864; ldr r1, [r0, #0x24]`; skips the state-9 force while `remtin` and key injection are on ([tether.py](../../tools/koma/features/tether.py)) | `HW` |
| `0xcd090` | `tether`: redirect at the `-CCTL remtout` call ([tether.py](../../tools/koma/features/tether.py)) | `HW` |
| `0xcd03a`, `0xcd05e` | `-CCTL keyset` / `diaset` call sites (`0x65cfc`, `0x65d70`) ([tether.py](../../tools/koma/features/tether.py)) | `STATIC` |
| `0x1fb784`, `0x1fb794`, `0x1fb7a4` | `-FDWL` record (ID `0x1a`): name, args flag, handler; `liveview` turns it into `-LVFR` ([liveview.py](../../tools/koma/features/liveview.py)) | `HW` |
| `0x95af4` | `peaking` VD-task hook ([peaking.py](../../tools/koma/features/peaking.py)) | `EMU` |
| `0x117382` | SELECT (selective color) YUV pass hook ([06](06-image-pipeline-and-color.md)) | `HW` |

Earlier designs used a 32-byte tether cave at `0x56f80`; the shipped `tether` feature uses only the redirects above.

## Same function across versions

| 2.40 | 1.08 | 2.10 | 2.31 |
|---|---|---|---|
| Tone tables `0x193b30` | `0x18e3cc` | `0x18ff10` | `0x191514` |
| IMG SET table `0x1bd452` ([code map](../../analysis/feature-code-map/README.md), [2.x diff](../../analysis/version-diff/2x/README.md)) | | | `0x1bacda` |
| AVI writer `0x8909c` ([code map](../../analysis/feature-code-map/README.md), [2.x diff](../../analysis/version-diff/2x/README.md)) | | | `0x87e38` |
| Settings loader `0x3d4a4` ([update replay](../../analysis/emu-update-replay/README.md)) | `0x3d37c` | | `0x3d3f0` |
| USB table `0x1fb400` | `0x1f54e8` | | |
| MG1 table `0x177aa4` | `0x1727dc` | | |
| Contrast getter `0x92cc8` | | `0x69980` | `0x69c68` |
| Sharpness getter `0x91268` | | `0x8fa0c` | `0x8ff7c` |
| Mono writer `0x8ff0e` | | `0x8e716` | `0x8ec86` |
| CL-BKT loop `0xa0840` | | `0x9edb4` | `0x9f3ec` |
| CA-1 init `0x1319e8` | `0x12d560` | `0x12f0b8` | `0x12fe88` |
| CA-1 pulse classifier `0x131a78` ([VBUS](../../analysis/vbus-shutter/README.md)) | `0x12d5f0` | | |
| CA-1 state machine `0x131b3a` ([VBUS](../../analysis/vbus-shutter/README.md)) | `0x12d6b2` | | |
| Update gate `0x1eb22` ([update replay](../../analysis/emu-update-replay/README.md)) | `0x1eb24` | | |
| Update path, `QUICKVER.UP` open, skip path `0x1eb36`, `0x1eb74`, `0x1ec74` ([update replay](../../analysis/emu-update-replay/README.md)) | `0x1eb38`, `0x1eb58`, `0x1ec5a` | | |
| Update function, scan, type-5 driver `0x5d18c`, `0x278d4`, `0xd6a62` ([update replay](../../analysis/emu-update-replay/README.md)) | `0x5c6f0`, `0x278b4`, `0xd3c36` | | |
| CPU1 key table `0x18108c` ([update replay](../../analysis/emu-update-replay/README.md)) | `0x17bd84` | | |
| Boot-sync flag `0x1bd3a8` ([1.x diff](../../analysis/version-diff/1x/README.md)) | absent (1.09: `0x1b7cdc`) | | |
| State-9 force `0x4fe44` | `0x4fb58` | | `0x4fcec` |
| Dcfs island `0x56f80` | `0x569ec` | | `0x56e20` |
| MakerNote builder `0x112120` | `0x10e174` | | `0x110a24` |
| Mass-storage latch `0x1bd240` ([stock options](../../analysis/stock-options/README.md)) | `0x1b796c` (1.09 `0x1b7b74`, 2.01 `0x1b868c`, 2.02 `0x1b86d4`) | `0x1b949c` | `0x1baac8` |
| `KernPool` object `0x2bf81c` ([master firmware](../../analysis/master-firmware/README.md)) | `0x2b6250` (2.01 `0x2b9170`) | | `0x2bbdfc` |
| DIAL DIRECTION wrappers `0x7e714..0x7e780` ([1.x diff](../../analysis/version-diff/1x/README.md)) | (2.01 `0x7cbdc..0x7cc48`) | | |

Sources: [1.x diff](../../analysis/version-diff/1x/README.md), [2.x diff](../../analysis/version-diff/2x/README.md), [update replay](../../analysis/emu-update-replay/README.md), [VBUS](../../analysis/vbus-shutter/README.md), [stock options](../../analysis/stock-options/README.md), [master firmware](../../analysis/master-firmware/README.md), [12](12-version-history.md).


## Open

- `0x1f4c88`/`0x1f4c8c`: written and read by `tCamExtAf` in an emulator boot watch, but read as the TE (Cb,Cr) table by `0x8ff0e`. One reading may be off by an offset.
- Which caller enforces which size limit (`0x406800`, `0x422800`, `0x2f0000`); only the last is known to act after the info erase.
- Physical dial labels: Ricoh's guide has six positions and no SETUP; firmware test strings label `diaset,0` SETUP and `diaset,4` STILL.
- Meaning of `0x1201401c` bits, `0x1201407c`, `0x1b0a2`, block `0x53`, MakerNote `0x1003`, the producer of `[0x1f4dd8]` and the alternate header-string set at `0xa88ee`.
- Terminal commands `raw`, `im`, `io`, `t`, `i`, `lk` (handlers in `0x17781c`) are not decoded.
- Matrix bytes b1 versus b2 (both G-like).
