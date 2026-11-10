# 13. Display, live view and USB streaming

**Summary.** The live view is a ring of three semi-planar YCbCr frames at `0x8470000` (640x244 visible, stride 672), rotated in the sensor VD interrupt at 30 fps. Stock movie mode resizes a ring frame to 320x240 and JPEG-encodes it 30 times a second; the USB link is high speed and moves about 4 MB/s on `RETR`. Stock firmware never runs the monitor in the USB state (state 9). Koma's `tether` keeps the camera in shooting state 2 with the cable in, and `liveview` pulls one frame per `-LVFR <mode>,<seq>` on the dead `-FDWL` record. As of 2026-11-08 both run on both bodies: live view at about 28-29 fps per pull in the self-test, 640x240 frames at about 12.9 fps, and an 11-minute Zoom session through a host webcam bridge.

Tags: HW, EMU, STATIC, INFERRED, defined in [features](evidence-ledger.md). Addresses are firmware 2.40 runtime addresses.

Related: [01 Hardware and memory map](01-hardware-and-memory-map.md), [04 Application architecture](04-application-architecture.md), [06 Image pipeline and colour](06-image-pipeline-and-colour.md), [07 USB protocol](07-usb-protocol.md) (command table, verbs, reply codes, `-CCTL` subcommands, Koma verbs), [10 Emulator and tooling](10-emulator-and-tooling.md), [11 Modification playbook](11-modification-playbook.md).

## Key addresses

| Address | What | Tag |
|---|---|---|
| `0x8470000` | Monitor ring base (`CMEM_MONI_START`), 3 frames x (Y + C), `0xfff00` B | `STATIC` |
| `0x2cf640` / `0x2cf658` | Y0..2 / C0..2 slot pointers | `STATIC` |
| `[0x1f501c]` | Display slot d (the completed frame) | `STATIC` |
| `[0x1f5014]` | Ring write index w | `STATIC` |
| `[0x1f511c]` | Monitor VD counter | `STATIC` |
| `[0x1f502f]` | Monitor running flag (1 = running) | `STATIC` |
| `0x9e218` | Wait for the next monitor VD | `STATIC` |
| `0x985a0` / `0x98a32` | Monitor start / stop | `STATIC` |
| `0x08422800` / `0x0842f280` | OSD pages A / B (4 bpp) | `STATIC` |
| `0x5e8f0` | Resize engine (CamImageResize) | `STATIC`, `EMU` |
| `0x5e5cc` | JPEG encode | `STATIC`, `EMU` |
| `0x8986c` | Writes the 193-byte AVI frame header | `STATIC` |
| `0x10200000` | M66592-family USB controller | `STATIC` |
| `0xcb0d4` | Data-frame builder | `STATIC` |
| `[0x1bd308]` | USB double buffer (`0x0bf844e0`) | `STATIC` |
| `0x53734` | tDcx (USB command task) entry | `STATIC` |
| `0x23a888` | USB cable-in flag | `STATIC` |
| `[0x2bce18]` / `0x2bce14` | File-layer semaphore / open-file count | `STATIC` |
| `0x235137` | PTP DeviceInfo | `STATIC` |
| `0x1fb784` | `-FDWL` record (ID 26), taken over as `-LVFR` | `STATIC` |
| `0x1b8be` | `stallfix` flag byte (settings offset `0x8be`) | `STATIC`, `HW` |

## Frame ring

| Item | Value | Tag |
|---|---|---|
| Sensor VD interrupt | INTC line 3, ISR `0x23950`. It acknowledges `0x12014040` bit 0 and calls up to three callbacks at `0x1a8d78/7c/80` in IRQ context | `STATIC` |
| Monitor VD callback | `0x9dfb4`, registered by `0x23be0` from monitor start `0x98704`. Counts VDs in `0x1f511c`; while `[0x1f50f8]==1` it calls `0x991fa` | `STATIC`; registration seen in `EMU` |
| Pointer tables | `0x99988(0x8470000)` fills Y0..2 at `0x2cf640` and C0..2 at `0x2cf658` (end words at `0x2cf64c`/`0x2cf664`). Y slots at `+0`, `+0x2aa80`, `+0x55500`; C slots at `+0x7ff80`, `+0xaaa00`, `+0xd5480` | `STATIC` |
| Geometry | 640x244 visible, stride 672, one slot 672x260. 156,160 B of Y plus 156,160 B of C per frame. The `0xfff00`-byte monitor space is exactly 3 x 672 x 260 x 2 | `STATIC` |
| Rotation `0x991fa` | Skips if CC2 is busy (`0x12018094` bit 1). Otherwise `w = (w+1)%3` in `0x1f5014`, programs the CC2 output DMA (`0x9942c` -> `0x16bd30`), and after warm-up sets `[0x1f501c] = (w+2)%3` and activates HISR MoniVenc (`0x9e5cc` -> `0x995d8`) | `STATIC` |
| Frame rate | `0x985a0` calls `0x9989c(6)`, which the debug printer `0xd1714` labels 30 fps. The other start `0x9885c` uses 15 fps (3.75 when `cp+0x1d == 6`) | `STATIC` |
| Frame sync | `0x9e218()` blocks until the next monitor VD (semaphore `[0x1f5108]`, released by HISR `CamMoniVdWait`). More than one waiter is supported | `STATIC` |
| Running flag | `[0x1f502f]` is 1 while the monitor runs. Set at `0x98814` (monitor start), cleared by capture (`0x97644`) and by monitor stop `0x98a32` | `STATIC` |
| OSD | A separate 4 bpp page, A at `0x08422800` or B at `0x0842f280`, selected by the OSD DMA base register `0x1200c080`. Palette registers at `0x12020100 + 4i`, `Y<<16 \| Cb<<8 \| Cr` with Cb/Cr signed | `STATIC`; pulling the page works on `HW` (mode 2 below) |

There are three frames, each with a Y and a C plane: six plane pointers, not six frames. In VD period k the CC2 DMA writes slot w, the display shows d = w-1, and w+1 is idle. Slot d is next written at VD k+2, so a frame read from `[0x1f501c]` right after a VD is stable for about two frame periods (~66 ms) without a copy `STATIC`. If CC2 is busy or `0xe9e40() != 0`, the ring does not advance.

The ring frames carry no OSD, because the OSD is a separate page `INFERRED`. There is no standing LCD-resolution copy: the display scans the 640x244 slot and scales in the display path. No monitor RAW or larger buffer exists in SDRAM during live view; every larger buffer is a `CMEM_STILL_*` buffer written only by a capture, so 640x244 is the largest live picture available `STATIC`.

C is a second plane with the same footprint and stride as Y, which points to 4:2:2 with interleaved CbCr `INFERRED`. The hardware JPEG encoder accepts the resize engine's output, and the resize engine uses one C-format flag for source and destination. Mode 1 frames decode with plausible colour on hardware, so the encoder handles the ring's own C layout `INFERRED, strong`. The raw CbCr byte order and signedness have not been read from a slot dump.

## Which states run the monitor

Record states are 2, 3 (movie), 4, 5, 10, 12 and 13 ([04](04-application-architecture.md)). Monitor start `0x985a0` is called from tCameraMain mode enter (`0x33670`, `0x339a8`), the menu return and setting changes (`0x8f280`). Monitor stop `0x98a32` clears `0x1f502f`/`0x1f502e`, the VD-callback enable `0x1f50f8` and the CC2/VEN control bits. Callers include mode exit `0x33d28` -> `0x8f204`, movie end, capture and the menu `STATIC`.

USB attach forces state 9 at `0x4fe44` when `0x23a888 == 1 && 0x23a88c == 0`. The state-9 runner `0x6d670` starts the session `0x5d5d0(0x800)`, draws the full-screen image `0x1e` through `0xc5724`, sets UI mode `0x32774({2,2,0,0})` and loops on USB messages. It never starts the monitor `STATIC`. The monitor stops because the previous record runner exits through mode exit (`0x8f204` -> `0x98a32`); the exact message link is `INFERRED`. About 3 s after `-VER`, the timer armed by `0x114824` posts `0xa201`. The state-9 runner `0x6d6f0` handles it with panel off (`0x3da80(2)`) and lens retract (`0x29fbe`) `STATIC`. Ricoh's documentation says the screen goes dark when the PC software connects, and the main camera did that on first contact (`HW 2026-11-06 main`).

With Koma `tether` the camera stays in state 2 while the cable is in, the monitor runs, and `[0x1f502f]` reads 1. This was seen in the emulator first and has worked on both bodies since 2026-11-08 (`HW`, see [Hardware results](#hardware-results-2026-11-07-and-2026-11-08)).

A capture stops the monitor (`0x97644`; also `0x113d56` when `cp+0x1c != 0`). `0x985a0` restarts it after develop, card write and the LCD confirm. Live view was back 1.75 s after a tethered shot in one run (`HW 2026-11-07 main`).

## Engines a stream can borrow

| Engine | Entry | Completion | Tag |
|---|---|---|---|
| Resize | `0x5e8f0(desc)`, descriptor `{srcY, srcC, srcStride, x, y, srcW, srcH, dstY, dstC, dstStride, ..., dstW, dstH, mode}`. Y pass, then C pass | IRQ 12, HISR `camRSZhr`, semaphore `[0x1bd9d0]`, timeout 2000 | `STATIC`, `EMU` |
| JPEG encode | `0x5e5cc(desc, scale, out)` = `0x5e224` (quant tables; closes an open decoder) + `0x5e2ec` (`cam_jpeg_encode.c`) + `0x5e478` | IRQ 5, HISR `camJPGhr`, semaphore `[0x1bd9c4]`, timeout 2000 | `STATIC`, `EMU` |
| Rate-controlled movie encode | `0x110528(prevQ, rateTable, desc, out)`: one or two `0x5e5cc` passes | as above | `STATIC` |

**Resize limits.** Source width 32..4096 and even; destination height even; each axis between 1/8 and 8x; destination stride a multiple of 4. `0x5e8f0` also requires destination stride = width + 32: it rewrites any other stride and returns -1 (160 wide needs `0xc0`, 320 wide needs `0x160`) `EMU`. Movie mode's 320x240 frame uses stride 352 and its 160x120 thumbnail `0xc0`, which agrees `STATIC`.

**Encoder limits.** `sdl_jpg_api` `0x162b6c` checks width % 16 == 0, width <= stride, stride % 4 == 0, 4-byte-aligned SDRAM addresses, and an MCU count (4:2:2: `(w>>4)*(h>>3)`) of at most `0xffff`. There is no maximum width or height. A 244-line source encodes as 240 lines (30 MCU rows of 8) `STATIC`. A 640x240 encode straight from the ring passes every assert in the real code `EMU`.

**JPEG output is scan-only.** The encoder emits entropy-coded data plus EOI. The length comes from the count register `0x171488` (8-byte units) and a scan back of at most 32 bytes for `FF D9` `STATIC`. The header is written separately by `0x8986c`: 193 bytes per AVI frame with SOI, APP0 `AVI1`, DRI 0, DQT (`0x114260`), SOF0 and SOS, and **no DHT** (Annex K tables implied). Real AVIs carry SOF0 320x240 with Y 2x1 and Cb/Cr 1x1 `STATIC`, `HW`. A host must therefore prepend SOI, DQT for the scale used, SOF0, a standard DHT and SOS. macOS ImageIO refuses a JPEG without DHT.

**Quantiser scale.** `0x5e224` scales the base DQT as `q = clamp(((scale*base >> 9)+1)>>1, 1, 255)`, so scale 1024 leaves the base table unchanged `STATIC`. DQT values in real movie frames invert to scales 320..531 `INFERRED`. The `liveview` default is 448.

**No locking.** Each engine has one global semaphore, and `0x5e224` tears down a decoder that is open. Stock users in record states are capture, thumbnails, QuickView and the dzoom HISR `STATIC`. A stream must run only while `[0x1f502f] == 1` and no shot is in progress. The full gate set is `INFERRED`.

**Memory.** The engines assert addresses of `0x08000000` or higher, so their buffers must be in SDRAM `STATIC`. `liveview` uses the gap between the ring end (`0x856ff00`) and `CMEM_STILL_TOP_NEW` (`0x85c5e00`): resize Y `0x8572000`, resize C `0x8587000`, JPEG out `0x859c000..0x85ac000`. Only movie mode (audio `0x856ff00`, display copies `0x858f400`/`0x85a7a00`) and non-live display paths (`0xf144c`, playback `0x112bdc`) use that gap `STATIC`. An earlier map that reused the movie buffers `0x85c0000`, `0x860ec00`, `0x865d800` was rejected: those overlap `CMEM_STILL_THUMB_Y` (`0x85c5e00`) and `CMEM_STILL_MASTER_Y` (`0x8605f00..`), which every shot writes. The emulator saw no write into the gap, but engine DMA is not modelled and the model stops at the develop step `EMU`. The low-RAM gap `0x3dadec..0x422800` holds Koma's static resident block (`0x3dae00`, `HW`); it is CPU-side only. Its SDRAM alias ends at OSD page A (`0x08422800`).

## Movie mode as the throughput proof

`0x11318c` is the movie recorder, `0x113e98` handles one frame and `0x110528` is the rate control. Setting byte `+0xf` = 9 selects 320x240, otherwise 160x120. Rate flag `0x1b0ce` selects 30 or 15 fps (labels `INFERRED`) `STATIC`. Frames are paced through callback slot 6 of dispatcher `0x1fe88` (`0x2022c(6, 0x113e74)`), a HISR and semaphore `[0x201f28]` `STATIC`.

Each frame takes slot `([0x1f5014]+2) mod 3`, resizes it to 320x240 at stride 352, encodes it at `0x201f1c` with a limit of w x h bytes, and records {addr, scale, size} in the index at `0xbee6f70` `STATIC`. On rate-control frames `0x110528` estimates size at scale 256 from strips (`0x5e5f6`) and fits a scale toward 16,416 / 18,144 / 17,280 B (table `0x193ac8`).

Measured from six AVIs written by the cameras (270 frames, two bodies): 33,333 µs per frame, 30 unique frames a second, no duplicate index entries, median 17.1 to 18.9 KB per frame, range 13.9 to 23.1 KB, about 530 KB/s `HW`.

| Payload per frame | Bytes | At 30 fps | Tag |
|---|---|---|---|
| Full Y+C ring slot | 312,320 | 9.4 MB/s | `INFERRED` (arithmetic) |
| Y only, 640x244 | 156,160 | 4.7 MB/s | `INFERRED` (arithmetic) |
| JPEG 320x240, movie quality | ~18,000 | ~0.54 MB/s | `HW` |

## USB link

**Controller and speed.** A Renesas M66592-family controller sits at `0x10200000`. `0x60100` sets `SYSCFG |= 0x180` (HSE), and `0x5fd3c` (`DVSTCTR.RHST == 3`) selects the high-speed descriptors and pipe tables, giving a bulk max packet of 512 at high speed and 64 otherwise `STATIC`. libusb reported the main camera as high speed (480 Mbit/s) (`HW 2026-11-07 main`).

**Measured throughput.**

| Transfer | Rate | Tag |
|---|---|---|
| Main, `RETR` from SD (389.6 MB) | 65,548-byte reads every 16.03 ms median (p10 15.90, p90 16.26; 5,871 frames) = 4.09 MB/s per chunk, 3.85 MB/s overall | `HW 2026-11-06 main` |
| Spare, `RETR` from `/IROM` | 21.9 ms median gap, about 3.0 MB/s per chunk (2.76 MB/s average) | `HW 2026-11-06 spare` |
| Self-test timed `RETR` | 3.85 MB/s | `HW 2026-11-08 main` |

The flat 16 ms gap tracks the medium, not the bus, so `RETR` is read-bound `INFERRED, strong`. Each 64 KiB data frame carries 12 bytes of framing (8 header, 1 trailer, 3 pad), needs no ZLP (65,548 is not a multiple of 512), and has no per-frame ack. A pull adds a command and two short replies, about 1 to 2 ms `HW`.

**Data-frame sender.** `0xcb0d4(buf, len, chan)` writes `01, chan, ff ff, len LE`, appends `03` after the payload and pads to a multiple of 4. There is no checksum. `0x5317a(0xb, 0, buf, len)` -> `0x24f34` -> `0x5f688(1, len, buf)` queues the frame on pipe 1 (EP `0x81`) and returns without waiting for completion. Per-packet work runs in HISR `USBP_DRQ` (`0x2508c`): DMA into CFIFO `0x10200010`, then a PIO tail (`0x5f730`, 16-bit writes). The last byte posts `0x401` to qDCX through `0x5324a` `STATIC`. The system DMA channel `0x1200c280` is shared; when it is busy the send falls back to PIO without notice `STATIC`.

**Double buffer.** `[0x1bd308]` = `0x0bf844e0`: payloads at `+8` and `+0x10018`, frame headers at `+0` and `+0x10010`. Each `0x401` starts the frame already built and then reads the next chunk into the other half, so file read and USB send overlap. In normal mode the receive pointer `[0x1bd304]` is the same address, so a command that arrives mid-stream overwrites half 0 `STATIC`. The stock path writes this buffer with CPU stores and DMAs it with no visible cache clean, so it is uncached or write-through `INFERRED, strong`.

**tDcx and the handler contract.** tDcx has entry `0x53734`, priority 80, a 10,000-byte stack and queue "qDCX". Every verb handler runs there, synchronously, through `0xcc304` -> `0x157acc` `STATIC`. The pipe state, the ZLP flag `0x1fb3c0` and the DCX state words are not locked, so only a tDcx handler may send; a frame from another task breaks the reply/data order `STATIC`, consequence `INFERRED`. Handler signature: `void h(char **reply, int *len)`.

- Reply only: set `*reply`, `*len`, `[0x1bd2c2]=1`, `[0x1bd2c0]=0`, `[0x1bd2d0]=3`.
- Reply then data: copy the first chunk to `[0x1bd308]+8`, set `[0x1bd358]` = total, `[0x1bd35c]` = first chunk, `[0x1bd344]` = the final `250` string, `[0x1bd2d0]=4`, and reply `150`.
- At the end the state machine closes handle `[0x1bd2bc]` through `0xcb230` -> `0x55f0c`. Handle `0x8001` returns at once, so a verb that sends from RAM sets it.

**Data-phase gate.** The data phase in `0xcb834` (state 4, "150 sent") and `0xcb9cc` (state 6, "next chunk") continues only for record IDs **2, 10, 13, 14 and 26 (`0x1a`)**. Any other ID stalls. `-CCTL` (ID 16) can never carry data. ID 26 is `-FDWL`, which no known Ricoh client sends; its handler `0xcd39f` is a bare return `STATIC`. While a data phase is open, every verb except `-ABOR` is dropped.

**Watchdog.** Timer `DCXTP_TIME` (`0xcb140`, 200 ticks) counts while an IN is in flight. After more than 5 periods in pipe state 5 or 7 it posts `0x902`, and `0x538bc` resets the pipe state but not `[0x1bd2d0]` `STATIC`. A host that stops reading leaves the transfer state set, and later verbs are dropped until `-ABOR` or a replug `INFERRED`.

The command table layout, the full verb list and the reply codes are in [07](07-usb-protocol.md).

## Session events

- **`0x23a888` is the cable-in flag**, not a session state. At boot, the poll loop `0x1e968` sets it (store at `0x1e98a`) when GPIO `0x38` reads active in all 25 samples. At run time it follows event `(8, x)` from UsbShutter `0x131b3a` (case 0 attach, case 1 detach), stored by the pre-filter `0x4fe6c` and the message filter `0x28bdc` `STATIC`. GPIO `0x38` being VBUS is `INFERRED, strong`. `0x23a88c` is the service-boot flag, written only at boot.
- **Event `0x801` (session end) is never posted in 2.40.** Its only sender `0x5d62a`, and the other `0x800` sender `0x5d61c`, have no caller (Thumb and ARM BL scan, no pointer literal) `STATIC`. The application never tells tDcx that a session ended. A second `0x800` is ignored unless the DCX state is `0x32`, so re-entering state 9 does not restart a session `STATIC`.
- **Unplug.** The detach event `(8,0)` reaches `0x4fe6c` first. In state 9 it calls `0x3eb1c` and rewrites the message to `0x720d`; `0x28cfe` turns that into a `0x5104` request, `0x28eb4` stores exit code 1, the state-9 runner exits, and the camera powers off `STATIC`; the `0x720d` meaning is `INFERRED`. The camera turned off after an unplug on 2026-11-06 (`HW main`). In any other state `0x4fe6c` only stores `0x23a888 = 0` and re-resolves. That is why `tether` handles the unplug itself (below).

## File layer and the shot-write race

The file layer serialises every open, find and close on semaphore `[0x2bce18]` (`0x5d978`/`0x5d9a4`) and counts open files in `0x2bce14`. Both open paths refuse a third file (`cmp r0,#2 ; bcs` -> -1 at `0x55d18` and `0x55e5a`), so **two open-file slots exist** `STATIC`.

| Verb | File-layer use | Tag |
|---|---|---|
| `-NLST` | find-first/next/close (`0x563c0`, `0x5644c`, `0x56528`): takes the semaphore, no slot | `STATIC` |
| `-INFO`, `-FDAT` | open the file briefly (`0xc2788` -> `0x55dc0`): one slot | `STATIC` |
| `RETR` | one slot for the whole transfer (`0xcb1cc` open, `0xcb230` -> `0x55f0c` close) | `STATIC` |

The capture writer's own slot use was not reached in the model, so whether a `RETR` held across a RAW+JPEG write can exhaust both slots is unknown.

One shot was damaged on the card while the host polled it: spare `R0015722.JPG`, 1,115,388 B, with the SOI/Exif header at offset 1,048,576. The first MiB is image data ending in an EOI at 1,026,678, followed by slack, and there is no EOI at the end of the file. During the write, the bridge was polling the predicted file name with `-INFO`/`-NLST`. This happened on 1 of 3 tethered shots on the spare and 0 of 7 on the main body (`HW 2026-11-08 spare`). The cause is taken to be a race between the shot write and host card commands `INFERRED`. The host rule is to send no card command until the camera reports idle (`-KSTA`); see [07](07-usb-protocol.md).

A card read inside a tethered session also stops live view: after `RETR` or `-INFO`, `-LVFR` answers `451` until the host re-arms with `-CCTL diaset,4` (`HW 2026-11-07` and `2026-11-08`).

## PTP is not an alternative

PTP mode (`05ca:0331`; tPtpTran `0xc5024`, tPtpData `0xc5108`, priority 130) carries a little-endian DeviceInfo at `0x235137`: 13 standard operations (`1001`-`100A`, `100C`, `100D`, `101B`), events `4001`-`4003` and `4009`, no device properties, image formats `3801 380D 300A 3008 3002`. There is no InitiateCapture, no vendor `0x9xxx` operation and no live view. The rest is PictBridge `STATIC`.

## Koma: tether

`tether` (code: [tether.py](../../tools/koma/features/tether.py)) keeps the camera in shooting state 2 while USB is attached, so the monitor runs and `-CCTL keyset` presses fire the shutter. It uses five exclusive BL redirects:

| Site | Stock call | Koma routine | Purpose |
|---|---|---|---|
| `0x4fe44` | `ldr r0,=0x23a864 ; ldr r1,[r0,#0x24]` (4 bytes) | `tether_force` | Skips the state-9 force while USB is attached, not the service boot, `remtin` is set (`[0x1bd1b8]`) and key injection is on (`[0x2b9d34]`); then stores 2 and re-resolves from the virtual dial. Also handles the unplug: no USB with `remtin` still set clears the injected keys and dial and re-resolves from the physical dial |
| `0xcd090` | `bl 0x4fc84` (`-CCTL remtout`) | `tether_out` | Clears the flag, every injected key, the virtual dial and the `-CCTL` state, then posts dial event `(0,4)` so the resolver forces state 9 again |
| `0xcd03a` | `bl 0x65cfc` (`keyset`) | `tether_key` | Renews the Auto Power Off hold (`0x290de(2)`) on each injected key |
| `0xcd05e` | `bl 0x65d70` (`diaset`) | `tether_dial` | Posts `(0,4)` after each successful `diaset`, so the state leaves 9 without waiting for a key event |
| `0xd27f2` | `bl 0x6eb70` in record dispatch `0xd2770` | `tether_msg` | Swallows `0xa201` (posted 3 s after `-VER`) while USB is attached; in state 2 it would otherwise end the record runner |

All `STATIC`, `EMU`; the whole sequence runs on `HW` (below). A resident word `arm_hook` is called once right after the state leaves 9; `stallfix` uses it. An earlier design that used a 32-byte stub plus a 48-byte routine in a cave at `0x56f80` ran only in the emulator and is not in the package.

**Second-session stall.** Before `stallfix`, a tethered session started 5 to 12 s after the previous `remtout` took 10 s or more to arm. The capture-path routine `0x23578` (only caller `0x9765e`) try-takes the LCD driver mutex `[0x1a8d14]` and never releases it when the panel mode is outside 2..4, which is the case with the panel off after `0xa201`. Every later blocking take (`0x228e8`, 10,001 ticks) then times out after 10 s `STATIC`. On hardware, with all fixes off, the second session armed in 10.41 s: MainCtrl was blocked at LR `0x22905` (171 samples) and the panel mutex (`0x2c02ec`) sat at 0 for 5.83 s. With the default fixes it armed in 0.66 s, the mutex was back within 1.02 s, and there were no `0x22905` waits (`HW 2026-11-08 main`).

`stallfix` (code: [stallfix.py](../../tools/koma/features/stallfix.py)) reads flag byte `0x1b8be` at every use and can be changed live over `-KSET 2238,<v>`. The high nibble must be `0xA`; any other value means the default.

| Bit | Fix | Site | Default |
|---|---|---|---|
| 0 | `0xa201` is a no-op in the state-9 runner while USB is attached (LCD stays on, lens not retracted) | `0x6d778` | on |
| 1 | No 60 s lens auto-retract timer armed while USB is attached | `0x284e6` | off |
| 2 | LCD-mutex leak guard: release the try-take when the panel mode is outside 2..4 | `0x9765e` | on |
| 3 | Panel and backlight on at tether arm (`0x3da80(1)`, `0x3db8c(1)`) via `arm_hook` | tether 1.8 | on |

Host values: 160 (`0xa0`) all off, 173 (`0xad`) default, 175 all on.

## Koma: `-LVFR` live view

`liveview` (code: [liveview.py](../../tools/koma/features/liveview.py)) takes over the `-FDWL` record (ID 26) with three record edits and one resident handler. The data-phase state machine is unchanged:

| Address | Before | After |
|---|---|---|
| `0x1fb784` (name) | `000cb64c` (`-FDWL`) | resident `-LVFR` |
| `0x1fb794` (args flag) | `00000000` | `00000001` (parser `0xcbd64` reads `a,b` into `0x1fb3e8`/`0x1fb3ec`) |
| `0x1fb7a4` (handler) | `000cd39f` (`bx lr`) | `lvfr_handler` |

**Command and replies.** `-LVFR <a>,<seq>\r\n`. A successful pull answers `150 -LVFR`, then exactly one channel-2 data frame (16-byte header plus payload, at most 65,536 B), then `250 -LVFR`: the same framing as `-PRET`. The host sends the next command only after `250`.

| Reply | When |
|---|---|
| `501 -LVFR mode` | `a` is not 0, 1 or 2 and `a & 0xf != 3`. Checked before the gate |
| `451 -LVFR off` | Modes 0, 1, 3: `[0x1f502f] != 1` (monitor not running) or `[0x1bd1c0] != 2` (not tether-held state 2). Also a resize or encode error, or no `FF D9` at the end of the scan (mode 3: after one retry at twice the scale) |

No data follows a `451` or a `501`. Without `tether`, the camera stays in state 9 under USB, so modes 0, 1 and 3 answer `451` and mode 2 still answers.

**Modes.**

| `a` | Output | Payload | Path |
|---|---|---|---|
| 0 | 160x120 grey, Y only | 19,200 B (rows packed from stride 192) | `0x5e8f0` to 160x120, stride 192 |
| 1 | 320x240 JPEG scan, 4:2:2 | scan + EOI | `0x5e8f0` to 320x240 stride 352, then `0x5e5cc` |
| 2 | OSD page, 360x240 at 4 bpp plus 16 palette entries | 43,200 + 64 = 43,264 B | Copies the page named by `0x1200c080`; no monitor or state gate |
| `scale<<4 \| 3` | 640x240 JPEG scan from the ring slot | scan + EOI | `0x5e5cc` straight from the slot, stride 672, out at `0x8572000`; scale 0 means the build's `scale3` (448) |

**Handler sequence (modes 0, 1, 3).** Set `[0x1bd2bc]=0x8001`. Check the gate, hold Auto Power Off (`0x290de(2)`), then wait on `0x9e218()` (300-tick timeout; the wait is a build option, on by default). Check the gate again, read the VD count `[0x1f511c]` and slot d = `[0x1f501c]`, and run the engines into the scratch gap. A VD count that advanced by 2 or more across the read and the resize (mode 3: the encode) sets the torn flag; the frame is still sent. The handler then copies the payload behind the header at `[0x1bd308]+8` with `0x157bac` and sets the reply-then-data state.

**Payload header, 16 bytes, big-endian.**

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | `LVFR` |
| 4 | 1 | mode |
| 5 | 1 | flags: bit 0 truncated (payload capped at 65,520), bit 1 VD sync timed out, bit 2 OSD page B, bit 3 torn, bit 4 OSD base register named neither page (page A sent), bit 5 mode 3 re-encoded at 2x scale |
| 6 | 2 | seq (low 16 bits of the host's value) |
| 8 | 2 | width |
| 10 | 2 | height |
| 12 | 2 | payload length after the header |
| 14 | 2 | scale: 0 in mode 0, JPEG scale in modes 1 and 3, palette entry count (16) in mode 2 |

Mode 3 pixels are about twice as tall as they are wide; display them at 4:3. Mode 3 gives twice mode 1's horizontal detail with the same 240 lines. Its frame must fit one 64 KiB chunk, so the encode limit is set to keep a found EOI inside the chunk `STATIC`.

**Host side.** `komabridge` ([README](../../tools/komaset/bridge/README.md)) turns pulls into `GET /live` (multipart MJPEG, at most 30 pulls a second). It rebuilds the 193-byte stock header from the reported scale, inserts the Annex K DHT, holds the stream around a shot, and retries a short `451`. It streams mode 2 at most 5 a second as indexed PNG for the Remote view. `komacam` ([README](../../tools/komacam/README.md)) feeds that stream to an MJPEG endpoint, a page for an OBS Browser source, or a virtual camera. For mode 3 it picks a scale per frame aiming at 24-48 KB. `--lcd-off` taps DISP (`keyset,3,22`) until the live display byte `0x1bef92` (read with `-PEEK`) is 4, and restores it at exit.

## Koma: peaking (VD task hook)

`peaking` (code: [peaking.py](../../tools/koma/features/peaking.py)) draws focus-peaking dots into the OSD once per frame (plane-1 shadow `0x302d58`, then the firmware flush `0x6bbfc`). It hooks `0x95af4`, the `bl 0x99a28` at the top of the VD task loop `0x95ad4` (task `0x95f40`). The VD callback `0x9dfb4` wakes that task through HISR `VdTaskWait` after rotating the ring, so the task runs in task context with a fresh, stable slot `[0x1f501c]`. A VD-count guard limits it to one pass per VD. The IRQ and HISR hook points (`0x9dfdc`, `0x996b0`) were rejected because the per-pass work and the flush do not belong in IRQ or HISR context. `EMU` only; not run on a camera.

## Hardware results, 2026-11-07 and 2026-11-08

Package koma04w (build `747F061F`: tether 1.8, liveview 1.3, stallfix 1.0) on generation 2 Koma firmware, build 2b, unless noted.

| Result | Value | Tag |
|---|---|---|
| Self-test live view, 30 pulls each of modes 0 / 1 / 2 (host caps pulls at 30/s) | 28.1 / 27.6 / 29.0 fps, 0 torn | `HW 2026-11-08 main` (2 GB card) |
| Same, spare | 21.9 / 21.7 / 28.3 fps | `HW 2026-11-08 spare` |
| Mode 1 in earlier self-tests | 26.9-28.8 fps; frames decode with natural proportions and plausible colour | `HW 2026-11-07 main` |
| Mode 2 in state 9 (tether not arming, older build) | answered at 29 fps while modes 0/1 answered `451`; the page decoded all white | `HW 2026-11-07 main` |
| Mode 3 (`komacam --mode 3`) | 60 valid frames at about 12.9 fps | `HW 2026-11-08 main` |
| `komacam --lcd-off` (mode 1) | 10.9 fps; LCD dark while streaming, restored at stop | `HW 2026-11-08 main` |
| Remote view on the spare (mode 1 plus mode 2 overlay) | 10.9 fps; menu navigation through the overlay (36 key taps); 3 shots | `HW 2026-11-08 spare` |
| Zoom via OBS (komacam `--lcd-off --focus 1`, OBS Browser source, OBS Virtual Camera) | 11 min at a steady 10.9 fps, 0 re-arms, no freeze; stopped for battery | `HW 2026-11-08 main` (64 GB card, package from `/IROM`) |
| Re-arm after the battery died mid-session and was replaced | komacam re-armed 0.4 s after the camera reappeared, then twice more within 35 s | `HW 2026-11-08 main` |
| Tethered shot (self-test) | `R0015716` in 7.59 s | `HW 2026-11-08 main` |
| Tethered shots from the web app | 4 on main; 3 on spare from the Remote view (focus, then shoot) | `HW 2026-11-08` both |
| Quick-press tethered shot (self-test step 6) | no file on the spare | `HW 2026-11-08 spare` |
| Second-session arm, stallfix off / on | 10.41 s / 0.66 s | `HW 2026-11-08 main` |
| Card read inside the remote session | live view stops; Remote view showed about 1 fps until re-entered | `HW 2026-11-08 spare` |
| `-KSET` after an EV change | answers `451 -KSET busy` for up to about 9 s (8.4-8.9 s in one session, none in another) | `HW 2026-11-07` |
| Battery over USB | main went from level 2 to dead in about 2.5 h of USB sessions | `HW 2026-11-08 main` |

The `-KSET` busy window is most likely the command-outstanding flag `[0x1befc2]` waiting for reply `0x2203`, which `0x8f280` sends only after its `0x9e218` loop `INFERRED`. The busy rule and the Koma verbs are in [07](07-usb-protocol.md).

## Open questions

- Why a continuous stream through the bridge and komacam runs at 10.9 fps when the self-test pulls mode 1 at about 28 fps. Engine time per pull and the camera-side share of a pull have not been measured.
- Raw CbCr byte order and signedness of the ring (one slot dump settles it).
- `-ABOR` during an `-LVFR` data phase, and the DCX watchdog tick length.
- A physical shutter press, or any capture not started over USB, during a pull. The engines and scratch buffers are shared with capture.
- The enumerated `wMaxPacketSize` has not been logged. Device speed is high speed (`HW`), so 512 is `INFERRED`.
- Cache attributes of the ring at `0x8470000`.
- The capture writer's open-file slot count, and whether the race that damaged `R0015722.JPG` is the file layer. The host fix (no card commands until idle) has not run on a camera.
- Whether `stallfix` fix 1 keeps the LCD on in the state-9 USB screen on hardware.
- Mode 2 palette: the 2026-11-07 pull decoded all white; whether the palette registers read back on hardware, or the host's ROM-palette fallback was used, is not recorded.

## Evidence

- [analysis/liveview/frames/README.md](../../analysis/liveview/frames/README.md): ring geometry, rotation, VD sync, states, engines, memory
- [analysis/liveview/encode/README.md](../../analysis/liveview/encode/README.md): JPEG engine, 193-byte header, movie mode, AVI measurements
- [analysis/liveview/encode/avi-index-stats.json](../../analysis/liveview/encode/avi-index-stats.json): per-AVI frame sizes and rates
- [analysis/liveview/speed/README.md](../../analysis/liveview/speed/README.md): USB link speed and `RETR` throughput
- [analysis/liveview/usb/README.md](../../analysis/liveview/usb/README.md): sender, buffer, tDcx, dispatcher, data-phase gate, watchdog, PTP
- [analysis/liveview/build/README.md](../../analysis/liveview/build/README.md): `-LVFR` build, replies, header, mode 2, emulator scenario
- [analysis/webcam/MODE3.md](../../analysis/webcam/MODE3.md): mode 3, encoder limits, pixel aspect
- [analysis/tether-feature/README.md](../../analysis/tether-feature/README.md): tether, `0x23a888`, detach path, file-slot use, LCD in state 9
- [analysis/stall/ROOT-CAUSE.md](../../analysis/stall/ROOT-CAUSE.md): second-session stall, `-KSET` busy mechanism, post-shot live view
- [analysis/hw-tests/RUNBOOK-2026-11-08.md](../../analysis/hw-tests/RUNBOOK-2026-11-08.md): stall A/B, self-test, mode 3, `--lcd-off` results
- [analysis/hw-tests/RUNBOOK-2026-11-08-PM.md](../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md): spare self-test, Remote view, damaged shot, Zoom soak, battery
- [analysis/hw-tests/2026-11-07-spare.md](../../analysis/hw-tests/2026-11-07-spare.md): 2026-11-07 main self-test (mode 2 in state 9, `RETR` rate)
- [analysis/hw-tests/tether-debug/selftest-1.4-run1/report.md](../../analysis/hw-tests/tether-debug/selftest-1.4-run1/report.md): USB device speed (high)
- [analysis/hw-tests/WEBAPP-WRITE-AUDIT.md](../../analysis/hw-tests/WEBAPP-WRITE-AUDIT.md): `-KSET` busy window
- [analysis/usb-first-contact/README.md](../../analysis/usb-first-contact/README.md): first USB contact, unplug power-off
- [analysis/peaking/README.md](../../analysis/peaking/README.md): VD task hook
- [analysis/static-resident/README.md](../../analysis/static-resident/README.md): low-RAM gap and static resident block
- [analysis/koma-0.4-candidate/README.md](../../analysis/koma-0.4-candidate/README.md): koma04 build with tether, liveview, stallfix
- [tools/koma/features/liveview.py](../../tools/koma/features/liveview.py), [tether.py](../../tools/koma/features/tether.py), [stallfix.py](../../tools/koma/features/stallfix.py), [peaking.py](../../tools/koma/features/peaking.py)
- [tools/komaset/bridge/README.md](../../tools/komaset/bridge/README.md), [tools/komacam/README.md](../../tools/komacam/README.md): host protocol handling and hardware notes
