"""fastkeys: records match the reference modules; init posts period 20 only from the stock state."""
from __future__ import annotations

import struct

from _util import fw
from koma.features import fastkeys
from koma.linker import link, phase_a_lines
from koma.unitemu import Unit

Q, PERIOD = 0x2C9E28, 0x2C9E58


def _unit(period=50, qope=1):
    mod = link([fastkeys.make({})]).module
    u = Unit(mod, ram_edits={Q: struct.pack(">I", qope), PERIOD: struct.pack(">I", period)})
    u.stub(0x32AEE, "f32aee")
    return u


def test_before_bytes_match():          # reference byte equality: test_provenance (research checkout only)
    for p in fastkeys.make({}).patches:
        assert fw.ram(p.addr, len(p.before)) == p.before


def test_sites_not_in_phase_a():
    lines = sorted(phase_a_lines())
    for p in fastkeys.make({}).patches:
        assert not [x for x in lines if x < p.end and x + 32 > p.addr]


def test_install_applies_and_posts_period_20():
    u = _unit()
    assert u.install() == 0
    for p in fastkeys.make({}).patches:
        assert u.read(p.addr, len(p.after)) == p.after
    assert ("f32aee", 20) in u.calls
    assert u.callee_saved_ok


def test_no_post_unless_stock_period_and_queue():
    for kw in (dict(period=20), dict(qope=0)):
        u = _unit(**kw)
        assert u.install() == 0
        assert not [c for c in u.calls if c[0] == "f32aee"]


def test_cpu1_timeout_pinned_at_stock_50():
    """0xca55e `ldr r1, [r0, #0x1c]` (timeout = scan period) -> `movs r1, #50`; scan keeps the stock bytes."""
    pins = [p for p in fastkeys.make({}).patches if p.addr == 0xCA55C]
    assert len(pins) == 1
    assert fw.ram(0xCA55C, 6) == bytes.fromhex("481369c14813")       # ldr r0,=0x2c9e3c ; ldr r1,[r0,#0x1c] ; ...
    assert pins[0].after == bytes.fromhex("481321324813")              # movs r1, #50
    assert not [p for p in fastkeys.make({"cpu1": "scan"}).patches if p.addr == 0xCA55C]
    u = Unit(link([fastkeys.make({})]).module, ram_edits={Q: struct.pack(">I", 1), PERIOD: struct.pack(">I", 50)})
    u.stub(0x32AEE, "f32aee")
    assert u.install() == 0
    assert u.read(0xCA55E, 2) == bytes.fromhex("2132")


def test_poll_off_keeps_stock_scan():
    f = fastkeys.make({"poll": "off"})
    assert f.init is None and not f.source
    assert [p.addr for p in f.patches] == [0x4FCD8, 0x66EFC, 0x6D2A4]   # fastrepeat2 only; no timeout pin needed
