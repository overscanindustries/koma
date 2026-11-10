"""clockkeep: link it alone, run the real installer + transient init under the unit emulator with the file API and the
date post (0x326ce) stubbed, over every boot state the firmware can leave (lost / running / never set)."""
from __future__ import annotations

import datetime as dt
import random
import struct

import pytest
from _util import fw  # noqa: F401  (sets sys.path)
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3

from koma.features import clockkeep as C
from koma.features import telemetry as T
from koma.linker import link
from koma.unitemu import Unit

FD = 0x7011
LOST, RUNNING = 0, 8
_cache = {}


def build(**opts):
    key = tuple(sorted(opts.items()))
    if key not in _cache:
        _cache[key] = link([C.make(opts)])
    return _cache[key]


def bcd(v):
    return (v // 10) << 4 | v % 10


def ram_clock(t: dt.datetime) -> bytes:
    return bytes([bcd(t.second), bcd(t.minute), bcd(t.hour), bcd(t.day), bcd(t.month), bcd(t.year - 2000), 0x20])


INIT_CLOCK = bytes.fromhex("00000001010020")      # 0x51b14's value before the first refresh (2000-01-01)


class CkUnit(Unit):
    def __init__(self, module, status=LOST, user_set=0, prompt=1, clock=INIT_CLOCK, card=None, create_ok=True,
                 short_write=False, **kw):
        edits = {C.RTC_STATUS: bytes([status]), C.PROMPT: struct.pack(">I", prompt), C.RAM_CLOCK: clock}
        super().__init__(module, ram_edits=edits, **kw)
        self.uc.mem_write(C.USER_SET, bytes([user_set]))      # inside the settings block Unit fills after ram_edits
        self.uc.mem_write(C.RESULT, bytes(4))      # 0x1b8e4..: left as the block fill (0x03): no 'P' yet
        self.card = card
        self.create_ok, self.short_write = create_ok, short_write
        self.posts, self.ops, self.written, self.poweroffs = [], [], None, []
        self.pos = 0

    def _code(self, uc, a, size, _):
        r0, r1, r2 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        if a == C.OPEN:
            path = bytes(uc.mem_read(r0, 32)).split(b"\0")[0].decode()
            self.ops.append(f"open {path} {r1:#x}")
            if self.card is None:
                return self._ret(0xFFFFFFFF)
            self.pos = 0
            return self._ret(FD)
        if a == C.READ:
            n = min(r2, len(self.card) - self.pos)
            uc.mem_write(r1, self.card[self.pos:self.pos + n])
            self.pos += n
            self.ops.append(f"read {r2}")
            return self._ret(n)
        if a == C.CREATE:
            path = bytes(uc.mem_read(r0, 32)).split(b"\0")[0].decode()
            self.ops.append(f"create {path}")
            return self._ret(FD if self.create_ok else 0xFFFFFFFF)
        if a == C.WRITE:
            self.written = bytes(uc.mem_read(r1, r2))
            self.ops.append(f"write {r2}")
            return self._ret(r2 - 1 if self.short_write else r2)
        if a == C.CLOSE:
            self.ops.append("close")
            return self._ret(0)
        if a == C.POST_DATE:
            self.posts.append(bytes(uc.mem_read(r0, 12)))
            return self._ret(0)
        if a == C.POWEROFF:
            self.poweroffs.append(tuple(uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                                                  UC_ARM_REG_R3)))
            return self._ret(0x5A)
        return super()._code(uc, a, size, _)

    def off(self, site=C.POWEROFF_SITES[0]):
        """Run the stock `bl 0x293f4(0xb)` at a power-off site (now the hook) with the caller's r0..r3."""
        self.ops.clear()
        self.written = None
        r0 = self.thumb(site, {UC_ARM_REG_R0: 0xB, UC_ARM_REG_R1: 0x11, UC_ARM_REG_R2: 0x22, UC_ARM_REG_R3: 0x33},
                        stop=site + 4)
        assert self.poweroffs[-1] == (0xB, 0x11, 0x22, 0x33) and r0 == 0x5A
        return r0

    def off_word(self):
        return struct.unpack(">I", self.read(C.OFF_RESULT, 4))[0]

    def word(self):
        return struct.unpack(">I", self.read(C.RESULT, 4))[0]

    def decision(self):
        return (self.word() >> 16) & 0xFF


def run(card=None, opts=None, **kw):
    u = CkUnit(build(**(opts or {})).module, card=card, **kw)
    assert u.install() == 0 and u.callee_saved_ok
    return u


def test_lost_clock_restores_stored_plus_offset_through_the_date_settings_path():
    u = run(card=C.seed_text(dt.datetime(2026, 11, 7, 14, 30, 5)))
    assert u.decision() == C.D_RESTORED and u.word() >> 24 == 0x43 and u.word() & 0xFF == 1
    assert len(u.posts) == 1
    want = ram_clock(dt.datetime(2026, 11, 7, 15, 30, 5))
    assert u.posts[0] == want + b"\0" + b"\0" * 4          # [7] = 0, flag [8] = 0 (clears XSTP, marks owner-set)
    assert u.read(C.RAM_CLOCK, 7) == want                  # stamps right away, before CPU1's next refresh
    assert u.read(C.PROMPT, 4) == b"\0\0\0\0"               # prompt=skip
    assert not any(o.startswith("create") for o in u.ops)  # restore never writes the card
    assert u.ops[0] == f"open {C.PATH} 0x0"
    allowed = [(C.RAM_CLOCK, 7), (C.PROMPT, 4), (C.RESULT, 4), (C.OFF_RESULT, 4)] + [(a, 4) for a in C.POWEROFF_SITES]
    for a, n in u.outside_writes():
        assert any(b <= a < b + m for b, m in allowed) or fw.PUBLISH <= a < fw.PUBLISH + 8, hex(a)


@pytest.mark.parametrize("case", [
    ((2026, 12, 31, 23, 30, 0), 60, (2027, 1, 1, 0, 30, 0)),
    ((2028, 2, 28, 23, 59, 9), 60, (2028, 2, 29, 0, 59, 9)),
    ((2027, 2, 28, 23, 59, 9), 60, (2027, 3, 1, 0, 59, 9)),
    ((2026, 4, 30, 1, 0, 0), 1440, (2026, 5, 1, 1, 0, 0)),
    ((2026, 4, 30, 1, 0, 0), 0, (2026, 4, 30, 1, 0, 0)),
])
def test_offset_carries(case):
    stored, offset, want = case
    u = run(card=C.seed_text(dt.datetime(*stored)), opts=dict(offset=offset))
    assert u.decision() == C.D_RESTORED
    assert u.posts[0][:7] == ram_clock(dt.datetime(*want))


def test_offset_random_against_python():
    rnd = random.Random(7)
    for offset in (37, 719):
        for _ in range(12):
            t = dt.datetime(2006, 1, 1) + dt.timedelta(minutes=rnd.randrange(0, 30 * 365 * 1440), seconds=rnd.randrange(60))
            u = run(card=C.seed_text(t), opts=dict(offset=offset))
            assert u.posts[0][:7] == ram_clock(t + dt.timedelta(minutes=offset)), (t, offset)


def test_prompt_keep_leaves_the_firmware_prompt():
    u = run(card=C.seed_text(dt.datetime(2026, 1, 2, 3, 4, 5)), opts=dict(prompt="keep"))
    assert u.decision() == C.D_RESTORED and u.read(C.PROMPT, 4) == b"\0\0\0\x01"


def test_pc_seeded_date_line_is_restored_without_offset():
    u = run(card=b"2026-11-07 09:15:00\r\n")                      # default offset 60 applies only to saved files
    assert u.decision() == C.D_RESTORED and u.posts[0][:7] == ram_clock(dt.datetime(2026, 11, 7, 9, 15))
    assert C.seed_text(dt.datetime(2026, 11, 7, 9, 15), header=False) == b"2026-11-07 09:15:00\n"


def test_lost_without_file_keeps_stock():
    u = run(card=None)
    assert u.decision() == C.D_LOST_NOFILE and u.word() & 0xFF == 0
    assert u.posts == [] and u.read(C.PROMPT, 4) == b"\0\0\0\x01" and u.read(C.RAM_CLOCK, 7) == INIT_CLOCK
    assert u.ops == [f"open {C.PATH} 0x0"]


@pytest.mark.parametrize("card", [
    b"KOMA TIME V1\n2005-01-01 00:36:00\n",        # the reset default: never restored
    b"KOMA TIME V1\n2036-01-01 00:00:00\n",        # past the firmware's 2035 clamp
    b"KOMA TIME V1\n2026-02-29 10:00:00\n",        # not a leap year
    b"KOMA TIME V1\n2026-13-01 10:00:00\n",
    b"KOMA TIME V1\n2026-11-07 24:00:00\n",
    b"KOMA TIME V1\n2026-11-07 10:6x:00\n",
    b"KOMA TIME V1\n1999-11-07 10:00:00\n",
    b"KOMA TIME V1\n2026-11-07",                   # truncated
    b"",
])
def test_lost_with_bad_file_keeps_stock(card):
    u = run(card=card)
    assert u.decision() == C.D_LOST_NOFILE and u.word() & 0xFF == 2 and u.posts == []
    assert C.parse_text(card) is None


def test_healthy_boot_without_file_saves_the_clock():
    now = dt.datetime(2026, 11, 7, 18, 2, 33)
    u = run(card=None, status=RUNNING, user_set=1, prompt=0, clock=ram_clock(now))
    assert u.decision() == C.D_SAVED and u.word() & 0xFF == 0
    assert u.written == C.seed_text(now) and len(u.written) == C.TEXT_LEN
    assert u.ops[-3:] == [f"create {C.PATH}", f"write {C.TEXT_LEN}", "close"]
    assert u.posts == [] and u.read(C.RAM_CLOCK, 7) == ram_clock(now)   # a valid clock is never touched
    assert (u.word() >> 8) & 0xFF == 0x18                                 # status 8, owner-set, no prompt


@pytest.mark.parametrize("case", [(0, C.D_SAME), (14, C.D_SAME), (15, C.D_SAVED), (600, C.D_SAVED),
                                            (-1, C.D_SAVED)])
def test_healthy_boot_writes_only_when_moved(case):
    delta, decision = case
    stored = dt.datetime(2026, 11, 7, 12, 0, 0)
    now = stored + dt.timedelta(minutes=delta)
    u = run(card=C.seed_text(stored), status=RUNNING, user_set=1, prompt=0, clock=ram_clock(now))
    assert u.decision() == decision
    assert (u.written == C.seed_text(now)) if decision == C.D_SAVED else (u.written is None)
    assert u.posts == []


def test_minutes_across_years_and_leap_days():
    # stored 2028-02-28 23:50, clock 2028-03-01 00:04: 14 min + Feb 29 (1440) -> moved enough
    u = run(card=C.seed_text(dt.datetime(2028, 2, 28, 23, 50)), status=RUNNING, user_set=1, prompt=0,
            clock=ram_clock(dt.datetime(2028, 3, 1, 0, 4)))
    assert u.decision() == C.D_SAVED
    u = run(card=C.seed_text(dt.datetime(2026, 12, 31, 23, 55)), status=RUNNING, user_set=1, prompt=0,
            clock=ram_clock(dt.datetime(2027, 1, 1, 0, 5)))
    assert u.decision() == C.D_SAME                               # 10 minutes across the year boundary


@pytest.mark.parametrize("clock", [INIT_CLOCK, bytes.fromhex("00360001010520"), bytes.fromhex("0000000101262a"),
                                   bytes.fromhex("0000001a01112620")])
def test_healthy_boot_with_unrefreshed_or_default_ram_clock_does_not_write(clock):
    u = run(card=None, status=RUNNING, user_set=1, prompt=0, clock=clock)
    assert u.decision() == C.D_NOTREADY and u.written is None and u.ops == [f"open {C.PATH} 0x0"]


def test_never_committed_date_is_left_to_the_firmware():
    u = run(card=C.seed_text(dt.datetime(2026, 1, 1)), status=RUNNING, user_set=0, prompt=1,
            clock=ram_clock(dt.datetime(2026, 6, 1)))
    assert u.decision() == C.D_UNSET and u.posts == [] and u.written is None
    assert u.read(C.PROMPT, 4) == b"\0\0\0\x01"


@pytest.mark.parametrize("kw", [dict(create_ok=False), dict(short_write=True)])
def test_save_failure_is_reported(kw):
    u = run(card=None, status=RUNNING, user_set=1, prompt=0, clock=ram_clock(dt.datetime(2026, 5, 5, 5, 5)), **kw)
    assert u.decision() == C.D_SAVEFAIL


def test_resident_for_the_poweroff_hooks_and_options():
    r = build()
    f = r.manifest["features"][0]
    assert not f["transient"] and f["resident_bytes"] > 0
    assert sorted(int(h["site"], 16) for h in f["hooks"]) == sorted(C.POWEROFF_SITES)
    for bad in (dict(offset=-1), dict(offset=1441), dict(every=0), dict(prompt="maybe")):
        with pytest.raises(ValueError):
            C.make(bad)


def test_host_helpers():
    t = dt.datetime(2026, 11, 7, 1, 2, 3)
    assert C.parse_text(C.seed_text(t)) == t and len(C.seed_text(t)) == C.TEXT_LEN
    d = C.describe(0x43011801)
    assert d["valid"] and d["decision"].startswith("RESTORED") and d["owner_set_0x1b0ca"] and d["file"] == "valid"
    assert not C.describe(0x03030303)["valid"]


def test_telemetry_clock_line():
    r = link([C.make({}), T.make({"cp15": "off", "clock": "on"})])
    from test_telemetry import TlmUnit

    class Both(TlmUnit):
        def __init__(self, module):
            super().__init__(module, ram_edits={C.RTC_STATUS: b"\0", C.PROMPT: b"\0\0\0\1"})

        def _code(self, uc, a, size, _):
            if a == C.OPEN:
                return self._ret(0xFFFFFFFF)
            return super()._code(uc, a, size, _)

    u = Both(r.module)
    assert u.install() == 0
    p = T.parse(u.snapshots[-1])
    assert p["fixed_length"] and p["trailer_ok"] and len(u.snapshots[-1]) == len(T.template(True))
    w = p["fields"]["CLOCK"]
    assert w >> 24 == 0x43 and C.describe(w)["decision"].startswith("LOST")
    assert T.make({"cp15": "off"}).options == {"cp15": "off"}       # default builds unchanged


# ---------------------------------------------------------------- 1.1: save at power-off

def booted(clock_at_boot=INIT_CLOCK, **kw):
    """A healthy camera whose RAM copy was not refreshed by install (NOT READY at boot)."""
    kw.setdefault("status", RUNNING)
    kw.setdefault("user_set", 1)
    kw.setdefault("prompt", 0)
    u = run(clock=clock_at_boot, **kw)
    u.uc.mem_write(C.USB, struct.pack(">I", 0))
    u.uc.mem_write(C.LVL, bytes([3]))
    return u


@pytest.mark.parametrize("site", C.POWEROFF_SITES)
def test_not_ready_at_boot_saves_at_poweroff(site):
    u = booted(card=C.seed_text(dt.datetime(2026, 11, 7, 12, 0)))
    assert u.decision() == C.D_NOTREADY and u.written is None
    now = dt.datetime(2026, 11, 9, 18, 45, 12)
    u.uc.mem_write(C.RAM_CLOCK, ram_clock(now))           # CPU1 refreshed the copy during the session
    u.off(site)
    assert u.written == C.seed_text(now)
    assert u.ops == [f"create {C.PATH}", f"write {C.TEXT_LEN}", "close"]
    w = u.off_word()
    assert w >> 24 == 0x50 and (w >> 16) & 0xFF == C.D_SAVED and (w >> 8) & 0xFF == 0x18 and w & 0xFF == 1
    assert C.describe(w)["poweroff"] and C.describe(w)["saves"] == 1
    u.off(site)                                            # every power-off saves: the file follows the clock
    assert u.written == C.seed_text(now) and u.off_word() & 0xFF == 2
    assert u.word() >> 16 == 0x4305                        # the boot word is left as the boot decided
    assert u.posts == [] and u.read(C.RAM_CLOCK, 7) == ram_clock(now)   # never sets the clock


def test_poweroff_saves_after_a_restore():
    u = run(card=C.seed_text(dt.datetime(2026, 11, 7, 14, 30, 5)))
    assert u.decision() == C.D_RESTORED
    # the firmware's flag-0 set path (0x51bd4) then sets status 8 and the owner-set byte; the copy runs on
    u.uc.mem_write(C.RTC_STATUS, bytes([RUNNING]))
    u.uc.mem_write(C.USER_SET, bytes([1]))
    u.uc.mem_write(C.USB, struct.pack(">I", 0))
    u.uc.mem_write(C.LVL, bytes([3]))
    now = dt.datetime(2026, 11, 7, 15, 52, 0)
    u.uc.mem_write(C.RAM_CLOCK, ram_clock(now))
    u.off()
    assert u.written == C.seed_text(now)


@pytest.mark.parametrize("case", [
    ("usb", C.D_SKIPPED), ("end-below", C.D_SKIPPED), ("end-recovered", C.D_SAVED), ("lost", C.D_UNSET),
    ("never-set", C.D_UNSET), ("2005", C.D_NOTREADY), ("unrefreshed", C.D_NOTREADY), ("create-fails", C.D_SAVEFAIL),
    ("short-write", C.D_SAVEFAIL)])
def test_poweroff_gates(case):
    what, decision = case
    u = booted(card=None, create_ok=what != "create-fails", short_write=what == "short-write")
    u.uc.mem_write(C.RAM_CLOCK, ram_clock(dt.datetime(2026, 11, 9, 9, 0)))
    if what == "usb":
        u.uc.mem_write(C.USB, struct.pack(">I", 1))
    if what.startswith("end"):
        u.uc.mem_write(C.LVL, bytes([0]))
        u.uc.mem_write(C.END_T, struct.pack(">H", 121))
        u.uc.mem_write(C.RAW, struct.pack(">H", 110 if what == "end-below" else 130))
    if what == "lost":
        u.uc.mem_write(C.RTC_STATUS, bytes([LOST]))
    if what == "never-set":
        u.uc.mem_write(C.USER_SET, bytes([0]))
    if what == "2005":                                   # a running 2005 date committed as owner-set
        u.uc.mem_write(C.RAM_CLOCK, bytes.fromhex("10370001010520"))
    if what == "unrefreshed":
        u.uc.mem_write(C.RAM_CLOCK, INIT_CLOCK)
    u.off()
    assert (u.off_word() >> 16) & 0xFF == decision, hex(u.off_word())
    wrote = any(o.startswith("write") for o in u.ops)
    assert wrote == (decision in (C.D_SAVED, C.D_SAVEFAIL) and what != "create-fails")
    if decision != C.D_SAVED:
        assert u.off_word() & 0xFF == 0


def test_poweroff_writes_only_its_own_bytes():
    u = booted(card=None)
    u.uc.mem_write(C.RAM_CLOCK, ram_clock(dt.datetime(2026, 11, 9, 9, 0)))
    n = len(u.writes)
    u.off()
    res = u.resident or (0, 0)
    for a, size in u.writes[n:]:
        if res[0] <= a < res[0] + res[1] or 0x7D0000 <= a < 0x7E0100:
            continue                                     # the module's own text buffer, the stack
        assert C.OFF_RESULT <= a < C.OFF_RESULT + 4, hex(a)


def test_poweroff_word_starts_clean_and_keeps_its_count():
    u = booted(card=None)
    assert u.off_word() == 0                               # block fill 03030303 cleared at the first install
    u.uc.mem_write(C.OFF_RESULT, bytes.fromhex("50030807"))
    u.install()
    assert u.off_word() == 0x50030807                      # a 'P' word survives the next boot (count 7)
