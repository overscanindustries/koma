# Address reference

Unless a row says otherwise, an address is a 2.40 application runtime address. The image loads at `0x1c000`. In the `qrflect3` update file, file offset = runtime address - `0x1be00`, and payload offset = runtime address - `0x1c000`. Values below `0x10000` in the Monitor section are Monitor addresses. `0x08xxxxxx` is SDRAM, `0x10xxxxxx` to `0x12xxxxxx` is MMIO, and `+0x..` is a header or store offset. Flash blocks are 64 KiB. Names are descriptive, not firmware symbols. Addresses from other firmware versions are not listed.

## Monitor

The Monitor is the 64 KiB boot program in flash block 0.

| Address | Name | Purpose |
|---|---|---|
| `0x156c` | Flash-to-RAM loader | Loads the application; returns -1 on read failure |
| `0x2194` | Card RAM loader | Loads file types 2, 3, 7 |
| `0x2394` | Card scanner | Tries `/ATA1/QRFLECT0..9` in order |
| `0x1220000c` | Fourth handoff word | `0x0a780000` after a flash boot, `0x0e780000` after a card RAM boot |

## Boot and update

| Address | Name | Purpose |
|---|---|---|
| `0x1c000` | Load and entry address | |
| `0x1c1d8` | Return to Monitor | R0 = 0 returns through `0x235964` |
| `0x1c214` | IRQ entry | Reads INTC pending `+0x10` and mask `+0x08`, dispatches bits 1..17 then 0 |
| `0x1e7ac` | Startup function | Settings load, service keys, update gate, script wait; runs on `MainCtrl` |
| `0x1eb22` | Update gate | UP + TELE + dial 1/10/9, or invalid info |
| `0x275cc` | Streaming weighted checksum | The updater's gate |
| `0x278d4` | `QRFLECT0..9` scanner | Name template at `0x177968` |
| `0x32b8c..0x32de1` | Shutdown body | Settings save, CPU1 specials, `0x4fcaa` |
| `0x5d18c` | Update orchestration | Returns 1 when no store write happened |
| `0x62d76` | Header validator | Windows: type 1 `[0,0xfc00)`, types 2/3/7 `[0x1c000,0x406800)`, type 6 `[0,0x1000000)` |
| `0x63b88` | Program main | Refuses more than `0x2f0000` bytes after the info erase |
| `0x63c68` | Erase main info | First destructive step |
| `0x63c80` | Program main info | Last step |

## Update file header

| Offset | Name | Purpose |
|---|---|---|
| `+0x00` | Magic | `R_MG1_REFLECTOR` XOR `0x80` |
| `+0x10` | Type | 1 Monitor, 2 main, 3 main, 5 adjustment text, 6 extended data, 7 RAM carrier |
| `+0x12..+0x17` | Version and bounds | Official: `02 40 00 00 00 00` |
| `+0x1f0` | Tail | `R_MG1_UPDATED!!!`, checked at boot |

## Memory and MMIO

| Address | Name | Purpose |
|---|---|---|
| `0x08000000` | Image SDRAM base | |
| `0x36adec..0x3dadec` | `KernPool` | Kernel memory pool |
| `0x3dadec..0x422800` | Low-RAM gap | Unused by stock; Koma links its resident segment here |
| `0x8470000` | Live-view ring | 3 frames of 640 x 244, stride 672; Y slots `+0`, `+0x2aa80`, `+0x55500`; CbCr `+0x7ff80`, `+0xaaa00`, `+0xd5480` |
| `0x10200000` | USB controller | M66592 family |
| `0x10800000` | OneNAND window | |
| `0x1081e000..0x1081e498` | OneNAND registers | |
| `0x12000000` | INTC | `+0x04` soft reset, `+0x08` mask, `+0x10` pending |
| `0x1201401c` | Capture timing status | Bit 31 valid, bits 23:16 window |
| `0x12030000` | GPIO | `+0x1c` polarity, `+0x20`/`+0x24` data, `+0x2c`/`+0x30` pending, `+0x34`/`+0x38` mask |

## RAM state

| Address | Name | Purpose |
|---|---|---|
| `0x1bd1b8` | `remtin` flag | Remote-in |
| `0x1bd240` | Mass-storage write latch | Lasts one boot |
| `0x1bd241` | USB mode | 0 ORIGINAL, 1 PTP, 2 MASS STR |
| `0x1bd3a8` | Card-probe done | The loader and update path wait on it |
| `0x1f4db0`, `0x1f4db3` | LUT selector cache | Clear after a module upload |
| `0x1f4e7a`, `0x1f4e7b` | Focus type, RL2 state | RL2 state 2 = full press pending at focus start |
| `0x23b068` | `cp` shooting parameters | `cp+0x1d` special state, `cp+0x22` mode, `cp+0x40` CC1 select |
| `0x23b08a` | Colour mode | 0 colour, 1 B&W, 2 TE |
| `0x2b9d84` | Key state array | |
| `0x2cf640`, `0x2cf658` | Live-view ring pointer tables | Y, CbCr |

## Tasks, kernel and dispatch

| Address | Name | Purpose |
|---|---|---|
| `0x15f024`, `0x15f084` | `KernPool` alloc, free | |
| `0x5d978` | Semaphore wait | Stores the caller's LR at `TCB+0xd0` |
| `0x34038` | `tCameraMain` loop | |
| `0x8a3b8` | `tOpeMain` loop | |
| `0x23578` | Capture-path try-take | Leaks the LCD mutex when the panel is off |

## Keys and UI

| Address | Name | Purpose |
|---|---|---|
| `0x50eb0` | Key poll | With injection overlay |
| `0x51228` | Dial reader | Virtual while injecting |
| `0x513c0` | Key event emitter | Emits nothing while two keys are down |
| `0x51740` | Key injection sink | Control codes 0, 1, 2, 42 |
| `0x517dc` | Key funnel | Scanned, injected and CA-1 keys; message `0x7202` |
| `0xe3698` | SETUP action rows | Dispatched on hard-coded row numbers |
| `0x18108c` | Key table | 42 entries of `0x24` bytes |
| `0x1c224c` + 12k | SETUP rows | k = 0..29 |
| `0x1bd452` | IMG SET cursor to value | `00 01 02 03 06 04 05` |

## Settings block (`0x1b000`)

Settings live in a 4 KB RAM block loaded and saved by the routines below. See [settings-and-image.md](settings-and-image.md) for byte meanings.

| Address | Name | Purpose |
|---|---|---|
| `0x1b000` | Magic | `MG1H` |
| `0x1b030` | IMG SET | 3 = B&W, 6 = B&W(TE) |
| `0x1b053` | EXPOSURE COMP. | Odd values 1..25, default 13 |
| `0x1b054` | WHITE BALANCE | 1 = OUTDOORS, 7 = DETAIL |
| `0x1b058` | FOCUS | 0 MULTI AF, 6 SPOT AF, 1 MF, 2 SNAP, 3 INFINITY |
| `0x1b059` | ISO | 0 AUTO, 1 64, 2 100, 4 200, 6 400, 7 800, 8 1600 |
| `0x1b074`, `0x1b076` | Manual WB R, B | Clamped `0x10..0x310` at `0x9af40` |
| `0x1b0d7` | USB mode | |
| `0x1b198` | B&W sharpness | |
| `0x1b2f8..0x1b327` | Lens-error log | 3 x `0x10`; writer `0x38610` |
| `0x1b338..0x1b7ff` | Free tail | No code reference |
| `0x1b8b0`, `0x1b8b4` | Koma magic `KOMA`, Koma settings version | Version is 1 |
| `0x1b8b5..0x1b8cf` | Koma menu rows | One byte each |
| `0x1b8be` | STALL FIX FLAGS | Set by host `-KSET`; high nibble `0xA` validates the fix mask |
| `0x1b8bf` | PEAKING | Read per live-view frame by the hook at `0x95af4` |
| `0x1b8d0..0x1b8dc` | Settings-file header `KSIO` | Version `0x1b8d4`, firmware tag `0x1b8d5..0x1b8db`, last-import status `0x1b8dc` |
| `0x1b900..0x1bbff` | Recipe store `RCP6` v6 | Header, 20 records of 12 bytes from `0x1b910`; SELECT colour / range at `0x1b909` / `0x1b90a` |
| `0x1ba00..0x1ba13` | `RCP6` FX table | One byte per recipe: `0x50 \| grain \| vignette << 2` |
| `0x1bffc..0x1bfff` | Checksum | Big-endian u32, the sum of big-endian u16 words over `0x000..0xffb` |
| `0x4947c`, `0x4949c` | Settings block load, save | Called as `(0x1b000, 0x1000)` |
| `0x3d4a4` | Settings load | Validation, defaults, marker migration |
| `0x3d9b8` | Checksum routine | |
| `0x6dc18..0x6fa90` | Per-setting validators | |
| `0x55d80` | File remove | Used by the `PARAM.DAT` one-shot |

## Adjustment store (`0x08010000`)

An 8 KB factory calibration store in SDRAM, backed by flash block `0x3d`.

| Address | Name | Purpose |
|---|---|---|
| `0x08010002/3` | ADJ version | |
| `0x08010008..0x0801000b` | Factory date | BCD |
| `0x08010017` | Language | `0xff` forces USB mode 0 |
| `0x08010020` | MakerNote tag 5 source | 16 bytes |
| `0x08010096..0ab` | FINE/NORMAL JPEG targets | |
| `0x080103a0`, `0x080103c4` | Per-body WB gain tables | Indexed by WB DETAIL |
| `0x08010431` | Calibration byte | Default `0x0e`; `0xff` hangs boot |
| `0x080106e0` | Focus-type calibration | Read by `0x70b8c` |
| `0x08010892`, `0x08010894` | SNAP distance, infinity preset | SNAP distance 410 = 1024/2.5 m |
| `0x08010a04..0x08010af7` | Filter parameter table | |
| `0x08010b04..0x08010c54` | Filter groups | YH, ENH, knee, ColorNR, RGB filter, interpolation, LowPass |
| `0x08010c78..0x08010c7e` | Colour NR thresholds | |
| `0x08011380..94` | Matrix rows | 0 in stock |
| `0x080113f0..f7` | Tone-curve selectors | |
| `0x080113f8` | Luma word | Source for `0x1201800c` |
| `0x175380` | ROM defaults of the store | |
| `0x27d0c` | Store to `/ATA1/ADJROM.DAT` | Writes a file, not a loader |
| `0x63d9e` | Store writer | Erases block `0x3d`, programs `0x2000` bytes |

## Flash blocks

| Block | Purpose |
|---|---|
| `0` | Monitor (`MON`) and info sector `0x7e` (`INF`) |
| `1..0xc` | Replacement table and spare pool (`REP`) |
| `0xd..0x3b` | Main body (logical); `0xd..0x2e` hold the stock image |
| `0x3c` | Main info at sector `0x7e`; erased first, programmed last |
| `0x3d` | Adjustment store |
| `0x3e..0x52` | Extended data |
| `0x53` | Unused |
| `0x54..0x1ff` | `/IROM` store; `0x55` and `0x66` are written every boot |

## Flash driver

| Address | Name | Purpose |
|---|---|---|
| `0x28194` | Erase all blocks | Blocks `1..0x1ff` except bad ones |
| `0x177978` | Geometry table | `+0` valid, `+2` device ID, `+6` 512 blocks, `+8` 64 pages, `+0xa` 2 sectors |

## Service engine, scripts and terminal

| Address | Name | Purpose |
|---|---|---|
| `0x54164` | Adjustment command engine | Letters A..Z, 17 handlers, arbitrary memory read/write |
| `0x54020` | `tAdjust` terminal task | Gets `qADJUSTUSB` into `0x1bd394` |
| `0x66878` | `adjcom` | Upper-cases a 5-character token and calls `0x54164` |
| `0x62670` | Terminal line dispatcher | Table at `0x17781c`; no authentication |
| `0x17781c` | Terminal table | `r`, `w`, `d`, `x`, `ds`, `im`, `io`, `raw`, `lk`, `t`, `i` |
| `0x558ac` | Script file search | Tries `/IROM/SCRIPT.MG1`, then `/ATA1/SCRIPT.MG1` |
| `0x65202`, `0x65220` | `tScript` body, exit handling | Return code -2 skips a line, -1 stops |
| `0x65260` | Script buffer | 64 KiB |
| `0x661b0`, `0x66ab4` | `poff` | Saves state, arms reboot flag `0x2b9e5d`, pulses POWER |
| `0x66988` | `install` | Deletes `/IROM/SCRIPT.MG1`, copies `/ATA1/SCINST.TST` to it |
| `0x67434` | Flash dump to `/ATA1/IROM.DAT` | Read-only to flash |
| `0x674bc` | Flash restore | Blocks 1 and up; skips block 0 |

## USB

| Address | Name | Purpose |
|---|---|---|
| `0x1fb400` | USB command table | 35 records of 36 bytes |
| `0x1fb910` | `-CCTL` sub-command table | |
| `0x5d5d0` | USB session start | `0x800` |
| `0x5f688` | Bulk IN send | Needs state `0x30` |
| `0xc4b64` | Switch to PTP mode | |
| `0xcb6c4` | Command-protocol transfer state machine | |
| `0xcc434` | `RETR` | |
| `0xcc598` | `-STOR` | Writes a file |
| `0xcc628` | `DELE` | |
| `0xcc7c0` | `PWD` | |
| `0xcc970` | `-NLST` | |
| `0xccb5c` | `-VER` | Replies `200 9.17 VV.VV` |
| `0xcd000` | `-CCTL` | Sub-commands `remtin`, `remtout`, `diaset`, `keyset`, `poff`; answers 200, shutter not fired |
| `0x53564` | USB console enable | Sets `0x1bd294`, sends a 12-byte `c1 20` notification |
| `0x4fe44..0x4fe63` | State-9 force | |
| `0xb7954` | Directory reader | Root end tests use FAT16 `RootEntCnt`, which is 0 on FAT32 |
| `0x131b3a` | `UsbShutter` task | Posts `(8,1)` |

USB identities: `05ca:2218` is ORIGINAL mode (bulk IN `0x81`, bulk OUT `0x02`, interrupt IN `0x83`), `05ca:0331` is PTP, and `05ca:0109` is MASS STR.

## Image pipeline

| Address | Name | Purpose |
|---|---|---|
| `0x29b28` | Capture entry | Record states only |
| `0x6e588` | Mono mode getter | 0 colour, 1 B&W, 2 TE |
| `0x8ff0e` | Mono/TE writer | Writes `0x12018038`; holds the TE table in 2.40 |
| `0x8ffc8` | Tone-table upload | `(selector, arg)`; contrast 1..5 maps to selectors `0x1e..0x22` |
| `0x91268` | Sharpness getter by mode | B&W `0x1b198`, TE `0x1b1a8` |
| `0x91e78` | Filter apply | YH, ENH, LowPass, knee, ColorNR, RGB, interpolation |
| `0x92598` | Matrix and UV-offset writer | Runs every shot |
| `0x92cc8` | Contrast getter | Reads `0x080113f0/f4`, picks the selector |
| `0x947e4` | Shutter wait | On `0x1201401c` |
| `0x96350` | Still develop pass 1 | CCD to SDRAM 16-bit raw |
| `0x99988` | Live-view ring pointer fill | Y `0x2cf640`, CbCr `0x2cf658` |
| `0xa0840` | WB / CL-BKT loop | One exposure, three develops |
| `0xa0650..0xa1056` | AE-bracket block | Holds the CL-BKT loop |
| `0xafd20` | Second develop pass | Rewrites colour registers |
| `0x10f984` | JPEG rate control | Adaptive scale, floor `0x14` |
| `0x112120` | MakerNote builder | |
| `0x11703c` | Master YUV | `CamStillRecGenMasterYUV`; date imprint at `0x117372` |
| `0x193b30` | Tone table, contrast 1 (selector `0x1e`) | 4096 u16, 12-bit index, 10-bit value |
| `0x199b30` | Tone table, contrast 5 (`0x22`) | File offset `0x17dd30` |
| `0x1f4c88` | TE (Cb, Cr) table | 6 tones x 5 densities |

## ISP colour registers

The CC2 colour stage sits at `0x12018000`.

| Address | Name | Purpose |
|---|---|---|
| `0x12018004` | Chroma gain | 4 x u8, `0x80` = 1.0; 0 gives a grey frame |
| `0x12018038` | Mono control | Bit 24 fixMode, fixed `cfr`/`cfb` (s10) |
| `0x120180d8` | Chroma coring | Two 6-bit fields (`& 0x3f3f`): low cores Cb, high cores Cr; word = (Cr << 8) \| Cb |
| `0x12018104`, `0x12018108`, `0x1201810c` | `MatR`, `MatG`, `MatB` | Mask `0xbfbfbfbf`, 0 in stock; works on 8-bit gamma values, sign-magnitude |
| `0x12018130..0x1201814c` | Knee registers | Written via `0x16d8e8` |

## Koma hook sites

| Address | Name | Purpose |
|---|---|---|
| `0x281ee` | Loader hook | `bl 0x282cc` becomes `bl 0x57f78`; the loader calls `0x282cc` first |
| `0x57f74..0x58197` | Loader cave | 548 bytes in the Dcfs test group (`0x56f80..0x58800`), used by Koma 1 and Koma 2 |
| `0x57f78` | Card-module loader entry | Loads `/ATA1/GRMOD.BIN` |
| `0x5809c` | Loader service table | 48 bytes: file open/read/close, alloc/free, key funnel, LUT upload |
| `0x58190`, `0x58194` | Publish slot | `KOMA` and the manifest address, written after install |
| `0x112578` | Version tag slot | `Rev%4.4x` rewritten to `Rev024M` (Koma 1) or `240K2` (Koma 2) |
| `0x580cc..0x58104` | `open_both` tail (Koma 2) | Opens card `GRMOD.BIN`, else mounts drive 0 and opens `/IROM/GRMOD.BIN` |
| `0x4fe44` | `tether` redirect | Skips the state-9 force while `remtin` and key injection are on |
| `0xcd090` | `tether` redirect | At the `-CCTL remtout` call |
| `0x1fb784`, `0x1fb794`, `0x1fb7a4` | `-FDWL` record (ID `0x1a`) | Name, args flag, handler; `liveview` turns it into `-LVFR` |
| `0x95af4` | `peaking` hook | VD-task hook |
| `0x117382` | SELECT colour pass hook | In the master YUV pass |
| `0x3c83c`, `0x3c840`, `0x3c850` | SNAP routine and its two load sites | `0x36ff0` re-applies SNAP |
