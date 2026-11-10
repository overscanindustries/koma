#!/usr/bin/env python3
"""End-to-end: the Koma launcher (tools/komalauncher) starts the bridge, and the app it opens loads in a browser.

The launcher runs as a user would run it, as a separate process (`python -m komalauncher --mock --headless
--no-browser`, the simulated camera), on port 8900. Headless Chrome then opens the address the launcher publishes in
launcher.json, the same one Open Koma and Copy address use. Everything here is MOCK.

    (cd tools/komaapp && npm run build)
    python3 -m pytest tools/komaset/web/test_e2e_launcher.py -v
    KOMA_E2E_VIEWPORT=phone python3 -m pytest tools/komaset/web/test_e2e_launcher.py -v

Needs the build, the `playwright` Python package and a Chrome / Chromium; skipped otherwise.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1]
LAUNCHER = TOOLS / "komalauncher"
DIST = TOOLS / "komaapp" / "dist"

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

DESKTOP = {"width": 1280, "height": 860}
PHONE = {"width": 390, "height": 844}
VIEW = PHONE if os.environ.get("KOMA_E2E_VIEWPORT") == "phone" else DESKTOP
PORT = 8900


def launch(p):
    for kw in ({"channel": "chrome"}, {}):
        try:
            return p.chromium.launch(**kw)
        except Exception:  # noqa: BLE001
            continue
    for exe in sorted(Path.home().glob("Library/Caches/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell")):
        try:
            return p.chromium.launch(executable_path=str(exe))
        except Exception:  # noqa: BLE001
            continue
    return None


def port_open(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


@unittest.skipIf(sync_playwright is None, "playwright not installed")
@unittest.skipUnless((DIST / "index.html").is_file(), "the app is not built (cd tools/komaapp && npm run build)")
class LauncherE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = launch(cls.pw)
        if cls.browser is None:
            cls.pw.stop()
            raise unittest.SkipTest("no Chrome / Chromium for playwright")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def start_launcher(self, data: str) -> subprocess.Popen:
        env = {**os.environ, "PYTHONPATH": str(LAUNCHER)}
        proc = subprocess.Popen([sys.executable, "-m", "komalauncher", "--mock", "--headless", "--no-browser",
                                 "--port", str(PORT), "--webcam-port", str(PORT + 1), "--data-dir", data],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        state_file = Path(data) / "launcher.json"
        for _ in range(300):
            if state_file.exists() and json.loads(state_file.read_text() or "{}").get("phase") == "running":
                return proc
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        proc.kill()
        out, err = proc.communicate()
        self.fail(f"the launcher did not reach running: {out[-800:]} {err[-800:]}")

    def stop_launcher(self, proc: subprocess.Popen):
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0)

    def test_the_app_opens_from_the_launchers_address(self):
        with tempfile.TemporaryDirectory() as data:
            proc = self.start_launcher(data)
            try:
                url = json.loads((Path(data) / "launcher.json").read_text())["url"]
                self.assertEqual(url, f"http://localhost:{PORT}/")
                ctx = self.browser.new_context(viewport=VIEW, reduced_motion="reduce")
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("console", lambda m: errors.append(m.text) if m.type == "error"
                        and "Failed to load resource" not in m.text else None)
                bad = []
                page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 400
                        and not any(p in r.url for p in ("/status", "/settings", "/photos", "/live", "/ram")) else None)
                page.goto(url)
                page.wait_for_function("() => document.querySelector('#app') && document.querySelector('#app')"
                                       ".children.length > 0 && /koma/i.test(document.body.innerText)",
                                       timeout=20000)
                # the simulated camera is connected through the launcher's bridge: the app leaves Connect
                page.wait_for_function("() => !/Looking for the camera/.test(document.body.innerText)", timeout=20000)
                status = page.evaluate("() => fetch('/status').then(r => r.json())")
                self.assertTrue(status["connected"])
                self.assertTrue(status["simulated"])
                scroll = page.evaluate("() => document.documentElement.scrollWidth - "
                                       "document.documentElement.clientWidth")
                self.assertLessEqual(scroll, 0, "no horizontal scroll")
                self.assertEqual(errors, [])
                self.assertEqual(bad, [])
                ctx.close()
            finally:
                self.stop_launcher(proc)
            self.assertFalse(port_open(PORT), "Quit leaves no bridge behind")

    def test_page_follows_quit_and_a_new_launch(self):
        """Quit Koma: the open page shows its offline state ('Koma is not running on this
        computer.'); a new launch on the same port: the page comes back by itself, no reload."""
        with tempfile.TemporaryDirectory() as data:
            proc = self.start_launcher(data)
            ctx = self.browser.new_context(viewport=VIEW, reduced_motion="reduce")
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            try:
                page.goto(f"http://localhost:{PORT}/")
                page.wait_for_function("() => /koma/i.test(document.body.innerText)", timeout=20000)
            finally:
                self.stop_launcher(proc)
            page.wait_for_function("() => /not running/i.test(document.body.innerText)", timeout=20000)
            proc = self.start_launcher(data)
            try:
                page.wait_for_function("() => !/not running/i.test(document.body.innerText)", timeout=30000)
            finally:
                self.stop_launcher(proc)
            self.assertEqual(errors, [])
            ctx.close()


if __name__ == "__main__":
    unittest.main()
