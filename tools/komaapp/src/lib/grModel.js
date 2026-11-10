// Parametric line-art model of the Ricoh GR DIGITAL (2005): front, back and top views as SVG path data, in
// millimetres (the viewBox is in mm). GrCamera.svelte draws it; this module is geometry only.
//
// Geometry sources:
//   - Body 107.0 x 58.0 x 25.0 mm (W x H x D, without protrusions): Ricoh, "GR DIGITAL Main Specifications"
//     (firmware/pc-software/docs-accessory-pages/english_r_dc_gr_gr_digital_specs.html); the Japanese manual's spec
//     page says the same (外形寸法 107.0 x 58.0 x 25.0 mm).
//   - Which control is where, and the names: the camera's English manual, "Names of parts" pp. 14-15 and "Using the
//     Mode Dial" / "Up-down Dial/ADJ. Dial" p. 16 (firmware/pc-software/docs-manual-en/GR_DIGITAL_E.pdf).
//   - Positions and sizes: measured on Ricoh's near-orthographic product photos in the GR DIGITAL brochure
//     (firmware/pc-software/docs-brochure/gr_digital.pdf p. 6), rendered at 600 dpi and calibrated to the 107 mm body
//     width: back view 11.1 px/mm, front view 8.0 px/mm. The 2.5-type LCD's active area (50.8 x 38.1 mm, 4:3) checked
//     the scale to within 1 mm. Top-plate positions come from the back and front silhouettes plus the manual's
//     drawings. Estimated accuracy: +-1 mm for the LCD, pad and buttons; +-2 mm for the top-plate controls; the lens
//     extension (21 mm in two stages) is an estimate from product photos, not a measurement.
//   - Not drawn on purpose: the RICOH and GR DIGITAL logos and every engraved word (no Ricoh marks; engraving is never
//     typeset); the speaker and the battery/card cover (underside, not in these views).
//
// Coordinates: x from the left end as the user holds the camera (flash end) for the back and top views; the front view
// is mirrored (grip on the left). y down. Back and front: y = 0 is the top edge, 58 the bottom. Top view: y = 0 is the
// front face (lens side, drawn at the top), 25 the back (LCD side), so the top view is what the user sees looking down
// at the camera in their hands.

export const BODY = {w: 107, h: 58, d: 25};

const f = n => Math.round(n * 100) / 100;

// Rounded rectangle; r is one radius or [top-left, top-right, bottom-right, bottom-left].
export function rr(x, y, w, h, r) {
  const [tl, tr, br, bl] = Array.isArray(r) ? r : [r, r, r, r];
  return `M${f(x + tl)} ${f(y)}H${f(x + w - tr)}` + (tr ? `A${tr} ${tr} 0 0 1 ${f(x + w)} ${f(y + tr)}` : '')
    + `V${f(y + h - br)}` + (br ? `A${br} ${br} 0 0 1 ${f(x + w - br)} ${f(y + h)}` : '')
    + `H${f(x + bl)}` + (bl ? `A${bl} ${bl} 0 0 1 ${f(x)} ${f(y + h - bl)}` : '')
    + `V${f(y + tl)}` + (tl ? `A${tl} ${tl} 0 0 1 ${f(x + tl)} ${f(y)}` : '') + 'Z';
}
export function circ(cx, cy, r) {
  return `M${f(cx - r)} ${f(cy)}A${r} ${r} 0 1 0 ${f(cx + r)} ${f(cy)}A${r} ${r} 0 1 0 ${f(cx - r)} ${f(cy)}Z`;
}
export function ln(x1, y1, x2, y2) { return `M${f(x1)} ${f(y1)}L${f(x2)} ${f(y2)}`; }
const rad = a => a * Math.PI / 180;
function pt(cx, cy, r, a) { return [cx + r * Math.cos(rad(a)), cy + r * Math.sin(rad(a))]; }
// Annular sector between radii r0 < r1 from angle a0 to a1 (degrees, 0 = right, clockwise on screen).
export function sector(cx, cy, r0, r1, a0, a1) {
  const [x0, y0] = pt(cx, cy, r1, a0), [x1, y1] = pt(cx, cy, r1, a1), [x2, y2] = pt(cx, cy, r0, a1), [x3, y3] = pt(cx, cy, r0, a0);
  const large = a1 - a0 > 180 ? 1 : 0;
  return `M${f(x0)} ${f(y0)}A${r1} ${r1} 0 ${large} 1 ${f(x1)} ${f(y1)}L${f(x2)} ${f(y2)}A${r0} ${r0} 0 ${large} 0 ${f(x3)} ${f(y3)}Z`;
}
// Radial ticks (knurling seen from above, or around a wheel face).
function ticks(cx, cy, r0, r1, from, to, step) {
  let d = '';
  for (let a = from; a <= to + 1e-6; a += step) { const [x0, y0] = pt(cx, cy, r0, a), [x1, y1] = pt(cx, cy, r1, a); d += ln(x0, y0, x1, y1); }
  return d;
}
// Parallel knurl lines across a wheel seen edge-on.
function knurl(x0, x1, y0, y1, step) {
  let d = '';
  for (let x = x0; x <= x1 + 1e-6; x += step) d += ln(x, y0, x, y1);
  return d;
}

// ------------------------------------------------------------------------------------------------ measured layout
// User-view (back) coordinates, mm. Back face: from the top-back edge. Top plate: y from the front face.
const L = {
  // back face
  bezel: [1.6, 6.8, 59.8, 49.8, 2.4],
  lcd: [5.6, 12.45, 50.8, 38.1, 0.8],
  lamp: [55.9, 4.8, 3.0, 1.2],
  adj: [66.9, 7.2, 12.8, 3.8, 0.8],
  tw: [94.9, 7.8, 6.8, 12.2, 1.4], twSplit: 13.9,
  play: [68.6, 22.8, 3.0],
  pad: [79.6, 36.4], padOuter: 10.6, padInner: 5.4, ok: 3.7,
  del: [68.4, 49.8, 3.0],
  disp: [90.1, 49.8, 3.0],
  // top plate (x: user view, y: from the front face)
  dial: [80.4, 15.8, 7.6],
  shutter: [71.0, 6.4, 4.6],
  power: [57.0, 12.6, 2.5],
  unlock: [86.4, 4.4, 1.9],
  shoe: [27.5, 3.0, 21.5, 19.0],
  flash: [2.5, 0.6, 22.0, 9.0],
  updown: [89.4, 96.4],          // the up-down dial's rim, x range (a wheel in the front-face plane)
  // front face (front view coordinates, grip on the left)
  lens: [71.5, 34.0], ring: 22.0, ringStep: 19.4, barrier: 11.0,
  barrel1: 16.0, barrel2: 12.4, frontRing: 9.2, glass: 6.4, glassIn: 3.4,
  ext1: 10.0, ext2: 11.0,        // lens extension, stage 1 and 2 (estimate)
  ringProud: 1.8,                // the lens ring stands 1.8 mm proud of the front face
  grip: 39.6,                    // the grip seam, from the grip end
  afWin: [44.0, 3.4, 13.0, 4.8, 1.0],
  afLight: [42.6, 11.4, 1.6],
  mic: [31.4, 6.4, 4.2, 1.0],
  flashFace: [82.6, 0.8, 22.4, 4.4, 1.0],
  // heights above the top plate, seen from the front or back
  hDial: 3.6, hShutter: 1.3, hPower: 0.7, hShoe: 2.0, hUpdown: 2.2,
};
const mx = x => BODY.w - x; // mirror a user-view x into the front view

// A control: id, its camera name (the manual's), the parts it draws, the outline the highlight follows, the callout
// target, the hold-ring radius and the animation it takes (press | turn | slide).
function control(id, name, parts, opts = {}) {
  return {id, name, parts, hl: opts.hl || parts.filter(p => p.w === 'major').map(p => p.d).join(''), at: opts.at,
    ring: opts.ring || 0, anim: opts.anim || 'press', origin: opts.origin || null, spin: opts.spin || []};
}
const M = d => ({d, w: 'major'});
const D = d => ({d, w: 'detail'});

// ------------------------------------------------------------------------------------------------ back view
function back() {
  const [px, py] = L.pad, po = L.padOuter, pi = L.padInner;
  const seg = (a0, a1) => sector(px, py, pi, po, a0, a1);
  const [tx, ty, tw, th, tr] = L.tw;
  const tHalf = `M${f(tx)} ${f(L.twSplit)}V${f(ty + tr)}A${tr} ${tr} 0 0 1 ${f(tx + tr)} ${f(ty)}H${f(tx + tw - tr)}A${tr} ${tr} 0 0 1 ${f(tx + tw)} ${f(ty + tr)}V${f(L.twSplit)}Z`;
  const wHalf = `M${f(tx)} ${f(L.twSplit)}H${f(tx + tw)}V${f(ty + th - tr)}A${tr} ${tr} 0 0 1 ${f(tx + tw - tr)} ${f(ty + th)}H${f(tx + tr)}A${tr} ${tr} 0 0 1 ${f(tx)} ${f(ty + th - tr)}Z`;
  const [dx, , dr] = L.dial, [sx, , sr] = L.shutter, [pwx, , pwr] = L.power, [ux0, ux1] = L.updown;
  const top = (x0, x1, h, rc = 0.6) => `M${f(x0)} 0V${f(-h + rc)}Q${f(x0)} ${f(-h)} ${f(x0 + rc)} ${f(-h)}H${f(x1 - rc)}Q${f(x1)} ${f(-h)} ${f(x1)} ${f(-h + rc)}V0`;
  const dialL = dx - dr, dialR = dx + dr;
  const [lx, ly, lw, lh] = L.lamp, [ax, ay, aw, ah, ar] = L.adj;
  return {
    vb: [-2.5, -5, 112, 65],
    body: [
      M(rr(0, 0, BODY.w, BODY.h, [4.5, 5.5, 5.0, 4.0])),
      D(rr(...L.bezel)),
      D(`M91.6 6V27C91.6 33 95.2 36 95.2 42V56.6`),                                   // thumb-rest seam
      D(`M0 3.6H-1.2Q-1.6 3.6 -1.6 4V8.2Q-1.6 8.6 -1.2 8.6H0M107 3.6H108.2Q108.6 3.6 108.6 4V8.2Q108.6 8.6 108.2 8.6H107`), // strap eyelets
      D(ln(px + pi * 0.7071, py - pi * 0.7071, px + po * 0.7071, py - po * 0.7071) + ln(px + pi * 0.7071, py + pi * 0.7071, px + po * 0.7071, py + po * 0.7071)
        + ln(px - pi * 0.7071, py + pi * 0.7071, px - po * 0.7071, py + po * 0.7071) + ln(px - pi * 0.7071, py - pi * 0.7071, px - po * 0.7071, py - po * 0.7071)), // pad seams
      D(circ(px, py, pi)),
      M(circ(px, py, po)),
      M(top(L.shoe[0], L.shoe[0] + L.shoe[2], L.hShoe)), D(ln(L.shoe[0] + 2.5, -1.0, L.shoe[0] + L.shoe[2] - 2.5, -1.0)), // hot shoe
    ],
    controls: [
      control('lcd', 'LCD monitor', [{d: rr(...L.lcd), w: 'major', lcd: true}], {at: [L.lcd[0] + L.lcd[2] / 2, L.lcd[1] + L.lcd[3] / 2]}),
      control('lamp', 'Auto Focus/Flash lamp', [M(rr(lx - lw / 2, ly - lh / 2, lw, lh, lh / 2))], {at: [lx, ly], ring: 3.0}),
      control('adj', 'ADJ. dial', [M(rr(ax, ay, aw, ah, ar)), {d: knurl(ax + 1.0, ax + aw - 1.0, ay + 0.7, ay + ah - 0.7, 0.9), w: 'detail', spin: true}],
        {at: [ax + aw / 2, ay + ah / 2], ring: 7.6, anim: 'slide'}),
      control('t', 'Enlarged view button', [M(tHalf)], {at: [tx + tw / 2, (ty + L.twSplit) / 2], ring: 4.6}),
      control('w', 'Thumbnail display button', [M(wHalf)], {at: [tx + tw / 2, (L.twSplit + ty + th) / 2], ring: 4.6}),
      control('play', 'Playback button', [M(circ(...L.play)), D(circ(L.play[0], L.play[1], 2.1))], {at: [L.play[0], L.play[1]], ring: 4.6}),
      control('up', '▲ (flash) button', [M(seg(-135, -45))], {at: [px, py - (pi + po) / 2], ring: 0}),
      control('right', '▶ button', [M(seg(-45, 45))], {at: [px + (pi + po) / 2, py]}),
      control('down', '▼ (macro) button', [M(seg(45, 135))], {at: [px, py + (pi + po) / 2]}),
      control('left', '◀ (quick review) button', [M(seg(135, 225))], {at: [px - (pi + po) / 2, py]}),
      control('menu-ok', 'MENU/OK button', [M(circ(px, py, L.ok))], {at: [px, py], ring: 5.2}),
      control('delete', 'Delete / self-timer button', [M(circ(...L.del)), D(circ(L.del[0], L.del[1], 2.1))], {at: [L.del[0], L.del[1]], ring: 4.6}),
      control('disp', 'DISP. button', [M(circ(...L.disp)), D(circ(L.disp[0], L.disp[1], 2.1))], {at: [L.disp[0], L.disp[1]], ring: 4.6}),
      // top-plate controls in silhouette
      control('mode-dial', 'Mode dial', [M(top(dialL, dialR, L.hDial, 0.7)), D(ln(dialL, -L.hDial + 0.9, dialR, -L.hDial + 0.9)),
        {d: knurl(dialL + 0.8, dialR - 0.8, -L.hDial + 1.4, -0.3, 0.8), w: 'detail', spin: true}], {at: [dx, -L.hDial / 2], anim: 'slide'}),
      control('shutter', 'Shutter release button', [M(`M${f(sx - sr)} 0V-0.8Q${f(sx - sr)} ${f(-L.hShutter)} ${f(sx - sr + 0.5)} ${f(-L.hShutter)}H${f(dialL)}`)],
        {at: [(sx - sr + dialL) / 2, -L.hShutter / 2]}),
      control('power', 'Power button', [M(top(pwx - pwr, pwx + pwr, L.hPower, 0.3))], {at: [pwx, -L.hPower / 2]}),
      control('updown-dial', 'Up-down dial', [M(`M${f(ux0)} 0Q${f((ux0 + ux1) / 2)} ${f(-2 * L.hUpdown + 0.6)} ${f(ux1)} 0`)], {at: [(ux0 + ux1) / 2, -0.8], anim: 'slide'}),
    ],
  };
}

// ------------------------------------------------------------------------------------------------ front view
function front() {
  const [cx, cy] = L.lens;
  const [dx, , dr] = L.dial, [sx, , sr] = L.shutter, [pwx, , pwr] = L.power, [ux0, ux1] = L.updown;
  const top = (x0, x1, h, rc = 0.6) => `M${f(x0)} 0V${f(-h + rc)}Q${f(x0)} ${f(-h)} ${f(x0 + rc)} ${f(-h)}H${f(x1 - rc)}Q${f(x1)} ${f(-h)} ${f(x1)} ${f(-h + rc)}V0`;
  // mirrored top-plate ranges
  const dial0 = mx(dx + dr), dial1 = mx(dx - dr), sh0 = mx(sx + sr), sh1 = mx(sx - sr);
  const pw0 = mx(pwx + pwr), pw1 = mx(pwx - pwr), sh = [mx(L.shoe[0] + L.shoe[2]), mx(L.shoe[0])];
  // the up-down dial: a wheel in the front-face plane, its rim showing above the top edge
  const uc = (mx(ux1) + mx(ux0)) / 2, ur = 5.0, ucy = ur - L.hUpdown, half = Math.sqrt(ur * ur - ucy * ucy);
  const g = L.grip;
  return {
    vb: [-2.5, -5, 112, 65],
    body: [
      M(rr(0, 0, BODY.w, BODY.h, [5.5, 4.5, 4.0, 5.0])),
      // grip seam: the grip wraps round the end, so the seam runs from the outline's top-left arc to its bottom-left arc
      D(`M0.05 4.8H${f(g - 3.6)}Q${f(g)} 4.8 ${f(g)} 8.4V52.6Q${f(g)} 56.2 ${f(g - 3.6)} 56.2H1.16`),
      D(`M0 3.6H-1.2Q-1.6 3.6 -1.6 4V8.2Q-1.6 8.6 -1.2 8.6H0M107 3.6H108.2Q108.6 3.6 108.6 4V8.2Q108.6 8.6 108.2 8.6H107`),
      M(top(sh[0], sh[1], L.hShoe)), D(ln(sh[0] + 2.5, -1.0, sh[1] - 2.5, -1.0)),
      // retracted lens: the barrier closed. Its blade seam is not drawn: the photos do not resolve it, and a line across the
      // lens reads as a prohibition slash.
      {d: circ(cx, cy, L.barrier), w: 'major', group: 'retracted'},
      // extended lens: the barrels and the glass
      {d: circ(cx, cy, L.barrel1) + circ(cx, cy, L.barrel2), w: 'major', group: 'extended'},
      {d: circ(cx, cy, L.frontRing) + circ(cx, cy, L.glass) + circ(cx, cy, L.glassIn), w: 'detail', group: 'extended'},
    ],
    controls: [
      control('lens', 'Lens', [M(circ(cx, cy, L.ring)), D(circ(cx, cy, L.ringStep))], {at: [cx + L.ring * 0.7071, cy - L.ring * 0.7071], hl: circ(cx, cy, L.ring)}),
      control('af-window', 'AF window', [D(rr(...L.afWin)), D(circ(48.3, 5.8, 1.3) + circ(52.7, 5.8, 1.3))],
        {at: [L.afWin[0] + L.afWin[2] / 2, L.afWin[1] + L.afWin[3] / 2], hl: rr(...L.afWin)}),
      control('af-light', 'AF auxiliary light', [D(circ(...L.afLight)), D(circ(L.afLight[0], L.afLight[1], 0.9))],
        {at: [L.afLight[0], L.afLight[1]], hl: circ(...L.afLight)}),
      control('mic', 'Microphone', [D(rr(...L.mic, 0.5))], {at: [L.mic[0] + L.mic[2] / 2, L.mic[1] + L.mic[3] / 2], hl: rr(...L.mic, 0.5)}),
      control('flash', 'Flash', [D(rr(...L.flashFace))], {at: [L.flashFace[0] + L.flashFace[2] / 2, L.flashFace[1] + L.flashFace[3] / 2], hl: rr(...L.flashFace)}),
      control('updown-dial', 'Up-down dial', [M(`M${f(uc - half)} 0A${ur} ${ur} 0 0 1 ${f(uc + half)} 0`),
        {d: ticks(uc, ucy, ur - 0.8, ur, -150, -30, 9), w: 'detail', spin: true}], {at: [uc, -L.hUpdown / 2], anim: 'turn', origin: [uc, ucy]}),
      control('mode-dial', 'Mode dial', [M(`M${f(dial0)} 0V${f(-L.hDial + 0.7)}Q${f(dial0)} ${f(-L.hDial)} ${f(dial0 + 0.7)} ${f(-L.hDial)}H${f(dial1 - 0.7)}Q${f(dial1)} ${f(-L.hDial)} ${f(dial1)} ${f(-L.hDial + 0.7)}V${f(-L.hShutter)}`),
        D(ln(dial0, -L.hDial + 0.9, dial1, -L.hDial + 0.9)), {d: knurl(dial0 + 0.8, sh0 - 0.6, -L.hDial + 1.4, -0.3, 0.8), w: 'detail', spin: true}],
        {at: [(dial0 + sh0) / 2, -L.hDial / 2], anim: 'slide'}),
      control('shutter', 'Shutter release button', [M(top(sh0, sh1, L.hShutter, 0.5))], {at: [(sh0 + sh1) / 2, -L.hShutter / 2]}),
      control('power', 'Power button', [M(top(pw0, pw1, L.hPower, 0.3))], {at: [(pw0 + pw1) / 2, -L.hPower / 2]}),
    ],
  };
}

// ------------------------------------------------------------------------------------------------ top view
function topView() {
  const [cx] = L.lens, lx = mx(cx);           // the lens axis in user-view x
  const [dx, dy, dr] = L.dial, [sx, sy, sr] = L.shutter, [pwx, pwy, pwr] = L.power, [ux0, ux1] = L.updown;
  const [hx, hy, hw, hh] = L.shoe;
  const ringW = L.ring, r1 = L.barrel1, r2 = L.barrel2, p = L.ringProud, e1 = p + L.ext1, e2 = e1 + L.ext2;
  return {
    vb: [-2.5, -26.5, 112, 54],
    vbTight: [-2.5, -4.5, 112, 32],   // the lens retracted: no room needed above the ring
    body: [
      // outline with the grip standing 1.4 mm proud at the front-right
      M(`M3 0H66.2Q67.4 0 67.6 -0.8Q67.9 -1.4 69 -1.4H101A6 6 0 0 1 107 4.6V21.5A3.5 3.5 0 0 1 103.5 25H3A3 3 0 0 1 0 22V3A3 3 0 0 1 3 0Z`),
      D(`M-0 16.4H-1.3Q-1.7 16.4 -1.7 16.8V21.2Q-1.7 21.6 -1.3 21.6H0M107 16.4H108.3Q108.7 16.4 108.7 16.8V21.2Q108.7 21.6 108.3 21.6H107`), // eyelets
      D(rr(hx, hy, hw, hh, 1.0)), D(ln(hx + 2.7, hy + 1.6, hx + 2.7, hy + hh - 1.6) + ln(hx + hw - 2.7, hy + 1.6, hx + hw - 2.7, hy + hh - 1.6)),
      D(circ(hx + hw / 2, hy + hh / 2 - 0.1, 1.8)),
      // the lens: the ring, then the two barrels when extended
      {d: `M${f(lx - ringW)} 0V${f(-p + 0.6)}Q${f(lx - ringW)} ${f(-p)} ${f(lx - ringW + 0.6)} ${f(-p)}H${f(lx + ringW - 0.6)}Q${f(lx + ringW)} ${f(-p)} ${f(lx + ringW)} ${f(-p + 0.6)}V0`, w: 'major'},
      {d: `M${f(lx - r1)} ${f(-p)}V${f(-e1)}H${f(lx + r1)}V${f(-p)}M${f(lx - r2)} ${f(-e1)}V${f(-e2 + 0.8)}Q${f(lx - r2)} ${f(-e2)} ${f(lx - r2 + 0.8)} ${f(-e2)}H${f(lx + r2 - 0.8)}Q${f(lx + r2)} ${f(-e2)} ${f(lx + r2)} ${f(-e2 + 0.8)}V${f(-e1)}`, w: 'major', group: 'extended'},
      {d: ln(lx - r1, -e1 + 0.9, lx + r1, -e1 + 0.9) + ln(lx - r2, -e2 + 1.3, lx + r2, -e2 + 1.3), w: 'detail', group: 'extended'},
    ],
    controls: [
      control('flash', 'Flash', [D(rr(...L.flash, 1.2))], {at: [L.flash[0] + L.flash[2] / 2, L.flash[1] + L.flash[3] / 2], hl: rr(...L.flash, 1.2)}),
      control('hot-shoe', 'Hot shoe', [], {at: [hx + hw / 2, hy + hh / 2], hl: rr(hx, hy, hw, hh, 1.0)}),
      control('power', 'Power button', [M(circ(pwx, pwy, pwr)), D(circ(pwx, pwy, 1.5))], {at: [pwx, pwy], ring: pwr + 1.4}),
      control('shutter', 'Shutter release button', [M(circ(sx, sy, sr)), D(circ(sx, sy, sr - 1.1))], {at: [sx, sy], ring: sr + 1.4}),
      control('mode-dial', 'Mode dial', [M(circ(dx, dy, dr)), D(circ(dx, dy, dr - 2.0)), {d: ticks(dx, dy, dr - 1.2, dr, 0, 350, 10), w: 'detail', spin: true}],
        {at: [dx, dy], ring: dr + 1.4, anim: 'turn', origin: [dx, dy]}),
      control('unlock', 'Unlock button', [M(circ(...L.unlock))], {at: [L.unlock[0], L.unlock[1]], ring: L.unlock[2] + 1.4}),
      control('updown-dial', 'Up-down dial', [M(rr(ux0, -0.6, ux1 - ux0, 3.4, 0.6)), {d: knurl(ux0 + 0.8, ux1 - 0.8, -0.1, 2.3, 0.8), w: 'detail', spin: true}],
        {at: [(ux0 + ux1) / 2, 1.1], anim: 'slide'}),
      control('usb-door', 'USB and AV-out terminal cover', [M(`M107 3.4H107.7Q108.2 3.4 108.2 3.9V14.1Q108.2 14.6 107.7 14.6H107`)], {at: [108.2, 9.0]}),
      control('lens', 'Lens', [], {at: [lx - ringW * 0.5, -p], hl: `M${f(lx - ringW)} 0V${f(-p)}H${f(lx + ringW)}V0`}),
    ],
  };
}

export const VIEWS = {back: back(), front: front(), top: topView()};

// Every control id per view, for the gallery and the README.
export const CONTROL_IDS = Object.fromEntries(Object.entries(VIEWS).map(([v, m]) => [v, m.controls.map(c => c.id)]));

export function controlOf(view, id) { return VIEWS[view] ? VIEWS[view].controls.find(c => c.id === id) || null : null; }
