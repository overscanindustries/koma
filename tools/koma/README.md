# koma

`tools/koma` builds Koma. It links features into the single package the camera's loader reads from the card root (`GRMOD.BIN`), and it builds the loader firmware image from your own stock 2.40 file. It runs on the computer only and touches no camera, card or USB device. The walkthrough is [`docs/building.md`](../../docs/building.md), and flashing is in [`docs/install.md`](../../docs/install.md).

## Setup

Every command except `make-loader` reads your Ricoh 2.40 file, given as `--firmware PATH` or `KOMA_FIRMWARE=PATH` ([`firmware/README.md`](../../firmware/README.md)). PATH can be `qrflect3`, Ricoh's `gr-240.exe`, a zip holding `qrflect3`, or the bare payload. `smoke` needs the full `qrflect3`. The assembler is LLVM 20, found through `KOMA_LLVM` (the directory with `llvm-mc`), Homebrew `llvm@20` or `PATH`. Reading `gr-240.exe` needs `bsdtar`.

## Commands

From the repository root:

```
tools/koma/koma list
tools/koma/koma build --features koma04 --opt telemetry.cp15=off -o koma04.grmod
tools/koma/koma inspect koma04.grmod
tools/koma/koma selfcheck koma04.grmod [--json report.json]
tools/koma/koma smoke koma04.grmod [--feature NAME] [--json report.json]
tools/koma/koma make-loader <qrflect3 | gr-240.exe | zip> -o QRFLECT3
```

| Command | Does |
|---|---|
| `list` | Lists features, options and named sets |
| `build` | Links the features and writes the package plus `OUT.json` (sizes, sha256, version, build id) |
| `inspect` | Decodes a package and checks every before-byte against 2.40 |
| `selfcheck` | Static and unit-emulated install, shot and negative checks |
| `smoke` | One emulator boot and one shot per feature (about 2 minutes) |
| `make-loader` | Builds the Koma 1 firmware image (stock 2.40 plus the package loader, tag `Rev024M`) |

`--features` takes feature names or a named set. `--opt FEATURE.OPTION=VALUE` can be repeated. Camera builds must pass `--opt telemetry.cp15=off`, because reading CP15 hangs the camera at boot. Copy the package to the card root as `GRMOD.BIN`.

`make-loader` accepts only the stock `qrflect3` and writes its output only if the sha256 matches the known image.

## Koma 2 (SDHC and SDXC cards)

```
.venv/bin/python tools/koma/firmware2/build.py <qrflect3 | gr-240.exe | zip> -o QRFLECT3 [--report build.json]
KOMA_FIRMWARE=<2.40 file> .venv/bin/python tools/koma/sdhc/build_koma04w.py -o koma04w.grmod
.venv/bin/python tools/koma/sdhc/irominstall.py -o OUTDIR
```

| Script | Builds |
|---|---|
| `firmware2/build.py` | The Koma 2 firmware image (tag `240K2`): Koma 1 plus the SDHC driver (`firmware2/sdhc_patch.py`) and a loader fallback to `/IROM/GRMOD.BIN`. Written only if its sha256 is the known Koma 2 image |
| `sdhc/build_koma04w.py` | `koma04w.grmod`: `koma04` plus `widefs`, the full-capacity FAT32 for SDHC/SDXC cards (`sdhc/widefs_feature.py`) |
| `sdhc/irominstall.py` | `irominstall.grmod` and `irodump.grmod`, which copy a package from the card into internal memory (`/IROM`), and `usbmass.grmod` |

## Tests

```
KOMA_FIRMWARE=<2.40 file> .venv/bin/python tools/koma/tests/run_tests.py [-k text]
```

The tests run the real installer under Unicorn. `tools/check.py test-all` runs them too.

## How a package works

The installer checks every before-byte first, so a mismatch writes nothing. Then it relocates, runs the feature inits and applies all records with IRQs masked. The loader caps the package at 64 KiB.

## Writing a feature

A feature is `features/<name>.py` with `NAME`, `SUMMARY`, `OPTIONS` and `make(opts) -> Feature`, registered in `features/__init__.py`. [`docs/internals/writing-features.md`](../../docs/internals/writing-features.md) has a worked example. The main `Feature` fields (`feature.py` has the rest):

| Field | Effect |
|---|---|
| `patches=[Patch(addr, before, after, why)]` | Writes `after` over `before` |
| `pointers=[Pointer(addr, before, label, thumb, why)]` | A 4-aligned word becomes the resident address of `label` |
| `redirects=[Redirect(site, before, handler, why)]` | Exclusive `bl` rewrite, no chaining |
| `hooks=[Hook(site, target, handler, next, why)]` | Wraps a Thumb `bl target`. Features on one site chain in build order |
| `source`, `checks`, `settings`, `requires`, `conflicts` | Thumb assembly (llvm-mc, big-endian, position independent), checked bytes, settings rows, dependencies |

Labels in feature source are private. Reach another feature's export as `$other.symbol` (and list it in `requires`) and core symbols as `$koma.version`. Call firmware with `ldr r3, =0xADDR+1` then `bl call_r3`, never an absolute `bl`. A hook handler calls `next` with the original arguments and preserves r4-r11.

Settings rows use bytes `0x1b8b5..0x1b8cf`. Labels are at most 16 characters and options at most 9.

## What the linker refuses

`link()` reports every problem at once: overlapping writes, before-bytes that disagree with 2.40, and packages over 64 KiB. It also refuses code that calls a flash or store-write routine (`linker.FORBIDDEN_CALLS`) and any patch on a line that runs before the loader hook at `0x281ee`.

## Layout

| Path | Holds |
|---|---|
| `features/` | The features |
| `tests/` | Unit tests |
| `emu/` | The GR DIGITAL 2.40 emulator (`grdemu`) and its UI session layer (`grdui`), used by `smoke`, the tests and `komabridge --emu` |
| `vendor/` | Builders reused by some features |
| `data/` | Tone and look tables |
| `firmware2/`, `sdhc/` | Koma 2 |
