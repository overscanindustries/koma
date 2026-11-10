# Building

Everything here runs on the host and never touches a camera, card or USB device.

## Setup

- Python 3.12.
- LLVM 20 (`llvm-mc`, the assembler for package builds). Found in this order: `$KOMA_LLVM`, Homebrew `llvm@20` (`/opt/homebrew/opt/llvm@20/bin` or `/usr/local/opt/llvm@20/bin`), `/usr/lib/llvm-20/bin`, then `llvm-mc` on `PATH`.
- `bsdtar`, only to read Ricoh's `gr-240.exe` directly (macOS ships `/usr/bin/bsdtar`; on Linux it is in `libarchive-tools`).

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`requirements.txt` pins capstone, numpy, pefile, pillow, pyusb, rawpy and unicorn. Optional and unpinned: `pytest` (the Koma tests have their own runner), `pyvirtualcam` (`komacam --vcam`), `opencv-python` (one bridge test), Playwright with Chrome (the browser end-to-end test, skipped when absent), `exiftool`, and libusb for any `--real` / `--live` USB mode.

## Firmware input

Every build tool (`tools/koma/koma`) command except `make-loader` reads Ricoh's 2.40 firmware, chosen in this order:

1. `--firmware PATH` (before or after the command).
2. `KOMA_FIRMWARE=PATH`.
3. The copy in this checkout, `firmware/extracted/GR DIGITAL Update/Firmware rel 2.40/qrflect3` (or `firmware/derived/grd240-armbe.bin`), when present.

PATH can be `qrflect3`, `gr-240.exe`, a zip holding `qrflect3`, or the bare payload (`qrflect3` without its 512-byte header). Every form is checked by SHA-256; anything else is refused. `koma smoke` needs the full `qrflect3`, not the bare payload. Provenance of the files under `firmware/`: [evidence](internals/evidence.md#firmware-inputs).

## The build tool

`tools/koma/koma` runs `python -m koma` from `.venv` with `tools/` on the path. The installed `koma` command runs the Koma app instead ([install](install.md#the-app)). In the table below, `koma` means `tools/koma/koma`.

| Command | Does |
|---|---|
| `koma list` | Every feature with version, summary, sites, settings, options; then the named sets |
| `koma build --features SET -o X.grmod` | Links a package; also writes `X.grmod.json` (sizes, records, hashes, version string) |
| `koma inspect X.grmod` | Decodes the file and checks every before-byte against 2.40 and the build id |
| `koma selfcheck X.grmod` | Static (capstone) and unit-emulator install, hook and negative checks; a few seconds |
| `koma smoke X.grmod` | One emulator boot of the loader image with the package, one shot; PASS/FAIL per feature; about 2 minutes |
| `koma package-delta STOCK X.grmod -o X.grmod.delta` | Encodes a package as Koma's bytes plus copies from 2.40, the form a release ships; `koma make-package STOCK --delta X.grmod.delta -o GRMOD.BIN` rebuilds it |
| `koma make-loader STOCK -o QRFLECT3` | Builds the first, card-loader-only Koma firmware image, also the input to the SDHC image (see [install](manual-install.md#build-and-flash-the-firmware)) |

A camera package:

```
tools/koma/koma build --firmware <2.40 file> --features koma04 --opt telemetry.cp15=off -o koma04.grmod
tools/koma/koma inspect koma04.grmod
tools/koma/koma selfcheck koma04.grmod
tools/koma/koma smoke koma04.grmod
```

`--opt telemetry.cp15=off` is required for every camera build (CP15 reads have frozen the camera at boot); `koma build` defaults to `cp15=on`. `--features` takes feature names, named sets, or both, comma-separated. `--opt FEATURE.OPTION=VALUE` is repeatable and the feature must be in the build. `--resident pool|static[:ADDR]` overrides where the resident block goes (`koma04` defaults to static at `0x3dae00`). `--source FILE` also writes the generated assembly.

Named sets:

| Set | Contents |
|---|---|
| `koma04` | The current set: menu, focus/exposure/display rows, looks, SELECT, GRAIN/VIGNETTE, preview, tether, live view, ksettings, peaking, `waitdiag` (temporary diagnostic), stall fix, settings rows, settings I/O, telemetry |
| `koma04-lite` | `koma04` without `ksettings`, `peaking`, `waitdiag` |
| `koma03`, `koma03io`, `koma03sio`, `koma03lv` | An earlier set (looks, GRAIN/VIGNETTE, preview) and its settings-I/O and live-view variants |
| `koma02`, `koma01` | Smaller, earlier sets |
| `drop1`..`drop3`, `probe` | Early drops and the color-register probe |

With no `--features`, `build` uses an old three-feature default (`tone-r3,snap15,recipe-slots`). `koma04w` (`koma04` plus `widefs` for SDHC/SDXC) is built by `analysis/sdhc/bundle/build.py`, because `widefs` is not a registered feature ([install](manual-install.md#install-koma)).

`smoke` boots the loader image with the package as `GRMOD.BIN`, formats the modelled memory through the native menu, takes one shot and stops after develop. Per feature it checks that the loader called the package, the install status is 1, every record is in RAM, every hook ran, and the feature's own checks pass. Hooks the shot cannot reach are reported as SKIP; unit tests cover them. A smoke pass is `EMU` evidence only.

## Package format

The container, the four record kinds and the install sequence are in [how it works](how-it-works.md#grmodbin). Resident code stays in memory. The installer, records, relocations and any `transient=True` feature are freed after install. The loader caps the whole body at 64 KiB.

## Writing a feature

A feature is `tools/koma/features/<name>.py` with `NAME`, `SUMMARY`, `OPTIONS` (name to help text), optional `ROWS`, and `make(opts)` returning a `Feature`. Register it in `REGISTRY` in `tools/koma/features/__init__.py`.

```python
from ..feature import Check, Feature, Hook

NAME, SUMMARY, OPTIONS = "my-hook", "counts stills", {}
ROWS = [dict(id="count", label="COUNT", options=["OFF", "ON"], byte=0x1b8ce, default=0, group="display")]

SRC = """
handler:
    push {r4, lr}
    adr r1, count
    ldr r2, [r1, #0]
    adds r2, #1
    str r2, [r1, #0]
    ldr r3, =next
    bl call_r3                 @ original 0x8ff0e, or the next feature's handler
    pop {r4}
    pop {r3}
    bx r3
call_r3:
    bx r3
.ltorg
.p2align 2
count: .word 0
"""

def make(opts):
    return Feature(NAME, "1.0", SUMMARY, source=SRC, options=opts,
                   hooks=[Hook(0x96436, 0x8FF0E, "handler", "next", "still develop")],
                   checks=[Check(0x8FF0E, bytes.fromhex("b5884929"), "callee prologue")])
```

This example links and installs cleanly in the unit emulator.

- Source is Thumb in llvm-mc syntax (ARMv4T, `arm7tdmi`, big-endian), position independent. Labels are private to the feature. Reach another feature's export as `$other.symbol` (list it in `requires`) and core symbols as `$koma.version`. Call firmware with `ldr r3, =0xADDR+1` then `bl call_r3`, never an absolute `bl`. Never use CP15 instructions.
- Hook handlers are entered like the original target (same r0-r3, stack and lr), call `next` with the original arguments, and preserve r4-r11.
- Patches carry before-bytes: `Patch(addr, before, after, why)`. A `Check(addr, before, why)` verifies bytes your code depends on and writes nothing.
- A menu row's byte must be in `0x1b8b5..0x1b8cf` and unused by other features; the row claims it, so do not declare it again as `Settings`. Labels are at most 16 characters (14 recommended), options at most 9, in the stock font (A-Z, 0-9, space and `!&'()+-./:*`). `default` indexes `options`. `group` is `recipe`, `recipe-edit`, `focus`, `exposure` or `display`; anything else goes to the OTHER page. Read the byte at the point of use and treat out-of-range as the default.
- For other settings bytes, `Settings(start, end, owned=True)` marks a private store. Writing a stock field needs `owned=False` plus `values`, the set of values stock itself writes.
- For smoke tests, optionally add a module-level `smoke_setup()` returning `{address: byte}` pokes made before the shot, and `smoke_checks(obs)` returning `[(name, ok, detail)]`, passed as `Feature(..., smoke=smoke_checks)`.

The linker refuses a build that breaks any rule in [build-time lint](how-it-works.md#build-time-lint).

`tools/koma/data/` holds tables the features read: a B&W tone table transcribed as numbers from a sibling Ricoh firmware, and look parameters derived from GR DIGITAL IV firmware tables.

### Unit tests for a feature

`tools/koma/unitemu.py` runs the real installer under Unicorn on the 2.40 payload, with firmware callees stubbed:

```python
from koma.linker import link
from koma.unitemu import Unit

u = Unit(link([my_hook.make({})]).module)
assert u.install() == 0 and u.callee_saved_ok
u.stub(0x8FF0E, "original")
```

`u.read(addr, n)` checks patched bytes; `u.run_site(start, end, regs)` executes stock code around a hook. Tests live in `tools/koma/tests/test_<name>.py`.

## Running the tests

```
KOMA_FIRMWARE=<2.40 file> .venv/bin/python tools/koma/tests/run_tests.py [-k text]
```

The Koma suite (about 445 tests, about 3.5 minutes) uses pytest when installed and its own minimal runner otherwise. In a clean clone two provenance tests fail because they need files that are not tracked.

The repository-wide checks go through one entry point, which sets `PYTHONPATH` itself:

```
.venv/bin/python tools/check.py list         # how every test file is classified
.venv/bin/python tools/check.py test         # fast suite
.venv/bin/python tools/check.py test-all     # everything, including slow tests that regenerate fixtures
.venv/bin/python tools/check.py verify       # read-only checks of retained evidence (see evidence)
.venv/bin/python tools/check.py playground   # browser playground of the emulated camera UI
```

`test` runs the fast unittest and script groups and writes no tracked file. `test-all` adds the slow groups: the Koma suite through `run_tests.py`, the komacam tests under `pytest` (install it first), and tests that rewrite fixtures under `analysis/verify-fixtures/` (byte-identical when nothing changed).

Some checks need evidence that is not in git (`analysis/camera-dumps/`, native proof reports). In a secondary worktree `check.py` reads them from the reference checkout (`REVERSE_REFERENCE_ROOT`, else the first `git worktree list` entry); in a plain clone those steps cannot run. See [evidence](internals/evidence.md).

## Other tools

Research tools under `tools/` (emulator, UI playground, patch builder, card-script linter, look prototyping) each have a README in their folder; [internals 10](internals/10-emulator-and-tooling.md) gives the overview. Host tools that talk to the camera are in [host tools](host-tools.md).

Decode a card battery log with `python3 tools/koma_battlog_decode.py KOMABATT.TXT`.
