"""bwrecipes: GR II-style Image Settings ("recipes") as virtual IMG SET entries: B&W, SELECT and colour. Koma 0.2, v4.

The feature NAME stays "bwrecipes" (module file and namespace unchanged, so builds, tests and render scripts keep
their symbols); since v4 it carries the colour recipes too.

A recipe is a table row: base tone curve + contrast / high-low key / shadow / highlight (parametric, generated on
the fly) + three type-specific slots + sharpness, plus a kind byte (low 2 bits = type: 0 B&W, 1 colour, 2 SELECT;
bits 4..6 = the colour look whose ROM matrix a colour recipe uses). 10 curated B&W looks, SELECT, 6 colour looks and
3 custom slots (CUSTOM 1..3, defaults = MONO), every one editable in camera, edits sticking to the look, with RESET
and SAVE TO CUSTOM (which copies the kind, so a colour look saved to CUSTOM keeps its matrix).

  type      p5            p6          p7            base 0x1b030
  B&W       FILTER EFF.   TONING      TONE LEVEL    3, or 6 when toned
  colour    SATURATION    WARMTH      TINT          1 (NORMAL)
  SELECT    COLOUR        RANGE       SATURATION    1 (NORMAL)

Selection (never a non-stock value in the stock byte):
  * IMG SET list (menu, AUTO / P-A / M pages): the 7 stock entries stay; then the 10 B&W looks, SELECT, the 6 colour
    looks, CUSTOM 1..3. Choosing a recipe stores its base (1, 3 or 6) in 0x1b030 plus the recipe id in byte 0x1b8c0;
    a stock entry clears the id. The row shows the recipe name.
  * ADJ lever (IMG SET assigned to an ADJ slot): stepping walks stock entries then recipes, wrapping.
  * KOMA tab: RECIPE row (same id byte) + EDIT RECIPE sub-page rows for the ACTIVE recipe (edit buffer
    0x1b8c1..0x1b8c9, SAVE TO CUSTOM 0x1b8ca, RESET 0x1b8cb, GRAIN 0x1b8cc, VIGNETTE 0x1b8cd), synced into the per-recipe record store by `sync`
    (on_change, menu/ADJ hooks, every shot). Rows p5..p7 carry per-type labels (menu5 row variants, selector byte
    0x1b908 = the active recipe's type, published by `sync`).
Store: 'RCP6' v6 at 0x1b900 (Koma 0.3): +8 active type, +9 SELECT colour (1..6, 0 = no SELECT shot), +10 SELECT
range (both read by selcolour), +11 GRAIN, +12 VIGNETTE (read by fx), +13 owner id of the GRAIN / VIGNETTE rows; records
12 B from 0x1b910: p0..p8, kind, 0xa5, xor (unchanged since v5); then the FX table, one byte per recipe at 0x1ba00
(0x50 | grain | vignette << 2; anything else = OFF / OFF). EDIT RECIPE rows GRAIN 0x1b8cc, VIGNETTE 0x1b8cd. An 'RCP5'
v5 store (Koma 0.2) keeps every record (CUSTOM 1..3 included), its FX table starts OFF. An 'RCP4' v4 store (Koma 0.2
B&W) is migrated: B&W records stay, CUSTOM 1..3 move from ids 11..13 to 18..20. Any other header resets every record.
Every rewrite writes the records and the FX table first and the header last, and clears the active id (0x1b8c0, review
F3), SAVE TO CUSTOM, RESET and the GRAIN / VIGNETTE rows. fx build options never change this layout. A recipe applies
to a shot only while 0x1b030 equals its base: pull the card and the camera sees plain NORMAL / B&W / B&W(TE).

Per shot (hooks on the stock mono writer call, both develop passes):
  pass 1 0x96436 (0x96350): B&W: filter luma weights (+ red WB tilt), toning word. Colour: chroma gain 0x12018004
                            (SATURATION), MatR/G/B 0x12018104/108/10c (the look's matrix, TINT on the G row), WB
                            0x12018110/114 (WARMTH, R/B multipliers on the shot's AWB). SELECT: chroma gain. All types:
                            curve upload (16 x 256 entries through 0x16d31c(offset, 256, buf)), CC2 cache 0x1f4db3 := 0xff
  pass 2 0xafd3e (0xafd20): the same register writes again (pass 2 rewrites WB, chroma, matrix, mono; no LUT)
  sharpness getter 0x91268 at 0x91a8c (develop) and 0x4165a (EXIF): returns the recipe's sharpness.
  SELECT's hue selection is selcolour's UV-plane pass (it reads +9/+10 of the store header).
"""
from __future__ import annotations

import csv
import functools
import hashlib
import json
import struct

from .. import firmware as fw
from ..feature import Check, Feature, Hook, Settings

NAME = "bwrecipes"
SUMMARY = "recipes: 10 B&W + SELECT + 6 colour looks + 3 custom virtual IMG SET entries, editable per look"
OPTIONS = {"adj": "on (default) | off: no ADJ-lever records (hooks, ADJ item list/count, hint table); bisect build"}
VERSION = "4.2"

# ------------------------------------------------------------------ settings layout
ACT = 0x1B8C0          # active recipe id, 0 = none (ROWS byte; core range)
EB = 0x1B8C1           # edit buffer p0..p8 of the active recipe (ROWS bytes)
SAVE = 0x1B8CA         # SAVE TO CUSTOM row (0 OFF, k = copy the active recipe into CUSTOM k, then back to 0)
RST = 0x1B8CB          # RESET row (0 NO, 1 YES)
ST = 0x1B900           # 'RCP5' store: +0 magic, +4 version, +5 edit-row owner id, +6 last applied id, +7 N,
ST_END = 0x1BC00       # +8 active type, +9 SELECT colour, +10 SELECT range, +11 GRAIN, +12 VIGNETTE (published by
                       # sync), +13 GRAIN / VIGNETTE row owner, +14..15 0
SELB = ST + 8          # menu5 editor variant selector: type of the active recipe (0 when none)
SELCOL, SELRNG = ST + 9, ST + 10     # SELECT applying to the next shot: colour 1..6 (0 = none), range 0/1
REC = ST + 0x10        # records, 12 B each: p0..p8, kind, VALID, xor(bytes 0..9) ^ 0x5a
RECLEN = 12
NPARAM = 9             # p0 curve, p1 contrast, p2 key, p3 shadow, p4 highlight, p5..p7 by type, p8 sharp
DEFLEN = NPARAM + 1    # default-table row: p0..p8, kind
VALID = 0xA5
MAGIC = b"RCP6"
STORE_VER = 6
V5_MAGIC = b"RCP5"     # Koma 0.2: the same header and records, no FX table
FXG, FXV = 0x1B8CC, 0x1B8CD          # EDIT RECIPE rows GRAIN / VIGNETTE of the active recipe (the reserved tail bytes)
FXGRAIN, FXVIG, FXOWN = ST + 11, ST + 12, ST + 13
FXMARK = 0x50          # FX table byte = FXMARK | grain | vignette << 2 (anything else reads OFF / OFF)
V4_MAGIC, V4_N, V4_NBW = b"RCP4", 13, 10        # Koma 0.2 B&W store: 10 looks + CUSTOM 1..3 (ids 11..13)
# 0.3 accepts RCP6 only with exactly N records: the FX table sits at REC + N * RECLEN, so a store with more records
# (a later release) would put records where 0.3 reads FX bytes. 0.2.2 accepts RCP6 with N or more (no FX table there).
TYPE_BW, TYPE_COLOUR, TYPE_SEL = 0, 1, 2
MAXTYPE = TYPE_SEL     # kind & 3 above this (or a colour look index >= NCOL) -> the look's default

# ------------------------------------------------------------------ curves
LO, HI, PIVOT = 65, 1023, 480       # LO = every base table's value at index 257 (end of Ricoh's pinned toe)
KEY_SH, CON_SH, SHD_SH, HIL_SH = 13, 17, 12, 13
LUTN, CHUNK = 4096, 256
LUTS = fw.DATA / "recipes/luts"
PINNED = fw.DATA / "tone-pinned.csv"          # sib-R3-18c3c4 column


def _rom(addr):
    return list(struct.unpack(">4096H", fw.ram(addr, 0x2000)))


def _csv(name):
    return [int(r["value"]) for r in csv.DictReader(open(LUTS / f"{name}.csv"))]


def _pinned(col):
    return [int(r[col]) for r in csv.DictReader(open(PINNED))]


# ------------------------------------------------------------------ GR DIGITAL IV looks (measured firmware tables)
# data/grd4/grd4-looks.json: derived numbers measured from the GRD IV 2.30 tables (static analysis of the M32R image;
# GRD IV firmware README, pass 2), no table rows: 33-point tone-curve values (x = 0, 8, .., 248, 255) for Standard A5
# (+ A6, Vivid's contrast +1), Bleach Bypass B5 and Positive Film C5; per light x sector chroma responses of Standard
# (c0) and of the Bleach Bypass / Positive Film colour groups (ct) at each sector-centre hue; mean gains; WB factors.
# The CMAT reading behind the responses (I): 24 hue boundaries (u8, 256 = 360 deg) + per-sector 2x2 on (Cb, Cr).
# Curve mapping (gamma-consistent): the GRD IV curve sits after its own gamma stage, so only its shape RELATIVE to
# Standard is used: on the 8-bit display-referred output, look = T_look(T_A5^-1(v)). Both cameras' outputs are
# display-encoded, so this remap is applied to the STANDARD table's 10-bit output (v8 = v * 255/1023, back x 1023/255),
# entry by entry in the 12-bit index domain, Ricoh's toe (indices 0..257) kept, running max. That remap is what
# `fval` is (an output-domain map of the base), so a look is fitted as STANDARD + con/key/shd/hil. Check of the
# method: A6 (GRD IV contrast +1) comes out as STANDARD CONTRAST +1 at 0.6 level rms.
GRD4 = fw.DATA / "grd4"
GRD4_X = list(range(0, 256, 8)) + [255]


@functools.lru_cache(maxsize=None)
def _grd4():
    return json.loads((GRD4 / "grd4-looks.json").read_text())


def grd4_curve33(family, level):
    return tuple(_grd4()["curves33"][f"{family}{level}"])


def _interp(x, xp, fp):
    """np.interp in pure Python (xp increasing, ends clamped)."""
    if x <= xp[0]:
        return float(fp[0])
    for i in range(1, len(xp)):
        if x <= xp[i]:
            return fp[i - 1] + (fp[i] - fp[i - 1]) * (x - xp[i - 1]) / (xp[i] - xp[i - 1])
    return float(fp[-1])


def grd4_target(family, level, base=1):
    """4,096-entry target table: base (default STANDARD) remapped by GRD IV curve family/level relative to A5."""
    a5, t = grd4_curve33("A", 5), grd4_curve33(family, level)
    out, m = [], 0
    for i, v in enumerate(base_table(base)):
        if i > 257:
            v = min(1023, round(_interp(_interp(v * 255 / 1023, a5, GRD4_X), GRD4_X, t) * 1023 / 255))
        m = max(m, v)
        out.append(m)
    return out


def step_limit(t):
    """Nearest table the 2-bit coding holds: steps clamped to 0..3 (lags a too-steep stretch, then catches up)."""
    out, v = [], 0
    for x in t:
        v += max(0, min(3, x - v))
        out.append(v)
    return out


# (menu name, intent, loader). Every table keeps Ricoh's toe: indices 0..257 identical (black clip 170, 256 -> 64).
# 7 B&W bases (v2 had 10). Cut: DENSE (monochrom-like) is within 1.4 mean / 4 max 8-bit levels of SOFT MONO, so the
# DENSE look moved onto SOFT MONO; c1 SOFT and SILVER lost their only looks (TONED 1, BLEACH). bwrecipes.md has the
# distance table. The same 7 bases serve colour and SELECT (one CURVE list for every type): the proposed colour bases
# SLIDE (fuji-provia-pinned) and NEG (portra-400-like-pinned) are 2.1/4.2 and 2.0/3.7 8-bit levels (mean/max) from
# STANDARD, and each colour look's own curve (base + its con/key/shd/hil) is within 1.5..2.2 mean / 3.5..5.0 max of
# STANDARD + the same settings: near-duplicates by the DENSE rule, so no colour curve is stored (bwrecipes.md).
# A new base still appends here (base id = index; 1,024 B each in the blob).
# v4.1 (GRD IV refit): BLEACH = the GRD IV Bleach Bypass curve B5 on STANDARD (grd4_target); the best con/key/shd/hil
# on STANDARD misses it by 9.2 levels rms (its mid lift 120 -> 172 is past KEY +4), so it is the one new base. The
# 2-bit coding clips 16 steps of 4 (step_limit; max 0.25 level from the target). Positive Film C5 fits STANDARD +
# con/key/shd/hil at 2.2 levels rms, under the ~3-level bar: no POSI base.
BASES = [
    ("R3", "Ricoh R3 sibling curve sib-R3-18c3c4 (owner's look)", lambda: _pinned("sib-R3-18c3c4")),
    ("STANDARD", "stock B&W contrast 3 (NORMAL c3)", lambda: _rom(0x19BB30)),
    ("HARD", "stock contrast 5", lambda: _rom(0x199B30)),
    ("HI-CONT", "GR Hi-Contrast B&W, pinned", lambda: _csv("hi-contrast-bw")),
    ("SOFT MONO", "GR III Soft Monotone, pinned (also carries DENSE: monochrom-like is within 4/255)",
     lambda: _csv("soft-monotone")),
    ("FILM 400", "classic 400-speed cubic-grain film, pinned (tri-x-like fit)", lambda: _csv("tri-x-like")),
    ("FINE 100", "fine-grain 100-speed film: soft shadows, hard mids-highlights (acros-like fit)",
     lambda: _csv("acros-like")),
    ("BLEACH", "GR DIGITAL IV Bleach Bypass curve B5 relative to its Standard A5, on STANDARD (measured firmware)",
     lambda: step_limit(grd4_target("B", 5))),
]
NB = len(BASES)
NB_BW = NB             # bases 0..NB_BW-1 = the CURVE row's options, every type (BLEACH included)
R3_SHA = "9aad2861"


@functools.lru_cache(maxsize=None)
def base_table(i):
    return tuple(BASES[i][2]())


def encode(t):
    """2-bit first differences, MSB first, t[0] = 0 implicit. Every pinned base has steps 0..3 (lossless)."""
    assert len(t) == LUTN and t[0] == 0
    d = [t[0]] + [t[i] - t[i - 1] for i in range(1, LUTN)]
    assert all(0 <= x <= 3 for x in d), "base curve has a step outside 0..3; 2-bit coding cannot hold it"
    out = bytearray()
    for i in range(0, LUTN, 4):
        out.append((d[i] << 6) | (d[i + 1] << 4) | (d[i + 2] << 2) | d[i + 3])
    return bytes(out)


def decode(blob):
    t, v = [], 0
    for b in blob:
        for s in (6, 4, 2, 0):
            v += (b >> s) & 3
            t.append(v)
    return t


def fval(v, k, c, s, h):
    """The on-camera tone map, integer-exact (asr = Python >> on negatives). Endpoints LO and HI stay fixed."""
    if v <= LO:
        return v
    d, e = v - LO, HI - v
    a = d * e
    t = (k * a) >> KEY_SH
    vp = v - PIVOT
    t += (c * ((a >> 4) * vp)) >> CON_SH
    if vp < 0:
        t += (s * (-vp * d)) >> SHD_SH
    else:
        t += (h * (vp * e)) >> HIL_SH
    return min(HI, max(LO, v + t))


def curve(p):
    """Full 4,096-entry CC2 stream for params p (base, contrast, key, shadow, highlight as option indices)."""
    k, c, s, h = p[2] - 4, p[1] - 4, p[3] - 4, p[4] - 4
    out, m = [], 0
    for v in decode(encode(base_table(p[0]))):
        m = max(m, fval(v, k, c, s, h))
        out.append(m)
    return out


# ------------------------------------------------------------------ filters, toning
# B&W filters: CC2 luma weights 0x1201800c (ids 1..4); red adds a WB tilt
# (Q16 multipliers on the shot's own AWB values cp+0x268/26a/26c, clamped to 0x1ff).
FILTERS = ["OFF", "YELLOW", "ORANGE", "RED", "GREEN"]
FTAB = [(0x4008C31A, 0, 0, 0), (0x4008FF0A, 0, 0, 0), (0x4008FF00, 0xD8C5, 0xB715, 0x752C), (0x40086633, 0, 0, 0)]
# Toning (GR III names): (Cb, Cr) per level 1..5 from the stock TE table rows (0x1f4c88 + 40*tint + 8*level),
# written as the B&W(TE) mono word after the stock writer. SEPIA = TE sepia (6), SELENIUM = TE purple (4: cool
# purple-brown blacks, v2's TONED 3), BLUE = TE blue (3: v2's TONED 4 cyanotype and HARD MONO's cool), GREEN = TE
# green (2). The menu-less TE tint 5 (Cr-only green, -45..-142) is stronger; not used (static only, never shot).
TONES = ["OFF", "SEPIA", "SELENIUM", "BLUE", "GREEN"]
TE_TINT = {"SEPIA": 6, "SELENIUM": 4, "BLUE": 3, "GREEN": 2}


def _s10(x):
    x &= 0x3FF
    return x - 1024 if x >= 512 else x


def te_row(tint):
    out = []
    for d in range(1, 6):
        a = 0x1F4C88 + 40 * tint + 8 * d
        cb, cr = struct.unpack(">II", fw.ram(a, 8))
        out.append((_s10(cb), _s10(cr)))
    return out


def tone_table():
    return [te_row(TE_TINT[n]) for n in TONES[1:]]


# ------------------------------------------------------------------ recipes
SIGNED = ["-4", "-3", "-2", "-1", "0", "+1", "+2", "+3", "+4"]
SHARP = ["-2", "-1", "0", "+1", "+2"]
DENS = ["1", "2", "3", "4", "5"]
STEP7 = ["-3", "-2", "-1", "0", "+1", "+2", "+3"]
WARMS = ["COOL 3", "COOL 2", "COOL 1", "0", "WARM 1", "WARM 2", "WARM 3"]
TINTS = ["MAG 3", "MAG 2", "MAG 1", "0", "GREEN 1", "GREEN 2", "GREEN 3"]
SEL_COLOURS = ["RED", "ORANGE", "YELLOW", "GREEN", "BLUE", "MAGENTA"]     # selcolour COLOUR 1..6 (stored 0..5)
SEL_RANGES = ["NARROW", "WIDE"]
GRAINS = ["OFF", "1", "2", "3"]                         # every type: fx (0 = OFF, zero cost); digits share VIGNETTE's labels
VIGS = ["OFF", "1", "2", "3"]
B = {n: i for i, (n, _, _) in enumerate(BASES)}
F = {n: i for i, n in enumerate(FILTERS)}
T = {n: i for i, n in enumerate(TONES)}
P_CURVE, P_CON, P_KEY, P_SHD, P_HIL, P_FILT, P_TONE, P_DENS, P_SHARP = range(NPARAM)
P_SAT, P_WARM, P_TINT = 5, 6, 7          # colour
P_SCOL, P_SRNG, P_SSAT = 5, 6, 7         # SELECT

# SATURATION: chroma gain 0x12018004 = stock NORMAL word 84 70 69 7c (isp-capabilities section 3: 0x922bc(1) writes
# it every NORMAL shot; byte roles U) with every byte scaled, rounded, capped at 0xff. Steps x0.55..x1.45 by 0.15:
# PROPOSAL section 2's starting values (POSITIVE 1.30, NEGATIVE 0.85, BLEACH 0.55) land exactly on steps.
STOCK_CHROMA = 0x8470697C
SAT_STEPS = [0.55, 0.70, 0.85, 1.00, 1.15, 1.30, 1.45]


def chroma_word(i):
    return int.from_bytes(bytes(min(0xFF, round(b * SAT_STEPS[i])) for b in STOCK_CHROMA.to_bytes(4, "big")), "big")


# WARMTH: WB multipliers on the shot's own AWB values, written like the stock head of 0x96350 does (R = cp+0x268 *
# 16/25, G = cp+0x26a, B = cp+0x26c * 16/25): R x (1 + 0.035 s), B x (1 - 0.035 s)
# for s = -3..+3 (+-10.5 % at the ends), G untouched, each clamped to 0x1ff. Q16 constants.
WB0 = 41943            # 16/25 in Q16
WK = 1468              # 0.035 * 16/25 in Q16


def wb_words(cp_wb, warm_step):
    """(0x12018110, 0x12018114) for AWB values (r, g, b) at cp+0x268.. and WARMTH option 0..6; None for 0 (step 3)."""
    s = warm_step - 3
    if s == 0:
        return None
    r, g, b = cp_wb
    g9 = lambda v, m: min(0x1FF, (v * m) >> 16)     # noqa: E731  (the module's gain9)
    return (g9(r, WB0 + WK * s) << 16) | g9(g, 0x10000), g9(b, WB0 - WK * s)


# Colour matrix MatR/G/B 0x12018104/108/10c (O-hw probes 2 + 3; model v2, vendored as vendor/colour/
# matrix_model.py): row = output channel; bytes b3 = R input, b2/b1 = G-like, b0 = B input; sign + 6-bit magnitude
# (bit 7 = sign, confirmed), bit 6 ignored; rows superpose linearly; acts on chroma after the tone curve, luma unchanged. Per look: a ROM constant derived by
# derive_matrix() from the colour-recipes lane's recipes.json tier B
# (`M_rgb_linear`, a pre-tone linear 3x3) by a
# least-squares fit of the chroma change that matrix makes through the STANDARD curve, in model v2's chroma-domain
# units (luma change dropped: the stage cannot make one), then matrix_model.encode(). Frozen here so a build needs no
# numpy.
# TINT: a G-row delta (probe 2: a positive MatG entry moves chroma along -Cb -Cr, i.e. green; negative = magenta), one
# LSB (1/64) per step on b2, then b1, then b3 (TINT_TAPS), on top of the look's G row. Luma-neutral by construction of
# the stage. Mid-grey (Y 128) shift per step is about 2.9 codes of chroma (|J_G| x 128/64).
TINT_TAPS = (2, 1, 3)


def _sm(b):
    m = b & 0x3F
    return -m if b & 0x80 else m


def _smb(m):
    m = max(-63, min(63, m))
    return (0x80 | -m) if m < 0 else m


def tint_word(w, t):
    """Row word + TINT t (-3..+3): +-1 on the TINT_TAPS bytes, sign-magnitude, clamped to +-63."""
    bs = {k: (w >> (8 * k)) & 0xFF for k in range(4)}
    for k in range(abs(t)):
        pos = TINT_TAPS[k]
        bs[pos] = _smb(_sm(bs[pos]) + (1 if t > 0 else -1))
    return sum(bs[k] << (8 * k) for k in range(4))


def matrix_model():
    """The colour-matrix model v2 (probe 3 refit: sign-magnitude, b0 = B and superposition confirmed; row-output clip),
    vendored as koma.vendor.colour.matrix_model (needs numpy; tests only, the build uses the frozen words)."""
    from ..vendor.colour import matrix_model as mm
    return mm


# GRD IV CMAT (colour engine) -> one chroma matrix. Reading of the tables (I, bwrecipes.md "GRD IV refit"): per table,
# 24 hue boundaries (u8, 256 = 360 deg) cut the hue circle into sectors, each with its own 2x2 [[a, b], [c, d]] on
# (Cb, Cr); hue = atan2(Cr, Cb). Evidence for that orientation: Standard's sectors 5..12 crowd into 112..155 deg,
# where skin sits in this convention (fine control where it matters). Sector-to-hue is U in the firmware README.
def grd4_sector_pairs(look_group):
    """[(light, sector, c0, ct)] for each of the 4 lights x 24 sectors of the look group (1 = B Bleach, 2 = C
    Positive): unit input at the look sector's centre hue; c0 = Standard's output (normalised to 1), ct = the look's,
    on the same scale (data/grd4/grd4-looks.json, derived from the CMAT tables). Needs numpy."""
    import numpy as np
    rows = _grd4()["sectors"][{1: "bleach", 2: "positive"}[look_group]]["rows"]
    return [(int(r[0]), int(r[1]), np.array(r[3:5]), np.array(r[5:7])) for r in rows]


def grd4_mean_gain(look_group):
    """Mean chroma gain of a GRD IV look group against Standard over 4 lights x 24 sectors (SATURATION default)."""
    import numpy as np
    return float(np.mean([np.linalg.norm(ct) for _, _, _, ct in grd4_sector_pairs(look_group)]))


GRD4_FIT = dict(ys=(96, 128, 160), radius=32.0, w_grey=200.0)    # grey weight: cast <= 1.6 codes at Y 160


def grd4_matrix(look_group, sat_step):
    """Best MatR/G/B for a GRD IV look group with SATURATION sat_step (0..6) in place: least squares, in model v2's
    chroma-domain units, of the look's per-sector change at the 4 x 24 sector centre hues (chroma radius 32 codes at
    Y 96/128/160) plus neutral greys at those Y (weight w_grey, target: no change); min-norm, clipped to +-63/64, then
    matrix_model.encode(). Returns ((MatR, MatG, MatB), report) with the residual per sector (8-bit codes, model
    prediction incl. clip vs target). Needs numpy."""
    import numpy as np
    MM = matrix_model()
    g = SAT_STEPS[sat_step]
    pairs = grd4_sector_pairs(look_group)
    rows, tgt, w, meta = [], [], [], []
    rad = GRD4_FIT["radius"]
    for y in GRD4_FIT["ys"]:
        for _, s, c0, ct in pairs:
            rows.append([y, *(rad * c0)]), tgt.append(rad * ct), w.append(1.0), meta.append(s)
        rows.append([y, 0.0, 0.0]), tgt.append([0.0, 0.0]), w.append(GRD4_FIT["w_grey"]), meta.append(-1)
    ycc, tgt, w, meta = np.array(rows, float), np.array(tgt, float), np.sqrt(np.array(w)), np.array(meta)
    xd = ycc[:, :1] + MM.LAMBDA * (MM.ycc_to_rgb(ycc) - ycc[:, :1])
    A = np.einsum("kr,ic->ikrc", MM.J, xd).reshape(-1, 9) * np.repeat(w, 2)[:, None]
    D = np.linalg.lstsq(A, ((tgt - g * ycc[:, 1:]) * w[:, None]).ravel(), rcond=None)[0].reshape(3, 3)
    regs = MM.encode(np.clip(D, -63 / 64, 63 / 64))
    regs.pop(MM.REG_UV)

    def cost(rg):
        return float((((MM.predict_ycc(ycc, rg, chroma_gain=g)[:, 1:] - tgt) * w[:, None]) ** 2).sum())

    best, better = cost(regs), True          # integer polish: +-1 LSB per byte while the weighted cost drops
    while better:
        better = False
        for r in MM.ROWS:
            for k in range(4):
                for dm in (1, -1):
                    m = max(-63, min(63, _sm((regs[r] >> (8 * k)) & 0xFF) + dm))
                    trial = dict(regs)
                    trial[r] = (regs[r] & ~(0xFF << (8 * k))) | (_smb(m) << (8 * k))
                    c = cost(trial)
                    if c < best - 1e-9:
                        regs, best, better = trial, c, True
    words = tuple(int(regs[r]) for r in MM.ROWS)
    pred = MM.predict_ycc(ycc, regs, chroma_gain=g)[:, 1:]
    gain_only = MM.predict_ycc(ycc, {}, chroma_gain=g)[:, 1:]
    rms = lambda e: float(np.sqrt((e ** 2).mean()))         # noqa: E731
    col = meta >= 0
    per = []
    for s in range(24):
        m = meta == s
        ang = lambda c: np.degrees(np.arctan2(c[:, 1], c[:, 0]))     # noqa: E731
        dh = (ang(pred[m]) - ang(tgt[m]) + 180) % 360 - 180
        per.append(dict(sector=s, hue=float(np.degrees(np.arctan2(*ycc[m][0, :0:-1])) % 360),
                        target_gain=float(np.linalg.norm(tgt[m], axis=1).mean() / rad),
                        model_gain=float(np.linalg.norm(pred[m], axis=1).mean() / rad),
                        hue_err=float(dh.mean()), rms=rms(pred[m] - tgt[m])))
    rep = dict(rms_change=rms(ycc[col, 1:] - tgt[col]), rms_gain_only=rms(gain_only[col] - tgt[col]),
               rms_residual=rms(pred[col] - tgt[col]), grey_cast=float(np.abs(pred[~col]).max()), sectors=per)
    return words, rep


def R(name, base, intent, shoot, con=0, key=0, shd=0, hil=0, filt="OFF", sharp=0, tone="OFF", dens=3):
    p = [B[base], con + 4, key + 4, shd + 4, hil + 4, F[filt], T[tone], dens - 1, sharp + 2]
    return dict(name=name, params=p, type=TYPE_BW, intent=intent, shoot=shoot)


def S(name, base, intent, shoot, con=0, key=0, shd=0, hil=0, colour="RED", rng="NARROW", sat=0, sharp=0):
    p = [B[base], con + 4, key + 4, shd + 4, hil + 4, SEL_COLOURS.index(colour), SEL_RANGES.index(rng), sat + 3,
         sharp + 2]
    return dict(name=name, params=p, type=TYPE_SEL, intent=intent, shoot=shoot)


def C(name, look, src, base, intent, shoot, con=0, key=0, shd=0, hil=0, sat=0, warm=0, tint=0, sharp=0):
    p = [B[base], con + 4, key + 4, shd + 4, hil + 4, sat + 3, warm + 3, tint + 3, sharp + 2]
    return dict(name=name, params=p, type=TYPE_COLOUR | (look << 4), look=look, src=src, intent=intent, shoot=shoot)


# List order = IMG SET / ADJ / KOMA RECIPE order after the 7 stock entries: the 10 B&W looks (owner, 2026-11-06),
# SELECT (a B&W look with one hue kept), the 6 colour looks, CUSTOM 1..3. B&W-family first and colour after keeps the
# ADJ walk one block per base (3/6, then 1) and leaves the B&W ids 1..10 of the v4 store where they were.
CURATED = [
    R("MONO", "R3", "the owner's pinned R3 curve: deeper mids, earlier highlight roll-off; no filter",
      "your usual street frames", sharp=1),
    R("SHINJUKU", "HI-CONT", "inspired by Daido Moriyama's GR work: hard contrast, crushed blacks, bright clipped "
      "whites, red filter, crisp", "night streets, signs, hard sun", con=2, shd=-2, hil=1, filt="RED", sharp=2),
    R("HICON B&W", "HI-CONT", "GR Hi-Contrast B&W: bold, near-black shadows, bright whites", "hard sun, shadows",
      sharp=2),
    R("HARD MONO", "HARD", "GR III Hard Monotone: very high contrast, faintly cool", "graphic shapes", con=2,
      sharp=1, tone="BLUE", dens=1),
    R("SOFT MONO", "SOFT MONO", "GR III Soft Monotone: low contrast, lifted feel", "overcast, portraits", sharp=-1),
    R("FILM 400", "FILM 400", "classic 400 film with a yellow filter: punchy mids, open highlights",
      "people outdoors", con=1, filt="YELLOW", sharp=1),
    R("FINE 100", "FINE 100", "fine-grain 100 film: soft shadows, crisp mid-to-highlight", "detail in daylight"),
    R("DENSE", "SOFT MONO", "dedicated mono sensor look: long, smooth, dense scale (darker, firmer than SOFT MONO)",
      "full tonal range scenes", con=1, key=-1, sharp=1),
    R("NOIR", "HI-CONT", "film noir: low key, red filter, deep blacks", "night light, single sources",
      con=2, key=-2, filt="RED", sharp=1),
    R("MATTE", "STANDARD", "matte: flat contrast, lifted shadows, soft whites", "soft light, faded look",
      con=-3, shd=3, hil=-2),
    S("SELECT", "R3", "selective colour: mono (R3 curve) except one hue band, which keeps its colour",
      "a red sign, a yellow cab, one coloured coat in a grey street", sharp=1),
    # colour: POSITIVE and BLEACH are fitted to the GR DIGITAL IV firmware (v4.1, src "grd4:..."; bwrecipes.md "GRD IV
    # refit"): POSITIVE = curve C5 as STANDARD + con/key/shd/hil (2.1 levels rms), SATURATION from group C's mean
    # sector gain (0.82 -> x0.85), matrix = grd4_matrix(group C); BLEACH = base BLEACH (curve B5), SATURATION from
    # group B's x0.43 (floor x0.55), WARMTH from the Normal WB triple (teal, past COOL 3), no matrix. The other four
    # are estimates: PROPOSAL section 2 values (SLIDE/NEG bases -> STANDARD, see BASES; RETRO's FADE base -> STANDARD
    # with SHADOW +4), matrix from `src` (tier B). v4.2 (looks lane): checked against published sources (REFS); NEGATIVE
    # HIGHLIGHT -1 -> 0 and WARMTH COOL 1 -> 0 (GR III defaults: highlight contrast 0, no WB bias; Ricoh: "faded"),
    # WARM NEG WARMTH 2 -> 3 (Portra recipe WB R+2 / B-5). RETRO and SLIDE 50 unchanged. GRD IV has no Retro look.
    C("POSITIVE", 0, "grd4:positive-film", "STANDARD", "GR Positive Film (GRD IV curve + colour): key up, deep "
      "shadows, warm hues kept, cool hues muted", "everyday colour, warm light", con=2, key=2, shd=-3, hil=-3,
      sat=-1),
    C("NEGATIVE", 1, "gr-negative-film", "STANDARD", "GR III Negative Film: soft, faded, lifted shadows, muted "
      "reds and blues, bluish greens", "people, soft light", con=-1, key=1, shd=2, sat=-1),
    C("BLEACH", 2, "grd4:bleach-bypass", "BLEACH", "GR Bleach Bypass (GRD IV curve + colour): bright crushed "
      "mids, low saturation, teal", "metal, concrete, hard light", sat=-3, warm=-3, sharp=1),
    C("RETRO", 3, "gr-retro", "STANDARD", "GR Retro: warm, faded, low contrast, sepia lean", "old streets, interiors",
      con=-2, key=1, shd=4, hil=-2, sat=-1, warm=2),
    C("WARM NEG", 4, "portra-400-like", "STANDARD", "warm portrait negative: soft contrast, warm, faint magenta",
      "skin, golden hour", con=-2, key=2, shd=2, hil=-3, warm=3, tint=-1),
    C("SLIDE 50", 5, "fuji-velvia", "STANDARD", "50-speed slide film: deep contrast, very saturated, slightly warm",
      "landscape, flowers, colour in sun", con=3, shd=-2, hil=-1, sat=3, warm=1),
]
NCUR = len(CURATED)
# Published sources behind the estimate looks (bwrecipes.md "Koma 0.3 looks"); none gives a measured curve.
REFS = {
    "NEGATIVE": ["https://www.grblog.jp/en/article/7151/",            # GR III Negative Film defaults + hue notes
                 "https://www.grblog.jp/en/article/6778/"],           # Ricoh: soft, gentle, faded
    "RETRO": ["https://grblog.jp/en/article/3517",                     # Ricoh: Retro nostalgic vs Cross otherworldly
              "https://red-town-dhs3.squarespace.com/blog/2024/3/8/testing-every-ricohgriii-jpeg-recipe"],
    "WARM NEG": ["https://fujixweekly.com/2018/05/30/my-fujifilm-x100f-kodak-portra-400-film-simulation-recipe/"],
    "SLIDE 50": ["https://fujixweekly.com/2017/10/01/my-fujifilm-x100f-velvia-film-simulation-recipe/"],
}
NBW = sum(r["type"] == TYPE_BW for r in CURATED)
CLOOKS = [r for r in CURATED if r["type"] & 3 == TYPE_COLOUR]
NCOL = len(CLOOKS)
# (MatR, MatG, MatB) per colour look, frozen (no numpy at build time). POSITIVE = grd4_matrix(2, its SATURATION)
# (test_bwrecipes re-derives; chroma rms over the GRD IV sectors, 8-bit codes at radius 32: change 6.62 -> SATURATION
# alone 5.09 -> + matrix 3.75, grey cast <= 1.6); BLEACH none (GRD IV: uniform x0.43, no rotation: SATURATION only).
# The rest = derive_matrix(tier-B src).
LOOK_MATRIX = [(0x01818101, 0x02828202, 0x84030383),     # POSITIVE  GRD IV group C: 6.62 -> 3.75
               (0x82828306, 0x02828202, 0x0005058A),     # NEGATIVE 10.12 -> 4.89
               (0x00000000, 0x00000000, 0x00000000),     # BLEACH   GRD IV group B: no matrix (stock rows)
               (0x88020204, 0x04838303, 0x05010188),     # RETRO     9.67 -> 3.60
               (0x02828200, 0x81000002, 0x82020383),     # WARM NEG  8.53 -> 6.21
               (0x82818105, 0x01000082, 0x01010184)]     # SLIDE 50  7.88 -> 7.60
NCUSTOM = 3
CUSTOM_SRC = 1         # CUSTOM n start as (and RESET to) a copy of look 1, MONO
RECIPES = CURATED + [dict(name=f"CUSTOM {i + 1}", params=list(CURATED[CUSTOM_SRC - 1]["params"]),
                          type=CURATED[CUSTOM_SRC - 1]["type"], intent="owner's own recipe (starts as MONO)",
                          shoot="") for i in range(NCUSTOM)]
N = len(RECIPES)
LIMITS = {TYPE_BW: [NB_BW, 9, 9, 9, 9, len(FILTERS), len(TONES), len(DENS), len(SHARP)],
          TYPE_COLOUR: [NB_BW, 9, 9, 9, 9, 7, 7, 7, len(SHARP)],
          TYPE_SEL: [NB_BW, 9, 9, 9, 9, len(SEL_COLOURS), len(SEL_RANGES), 7, len(SHARP)]}


def imgset_of(p, kind=TYPE_BW):
    if kind & 3:
        return 1
    return 6 if p[P_TONE] else 3


def colour_regs(p, kind, cp_wb=(185, 64, 172)):
    """Host model of the colour / SELECT writes for params p: {register: word} (both develop passes)."""
    t = kind & 3
    out = {0x12018004: chroma_word(p[P_SAT] if t == TYPE_COLOUR else p[P_SSAT])}
    if t == TYPE_COLOUR:
        mr, mg, mb = LOOK_MATRIX[kind >> 4]
        out.update({0x12018104: mr, 0x12018108: tint_word(mg, p[P_TINT] - 3), 0x1201810C: mb})
        wb = wb_words(cp_wb, p[P_WARM])
        if wb:
            out.update({0x12018110: wb[0], 0x12018114: wb[1]})
    return out


# ------------------------------------------------------------------ KOMA tab rows (bytes in the core range)
# KOMA tab: RECIPE page (group "recipe") + the EDIT RECIPE sub-page (group "recipe-edit", edits the active recipe;
# menu5 lists them in row order: CURVE..SHARPNESS, GRAIN, VIGNETTE (fx builds only), SAVE TO CUSTOM, RESET). Rows p5..p7 change
# label and options with the active recipe's type (`variants`: menu5 picks alt[type] from the selector byte when the
# menu loads). The declared label/options are the colour ones: 7 options, the longest list, so the core's
# out-of-range repair at boot never clips a valid value of any type.
_VAR = lambda b, c, s: dict(sel=SELB, alts=[b, c, s])     # noqa: E731
ROWS = [
    dict(id="rcp_active", label="RECIPE", options=["OFF"] + [r["name"] for r in RECIPES], byte=ACT, default=0,
         group="recipe"),
    dict(id="rp_curve", label="CURVE", options=[b[0] for b in BASES[:NB_BW]], byte=EB + P_CURVE, default=0,
         group="recipe-edit"),
    dict(id="rp_contrast", label="CONTRAST", options=SIGNED, byte=EB + P_CON, default=4, group="recipe-edit"),
    dict(id="rp_key", label="KEY", options=SIGNED, byte=EB + P_KEY, default=4, group="recipe-edit"),
    dict(id="rp_shadow", label="SHADOW", options=SIGNED, byte=EB + P_SHD, default=4, group="recipe-edit"),
    dict(id="rp_hilite", label="HIGHLIGHT", options=SIGNED, byte=EB + P_HIL, default=4, group="recipe-edit"),
    dict(id="rp_p5", label="SATURATION", options=STEP7, byte=EB + 5, default=3, group="recipe-edit",
         variants=_VAR(("FILTER EFF.", FILTERS), ("SATURATION", STEP7), ("COLOUR", SEL_COLOURS))),
    dict(id="rp_p6", label="WARMTH", options=WARMS, byte=EB + 6, default=3, group="recipe-edit",
         variants=_VAR(("TONING", TONES), ("WARMTH", WARMS), ("RANGE", SEL_RANGES))),
    dict(id="rp_p7", label="TINT", options=TINTS, byte=EB + 7, default=3, group="recipe-edit",
         variants=_VAR(("TONE LEVEL", DENS), ("TINT", TINTS), ("SATURATION", STEP7))),
    dict(id="rp_sharp", label="SHARPNESS", options=SHARP, byte=EB + P_SHARP, default=2, group="recipe-edit"),
    dict(id="rp_save", label="SAVE TO CUSTOM", options=["OFF"] + [f"CUSTOM {i + 1}" for i in range(NCUSTOM)],
         byte=SAVE, default=0, group="recipe-edit"),
    dict(id="rp_reset", label="RESET", options=["NO", "YES"], byte=RST, default=0, group="recipe-edit"),
    # GRAIN / VIGNETTE list after SHARPNESS, before SAVE TO CUSTOM (order), and only in builds with fx (needs)
    dict(id="rp_grain", label="GRAIN", options=GRAINS, byte=FXG, default=0, group="recipe-edit", order=EB + P_SHARP,
         needs="fx"),
    dict(id="rp_vig", label="VIGNETTE", options=VIGS, byte=FXV, default=0, group="recipe-edit", order=EB + P_SHARP,
         needs="fx"),
]

# ------------------------------------------------------------------ stock sites
SITE_DEV1, SITE_DEV2, MONO = 0x96436, 0xAFD3E, 0x8FF0E
SHARP_GET = 0x91268
SITES_SHARP = [0x91A8C, 0x4165A]                       # develop sharpening setup, EXIF sharpness
LOAD_AUTO, LOAD_PAM, COMMIT_AUTO, COMMIT_PAM = 0x5AAF0, 0x5AEB4, 0x5B4E4, 0x5A260
SITES_LOAD = [(0x5B394, LOAD_AUTO, 0), (0x5B446, LOAD_AUTO, 0), (0x5B3D4, LOAD_PAM, 1), (0x5B486, LOAD_PAM, 1)]
SITES_COMMIT = [(0x58882, COMMIT_AUTO, 0), (0x5A0A8, COMMIT_PAM, 1), (0x6DBB6, COMMIT_PAM, 1)]
IMG_OPTS, IMG_ROWS = 0x1BF434, [0x1C1F18, 0x1C20EC, 0x1C2188]     # AUTO, P/A, M rows (label, opts, count 7, ...)
NSTOCK = 7
NOPT = NSTOCK + N
CC2_UP, CC2_CACHE = 0x16D31C, 0x1F4DB3
# Unity WB after the develop hooks: 0x96350 / 0xafd20 call the Thumb veneer
# 0x174ca4 (bx pc; b 0x9b7e0) for readout format cp+0x40 != 3 (pass 1: also not 0, not WB/colour bracket): 0x9b7e0
# sets cp+0x268/26a/26c = 100 and 0x12018110/114 to unity (0x40). f = 1 on continuous frames 2.. and AE-BKT frames
# 2..3 (0x9f854); single shots are f = 3 and never take it. WB mode, flash, ISO play no part.
WB_UNITY = 0x174CA4
SITES_WBU = [0x964B8, 0xAFDDC]
TE_SET, TE_SHADOW = 0x171D80, 0x178CAC
# wlog (resident): 'KRCPLOG1', +8 colour-write count (u8), +9 last pass, +10 last kind, +11 unity-WB re-applies (u8,
# hook_wbu), +12 chroma, +16/20/24 MatR/G/B,
# +28/32 WB words (0 when WARMTH is 0). Read by smoke_checks and the unit tests; costs 36 B.
WL_MAGIC = b"KRCPLOG1"
WL_COUNT, WL_PASS, WL_KIND, WL_UNITY, WL_CHROMA, WL_MAT, WL_WB, WL_LEN = 8, 9, 10, 11, 12, 16, 28, 36

ENGINE = f"""
.equ ACT, 0x{ACT:x}
.equ EB, 0x{EB:x}
.equ RST, 0x{RST:x}
.equ SAVE, 0x{SAVE:x}
.equ NCUR, {NCUR}
.equ NCUSTOM, {NCUSTOM}
.equ CUSTOM_SRC, {CUSTOM_SRC}
.equ DEFLEN, {DEFLEN}
.equ MAXTYPE, {MAXTYPE}
.equ NCOL, {NCOL}
.equ MAGIC, 0x{MAGIC.hex()}
.equ V4MAGIC, 0x{V4_MAGIC.hex()}
.equ V5MAGIC, 0x{V5_MAGIC.hex()}
.equ FXT, 0x{REC + N * RECLEN:x}
.equ FXG, 0x{FXG:x}
.equ ST, 0x{ST:x}
.equ REC, 0x{REC:x}
.equ N, {N}
.equ NOPT, {NOPT}
.equ VALID, 0x{VALID:x}
.equ LO, {LO}
.equ PIVOT, {PIVOT}
.equ IMGSET, 0x1b030
.equ WB0, {WB0}
.equ WK, {WK}

@ ================================================================ store / sync
@ store_check: r0 = 1 if 'RCP6' v6 with N records, else 0; v5ok: the same for 'RCP5' v5 (Koma 0.2). Leaf, r0-r3.
v5ok:
    ldr r1, =V5MAGIC
    movs r3, #5
    b 1f
store_check:
    ldr r1, =MAGIC
    movs r3, #{STORE_VER}
1:  ldr r2, =ST
    ldr r0, [r2, #0]
    cmp r0, r1
    bne 9f
    ldrb r0, [r2, #4]
    cmp r0, r3
    bne 9f
    ldrb r0, [r2, #7]
    cmp r0, #N
    bne 9f
    movs r0, #1
    bx lr
9:  movs r0, #0
    bx lr
.ltorg

@ migrate4: an 'RCP4' v4 store with {V4_N} records (Koma 0.2 B&W: ids 1..{V4_NBW} = the same B&W looks, {V4_NBW + 1}..{V4_N} =
@ CUSTOM 1..3) -> CUSTOM records move to ids {N - NCUSTOM + 1}..{N}, the new ids {V4_NBW + 1}..{N - NCUSTOM} start empty, an
@ active CUSTOM id moves with them. Returns r0 = 1 if migrated, else 0 (nothing written). Leaf, r0-r3.
migrate4:
    ldr r2, =ST
    ldr r0, [r2, #0]
    ldr r1, =V4MAGIC
    cmp r0, r1
    bne 9f
    ldrb r0, [r2, #4]
    cmp r0, #4
    bne 9f
    ldrb r0, [r2, #7]
    cmp r0, #{V4_N}
    bne 9f
    ldr r2, =REC + {V4_NBW * RECLEN}                @ copy the 3 CUSTOM records (36 B) to their v5 slots
    movs r1, #0
1:  ldrb r0, [r2, r1]
    adds r1, #{(N - NCUSTOM - V4_NBW) * RECLEN}
    strb r0, [r2, r1]
    subs r1, #{(N - NCUSTOM - V4_NBW) * RECLEN - 1}
    cmp r1, #{NCUSTOM * RECLEN}
    blo 1b
    movs r0, #0                                 @ the new looks' slots (old CUSTOM bytes): empty
    movs r1, #0
2:  strb r0, [r2, r1]
    adds r1, #1
    cmp r1, #{(N - NCUSTOM - V4_NBW) * RECLEN}
    blo 2b
    ldr r2, =ACT
    ldrb r0, [r2]
    cmp r0, #{V4_NBW}
    bls 3f
    cmp r0, #{V4_N}
    bhi 3f
    adds r0, #{N - V4_N}
    strb r0, [r2]
3:  movs r0, #1
    bx lr
9:  movs r0, #0
    bx lr
.ltorg

@ store_fix: invalid store -> a v5 store keeps its records, a v4 store is migrated (records kept), anything else (first
@ boot, 0.1's RCP2 v3, garbage) has every record set to its default (valid byte 0). Then, in this order: FX table OFF;
@ active id, SAVE TO CUSTOM, RESET, GRAIN, VIGNETTE rows 0; owner / last / FX owner 0xff, published bytes 0; the header
@ (magic last).
store_fix:
    push {{r4, lr}}
    bl store_check
    cmp r0, #0
    bne 8f
    bl v5ok
    cmp r0, #0
    bne 7f
    bl migrate4
    cmp r0, #0
    bne 7f
    ldr r2, =REC
    movs r0, #0
1:  strb r0, [r2, #0]
    adds r2, #1
    ldr r3, =FXT
    cmp r2, r3
    blo 1b
7:  ldr r2, =FXT
    movs r0, #0
    movs r1, #N
2:  subs r1, #1
    strb r0, [r2, r1]
    bne 2b
    ldr r2, =ACT
    strb r0, [r2]
    ldr r2, =SAVE
    strb r0, [r2]
    ldr r2, =RST
    strb r0, [r2]
    ldr r2, =ST
    str r0, [r2, #8]
    str r0, [r2, #12]
    movs r0, #0xff
    strb r0, [r2, #5]
    strb r0, [r2, #6]
    strb r0, [r2, #13]
    movs r0, #N
    strb r0, [r2, #7]
    movs r0, #{STORE_VER}
    strb r0, [r2, #4]
    ldr r0, =MAGIC
    str r0, [r2, #0]
8:  pop {{r4}}
    pop {{r3}}
    bx r3
.ltorg

@ pok: r0 = params pointer, r1 = type (0..MAXTYPE) -> r0 = 1 if every field is below that type's limit.
pok:
    push {{r4, lr}}
    movs r2, #{NPARAM}
    muls r1, r2, r1
    ldr r2, =limits
    adds r1, r1, r2
    movs r2, #0
1:  ldrb r3, [r0, r2]
    ldrb r4, [r1, r2]
    cmp r3, r4
    bhs 9f
    adds r2, #1
    cmp r2, #{NPARAM}
    blo 1b
    movs r0, #1
    b 8f
9:  movs r0, #0
8:  pop {{r4}}
    pop {{r3}}
    bx r3
.ltorg

@ recaddr: r0 = id (1..N) -> r0 = record address. Leaf, r1.
recaddr:
    subs r0, #1
    movs r1, #{RECLEN}
    muls r0, r1, r0
    ldr r1, =REC
    adds r0, r0, r1
    bx lr
.ltorg

@ eff: r0 = id 1..N, r1 = dst (9 bytes): the record if valid (store ok, valid byte, xor, kind ok: type <= MAXTYPE and
@ colour look < NCOL, params in that type's ranges), else the default (CUSTOM n: look CUSTOM_SRC's). Returns r0 = the
@ kind (record byte 9 / default-row byte 9).
eff:
    push {{r4, r5, r6, lr}}
    movs r4, r0
    movs r5, r1
    bl store_check
    cmp r0, #0
    beq 5f
    movs r0, r4
    bl recaddr
    movs r6, r0
    ldrb r0, [r6, #10]
    cmp r0, #VALID
    bne 5f
    ldrb r0, [r6, #9]
    lsrs r1, r0, #4
    cmp r1, #NCOL - 1
    bhi 5f
    lsls r1, r0, #28
    lsrs r1, r1, #28
    cmp r1, #MAXTYPE
    bhi 5f
    movs r1, #0x5a
    movs r2, #0
1:  ldrb r3, [r6, r2]
    eors r1, r3
    adds r2, #1
    cmp r2, #10
    blo 1b
    ldrb r3, [r6, #11]
    cmp r1, r3
    bne 5f
    ldrb r1, [r6, #9]
    movs r0, #3
    ands r1, r0
    movs r0, r6
    bl pok
    cmp r0, #0
    beq 5f
    movs r1, r6
    b 6f
5:  movs r0, r4
    cmp r0, #NCUR
    bls 51f
    movs r0, #CUSTOM_SRC
51: subs r0, #1
    movs r1, #DEFLEN
    muls r0, r1, r0
    ldr r1, =deftab
    adds r1, r1, r0
6:  movs r2, #0
7:  ldrb r3, [r1, r2]
    strb r3, [r5, r2]
    adds r2, #1
    cmp r2, #{NPARAM}
    blo 7b
    ldrb r0, [r1, #{NPARAM}]         @ kind
    pop {{r4, r5, r6}}
    pop {{r3}}
    bx r3
.ltorg

@ wrec: r0 = id, r1 = params (9 bytes), r2 = kind -> record: p0..p8, kind, VALID, xor(bytes 0..9) ^ 0x5a.
wrec:
    push {{r4, r5, r6, lr}}
    movs r4, r1
    movs r5, r2
    bl recaddr
    movs r6, #0x5a
    movs r2, #0
1:  ldrb r3, [r4, r2]
    strb r3, [r0, r2]
    eors r6, r3
    adds r2, #1
    cmp r2, #{NPARAM}
    blo 1b
    strb r5, [r0, #{NPARAM}]
    eors r6, r5
    movs r3, #VALID
    strb r3, [r0, #10]
    strb r6, [r0, #11]
    pop {{r4, r5, r6}}
    pop {{r3}}
    bx r3
.ltorg

@ imgset_p: r0 = params, r1 = type -> r0 = the base IMG SET: colour / SELECT 1 (NORMAL); B&W 3, or 6 (B&W(TE)) when
@ toned. Leaf.
imgset_p:
    cmp r1, #0
    beq 2f
    movs r0, #1
    bx lr
2:  ldrb r0, [r0, #{P_TONE}]
    cmp r0, #0
    beq 1f
    movs r0, #6
    bx lr
1:  movs r0, #3
    bx lr
.ltorg

@ curof: r0 = IMG SET value -> r0 = the stock menu / ADJ cursor showing it (0x1bd452 inverse; > 6 -> NORMAL). Leaf.
curof:
    cmp r0, #6
    bhi 1f
    ldr r1, =curmap
    ldrb r0, [r1, r0]
    bx lr
1:  movs r0, #2
    bx lr
.ltorg

@ basecur: r0 = the stock cursor of the active recipe's base (edit rows + published type; call after sync).
basecur:
    push {{r4, lr}}
    ldr r1, =ST
    ldrb r1, [r1, #8]
    ldr r0, =EB
    bl imgset_p
    bl curof
    pop {{r4}}
    pop {{r3}}
    bx r3
.ltorg

@ sync: reconcile the active id (0x1b8c0), the edit buffer (KOMA rows) and the record store, then publish. Called from
@ on_change, the IMG SET load/commit hooks, the ADJ hooks and every shot. Writes 0x1b030 only with 1, 3 or 6, and
@ only when the active recipe changes or a toning edit / RESET moves the active recipe to another base.
sync:
    push {{r4, r5, r6, r7, lr}}
    bl store_fix
    ldr r5, =ST
    ldr r1, =ACT
    ldrb r4, [r1]
    cmp r4, #N
    bls 1f
    movs r4, #0
    strb r4, [r1]
1:  ldrb r0, [r5, #6]                @ last applied id
    cmp r0, r4
    beq 3f
    strb r4, [r5, #6]                @ active changed: load its params into the edit rows, set its base IMG SET
    strb r4, [r5, #5]
    ldr r1, =RST
    movs r0, #0
    strb r0, [r1]
    cmp r4, #0
    beq 9f
    movs r0, r4
    ldr r1, =EB
    bl eff
    movs r1, #3
    ands r1, r0
    ldr r0, =EB
    bl imgset_p
    ldr r1, =IMGSET
    strb r0, [r1]
    b 9f
3:  ldrb r0, [r5, #5]                @ owner of the edit rows
    cmp r0, r4
    bne 4f
    cmp r4, #0
    bne 5f
4:  strb r4, [r5, #5]                @ adopt: rows <- active recipe
    cmp r4, #0
    beq 9f
    movs r0, r4
    ldr r1, =EB
    bl eff
    b 9f
5:  movs r0, r4                      @ owner == active != 0: did the rows change?
    ldr r1, =tmp
    bl eff                           @ tmp = current effective params
    movs r7, r0                      @ r7 = its kind (kept by a record write)
    ldr r1, =RST
    ldrb r0, [r1]
    cmp r0, #0
    beq 6f
    movs r0, #0                      @ RESET: drop the record, rows <- default
    strb r0, [r1]
    movs r0, r4
    bl recaddr
    movs r1, #0
    strb r1, [r0, #10]
    ldr r0, =FXT - 1                 @ GRAIN / VIGNETTE back to OFF, rows reload (FX owner 0xff)
    strb r1, [r0, r4]
    movs r1, #0xff
    strb r1, [r5, #13]
    movs r0, r4
    ldr r1, =EB
    bl eff
    movs r6, r0                      @ r6 = the default's kind (a CUSTOM slot goes back to MONO, B&W)
    b 8f
6:  ldr r0, =EB
    movs r1, #3
    ands r1, r7
    bl pok
    cmp r0, #0
    bne 7f
    ldr r0, =EB                      @ rows out of range: put the effective params back
    ldr r1, =tmp
    movs r2, #0
61: ldrb r3, [r1, r2]
    strb r3, [r0, r2]
    adds r2, #1
    cmp r2, #{NPARAM}
    blo 61b
    b 9f
7:  ldr r0, =EB                      @ compare rows with tmp
    ldr r1, =tmp
    movs r2, #0
71: ldrb r3, [r0, r2]
    ldrb r6, [r1, r2]
    cmp r3, r6
    bne 72f
    adds r2, #1
    cmp r2, #{NPARAM}
    blo 71b
    b 9f
72: movs r0, r4                      @ write the record: p0..p8, kind, VALID, xor
    ldr r1, =EB
    movs r2, r7
    bl wrec
    movs r6, r7
8:  ldr r0, =tmp                     @ base moved (toning on/off, RESET) and IMG SET still holds the old base: follow
    movs r1, #3
    ands r1, r7
    bl imgset_p
    movs r7, r0                      @ r7 = old base
    ldr r0, =EB
    movs r1, #3
    ands r1, r6
    bl imgset_p
    cmp r0, r7
    beq 9f
    ldr r1, =IMGSET
    ldrb r2, [r1]
    cmp r2, r7
    bne 9f
    strb r0, [r1]
9:  movs r0, r4
    bl fxsync
    ldr r1, =SAVE                    @ SAVE TO CUSTOM k: the rows (= the active recipe's params now) and its kind go
    ldrb r6, [r1]                    @ into CUSTOM k's record; the row goes back to OFF; the active recipe stays
    cmp r6, #0
    beq 10f
    movs r0, #0
    strb r0, [r1]
    cmp r6, #NCUSTOM
    bhi 10f
    cmp r4, #0
    beq 10f
    movs r0, r4
    ldr r1, =tmp
    bl eff
    movs r2, r0
    movs r0, r6
    adds r0, #NCUR
    ldr r1, =EB
    bl wrec
    ldr r1, =FXT - 1                 @ and its GRAIN / VIGNETTE byte
    ldrb r0, [r1, r4]
    adds r1, r1, r6
    strb r0, [r1, #NCUR]
10: bl publish
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
.ltorg

@ fxget: r0 = id 1..N -> r0 = its FX value (grain | vignette << 2), 0 unless the byte carries FXMARK; r1 = the byte's
@ address. Leaf, r0-r2.
fxget:
    ldr r1, =FXT - 1
    adds r1, r1, r0
    ldrb r0, [r1]
    lsrs r2, r0, #4
    cmp r2, #{FXMARK >> 4}
    beq 1f
    movs r0, #0
1:  movs r2, #15
    ands r0, r2
    bx lr

@ fxsync: r0 = active id (0..N). GRAIN / VIGNETTE rows <-> the recipe's FX byte: rows owned by another id (FX owner,
@ header +13) or out of range are loaded from the byte; rows of the active recipe that differ are written to it.
fxsync:
    push {{r4, lr}}
    movs r4, r0
    beq 9f
    bl fxget                         @ r0 = stored value, r1 = &byte
    ldr r3, =ST
    ldrb r2, [r3, #13]
    strb r4, [r3, #13]
    ldr r3, =FXG
    cmp r2, r4
    bne 5f
    ldrb r2, [r3, #1]
    lsls r2, r2, #2
    ldrb r4, [r3]
    orrs r2, r4
    cmp r2, #15
    bhi 5f                           @ a row out of range (> 3)
    lsrs r4, r4, #2
    bne 5f
    cmp r2, r0
    beq 9f
    adds r2, #{FXMARK}
    strb r2, [r1]
    b 9f
5:  movs r2, #3
    ands r2, r0
    strb r2, [r3]
    lsrs r0, r0, #2
    strb r0, [r3, #1]
9:  pop {{r4}}
    pop {{r3}}
    bx r3
.ltorg

@ publish: store header +8 = type of the active recipe (menu5's editor labels), +9/+10 = SELECT colour (1..6) and
@ range when a SELECT recipe applies to the next shot, else 0 / 0 (selcolour reads them at the master-YUV step);
@ +11 / +12 = GRAIN / VIGNETTE of the recipe applying to the next shot (any type), else 0 / 0 (fx reads them there).
publish:
    push {{r4, r5, lr}}
    ldr r4, =ST
    movs r5, #0
    ldr r1, =ACT
    ldrb r0, [r1]
    cmp r0, #0
    beq 1f
    cmp r0, #N
    bhi 1f
    ldr r1, =tmp
    bl eff
    movs r5, #3
    ands r5, r0
1:  strb r5, [r4, #8]
    bl getcur
    movs r1, #0
    movs r2, #0
    cmp r0, #0
    beq 2f
    bl fxget
    movs r1, #3
    ands r1, r0
    strb r1, [r4, #11]
    lsrs r0, r0, #2
    strb r0, [r4, #12]
    movs r1, #0
    movs r2, #0
    ldr r3, =ckind
    ldrb r3, [r3]
    movs r0, #3
    ands r3, r0
    cmp r3, #{TYPE_SEL}
    bne 3f
    ldr r3, =cur
    ldrb r1, [r3, #{P_SCOL}]
    adds r1, #1
    ldrb r2, [r3, #{P_SRNG}]
    b 3f
2:  strb r1, [r4, #11]                 @ no recipe applies: GRAIN / VIGNETTE OFF
    strb r1, [r4, #12]
3:  strb r1, [r4, #9]
    strb r2, [r4, #10]
    pop {{r4, r5}}
    pop {{r3}}
    bx r3
.ltorg

@ on_change (menu5 KOMA tab: r0 = value, r1 = byte address): just reconcile.
on_change:
    push {{r4, lr}}
    bl sync
    pop {{r4}}
    pop {{r3}}
    bx r3
.ltorg

@ getcur: r0 = active id if a recipe applies now (store ok or defaults, id valid, 0x1b030 == its base), else 0.
@ Fills cur (9 bytes), ckind (its kind) and prm (k, c, s, h as signed words). No writes outside the module.
getcur:
    push {{r4, lr}}
    ldr r1, =ACT
    ldrb r4, [r1]
    cmp r4, #0
    beq 8f
    cmp r4, #N
    bhi 8f
    movs r0, r4
    ldr r1, =cur
    bl eff
    ldr r1, =ckind
    strb r0, [r1]
    movs r1, #3
    ands r1, r0
    ldr r0, =cur
    bl imgset_p
    ldr r1, =IMGSET
    ldrb r1, [r1]
    cmp r0, r1
    bne 8f
    ldr r0, =cur
    ldr r1, =prm
    ldrb r2, [r0, #2]                @ key
    subs r2, #4
    str r2, [r1, #0]
    ldrb r2, [r0, #1]                @ contrast
    subs r2, #4
    str r2, [r1, #4]
    ldrb r2, [r0, #3]                @ shadow
    subs r2, #4
    str r2, [r1, #8]
    ldrb r2, [r0, #4]                @ highlight
    subs r2, #4
    str r2, [r1, #12]
    movs r0, r4
    b 9f
8:  movs r0, #0
9:  pop {{r4}}
    pop {{r3}}
    bx r3
.ltorg

@ ================================================================ tone curve generator + CC2 upload
@ fval: r0 = v (0..1023) -> r0 = F(v) clamped to LO..1023 (see bwrecipes.fval). Preserves r4-r7.
fval:
    cmp r0, #LO
    bhi 1f
    bx lr
1:  push {{r4, r5, r6, r7, lr}}
    ldr r6, =prmu                    @ upload_curve's private copy of prm (review 0.2.1 F2)
    movs r4, r0                      @ v
    movs r1, r0
    subs r1, #LO                     @ d = v - LO
    ldr r2, =1023
    subs r2, r2, r4                  @ e = HI - v
    movs r3, r1
    muls r3, r2, r3                  @ A = d * e
    ldr r5, [r6, #0]
    muls r5, r3, r5
    asrs r5, r5, #{KEY_SH}            @ t = (k * A) >> KEY_SH
    ldr r7, =PIVOT
    subs r7, r4, r7                  @ vp = v - PIVOT
    asrs r0, r3, #4
    muls r0, r7, r0
    ldr r3, [r6, #4]
    muls r0, r3, r0
    asrs r0, r0, #{CON_SH}
    adds r5, r5, r0                  @ t += (c * ((A >> 4) * vp)) >> CON_SH
    cmp r7, #0
    bge 2f
    negs r0, r7
    muls r0, r1, r0                  @ -vp * d
    ldr r3, [r6, #8]
    muls r0, r3, r0
    asrs r0, r0, #{SHD_SH}
    b 3f
2:  movs r0, r7
    muls r0, r2, r0                  @ vp * e
    ldr r3, [r6, #12]
    muls r0, r3, r0
    asrs r0, r0, #{HIL_SH}
3:  adds r5, r5, r0
    adds r0, r4, r5
    cmp r0, #LO
    bge 4f
    movs r0, #LO
4:  ldr r1, =1023
    cmp r0, r1
    ble 5f
    movs r0, r1
5:  pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
.ltorg

@ gen_chunk: write the next 256 entries of the stream into buf (state in gs: byte ptr, 2-bit slot, base value,
@ running max).
gen_chunk:
    push {{r4, r5, r6, r7, lr}}
    sub sp, #8
    ldr r0, =buf
    str r0, [sp, #0]
    movs r0, #0
    str r0, [sp, #4]
    ldr r3, =gs
    ldr r4, [r3, #0]
    ldr r5, [r3, #4]
    ldr r6, [r3, #8]
    ldr r7, [r3, #12]
1:  ldrb r0, [r4]
    lsls r1, r5, #1
    movs r2, #6
    subs r2, r2, r1
    lsrs r0, r2
    movs r1, #3
    ands r0, r1
    adds r6, r6, r0                  @ base value += 2-bit step
    adds r5, #1
    cmp r5, #4
    bne 2f
    movs r5, #0
    adds r4, #1
2:  movs r0, r6
    bl fval
    cmp r0, r7
    bhs 3f
    movs r0, r7                      @ running max: never decreasing
3:  movs r7, r0
    ldr r1, [sp, #0]
    strh r0, [r1]
    adds r1, #2
    str r1, [sp, #0]
    ldr r1, [sp, #4]
    adds r1, #1
    str r1, [sp, #4]
    lsrs r1, r1, #8
    beq 1b
    ldr r3, =gs
    str r4, [r3, #0]
    str r5, [r3, #4]
    str r6, [r3, #8]
    str r7, [r3, #12]
    add sp, #8
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
.ltorg

@ upload_curve: r0 = base id (prm already set). 16 x 0x16d31c(offset, 256, buf), then CC2 cache 0x1f4db3 := 0xff.
@ prm is copied to prmu first, so a getcur from another task (sharpness hook, menu) during the 16 chunks cannot mix
@ two recipes' k/c/s/h into one shot's curve (review 0.2.1 F2).
upload_curve:
    push {{r4, lr}}
    ldr r1, =prm
    ldr r2, =prmu
    movs r4, #12
2:  ldr r3, [r1, r4]
    str r3, [r2, r4]
    subs r4, #4
    bpl 2b
    lsls r0, r0, #10                 @ 1,024 bytes per base
    ldr r1, =bases
    adds r0, r0, r1
    ldr r3, =gs
    str r0, [r3, #0]
    movs r0, #0
    str r0, [r3, #4]
    str r0, [r3, #8]
    str r0, [r3, #12]
    movs r4, #0
1:  bl gen_chunk
    movs r0, r4
    movs r1, #1
    lsls r1, r1, #8
    ldr r2, =buf
    ldr r3, =0x{CC2_UP | 1:x}
    bl call_r3
    movs r0, #1
    lsls r0, r0, #8
    adds r4, r4, r0
    lsrs r0, r4, #12
    beq 1b
    ldr r0, =0x{CC2_CACHE:x}
    movs r1, #0xff
    strb r1, [r0]
    pop {{r4}}
    pop {{r3}}
    bx r3
.ltorg

@ ================================================================ shot hooks (wrap bl 0x8ff0e; r0 = cp+0x22, r7 = cp)
hook_dev1:
    push {{r4, r5, r6, r7, lr}}
    ldr r3, =next_dev1
    movs r6, #1
    b dev_common
hook_dev2:
    push {{r4, r5, r6, r7, lr}}
    ldr r3, =next_dev2
    movs r6, #2
dev_common:
    movs r4, r0                      @ mono flag
    movs r5, r7                      @ cp
    bl call_r3                       @ stock mono writer (or the next hook)
    ldr r0, =wbm
    movs r1, #0
    str r1, [r0, #0]                 @ no recipe WB multipliers yet this pass (hook_wbu re-applies only these)
    cmp r6, #1
    bne 1f
    bl sync
1:  bl getcur
    cmp r0, #0
    beq nofilter                     @ no recipe applies: stock shot
    ldr r0, =ckind
    ldrb r0, [r0]
    movs r1, #3
    ands r0, r1
    beq bwp
    bl yw_restore                    @ ---- colour / SELECT (base NORMAL): no B&W filter word
    cmp r4, #0
    bne curvep                       @ develop not in colour (never on base NORMAL): the curve only
    movs r0, r5
    movs r1, r6
    bl colour_regs
    b curvep
bwp:
    subs r4, #1
    cmp r4, #1
    bls 2f
    bl yw_restore                    @ develop says colour: no filter / toning, the curve still follows the recipe
    b curvep
2:  ldr r0, =cur                      @ ---- filter
    ldrb r0, [r0, #{P_FILT}]
    subs r0, #1
    bmi nofilt2
    lsls r0, r0, #4
    ldr r4, =ftab
    adds r4, r4, r0
    ldr r0, [r4, #0]
    ldr r1, =0x1201800c
    str r0, [r1, #0]
    ldr r1, =state
    movs r0, #1
    strb r0, [r1, #0]
    ldr r0, [r4, #8]
    cmp r0, #0
    beq tone                         @ no WB tilt
    movs r0, #0x13
    lsls r0, r0, #5
    adds r0, r5, r0                  @ cp+0x260
    adds r1, r4, #4                  @ the filter's R, G, B multipliers
    bl wb_write
    b tone
nofilt2:
    bl yw_restore
tone:                                @ ---- toning: (Cb, Cr) for tint/density, through the stock TE setter
    ldr r0, =cur
    ldrb r1, [r0, #{P_TONE}]
    subs r1, #1
    bmi curvep
    movs r2, #5
    muls r1, r2, r1
    ldrb r2, [r0, #{P_DENS}]
    adds r1, r1, r2
    lsls r1, r1, #1
    ldr r2, =ttab
    adds r2, r2, r1
    movs r0, #0
    ldrsb r1, [r2, r0]               @ Cb
    movs r0, #1
    ldrsb r2, [r2, r0]               @ Cr
    ldr r0, =0x3ff
    ands r1, r0
    ands r2, r0
    lsls r2, r2, #12
    orrs r1, r2
    ldr r0, =0x{TE_SHADOW:x}
    ldr r0, [r0]
    movs r2, #1
    lsls r2, r2, #24
    orrs r0, r2
    ldr r2, =0xffc00c00
    ands r0, r2
    orrs r0, r1
    sub sp, #8
    str r0, [sp, #0]
    mov r0, sp
    ldr r3, =0x{TE_SET | 1:x}
    bl call_r3
    add sp, #8
curvep:
    cmp r6, #1                       @ ---- curve: pass 1 only (pass 2 uploads no LUT)
    bne out
    ldr r0, =cur
    ldrb r0, [r0, #0]
    bl upload_curve
    b out
nofilter:
    bl yw_restore
out:
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
.ltorg

@ colour_regs: r0 = cp, r1 = develop pass (1/2); cur/ckind = the applying colour or SELECT recipe. Writes, after the
@ stock writers of this pass: chroma gain 0x12018004 (SATURATION: colour p5, SELECT p7); colour only: MatR/G/B
@ 0x12018104/108/10c (the look's ROM rows, G row with the TINT step), and when WARMTH != 0 the WB words 0x12018110/114
@ (R, B multipliers on the shot's AWB values cp+0x268/26c; G passes). Every value also goes to wlog (smoke / tests).
colour_regs:
    push {{r4, r5, r6, r7, lr}}
    movs r5, r0
    ldr r6, =wlog
    strb r1, [r6, #{WL_PASS}]
    ldrb r0, [r6, #{WL_COUNT}]
    adds r0, #1
    strb r0, [r6, #{WL_COUNT}]
    ldr r4, =cur
    ldr r0, =ckind
    ldrb r7, [r0]
    strb r7, [r6, #{WL_KIND}]
    lsls r1, r7, #30
    lsrs r1, r1, #30
    ldrb r0, [r4, #{P_SAT}]
    cmp r1, #{TYPE_COLOUR}
    beq 1f
    ldrb r0, [r4, #{P_SSAT}]
1:  lsls r0, r0, #2
    ldr r2, =chromatab
    ldr r0, [r2, r0]
    ldr r2, =0x12018004
    str r0, [r2, #0]
    str r0, [r6, #{WL_CHROMA}]
    movs r0, #0
    str r0, [r6, #{WL_WB}]
    str r0, [r6, #{WL_WB} + 4]
    cmp r1, #{TYPE_COLOUR}
    bne 9f
    lsrs r0, r7, #4                  @ ---- matrix: look row block (R, B, G for TINT 0..6)
    movs r1, #36
    muls r0, r1, r0
    ldr r1, =mtab
    adds r0, r0, r1
    ldrb r3, [r4, #{P_TINT}]
    lsls r3, r3, #2
    adds r3, r3, r0
    ldr r3, [r3, #8]
    ldr r1, [r0, #0]
    ldr r2, [r0, #4]
    ldr r0, =0x12018104
    str r1, [r0, #0]
    str r3, [r0, #4]
    str r2, [r0, #8]
    str r1, [r6, #{WL_MAT}]
    str r3, [r6, #{WL_MAT} + 4]
    str r2, [r6, #{WL_MAT} + 8]
    ldrb r0, [r4, #{P_WARM}]         @ ---- WARMTH: s = option - 3; R x (WB0 + WK s), B x (WB0 - WK s), G passes
    subs r0, #3
    beq 9f
    ldr r1, =WK
    muls r1, r0, r1
    ldr r2, =WB0
    ldr r3, =wbm
    adds r0, r2, r1
    str r0, [r3, #0]                 @ R x (WB0 + WK s)
    subs r0, r2, r1
    str r0, [r3, #8]                 @ B x (WB0 - WK s)
    movs r0, #1
    lsls r0, r0, #16
    str r0, [r3, #4]                 @ G passes
    movs r0, #0x13
    lsls r0, r0, #5
    adds r0, r5, r0                  @ cp+0x260
    movs r1, r3
    bl wb_write
    ldr r6, =wlog
    str r1, [r6, #{WL_WB}]
    str r0, [r6, #{WL_WB} + 4]
9:  pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
.ltorg

@ wb_write: r0 = WB block (halfwords R, G, B at +8/+0xa/+0xc: cp+0x260, or wbunity), r1 = 3 Q16 multipliers (R, G, B).
@ Writes 0x12018110 = R<<16 | G and 0x12018114 = B (each gain9), keeps the multipliers in wbm for hook_wbu.
@ Returns r1 = the R|G word, r0 = the B word.
wb_write:
    push {{r4, r5, r6, r7, lr}}
    movs r5, r0
    ldr r6, =wbm
    ldr r0, [r1, #0]
    ldr r2, [r1, #4]
    ldr r3, [r1, #8]
    str r0, [r6, #0]
    str r2, [r6, #4]
    str r3, [r6, #8]
    ldrh r0, [r5, #8]
    ldr r1, [r6, #0]
    bl gain9
    lsls r7, r0, #16
    ldrh r0, [r5, #0xa]
    ldr r1, [r6, #4]
    bl gain9
    orrs r7, r0
    ldrh r0, [r5, #0xc]
    ldr r1, [r6, #8]
    bl gain9
    ldr r2, =0x12018110
    str r7, [r2, #0]
    str r0, [r2, #4]
    movs r1, r7
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
.ltorg

@ ================================================================ unity-WB wrap (bl 0x174ca4 -> ARM 0x9b7e0; r0 = cp)
@ After the develop hook, frames with readout format cp+0x40 != 3 (continuous frames 2.., AE-BKT 2..3: front-end WB)
@ call 0x9b7e0 at 0x964b8 (pass 1) / 0xafddc (pass 2): cp+0x268.. := 100 and CC2 WB := unity, which dropped WARMTH
@ and the red filter's WB tilt. Stock first, then the same multipliers on top of unity (R = B = 100 x 16/25, G = 64).
hook_wbu1:
    push {{r4, lr}}
    ldr r3, =next_wbu1
    b wbu_common
hook_wbu2:
    push {{r4, lr}}
    ldr r3, =next_wbu2
wbu_common:
    bl call_r3
    movs r4, r0
    ldr r1, =wbm
    ldr r0, [r1, #0]
    cmp r0, #0
    beq 1f                           @ this pass's hook wrote no recipe WB: stock unity stands
    ldr r0, =wbunity
    bl wb_write
    ldr r1, =wlog
    ldrb r0, [r1, #{WL_UNITY}]
    adds r0, #1
    strb r0, [r1, #{WL_UNITY}]
1:  movs r0, r4
    pop {{r4}}
    pop {{r3}}
    bx r3
.ltorg

@ leaf: if this module changed 0x1201800c, put back exactly what 0x92050 writes ([0x080113f8] or 0x4008993a,
@ masked 0x7f0fffff); otherwise touch nothing (bw-filters yw_restore).
yw_restore:
    ldr r1, =state
    ldrb r0, [r1, #0]
    cmp r0, #0
    beq 2f
    movs r0, #0
    strb r0, [r1, #0]
    ldr r2, =0x080113f8
    ldr r0, [r2, #0]
    cmp r0, #0
    bne 1f
    ldr r0, =0x4008993a
1:  ldr r1, =0x7f0fffff
    ands r0, r1
    ldr r1, =0x1201800c
    str r0, [r1, #0]
2:  bx lr
.ltorg

@ leaf: r0 = min(0x1ff, (r0 * r1) >> 16)
gain9:
    muls r0, r1, r0
    lsrs r0, r0, #16
    ldr r1, =0x1ff
    cmp r0, r1
    bls 1f
    movs r0, r1
1:  bx lr
.ltorg

@ ================================================================ sharpness getter 0x91268 (no args, r0 = 1..5)
hook_sh1:
    push {{r4, lr}}
    ldr r3, =next_sh1
    b sh_common
hook_sh2:
    push {{r4, lr}}
    ldr r3, =next_sh2
sh_common:
    bl call_r3
    movs r4, r0
    bl getcur
    cmp r0, #0
    beq 1f
    ldr r0, =cur
    ldrb r4, [r0, #{P_SHARP}]
    adds r4, #1                      @ option 0..4 (-2..+2) -> stock sharpness 1..5 (3 = stock default)
1:  movs r0, r4
    pop {{r4}}
    pop {{r3}}
    bx r3
.ltorg

call_r3:
    bx r3
"""

MENU = f"""
@ ================================================================ IMG SET menu row (AUTO / P-A / M)
.equ NSTOCK, {NSTOCK}
@ imgrow: r0 = 0 AUTO, 1 P/A/M -> r0 = the live IMG SET row if it carries our option list, else 0. Leaf.
imgrow:
    cmp r0, #0
    bne 1f
    ldr r1, =0x1c1ee8
    ldr r2, =0x1a91bd
    b 3f
1:  ldr r2, =0x1bf01d                @ 0x6f7ec: 2 = M
    ldrb r2, [r2]
    cmp r2, #2
    bne 2f
    ldr r1, =0x1c2158
    ldr r2, =0x1a91ef
    b 3f
2:  ldr r1, =0x1c20bc
    ldr r2, =0x1a91e3
3:  ldrb r2, [r2]                    @ row-map slot (1-based; 0x29e14 writes 5)
    subs r2, #1
    bmi 9f
    movs r0, #12
    muls r2, r0, r2
    adds r1, r1, r2
    ldr r2, [r1, #4]
    ldr r0, =imgopts
    cmp r0, r2
    bne 9f
    movs r0, r1
    bx lr
9:  movs r0, #0
    bx lr
.ltorg

@ commit wrappers (0x5b4e4 AUTO, 0x5a260 P/A/M; r0 = 0/1 from the cursors, else a saved record pointer).
@ A recipe cursor (8..) becomes: id -> 0x1b8c0, sync (0x1b030 := its base 1/3/6), the stock cursor of that base (NORMAL
@ 2, B&W 4, B&W(TE) 5) for the stock commit, then the display cursor is put back. A stock cursor clears the id.
@ If the IMG SET row still shows the cursor the menu load gave it (mlcur: untouched in this menu session), the KOMA
@ tab's RECIPE row (0x1b8c0, possibly changed in this session) decides instead: a recipe commits as that recipe;
@ OFF with a stale recipe cursor commits the plain base entry (NORMAL / B&W / B&W(TE)). Otherwise a stale NORMAL cursor would undo a KOMA-page
@ recipe choice (and a stale recipe cursor would undo KOMA-page OFF) when the menu closes.
hook_mc0:
    push {{r4, r5, r6, r7, lr}}
    ldr r3, =next_mc0
    movs r7, #0
    b mc_common
hook_mc1:
    push {{r4, r5, r6, r7, lr}}
    ldr r3, =next_mc1
    movs r7, #1
    b mc_common
hook_mc2:
    push {{r4, r5, r6, r7, lr}}
    ldr r3, =next_mc2
    movs r7, #1
mc_common:
    movs r4, r0
    movs r5, r3
    cmp r0, #1
    bhi mc_plain
    movs r0, r7
    bl imgrow
    cmp r0, #0
    beq mc_plain
    movs r6, r0
    ldrb r7, [r6, #9]
    ldr r1, =mlcur
    ldrb r1, [r1]
    cmp r1, r7
    bne mc_user                      @ the IMG SET row was changed in this menu: it decides
    ldr r1, =ACT
    ldrb r0, [r1]
    cmp r0, #0
    beq mc_koff
    cmp r0, #N
    bhi mc_user
    movs r7, r0                      @ untouched row, KOMA-page recipe: commit it as that recipe's cursor
    adds r7, #NSTOCK
    strb r7, [r6, #9]
    b mc_user
mc_koff:
    cmp r7, #NSTOCK
    bls mc_plain                     @ untouched stock cursor, no recipe: stock commit
    ldr r1, =IMGSET                  @ untouched stale recipe cursor, recipe OFF: plain base entry
    ldrb r0, [r1]
    bl curof
    strb r0, [r6, #9]
    b mc_plain
mc_user:
    cmp r7, #NSTOCK
    bls mc_stock
    movs r0, r7
    subs r0, #NSTOCK
    cmp r0, #N
    bhi mc_stock
    ldr r1, =ACT
    strb r0, [r1]
    bl sync
    bl basecur
    strb r0, [r6, #9]
    movs r0, r4
    movs r3, r5
    bl call_r3
    strb r7, [r6, #9]
    b mc_out
mc_stock:
    ldr r1, =ACT
    movs r0, #0
    strb r0, [r1]
    bl sync
mc_plain:
    movs r0, r4
    movs r3, r5
    bl call_r3
mc_out:
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
.ltorg

@ load wrappers (0x5aaf0 AUTO, 0x5aeb4 P/A/M; no args): after the stock load, a recipe that still applies shows
@ as its own cursor; one whose base IMG SET was changed elsewhere (ADJ off, MY SETTINGS, reset) is switched off.
hook_ml0:
    push {{r4, r5, r6, lr}}
    ldr r3, =next_ml0
    movs r4, #0
    b ml_common
hook_ml1:
    push {{r4, r5, r6, lr}}
    ldr r3, =next_ml1
    movs r4, #0
    b ml_common
hook_ml2:
    push {{r4, r5, r6, lr}}
    ldr r3, =next_ml2
    movs r4, #1
    b ml_common
hook_ml3:
    push {{r4, r5, r6, lr}}
    ldr r3, =next_ml3
    movs r4, #1
ml_common:
    bl call_r3
    bl sync
    movs r0, r4
    bl imgrow
    movs r5, r0                      @ r5 = IMG SET row or 0 (flags from the move)
    beq 9f
    ldr r1, =ACT
    ldrb r0, [r1]
    cmp r0, #0
    beq 9f
    bl getcur
    cmp r0, #0
    bne 1f
    ldr r1, =ACT
    strb r0, [r1]
    bl sync
    b 9f
1:  adds r0, #NSTOCK
    strb r0, [r5, #9]
9:  cmp r5, #0                       @ remember the cursor this load left (mlcur; 0xff if no IMG SET row)
    beq 8f
    ldrb r0, [r5, #9]
    b 7f
8:  movs r0, #0xff
7:  ldr r1, =mlcur
    strb r0, [r1]
    pop {{r4, r5, r6}}
    pop {{r3}}
    bx r3
"""

ADJD, ADJ_TYPE, ADJ_IMGSET = 0x1C249C, 0x1BF090, 8    # ADJ_TYPE: big-endian word (IMG SET = 8), not a byte
ADJ_ITEM = 0x1C2450                                   # ADJ item table entry for IMG SET: options word, count byte
ADJ_HINT_LIT, ADJ_HINT = 0x83D8C, 0x1C31E0            # literal -> 12-byte hint rows (selected IMG SET row only)
SITE_ADJ_OPEN = (0x592CC, 0x5888A)
SITES_ADJ_STEP = [0x7529C, 0x7535C, 0x7541C, 0x77DF2, 0x77E30]     # bl 0x77450: IMG SET applied live per step
SITES_ADJ_COMMIT = [0x77174, 0x780D0, 0x7D9CA, 0x7E894]            # bl 0x59314: ADJ IMG SET commit (clamps 1..7)
ADJ_STEP, ADJ_COMMIT = 0x77450, 0x59314


def _adj_src():
    hs = []
    for i in range(len(SITES_ADJ_STEP)):
        hs.append(f"hook_as{i}:\n    push {{r4, r5, r6, r7, lr}}\n    ldr r3, =next_as{i}\n    movs r6, #1\n"
                  f"    b adj_common")
    for i in range(len(SITES_ADJ_COMMIT)):
        hs.append(f"hook_ac{i}:\n    push {{r4, r5, r6, r7, lr}}\n    ldr r3, =next_ac{i}\n    movs r6, #0\n"
                  f"    b adj_common")
    return f"""
@ ================================================================ ADJ lever IMG SET (descriptor 0x1c249c, cursor +9)
.equ ADJD, 0x{ADJD:x}
@ open (0x592cc, after the stock cursor is set from 0x1b030): an applying recipe shows as its own entry.
hook_ao:
    push {{r4, lr}}
    ldr r3, =next_ao
    bl call_r3
    movs r4, r0
    bl sync
    bl getcur
    cmp r0, #0
    beq 1f
    adds r0, #NSTOCK
    ldr r1, =ADJD
    strb r0, [r1, #8]
    strb r0, [r1, #9]
1:  movs r0, r4
    pop {{r4}}
    pop {{r3}}
    bx r3
.ltorg

@ step (0x77450 applies +9 live: r6 = 1, only for ADJ type 8 = IMG SET) and commit (0x59314, IMG SET only: r6 = 0).
@ Recipe cursor c > 7: id -> 0x1b8c0 + sync, +9 := the base's stock cursor (2/4/5) around the stock call, then
@ +9 := c.
@ Stock cursor: id cleared.
""" + "\n".join(hs) + f"""
adj_common:
    movs r4, r0
    movs r5, r3
    cmp r6, #0
    beq 1f
    ldr r0, =0x{ADJ_TYPE:x}              @ ADJ item type: a 32-bit word (0x77040 ldr [r7,#0x4c]); ldrb read its MSB (0)
    ldr r0, [r0]
    cmp r0, #{ADJ_IMGSET}
    bne adj_plain
1:  ldr r6, =ADJD
    ldrb r7, [r6, #9]
    cmp r7, #NSTOCK
    bls adj_stock
    movs r0, r7
    subs r0, #NSTOCK
    cmp r0, #N
    bhi adj_stock
    ldr r1, =ACT
    strb r0, [r1]
    bl sync
    bl basecur
    strb r0, [r6, #9]
    movs r0, r4
    movs r3, r5
    bl call_r3
    strb r7, [r6, #9]
    b adj_out
adj_stock:
    ldr r1, =ACT
    movs r0, #0
    strb r0, [r1]
    bl sync
adj_plain:
    movs r0, r4
    movs r3, r5
    bl call_r3
adj_out:
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3
"""


ADJ = _adj_src()

# ------------------------------------------------------------------ data
P1 = {ch: 0x02 + i for i, ch in enumerate(" !&'()+-./0123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ")}
P1.update({"*": 0x31, "\u221e": 0x3B})


def glyphs(s):
    return b"".join(bytes([1, P1[c]]) for c in s) + b"\0\0"


def label_src(name, s):
    """9-language label table (all slots -> one page-1 glyph string), as the menu5 lane does."""
    return (f".p2align 2\n{name}:\n    .word " + ", ".join([f"{name}_s"] * 9) + f"\n{name}_s:\n    .byte "
            + ", ".join(str(b) for b in glyphs(s)) + "\n")


def data_src():
    stock = struct.unpack(">7I", fw.ram(IMG_OPTS, 28))
    out = [".p2align 2", "imgopts:", "    .word " + ", ".join(hex(w) for w in stock)]
    out += [f"    .word rname{i}" for i in range(N)]
    out += [label_src(f"rname{i}", r["name"]) for i, r in enumerate(RECIPES)]
    out += [".p2align 2", "adjhint:", "    .byte " + ", ".join(str(b) for b in fw.ram(ADJ_HINT, 12 * NSTOCK)),
            f"    .space {12 * N}"]
    out += [".p2align 2", "ftab:"] + [f"    .word 0x{a:08x}, 0x{b:x}, 0x{c:x}, 0x{d:x}" for a, b, c, d in FTAB]
    tt = [x for row in tone_table() for cbcr in row for x in cbcr]
    out += ["ttab:", "    .byte " + ", ".join(str(x & 0xFF) for x in tt)]
    out += ["limits:", "    .byte " + ", ".join(str(x) for t in range(MAXTYPE + 1) for x in LIMITS[t])]
    out += ["deftab:", "    .byte " + ", ".join(str(x) for r in CURATED for x in r["params"] + [r["type"]])]
    out += ["curmap:", "    .byte 1, 2, 3, 4, 6, 7, 5"]                 # IMG SET value 0..6 -> stock cursor
    out += [".p2align 2", "chromatab:", "    .word " + ", ".join(f"0x{chroma_word(i):08x}" for i in range(7))]
    out += ["mtab:"]                                                         # per look: MatR, MatB, MatG + TINT 0..6
    for mr, mg, mb in LOOK_MATRIX:
        out += ["    .word " + ", ".join(f"0x{w:08x}" for w in [mr, mb] + [tint_word(mg, t) for t in range(-3, 4)])]
    out += [".p2align 2", "wlog:", f'    .ascii "{WL_MAGIC.decode()}"', f"    .space {WL_LEN - len(WL_MAGIC)}"]
    out += [".p2align 2", "prm:", "    .word 0, 0, 0, 0", "prmu:", "    .word 0, 0, 0, 0", "gs:", "    .word 0, 0, 0, 0",
            "cur:", "    .space 12", "tmp:", "    .space 12", "state:", "    .byte 0, 0, 0, 0",
            "mlcur:", "    .byte 0xff, 0, 0, 0", "ckind:", "    .byte 0, 0, 0, 0",
            "wbm:", "    .word 0, 0, 0", "wbunity:", "    .hword 0, 0, 0, 0, 100, 64, 100, 0",
            ".p2align 2", "buf:", "    .space 512"]
    return "\n".join(out) + "\n"


def bases_blob():
    return b"".join(encode(base_table(i)) for i in range(NB))


INIT = """
init:
    push {r4, lr}
    bl sync
    pop {r4}
    pop {r3}
    bx r3
"""


def source(adj=True):
    return ENGINE + MENU + (ADJ if adj else "") + INIT + ".ltorg\n" + data_src()


# ------------------------------------------------------------------ feature
def _bl(site, target):
    from ..asm import bl_target
    got = bl_target(site, fw.ram(site, 4))
    assert got == target, f"{site:#x}: expected bl {target:#x}, image has {got}"


CHECKS = [
    (0x96430, "1df830197880", "0x96350: r0 = [cp+0x22] (r7 = cp) before the hooked bl 0x8ff0e at 0x96436"),
    (0xAFD38, "1df830197880", "0xafd20: r0 = [cp+0x22] before the hooked bl 0x8ff0e at 0xafd3e"),
    (0x96356, "1c07", "0x96350: r7 = cp"),
    (0xAFD22, "1c07", "0xafd20: r7 = cp"),
    (0x964B6, "1c38", "0x96350: r0 = cp before the hooked bl 0x174ca4 (unity WB) at 0x964b8"),
    (0xAFDDA, "1c38", "0xafd20: r0 = cp before the hooked bl 0x174ca4 (unity WB) at 0xafddc"),
    (WB_UNITY, "47780000eafc9acc", "0x174ca4 Thumb veneer: bx pc; b 0x9b7e0 (unity WB, ARM)"),
    (0x96360, "23137880015b18fd2802d02489280101", "0x96350 head: WB from cp+0x268.. (red WB tilt reads them)"),
    (CC2_UP, "b5f0b08427001c0c", "0x16d31c(offset, size, buf) CC2 LUT upload prologue"),
    (0x16D3DC, "4844433161812600", "0x16d31c: 0x12018118 = offset | 0x1000000, then the stream"),
    (0x9031A, "4c0d21007ae042b8d103", "CC2 selector-cache compare ldrb [r4,#0xb] (cache 0x1f4db3)"),
    (0x90350, "001f4da8", "cache base literal: cache byte = 0x1f4da8 + 0xb = 0x1f4db3"),
    (TE_SET, "b5901c076800", "0x171d80 mono/TE word setter (called with a stack word, as 0x8ff0e does)"),
    (0x8FFAC, "4668f0e1fee7", "0x8ff0e passes sp to 0x171d80 (same calling form)"),
    (0x8FFB8, "00178cac", "0x8ff0e literal: mono word shadow 0x178cac"),
    (0x1725F8, "680049016108477012018100", "WB setter: 0x12018110 = *r0"),
    (0x96426, "f7fcf8b7", "pass 1: bl 0x92598 (MatR/G/B, UV) before the hooked site: colour writes win"),
    (0x9642C, "f7fbff46", "pass 1: bl 0x922bc (chroma gain 0x12018004) before the hooked site"),
    (0xAFD2E, "f7e2fc33", "pass 2: bl 0x92598 before the hooked site 0xafd3e"),
    (0xAFD34, "f7e2fac2", "pass 2: bl 0x922bc before the hooked site 0xafd3e"),
    (0x171958, "6800490160c8477012018000", "Y-weight setter: 0x1201800c = *r0"),
    (SHARP_GET, "48104a117801", "0x91268 sharpness getter (switch on 0x1b030)"),
    (0x5AEA4, "001a91bd", "AUTO IMG SET row-map slot literal"),
    (0x5A63C, "001a91e3", "P/A IMG SET row-map slot literal"),
    (0x5A5FC, "001a91ef", "M IMG SET row-map slot literal"),
    (ADJ_HINT, fw.ram(ADJ_HINT, 12 * NSTOCK).hex(), "ADJ IMG SET hint rows (copied into the module table)"),
    (IMG_OPTS, fw.ram(IMG_OPTS, 28).hex(), "IMG SET option label pointers (copied into the module list)"),
]


def make(opts):
    from ..feature import Patch, Pointer
    adj = (opts or {}).get("adj", "on")
    if adj not in ("on", "off"):
        raise ValueError(f"bwrecipes.adj={adj!r}: use on or off")
    adj = adj == "on"
    # smoke_reachable: `koma smoke` stops at 0x9643a and never opens a menu or the ADJ lever, so pass 2, the
    # develop sharpening site and every menu/ADJ hook are SKIPs there (unit-emu covers them). EXIF sharpness is hit.
    nosmoke = dict(smoke_reachable=False)
    hooks = [Hook(SITE_DEV1, MONO, "hook_dev1", "next_dev1", "still develop pass 1: filter, toning / chroma, "
                                                            "matrix, WB; curve"),
             Hook(SITE_DEV2, MONO, "hook_dev2", "next_dev2", "still develop pass 2 (0xafd20): filter + WB tilt, "
                                                            "toning / chroma, matrix, WB (pass 2 rewrites them)",
                  **nosmoke)]
    hooks += [Hook(s, WB_UNITY, f"hook_wbu{i + 1}", f"next_wbu{i + 1}", "unity WB after the develop hook "
                   "(cp+0x40 != 3: continuous / AE-BKT frames 2..): re-apply WARMTH / red-filter WB on top",
                   smoke_reachable=False) for i, s in enumerate(SITES_WBU)]
    hooks += [Hook(s, SHARP_GET, f"hook_sh{i + 1}", f"next_sh{i + 1}", "sharpness getter -> recipe sharpness",
                   smoke_reachable=s != 0x91A8C)
              for i, s in enumerate(SITES_SHARP)]
    hooks += [Hook(s, t, f"hook_ml{i}", f"next_ml{i}", "IMG SET menu load: recipe cursor", **nosmoke)
              for i, (s, t, _) in enumerate(SITES_LOAD)]
    hooks += [Hook(s, t, f"hook_mc{i}", f"next_mc{i}", "IMG SET menu commit: recipe -> 3/6 + id", **nosmoke)
              for i, (s, t, _) in enumerate(SITES_COMMIT)]
    if adj:
        hooks += [Hook(SITE_ADJ_OPEN[0], SITE_ADJ_OPEN[1], "hook_ao", "next_ao", "ADJ IMG SET open: recipe cursor",
                       **nosmoke)]
        hooks += [Hook(s, ADJ_STEP, f"hook_as{i}", f"next_as{i}", "ADJ IMG SET live step", **nosmoke)
                  for i, s in enumerate(SITES_ADJ_STEP)]
        hooks += [Hook(s, ADJ_COMMIT, f"hook_ac{i}", f"next_ac{i}", "ADJ IMG SET commit", **nosmoke)
                  for i, s in enumerate(SITES_ADJ_COMMIT)]
    opt_be = struct.pack(">I", IMG_OPTS)
    pointers = [Pointer(r + 4, opt_be, "imgopts", why="IMG SET row options -> 7 stock + recipes") for r in IMG_ROWS]
    patches = [Patch(r + 8, b"\x07", bytes([NOPT]), "IMG SET row count 7 -> 7 + recipes") for r in IMG_ROWS]
    if adj:
        pointers += [Pointer(ADJ_ITEM, opt_be, "imgopts", why="ADJ IMG SET options -> 7 stock + recipes"),
                     Pointer(ADJ_HINT_LIT, struct.pack(">I", ADJ_HINT), "adjhint", why="ADJ IMG SET hint rows")]
        patches += [Patch(ADJ_ITEM + 4, b"\x07", bytes([NOPT]), "ADJ IMG SET count 7 -> 7 + recipes")]
    return Feature(
        name=NAME, version=VERSION, summary=SUMMARY, options=opts, source=source(adj),
        blobs={"bases": bases_blob()}, init="init", hooks=hooks, pointers=pointers, patches=patches,
        checks=[Check(a, bytes.fromhex(h), w) for a, h, w in CHECKS],
        settings=[Settings(ST, ST_END, True, "RCP6 recipe store: header (+8..12 published) + 12-byte records + FX table"),
                  Settings(0x1B030, 0x1B031, False, "IMG SET: recipe base NORMAL (1) / B&W (3) / B&W(TE) (6)",
                           values={1, 3, 6})],
        exports=["on_change", "getcur"] + SHARED, smoke=smoke_checks)


# Shared with preview (live-view re-apply): tables, scratch and leaf routines, so nothing is duplicated. Exports only:
# the bwrecipes code bytes are unchanged by them.
SHARED = ["cur", "ckind", "prm", "prmu", "ftab", "ttab", "mtab", "state", "yw_restore", "gen_chunk", "gs", "buf", "bases"]


SMOKE_RECIPE = "POSITIVE"   # colour: STANDARD + params, chroma x1.30, the POSITIVE matrix; looked up by name


def _smoke_id():
    return [r["name"] for r in RECIPES].index(SMOKE_RECIPE) + 1


def smoke_setup():
    """Recipe POSITIVE active on IMG SET NORMAL, Koma magic so the core keeps the rows."""
    return {0x1B030: 1, 0x1B8B0: 0x4B, 0x1B8B1: 0x4F, 0x1B8B2: 0x4D, 0x1B8B3: 0x41, 0x1B8B4: 1, ACT: _smoke_id()}


def wlog_addr(read):
    """Resident wlog: Koma publish slot -> manifest (resident start), then the magic."""
    if read(fw.PUBLISH, 4) != b"KOMA":
        return None
    base = struct.unpack(">I", read(fw.PUBLISH + 4, 4))[0]
    blob = read(base, 0x10000)
    i = blob.find(WL_MAGIC)
    return None if i < 0 else base + i


def smoke_checks(obs):
    """POSITIVE poked active on NORMAL: the smoke stops right after pass 1's hook (0x9643a), so the last CC2 stream and
    the last chroma gain / matrix values the CC2 register model holds are pass 1's. Checked three ways: the module's
    own write log, the emulator's register file (last value written to each address) and the CC2 stream."""
    rid = _smoke_id()
    r = RECIPES[rid - 1]
    want = colour_regs(r["params"], r["type"])
    ups = obs.cc2_uploads
    curve_sha = hashlib.sha256(struct.pack(">4096H", *curve(r["params"]))).hexdigest()
    out = [("recipe still active, IMG SET NORMAL", obs.ram8(ACT) == rid and obs.ram8(0x1B030) == 1,
            f"act={obs.ram8(ACT)} (want {rid}) 0x1b030={obs.ram8(0x1B030)}"),
           (f"last CC2 stream = {SMOKE_RECIPE} generated curve (16 x 256 chunks)", bool(ups) and ups[-1][0] == curve_sha,
            f"uploads={[(h[:8], n) for h, n in ups]} want={curve_sha[:8]}"),
           ("CC2 cache invalidated (0xff)", obs.ram8(CC2_CACHE) == 0xFF, f"0x1f4db3={obs.ram8(CC2_CACHE):#x}"),
           ("store 'RCP6' v6, published type colour", obs.ram(ST, 5) == MAGIC + bytes([STORE_VER]) and
            obs.ram8(SELB) == TYPE_COLOUR and obs.ram8(SELCOL) == 0, obs.ram(ST, 11).hex())]
    wl = wlog_addr(obs.ram)
    out.append(("write log found in the resident block", wl is not None, f"wlog={wl and hex(wl)}"))
    if wl is not None:
        b = obs.ram(wl, WL_LEN)
        got = {0x12018004: struct.unpack(">I", b[WL_CHROMA:WL_CHROMA + 4])[0]}
        got.update(zip((0x12018104, 0x12018108, 0x1201810C), struct.unpack(">3I", b[WL_MAT:WL_MAT + 12])))
        out.append(("module wrote chroma gain + MatR/G/B in pass 1 (write log)",
                    b[WL_COUNT] >= 1 and b[WL_PASS] == 1 and b[WL_KIND] == r["type"] and
                    all(got[a] == want[a] for a in got),
                    f"count={b[WL_COUNT]} pass={b[WL_PASS]} kind={b[WL_KIND]:#x} " +
                    " ".join(f"{a:#x}={got[a]:08x}/{want[a]:08x}" for a in got)))
    regs = getattr(getattr(obs._m, "default_model", None), "regs", None)
    if regs is not None:
        cur = {a: regs.get(a) for a in (0x12018004, 0x12018104, 0x12018108, 0x1201810C)}
        out.append(("CC2 register model holds the recipe's chroma gain + matrix after pass 1",
                    all(cur[a] == want[a] for a in cur),
                    " ".join(f"{a:#x}={'-' if v is None else f'{v:08x}'}" for a, v in cur.items())))
    obs.bwrecipes = dict(want={hex(a): f"{v:08x}" for a, v in want.items()})
    return out
