# Settings, adjustment store and image pipeline

Addresses are firmware 2.40 runtime addresses. `0x0801xxxx` addresses are the SDRAM copy of the factory adjustment store (the store offset is the low 16 bits). `cp` is the camera parameter block at `0x23b068`. See also [firmware.md](firmware.md), [usb.md](usb.md), [writing-features.md](writing-features.md), [addresses.md](addresses.md).

## Stored state layers

- RAM only (reset at power-off): INTERVAL, CONT. MODE, AUTO BRACKET, SELF-TIMER, MACRO (`0x1bef78`), IMAGE WITH SOUND.
- User settings block `0x1b000` (4 KB): settings, ADJ slots, My Settings, counters, error log, Koma area. Mostly survives an official update.
- Factory adjustment store (flash block `0x3d`): calibration, register init values, tone-selector bytes. `qrflect3` leaves it alone; `qrflect7` overwrites the words it names.
- `/IROM` files survive an update.

## User settings block

Startup `0x1e7ac` loads the block (`0x4947c(0x1b000, 0x1000)`); `0x3d4a4` resets every setting if the magic or checksum fails. Power-off `0x32b8c` saves it (`0x4949c(0x1b000, 0x1000)`).

| Item | Value |
|---|---|
| Magic | `MG1H` (`0x4d473148`) at `0x1b000` |
| Checksum | Big-endian u32 at `0x1bffc` (all multi-byte fields are big-endian): sum of big-endian u16 words over block offsets `0x000..0xffb` (`0x3d9b8`) |

### Field map

Stored values often differ from menu order. Commit tables and validators (`0x6dc18..0x6fa90`) give the mapping.

| Address | Setting | Values |
|---|---|---|
| `0x1b030` | IMG SET | 0 HARD, 1 NORMAL, 2 SOFT, 3 B&W, 6 B&W(TE), 4 SETTING1, 5 SETTING2 |
| `0x1b031`, `0x1b034` | Contrast, SETTING1 / SETTING2 | 1..5, default 3 |
| `0x1b197`, `0x1b198` | Contrast, sharpness, B&W | 1..5, default 3 |
| `0x1b1a7..0x1b1aa` | B&W(TE) contrast, sharpness, tone (1..6), density (1..5) | |
| `0x1b053` | EXPOSURE COMP. | odd 1..25 = -2.0..+2.0 EV in 1/3 steps, default 13 |
| `0x1b054` | WHITE BALANCE | 0 AUTO, 1 OUTDOORS, 2 CLOUDY, 3 INCAND., 4 FLUORES., 5 MANUAL, 7 DETAIL |
| `0x1b074`, `0x1b076` | Manual WB R, B gains | clamped `0x10..0x310` |
| `0x1b059` | ISO | 0 AUTO, 1 ISO 64, 2 ISO 100, 4 ISO 200, 6 ISO 400, 7 ISO 800, 8 ISO 1600 |
| `0x1b0f6..0x1b0fb` | JPEG rate-control bias | `0x3d..0x43`, default `0x40` |
| `0x1b0c4`, `0x1b05d`, `0x1b191`, `0x1b192` | ADJ-button slots 1..4 | item codes (OFF 0, QUALITY 1, FOCUS 2, IMG SET 3, METERING 4, ISO 5, ...) |

### Markers

Markers `0x1b18f`, `0x1b1a1`, `0x1b1a2`, `0x1b1a3` cover `0x1b18c..0x1b1b2`; a group whose marker byte is not `0x55` is reset to defaults. A merge between bodies must exclude the markers.

## Settings import and export (PARAM.DAT)

Stock 2.40 has no settings-file code. Koma's `settingsio` feature runs once per boot after the stock load and moves the 4 KB block to and from the card root (`/ATA1`).

| File | Written by | Content |
|---|---|---|
| `PARAM.CUR` | Every boot, after the import step | The 4,096 bytes at `0x1b000` as in RAM |
| `PARAM.DAT` | The host (`komaset`) | A 4,096-byte block to import once at the next boot |
| `PARAM.OK` / `PARAM.BAD` | After an import attempt | `OK`, or a reason (`BAD MAGIC`, `BAD CHECKSUM`, `BAD KOMA VERSION`, `BAD REOPEN`, `BAD READ`) |
| `PARAM.SAV` | SAVE TO CARD = YES | The current block, checksum recomputed |

Import:
1. `PARAM.DAT` must be exactly 4,096 bytes. A first pass checks magic `MG1H`, the checksum and, if `KOMA` is at block offset `0x8b0`, that the Koma version at `0x8b4` is 1.
2. A second pass copies the file over `0x1b000..0x1bffb`, skipping magic, checksum, markers, counters, calibration-derived defaults, manual WB gains, focus positions, rate-control bias, error log, the Koma headers and the SAVE TO CARD byte.
3. A file without a `KOMA` header copies only block offsets `0x000..0x7ff`, so a stock body's block cannot wipe the Koma area.
4. Koma row bytes (`0x1b8b5..0x1b8cf`) at or above their option count are reset to defaults.

The file is then rewritten as `DONE` and removed. On a write-protected card it imports at every boot.

`PARAM.CUR` carries the checksum as it sits in RAM, which is normally stale because Koma's boot writes land after the last checksum update. Recompute it on the host before reusing a `PARAM.CUR` as a `PARAM.DAT`. Persistence is the stock power-off save; Koma calls no flash routine.

## Koma area

Koma keeps its state at `0x1b800..0x1bffb`, past everything stock 2.40 references, so it rides on the stock power-off save. Init writes every row default when the magic is missing and repairs out-of-range row bytes every boot.

| Byte | Row | Values (default first) |
|---|---|---|
| `0x1b8b0..0x1b8b3` | Magic `KOMA` | |
| `0x1b8b4` | Version | 1 |
| `0x1b8b5..0x1b8ba` | SNAP DIST, FULL SNAP, AUTO-ISO MAX, SLOW SHUTTER, HIGHLIGHT ALRT, LONG EXP LIMIT | Small enums; SNAP DIST default 3, the rest 0 |
| `0x1b8bd` | SAVE TO CARD (action) | 0 NO, 1 YES, 2 SAVED, 3 FAILED. Reset every boot, never imported |
| `0x1b8be` | Stall-fix flags | High nibble `0xA` makes the low nibble the fix mask (bits in [usb.md](usb.md)). Any other value means mask `0xd` |
| `0x1b8c0` | Active recipe id | 0 = none |
| `0x1b8c1..0x1b8c9` | Edit-recipe p0..p8 | CURVE, CONTRAST, KEY, SHADOW, HIGHLIGHT, p5, p6, p7, SHARPNESS. p5..p7 are FILTER EFF., TONING, TONE LEVEL (B&W), SATURATION, WARMTH, TINT (colour) or COLOUR, RANGE, SATURATION (SELECT) |
| `0x1b8cc`, `0x1b8cd` | GRAIN, VIGNETTE | OFF, 1, 2, 3 |

Other rows: MENU LAYOUT `0x1b8bb`, KOMA FEATURES `0x1b8bc`, PEAKING `0x1b8bf`, and the actions SAVE TO CUSTOM `0x1b8ca` and RESET `0x1b8cb`.

The `KSIO` header at `0x1b8d0` is never copied by import. It holds the magic `KSIO`, a 7-byte firmware tag at `0x1b8d5` (`Rev024M` Koma 1, `240K2` Koma 2) and the last import status at `0x1b8dc` (0 none, 1 OK, 2 BAD, 3 OK with a different firmware tag).

The `RCP6` recipe store starts at `0x1b900`:

| Offset | Field |
|---|---|
| +0 / +4 | Magic `RCP6` / version 6 |
| +5 / +6 | Owner id of the edit rows / last applied id |
| +7 | Record count N = 20 (10 B&W looks, SELECT, 6 colour looks, 3 CUSTOM). Another N is rejected (the FX table follows the records) |
| +8 | Active type (selects the p5..p7 variants) |
| +9 / +10 | SELECT colour (1..6, 0 = none) / SELECT range |
| +11 / +12 / +13 | GRAIN / VIGNETTE for the next shot / owner id of those rows |
| +0x10 + 12·n | Record n: bytes 0..8 = p0..p8; byte 9 = kind (bits 0..1 type: 0 B&W, 1 colour, 2 SELECT; bits 4..6 colour look 0..5); byte 10 = `0xa5` valid; byte 11 = XOR of bytes 0..9 with `0x5a` |
| `0x1ba00..0x1ba13` | FX table, one byte per recipe: `0x50 \| grain \| vignette << 2`; other values read OFF |

A record applies only if valid, its XOR matches and every parameter is below its type's limit. A rewrite writes records and FX table first and the header last.

## Factory adjustment store

### Layout and validation

Flash block `0x3d` (64 KB, `0x2000` bytes programmed). Boot copies it to `0x08010000` (`0x279ec`, `0x63e30`). If byte 0 is not `0xAA` or `0x55`, or the store sum fails, `0x27bc0` copies the ROM defaults from `0x175380` and the unit-specific words are lost. The store sum is the word at store offset `0x1ffc` over offsets `0..0x1b00`.

Boot reloads the store from flash (`0x1e444`, `0x27bdc`), so a RAM-only change lasts one power-on. A card `SCRIPT.MG1` with `adjcom X12C0 / V... / W...` is the only RAM-only route that reaches a shot, and must be present at every power-on. Token `UEE52` (`0x27d0c`) dumps the flash store to `/ATA1/ADJROM.DAT`.

### Commit chain

1. `X12C0` (twice) selects region base `0x08010000` (`0xd1e70`). `UEEF1` reloads the store from flash into RAM (`0x27c28`).
2. Each `V<off>` sets an offset. Each `W<val>` writes a big-endian halfword at `0x08010000 + off` (`0xd1c30`, `0xd1d4c`).
3. `UEE00` (`0x27c5c`) requires byte 0 = `0xAA` or `0x55`, recomputes the sum, then `0x63d9e` erases block `0x3d`, programs `0x2000` bytes and re-sums.

`qrflect7` is a type-5 update file: a 512-byte header, then 4,486 CRLF lines (2,241 `V`/`W` pairs over `0x0002..0x1cd4`). It is read-modify-write, so unnamed words survive.

Write hazards (none tested deliberately on a camera):

| Hazard | Detail |
|---|---|
| Torn commit | A bad sum or magic boots on ROM defaults and loses about 857 unit words. Re-running `qrflect7` cannot repair it (byte 0 stays `0xFF`, `UEE00` refuses) |
| Bad value, valid sum | `0x08010431` = `0xFF` hangs `MainCtrl` before the update prompt |
| Guard | A non-zero store `0xcc` that differs from RAM `0xae` (`0xd0e86`) powers off after `qrflect3` has already flashed |
| Ordering | After a failed file the driver (`0xd6dec`) goes on to the next `QRFLECTn` |

### Known words

| Store address | Meaning | Value |
|---|---|---|
| `0x08010002` | Adjustment version (`ADJ:` on the version screen) | `0240` |
| `0x08010431` | Loop bound at `0x3ec14`; `0xFF` never terminates (never write) | `0x0e` |
| `0x08010096..ab`, `8a..8d` | JPEG size targets (bpp x10) and rate-control limits | 2.8 bpp FINE |

Enhancer tables: `0x08010a04..af7`. Colour presets, chroma-gain table, matrix and UV slots: `0x08011304..94`.

### Bytes that feed the image pipeline

| Offset | Meaning |
|---|---|
| `+0x13f0` c5, `+0x13f1` c4, `+0x13f2` c1, `+0x13f3` c2, `+0x13f7` c3 | Tone-curve selector per contrast level |
| `+0x13f4`, `+0x13f5` | Selector when special state `0x23b085` is 1, or 3 / 4 |
| `+0x13f6` | Selector for contrast outside 1..5 (`0x05` = no upload) |

## Image pipeline

A shot runs from the sensor through white balance, sharpening, one shared tone curve, a colour stage and a mono register, then JPEG encode. Ricoh ships five tone curves, one per contrast level, shared by all modes. Koma changes curve, chroma, matrix and pixels through the hooks at the end of this page.

### Stages

| Stage | Routine |
|---|---|
| Sensor to raw, develop pass 1 | `0x96350` |
| WB gains `cp+0x268/26a/26c` to `0x12018110/114`, R and B scaled x16/25 (64 = 1.0, inferred) | `0x96242` |
| Tone LUT, 4096 entries, CC2 per shot (CC1 when `cp+0x40` = 1) | `0x90278` to `0x16d31c` |
| Chroma gain, matrix, coring, unity gains | `0x922bc`, `0x92598`, `0x928d8` |
| Mono / tint (B&W and TE only) | `0x8ff0e` writing `0x12018038` |
| Master YUV 4:2:2; chroma NR | `0x11703c` |
| JPEG rate control and encode | `0x10f984` |

### Tone tables

Five tables of `0x2000` bytes sit back to back from `0x193b30`. Each holds 4096 big-endian u16 entries (12-bit input, output 0..1023), non-decreasing. Entries 0..257 are identical in all five (64 at input 256).

| Contrast | Selector | Table | Reached by |
|---|---|---|---|
| 1 | `0x1e` | `0x193b30` | |
| 2 | `0x1f` | `0x195b30` | SOFT |
| 3 | `0x20` | `0x19bb30` | NORMAL; `0x23b085` states 1, 3, 4 |
| 4 | `0x21` | `0x197b30` | HARD |
| 5 | `0x22` | `0x199b30` | contrast 5 in modes 3, 4, 5, 6 |

Selector `0x0e` maps to `0x19db30`, a descending factory curve that normal shooting never selects. Other values upload nothing. `0x92cc8` reads the contrast: HARD from store `0x08010c68`, SOFT from `0x08010c6c`, NORMAL literal 3, B&W `0x1b197`, B&W(TE) `0x1b1a7`, SETTING1 / SETTING2 `0x1b031` / `0x1b034`. A single-table edit is global (all modes and the LCD preview). One LUT serves all channels.

### Upload routes

`0x8ffc8(sel, 0)` maps a selector to a table pointer, calls the CC1 transfer `0x166334` (port `0x12014014`, control `0x12014010`, busy `0x12014064` bit 0), then the CC2 transfer `0x16d31c(off, n, buf)` (port `0x1201811c`, control `0x12018118`, busy bit 16, masks to 10 bits).

- Monitor start (`0x985a0` to `0x92050`) loads CC1 and CC2 for the LCD preview and writes the luma weights.
- A shot goes `0x9733a`, `0x90278`, `0x90408`, `0x16d31c` with cached selector `0x1f4db3`. `0x900f4` loads CC1 for stills only when `cp+0x40` = 1.
- `0x92050` overwrites the LUTs without updating the cached selector bytes (`0x1f4db3` CC2, `0x1f4db0` CC1). Code that uploads its own curve must set the cache byte to `0xff`.

### Mono and toning

`0x8ff0e(mode)` writes `0x12018038`: 0 for colour, `0x1000000` (bit 24) for B&W, `0x1000000 | cfr<<12 | cfb` for TE, with `cfr` (bits 21:12) and `cfb` (bits 9:0) signed 10-bit. Chroma is replaced by constants.

The TE tint entry (Cb, Cr) is at `0x1f4c88 + 40*tone + 8*density`, with tone `0x1b1a9` (1..6) and density `0x1b1aa` (1..5). At density 3: tone 6 sepia (-24, +16, the default), 1 red (0, +16), 2 green (0, -16), 3 blue (+20, -8), 4 purple (+12, +4), 5 strong green (0, -80). The register allows +/-511 while menu tints stay within +/-44.

### White balance

At ISO 1600 all three gains double (store `0x08010312`). WB is programmed in B&W with no mode test.

### Colour matrix and chroma

Every shot, `0x96350` calls `0x92598(1)` and `0x922bc(1)`, then `0x8ff0e` at `0x96436`. Pass 2 (`0xafd20`) rewrites WB and mono again.

| Register | Setter | Format |
|---|---|---|
| `0x12018004` chroma gain | `0x922bc` | 4 x u8, `0x80` = 1.0 (inferred). 0 gives a grey frame. Values over 255 truncate |
| `0x12018104` / `108` / `10c` MatR / MatG / MatB | `0x1725b0/c8/e0` | One row per output channel. Stock slots `0x08011380..94` are zero in every body |
| `0x120180d8` `pUVoffset` | `0x17231c`, mask `0x3f3f` | Chroma dead zone, word = (Cr << 8) \| Cb. Low 6-bit field n maps Cb to sign(Cb)*max(\|Cb\| - n, 0); bits 8..13 do the same for Cr |
| `0x1201800c` luma weights | `0x171958` | `40 08 99 3a`. Set at monitor start only |

Each matrix byte codes one coefficient as bit 7 sign, bit 6 ignored and bits 5..0 magnitude m, giving m/64. Rows are linear and add. This coding was fitted on frames of one scene and may be off by up to x1.3. Input by byte: b3 = R (all rows), b2 = G, b1 = G, b0 = B. The matrix moves only Cb and Cr.

Registers `0x12018028` (IQ hue matrix), `0x1201802c` (chroma clip) and `0x12018030` (chroma offset) did nothing when written. Hue-selective looks need the matrix or a pixel pass.

Stock live view reloads preset `0x08011304` on every monitor start and after each shot.

### Sharpening and noise reduction

`0x91e78(1)` calls `0x912bc(1, sharpness, ISO offset)`, which reads the tables at `0x08010a04..af7`. Chroma NR (`0x116dbc`) works on UV only, in colour mode at ISO 400 or above.

### Pixel access

| Item | Value |
|---|---|
| Master planes | Y at `0x8605f00`, UV at `0x8f40e00` (8 MP), stride `0xd00` = 3328. Plain CPU stores, no cache maintenance |
| UV layout | 4:2:2, interleaved Cb, Cr byte pairs (one pair per 2 pixels), same stride as Y |

### JPEG output

| Item | Value |
|---|---|
| Quantisation | Annex K luma table `0x18c330` and chroma table `0x18c370`: entry = `((scale*q >> 9) + 1) >> 1`, clamped 1..255. Scale is the low 16 bits of Camera Info tag 2; floor `0x14` |
| Target size | `0x10f1dc` takes min, target, max from `0x10f294`: `W*H*bpp/10/8*pct/100`, bpp from store `0x96..0xaa` (FINE 3264 is 2.8 bpp at 90 / 105 / 120%) |

MakerNote tag `0x1001` byte 40 holds IMG SET (`0x1b030`). Contrast is recorded nowhere.

## Hook points for tone and colour

| Hook | Runs | Overrides | Status |
|---|---|---|---|
| `0x96436` (`bl 0x8ff0e` in pass 1) | After `0x92598` / `0x922bc`, before the enhancer | Chroma gain, matrix, `pUVoffset`, mono | Hardware |
| `0xafd3e` (pass 2) | After pass 2's WB and mono rewrite | Pass-2 WB and mono | Hardware |
| `0x117382` (`bl 0x1173e8` at the tail of `0x11703c`) | After master resize and date imprint, before rate control | Pixels of the finished planes | Hardware |

- A per-shot curve upload at `0x96436` sends 16 chunks of 256 entries through `0x16d31c(offset, 256, buf)`, then sets `0x1f4db3` to `0xff`. Pass 2 uploads no LUT.
- `0x9b7e0` (called at `0x964b8` on some WB branches, after `0x96436`) resets `cp+0x268..` to 100 and undoes a WB change made at `0x96436`.
- A chroma pass should skip B&W and TE develops (`cp+0x22` not 0).
- Koma's selective colour classes each Cb/Cr pair through a 32x32 table indexed by `(Cb >> 3) << 5 | (Cr >> 3)`.
