// Numbers, units, times and exposure, written once. Views never format these by hand.
//
// Value, thin space (U+2009), unit. True minus (U+2212). Units only where the camera prints them or the number is
// ambiguous without one: EV, mm, MB, V, and s for durations the app measures. Shutter speed and aperture print bare,
// as the camera's LCD prints them.

export const THIN = ' ';
export const MINUS = '−';

// The order exposure values print in: the strip, every caption, the Overview caption. STATIC until the order the
// GR DIGITAL's own LCD uses at half-press is checked (/live?mode=2 captures the OSD); then change it here
// and only here.
export const EXPOSURE_ORDER = ['aperture', 'shutter', 'iso'];

const pad2 = n => String(n).padStart(2, '0');

// A signed number with a true minus: -0.3 -> "−0.3", 0.7 -> "+0.7", 0 -> "0.0" (EV the way the camera prints it).
export function signed(x, digits = 1) {
  const v = Number(x);
  if (!Number.isFinite(v)) return '';
  const t = Math.abs(v).toFixed(digits);
  return (v < 0 ? MINUS : v > 0 ? '+' : '') + t;
}

// Text from the camera or the settings map, with its hyphen-minus turned into a true minus where it is a sign.
export function trueMinus(t) { return String(t).replace(/(^|\s)-(?=\d)/g, '$1' + MINUS); }

export function withUnit(value, unit) { return unit ? value + THIN + unit : String(value); }

// F2.4, F5.6, F11: one decimal below F10.
export function aperture(f) {
  const v = Number(f);
  if (!Number.isFinite(v) || v <= 0) return '';
  return 'F' + (v < 10 ? (Math.round(v * 10) / 10).toFixed(1) : String(Math.round(v)));
}

// 1/250, 1/3, 1/2 and 1/1.6 below a second; from 1 s on, the double prime (U+2033) camera LCDs use for long exposures
// (1″, 1.3″, 2.5″). Both forms are STATIC (the GR DIGITAL's own LCD has not been checked).
export function shutter(t) {
  const v = Number(t);
  if (!Number.isFinite(v) || v <= 0) return '';
  if (v < 1) {
    const d = 1 / v;
    return '1/' + (d >= 10 ? Math.round(d) : Math.round(d * 10) / 10);
  }
  const r = Math.round(v * 10) / 10;
  return (Number.isInteger(r) ? String(r) : r.toFixed(1)) + '\u2033';
}

export function iso(n) {
  const v = Number(n);
  return Number.isFinite(v) && v > 0 ? 'ISO ' + Math.round(v) : '';
}

// {aperture, shutter, iso} from EXIF -> "F2.4 1/250 ISO 64" in EXPOSURE_ORDER; what is missing is left out.
export function exposure(x, sep = ' ') {
  if (!x) return '';
  const part = {aperture: aperture(x.fNumber), shutter: shutter(x.exposureTime), iso: iso(x.iso)};
  return EXPOSURE_ORDER.map(k => part[k]).filter(Boolean).join(sep);
}

// Dates and times: 2026/11/08 and 14:32 (24 h), joined with " · ". Accepts a Date, or the camera's
// "YYYY:MM:DD HH:MM:SS" / "YYYY-MM-DD HH:MM:SS" text.
export function parseStamp(s) {
  if (s instanceof Date) return s;
  const m = /^(\d{4})[:\-/](\d{2})[:\-/](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(String(s || ''));
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)) : null;
}
export function date(s) { const d = parseStamp(s); return d ? d.getFullYear() + '/' + pad2(d.getMonth() + 1) + '/' + pad2(d.getDate()) : ''; }
export function time(s) { const d = parseStamp(s); return d ? pad2(d.getHours()) + ':' + pad2(d.getMinutes()) : ''; }
export function dateTime(s) { const a = date(s), b = time(s); return a && b ? a + ' · ' + b : a || b; }

// A moment in a caption: the time alone when it fell today (local time), else date and time. "14:32", "2026/11/07 · 09:15".
export function when(s) {
  const d = typeof s === 'number' ? new Date(s) : parseStamp(s);  // also a Date.now number
  if (!d || isNaN(d)) return '';
  const n = new Date();
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate()
    ? time(d) : dateTime(d);
}

// Elapsed time counting from a phase start: 0:12, 1:02:10 past an hour.
export function elapsed(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? h + ':' + pad2(m) + ':' + pad2(r) : m + ':' + pad2(r);
}

// Sizes for Details: 96 MB, 812 KB, 4096 B.
export function bytes(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '';
  if (v >= 1e6) return withUnit((v / 1e6).toFixed(v >= 1e7 ? 0 : 1), 'MB');
  if (v >= 1e3) return withUnit(Math.round(v / 1e3), 'KB');
  return withUnit(v, 'B');
}

// Age of a value in seconds for the Stale state: "6 s ago".
export function ago(seconds) { return withUnit(Math.max(0, Math.round(seconds)), 's') + ' ago'; }
