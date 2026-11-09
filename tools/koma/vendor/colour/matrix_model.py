"""Empirical model v2 of the GR DIGITAL 2.40 CC2 colour stage: matrix MatR/MatG/MatB (0x12018104/108/10c), pUVoffset
coring (0x120180d8) and chroma gain (0x12018004). Fitted to colour probes 2 and 3
. numpy only. Same API as v1 (probe2/matrix_model.py)
plus a `chroma_gain` argument.

What changed from v1 (O = observed on hardware in probe 3):
  * Byte coding CONFIRMED sign-magnitude (O): bit 7 = sign, bit 6 ignored, bits 5..0 = magnitude, coefficient m/64.
    0xBF = -63 gives the mirror image of +63 (same |dCr|/R, 0.97-1.03 vs 0.87-0.98); 0x81 = -1 (no visible change).
    7-bit two's complement is refuted.
  * b0 = B input CONFIRMED (O): MatR b0 dCr regresses on B (rms 3.6) not G (6.2); joint G+B fit gives G -0.09, B 1.02.
  * Entries add before the clamp (O): MatR 20909000 (+32, -16, -16, 0) predicted to 1.0-1.4 / 1.6-2.7 codes rms.
  * pUVoffset high field = Cr coring (O), same form as the low field: Cr' = sign(Cr)*max(|Cr| - n, 0), all luma,
    both signs of Cr.
  * Chroma gain 0x12018004: all four bytes scaled by g multiply Cr by g (1.298 for x1.30) at every hue and both signs
    (O). Whether it acts before or after the matrix is not settled (README Q6). The default here is BEFORE (it fits
    frame B6 at Cb rms 3.0 vs 5.4); GAIN_AFTER_MATRIX / gain_after= switches it. Treat matrix + gain together as
    +-5 codes Cb, up to +-15 on whites if the order is wrong. Coring order relative to the gain is U (applied last).
  * Clamp replaced (O-fit): v1's luma-dependent half-axis clamps fail for negative pushes (MatR -63 drives Cr to -127
    even on white). v2 clips each matrix row output X_r + dX_r to ROW_CLIP = [0, ~230] (a row already outside the range
    is not pulled in), then clips Cb/Cr to the 8-bit range. This fits both probe 2 (incl. MatG on orange) and probe 3.
  * Parameters (J, byte scales, LAMBDA, ROW_CLIP) refitted jointly on probe-2 k1..k8 and probe-3 A1, A3, B1, B3, B4,
    B6 (fit_v2.py). J[Cb,R] is now -0.17 (v1 -0.07): MatR moves Cb by about -0.16x its Cr move, in both directions.

Model: X = Y + LAMBDA*(RGB - Y) (stock 8-bit gamma RGB, desaturated); D = 3x3 delta from the register bytes;
dX = clip(X + D X, [0, 230]) - X; C = g*C0 + J dX (gain before) or g*(C0 + J dX) (after); clip to 8 bits; coring.
Y is never changed.

Accuracy (dense 4x4-block means, luma-consistent blocks, frame-weighted pooled rms Cb/Cr in 8-bit codes; fit_v2.json):
probe 2 k1..k9 2.7 / 4.2, probe 3 A1..B6 2.4 / 3.2, all 2.5 / 3.8 (v1 on the same set: 5.1 / 11.1). Where the
prediction is unsaturated (|C| < 90, Y < 180): 2.1 / 3.5. Worst frames: MatG/MatB +63 single entries (Cb 4-5).
Validity: two warm-light scenes, one camera, NORMAL; in-sample fit (12 parameters over ~15 frames x 10k blocks).
"""
from __future__ import annotations

import numpy as np

# ------------------------------------------------------------------ frozen parameters (probe3/fit_v2.py)
J = np.array([[-0.1684, -0.8656, 1.2096],
              [1.0321, -1.0583, 0.0055]])
BYTE_SCALE = {3: 1.0, 2: 1.0468, 1: 0.9753, 0: 0.9192}
BYTE_COL = {3: 0, 2: 1, 1: 1, 0: 2}                    # byte -> input column (R, G, G, B); b0 = B confirmed
LAMBDA = 0.5967
ROW_CLIP = (0.0, 229.57)                                # each matrix row output X_r + dX_r is clipped to this range
GAIN_AFTER_MATRIX = False                               # Q6 inconclusive; 'before' fits frame B6 better
PARAMS_VECTOR = np.r_[J.ravel(), BYTE_SCALE[2], BYTE_SCALE[1], BYTE_SCALE[0], LAMBDA, ROW_CLIP]

REG_MATR, REG_MATG, REG_MATB, REG_UV = 0x12018104, 0x12018108, 0x1201810C, 0x120180D8
REG_GAIN = 0x12018004
ROWS = (REG_MATR, REG_MATG, REG_MATB)


def pack(d):
    return np.r_[np.ravel(d["J"]), d["byte_scale"]["2"], d["byte_scale"]["1"], d["byte_scale"]["0"], d["lambda"],
                 d["row_clip"]]


def unpack(p):
    p = np.asarray(p, float)
    return {"J": p[:6].reshape(2, 3).round(4).tolist(),
            "byte_scale": {"3": 1.0, "2": round(float(p[6]), 4), "1": round(float(p[7]), 4), "0": round(float(p[8]), 4)},
            "lambda": round(float(p[9]), 4), "row_clip": [round(float(p[10]), 2), round(float(p[11]), 2)]}


# ------------------------------------------------------------------ colour helpers (JPEG full-range BT.601)
def ycc_to_rgb(ycc):
    """(Y, Cb, Cr) with Cb/Cr centred on 0 -> 8-bit gamma RGB (unclipped)."""
    ycc = np.asarray(ycc, float); y, cb, cr = ycc[..., 0], ycc[..., 1], ycc[..., 2]
    return np.stack([y + 1.402 * cr, y - 0.344136 * cb - 0.714136 * cr, y + 1.772 * cb], -1)


def rgb_to_ycc(rgb):
    rgb = np.asarray(rgb, float); r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return np.stack([0.299 * r + 0.587 * g + 0.114 * b, -0.168736 * r - 0.331264 * g + 0.5 * b,
                     0.5 * r - 0.418688 * g - 0.081312 * b], -1)


def srgb_to_linear(v):
    v = np.clip(np.asarray(v, float), 0, None)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(v):
    v = np.clip(np.asarray(v, float), 0, None)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055)


# ------------------------------------------------------------------ register coding
def byte_value(b):
    """Register byte -> signed magnitude m (coefficient m/64). Sign-magnitude, bit 6 ignored (both O)."""
    b &= 0xFF
    m = b & 0x3F
    return -m if b & 0x80 else m


def _to_byte(m):
    m = int(round(m))
    if not -63 <= m <= 63:
        raise ValueError(f"coefficient {m}/64 out of range +-63/64")
    return (0x80 | -m) if m < 0 else m


def decode_word(w):
    """32-bit row word -> {byte: m} (coefficient m/64)."""
    return {b: byte_value((w >> (8 * b)) & 0xFF) for b in (3, 2, 1, 0)}


def encode(M3x3_delta, uv_offset=(0, 0)):
    """Target 3x3 delta (rows = output R, G, B; columns = input R, G, B; 1.0 = 64 LSB of byte 3) -> register words.

    Corrects for the per-byte unity (BYTE_SCALE). The G column is split over bytes 2 and 1. uv_offset =
    (cb_core, cr_core): coring thresholds in 8-bit codes, 0..63 (both fields confirmed). Returns {address: word}.
    The chroma gain is not encoded here: scale the camera's own gain bytes (0x12018004) by g at run time.
    """
    M = np.asarray(M3x3_delta, float)
    out = {}
    for r, reg in enumerate(ROWS):
        mR = M[r, 0] * 64 / BYTE_SCALE[3]
        g_total = M[r, 1] * 64
        g2 = np.clip(g_total / 2 / BYTE_SCALE[2], -63, 63)
        g1 = np.clip((g_total - g2 * BYTE_SCALE[2]) / BYTE_SCALE[1], -63, 63)
        mB = M[r, 2] * 64 / BYTE_SCALE[0]
        out[reg] = (_to_byte(mR) << 24) | (_to_byte(g2) << 16) | (_to_byte(g1) << 8) | _to_byte(mB)
    cb, cr = (int(round(x)) for x in uv_offset)
    if not (0 <= cb <= 63 and 0 <= cr <= 63):
        raise ValueError("coring thresholds are 6-bit (0..63)")
    out[REG_UV] = (cr << 8) | cb
    return out


def registers_to_delta(regs, byte_scale=None):
    """{address: word} -> effective 3x3 delta (input-column units incl. per-byte scale), coring (cb, cr)."""
    sb = BYTE_SCALE if byte_scale is None else byte_scale
    D = np.zeros((3, 3))
    for r, reg in enumerate(ROWS):
        for b, m in decode_word(regs.get(reg, 0)).items():
            D[r, BYTE_COL[b]] += sb[b] * m / 64
    uv = regs.get(REG_UV, 0)
    return D, (uv & 0x3F, (uv >> 8) & 0x3F)


# ------------------------------------------------------------------ the model
def predict_ycc_p(p, stock_ycc, registers, chroma_gain=1.0, gain_after=None):
    """predict_ycc with an explicit parameter vector (used by fit_v2.py)."""
    p = np.asarray(p, float)
    Jp = p[:6].reshape(2, 3); sb = {3: 1.0, 2: p[6], 1: p[7], 0: p[8]}; lam = p[9]; pmin, pmax = p[10], p[11]
    after = GAIN_AFTER_MATRIX if gain_after is None else gain_after
    a = np.asarray(stock_ycc, float); shp = a.shape; a = a.reshape(-1, 3)
    Y, C0 = a[:, 0], a[:, 1:]
    D, (tcb, tcr) = registers_to_delta(registers, sb)
    if not after:
        C0 = C0 * chroma_gain
    Xd = Y[:, None] + lam * (ycc_to_rgb(a) - Y[:, None])
    dX = Xd @ D.T                                      # (N,3) delta per matrix output row
    if np.any(D):                                      # row clip; a row already outside the range is not pulled in
        dX = np.clip(Xd + dX, np.minimum(Xd, pmin), np.maximum(Xd, pmax)) - Xd
    C = C0 + dX @ Jp.T
    if after:
        C = C * chroma_gain
    C = np.clip(C, -128, 127)
    C = np.sign(C) * np.maximum(np.abs(C) - np.array([tcb, tcr]), 0)
    return np.c_[Y, C].reshape(shp)


def predict_ycc(stock_ycc, registers, chroma_gain=1.0):
    """stock_ycc: (..., 3) 8-bit JPEG Y, Cb, Cr with Cb/Cr centred on 0 (the stock look: matrix 0, UV 0, stock gain).
    registers: {address: word} for MatR/MatG/MatB/pUVoffset (missing = stock 0). chroma_gain: factor applied to all
    four bytes of the stock 0x12018004 value (1.0 = stock). Returns predicted (..., 3) Y, Cb, Cr."""
    return predict_ycc_p(PARAMS_VECTOR, stock_ycc, registers, chroma_gain)


def predict(stock_rgb_linear, registers, chroma_gain=1.0):
    """stock_rgb_linear: (..., 3) linear-light sRGB 0..1 of the stock JPEG -> predicted linear RGB 0..1."""
    g = linear_to_srgb(np.asarray(stock_rgb_linear, float)) * 255
    out = predict_ycc(rgb_to_ycc(g), registers, chroma_gain)
    return srgb_to_linear(np.clip(ycc_to_rgb(out), 0, 255) / 255)


if __name__ == "__main__":
    regs = encode([[63 / 64, 0, 0], [0, 0, 0], [0, 0, 0]])
    assert regs[REG_MATR] == 0x3F000000, hex(regs[REG_MATR])
    assert decode_word(0x7F000000)[3] == 63 and decode_word(0xBF000000)[3] == -63 and decode_word(0x81000000)[3] == -1
    print(predict_ycc(np.array([[42.0, -12.0, 15.0]]), {REG_MATR: 0x3F000000}))
