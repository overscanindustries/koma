# 04. Application architecture (firmware 2.40)

The application is a 2,201,960-byte big-endian ARM/Thumb image entered at `0x1c054`, running Nucleus PLUS with 16 tasks at boot (plus `tScript` when a card script runs), a few message queues and one event group. `MainCtrl` runs startup, the SD-update gate and the script gate, then reaches `0x281ee`, the hook point the Koma loader (generation 1 and generation 2 Koma firmware) uses; `tOpeMain` owns keys and dial and `tCameraMain` owns shooting state. Card and `/IROM` modules run at that hook on hardware and patch `.text` and the develop path; task, queue, capture and shutdown internals below are from static reading and the emulator.

Tags: `HW`, `EMU`, `STATIC`, `INFERRED`, defined in [features](evidence-ledger.md). `EMU` here means CPU1 replies were fabricated and `/ATA1` stubbed. Runtime addresses; `qrflect3` file offset = runtime - `0x1be00`. Related: [01 memory map](01-hardware-and-memory-map.md), [02 boot chain](02-boot-chain-and-monitor.md), [03 updater](03-firmware-image-and-update-format.md#updater-flow), [05 settings](05-settings-and-adjustment-store.md), [07 USB](07-usb-protocol.md), [08 card scripts](08-card-scripts-and-service-terminal.md), [10 emulator](10-emulator-and-tooling.md), [address index](address-index.md), [how it works](../how-it-works.md).

## Key addresses

| Address | Meaning | Tag |
|---|---|---|
| `0x1c054` | Reset handler: copies the handoff words to `0x1c044..0x1c050`, saves LR at `0x235964` | `STATIC`, `EMU` |
| `0x1c1d8` | Return to the Monitor through `(*0x235964)()` | `STATIC` |
| `0x1cf58` | C init (via `bx` at `0x1c1bc`); creates `KernPool` (`0x70000` B) | `STATIC` |
| `0x1cfb4` | Task-create call (priorities from its arguments) | `STATIC` |
| `0x1a8acc` | Firmware task table, `0x1c` B per task `{prio/flags, name*, queue name*, TCB*, queue, 0x8c, depth}` | `STATIC` |
| `0x281e8` | `MainCtrl` entry | `EMU` |
| `0x1e7ac` | Startup function: settings load, service keys, update gate, script wait | `STATIC` |
| `0x1eb22` | Update gate | `STATIC` |
| `0x281ee` | Instruction after startup returns; Koma loader hook | `HW` |
| `0x282cc` | `post_startup` milestone | `EMU` |
| `0x8a3b8` | `tOpeMain` loop | `STATIC` |
| `0x8a74c` / `0x8a790` / `0x8a814` | `tOpeMain` receive / poll tick / dispatch on id at `+6` | `STATIC` |
| `0x34038` | `tCameraMain` message loop | `STATIC` |
| `0x51740` | Key injection sink `{code, action}` | `STATIC` |
| `0x50eb0` | Key poll; overlays injected array `0x2b9dd8` on scan array `0x2b9d84` | `STATIC` |
| `0x18108c` | Key table, 42 records of `0x24` B; index = event code | `STATIC` |
| `0x4fd1e` | Dial position -> app state; forces state 9 under USB | `STATIC` |
| `0x23b068` | Shooting-parameter block (`cp`) | `STATIC` |
| `0x96350` / `0x96436` | Still develop pass 1 / per-shot hook site (`bl 0x8ff0e`) | `HW` (hooked) |
| `0xafd20` / `0xafd3e` | Second develop pass / its hook site | `HW` (hooked) |
| `0x8ffc8` | Tone-table upload `(selector, arg)` | `STATIC` |
| `0x947e4` | Shutter wait on `0x1201401c` | `EMU` |
| `0x8e3d0` | Stock image load (menu header art, shutdown image) | `STATIC` |
| `0x32b74` / `0x32b8c` | Power-off wrapper / shutdown body | `STATIC`, `EMU` |
| `0x520c8` | CPU1 power-off sequence builder | `STATIC` |
| `0xca524` | CPU1 link | `STATIC` |
| `0x24a80` / `0x534be` | Console ring writer / USB console writer | `STATIC` |
| `[0x1a8d14]` | LCD driver mutex handle (blocking take `0x228e8`, 10,001-tick timeout) | `STATIC`, `HW` |

## Image and entry

The Monitor loads the payload and branches to `0x1c054` with LR `0x3c4`, four handoff words `[0, 0, 0, 0x1220000c]`, CPSR `0xd3` and OneNAND config `0x40e0` ([02](02-boot-chain-and-monitor.md)). Reset copies the words to `0x1c044..0x1c050`, saves LR at `0x235964`, sets SVC, IRQ and FIQ stacks only, writes the vector pointers (IRQ `0x1c214`, FIQ `0x1c4ec`) at `0x80..0x9c`, zeroes `.bss` and enters C init. The abort and undefined handlers (`0x174984`, `0x17496c`, `0x174978`) rely on the Monitor's banked stacks (IRQ `0x12201fc0`, FIQ `0x12201ec0`, abort `0x12201eb0`, undefined `0x12201ea0`, SVC `0x12201e90`). `STATIC`; stacks `EMU`; reliance `INFERRED`

| Region | Range | Tag |
|---|---|---|
| Settings block | `0x1b000..0x1bfff` (read by `0x4947c`) | `STATIC` |
| `.text` | `0x1c000..0x1a8880` | `STATIC` |
| `.data` (in place) | to `0x235968` | `STATIC` |
| `.bss` (zeroed by reset) | to `0x35fdec` | `STATIC` |
| Task stacks | `0x35fdec..0x366de8` | `STATIC` |
| `KernPool` | `0x36adec..0x3dadec` (`0x70000` B) | `STATIC` |

Full map in [01](01-hardware-and-memory-map.md). The image contains no CP15 instruction, and executing `MRC p15` from a module hangs the core. Module code runs in SVC mode. `HW 2026-11-06 main`

## Boot sequence

`0x1c054` -> `.bss` done `0x1c0e4` -> reset done `0x1c1bc` (about 1.8 M emulated instructions) -> C init `0x1cf58` -> tasks created (`MainCtrl` and `tOpeMain` first) -> `MainCtrl` `0x281e8` (3,131,753 virtual instructions from cold reset in the captured-Monitor model, no firmware stubs) -> CPU1 handshake, LCD, clock -> `0x1e7ac` (update gate at about 18-70 M; update function `0x5d18c` at about 28.8 M if taken) -> `0x281ee` -> `0x282cc` (`post_startup`). `EMU`; hook `HW`. Virtual instruction counts are not time.

### Tasks

Recorded at creation in the stock model run. Names are truncated to 8 characters by the RTOS; function = entry - 1 for Thumb entries (`OsdHisr` is recorded without the Thumb bit). The created-at column is virtual instructions in that harness. `EMU`

| Name | Function | Priority | Stack | Created at | Role |
|---|---|---|---|---|---|
| tIdle | `0x26bc4` | 255 | 1 KB | 1.84 M | Idle |
| MainCtrl | `0x281e8` | 90 (`0x5a`) | 16 KB | 1.96 M | Startup, update gate, script start, requests `0x5xxx`, shutdown |
| tFileTas(k) | `0x849c4` | 120 | 10 KB | 1.99 M | File system; receives `0x1102`, runs the script-gate check, replies `0x1202` |
| tOpeMain | `0x8a3b8` | 80 (`0x50`) | 8 KB | 1.99 M | Keys, dial, UI messages |
| tCameraM(ain) | `0x34038` | 82 | 16 KB | 2.39 M | Shooting-state commands (`0x21xx`, `0x31xx`, `0x720a`) |
| RecVDTAS(K) | `0x95f40` | 83 | 18 KB | 2.50 M | Recording, vertical drive (name only) |
| RecRL1TA(SK) | `0x95f98` | 70 | 18 KB | 2.50 M | Shutter half-press recording; runs the full-press capture |
| RecAUDTA(SK) | `0x97e38` | 71 | 8 KB | 2.50 M | Recording, audio (name only) |
| RecYuvT(ask) | `0x8ec04` | 82 | 20 KB | 2.50 M | YUV recording (name only) |
| tCamExtA(f) | `0x8fd5e` | 110 | 16 KB | 2.54 M | External autofocus |
| tCamPlaM | `0x4c9e0` | 100 | 16 KB | 3.16 M | Playback |
| tDcx | `0x53734` | 80 | 10,000 B | 8.10 M | USB command task, created by `0x53b0c(arg)` (arg 1 with the service keys held) |
| tPtpTran / tPtpData | `0xc5024` / `0xc5108` | 130 | 8 KB each | 8.10 M / 8.26 M | PTP transport / data |
| tAdjust | `0x54020` | 110 | 32 KB | 8.10 M | Adjustment/terminal (message `0x6101`) |
| OsdHisr | `0x6a770` | 80 | 16 KB | 8.52 M | OSD high-level ISR |
| tScript | `0x65220` | 80 | 16 KB | run time | Created by `0x65108` when the script gate is set; time slice 0 |

Lower number runs first. `tScript` and `tOpeMain` share priority 80, so a script's key events are delivered only when the script blocks. Control blocks carry signatures `TASK` (`0x5441534b`) and `EVNT` (`0x45564e54`) at `+0xc`, checked by `0x1d698` and `0x15ec8c`/`0x15ecbc`. The RTOS names itself Nucleus PLUS in a firmware string. `STATIC`, `INFERRED` (priority order)

The firmware's own task table at `0x1a8acc` uses the long names: `MainCtrl` is `tAplCtrl` (TCB pointer `[0x1a8ad4]`, queue qAPL `[0x1a8ad8]`), then `tOpeMain` `[0x1a8af0]`, `tFileTask` `[0x1a8b0c]`, `tCameraMain` `[0x1a8b2c]`, `tAdjust` `[0x1a8b48]`, `tCameraSub` `[0x1a8b64]`, `tCamPlaMain` `[0x1a8b80]`, `tDcx` `[0x1a8b9c]`. The semaphore wait `0x5d978` stores the waiting task's LR at `TCB+0xd0`. `STATIC`

A failed driver assert calls `0x289ec` -> `0x288b0`, which suspends the calling task. The emulator needed three supplied register values to stop such asserts: `0x11000004` (SDRAM capacity), `0x12020010` (VEN reset) and `0x1200c16c` (DMA latch); each was added only after an assert named it. `STATIC`, `EMU`

## MainCtrl startup (`0x1e7ac`)

`MainCtrl` calls `0x1e7ac` once and only then reaches `0x281ee`. The update code runs here, on `MainCtrl`. `STATIC`, `EMU`

| Site | Action | Tag |
|---|---|---|
| entry | `0x4947c(0x1b000, 0x1000)` loads the settings block; reads AV-jack state `0x23a880` | `STATIC` |
| `0x1e444` | Reads main-info `+0x12` into `0x1a8c00/04` (version screen, MakerNote tag 2) | `STATIC` |
| `0x1e88c`, `0x1ea32` | Call `0x1effa`: unless `0x3e8ec()` returns 1, opens and closes `/ATA1/RADJ/NADJ0001.ADJ` and sets existence flag `0x1a8c1a`; then `0x31a14(0x8000, n)` sends `0x1102` to the file task | `STATIC` |
| `0x1e98c` | Sets `0x23a88c = 1` (and `0x23a888 = 1`) if RIGHT + TELE is held and pin `0x38` reads low through 25 polls | `STATIC` |
| `0x1ea6e` | DOWN + WIDE test: stores 1 into the mass-storage write latch `0x1bd240` | `STATIC` |
| `0x1ea82`, `0x1ea8e` | Service-key test; `0x53b0c(1)` creates `tDcx` with selector 1 and `tPtpTran` via `0xc4914` | `STATIC` |
| `0x1eae8` | Waits on event mask 2 (camera message) when `0x1ef90()` returns 0, else mask 4 (`0x1202` from the file task) | `STATIC` |
| `0x1eb22` | Update gate | `STATIC` |
| `0x1eb74` | `/ATA1/QUICKVER.UP` check | `STATIC` |
| `0x1ec12` | After a successful update: `0x23a870 = 1`, `0x23a87c = 1`, `0x32b74` | `STATIC` |
| `0x1ec74` | Skip path; other combinations: DOWN + TELE `0x1f09c` (version), LEFT + TELE `0x1f1e0` (counters), RIGHT + WIDE `0x1f1b8` | `STATIC` |
| `0x1ece8`, `0x1ecf2` | RADJ file check; with the NADJ flag set and no script, falls into the NADJ loader `0x5443c` | `STATIC` |
| end | If `0x23a88c == 1 && 0x23a888 == 1`: `0x5d5d0(0x800)`, then `0x26b04(1)` (console to USB) unless `0x1f090()` returns 1 | `STATIC` |

**Script gate.** At `0x2821e` `MainCtrl` reads gate `0x1a90e4`; if set it calls `0x65108` at `0x2822c` (needs a `0x10000` buffer, selects the source, starts `tScript`). The gate is set in the file task at `0x55844..0x5585c`: if `0x3e8ec()` returns 1 the gate is 0, else `0x558ac` tries `/IROM/SCRIPT.MG1` then `/ATA1/SCRIPT.MG1`. The mode byte `0x1b9866` (set by `0x3c270` from pin 8) closes the gate when high. On the mask-4 path the store and the read are ordered; on the mask-2 path they race. `STATIC`, `EMU`. On the main body the stock dump script ran from a Playback start and did nothing from a POWER start (one activation). `HW`

**Loader hook.** `0x281ee` is reached only if no update prompt blocks. Nothing before it is patched by the Koma loader, so the update gate and card probe run stock. The loader waits on the card-probe flag `0x1bd3a8` (set at `0x55864`). Generation 2's loader also mounts `/IROM` when no card file opens (boot mode 1 leaves it unmounted while a card works); see [03](03-firmware-image-and-update-format.md#modified-images). `HW`, `STATIC`

**Other `MainCtrl` code.**

- `0x28bdc` handles `0x5xxx` requests and `0x720d`. In state 9 it drops the dial event `(0,4)`, and a code-7 press or code-8 release becomes `0x720d`, turned into request `0x5104` with value 1 (power-off, `INFERRED`). `STATIC`
- `0x2833e` calls `0x4fc90`, the only reader of the `-CCTL` remote flag `0x1bd1b8`; the branch needs `0x23a870 == 6` in state 9, which looks unreachable under USB. `STATIC`
- `0x28794(msg, policy)` waits on `0x28af0(0x235e30, 10000)` until the expected id arrives; `0x5200` always returns, `0x5104` and `0x5205` return with policy 1. `STATIC`

## Messages and queues

### Message format

Queue entries are `0x8c` bytes: a 12-byte header and up to 128 bytes of payload. Header `+0` length, `+2` and `+4` 16-bit routing fields (seen: `0x8000`, `0x8100`, `0x8400`, `3`), `+6` message id. Example: `0x3386c` builds `{len 0x10, 3, 0x8000, 0x2201}`. `STATIC`

| Primitive | Address | Tag |
|---|---|---|
| Queue send `(queue, msg, size, timeout)` | `0x5d7a8` | `STATIC` |
| Queue receive `(queue, buf, 0x8c, timeout)`, -1 on timeout | `0x5d878` | `STATIC` |
| Queue depth | `0x5d914` | `STATIC` |
| Semaphore wait (saves caller LR at `TCB+0xd0`) / release | `0x5d978` / `0x5d9a4` | `STATIC` |
| Sleep | `0x288f6` | `STATIC` |
| Tick getter | `0x5df20` | `STATIC` |
| Event-group set / wait (`EVNT`) | `0x15ec8c` / `0x15ecbc` | `STATIC` |
| Generic sender (type at `+6`, payload at `+12`) | `0x5d4b8` | `STATIC` |

### Message-id families

Range names are descriptive; the firmware does not name them. `STATIC`

| Range | Receiver / use | Ids seen |
|---|---|---|
| `0x1xxx` | File task / startup sync | `0x1102` -> file task, `0x1202` -> MainCtrl, `0x1203` in `tCameraMain` |
| `0x21xx` | `tCameraMain` commands | `0x2102`..`0x2113`, `0x2118`, `0x212b` |
| `0x2201` | Shutdown acknowledgement to MainCtrl | from `0x3386c` |
| `0x31xx` | `tCameraMain` requests | `0x3100`, `0x3101` (shutdown) |
| `0x5xxx` | Requests to MainCtrl | `0x5103`, `0x5104`, `0x5105`, `0x5200`..`0x5202`, `0x5205`, `0x5207`, `0x5208` |
| `0x6101` | One character of terminal input to `tAdjust` | |
| `0x71xx` | `tOpeMain` UI messages | `0x7102`..`0x7113` |
| `0x72xx` | Sender-side OPE ids; `0x7200`, `0x7201`, `0x7202` (key funnel `0x517dc`), `0x7204`, `0x720d` pass the OPE gate `0x8a678` even when `0x2c9e40` is 0 | |
| `0xa201` | Deferred UI message from `-VER` | |

### Queues

| Handle | Used by | Back-pressure in `0x8a678` | Tag |
|---|---|---|---|
| `0x2c9e28` / `0x2c9e2c` | `tOpeMain` input (qOPE) | Sender sleeps while depth >= `0x60` | `STATIC` |
| `0x2c9e30` | OPE-side queue | depth >= `0x20` | `STATIC` |
| `0x2c9e38` | Third OPE queue | depth >= 6 | `STATIC` |
| `0x1b96cc` | `tCameraMain` input | | `STATIC` |
| `0x1b96c4` | Target of `0x2201` (MainCtrl's) | | `STATIC`; queue owner `INFERRED` |
| `0x1a8ad8` | qAPL, MainCtrl requests (from `0x67ad4`: `0x5201`, `0x5202`, `0x5103`, `0x5207`, `0x5208`) | | `STATIC` |
| `0x1bd394` | qADJUSTUSB (terminal input) | | `STATIC` |

### `tOpeMain` loop

`0x8a3b8`: optional init `0x8a3ec`/`0x32314`, then loop: while `0x8a74c()` times out run poll tick `0x8a790`; dispatch with `0x8a814(0x2c9e60)`; exit when a handler returns -1, then spin forever at `0x8a3e6`. The receive timeout is `0x2c9e58 - (now - 0x2c9e5c)`, clamped at 0; the key scan runs every 50 ticks when idle. `STATIC`

| Id | Handler | Note |
|---|---|---|
| `0x7102` | `0x8a8a8` | |
| `0x7103` | `0x8a8b8` | Key or dial event -> sink `0x51740` |
| `0x7105` / `0x7106` / `0x7107` | `0x8a8c6` / `0x8a920` / `0x8a8d2` | |
| `0x7108` | `0x8a93e` | Calls CPU1 bit-command writer `0xca6b8` (route to CPU1 special `0xfa`) |
| `0x7109` / `0x710a` / `0x710c` | `0x8a950` / `0x8aa0c` / `0x8aa18` | |
| `0x710d` | `0x8aa5e` | -> `0x520c8` (power-off), returns -1 |
| `0x710e`..`0x7113` | `0x8aa6a`, `0x8aa7c`, `0x8aa88`, `0x8aaa4`, `0x8aabe`, `0x8ab00` | |

All `STATIC`. Meanings other than `0x7103` and `0x710d` are not known.

### `tCameraMain` loop

`0x34038`: pre-check `0x331d8()`, then loop on `0x5d878(0x1b96cc, buf, 0x8c, -1)` and switch on the id. `STATIC`

| Id | Action |
|---|---|
| `0x2102` | Mode enter: `0x337e4`, `0x334cc`, `0x330fc`, `0x33dc4`, `0x946da`; sets `0x1f5248 = 1` |
| `0x2103` | `0x338cc`, `0x330fc`, event-group clear `0x15ec8c(0x2d1bd0, 0, 2)` |
| `0x2107`..`0x2109` | Sub-states `0x1b9700`, `0x1b96e4`, `0x1b96e8` |
| `0x210b` | `0x334cc`, then `0x900f4(1, 0x23b068)` (mode and tone setup, `INFERRED`) |
| `0x210d` | `0x33f1c`, `0x95574` |
| `0x3101` | Shutdown, `0x3386c`; then `0x1b976c = 0` |
| `0x720a` | Sub-switch on the top byte |
| others | `0x2106`, `0x210c`, `0x210e`..`0x2113`, `0x2118`, `0x212b`, `0x3100` |

The `0x23b068` block is the shared parameter record for these handlers.

## App state and dial

### Dial to state

`0x51158` maps the MODE bits from CPU1 to a dial position (bits 0..7 -> 9 none, 4, 8, 7, 3, 1, 6, 10; 10 is transitional and ignored by `0x511ec`). With injection on, `0x51228` returns the virtual position `0x2b9d39`. `0x4fd1e` maps position to state. `STATIC`

| Position | 1 | 2 | 3 | 4 | 6 | 7 | 8 | other |
|---|---|---|---|---|---|---|---|---|
| App state | 3 | 4 | 5 | 2 | 10 | 12 | 13 | 6 (playback, `0x4fe6c`) |

Record states are 2, 3, 4, 5, 10, 12, 13; capture entry `0x29b28` is listed only for them (table `0x1a911c`). Composed with the emulator's dial-bit assignment: still -> 2, movie -> 3, SCENE -> 5, M -> 10, P -> 12, A -> 13. State 4 is reachable only through a virtual dial value (`dialset 2`, `-CCTL diaset,2`). `STATIC`; labels `INFERRED`

### State 9 under USB

`0x4fd1e` forces state 9 whenever `0x23a888 == 1` and `0x23a88c == 0`. State 9 runs `0x6d670` (table `0x1a911c`), whose handlers `0x6d6f0` and `0x6d840` ignore RL1 and RL2. Code 24 or 36 with action 1 in sub-state 0 calls `0xc4b64` (switch to PTP). `0x23a888` follows pin `0x38` at boot and an `(8,1)` event from the `UsbShutter` task (`0x131b3a`) at run time. `STATIC`; "cable attached" `INFERRED`

On stock 2.40 in ORIGINAL mode, every `-CCTL` command (`remtin`, `diaset,0..8`, RL1 and RL2 holds and releases) answered `200` and no file appeared at any dial position, consistent with state 9 swallowing the shutter. `HW` (spare, file count only). Koma's tether feature redirects this path; see [07](07-usb-protocol.md).

### Shooting-parameter byte `0x23b085`

`0x23b085` is `cp+0x1d` in `0x23b068`. Values 0 and 5 are normal; 1, 3 and 4 force tone selector `0x20` and set EXIF SceneCaptureType (`0x2d27ac`). It is unrelated to app state 9. `cp+0x22` (`0x23b08a`) is the color / B&W / TE mode (0/1/2), `cp+0x40` selects CC1. `STATIC`. All 134 archived stock JPEGs read SceneCaptureType 0. `HW`

## CPU1 link and power-off

`0xca524` runs a request/response link over SPI with a GPIO60 per-byte handshake, using request table `0x181676`. The Monitor samples GPIO60 earlier and independently ([02](02-boot-chain-and-monitor.md)). CPU1 firmware is not in the image. `STATIC`

| Request | Register | Tag |
|---|---|---|
| `REQ_GET_VERSION` | `0xf10e` | `STATIC` |
| `REQ_SET_POWER_OFF` | `0xf110` (descriptor `0x182774`: `f110 4001`) | `STATIC` |
| `REQ_SET_REBOOT` | `0xf115` (payload `01 05 00 00 00`) | `STATIC` |
| `REQ_GET_HKEY_STATUS` | `0xf1d2` | `STATIC` |
| `REQ_GET_BOOT_TRIGGER` | `0xf201` (table index `0x3f`, read at `0x51a64`) | `STATIC` |
| Read after power-off | `0xf109` | `STATIC` |
| Specials | `0xf6`, `0xfa`, `0xfd` | `STATIC` |

`0xca76a(index, payload)` is the table-driven command writer; `0xca6b8(code, 0|1)` sets or clears a bit command. `0x520c8(msg)` builds the power-off: if `0x2b9e5d == 1` it first sends command `0x3e` with the reboot value `0x2b9e60`; then command `0x40` with byte 5 (`REQ_SET_POWER_OFF`), `0xca808(0xf109, 0, 1)`, command `0x41`. The script command `poff` arms `0x2b9e5d`/`0x2b9e60` through `0x66ab4` and posts `{6,3}`; the `poff` handler does not send `REQ_SET_REBOOT` itself, the power-off path does. `STATIC`. The meaning of the reboot value is unknown.

## Key and dial input

### Path

The key scan fills `0x2b9d84` from CPU1 status bytes (byte and mask per key in `0x18108c`) or GPIO. RL1/RL2 use GPIO `0x30`/`0x31` (HISRs at `0x50c94`). `0x50eb0` overlays the injected array `0x2b9dd8[code]`. `0x5188c`, `0x5110c` and `0x513c0` index the array and table with the same index. `0x513c0` emits events only when no other key is down. `0x4e010` translates to UI events. A PLAY hold of more than 300 polls (`0x5129c`) posts `0x720d`, as POWER does through `0x513c0` -> `0x52152`. `STATIC`

Key events reach `tOpeMain` as `0x7103 {code, action}`, posted by the scanner, by `SCRIPT.MG1` (`keyset`, `key`, `keyhold`, `keyrel`, via `0x324e6`) and by USB `-CCTL keyset,A,C`. `-CCTL remtout` does not release injected keys; `keyset,0,C` or `keyset,0,2` does. `STATIC`

### Injection sink `0x51740`

| Code | Behaviour |
|---|---|
| 0 | Commit virtual dial; emit `(0,4)` if changed |
| 1 | Set virtual dial to the real dial; emit `(0,4)` if different |
| 2, 42 | Injection off, clear injected keys, virtual dial back to 9; emit `(0,4)` if it differed |
| other | Injection on, `0x2b9dd8[code] = action`; no upper bound |

Actions: 0 stop overriding (cannot force a held key up); 1 hold (emits `(code,1)`, repeats `(code,2)` after 16 polls and every 10 after); 3 tap (one poll); 2 stored, ignored. `keyset` limits action < 4 and code < 42; `defkey` accepts codes to 255 and can write past the array (code `0x60` reaches `0x2b9e38`). The first injected key turns injection on, which makes the dial read 9 and the state 6, so key scripts start with `keyset 0 1`. A USB session event zeroes `0x1bd2ec` but not `0x1bd1b8` or the injected array. `STATIC`, `EMU`

### Key table `0x18108c`

Record: type, CPU1 byte and mask, polarity, name. Polarity-2 entries are excluded from injection and read 0 in hardware. Names `STATIC`; physical function from names `INFERRED`.

| Code | Name | CPU1 byte: mask | Pol. | Injection | UI event |
|---|---|---|---|---|---|
| 0, 1, 2 | MODE0..2 | 5: `0x1`, `0x2`, `0x4` | 1 | control codes | - |
| 3 | MODE3 | 5: `0x8` | 2 | none | - |
| 4, 5 | SLIDE1, SLIDE2 | 3, 4 | 2 | none | - |
| 6 | POWER | 3: `0x20` | 0 | yes; press posts `0x720d` | - |
| 7 | PLAY | 3: `0x4` | 0 | yes; long hold powers off | - |
| 8 | USB | 3: `0x40` | 1 | none | - |
| 9 | AVJACK | 4: `0x8` | 0 | level event | `ca` |
| 10 | DCJACK | 4 | 2 | none | - |
| 11 / 12 | UP / DOWN | 1: `0x8` / `0x1` | 0 | yes | `a0..a2` / `a3..a5` |
| 13 / 14 | RIGHT / LEFT | 1: `0x4` / `0x2` | 0 | yes | `a6..a8` / `a9..ab` |
| 15 / 16 | TELE / WIDE | 0: `0x20` / `0x40` | 0 | yes | `af..b1` / `ac..ae` |
| 17, 18, 21, 25 | MACRO, FLASH, CANCEL, SHAKE | - | 2 | none | - |
| 19 | MENU | 1 | 2 | none; inert when injected | `b2..b4` |
| 20 | TRASH | 0: `0x10` | 0 | yes | `b8..ba` |
| 22 | DISP | 1: `0x20` | 0 | yes | `bc..be` |
| 23 | ENTER (MENU/OK, `INFERRED`) | 1: `0x10` | 0 | yes | `b5..b7` |
| 24 | QACCESS | - | 2 | none | `cb..cd` |
| 26 / 27 | RL1 / RL2 | GPIO | 0 | yes | `c8,-,c9` / `bf..c1` |
| 28, 29 / 32, 33 | DIAL_AC, DIAL_BC / DIAL2_AC, DIAL2_BC | GPIO | 0 | wheel input level | - |
| 30, 31 / 34, 35 | PLUS, MINUS / PLUS2, MINUS2 | GPIO | 2 | generated from the wheels | `ce`, `cf` / `c6`, `c7` |
| 36 | DPUSH | GPIO | 0 | yes; needs a hold | `cb..cd` |
| 37, 38 | RING1, RING2 | 4: `0x20`, `0x40` | 0 | via ring handling | - |
| 39, 40 | LENS1, LENS2 | 4: `0xff` | 2 | none | - |
| 41 | HEADUP | GPIO | 1 | yes | - |

`0x256ea` takes a GPIO number (it builds a mask for `0x256c2`/`0x256d6`), not a table index; scripting is not suppressed through key index 8. The wheels are quadrature pairs converted to PLUS/MINUS: injecting `(28,1) (29,1) (28,0) (29,0)` with polls between gave four `30` events in the model. `dialset N` (N < 9) and `dial name` write the virtual position and post `{0,0}`. `defkey name A B` stores A at `+0x11` and the event code B at `+0x12`; `defdial` adds the virtual position at `+0x13`; 32 key and 16 dial slots, stride `0x14`. `STATIC`, `EMU`

Ricoh's guide maps UP to flash, DOWN to macro, LEFT to quick review, RIGHT to scene select / SETUP, MENU/OK to one button, and D/T to a separate delete and self-timer button; the mapping to key codes is `INFERRED`.

### Key combinations

| Test | Keys | Effect | Tag |
|---|---|---|---|
| `0x4fbcc(1,0)` -> `0x32540(0)` | RIGHT + TELE + dial 1/10/9 | Service terminal and console | `STATIC` |
| `0x4fbcc(2,0)` -> `0x32540(1)` | UP + TELE + dial 1/10/9 | Update gate | `STATIC` |
| `0x4fbcc(5,0)` (`0x66bb8`) | UP + WIDE | Factory flow on script start; can reset settings and reboot | `STATIC` |
| `0x1f09c` | DOWN + TELE | Version screen | `STATIC` |
| `0x1f1e0` | LEFT + TELE (dial Movie) | Counters screen, display only | `STATIC` |
| `0x1f1b8` | RIGHT + WIDE | 20 s screen | `STATIC` |
| `0x1ea6e` | DOWN + WIDE (dial Movie, MASS STR) | Writable mass storage for one boot | `STATIC` |

## UI and display

| Item | Finding | Tag |
|---|---|---|
| Controller | VEN `0x12020000`; driver `sdl_ven_api.c`/`sdl_ven_drv.c` (asserts at `0x1615a0`, `0x170534`) | `STATIC` |
| OSD DMA | Base `0x1200c080`, stride/4 `0x1200c088`, enable `0x1200c00c` bit 2; VEN enable `0x12020004` bit 1 | `STATIC` |
| Frame IRQ | INTC line 2 -> `0x217d8`; VEN mask `+0x14`, status `+0x18` (write-0-to-clear); bit `0x400` runs a pending callback that starts the OSD | `STATIC` |
| Pixels | 4 bpp, high nibble left, 360 x 240, stride `0xb4`; pages `0x08422800`, `0x0842f280` (288 lines for PAL) | `STATIC` |
| Palette | `0x12020100 + i*4`, i = 1..63, `Y<<16 \| Cb<<8 \| Cr` (signed chroma); index 0 transparent | `STATIC`; index 0 `INFERRED` |
| Layers | Back buffers `0x2f8498 + n*0xa8c0` (1 icons, 2 menus, 3 dialogs), copied to the page | `STATIC` |
| Primitives | Element `0x6ba80(layer, element)`; glyph `0xd5c30`; pixel `0xd5ec0`; blits `0xd6210` (2 bit), `0xd6084` (1 bit); memcpy `0x157bac`, memset `0x157b2c` | `STATIC` |
| Font | Page 1 glyphs `0x202cac`, 1 bpp, 20 rows; offsets/widths `0x1fbaf0` built by `0x6a110`; further pages through `0xd58ac` | `STATIC` |
| Images | Tables `0x1fe084`, `0x1ff084` (offset, width, height, type); data `0x21a8ec`, `0x22fee1` | `STATIC` |
| Menu strings | Not ASCII: 9 pointers per string (one per language), rows from `0x18d270`, glyph pairs at `0xf0000..0x106000`; 525 rows extracted; tab names at `0x1c1ec4` | `STATIC` |
| Panel serial | USART `0x12024000` carries panel control only (`0x229a0..0x23840`), no pixels | `STATIC` |
| Header art | `0x8e3d0` loads the stock tab-header image and waits on semaphore `0x2a2f4`. The emulator has no producer, so it times out and never draws the art; the camera draws it | `HW`, cause `INFERRED` |
| Update screens | `0x5d330(id)` -> `0xc5724`. `0xd6fc4` (from `0xd7014`) is the kernel `*** STOP ***` printer, not an update screen | `STATIC` |

A module that patches the stock menu ran on the main body: the tab table, header drawing and row tables were repointed, and rows, values and the bottom bar drew correctly. `HW 2026-11-06 main`. See [how it works](../how-it-works.md#the-menu).

**Model UI.** The stock image draws its shooting screens for all six dial values, menus, ADJ, playback and format screens in the emulator, and injected keys navigate them. Menu order and remaining-shot counts (26 MB internal: RAW 1, F3264 8, N3264 14, N640 277) agree with Ricoh's guide; the shooting-warning default (OFF in the model, ON per the guide) and the quality-list order (an extra N3:2) do not, because the model's adjustment store is erased and defaults such as the language come from `0x08010016..0x1b`. UI changes land in `0x1b000`: language `0x1b056`, flash `0x1b06f`, quality `0x1b068`, display `0x1b09c`, EV `0x1b053`, ISO `0x1b059`. Boot to the shooting screen takes about 4.3e8 virtual instructions there. `EMU`

## Develop and capture path

### Develop hooks

| Site | Role | Tag |
|---|---|---|
| `0x96350` | Still develop, pass 1; writes CC2 per shot (chroma gain, matrix rows, chroma coring, unity gains) | `EMU` |
| `0x96436` | `bl 0x8ff0e` in pass 1, after the stock register writes; per-shot hook | `HW 2026-11-06 main` |
| `0xafd20` | Second develop pass; rewrites the color registers, so pass-1 overrides are lost | `STATIC`, `HW` |
| `0xafd3e` | Pass-2 hook (`-> 0x8ff0e`) | `HW 2026-11-06 main` |
| `0x9665e` | `bl 0x91e78` (filter parameters); enhancer override point | `STATIC` |
| `0x9733a` -> `0x90278` -> `0x90408` -> `0x16d31c` | Shot tone upload (not `0x92050`, which runs before the update gate) | `EMU` |
| `0x8ffc8(selector, arg)` | Tone-table upload; contrast 1..5 -> selectors `0x1e..0x22` | `STATIC` |
| `0x1f4db0`, `0x1f4db3` | Selector cache compared at `0x90322`. The preview upload reloads a table without clearing it, so a module that uploads its own table must invalidate the cache | `STATIC`; invalidation `HW` |
| `0x8605f00` / `0x8f40e00` | Finished master Y / UV planes before JPEG encode | `STATIC` |

### Capture sequence

1. RL1 (code 26) then RL2 (code 27). `RecRL1TASK` runs the capture; a full press arrives at `0x973e4` inside `0x9733a`, then `0x947e4`. `EMU`
2. `0x947e4` polls bit 31 of `0x1201401c` through `0x170a30` without sleeping, then compares bits 23:16 with a window (`0x14..0xbe`, or `0x0f..0x38` in two capture states): in window returns 1, else sleeps a tick and returns 0. `0x94858` also polls it. `STATIC`
3. `0x12014024` low 12 bits = `vcycle` (assert `MAX_COUNT_V`, `0x166598`); `0x12014040` bit 0 written by line-3 ISR `0x23950` through `0x170b14`, which then runs three callbacks; `0x12014044` bit 0 and INTC mask bit 3 cleared together by `0x9e058`/`0x9733a` and set by `0x9e0ec`; callback `0x9dfb5`. Before the press the firmware has armed `vcycle` `0xff`, `+0x44` bit 0 clear, INTC bit 3 clear and callback `0x9dfb5`. `STATIC`, `EMU`
4. After `0x947e4` returns 1, `0x9670c` waits on semaphore `[0x1f4dd8]` (timeout 500); failure prints the CCD2 thumbnail timeout (Japanese string, at `0x96716`), then `CamStillRecGenQvgaThumbYUV: CamImageResize(Disp->Resize) Failed.` and `-- result : 320 x 240` on `0x12034000`, and no file is written. `EMU`

| Counter model in the emulator | Endpoint |
|---|---|
| None, or reads 0 | `0x947e4` never returns (3,110,590 reads); spins in `0x170a30`; key polls stop at 1432 |
| Bit 31 clear | Same spin; key polls stop at 1410 |
| Valid, count out of window | Sleeping retry, UI alive |
| Running, frame IRQ suppressed | Returns 1 after one read; `0x9670c` and resize time out; no file |
| Running with frame IRQ (2,468 ISR entries) | Returns 0 at 1,134,011,300, then a fetch fault at `pc=0x4551172` at 1,134,603,135 |

Supplied, not derived: bits 23:16 as the low byte of a vertical count, bit 31 as "count valid", the wrap interrupt, a frame of 66,667 virtual instructions, reset value 1 for `+0x44` bit 0. `StrobeTimerSem` (`0x3564c(2)`, 100-tick timeout, prints "an error here means system collapse") times out in every case. The camera itself shoots normally. `EMU`; shooting `HW`

### JPEG and resize completion

| Engine | INTC line / mask | Path | Tag |
|---|---|---|---|
| JPEG | 5 / `0x20` | Dispatcher tests `0x20` at `0x1c2ec`, calls ISR `0x2398c` at `0x1c304`; HISR `camJPGhr` (object `0x2366e8`, entry `0x239c1`, created by `0x23aaa`). Control `0x1201c000`, mask `0x1201c01c`, status `0x1201c020`: bit 1 -> `0x5df54` (success), bits `0x7c` -> `0x5df6c` (error); both release the decoder semaphore stored at `0x1bd9b8` (object `0x2bfccc`). Wrapper result 0, or `0xffffffff` with error flag 4 | `EMU`, `STATIC` |
| Resize | 12 / `0x1000` | Dispatcher `0x1c3e8` calls ISR `0x23a30` at `0x1c400`; status `0x12034004` bit 0 -> HISR `camRSZhr` (`0x236830`) runs `0x5e834`, releasing the semaphore stored at `0x1bd9d0` (created by `0x5e774`, observed at `0x2bfc94`). Control `0x12034000`; start helper `0x1714e4` sets bit `0x10000`; wait `0x5e7ce`, timeout 2000; failure `0x8d020000` | `EMU`, `STATIC` |
| Decoder | | Wrapper `0x5df84`, semaphore `0x2bfccc`, timeout 2000. Accessor `0x16fcf0` reads DMA register `0x1200c16c`; validator `0x16fcfc` requires `PXIR9` = 3 or 7 when `LNIR` = 7 or 15 (`sdl_dma_drv.c` line `0x931`), or the driver asserts. A supplied latch `0x300`/`0x700` becomes `0x307`/`0x707` and the decoder starts at `0x163db0`; none of these is an established reset value | `EMU` |

The shutdown image path needs one JPEG and two resize completions; with only one resize completion the second wait returns `0x8d020000`. With all three supplied, the decoder returns 0, convert (`0x5e7e4`) and the image pipeline return 0 at about 38.3 M, the shutdown image returns at 49.6 M, and `MainCtrl` waits for `0x2201` (policy 2, return site `0x32cae`) to the horizon; `0x4fcaa` is not reached. `EMU`

## Shutdown

POWER press -> `0x513c0` -> `0x52152` -> `0x720d` -> `0x32b74` -> `0x32b8c(1)`. A long PLAY hold posts the same message. A card script's `poff` instead saves CONFIG and arms a reboot. `STATIC`, `EMU`

`0x32b8c(1)`, contiguous `0x32b8c..0x32de1`:

1. Wait on two flags (`0x32de4`, `0x32dec`, the second capped at 500 polls); `0x4fd14(0)`; `0x3e8c8(1)`.
2. Unless the masked mode word excludes it: store the display mode (getter `0x6e91c`, byte `0x1bef92`) at `0x1b09c`, `0x29910`, copy configuration `0x8ba7c`, then **save settings** `0x4949c(0x1b000, 0x1000)`.
3. Normal branch: `0x685b4`, `0x29fb4`, wait `0x28794(msg, 2)`; `0x685f0`; shutdown image `0x8e3d0(5)` (10 when state field `+8` is 2) with `0x3da80`, `0x3db8c`; wait for `0x2201`, calling `0x4544c` if another message arrives; `0x235f8`; `0x293f4(0xb)`; `0x4fbcc(4, 0)`, `0x31860(1)`; `0x5d9a4`.
4. Save settings again, then `0x675e0`.
5. Tail: `0x31e28(0x8000)`, `0x3e8c8(0)`, CPU1 specials `0xf6` and `0xfd` (`0x327cc`), `0x3da80(2)`, `0x4fc50`, **`0x4fcaa(2 or 1)`**, then `sleep(1000)` forever.

All `STATIC`. The settings block is saved up to twice on this path. `0x1b09c` advances when DISP is pressed, so the display mode persists through this save. `INFERRED`. A zero reason word `0x23a890` selects extra configuration saving; it does not stop shutdown. `STATIC`, `EMU`

The shutdown image (`0x8e3d0(5)`) waits on `0x1b9890` (`0x3eb58`), calls `0xf1428`, loads `ricoh.res` into work pointer `0x1a8bfc` through `0x2751c` (limit `0x1fc00`, loader `0x8df30`) or falls back to the built-in image `0x1dee53`, decodes via `0x81cb8` -> `0xf144c` -> `0xf2098` -> `0x5df84`, and with argument 5 runs `0x235f8` afterwards. `STATIC`. The captured `ricoh.res` is 79,872 B; in a conditional model the original loader copies 14,336 B past the allocation. `EMU`

**Acknowledgement.** `tCameraMain` handles `0x3101` in `0x3386c`: `0x3c8dc(7)` starts the mechanical shutdown sequence (`0x37958(0x25)`, after `0x3d340` when store byte `0x08010267` bit 5 is set), `0x4544c`, `0x3c9dc` waits on event group `0x2d1bd0` (`MECHFLAG2`; mask 1, operation 3, timeout 10000), `0x3cfc8(0)`, then queues `0x2201`. The sequence engine `0x35ac8(mask)` walks table `0x17e464` (8-byte handler and event-mask records), fed by secondary-timer callbacks `0x38940` (posts `0x4000`) and `0x39014` (re-arms, eventually posts `0x8000`). `STATIC`. In the emulator channel 2 (reload `0x12010008 = 324000`) is never armed, the wait times out with -50 (ignored), and shutdown continues to CPU1 `0xf110 = 05`, a read of `0xf109` (supplied reply), and the spin at `0x8a3e6`; stock and marker images behave the same (33 programs, 3 erases). That is a timeout fallback, not a modelled healthy shutdown. `EMU`

## Consoles and logs

| Item | Detail | Tag |
|---|---|---|
| Console ring | `0x24a80` writes a 1 KB ring (`0x237f40`, limit `0x400`) when `0x1a9028 == 0`, else USB writer `0x534be`. A full ring blocks the writer on a semaphore | `STATIC` |
| USB console | `0x26b04(1)` selects USB. Path `0x26b10` -> `0x534be` -> `0x534f4` -> `0x5317a(8, ...)`. Output dropped while `0x1bd294 == 0`; `0x53564` sets it and queues a 12-byte `c1 20` block. Output cut at `0x3ff`; `0x534f4` discards `0x400` B or more; each call waits `sleep(10)` | `STATIC` |
| Log flags | `0x2c2d30 == 1` enables the `adjcom` print (`0x66878`); engine read output goes to the debug console `0x26baa`. Script `logon`/`logoff` switch interpreter logging | `STATIC` |
| Error logger | `0x5de6c` logs non-fatal entries during boot. Strings seen in the model: DMA transfer error and "did not finish" (live-view path), `StrobeTimerSem:`, `ExtData is Broken.`, `Monitor is Broken.` | `EMU` |
| Monitor console | Separate UART path in the Monitor (38,400 baud, `apl` RAM load, `jr` exit); the application's USB console does not continue as Monitor UART. See [02](02-boot-chain-and-monitor.md), [08](08-card-scripts-and-service-terminal.md) | `EMU` |

**Koma telemetry (not stock firmware).** The Koma `telemetry` module writes `/ATA1/KOMALOG.TXT` at boot: stage, build id, resident address, CPSR, SP, ticks at `0x2358dc`, `KernPool` headroom, an SMC coherence test, and CP15 fields (disabled on cameras). Example on the main body: `CPSR=80000013` (SVC), `SP=0038F1A8`, `POOL=00000F40` (3,904 B largest free block with a 29.8 KB module resident). `HW 2026-11-06 main`. Since `koma04` the resident block sits at fixed `0x3dae00` with a canary, and `KOMALOG.TXT` reports `RES 003DAE00` ([how it works](../how-it-works.md#grmodbin)). `HW`

## Second-session tether stall

A tether session started 5-12 s after the previous `remtout` armed only after about 10 s. Cause: the capture path's try-take `0x23578` leaks the LCD mutex `[0x1a8d14]` when the panel is off (mode 0/1 or >= 5); later blocking takes (`0x228e8`, 10,001 ticks) on `MainCtrl` then time out one by one. `STATIC`. Measured with the fix off: `MainCtrl` blocked at saved LR `0x22905`, mutex `0x2c02ec` held at 0 for 5.83 s, session armed in 10.41 s; with Koma's `stallfix` on: 0.66 s, no `0x22905` waits. `HW 2026-11-08`. USB detail in [07](07-usb-protocol.md); analysis in [`analysis/stall/ROOT-CAUSE.md`](../../analysis/stall/ROOT-CAUSE.md).

## Non-return annotations

`0x6e91c` is a six-byte getter (`ldr; ldrb; bx lr`, bytes `480178804770`) returning byte `0x1bef92`. The original Ghidra database flagged it non-returning, which cut `0x32b8c` at `0x32be8` and hid `0x32bec..0x32c03` (the `0x1b09c` store, `0x29910`, `0x8ba7c`, the settings save). `STATIC`, `EMU`

| Audit of the repaired database | Count | Tag |
|---|---|---|
| Functions | 9,535; six still marked no-return | `STATIC` |
| `CALL_RETURN` overrides before repair | 489: 174 to `0x6e91c`, 164 to `0x7f0d0`, 127 to `0x8128c`, 24 to `0x1f580` | `STATIC` |
| Getter repair | All 174 cleared; 604 instructions exposed across 50 callers (62 continuations owned by the recorded caller, 111 hit foreign function boundaries, 1 unowned) | `STATIC` |
| Remaining overrides | 332 after 17 newly decoded calls: 180 to `0x7f0d0`, 128 to `0x8128c`, 24 to `0x1f580`; unvalidated | `STATIC` |

The decompile export most analyses cite comes from the unrepaired database: pseudocode that ends at a call to `0x6e91c`, or says a call does not return, must be checked against bytes. Map limits: 4.4% of call sites sit in unowned code, at least 144 indirect sites lost their targets, 181 table-pointed entries are outside the map, and about 41-51 KB of likely code is undecoded, so every "no other caller" claim is bounded. `STATIC`

## Open questions

- Why the stock dump script ran from a Playback start but not a POWER start; what decides the script gate on hardware.
- The producer of `[0x1f4dd8]` (literal references at `0x96310`, `0x96334`, `0x966cc`, `0x9697c`, `0x9fcc0`, `0x9fe40`, `0xa0824`, `0xa0998`, `0xa0b04`, `0xa0c24`, `0xded34`, `0x118388`), the fetch fault at `0x4551172`, and the bit meanings of `0x1201401c`.
- What users see as `0x23b085` values 1, 3, 4.
- Physical dial labels: Ricoh's guide lists six positions and no SETUP, while firmware test strings label `diaset,0` SETUP and `diaset,4` STILL. Which wheel is which, and their rest state.
- Whether `0x23a888` is 1 while enumerated in ORIGINAL mode.
- Where `0x1b000` persists in `/IROM`, and whether the power-off save finishes before CPU1 cuts power. Two emulator harnesses disagree on the startup write pattern (8 programs and 1 erase alternating blocks `0x54`/`0x55`, against 10 programs in `0x66` and 1 erase in `0x55`).
- `0x1f4c88`/`0x1f4c8c`: logged as `tCamExtAf` working words in a boot watch, but read as the TE (Cb,Cr) table by `0x8ff0e`. One reading may be off by an offset.
- Routing-field meanings in message headers; queue names other than qOPE, qAPL and qADJUSTUSB; roles of `RecVDTASK`, `RecAUDTASK`, `RecYuvTask`, `tCamPlaM`.
- Whether a real shutdown reaches `0x4fcaa`; what arms secondary-timer channel 2; the CPU1 reboot value; the CPU1 `0xf24a` reply.
- Whether the other non-return roots (`0x7f0d0`, `0x8128c`, `0x1f580`) hide code.
- The emulator's Unicorn core leaves the FIQ mask clear where older cores set it, so FIQ races are not excluded by any model result.

## Evidence

- [`../../analysis/patch-results/matrix-stock-a.json`](../../analysis/patch-results/matrix-stock-a.json) (task table, creation times)
- [`../../analysis/captured-monitor-emulation/verification-summary.json`](../../analysis/captured-monitor-emulation/verification-summary.json), [`../../analysis/captured-monitor-emulation/cold-through-main-task.json`](../../analysis/captured-monitor-emulation/cold-through-main-task.json) (cold boot to `MainCtrl`)
- [`../../analysis/export/disasm.txt`](../../analysis/export/disasm.txt) (`0x1effa` and other byte-level checks)
- [`../../analysis/store-recipes/README.md`](../../analysis/store-recipes/README.md), [`../../analysis/adj-loader/README.md`](../../analysis/adj-loader/README.md) (NADJ probe `0x1effa`)
- [`../../analysis/usb-first-contact/README.md`](../../analysis/usb-first-contact/README.md) (`-CCTL` on stock, no file)
- [`../../analysis/ui-runs/string-table.json`](../../analysis/ui-runs/string-table.json) (menu strings)
- [`../../analysis/menu-5tab/README.md`](../../analysis/menu-5tab/README.md) (menu module on hardware)
- [`../../analysis/jpeg-completion/README.md`](../../analysis/jpeg-completion/README.md), [`../../analysis/resize-completion/README.md`](../../analysis/resize-completion/README.md) (completion paths, shutdown continuation)
- [`../../analysis/shutdown-analysis-repair/README.md`](../../analysis/shutdown-analysis-repair/README.md), [`../../analysis/shutdown-analysis-repair/corrected-shutdown.c`](../../analysis/shutdown-analysis-repair/corrected-shutdown.c) (shutdown body)
- [`../../analysis/shutdown-ack-dependency/verification-summary.json`](../../analysis/shutdown-ack-dependency/verification-summary.json) (acknowledgement, timer channel 2)
- [`../../analysis/noreturn-metadata-audit/README.md`](../../analysis/noreturn-metadata-audit/README.md), [`../../analysis/getter-call-repair/README.md`](../../analysis/getter-call-repair/README.md) (non-return audit)
- [`../../analysis/koma-0.1/hw/KOMALOG-v04-boot.TXT`](../../analysis/koma-0.1/hw/KOMALOG-v04-boot.TXT), [`../../analysis/koma-0.1/hw/KOMALOG-crash1.TXT`](../../analysis/koma-0.1/hw/KOMALOG-crash1.TXT) (Koma telemetry; CP15 hang)
- [`../../analysis/stall/ROOT-CAUSE.md`](../../analysis/stall/ROOT-CAUSE.md), [`../../analysis/hw-tests/RUNBOOK-2026-11-08.md`](../../analysis/hw-tests/RUNBOOK-2026-11-08.md) (tether stall, task table `0x1a8acc`, hardware A/B)
- [`../../analysis/sdhc/driver/README.md`](../../analysis/sdhc/driver/README.md) (generation 2 loader `/IROM` mount)
- [`../../archive/research/script-features.md`](../../archive/research/script-features.md), [`../../archive/research/capture-device.md`](../../archive/research/capture-device.md), [`../../archive/research/ui-emulation.md`](../../archive/research/ui-emulation.md), [`../../archive/research/emulator-limits.md`](../../archive/research/emulator-limits.md) (keys, scripts, capture model, UI model, decoder blocker)
