# komabridge

`komabridge.py` is a small local HTTP server on top of `tools/grdusb`. A browser cannot use libusb, so the bridge lets the Koma Setup page (`tools/komaset/web/koma-setup.html`) read settings and photos from a GR DIGITAL, push a settings file back, show live view and press buttons over USB. Shutter, live view and key presses need the Koma `tether` feature on the camera.

## Run

```
.venv/bin/python tools/komaset/bridge/komabridge.py --real                          # a real camera
.venv/bin/python tools/komaset/bridge/komabridge.py --mock --mock-tether --mock-hw  # a simulated camera
KOMA_FIRMWARE=<2.40 file> .venv/bin/python tools/komaset/bridge/komabridge.py --emu   # the firmware in the emulator
```

Then open http://localhost:8765/. On the camera, set SETUP > USB CONNECTION to ORIGINAL, turn the camera on, then plug in the cable. If the cable goes in first, the camera boots into a state that remote control cannot use. `--real` needs `pyusb` and `libusb`. With none of `--real`, `--mock` or `--emu` the bridge exits with code 2.

## Options

| Option | Meaning |
|---|---|
| `--port N` | Port, default 8765 |
| `--quiet` | No request log |
| `--lan` | Phone mode: listen on every interface and require a random per-run token (printed as a URL and a QR code) |
| `--selftest --out DIR` | Run the USB checks once, write `report.md`, `report.json` and the pulled files to `DIR`, exit 0 if no step failed |
| `--module FILE` | `--emu` only: the package to load. The default is `koma04`, built in memory |
| `--mock-tether` | The mock camera has the `tether` feature, so a full press adds a new JPEG |
| `--mock-hw` | The mock uses timings measured on a camera |
| `--mock-reboot-s S` | How long the mock camera is away after a restart (default 4) |
| `--mock-stays-off` | The mock camera never comes back after a restart |

In phone mode, anyone on the network who has the token can read the card and press the camera's buttons. Use it only on a network you trust.

## Endpoints

`GET /api` lists them. The main ones:

| Endpoint | Does |
|---|---|
| `GET /status` | Connection, firmware, backend, live and tether state |
| `GET /settings`, `POST /settings` | Read `PARAM.CUR`; write `PARAM.DAT` and read it back. The camera imports it at the next boot |
| `GET /photos`, `GET /photos/<folder>/<name>[?thumb=1]` | List and download JPEGs |
| `POST /tether/start`, `/tether/shoot`, `/tether/focus`, `/tether/stop`, `/tether/interval` | Remote control and shooting |
| `GET /live?mode=1\|0\|2`, `GET /live/frame`, `POST /live/stop` | Live view as JPEG, grey frames, or the camera's own menu layer |
| `GET /keys`, `POST /keys`, `POST /keys/tap`, `POST /dial` | Button presses and the virtual mode dial |
| `GET /ram`, `POST /ram`, `POST /ram/batch` | Read and write the settings block in RAM (`-KGET`, `-KSET`), limited to an allowlist |
| `GET /` | The Koma Setup page. `/build/koma04.grmod` (Koma 1), `/build/koma04w.grmod` (Koma 2) and `/build/SHA256SUMS` are served if you put your own builds there |

By default the bridge answers loopback clients only and checks `Host` and `Origin`.

## Files

| File | Is |
|---|---|
| `komabridge.py` | The server and the command allowlist |
| `mockcard.py` | The simulated camera |
| `emulink.py`, `tether_scenario.py` | The `--emu` backend |
| `selftest.py` | `--selftest` |
| `qrmini.py` | The QR code for `--lan` |

Tests: `.venv/bin/python tools/check.py test` runs them. `test_emu_integration.py` also needs `KOMA_FIRMWARE`.
