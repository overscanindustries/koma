# 08. Card scripts and the service terminal

**Summary.** Firmware 2.40 contains a text-script interpreter (25 commands, task `tScript`) that runs `SCRIPT.MG1` from the card or `/IROM` at power-on and can inject keys and dial positions. Its `adjcom` command feeds five-character tokens to the adjustment command engine `0x54164`, which reads and writes memory, commits the adjustment store and can copy flash to the card; a stock two-line dump script produced a partial flash image on the main body (HW 2026-11-06); no other script has run on a camera. A separate service terminal (`r`, `w`, `d`, `x`, `ds`, …) sits behind a second USB line handler reached by a boot key combination and has never been exercised on hardware; no service-terminal or key-macro script has run on a camera. The Koma `/IROM/GRMOD.BIN` package is not written by any script command: it is written by a card module installer (see [Script commands](#commands)). The factory adjustment loader (`NADJ0001.ADJ`) needs a key combination at every boot, so it is a development path; Koma's delivery route is the flash loader plus the `GRMOD.BIN` package.

Tags: `HW`, `EMU`, `STATIC`, `INFERRED`, defined in [features](evidence-ledger.md). Addresses are firmware 2.40 runtime addresses (payload offset + `0x1c000`; file offset + `0x1be00`).

Related chapters: [05 settings and adjustment store](05-settings-and-adjustment-store.md), [06 image pipeline and colour](06-image-pipeline-and-colour.md), [07 USB protocol](07-usb-protocol.md), [02 boot chain and Monitor](02-boot-chain-and-monitor.md), [03 firmware image and update format](03-firmware-image-and-update-format.md), [09 recovery and failure modes](09-recovery-and-failure-modes.md).

## Key addresses

| Address | Meaning | Tag |
|---|---|---|
| file `0x15bca4` | 25-entry MG1 dispatch table, 28-byte records (16-byte name, BE Thumb pointer, argument count, type codes) | `STATIC` |
| `0x65108` | Interpreter entry: needs buffer `0x10000`, selects source, starts `tScript` (stack `0x4000`, priority `0x50`) | `STATIC` |
| `0x6515c` | Source selection: `/ATA1` first, `/IROM` only if the card open fails | `STATIC` |
| `0x65220` / `0x65202` | Task body / script exit | `STATIC` |
| `0x658d8` / `0x659b2` / `0x65858` / `0x658a8` | Tokenizer / line parser / line skip (to CR, then two bytes) / comment skip (`#` first byte) | `STATIC` |
| `0x65444` | Interpreter state reset at script start (clears counters, 64 label slots, 16 loop slots) | `STATIC` |
| `0x66a9c` | Posts the key-release event `{2,0}` after `end` saves | `STATIC` |
| `0x66b90` | True while a card script is active for this power-on; read by `0x4fd1e` | `STATIC` |
| `0x177bcc` | Label-line handler pointer, holds `0x66221` (the `end` handler) | `STATIC` |
| `0x652fc` / `0x653c0` / `0x654ec` | CONFIG load / rotation / save (3,192 bytes) | `STATIC` |
| `0x55734`, `0x55844`, `0x558ac`, `0x55888` | Script gate (run by tFileTask): `0x3e8ec()` test, probe both locations, set `0x1a90e4`, message `0x1202` to MainCtrl | `STATIC` |
| `0x281e8` / `0x2821e` / `0x2822c` | MainCtrl handler: reads the gate, calls `0x65108` | `STATIC` |
| `0x1a90e4` | Script gate byte, read once per boot at `0x2821e` | `STATIC` |
| `0x1b9866` / `0xa84fc` | Mode byte = level of pin 8; high closes the gate | `STATIC` |
| `0x51740` | Key/dial injection sink (overlay `0x2b9dd8`) | `STATIC` |
| `0x4fd1e` | Dial-position to app-state resolver | `STATIC` |
| `0x1a8bfc` | Shared work-buffer pointer (128 KB block) | `STATIC` |
| `0x66878` | `mg1_adjcom` | `STATIC` |
| `0x66988` / `0x66a30` | `install` / `uninstall` | `STATIC` |
| `0x54164` | Adjustment command engine; lock flag `0x1bd3a4`, unlock token `UEE40` | `STATIC` |
| `0xd095c` / `0xd0a1c` / `0xd0dde` | Engine `U` handler / 75-entry table / case body for `UE103`/`UE104` | `STATIC` |
| `0x67434` / `0x674bc` | Flash-to-card dump / card-to-flash restore; reached from `0xb62b4` / `0xb62be`; read loop `0x64d80` | `STATIC` |
| `0xd6a62` / `0xd6888` / `0xd68cc` | Type-5 update text reader / session open / close | `STATIC` |
| `0x28a60`, `0x28a61`, `0x1c1d8`, `0x3c4`, `0x9d0` | Terminal `x` wrapper (ARM / Thumb entry), native return, saved Monitor callback, Monitor console entry | `STATIC` |
| `0x5443c` → `0x544ec` | Factory adjustment-program loader (`NADJ0001.ADJ`) | `STATIC` |
| `0x62670` | Terminal dispatcher (table `0x17781c`, help `0x61e3c`) | `STATIC` |
| `0x54020` (`tAdjust`), `0x542b8`, `0x5d4b8`, `0xcdbb0`, `0xcdad8`, `0xcda50` | Service USB input chain | `STATIC` |
| `0x53b0c` / `0x1bd2c8` / `0x1bd39c` | Task `tDcx` / router selector / input-source selector | `STATIC` |
| `0x26b10`, `0x534be`, `0x1a9028`, `0x1bd294` | Console output chain and its gates | `STATIC` |
| `0x4fbcc`, `0x32540`, `0x51228` | Boot key-combination tests | `STATIC` |
| `0x1eb26` | Update gate `0x4fbcc(2,0)` (UP + TELE) | `STATIC` |
| `0x2c39a0` | Block shared by `tScript` and `-CCTL` handlers | `STATIC` |

## Script interpreter

### Text format

| Rule | Detail | Tag |
|---|---|---|
| Line endings | CRLF required on every line, including the last; LF-only fails; a final line without CRLF ends without running `end`. The line-skip routine `0x65858` stops at CR and then advances two bytes, so the byte after CR is assumed to be LF | `STATIC`, `EMU` |
| Tokens | Tokenizer `0x658d8`: space and tab separate, CR ends the line, up to 16 characters (longer is `overflow term length`); no quoted strings | `STATIC` |
| Numbers | `0x`/`0X` hex, otherwise decimal | `EMU` |
| Case | Command names are case-sensitive (`wait`, not `WAIT`) | `EMU` |
| Comments | A line whose first byte is `#`. An indented `#` parses as unknown tokens. `rem` also swallows the next line | `EMU` |
| Labels | Start with `@` (64 entries; `@main` preferred start). Executing a label line runs the `end` handler, so falling into a label ends the script and saves `CONFIG.TST` | `STATIC`, `EMU` |
| Size | Below 65,536 bytes; no signature or header check | `STATIC` |
| Tables | 32 key aliases, 16 dial aliases, 16 loop slots, 64 labels, bounded gosub stack; no built-in names | `STATIC` |

A first-line label with no `@main` ends the script at once (`STATIC`). `repeat 0` has no decrement-to-zero exit; `defkey` argument A (0..8) is stored at `+0x11` and `defdial` A, B are stored with no reader found (`STATIC`). Each start resets the interpreter through `0x65444`.

Supersedes earlier readings: labels are not inert markers; `rem` skips two lines, not one.

### Task loop and return codes

A parse failure skips to the next token; a handler failure, `end` or `poff` stops the task; end of file stops it without saving or clearing keys. `tScript` and `tOpeMain` share priority `0x50` with no time slice, so key events land only when the script blocks: `keyhold x` immediately followed by `keyrel x` with no `wait` does nothing visible (`STATIC`; `INFERRED` for the scheduling reading). Scripts have no read command, no branch on camera state and no feedback.

### Commands

| Command | Handler | Effect | Persistence | Tag |
|---|---|---|---|---|
| `keyset A C` | `0x65cfc` | Post `{C, A}` as message `0x7103`; A < 4, C < 42, else the task stops | RAM | `STATIC` |
| `dialset N` | `0x65d70` | Virtual dial N (< 9), then post `{0,0}` | RAM | `STATIC` |
| `key` / `keyhold` / `keyrel name` | `0x65dc4` / `0x65e64` / `0x65ee8` | Alias lookup; tap / hold / release | RAM | `STATIC` |
| `dial name` | `0x65f64` | Virtual dial from the alias (unchecked) | RAM | `STATIC` |
| `wait N` | `0x66008` | Sleep N × 1000 ticks; wraps above 4294 (`wait 4295` = 32 ticks); `wait 0` yields | none | `STATIC` |
| `repeat N` / `next` | `0x66030` / `0x660c4` | Counted loop; `repeat 0` is unbounded | none | `STATIC` |
| `poff N` | `0x661b0` | State 1, save CONFIG, arm reboot flag `0x2b9e5d`/`0x2b9e60`, post `{6,3}`: a boot loop by design. It does not send the CPU1 reboot request itself (`0x520c8` does, later) | CONFIG | `STATIC` |
| `end` | `0x66220` | State 2, rotate and save `CONFIG.TST`, post release `{2,0}` | CONFIG | `STATIC` |
| `rem` | `0x66244` | Skip the rest of the line and the next line | none | `EMU` |
| `defkey n A B` | `0x6625a` | B = event code at `+0x12`; codes above 42 write past the array | RAM corruption | `STATIC` |
| `defdial n A B C` | `0x66314` | C = virtual dial used by `dial` | RAM | `STATIC` |
| `logon` / `logoff` | `0x66408` / `0x66444` | Interpreter log to a 1 KB console ring `0x237f40` (blocks when full and undrained) | none | `STATIC` |
| `gosub` / `return` / `goto` | `0x66480` / `0x665cc` / `0x6669c` | Control flow | none | `STATIC` |
| `logtime` / `logbatt` / `logstr` | `0x66754` / `0x667c0` / `0x66844` | Log clock, battery A/D, a token | none | `STATIC` |
| `adjcom TOKEN` | `0x66878` | Upper-case a 5-character token starting A..Z and call `0x54164`; reports success even if the engine rejects it | engine can persist, reboot, dump flash | `STATIC` |
| `install` | `0x66988` | Delete `/IROM/SCRIPT.MG1`, copy `/ATA1/SCINST.TST` → `/IROM/SCRIPT.MG1` (then runs with no card) | `/IROM` | `STATIC` |
| `uninstall` | `0x66a30` | Delete `/IROM/SCRIPT.MG1`, `CONFIG.TST`, `CONFIG.BAK` | `/IROM` | `STATIC` |

Supersedes the earlier reading that `install` copies `/ATA1/SCRIPT.MG1` to `/IROM/SCINST.TST`. `install`/`uninstall` do not load native code.

The Koma `/IROM/GRMOD.BIN` package is not installed with `install`: that command's file names are fixed (`SCINST.TST` to `/IROM/SCRIPT.MG1`). The package is written by the card module `irominstall.grmod`, run as the card's `GRMOD.BIN` with the package beside it as `KOMAPKG.BIN`; it copies, reads back and compares, and `KOMARM.TXT` beside it makes it delete the `/IROM` copy instead (HW 2026-11-08 both bodies; [analysis/sdhc/spare-test/modules/README.md](../../analysis/sdhc/spare-test/modules/README.md)).

### Key and dial injection

Events `{code, action}` go as message `0x7103` to `qOPE` and are consumed by sink `0x51740`, polled every 50 ticks in `tOpeMain`. The same primitives serve USB `-CCTL` ([07](07-usb-protocol.md#subcommands)).

| Sink code | Behaviour |
|---|---|
| 0 | Commit the virtual dial; emit dial event `(0,4)` if it changed |
| 1 | Set the virtual dial to the real dial |
| 2, 42 | Injection off, clear all injected keys, virtual dial back to 9 |
| others | Injection on, `0x2b9dd8[code] = action` (no upper bound) |

Actions: 0 stop overriding (cannot force a physically held key up), 1 hold (`(code,1)`, repeats `(code,2)` after 16 polls then every 10), 3 tap; 2 is stored but ignored (`STATIC`, `EMU`).

Key codes (names from the firmware table `0x18108c`, physical function `INFERRED`): 6 POWER (also posts power-off `0x720d`), 7 PLAY, 11 UP, 12 DOWN, 13 RIGHT, 14 LEFT, 15 TELE, 16 WIDE, 20 TRASH, 22 DISP, 23 ENTER (likely MENU/OK), 26 RL1, 27 RL2, 36 DPUSH, 41 HEADUP. Inert when injected: 3, 4, 5, 10, 17, 18, 19 (MENU), 21, 24, 25, 39, 40. 30/31/34/35 come from the wheels (28/29, 32/33) and cannot be injected. 0, 1, 2 are control codes.

Mode dial: MODE0..2 bits map 0→9 (none), 1→4, 2→8, 3→7, 4→3, 5→1, 6→6, 7→10 (transitional). `0x4fd1e` maps position to app state: 1→3, 2→4, 3→5, 4→2, 6→10, 7→12, 8→13, else 6 (playback). The virtual dial starts at 9, so the first injected key turns injection on with a record state resolving to playback; key scripts should start with `keyset 0 1` (`STATIC`, `EMU`). Which printed dial label is which position is not established.

### Activation

1. Startup `0x1effa` sends `0x1102` to tFileTask; its state-table row 0 runs `0x55734`.
2. At `0x55844` the gate stays 0 if `0x3e8ec()` returns 1; otherwise `0x558ac` probes `/IROM/SCRIPT.MG1` then `/ATA1/SCRIPT.MG1` and sets gate `0x1a90e4` on either hit (the order does not matter: the result is an OR).
3. `0x55888` sends `0x1202` to MainCtrl; the handler at `0x281e8` reads the gate once at `0x2821e` and calls `0x65108` at `0x2822c`.

(`STATIC`, `EMU`.)
- A high pin 8 (`0x1b9866`) closes the gate; that pin 8 is USB is `INFERRED`. Supersedes "suppressed under USB via name-table index 8".
- No separate key combination starts a script (`STATIC`).
- Gate race: `0x1e7ac` chooses what MainCtrl waits for. When `0x1ef90()` reports a record-start state it waits on mask 2, which does not order the gate store against the gate read; otherwise it waits on mask 4 (message `0x1202`, ordered). A lost race means no script and no error (`STATIC`; `EMU` shows the store winning, at 23.84 M instructions against the read at 70.36 M).
- The card copy is loaded first; `/IROM` is used only if the card open fails. A card script that opens but fails read or stat gets no `/IROM` fallback and no task (`EMU`). An installed `/IROM/SCRIPT.MG1` runs with no card and writes its CONFIG files to `/IROM`.
- A card script makes `0x66b90` true for that power-on, so `0x4fd1e` resolves playback to the dial mode (state 6 to 3 in the emulator, `EMU`). The Playback start on the main body still ran the script.
- By code the gate is evaluated at every power-on while the file exists, and `CONFIG.TST` with state 2 is not a "done" flag (`STATIC`, `EMU`). The script also runs after a declined or rejected update and on the first boot after a successful flash, but not during a successful update (`0x1e7ac` never returns to the reader), so a script card is not a valid update card (`STATIC`).
- On the main body (2.40, 2026-11-06) a start from Playback ran the stock dump script; a start with POWER produced no output (`HW`). This is one successful activation and one non-result, not a POWER/Playback rule. The cause of the POWER-start result is unknown (race lost, load failure, command rejection; the card readback cannot tell them apart).

### CONFIG.TST and CONFIG.BAK

3,192 bytes (`0xc78`) of interpreter state starting with a selector word: 1 = card, anything else directs config operations at `/IROM` (`STATIC`). Exceptions that keep selector 1: an empty file, 1 to 3 NUL bytes, and a first word of exactly 1 (`EMU`).

| Moment | Operations (selector 1) |
|---|---|
| Load, no file | nothing written |
| Load, full-size, not a resume | delete BAK, rename TST → BAK, delete TST |
| Load, short file | read over live state before the length is rejected; no rotation |
| Load, state 1, matching stamp and size | resume at the saved offset (not bounds-checked) |
| `end` / `poff` | rotate, create TST, write 3,192 bytes; close result ignored |

Hazards (`STATIC`, `EMU`):
- A foreign or corrupt `CONFIG.TST` whose first word is not 1 redirects rotation and save to `/IROM` while the script still loads from the card.
- A resume offset past the end exits silently with no save; below the buffer, RAM is parsed as script text.
- `CONFIG.BAK` is never read back.
- Rotation is not transactional: a failed rename leaves both files deleted; a failed close still reports success.
- A state-1 `CONFIG.TST` without a script is inert, and `/IROM/CONFIG.TST` alone starts nothing.
- Causes of an absent `CONFIG.TST` after a run: gate closed or race lost, load failure, foreign redirect, create failure, rename-and-create failure, past-end resume.
- In emulation, injected rename, create, write, close, read-only and full-card failures on a finished minimal script (`end` only) never wrote to `/IROM`; the only route found to `/IROM` writes is a `CONFIG.TST` whose first word is not 1 (`EMU`; the 41-scenario run records are not retained in `analysis/`).

### Shared work buffer

Every script start advances `0x1a8bfc` by `0x10000` inside a 128 KB (`0x20000`) block (allocated in `0x1e444`) and clears `0x1a909d`, which turns templates off for that power-on. `ricoh.res` (79,872 bytes, from the main body's flash) loads to that pointer with limit `0x1fc00`, which overruns the shifted remainder by `0x3800` bytes; a second consumer `0x115148` overruns by `0x4000` (`EMU`, with the real resource size). Whether these consumers run after a script on a real body is unknown.

## Adjustment command engine

The engine takes a five-character token, upper-cases it and dispatches on the first letter, gated by lock flag `0x1bd3a4`. It is reached by type-5 update files (`0xd6a62`), by `adjcom`, and by the service-terminal input task (`STATIC`).

| Letter | Handler | Effect | Class |
|---|---|---|---|
| A | `0xcddbc` | Sensor/gain latch | write (RAM) |
| B | `0xcde6c` | Pointer = addr + `0x08000000`; read or latch | read / write pointer |
| C | `0xcdf50` | Write 16 bits at the pointer, `ptr += 2` | write memory |
| D | `0xcdfd0` | Table lookup `0xa1af0` | read |
| F | `0xce010` | Sub-dispatcher; clears the `0x1b000` settings region and counters | persistent writes |
| I J L M N | `0xcea4a`..`0xcf86c` | Sensor and image-pipe adjustment | writes |
| S | `0xd08b8` | Sub-dispatcher | mixed |
| U | `0xd095c` | Large sub-dispatcher: mode set, commits, store tokens, flash dump and restore | persistent writes, reboot, flash |
| V | `0xd1c30` | Set offset; read 1/2/4 bytes at base + offset (`%.4X`) | read memory |
| W | `0xd1d4c` | Write at the `V` location | write memory |
| X | `0xd1e70` | Region base = arg << 16 (`X0000` → 0; `X12C0` → `0x08010000`) | select |
| Y | `0xd1ee0` | Debug print only | none |
| Z | `0xd1f28` | Key injection, power, LED, sensor power | persistent writes, power |

(`STATIC`.) `Y` prints `[AdjCommandY] com = [%s]`; the sub-dispatcher `F` (`0xce010`) calls the adjustment-program loader `0x5443c`, which startup (`0x1e7ac`) also calls. The debug flag is `0x2c2d30 == 1`.

- Coverage: 17 routed root letters; eight guarded ADD-PC tables inside `U` resolve to 1,470 instructions, 157 direct call sites and 98 targets with no direct call to the Monitor entries; all 75 top-table cases were run up to, not into, the case body (77 checks pass with the locked/unlocked controls). No unknown adjustment case was executed (`EMU`, `STATIC`).
- `X0000` then `V<off>` reads the Monitor region `[0, 0xfc00)` word by word, but replies go only to the debug console (through `adjcom` only if `0x2c2d30 == 1`).
- `UE103` reaches the flash-to-card dump `0x67434` (chain `0x138ae8(0x54)` → `0xb62b4` → `0x67434` → `0x64d80` → `/ATA1/IROM.DAT`); the adjacent `UE104` reaches restore `0x674bc`, which writes flash. One character separates a read of all flash from a write of all flash (`STATIC`; both intercepted before execution in the emulator). The full dump is 65,536 records of 516 bytes, 33,816,576 bytes (`STATIC`).
- The dump writes 516-byte records (512 data + 3 packed metadata + 1 buffer byte, not a spare/ECC image) for 65,536 sectors to `/ATA1/IROM.DAT`, deleting any existing file; no capacity check; only `-1` counts as a write error; the close result is ignored (`EMU`).
- Other flash routines: restores from `FPROM.DAT`/`IFPROM.DAT` (`0x27e38`/`0x27f50`) and an erase of blocks 1..`0x1ff` (`0x28194`) (`STATIC`).

### The UEE tokens

| Token | Function | Effect | Flash |
|---|---|---|---|
| `UEE00` | `0x27c5c` | Byte 0 must be `0xAA`/`0x55`; checksum; erase and program block `0x3d` | erase + program |
| `UEE40` | | Clear the engine lock flag (RAM) | none |
| `UEE50` / `UEE51` | `0x27cbc` / `0x27d54` | RAM store to / from `/ATA1/ADJRAM.DAT` (else `/IROM`), exactly `0x2000` bytes, no checks on load | none |
| `UEE52` | `0x27d0c` | Read the flash store and write `/ATA1/ADJROM.DAT` | read |
| `UEE53` | `0x27da0` | `ADJROM.DAT` → RAM, then the `UEE00` commit | erase + program |
| `UEEF1` / `UEEFF` / `UEEAB` | `0x27c28` / `0x27bdc` / `0x27bc0` | Reload from flash / boot-style load with default fallback / ROM defaults to RAM | read / none |

The official type-5 update `qrflect7` (31,914 bytes) is text for this engine: `X12C0` twice, `UEEF1`, 2,241 `V<offset>` / `W<value>` pairs with offsets `0x0002` to `0x1cd4`, then `UEE00`; it rewrites the calibration region at `0x08010000` and commits it (`STATIC`, read from the file in `firmware/`). In a type-5 file, a line that is not five characters without spaces goes to the terminal dispatcher `0x62670` (see Returns into the Monitor).

No boot-path caller reaches any of the `UEE` tokens (`STATIC`). Supersedes the reading of `0x27d0c` as a boot-time `ADJROM.DAT` loader: it is a dump, reachable only as `UEE52`. Store layout and commit risks are in [05](05-settings-and-adjustment-store.md#factory-adjustment-store).

### Factory adjustment loader

At startup `0x1effa` only tests that `/ATA1/RADJ/NADJ0001.ADJ` opens. After the update gate, if that flag is set, the keys are not a plain record start and `SCRIPT.MG1` is absent, `0x5443c` → `0x544ec(n)` reads a `GAUSS_CADJ_FILE` header (body = file size − `0x200`, at most `0x8000`), loads the body to `0x13000`, checks a byte sum and calls it: unsigned native code from the card (`STATIC`). An unmodified 2.40 application executes a minimal probe this way and returns to post-startup under supplied RIGHT + TELE and dial value 1 (`EMU`). An `0xff` chain sentinel requests power-off; a missing next file returns −1 and startup continues. The route needs the key combination held at every boot, so it serves development, not delivery (`INFERRED`).

### RAM-only store route

Boot always reloads the store from flash (`0x1e444` → `0x27bdc`). A type-5 file without `UEE00` changes the RAM store with no flash operation, but the updater always ends in power-off `0x32b74`, so the change is lost. A card `SCRIPT.MG1` with `adjcom X12C0` / `V…` / `W…` (and no commit) changes the RAM store for the current power-on with no operation on block `0x3d`; the next B&W contrast-3 shot uploaded the contrast-5 table, and the luma word reached `0x1201800c` only at the next pipeline set-up (`EMU`). The script must be present at every power-on.

## Stock flash dump

The script is 19 bytes, CRLF: one `adjcom` line with the dump token and one `end` line. It deletes any existing `/ATA1/IROM.DAT` and rotates `CONFIG.TST`/`CONFIG.BAK`.

| Start | Observation | Tag |
|---|---|---|
| POWER, no USB | No `IROM.DAT`, no CONFIG files; script unchanged | `HW 2026-11-06 main` |
| Playback (first) | Lens extended, controls briefly unresponsive, camera turned off; zero-byte `IROM.DAT`, no CONFIG files | `HW 2026-11-06 main` |
| Playback (second) | `IROM.DAT` 6,736,896 bytes (two reads identical) and `CONFIG.TST` 3,192 bytes; lamp blinked, controls briefly unresponsive | `HW 2026-11-06 main` |

What the dump holds (`HW main 2.40`):
- 13,056 complete 516-byte records, ending just before block `0x66` (last sector `0x32ff`), not the full 65,536. The dump loop closes and returns -1 after any nonzero read result or a -1 write result; either an uncorrectable read or a card write error reproduces the exact length (`EMU`). Every fourth trailer byte is zero: no spare/ECC or bad-block marker is in the output. `adjcom` discards the engine result, so the cause is unknown ([analysis/dump-boundary-audit](../../analysis/dump-boundary-audit/boundary-report.json)).
- `CONFIG.TST`: selector 1, state 2, offset 19, line 3, consistent with reaching `end`.
- The main image from block `0x0d` equals all 2,201,960 bytes of the official 2.40 payload.
- A complete `0xfc00`-byte Monitor window with `MON` metadata; its header and code checksums validate as Monitor 1.00.
- The extended-data directory at relative `0x146e00` places `ricoh.res` at relative `0x200`, 79,872 bytes.

The device-specific capture is not published. Shutdown reaches the settings save only conditionally after a script (`STATIC`), and clean shutdown after a dump is not proven. In the emulator the modelled full cycle writes 65,536 records, reaches `end` and the 3,192-byte `CONFIG.TST`, and performs no flash operation (`EMU`).

## Failure behaviour

| Event | Result | Tag |
|---|---|---|
| Finite script, `end` succeeds | keys cleared, CONFIG written, task stops | `EMU` |
| Handler error (undefined key or label, bad value, nest overflow) | task stops at once, no save, keys stay held until power cycle | `EMU` |
| Syntax error | line skipped; a skipped release line leaves a key held | `EMU` |
| No `end` or no final CRLF | stops at end of file, no save, no clear | `EMU` |
| Loop with `wait` | runs forever; polling continues | `EMU` |
| Loop without `wait` | `tScript` never blocks; key polling stops; POWER handling appears starved | `EMU` (starvation `INFERRED`) |
| Card removed mid-run | script keeps running from RAM; `end` then fails | `EMU` |
| Full card | `end` returns −1, no release event; a short file may remain | `EMU` |
| Power loss during the CONFIG write | file-system behaviour unknown | `INFERRED` |

Routes to persistent state or a boot loop: `end`/`poff` (CONFIG files), `install`, a foreign CONFIG redirect, `adjcom`, a `poff` loop, injecting POWER (code 6), a spin loop on the first line, key sequences that change settings, `defkey` codes above 42 (`STATIC`). Removing the card does not undo settings changed by injected keys.

## Service terminal

### Commands

Table `0x17781c`, 8-byte records (name[4] + handler) (`STATIC`).

| Name | Handler | Effect | Tag |
|---|---|---|---|
| `d` | `0x618bc` | Dump 8 lines of 16 bytes, address aligned to 4, saved pointer += 128; accepts address 0 | `EMU` |
| `r` | `0x61cf8` | Resolve a symbol or address (resolver `0x6188e`), print one 32-bit word as `%s=%ld (0x%08lx / 0b%s)`; a zero result prints `-- Symbol not found or NULL pointer.`; no flash-sector primitive is called | `EMU` |
| `w` | `0x61c88` | Write memory | `STATIC` |
| `x`, `x&` | `0x61a20` | Execute code | `STATIC` |
| `ds` | `0x61b6c` | Load an S-record into memory (`0xdab6c`), report `[start-end]` | `STATIC` |
| `?`, `?k` | `0x61d9c`, `0x62458` | Help | `STATIC` |
| `i`, `raw`, `im`, `io`, `lk`, `t` | `0xdacd8`, `0x6195c`, `0xdb1a0`, `0xdbc04`, `0x61b0c`, `0x61d90` | not decoded | |

`0x62670` has no authentication or confirmation: a line whose first token matches runs at once (up to 10 tokens). The `UEE40` lock belongs to the engine, not the terminal (`STATIC`).

### Transport and console routing

```
service USB handler 0xcda50 (state 1)
  -> 0xcdad8 (events 0x403/0x430) -> 0xcdbb0 (CR-terminated line)
  -> 0x5d4b8 (message 0x6101 -> queue qADJUSTUSB)
  -> 0x542b8 -> task 0x54020 (tAdjust)
  -> 5 characters, no space in positions 1-4 -> engine 0x54164
     any other line                         -> terminal 0x62670 -> table 0x17781c
```

(`STATIC`; three synthetic lines routed correctly, `EMU`.) `0x53734(arg)` copies its argument into router selector `0x1bd2c8`; `0x54320(arg)` sets input selector `0x1bd39c`.

Output waits `0x288f6(10)` per call. Path: `0x26b10` (destination flag `0x1a9028`) → `0x534be` (drops output while `0x1bd294` = 0) → `0x534f4` (rejects ≥ `0x400` bytes) → `0x5317a(8)` → `0x24f34` → `0x5f688(pipe 1)` → `0x5f5d4` → bulk IN pipe 1. The queue handle for qADJUSTUSB is `0x1bd394`. It is plain text with no frame header or trailer. Ready flag `0x1bd294` is set by vendor request `0x22` (type `0x41`/`0x40`, value 3, mode 0), not by the class-type request `0x21` or in another mode (`EMU`); request `0x22` and request `0x20` each queue a 12-byte `c1 20 …` notification (`EMU`, [analysis/rea-recovery-terminal/setup-and-pipes-run.json](../../analysis/rea-recovery-terminal/setup-and-pipes-run.json)). Descriptors are the same as ORIGINAL mode, so the camera still enumerates as `05ca:2218`; whether a real host sees the console is unknown.

### Key-sequence entry

`0x4fbcc(1,0)` → `0x32540(0)` passes when RIGHT and TELE are held and `0x51228()` returns 1, 10 or 9 (`STATIC`). By analogy with the update gate (UP + TELE, dial on Movie), the terminal combination is RIGHT + TELE with the dial on Movie, held through the sample points `0x1e98c`, `0x1ef96..0x1efb2`, `0x1ea82` (`INFERRED`); hold timing and whether PLAY is needed are unknown.

Effects when held (`STATIC`): `0x1e98c` sets `0x23a88c` = 1 (and `0x23a888` = 1 if pin `0x38` stays low through 25 polls); `0x53b0c(1)` creates `tDcx` with argument 1 plus `tPtpTran`; `0x54320(1)` selects queue input; at the end of startup, if both flags are 1, `0x5d5d0(0x800)` and `0x26b04(1)` point the console at USB. With RIGHT + TELE modelled, both selectors become 1 even with pin `0x38` high, but the console destination only with the pin low as well (`EMU`). Do not combine with `-CCTL`: both write block `0x2c39a0`.

### Returns into the Monitor

| Route | Result | Tag |
|---|---|---|
| Terminal `x 0x28a61 0` | Wrapper `0x28a60` → native return `0x1c1d8(0)` → saved callback `0x3c4` → Monitor warm reset and UART prompt `OneNand>` (UART `0x12028000`); the component run reaches `0x9d0` after 20,637 virtual instructions with flash unchanged | `EMU` |
| Monitor `apl`, then `jr` | Loads a type-7 marker into RAM; the application starts normally (about 4.94e8 virtual instructions in the chain); startup makes ten programs in block `0x66` and one erase in `0x55`; blocks `0x00..0x53` unchanged | `EMU` |
| Type-5 file with the line `x 0x28a61 0` | The update reader (`0xd6a62`, entered through `0xd6888(1)`) sends non-5-character lines to `0x62670`, reaching Monitor console entry `0x9d0` at 119,370,677 virtual instructions with no USB terminal and no flash operation (program/erase 11 → 11, lock records 22 → 22) | `EMU` |
| Card script to Monitor | No direct link from any of the 25 MG1 handlers, 17 engine roots or 77 guarded `U` cases; a terminal `x` line is not a script opcode | `STATIC` (bounded) |

The type-5 result depends on input timing in the model: confirmation pulses spaced 1 M instructions apart skipped the UI, 12 M reached the update. A comment-only type-5 file reaches end of file and returns from the updater at 119,321,639 instructions without entering the Monitor. The run stops at console entry; fresh prompt output in that chain, FIQ masking and real timing are not established. The application's USB transport does not continue as the Monitor UART. The bounded negative for scripts covers the 25 MG1 handlers, 17 engine roots and 77 guarded `U` cases; indexed callers of `0x28a60` are none, which is not a proof that indirect entry is impossible ([analysis/script-monitor-entry](../../analysis/script-monitor-entry/README.md)).

## `grdscript` lint

[`tools/grdscript`](../../tools/grdscript/README.md) lints and builds scripts offline by running the firmware's own parser under emulation (95 tests): `lint FILE [--strict]`, `explain FILE`, `build interval --shots N --gap-seconds S`, `build sequence --gaps a,b,c`. Refusals with no override: `adjcom`, `install`, `uninstall`, `poff`, injecting POWER, unbounded loops, `repeat 0`. Warnings include `rem` swallowing a line, label fall-through, loops without `wait`, keys held at exit, missing `end`, missing `keyset 0 1` prelude, inert keys, `wait` overflow above 4294, long waits, keys within 3 units of start, arrow + TELE/WIDE service combinations, and trigger file names (`QRFLECTn`, `QUICKVER.UP`, `RADJ/…`, `IROM.DAT`, `SCINST.TST`). Lint errors include `crlf`, `final-crlf`, `size`, `token-length`, `label-limit`, `alias-limit`, `case`, `unknown-command`, `undefined-label` and `handler-error`. Further warnings: `long-wait` (120 or more units) and `early-key`. Output is never named `SCRIPT.MG1` unless asked. The eight prepared candidate scripts in `scripts/candidates/` (minimal `end`, DISP cycle, single shot, interval, sequence, EV and focus bracket guesses) lint clean under the simulated parser; none has run on a camera except the dump script. No key-macro script built this way has run on a camera.

## Open questions

- Why the POWER start produced nothing while Playback ran the script (gate race, mode, load failure). Whether the gate is reliable at every power-on is unconfirmed.
- Why the dump stopped at sector `0x32ff` (read failure, card error or another condition), whether the rest of flash (blocks `0x66..0x1ff`) can be dumped, and whether the body was left in a clean state.
- Whether the work-buffer overrun consumers run after a script on a real body.
- Whether `/IROM/SCRIPT.MG1` or `/IROM/CONFIG.*` exist on a given body.
- Physical labels for key and dial codes (which printed dial label is which position); whether injected keys reset Auto Power Off; whether pin 8 is USB; tick length (1 tick = 1 ms, `INFERRED`, no stopwatch trial).
- What the CPU1 `poff` value means and whether its reboot timer survives battery removal.
- Service terminal: physical activation (dial position, hold timing, GPIO `0x38`), whether a host sees the console, and what `i`, `raw`, `im`, `io`, `lk`, `t` do (a static decompile of the six handlers in table `0x17781c` has not been done). Only `w`, `x` and `ds` have established write or execute behaviour.
- Full hardware start-up through the factory adjustment loader, and cache coherence for card-loaded code on that route.
- Whether a Monitor card-RAM load can be started from the service terminal: the terminal return reaches the Monitor console in emulation only.

## Evidence

- [analysis/script-monitor-entry/README.md](../../analysis/script-monitor-entry/README.md): 25 handlers, engine roots, `U` dispatch coverage, type-5 route to Monitor entry
- [analysis/ram-activation/terminal-handoff/report.json](../../analysis/ram-activation/terminal-handoff/report.json): terminal return, `apl`/`jr` chain
- [analysis/ram-activation/emulated-monitor-return.json](../../analysis/ram-activation/emulated-monitor-return.json): `0x1c1d8` to `0x9d0`
- [analysis/dump-boundary-audit/boundary-report.json](../../analysis/dump-boundary-audit/boundary-report.json): dump length and boundary
- [analysis/rea-recovery-terminal/setup-and-pipes-run.json](../../analysis/rea-recovery-terminal/setup-and-pipes-run.json): service USB ready flag, notifications, pipes
- [analysis/store-recipes/README.md](../../analysis/store-recipes/README.md): `UEE` tokens, RAM-only route
- [analysis/hidden-modes/README.md](../../analysis/hidden-modes/README.md): boot key tests, `NADJ0001.ADJ` loader
- [analysis/sdhc/irom-fallback/README.md](../../analysis/sdhc/irom-fallback/README.md): script gate and `/IROM` fallback, `install` routine
- [analysis/sdhc/spare-test/modules/README.md](../../analysis/sdhc/spare-test/modules/README.md): `irominstall` card module
- [analysis/export/disasm.txt](../../analysis/export/disasm.txt): 2.40 listing for the addresses above
- [tools/grdscript/README.md](../../tools/grdscript/README.md): lint rules and builders
