# Writing features

Koma changes the camera in two steps. A small loader is flashed into the firmware once. After that, every feature arrives in one package file, `GRMOD.BIN`, read from the memory card or from the camera's internal `/IROM` storage. Each change in the package names the exact bytes it replaces, so a package built for the wrong firmware applies nothing.

Addresses are firmware 2.40 runtime addresses. Tags appear only where trusting an unverified fact could hurt the camera: `HW` was seen on a camera, `EMU` ran only in an emulator, `STATIC` comes from reading the code, `INFERRED` is a guess from indirect evidence. See [firmware.md](firmware.md) for the firmware, [settings-and-image.md](settings-and-image.md) for the settings block, [usb.md](usb.md) for USB commands and [addresses.md](addresses.md) for more addresses.

## How the loader hooks in

At boot, `MainCtrl` (the main application task) runs the update check at `0x1e7ac`, then calls `0x282cc` at `0x281ee`. The flashed loader redirects that one `bl`. Nothing of Koma runs before the update gate at `0x1eb22`, so the keyed update path still works against a broken package.

The loader sits at `0x57f78` (344 bytes) inside the Dcfs self-test island `0x56f80..0x58800`, which no other code references. The flash image cannot grow, so flashed code goes into dead space like this.

| Step | What happens |
|---|---|
| 1 | Calls the original `0x282cc`, so stock startup completes |
| 2 | Waits up to about 2.5 s for the card-probe flag `0x1bd3a8`. A slower probe skips the module silently |
| 3 | Opens `/ATA1/GRMOD.BIN`. If that fails (Koma 2 loader only), mounts `/IROM` with `0x49384(0)` and opens `/IROM/GRMOD.BIN` |
| 4 | Validates the 20-byte header and reads the body into KernPool (`0x2bf81c`), then checks the checksum |
| 5 | Calls `body + entry + 1` (Thumb) with a 48-byte service table at `0x5809c` |
| 6 | Frees the transient part when the module returns |

Every rejection frees what it took and returns to stock. The loader mounts `/IROM` itself because tFileTask (the file-system task) mounts it only when SD init fails. `0x1fb200` = 1 means mounted.

Koma 1 reads only `/ATA1/GRMOD.BIN`, and the card must be SDSC (2 GB or smaller). Koma 2 adds an SD 2.0/SDHC/SDXC driver and the `/IROM` fallback. A JPEG's MakerNote tag 2 shows which image runs: `Rev024M` (Koma 1), `240K2` (Koma 2), `Rev0240` (stock). `-VER` and the version screen read 2.40 in all cases.

| Situation | Result |
|---|---|
| Card has a `GRMOD.BIN` that opens | The card package wins, even if invalid (then stock boots, with no `/IROM` try) |
| No card file, or no card | The `/IROM` package runs |
| 64 GB SDXC card | The card is not mounted at loader time, so the card file is never read and `/IROM` is used. With a large card the package must be in `/IROM` |
| No package, or a corrupt file | Stock boot |

A package reaches `/IROM` only through a standalone card module. It checks the package with the loader's own rules, removes the old `/IROM/GRMOD.BIN`, copies the new one, reads it back and deletes a bad copy. Every `/IROM` access must be gated on `0x1fb200` = 1, because an unmounted `/IROM` accepts a copy that never reaches NAND. Delete the card `GRMOD.BIN` afterwards, because it hides the `/IROM` one. A firmware flash does not touch `/IROM` (flash blocks `0x54+`), and stock 2.40 ignores the file.

## Package format

A 20-byte header followed by the body, at most `0x10000` bytes.

| Field | Rule |
|---|---|
| Magic | `GRMD` |
| Format | 1 |
| Checksum | `c = rol(c, 1) + word` over the body |
| Entry | Offset from body start. The loader calls `body + entry + 1` |

The body holds an installer, the resident code and tables, a relocation table and the records. The installer verifies every before-byte before writing any, then applies the records.

| Record | Effect |
|---|---|
| `DATA` | Writes `after` over `before`. Code or data |
| `CHECK` | Writes nothing. Asserts bytes your code depends on (a callee's prologue, a table) |
| `BL` | Rewrites a Thumb `bl` at a site to point at your handler |
| `PTR` | A 4-aligned word becomes the resident address of a label (+1 if Thumb) |

Resident code, tables and the manifest stay until power-off. The installer, the records, the relocation table and any init-only (transient) code are freed after install.

| Memory | Value |
|---|---|
| KernPool | `0x36adec..0x3dadec`. Package bodies load here. Largest free block was 3.9 KB with a 29.8 KB body resident |
| Static resident block | `0x3dae00`, in the gap `0x3dadec..0x422800` above KernPool that no 2.40 literal points into, followed by a 16-byte canary. Leaves 23.3 KB free in the pool. The gap is known free only for the boot window the canary covers (`INFERRED` beyond that) |

## Calling firmware functions

The core is an ARM7TDMI-class CPU (ARMv4T) in SVC (supervisor) mode. Arguments go in r0 to r3, then the stack, and the result is in r0. The callee preserves r4 and up. Data and literal pools are big-endian.

- There is no `blx`. Return with `pop {r3}; bx r3`. Calling ARM code needs `bx` through a register.
- A plain label has no Thumb bit. Write `label + 1` for a function pointer.
- Thumb `bl` reaches about 4 MB. Call firmware with `ldr r3, =0xADDR+1` then `bl call_r3` (where `call_r3: bx r3`), or use the service table.
- Island functions can start 2 mod 4. Add a pad halfword to keep literal pools aligned.

A minimal wrapping hook counts stills at the first develop pass (`bl 0x8ff0e` at `0x96436`). The package adds a `CHECK` of the callee's prologue (`b5884929` at `0x8ff0e`). `0x8ff0e` takes one argument, the mono flag in r0, and writes r1 to r3 before it reads them, so the handler keeps r0 and may use r1 to r3 as scratch.

```asm
handler:
    push {r4, lr}              @ r0 = mono flag, passed on unchanged
    adr r1, count
    ldr r2, [r1, #0]
    adds r2, #1
    str r2, [r1, #0]
    ldr r3, =next              @ the original 0x8ff0e + 1, or the next hook in the chain
    bl call_r3
    pop {r4}
    pop {r3}
    bx r3
call_r3:
    bx r3
.ltorg
.p2align 2
count: .word 0
```

| Function | Address | Notes |
|---|---|---|
| File open / read / write / close / unlink | `0x55dc0` / `0x56024` / `0x560ec` / `0x55f0c` / `0x55d80` | `/ATA1` card, `/IROM` internal. At most 2 files open. `0x55d80` has no mount gate, so delete only a path that just opened |
| `/IROM` mount | `0x49384(0)` | Sets `0x1fb200` = 1 |
| Pool alloc / free | `0x15f024(0x2bf81c, &ptr, size, 0)` / `0x15f084` | Returns 0 on success. Never use the wrapper `0x6128a` |
| Key submit | `0x324e6` | Posts message `0x7103`. Codes: 6 POWER, 7 PLAY, 15 TELE, 16 WIDE, 19 MENU, 23 ENTER, 26 RL1, 27 RL2 |
| LUT select and upload | `0x8ffc8(selector, arg)` | Uploads a tone table to CC1/CC2 |

### Where to hook

| Goal | Site | Notes |
|---|---|---|
| Per-shot register writes, first develop pass | Wrap the call at `0x96436` (target `0x8ff0e`) | Runs after stock wrote its values |
| Per-shot writes, second develop pass | Wrap at `0xafd3e` | Values set only in pass 1 are lost when pass 2 rewrites them |
| Shot tone-table upload | Capture route `0x9733a`, `0x90278`, `0x90408`, `0x16d31c` | The boot route `0x985a0`, `0x92050`, `0x8ffc8` feeds the live preview, not the shot |
| Key remap | Key funnel `0x517dc` | Runs before the update check. Guard with a flag set from the `0x281ee` hook |
| Key dispatch | `0x8a814`, case `0x7103` | Runs on tOpeMain (priority `0x50`), which can run before the loader hook. Do nothing until a hook-set flag is seen |
| USB vendor commands | Command table `0x1fb400`, `-CCTL` handler, app-state resolver `0x4fd1e` | See [usb.md](usb.md) |

A resident module sits within 3.9 MB of `.text`, so it can redirect any stock call site directly.

Do not hook these, which run before the gate or are the update path: reset `0x1cf58`, `0x1e444`, `0x1e7ac`; task creation `0x28800`; USB descriptor and bring-up code; the settings loader `0x3d4a4`; and `0x5d18c`, `0x27400`, `0x276d8`, `0x275cc`, `0xd6dec`, `0xd6c58`, `0x63b88`, `0x63c80`.

## Safe patterns

- Wrap a `bl` rather than replace it. A hook is entered exactly as the original target (same r0 to r3, stack and lr), calls the next link with the original arguments, and preserves r4 to r11. Argument registers must reach the next link unchanged. Registers the target does not take as arguments are scratch. Several hooks on one site chain, and the original runs once. An exclusive `bl` redirect cannot chain.
- Prefer a data patch over code, and a hook over a redirect.
- Add a `CHECK` for every firmware function or table your code relies on.
- Patch only code that first runs after your package loads. Code fetched earlier could sit in a stale instruction-cache line.
- Keep per-shot work in the develop hooks and boot work in the installer.
- If a package writes a stock settings field, give it a list of stock-valid values so stock code always finds one it accepts. Private bytes belong in the unused settings tail (see [settings-and-image.md](settings-and-image.md)).
- Test from an SDSC card first and install to `/IROM` last. Pulling the card is the undo.
- To replace a tone table, upload your own on the capture route and invalidate the selector cache (`0x1f4db0`/`0x1f4db3`) afterwards. Replacing a stock table (4096 big-endian u16 values at `0x193b30 + k * 0x2000`) also changes the live preview and every mode that selects it. After a custom upload the stock preview upload leaves the cache holding your id, and the next shot can skip its own upload.

Menu labels use only stock font glyphs (16 characters, 9 for option strings). The emulator has no lens, sensor or second CPU, so check visual changes on a camera.

## Known hazards

| Hazard | Detail | Tag |
|---|---|---|
| Reading CP15 hangs the core | `MRC p15` hangs the core (lens extends, screen never comes on). The firmware contains no CP15 instruction. Never execute one. The `koma` build tool defaults its `telemetry.cp15` option to on, so pass `--opt telemetry.cp15=off` for every camera build | `HW` |
| Early code sites | Never patch a 32-byte code line that runs before the loader hook at `0x281ee`. It may sit in a stale instruction-cache line, and nothing may change what runs before the update gate | `STATIC` |
| Flash writes | A package must never write flash, the adjustment store or the Monitor blocks, and must never call the settings save `0x4949c`. A package that writes flash cannot be undone by removing it. Only the dedicated installer writes `/IROM`, and only the package file | `STATIC` |
| Calibration byte `0x08010431` | `0xFF` leaves `MainCtrl` spinning in `0x3ec14` before the update gate. Info stays valid, so no recovery is known. Never use `adjcom`, the script `V`/`W` engine or `qrflect7` edits from a module. Calibration values mirrored from `0x08010000` are read by many routines, so never write them | `EMU` |
| Hooks before the update gate | A crash there disables every recovery path | `STATIC` |
| Flash image growth | Added bytes land in `.bss` (zeroed at `0x1c0d8`). The size refusal (`0x2f0000`) comes after the info erase at `0x63c68`, the point of no return | `STATIC` |
| Pool allocation | The wrapper `0x6128a` is fatal on an empty pool (`KERNEL MEMORY EMPTY`). Budget in kilobytes | `STATIC` |
| LCD mutex `0x2c02ec` | Leaks on the capture path. A second tether session 5 to 12 s after the first stalled about 10 s | `HW` |
| Large cards | The stock file system never mounts an SDHC/SDXC card, so a card package cannot load from one. The update check runs before any package, so such a card cannot carry an update either | `HW` |
| Low battery during flashing | Not detected (polling is off once flashing starts). It becomes an interrupted update | `EMU` |
| `-NLST` on a card root | On a 64 GB FAT32 card `-NLST /EXT0` answers `150` and never sends data. Subdirectories and `RETR` work | `HW` |

## Flashing the loader

A flash image is needed only for what a module cannot do, such as running before the card is probed. Flashing is the one step with brick risk.

- Keep the image exactly `0x219b68` bytes and base it on the official 2.40 `QRFLECT3` (SHA-256 `de00e03d563fd1bdd0a2a925761b1a029993425049841119e3e83eef990c783b`).
- Container fields that must stay correct: type 2 at `+0x10`, format `1` at `+0x11`, version `0240` at `+0x12`, zero version bounds at `+0x14` and `+0x16`, load address `>= 0x1c000` with load plus length at most `0x422800` at `+0x20`/`+0x24`, total size 2,202,472 at `+0x2c`, tail `R_MG1_UPDATED!!!` at `+0x1f0` (a wrong tail flashes, then reads invalid). The weighted checksum at `+0x30` is `sum(byte * (2 + (load + i) % 6)) mod 2^32` over the payload.
- Procedure: charged battery (it is checked only before flashing), a 2 GB SDSC card holding only the one `QRFLECT3`, camera off, no USB cable, mode dial on Movie. Hold UP + TELE + PLAY, release all three, press RIGHT (Yes), press MENU/OK. The green lamp blinks 2 to 3 minutes. Never cut power or remove the card while it blinks. Check a JPEG's MakerNote tag 2 afterwards.

A failed flash shows screen `0x1a` for about 10 s and powers off, which looks like success. Do the first flash of any new image on a spare body. Recovery from a flash that does not boot has not been tested on a camera. Treat a bad flash as potentially fatal.

## Recovery

Recovery is what the code and the emulator predict unless the tag is `HW`.

| Failure | How it shows | Recovery | Tag |
|---|---|---|---|
| Card package hangs | Lens out, screen never on | Pull the card. Stock boots | `HW` |
| `/IROM` package hangs | Same, and pulling the card does not help | Boot once with an SDSC card holding an empty `GRMOD.BIN` (the card file wins, then stock boots). A 64 GB card cannot do this | `EMU` |
| Remove the `/IROM` package | n/a | SDSC card with the installer module as `GRMOD.BIN` and an empty `KOMARM.TXT`. Boot about 60 s. The log reads `result=removed` | `EMU` |
| Power cut during an update, after the info erase | Monitor finds invalid info | Official `QRFLECT3` on the card. All 35 modelled cut points recovered (see below) | `EMU` |
| Invalid info and no card | Camera looks dead. After about 5,000 ticks the Monitor enters a UART console (38,400 baud, pins unknown) | Insert a card with the official file and cold-boot. Flash is unchanged | `EMU` |
| Corrupt body under a valid header | Nothing: the payload checksum is not checked at boot | Keyed update with the official file, if the gate `0x1eb22` is reached | `EMU` |
| Hang after the update gate (bad flashed hook) | Boot stops | Keyed update (UP + TELE) with the official file | `EMU` |
| Hang before the update gate, valid info | Boot stops | None established | `STATIC` |

### The Monitor card RAM loader

The Monitor is the first-stage boot code in flash block 0. On a normal boot it checks that the main-image info (block `0x3c`) has the right magic, the updated marker at `+0x1f0` and type 2 or 3, then copies the payload to RAM and jumps to `0x1c000`. It does not check the payload checksum.

If the info is invalid, the Monitor scans `/ATA1/QRFLECT0..9`, validates a card file (size, magic, format, range, weighted checksum, type 2, 3 or 7), stages it at `0x08000000 + load` and runs it from RAM. That official image's own updater then reinstalls it from the same file. This path has never run on a real camera, and no supported user action triggers it on a healthy body. Treat it as a safety net, not a procedure.

### Interrupted update

The type-2 update erases main info first, programs the body (blocks `0xd..0x2e`) and writes info last. A cut after the info erase leaves info invalid, so the Monitor will not run a partial body, and in the model every cut recovers through the card path. If the interrupted file was a modified image, swap the card to the official file.

### Restoring stock

Flash the official 2.40 `QRFLECT3` alone with the keyed update, the same keystrokes as an install. Over Koma 1 this booted normally with tag `Rev0240` and kept the photos (`HW`, once). Over Koma 2 it is `EMU` only. A package left in `/IROM` stays there; stock ignores it, and a later Koma 2 flash would load that old copy again. Never restore with `QRFLECT7`: it rewrites the adjustment store (block `0x3d`), and a tear there cannot be repaired.
