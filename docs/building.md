# Building

Everything here runs on the computer and never touches a camera, card or USB device.

## Setup

You need Python 3.12 and LLVM 20. The tools find `llvm-mc` through `$KOMA_LLVM`, Homebrew `llvm@20`, `/usr/lib/llvm-20/bin`, then `PATH`. `bsdtar` is needed only to read `gr-240.exe`. The USB tools also need libusb.

On a Mac, `brew install python@3.12 llvm@20 libusb` covers these. On Debian or Ubuntu, install `llvm-20`, `libusb-1.0-0` and `libarchive-tools` (for `bsdtar`).

```
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Every command except `make-loader` reads your Ricoh 2.40 file from `--firmware PATH` or `KOMA_FIRMWARE=PATH`, or from the paths in [firmware/README.md](../firmware/README.md). PATH can be `qrflect3`, `gr-240.exe`, a zip holding `qrflect3`, or the bare payload. `smoke` needs the full `qrflect3`.

## Commands

```
tools/koma/koma list
tools/koma/koma build --features koma04 --opt telemetry.cp15=off -o koma04.grmod
tools/koma/koma inspect koma04.grmod
tools/koma/koma selfcheck koma04.grmod
tools/koma/koma smoke koma04.grmod
```

`list` shows every feature, its options and the named sets. `build` links a package and writes `OUT.json` beside it. `inspect` decodes a package and checks its expected bytes against 2.40. `selfcheck` runs the installer in a small emulator in a few seconds. `smoke` boots the firmware with the package in the full emulator and takes one shot, in about 2 minutes.

`--features` takes feature names, named sets or both, comma-separated. `--opt FEATURE.OPTION=VALUE` can be repeated. Camera builds must pass `--opt telemetry.cp15=off`, because reading CP15 hangs the camera.

The firmware images and the Koma 2 package have their own scripts, listed in [install](install.md) and [tools/koma/README.md](../tools/koma/README.md).

`komabridge.py --emu` runs the Koma Setup page against the firmware in the emulator. The emulator does not model the sensor, the LCD, the second processor or real timing, so a pass there does not prove a feature works on a camera.

## Adding a feature

A feature is `tools/koma/features/<name>.py` with `NAME`, `SUMMARY`, `OPTIONS` and `make(opts)` returning a `Feature`. Register it in `REGISTRY` in `tools/koma/features/__init__.py`. The linker refuses a package that breaks the rules and lists every problem at once. [Writing features](internals/writing-features.md) has a worked hook, the rules, and the firmware calls you can use.

## Tests

```
KOMA_FIRMWARE=<2.40 file> .venv/bin/python tools/koma/tests/run_tests.py [-k text]
.venv/bin/python tools/check.py test
KOMA_FIRMWARE=<2.40 file> .venv/bin/python tools/check.py test-all
```

The first runs the Koma tests. `check.py test` runs the fast suite without firmware, and `test-all` runs everything. Put tests for a feature in `tools/koma/tests/test_<name>.py`.
