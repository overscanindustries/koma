"""adjdiag: the CPU1-wait wrapper returns the stock result, latches one record after N consecutive timeouts."""
from __future__ import annotations

from _util import fw  # noqa: F401  (sets up the firmware path like the other tests)
from koma.features import adjdiag
from koma.linker import link
from koma.unitemu import Unit
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1

SITE = adjdiag.SITE
TIMEOUT = 0xFFFFFFFF


def _unit():
    u = Unit(link([adjdiag.make({})]).module)
    assert u.install() == 0 and u.callee_saved_ok
    u.stub(adjdiag.CREATE, "create", 7)
    u.stub(adjdiag.WRITE, "write", len(adjdiag.template()))
    u.stub(adjdiag.CLOSE, "close", 0)
    return u


def _wait(u, result, timeout=20):
    u.stub(adjdiag.WAIT, "wait", result)
    return u.run_site(SITE, SITE + 4, {UC_ARM_REG_R0: 0x1234, UC_ARM_REG_R1: timeout})


def _record(u):
    blob = u.read(u.resident[0], u.resident[1])
    i = blob.find(adjdiag.HEADER)
    assert i >= 0
    text = blob[i:i + len(adjdiag.template())].decode()
    return dict(line.split("=") for line in text.splitlines()[1:-1])


def test_passes_result_through_and_writes_once_after_three_timeouts():
    u = _unit()
    names = []
    for n in range(1, 7):
        assert _wait(u, TIMEOUT) == TIMEOUT
        names.append([c[0] for c in u.calls])
    assert names[0] == ["wait"] and names[1] == ["wait"]
    assert names[2] == ["wait", "create", "write", "close"]
    assert all(x == ["wait"] for x in names[3:])          # latched: one record per boot
    rec = {k.strip(): v for k, v in _record(u).items()}
    assert rec["CONSEC"] == "00000003" and rec["TOTAL"] == "00000003" and rec["TMO"] == "00000014"


def test_answer_clears_the_consecutive_count():
    u = _unit()
    for r in (TIMEOUT, TIMEOUT, 0, TIMEOUT, TIMEOUT, 0):
        assert _wait(u, r) == r
        assert [c[0] for c in u.calls] == ["wait"]
