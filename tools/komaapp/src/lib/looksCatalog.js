// The 21 looks of the IMG SET list in the camera's display order (GR III order). `id` is the stored look id (bwrecipes: 1..20 in the record store, STANDARD 21), `name` the camera's string,
// `group` the gallery section, `line` the one-liner. Display order only: ids never move.
// Same shape as src/lib/looks.js ({id, name, group, line}) plus `slug` for the editor's address.
import {LOOKS_DATA as D} from './looksData.js';

const LINES = {
  'STANDARD': "The camera's standard rendering",
  'MONO': 'Black and white, no filter',
  'SOFT MONO': 'Gentle black and white, soft contrast',
  'HARD MONO': 'Hard black and white, a faint blue tone',
  'HICON B&W': 'High-contrast black and white',
  'SHINJUKU': 'Hard contrast, deep blacks, red filter',
  'FILM 400': "A 400 film's curve, yellow filter",
  'FINE 100': "A fine-grain 100 film's curve",
  'DENSE': 'A long, dense scale of greys',
  'NOIR': 'Low key, red filter',
  'MATTE': 'Flat, lifted shadows, soft whites',
  'SELECT': 'Black and white with one color kept',
  // POSITIVE and BLEACH follow the GRD IV refit: "warm hues kept, cool
  // hues muted" and "teal", not the earlier wording
  'POSITIVE': 'Punchy contrast, warm hues kept, cool hues muted',
  'NEGATIVE': 'Gentle, muted, slightly cool',
  'SLIDE 50': 'Deep contrast, strong color, slightly warm',
  'WARM NEG': 'Soft and warm, a faint magenta cast',
  'BLEACH': 'High contrast, drained color, a teal cast',
  'RETRO': 'Warm, faded, low contrast',
  'CUSTOM 1': 'A look of its own, starting from MONO',
  'CUSTOM 2': 'A look of its own, starting from MONO',
  'CUSTOM 3': 'A look of its own, starting from MONO',
};

function groupOf(id) {
  if (id === D.std) return 'standard';
  if (id >= 18) return 'custom';
  return D.defaults[id - 1][9] & 1 ? 'color' : 'mono';    // type 1 = color; B&W (0) and SELECT (2) are mono
}

export function slugOf(name) {
  return name.toLowerCase().replace(/&/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

export const LOOKS = D.order.map(id => {
  const name = D.names[id - 1];
  return {id, name, group: groupOf(id), line: LINES[name] || '', slug: slugOf(name)};
});

export const GROUPS = ['standard', 'mono', 'color', 'custom'];

export function lookById(id) { return LOOKS.find(l => l.id === id) || null; }
export function lookBySlug(slug) { return LOOKS.find(l => l.slug === slug) || null; }
