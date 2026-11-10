"""Research-checkout only (scripts/export-release.sh leaves this file out): the vendored copies in koma/vendor,
koma/data and koma/emu still match the analysis sources they came from, and the cross-checks that need those sources.
Every test returns early when the research tree is absent."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys

from _util import fw

REPO = fw.ROOT
LANES = REPO / "analysis"


def _have(*paths):
    return all((REPO / p).exists() for p in paths)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_vendored_data_equals_lane_data():
    if not _have("analysis/tone-pinned/candidate-tables.csv"):
        return
    rows = list(csv.DictReader(open(LANES / "tone-pinned/candidate-tables.csv")))
    mine = list(csv.DictReader(open(fw.DATA / "tone-pinned.csv")))
    assert [(r["index"], r["sib-R3-18c3c4"]) for r in rows] == [(r["index"], r["sib-R3-18c3c4"]) for r in mine]
    for n in ("acros-like", "hi-contrast-bw", "soft-monotone", "tri-x-like"):
        assert (fw.DATA / f"recipes/luts/{n}.csv").read_bytes() == (LANES / f"recipes/luts/{n}.csv").read_bytes(), n
    assert (fw.DATA / "colour-recipes/fuji-classic-chrome-pinned.csv").read_bytes() == \
        (LANES / "colour-recipes/luts/fuji-classic-chrome-pinned.csv").read_bytes()
    cc = json.loads((LANES / "colour-recipes/recipes.json").read_text())["recipes"]["fuji-classic-chrome"]
    assert json.loads((fw.DATA / "colour-recipes/fuji-classic-chrome.json").read_text()) == cc
    pa = json.loads((LANES / "adj-cache-exposure/report.json").read_text())["phase_A_lines32"]
    assert json.loads((fw.DATA / "phase-a-lines32.json").read_text())["phase_A_lines32"] == pa


def test_vendored_builders_equal_lane_builders():
    if not _have("analysis/menu-5tab/menu5.py", "analysis/recipe-slots/build.py", "analysis/module-loader/samples.py"):
        return
    for p in ("tools", "analysis/module-loader", "analysis/menu-5tab"):
        if str(REPO / p) not in sys.path:
            sys.path.insert(0, str(REPO / p))
    import menu5 as lane_m5          # noqa: E402  (analysis copies, imported by their own names)
    import menu5v2 as lane_v2        # noqa: E402
    import samples as lane_samples   # noqa: E402
    import grmod as lane_grmod       # noqa: E402
    from koma.features import _legacy
    from koma.features.menu5 import legacy
    from koma.vendor import grmod
    m5, v2, _ = legacy()
    assert m5.CODE == lane_m5.CODE and m5.patches() == lane_m5.patches() and m5.source() == lane_m5.source()
    assert v2.ANCHOR == lane_v2.ANCHOR and v2.BAND_SRC == lane_v2.BAND_SRC
    s = _legacy.samples()
    assert s.r3_table() == lane_samples.r3_table() and s.SNAP_PATCHES == lane_samples.SNAP_PATCHES
    assert grmod.PATCHER == lane_grmod.PATCHER
    rb, lane_rb = _legacy.recipe_build(), _load("lane_recipe_slots_build", "analysis/recipe-slots/build.py")
    assert rb.CURVE_BYTES == lane_rb.CURVE_BYTES and rb.CHECKS == lane_rb.CHECKS and rb.SLOTS == lane_rb.SLOTS
    assert rb.source(1, 1) == lane_rb.source(1, 1)


def test_vendored_emulator_matches_research_copy():
    if not _have("tools/grdemu/core.py"):
        return
    emu = fw.PKG / "emu"
    for n in ("core", "periph", "cpu_profile", "capture_device"):          # copied unchanged
        assert (emu / f"grdemu/{n}.py").read_bytes() == (REPO / f"tools/grdemu/{n}.py").read_bytes(), n


def test_module_loader_image_equals_lane_image():
    img = REPO / "analysis/module-loader/images/UNAPPROVED_DO_NOT_FLASH_loader.type2.bin"
    if not img.exists():
        return
    assert fw.loader_image() == img.read_bytes()


def test_make_loader_equals_flash1_package_and_updater():
    from koma import loader as L
    pkg = REPO / "analysis/flash-1-package/UNAPPROVED_DO_NOT_FLASH_flash1-loader-rev024m.type2.bin"
    if pkg.exists():
        assert L.build(fw.stock()) == pkg.read_bytes()
        assert hashlib.sha256(pkg.read_bytes()).hexdigest() == L.OUTPUT_SHA256
    upd = REPO / "firmware/original/gr-240.exe"
    import shutil
    if upd.exists() and shutil.which("bsdtar"):
        assert L.read_stock(upd) == fw.stock()


def test_fastkeys_equal_speed_lane():
    if not _have("analysis/speed/build_modules.py"):
        return
    from koma.features import fastkeys
    bm = _load("lane_speed_build_modules", "analysis/speed/build_modules.py")
    assert [(a, b, n) for a, b, n in bm.FASTREPEAT2] == [(p.addr, p.before, p.after) for p in fastkeys.make({"cpu1": "scan"}).patches]  # 1.1 adds the CPU1 pin (not in the analysis source)


def test_colourprobe_table_against_matrix_model():
    """The words are what probe2/matrix_model.py encodes / decodes (sign-magnitude, b3 b2 b1 b0)."""
    if not _have("analysis/colour-research/probe2/matrix_model.py"):
        return
    from koma.features import colourprobe as P
    mm = _load("matrix_model", "analysis/colour-research/probe2/matrix_model.py")
    assert mm.decode_word(0xBF000000) == {3: -63, 2: 0, 1: 0, 0: 0}
    assert mm.decode_word(0x81000000)[3] == -1                     # sign-magnitude reading of the 2's-c. candidate
    assert mm.decode_word(0x20909000) == {3: 32, 2: -16, 1: -16, 0: 0}
    assert mm._to_byte(32) << 24 | mm._to_byte(-16) << 16 | mm._to_byte(-16) << 8 == 0x20909000
    assert mm.encode([[0, 0, 0], [0, 0, 0], [0, 0, 0]], (0, 31))[mm.REG_UV] == 0x1F00
    assert P.scaled(0x8470697C) == 0xAB9188A1


def derive_matrix(key):
    """recipes.json tier-B look `key` -> (MatR, MatG, MatB) words and the fit report: least-squares fit, in matrix model
    v2's chroma-domain units, of the chroma change the tier-B pre-tone matrix makes through the STANDARD curve (c3);
    luma change dropped (the stage cannot make one); then the model's encode(). This is how bwrecipes.LOOK_MATRIX was
    made."""
    import numpy as np
    from koma.features import bwrecipes as B
    if str(LANES / "colour-recipes") not in sys.path:
        sys.path.insert(0, str(LANES / "colour-recipes"))
    import colour_model as CM
    MM = B.matrix_model()
    M = np.array(json.loads((LANES / "colour-recipes/recipes.json").read_text())["recipes"][key]["tiers"]["B"]
                 ["M_rgb_linear"])
    X = CM.training_set()                                    # ColorChecker x2, 5^3 grid, neutral ramp x12
    s, t = CM.hw(X, CM.C3) * 255, CM.hw(X, CM.C3, M=M) * 255
    ys, yt = MM.rgb_to_ycc(s), MM.rgb_to_ycc(t)
    dC = yt[:, 1:] - ys[:, 1:]
    Xd = ys[:, :1] + MM.LAMBDA * (s - ys[:, :1])
    A = np.einsum("kr,ic->ikrc", MM.J, Xd).reshape(-1, 9)
    D = np.clip(np.linalg.lstsq(A, dC.ravel(), rcond=None)[0].reshape(3, 3), -63 / 64, 63 / 64)
    regs = MM.encode(D)
    regs.pop(MM.REG_UV)
    pred = MM.predict_ycc(ys, regs)
    rms = lambda e: float(np.sqrt((e ** 2).mean()))         # noqa: E731
    return tuple(regs[r] for r in MM.ROWS), dict(rms_change=rms(dC), rms_residual=rms(pred[:, 1:] - yt[:, 1:]))


def test_color_look_matrices_rederive_and_vendored_model():
    """bwrecipes.LOOK_MATRIX = derive_matrix(tier-B src) for all six color looks; the vendored model v2 equals the
    probe-3 analysis file."""
    if not _have("analysis/colour-research/probe3/matrix_model.py", "analysis/colour-recipes/recipes.json"):
        return
    from koma.features import bwrecipes as B
    assert (fw.PKG / "vendor/colour/matrix_model.py").read_bytes() == \
        (LANES / "colour-research/probe3/matrix_model.py").read_bytes()
    for look, r in enumerate(B.CLOOKS):
        if r["src"].startswith("grd4:"):          # GRD IV refit (v4.1): grd4_matrix, re-derived in test_bwrecipes
            continue
        words, fit = derive_matrix(r["src"])
        assert words == B.LOOK_MATRIX[look], (r["name"], [hex(w) for w in words])
        assert fit["rms_residual"] < fit["rms_change"], r["name"]
