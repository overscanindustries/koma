# Koma module build system (`tools/koma/`)

The module loader reads exactly one `GRMOD.BIN` from the card root. Koma links any set of features into that one file: one installer, one all-or-nothing apply, one resident block, and one manifest with a version string the menu can show.

The tool runs on the host only and never touches a camera, card or USB. For a walkthrough see [`docs/building.md`](../../docs/building.md); for flashing, [`docs/install.md`](../../docs/install.md).

## Your firmware

Koma's source holds no Ricoh bytes. Every command except `make-loader` reads a Ricoh 2.40 file, given by `--firmware PATH` (before or after the command) or, failing that, `KOMA_FIRMWARE=PATH`. With neither set, the command stops with an error. PATH can be `qrflect3`, Ricoh's `gr-240.exe`, a zip holding `qrflect3`, or the bare payload; each is checked by sha256. `smoke` needs the full `qrflect3`, not the bare payload. The unit tests need `KOMA_FIRMWARE` too.

The assembler is LLVM 20. It is found through `KOMA_LLVM` (the directory holding `llvm-mc`), then Homebrew `llvm@20`, then `PATH`.

## Commands

`tools/koma/koma` runs `python -m koma` from `.venv` with `tools/` on the path. From the repository root:

```
tools/koma/koma list
tools/koma/koma build --features koma02 --opt telemetry.cp15=off -o koma.grmod
tools/koma/koma inspect koma.grmod
tools/koma/koma selfcheck koma.grmod [--json report.json]
tools/koma/koma smoke koma.grmod [--feature NAME] [--json report.json]
tools/koma/koma make-loader <qrflect3 | gr-240.exe | zip> -o QRFLECT3
KOMA_FIRMWARE=<file> .venv/bin/python tools/koma/tests/run_tests.py [-k text]
```

| Command | Does |
|---|---|
| `list` | Every feature with version, summary, patches, hooks, settings, options, and the named sets |
| `build` | Links the features and writes the module plus `OUT.json` (sizes, records, sha256, version string). `--source FILE` also writes the generated assembly; `--no-publish` skips the manifest pointer at `0x58190`. `QUICKVER.UP` is refused as an output name |
| `inspect` | Decodes a module and checks every before-byte against 2.40 and the build id |
| `selfcheck` | Static (capstone) and unit-emulated install, shot and negative checks on a finished module |
| `smoke` | One emulator boot and one shot; PASS or FAIL per feature. About 2 minutes, simulated, not hardware evidence |
| `make-loader` | Builds the generation 1 image from your stock 2.40 file; needs no `--firmware` |

### Build options

`--features` takes feature names or a named set. `--opt FEATURE.OPTION=VALUE` is repeatable and the feature must be in the list. Camera builds must pass `--opt telemetry.cp15=off`: reading CP15 hangs the camera's core at boot. Copy the module to the card root as `GRMOD.BIN`.

### Features

Features are registered in `features/__init__.py`; `koma list` prints them with their options. They include `menu5`, `snapdist`, `fullpress`, `fastkeys`, `autoiso`, `longexp`, `hlalert`, `bwrecipes`, `selcolour`, `fx`, `preview`, `peaking`, `ksettings`, `settingsio`, `tether`, `liveview`, `stallfix`, `rootlist` (FAT32 card-root listing fix), `clockkeep` (restores a lost clock from `/ATA1/KOMATIME.TXT`, transient), `waitdiag`, `telemetry`, `adjdiag` (diagnostic), plus `tone-r3`, `snap15`, `recipe-slots` (older, superseded) and `colourprobe` (hardware probe).

### Named sets

`koma04` and `koma04-lite` (the full package, which includes `rootlist` and `clockkeep`, and a smaller variant), `koma03`, `koma03sio`, `koma03io`, `koma03lv` (the same set with settings I/O and live view variants), `koma03clock` (koma03 with `clockkeep` before `telemetry`; add `--opt telemetry.clock=on` for the KOMALOG `CLOCK` line), `koma02`, `koma01`, `drop1`, `drop2`, `drop3` (earlier drops; `drop3` equals `koma01`), and `probe` (`colourprobe` plus `telemetry`, not a package). `koma list` shows each set's members. With no `--features`, `build` uses the default `tone-r3,snap15,recipe-slots`; every build stamps a version string that ends in its build id, whatever the feature list.

### Loader image

`make-loader` accepts only the stock 2.40 file (`qrflect3`, 2,202,472 bytes). It adds the 344-byte module loader at `0x57f74`, the hook at `0x281ee` and the `Rev024M` marker, recomputes the container checksum, and writes the output only if its sha256 matches the known generation 1 image. Reading the `.exe` needs `bsdtar`.

## What a build contains

```
body 0x00   'KMOD' boot header: offsets of records / inits / resident segment / relocations
     0x20   installer (Thumb, transient)
            records, each with its before-bytes (transient)
            init table (transient)
     O_RES  resident segment: manifest ('KOMA', build id, status, version string, feature table),
            then each feature's code, tables and blobs
            relocation table (transient)
```

The installer checks the ABI and image version, verifies every before-byte first (a mismatch writes nothing), allocates the resident block, range-checks the hooks, relocates, runs the inits, then applies all records with IRQs masked and sets manifest status 1. It always returns 0 so the loader frees the transient part. The module body is capped at 64 KiB by the loader.

The build id is the first 8 hex digits of the sha256 of the body with the id fields zeroed. It and the version string (`KOMA V1.0.0 1A2B3C4D G1`: the package release `linker.KOMA_VERSION`, the build id, and the internal minimum Koma firmware generation `linker.MIN_FIRMWARE_GEN`) are in `OUT.json` as `version_string`, `version_short`, `menu_version` (the KOMA menu row, `2.40K1.0`), `photo_tag` (`240K1.0`), `min_firmware_gen` and `build_id`; a feature reads the string through `$koma.version`. See [`docs/versioning.md`](../../docs/versioning.md).

## Writing a feature

A feature is `features/<name>.py` with `NAME`, `SUMMARY`, `OPTIONS` (option name to help text), optional `ROWS` and `make(opts) -> Feature`. Register it in `features/__init__.py`. [`docs/building.md`](../../docs/building.md) has a worked example. Fields of `Feature` (`feature.py`):

| Record kind | Feature field | Effect |
|---|---|---|
| Patch | `patches=[Patch(addr, before, after, why)]` | Writes `after` over `before`, on code or data |
| Pointer | `pointers=[Pointer(addr, before, label, thumb, why)]` | A 4-aligned word becomes the resident address of `label` (+1 if `thumb`) |
| Redirect | `redirects=[Redirect(site, before, handler, why)]` | Exclusive `bl` rewrite at a site; no chaining |
| Hook | `hooks=[Hook(site, target, handler, next, why)]` | Wraps a Thumb `bl target`; several features on one site chain in build order and the original runs once |

Other fields: `checks=[Check(addr, before, why)]` (bytes your code depends on; verified, never written), `source` (Thumb asm, llvm-mc syntax, arm7tdmi, big-endian, position independent), `init`, `blobs`, `settings=[Settings(start, end, owned, why, values)]`, `exports`, `requires`, `conflicts`, `transient=True` (init-only feature freed after install), and `smoke` with module-level `smoke_setup()`.

Source rules: labels are private to the feature; reach another feature's export as `$other.symbol` (list it in `requires`) and core symbols as `$koma.version`, `$koma.manifest`, `$koma.status`. Call firmware with `ldr r3, =0xADDR+1` then `bl call_r3`, never an absolute `bl`. A hook handler is entered like the original target, calls `next` with the original arguments, and preserves r4-r11.

Settings rows use bytes `0x1b8b5..0x1b8cf` of the persisted settings block. A row's `label` is at most 16 characters (the linker warns over 14), each option at most 9, `default` indexes `options`, and only stock-font glyphs are allowed. `owned=False` marks a stock field you write and needs `values`.

## What the linker enforces

`link()` refuses a build and lists every problem at once. Besides the conflict checks (overlapping writes, before-bytes that disagree with the 2.40 image or each other, a hook site that is not `bl target`, dependency and conflict declarations, the 64 KiB limit), it lints:

- Flash and store writes: source that loads or holds the address of a flash or store-write routine (`ldr =`, `.word`, `.equ`), a patch whose bytes form a `bl` to one, or a hook that wraps a call to one. The list is `linker.FORBIDDEN_CALLS`.
- Early sites: any patch, hook, redirect or pointer on a 32-byte line executed before the loader hook `0x281ee`. The line set is `data/phase-a-lines32.json`.
- Stock writes without values: `Settings(owned=False)` with no `values`.

## Layout

`features/` the features. `vendor/` assembler wrapper, module writer and menu builder code. `data/` tables the features read. `emu/` the emulator pieces `smoke` drives; the image and card files stay in memory, so no `GRMOD.BIN` or image file is written to disk. `tests/` the unit tests, which run the real installer under Unicorn on the 2.40 payload.
