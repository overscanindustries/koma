"""settingsio + settingsio-menu: the real install under the unit emulator with a fake card behind the firmware file
API (open/read/close through the service table, create/write/close by address). Checks the import envelope, the
merge with the per-body exclusions, the one-shot marker files, the KSIO sub-header, the PARAM.CUR export, the SAVE TO
CARD on_change, the exclusion table against the byte map, and the bulk-settings lint exception."""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from _util import Feature, Settings, fw
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R4

from koma import features
from koma.features import settingsio as S
from koma.features import settingsio_menu as M
from koma.linker import LinkError, link
from koma.unitemu import RES_POOL, Unit

ROOT = Path(__file__).resolve().parents[3]
OPEN, READ = 0x55DC0, 0x56024
FD0 = 0x7000


class CardUnit(Unit):
    """Unit with an in-memory /ATA1 card behind the file API (the shape of koma/emu FileLayer)."""

    def __init__(self, module, card=None, create_ok=True, remove_ok=True, reopen=None, read_fail=False, **kw):
        super().__init__(module, **kw)
        self.remove_ok = remove_ok
        # reopen: what a second open of PARAM.DAT finds (bytes = the card changed between the passes; False = the open
        # fails); read_fail: every read of PARAM.DAT returns -1
        self.reopen, self.read_fail, self.opened = reopen, read_fail, {}
        self.card = {k.upper(): bytes(v) for k, v in (card or {}).items()}
        self.fds, self.ops, self.create_ok = {}, [], create_ok
        blk = bytearray(self.read(0x1B000, 0x1000))
        for i in range(0x100, 0x800):          # a recognisable running body: offset-derived bytes
            blk[i] = (i * 7 + 3) & 0xFF
        blk[0x8B0:0x8B5] = b"KOMA\x01"
        blk[0x8BB:0x8BE] = bytes(3)            # rows in range: the core and menu inits leave the block alone
        self.uc.mem_write(0x1B000, S.with_checksum(bytes(blk)))

    def _fd(self, name):
        fd = FD0 + len(self.fds) + 1
        while fd in self.fds:
            fd += 1
        self.fds[fd] = [name, 0]
        return fd

    def _code(self, uc, a, size, _):
        r = lambda n: uc.reg_read(n)   # noqa: E731
        if a == OPEN:
            name = self.cstr(r(UC_ARM_REG_R0))[6:].upper()
            self.opened[name] = self.opened.get(name, 0) + 1
            if name == "PARAM.DAT" and self.opened[name] == 2 and self.reopen is not None:
                if self.reopen is False:
                    self.card.pop(name, None)
                else:
                    self.card[name] = bytes(self.reopen)
            if name not in self.card:
                self.ops.append(f"open {name} -1")
                return self._ret(r(UC_ARM_REG_R4))          # path/lookup failure returns the caller's r4
            self.ops.append(f"open {name}")
            return self._ret(self._fd(name))
        if a == S.CREATE:
            name = self.cstr(r(UC_ARM_REG_R0))[6:].upper()
            self.ops.append(f"create {name}")
            if not self.create_ok:
                return self._ret(0xFFFFFFFF)
            self.card[name] = b""
            return self._ret(self._fd(name))
        if a == READ:
            fd, buf, n = r(UC_ARM_REG_R0), r(UC_ARM_REG_R1), r(UC_ARM_REG_R2)
            name, pos = self.fds[fd]
            if self.read_fail and name == "PARAM.DAT":
                return self._ret(0xFFFFFFFF)
            chunk = self.card[name][pos:pos + n]
            uc.mem_write(buf, chunk)
            self.fds[fd][1] = pos + len(chunk)
            return self._ret(len(chunk))
        if a == S.WRITE:
            fd, buf, n = r(UC_ARM_REG_R0), r(UC_ARM_REG_R1), r(UC_ARM_REG_R2)
            name, pos = self.fds[fd]
            data = bytes(uc.mem_read(buf, n))
            self.card[name] = self.card[name][:pos] + data + self.card[name][pos + n:]
            self.fds[fd][1] = pos + n
            self.ops.append(f"write {name} {n}")
            return self._ret(n)
        if a == S.REMOVE:
            name = self.cstr(r(UC_ARM_REG_R0))[6:].upper()
            self.ops.append(f"remove {name}")
            if not self.remove_ok or name not in self.card:
                return self._ret(0xFFFFFFFF)
            del self.card[name]
            return self._ret(0)
        if a == S.CLOSE:
            fd = r(UC_ARM_REG_R0)
            self.ops.append(f"close {self.fds[fd][0]}")
            del self.fds[fd]
            return self._ret(0)
        return super()._code(uc, a, size, _)

    def cstr(self, a):
        return bytes(self.uc.mem_read(a, 64)).split(b"\0")[0].decode()

    def block(self):
        return self.read(0x1B000, 0x1000)


_BUILT = []


def built():
    if not _BUILT:
        _BUILT.append(link([features.make(n) for n in (M.NAME, S.NAME)]))   # registry make: the menu's ROWS
    return _BUILT[0]


def boot(card=None, **kw):
    u = CardUnit(built().module, card, **kw)
    before = u.block()
    assert u.install() == 0 and u.callee_saved_ok
    assert struct.unpack(">I", u.read(RES_POOL + 12, 4))[0] == 1
    assert not u.fds, "every file closed"
    return u, before


def ksio(blk):
    h = blk[S.KSIO - S.BASE:S.KSIO - S.BASE + 13]
    return h[:4], h[4], h[5:12], h[12]


def with_ksio(blk, status):
    b = bytearray(blk)
    b[S.KSIO - S.BASE:S.KSIO - S.BASE + 13] = b"KSIO" + bytes([1]) + fw.ram(S.TAG_SRC, 7) + bytes([status])
    return bytes(b)


ROWS = [dict(r) for r in M.ROWS]                                        # the rows of this build (menu feature only)
CORE = range(0x1B8B0, 0x1B8D0)                                          # core settings init: magic, version, rows


def undeclared(u, file=None):
    """Writes to camera RAM that no declared state covers. Always allowed: the KSIO header (13 B), the core settings
    area 0x1b8b0..0x1b8cf (core init, menu init, row clamp) and the publish slot. With `file` (an import ran): the
    block bytes the merge copies (not excluded, below the copy limit); an excluded byte is never written."""
    copy = set()
    if file is not None:
        limit = S.CK_OFF if file[S.KOMA_OFF:S.KOMA_OFF + 4] == b"KOMA" else S.NOKOMA_LIMIT
        mask = S.exclude_mask()
        copy = {S.BASE + o for o in range(limit) if not mask[o >> 3] >> (o & 7) & 1}
    return [hex(a) for a, n in u.outside_writes() for a in range(a, a + n)
            if not (S.KSIO <= a < S.KSIO + 13 or a in CORE or fw.PUBLISH <= a < fw.PUBLISH + 8 or a in copy)]


def file_from(running, edits):
    b = bytearray(running)
    for o, v in edits.items():
        b[o] = v
    return S.with_checksum(bytes(b))


EDITS = {0x059: 0x02, 0x160: 0x11, 0x7FF: 0x22, 0x8B5: 0x01, 0x8C0: 0x03, 0xA20: 0x44,     # imported
         0x070: 0x99, 0x2F8: 0x99, 0x8D0: 0x99, 0x8DC: 0x99, 0x8BD: 0x01, 0x900: 0x99}     # excluded (per-body)


def test_no_file_exports_block_and_writes_header():
    u, before = boot()
    blk = u.block()
    assert blk == with_ksio(before, S.ST_NONE)
    assert ksio(blk)[2] == fw.ram(S.TAG_SRC, 7)                     # stock payload here: 'Rev%4.4'
    assert u.card == {"PARAM.CUR": blk}
    assert u.ops == ["open PARAM.DAT -1", "create PARAM.CUR", f"write PARAM.CUR {S.SIZE}", "close PARAM.CUR"]
    assert not undeclared(u)


def test_valid_import_merges_and_consumes():
    u0, running = boot()
    running = u0.block()
    f = file_from(running, EDITS)
    assert S.verdict(f) == "ok"
    u, before = boot({"PARAM.DAT": f})
    blk = u.block()
    want = with_ksio(S.merge(before, f), S.ST_OK)
    assert blk == want
    for o in (0x059, 0x160, 0x7FF, 0x8B5, 0x8C0, 0xA20):
        assert blk[o] == EDITS[o], hex(o)
    for o in (0x070, 0x2F8, 0x8BD, 0x900):
        assert blk[o] == before[o], hex(o)
    assert blk[0xFFC:] == before[0xFFC:]                              # stored checksum untouched (save recomputes)
    assert "PARAM.DAT" not in u.card and u.card["PARAM.OK"] == b"OK\r\n" and "PARAM.BAD" not in u.card
    assert u.ops.index("close PARAM.DAT") < u.ops.index("remove PARAM.DAT") < u.ops.index("create PARAM.OK")
    assert u.card["PARAM.CUR"] == blk and ksio(blk)[3] == S.ST_OK
    assert not undeclared(u, f)
    assert u.ops.index("create PARAM.DAT") < u.ops.index("create PARAM.OK") < u.ops.index("create PARAM.CUR")
    # the consumed file is ignored on the next boot; the status of the last import is kept
    # remove not working on the camera: the DONE marker stays and the next boot ignores it
    u1, _ = boot({"PARAM.DAT": f}, remove_ok=False)
    assert u1.card["PARAM.DAT"] == b"DONE" and u1.card["PARAM.OK"] == b"OK\r\n"
    u2, _ = boot(dict(u1.card))
    assert u2.card["PARAM.DAT"] == b"DONE" and ksio(u2.block())[3] == S.ST_NONE      # fresh unit: no KSIO before
    assert "remove PARAM.DAT" not in u2.ops


def test_import_without_koma_header_keeps_koma_area():
    u0, _ = boot()
    running = u0.block()
    b = bytearray(running)
    b[0x800:0xFFC] = b"\xAB" * (0xFFC - 0x800)                        # a stock body's tail: no KOMA header
    b[0x059] = 5
    f = S.with_checksum(bytes(b))
    u, before = boot({"PARAM.DAT": f})
    blk = u.block()
    assert blk[0x059] == 5 and blk[0x800:0x8D0] == before[0x800:0x8D0] and blk[0x900:0xFFC] == before[0x900:0xFFC]
    assert blk == with_ksio(S.merge(before, f), S.ST_OK)
    assert u.card["PARAM.OK"] == b"OK\r\n"


@pytest.mark.parametrize("case", [
    "BAD MAGIC", "BAD CHECKSUM", "BAD KOMA VERSION"])
def test_bad_file_rejected_block_unchanged(case):
    reason = case
    damage = {"BAD MAGIC": lambda b: b"MG1X" + b[4:],
              "BAD CHECKSUM": lambda b: b[:0xFFF] + bytes([b[0xFFF] ^ 1]),
              "BAD KOMA VERSION": lambda b: S.with_checksum(b[:0x8B4] + b"\x02" + b[0x8B5:])}[case]
    u0, _ = boot()
    f = damage(file_from(u0.block(), EDITS))
    assert S.verdict(f) == "bad:" + reason
    u, before = boot({"PARAM.DAT": f})
    assert u.block() == with_ksio(before, S.ST_BAD)
    assert u.card["PARAM.BAD"] == reason.encode() + b"\r\n" and "PARAM.DAT" not in u.card
    assert "PARAM.OK" not in u.card
    assert not undeclared(u)


@pytest.mark.parametrize("size", [0, 4, 4095, 4097, 8192])
def test_wrong_size_ignored(size):
    f = (b"MG1H" + bytes(size))[:size]
    u, before = boot({"PARAM.DAT": f})
    assert u.block() == with_ksio(before, S.ST_NONE)
    assert u.card["PARAM.DAT"] == f and set(u.card) == {"PARAM.DAT", "PARAM.CUR"}
    assert S.verdict(f) == "ignore"


def test_done_prefixed_full_size_ignored():
    f = b"DONE" + bytes(4092)                                          # create that did not truncate
    u, before = boot({"PARAM.DAT": f})
    assert u.block() == with_ksio(before, S.ST_NONE) and set(u.card) == {"PARAM.DAT", "PARAM.CUR"}


def test_create_failure_still_imports_once_in_ram():
    u0, _ = boot()
    f = file_from(u0.block(), EDITS)
    u, before = boot({"PARAM.DAT": f}, create_ok=False)
    assert u.block() == with_ksio(S.merge(before, f), S.ST_OK)
    assert "PARAM.DAT" not in u.card                                   # create failed, the remove still consumed it
    u, _ = boot({"PARAM.DAT": f}, create_ok=False, remove_ok=False)
    assert u.card["PARAM.DAT"] == f                                    # neither worked: re-imports next boot


def _on_change(u, r, value, byte):
    off = r.symbols["kf_settingsio_menu__on_change"] - r.symbols["koma_res"]
    u.uc.mem_write(byte, bytes([value]))
    return u.thumb(u.resident[0] + off, {UC_ARM_REG_R0: value, UC_ARM_REG_R1: byte})


def test_save_to_card():
    u, _ = boot()
    assert u.u8(M.SAVE_BYTE) == M.SAVE_NO
    blk = u.block()
    u.stub(M.IRQ_SET, "irq_set", r0=0x13)                              # returns the old mask
    u.writes.clear()
    u.calls.clear()
    _on_change(u, built(), M.SAVE_YES, M.SAVE_BYTE)
    assert [c for c in u.calls if c[0] == "irq_set"] == [("irq_set", 0xC0), ("irq_set", 0x13)]   # masked sum
    assert [hex(a) for a, _ in u.outside_writes()] == [hex(M.SAVE_BYTE)]  # only the row byte (SAVED)
    blk = blk[:0x8BD] + bytes([M.SAVE_YES]) + blk[0x8BE:]             # the menu wrote the row byte before on_change
    assert u.card["PARAM.SAV"] == S.with_checksum(blk)
    assert S.verdict(u.card["PARAM.SAV"]) == "ok"
    assert u.u8(M.SAVE_BYTE) == M.SAVE_SAVED and u.read(0x1BFFC, 4) == blk[0xFFC:]   # RAM checksum not written
    assert M.PATH_SAV == "/ATA1/PARAM.SAV" and "PARAM.DAT" not in u.card
    u.card.clear()
    _on_change(u, built(), M.SAVE_NO, M.SAVE_BYTE)
    _on_change(u, built(), 1, M.LAYOUT_BYTE)
    assert u.card == {}
    u.create_ok = False
    _on_change(u, built(), M.SAVE_YES, M.SAVE_BYTE)
    assert u.u8(M.SAVE_BYTE) == M.SAVE_FAILED
    # YES again from FAILED (and from SAVED) saves again: any change to YES is the trigger
    u.create_ok = True
    _on_change(u, built(), M.SAVE_YES, M.SAVE_BYTE)
    assert u.u8(M.SAVE_BYTE) == M.SAVE_SAVED and S.verdict(u.card["PARAM.SAV"]) == "ok"
    u.card.clear()
    _on_change(u, built(), M.SAVE_YES, M.SAVE_BYTE)
    assert u.u8(M.SAVE_BYTE) == M.SAVE_SAVED and "PARAM.SAV" in u.card
    # SAVED / FAILED picked by hand: not a request, back to NO, nothing written
    for v in (M.SAVE_SAVED, M.SAVE_FAILED):
        u.card.clear()
        u.writes.clear()
        _on_change(u, built(), v, M.SAVE_BYTE)
        assert u.u8(M.SAVE_BYTE) == M.SAVE_NO and u.card == {}
        assert [hex(a) for a, _ in u.outside_writes()] == [hex(M.SAVE_BYTE)]


def test_exclusion_table_matches_map():
    m = json.loads((ROOT / "tools/komaset/settings-map.json").read_text())
    have = sorted((int(e["offset"], 16), e["size"]) for e in m["exclude_on_merge"])
    add = json.loads((ROOT / "analysis/settings-io/map-additions.json").read_text())
    extra = sorted((int(e["offset"], 16), e["size"]) for e in add["exclude_on_merge"])
    extra_offs = {o for o, _ in extra}
    assert sorted(S.MAP_EXCLUDE) == [x for x in have if x[0] not in extra_offs], \
        "settingsio.MAP_EXCLUDE is out of date with tools/komaset/settings-map.json exclude_on_merge"
    assert sorted(S.SIO_EXCLUDE) == extra
    fields = {int(f["offset"], 16): f for f in m["fields"]}
    for o in range(0x8D0, 0x900):                 # per-body KSIO area: settingsio, plus clockkeep's word at 0x8e0
        assert o not in fields or fields[o]["name"].startswith(("settingsio", "clockkeep")), hex(o)


def test_keep_table_matches_ranges():
    t = S.range_table()
    pairs = [(int.from_bytes(t[i:i + 2], "big"), int.from_bytes(t[i + 2:i + 4], "big")) for i in range(0, len(t), 4)]
    assert pairs[-1] == (0xFFFF, 0xFFFF) and all(a < b for a, b in pairs[:-1])
    assert all(pairs[i][1] < pairs[i + 1][0] for i in range(len(pairs) - 2))         # sorted, merged
    ex = {o for off, n in S.EXCLUDE_RANGES for o in range(off, off + n)}
    assert {o for a, b in pairs[:-1] for o in range(a, b)} == ex
    mask = S.exclude_mask()
    assert all(bool(mask[o >> 3] >> (o & 7) & 1) == (o in ex) for o in range(0x1000))


def test_sizes():
    feats = {f["name"]: f for f in built().manifest["features"]}
    assert feats["settingsio"]["transient"] and feats["settingsio"]["resident_bytes"] == 0
    assert feats["settingsio-menu"]["resident_bytes"] < 300


def test_bulk_settings_reserved_for_settingsio():
    other = Feature(name="other", version="t", summary="t", source="x:\n    bx lr\n", init="x",
                    settings=[Settings(0x1B000, 0x1BFFC, False, "bulk", bulk=True)])
    with pytest.raises(LinkError, match="bulk settings"):
        link([other])
    link([S.make({})])                                                   # settingsio alone links (the exception)


def test_no_flash_calls_in_source():
    from koma.linker import FORBIDDEN_CALLS
    src = S.source() + M.SRC
    assert not any(f"{a:x}" in src.lower() for a in FORBIDDEN_CALLS)


def test_sibling_ga01_block_rejected():
    """GX8/R2 PARAM.DAT blocks carry magic 'Ga01' (sibling model): never imported."""
    u0, _ = boot()
    f = S.with_checksum(b"Ga01" + u0.block()[4:])
    u, before = boot({"PARAM.DAT": f})
    assert u.block() == with_ksio(before, S.ST_BAD) and u.card["PARAM.BAD"] == b"BAD MAGIC\r\n"


# ---------------------------------------------------------------- Regression tests: tag, row clamp, short reads

def _with_tag(blk, tag):
    b = bytearray(blk)
    b[S.TAG_OFF:S.TAG_OFF + S.TAG_LEN] = tag
    return bytes(b)


def test_firmware_tag_compared_not_refused():
    u0, _ = boot()
    running = u0.block()
    tag = fw.ram(S.TAG_SRC, S.TAG_LEN)
    assert running[S.KSIO_OFF:S.KSIO_OFF + 4] == b"KSIO" and running[S.TAG_OFF:S.TAG_OFF + 7] == tag
    edits = {0x059: 0x02, 0x160: 0x11}
    # matching tag (the same firmware): accepted
    f = file_from(running, edits)
    assert S.verdict(f, tag=tag) == "ok"
    u, before = boot({"PARAM.DAT": f})
    assert u.block() == with_ksio(S.merge(before, f), S.ST_OK) and u.card["PARAM.OK"] == b"OK\r\n"
    assert ksio(u.block())[3] == 1 == S.ST_OK                            # matching tag: status 1
    # another firmware's tag (main Rev024M -> spare 240K2): imported, status 3, PARAM.OK, file consumed; the
    # running body's tag stays in the KSIO header (the per-body KSIO range is never copied)
    for other in (b"240K1.0", b"Rev024X", tag[:6] + bytes([tag[6] ^ 0x20])):   # last: one byte off at the end
        mis = S.with_checksum(_with_tag(f, other))
        assert S.verdict(mis, tag=tag) == "ok:tag" and S.verdict(mis) == "ok"
        u, before = boot({"PARAM.DAT": mis})
        assert u.block() == with_ksio(S.merge(before, mis), S.ST_OK_TAG), other
        assert ksio(u.block())[2] == tag and ksio(u.block())[3] == 3 == S.ST_OK_TAG
        assert u.card["PARAM.OK"] == b"OK\r\n" and "PARAM.DAT" not in u.card and "PARAM.BAD" not in u.card
        assert u.card["PARAM.CUR"] == u.block()
        assert not undeclared(u, mis)
    assert S.STATUS[S.ST_OK_TAG] == "OK, tag differed" and 5 not in S.REASONS
    # a differing tag never masks a real envelope fault: Koma version still refuses
    kv = S.with_checksum(_with_tag(f[:0x8B4] + b"\x02" + f[0x8B5:], b"240K1.0"))
    u, before = boot({"PARAM.DAT": kv})
    assert u.block() == with_ksio(before, S.ST_BAD) and u.card["PARAM.BAD"] == b"BAD KOMA VERSION\r\n"
    # no KSIO sub-header (stock body or older Koma): accepted under the old rules, whatever the tag bytes hold
    b = bytearray(_with_tag(f, b"XXXXXXX"))
    b[S.KSIO_OFF:S.KSIO_OFF + 4] = bytes(4)
    nok = S.with_checksum(bytes(b))
    assert S.verdict(nok, tag=tag) == "ok"
    u, before = boot({"PARAM.DAT": nok})
    assert u.block() == with_ksio(S.merge(before, nok), S.ST_OK) and u.card["PARAM.OK"] == b"OK\r\n"


def test_out_of_range_row_reset_after_import():
    u0, _ = boot()
    running = u0.block()
    lay, feat = M.LAYOUT_BYTE - S.BASE, M.FEATURES_BYTE - S.BASE
    f = file_from(running, {lay: 7, feat: 1, 0x059: 0x02})            # MENU LAYOUT has 3 options: 7 is out of range
    assert S.verdict(f) == "ok"
    u, before = boot({"PARAM.DAT": f})
    blk = u.block()
    assert blk[lay] == 0 and blk[feat] == 1 and blk[0x059] == 0x02  # clamped to the default; in-range row imported
    assert blk == with_ksio(S.clamp_rows(S.merge(before, f), ROWS), S.ST_OK)
    assert u.card["PARAM.OK"] == b"OK\r\n" and u.card["PARAM.CUR"] == blk
    assert not undeclared(u, f)
    assert S.row_table(ROWS) == bytes([0x0B, 3, 0, 0x0C, 2, 0, 0x0D, 4, 0, 0xFF])


def test_pass2_short_read_is_bad_read():
    u0, _ = boot()
    f = file_from(u0.block(), EDITS)
    u, before = boot({"PARAM.DAT": f}, reopen=f[:2000])                 # the card changed between the passes
    blk = u.block()
    applied = S.merge(before, f[:0x7C0] + before[0x7C0:])               # 31 full chunks copied before the short one
    assert blk == with_ksio(S.clamp_rows(applied, ROWS), S.ST_BAD) and ksio(blk)[3] == S.ST_BAD == 2
    assert u.card["PARAM.BAD"] == b"BAD READ\r\n" and "PARAM.DAT" not in u.card and "PARAM.OK" not in u.card
    assert u.card["PARAM.CUR"] == blk
    assert not undeclared(u, f)


def test_reopen_failure_is_bad_reopen():
    u0, _ = boot()
    f = file_from(u0.block(), EDITS)
    u, before = boot({"PARAM.DAT": f}, reopen=False)
    assert u.block() == with_ksio(before, S.ST_BAD)
    assert u.card["PARAM.BAD"] == b"BAD REOPEN\r\n" and "PARAM.OK" not in u.card
    assert u.card.get("PARAM.DAT") is None                             # consumed (re-created DONE, then removed)
    assert not undeclared(u)


def test_pass1_read_error_ignored():
    u0, _ = boot()
    f = file_from(u0.block(), EDITS)
    u, before = boot({"PARAM.DAT": f}, read_fail=True)                  # read returns -1
    assert u.block() == with_ksio(before, S.ST_NONE)
    assert u.card["PARAM.DAT"] == f and set(u.card) == {"PARAM.DAT", "PARAM.CUR"}
    assert "close PARAM.DAT" in u.ops
    assert not undeclared(u)
