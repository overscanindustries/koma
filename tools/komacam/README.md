# komacam: the GR DIGITAL as a webcam

komacam turns the camera's USB live view into an MJPEG stream, a virtual camera, or a folder of frames. It never
touches USB itself. It is a client of `tools/komaset/bridge/komabridge.py`: it arms live view with
`POST /tether/start`, reads `GET /live?mode=1`, and leaves remote mode with `POST /tether/stop`. It can start the bridge
for you (`--spawn`).

    camera --USB--> komabridge :8765 --HTTP--> komacam :8770 --> /stream.mjpg, /hd.mjpg, / (the OBS Browser source)
                                                        --> virtual camera (pyvirtualcam -> "OBS Virtual Camera")
                                                        --> --dump folder

**In the Koma app (2026-11-08, MOCK).** The app's Webcam area runs komacam for you: komabridge imports it
(`tools/komaset/bridge/webcamhost.py`) and the Webcam switch starts and stops it, with the preview, the device name the
call app lists, a check of what this computer is missing for the virtual camera, and the bridge's Auto Power Off
keepalive running while it streams. The page komacam used to serve for people (mirror, fill and status buttons) is
retired in favour of the app; `/` is now the OBS Browser source alone. This command line works as before.

Tags: **HW** measured on a camera, **STATIC** read from the firmware, **INFERRED** deduced, **MOCK** shown only against the bridge's simulated camera, **UNTESTED** not run.

On hardware (main body, 2026-11-08, 64 GB card): `--spawn real` streamed JPEG live view at 10.9-12.9 fps, and Zoom showed it through OBS Virtual Camera for an 11 minute soak (details below). Options marked MOCK or UNTESTED have not been run on a camera.

## Quickstart

1. The camera needs Koma firmware with the `liveview` and `tether` features, USB CONNECTION set to ORIGINAL, and a
   charged DB-60 or the AC-4c adapter.
2. **Turn the camera on first, then plug in USB.** If you plug in first, it boots into a lens-retracted mode (HW).
3. Run this one command from the repository root:

       .venv/bin/python tools/komacam/komacam.py --spawn real

   It starts the bridge with `--real` and prints the bridge's warning. It then arms remote mode (`remtin`,
   `diaset,4`, then a wait for `-LVFR` 150) and streams. A second USB session can take about 20 s to arm (HW); the
   outputs show the "Waking the camera" card meanwhile.
4. Open `http://127.0.0.1:8770/` to check the picture: the OBS Browser source, a black page with the frame scaled to
   fit and nothing else. `?mirror=1` mirrors it, `?fill=1` crops to fill. (Through the Koma app, its Webcam area shows
   the preview instead.)
5. Press Ctrl-C once. komacam ends the stream and sends `/tether/stop` (`keyset,0,2` then `remtout`), then it stops the
   bridge it started. Pressing Ctrl-C twice exits at once and can leave the camera in remote mode. If that happens, run
   `curl -X POST -H 'Host: 127.0.0.1:8765' http://127.0.0.1:8765/tether/stop`, or power-cycle the camera.

If a bridge is already running (for example, one you started for the Koma app), drop `--spawn real` and komacam
uses it. **Don't open the app's Shoot live view at the same time, and don't turn on the app's Webcam while this runs.**
The bridge serves one video stream, and a newer `/live` ends the older one. komacam would then see a drop and re-arm.
(The app refuses to start its webcam while a komacam already serves port 8770.)

## Into Zoom via OBS (the recommended path)

OBS 33.0.1 is installed at `/Applications/OBS.app`. Its virtual camera extension still needs your one-time approval
in System Settings > General > Login Items & Extensions > Camera Extensions.

**HW, 2026-11-08** (main body, 64 GB card, `--spawn real --lcd-off --focus 1`): Zoom showed the live
picture through "OBS Virtual Camera". An 11 minute soak ran at 10.9 fps with 0 re-arms and no freeze; `--mode 3` ran at 12.9 fps. If Zoom shows only
the idle frame, you have not clicked "Start Virtual Camera". Keep a charged spare battery handy: USB use drains it fast.

1. Start komacam (Quickstart step 3).
2. In OBS, add a source: **+ > Browser**.
   - URL: `http://127.0.0.1:8770/`. Add `?fill=1` to crop to 16:9, or `?mirror=1` to mirror.
   - Width 1280, height 720.
   - Untick "Shutdown source when not visible".
3. Right-click the source and choose **Transform > Fit to screen**.
4. Click **Start Virtual Camera**. The first time, macOS asks you to approve the extension.
5. In Zoom, go to **Settings > Video > Camera** and pick **OBS Virtual Camera**. Zoom mirrors your own preview by
   default ("Mirror my video"). The others see the unmirrored picture.

A **Media Source** also works: untick "Local File", set Input to `http://127.0.0.1:8770/hd.mjpg` and Input Format to
`mpjpeg`. The Browser Source is simpler, and it reconnects by itself.

**Self-healing page.** In OBS 33 (Chromium 150) the Browser source showed the stream fine, but when the camera dropped and
the multipart stream ended, the `<img>` kept its last frame and fired no error, so the page never reconnected and Zoom
showed one frozen frame until the source was refreshed by hand (HW 2026-11-08). komacam itself had re-armed and was
streaming again within seconds. The page reads `/stream.mjpg` with `fetch()`, splits the multipart body itself and
draws each JPEG into a canvas. If no complete frame arrives for 3 s it aborts and reconnects after 1 s (backoff 1 s, 2 s,
3 s, then stays at 3 s), and it shows a small "reconnecting..." label after 3 s without frames. Browsers without
`ReadableStream` fall back to the old `<img>` plus a reload every 5 s. The `/stream.mjpg`, `/hd.mjpg` and `/snapshot.jpg`
endpoints are unchanged. The reconnect logic has not been run in OBS (UNTESTED).

## Alternative: pyvirtualcam (no OBS window)

    .venv/bin/python tools/komacam/komacam.py --spawn real --vcam --fit letterbox

pyvirtualcam is optional and not in `requirements.txt` (install it with pip; `--vcam` also needs numpy and Pillow). On macOS, pyvirtualcam drives the same "OBS Virtual Camera"
extension, so OBS must have run Start Virtual Camera once and the extension must be approved. OBS must not be running
its own virtual camera at the same time. Until then, komacam prints instructions and keeps serving MJPEG; it never
fails hard. Without the approved extension, pyvirtualcam reports "OBS Virtual Camera is not installed". **No frame has reached Zoom through pyvirtualcam (UNTESTED).**

## Options

| Option | What it does |
|---|---|
| `--spawn mock\|mock-tether\|real\|emu` | Start the bridge as a child process and stop it on exit. Without it, komacam uses a bridge already on `--bridge-port` (8765). |
| `--port 8770` | komacam's port, on 127.0.0.1 only. |
| `--vcam` | Also feed the virtual camera at `--size` and `--vcam-fps` (30). It repeats the last frame between camera frames. |
| `--dump DIR` | Write frames and `index.csv`. `--dump-variant raw\|hd`; `--dump-max N` stops after N frames. |
| `--size 1280x720` | The size of `/hd.mjpg` and the virtual camera. Zoom prefers 16:9. |
| `--fit letterbox\|crop\|stretch` | How 4:3 goes into 16:9: letterbox gives side bars, crop cuts top and bottom, stretch distorts. |
| `--look none\|bw\|hicon\|film` | Host-side looks: B&W with a gentle S-curve, hard high-contrast B&W, or film (B&W plus grain and a slight vignette). `--grain 0..1.5` sets the grain strength. |
| `--mirror`, `--overlay` | Mirror the hd and virtual camera output; draw fps, state and counters on it. |
| `--mode 1\|0\|3` | 1 is the 320x240 JPEG (default). 0 is the 160x120 grey frame. 3 is the 640x240 ring JPEG (liveview 1.3), shown at 640x480. |
| `--focus 1\|1.5\|2\|2.5\|5\|inf` | Snap distance: `-KSET 0x8b5`, then one half-press after each arm. Needs FOCUS = SNAP set once on the camera (komacam reads `0x058` and warns), plus Koma snapdist + ksettings (STATIC; MOCK). |
| `--af-once` | One half-press after the first arm only (MOCK). |
| `--lcd-off` | **UNTESTED on HW.** Taps DISP (key 22) until the live display byte `0x1bef92` (read with -PEEK) is 4, the stock LCD-off position. It restores the byte before `remtout` at exit and never taps blind (EMU + STATIC; MOCK). |
| `--ev X` | EV -2.0..+2.0 in 1/3 steps (`-KSET 0x053`). At most one stock write per 8 s, because of the HW busy window (MOCK). |
| `--img-set bw\|bwte\|...`, `--te-tone sepia\|...` | The camera's own B&W through `-KSET 0x030` / `0x1a9` (STATIC that the monitor mono flag follows; HW pending). |
| `--health-every 60` | `-KSTA` every N s: battery level, the APO enable `apo_2b9e58`, LCD words, monitor flag. |
| `--no-caffeinate` | On macOS komacam runs `caffeinate -i -s` by default, so the Mac stays awake during the call. |
| `--frame-timeout 15` | With no frame for this long, close and re-arm. Keep it above the bridge's 10 s 451 grace. |

### Endpoints (127.0.0.1:8770)

- `/`: the OBS Browser source (the self-healing canvas reader below), no controls. URL flags: `?mirror=1`, `?fill=1`
  and `?src=hd` for the 1280x720 stream.
- `/stream.mjpg`: frames at camera size. With `--look none`, these are the camera's JPEGs passed through without
  re-encoding.
- `/hd.mjpg`: frames at `--size`, fitted, with the look applied.
- `/raw.mjpg`: always the bridge's bytes, untouched.
- `/snapshot.jpg`: the latest frame.
- `POST /control`: runtime controls, loopback only, with foreign `Origin` refused. Body `{"ev": 0.3}`,
  `{"img_set": "bw"}`, `{"te_tone": "sepia"}`, `{"lcd": "off"|"restore"}` or `{"af": true}`. Stock bytes are
  queued, and the latest value wins.
- `/status.json`: `state`, `fps_in`, `frames`, `arms`, `rearms`, `reconnects`, `stalls`, `clients`, `last_arm_s` and
  `ms_per_hd_frame`.

No response carries `Access-Control-Allow-Origin` (since 2026-11-08): the OBS Browser source reads the streams from
this same origin and the macOS extension is not a browser, so no other website can read the picture or the state.

While there is no live frame (arming, camera gone), every output shows a dark card with the state in plain words
("GR DIGITAL", then "Waking the camera", "Camera not connected", "No live view" while it re-arms a camera that refuses
live view, ...), twice a second, at 60 and 36 px on a 720-line frame so a call's tile can show it, set in the Koma app's
Archivo (from `tools/komaapp/dist`, else its npm package; Pillow's own font when neither is there). The call sees it, so
it carries no protocol strings; the log keeps those. That way OBS, Zoom and the page never freeze on a stale frame or drop the
connection.

## What it does about drops (MOCK)

| Event | What happens |
|---|---|
| `-LVFR` answers 451 for a short time. After a shot, live view returns in ~7 s (HW); after `RETR` it drops (HW). | The bridge retries 451 for 10 s and keeps the stream open. komacam shows the state card and resumes with no re-arm. |
| 451 lasts longer than the bridge's grace | The bridge ends the stream. komacam re-arms with `/tether/start` and resumes. |
| The camera leaves remote mode | Same as above: re-arm. |
| USB unplugged or camera off | The state becomes `no camera` and komacam polls `/status` every second. When the camera is back: reconnect, arm, stream. The arm can take ~20 s on a second session (HW); one `/tether/start` covers it, because the bridge waits up to 30 s with re-arms. |
| Two arms fail in a row | komacam runs `/tether/stop` and then arms again, with backoff. |
| The bridge process dies (with `--spawn`) | komacam restarts it, up to 5 times. |
| The Mac sleeps and wakes | A wall-clock jump that the monotonic clock did not see aborts the stream. komacam then runs `/tether/stop` and arms again, re-applying LCD, focus and EV. |
| An external bridge restarts | `no bridge`, then komacam arms and streams again when the bridge answers. |
| Ctrl-C, SIGTERM or SIGHUP (LCD restored first) | komacam sends `/live/stop` and `/tether/stop` (`keyset,0,2`, `remtout`) and stops the spawned bridge. If an arm is in progress, `/tether/stop` waits for it to finish (≤30 s). |

komacam never sends `RETR`, so the drop that follows a `RETR` happens only if another client reads photos meanwhile.

## Measured against the mock (MOCK, 2026-11-07)

`analysis/webcam/bench-mock.json` holds the numbers. The mock produces frames at 30 fps, and the bridge caps its
pulls at 30 per second.
- Input, `/stream.mjpg` and `/hd.mjpg` ran at 26.8 fps for 60 s (1,609 frames), with no re-arms and no stalls. Frame
  gaps were 37-38 ms (p50/p95), and the largest was 45 ms.
- Latency from the mock's `-LVFR` answer to the MJPEG client: 0.23 ms median, 0.42 ms p95 and 9.8 ms max. That is the host pipeline only. On
  hardware, add the pull round trip (~1-2 ms per pull, HW) and the camera's own capture and JPEG time, which has not
  been measured.
- `/hd.mjpg` at 1280x720: 5.7 ms per frame with letterbox and no look, 11.4 ms with the film look. The virtual camera
  path with a fake pyvirtualcam held 30.3 fps.
- Recovery:
  - a 7 s drop came back in 7.1 s with no re-arm;
  - a 13 s drop came back in 13.2 s with one re-arm;
  - a 20 s first arm gave its first frame at 20.3 s;
  - after an unplug plus a 20 s second-session arm, streaming resumed 24.5 s after the pull.

On the real camera, expect what HW showed: **320x240 JPEG at 22-29 fps** (sessions 7-13).

## Honest limits

- **Source resolution: 320x240 (HW) in mode 1.** 1280x720 is an upscale. It looks like a 2005 compact's LCD feed, soft
  and with blocky JPEG. The `film` and `hicon` looks lean into that.
- **`--mode 3` (liveview 1.3):** ran on the main body at 12.9 fps (HW, 2026-11-08). the full live-view slot, 640x240 JPEG from the camera's
  own encoder with no resize, twice mode 1's horizontal detail and the same 240 lines. Its pixels are about twice as
  tall as wide, so komacam stretches every frame to 640x480 (4:3) before `--fit` and the looks; the native stream is
  640x480. Needs Pillow. The bridge picks the JPEG scale per frame (target 24-48 KB, one USB chunk). Evidence and
  risks: `analysis/webcam/MODE3.md`.
- **No audio.** The camera has no USB audio path. Use the Mac's microphone in Zoom.
- **Power.**
  - Live view plus USB for an hour is near or beyond one DB-60's capacity (INFERRED). Use the **AC-4c** adapter for
    long calls.
  - USB does not power the camera (INFERRED, strong).
- **Auto power-off.** The liveview feature renews APO on every frame through `0x290de` (STATIC). That has not been
  proven on HW for a full hour. While the stream is paused, the bridge's `keyset,0,27` keepalive after idle covers it
  (MOCK).
- **Focus.** The lens does not focus continuously. Use SNAP focus plus `--focus 1` (STATIC).
- **Hour-long call checklist and evidence:** `analysis/webcam/WEBCAM-PRACTICAL.md`.
- **White balance.** Continuous AWB in live view is unknown, probably absent (STATIC). Set a fixed WB preset on the
  camera.
- **Latency on HW is unmeasured.**
- **Battery.** USB live view drained the main body's battery from level 2 to dead in about 2.5 hours (HW). Use the AC adapter or a spare battery.
- **pyvirtualcam is UNTESTED end to end.** The OBS Browser Source path is HW (above).
- **CoreMediaIO extension.** A native extension that would skip OBS is drafted in `macos-extension/`. It is
  typechecked only: not built, not signed, not installed.

## Tests (MOCK)

    python3 -m pytest -q -s tools/komacam/test_komacam.py      # ~2.5 min; KOMACAM_SOAK_S=60 default

The tests run the bridge in-process on the simulated camera and poke its hooks. They cover:
- a 60 s soak: fps, latency, and a clean `remtout`;
- a 7 s drop that the bridge rides out;
- a 13 s drop that forces a re-arm;
- a lost remote mode;
- a 20 s first arm;
- an unplug followed by a 20 s second-session arm;
- a camera whose arm never takes;
- the processing variants;
- the virtual camera, with pyvirtualcam missing and with a fake pyvirtualcam;
- the real CLI with `--spawn mock` and SIGINT.

Dependencies: the stdlib alone covers the MJPEG pass-through. Pillow is needed for the looks, `/hd.mjpg`, the state
card and mode 0 (listed in `requirements.txt`). numpy and pyvirtualcam are needed for `--vcam`; pyvirtualcam and pytest are not in `requirements.txt`.
