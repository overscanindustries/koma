"""menu5: the five-tab menu with the v2 header band; the second tab is the KOMA tab.

Port of the earlier five-tab menu builders (menu5 + menu5v2) (`modules/menu5tab-v2.grmod`, the build on the owner's camera).
Reused at build time, read only: the Thumb CODE, the TABS layout of the stock tabs, the 23 patch records with their
before-bytes (`patches()`), the v2 band source (`menu5v2.BAND_SRC`) and the glyph encoder. Changes for Koma:
  * `&label` -> `label`: Koma's assembler emits the relocations;
  * the old installer goes; its 23 records become 8 Redirect (BL), 12 Pointer (PTR) and 3 Patch (RAW) records, each
    with the same before-bytes;
  * IA v2 (Koma 0.1 "ia2"): tab strip STILL | KOMA | PLAY | CUSTOM | SETUP. The KOMA tab takes the MOVIE slot, which
    in still mode only held "MOVIE MODE ONLY". In movie mode that slot is the stock movie page and is labelled MOVIE
    (KOMA is not reachable there; its settings are still-mode settings). SETUP is back to its stock rows.
  * KOMA tab: one topic per screen page (5 rows), data-driven from each row's `group`
    (recipe, focus, exposure, display; anything else -> OTHER). Each page starts with a title row "KOMA: <TOPIC>"
    that the cursor skips, and is padded with blank rows (also skipped). The RECIPE page ends with EDIT RECIPE, which
    opens a sub-table (the editor: "EDITING <recipe name>" + every `recipe-edit` row). LEFT in the editor goes back.
  * Rows: a row shows its byte (out of range -> default); a commit writes the chosen index back (only in-range values,
    only on change, then calls the feature's exported `on_change`, if any, with r0 = value, r1 = byte address).
    Changes are found for every row first and applied after, and the cursors are reloaded from the bytes at the end,
    so an on_change that moves other rows (recipe change, RESET) never has its result overwritten by stale cursors.
  * Row variants (Koma 0.2, for the recipe editor): a row may carry `variants` = dict(sel=<byte address>,
    alts=[(label, options), ...]) in its feature's ROWS. When the rows are loaded (ksync_load), alt[sel byte] (out of
    range -> alt 0) replaces the row's label, option list and option count (display entry and ktab), so one row reads
    FILTER EFF. / SATURATION / COLOUR with the active recipe's type. The row's declared options must be at least as long
    as every alt (the core's boot repair uses the declared count).
  * Koma modes: the init picks the menu directory from MENU LAYOUT (0x1b8bb, when the
    build has that row): 0 / 2 -> the 5-tab directory, 1 -> STOCK+KOMA (mode page | KOMA | the stock 30-row SETUP
    table itself). A directory is (metatab, stripdata, tab count, mode-slot flag, GR II flag, label-table range),
    copied into `vars` at install; the tab code reads the count from there instead of the old immediate 5.
    Resident diet (Koma 0.4): each layout's own tables are a data overlay (Feature.overlays, linker.py). 5-TAB's
    (its tabs 2-4, metatab, strip, label tables) sit in place in the resident area `ovl`, which is sized for the
    largest layout; STOCK+KOMA's and GR II's (directory, metatab, maps, strip, tblG1/G2, kgfix, kmbuf, kmtabs, kms,
    kmrows, kx, kmapfix) stay in the transient body and a linker-generated transient init copies the selected
    one over the area before kinit. Each overlay starts with its directory, so kinit reads it at `ovl`. The GR II code
    (krowmap, kmap, kml/kmc, kmsync, kright, kedpend) stays resident: its hooks and pointers are installed in every
    layout and fall through to stock unless the GR II flag is set. With KOMA FEATURES = ESSENTIALS latched, the
    init clears the on_change of rows whose feature the installer skipped (the row shows and stores, nothing runs).
  * Label tables (9 language slots, one string) are filled by the init: the module carries the 36-B table zeroed plus
    its string, no relocations (was 9 x 4 B per table in the body).
Row cursors are synced by the same stock-load / stock-commit wrappers that sync the stock rows (setup_enter, commit).
Navigation on the KOMA tables is handled in a wrapper around the menu5 classifier (`classify` -> `classify0`):
UP/DOWN on rows and from the header, and RIGHT from the page column, move to the next selectable row (stock rules:
past either end -> header). Everything else goes to the stock path.
"""
from __future__ import annotations

import re
import struct

from .. import firmware as fw
from ..feature import Feature, Hook, Patch, Pointer, Redirect

NAME = "menu5"
SUMMARY = "five-tab menu v2 (opaque header band) + KOMA tab: one topic per page, recipe editor sub-page"
OPTIONS = {}

ROWS_PER_PAGE = 5                  # rows per screen page (O-emu)
VERSION_LABEL = "KOMA VERSION"
GROUPS = [("recipe", "RECIPE"), ("focus", "FOCUS"), ("exposure", "EXPOSURE"), ("display", "DISPLAY")]
OTHER = "OTHER"
EDIT_GROUP = "recipe-edit"
EDIT_LABEL = "EDIT RECIPE"
EDITING_LABEL = "EDITING"
STRIP = ["STILL", "KOMA", "PLAY", "CUSTOM", "SETUP"]      # slot 1 reads MOVIE in movie mode (kstripfix)
MAX_ROWS = 30
_L = {}

# descriptor 0x1c23b8
D_TAB, D_TBL, D_CNT, D_PAGE, D_ROW, D_STATE = 0x1C23B9, 0x1C23E0, 0x1C23EC, 0x1C23ED, 0x1C23EE, 0x1C23F0


def legacy():
    """The five-tab menu builder menu5 + menu5v2 + glyphs (vendor/menu5tab, imported lazily: it reads the firmware)."""
    if not _L:
        from ..vendor.menu5tab import glyphs, menu5, menu5v2
        _L["m"], _L["v2"], _L["g"] = menu5, menu5v2, glyphs
    return _L["m"], _L["v2"], _L["g"]


def _deamp(src: str) -> str:
    return re.sub(r"&([A-Za-z_]\w*)", r"\1", src)


# ------------------------------------------------------------------ KOMA tab layout (pure data; tested)
def title(topic):
    return f"KOMA: {topic}"


def layout(rows):
    """KOMA tab entries from the collected rows. Returns (main, editor, recipe_row):
    main/editor = lists of ("title", label) | ("blank",) | ("row", row) | ("edit",) | ("version",) | ("etitle",).
    Pages: RECIPE (its rows + EDIT RECIPE), FOCUS, EXPOSURE, DISPLAY (+ KOMA VERSION, always), then OTHER (unknown or
    missing groups). Each page: a title row + up to 4 rows; a longer topic continues on a new page with the same title.
    Every page but the last is padded to 5 rows with blank rows."""
    known = {g for g, _ in GROUPS} | {EDIT_GROUP}
    by = {g: [r for r in rows if r.get("group") == g] for g, _ in GROUPS}
    by[OTHER] = [r for r in rows if r.get("group") not in known]
    edit_rows = [r for r in rows if r.get("group") == EDIT_GROUP]
    items = {g: [("row", r) for r in by[g]] for g in by}
    if edit_rows:
        items["recipe"].append(("edit",))
    items["display"].append(("version",))
    pages = []
    for g, topic in GROUPS + [(OTHER, OTHER)]:
        its = items[g]
        for i in range(0, len(its), ROWS_PER_PAGE - 1):
            pages.append([("title", title(topic))] + its[i:i + ROWS_PER_PAGE - 1])
    main = []
    for i, p in enumerate(pages):
        main += p + ([("blank",)] * (ROWS_PER_PAGE - len(p)) if i < len(pages) - 1 else [])
    recipe_row = by["recipe"][0] if by["recipe"] else None
    editor = ([("etitle",)] + [("row", r) for r in edit_rows]) if edit_rows else []
    assert len(main) <= MAX_ROWS and len(editor) <= MAX_ROWS, "KOMA tab: more than 30 rows in one table"
    return main, editor, recipe_row


def skips(entries):
    """1 = the cursor skips this row (titles, blanks, the editor's title)."""
    return [1 if e[0] in ("title", "blank", "etitle") else 0 for e in entries]


# ------------------------------------------------------------------ code
KOMA_SRC = """
@ ---------------------------------------------------------------- Koma: KOMA tab rows
@ ktab entry (16 B): +0 byte address, +4 on_change|1 or 0, +8 display entry, +12 count, +13 default,
@                    +14 flags (1 = display only), +15 change mark (ksync_store)
ksync_load:                             @ row bytes -> display cursors (1-based), out of range -> default
    push {r4, r5, lr}
    bl kvar
    ldr r4, =ktab
    ldr r5, =ktab_end
kl_loop:
    cmp r4, r5
    bhs kl_done
    ldr r0, [r4, #0]
    ldrb r0, [r0]
    ldrb r1, [r4, #12]
    cmp r0, r1
    blo 1f
    ldrb r0, [r4, #13]
1:  adds r0, #1
    ldr r1, [r4, #8]
    strb r0, [r1, #9]
    adds r4, #16
    b kl_loop
kl_done:
    ldr r1, =kv_entry                   @ version row: always show the version (cursor 1)
    movs r0, #1
    strb r0, [r1, #9]
G2_KV
    bl kver
    pop {r4, r5}
    pop {r3}
    bx r3

@ kvtab entry: +0 selector byte address, +4 display entry, +8 ktab entry, +12 alt count; then per alt (12 B):
@ +0 label table, +4 option table, +8 option count, +9 default
kvar:                                   @ row variants: alt[selector] -> label, options, count (display + ktab)
    push {r4, r5, lr}
    ldr r4, =kvtab
    ldr r5, =kvtab_end
kv_loop:
    cmp r4, r5
    bhs kv_done
    ldr r0, [r4, #0]
    ldrb r0, [r0]
    ldrb r1, [r4, #12]
    cmp r0, r1
    blo 1f
    movs r0, #0
1:  movs r2, #12
    muls r0, r2, r0
    adds r0, #16
    adds r0, r4, r0
    ldr r2, [r4, #4]
    ldr r3, [r0, #0]
    str r3, [r2, #0]
    ldr r3, [r0, #4]
    str r3, [r2, #4]
    ldrb r3, [r0, #8]
    strb r3, [r2, #8]
    ldr r2, [r4, #8]
    strb r3, [r2, #12]
    ldrb r3, [r0, #9]
    strb r3, [r2, #13]
    movs r2, #12
    muls r1, r2, r1
    adds r1, #16
    adds r4, r4, r1
    b kv_loop
kv_done:
    pop {r4, r5}
    pop {r3}
    bx r3

ksync_store:                            @ display cursors -> row bytes (in range, on change), then on_change
    push {r4, r5, r6, r7, lr}
    ldr r4, =ktab
    ldr r5, =ktab_end
ks_mark:                                @ pass 1: mark every row whose cursor holds a new in-range value
    cmp r4, r5
    bhs ks_pass2
    movs r2, #0
    ldrb r0, [r4, #14]
    cmp r0, #0
    bne ks_m1
    ldr r1, [r4, #8]
    ldrb r0, [r1, #9]
    subs r0, #1
    ldrb r1, [r4, #12]
    cmp r0, r1
    bhs ks_m1
    ldr r1, [r4, #0]
    ldrb r1, [r1]
    cmp r0, r1
    beq ks_m1
    movs r2, #1
ks_m1:
    strb r2, [r4, #15]
    adds r4, #16
    b ks_mark
ks_pass2:                               @ pass 2: write the marked rows, call their on_change
    ldr r4, =ktab
ks_loop:
    cmp r4, r5
    bhs ks_done
    ldrb r0, [r4, #15]
    cmp r0, #0
    beq ks_next
    movs r0, #0
    strb r0, [r4, #15]
    ldr r1, [r4, #8]
    ldrb r0, [r1, #9]
    subs r0, #1
    ldr r6, [r4, #0]
    strb r0, [r6]
    ldr r3, [r4, #4]
    cmp r3, #0
    beq ks_next
    movs r1, r6
    bl call_r3
ks_next:
    adds r4, #16
    b ks_loop
ks_done:
    bl ksync_load                       @ an on_change may have moved other rows (recipe change, RESET)
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3

kver:                                   @ $koma.version "KOMA Vx.y IIIIIIII" -> glyph strings "x.y" and "IIIIIIII"
    push {r4, lr}
    ldr r0, =$koma.version + VSKIP
    ldr r1, =kv_short_s
    movs r2, #VSHORT
    bl kconv
    ldr r0, =$koma.version + VSKIP + VSHORT + 1
    ldr r1, =kv_id_s
    movs r2, #8
    bl kconv
    pop {r4}
    pop {r3}
    bx r3

kconv:                                  @ r0 ASCII, r1 glyph out, r2 max chars; page-1 glyphs (glyphs.P1)
    push {r4, lr}
kc_loop:
    cmp r2, #0
    beq kc_end
    ldrb r3, [r0]
    cmp r3, #0
    beq kc_end
    movs r4, #2                         @ space
    cmp r3, #0x20
    beq kc_put
    movs r4, r3
    subs r4, #0x24                      @ '.' '/' '0'..'9'
    cmp r3, #0x3a
    blo kc_put
    subs r4, #6                         @ 'A'..'Z'
kc_put:
    movs r3, #1
    strb r3, [r1, #0]
    strb r4, [r1, #1]
    adds r1, #2
    adds r0, #1
    subs r2, #1
    b kc_loop
kc_end:
    movs r3, #0
    strb r3, [r1, #0]
    strb r3, [r1, #1]
    pop {r4}
    pop {r3}
    bx r3
call_r3:
    bx r3
.ltorg

@ ---------------------------------------------------------------- KOMA tab navigation
classify:                               @ replaces bl 0xe3698 at 0xe34b4 (r0 = key; the caller keeps it in r6,
    push {r4, r5, lr}                   @ which classify0 may rewrite: r6 is not touched here)
    movs r4, r0
    ldr r1, =D_TAB
    ldrb r1, [r1]
    cmp r1, #2
    bne k_plain
    ldr r1, =D_TBL
    ldr r5, [r1]
    ldr r1, =tbl1
    cmp r5, r1
    beq 1f
    ldr r1, =ED_TBL
    cmp r5, r1
    bne k_plain
1:  ldr r1, =D_STATE
    ldrb r2, [r1]
    cmp r2, #2
    beq k_rows
    cmp r2, #1
    beq k_pagecol
    cmp r2, #0
    bne k_plain
    bl kdir                             @ header: UP/DOWN enter the rows on a selectable row
    cmp r0, #0
    beq k_plain
    movs r1, #0                         @ DOWN: from before row 1
    cmp r0, #0
    bgt 2f
    ldr r1, =D_CNT                      @ UP: from after the last row
    ldrb r1, [r1]
    adds r1, #1
2:  bl kmove
    b k_done
k_pagecol:                              @ page column: RIGHT enters the first selectable row of that page
    cmp r4, #0x20
    beq 3f
    cmp r4, #0x22
    bne k_plain
3:  ldr r1, =D_PAGE
    ldrb r1, [r1]
    movs r2, #5
    muls r1, r2, r1
    subs r1, #5
    movs r0, #1
    bl kmove
    b k_done
k_rows:
    bl kdir
    cmp r0, #0
    beq 4f
    ldr r1, =D_ROW
    ldrb r1, [r1]
    bl kmove
    b k_done
4:
EDIT_KEYS
k_plain:
    movs r0, r4
    bl classify0
    b k_out
k_done:
    movs r0, #0xff                      @ >= 0xd: e343c does nothing else
k_out:
    pop {r4, r5}
    pop {r3}
    bx r3

kdir:                                   @ r4 = key -> r0 = 1 (DOWN 0x40/0x42), -1 (UP 0x30/0x32), else 0
    movs r0, #1                         @ (0x6c7cc: 0x30/0x32 -> UI 0x13/0x1d row-1, 0x40/0x42 -> 0x14/0x1e row+1)
    cmp r4, #0x40
    beq 1f
    cmp r4, #0x42
    beq 1f
    movs r0, #0
    cmp r4, #0x30
    beq 2f
    cmp r4, #0x32
    bne 1f
2:  subs r0, #1
1:  bx lr

kmove:                                  @ r0 = +1/-1, r1 = start row: go to the next selectable row that way;
    push {r4, r5, r6, lr}               @ past either end -> header (as stock: state 0, row 1, page 1). Redraws.
    movs r4, r0
    movs r5, r1
    ldr r1, =D_CNT
    ldrb r6, [r1]
    ldr r2, =D_TBL
    ldr r2, [r2]
    ldr r1, =skip1
    ldr r3, =ED_TBL
    cmp r2, r3
    bne 1f
    ldr r1, =ED_SKIP
1:  adds r5, r5, r4
    cmp r5, #0
    ble km_head
    cmp r5, r6
    bgt km_head
    subs r2, r5, #1
    ldrb r2, [r1, r2]
    cmp r2, #0
    bne 1b
    ldr r1, =D_ROW
    strb r5, [r1]
    movs r0, #0                         @ screen page = (row - 1) / 5 + 1
    subs r2, r5, #1
2:  cmp r2, #5
    blo 3f
    subs r2, #5
    adds r0, #1
    b 2b
3:  adds r0, #1
    ldr r1, =D_PAGE
    strb r0, [r1]
    movs r0, #2
    b km_state
km_head:
    movs r0, #1
    ldr r1, =D_ROW
    strb r0, [r1]
    ldr r1, =D_PAGE
    strb r0, [r1]
    movs r0, #0
km_state:
    ldr r1, =D_STATE
    strb r0, [r1]
    bl redraw
    pop {r4, r5, r6}
    pop {r3}
    bx r3

kshow:                                  @ r0 = table, r1 = its row count, r2 = start row: show it, cursor on the
    push {r4, lr}                       @ first selectable row after r2
    ldr r3, =D_TBL
    str r0, [r3]
    ldr r3, =D_CNT
    strb r1, [r3]
    movs r0, #1
    movs r1, r2
    bl kmove
    pop {r4}
    pop {r3}
    bx r3

kstripfix:                              @ strip slot 1: KOMA, or MOVIE in movie mode (the stock movie page)
    push {r4, lr}
G2_STRIPFIX
    bl mode_slot
    ldr r1, =vars
    ldr r1, [r1, #8]                    @ the directory's stripdata
    adds r1, #8
    ldr r2, =strip_KOMA
    movs r3, #KOMA_W
    cmp r0, #1
    bne 1f
    ldr r2, =strip_MOVIE
    movs r3, #MOVIE_W
1:  strh r3, [r1, #2]
    str r2, [r1, #4]
2:  pop {r4}
    pop {r3}
    bx r3

kinit:                                  @ install-time init (after the core settings init and the overlay copy)
    push {r4, lr}
    ldr r0, =lt_start                   @ label tables: all 9 language slots -> the string after the table
    ldr r1, =lt_end
    bl klt
    ldr r0, =ovl                        @ menu directory (the selected layout overlay starts with it) -> vars+4:
    ldr r1, =vars + 4                   @ metatab, stripdata, tab count, mode-slot flag, GR II flag
    ldr r2, [r0, #0]
    str r2, [r1, #0]
    ldr r2, [r0, #4]
    str r2, [r1, #4]
    ldr r2, [r0, #8]
    str r2, [r1, #8]
    ldr r1, [r0, #16]                   @ the overlay's own label tables
    ldr r0, [r0, #12]
    bl klt
G2_INIT
ESS_GATE
    pop {r4}
    pop {r3}
    bx r3

klt:                                    @ label tables [r0, r1): slot 0 holds the record length; all 9 slots := the
    push {r4, lr}                       @ string after the table
1:  cmp r0, r1
    bhs 3f
    ldr r2, [r0, #0]
    movs r3, r0
    adds r3, #36
    movs r4, #32
2:  str r3, [r0, r4]
    subs r4, #4
    bpl 2b
    adds r0, r0, r2
    b 1b
3:  pop {r4}
    pop {r3}
    bx r3
EDIT_SRC
.ltorg
G2_ROWMAP
G2_MODE
.ltorg
"""

# RIGHT / OK on EDIT RECIPE opens the editor (not while the recipe row says OFF); LEFT in the editor goes back.
EDIT_KEYS_SRC = """    ldr r1, =ED_TBL
    cmp r5, r1
    bne 5f
    cmp r4, #0x10
    beq k_back
    cmp r4, #0x12
    beq k_back
    b k_plain
5:  ldr r1, =D_ROW
    ldrb r1, [r1]
    cmp r1, #EDITROW
    bne k_plain
    cmp r4, #0x20
    beq k_edit
    cmp r4, #0x22
    beq k_edit
    cmp r4, #0x50
    bne k_plain
k_edit:
RECIPE_OFF_CHECK
    bl ksync_load                       @ fresh cursors: the active recipe may have changed in this menu session
    ldr r0, =tblE
    movs r1, #ECOUNT
    movs r2, #0
    bl kshow
    b k_done
k_back:
G2_BACK
    ldr r0, =tbl1
    movs r1, #KCOUNT
    movs r2, #EDITROW - 1
    bl kshow
    b k_done
"""
# ---------------------------------------------------------------- MENU LAYOUT = 2 (GR II)
# vars +14 = 1: the GR II directory is installed (kdir2). kinit then puts SHOOTING into the still pages' tab label and
# points each placed Koma row's ktab display entry (+8) at its GR II copy (kgfix). The stock still pages (AUTO, P/A, M)
# are reordered in place by krowmap, the init-list entry that replaces the row-map init 0x29e14 (it runs after the
# installer, so kinit cannot write the map: 0x29e14 would overwrite it).
G2_INIT_SRC = """    ldr r1, =vars
    ldrb r0, [r1, #14]
    cmp r0, #0
    beq kg_skip
    ldr r0, =lab_still                  @ the still pages' tab label reads SHOOTING (all 9 language slots)
    ldr r1, =g2_shoot_s
    movs r2, #32
1:  str r1, [r0, r2]
    subs r2, #4
    bpl 1b
    ldr r0, =kgfix                      @ (ktab entry, GR II display entry) pairs
    ldr r3, =kgfix_end
2:  cmp r0, r3
    bhs kg_skip
    ldr r1, [r0, #0]
    ldr r2, [r0, #4]
    str r2, [r1, #8]
    adds r0, #8
    b 2b
kg_skip:
"""
G2_STRIPFIX_SRC = """    ldr r1, =vars
    ldrb r1, [r1, #14]
    cmp r1, #0
    bne 2f
"""
# kv_entry_g2 lives in the GR II overlay: in the other layouts its name aliases their own tables (5-TAB: the metatab's
# CUSTOM entry), so the write is GR II only
G2_KV_SRC = """    ldr r1, =vars
    ldrb r1, [r1, #14]
    cmp r1, #0
    beq 1f
    ldr r1, =kv_entry_g2
    strb r0, [r1, #9]
1:
"""
# LEFT in the recipe editor (GR II): back to the SHOOTING page through the stock exit (classifier case 9, as c_leave:
# header state, caller's key register = LEFT); kright then puts the cursor back on EDIT RECIPE (kmv +2)
G2_BACK_SRC = """    ldr r1, =vars
    ldrb r0, [r1, #14]
    cmp r0, #0
    beq 6f
    movs r0, #0
    strb r0, [r1, #0]                   @ slot 0: the mode page
    ldr r1, =kmv
    movs r0, #1
    strb r0, [r1, #2]
    movs r0, #0
    ldr r1, =D_STATE
    strb r0, [r1]
    movs r0, #1
    ldr r1, =D_ROW
    strb r0, [r1]
    ldr r1, =D_PAGE
    strb r0, [r1]
    movs r6, #0x10                      @ caller's key register: case 9 sends LEFT to the UI (tab -> 1)
    movs r0, #0x10
    ldr r3, =0xe3699
    bl call_r3
    b k_out
6:
"""
# krowmap: the init-list word 0x1a90f4 (was 0x29e14|1, called once at boot by 0x28a8c right after the loader hook)
# points here. It runs the stock row-map init, then, with the GR II directory installed: checks every still-page map
# byte against its stock value (any mismatch: everything stays stock), sets the map to the SHOOTING display positions
# (kmapfix column 2) and points the descriptor's page 0 / 6 / 7 tables at kmbuf with the GR II row counts. The stock
# tables stay at their addresses in stock order; the load / commit hooks below swap the map back to stock values
# around the stock code (which addresses rows as table + map[slot] * 12) and copy rows between kmbuf and the table.
G2_ROWMAP_SRC = """
krowmap:
    push {r4, r5, r6, r7, lr}
    ldr r3, =0x29e15
    bl call_r3
    movs r7, r0                         @ its result (0x28a8c stops the list on non-zero)
    ldr r1, =vars
    ldrb r0, [r1, #14]
    cmp r0, #0
    beq kr_out
    ldr r6, =kmapfix                    @ (offset from 0x1a91ac, stock value, SHOOTING position); 0xff ends
    ldr r5, =0x1a91ac
kr_chk:
    ldrb r0, [r6, #0]
    cmp r0, #0xff
    beq kr_set
    ldrb r1, [r5, r0]
    ldrb r2, [r6, #1]
    cmp r1, r2
    bne kr_out
    adds r6, #3
    b kr_chk
kr_set:
    movs r0, #2
    bl kmap
    ldr r0, =kmbuf
    ldr r1, =0x1c23c0                   @ descriptor: page 0 / 6 / 7 table pointers and row counts
    str r0, [r1, #0]
    str r0, [r1, #0x18]
    str r0, [r1, #0x1c]
    ldr r1, =0x1c23e4
    movs r0, #KMN0
    strb r0, [r1, #0]
    movs r0, #KMN1
    strb r0, [r1, #6]
    movs r0, #KMN2
    strb r0, [r1, #7]
    ldr r1, =vars
    movs r0, #1
    strb r0, [r1, #15]                  @ the SHOOTING page is extended: the hooks act
kr_out:
    movs r0, r7
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3

kmap:                                   @ r0 = 1 stock values, 2 SHOOTING positions -> row-index map
    push {r4, lr}
    ldr r1, =kmapfix
    ldr r2, =0x1a91ac
1:  ldrb r3, [r1, #0]
    cmp r3, #0xff
    beq 2f
    ldrb r4, [r1, r0]
    strb r4, [r2, r3]
    adds r1, #3
    b 1b
2:  pop {r4}
    pop {r3}
    bx r3
.ltorg
"""
# The SHOOTING rows (GR II layout): kmbuf is the display table of all three still pages (one page is open at a time).
# Per still table a spec (n, EDIT RECIPE row, then per display row: 1..14 = that stock row, 0x80 + k = kmrows[k]).
# kmrows[k] = (template display entry, ktab-format entry or 0): Koma rows use their KOMA-tab entry kr_e_i and ktab
# entry kt_i, the SETUP-app rows (RAW/JPEG, DIGITAL ZOOM, CL-BKT) the stock SETUP row and a kx entry, EDIT RECIPE kg_edit.
# Hooks (chained first, before bwrecipes' IMG SET hooks, which find their row through the stock table + map): the
# still loads 0x5aaf0 / 0x5aeb4 and commits 0x5b4e4 / 0x5a260 run with the map at stock values; after a load kmbuf is
# filled from the table and the extra rows' bytes; before a commit from the cursors (r0 0/1) the cursors go back to the
# table and the extra rows' bytes are written (in range, on change, then on_change), after it kmbuf is refilled.
# Under ESSENTIALS the hooks call the stock function directly: the chain partners here (bwrecipes) are gated features.
G2_MODE_SRC = """
kml0:
    push {r4, r5, r6, r7, lr}
    ldr r5, =next_kml0
    movs r4, #0
    b kml
kml1:
    push {r4, r5, r6, r7, lr}
    ldr r5, =next_kml1
    movs r4, #0
    b kml
kml2:
    push {r4, r5, r6, r7, lr}
    ldr r5, =next_kml2
    movs r4, #1
    b kml
kml3:
    push {r4, r5, r6, r7, lr}
    ldr r5, =next_kml3
    movs r4, #1
kml:
    movs r7, r0                         @ (no arguments; r0 passed on as received)
    ldr r1, =$koma.ess
    ldrb r1, [r1]
    cmp r1, #1
    bne 3f
    ldr r5, =0x5aaf1
    cmp r4, #0
    beq 3f
    ldr r5, =0x5aeb5
3:  ldr r1, =vars
    ldrb r6, [r1, #15]
    cmp r6, #0
    beq 1f
    movs r0, #1
    bl kmap
1:  movs r0, r7
    movs r3, r5
    bl call_r3
    cmp r6, #0
    beq 2f
    movs r7, r0
    movs r0, #2
    bl kmap
    movs r0, r4
    bl kmid
    bl kmsync_in
    movs r0, r7
2:  pop {r4, r5, r6, r7}
    pop {r3}
    bx r3

kmc0:
    push {r4, r5, r6, r7, lr}
    ldr r5, =next_kmc0
    movs r7, #0
    b kmc
kmc1:
    push {r4, r5, r6, r7, lr}
    ldr r5, =next_kmc1
    movs r7, #1
    b kmc
kmc2:
    push {r4, r5, r6, r7, lr}
    ldr r5, =next_kmc2
    movs r7, #1
    b kmc
kmc3:
    push {r4, r5, r6, r7, lr}
    ldr r5, =next_kmc3
    movs r7, #1
kmc:
    movs r4, r0                         @ 0 / 1: from the cursors, else a saved record
    ldr r0, =$koma.ess
    ldrb r0, [r0]
    cmp r0, #1
    bne 3f
    ldr r5, =0x5b4e5
    cmp r7, #0
    beq 3f
    ldr r5, =0x5a261
3:  ldr r0, =vars
    ldrb r6, [r0, #15]
    cmp r6, #0
    beq kmc_call
    cmp r4, #1
    bhi 1f
    movs r0, r7
    bl kmid
    bl kmsync_out
    adds r6, r6, r0                     @ 2: this page was open, refill kmbuf after the commit
1:  movs r0, #1
    bl kmap
kmc_call:
    movs r0, r4
    movs r3, r5
    bl call_r3
    cmp r6, #0
    beq kmc_out
    movs r4, r0
    movs r0, #2
    bl kmap
    cmp r6, #2
    bne 2f
    movs r0, r7
    bl kmid
    bl kmsync_in
2:  movs r0, r4
kmc_out:
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3
.ltorg

kmid:                                   @ r0 = 0 AUTO hook, 1 P/A/M hook -> table id 0 AUTO, 1 P/A, 2 M
    cmp r0, #0
    beq 1f
    ldr r0, =0x1bf01d                   @ 0x6f7ec: 2 = M
    ldrb r0, [r0]
    cmp r0, #2
    beq 1f
    movs r0, #1
1:  bx lr

kmtab:                                  @ r0 = table id -> r4 stock table, r5 spec rows, r6 count, r7 kmbuf
    ldr r1, =kmtabs
    lsls r0, r0, #3
    adds r1, r1, r0
    ldr r4, [r1, #0]
    ldr r5, [r1, #4]
    ldrb r6, [r5, #0]
    adds r5, #2
    ldr r7, =kmbuf
    bx lr

kmsync_in:                              @ r0 = table id: stock rows (12 B, as loaded) and extra rows -> kmbuf
    push {r4, r5, r6, r7, lr}
    ldr r1, =kmv
    adds r2, r0, #1
    strb r2, [r1, #0]                   @ kmbuf now shows this table
    bl kmtab
mi_loop:
    cmp r6, #0
    beq mi_done
    ldrb r0, [r5]
    movs r2, #0
    cmp r0, #0x80
    bhs mi_x
    subs r0, #1
    movs r1, #12
    muls r0, r1, r0
    adds r1, r4, r0
    b mi_copy
mi_x:
    subs r0, #0x80
    lsls r0, r0, #3
    ldr r1, =kmrows
    adds r0, r1, r0
    ldr r1, [r0, #0]
    ldr r2, [r0, #4]
mi_copy:
    ldr r0, [r1, #0]
    str r0, [r7, #0]
    ldr r0, [r1, #4]
    str r0, [r7, #4]
    ldr r0, [r1, #8]
    str r0, [r7, #8]
    cmp r2, #0
    beq mi_next
    ldr r0, [r2, #0]                    @ the row's byte -> cursor (1-based), out of range -> default
    ldrb r0, [r0]
    ldrb r1, [r2, #12]
    cmp r0, r1
    blo 1f
    ldrb r0, [r2, #13]
1:  adds r0, #1
    strb r0, [r7, #9]
mi_next:
    adds r5, #1
    adds r7, #12
    subs r6, #1
    b mi_loop
mi_done:
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3

kmsync_out:                             @ r0 = table id -> r0 = 1 if kmbuf shows it (then: cursors -> table, extra
    push {r4, r5, r6, r7, lr}           @ rows -> bytes), else 0
    ldr r1, =kmv
    ldrb r2, [r1, #0]
    adds r3, r0, #1
    cmp r2, r3
    bne mo_none
    bl kmtab
mo_loop:
    cmp r6, #0
    beq mo_done
    ldrb r0, [r5]
    cmp r0, #0x80
    bhs mo_x
    subs r0, #1
    movs r1, #12
    muls r0, r1, r0
    adds r0, r4, r0
    ldrb r1, [r7, #9]
    strb r1, [r0, #9]
    b mo_next
mo_x:
    subs r0, #0x80
    lsls r0, r0, #3
    ldr r1, =kmrows
    adds r0, r1, r0
    ldr r2, [r0, #4]
    cmp r2, #0
    beq mo_next
    ldrb r0, [r7, #9]
    subs r0, #1
    ldrb r1, [r2, #12]
    cmp r0, r1
    bhs mo_next
    ldr r1, [r2, #0]
    ldrb r3, [r1]
    cmp r0, r3
    beq mo_next
    strb r0, [r1]
    ldr r3, [r2, #4]
    cmp r3, #0
    beq mo_next
    bl call_r3                          @ on_change(r0 = value, r1 = byte address)
mo_next:
    adds r5, #1
    adds r7, #12
    subs r6, #1
    b mo_loop
mo_done:
    movs r0, #1
    b 1f
mo_none:
    movs r0, #0
1:  pop {r4, r5, r6, r7}
    pop {r3}
    bx r3
.ltorg
@ GR II state (kmv +0 table kmbuf shows, +1 editor pending for kedpend, +2 back from the editor). Resident, outside the
@ layout overlays: kedpend runs in every layout, and an overlay alias (ovl + offset) would read 5-TAB's label tables.
.p2align 2
kmv:
    .byte 0, 0, 0, 0
"""
# RIGHT on EDIT RECIPE on the SHOOTING page (mode-app RIGHT handler words 0x1a3374 / 0x1a338c, stock 0x79118): the
# page's rows are written first (a RECIPE chosen here is the one edited), then the stock header RIGHT runs the SETUP app
# (as lefth does) with the editor pending (kedpend in setup_enter). After it returns, a LEFT from the editor (kmv +2)
# puts the cursor back on EDIT RECIPE. Not while the recipe row says OFF. Everything else: stock.
G2_RIGHT_SRC = """
kright:
    push {r4, r5, r6, r7, lr}
    movs r4, r0
    movs r5, r1
    movs r6, r2
    ldr r1, =vars
    ldrb r0, [r1, #15]
    cmp r0, #0
    beq kq_stock
    ldr r1, =D_STATE
    ldrb r0, [r1]
    cmp r0, #2
    bne kq_stock
    ldr r1, =D_TAB
    ldrb r0, [r1]
    cmp r0, #1
    bne kq_stock
    ldr r0, =0x1c23b8                   @ page 0 / 6 / 7 -> table id 0 / 1 / 2
    ldrb r0, [r0]
    cmp r0, #0
    beq 1f
    subs r0, #5
    cmp r0, #1
    beq 1f
    cmp r0, #2
    bne kq_stock
1:  movs r7, r0
    bl kmerow
    cmp r0, #0
    beq kq_stock
    ldr r1, =D_ROW
    ldrb r1, [r1]
    cmp r0, r1
    bne kq_stock
    movs r0, r7
    bl kmsync_out                       @ this page's rows reach their bytes (the RECIPE chosen here is the one edited)
    ldr r1, =RECIPE_BYTE
    ldrb r1, [r1]
    movs r0, #0
    cmp r1, #0
    beq kq_out
    ldr r1, =kmv
    movs r0, #1
    strb r0, [r1, #1]                   @ setup_enter: open the editor
    ldr r1, =vars
    movs r0, #2
    strb r0, [r1, #1]                   @ pending tab: KEY CUST. (under the editor)
    ldr r1, =D_STATE
    movs r0, #0
    strb r0, [r1]                       @ header: the stock RIGHT enters the SETUP app
    movs r0, #0x17
    movs r1, r5
    movs r2, r6
    ldr r3, =0x79119
    bl call_r3
    movs r4, r0
    ldr r1, =kmv
    movs r0, #0
    strb r0, [r1, #1]
    ldrb r2, [r1, #2]
    strb r0, [r1, #2]
    cmp r2, #0
    beq 3f
    movs r0, r7                         @ back from the editor with LEFT: cursor on EDIT RECIPE, row state
    bl kmerow
    ldr r1, =D_ROW
    strb r0, [r1]
    subs r0, #1
    movs r1, #1
2:  cmp r0, #5
    blo 4f
    subs r0, #5
    adds r1, #1
    b 2b
4:  ldr r0, =D_PAGE
    strb r1, [r0]
    ldr r1, =D_STATE
    movs r0, #2
    strb r0, [r1]
    bl redraw
3:  movs r0, r4
    b kq_out
kq_stock:
    movs r0, r4
    movs r1, r5
    movs r2, r6
    ldr r3, =0x79119
    bl call_r3
kq_out:
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3
.ltorg

kmerow:                                 @ r0 = table id -> r0 = its EDIT RECIPE row (0: none)
    ldr r1, =kmtabs
    lsls r0, r0, #3
    adds r1, r1, r0
    ldr r1, [r1, #4]
    ldrb r0, [r1, #1]
    bx lr

kedpend:                                @ setup_enter, before its redraw: kright asked for the editor
    push {r4, lr}
    ldr r1, =kmv
    ldrb r0, [r1, #1]
    cmp r0, #0
    beq 1f
    movs r0, #0
    strb r0, [r1, #1]
    ldr r0, =tblE
    movs r1, #ECOUNT
    movs r2, #0
    bl kshow
1:  pop {r4}
    pop {r3}
    bx r3
"""
# ESSENTIALS latched by the core settings init: rows of features the installer skipped keep showing their byte and
# storing a change, but their on_change (ktab +4) is cleared, so nothing runs
ESS_GATE_SRC = """    ldr r0, =$koma.ess
    ldrb r0, [r0]
    cmp r0, #1
    bne 3f
    ldr r1, =kgated
    ldr r2, =ktab
    movs r3, #0
4:  ldrb r0, [r1]
    cmp r0, #0xff
    beq 3f
    lsls r0, r0, #4
    adds r0, r0, r2
    str r3, [r0, #4]
    adds r1, #1
    b 4b
3:
"""
LAYOUT_BYTE = 0x1B8BB             # settingsio_menu.LAYOUT_BYTE: MENU LAYOUT 0 5-TAB, 1 STOCK+KOMA, 2 GR II
STOCK_SETUP_ROWS = 30
SK_STRIP = ["STILL", "KOMA", "SETUP"]   # STOCK+KOMA: slot 0 is the mode page (label from the stock page table)

# GR II: three tabs SHOOTING | KEY CUST. | SETUP. Slot 0 is the
# stock mode page (its tab label reads SHOOTING on the still pages, MOVIE / PLAY in those modes); on the still pages
# its display table is kmbuf (krowmap + the load / commit hooks), in G2_SHOOT order. Slots 1 and 2 are SETUP-app
# tables. In these lists: ints = stock SETUP rows (1..30), upper-case strings = stock still rows, other strings = Koma
# row ids, "edit" = EDIT RECIPE, "version" = the KOMA version row, "extra" = Koma focus / exposure / recipe rows with no
# slot of their own. Koma rows a build lacks are skipped; a still table without a stock row skips it (P/A: no RESTORE
# DEFAULTS, M: also no EXPOSURE COMP.).
G2_STRIP = ["SHOOTING", "KEY CUST.", "SETUP"]   # 9-char tab-label cap: KEY CUSTOM (10) -> KEY CUST.
G2_SHOOT = ["FOCUS", "snapdist", "fullsnap", "PHOTOMETRY", "PIC QUALITY/SIZE", 28, 19, "rcp_active", "edit", "IMG SET",
            "DATE IMPRINT", "CONT. MODE", "AUTO BRACKET", 29, "INTERVAL", "IMAGE WITH SOUND", "WHITE BALANCE",
            "ISO SETTING", "EXPOSURE COMP.", "longexp", "extra", "FLASH EXPO. COMP.", "RESTORE DEFAULTS"]
# SETUP-app rows on SHOOTING: (settings byte, SETUP commit value table (byte = table[cursor]), default byte). Their
# SHOOTING entry lists the stock options in byte order (cursor - 1 = byte), so they share the Koma rows' load / commit.
G2_SHOOT_SETUP = {19: (0x1B0A4, 0x1BEEEF, 0), 28: (0x1B193, 0x1BEF02, 0), 29: (0x1B194, 0x1BEF06, 0)}
G2_KEY = [26, 27, 21, 22, 23, 24, 25, 20, 4]
G2_SETUP = [15, 1, 2, 10, 3, 5, 7, 8, 6, "isomax", "slowshut", 18, 9, 30, 14, "hilite", 16, 11, 12, 17, 13,
            "menulayout", "komafeat", "savecard", "version"]
G2_SHOWN_TAIL = {"menulayout", "komafeat", "savecard"}   # decision 4: other settings / OTHER rows are hidden in GR II
# Still pages: AUTO 14 rows, P/A 13 (no RESTORE DEFAULTS),
# M 12 (also no EXPOSURE COMP.), same row order; each addressed through its row-index map region, which 0x29e14 fills
# with immediates (offset from 0x1a91ac: stock row number; disasm 0x29e5c..0x29f3c).
ROWMAP = 0x1A91AC
STOCK_STILL = ["PIC QUALITY/SIZE", "FOCUS", "PHOTOMETRY", "CONT. MODE", "IMG SET", "AUTO BRACKET", "INTERVAL",
               "IMAGE WITH SOUND", "DATE IMPRINT", "EXPOSURE COMP.", "WHITE BALANCE", "ISO SETTING",
               "FLASH EXPO. COMP.", "RESTORE DEFAULTS"]
STILL_TABLES = [   # (table, row count, {map offset: stock row})
    (0x1C1EE8, 14, {0x00: 1, 0x01: 2, 0x02: 3, 0x03: 4, 0x04: 4, 0x05: 6, 0x06: 8, 0x07: 7, 0x08: 8, 0x09: 9,
                    0x0A: 10, 0x0B: 11, 0x0C: 12, 0x0D: 14, 0x0E: 14, 0x0F: 5, 0x11: 5, 0x12: 13}),
    (0x1C20BC, 13, {0x2C: 1, 0x2D: 2, 0x2E: 3, 0x2F: 4, 0x30: 6, 0x31: 7, 0x32: 8, 0x33: 9, 0x34: 10, 0x35: 11,
                    0x36: 12, 0x37: 5, 0x38: 13}),
    (0x1C2158, 12, {0x39: 1, 0x3A: 2, 0x3B: 3, 0x3C: 4, 0x3D: 6, 0x3E: 7, 0x3F: 8, 0x40: 9, 0x41: 10, 0x42: 11,
                    0x43: 5, 0x44: 12}),
]
INIT_LIST_ROWMAP = 0x1A90F4      # 0x28a8c's init list (run by 0x281f2, right after the loader hook): 0x29e14|1
DESC_SLOTS = (0x1C23C0, 0x1C23D8, 0x1C23DC)    # descriptor table pointers of pages 0 (AUTO), 6 (P/A), 7 (M)
DESC_COUNTS = (0x1C23E4, 0x1C23EA, 0x1C23EB)
# still load / commit call sites (bwrecipes hooks the first seven too; menu5 is chained first): (site, target, kind)
G2_LOADS = [(0x5B394, 0x5AAF0, 0), (0x5B446, 0x5AAF0, 0), (0x5B3D4, 0x5AEB4, 1), (0x5B486, 0x5AEB4, 1)]
G2_COMMITS = [(0x58882, 0x5B4E4, 0), (0x5A0A8, 0x5A260, 1), (0x6DBB6, 0x5A260, 1), (0x58852, 0x5A260, 1)]
MODE_RIGHT = (0x1A3374, 0x1A338C)   # mode-app handler words of events 0x17 / 0x23 (RIGHT): 0x79118|1


def still_names(table, n):
    """Row names of a stock still table, by label pointer (the AUTO table names every row)."""
    auto = {struct.unpack(">I", fw.ram(0x1C1EE8 + 12 * k, 4))[0]: STOCK_STILL[k] for k in range(14)}
    return [auto[struct.unpack(">I", fw.ram(table + 12 * k, 4))[0]] for k in range(n)]


def g2_shoot(shoot):
    """Per still table: (table, spec, mapfix, editrow). spec = display rows: ("stock", k) = stock row k (1-based) of that
    table | ("x", key) = an extra row (key: ("row", id) | ("setup", n) | ("edit",)); mapfix = (map offset, stock value,
    SHOOTING position); editrow = EDIT RECIPE's position (0: none)."""
    out = []
    for table, n, rmap in STILL_TABLES:
        names = still_names(table, n)
        spec = []
        for x in shoot:
            if isinstance(x, str):
                if x in names:
                    spec.append(("stock", names.index(x) + 1))
            elif isinstance(x, int):
                spec.append(("x", ("setup", x)))
            elif x[0] == "edit":
                spec.append(("x", ("edit",)))
            else:
                spec.append(("x", ("row", x[1]["id"])))
        pos = {s[1]: j + 1 for j, s in enumerate(spec) if s[0] == "stock"}
        assert sorted(pos) == list(range(1, n + 1)), (hex(table), names)
        assert len(spec) <= MAX_ROWS
        editrow = spec.index(("x", ("edit",))) + 1 if ("x", ("edit",)) in spec else 0
        out.append((table, spec, [(o, v, pos[v]) for o, v in sorted(rmap.items())], editrow))
    return out


def ltab_src(name, s, G):
    """A label table filled by kinit: slot 0 holds the record length, slots 1..8 zero; the glyph string follows."""
    return (f".p2align 2\n{name}:\n    .word {name}_z - {name}\n    .space 32\n" + G.str_src(name + "_s", s)
            + f".p2align 2\n{name}_z:\n")


RECIPE_OFF_SRC = """    ldr r1, =RECIPE_BYTE
    ldrb r1, [r1]
    cmp r1, #0
    beq k_done
"""


def _entry_rows(entries, prefix, rows_index, labels, extra, recipe_row, ktab, kt_rows):
    """Table source for KOMA entries; appends ktab lines for the rows it shows."""
    out = []
    for j, e in enumerate(entries):
        kind = e[0]
        if kind in ("title", "blank"):
            name = f"{prefix}_t{j}"
            labels[name] = e[1] if kind == "title" else ""
            out.append(f"    .word {name}, 0\n    .byte 0, 0, 1, 0")
        elif kind == "edit":
            labels["ke_lab"] = EDIT_LABEL
            out.append(f"    .word ke_lab, 0\n    .byte 0, 0, 1, 0")
        elif kind == "version":
            labels["kv_lab"] = VERSION_LABEL
            out.append("kv_entry:\n    .word kv_lab, kv_opts\n    .byte 2, 1, 0, 0")
        elif kind == "etitle":
            labels["ket_lab"] = EDITING_LABEL
            if recipe_row is None:
                out.append("    .word ket_lab, 0\n    .byte 0, 0, 1, 0")
            else:
                i = rows_index[recipe_row["id"]]
                out.append(f"ket_entry:\n    .word ket_lab, kr_opts_{i}\n"
                           f"    .byte {len(recipe_row['options'])}, {recipe_row['default'] + 1}, 1, 0")
                ktab.append(_ktab_line(recipe_row, "ket_entry", readonly=True))
                kt_rows.append(None)
        else:
            r = e[1]
            i = rows_index[r["id"]]
            labels[f"kr_lab_{i}"] = r["label"]
            out.append(f"kr_e_{i}:\n    .word kr_lab_{i}, kr_opts_{i}\n"
                       f"    .byte {len(r['options'])}, {r['default'] + 1}, 0, 0")
            ktab.append(f"kt_{i}:\n" + _ktab_line(r, f"kr_e_{i}"))
            kt_rows.append(r)
    return out


def _ktab_line(r, entry, readonly=False):
    on = r["on_change"] + " + 1" if r.get("on_change") else "0"
    return (f"    .word 0x{r['byte']:x}, {on}, {entry}\n"
            f"    .byte {len(r['options'])}, {r['default']}, {1 if readonly else 0}, 0")


def row_variants(r):
    """A row's `variants` (dict(sel, alts)): from the row itself, else from its feature's ROWS (the linker's row
    collection keeps only the core keys). Validated like rows: label <= 14, options 1..9 chars, menu font glyphs, every
    alt no longer than the declared options."""
    v = r.get("variants")
    if v is None and r.get("feature"):
        from . import REGISTRY, module
        if r["feature"] in REGISTRY:
            v = next((x.get("variants") for x in getattr(module(r["feature"]), "ROWS", []) if x.get("id") == r["id"]),
                     None)
    if v is None:
        return None
    from ..linker import GLYPHS, ROW_LABEL_SOFT, ROW_OPT_MAX
    assert 0x1B000 <= v["sel"] < 0x1C000 and 1 <= len(v["alts"]) <= 8, r["id"]
    for lab, opts in v["alts"]:
        assert len(lab) <= ROW_LABEL_SOFT and 1 <= len(opts) <= len(r["options"]), (r["id"], lab)
        assert all(1 <= len(o) <= ROW_OPT_MAX for o in opts) and not set(lab + "".join(opts)) - GLYPHS, (r["id"], lab)
    return v


def _variant_src(rows, editor_ids, labels, extra):
    """kvtab: one entry per editor row with variants (see kvar)."""
    out = [".p2align 2", "kvtab:"]
    for i, r in enumerate(rows):
        v = row_variants(r) if r["id"] in editor_ids else None
        if not v:
            continue
        out.append(f"    .word 0x{v['sel']:x}, kr_e_{i}, kt_{i}\n    .byte {len(v['alts'])}, 0, 0, 0")
        for a, (lab, opts) in enumerate(v["alts"]):
            labels[f"kv_lab_{i}_{a}"] = lab
            for k, o in enumerate(opts):
                labels[f"kv_o_{i}_{a}_{k}"] = o
            extra.append(f".p2align 2\nkv_opts_{i}_{a}:\n    .word "
                         + ", ".join(f"kv_o_{i}_{a}_{k}" for k in range(len(opts))))
            out.append(f"    .word kv_lab_{i}_{a}, kv_opts_{i}_{a}\n"
                       f"    .byte {len(opts)}, {min(r['default'], len(opts) - 1)}, 0, 0")
    out.append("kvtab_end:")
    return out


def _stock_entry(M, src, lab_ref=None):
    """A display entry for stock SETUP row `src` (stock label unless lab_ref; stock options; cursor 1)."""
    stock = fw.ram(M.SETUP + 12 * (src - 1), 12)
    lab_w, opt_w = struct.unpack(">II", stock[:8])
    tail = stock[8:]
    return f"    .word {lab_ref or hex(lab_w)}, {hex(opt_w)}\n    .byte {tail[0]}, 1, {tail[2]}, 0"


def g2_layout(rows, main, editor):
    """GR II contents from the rows the KOMA tab shows. Returns (shoot, key, setup, hidden ids): shoot = G2_SHOOT resolved
    (stock still names | int SETUP row | ("row", row) | ("edit",)); key / setup = slot 1 / slot 2 tables: lists of
    int (stock SETUP row) | ("row", row) | ("version",)."""
    shown = {e[1]["id"]: e[1] for e in main if e[0] == "row"}
    placed = {x for x in G2_SHOOT + G2_KEY + G2_SETUP if isinstance(x, str)}
    extra_x, extra_disp, hidden = [], [], []
    for rid, r in shown.items():               # rows with no GR II slot: by group (decision 4 hides the tail)
        if rid in placed:
            continue
        g = r.get("group")
        if g in ("focus", "exposure", "recipe"):
            extra_x.append(rid)
        elif g == "display":
            extra_disp.append(rid)
        else:
            hidden.append(rid)

    def resolve(spec):
        out = []
        for x in spec:
            if isinstance(x, int) or x in STOCK_STILL:
                out.append(x)
            elif x == "edit":
                if editor and any(isinstance(o, tuple) and o[0] == "row" and o[1].get("group") == "recipe"
                                  for o in out):
                    out.append(("edit",))
            elif x == "version":
                out.append(("version",))
            elif x == "extra":
                out += [("row", shown[i]) for i in extra_x]
            elif x in shown:
                out.append(("row", shown[x]))
            if x == "hilite" or (x == "menulayout" and "hilite" not in shown):
                out += [("row", shown[i]) for i in extra_disp if ("row", shown[i]) not in out]
        return out
    shoot = resolve(G2_SHOOT)
    setup = resolve(G2_SETUP)
    key = resolve(G2_KEY)
    for x in extra_disp:
        if ("row", shown[x]) not in setup:
            setup.insert(-1, ("row", shown[x]))
    used = sorted(x for x in key + setup + shoot if isinstance(x, int))
    assert used == list(range(1, STOCK_SETUP_ROWS + 1)), used
    assert len(key) <= MAX_ROWS and len(setup) <= MAX_ROWS
    return shoot, key, setup, hidden


def _setup_row_src(M, n):
    """A SETUP-app row on SHOOTING: (template entry ref, kx entry source, extra source). The template lists the stock
    options in byte order; when that is the stock order, the stock SETUP row itself is the template."""
    byte, vtab, default = G2_SHOOT_SETUP[n]
    stock = fw.ram(M.SETUP + 12 * (n - 1), 12)
    lab_w, opt_w = struct.unpack(">II", stock[:8])
    cnt, flags = stock[8], stock[10]
    vals = list(fw.ram(vtab + 1, cnt))             # byte for cursor 1..cnt (the SETUP commit 0x6c4bc)
    assert sorted(vals) == list(range(cnt)) and default < cnt, (n, vals)
    kx = f".p2align 2\nkx_{n}:\n    .word 0x{byte:x}, 0, 0\n    .byte {cnt}, {default}, 0, 0"
    if vals == list(range(cnt)):
        return hex(M.SETUP + 12 * (n - 1)), kx, ""
    opts = struct.unpack(f">{cnt}I", fw.ram(opt_w, 4 * cnt))
    by_val = [opts[vals.index(v)] for v in range(cnt)]
    ex = (f".p2align 2\nkx_e_{n}:\n    .word 0x{lab_w:x}, kx_o_{n}\n    .byte {cnt}, 1, {flags}, 0\n"
          f"kx_o_{n}:\n    .word {', '.join(hex(w) for w in by_val)}")
    return f"kx_e_{n}", kx, ex


def _g2_src(rows, M, G, main, editor, rows_index, labels):
    """GR II tables, directory, strip, Koma-entry fixups and the SHOOTING page data (krowmap, kmsync)."""
    shoot, key, setup, hidden = g2_layout(rows, main, editor)
    out = [f".p2align 2\nkdir2:\n    .word metatab_g2, stripdata_g2\n    .byte {len(G2_STRIP)}, 1, 1, 0\n    .word 0, 0"]
    fix = []

    def entries(spec, prefix):
        words, maps = [], []
        for j, x in enumerate(spec):
            if isinstance(x, int):
                words.append(_stock_entry(M, x))
                maps.append(x)
                continue
            maps.append(0)
            if x[0] == "version":
                words.append("kv_entry_g2:\n    .word kv_lab, kv_opts\n    .byte 2, 1, 0, 0")
            else:
                r = x[1]
                i = rows_index[r["id"]]
                words.append(f"kg_e_{i}:\n    .word kr_lab_{i}, kr_opts_{i}\n"
                             f"    .byte {len(r['options'])}, {r['default'] + 1}, 0, 0")
                fix.append(f"    .word kt_{i}, kg_e_{i}")
        return words, maps
    for slot, spec in ((1, key), (2, setup)):
        words, maps = entries(spec, f"g{slot}")
        out.append(f".p2align 2\ntblG{slot}:\n" + "\n".join(words))
        out.append(f"mapG{slot}:\n    .byte {', '.join(map(str, maps))}")
    out.append(f".p2align 2\nmetatab_g2:\n    .word tbl0, map0, MAP0N\n    .word tblG1, mapG1, {len(key)}\n"
               f"    .word tblG2, mapG2, {len(setup)}")
    xs, x = [], M.X0
    for n in G2_STRIP:
        xs.append(x)
        x += M.ADV * len(n) + M.GAP
    assert x - M.GAP + 4 <= 356, x
    names = ["0", "strip_KEYCUST", "strip_SETUP"]
    out.append(".p2align 2\nstripdata_g2:\n" + "\n".join(
        f"    .hword {xs[i]}, {M.ADV * len(n)}\n    .word {names[i]}" for i, n in enumerate(G2_STRIP)))
    out.append(G.str_src("strip_KEYCUST", G2_STRIP[1]))
    out.append(G.str_src("g2_shoot_s", G2_STRIP[0]))
    out.append(".p2align 2\nkgfix:\n" + "\n".join(fix) + "\nkgfix_end:")
    # SHOOTING: one display table for the three still pages, a spec per table, the extra rows (single source: the KOMA
    # tab's entry + ktab entry for Koma rows, the stock SETUP row + a kx entry for SETUP-app rows)
    tabs = g2_shoot(shoot)
    mrows, mrow_src, extra = [], [], []
    for _, spec, _, _ in tabs:
        for kind, k in spec:
            if kind == "x" and k not in mrows:
                mrows.append(k)
    for k in mrows:
        if k[0] == "row":
            i = rows_index[k[1]]
            mrow_src.append(f"    .word kr_e_{i}, kt_{i}")
        elif k[0] == "setup":
            tmpl, kx, ex = _setup_row_src(M, k[1])
            mrow_src.append(f"    .word {tmpl}, kx_{k[1]}")
            extra += [kx] + ([ex] if ex else [])
        else:
            mrow_src.append("    .word kg_edit, 0")
            extra.append(".p2align 2\nkg_edit:\n    .word ke_lab, 0\n    .byte 0, 0, 1, 0")
    nmax = max(len(spec) for _, spec, _, _ in tabs)
    out.append(f".p2align 2\nkmbuf:\n    .space {12 * nmax}")
    out.append(".p2align 2\nkmtabs:\n" + "\n".join(f"    .word 0x{t:x}, kms{j}" for j, (t, _, _, _) in enumerate(tabs)))
    for j, (_, spec, _, erow) in enumerate(tabs):
        codes = [v if kind == "stock" else 0x80 + mrows.index(v) for kind, v in spec]
        out.append(f"kms{j}:\n    .byte {len(spec)}, {erow}, {', '.join(map(str, codes))}")
    out.append(".p2align 2\nkmrows:\n" + "\n".join(mrow_src))
    out += extra
    out.append("kmapfix:\n    .byte " + ", ".join(f"{o}, {v}, {w}" for _, _, mf, _ in tabs for o, v, w in mf)
               + ", 0xff")
    return out, dict(g1count=len(key), g2count=len(setup), hidden=hidden, kmn=[len(sp) for _, sp, _, _ in tabs],
                     editrows=[e for _, _, _, e in tabs], shoot=shoot, mrows=mrows)


def _tables(rows, M, G):
    """Display tables: menu5's stock tabs (SETUP without the old KOMA row), the KOMA tab in slot 1, the editor."""
    tabs = {k: list(v) for k, v in M.TABS.items()}
    assert tabs[4][-1][1] == "KOMA", "menu5 SETUP tab no longer ends with the KOMA row"
    tabs[4] = tabs[4][:-1]                          # SETUP: stock rows only
    main, editor, recipe_row = layout(rows)
    rows_index = {r["id"]: i for i, r in enumerate(rows)}
    out, extra, labels, ktab, kt_rows = [], [], {}, [".p2align 2", "ktab:"], []
    ov0, ov_labels = [], {}       # layout overlay 0 (5-TAB: its own tabs, metatab, strip, label tables); see make()
    optlists = {}                                   # identical option lists share one pointer list (Koma 0.3)
    for i, r in enumerate(rows):                    # every row's option list (editor title reuses the recipe list)
        key = tuple(r["options"])
        if key in optlists:
            extra.append(f".set kr_opts_{i}, kr_opts_{optlists[key]}")
            continue
        optlists[key] = i
        for k, o in enumerate(r["options"]):
            labels[f"kr_o_{i}_{k}"] = o
        extra.append(f".p2align 2\nkr_opts_{i}:\n    .word "
                     + ", ".join(f"kr_o_{i}_{k}" for k in range(len(r["options"]))))
    counts = {}
    for slot in range(5):
        if slot == 1:
            words = _entry_rows(main, "k1", rows_index, labels, extra, recipe_row, ktab, kt_rows)
            maps = [0] * len(main)
        else:
            words, maps = [], []
            for j, spec in enumerate(tabs[slot]):
                src, lab = spec[0], spec[1]
                lab_ref = None
                if lab is not None:
                    (labels if slot == 0 else ov_labels)[f"lab_{slot}_{j}"] = lab
                    lab_ref = f"lab_{slot}_{j}"
                if src:
                    stock = fw.ram(M.SETUP + 12 * (src - 1), 12)
                    lab_w, opt_w = struct.unpack(">II", stock[:8])
                    tail = stock[8:]
                    words.append(f"    .word {lab_ref or hex(lab_w)}, {hex(opt_w)}\n"
                                 f"    .byte {tail[0]}, 1, {tail[2]}, 0")
                else:
                    words.append(f"    .word {lab_ref}, 0\n    .byte 0, 0, 1, 0")
                maps.append(src)
        dst = out if slot < 2 else ov0
        dst.append(f".p2align 2\ntbl{slot}:\n" + "\n".join(words))
        dst.append(f"map{slot}:\n    .byte {', '.join(str(m) for m in maps)}")
        counts[slot] = len(maps)
    ov0.insert(0, ".p2align 2\nkdir0:\n    .word metatab, stripdata\n    .byte 5, 0, 0, 0\n    .word lt0_s, lt0_e")
    ov0.append(".p2align 2\nmetatab:\n" + "\n".join(f"    .word tbl{s}, map{s}, {counts[s]}" for s in range(5)))
    out.append(f"skip1:\n    .byte {', '.join(map(str, skips(main)))}")
    stockkoma = any(r["byte"] == LAYOUT_BYTE for r in rows)
    ov1, ov2 = [], []
    if stockkoma:       # STOCK+KOMA: mode page, KOMA, the stock SETUP table in place (identity map, 30 rows)
        ov1.append(f".p2align 2\nkdir1:\n    .word metatab_sk, stripdata_sk\n    .byte {len(SK_STRIP)}, 1, 0, 0\n"
                   "    .word 0, 0")
        ov1.append(f"metatab_sk:\n    .word tbl0, map0, {counts[0]}\n    .word tbl1, map1, {counts[1]}\n"
                   f"    .word 0x{M.SETUP:x}, mapS, {STOCK_SETUP_ROWS}")
        ov1.append(f"mapS:\n    .byte {', '.join(str(i) for i in range(1, STOCK_SETUP_ROWS + 1))}")
        g2, g2info = _g2_src(rows, M, G, main, editor, rows_index, labels)
        ov2 += [x.replace("MAP0N", str(counts[0])) for x in g2]
    else:
        g2info = None
    if editor:
        out.append(".p2align 2\ntblE:\n" + "\n".join(
            _entry_rows(editor, "kE", rows_index, labels, extra, recipe_row, ktab, kt_rows)))
        out.append(f"skipE:\n    .byte {', '.join(map(str, skips(editor)))}")
    out += _variant_src(rows, {e[1]["id"] for e in editor if e[0] == "row"}, labels, extra)    # always (kvar)
    out += extra
    # tab strip: slot 1 is as wide as MOVIE so either label fits (kstripfix swaps it in movie mode)
    widths = [M.ADV * len(n) for n in STRIP]
    widths[1] = M.ADV * max(len("KOMA"), len("MOVIE"))
    xs, x = [], M.X0
    for w in widths:
        xs.append(x)
        x += w + M.GAP
    assert x - M.GAP + 4 <= 356, x
    ov0.append(".p2align 2\nstripdata:\n" + "\n".join(
        f"    .hword {xs[i]}, {M.ADV * len(STRIP[i])}\n    .word strip_{STRIP[i]}" for i in range(5)))
    if stockkoma:
        xs, x = [], M.X0
        for n in SK_STRIP:
            xs.append(x)
            x += M.ADV * len(n) + M.GAP
        ov1.append(".p2align 2\nstripdata_sk:\n" + "\n".join(
            f"    .hword {xs[i]}, {M.ADV * len(n)}\n    .word {'0' if i == 0 else 'strip_' + n}"
            for i, n in enumerate(SK_STRIP)))
    for name in STRIP + ["MOVIE"]:
        out.append(G.str_src("strip_" + name, name))
    seen = {}                                       # one label table per distinct text; later names alias it
    out.append(".p2align 2\nlt_start:")
    for name, lab in list(labels.items()) + [("lab_still", "STILL"), ("lab_movie", "MOVIE"), ("lab_play", "PLAY"),
                                             ("lab_tab", "CHANGE TAB")]:
        if lab in seen:
            out.append(f".set {name}, {seen[lab]}")
            continue
        seen[lab] = name
        out.append(ltab_src(name, lab, G))
    out.append(".p2align 2\nlt_end:")
    ov0.append(".p2align 2\nlt0_s:")                  # overlay 0's label tables (kinit fills them from the directory)
    for name, lab in ov_labels.items():
        if lab in seen:
            ov0.append(f".set {name}, {seen[lab]}")
            continue
        seen[lab] = name
        ov0.append(ltab_src(name, lab, G))
    ov0.append(".p2align 2\nlt0_e:")
    # version row: options "x.y" and the build id, filled from $koma.version at SETUP entry (kver)
    nbuf = 2 * 16 + 2
    out.append(f".p2align 2\nkv_opts:\n    .word kv_short, kv_id\nkv_short:\n    .word {', '.join(['kv_short_s'] * 9)}\n"
               f"kv_id:\n    .word {', '.join(['kv_id_s'] * 9)}\nkv_short_s:\n    .space {nbuf}\nkv_id_s:\n    .space {nbuf}")
    out.append("\n".join(ktab + ["ktab_end:"]))
    gated = [k for k, r in enumerate(kt_rows) if r is not None and r.get("gated")]
    if gated:
        out.append(f"kgated:\n    .byte {', '.join(map(str, gated + [0xFF]))}")
    assert "STILL" not in [lab for n, lab in labels.items()], "lab_still must not alias a row label (GR II rewrites it)"
    info = dict(kcount=len(main), ecount=len(editor), recipe_row=recipe_row, stockkoma=stockkoma, gated=bool(gated),
                overlays=["\n".join(o) + "\n" for o in ([ov0, ov1, ov2] if stockkoma else [ov0])],
                g2=g2info,
                editrow=(main.index(("edit",)) + 1) if ("edit",) in main else 0)
    return "\n".join(out), info


# The vendor tab code with the tab count and the directory read from `vars` (filled by kinit) instead of immediates:
# vars +0 slot, +1 pending, +4 metatab, +8 stripdata, +12 tab count, +13 1 = the mode page is always slot 0.
DIR_EDITS = (
    ("vars:\n    .byte 0, 0, 0, 0\n", "vars:\n    .byte 0, 0, 0, 0\n    .word 0, 0\n    .byte 0, 0, 0, 0\n"),
    ("    ldr r3, =&metatab\n", "    ldr r3, =&vars\n    ldr r3, [r3, #4]\n"),
    ("mode_slot:                              @ -> r0: 1 movie page, 2 playback page, else 0 (still pages)\n",
     "mode_slot:                              @ -> r0: 1 movie page, 2 playback page, else 0 (still pages)\n"
     "    ldr r0, =&vars\n    ldrb r0, [r0, #13]\n    cmp r0, #0\n    bne ms_zero\n"),
    ("    cmp r0, #5\n    beq 2f\n    movs r0, #0\n", "    cmp r0, #5\n    beq 2f\nms_zero:\n    movs r0, #0\n"),
    ("    adds r0, #1\n    cmp r0, #5\n    blo 2f\n",
     "    adds r0, #1\n    ldr r1, =&vars\n    ldrb r1, [r1, #12]\n    cmp r0, r1\n    blo 2f\n"),
    ("c_left:\n    movs r5, #4\n", "c_left:\n    ldr r1, =&vars\n    ldrb r5, [r1, #12]\n    subs r5, #1\n"),
    ("    adds r5, r5, r0\n    cmp r5, #5\n    blo 1f\n    subs r5, #5\n",
     "    adds r5, r5, r0\n    ldrb r0, [r1, #12]\n    cmp r5, r0\n    blo 1f\n    subs r5, r5, r0\n"),
    ("    bl mode_slot\n    adds r0, #4\n    cmp r0, #5\n    blo 1f\n    subs r0, #5\n",
     "    bl mode_slot\n    ldr r1, =&vars\n    ldrb r1, [r1, #12]\n    adds r0, r0, r1\n    subs r0, #1\n"
     "    cmp r0, r1\n    blo 1f\n    subs r0, r0, r1\n"),
    ("    adds r4, #1\n    cmp r4, #5\n    blo st_loop\n",
     "    adds r4, #1\n    ldr r0, =&vars\n    ldrb r0, [r0, #12]\n    cmp r4, r0\n    blo st_loop\n"),
    ("    adds r7, #1\n    cmp r7, #5\n    blo s_slot\n",
     "    adds r7, #1\n    ldr r0, =&vars\n    ldrb r0, [r0, #12]\n    cmp r7, r0\n    blo s_slot\n"),
)


def build_source(rows):
    return _build(rows)[0]


def _build(rows):
    M, V2, G = legacy()
    from ..linker import KOMA_VERSION
    code = M.CODE
    assert code.count(V2.ANCHOR) == 1
    code = code.replace(V2.ANCHOR, V2.BAND_SRC)                       # v2: opaque header band
    strip_head = "strip:                                  @ r0 = layer\n    push {r4, r5, r6, r7, lr}\n"
    for a, b in (("init:\n    bx lr\n", ""),
                 ("    movs r0, #0\n    bl sync\n", "    movs r0, #0\n    bl sync\n    bl ksync_load\n"),
                 ("    movs r0, #1\n    bl sync\n", "    movs r0, #1\n    bl sync\n    bl ksync_store\n"),
                 ("\nclassify:", "\nclassify0:"),
                 # xlate: a displayed row past the slot's map (the recipe editor, when it has more rows than the KOMA
                 # tab) is "no stock row" (31), not the same-numbered stock SETUP row (DATE / LANGUAGE / VOLUME cases)
                 ("    cmp r4, r2\n    bhi 9f\n    subs r3, r4, #1\n    ldrb r0, [r1, r3]\n    cmp r0, #0\n"
                  "    bne 8f\n    movs r0, #31\n",
                  "    cmp r4, r2\n    bhi xlate_none\n    subs r3, r4, #1\n    ldrb r0, [r1, r3]\n    cmp r0, #0\n"
                  "    bne 8f\nxlate_none:\n    movs r0, #31\n"),
                 (strip_head, strip_head + "    movs r4, r0\n    bl kstripfix\n    movs r0, r4\n"),
                 *DIR_EDITS):
        assert code.count(a) == 1, a
        code = code.replace(a, b)
    assert code.count("    ldr r5, =&stripdata\n") == 2
    code = code.replace("    ldr r5, =&stripdata\n", "    ldr r5, =&vars\n    ldr r5, [r5, #8]\n")
    data, info = _tables(rows, M, G)
    g2 = info["g2"]
    g2e = bool(g2 and any(g2["editrows"]) and info["ecount"] and info["recipe_row"])
    if g2e:                         # GR II: the editor opened from SHOOTING replaces the tab before the first redraw
        a = "2:  bl select\n    bl redraw\n    movs r0, r4\n"
        assert code.count(a) == 1
        code = code.replace(a, "2:  bl select\n    bl kedpend\n    bl redraw\n    movs r0, r4\n")
    src = KOMA_SRC
    if info["ecount"]:
        rr = info["recipe_row"]
        src = src.replace("EDIT_KEYS\n", EDIT_KEYS_SRC).replace(
            "RECIPE_OFF_CHECK\n", RECIPE_OFF_SRC.replace("RECIPE_BYTE", f"0x{rr['byte']:x}") if rr else "")
        src = src.replace("ED_TBL", "tblE").replace("ED_SKIP", "skipE")
    else:
        src = src.replace("EDIT_KEYS\n", "").replace("ED_TBL", "0").replace("ED_SKIP", "skip1")
    src = src.replace("EDIT_SRC\n", "")
    src = src.replace("ESS_GATE\n", ESS_GATE_SRC if info["gated"] else "")
    mode = G2_MODE_SRC + (G2_RIGHT_SRC.replace("RECIPE_BYTE", f"0x{info['recipe_row']['byte']:x}") if g2e else "")
    for ph, body, on in (("G2_INIT", G2_INIT_SRC, g2), ("G2_STRIPFIX", G2_STRIPFIX_SRC, g2), ("G2_KV", G2_KV_SRC, g2),
                         ("G2_ROWMAP", G2_ROWMAP_SRC, g2), ("G2_MODE", mode, g2), ("G2_BACK", G2_BACK_SRC, g2e)):
        assert src.count(ph + "\n") == (1 if on or ph != "G2_BACK" else src.count(ph + "\n")), ph  # BACK: editor only
        src = src.replace(ph + "\n", body if on else "")
    equ = "".join(f".equ {k}, {v}\n" for k, v in dict(
        VSKIP=len("KOMA V"), VSHORT=len(KOMA_VERSION), KCOUNT=info["kcount"], ECOUNT=info["ecount"],
        EDITROW=info["editrow"], KOMA_W=M.ADV * len("KOMA"), MOVIE_W=M.ADV * len("MOVIE"),
        D_TAB=hex(D_TAB), D_TBL=hex(D_TBL), D_CNT=hex(D_CNT), D_PAGE=hex(D_PAGE), D_ROW=hex(D_ROW),
        D_STATE=hex(D_STATE), **{f"KMN{j}": (g2["kmn"][j] if g2 else 0) for j in range(3)}).items())
    info["g2e"] = g2e
    info["overlays"] = [_deamp(o) for o in info["overlays"]]
    return _deamp(equ + code + src + "\n" + data + "\n"), info


def records():
    M, _, _ = legacy()
    redirects, pointers, patches = [], [], []
    for site, kind, arg, before in M.patches():
        if kind == 1:
            redirects.append(Redirect(site, before, arg, "menu5tab-v2 record"))
        elif kind == 2:
            label, add = arg
            pointers.append(Pointer(site, before, label, thumb=bool(add), why="menu5tab-v2 record"))
        else:
            patches.append(Patch(site, before, struct.pack(">I", arg), "menu5tab-v2 record"))
    return redirects, pointers, patches


def rowmap_pointer():
    """GR II still-page reorder: the init-list word that calls the row-map init 0x29e14 -> krowmap (which calls it)."""
    return Pointer(INIT_LIST_ROWMAP, fw.ram(INIT_LIST_ROWMAP, 4), "krowmap", thumb=True,
                   why="GR II layout: row-map init wrapper (reorders the still pages when MENU LAYOUT = 2)")


def mode_hooks():
    """GR II SHOOTING rows: the still load / commit hooks (chained before bwrecipes' IMG SET hooks on the same sites)."""
    return ([Hook(s, t, f"kml{i}", f"next_kml{i}", "GR II SHOOTING: still load, map at stock values, then kmbuf",
                  smoke_reachable=False) for i, (s, t, _) in enumerate(G2_LOADS)]
            + [Hook(s, t, f"kmc{i}", f"next_kmc{i}", "GR II SHOOTING: kmbuf -> table and extra-row bytes, still commit",
                    smoke_reachable=False) for i, (s, t, _) in enumerate(G2_COMMITS)])


def make(opts):
    redirects, pointers, patches = records()
    f = Feature(NAME, "3.0", SUMMARY, options=opts, source="@ generated at link time (late_source)\n",
                redirects=redirects, pointers=pointers, patches=patches, init="kinit", smoke=smoke_checks)

    def late(rows):
        # GR II records exist only in builds with the MENU LAYOUT row; they are added here, where the rows are known
        # (the linker copies the feature after this call, so the list edits are part of this link). They come after
        # the ESSENTIALS split on purpose: sharing the load / commit sites must not make bwrecipes essential; the
        # hooks call the stock function directly under ESSENTIALS instead.
        src, info = _build(rows)
        # MENU LAYOUT tables as overlays: 5-TAB in place (resident), STOCK+KOMA and GR II in the transient body, copied
        # over it at install when MENU LAYOUT selects them (linker: Feature.overlays)
        f.overlays = info["overlays"]
        f.overlay_select = LAYOUT_BYTE if info["stockkoma"] else None
        own = {INIT_LIST_ROWMAP, *MODE_RIGHT}
        f.pointers[:] = [p for p in f.pointers if p.addr not in own]
        f.hooks[:] = []
        if info["g2"]:
            f.pointers.append(rowmap_pointer())
            f.hooks.extend(mode_hooks())
            if info["g2e"]:
                f.pointers.extend(Pointer(a, fw.ram(a, 4), "kright", thumb=True,
                                          why="GR II: RIGHT on EDIT RECIPE (SHOOTING) opens the recipe editor")
                                  for a in MODE_RIGHT)
        return src
    f.late_source = late
    return f


def smoke_setup():
    return {}


def smoke_checks(obs):
    """Settings block intact; the Koma tail is either initialised (builds with ROWS) or untouched (menu5 alone)."""
    stock, tail = obs.ram(0x1B000, 4), obs.ram(0x1B8B0, 5)
    return [("stock settings magic MG1H intact", stock == b"MG1H", stock.hex()),
            ("Koma tail: 'KOMA' v1, or untouched without rows", tail == b"KOMA\x01" or tail[:4] != b"KOMA",
             tail.hex())]
