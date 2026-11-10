# 06. Image pipeline and colour

**Summary.** A still goes from the CCD through white balance, an edge-enhancement block, one shared 4096-entry tone table, a colour stage and a mono/tint register to a 4:2:2 master YUV image and a size-targeted JPEG. Five stock tone tables (one per contrast level) are shared by every mode, so stock B&W has no curve of its own. On hardware, the per-shot hook at `0x96436` reaches the JPEG: chroma gain, the 3×3 colour matrix and a chroma-coring field are live, Koma's per-shot tone-curve uploads produce distinct curves, and a software pass on the finished UV plane gives selective colour. The never-called IQ hue matrix, chroma clip and chroma offset do nothing even when written. No edit of a stock tone table has been shown to reach a JPEG.

Tags: `HW`, `EMU`, `STATIC`, `INFERRED`, defined in [features](evidence-ledger.md). Emulator register writes are not pixels. Addresses are firmware 2.40 runtime addresses (payload offset + `0x1c000`). `cp` is the camera parameter block at `0x23b068`. Related chapters: [04 application architecture](04-application-architecture.md), [05 settings and adjustment store](05-settings-and-adjustment-store.md), [07 USB protocol](07-usb-protocol.md), [08 card scripts](08-card-scripts-and-service-terminal.md), [10 emulator](10-emulator-and-tooling.md), [11 modification playbook](11-modification-playbook.md), [12 version history](12-version-history.md).

## Key addresses

| Address | Meaning | Tag |
|---|---|---|
| `0x193b30` / `0x195b30` / `0x19bb30` / `0x197b30` / `0x199b30` | Stock tone tables, contrast 1 / 2 / 3 / 4 / 5 (selectors `0x1e..0x22`) | `STATIC` |
| `0x19db30` | Sixth table (descending, 1019 → 1), selector `0xe`; never selected by normal shooting | `STATIC` |
| `0x8ffc8(sel, 0)` | Selector API: table pointer, then both LUT transfers | `STATIC` |
| `0x166334` | CC1 LUT transfer (port `0x12014014`, control `0x12014010`, busy `0x12014064` bit 0) | `STATIC` |
| `0x16d31c(off, n, buf)` | CC2 LUT transfer (port `0x1201811c`, control/status `0x12018118`, busy bit 16; masks to 10 bits) | `STATIC` |
| `0x900f4` / `0x9035c` | CC1 shot path: selector / uploader | `STATIC` |
| `0x90278` / `0x90408` | CC2 shot path: selector / uploader | `STATIC` |
| `0x92050` | Monitor-start route: loads both LUTs and the luma weights; runs before the update gate | `STATIC` |
| `0x92cc8` | Contrast getter by mode | `STATIC` |
| `0x9733a` | Normal shot (RecRL1TASK); full-press wait at `0x947e4` | `STATIC` |
| `0x1f4db3` / `0x1f4db0` | Cached CC2 / CC1 selector bytes | `EMU` |
| `0x96350` | Still develop pass 1; per shot writes chroma gain, matrix rows, coring, unity gains and mono | `EMU` |
| `0x96436` | `bl 0x8ff0e` in pass 1: the per-shot override point | `HW 2026-11-06 main` |
| `0xafd20` / `0xafd3e` | Develop pass 2 / its `bl 0x8ff0e` | `STATIC` |
| `0x8ff0e` | Mono/tint register writer (`0x12018038`) | `STATIC` |
| `0x92598` / `0x922bc` / `0x92200` | Matrix+coring writer / chroma-gain writer / per-mode packed word | `STATIC` |
| `0x9b54c` | WB gain computation (no B&W test) | `STATIC` |
| `0xdd888` | Stored defect-list repair (callers `0x96bc8`, `0x9fb20`, `0x9fcf4`, `0xa0144`) | `STATIC` |
| `0x12018004` | Chroma gain | `HW 2026-11-06 main` |
| `0x12018104` / `108` / `10c` | Colour matrix rows MatR / MatG / MatB | `HW 2026-11-06 main` |
| `0x120180d8` | `pUVoffset`, chroma coring: low field Cb, high field Cr | `HW 2026-11-06 main` |
| `0x12018028` / `02c` / `030` | IQ hue matrix / chroma clip / chroma offset: dead | `HW 2026-11-06 main` |
| `0x1201800c` | Luma weights (`40 08 99 3a`) | `STATIC` |
| `0x12018038` | Mono / fixed chroma (`fixMode`, `cfr`, `cfb`) | `STATIC` |
| `0x12018110` / `114` | WB gains | `STATIC` |
| `0x1f4c88` | TE tint table base; entry = base + 40·tone + 8·density, first real entry `0x1f4cb8` (tone 1, density 1) | `STATIC` |
| `0x1b074` / `0x1b076` | Manual WB R, B (clamped `0x10..0x310`) | `STATIC` |
| `0x91e78` / `0x912bc` | Enhancer setup / parameter picker | `STATIC` |
| `0x91268` | Sharpness getter (develop and EXIF) | `STATIC` |
| `0x11703c` | `CamStillRecGenMasterYUV` (master YUV, shading call, Koma pixel-pass hook at `0x117382`) | `STATIC` |
| `0x8605f00` / `0x8f40e00` | 8 MP master Y / UV planes (stride `0xd00`) | `STATIC` |
| `0x8470000` | Live-view ring: 3 frames × 2 planes (Y, CbCr), 640×244 at stride 672 | `STATIC` |
| `0x10f984` | JPEG rate control (real path `0x1103d0` → `0x10f984`) | `STATIC` |
| `0x18c330`, `0x18c370` | Annex K luma and chroma quant tables | `STATIC` |
| `0x112120` | MakerNote builder | `STATIC` |

## Pipeline overview

### Stage table

| Stage | Where | Mode-dependent? | Tag |
|---|---|---|---|
| Mode from settings: 0 colour, 1 B&W, 2 TE, stored `cp+0x22` (`0x23b08a`) | `0x6e588` (callers `0x54bd0`, `0x334cc`) | sets the mode | `STATIC` |
| CCD to SDRAM raw, 16-bit | `0x96350` | no | `STATIC` |
| Long exposure: second frame (`0x1199b4`), dark subtract `0x16e4e4`, hot-pixel filter `0x119e18` when `0x080103e8` = `0xAA` | `0x97aee` | no | `STATIC` |
| Stored defect-list repair | `0xdd888` | no | `STATIC` |
| WB gains `cp+0x268/26a/26c` → `0x12018110/114`; R and B scaled ×16/25 (64 = 1.0, `INFERRED`) | `0x96242`, `0x96350`; gains `0x9b54c` | no B&W test | `STATIC` |
| Interpolation, RGB filter, edge enhancement (YH, ENH) | `0x91e78` | no B&W branch | `STATIC` |
| Tone LUT, 4096 entries, CC2 per shot (CC1 when `cp+0x40` = 1) | `0x90278` → `0x16d31c` | selector depends on mode | `STATIC` |
| Chroma gain, colour matrix, chroma coring, `SxUV`, unity gains | `0x922bc`, `0x92598`, `0x928d8` | chroma gain only | `STATIC` |
| Mono / tint: fixed Cb/Cr | `0x8ff0e` → `0x12018038` | B&W and TE only | `STATIC` |
| Master YUV 4:2:2; software chroma NR if colour and ISO ≥ 400 | `0x11703c`; gate `0x116fb8` | skipped in B&W | `STATIC` |
| Lens-shading multiply on RAW-Y (WB-bracket shots only) | `0x11705c` → `0xa32dc` | gated | `STATIC` |
| Date imprint into the finished planes | `0x13f0b4` at `0x117372` (also `0x1181ba`, gated `0x118192`) | no | `STATIC` |
| JPEG rate control and encode | `0x10f984` | no | `STATIC` |
| EXIF/MakerNote | `0x112120` | no | `STATIC` |

Normal still path: `0x97aee` → `0x97694` → `0x969b8` → `0x966d0` → `0x96350`, `0x96242`, `0x11703c` (`STATIC`). Order inside the hardware block is not documented; the colour-matrix fits place the matrix and coring after the tone curve (see [Colour matrix](#colour-matrix-matrgb)).

### Diagram

```
 shutter full-press (0x947e4 in 0x9733a)
        v
 CCD -> SDRAM raw16 (0x96350) [long exposure: dark subtract, hot-pixel filter]
        v
 defect repair (0xdd888)
        v
 WB gains R,G,B -> 0x12018110/114   (also programmed in B&W)
        v
 +--------------------------- CC1/CC2 hardware block ---------------------------+
 | interpolation / RGB filter / YH+ENH edge enhance  (0x91e78, store tables)    |
 | tone LUT: 4096 x 10-bit, one table for all channels (CC2 0x1201811c)         |
 | colour matrix MatR/G/B 0x12018104..10c  (chroma only, after the tone curve)  |
 | chroma gain 0x12018004; chroma coring 0x120180d8                             |
 | mono/tint 0x12018038 (B&W: fixed Cb/Cr; TE: adds tint)                       |
 | luma weights 0x1201800c (written at monitor start)                           |
 +------------------------------------------------------------------------------+
        v
 master YUV 4:2:2 (0x11703c)  -> date imprint -> Koma pixel-pass hook 0x117382
        v
 JPEG rate control (0x10f984) -> encode (camJPGhr, IRQ line 5)
        v
 EXIF/MakerNote (0x112120)
        v
 SOI APP1(Exif + MakerNote + 160x120 thumb + 320x240 preview) DQT DHT SOF0 SOS ... EOI
```

## Tone tables

### Format

| Property | Value | Tag |
|---|---|---|
| Count and placement | Five tables of `0x2000` bytes, back to back from `0x193b30` to `0x19db30`; table `0x193b30` is at payload offset `0x177b30` | `STATIC` |
| Entries | 4096 big-endian u16; input index 12-bit, output 0..1023 | `STATIC` |
| Shape | Non-decreasing, first 0, last 1023, steps 0..3 | `STATIC` |
| Shared toe | Entries 0..257 identical in all five: 0 through index 169, 1 from 170, 64 at 256 | `STATIC` |
| Shoulder | First reaches 1023 at index 4060 (c1, c2), 4086 (c3), 4093 (c4), 4056 (c5) | `STATIC` |
| Address order | Contrast 1, 2, 4, 5, 3 (the default table is the highest-addressed) | `STATIC` |
| Uniqueness | A whole-payload scan finds exactly these five long monotone runs; a little-endian read gives none | `STATIC` |
| Full LUTs | Not rebuildable from knots ≥ 16 inputs apart within ±1 code; nothing upstream regenerates them | `STATIC` |
| Hardware side | Both transfer loops branch only on the busy bit and count, never on a value | `STATIC` |

Contrast changes only inputs ≥ 258. c2 = (c1 + c3)/2 within rounding and c4 ≈ c3 + 0.507·(c5 − c3), so Ricoh designed three anchors (c1, c3, c5) and interpolated the others. Mean offset from c3: c1 −14.4, c2 −7.0, c4 +12.0, c5 +23.5 codes (`STATIC`). Stock contrast 5 is already hard: 4.8% of input codes map to ≤ 2/255 and 15.7% to ≥ 253 (`STATIC`, simulated from table data).

### Contrast to table

| Contrast | UI position | Selector | Table | Also reached by |
|---|---|---|---|---|
| 1 | minus end | `0x1e` | `0x193b30` | contrast 1 in TE, SETTING1/2 |
| 2 | one below centre | `0x1f` | `0x195b30` | SOFT (store `0x08010c6c` = 2) |
| 3 | centre | `0x20` | `0x19bb30` | NORMAL; `0x23b085` states 1, 3, 4 |
| 4 | one above centre | `0x21` | `0x197b30` | HARD (store `0x08010c68` = 4) |
| 5 | plus end | `0x22` | `0x199b30` | contrast 5 in modes 3, 4, 5, 6 |

(`STATIC`, from the default store bytes; the selector bytes live in the adjustment store, see [05](05-settings-and-adjustment-store.md).) Selection has no ISO input. Values outside 1..5 are reset to 3 at settings load (`0x3d4a4`), so a settings byte cannot select any other table. The detail screen's marker sits at x = 227/245/274/301/319 for contrast 1..5 (`STATIC`). The detail screens log "白黒詳細設定" (B&W) and "セピア詳細設定" (TE) on route `0x79346` (`STATIC`).

### Mode routing

| `0x1b030` | Label | Contrast source | Table |
|---|---|---|---|
| 0 | HARD | store `0x08010c68` = 4 | c4 |
| 1 | NORMAL | literal 3 | c3 |
| 2 | SOFT | store `0x08010c6c` = 2 | c2 |
| 3 | B&W | `0x1b197` | detail 1..5 |
| 6 | B&W(TE) | `0x1b1a7` | detail 1..5 |
| 4, 5 | SETTING1, SETTING2 | `0x1b031`, `0x1b034` | detail 1..5 |

(`STATIC`.) Across 504 mode/detail/state combinations contrast 5 gives selector `0x22` only in modes 3, 4, 5, 6 with `0x23b085` not 1/3/4 (`EMU`); modes 0, 1, 2, 7 and 255 give other selectors. A single-table edit is therefore global: it changes every mode at that level and the LCD preview. The main body's captured settings were B&W, contrast 5 (`HW main 2.40`); no JPEG records which table a given shot used.

### The 256 → 64 pin

All 11 distinct shipped u16 tone tables (42 copies) in 10 Ricoh ARM images read 64 at input 256 (65 for one curve shared by R3, GX8 and the R1 line). Ricoh varies contrast by moving the mid and upper curve and keeps the toe; between products it moves the black-clip index (4, 89, 103, 138 or 170; the GR uses 170). Ricoh never changed format, length, range, monotonicity or the pin (`STATIC`). Whether the pin matters to the hardware is unknown; Koma's curves keep it.

### History and sibling tables

| Element | History | Tag |
|---|---|---|
| Five tables | Byte-identical in 1.08, 1.09, 2.01, 2.02, 2.10, 2.31, 2.40 (start `0x18e3cc` in 1.08, `0x18ff10` in 2.10, `0x191514` in 2.31) | `STATIC` |
| Selector, LUT upload, CC1/CC2 transfers, enhancer | Relocation-identical 1.08 → 2.40 | `STATIC` |
| B&W contrast source | 1.x-2.2x: store `0x08010c74` (2.10 getter `0x69980`); 2.30: user byte `0x1b197` (2.31 getter `0x69c68`); 2.40: same byte, getter `0x92cc8` | `STATIC` |
| B&W sharpness source | 1.x-2.2x: `0x08010c75` (2.10 getter `0x8fa0c`); 2.30 onward: `0x1b198` (2.31 `0x8ff7c`); 2.40 getter `0x91268` | `STATIC` |
| Mono/tint writer | Unchanged 2.01-2.31 (2.10 `0x8e716`, 2.31 `0x8ec86`); 2.40 reads the tone × density table `0x1f4c88` for mode 2 | `STATIC` |
| IMG SET value 6 | SEPIA in 2.30/2.31; B&W(TE) in 2.40, reusing SEPIA's detail screen id 4. IMG SET order table `00 01 02 03 06 04 05` at 2.31 `0x1bacda`, 2.40 `0x1bd452`. The 2.30 SEPIA bytes `0x1b195`/`0x1b196` are now touched only by migration `0x3d4a4` and My Settings `0x298f0` | `STATIC` |
| Siblings | 500G wide carries the GR's five tables byte for byte; GX8's NORMAL equals GR c3; R3 and the R1 line re-derive curves; the M32R models (GR II, GR III, GX100/200) store a full 12-bit BT.601 RGB→YCbCr matrix instead of a tone LUT | `STATIC` |
| Companding table | `0x1b1670`: separate u8 32768-entry table shared with R3, GX8, R2, R1 (not 500G); role unknown | `STATIC` |

No release ever edited a table; 2.30's "B&W contrast" added no curve, only a new source byte. The 1.08 firmware has no B&W contrast or sharpness at all (Ricoh's V2.30 note; B&W(TE) arrived in 2.40), so 1.08 bodies cannot shoot B&W contrast frames (`HW` docs).

### Input encoding

Whether the 12-bit input is linear light, gamma-encoded or post-matrix is not established. The only support for "linear" is the toe (about 170 zeros, then slope 0.25) (`INFERRED`). One LUT streams for every channel with no channel select, so per-channel (R/G/B) curves are impossible (`STATIC`).

## Table uploads

### Selector and transfers

`0x8ffc8(sel, 0)` maps selectors `0x1e..0x22` (and `0xe`) to table pointers, calls CC1 transfer `0x166334` (4096 entries), configures, calls CC2 transfer `0x16d31c` with the same pointer, returns 0 and clears success flag `0x1f4daa`. With argument 3 it returns `0xffffffff` with no MMIO writes. An idle upload is 8,203 MMIO writes: 4,096 to each port plus 11 control writes (`EMU`). Under a frozen clock a busy port makes the polling loop spin rather than time out (`EMU`).

### Monitor route `0x92050`

`0x985a0` (and `0xa1154`) → `0x92050` → `0x92cc8` → `0x8ffc8(sel, 0)`. It loads CC1 and CC2 for the LCD preview and writes the luma weights `0x1201800c` from store `0x080113f8`, else the literal `0x4008993a`. It runs before the update gate `0x1eb22` (table read at about 9.2 M virtual instructions, gate at about 61.5 M) when the saved settings select a table (`EMU`). It does not make the shot's upload.

### Shot route

A shutter press goes `0x9733a` → `0x90278` → `0x90408` → `0x16d31c` with its own cached selector `0x1f4db3`; `0x900f4` loads CC1 for stills only when `cp+0x40` = 1 (`STATIC`). Consequences (`EMU`):
- The LCD preview keeps the monitor-route curve while the shot's CC2 upload can differ.
- `0x92050` overwrites the LUTs without updating the cached selector bytes, so a modified upload must invalidate `0x1f4db3` or the next shot may skip its upload.
- After a custom shot, a same-selector colour shot with no intervening `0x92050` keeps the custom LUT; a menu mode change runs `0x92050` and restores stock.

Other selector users: service command `0xd095c` case `0xb0` (any selector), factory calibration `0xdc7d0`/`0xdd058` (selectors 1 and `0xe`) (`STATIC`).

### Sixth slot `0xe`

All three selector paths map selector `0xe` to `0x19db30`, which only factory adjustment `0xdc7d0` (behind flag `0x1ffe3e`) and console command `0xd0be0` use (`STATIC`). Not implemented: routing B&W contrast 5 to slot `0xe` through a gate at the cached-compare sites was designed and emulated but never built; Koma uploads its curves per shot instead (see [Recipe curve generation](#recipe-curve-generation)).

## Mono and toning

`0x8ff0e(mode)` writes `0x12018038`: 0 for colour, `0x1000000` (`fixMode`, bit 24) for B&W, `0x1000000 | cfr<<12 | cfb` for TE, with `cfr[21:12]` and `cfb[9:0]` signed 10-bit (−512..511) (`STATIC`). Chroma is replaced by constants; Y is not reweighted by this register (`INFERRED` for Y). Other bits pass through unchecked; a dead API `0x16d078` packs 8-bit Cb/Cr `<<2` and preserves bits 31:25, 23:22, 11:10 from a fourth argument, which are undocumented fields (`STATIC`).

TE tint table: entry address = `0x1f4c88 + 40·tone + 8·density`, tone `0x1b1a9` (1..6), density `0x1b1aa` (1..5), each entry (Cb, Cr). Because tone and density start at 1, the first real entry is `0x1f4cb8` (tone 1, density 1); older notes give either address for the same table (`STATIC`).

| Tone | Hue | (Cb, Cr) at density 3 | Menu route |
|---|---|---|---|
| 6 (default) | sepia | (−24, +16) | yes |
| 1 | red | (0, +16) | yes |
| 2 | green | (0, −16) | yes |
| 3 | blue | (+20, −8) | yes |
| 4 | purple | (+12, +4) | yes |
| 5 | strong green, Cr only: −45/−60/−80/−106/−142 at density 1..5 | (0, −80) | no (menu `0x5b89c` writes 6, 1, 2, 3, 4) |

Every menu tint stays within ±44 at density 5; the register allows ±511 (`STATIC`). TE never touches Y. A second, software route to fixed chroma (`0x930cc`, constant-UV fill) has no caller (`STATIC`). Chroma gain 0 also gives a true grey frame in colour mode (`HW 2026-11-06 main`; see below).

### White balance

| Item | Fact | Tag |
|---|---|---|
| Register scaling | R and B gains go to `0x12018110/114` scaled ×16/25; 64 reads as 1.0 | `STATIC` (1.0 point `INFERRED`) |
| Manual WB | Stores R, B at `0x1b074`, `0x1b076`, clamped `0x10..0x310` (`0x9af40`), about ×0.16 to ×7.84 | `STATIC` |
| ISO 1600 | All three WB gains ×2 (`0x08010312` = `0xc8`) when `cp+9` = 8: part of ISO 1600 is a digital gain | `STATIC` |
| Recorded gains | Camera Info tag 1 holds R, B as register values; on the main body's B&W card `0x65`, `0x7a` (×1.58, ×1.91) on all 59 flash-off frames | `HW main 2.40` |
| Effect in B&W | WB is programmed in B&W with no mode test; whether the gains act before Y forms (WB as a colour filter) is not tested | `INFERRED` |

### Colour bracket

The colour bracket develops frames 0, 1, 2 as B&W, colour and TE through pass 2 `0xafd20` (`0x6f768` reports 3 or 6) (`STATIC`); that all three come from one exposure is `INFERRED` from the manual and `0xa0840`.

## Colour path

### What every shot writes

`0x96350` calls `0x92598(1)`, `0x922bc(1)` (at `0x96426`/`0x9642c`) and then `0x8ff0e` (at `0x96436`). They program chroma gain, the matrix rows, the coring word `pUVoffset`, `SxUV` `0x12018000`, four Q14 unity gains `0x12018160..16c` and the mono register. Values come from a per-mode packed word (`0x92200`) whose nibbles index small store tables. Develop pass 2 (`0xafd20`) rewrites WB and mono again (`STATIC`; the 8 IMG SET cases wrote exactly the predicted values, `EMU`).

### Per-mode values in stock

Only chroma gain differs between modes. The matrix and `pUVoffset` are 0 in every mode and `SxUV` is constant (`0x2038` stills, `0x3030` monitor). Chroma is scaled by a percentage that compensates for contrast (VIVID 90%, SOFT 110%, SETTING contrast 1..5 → 120/110/100/90/80%), so VIVID's chroma word `836e687a` is close to NORMAL's `8470697c`; VIVID differs mainly by contrast 4 and sharpness 4. The stock maximum is `c0a398b4` (SETTING depth 5, contrast 1). In Adobe RGB, `0x922bc` scales bytes 31:24, 23:16, 15:8 by 97% and byte 7:0 by 90% (`STATIC`). The monitor route uses preset `0x08011304` (`755f5960`), so the LCD preview does not show the shot's saturation (`EMU`).

### Chroma gain

| Probe write (after stock `0x8ff0e`, both passes, NORMAL colour) | Mean RGB | Mean chroma | Result | Tag |
|---|---|---|---|---|
| none (reference) | 128/76/38 | 63.8 | | `HW 2026-11-06 main` |
| `0x12018004` = 0 | 89/89/89 | 0.0 | live: true grey | `HW 2026-11-06 main` |

Format: 4 × u8, `0x80` = 1.0 (`INFERRED` from the monitor default `80808080`); per-byte roles not yet resolved. Scaling all four bytes by 1.30 gives a uniform ×1.30 chroma gain: Cr/Cr₀ = 1.298 in every band (`HW 2026-11-06 main`). Bits 31:24 are computed without a mask, so a computed value above 255 is truncated (`STATIC`). Values up to `0xff` give about 2× saturation (`INFERRED`).

### Colour matrix MatR/G/B

Registers `0x12018104` (MatR), `0x12018108` (MatG), `0x1201810c` (MatB); setters `0x1725b0/c8/e0` from `0x92598`; store slots `0x08011380/84`, `88/8c`, `90/94`, all zero in every Ricoh body that has them (`STATIC`). Writing one byte (`MatR` = `0x3F000000`) turned the frame red: mean RGB 128/76/38 → 206/32/21, mean chroma 63.8 → 136.8 (`HW 2026-11-06 main`).

Behaviour fitted to three rounds of single-register frames (18 frames: single bytes of each row at `0x3F`, `MatR` byte 3 at `0x7F`, `0x20`, `0xBF` and `0x81`, combined entries; `HW 2026-11-06 main`). The current model is the second fit (v2):

| Property | Finding | Tag |
|---|---|---|
| Effect on Y | None; the matrix moves only Cb/Cr | `HW 2026-11-06` |
| Byte coding | Sign-magnitude: bit 7 sign, bit 6 ignored (`0x7F` acts like `0x3F`), bits 5..0 magnitude m, coefficient m/64; response linear (`0x20` gives 0.44-0.49 of `0x3F`) | `HW 2026-11-06` |
| Sign | `BF000000` pushes strongly cyan, `81000000` stays at stock; 7-bit two's complement is ruled out | `HW 2026-11-06` |
| Superposition | Entries of one row add linearly | `HW 2026-11-06` |
| Row | Output channel: MatR, MatG, MatB | `HW 2026-11-06` |
| Byte → input | b3 = R (all three rows), b2 = G, b1 = G (two near-identical G taps), b0 = B; relative unity 1.00 / 1.05 / 0.98 / 0.92 | `HW 2026-11-06` |
| Domain | 8-bit gamma RGB after the tone curve, desaturated toward Y: X = Y + 0.60·(RGB − Y) | `INFERRED` (fit to `HW` frames) |
| Chroma formation | ΔCb, ΔCr per row output: R (−0.17, +1.03), G (−0.87, −1.06), B (+1.21, +0.01); not BT.601 | `INFERRED` (fit to `HW` frames) |
| Clamp | Each row output is clipped to about [0, 230]; a row already outside that range is not pulled in | `INFERRED` (fit to `HW` frames) |
| Model accuracy | Pooled Cb/Cr rms 2.5 / 3.8 codes over all 18 frames; worst on strong MatG/MatB pushes and bright saturated orange | `HW 2026-11-06` |

Not validated: other light and scenes, and the order of a chroma-gain change and a matrix delta in one frame (the matrix term may be off by up to ×1.3).

### Chroma coring (`pUVoffset`)

`0x120180d8` (setter `0x17231c`, mask `0x3f3f`) is a chroma dead zone, not an offset or a colour cast: the low 6-bit field n maps Cb → sign(Cb)·max(\|Cb\| − n, 0) and the high field (bits 8..13) maps Cr the same way, in 8-bit codes at every luma level. The word is (Cr << 8) \| Cb. With n = `0x1F` the Cr fit has rms 0.48 against coring and 21.5 against an offset (`HW 2026-11-06 main`). The store supplies only one byte (`0x0801137c` or `…7d`), so stock code can reach only the low field; stock value 0 (`STATIC`).

### Dead IQ stage

| Register | Setter | Fields (s8) | Probe write | Result | Tag |
|---|---|---|---|---|---|
| `0x12018028` IQ hue matrix | `0x171a00` | `iry[31:24] qry[23:16] iby[15:8] qby[7:0]` | `C00000C0`; `40000040` | no change | `HW 2026-11-06 main` |
| `0x12018030` chroma offset | `0x171cb4` | `icon[15:8] qcon[7:0]` | `icon` = +64 | no change | `HW 2026-11-06 main` |
| `0x1201802c` chroma clip | `0x171b74` | `imax imin qmax qmin` | 0 | no change | `HW 2026-11-06 main` |
| `0x12018028` with control-word enable | | | `C00000C0` and `0x12018100` bit 6 set | no change (chroma rms vs stock 0.6, the noise floor) | `HW 2026-11-06 main` |

None of the three setters has a caller in any of 17 Ricoh ARM images (GR 1.08-2.40, R3, GX8, R2, 500G wide, GX, R1, G4, 400G wide, RZ1), and the defaults loader `0x98cb8` skips `0x28/2c/30` (`STATIC`). The stage is either bypassed in hardware or behind an enable no firmware sets; the read-modify-write sites of `0x12018100` touch only bits 0-5 and 8-9 (`STATIC`). Hue-selective looks therefore need the matrix or a pixel pass.

### Other colour registers

| Register | Setter | Format | Stock use | Tag |
|---|---|---|---|---|
| `SxUV` `0x12018000` | `0x17190c` | two byte fields, meaning unknown | `0x2038` stills, `0x3030` monitor; R3 also uses `0x2030`, `0x2018` | `STATIC` |
| Unity gains `0x12018160..16c` | `0x17271c..0x172740` ← `0x928d8` | 4 × Q14 (`0x4000`); channels unknown | constant | `STATIC` |
| Chroma clip B `0x12018078` | `0x172080` | `imaxb iminb qmaxb qminb`, s8 | 0 at monitor defaults only, in every body | `STATIC` |
| Luma weights `0x1201800c` | `0x171958` ← `0x92050` | `40 08 99 3a` (`0x99` = 153 ≈ 0.299·512, `0x3a` = 58 ≈ 0.114·512: BT.601 R, B) | monitor start only; stills inherit | `STATIC` (forms Y: `INFERRED`) |
| WB `0x12018110/114` | `0x1725f8/0x172610` | 9-bit | every shot | `STATIC` |
| OB / black `0x12014004` | `0x69678` | 4 × 7-bit sign-magnitude per Bayer channel | every shot | `STATIC` |

CC2 offsets with no driver at all: `0x24`, `0x34`, `0x88`, `0x98..0xbc`, `0xc8..0xd0`, `0xe0`, `0xf0..0xfc`, `0x158/15c` (`STATIC`); their contents are unknown.

### Unused capacity

- Chroma-gain table `0x08011330[1..12]`: stock words use slots 3..7; slots 1, 2, 8..12 all hold `755f5960`. No hidden presets (`STATIC`). R3's table holds presets the GR lacks: `72626160 947f7e7c 4f444343 85676160`.
- Matrix and UV slots: two per row, all zero (`STATIC`).
- `SxUV` table `0x08011360[1..6]`: only `[1]` monitor and `[2]` stills used; `[3..6]` = `0x3030` (`STATIC`).
- Colour words `0x0801130c..`: seven per-mode words; scene states 1/3 use `22222222`; `0x08011328/2c` zero (`STATIC`).
- Tone slot `0x19db30` (selector `0xe`) and TE tone 5 (above).

### Hook points

| Hook | When it runs | Wins over | Tag |
|---|---|---|---|
| `0x96436` (`bl 0x8ff0e` in pass 1) | every shot, after `0x92598`/`0x922bc`, before the enhancer | stock chroma gain, matrix, `pUVoffset`, mono | `HW 2026-11-06 main` |
| `0xafd3e` (pass 2) | every shot, after pass 2's WB/mono rewrite | pass-2 WB and mono | `HW 2026-11-06 main` (probe writes in both passes) |
| `0x9733a` upload path | every shot, before `0x96350` | only registers `0x96350` does not rewrite | `STATIC` |
| `0x92050` | monitor start, before the update gate | luma weights | `STATIC` |
| `0x117382` | after master YUV and date imprint, before encode | pixels of the finished planes | `HW 2026-11-06 main` |

Writing in both develop passes and restoring the values read back through the stock getters on the last frame reproduced the stock picture (`HW 2026-11-06 main`). `0x96350` also calls `0x9b7e0` at `0x964b8`, after the `0x96436` hook, on some WB-state branches; that branch resets `cp+0x268..` to 100 and CC2 WB to unity, which would undo a WB change made at `0x96436` on those shots (`STATIC`; branch conditions not decoded).

## Noise reduction, sharpening, lens shading

| Item | Fact | Tag |
|---|---|---|
| Chroma NR `0x116dbc` | UV only, two resize passes; gate `0x116fb8` needs colour (`cp+0x22` = 0) and ISO ≥ 400 or a Tv condition; B&W never gets it | `STATIC` |
| Enhancer | `0x91e78(1)` (from `0x96350` and `0xafd20`) → `0x912bc(1, sharpness, ISO offset)` returns two packed words from store `0x08010a04..af7`; each nibble selects an entry of a smaller store table (table below); no B&W branch | `STATIC` |
| ISO bands | ISO offset `0x08011de2` vs `0x08010c64` = `0x20` and `0x08010c66` = `0x30`: below 400, 400-799, 800+; `0x91a30` blends toward the 400 band between ISO 100 and 400 | `STATIC` (`EMU` values) |
| ISO 800+ | YH offset 5 → `0x10`, ENH offset 0 → 4, ENH filter narrows (`0e001254 00000214` → `18001140 00000100`), interpolation low byte `0x30` → `0x20` → `0x03`: coring and filter choice | `STATIC` |
| ENH gain | Not monotonic in ISO: at sharpness 5, ISO 800 returns to ISO 100 strength (`05060504`); sharpness 1 at ISO 400+ is zero. A lens-index term at `0x91634` adds up to about +2 per lane, capped `12 10 0e 07` (what the index tracks is unknown) | `STATIC` (`EMU` values) |
| LowPass | Never engaged; index 3 (`00040e40 00000200`) unused | `STATIC` |
| Grain | No random or dither source in imaging code or ISP registers (the ISP has edge-filter coefficients, resizer bias and OB offsets); the only `rand()` is at `0x641ec` in the flash-driver range | `STATIC` (bounded) |
| Full-size resampling | None (`cp+0xf` = 9, `cp+5` = 0 skip the resize) | `STATIC` |
| Lens shading | 160×120 u8 gain map (`0x80` = 1.0, `INFERRED`) read from `/IROM/PICTDATA.RDD` (`0x4c8f0`) into `0xbffb3bc`, upsampled to `0x987bd20` (`0xa343c`), multiplied into RAW-Y by DMA calc engine `0x16e4e4` (CalcType 1). Called at `0x11705c`; gate `0xa39bc` = `cp+0x1d == 5 && [0x1f9ec2] < 2` (WB bracket only). A missing file or non-zero store byte `0x080103f7` gives a constant map (`0xa3644`) | `STATIC` |

Enhancer parameters (`STATIC`; names from assertion strings):

| Word, bits | Parameter | Selector | Store table | Register |
|---|---|---|---|---|
| 1, 28-31 | YH limit | `0x90554` | `0x08010b04` | `0x120180dc` |
| 1, 24-27 | YH thresh | `0x90604` | `0x08010b08` | `0x12018124` |
| 1, 20-23 | YH offset | `0x906cc` | `0x08010b10` | `0x12018010` |
| 1, 16-19 | YH gain 1, 2 | `0x907b4` | `0x08010b28` | `0x12018128/12c` |
| 1, 12-15 | ENH filter 1, 2 | `0x90938` | `0x08010b68` | `0x120180e8/ec` |
| 1, 8-11 | ENH comp | `0x90ac0` | `0x08010b98` | `0x12018018` |
| 1, 4-7 | ENH offset | `0x90ba8` | `0x08010c04` | via `0x16d8e8` |
| 1, 0-3 | ENH gain | `0x90c74` | `0x08010bc0` | `0x1201801c`, via `0x16d8e8` |
| 2, 28-31 | LowPass 1, 2 | `0x90d70` | `0x08010b80` | `0x12018150/154` |
| 2, 16-27 | Knee mid gain, mid point, top | `0x90e48` | `0x08010ba8`, `c1c`, `c34` | `0x12018130..14c` via `0x16d8e8` |
| 2, 8-11 | ColorNR | `0x91032` | `0x08010c4c` | `0x12018120` |
| 2, 4-7 | RGB filter | `0x910d4` | `0x08010c50` | `0x120180e4` |
| 2, 0-3 | Interpolation | `0x91184` | `0x08010c54` | `0x12018170` |

The stock shading gate is a separate, unused mechanism: a darkening map behind it would give a hardware vignette on WB-bracket shots only (visual effect `INFERRED`; whether the file exists on a given body is unknown). It is unrelated to Koma's VIGNETTE, which is a software pass (see [Grain and vignette](#grain-and-vignette)). EXIF sharpness reads 2 (hard) on all 136 main-body B&W frames, so stored B&W sharpness is 4 or 5 (`HW main 2.40`).

## Pixel access

| Item | Fact | Tag |
|---|---|---|
| Master planes | Y at `0x8605f00`, UV at `0x8f40e00` (8 MP), stride `0xd00` = 3328; plain CPU stores, no cache maintenance. Stock already writes them in the date imprint `0x13f8a0` | `STATIC` |
| Plane fields | Y base `cp+0x54`, UV base `cp+0x58`, stride `cp+0x5c`, width `cp+0x5e`, lines `cp+0x60`; width holds width + 16 until the resize returns | `STATIC` |
| UV layout | 4:2:2, interleaved byte pairs (one pair per 2 pixels), same stride as Y | `STATIC` |
| Pair order | Cb first (JPEG order) | `HW 2026-11-06 main` |
| Bayer buffer | `0x987bd00`, 16-bit, stride `0x19f0`; RAW packer `0xde6d8` (78.1 M instructions per frame) | `STATIC` (`EMU` count) |
| Live view | Ring of 3 semi-planar frames at `0x8470000`: Y slots `+0`, `+0x2aa80`, `+0x55500`, CbCr slots `+0x7ff80`, `+0xaaa00`, `+0xd5480` (pointer tables `0x2cf640`/`0x2cf658`, filled by `0x99988`). 640×244 visible, stride 672, slot 672×260. CC2 output DMA writes one slot while the display scans another; rotation in the VD interrupt at 30 fps. The stock histogram `0x825c8` reads the ring | `STATIC` |
| Playback resize | `0x112b00` decodes into Y `0x8570000` / UV `0xb45f500`, re-encodes (640 and 1280 only) | `STATIC` |
| DNG re-develop | `0x960b4` → `0x690f8` → `0xdf10c` loads `RIMG0001.DNG` + `.PRM`; trigger unknown | `STATIC` |
| Cost | A full 8 MP per-pixel pass ≈ 62 M ARM instructions (7.8 per pixel); a 256-bin histogram of a 640×480-class frame 0.54 M. Seconds unknown (core clock unknown) | `EMU` |

| Size | Stride | Width | Lines |
|---|---|---|---|
| 3264 (8 MP) | 3328 | 3264 | 2448 |
| 2816 | 2848 | 2816 | 2112 |
| 2592 | 2624 | 2592 | 1944 |
| 2560 | 2592 | 2560 | 1920 |
| 2304 | 2336 | 2304 | 1728 |
| 2048 | 2080 | 2048 | 1536 |
| 1600 | 1632 | 1600 | 1200 |
| 1280 | 1328 | 1280 | 960 |
| 640 | 672 | 640 | 480 |

(`STATIC`.) Live-view streaming over USB is in [07](07-usb-protocol.md).

## Koma recipes and selective colour

The recipes are implemented by `tools/koma/features/bwrecipes.py`; the record layout and the store are in [05](05-settings-and-adjustment-store.md). Hardware results below come from earlier frames shot on the main body (`analysis/colour-research/test02/`, composite `docs/img/looks-real.jpg`).

### Recipe curve generation

| Step | Mechanism | Tag |
|---|---|---|
| Base curves | 8 bases (R3, STANDARD c3, HARD c5, HI-CONT, SOFT MONO, FILM 400, FINE 100, BLEACH), 1,024 B each in the module blob, base id = index; BLEACH is offered as the 8th CURVE option for every look type | `EMU` |
| Parameters | CONTRAST, KEY, SHADOW, HIGHLIGHT −4..+4 applied on the fly; every generated curve keeps Ricoh's toe (entries 0..257) and is monotone | `EMU` |
| Upload | At `0x96436`: 16 chunks of 256 entries, each generated into a 512-byte buffer and sent by `0x16d31c(offset, 256, buf)` | `HW` (earlier frames: NOIR, SHINJUKU, MONO and HICON B&W come out with distinct curves) |
| Cache | `0x1f4db3` := `0xff` after the upload, so the next stock upload is not skipped | `EMU` |
| Pass 2 | `0xafd3e` reapplies filter, WB tilt and toning; it uploads no LUT | `EMU` |
| Colour looks | After the same stock writers: SATURATION scales the chroma-gain word (7 steps, ×0.55 to ×1.45 of NORMAL's `8470697c`); the look's ROM matrix goes into MatR/G/B; TINT adds ±1/64 steps to the G row (green-magenta, luma untouched); WARMTH ≠ 0 tilts the R and B white-balance gains by 3.5% per step | `HW` for POSITIVE, NEGATIVE, BLEACH, RETRO (earlier frames: NEGATIVE, BLEACH, RETRO at ×0.40, ×0.29, ×0.41 of POSITIVE's chroma, lifted shadows on NEGATIVE and RETRO, RETRO +18° hue shift toward yellow, no cast errors); `EMU` for WARM NEG, SLIDE 50 |

Not yet shot: SOFT MONO, FINE 100, MATTE, HARD MONO, WARM NEG, SLIDE 50; DENSE and FILM 400 are indistinguishable from MONO on the one-hue test scene. The earlier session has no OFF frame, so POSITIVE against stock is not measured directly. The current package (koma04w) has not been re-shot with these looks.

### GR DIGITAL IV refit

POSITIVE and BLEACH are fitted from look tables recovered from GR DIGITAL IV 2.30 firmware. Koma vendors only derived numbers (curve shapes relative to that camera's Standard curve, per-hue-sector chroma responses, mean gains, white-balance factors), no Ricoh bytes (`INFERRED` where the table orientation was assumed). POSITIVE fits STANDARD plus CONTRAST +2, KEY +2, SHADOW −3, HIGHLIGHT −3 (rms 2.1 levels), SATURATION −1 and a fitted matrix. BLEACH needed its own curve base (rms 9.2 levels as STANDARD plus settings; 0.02 with the base), SATURATION −3 and WARMTH COOL 3. NEGATIVE, RETRO, WARM NEG and SLIDE 50 have no GRD IV source and stay estimates (`EMU`). A linear matrix gives opposite hues the same gain, so POSITIVE's reds and yellows come out about 15-20% too weak.

### Filters and toning

| Filter | Luma word `0x1201800c` | WB tilt (Q16 multipliers on the shot's R, G, B gains, clamped `0x1ff`) |
|---|---|---|
| OFF | stock (restored only if the module changed it) | none |
| YELLOW | `0x4008C31A` | none |
| ORANGE | `0x4008FF0A` | none |
| RED | `0x4008FF00` | `0xD8C5`, `0xB715`, `0x752C` |
| GREEN | `0x40086633` | none |

(`EMU`.) On OFF or a colour develop the module writes back exactly what `0x92050` writes (`[0x080113f8]` or `0x4008993a`, masked `0x7f0fffff`). That `0x1201800c` forms Y is `INFERRED`, so the filter effect is not proven.

Toning writes the B&W(TE) mono word from TE table rows: SEPIA = tone 6, SELENIUM = tone 4 (purple), BLUE = tone 3, GREEN = tone 2, level 1..5 = density; tone 5 is not used (`EMU`).

### Sharpness

The sharpness getter `0x91268` is wrapped at `0x91a8c` (develop) and `0x4165a` (EXIF); it returns the recipe's sharpness (−2..+2 mapped to stock 1..5) (`EMU`).

### Selective colour

Implemented by `tools/koma/features/selcolour.py` as the SELECT look.

| Item | Fact | Tag |
|---|---|---|
| Hook | `0x117382 bl 0x1173e8` at the tail of `0x11703c`: after the master resize and date imprint, before rate control and encode; all five callers (r1 = 0) reach it; the `cp+0x1d == 9` path `0x118074` does not. The pass ran over the full frame on a camera | `HW 2026-11-06 main` |
| Settings | COLOUR RED, ORANGE, YELLOW, GREEN, BLUE, MAGENTA (published as 1..6 at `0x1b909`); RANGE NARROW or WIDE (`0x1b90a`). Both live in the SELECT record (p5, p6) and are published per shot in the `RCP6` store header | `EMU` |
| Skip | Not run when `cp+0x22` ≠ 0 (B&W or TE develop) | `EMU` |
| Class table | 1 KiB, 32 × 32 cells of 8 codes, indexed by `(Cb >> 3) << 5 \| (Cr >> 3)`; one pool block at install; rebuilt only when COLOUR or RANGE changes (≈ 0.84 M instructions). An earlier build used a 16 KiB table | `EMU` |
| Classes | Neutral if \|cb\| + \|cr\| ≤ 12; KEEP inside the hue sector; HALF (`(byte + 128) >> 1`) in an 8° soft edge each side; otherwise neutral | `EMU` |
| Result | COLOUR = RED, NARROW: red and orange stayed saturated, every other hue went grey, full frame, no band boundary (earlier build); SELECT on the earlier frames kept only the red-orange floor | `HW 2026-11-06 main` |
| Fast path | Two neutral pairs store `0x80808080` (19 Thumb instructions per two pairs with the loop) | `EMU` |
| Cost | 43.0 M instructions for the 8 MP smoke plane (RED NARROW); worst case (whole frame in the soft band) higher. Seconds per shot are not measured | `EMU` |

| Colour | NARROW (°) | WIDE (°) |
|---|---|---|
| MAGENTA | 30-70 | 20-80 |
| RED | 88-122 | 78-132 |
| ORANGE | 127-149 | 119-157 |
| YELLOW | 151-179 | 141-189 |
| GREEN | 200-256 | 186-270 |
| BLUE | 295-355 | 283-7 |

Hue angle is `atan2(cr, cb)`; the Cb-first pair order is confirmed by the red band surviving on a camera. The soft-edge width was not measured on hardware.

### Grain and vignette

`tools/koma/features/fx.py` adds per-recipe GRAIN and VIGNETTE (OFF/1/2/3 each, recipe bytes published at `RCP6` header +11/+12) as one software pass over the master Y plane, chained at the same `0x117382` hook as selective colour (which works on UV). GRAIN adds xorshift32 noise of ±7 or ±14 levels to pixels in 16..239; VIGNETTE multiplies by a separable parabola per axis that puts a 4:3 frame's corners at 0.76 / 0.57 / 0.44 (−0.4 / −0.8 / −1.2 EV). Planes smaller than about 1660 × 1250 get no vignette. The fused ARM loop costs about 37.5 M instructions for 8 MP (`EMU`; the visual result has not been checked on a camera, and the time per shot is an estimate only).

### Live view and recipes

The monitor sets itself up at start (`0x985a0`), after each shot (`0x95cd0`, reached from `0x34004` through a table) and on setting changes (`0x338cc`). Each time it loads preset word `0x08011304` (chroma `755f5960`, matrix 0) and re-runs the mono writer `0x8ff0e`, so stock code never shows develop-time overrides other than the mono/TE switch: stock live view does not show recipes (`STATIC`). Opening a menu stops the monitor and closing it runs a full start (`STATIC`; menu behaviour `EMU`). Live white balance is applied at `0x12014008/0c`, and the monitor AWB loop pins CC2 WB `0x12018110/114` to unity (`STATIC`). The post-shot resume path does not rewrite the luma weights or re-upload the LUT, so after a recipe shot the filter weights and the recipe curve stay in the registers until the next full monitor start (`STATIC`; whether the monitor stream reads CC2 and the luma weights is unknown).

### Live-view preview (Koma `preview`, emulator only)

`tools/koma/features/preview.py` wraps the three monitor `bl 0x8ff0e` (`0x98726`, `0x95e24`, `0x8f2e0`) and `bl 0x922bc(0)` at `0x8f1f0`, and taps the monitor chroma word at `0x923fc` (`STATIC`). After the stock write it restores the recipe's chroma (monitor word × SATURATION), matrix + TINT, B&W filter luma weights and toning word; with no recipe it writes nothing (`EMU`). `preview.curve=on` also uploads the recipe curve to CC2 and CC1 at start and resume and sets both cache bytes `0x1f4db0`/`0x1f4db3` to `0xff`, so the next shot uploads its own curve (`EMU`). `preview.warmth=on` points the monitor WB veneers (`0x174e94`/`0x174e88`, literals `0x174e9c`/`0x174e90`) at Koma while `0x1f502f` is set (`EMU`, unit only). It has no per-frame hook. Not previewed: the red filter's WB tilt, SELECT's hue isolation, GRAIN and VIGNETTE. No camera result exists for `preview`; what the LCD shows with it is unobserved.

## JPEG encoder

### Quantisation and rate control

| Item | Fact | Tag |
|---|---|---|
| DQT | Annex K luma `0x18c330` and chroma `0x18c370` × scale: entry = `((scale·q >> 9) + 1) >> 1`, clamp 1..255; scale = low 16 bits of Camera Info tag 2 | `HW main 2.40` (133/136 frames; 18/18 on 1.08) |
| Mismatches | The 3 exceptions are loop-limit cases where the recorded scale is the unused one computed after the last encode (`0x10fff8`) | `STATIC` |
| Scale range | 20..65 on 2.40 B&W FINE 3264; 56 of 136 frames on the floor `0x14` = 20, where luma entries are 1 (some 2) and chroma mostly 2. At scale 65 luma is 1..8, chroma 1..6. Luma DC step 1. The 31 distinct DQT sets are the 31 distinct scale values. On 1.08 (NORMAL sizes): scale 115..261, DC step 2..4 | `HW main 2.40`, `HW spare 1.08` |
| Target | `0x10f1dc` gets min/target/max from `0x10f294`: `W·H·bpp/10/8·pct/100`, rounded to 4 bytes; size and quality index select store bytes `0x96..0xaa`. Aspect index 1 scales by 8/9, index 2 by 3/4; `shot+0x14` = 1 with size index above 2 uses 70/85/100% | `STATIC` |
| FINE 3264 band | 2.8 bpp (store `0x96` = `0x1c`), 90/105/120% = 2,516,932 / 2,936,424 / 3,355,912 bytes. Observed scan sizes 2,237,220..3,330,489: none above max, 14 below min (11 at the scale floor) | `STATIC`; sizes `HW main 2.40` |
| Iteration | First pass at scale `0x100`; up to `0x8c` = 2 fit steps while outside `0x8a`/`0x8b` = ±5%, then up to `0x8d` = 3 corrections; above max, ×1.1; floor `0x14` (`0x10fb50`, `0x110110`); observed maximum 7 passes | `STATIC`; passes `HW main 2.40` |
| Fast path | Reuses the last scale with one encode if the thumbnail's scale is within ±20 (`0x110464`) at the same size and quality; 7 frames | `STATIC`; count `HW main 2.40` |
| Feedback | Bias bytes `0x1b0f6..fb` (`0x3d..0x43`, default `0x40`) move the next target by (bias − 0x40)/4 toward min or max; persisted in the settings block; recorded in Camera Info tag `0x12` byte 0. 75 of 134 frames read `0x43` (target about 3,251,040 bytes) | `STATIC`; values `HW main 2.40` |
| Quality codes | MakerNote `0x1001` bytes 4-5 and EXIF `0x9102`: 280 = FINE 3264 (2.80 bpp); 200 (N1280, 12 frames) and 160 (N2592, 6 frames) on 1.08 | `HW` (size names `INFERRED`) |
| B&W | Chroma planes are flat, so nearly all of the size budget goes to luma | `INFERRED` |

### File structure

| Item | Value (identical in 1.08 and 2.40) |
|---|---|
| Segment order | `SOI APP1(Exif) DQT DHT SOF0 SOS`; no APP0/JFIF, APP2, DRI |
| DQT | one 130-byte payload, both tables (luma id 0, chroma id 1) |
| DHT | one 418-byte segment, the 4 standard Annex K tables (sha1 prefix `8f8ba97c8f` in 136/136 and 18/18) |
| SOF0 | 4:2:2 (Y 2×1, Cb 1×1, Cr 1×1); table ids 0, 1, 1 |
| IFD1 thumbnail | 160×120 JPEG, 3-6 KB, 2 bytes after its EOI |
| Embedded preview | 320×240 JPEG at TIFF offset 1471 (`0x5bf`); MakerNote length = preview length + 713..716 |
| MakerNote length | `P8 + pad + 0x516` (from `0x84c88`; 134/134) |
| Bytes after file EOI | 0-3 (padding of the scan, `INFERRED`) |
| APP1 | IFD0 12 tags; EXIF IFD 28 tags; MakerNote 9 tags; Camera Info 22 tags |

(`HW main 2.40`, 136 frames; `HW spare 1.08`, 18 frames.) No structural encoder change between 1.08 and 2.40 is visible; differences in scale and size are setting-driven as far as the data show (no like-for-like frames exist) (`STATIC`). MakerNote `0x1003` is 1 on all 1.08 frames and 0 on all 2.40 frames; firmware marker or setting is unknown.

### Hardware completion

The JPEG block signals completion on interrupt line 5 (mask `0x20`; dispatcher test at `0x1c2ec`, handler `0x2398c`); task `camJPGhr` is created by `0x23aaa`. Registers: control `0x1201c000`, mask `0x1201c01c`, status `0x1201c020`; start command `0xf`; status `0x2` success, `0x4` error (`STATIC`; status timing is a model assumption, `EMU`). Semaphore `0x2bfccc` is named the decoder semaphore in the source; whether the same block encodes is not stated.

### FINE target

`0x846aa` is only the shots-remaining estimate; the encoder band comes from `0x10f2ec(size, q)` reading store `0x08010096..ab`. The ROM default at `0x175380` is used only for a blank or torn store, so editing it does nothing on a healthy body (`STATIC`). With 41% of FINE frames already at the scale floor, a larger target would change few frames (`HW main 2.40` counts). Not implemented: a code change in `0x10f2ec` for a higher FINE bpp.

## EXIF and MakerNote fields

| Field | Content | Tag |
|---|---|---|
| MakerNote `0x1001` byte 40 | IMG SET from `0x1b030` (via `0x6f768` at `0x11294a`); 3 on 136/136 main-body B&W frames | `HW main 2.40` |
| EXIF `0xa40a` Sharpness | 3-level reduction of the 5-level value (1, 2 soft; 4, 5 hard) | `HW main 2.40` |
| EXIF `0xa408` Contrast, `0xa409` Saturation | Not written: the EXIF IFD holds the same 28 tags in 136/136, and only `a402 a403 a406 a40a` come from the `0xa4xx` block | `HW main 2.40` |
| EXIF `0xa406` SceneCaptureType | From `0x2d27ac`, driven by `0x23b085`; 0 on all frames | `HW main 2.40` |
| Camera Info tag 1 | WB gains R, B (×16/25 register values, via `0x9d980`) | `STATIC` |
| Camera Info tag 2 | Rate-control passes, corrections, scale | `HW main 2.40` |
| Camera Info tag `0x12` | Target bias, retry count | `STATIC` |
| Camera Info tag `0x15` | ISO offset in 1/16 EV from ISO 100 | `STATIC` |

Contrast is recorded nowhere found; whether it hides in constant bytes (`0x1001` bytes 23-27, 33-34, 41-47; Camera Info `0x11`, `0xe`) cannot be told from one body's single-setting card. Unit-specific fields (MakerNote `0x5`, Camera Info 4, `0xc`, `0x14`) are not reproduced.

## What is and is not shown

| Claim | State |
|---|---|
| Five tables, 4096 × u16 BE, 10-bit | `STATIC` |
| Contrast → selector → table | `STATIC`; routing `EMU` (504 combinations) |
| Shot upload route differs from the monitor route | `EMU` |
| Table read at startup before the update gate | `EMU` |
| Per-shot hook `0x96436` runs and its CC2 writes reach the JPEG | `HW 2026-11-06 main` |
| Chroma gain, colour matrix, chroma coring live; matrix sign-magnitude, entries add, byte 0 = B | `HW 2026-11-06 main` |
| IQ hue matrix, chroma clip, chroma offset dead (also with the bit-6 enable) | `HW 2026-11-06 main` |
| A recipe-curve change reaches a JPEG (16-chunk upload, selector cache invalidated) | `HW` (earlier B&W looks) |
| An edit of a stock tone table reaches a JPEG | not shown |
| Selective colour pass on the master UV plane | `HW 2026-11-06 main` |
| Koma GRAIN and VIGNETTE | `EMU` |
| Luma weights form Y; B&W filters work | not shown (`INFERRED`; the one-hue test scene cannot measure filter colour) |
| WB gains shift B&W tones | not tested |
| LCD preview follows a modified table | `STATIC` (route), not observed |
| Recipes in live view | stock: not shown (`STATIC`); Koma `preview`: `EMU` only, no camera result |
| Contrast recorded in metadata | not found |

## Open questions

- Tone-table input encoding (linear or gamma, black offset), and order of LUT, enhancer and WB inside the hardware block.
- Whether the 256 → 64 pin matters to the hardware.
- Whether `0x1201800c` forms Y (B&W filters) and whether WB gains act before Y.
- Matrix: other scenes; the order of a chroma-gain change and a matrix delta; chroma-gain byte roles; meaning of `SxUV` and the unity gains.
- Whether the IQ stage has an enable anywhere other than `0x12018100` bit 6, or in an undriven CC2 register.
- Whether the monitor stream reads CC1 or CC2, and the luma weights.
- What `0x23b085` states 1, 3, 4 and selector mode 7 mean to a user.
- Whether `/IROM/PICTDATA.RDD` exists on a given body, and the visual effect of CalcType 1.
- `0x1f4c88` is also read elsewhere as `tCamExtAf` working words; the TE-table reading is the one the bytes and the menu tints support.
- Whether contrast is stored in a constant MakerNote or Camera Info byte, and whether MakerNote `0x1003` is a firmware marker.
- The standard DHT set lies at offset `0x15c83d` of the 2.40 payload; whether that is a payload or file offset needs a one-line check.
- Time per shot of a full-frame pixel pass (core clock unknown).

## Evidence

- [../../analysis/tone-curves/](../../analysis/tone-curves/) (table CSVs, `layout-report.json`)
- [../../analysis/sibling-tone-tables/README.md](../../analysis/sibling-tone-tables/README.md)
- [../../analysis/tone-mode-mapping/README.md](../../analysis/tone-mode-mapping/README.md)
- [../../analysis/tone-selector-rea/README.md](../../analysis/tone-selector-rea/README.md)
- [../../analysis/tone-detail-ui/README.md](../../analysis/tone-detail-ui/README.md)
- [../../analysis/isp-capabilities/README.md](../../analysis/isp-capabilities/README.md)
- [../../analysis/bw-pipeline/filt.json](../../analysis/bw-pipeline/filt.json) (enhancer register values by ISO and sharpness, `EMU`)
- [../../archive/research/bw-pipeline.md](../../archive/research/bw-pipeline.md) (stage table, enhancer table, WB, TE table, colour bracket)
- [../../archive/research/card-evidence.md](../../archive/research/card-evidence.md) (rate control, MakerNote layout, Camera Info fields)
- [../../analysis/colour-research/probe1/README.md](../../analysis/colour-research/probe1/README.md), [probe2](../../analysis/colour-research/probe2/README.md), [probe3](../../analysis/colour-research/probe3/README.md) (chroma gain, matrix, coring, dead IQ stage; `HW 2026-11-06 main`)
- [../../analysis/colour-research/selcolour1/README.md](../../analysis/colour-research/selcolour1/README.md) (selective colour on hardware)
- [../../analysis/colour-research/test02/README.md](../../analysis/colour-research/test02/README.md) (recipe frames from an earlier build)
- [../../analysis/colour-research/liveview/README.md](../../analysis/colour-research/liveview/README.md) (monitor set-up and why recipes do not show)
- [../img/looks-real.jpg](../img/looks-real.jpg), [../img/select-real.jpg](../img/select-real.jpg)
- [../../analysis/colour-recipes/README.md](../../analysis/colour-recipes/README.md), [../../analysis/grd4-firmware/README.md](../../analysis/grd4-firmware/README.md), [../../analysis/koma-0.1/review-0.2.1.md](../../analysis/koma-0.1/review-0.2.1.md)
- [../../analysis/koma-0.1/bwrecipes.md](../../analysis/koma-0.1/bwrecipes.md), [../../analysis/koma-0.1/selcolour.md](../../analysis/koma-0.1/selcolour.md), [../../analysis/koma-0.1/colourprobe.md](../../analysis/koma-0.1/colourprobe.md)
- [../../analysis/koma-0.3-preview/](../../analysis/koma-0.3-preview/) (`preview` emulator runs)
- [../../analysis/pixel-access/README.md](../../analysis/pixel-access/README.md)
- [../../analysis/liveview/frames/README.md](../../analysis/liveview/frames/README.md) (live-view ring layout)
- [../../analysis/jpeg-quality/README.md](../../analysis/jpeg-quality/README.md)
- [../../analysis/spare-usb/README.md](../../analysis/spare-usb/README.md) (JPEG structure 1.08 against 2.40; 1.08 has no B&W contrast)
- [../../analysis/card-metadata/](../../analysis/card-metadata/)
- [../../analysis/jpeg-completion/README.md](../../analysis/jpeg-completion/README.md)
- [../../tools/koma/features/bwrecipes.py](../../tools/koma/features/bwrecipes.py), [selcolour.py](../../tools/koma/features/selcolour.py), [fx.py](../../tools/koma/features/fx.py), [preview.py](../../tools/koma/features/preview.py), [colourprobe.py](../../tools/koma/features/colourprobe.py)
