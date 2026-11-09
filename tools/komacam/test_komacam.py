"""komacam against komabridge's SIMULATED camera (mockcard). Nothing here says anything about a real GR DIGITAL.

The bridge runs in-process (komabridge.make_server + Link(MockBackend(MockBody))) so the tests can poke the mock:
a live-view drop (lv_off_until), a 20 s slow first arm (a MockBody subclass), and an unplug (transports dead + off the
bus for a few seconds). komacam talks to it over HTTP exactly as in production.

Run:  python3 -m pytest -q -s tools/komacam/test_komacam.py
      KOMACAM_SOAK_S=60 (default) sets the long streaming test's length.
"""
from __future__ import annotations

import io
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BRIDGE_DIR = REPO / "tools" / "komaset" / "bridge"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BRIDGE_DIR))

import komacam  # noqa: E402
import komabridge  # noqa: E402
from mockcard import MockBody  # noqa: E402

SOAK_S = float(os.environ.get("KOMACAM_SOAK_S", "60"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StampedBody(MockBody):
    """Records when each -LVFR 0/1 frame left the simulated camera (seq -> monotonic), for latency."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.produced: dict = {}

    def lvfr(self, argument):
        reply, payload = super().lvfr(argument)
        if payload is not None and not argument.startswith("2,"):
            self.produced[int(argument.split(",")[1])] = time.monotonic()
        return reply, payload


class SlowArmBody(StampedBody):
    """The first arm after a session opens takes arm_s seconds to answer -LVFR 150 (hardware: a second-session arm
    stalled ~16 s, 2026-11-07); re-arms inside that window do not reset it."""

    def __init__(self, *a, arm_s=20.0, from_session=1, **kw):
        super().__init__(*a, **kw)
        self.arm_s, self.from_session = arm_s, from_session
        self.ready_at = None
        self.sessions = 0

    def open(self):
        t = super().open()
        self.sessions += 1
        self.ready_at = None if self.sessions >= self.from_session else -1.0  # -1: this session arms at once
        return t

    def cctl(self, argument):
        reply = super().cctl(argument)
        if argument.startswith("diaset") and reply and reply.startswith("200") and self.ready_at is None:  # first arm
            self.ready_at = self.clock() + self.arm_s
        return reply

    def lvfr(self, argument):
        if not argument.startswith("2,") and self.ready_at != -1.0 and (self.ready_at is None
                                                                        or self.clock() < self.ready_at):
            return "451 -LVFR off", None
        return super().lvfr(argument)


def unplug(body: MockBody, seconds: float):
    """USB pulled: every open transport dies, the camera is off the bus for `seconds`."""
    with body.lock:
        body.off_until = body.clock() + seconds
    for t in body.transports:
        t.dead = True


class Rig:
    """In-process mock bridge + komacam App, both on free ports."""

    def __init__(self, body: MockBody, **opts):
        self.body = body
        self.link = komabridge.Link(komabridge.MockBackend(body))
        self.link.log = lambda text: None
        self.bport = free_port()
        self.bridge = komabridge.make_server(self.link, port=self.bport)
        self.bridge.quiet = True
        threading.Thread(target=self.bridge.serve_forever, daemon=True).start()
        a = komacam.build_parser().parse_args(["--bridge-port", str(self.bport), "--port", str(free_port()),
                                               *opts.pop("argv", [])])
        a.outputs = {"mjpeg"}
        a.caffeinate = False
        for k, v in opts.items():
            setattr(a, k, v)
        self.app = komacam.App(a)
        self.port = a.port

    def __enter__(self):
        self.app.start()
        return self

    def __exit__(self, *exc):
        self.app.stop()
        self.bridge.shutdown()
        self.bridge.server_close()

    def wait(self, pred, timeout, step=0.1):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(step)
        return False


class MjpegReader(threading.Thread):
    """Reads komacam's /stream.mjpg like a browser would; records (recv monotonic, headers, size) per part."""

    def __init__(self, port, path="/stream.mjpg"):
        super().__init__(daemon=True)
        self.port, self.path = port, path
        self.parts: list = []
        self.stop = threading.Event()
        self.error = None

    def run(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", self.path, headers={"Host": f"127.0.0.1:{self.port}"})
            resp = conn.getresponse()
            assert resp.status == 200, resp.status
            for headers, body in komacam.iter_multipart(resp, komacam.BOUNDARY):
                self.parts.append((time.monotonic(), headers, body))
                if self.stop.is_set():
                    break
        except Exception as error:  # noqa: BLE001
            self.error = error
        finally:
            conn.close()

    def live(self):
        return [p for p in self.parts if "x-komacam-placeholder" not in p[1]]


def stats(xs):
    xs = sorted(xs)
    if not xs:
        return {}
    return {"n": len(xs), "mean_ms": round(1000 * statistics.mean(xs), 2), "p50_ms": round(1000 * xs[len(xs) // 2], 2),
            "p95_ms": round(1000 * xs[int(len(xs) * 0.95) - 1], 2), "max_ms": round(1000 * xs[-1], 2)}


def write_bench(key, value):
    """Print one benchmark result (simulated camera: komabridge mock, in-process; says nothing about a real camera)."""
    print(f"bench {key}: {json.dumps(value)}")


# ------------------------------------------------------------------------------------------------ tests

def test_soak_fps_latency_and_clean_stop():
    """SOAK_S seconds of streaming through /stream.mjpg: fps, end-to-end latency (mock -LVFR -> MJPEG client),
    no re-arms, and the stop sends keyset,0,2 + remtout."""
    body = StampedBody()
    with Rig(body) as rig:
        assert rig.wait(lambda: rig.app.sup.state == "streaming", 10)
        reader = MjpegReader(rig.port)
        reader.start()
        hd = MjpegReader(rig.port, "/hd.mjpg")
        hd.start()
        time.sleep(SOAK_S)
        reader.stop.set()
        hd.stop.set()
        live = reader.live()
        hd_live = hd.live()
        span = live[-1][0] - live[0][0]
        fps = (len(live) - 1) / span
        hd_fps = (len(hd_live) - 1) / (hd_live[-1][0] - hd_live[0][0])
        lat = [t - body.produced[int(h["x-frame-seq"])] for t, h, _ in live if int(h["x-frame-seq"]) in body.produced]
        gaps = [b[0] - a[0] for a, b in zip(live, live[1:])]
        snap = rig.app.sup.snapshot()
        transport = body.transports[-1]
        rig.app.stop()
        sent = transport.sent
    result = {"seconds": round(span, 1), "frames": len(live), "fps": round(fps, 2), "hd_fps": round(hd_fps, 2),
              "latency_lvfr_to_client": stats(lat), "frame_gap": stats(gaps),
              "hd_ms_per_frame": rig.app.proc.ms_per_hd_frame(), "rearms": snap["rearms"], "stalls": snap["stalls"]}
    print("\nSOAK", json.dumps(result))
    write_bench("soak", result)
    assert span >= SOAK_S - 2
    assert fps > 20, fps
    assert hd_fps > 15, hd_fps
    assert snap["rearms"] == 0 and snap["stalls"] == 0
    assert stats(lat)["p95_ms"] < 150
    assert any(c.startswith("-CCTL remtout") for c in sent)
    assert any(c.startswith("-CCTL keyset,0,2") for c in sent)
    assert body.remote == 0


def _drop(drop_s, expect_rearm):
    body = StampedBody()
    with Rig(body) as rig:
        assert rig.wait(lambda: rig.app.sup.state == "streaming", 10)
        grace = rig.link.live_off_grace_s
        drop_s = drop_s(grace)
        reader = MjpegReader(rig.port)
        reader.start()
        time.sleep(2)
        t_drop = time.monotonic()
        body.lv_off_until = body.clock() + drop_s
        time.sleep(drop_s)
        assert rig.wait(lambda: rig.app.sup.state == "streaming" and rig.app.hub.age() < 0.2, 25)
        t_back = time.monotonic()
        time.sleep(2)
        reader.stop.set()
        live = reader.live()
        gap = max(b[0] - a[0] for a, b in zip(live, live[1:]))
        result = {"drop_s": round(drop_s, 1), "bridge_451_grace_s": grace,
                  "recovered_after_s": round(t_back - t_drop, 2), "max_gap_s": round(gap, 2),
                  "placeholder_parts": len(reader.parts) - len(live), "rearms": rig.app.sup.rearms,
                  "client_error": repr(reader.error)}
    assert reader.error is None
    assert (result["rearms"] >= 1) == expect_rearm, result
    assert result["recovered_after_s"] < drop_s + 6, result
    return result


def test_short_drop_ridden_out_by_bridge():
    """The monitor stops for 7 s (-LVFR answers 451; HW: live view returns ~7 s after a shot): inside the bridge's 451
    grace, so the bridge keeps the stream open and komacam does not re-arm; the client sees the state card, then frames."""
    result = _drop(lambda grace: min(7.0, grace - 2), expect_rearm=False)
    print("\nSHORTDROP", json.dumps(result))
    write_bench("live_drop_short", result)


def test_long_drop_rearms():
    """The monitor stays off longer than the bridge's grace: the bridge ends the stream, komacam re-arms
    (/tether/start: remtin + diaset,4 + wait for 150) and frames resume; the MJPEG client stays connected."""
    result = _drop(lambda grace: grace + 3.0, expect_rearm=True)
    print("\nLONGDROP", json.dumps(result))
    write_bench("live_drop_long", result)


def test_remote_mode_lost_rearms():
    """The camera leaves remote mode on its own (state 0, as after a session event): the stream ends, /tether/start
    sends remtin + diaset,4 again, frames resume."""
    body = StampedBody()
    with Rig(body) as rig:
        assert rig.wait(lambda: rig.app.sup.state == "streaming", 10)
        body.remote = 0
        assert rig.wait(lambda: rig.app.sup.rearms >= 1, 15)
        assert rig.wait(lambda: rig.app.sup.state == "streaming" and rig.app.hub.age() < 0.2, 20)
        assert body.remote == 2


def test_slow_arm_20s():
    """First arm answers -LVFR 150 only after 20 s (bridge waits up to 30 s with re-arms): komacam waits it out with
    one /tether/start, serves the state card meanwhile, then streams."""
    body = SlowArmBody(arm_s=20.0)
    with Rig(body) as rig:
        reader = MjpegReader(rig.port)
        reader.start()
        t0 = time.monotonic()
        assert rig.wait(lambda: rig.app.sup.state == "streaming" and rig.app.hub.frames > 0, 40)
        armed_after = time.monotonic() - t0
        time.sleep(2)
        reader.stop.set()
        snap = rig.app.sup.snapshot()
        result = {"arm_s_simulated": 20.0, "first_frame_after_s": round(armed_after, 2), "last_arm_s": snap["last_arm_s"],
                  "arm_failures": snap["arm_failures"], "placeholder_parts": len(reader.parts) - len(reader.live()),
                  "bridge_rearms": rig.link.lvfr_rearms_used}
    print("\nSLOWARM", json.dumps(result))
    write_bench("slow_arm", result)
    assert 19 <= armed_after <= 26
    assert snap["arm_failures"] == 0 and snap["arms"] == 1
    assert result["placeholder_parts"] > 10  # the client saw the "arming" card, not a frozen or dead stream


def test_unplug_reconnect_with_slow_second_session_arm():
    """USB pulled for 4 s, then the second session's arm takes ~20 s (HW 2026-11-07: a second-session arm stalled
    ~16-20 s): komacam waits for the camera, reconnects, waits out the slow arm with one /tether/start, streams."""
    body = SlowArmBody(arm_s=20.0, from_session=2)
    with Rig(body) as rig:
        assert rig.wait(lambda: rig.app.sup.state == "streaming", 10)
        t0 = time.monotonic()
        unplug(body, 4.0)
        assert rig.wait(lambda: rig.app.sup.state == "no camera", 10)
        assert rig.wait(lambda: rig.app.sup.reconnects == 1 and rig.app.sup.state == "streaming"
                        and rig.app.hub.age() < 0.2, 45)
        snap = rig.app.sup.snapshot()
        result = {"unplugged_s": 4.0, "second_session_arm_s_simulated": 20.0,
                  "streaming_again_after_s": round(time.monotonic() - t0, 2), "arms": snap["arms"],
                  "last_arm_s": snap["last_arm_s"], "arm_failures": snap["arm_failures"]}
    print("\nUNPLUG", json.dumps(result))
    write_bench("unplug_slow_rearm", result)
    assert result["arms"] == 2 and result["arm_failures"] == 0
    assert 19 <= result["last_arm_s"] <= 26


def test_camera_without_liveview_keeps_trying():
    """A camera that never answers -LVFR 150 (stock firmware / no liveview feature): no crash, state card, retries."""
    body = StampedBody()
    body.diaset_duds = 10 ** 6  # every diaset 'answers 200 and does not take'
    with Rig(body) as rig:
        rig.link.lvfr_wait_s, rig.link.lvfr_rearm_s = 2.0, 1.0
        assert rig.wait(lambda: rig.app.sup.arm_failures >= 2, 20)
        assert rig.app.sup.state in ("arming", "no camera") or rig.app.sup.arm_failures >= 2
        assert rig.app.hub.frames == 0


@pytest.mark.parametrize("fit,look", [("letterbox", "none"), ("crop", "film"), ("stretch", "hicon"),
                                      ("letterbox", "bw")])
def test_processing_variants(fit, look):
    from PIL import Image
    body = StampedBody()
    with Rig(body, argv=["--fit", fit, "--look", look, "--overlay"]) as rig:
        assert rig.wait(lambda: rig.app.hub.frames > 3, 10)
        f = rig.app.hub.latest
        hd = Image.open(io.BytesIO(rig.app.proc.hd_jpeg(f)))
        nat = Image.open(io.BytesIO(rig.app.proc.native_jpeg(f)))
        assert hd.size == (1280, 720)
        assert nat.size == (320, 240)
        if fit == "letterbox":
            assert hd.convert("L").getpixel((40, 360)) < 8  # side bar is black
        if look in ("bw", "hicon", "film"):
            r, g, b = hd.convert("RGB").getpixel((640, 200))
            assert abs(r - g) < 6 and abs(g - b) < 6
        if look == "none":
            assert rig.app.proc.native_jpeg(f) is f.data  # pass-through, no re-encode


def test_vcam_missing_module_prints_help(capsys):
    body = StampedBody()
    with Rig(body) as rig:
        vc = komacam.VirtualCam(rig.app.out, page_url=rig.app.url)
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def fake_import(name, *a, **kw):
            if name == "pyvirtualcam":
                raise ImportError("No module named 'pyvirtualcam'", name="pyvirtualcam")
            return real_import(name, *a, **kw)
        import builtins
        builtins.__import__ = fake_import
        try:
            vc.run()
        finally:
            builtins.__import__ = real_import
        assert vc.error and "pyvirtualcam" in vc.error
    assert "OBS Virtual Camera" in capsys.readouterr().err


def test_vcam_with_fake_backend():
    """pyvirtualcam's API with a fake camera: frames reach it at 1280x720 RGB, ~fps."""
    import numpy as np
    got = []

    class FakeCam:
        def __init__(self, width, height, fps, fmt, **kw):
            self.device, self.w, self.h, self.period, self.next = "FakeCam", width, height, 1 / fps, time.monotonic()

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def send(self, arr):
            assert isinstance(arr, np.ndarray) and arr.shape == (self.h, self.w, 3)
            got.append(time.monotonic())

        def sleep_until_next_frame(self):
            self.next += self.period
            time.sleep(max(0, self.next - time.monotonic()))

    fake = types.SimpleNamespace(Camera=FakeCam, PixelFormat=types.SimpleNamespace(RGB="rgb"))
    body = StampedBody()
    with Rig(body, argv=["--look", "film"]) as rig:
        assert rig.wait(lambda: rig.app.hub.frames > 3, 10)
        vc = komacam.VirtualCam(rig.app.out, fps=30, module=fake)
        vc.start()
        time.sleep(5)
        rate = (len(got) - 1) / (got[-1] - got[0])
        write_bench("vcam_fake", {"fps": round(rate, 1), "look": "film", "hd_ms_per_frame": rig.app.proc.ms_per_hd_frame()})
        assert rate > 25


@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM"])
def test_cli_spawn_mock_sigint_clean_exit(tmp_path, signame):
    """The real CLI: --spawn mock, frames dumped, SIGINT/SIGTERM -> /tether/stop (remtout), exit 0, bridge child gone."""
    bport, cport = free_port(), free_port()
    p = subprocess.Popen([sys.executable, str(HERE / "komacam.py"), "--spawn", "mock", "--bridge-port", str(bport),
                          "--port", str(cport), "--dump", str(tmp_path / "d"), "--status-every", "1"],
                         stderr=subprocess.PIPE, text=True)
    try:
        end = time.monotonic() + 20
        while time.monotonic() < end and len(list((tmp_path / "d").glob("*.jpg"))) < 30:
            time.sleep(0.2)
        assert len(list((tmp_path / "d").glob("*.jpg"))) >= 30
        p.send_signal(getattr(signal, signame))
        err = p.communicate(timeout=30)[1]
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, err
    assert "-CCTL remtout -> 200" in err, err
    assert "stopped; remote mode off" in err
    with pytest.raises(OSError):  # the spawned bridge is gone: nothing listens on its port
        socket.create_connection(("127.0.0.1", bport), timeout=1).close()


# ------------------------------------------------------------------------------------------------ practical-session tests
# (WEBCAM-PRACTICAL.md). The DISP model below is the EMU trace of the stock DISP handler 0x7e260 on the still shooting
# screen (live display byte 0x1bef92: 0 -> 5 -> 2 -> 3 -> 4 -> 0, 4 = LCD off). SIMULATED, like everything here.

DISP_NEXT = {0: 5, 5: 2, 2: 3, 3: 4, 4: 0}


class DispBody(StampedBody):
    """The mock plus a DISP key that walks the live display byte, and a log of every -CCTL in order."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.disp = 0
        self.ksta = True  # the plain mock is a stock body (-KSTA 501); these tests use the Koma -KSTA/-PEEK routes
        self.cctl_log: list = []

    def cctl(self, argument):
        reply = super().cctl(argument)
        self.cctl_log.append((argument, reply))
        if argument == "keyset,3,22" and reply and reply.startswith("200"):
            self.disp = DISP_NEXT[self.disp]
        return reply


@pytest.fixture
def disp_peek(monkeypatch):
    """-PEEK of 0x1bef92 reads the mock's display byte (the mock's -PEEK is a fixed pattern otherwise)."""
    bodies = []
    real = komabridge.peek

    def peek(link, addr, length):
        body = link.backend.body if hasattr(link, "backend") else None
        for b in bodies:
            if addr == komacam.DISP_LIVE and length == 1 and (body is None or body is b):
                real(link, addr, length)  # still a real -PEEK over the mock wire
                return bytes([b.disp])
        return real(link, addr, length)
    monkeypatch.setattr(komabridge, "peek", peek)
    return bodies


def _rig(body, argv=(), **ctl):
    rig = Rig(body, argv=list(argv))
    rig.app.controls.settle_s = 0.05
    for k, v in ctl.items():
        setattr(rig.app.controls, k, v)
    return rig


def test_bridge_ram_state_and_peek_routes():
    """The two read-only routes komacam needs: GET /ram/state (-KSTA) and GET /ram/peek (-PEEK, range-checked)."""
    body = DispBody()
    with Rig(body) as rig:
        c = rig.app.client
        code, st = c.request("GET", "/ram/state")
        assert code == 200 and "apo_2b9e58" in st and "lcd_1a8d4c" in st
        code, pk = c.request("GET", "/ram/peek?addr=0x1bef92&len=2")
        assert code == 200 and len(pk["hex"]) == 4
        assert c.request("GET", "/ram/peek?addr=0x100&len=1")[0] == 400
        assert c.request("GET", "/ram/peek?addr=0x1a8000&len=300")[0] == 400
        body.ksta = False
        assert c.request("GET", "/ram/state")[0] == 502


def test_lcd_off_then_restore_before_remtout(disp_peek):
    body = DispBody()
    disp_peek.append(body)
    with _rig(body, ["--lcd-off"]) as rig:
        assert rig.wait(lambda: rig.app.controls.lcd_state == "off", 15)
        assert body.disp == 4
        n0 = rig.app.hub.frames
        assert rig.wait(lambda: rig.app.hub.frames > n0 + 30, 5)  # live view keeps streaming (mock)
        taps = sum(1 for a, _ in body.cctl_log if a == "keyset,3,22")
        assert taps == 4
    assert body.disp == 0  # restored
    args = [a for a, _ in body.cctl_log]
    last_tap = max(i for i, a in enumerate(args) if a == "keyset,3,22")
    assert last_tap < args.index("remtout", last_tap)  # the restore taps happen while remote control is still on


def test_lcd_off_starting_from_another_disp_mode(disp_peek):
    body = DispBody()
    body.disp = 2
    disp_peek.append(body)
    with _rig(body, ["--lcd-off"]) as rig:
        assert rig.wait(lambda: rig.app.controls.lcd_state == "off", 15)
        assert rig.app.controls.disp_start == 2
    assert body.disp == 2


def test_lcd_off_without_peek_never_taps_blind(monkeypatch):
    def no_peek(link, addr, length):
        raise komabridge.CameraError("501 Syntax error in parameters or arguments.")
    monkeypatch.setattr(komabridge, "peek", no_peek)
    body = DispBody()
    with _rig(body, ["--lcd-off"]) as rig:
        assert rig.wait(lambda: rig.app.controls.lcd_state == "failed", 15)
        assert rig.wait(lambda: rig.app.sup.state == "streaming", 10)
    assert not any(a == "keyset,3,22" for a, _ in body.cctl_log)


def test_focus_snap_distance_and_half_press():
    body = DispBody()
    body.ram[0x058] = 2  # FOCUS = SNAP, set on the camera
    with _rig(body, ["--focus", "1"]) as rig:
        assert rig.wait(lambda: rig.app.sup.state == "streaming", 15)
        assert rig.wait(lambda: any(a == "keyset,1,26" for a, _ in body.cctl_log), 10)
        assert body.ram[0x8B5] == 0
        assert not rig.app.controls.warnings


def test_focus_warns_when_focus_mode_is_not_snap():
    body = DispBody()
    body.ram[0x058] = 6  # SPOT AF
    with _rig(body, ["--focus", "1.5"]) as rig:
        assert rig.wait(lambda: any("not SNAP" in w for w in rig.app.controls.warnings), 15)
        assert body.ram[0x8B5] == 1


def test_af_once_only_on_first_arm():
    body = DispBody()
    with _rig(body, ["--af-once"]) as rig:
        assert rig.wait(lambda: rig.app.sup.state == "streaming", 15)
        body.remote = 0  # forces a re-arm
        assert rig.wait(lambda: rig.app.sup.arms >= 2 and rig.app.sup.state == "streaming", 20)
    assert sum(1 for a, _ in body.cctl_log if a == "keyset,1,26") == 1


def test_ev_and_img_set_respect_kset_busy_window():
    """--ev at arm, then runtime EV + IMG SET over POST /control. The mock answers -KSET busy for 2 s after a stock
    write (HW: 6-8 s after an EV change); komacam spaces stock writes and retries, and both land."""
    body = DispBody()
    body.kset_busy_after_s = 2.0
    with _rig(body, ["--ev", "0.3"], kset_spacing_s=2.5) as rig:
        assert rig.wait(lambda: rig.app.sup.state == "streaming", 15)
        assert body.ram[0x053] == 15
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", rig.port, timeout=5)
        conn.request("POST", "/control", body=json.dumps({"ev": -0.7, "img_set": "bwte"}),
                     headers={"Host": f"127.0.0.1:{rig.port}", "Content-Type": "application/json"})
        r = conn.getresponse()
        assert r.status == 200, r.read()
        conn.close()
        assert body.ram[0x030] != 6
        assert rig.wait(lambda: rig.app.controls.pending() == {}, 20)
        assert body.ram[0x053] == 9 and body.ram[0x030] == 6


def test_control_endpoint_refuses_bad_input_and_foreign_origin():
    body = DispBody()
    with _rig(body) as rig:
        import http.client

        def post(obj, origin=None):
            conn = http.client.HTTPConnection("127.0.0.1", rig.port, timeout=5)
            h = {"Host": f"127.0.0.1:{rig.port}", "Content-Type": "application/json"}
            if origin:
                h["Origin"] = origin
            conn.request("POST", "/control", body=json.dumps(obj), headers=h)
            r = conn.getresponse()
            r.read()
            conn.close()
            return r.status
        assert post({"ev": 0.5}) == 400
        assert post({"ev": 3}) == 400
        assert post({"img_set": "sepia"}) == 400
        assert post({}) == 400
        assert post({"ev": 0}, origin="http://evil.example") == 403
        assert post({"ev": 0}, origin=f"http://127.0.0.1:{rig.port}") == 200


def test_host_sleep_wake_resets_session():
    body = DispBody()
    with _rig(body) as rig:
        assert rig.wait(lambda: rig.app.sup.state == "streaming", 15)
        stops0 = sum(1 for a, _ in body.cctl_log if a == "remtout")
        jump = 120.0
        rig.app.sup.wall = lambda: time.time() + jump  # the wall clock moved 2 min while monotonic did not
        assert rig.wait(lambda: rig.app.sup.wakes == 1, 5)
        assert rig.wait(lambda: sum(1 for a, _ in body.cctl_log if a == "remtout") > stops0, 15)
        assert rig.wait(lambda: rig.app.sup.state == "streaming" and rig.app.hub.age() < 0.3, 20)
        assert "host wake" in (rig.app.sup.last_end or "")


def test_bridge_restart_recovers():
    """An external bridge goes away mid-stream and a new one comes up on the same port (camera still in remote
    control): komacam shows 'no bridge', then arms and streams again."""
    body = DispBody()
    with _rig(body) as rig:
        assert rig.wait(lambda: rig.app.sup.state == "streaming", 15)
        rig.link.stop_live()
        rig.bridge.shutdown()
        rig.bridge.server_close()
        assert rig.wait(lambda: rig.app.sup.state == "no bridge", 10)
        time.sleep(1.5)
        rig.link = komabridge.Link(komabridge.MockBackend(body))
        rig.link.log = lambda text: None
        rig.bridge = komabridge.make_server(rig.link, port=rig.bport)
        rig.bridge.quiet = True
        threading.Thread(target=rig.bridge.serve_forever, daemon=True).start()
        assert rig.wait(lambda: rig.app.sup.state == "streaming" and rig.app.hub.age() < 0.3, 30)


def test_page_is_self_healing_canvas_reader():
    page = komacam.PAGE
    assert '<canvas id="c"' in page and "getReader()" in page and "AbortController" in page
    for const in ("STALL_MS=3000", "RETRY_MS=1000", "RETRY_MAX=3000", "IMG_RELOAD_MS=5000"):
        assert const in page
    assert r"'\r\n\r\n'" in page  # raw string: JS sees the escapes, not real CR LF


def test_stream_advertises_boundary():
    assert komacam.BOUNDARY == "komacam"
    assert "multipart/x-mixed-replace; boundary={BOUNDARY}" in Path(komacam.__file__).read_text()
