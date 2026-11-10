"""`--self-test`: the launcher's jobs, run once inside the real process (with its tray UI when there is one) and
reported as JSON. Meant for a built bundle against the simulated camera (`--mock`); it presses no camera button
beyond what the outside webcam's own arm does.

  1. the bridge comes up and the page reaches it (GET / and GET /api); the launcher has not read /status by itself
  2. a look (the menu opening) reads the camera line from the bridge; it goes when the look ends
  3. a webcam started outside the launcher (komacam, as the app's Webcam screen would run it) shows as
     "Webcam running", and goes from the menu when it stops; it leaves remote control (the bridge reports tether off)
  4. the bridge is killed and the launcher starts it again
  5. Quit: the bridge stops, the port is free
"""
from __future__ import annotations

import json
import os
import signal
import threading
import time

from . import model, supervisor, words


def wait_for(sup, test, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test(sup.snapshot()):
            return True
        time.sleep(0.2)
    return False


def menu_text(snap) -> list[str]:
    return ["---" if i.kind == "separator" else (i.text if i.enabled or i.kind == "status" else f"({i.text})")
            for i in model.menu(snap)]


def run(sup: supervisor.Supervisor, done=None) -> dict:
    """Runs the checks, asks the supervisor to quit, and returns the report (also passed to done(report))."""
    report: dict = {"checks": [], "menus": {}}
    exited = threading.Event()
    sup.on_exit.append(exited.set)

    def check(name: str, ok: bool, detail: str = "") -> bool:
        report["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        return ok

    def menu(stage: str):
        report["menus"][stage] = menu_text(sup.snapshot())

    t0 = time.monotonic()
    webcam = None
    try:
        if check("bridge running", wait_for(sup, lambda s: s.phase == "running", 60), sup.snapshot().phase):
            menu("running")
            try:
                code, body = supervisor.http("GET", sup.port, "/", 5.0)
            except supervisor.Down as error:
                code, body = 0, str(error).encode()
            check("page served at /", code == 200 and b'id="app"' in body, f"HTTP {code}, {len(body)} bytes")
            check("no camera read without a look", "/status" not in list(sup.reads), ", ".join(sup.reads))
            status = supervisor.get_json(sup.port, "/status", 3.0) or {}
            check("status answers", status.get("bridge") == "komabridge",
                  f"bridge {status.get('bridge_version')}, simulated={status.get('simulated')}")

            snap = sup.look(True, wait=5.0)
            line = model.camera_line(snap.status) if snap.status is not None else ""
            menu("menu open")
            check("camera line on a look", line.startswith("Camera: GR DIGITAL"), line)
            sup.look(False)
            check("camera line gone after the look", wait_for(sup, lambda s: s.status is None, 10))

            argv, env = supervisor.child_command(
                "webcam", ["--bridge-port", str(sup.port), "--port", str(sup.webcam_port), "--status-every", "60"])
            webcam = supervisor.Child("webcam", argv, env, sup.logs / "webcam.log").start()
            streaming = wait_for(sup, lambda s: s.webcam, 120)
            menu("webcam running")
            check("webcam line while it streams", streaming and words.WEBCAM_RUNNING in menu_text(sup.snapshot()),
                  f"webcam={sup.snapshot().webcam}")
            webcam.stop(40.0)
            webcam = None
            gone = wait_for(sup, lambda s: not s.webcam, 30)
            tether = (supervisor.get_json(sup.port, "/status", 3.0) or {}).get("tether")
            check("webcam line gone after it stops, remote control off", gone and tether is False, f"tether={tether}")

            starts, pid = sup.bridge_starts, sup.bridge.pid if sup.bridge else None
            if pid:
                os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
            back = wait_for(sup, lambda s: s.phase == "running" and sup.bridge_starts > starts
                            and sup.bridge.pid != pid, 45)
            check("bridge restarted after it died", back, f"pid {pid} -> {sup.bridge.pid if sup.bridge else None}")
            menu("after restart")
    except Exception as error:  # noqa: BLE001
        check("no exception", False, f"{type(error).__name__}: {error}")
    finally:
        if webcam is not None:
            webcam.stop(40.0)
        sup.command("quit")
        quit_ok = exited.wait(90)
        check("quit finished", quit_ok)
        check("port free after quit", not supervisor.port_in_use(sup.port), f"port {sup.port}")
        report["ok"] = all(c["ok"] for c in report["checks"])
        report["seconds"] = round(time.monotonic() - t0, 1)
        report["bridge_starts"] = sup.bridge_starts
    if done:
        done(report)
    return report


def dumps(report: dict) -> str:
    return json.dumps(report, indent=2)
