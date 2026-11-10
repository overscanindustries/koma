# 03. Firmware image and update format

Ricoh ships GR DIGITAL firmware as "reflector" containers: `qrflect3` (type 2, the 2,201,960-byte application loaded at `0x1c000`) and `qrflect7` (type 5, a text script of adjustment-engine commands), each with a 512-byte header, an XOR-`0x80` magic and a weighted additive checksum. The on-camera updater scans `/ATA1/QRFLECT0..9`, validates every file before the first erase, erases main-info block `0x3c`, programs the body, and writes the info block last. On hardware the official pair took the spare body from 1.08 to 2.40; modified same-size `QRFLECT3` images (generation 1 = the generation 1 image, then generation 2 Koma firmware, build 2b) were accepted by the stock updater on both bodies; and the official 2.40 `QRFLECT3` was restored over generation 1 Koma firmware once, on the spare.

Tags: `HW`, `EMU`, `STATIC`, `INFERRED`, defined in [features](evidence-ledger.md). Runtime address = `qrflect3` file offset + `0x1be00`. Related: [01 memory map](01-hardware-and-memory-map.md), [02 boot chain](02-boot-chain-and-monitor.md), [04 application](04-application-architecture.md), [05 settings and store](05-settings-and-adjustment-store.md), [09 recovery](09-recovery-and-failure-modes.md), [11 modification playbook](11-modification-playbook.md), [12 version history](12-version-history.md), [address index](address-index.md), [install](../install.md).

## Key addresses

| Address | Meaning | Tag |
|---|---|---|
| `0x1eb22` (in `0x1e7ac`) | Update gate: keys, or invalid main info | `STATIC` |
| `0x4fbcc(2,0)` -> `0x32540(1)` | Key test: UP (`0xb`) + TELE (`0xf`) + dial value 1/10/9 | `STATIC` |
| `0x273e4` -> `0x62c42` | Main-info validity: magic, `+0x1f0` tail, type 2/3; no checksum | `STATIC` |
| `0x5d18c`, `0x5d330` | Update orchestration; screen and wait helper | `STATIC` |
| `0x278d4` | Scanner of `/ATA1/QRFLECT0..9` (name template `0x177968`) | `STATIC` |
| `0x27400` | Main header reader (returns type) | `EMU` |
| `0x276d8`, `0x277ec`, `0x27688`, `0x27738`, `0x27824` | Per-type checks for types 2, 5, 1, 3, 6 | `STATIC` |
| `0x275cc` | Streaming weighted checksum (the updater's own check); divide helper `0x157c84` | `STATIC` |
| `0x27648` | Target-version range gate | `STATIC` |
| `0x62bbc` | Magic compare (literal `0x177894`) | `STATIC` |
| `0x62cf0` | Weighted checksum routine | `EMU` |
| `0x62d76`, `0x639c0` | Header validator; type admission (1, 2, 3, 6) | `STATIC` |
| `0xd6dec` | Updater loop over accepted files (does not stop at a failed file) | `STATIC` |
| `0xd6c58` | Main-image flasher (types 1/2/3/6; admits 7 when called directly) | `STATIC`, `EMU` |
| `0xd6a62` | Type-5 script driver | `STATIC` |
| `0x63c68`, `0x63b88`, `0x63c80` | Erase main info, program main, program info | `STATIC` |
| `0x63d9e`, `0x27c5c`, `0x27c28` | Store erase/program, `UEE00` commit, `UEEF1` reload | `STATIC` |
| `0x63598`, `0x636dc`, `0x6339c`, `0x63364` | Erase, program, take spare, map through replacement table | `STATIC` |
| `0x64838`; `0x649a8`/`0x64b88`; `0x647c8`/`0x64764`; `0x64930`/`0x64a74` | OneNAND driver: program setup; erase; lock/unlock; DataRAM/spare transfer | `STATIC` |
| `0x64b1c`; `0x64d24`, `0x64f04`; `0x64d80` | Bad-block marker check; unlock-operate-relock block operations; raw physical read | `STATIC` |
| `0x638e8`, `0x63886` | Read-back compare while streaming | `STATIC` |
| `0x32a22` / `0x2b9e92` | Battery check / battery state | `STATIC` |
| `0x26e04`, `0x26f38` | `QUICKVER.UP` flasher | `STATIC` |
| `0x32b74`, `0x32b8c`, `0x4fcaa` | End of update: power-off request, settings save, power control | `STATIC` |
| `0x5c95c`, `0x1f09c` | Version screen builder; version datum | `STATIC` |
| `0x112578` | MakerNote `Rev%4.4x` format string (file `0xf6778`); rewritten by the generation 1 image and generation 2 | `STATIC`, `HW` |
| `0x199b30` | Contrast-5 tone table (file `0x17dd30`) | `STATIC` |
| `0x1c000`, `0x219968`, `0x2f0000` | Application load address; payload size; main-body capacity (47 blocks) | `STATIC` |

## The official package

| Item | Value | Tag |
|---|---|---|
| Package | One cumulative "V2.40" package from Ricoh's support pages (registered 2005-11-16, updated 2007-11-22), history starting at V1.08 | `STATIC` |
| `gr-240.exe` | 1,278,644 B, SHA-256 `e9257cbd89cd3f07163ef8700a9143121ecb69ecee94c1c935c7b7486fafa555`; self-extractor, extractable with bsdtar | `STATIC` |
| `qrflect3` | 2,202,472 B (`0x219b68`), SHA-256 `de00e03d563fd1bdd0a2a925761b1a029993425049841119e3e83eef990c783b` | `STATIC` |
| `qrflect7` | 31,914 B (`0x7caa`), SHA-256 `1dd1a9d9cb2d5a8386b059722c01ac025f10dbe5f507c7cdb9f5d486f9c21c3f` | `STATIC` |
| Payload (header stripped, `firmware/derived/grd240-armbe.bin`) | 2,201,960 B, SHA-256 `60b0a387126347a37f7d2217bb0b36b5a70fc33ce28959a8244b07757b9887a4` | `STATIC` |
| `FirmUpdate.exe` | 143,360 B, SHA-256 `9612a4a614e63791be4f046acfa89a0b2183da6d441c6a77d8fe144671656074`. Windows tool that stages files on the card over USB; it never flashes. Its "Already updated" check (camera `-VER` against file version) exists only here | `STATIC` |
| `RDT3CM32.dll`, `RDTCCM32.dll`, `RDTPCM32.dll`, `rdstiapl.dll` | 110,592 / 73,728 / 53,248 / 57,344 B; communication DLLs of the PC tool (hashes in the manifest) | `STATIC` |
| `SUPPORT.INI` | 45 B: `[SUPPORT] VENDOR=RICOH PRODUCT=GR DIGITAL` | `STATIC` |

The package holds only type-2 and type-5 containers; there is no public Monitor (type 1) file and the 2.40 application embeds no Monitor code. `STATIC`

Sibling packages (GR DIGITAL II/III, GX100, GX200, R7, R8, R10) use the same container: per-model file prefixes (`ylaunch3`, `slaunch3`, `claunch3`, ...), per-family magics (`R_MG2_`, `R_GC1_`, `R_CL2_`, ...), the same XOR `0x80`, the same `R_xxx_UPDATED!!!` tail pattern, the same checksum weights, and only types 2, 5 and 6. `STATIC`. Their code is big-endian M32R, not ARM. `INFERRED` (strong: register-dump strings and instruction patterns)

## Container header

512 bytes, big-endian.

| Offset | Size | Field | `qrflect3` | `qrflect7` | Tag |
|---|---|---|---|---|---|
| `+0x00` | 16 | Magic `"R_MG1_REFLECTOR\0"`, each byte XOR `0x80` (`d2 df cd c7 b1 df d2 c5 c6 cc c5 c3 d4 cf d2 80`) | same | same | `STATIC` |
| `+0x10` | 1 | Type | `02` | `05` | `STATIC` |
| `+0x11` | 1 | Format (type 2 requires 1, type 5 requires 3) | `01` | `03` | `STATIC` |
| `+0x12` | 2 | Version, BCD | `0240` | `0001` | `STATIC` |
| `+0x14`, `+0x16` | 2+2 | Target-version bounds (older/newer); zero = no restriction | `0000 0000` | `0000 0000` | `STATIC` |
| `+0x18` | 4 | Date, BCD `yyyymmdd` | `20071024` | `20071024` | `STATIC` |
| `+0x1c` | 4 | Time bytes | `00 18 56 14` | `00 18 40 01` | `STATIC`, encoding `INFERRED` |
| `+0x20` | 4 | Load address | `0x0001c000` | `0` | `STATIC` |
| `+0x24` | 4 | Payload length | `0x00219968` | `0x7aaa` (31,402) | `STATIC` |
| `+0x28` | 4 | Entry address | `0x0001c000` | `0` | `STATIC` |
| `+0x2c` | 4 | Total file size (header + payload), compared with the fstat size | `0x00219b68` | `0x7caa` | `STATIC` |
| `+0x30` | 4 | Weighted checksum | `0x346f08ed` | `0x0065c71d` | `STATIC` |
| `+0x34..+0x1ef` | | `0xff` filler | | | `STATIC` |
| `+0x1f0` | 16 | Tail `"R_MG1_UPDATED!!!"`, XOR `0x80` | present | present | `STATIC` |
| `+0x200` | | Payload | | | |

- `+0x2c` is the file size, not a destination base: a checksum run that treated it as the base failed the official control. Older analysis notes call it a destination base. `STATIC`, `EMU`
- The scanner does not check the `+0x1f0` tail, but the boot-time validity test (`0x62c42`) does. An image with a wrong tail would flash, then read back as invalid. `STATIC`
- The digit in the file name is not the type: `QRFLECT3` holds type 2 and `QRFLECT7` type 5. The scanner opens `QRFLECT0..9` by name and dispatches on `+0x10`. `STATIC`
- The Monitor's own info record is type 1, format 1, version `0100`, date `20050806`, load and entry 0, payload 63,660 B, checksum `0x0196d9b0`. `HW` (capture)

### Header types

| Type | Destination window (`0x62d76`) | Role | Accepted by | Tag |
|---|---|---|---|---|
| 1 | `[0, 0xfc00)` | Monitor | `0x639c0`; a second reflector driver (`0x26f38` family) with `programMonitor`/`verifyMonitor` strings. No file exists | `STATIC` |
| 2 | `[0x1c000, 0x406800)` | Application (`qrflect3`) | SD scanner, `0x639c0`, Monitor RAM loader, Monitor flash updater | `STATIC`, `HW` |
| 3 | as type 2 | Second application type; no file known | Monitor RAM loader; boot validity test | `STATIC` |
| 5 | none (dest 0) | Adjustment text script (`qrflect7`) | Scanner check `0x277ec`, driver `0xd6a62` | `STATIC`, `HW` |
| 6 | `[0, 0x1000000)` | Extended data (glyphs in sibling models); no GR file | `0x639c0` | `STATIC`, role `INFERRED` |
| 7 | as type 2 | RAM-loader carrier, not stock-updatable | Monitor RAM loader; stock scan, dispatcher and `0x639c0` reject it; direct `0xd6c58` admits it | `EMU` |

Types 0, 4, 7 and above, and short reads, count as present but not accepted, and reject the whole card. `STATIC`

## Checksum

```
checksum = sum over i of payload[i] * (2 + (load_address + i) % 6)   mod 2^32
```

Over the payload only, stored big-endian at `+0x30`. The weight cycles 2..7 with phase set by the load address (phase 0 for `qrflect7`). It reproduces both vendor checksums in Python and through the firmware's own `0x62cf0`. Sibling packages verify with phase 0 (file-offset based), because their segments have no single load address. The Monitor's `0x19ac` computes the same sum and returns `0x0196d9b0` on the captured Monitor. It is an integrity check, not authentication. `STATIC`, `EMU`

| Place | Address | Role | Tag |
|---|---|---|---|
| Checksum routine | `0x62cf0` | Sum over a buffer; one caller, inside `0x62d76` | `STATIC` |
| Header validator | `0x62d76` (via `0x639c0`) | Magic, `+0x11 == 1`, `+0x2c`, destination window, checksum. `0x639c0` has no direct caller; it never ran in a simulated update | `STATIC`, `EMU` |
| Streaming check | `0x275cc` (from `0x276d8`, from `0x278d4`) | Accumulates `((base+i)%6+2)*byte` while reading; base `+0x20`, length `+0x24` | `STATIC` |
| Monitor RAM loader | `0x19ac` via `0x2138`/`0x1cb0` | Size, magic, format, range, checksum (after the copy) | `EMU` |
| Monitor flash updater | `0x4e54` -> `0x3e7c` | Skipped: `0x50ec` passes 1, so a stale checksum is programmed | `EMU` |

The stock updater is gated by the streaming check `0x275cc`. A flipped checksum bit gives screens `0x18`, `0x1c`, `0x1a` and then a normal boot with no flash write. `EMU`

**No boot-time body checksum.** Neither the application nor the Monitor's normal load path recomputes the main payload checksum. `STATIC`, `EMU`

### Tool: `tools/grd_firmware.py`

Commands `inspect`, `unpack`, `pack`, `compare`. `parse()` checks the magic, that `+0x2c` equals the file length and `+0x24` the payload length, and the checksum. `pack(template, payload)` accepts only the official `qrflect3` (SHA-256 pinned, type 2) as template, refuses a payload of different length, rewrites only the 4 checksum bytes at `+0x30`, and re-parses its output. Outputs are opened with `xb` (never overwrite). The generation 1 image and generation 2 images were packed this way. `STATIC`

## `qrflect7` adjustment script

4,486 CRLF lines:

```
X12C0
X12C0
UEEF1
V0002   W0240       (store version word = 0x0240)
V000C   W2307
V000E   W0907
V0018   W0001
V001A   W0106
...                 (2,241 V/W pairs, offsets 0x0002..0x1cd4)
V1CD2   W0403
V1CD4   W0303
UEE00
```

| Token | Meaning | Tag |
|---|---|---|
| `X<4 hex>` | Select region; `X12C0` = `0x08010000` (store in RAM) | `STATIC` |
| `UEEF1` | Reload the store from flash to RAM (`0x27c28`) | `STATIC` |
| `V<offset>` | Set offset (also a read primitive) | `STATIC` |
| `W<value>` | Write the 16-bit value at the `V` offset | `STATIC` |
| `UEE00` | Commit (`0x27c5c`): requires byte 0 = `0xAA` or `0x55`, recomputes the sum to `0x08011ffc`, then `0x63d9e` erases block `0x3d` and programs `0x2000` bytes | `STATIC` |

| Fact | Tag |
|---|---|
| Engine `0x54164` dispatches 5-character tokens (upper-cased). Sibling scripts use the same format | `STATIC` |
| Driver `0xd6a62` brackets the file with `0xd6888(1)` / `0xd68cc(0)` (lamp patterns), skips `#` and `;` lines, sends 5-character lines to the engine and **all other lines to the service terminal `0x62670`** (table `0x17781c`). An edited type-5 file can run terminal commands | `STATIC` |
| Read-modify-write: words the script does not name survive. 857 store words on the main body differ from the ROM defaults at `0x175380` outside the script, so per-unit calibration is not in `qrflect7` | `HW` (capture), `INFERRED` |
| `V0002 W0240` sets the `ADJ:` version; `V000C`/`V000E` set Camera Info tag 5 to `2007-09` | `STATIC` |
| Guard at `UEEF1` (`0xd0e86..0xd0f08`): if stored `0xcc` is non-zero and differs from RAM `0xae` (forced to `0x1c` at `0x1e7ac`), screen `0x1a` and power-off, after `qrflect3` has flashed and before any store write | `STATIC` |
| 2.31 -> 2.40 store change: halfword `+0x18ec` `0x405c` -> `0x4055`, so byte `+0x18ed` (an AF contrast-peak threshold) goes 92 -> 85 | `STATIC` |
| The commit completed on the spare body (tag 5 reads `2007-09` after the update) | `HW` (spare body) |
| No emulator run completes the type-5 step. In the update replay it reads the script byte by byte and stops after file byte 895 (about line 55 of 4,486, after `V0058`/`W0000`); no further read in 1.07e9 more instructions; the update task is blocked on an unknown event. `UEE00` never ran in a model | `EMU` |

| | `qrflect3` | `qrflect7` |
|---|---|---|
| Type / format | 2 / 1 | 5 / 3 |
| Payload | ARM/Thumb big-endian application, load/entry `0x1c000` | Text, load/entry 0 |
| Writes | Block `0x3c` and body (physical `0xd..0x2e` for stock) | Block `0x3d` via `UEE00` |
| Driver | `0xd6c58` + `0x63c68`/`0x63b88`/`0x63c80` | `0xd6a62` -> `0x54164` / `0x62670` |
| Version gate | `0x27648` (inert: bounds zero) | none |
| Range check | `+0x20 >= 0x1c000`, end `< 0x422800` | none |
| Monitor RAM loader | accepted | rejected |

## Updater flow

Runs on the `MainCtrl` task inside `0x1e7ac`. `STATIC`, `EMU`

| # | Address | Check | On failure | Tag |
|---|---|---|---|---|
| 1 | `0x1eb22` | UP + TELE + dial 1/10/9 (`0x51228`), or main info invalid | Other combinations tested at `0x1ec74` (DOWN + TELE = version screen); else normal boot | `STATIC` |
| 2 | `0x1eb3e` | Wait for file-task flag `0x1bd3a8`, up to 6000 x `sleep(10)` (absent in 1.08) | Continues anyway | `STATIC` |
| 3 | `0x1eb74` | `/ATA1/QUICKVER.UP` present | Separate flasher `0x26e04`/`0x26f38` with no visible validation and ignored return values; keep it off the card | `STATIC` |
| 4 | `0x5d18c`, screen `0x18` | If info valid: "execute?" (default No) | No, timeout, POWER: normal boot or power-off | `STATIC` |
| 5 | `0x32a22` | Battery state `0x2b9e92` is 3 or 2 | Screen `0x1d`, then boot (info valid) or power-off | `STATIC` |
| 6 | `0x5ceac` | `0x32430(1,0)` | No screen; power-off path | `STATIC` |
| 7 | screen `0x1c`, `0x278d4` | Per file: read `0x200`, magic, per-type check | see 9 | `STATIC` |
| 8 | `0x5cee8` | Abort flag `0x2b9e5c` (cleared at `0x52022`; no setter found) | | `STATIC` |
| 9 | `0x5d18c` | None present: screen `0x1e`. Accepted != present (or invalid info with no type 2/3): screen `0x1a`. **One bad file rejects the card** | | `STATIC` |
| 10 | `0x32a22` | Battery again | Screen `0x1d` | `STATIC` |
| 11 | screen `0x19`, `0xd6dec` | Reopen each accepted file, re-check magic | File marked failed; the loop continues to the next file | `STATIC` |
| 12 | `0xd6c58` | Block fits buffer `0x10000` (`0x63574`); `0xd6bcc` stops key and battery polling (`0x2c9e48 = 0`) | | `STATIC` |
| 13 | `0x63c68` -> `0x63598(0x3c)` | **First destructive step: erase main-info block `0x3c`**. No cancel exists after Yes: `0x5d330` returns at once for screens `0x1c` and `0x19` | | `STATIC` |

### Per-type checks

| Type | Function | Requires |
|---|---|---|
| 2 | `0x276d8` | `+0x11 == 1`; version gate `0x27648`; `+0x20 >= 0x1c000`; `+0x20 + +0x24 - 1 < 0x422800`; fstat size `== +0x2c`; streaming checksum `== +0x30` |
| 5 | `0x277ec` | `+0x11 == 3`; size `== +0x2c`; checksum `== +0x30` |
| 1, 3, 6 | `0x27688`, `0x27738`, `0x27824` | As source; type 1 targets the Monitor window. Keep off the card |
| 0, 4, 7+ | none | Counted present, not accepted: card rejected |

**Version gate `0x27648`.** Rejects only if `+0x14` or `+0x16` is non-zero and the installed version lies outside them, with valid info. Official bounds are zero, so reinstalling 2.40 and downgrading are both accepted; there is no anti-downgrade check. Keep the bound bytes zero in any built image. `STATIC`

**Size trap.** The scan admits a body up to `0x406800` (window) or end `< 0x422800`, but `0x63b88` refuses more than `0x2f0000` B (47 blocks) after the info block is erased. Keep the image size equal to the official one. `STATIC`

**Invalid-info entry.** With invalid main info the update path runs with no keys and no prompt. This happens only if the application runs at all, which after an info erase needs the Monitor's RAM fallback ([02](02-boot-chain-and-monitor.md), [09](09-recovery-and-failure-modes.md)). That fallback is `EMU` only: the Monitor's SD path has never run on hardware. `STATIC`, `EMU`

### Screens and keys

| Screen | Debug text | Keys | Timeout |
|---|---|---|---|
| `0x18` | "execute?" | RIGHT = Yes (`0x1bd9a4 = 0xe`), LEFT = No; release of ENTER (`0x17`) or RL2 (`0x1b`) answers | 30000 units, then No |
| `0x1d` | "battery capacity insufficient" | release of ENTER/RL2 | 10000 |
| `0x1c` | "checking update file" | none | none |
| `0x1e` | "no update file" | as `0x1d` | 10000 |
| `0x1a` | "update file not correct" | as `0x1d` | 10000 |
| `0x19` | "updating program" | none | none |
| none | Success has no screen: `0xd6dec` returns 0, `0x5d18c` returns 1, `0x1ec12` -> `0x32b74`; `0x32b8c` saves settings, CPU1 specials `0xf6`/`0xfd`, `0x4fcaa(2)` | | |

Screens are drawn by `0x5d330(id)` -> `0xc5724`; the strings are debug text, not on-screen text, and the screen element tables (`0x1ff7a8` onward) are not decoded. No screen has been rendered in a model or seen on a camera (the spare's LCD is broken). `0x513c0` emits nothing while two keys are down, so release the held keys before RIGHT. Holding PLAY past 300 polls posts the POWER message, which powers off at the prompt. A blind mistake defaults to No. `STATIC`

### Ricoh's procedure against the code

| Step | Ricoh | Code | Tag |
|---|---|---|---|
| Battery | Fully charged DB-60 or AC adapter | State 3 or 2 | `STATIC` |
| Card | SD, files in root | Only `/ATA1` scanned; upper-case names in code, lower-case in the package (the FAT layer folds) | `STATIC` |
| Dial | Movie | Value 1, 10 or 9 passes | `STATIC` |
| Keys | Hold UP + TELE, then PLAY | The gate reads UP + TELE only; PLAY is most likely the power-on trigger (CPU1 `0xf201` bit 2) | `INFERRED` |
| Confirm | RIGHT, then MENU/OK | RIGHT sets Yes; the release of ENTER/RL2 confirms (a full shutter press also answers) | `STATIC` |
| Duration | "about 1 minute" / "approx. 2 minutes" | Flash busy time alone is about 0.6-1.9 s; on hardware the lamp blinked about 2-3 min (observed, not timed) | `STATIC`, `HW` |

## Flash write order

| Step | Routine | Action | Tag |
|---|---|---|---|
| 1 | `0x63c68` | Erase main-info block `0x3c` | `STATIC`, `EMU` |
| 2 | `0x63b88` | Walk logical blocks from `0xd` (at most 47), map, erase (`0x63598`), program (`0x636dc`). Stock lands on physical `0xd..0x2e` | `STATIC`, `EMU` |
| 3 | per block | Read-back compare while streaming (`0x638e8`, `0x63886`, `0x64f04`); not a second checksum | `STATIC` |
| 4 | `0x63c80` | Program the 512-byte info record at block `0x3c`, sector `0x7e` | `STATIC`, `EMU` |

An interruption between steps 1 and 4 leaves the main image marked invalid. One type-2 install is 35 erases and 2,152 page programs (2,187 operations on `0xd..0x2e` and `0x3c`); the rest of a run is ordinary store traffic at block `0x54` and above, so whole-run totals (2,198 or 2,209) depend on the harness. `EMU`

There is no bulk erase. Every erase or program checks the bad-block marker (`0x64b1c`), unlocks the target, then relocks it. Erase failure takes a spare (`0x6339c`, record type 2); program failure records type 3, copies earlier sectors and retries; the table holds 10 entries. `STATIC`

A type-2 update does not write block 0, the store `0x3d`, extended data or block `0x53`; it writes `1..0xc` only if a block fails. `STATIC`, `EMU`

### Application updater against the Monitor updater

| | Application updater | Monitor updater `0x50ec` |
|---|---|---|
| Entry | Keys, or invalid info with a running application | `MG1MODE=VERUP_INMONITOR`, GPIO60 high |
| Scan | `QRFLECT0..9` through `0x278d4` | Same names through `0x4cc8` |
| Validation | Streaming checksum, magic, ranges, battery | `0x3e7c` skipped: stale checksum accepted |
| Types 5, 7 | 5 via `0xd6a62`; 7 skipped | 7 returns -1; 5 untested |
| Writes | Erase info, body, info | Erase info `0x4558`, body `0x4308`, info `0x4588`, verify `0x4658` |

The Monitor updater is `EMU` only.

## Version strings and markers

| Place | Value | Source | Tag |
|---|---|---|---|
| Main info `+0x12` | `0240` | Copied from the container at flash | `HW` (capture) |
| Version screen `0x5c95c` | `CPU1: V%2x.%02x`, `CPU2: V...`, `MON : V...`, `ADJ`, `EXT`, `SH`, `ST` | CPU2 from `+0x12` via `0x1e444`; MON from a RAM table via `0x1f09c` | `STATIC` |
| `0x1f09c` | High nibble of the first version byte adds `TEST`/`EVAB`/`SUB` | | `STATIC` |
| MakerNote tag 2 | `Rev%4.4x` of `0x1a8c00<<8 \| 0x1a8c04` (formatter `0x112578`), e.g. `Rev0240`. Tag field is 8 bytes (7 characters + NUL). EXIF `Software` is absent (134 of 134 archived JPEGs) | main-info `+0x12` | `HW`, field size `STATIC` |
| Generation 1 marker | The generation 1 image rewrites the format string at `0x11257b` (file `0xf677b`), giving `Rev024M` | | `HW` (both bodies) |
| Generation 2 marker | Generation 2 rewrites the format string at `0x112578` to `240K2` | | `HW` (both bodies) |
| Camera Info tag 5 | `2007-09` written by `qrflect7`; `2005-11` on 1.08 frames | store bytes `0x0d..0x0f` | `HW` |
| USB `-VER` | `200 9.17 VV.VV` from the flash header | main-info header | `HW` |

The version tag and `-VER` read the header only; they say nothing about body bytes. Koma images keep `+0x12` = `0240`, so `-VER` reads `02.40` on every Koma firmware; the MakerNote tag is the way to tell them apart. A dump comparison is the only body check. Other readers of `+0x12` (`0x1f0c2`, `0x1f144`, `0x1f324`) are not audited. `STATIC`, `HW`

## Building an image

| Rule | Why | Tag |
|---|---|---|
| Start from the official `qrflect3` and keep the size | Size changes hit the `0x2f0000` refusal after the erase, and added bytes would land in `.bss` | `STATIC` |
| Change only payload bytes and the 4 checksum bytes at `+0x30` | Everything else in the header must match the official file | `STATIC` |
| Keep `+0x14`/`+0x16` zero and the `+0x1f0` tail intact | Version gate; boot-time validity | `STATIC` |
| Put the image on the card as `QRFLECT3` alone | With `QRFLECT7` also present, `0xd6dec` continues to the store script after the image, even when the image failed. Every modified-image flash on hardware used `QRFLECT3` alone | `STATIC`, `EMU`; one-file use `HW` |
| Use an SDSC card (2 GB) for updates | All hardware updates used one. Under generation 2 the stock file system mounts an SDHC/SDXC card only after a module sets `fs_wide_ok`, and the update gate runs before the loader | `HW` (SDSC), SDHC `INFERRED` |
| Never ship `QUICKVER.UP` or an edited type-5 file | Unvalidated flasher; terminal command injection | `STATIC` |

### Modified images

| | Generation 1 Koma firmware | Generation 2 Koma firmware, build 2b |
|---|---|---|
| Base | Official 2.40 `qrflect3` | The generation 1 image |
| Content | Card-module loader in the Dcfs cave (`0x57f74..0x58197`, loader at `0x57f78`, 344 B), hook at `0x281ee` (`bl 0x282cc` -> `bl 0x57f78`), `Rev0240` -> `Rev024M` (5 data bytes) | The generation 1 image plus an SD 2.0/SDHC/SDXC init path, real-capacity block addressing, and an `/IROM/GRMOD.BIN` fallback in the loader's `open_both` tail (`0x580cc..0x58104`; card file wins). Tag `240K2`. 24 declared edits, 2,052 bytes in 54 runs vs the generation 1 image (2,385 bytes vs stock) |
| Image | SHA-256 `107cb2b6ea500e7e9397f62937af327d306fb10fba66495f69a61d433286c995`, checksum `0x346e94a7` | SHA-256 `75425a70810f3c7b51554bb2941be30116f3f074e8e30afdbbda778095fa9680`, checksum `0x346d46ce` |
| Built by | `tools/koma/koma make-loader` from the user's own `qrflect3` or `gr-240.exe` (refuses unless input and output hashes match) | `analysis/sdhc/driver/build.py`. The installed file is `analysis/sdhc/driver/UNAPPROVED_DO_NOT_FLASH_koma-fw2-240.type2.bin`; the `UNAPPROVED_DO_NOT_FLASH_` prefix is a naming convention of the analysis directory, and these files are what is installed |
| Both | Same size as official, `+0x12` = `0240`, packed with `grd_firmware.pack`; accepted by the stock 2.40 updater | |
| Tag | `STATIC`; installed `HW` | `STATIC`; installed `HW` |

The loader contract is in [how it works](../how-it-works.md#boot-and-the-loader-hook) and [building](../building.md). The generation 2 driver design, its proofs and its card rules are in [`analysis/sdhc/driver/README.md`](../../analysis/sdhc/driver/README.md) and [11](11-modification-playbook.md).

### Contrast-5 tone table

The B&W contrast-5 tone table lives at `0x199b30` (file `0x17dd30`): 4,096 big-endian u16 entries (8,192 B), 12-bit index, 10-bit value, monotone, endpoints 0 and 1023. Three literals point at it: `0x900c4`, `0x90404`, `0x90498` (in `0x8ffc8`, `0x9035c`, `0x90408`); preview and stills both use it. `STATIC`. It is read before the update gate when B&W contrast 5 is the saved setting (boot-time preview upload), and not with default settings. `EMU`. Every stock table keeps the pin 256 -> 64. `STATIC`

Not implemented: a data-only image that replaces this table was built and emulated but never flashed. Looks are applied in RAM by card modules on hardware ([06](06-image-pipeline-and-colour.md)). `HW`

## Update runs on hardware

| Run | Detail | Tag |
|---|---|---|
| Official pair on the spare, 1.08 -> 2.40 | `QRFLECT3` + `QRFLECT7` on a 2 GB SDSC card (1,989,804,032 B); blind keyed power-on (dial Movie; UP + TELE + PLAY; release; RIGHT; MENU/OK); about 2 minutes hands-off. `-VER` then `02.40`, same camera ID; internal memory (19 files) byte-identical; QRFLECT files left on the card | `HW 2026-11-06 spare` |
| The generation 1 image on both bodies | `QRFLECT3` alone (modified) by the same keyed power-on; normal boot, `-VER` `02.40`, JPEGs tagged `Rev024M` | `HW 2026-11-06 spare and main` |
| Official 2.40 restored over generation 1 | Official `QRFLECT3` (`de00e03d…`) alone over the generation 1 image by the keyed update from a 2 GB card that still held photos; lamp blinked green about 2-3 min; normal boot, new JPEG tagged `Rev0240`. Only `QRFLECT3` was read; the photos on the card survived | `HW 2026-11-07 spare` (once) |
| Generation 2 Koma firmware, build 2b on both bodies | `QRFLECT3` alone (`75425a70…`); afterwards MakerNote and settings export tag `240K2`, 64 GB SDXC cards work with the `widefs` module, packages load from `/IROM` | `HW 2026-11-07 main and spare` |
| Restore from generation 2 to stock | Not done on hardware. In the model, install, cold boot, official restore and cold boot end with main and info byte-exact and the whole flash equal to the control | `EMU` |

**1.08 updater.** A function-by-function comparison finds the 1.08 scanner, per-type checks, version gate, streaming checksum, info erase, body programming, type-5 path and `QUICKVER.UP` path identical or the same after relocation to 2.40's. The one difference: 1.08 does not wait for the file task before the card scan (the wait at 2.40 `0x1eb3e`; added in 1.09, whose flag is `0x1b7cdc`). `STATIC`. In the emulator 1.08 runs the official 2.40 update with the same 2,198 operations as 2.40 itself (2,162 programs, 36 erases) and leaves the Monitor, main body, info, store, extended data and block `0x53` byte-identical to a 2.40 self-update; only the `/IROM` settings area differs. `EMU`

## Other loaders that reuse the container

- **Monitor RAM loader**: types 2, 3, 7; staged at `0x08000000 + header.load`; copies before validating. See [02](02-boot-chain-and-monitor.md). `EMU`
- **`SCRIPT.MG1` / `adjcom`**: not container loaders, but share engine `0x54164`; `adjcom UE103` is the flash dump token and `UE104`, one byte away, is restore (a flash writer). See [08](08-card-scripts-and-service-terminal.md). `STATIC`
- **`QUICKVER.UP`**: flashes `QRFLECT3`, `4`, `5`, `0` through `0x26e04`/`0x26f38` with no visible validation. `STATIC`

## Open questions

- Which caller enforces which size window (`0x406800` at `0x62d76`, `0x422800` at `0x276d8`, `0x2f0000` at `0x63b88`). Only the last is known to act after the erase.
- Real duration, lamp pattern and on-screen text of each update screen; whether PLAY plays any role beyond power-on; which dial positions give values 10 and 9.
- What the type-5 step waits for in the emulator.
- The update end path (`0x32b8c`, CPU1 restart request): not observed in any model run.
- Meaning of format values other than 1 and 3; purpose of type 3; the `9.17` prefix of `-VER`; the exact encoding of the `+0x1c` time bytes.
- Whether the updater can read `QRFLECT3` from an SDHC/SDXC card under generation 2 (untested; use SDSC).
- Restore from generation 2 Koma firmware to stock on hardware.

## Evidence

- [`../../evidence/artifact-manifest.json`](../../evidence/artifact-manifest.json) (package file sizes and hashes)
- [`../../tools/grd_firmware.py`](../../tools/grd_firmware.py) (container parse, checksum, pack rules)
- [`../../analysis/flash-review/REVIEW.md`](../../analysis/flash-review/REVIEW.md) (streaming check, file/runtime offsets)
- [`../../analysis/flash-candidate/README.md`](../../analysis/flash-candidate/README.md) (`+0x2c`, flipped checksum, table consumers, one file against two)
- [`../../analysis/flash-failure-real-monitor/README.md`](../../analysis/flash-failure-real-monitor/README.md) (flash-operation counts)
- [`../../analysis/emu-update-replay/README.md`](../../analysis/emu-update-replay/README.md) (1.08 replay, type-5 stall)
- [`../../analysis/version-diff/1x/README.md`](../../analysis/version-diff/1x/README.md) (1.08 updater code comparison)
- [`../../analysis/usb-first-contact/README.md`](../../analysis/usb-first-contact/README.md), [`../../analysis/spare-usb/README.md`](../../analysis/spare-usb/README.md) (spare 1.08 -> 2.40 update)
- [`../../analysis/module-loader/README.md`](../../analysis/module-loader/README.md) (the generation 1 image loader, cave, hook)
- [`../../analysis/sdhc/driver/README.md`](../../analysis/sdhc/driver/README.md), [`../../analysis/sdhc/driver/build.py`](../../analysis/sdhc/driver/build.py), [`../../analysis/sdhc/driver/prove-image/koma-fw2-240-75425a70.md`](../../analysis/sdhc/driver/prove-image/koma-fw2-240-75425a70.md) (generation 2 image, tag, restore cycle)
- [`../../analysis/hw-tests/2026-11-07-spare.md`](../../analysis/hw-tests/2026-11-07-spare.md), [`../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md`](../../analysis/hw-tests/RUNBOOK-2026-11-08-PM.md) (generation 2 on both bodies)
- [`../../archive/STATUS.md`](../../archive/STATUS.md) section 1 (the generation 1 image installs 2026-11-06; official restore 2026-11-07)
- [`../../archive/research/sibling-firmware.md`](../../archive/research/sibling-firmware.md) (sibling containers, M32R)
- [`../../archive/research/recovery-analysis.md`](../../archive/research/recovery-analysis.md) (OneNAND driver roles, write order, replacement table)
