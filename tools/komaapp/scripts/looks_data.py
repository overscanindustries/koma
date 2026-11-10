#!/usr/bin/env python3
"""Write tools/komaapp/src/lib/looksData.js: the numbers the Looks area's try-on and look store use, taken from the
Koma firmware sources so the app never keeps its own copy of a look.

Sources (all STATIC, nothing measured on a camera here):
  tools/koma/features/bwrecipes.py  5.0  base tone curves (the 2-bit blob the module carries), the on-camera tone map
                                         constants (fval), every look's default record, limits, display order, the
                                         ADJ LEVER default mask, B&W filter luma words and the RED WB tilt, toning
                                         (TE rows read from the 2.40 image), saturation steps, WARMTH step, the color
                                         looks' matrix words and the TINT taps
  tools/koma/vendor/colour/matrix_model.py  model v2 of the CC2 color stage (fitted to color probes 2 and 3, O-hw)
  tools/koma/features/selcolour.py  2.0  SELECT hue bands, chroma threshold and soft edge
  tools/koma/features/fx.py         1.0  GRAIN amplitudes and VIGNETTE k

    python3 tools/komaapp/scripts/looks_data.py          # rewrite the file
    python3 tools/komaapp/scripts/looks_data.py --check  # exit 1 when the file is out of date

tools/komaset/web/test_e2e_looks.py runs the --check comparison as a plain unit test.
"""
from __future__ import annotations

import base64
import inspect
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
TOOLS = APP.parent
OUT = APP / "src" / "lib" / "looksData.js"


def select_defaults(sc) -> dict:
    """SELECT's chroma threshold and soft edge: the defaults the module is built with (selcolour.source), which its
    Python model (reference_lut) must share."""
    built, model = inspect.signature(sc.source).parameters, inspect.signature(sc.reference_lut).parameters
    out = {"thr": built["thr"].default, "soft": built["soft"].default}
    if out != {"thr": model["thr"].default, "soft": model["soft"].default}:
        raise SystemExit("selcolour: source() and reference_lut() defaults differ")
    return out


def grain_amplitudes(fx) -> list:
    """The noise each GRAIN step adds, in 8-bit levels: fx's own model of the kernel (reference_pass) run on a flat
    mid-grey plane, largest change seen (the noise is uniform odd values in -a..a)."""
    w, lines, flat = 256, 4, 128
    plane = bytes([flat]) * (w * lines)
    return [max(abs(v - flat) for v in fx.reference_pass(plane, w, w, lines, g, 0, 0x9E3779B9)) for g in range(4)]


def build() -> str:
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    from koma.features import bwrecipes as bw, selcolour as sc, fx
    from koma.vendor.colour import matrix_model as mm

    bases = [{"name": n, "blob": base64.b64encode(bw.encode(bw.base_table(i))).decode()}
             for i, (n, _, _) in enumerate(bw.BASES)]
    names = [r["name"] for r in bw.ALL]                      # by id - 1 (STANDARD is id 21)
    defaults = [list(r["params"]) + [r["type"]] for r in bw.ALL]

    def weights(word):
        r, b = (word >> 8) & 0xFF, word & 0xFF
        return [r, 512 - r - b, b]
    stock_word = 0x4008993A                                  # the stock luma word 0x92050 writes (0x080113f8 default)
    # FTAB: (luma word, WB R, WB G, WB B) per filter 1..4; the WB words are Q16 multipliers on the shot's own AWB
    # values, against the stock head's R x 16/25, G x 1, B x 16/25 (bwrecipes WB0 = 41943)
    filters = [{"name": "OFF", "w": weights(stock_word), "wb": [1, 1, 1]}]
    for name, (word, r, g, b) in zip(bw.FILTERS[1:], bw.FTAB):
        tilt = [round(r / bw.WB0, 4), round(g / 65536, 4), round(b / bw.WB0, 4)] if r else [1, 1, 1]
        filters.append({"name": name, "w": weights(word), "wb": tilt})

    data = {
        "source": {"bwrecipes": bw.VERSION, "selcolour": sc.VERSION, "fx": fx.VERSION, "matrix_model": "v2"},
        "bases": bases,
        "fval": {"lo": bw.LO, "hi": bw.HI, "pivot": bw.PIVOT, "key": bw.KEY_SH, "con": bw.CON_SH, "shd": bw.SHD_SH,
                 "hil": bw.HIL_SH},
        "names": names,
        "defaults": defaults,
        "std": bw.STD,
        "order": bw.DMAP,
        "adj": bw.DEFMASK,
        "limits": [bw.LIMITS[t] for t in (bw.TYPE_BW, bw.TYPE_COLOR, bw.TYPE_SEL)],
        "rows": {r["id"]: {"label": r["label"], "options": r["options"]} for r in bw.ROWS
                 if r["id"] in ("rp_curve", "rp_contrast", "rp_sharp", "rp_grain", "rp_vig", "rp_adjlever")},
        # p5..p7 by look type (0 B&W, 1 color, 2 SELECT): [label, options], as menu5's row variants show them
        "slots": [[list(row["variants"]["alts"][t]) for row in (next(r for r in bw.ROWS if r["id"] == pid)
                                                                  for pid in ("rp_p5", "rp_p6", "rp_p7"))]
                  for t in range(3)],
        "filters": filters,
        "tones": [[list(cc) for cc in row] for row in bw.tone_table()],          # SEPIA, SELENIUM, BLUE, GREEN x 1..5
        "te": {str(t): [list(cc) for cc in bw.te_row(t)] for t in range(1, 7)},  # stock B&W (TE) tints, for ghosts
        "sat": bw.SAT_STEPS,
        "warm": round(bw.WK / bw.WB0, 4),
        "matrix": [list(m) for m in bw.LOOK_MATRIX],
        "tintTaps": list(bw.TINT_TAPS),
        "model": {"J": mm.J.round(4).tolist(), "scale": [mm.BYTE_SCALE[b] for b in (0, 1, 2, 3)],
                  "col": [mm.BYTE_COL[b] for b in (0, 1, 2, 3)], "lambda": mm.LAMBDA, "clip": list(mm.ROW_CLIP)},
        "bands": {str(k): list(v) for k, v in sc.BANDS.items()},
        "select": select_defaults(sc),
        "grain": grain_amplitudes(fx),
        "vignette": [k / 256 for k in fx.KQ8],
    }
    body = json.dumps(data, separators=(",", ":"))
    return ("// Generated by tools/komaapp/scripts/looks_data.py from tools/koma (bwrecipes " + bw.VERSION + ", selcolour "
            + sc.VERSION + ", fx " + fx.VERSION + ", color matrix model v2). Do not edit: run the script.\n"
            "// STATIC: read from the firmware sources and the 2.40 image; nothing here was measured on a camera.\n"
            "export const LOOKS_DATA = " + body + ";\n")


def main():
    text = build()
    if "--check" in sys.argv:
        ok = OUT.exists() and OUT.read_text() == text
        print("looksData.js is up to date" if ok else "looksData.js is out of date: run tools/komaapp/scripts/looks_data.py")
        sys.exit(0 if ok else 1)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(TOOLS.parent)} ({len(text)} bytes)")


if __name__ == "__main__":
    main()
