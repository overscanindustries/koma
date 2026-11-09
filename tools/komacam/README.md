# komacam

komacam turns the camera's USB live view into an MJPEG stream, a virtual camera or a folder of frames, so the GR DIGITAL can work as a webcam. It is a client of `tools/komaset/bridge/komabridge.py` and can start the bridge itself. It never touches USB directly.

```
camera --USB--> komabridge :8765 --HTTP--> komacam :8770 --> /stream.mjpg, /hd.mjpg, / (fullscreen page)
                                                         --> virtual camera (pyvirtualcam)
                                                         --> --dump folder
```

## Run

The camera needs a Koma package with the `liveview` and `tether` features and USB CONNECTION set to ORIGINAL. Turn the camera on first, then plug in USB.

```
.venv/bin/python tools/komacam/komacam.py --spawn real
```

This starts the bridge, arms remote mode and streams. Open http://127.0.0.1:8770/ to see the picture. Press Ctrl-C once to stop: komacam leaves remote mode and stops the bridge. If a bridge is already running, leave out `--spawn` and komacam uses it.

## Use it in Zoom through OBS

1. Start komacam.
2. In OBS, add a Browser source with URL `http://127.0.0.1:8770/`, width 1280 and height 720. Add `?fill=1` to crop to 16:9 or `?mirror=1` to mirror. Untick "Shutdown source when not visible".
3. Right-click the source, choose Transform > Fit to screen, then click Start Virtual Camera.
4. In Zoom, pick OBS Virtual Camera as the camera.

For a virtual camera without OBS's window, add `--vcam --fit letterbox`. It needs pyvirtualcam, numpy and Pillow.

## Options

| Option | Does |
|---|---|
| `--spawn mock\|mock-tether\|real\|emu` | Start the bridge as a child process. Without it komacam uses the bridge on `--bridge-port` (8765) |
| `--port N` | komacam's port on 127.0.0.1 (default 8770) |
| `--mode 1\|0\|3` | 1: 320x240 JPEG (default). 0: 160x120 grey. 3: 640x240 JPEG shown at 640x480 (needs Pillow) |
| `--vcam`, `--vcam-fps N` | Also feed a virtual camera |
| `--dump DIR`, `--dump-variant raw\|hd`, `--dump-max N` | Write frames and `index.csv` to a folder |
| `--size WxH`, `--fit letterbox\|crop\|stretch` | Size and fit of `/hd.mjpg` and the virtual camera (default 1280x720, letterbox) |
| `--look none\|bw\|hicon\|film`, `--grain X` | Looks applied on the computer |
| `--mirror`, `--overlay` | Mirror the output; draw fps and state on it |
| `--focus 1\|1.5\|2\|2.5\|5\|inf`, `--af-once` | Snap focus distance (needs FOCUS set to SNAP on the camera), or one half-press after arming |
| `--ev X` | Exposure compensation, -2.0 to +2.0 in 1/3 steps |
| `--img-set NAME`, `--te-tone NAME` | The camera's own image setting and B&W toning |
| `--lcd-off` | Turn the camera's LCD off while streaming, and back on at exit |
| `--frame-timeout S` | Re-arm after S seconds without a frame (default 15) |

`--help` lists the rest.

## Endpoints

All on 127.0.0.1:8770.

| Endpoint | Serves |
|---|---|
| `/` | Fullscreen page (`?mirror=1`, `?fill=1`, `?hud=1`, `?src=hd`) |
| `/stream.mjpg` | Frames at camera size |
| `/hd.mjpg` | Frames at `--size` with the look applied |
| `/raw.mjpg` | The bridge's bytes |
| `/snapshot.jpg`, `/status.json` | The latest frame; state and counters |
| `POST /control` | Runtime changes such as `{"ev": 0.3}`, `{"img_set": "bw"}`, `{"lcd": "off"}`, `{"af": true}` |

When no frame is coming, every output shows a dark placeholder so OBS and Zoom do not freeze. komacam re-arms by itself after drops.

## Limits

- Live view is 320x240, so 1280x720 is an upscale.
- There is no audio.
- USB does not power the camera. Use the AC adapter or a spare battery for long calls.
- The lens does not focus continuously. Use snap focus.

Tests: `python3 -m pytest -q tools/komacam/test_komacam.py` (simulated camera; needs pytest).
