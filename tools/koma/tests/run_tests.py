#!/usr/bin/env python3
"""Run the Koma tests: pytest when it is importable, else a minimal built-in runner (plain functions, parametrize,
tmp_path/capsys fixtures) so the suite runs on the project venv, which has no pytest.

  .venv/bin/python tools/koma/tests/run_tests.py [-k substring]
"""
from __future__ import annotations

import contextlib
import importlib
import io
import sys
import tempfile
import traceback
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))


def _shim():
    """A tiny pytest stand-in: raises / mark.parametrize only."""
    m = types.ModuleType("pytest")

    class _Raises:
        def __init__(self, exc, match=None):
            self.exc, self.match, self.value = exc, match, None

        def __enter__(self):
            return self

        def __exit__(self, t, v, tb):
            if t is None:
                raise AssertionError(f"did not raise {self.exc}")
            if not issubclass(t, self.exc):
                return False
            if self.match:
                import re
                assert re.search(self.match, str(v)), f"{v!s} !~ {self.match}"
            self.value = v
            return True

    def parametrize(name, values):
        def deco(fn):
            fn._params = (name, values)
            return fn
        return deco
    m.raises = _Raises
    m.mark = types.SimpleNamespace(parametrize=parametrize)
    return m


class _Capsys:
    def __init__(self):
        self.buf = io.StringIO()

    def readouterr(self):
        v = self.buf.getvalue()
        self.buf.seek(0)
        self.buf.truncate()
        return types.SimpleNamespace(out=v, err="")


def builtin(k=None):
    sys.modules.setdefault("pytest", _shim())
    passed = failed = selected = 0
    for path in sorted(HERE.glob("test_*.py")):
        mod = importlib.import_module(path.stem)
        for name in [n for n in dir(mod) if n.startswith("test_")]:
            fn = getattr(mod, name)
            if k and k not in name and k not in path.stem:
                continue
            selected += 1
            runs = [({}, "")]
            if hasattr(fn, "_params"):
                pn, vals = fn._params
                runs = [({pn: v}, f"[{v!r}]") for v in vals]
            for kwargs, tag in runs:
                cap = _Capsys()
                try:
                    with tempfile.TemporaryDirectory() as t, contextlib.redirect_stdout(cap.buf):
                        args = {}
                        params = fn.__code__.co_varnames[:fn.__code__.co_argcount]
                        if "tmp_path" in params:
                            args["tmp_path"] = Path(t)
                        if "capsys" in params:
                            args["capsys"] = cap
                        fn(**args, **kwargs)
                    passed += 1
                    print(f"PASS {path.stem}::{name}{tag}")
                except Exception:
                    failed += 1
                    print(f"FAIL {path.stem}::{name}{tag}")
                    traceback.print_exc()
    if k and selected == 0:
        print(f"ERROR: -k {k!r} selected zero tests")
        return 2
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        import pytest
    except ImportError:
        k = args[args.index("-k") + 1] if "-k" in args else None
        sys.exit(builtin(k))
    sys.exit(pytest.main([str(HERE), "-q", "-p", "no:cacheprovider"] + args))
