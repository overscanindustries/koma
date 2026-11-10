"""Smoke test of an installed `koma` (the koma-gr wheel), against the simulated camera only. Never opens USB.

    python3 packaging/smoke.py PATH/TO/koma [--port 9718] [--require-usb] [--firmware PATH]

Runs `koma --help`, `koma --version`, `koma doctor` (its exit status counts only with --require-usb; its package row
must be ok), then `koma --mock --no-browser` on --port (9700-9720: never 8765, where a real camera may be served) with
its own data folder, reads / (the app), /status and /install (the card package) from it, and stops it with SIGTERM
(Windows: Ctrl-Break, which quits the way Ctrl-C does). Exit 0 when every step passed.

The Koma package ships as a delta and is rebuilt from Ricoh's 2.40 file, which no download carries. Without
--firmware, `koma` runs without KOMA_FIRMWARE, as on a fresh install: the installed delta must be the one
dist-release/SHA256SUMS records, the package must report not ready, and /install/GRMOD.BIN must answer 409 no_stock.
With --firmware PATH (your own copy of Ricoh's file; never in this repository or in CI), the package must be ready and
/install/GRMOD.BIN must be the published build (dist-release/SHA256SUMS).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def get(port: int, path: str, timeout: float = 2.0) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
        return response.read()


def get_status(port: int, path: str, timeout: float = 60.0) -> tuple[int, bytes]:
    try:
        return 200, get(port, path, timeout)
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def published() -> dict:
    """dist-release/SHA256SUMS: file name -> sha256."""
    sums = {}
    for line in (ROOT / "dist-release" / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if line.strip():
            sha, name = line.split(None, 1)
            sums[name.strip().lstrip("*")] = sha
    return sums


def check_install(port: int, install: dict, with_stock: bool) -> list[str]:
    """The card package and the /IROM installer as this installed copy serves them."""
    failures = []
    sums = published()
    package = install.get("package") or {}
    name = package.get("name")
    if not package or name not in sums or package.get("published_sha256") != sums[name]:
        return [f"/install package is not the one dist-release/SHA256SUMS records: {install}"]
    delta = sums.get(f"{name}.delta")
    if not delta or package.get("delta_sha256") != delta or package.get("delta_published_sha256") != delta:
        failures.append(f"/install: the installed delta is not {name}.delta from SHA256SUMS: {package}")
    installer = install.get("installer") or {}
    if not installer or installer.get("sha256") != installer.get("published_sha256"):
        failures.append(f"/install installer: {installer}")
    if bool(package.get("ready")) != with_stock:
        failures.append(f"/install package ready={package.get('ready')}, expected {with_stock}")
    status, body = get_status(port, package.get("route") or "/install/GRMOD.BIN")
    if with_stock:
        if status != 200 or hashlib.sha256(body).hexdigest() != sums[name] or len(body) != package.get("bytes"):
            failures.append(f"{package.get('route')}: HTTP {status}, {len(body)} B, "
                            f"sha256 {hashlib.sha256(body).hexdigest()}; expected {sums[name]}")
    else:
        code = json.loads(body).get("code") if status != 200 else None
        if (status, code) != (409, "no_stock"):
            failures.append(f"{package.get('route')} without Ricoh's file: HTTP {status} {code}, expected 409 no_stock")
    return failures


def remove(folder: str, seconds: float = 30.0) -> bool:
    """Remove the data folder; on Windows a file a process still holds cannot be removed, so retry for a while."""
    end = time.monotonic() + seconds
    while True:
        try:
            shutil.rmtree(folder)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if time.monotonic() > end:
                return False
            time.sleep(0.5)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("koma")
    ap.add_argument("--port", type=int, default=9718)
    ap.add_argument("--require-usb", action="store_true")
    ap.add_argument("--firmware", help="your own copy of Ricoh's 2.40 file: also check the package rebuilds from it")
    a = ap.parse_args(argv)
    if not 9700 <= a.port <= 9720:
        print("smoke: use a port in 9700-9720", file=sys.stderr)
        return 2
    env = {k: v for k, v in os.environ.items() if k != "KOMA_FIRMWARE"}
    if a.firmware:
        env["KOMA_FIRMWARE"] = str(Path(a.firmware).expanduser().resolve())
    failures = []

    def run(*args):
        done = subprocess.run([a.koma, *args], capture_output=True, text=True, timeout=120, env=env)
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
    if not any(line.split()[:2] == ["ok", "package"] for line in doctor.stdout.splitlines()):
        failures.append("doctor: the package row is not ok")

    data = tempfile.mkdtemp(prefix="koma-smoke-")
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
    proc = subprocess.Popen([a.koma, "--mock", "--no-browser", "--port", str(a.port), "--webcam-port",
                             str(a.port + 1), "--data-dir", data], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env, **kw)
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
        failures += check_install(a.port, install, with_stock=bool(a.firmware))
        package = install.get("package") or {}
        print(f"/ {len(page)} B; /status simulated={status.get('simulated')} connected={status.get('connected')}; "
              f"/install package {package.get('name')} {package.get('version')} ready={package.get('ready')} "
              f"delta {package.get('delta_sha256')}")
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
            failures.append("koma did not stop within 60 s")
        print(f"$ koma --mock ...  -> {proc.returncode}\n{out}{err}")
    if proc.returncode != 0:
        failures.append(f"koma --mock exited {proc.returncode} when asked to quit")
    for _ in range(50):
        try:
            get(a.port, "/api", 0.5)
            time.sleep(0.2)
        except OSError:
            break
    else:
        failures.append("the port is still served after koma stopped")
    if not remove(data):
        failures.append(f"the data folder is still in use 30 s after koma stopped: {data}")
    print("smoke: " + ("passed" if not failures else "FAILED: " + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
