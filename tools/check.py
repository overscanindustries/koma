#!/usr/bin/env python3
"""Run the repository's tests. Offline; never touches a camera, card or USB device.

    .venv/bin/python tools/check.py test       fast suite (no firmware needed)
    .venv/bin/python tools/check.py test-all   fast suite plus the slow tests and the Koma tests (needs KOMA_FIRMWARE)
    .venv/bin/python tools/check.py list       show how every test file is run

The script sets PYTHONPATH (tools/ and the repository root) for every child process.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'

# Test files are discovered by glob (tools/**/test_*.py). Only exceptions are named here.
# unittest modules left out of the fast suite: dotted module name -> reason.
SLOW = {'tools.grdusb.test_grdusb': 'simulated camera; USB-adjacent tool'}
# Modules that import pytest (not in requirements.txt); test-all runs them with `python -m pytest` when it is installed.
PYTEST_MODULES = {'tools.komacam.test_komacam', 'tools.komacam.test_komacam_mode3'}
# Koma tests are pytest-style (plain functions, sibling imports); tools/koma/tests/run_tests.py runs them.
KOMA_TESTS = 'tools.koma.tests'


def python():
    venv = ROOT / '.venv/bin/python'
    return str(venv) if venv.exists() else sys.executable


def env():
    e = dict(os.environ)
    e['PYTHONPATH'] = os.pathsep.join([str(TOOLS), str(ROOT)] + ([e['PYTHONPATH']] if e.get('PYTHONPATH') else []))
    e.pop('PYTHONOPTIMIZE', None)  # tests rely on assertions
    return e


def discover():
    """All test files under tools/, as (dotted module, path)."""
    return [('.'.join(p.relative_to(ROOT).with_suffix('').parts), p) for p in sorted(TOOLS.rglob('test_*.py'))]


def kind(module):
    if module.startswith(KOMA_TESTS + '.'):
        return 'koma'
    if module in PYTEST_MODULES:
        return 'pytest'
    if module in SLOW:
        return 'slow'
    return 'fast'


def plan(everything):
    """Ordered steps: (label, argv)."""
    mods = [(m, kind(m)) for m, _ in discover()]
    fast = [m for m, k in mods if k == 'fast']
    steps = [('unittest (%d modules)' % len(fast), ['-m', 'unittest'] + fast)]
    if everything:
        steps += [(m, ['-m', 'unittest', m]) for m, k in mods if k == 'slow']
        if subprocess.run([python(), '-c', 'import pytest'], capture_output=True).returncode == 0:
            steps += [(m, ['-m', 'pytest', '-q', str(ROOT / (m.replace('.', '/') + '.py'))]) for m, k in mods if k == 'pytest']
        else:
            print('note: pytest is not installed; skipping ' + ', '.join(sorted(PYTEST_MODULES)), flush=True)
        steps.append(('koma tests', [str(TOOLS / 'koma/tests/run_tests.py')]))
    return steps


def run(steps, title):
    results = []
    for label, argv in steps:
        print(f'--- {label}', flush=True)
        start = time.time()
        code = subprocess.call([python()] + argv, cwd=ROOT, env=env())
        results.append((label, code == 0, time.time() - start))
    print(f'\n{title}')
    for label, ok, seconds in results:
        print(f'  {"ok" if ok else "FAIL":5s} {seconds:6.1f}s  {label}')
    failed = sum(1 for r in results if not r[1])
    print(f'{len(results) - failed} passed, {failed} failed')
    return 1 if failed else 0


def cmd_test(_):
    return run(plan(False), 'test')


def cmd_test_all(_):
    if not os.environ.get('KOMA_FIRMWARE'):
        print('test-all needs KOMA_FIRMWARE set to your 2.40 firmware file (see firmware/README.md)', file=sys.stderr)
        return 2
    return run(plan(True), 'test-all')


def cmd_list(_):
    for module, path in discover():
        print(f'{kind(module):8s} {path.relative_to(ROOT)}')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    for name, function in (('test', cmd_test), ('test-all', cmd_test_all), ('list', cmd_list)):
        sub.add_parser(name).set_defaults(function=function)
    args = parser.parse_args(argv)
    return args.function(args)


if __name__ == '__main__':
    sys.exit(main())
