#!/usr/bin/env python3
"""The committed Koma app build (tools/komaapp/dist) is the build of the committed source: rebuild into a temp dir and
compare every file, byte for byte. Fails when someone changed src/ and forgot `npm run build` (or the reverse).

    python3 -m pytest tools/komaset/web/test_app_dist.py -v

Needs node and the app's node_modules (cd tools/komaapp && npm ci); skipped otherwise.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "komaapp"
DIST = APP / "dist"
VITE = APP / "node_modules" / ".bin" / "vite"


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@unittest.skipUnless(shutil.which("node") and VITE.exists(), "node or the app's node_modules missing")
class AppDistTest(unittest.TestCase):
    def test_committed_dist_matches_a_fresh_build(self):
        self.assertTrue((DIST / "index.html").is_file(), "no committed build in tools/komaapp/dist")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            res = subprocess.run([str(VITE), "build", "--outDir", str(out), "--emptyOutDir", "--logLevel", "error"],
                                 cwd=APP, capture_output=True, text=True, timeout=300)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            fresh, committed = tree(out), tree(DIST)
            self.assertEqual(sorted(fresh), sorted(committed), "file list differs: run `npm run build` in tools/komaapp")
            changed = [name for name in fresh if fresh[name] != committed[name]]
            self.assertEqual(changed, [], "these files differ from a fresh build: run `npm run build` in tools/komaapp")


if __name__ == "__main__":
    unittest.main()
