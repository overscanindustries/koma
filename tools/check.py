#!/usr/bin/env python3
"""One entry point to test and verify this repository. Offline; never touches a camera, card or USB.

    python tools/check.py test       fast suite; writes nothing tracked
    python tools/check.py test-all   everything, including slow and fixture-regenerating tests
    python tools/check.py verify     retained-evidence checks (read-only)
    python tools/check.py playground [args]   start the browser playground
    python tools/check.py list       show how every test file is classified

Every step runs with the Python that runs this script (sys.executable): use the one you installed
requirements.txt into. No PYTHONPATH is needed: the script sets it (repo root and tools/) for every child process.
Test files are tools/**/test_*.py and packaging/test_*.py; a checkout that lacks a tool simply has fewer tests.

Some checks need evidence that is not tracked in git (camera dumps, native proof reports). They run in this
checkout when it holds that evidence, else against REVERSE_REFERENCE_ROOT or the first entry of
`git worktree list` when one of those does (only read); otherwise they are reported as SKIP, not as failures.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
PACKAGING = ROOT / 'packaging'

# Test files are discovered by glob (tools/**/test_*.py). Only exceptions are named here.
# Self-running scripts (no unittest cases): command-line arguments, `{tmp}` is a fresh temp directory.
SCRIPTS = {
    'test_grdemu_flash': ([], 'fast'),
    'test_grdpatch': ([], 'fast'),
    'test_secondary_camera_timer': (['--output', '{tmp}/secondary_camera_timer.json'], 'fast'),
    'test_supplied_gpio_irq': (['--output', '{tmp}/supplied_gpio_irq.json'], 'fast'),
    'test_application_checksum_accel': (['--output', '{tmp}/application_checksum_accel.json'], 'fast'),
    'test_grd_lifecycle_capacity': (['--output', '{tmp}/lifecycle_capacity.json'], 'evidence'),
    'test_grd_lifecycle': (['--output', '{tmp}/lifecycle.json'], 'evidence'),
    # Rewrites analysis/verify-fixtures/DO_NOT_FLASH_*.bin (byte-identical when nothing changed); ~3.5 minutes.
    'test_grd_verify': ([], 'regenerates'),
}
# unittest modules that are not in the fast suite. Dotted module name -> (kind, reason).
UNITTEST_EXTRA = {
    'tools.grdscript.tests.test_cli': ('slow', '95 tests, ~30 s; two known failures (see tools/README.md)'),
    'tools.grdusb.test_grdusb': ('usb', 'simulated camera only; kept out of the fast suite as a USB-adjacent tool'),
}
# unittest modules that read evidence not tracked in git; they run against the evidence root.
UNITTEST_EVIDENCE = {'tools.grdlook.test_verify_bw_feature', 'tools.test_monitor_checksum_accel',
                     'tools.test_evidence_verification'}
# Tone Lab tests create analysis/playground-tone/session-*/ (new untracked directories, nothing tracked changes).
PLAYGROUND_NOTE = {'tools.grdlook.test_playground'}
# Koma tests are pytest-style (plain functions, sibling imports); tools/koma/tests/run_tests.py runs them.
KOMA_TESTS = 'tools.koma.tests'
# Modules that import pytest (not in requirements.txt); test-all runs them with `python -m pytest`.
PYTEST_MODULES = {'tools.komacam.test_komacam', 'tools.komacam.test_komacam_mode3'}
# Evidence a checkout must hold for evidence steps to run in place.
EVIDENCE_MARKERS = ('analysis/camera-dumps', 'analysis/bw-tone-prototype-v2/native-gating.json')
# A test that fails only because something not in this checkout is missing is reported as skipped, with the reason:
# Ricoh's 2.40 firmware (never shipped; tools/koma/firmware.py finds it from KOMA_FIRMWARE or firmware/original/), or
# an optional module requirements.txt does not install (tool-building and platform-only modules).
MISSING_FIRMWARE = 'no stock GR DIGITAL 2.40 firmware given'
# A research directory: a test reading a file under one of these is skipped only when the directory is absent
# altogether (a public checkout), never when one file in it is missing.
RESEARCH_DIRS = ('analysis', 'archive', 'evidence', 'firmware')
OPTIONAL_MODULES = ('fontTools', 'AppKit', 'Foundation', 'objc', 'playwright', 'pystray', 'pyvirtualcam', 'cv2', 'pytest')
RUNNER = [str(Path(__file__).resolve()), '_unittest']


def python(_root=None):
    return sys.executable


def env_for(root):
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join([str(root / 'tools'), str(root)] + ([env['PYTHONPATH']] if env.get('PYTHONPATH') else []))
    env.pop('PYTHONOPTIMIZE', None)  # verifiers refuse -O; tests rely on assertions
    return env


def has_evidence(root):
    return all((root / marker).exists() for marker in EVIDENCE_MARKERS)


def evidence_root():
    """This checkout if it holds the evidence, else REVERSE_REFERENCE_ROOT / the first worktree, else None."""
    if has_evidence(ROOT):
        return ROOT
    candidates = []
    if os.environ.get('REVERSE_REFERENCE_ROOT'):
        candidates.append(Path(os.environ['REVERSE_REFERENCE_ROOT']))
    try:
        out = subprocess.run(['git', '-C', str(ROOT), 'worktree', 'list', '--porcelain'], capture_output=True, text=True, check=True).stdout
        candidates.append(Path(out.splitlines()[0].split(' ', 1)[1]))
    except (OSError, subprocess.CalledProcessError, IndexError, ValueError):
        pass
    return next((path for path in candidates if has_evidence(path)), None)


def discover():
    """All test files under tools/, as (dotted module, path)."""
    return [('.'.join(path.relative_to(ROOT).with_suffix('').parts), path) for path in sorted(TOOLS.rglob('test_*.py'))]


def plan(everything):
    """Ordered steps: (label, kind, root, argv). kind is only used for the summary."""
    steps, plain, evidence_units = [], [], []
    for module, path in discover():
        short = module.rsplit('.', 1)[-1]
        if module.startswith(KOMA_TESTS + '.'):
            continue  # pytest-style; run through their own runner (added below for test-all)
        if module in PYTEST_MODULES:
            if everything:
                steps.append((module, 'slow', None, ['-m', 'pytest', '-q', str(path)]))
            continue
        if short in SCRIPTS:
            args, kind = SCRIPTS[short]
            if kind == 'fast' or everything:
                steps.append((short, kind, None, [str(path)] + args))
        elif module in UNITTEST_EVIDENCE:
            evidence_units.append(module)
        elif module in UNITTEST_EXTRA:
            if everything:
                steps.append((module, UNITTEST_EXTRA[module][0], None, RUNNER + [module]))
        else:
            plain.append(module)
    if everything and (TOOLS / 'koma/tests/run_tests.py').exists():
        steps.append(('koma tests (needs KOMA_FIRMWARE)', 'slow', 'firmware', [str(TOOLS / 'koma/tests/run_tests.py')]))
    if sorted(PACKAGING.glob('test_*.py')):
        # discovered from packaging/ itself (on sys.path), so it never shadows the PyPI package named `packaging`
        steps.insert(0, ('packaging tests', 'fast', None, RUNNER + ['--discover', str(PACKAGING)]))
    if plain:
        steps.insert(0, ('unittest (%d modules)' % len(plain), 'fast', None, RUNNER + plain))
    if evidence_units:
        steps.insert(1, ('unittest on evidence (%d modules)' % len(evidence_units), 'evidence', 'evidence', ['-m', 'unittest'] + evidence_units))
    # script tests that need untracked evidence run against the evidence root
    steps = [(label, kind, 'evidence' if kind == 'evidence' else root, argv) for label, kind, root, argv in steps]
    return steps


def firmware_available():
    """KOMA_FIRMWARE names a file, or the research checkout's copy is here (tools/koma/firmware.py DEFAULT_FIRMWARE)."""
    if os.environ.get('KOMA_FIRMWARE'):
        return Path(os.environ['KOMA_FIRMWARE']).expanduser().is_file()
    return any((ROOT / p).is_file() for p in ('firmware/extracted/GR DIGITAL Update/Firmware rel 2.40/qrflect3',
                                              'firmware/derived/grd240-armbe.bin'))


def missing_reason(text):
    """Why a failure is really an absent input (see MISSING_FIRMWARE, OPTIONAL_MODULES), or None."""
    if MISSING_FIRMWARE in text:
        return 'needs the stock 2.40 firmware (set KOMA_FIRMWARE)'
    import re
    for path in re.findall(r"No such file or directory: '([^']+)'", text):
        try:
            top = Path(path).resolve().relative_to(ROOT.resolve()).parts[0]
        except (ValueError, IndexError):
            continue
        if top in RESEARCH_DIRS and not (ROOT / top).is_dir():
            return f'needs research files ({top}/) that this checkout does not have'
    for name in OPTIONAL_MODULES:
        if f"No module named '{name}'" in text or f'No module named "{name}"' in text:
            return f'optional module {name} is not installed'
    return None


def cmd_unittest_runner(argv):
    """`check.py _unittest [--discover DIR | MODULE...]`: unittest, with absent-input failures reported as skips."""
    import unittest

    class Result(unittest.TextTestResult):
        def _maybe_skip(self, test, err, record):
            reason = missing_reason(self._exc_info_to_string(err, test))
            if reason:
                self.addSkip(test, reason)
            else:
                record(test, err)

        def addError(self, test, err):
            self._maybe_skip(test, err, super().addError)

        def addFailure(self, test, err):
            self._maybe_skip(test, err, super().addFailure)

    for path in (str(TOOLS), str(ROOT)):  # what env_for puts on PYTHONPATH, for a direct `check.py _unittest`
        if path not in sys.path:
            sys.path.insert(0, path)
    loader = unittest.TestLoader()
    if argv[:1] == ['--discover']:
        sys.path.insert(0, argv[1])
        suite = loader.discover(argv[1], pattern='test_*.py', top_level_dir=argv[1])
    else:
        suite = loader.loadTestsFromNames(argv)
    result = unittest.TextTestRunner(resultclass=Result, verbosity=1).run(suite)
    absent = {}
    for test, reason in result.skipped:
        if reason.startswith(('needs the stock', 'optional module', 'needs research files')):
            absent.setdefault(reason, []).append(test.id())
    other = {}
    for test, reason in result.skipped:
        if reason not in absent:
            other[reason] = other.get(reason, 0) + 1
    for reason, count in sorted(other.items(), key=lambda item: -item[1]):
        print(f'skipped {count} test(s) by the test itself: {reason}', file=sys.stderr)
    for reason, ids in sorted(absent.items()):
        print(f'SKIPPED {len(ids)} test(s), {reason}:', file=sys.stderr)
        for test_id in ids:
            print(f'    {test_id}', file=sys.stderr)
    return 0 if result.wasSuccessful() else 1


def run_streaming(command, root, keep=80):
    """Run a step with its output shown live; return (exit code, its last `keep` lines)."""
    import collections
    tail = collections.deque(maxlen=keep)
    with subprocess.Popen(command, cwd=root, env=env_for(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors='replace', bufsize=1) as proc:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            tail.append(line)
    return proc.returncode, list(tail)


def select(steps, only):
    """--only TEXT: the steps whose label contains TEXT (case-insensitive)."""
    return [step for step in steps if not only or only.lower() in step[0].lower()]


def run_steps(steps, title):
    results, failures = [], []
    with tempfile.TemporaryDirectory(prefix='grd-check-') as tmp:
        for label, kind, where, argv in steps:
            root = ROOT
            if where == 'firmware' and not firmware_available():
                print(f'--- {label}: SKIPPED, no stock 2.40 firmware (set KOMA_FIRMWARE to your own qrflect3 or gr-240.exe)', flush=True)
                results.append((label, 'SKIP', 0.0, kind))
                continue
            if where == 'evidence':
                root = evidence_root()
                if root is None:
                    print(f'--- {label}: SKIPPED, no checkout with the evidence (set REVERSE_REFERENCE_ROOT)', flush=True)
                    results.append((label, 'SKIP', 0.0, kind))
                    continue
            argv = [a.replace('{tmp}', tmp) for a in argv]
            # script paths are given for ROOT; re-point them when running against another checkout
            argv = [str(root / Path(a).relative_to(ROOT)) if a.startswith(str(ROOT) + os.sep) else a for a in argv]
            note = '' if root == ROOT else f'  [run in {root}]'
            print(f'--- {label} ({kind}){note}', flush=True)
            start = time.time()
            if kind == 'read-only':  # verifier reports are large JSON; show them only when something fails
                done = subprocess.run([python(root)] + argv, cwd=root, env=env_for(root), capture_output=True, text=True)
                code = done.returncode
                print(done.stdout[:200] + ' ...' if code == 0 else done.stdout + done.stderr, flush=True)
            else:
                code, tail = run_streaming([python(root)] + argv, root)
                if code != 0:
                    failures.append((label, code, tail))
            results.append((label, 'ok' if code == 0 else 'FAIL', time.time() - start, kind))
    print(f'\n{title}')
    for label, code, tail in failures:  # repeated here so a failure is visible in the last screen of output
        print(f'\n=== {label} FAILED (exit {code}); last {len(tail)} lines:')
        print(''.join(tail), end='')
    for label, status, seconds, kind in results:
        print(f'  {status:5s} {seconds:6.1f}s  {label} [{kind}]')
    failed = [r for r in results if r[1] == 'FAIL']
    skipped = [r for r in results if r[1] == 'SKIP']
    print(f'{len(results) - len(failed) - len(skipped)} passed, {len(failed)} failed, {len(skipped)} skipped')
    return 1 if failed else 0


def cmd_test(_):
    print('Fast suite: writes nothing tracked.' + (' Tone Lab tests add untracked analysis/playground-tone/session-*/ directories.'
                                                   if (TOOLS / 'grdlook').is_dir() else ''))
    return run_steps(select(plan(False), _.only), 'test')


def cmd_test_all(_):
    print('FULL SUITE. Includes slow tests; test_grd_verify rewrites analysis/verify-fixtures/ (byte-identical output expected).')
    print('Run it in a worktree and check `git status` afterwards.')
    return run_steps(select(plan(True), _.only), 'test-all')


def cmd_verify(_):
    root = evidence_root()
    if root is None:
        print('verify needs the retained evidence (untracked). Set REVERSE_REFERENCE_ROOT to a checkout that has it.', file=sys.stderr)
        return 2
    steps = [('verify_saved_evidence (13 checks)', 'read-only', 'evidence', [str(root / 'tools/verify_saved_evidence.py')]),
             ('grdlook.verify_bw_feature', 'read-only', 'evidence', ['-m', 'tools.grdlook.verify_bw_feature'])]
    return run_steps(steps, 'verify')


def cmd_playground(args):
    command = [python(ROOT), '-m', 'tools.grdui.playground_web'] + (args.extra or ['--port', '0', '--open'])
    return subprocess.call(command, cwd=ROOT, env=env_for(ROOT))


def cmd_list(_):
    for path in sorted(PACKAGING.glob('test_*.py')):
        print(f"{'unittest/fast':28s} {path.relative_to(ROOT)}")
    for module, path in discover():
        short = module.rsplit('.', 1)[-1]
        if module.startswith(KOMA_TESTS + '.'):
            kind = 'koma-runner/slow'
        elif module in PYTEST_MODULES:
            kind = 'pytest/slow'
        elif short in SCRIPTS:
            kind = 'script/' + SCRIPTS[short][1]
        elif module in UNITTEST_EVIDENCE:
            kind = 'unittest/evidence'
        elif module in UNITTEST_EXTRA:
            kind = 'unittest/' + UNITTEST_EXTRA[module][0]
        else:
            kind = 'unittest/fast' + ('+writes-untracked' if module in PLAYGROUND_NOTE else '')
        print(f'{kind:28s} {path.relative_to(ROOT)}')
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['_unittest']:
        return cmd_unittest_runner(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    for name, function in (('test', cmd_test), ('test-all', cmd_test_all), ('verify', cmd_verify), ('list', cmd_list)):
        command = sub.add_parser(name)
        command.set_defaults(function=function)
        if name in ('test', 'test-all'):
            command.add_argument('--only', metavar='TEXT', help='run only the steps whose label contains TEXT '
                                 '(e.g. --only evidence, --only packaging); `list` shows the files, the run shows labels')
    play = sub.add_parser('playground', help='arguments after -- go to tools.grdui.playground_web')
    play.add_argument('extra', nargs=argparse.REMAINDER)
    play.set_defaults(function=cmd_playground)
    args = parser.parse_args(argv)
    return args.function(args)


if __name__ == '__main__':
    sys.exit(main())
