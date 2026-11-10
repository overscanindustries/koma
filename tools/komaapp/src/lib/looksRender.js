// Try-on: a JavaScript imitation of what Koma does to a photo for a look, run on a JPEG the camera already made.
// APPROXIMATE until compared with real output. Every number comes from looksData.js (generated from tools/koma):
//
//   tone      the look's curve: its base table and the camera's own tone map (`fval`: CONTRAST, KEY, SHADOW,
//             HIGHLIGHT, integer-exact), applied relative to the curve the photo was made with (the look on the
//             camera now): out = look(source^-1(v)). Both are display-referred 10-bit, so the remap is the same one
//             bwrecipes uses to fit GRD IV curves (bwrecipes.md, "Curve mapping").
//   B&W       luma from the filter's luma word (R and B weights over 512, G the rest) and, for RED, its WB tilt
//             (Q16 against the stock head's R x 16/25, B x 16/25), then the fixed Cb/Cr of TONING: the stock TE rows,
//             10-bit register values, read as 8-bit chroma x 4 (the firmware's own packer shifts 8-bit chroma left
//             by 2). INFERRED: that the luma word forms Y.
//   colour    WARMTH as WB on linear light (R x (1 + 0.035 s), B x (1 - 0.035 s)), the look's matrix words with
//             TINT on the G row and SATURATION's chroma gain through the colour-stage model v2 (fitted to the
//             camera's colour probes 2 and 3, gain before the matrix), Y untouched.
//   SELECT    chroma gain, then selcolour's cell classes: keep inside the band, halve in the 8-degree soft edge,
//             grey elsewhere and below |Cb| + |Cr| = 12.
//   finish    SHARPNESS as an unsharp mask on Y, GRAIN as fx's uniform noise on Y in 16..239, VIGNETTE as fx's
//             (1 - k tx^2)(1 - k ty^2). Sharpening and grain are scaled to the preview size, the way they would
//             average out when the 8-megapixel photo is shown that small.
//
// What it cannot do: undo the matrix, saturation or filter of the look the photo was made with; give colour back
// to a black and white photo.
import {LOOKS_DATA as D} from './looksData.js';
import {P, TYPE} from './looksStore.js';

const CAMERA_WIDTH = 3264;           // the GR DIGITAL's 8-megapixel master width (the user's JPEGs are 3264 x 2448)

// ---------------------------------------------------------------- tone curves
const baseCache = [];
export function baseTable(i) {
  if (baseCache[i]) return baseCache[i];
  const bin = atob(D.bases[i].blob), out = new Uint16Array(4096);
  let v = 0, n = 0;
  for (let k = 0; k < bin.length; k++) {
    const byte = bin.charCodeAt(k);
    for (let s = 6; s >= 0; s -= 2) { v += (byte >> s) & 3; out[n++] = v; }
  }
  return (baseCache[i] = out);
}

const F = D.fval;
function fval(v, k, c, s, h) {
  if (v <= F.lo) return v;
  const d = v - F.lo, e = F.hi - v, a = d * e;
  let t = (k * a) >> F.key;
  const vp = v - F.pivot;
  t += (c * ((a >> 4) * vp)) >> F.con;
  if (vp < 0) t += (s * (-vp * d)) >> F.shd;
  else t += (h * (vp * e)) >> F.hil;
  return Math.min(F.hi, Math.max(F.lo, v + t));
}

const curveCache = new Map();
// The camera's full 4,096-entry stream for params p (bwrecipes `curve`): the base through fval, running max.
export function curve(p) {
  const key = p.slice(0, 5).join(',');
  let out = curveCache.get(key);
  if (out) return out;
  const base = baseTable(p[P.curve] < D.bases.length ? p[P.curve] : 1);
  const k = p[P.key] - 4, c = p[P.con] - 4, s = p[P.shd] - 4, h = p[P.hil] - 4;
  out = new Uint16Array(4096);
  let m = 0;
  for (let i = 0; i < 4096; i++) { const v = fval(base[i], k, c, s, h); if (v > m) m = v; out[i] = m; }
  curveCache.set(key, out);
  return out;
}

// Fractional index where curve C reaches each 8-bit output value (C is non-decreasing, 0..1023).
function inverse8(C) {
  const inv = new Float32Array(256);
  for (let v = 0; v < 256; v++) {
    const t = v * 1023 / 255;
    let lo = 0, hi = 4095;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (C[mid] < t) lo = mid + 1; else hi = mid; }
    if (C[lo] === t) { let j = lo; while (j < 4095 && C[j + 1] === t) j++; inv[v] = (lo + j) / 2; }
    else if (lo === 0) inv[v] = 0;
    else inv[v] = lo - 1 + (t - C[lo - 1]) / (C[lo] - C[lo - 1]);
  }
  return inv;
}

const lutCache = new Map();
// v8 -> v8 (float): the look's curve after the source curve's inverse. Identity in Ricoh's shared toe.
export function toneLut(srcP, dstP) {
  const key = srcP.slice(0, 5).join(',') + '>' + dstP.slice(0, 5).join(',');
  let lut = lutCache.get(key);
  if (lut) return lut;
  const inv = inverse8(curve(srcP)), C = curve(dstP);
  lut = new Float32Array(256);
  for (let v = 0; v < 256; v++) {
    const x = inv[v], i = Math.min(4094, Math.floor(x)), f = x - i;
    lut[v] = (C[i] + (C[i + 1] - C[i]) * f) * 255 / 1023;
  }
  if (lutCache.size > 200) lutCache.clear();
  lutCache.set(key, lut);
  return lut;
}

// ---------------------------------------------------------------- per-channel input tables (tone + white balance)
const s2l = v => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
const l2s = v => (v <= 0.0031308 ? v * 12.92 : 1.055 * Math.pow(v, 1 / 2.4) - 0.055);
function channelLut(tone, k) {
  const out = new Float32Array(256);
  for (let v = 0; v < 256; v++) {
    let x = v;
    if (k !== 1) x = 255 * l2s(Math.min(1, s2l(v / 255) * k));
    const i = Math.min(254, Math.floor(x)), f = x - i;
    out[v] = tone[i] + (tone[i + 1] - tone[i]) * f;
  }
  return out;
}

// ---------------------------------------------------------------- colour stage (model v2)
function smb(b) { const m = b & 0x3f; return b & 0x80 ? -m : m; }
function tintWord(w, t) {
  const bs = [0, 1, 2, 3].map(k => (w >>> (8 * k)) & 0xff);
  for (let k = 0; k < Math.abs(t); k++) {
    const pos = D.tintTaps[k];
    const m = Math.max(-63, Math.min(63, smb(bs[pos]) + (t > 0 ? 1 : -1)));
    bs[pos] = m < 0 ? 0x80 | -m : m;
  }
  return ((bs[3] << 24) | (bs[2] << 16) | (bs[1] << 8) | bs[0]) >>> 0;
}
function matrixDelta(words) {
  const M = D.model, d = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  let any = false;
  words.forEach((w, r) => {
    for (let b = 0; b < 4; b++) {
      const m = smb((w >>> (8 * b)) & 0xff);
      if (m) { d[r][M.col[b]] += M.scale[b] * m / 64; any = true; }
    }
  });
  return any ? d : null;
}

// SELECT: selcolour's 32 x 32 cell classes (0 grey, 1 keep, 2 half) for COLOUR 1..6 and RANGE 0/1.
const selCache = new Map();
function selTable(colour, range) {
  const key = colour + ':' + range;
  if (selCache.has(key)) return selCache.get(key);
  const band = D.bands[String(colour)] || D.bands['1'];
  const c = band[0], w = band[1 + (range ? 1 : 0)], soft = D.select.soft, thr = D.select.thr;
  const t = new Uint8Array(1024);
  for (let i = 0; i < 32; i++) for (let j = 0; j < 32; j++) {
    const cb = i * 8 + 4 - 128, cr = j * 8 + 4 - 128;
    if (Math.abs(cb) + Math.abs(cr) <= thr) continue;
    const th = (Math.atan2(cr, cb) * 180 / Math.PI + 360) % 360;
    const dd = Math.abs((((th - c) % 360) + 540) % 360 - 180);
    t[i * 32 + j] = dd <= w ? 1 : dd <= w + soft ? 2 : 0;
  }
  selCache.set(key, t);
  return t;
}

// ---------------------------------------------------------------- plan: everything a render needs, built once
// target: {p, kind, g, v, te?}; source: the state the photo was made with (or null: STANDARD). mono: the photo has no
// colour of its own (a black and white or toned photo): colour looks then start from its grey, not from its tint.
// cast: that black and white photo carries a tone. A target equal to the source is drawn as the photo is, except
// when the photo's tone is one the target does not make (a toned photo read as the untoned stock B&W setting): the
// B&W pass then sets the target's own tone.
const STD_P = D.defaults[D.std - 1].slice(0, 9);
export function makePlan(target, source, mono = false, cast = false) {
  const src = source || {p: STD_P, kind: D.defaults[D.std - 1][9], g: 0, v: 0};
  const t = target.kind & 3, sc = src.kind & 3;
  const asShot = !(mono && cast) || castOf(target) > 2;
  if (asShot && !target.te && !source?.te && t === sc && target.kind === src.kind && target.g === src.g
      && target.v === src.v && target.p.every((x, i) => x === src.p[i])) return {identity: true};
  const tone = toneLut(src.p, target.p);
  const plan = {identity: false, type: t, mono, grain: D.grain[target.g] || 0, vig: D.vignette[target.v] || 0,
    sharp: (target.p[P.sharp] - 2) - (src.p[P.sharp] - 2)};
  if (t === TYPE.bw) {
    const f = D.filters[target.p[P.s5]] || D.filters[0];
    plan.lr = channelLut(tone, f.wb[0]); plan.lg = channelLut(tone, f.wb[1]); plan.lb = channelLut(tone, f.wb[2]);
    plan.w = f.w.map(x => x / 512);
    let cc = [0, 0];
    if (target.te) cc = target.te;
    else if (target.p[P.s6] > 0) cc = D.tones[target.p[P.s6] - 1][target.p[P.s7]] || [0, 0];
    plan.cb = cc[0] / 4; plan.cr = cc[1] / 4;
    return plan;
  }
  const srcSat = sc === TYPE.colour ? D.sat[src.p[P.s5]] : sc === TYPE.select ? D.sat[src.p[P.s7]] : 1;
  if (t === TYPE.colour) {
    const s = (target.p[P.s6] - 3) - (sc === TYPE.colour ? src.p[P.s6] - 3 : 0);
    plan.lr = channelLut(tone, 1 + D.warm * s); plan.lg = channelLut(tone, 1); plan.lb = channelLut(tone, 1 - D.warm * s);
    plan.gain = D.sat[target.p[P.s5]] / srcSat;
    const words = D.matrix[target.kind >> 4] || [0, 0, 0];
    plan.d = matrixDelta([words[0], tintWord(words[1], target.p[P.s7] - 3), words[2]]);
    return plan;
  }
  plan.lr = plan.lg = plan.lb = channelLut(tone, 1);              // SELECT: base NORMAL, stock matrix
  plan.gain = D.sat[target.p[P.s7]] / srcSat;
  plan.sel = selTable(target.p[P.s5] + 1, target.p[P.s6]);
  return plan;
}

// ---------------------------------------------------------------- the pass
let Yb = new Float32Array(0), Cbb = new Float32Array(0), Crb = new Float32Array(0), Tb = new Float32Array(0);
function planes(n) {
  if (Yb.length < n) { Yb = new Float32Array(n); Cbb = new Float32Array(n); Crb = new Float32Array(n); Tb = new Float32Array(n); }
}

// src: RGBA bytes (w x h) of the photo at preview size; out: RGBA bytes of the same size. `previewWidth` is the
// width the photo is shown at, in device pixels, for the size-dependent finish.
export function render(plan, src, out, w, h) {
  const n = w * h;
  if (plan.identity) { out.set(src.subarray(0, n * 4)); return; }
  planes(n);
  const Y = Yb, CB = Cbb, CR = Crb, lr = plan.lr, lg = plan.lg, lb = plan.lb;
  if (plan.type === TYPE.bw) {
    const wr = plan.w[0], wg = plan.w[1], wb = plan.w[2];
    for (let i = 0, j = 0; i < n; i++, j += 4) Y[i] = wr * lr[src[j]] + wg * lg[src[j + 1]] + wb * lb[src[j + 2]];
    CB.fill(plan.cb, 0, n); CR.fill(plan.cr, 0, n);
  } else {
    const gain = plan.gain, d = plan.d, sel = plan.sel, mono = plan.mono;
    const M = D.model, lam = M.lambda, hiC = M.clip[1], J0 = M.J[0], J1 = M.J[1];
    for (let i = 0, j = 0; i < n; i++, j += 4) {
      let r0 = src[j], g0 = src[j + 1], b0 = src[j + 2];
      if (mono) { r0 = g0 = b0 = (77 * r0 + 150 * g0 + 29 * b0 + 128) >> 8; }
      const r = lr[r0], g = lg[g0], b = lb[b0];
      const y = 0.299 * r + 0.587 * g + 0.114 * b;
      let cb = (-0.168736 * r - 0.331264 * g + 0.5 * b) * gain;
      let cr = (0.5 * r - 0.418688 * g - 0.081312 * b) * gain;
      if (d) {
        const xr = y + lam * (r - y), xg = y + lam * (g - y), xb = y + lam * (b - y);
        let dr = d[0][0] * xr + d[0][1] * xg + d[0][2] * xb;
        let dg = d[1][0] * xr + d[1][1] * xg + d[1][2] * xb;
        let db = d[2][0] * xr + d[2][1] * xg + d[2][2] * xb;
        let t = xr + dr; dr = (t < Math.min(xr, 0) ? Math.min(xr, 0) : t > Math.max(xr, hiC) ? Math.max(xr, hiC) : t) - xr;
        t = xg + dg; dg = (t < Math.min(xg, 0) ? Math.min(xg, 0) : t > Math.max(xg, hiC) ? Math.max(xg, hiC) : t) - xg;
        t = xb + db; db = (t < Math.min(xb, 0) ? Math.min(xb, 0) : t > Math.max(xb, hiC) ? Math.max(xb, hiC) : t) - xb;
        cb += J0[0] * dr + J0[1] * dg + J0[2] * db;
        cr += J1[0] * dr + J1[1] * dg + J1[2] * db;
      }
      if (cb < -128) cb = -128; else if (cb > 127) cb = 127;
      if (cr < -128) cr = -128; else if (cr > 127) cr = 127;
      if (sel) {
        const ib = Math.min(255, Math.max(0, (cb + 128) | 0)) >> 3, ir = Math.min(255, Math.max(0, (cr + 128) | 0)) >> 3;
        const k = sel[ib * 32 + ir];
        if (k === 0) { cb = 0; cr = 0; } else if (k === 2) { cb *= 0.5; cr *= 0.5; }
      }
      Y[i] = y; CB[i] = cb; CR[i] = cr;
    }
  }
  finish(plan, Y, w, h);
  for (let i = 0, j = 0; i < n; i++, j += 4) {
    const y = Y[i], cb = CB[i], cr = CR[i];
    out[j] = y + 1.402 * cr;
    out[j + 1] = y - 0.344136 * cb - 0.714136 * cr;
    out[j + 2] = y + 1.772 * cb;
    out[j + 3] = 255;
  }
}

function finish(plan, Y, w, h) {
  const scale = Math.max(1, CAMERA_WIDTH / w);            // camera pixels per preview pixel
  const a = plan.sharp * 0.35 / scale;
  if (a && w >= 480) {                                     // too small to see on a thumbnail
    const T = Tb;
    for (let yy = 0; yy < h; yy++) {                       // 3 x 3 box: rows, then columns
      const o = yy * w;
      for (let x = 0; x < w; x++) T[o + x] = (Y[o + Math.max(0, x - 1)] + Y[o + x] + Y[o + Math.min(w - 1, x + 1)]) / 3;
    }
    for (let yy = 0; yy < h; yy++) {
      const up = Math.max(0, yy - 1) * w, o = yy * w, dn = Math.min(h - 1, yy + 1) * w;
      for (let x = 0; x < w; x++) {
        const blur = (T[up + x] + T[o + x] + T[dn + x]) / 3;
        Y[o + x] += a * (Y[o + x] - blur);
      }
    }
  }
  if (plan.grain) {
    const amp = plan.grain / scale;
    let s = 0x9e3779b9;                                    // fixed seed: the same grain on every render
    for (let i = 0, n = w * h; i < n; i++) {
      s ^= s << 13; s ^= s >>> 17; s ^= s << 5;
      const y = Y[i];
      if (y >= 16 && y <= 239) Y[i] = y + ((s >>> 0) / 4294967296 * 2 - 1) * amp;
    }
  }
  if (plan.vig) {
    const k = plan.vig, half = Math.hypot(w, h) / 2;
    const fy = new Float32Array(h);
    for (let yy = 0; yy < h; yy++) { const t = (yy + 0.5 - h / 2) / half; fy[yy] = 1 - k * t * t; }
    const fxr = new Float32Array(w);
    for (let x = 0; x < w; x++) { const t = (x + 0.5 - w / 2) / half; fxr[x] = 1 - k * t * t; }
    for (let yy = 0; yy < h; yy++) {
      const o = yy * w, f = fy[yy];
      for (let x = 0; x < w; x++) Y[o + x] *= f * fxr[x];
    }
  }
}

// The cast (8-bit chroma) a black and white state's TONING gives every pixel; 0 without toning.
export function castOf(st) {
  if (!st || (st.kind & 3) !== TYPE.bw) return 0;
  const cc = st.te || (st.p[P.s6] > 0 ? D.tones[st.p[P.s6] - 1][st.p[P.s7]] : null);
  return cc ? Math.hypot(cc[0], cc[1]) / 4 : 0;
}

// How much colour a photo has: the spread of its chroma (mono: none), and the cast a toned black and white photo
// carries (cast: its mean chroma, in 8-bit codes, is past 2).
export function colourOf(src, n) {
  let sb = 0, sr = 0, sbb = 0, srr = 0, k = 0;
  const step = Math.max(1, Math.floor(n / 20000));
  for (let i = 0; i < n; i += step) {
    const j = i * 4, r = src[j], g = src[j + 1], b = src[j + 2];
    const cb = -0.168736 * r - 0.331264 * g + 0.5 * b, cr = 0.5 * r - 0.418688 * g - 0.081312 * b;
    sb += cb; sr += cr; sbb += cb * cb; srr += cr * cr; k++;
  }
  if (!k) return {mono: true, cast: false};
  const mb = sb / k, mr = sr / k, vb = sbb / k - mb * mb, vr = srr / k - mr * mr;
  return {mono: Math.sqrt(Math.max(0, vb + vr)) < 3, cast: Math.hypot(mb, mr) > 2};
}

// The stock IMG SET values outside the 21 looks (the ghost entry), as the try-on can show them: B&W and B&W (TE)
// with the stock contrast 3 curve and the TE tint, HARD with the contrast 5 curve, the rest as the camera's NORMAL.
export function ghostState(value, b) {
  const p = STD_P.slice();
  if (value === 0) { p[P.curve] = 2; return {p, kind: 1 | (2 << 4), g: 0, v: 0}; }      // HARD: stock contrast 5
  if (value === 3 || value === 6) {
    const st = {p: [1, 4, 4, 4, 4, 0, 0, 2, 2], kind: 0, g: 0, v: 0};
    if (value === 6 && b) {
      const row = D.te[String(b[0x1a9])], lvl = Math.max(1, Math.min(5, b[0x1aa] || 3));
      if (row) st.te = row[lvl - 1];
    }
    return st;
  }
  return {p, kind: D.defaults[D.std - 1][9], g: 0, v: 0};
}

