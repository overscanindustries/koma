# grdusb

Minimal Mac client for the original Ricoh GR DIGITAL in ORIGINAL USB mode, written from
[the USB protocol chapter](../../docs/internals/07-usb-protocol.md). It contains no Ricoh code and bundles no Ricoh DLLs or firmware.

Hardware status: `ver` has run on two bodies (main `200 9.17 02.40`, spare `200 9.17 01.08`). After `-VER` the main camera's screen went dark and its lamp blinked green, as in an update; it worked normally after a power cycle. On the spare, the version gate, `info`, `ls` and `get-many --allow-internal` have run live, and `stage-update` ran once with no SD card and stopped before any write. grdusb is also the transport of komabridge, which has run settings, photo, tether and live-view traffic on both bodies (`analysis/hw-tests/`). Spare-run logs stay local (`analysis/spare-dumps/`, they hold the camera ID); first-run hashes and limits are in `analysis/usb-first-contact/README.md`. The write path of `stage-update` has only run against the mock (`mock.py`). The tests show agreement with the code reading, not camera behaviour. Restricted does not mean physically read-only: see "What can still change state".

## Commands, and exactly what each sends

Run from the project root. Needs `pyusb` in `.venv` and Homebrew `libusb`. Every command that touches a camera needs `--live` (or `--mock` for the simulation). `--dry-run` prints the USB bring-up and the exact request list without touching USB (no enumeration, no open, no log file).

```
.venv/bin/python -m tools.grdusb list                                  # descriptors only; no camera request
.venv/bin/python -m tools.grdusb --live ver                            # -VER
.venv/bin/python -m tools.grdusb --live info                           # -GCID, -GSID, -TIME, -DCAP /EXT0
.venv/bin/python -m tools.grdusb --live info --include-internal        # the above plus -DCAP /IROM (explicit)
.venv/bin/python -m tools.grdusb --live ls /EXT0/DCIM                  # -NLST /EXT0/DCIM
.venv/bin/python -m tools.grdusb --live get /EXT0/DCIM/100RICOH/RIMG0001.JPG photo.jpg   # RETR <path>
.venv/bin/python -m tools.grdusb --dry-run info                        # print the plan, send nothing
.venv/bin/python -m unittest tools.grdusb.test_grdusb -v               # simulated camera only
```

- `list` reads attached Ricoh devices from libusb's descriptor cache and sends no camera request.
- `ver` sends one `-VER`, reads one reply, releases the interface and disconnects. It then tells you to watch the LCD and lamps beyond the camera's 3 s timer, write down any change, and run nothing else until you have finished. It refuses `--line-state-fallback`.
- `info` is card-only. It probes `/IROM` only with `--include-internal`.
- `get` refuses `/IROM` paths unless `--allow-internal` and `--require-version` are both given (see "Version gate"). It always refuses to overwrite an existing destination, checked before any USB access.
- Only `ver` sends `-VER`, unless you pass `--require-version` or `--require-camera-id`. Every real run prints its plan (the requests in order) first; the mock tests check that the dry-run list equals what the simulated camera received.

Every real run writes a transfer log (JSON lines: one object per bring-up step, write, read including empty reads, error and close) to `grdusb-<command>-<UTC time>.jsonl` in the current directory, or to `--transfer-log FILE`. It is flushed and synced per line and never overwrites an existing file. If it cannot be created, nothing is sent. Hex is kept for the first 256 bytes of each transfer; lengths are always full.

## Version gate

The gate stops a command reaching the wrong body (for example 02.40 on one, 01.08 on another).

```
.venv/bin/python -m tools.grdusb --live --require-version 01.08 info
.venv/bin/python -m tools.grdusb --live --require-version 01.08 --require-camera-id <GCID value> ls /EXT0/DCIM
.venv/bin/python -m tools.grdusb --live --require-version 01.08 get --allow-internal /IROM/DIR/FILE out.bin
.venv/bin/python -m tools.grdusb --live --require-version 01.08 get-many --allow-internal /IROM/DIR outdir
.venv/bin/python -m tools.grdusb --live --require-version 01.08 ver    # one -VER, not two
```

- `--require-version VV.VV` (global, repeatable, any match passes) makes every command except `list` send `-VER` first. On a mismatch it prints the version it saw, sends nothing else, closes cleanly and exits 2.
- `--require-camera-id ID` then sends `-GCID` and refuses unless `CameraID=` equals `ID`. It needs `--require-version`.
- Both requests appear first in `--dry-run` and in the printed plan.
- `get --allow-internal` reads `/IROM` and needs at least one `--require-version`. The overwrite refusal and the repair-write caveat still apply, and it prints a line saying internal memory is being read on the version-gated body.
- `get-many REMOTE_DIR LOCAL_DIR` sends one `-NLST`, then one `RETR` per regular file (directories are skipped), in one session, with the same gating. It refuses if any destination exists. It writes `grdusb-manifest.json` (remote path, listed size, bytes received, SHA-256) into `LOCAL_DIR`. The dry-run shows only the `-NLST`, as file names are unknown until the listing arrives.
  A name repeated within one listing (seen on a real card: 8.3 aliases of macOS `._` sidecars, which can also change between listings) is fetched once per entry, in order. Repeats are saved as `NAME.dup1`, `.dup2` and listed under `duplicates` in the manifest with each file's `remote` and `local` name. If the run stops early, the manifest has `complete: false`, the last stderr line is `INCOMPLETE: N of M files copied; see manifest`, and the exit code is 1.
- The mock takes `--mock-version` and `--mock-camera-id` for testing the gate.

## `stage-update`: the one write command (spare only, one live run that stopped before writing)

`stage-update` copies one pinned Ricoh payload set to the SD card over USB and reads it back, as Ricoh's Windows updater does. It writes card files only and flashes nothing: the protocol has no flash command. The camera flashes later, only after Ricoh's keyed power-on. It was built for the spare body.

Its one live run (HW, spare, no SD card) sent only `-VER`, `-GCID` and `-DCAP /EXT0`. The camera answered `451` to `-DCAP /EXT0`, and the tool refused ("no usable SD card"), closed and stopped. No `DELE`, `-STOR` or other write was sent. Log: `analysis/spare-dumps/2026-11-06-usb-logs/11-stage-nocard.jsonl` (local only). The write path, read-back and final listing have only run against the mock.

```
.venv/bin/python -m tools.grdusb --dry-run --require-version 01.08 --require-camera-id <GCID value> \
    stage-update --i-understand-this-writes-the-card
.venv/bin/python -m tools.grdusb --live --require-version 01.08 --require-camera-id <GCID value> \
    stage-update --i-understand-this-writes-the-card [--set NAME] [--allow-older-update]
```

Payload sets (`--set NAME`, default `official-2.40-pair`). Each has pinned SHA-256 values and source paths, checked at load; no other file can be passed.

| Set | Files | Source |
|---|---|---|
| `official-2.40-pair` (default) | `QRFLECT3` `de00e03d…783b`, `QRFLECT7` `1dd1a9d9…c21c3f` | `firmware/extracted/GR DIGITAL Update/Firmware rel 2.40/` |
| `official-2.40-qrflect3` | `QRFLECT3` `de00e03d…783b` only | same |
| `official-2.31-qrflect3` | `QRFLECT3` `8cbf2d3a…1731c` | `firmware/original/history/gr-231-win-en/x/GR DIGITAL Update/Firmware rel 2.31/` |

Gates, all checked before anything is sent unless noted:

- `--require-version` is required (the version the camera must be running; any match passes). A listed version at or above the set's version is refused ("already updated", Ricoh's check) unless `--allow-older-update` is passed, which permits a camera strictly newer than the set (a deliberate downgrade, e.g. 02.40 to 2.31). An equal version is always refused.
- `--require-camera-id` is required.
- `--i-understand-this-writes-the-card` is required.
- On the camera: `-VER` must match and pass the same version rule, then `-GCID` must match. `-DCAP /EXT0` must answer `200` with free space of at least the set's files plus 1 MiB. `-NLST /EXT0` must show no `QUICKVER.UP`, no `SCRIPT.MG1`, and no `QRFLECT` name outside `QRFLECT0`..`QRFLECT9` (or any such directory).

The camera flashes every `QRFLECT*` file it finds, so the tool first sends `DELE` for every `/EXT0/QRFLECT0` to `QRFLECT9` (a `250` or `500` reply is recorded and accepted, as Ricoh ignores it). It then sends `-STOR <size> /EXT0/QRFLECT3` (and `QRFLECT7` for the pair) in data frames of at most 65536 bytes, expecting `150 data connection for -STOR.` then `250 -STOR command success.`. It reads each file back with `RETR` and compares every byte. Last, it sends `-DCAP /EXT0` and `-NLST /EXT0` again, prints the free-space change and the listing, and stops with an error unless the `QRFLECT*` files in the card root are exactly the staged set.

Any unexpected reply, short write, timeout, read-back mismatch or extra `QRFLECT*` file stops the run at once, with no retry. The tool closes the device, prints every request sent with its reply and the state each file may be in, and exits non-zero: 2 for a refusal, 1 for any other stop. Live runs write a transfer log like the other commands. At the end it prints Ricoh's on-camera steps and says, naming the set, that nothing has been flashed.

The write verbs live only in `stage.py`. The read-only modules cannot build them, and the mock treats them as violations for every other command.

## Is `-VER` a prerequisite? No, so nothing sends it automatically

The protocol analysis does not show that `-VER` is required:

- The parser and dispatch notes in [the USB protocol chapter](../../docs/internals/07-usb-protocol.md) describe a verb table lookup with no session or authentication prerequisite. The only ordering rules found are reply-pending and data-phase rules (`0xcb7b4`, `0xcb834`), which concern finishing a reply or transfer. (D, bounded to those notes)
- Ricoh's clients send `-VER` first to check the model and compare firmware versions (updater step 4, Mac `_FFSMount`); none of that code is a camera-side requirement. (D)
- `-VER` arms a 3 s timer whose expiry can post a UI message to the main task. Sending it before every command would hide which request caused an LCD change.
- Whether any command misbehaves without a prior `-VER` on real hardware is unknown (U).

So `ls`, `info` and `get` do not check that the model code is `9.17`. A live run is gated by the numeric descriptor match, the two descriptor strings and the single-device check. Only `ver` checks the model code; run it first if you want that check.

## What it refuses

- Any device that does not match the firmware's ORIGINAL-mode descriptors (`05ca:2218`, vendor class, endpoints `0x81`, `0x02`, `0x83`, then the two descriptor strings). `--live` opens the device only after the numeric descriptors match, then reads the string descriptors with control reads only. Nothing else is sent until the strings match.
- A device already configured to something other than configuration 1: refused after GET_CONFIGURATION, before any configuration change.
- Any verb outside `-VER`, `-GCID`, `-GSID`, `-TIME`, `-DCAP`, `-NLST`, `RETR`, except in `stage-update`, which adds `DELE` and `-STOR` for two fixed card paths only. Format, rename, calibration and memory commands do not exist here, not even behind a flag.
- `get` on `/IROM`, and bad or relative paths, all before any USB access.

## What can still change state

- `get` (`RETR`): on a file whose chain length disagrees with its directory entry, the firmware's close path can proceed into metadata-update logic, so a file with inconsistent metadata could trigger a filesystem repair write on close. This is why `get` refuses `/IROM` and prints this caveat on every run. Copy the card in a reader first and use an expendable card.
- `-VER` arms a camera-side timer with a deferred UI message (derivation, note 1). Not shown to reach a write sink.
- USB bring-up sends `SET_CONFIGURATION(1)` unconditionally. The firmware's bulk IN needs a configured controller state (D `0x5fda8`, I that `0x30` means configured). Repeating it on an already configured device is unverified.

## After an anomaly: the tool stops and tells you to power cycle

A timeout, malformed frame, short bulk write, or failure to store received data stops the run. The tool does not retry, resynchronise or drain, and sends no reset or clear-halt. It releases the interface and tells you the camera may retain transfer state (an unfinished command or open file handle) and that you must power cycle it (switch off, unplug USB, wait, reconnect) before sending anything else. A well-formed failure reply (for example `450 Open error`) is reported as an error without that advice. A short bulk write is a failure: the frame is not assumed delivered and no reply is awaited.

Bring-up failures release the interface (if claimed) and dispose the libusb handle on every path. Messages say what was sent by then: before `set_configuration`, only descriptor and GET_CONFIGURATION reads; after it, refusals say so.

## Bring-up and framing behaviour

Derived from code and firmware ([the USB protocol chapter](../../docs/internals/07-usb-protocol.md); class D, inferences marked I). Bring-up has since run on hardware. The simulated camera enforces the same reading, so the tests confirm consistency, not behaviour.

- Always `set_configuration(1)` after the descriptor, string and active-configuration gates and before claiming or any bulk traffic, even if libusb reports the device configured. Basis: firmware bulk IN needs controller state `0x30` (D `0x5fda8`; I that `0x30` means Configured), and Ricoh's Mac client configures on every process start.
- 2 s settle delay after configuring. Ricoh's XP path waits; the Mac client does not (D).
- Empty reads are tolerated until a 6 s quiet deadline, then fail cleanly as a timeout (D, Mac `_dcxtp_recv` retries zero-length results until about 5 s).
- Zero-length packets: the host appends none when writing (D). Camera side, as modelled in the mock: a frame whose padded length is an exact multiple of the packet size is followed by a ZLP; a 65536-byte payload frame is 65548 bytes and gets none (D `0x5f730`, `0xcaa5c`; I for the runtime's divide convention). A missing ZLP shows up as a read timeout.
- No `DeviceReset`-style call. On the XP path it reaches an empty method (O bytes, D).
- Control request `0x41/0x22` is off by default. `--line-state-fallback` sends it (wValue 3) after configuring, as Ricoh's Mac client does. Unverified: the firmware accepts it but nothing in the bulk path reads the result, and Ricoh's XP code never sends it. `ver` refuses it. Not part of the first-session plan.

## First-session plan (one stimulus at a time)

Read the "needs a real camera" list in [the USB protocol chapter](../../docs/internals/07-usb-protocol.md) first. Use a fresh battery or the AC adapter and the camera's own cable. Copy the card in a reader beforehand. Run `--dry-run` for each command before the real run and check the request list.

1. `list`: connect the cable, switch the camera on, run `list`. Compare the printed descriptors with the recovered ones in the USB protocol chapter and check `gate: pass`. Stop if it says REFUSE or the camera shows anything unexpected. Run nothing else yet.
2. `ver`: `--live ver` sends one `-VER`, reads one reply and disconnects. Watch the LCD and lamps for well beyond the 3 s timer and write down any change and when. Keep the transfer log. Run no other command in this session until the camera has been seen behaving normally. If anything looks wrong or the tool reports an anomaly, power cycle the camera and end the session.
3. Only in a later session, and only if steps 1 and 2 were uneventful: one `info`, then one `ls /EXT0/DCIM`, then one `get` of a small expendable file, each as its own stimulus, observing in between. Compare the downloaded bytes with the card-reader copy. Keep `/IROM` out of every stage.
