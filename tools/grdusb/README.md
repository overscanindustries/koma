# grdusb

grdusb is a small client for the GR DIGITAL in ORIGINAL USB mode. It lists devices, reads the version and card information, lists and downloads files, and can stage Ricoh's own update files on the card. The bridge (`tools/komaset/bridge`) is built on it. The protocol is described in [internals: USB](../../docs/internals/usb.md).

## Run

Run from the repository root. It needs `pyusb` in `.venv` and `libusb` (Homebrew on a Mac). Commands that touch a camera need `--live`, or `--mock` for the simulated camera. `--dry-run` prints the exact requests and sends nothing.

```
.venv/bin/python -m tools.grdusb list                                  # USB descriptors only, no camera request
.venv/bin/python -m tools.grdusb --live ver                            # -VER
.venv/bin/python -m tools.grdusb --live info [--include-internal]      # -GCID, -GSID, -TIME, -DCAP
.venv/bin/python -m tools.grdusb --live ls /EXT0/DCIM                  # -NLST
.venv/bin/python -m tools.grdusb --live get /EXT0/DCIM/100RICOH/RIMG0001.JPG photo.jpg   # RETR
.venv/bin/python -m tools.grdusb --live get-many /EXT0/DCIM/100RICOH outdir
```

Every real run writes a JSON-lines transfer log, `grdusb-<command>-<UTC time>.jsonl`, or the file given by `--transfer-log`.

## Options

| Option | Meaning |
|---|---|
| `--live`, `--mock` | A real camera, or the simulated one |
| `--dry-run` | Print the request plan and send nothing |
| `--require-version VV.VV` | Send `-VER` first and stop with exit 2 unless the version matches. Repeatable |
| `--require-camera-id ID` | Also check `-GCID`. Needs `--require-version` |
| `--allow-internal` | `get` and `get-many` may read `/IROM`. Needs the version gate |
| `--transfer-log FILE` | Where the transfer log goes |

## stage-update

`stage-update` copies Ricoh's stock 2.40 update files from `firmware/` (see `firmware/README.md`) to the card and reads them back byte for byte. It writes card files only and flashes nothing. The camera flashes later, and only after Ricoh's keyed power-on.

```
.venv/bin/python -m tools.grdusb --live --require-version 02.40 --require-camera-id <GCID> \
    stage-update --i-understand-this-writes-the-card [--set official-2.40-pair|official-2.40-qrflect3]
```

The file hashes are pinned and no other file can be passed. The set `official-2.31-qrflect3` stages Ricoh's 2.31 `qrflect3` from `firmware/original/history/gr-231-win-en/x/GR DIGITAL Update/Firmware rel 2.31/` and needs `--allow-older-update`. It first deletes any `QRFLECT0` to `QRFLECT9` on the card, because the camera flashes every one it finds.

## What it refuses

- Any device that does not match the camera's ORIGINAL-mode descriptors.
- Any command outside `-VER`, `-GCID`, `-GSID`, `-TIME`, `-DCAP`, `-NLST` and `RETR`. Only `stage-update` adds `DELE` and `-STOR`, for fixed card paths.
- `/IROM` reads without the version gate, and bad or relative paths.

A timeout, malformed frame or short write stops the run with no retry. Power cycle the camera before sending anything else.

Tests: `.venv/bin/python -m unittest tools.grdusb.test_grdusb -v` (simulated camera only).
