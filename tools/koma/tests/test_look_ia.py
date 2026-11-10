"""Look IA: one test per spec point. Unit emulator (Unicorn on
the 2.40 payload, firmware callees faked as in test_bwrecipes); MOCK evidence, nothing here ran on a camera.

Every test here exercises behaviour that is specific to the Look IA: the
27-entry stock-first list, the RECIPE / EDIT RECIPE rows and the stock entries in the lever all fail these asserts.
"""
from __future__ import annotations

import random
import struct

from test_bwrecipes import (AUTO_ROW, DISPLAY, GHOST, B, adj_open, call_site, fw, rid, shot, unit, v5_store, xor_ok)
from unicorn.arm_const import UC_ARM_REG_R0 as R0, UC_ARM_REG_R1 as R1

# Koma 0.4 (bwrecipes 4.2) stored ids: the record order of the 'RCP6' store the cameras carry today
IDS_04 = ["MONO", "SHINJUKU", "HICON B&W", "HARD MONO", "SOFT MONO", "FILM 400", "FINE 100", "DENSE", "NOIR", "MATTE",
          "SELECT", "POSITIVE", "NEGATIVE", "BLEACH", "RETRO", "WARM NEG", "SLIDE 50", "CUSTOM 1", "CUSTOM 2", "CUSTOM 3"]
STOCK_WORDS = set(struct.unpack(">7I", fw.ram(B.IMG_OPTS, 28)))
HIDDEN = (0, 2, 3, 4, 5, 6)                                       # HARD, SOFT, B&W, SETTING1, SETTING2, B&W(TE)
CUROF = [1, 2, 3, 4, 6, 7, 5]                                    # IMG SET value -> stock cursor (0x1bd452 inverse)


def _words(u, opts, n):
    return list(struct.unpack(f">{n}I", u.read(opts, 4 * n)))


def test_display_order_maps_to_the_unchanged_stored_ids():
    """21 entries in GR III order; entry k commits the look whose id (record slot) is the one Koma 0.4 used."""
    assert B.DISPLAY == DISPLAY and len(DISPLAY) == 21
    assert [r["name"] for r in B.RECIPES] == IDS_04                 # ids 1..20 unchanged; STANDARD is the new id 21
    u = unit()
    for k, name in enumerate(DISPLAY, 1):
        want = B.STD if name == "STANDARD" else IDS_04.index(name) + 1
        assert B.DMAP[k - 1] == want
        u.w8(AUTO_ROW + 9, k)
        call_site(u, 0x58882, r0=1)
        assert u.u8(B.ACT) == (0 if name == "STANDARD" else want), name    # STANDARD at defaults: no id
        assert list(u.read(B.EB, B.NPARAM)) == B.ALL[want - 1]["params"], name


def test_hidden_stock_values_never_in_the_list_or_on_the_lever():
    """No stock label (HARD, NORMAL, SOFT, B&W, B&W(TE), SETTING1/2) among the 21 entries; on the lever none either,
    except the ghost slot while that value is live."""
    u = unit()
    call_site(u, 0x5B394)
    opts = struct.unpack(">I", u.read(AUTO_ROW + 4, 4))[0]
    assert not STOCK_WORDS & set(_words(u, opts, B.NDISP)) and u.u8(AUTO_ROW + 8) == B.NDISP
    for v in (1,) + HIDDEN:
        u.w8(B.ACT, 0)
        u.w8(0x1B030, v)
        lst, _ = adj_open(u)
        stock = [w for w, i in lst if i == 0]
        assert stock == ([] if v == 1 else [struct.unpack(">I", fw.ram(B.IMG_OPTS + 4 * (CUROF[v] - 1), 4))[0]]), v
        assert all(isinstance(w, str) for w, i in lst if i)          # every look entry is a Koma label


def test_ghost_appears_while_live_and_goes_after_moving_off():
    u = unit()
    for v in HIDDEN:
        u.w8(B.ACT, 0)
        u.w8(0x1B030, v)
        call_site(u, 0x5B394)                                     # menu: ghost, last, highlighted
        assert (u.u8(AUTO_ROW + 8), u.u8(AUTO_ROW + 9)) == (GHOST, GHOST), v
        u.w8(AUTO_ROW + 9, 2)                                     # user moves to MONO, menu closes
        call_site(u, 0x58882, r0=1)
        call_site(u, 0x5B394)                                     # next menu: no ghost
        assert (u.u8(AUTO_ROW + 8), u.u8(AUTO_ROW + 9), u.u8(0x1B030)) == (B.NDISP, 2, 3), v
        u.w8(B.ACT, 0)                                            # ADJ: ghost while live, gone after a commit off it
        u.w8(0x1B030, v)
        lst, cur = adj_open(u)
        assert lst[-1][1] == 0 and cur == len(lst), v
        u.w8(B.ADJD + 9, 1)
        call_site(u, B.SITES_ADJ_COMMIT[0])
        lst, _ = adj_open(u)
        assert all(i for _, i in lst), v


def test_standard_default_writes_normal_without_id_and_edited_keeps_one():
    u = unit()
    done = lambda: u.thumb(u.sym("imgdone"), {})                                       # noqa: E731
    right = lambda c: u.thumb(u.sym("imgright"), {R0: c, R1: AUTO_ROW})               # noqa: E731
    call_site(u, 0x5B394)
    u.on_change(B.ACT, rid("NOIR"))
    u.w8(AUTO_ROW + 9, 1)                                         # STANDARD from the list
    call_site(u, 0x58882, r0=1)
    assert (u.u8(0x1B030), u.u8(B.ACT)) == (1, 0)
    right(1)
    u.on_change(B.EB + B.P_KEY, 7)
    done()
    assert (u.u8(0x1B030), u.u8(B.ACT)) == (1, B.STD) and xor_ok(u.rec(B.STD))
    call_site(u, 0x58882, r0=1)                                   # menu close keeps the edited STANDARD
    assert u.u8(B.ACT) == B.STD
    right(1)
    u.on_change(B.RST, 1)
    done()
    assert (u.u8(0x1B030), u.u8(B.ACT)) == (1, 0)


def _model(mask, cur_id, ghost):
    out = [i for i in B.DMAP if mask >> (i - 1) & 1 or i == cur_id]
    return out + ([0] if ghost else [])


def test_adj_walk_every_flag_pattern():
    """The ADJ walk = the ON looks in display order (+ the current one, + the ghost), wrapping. Patterns: all OFF, all
    ON, each single ON (21), each single OFF (21) and 40 seeded random masks; each with a current look, STANDARD or a
    ghost. Every step and the wrap are checked against the model."""
    rnd = random.Random(20261108)
    full = (1 << B.NALL) - 1
    masks = [0, full] + [1 << k for k in range(B.NALL)] + [full & ~(1 << k) for k in range(B.NALL)]
    masks += [rnd.getrandbits(B.NALL) for _ in range(40)]
    u = unit()
    for j, mask in enumerate(masks):
        m = mask.to_bytes(3, "little")
        u.uc.mem_write(B.AJM, m + bytes([m[0] ^ m[1] ^ m[2] ^ 0x5A]))
        u.w8(B.ST + 13, 0xFF)                                     # rows reload from the new mask (as after a boot)
        kind = j % 3
        if kind == 0:                                             # a look (alternating)
            cur = B.DMAP[1 + j % (B.NDISP - 1)]
            u.on_change(B.ACT, cur)
            ghost = False
        else:
            u.w8(B.ACT, 0)
            u.w8(0x1B030, 1 if kind == 1 else HIDDEN[j % 6])
            cur, ghost = (B.STD, False) if kind == 1 else (0, True)
        lst, c = adj_open(u)
        want = _model(mask, cur, ghost)
        assert [i for _, i in lst] == want, (hex(mask), cur)
        assert want[c - 1] == cur
        n = len(want)
        k = c
        for _ in range(n + 1):                                    # one full lap and the wrap
            k = k + 1 if k < n else 1
            u.w8(B.ADJD + 9, k)
            call_site(u, B.SITES_ADJ_STEP[0])
            i = want[k - 1]
            if i == 0:
                assert u.u8(B.ACT) == 0 and u.u8(0x1B030) in HIDDEN
            elif i == B.STD:
                assert (u.u8(B.ACT), u.u8(0x1B030)) == (0, 1)
            else:
                r = B.ALL[i - 1]
                assert (u.u8(B.ACT), u.u8(0x1B030)) == (i, B.imgset_of(r["params"], r["type"])), (hex(mask), i)
        assert u.u8(B.AJM + 3) == m[0] ^ m[1] ^ m[2] ^ 0x5A          # walking never writes the mask


def test_rcp6_store_from_koma04_loads_custom_slots_survive_flags_default():
    c1, c2, c3 = rid("CUSTOM 1"), rid("CUSTOM 2"), rid("CUSTOM 3")
    recs = {c1: ([3, 2, 4, 6, 4, 0, 1, 4, 0], 0), c2: ([1, 6, 6, 1, 1, 2, 3, 3, 2], B.RECIPES[16]["type"]),
            c3: ([0, 4, 4, 4, 4, 3, 0, 2, 3], 0)}
    st = bytearray(v5_store(recs))
    st[0:5] = B.MAGIC + bytes([B.STORE_VER])
    st += bytes([0x50 | 2 | 1 << 2 if k == c2 else 0 for k in range(1, B.N + 1)])        # 0.4 FX table
    u = unit(store=bytes(st) + b"\xff" * 0x20)                    # whatever 0.4 left after it (erased: 0xff)
    for k, (p, kind) in recs.items():
        assert list(u.rec(k)[:9]) == p and u.rec(k)[9] == kind and xor_ok(u.rec(k)), k
        u.on_change(B.ACT, k)
        assert list(u.read(B.EB, B.NPARAM)) == p
    assert (u.u8(B.FXG), u.u8(B.FXV)) == (0, 0)                   # (CUSTOM 3 active now)
    u.on_change(B.ACT, c2)
    assert (u.u8(B.FXG), u.u8(B.FXV)) == (2, 1)
    lst, _ = adj_open(u)                                          # flags: the defaults (bad check byte)
    assert [n for n, i in lst if i != c2] == [n for n in DISPLAY if n in
                                               ("STANDARD", "MONO", "HARD MONO", "HICON B&W", "SHINJUKU", "POSITIVE",
                                                "CUSTOM 1")]


def test_recipe_rows_absent_in_all_layouts_and_koma_tab_opens_on_focus():
    from koma import features as F
    from koma.features import menu5 as M5
    from koma.linker import collect_rows, link
    from test_menu5_modes import boot, res, text, word
    feats = [F.make(n) for n in F.SETS["koma04"] if n != "telemetry"]
    rows, _, _ = collect_rows(feats)
    main, editor, rr = M5.layout(rows)
    assert main[0] == ("title", "KOMA: FOCUS") and rr["id"] == "rcp_active"
    assert "rcp_active" not in [e[1]["id"] for e in main if e[0] == "row"] and ("edit",) not in main
    assert all(x not in M5.G2_SHOOT for x in ("rcp_active", "edit"))
    r = link([F.make(n) for n in ("menu5", "snapdist", "fullpress", "autoiso", "longexp", "hlalert", "bwrecipes",
                                  "settingsio-menu")])
    for layout in (0, 1, 2):
        u = boot(r, layout=layout)
        meta = word(u, res(r, "kf_menu5__vars") + 4)
        labels = []
        for slot in range(u.u8(res(r, "kf_menu5__vars") + 12)):
            tbl, _, n = word(u, meta + 12 * slot), word(u, meta + 12 * slot + 4), word(u, meta + 12 * slot + 8)
            for k in range(n):
                lw = word(u, tbl + 12 * k)
                if lw >= 0x3C0000:                                # module label tables only (stock ones are Ricoh's)
                    labels.append(text(u, word(u, lw)))
        assert not {"RECIPE", "EDIT RECIPE", "KOMA: RECIPE"} & set(labels), (layout, labels)
        if layout != 2:
            assert "KOMA: FOCUS" in labels and labels.index("KOMA: FOCUS") < labels.index("SNAP DIST"), layout
        assert "kf_menu5__ke_lab" not in r.symbols


def test_stock_byte_only_ever_holds_stock_values():
    """Fuzz (seeded, 600 operations): menu load / commit at any cursor, ADJ open / step / commit at any cursor,
    editor rows (any value), imgright / imgdone, shots, stock writes. After each: 0x1b030 is a stock value (0..6), and
    every change the module makes is 1 (NORMAL), 3 (B&W) or 6 (B&W(TE)). The commit fakes write 0x28 for a cursor
    outside 1..7, so a bad stock cursor shows up here too."""
    rnd = random.Random(1108)
    u = unit()
    rows = [B.ACT, B.EB + B.P_CURVE, B.EB + B.P_CON, B.EB + B.P_TONE, B.EB + B.P_FILT, B.SAVE, B.RST, B.FXG, B.AJROW]
    for step in range(600):
        before = u.u8(0x1B030)
        op = rnd.randrange(8)
        stock_write = False
        if op == 0:
            u.w8(0x1B030, rnd.randrange(7))
            stock_write = True
        elif op == 1:
            call_site(u, 0x5B394)
        elif op == 2:
            n = u.u8(AUTO_ROW + 8)                                # the stock list keeps the cursor in 1..count
            c = u.u8(AUTO_ROW + 9) if rnd.random() < .3 else rnd.randrange(1, n + 1)
            u.w8(AUTO_ROW + 9, c)
            call_site(u, 0x58882, r0=1)
            stock_write = c == GHOST                              # the ghost commits the hidden value back
        elif op == 3:
            adj_open(u)
            n = u.u8(B.ADJ_ITEM + 4)
            u.w8(B.ADJD + 9, rnd.randrange(0, n + 2))
            call_site(u, rnd.choice(B.SITES_ADJ_STEP + B.SITES_ADJ_COMMIT))
            stock_write = True                                    # the stock fakes map any cursor (ghost included)
        elif op == 4:
            b = rnd.choice(rows)
            u.on_change(b, rnd.randrange(0, 25))
        elif op == 5:
            u.thumb(u.sym("imgright"), {R0: rnd.randrange(0, 24), R1: AUTO_ROW})
        elif op == 6:
            u.thumb(u.sym("imgdone"), {})
        else:
            shot(u)
        after = u.u8(0x1B030)
        assert after <= 6, (step, op, before, after)
        if after != before and not stock_write:
            assert after in (1, 3, 6), (step, op, before, after)


def edited_standard(u):
    """STANDARD edited (CONTRAST +2) through the editor, then IMG SET put back to plain NORMAL with no look id (stock
    RESTORE DEFAULTS or a MY SETTINGS recall: they write 0x1b030 = 1 and leave 0x1b8c0 alone or 0)."""
    u.thumb(u.sym("imgright"), {R0: 1, R1: AUTO_ROW})
    u.on_change(B.EB + B.P_CON, 6)
    u.thumb(u.sym("imgdone"), {})
    assert u.u8(B.ACT) == B.STD and u.rec(B.STD)[10] == B.VALID
    u.w8(B.ACT, 0)
    u.w8(0x1B030, 1)


def test_normal_with_edited_standard_is_a_ghost_not_standard():
    """Ghost NORMAL: 0x1b030 = 1, no look id, STANDARD's record edited -> the menu shows a ghost NORMAL
    (stock label), the ADJ lever lists a ghost NORMAL, and leaving either untouched keeps plain NORMAL with id 0 (the
    edits do not start applying). Picking STANDARD itself does apply them. With STANDARD at its defaults NORMAL still
    shows as STANDARD."""
    normal = struct.unpack(">I", fw.ram(B.IMG_OPTS + 4, 4))[0]
    u = unit()
    call_site(u, 0x5B394)
    edited_standard(u)
    call_site(u, 0x5B394)                                         # menu: ghost NORMAL, highlighted
    opts = struct.unpack(">I", u.read(AUTO_ROW + 4, 4))[0]
    assert (u.u8(AUTO_ROW + 8), u.u8(AUTO_ROW + 9)) == (GHOST, GHOST)
    assert struct.unpack(">I", u.read(opts + 4 * B.NDISP, 4))[0] == normal
    call_site(u, 0x58882, r0=1)                                   # menu closed untouched
    assert (u.u8(0x1B030), u.u8(B.ACT)) == (1, 0)
    assert shot(u) == {} and not u.cc2                            # plain stock NORMAL shot
    lst, cur = adj_open(u)                                        # ADJ: ghost NORMAL last, current; STANDARD listed
    assert lst[-1] == (normal, 0) and cur == len(lst) and ("STANDARD", B.STD) in lst
    u.w8(B.ADJD + 9, cur)
    call_site(u, B.SITES_ADJ_COMMIT[0])
    assert (u.u8(0x1B030), u.u8(B.ACT)) == (1, 0)
    k = [n for n, _ in lst].index("STANDARD") + 1                 # the user picks STANDARD on the lever: edits apply
    u.w8(B.ADJD + 9, k)
    call_site(u, B.SITES_ADJ_COMMIT[0])
    assert (u.u8(0x1B030), u.u8(B.ACT)) == (1, B.STD)
    u.w8(B.ACT, 0)
    call_site(u, 0x5B394)
    u.w8(AUTO_ROW + 9, 1)                                         # ... or STANDARD in the menu
    call_site(u, 0x58882, r0=1)
    assert u.u8(B.ACT) == B.STD
    u.thumb(u.sym("imgright"), {R0: 1, R1: AUTO_ROW})             # RESET STANDARD: NORMAL shows as STANDARD again
    u.on_change(B.RST, 1)
    u.thumb(u.sym("imgdone"), {})
    call_site(u, 0x5B394)
    assert (u.u8(AUTO_ROW + 8), u.u8(AUTO_ROW + 9), u.u8(B.ACT)) == (B.NDISP, 1, 0)


def _mask(u):
    m = u.read(B.AJM, 4)
    return int.from_bytes(m[:3], "little"), m[3] == m[0] ^ m[1] ^ m[2] ^ 0x5A


def test_host_mask_write_byte_by_byte_survives_a_non_default_active_look():
    """The host (-KSET, one byte at a time, each followed by on_change -> sync) turns another look's
    ADJ LEVER on while the active look's flag differs from its default. The half-written mask (bad check
    byte, read as the defaults) made the active look's row rewrite all 4 bytes: want 0f0d124a, got 0f09124e. Now the
    row reloads whenever the raw mask changed under it. Every byte order, and an interrupted write that a menu edit
    then repairs."""
    import itertools
    noir, sel, hicon = rid("NOIR"), rid("SELECT"), rid("HICON B&W")
    for order in itertools.permutations(range(4)):
        u = unit()
        u.on_change(B.ACT, noir)
        u.on_change(B.AJROW, 0)                                   # NOIR ON: not its default
        m0, ok = _mask(u)
        assert ok and m0 == B.DEFMASK | 1 << (noir - 1)
        for change in (1 << (sel - 1), -(1 << (noir - 1)), -(1 << (hicon - 1))):   # SELECT on; NOIR, HICON off
            m = m0 | change if change > 0 else m0 & ~-change
            nb = list(m.to_bytes(3, "little"))
            nb.append(nb[0] ^ nb[1] ^ nb[2] ^ 0x5A)
            for k in order:
                u.on_change(B.AJM + k, nb[k])                     # ksettings: write the byte, then on_change
            assert _mask(u) == (m, True), (order, hex(change), u.read(B.AJM, 4).hex(), bytes(nb).hex())
            assert u.u8(B.AJROW) == (0 if m >> (noir - 1) & 1 else 1)      # the active look's row follows
            m0 = m
    u = unit()                                                    # interrupted host write: bad check stays
    u.on_change(B.ACT, noir)
    u.on_change(B.AJROW, 0)
    u.on_change(B.AJM, u.u8(B.AJM) ^ 0x10)                        # one byte, then the cable is pulled
    assert not _mask(u)[1] and u.u8(B.AJROW) == 1                 # reads as the defaults (NOIR OFF), nothing rewritten
    u.on_change(B.AJROW, 0)                                       # the user turns NOIR ON in the editor: repaired
    assert _mask(u) == (B.DEFMASK | 1 << (noir - 1), True)


def test_stdclean_compares_the_kind_too():
    """N2: a STANDARD record with default params but another kind (a color look's matrix) is not 'clean': NORMAL is a
    ghost and the look keeps its id."""
    u = unit()
    rec = list(B.STANDARD["params"]) + [B.TYPE_COLOR | 0 << 4]  # POSITIVE's matrix, STANDARD's params
    x = 0x5A
    for b in rec:
        x ^= b
    u.uc.mem_write(B.STDREC, bytes(rec + [B.VALID, x]))
    call_site(u, 0x5B394)
    assert (u.u8(AUTO_ROW + 8), u.u8(AUTO_ROW + 9)) == (GHOST, GHOST)
    u.thumb(u.sym("imgright"), {R0: 1, R1: AUTO_ROW})             # STANDARD picked and its editor closed:
    u.thumb(u.sym("imgdone"), {})
    assert u.u8(B.ACT) == B.STD                                   # not normalised away (its kind is not the default)
