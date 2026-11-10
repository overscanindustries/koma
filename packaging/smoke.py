"""Smoke test of an installed `koma` (the koma-gr wheel), against the simulated camera only. Never opens USB.

    python3 packaging/smoke.py PATH/TO/koma [--port 9718] [--require-usb]

Runs `koma --help`, `koma --version`, `koma doctor` (its exit status counts only with --require-usb), then
`koma --mock --no-browser` on --port (9700-9720: never 8765, where a real camera may be served) with its own data
folder, reads / (the app) and /status, /install (the card package) from it, and stops it with SIGTERM (Windows: the
process is ended; the bridge stops when its stdin closes). Exit 0 when every step passed.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request


def get(port: int, path: str, timeout: float = 2.0) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
        return response.read()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("koma")
    ap.add_argument("--port", type=int, default=9718)
    ap.add_argument("--require-usb", action="store_true")
    a = ap.parse_args(argv)
    if not 9700 <= a.port <= 9720:
        print("smoke: use a port in 9700-9720", file=sys.stderr)
        return 2
    failures = []

    def run(*args):
        done = subprocess.run([a.koma, *args], capture_output=True, text=True, timeout=120)
        print(f"$ koma {' '.join(args)}  -> {done.returncode}\n{done.stdout}{done.stderr}")
        return done

    if run("--help").returncode != 0:
        failures.append("--help")
    version = run("--version")
    if version.returncode != 0 or "Koma 1.0" not in version.stdout + version.stderr:
        failures.append("--version")
    doctor = run("doctor")
    if a.require_usb and doctor.returncode != 0:
        failures.append("doctor")

    with tempfile.TemporaryDirectory() as data:
        kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
        proc = subprocess.Popen([a.koma, "--mock", "--no-browser", "--port", str(a.port), "--webcam-port",
                                 str(a.port + 1), "--data-dir", data], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, **kw)
        try:
            for _ in range(120):
                try:
                    get(a.port, "/api")
                    break
                except OSError:
                    time.sleep(0.5)
            page = get(a.port, "/").decode("utf-8", "replace")
            if "<title>Koma</title>" not in page:
                failures.append("/ is not the app")
            status = json.loads(get(a.port, "/status"))
            if not (status.get("simulated") and status.get("connected")):
                failures.append(f"/status: {status}")
            install = json.loads(get(a.port, "/install"))
            package = install.get("package") or {}
            if not package or package.get("sha256") != package.get("published_sha256"):
                failures.append(f"/install: {install}")
            print(f"/ {len(page)} B; /status simulated={status.get('simulated')} connected={status.get('connected')}; "
                  f"/install package {package.get('name')} {package.get('version')}")
        except Exception as error:  # noqa: BLE001
            failures.append(f"mock: {type(error).__name__}: {error}")
        finally:
            if os.name == "nt":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.send_signal(signal.SIGTERM)
            try:
                out, err = proc.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = proc.communicate()
            print(f"$ koma --mock ...  -> {proc.returncode}\n{out}{err}")
    for _ in range(50):
        try:
            get(a.port, "/api", 0.5)
            time.sleep(0.2)
        except OSError:
            break
    else:
        failures.append("the port is still served after koma stopped")
    print("smoke: " + ("passed" if not failures else "FAILED: " + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
