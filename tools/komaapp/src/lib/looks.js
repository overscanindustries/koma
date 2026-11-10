// The 21 looks, in IMG SET order. The one canonical array:
// Looks, Overview and onboarding read the order, the group and the one-liner from here. Presentation data only. The
// ADJ LEVER flag and every parameter come from the camera's settings block at run time, never from this file.
//   id     the stored look id (settings map field recipe_rcp_active, 0x8c0; STANDARD is id 21). The camera writes plain NORMAL with no look id while STANDARD is unedited.
//   name   the camera's string
//   group  'standard' | 'mono' | 'color' | 'custom'
//   line   the one-liner (a fragment: no terminal period, no trademark, no photographer's name, no engineering number)
export const LOOKS = [
  {id: 21, name: 'STANDARD', group: 'standard', line: "The camera's standard rendering"},
  {id: 1, name: 'MONO', group: 'mono', line: 'Black and white, no filter'},
  {id: 5, name: 'SOFT MONO', group: 'mono', line: 'Gentle black and white, soft contrast'},
  {id: 4, name: 'HARD MONO', group: 'mono', line: 'Hard black and white, a faint blue tone'},
  {id: 3, name: 'HICON B&W', group: 'mono', line: 'High-contrast black and white'},
  {id: 2, name: 'SHINJUKU', group: 'mono', line: 'Hard contrast, deep blacks, red filter'},
  {id: 6, name: 'FILM 400', group: 'mono', line: "A 400 film's curve, yellow filter"},
  {id: 7, name: 'FINE 100', group: 'mono', line: "A fine-grain 100 film's curve"},
  {id: 8, name: 'DENSE', group: 'mono', line: 'A long, dense scale of greys'},
  {id: 9, name: 'NOIR', group: 'mono', line: 'Low key, red filter'},
  {id: 10, name: 'MATTE', group: 'mono', line: 'Flat, lifted shadows, soft whites'},
  {id: 11, name: 'SELECT', group: 'mono', line: 'Black and white with one color kept'},
  {id: 12, name: 'POSITIVE', group: 'color', line: 'Punchy contrast, rich blues and greens'},
  {id: 13, name: 'NEGATIVE', group: 'color', line: 'Gentle, muted, slightly cool'},
  {id: 17, name: 'SLIDE 50', group: 'color', line: 'Deep contrast, strong color, slightly warm'},
  {id: 16, name: 'WARM NEG', group: 'color', line: 'Soft and warm, a faint magenta cast'},
  {id: 14, name: 'BLEACH', group: 'color', line: 'High contrast, drained color, silvery'},
  {id: 15, name: 'RETRO', group: 'color', line: 'Warm, faded, low contrast'},
  {id: 18, name: 'CUSTOM 1', group: 'custom', line: 'A look of its own, starting from MONO'},
  {id: 19, name: 'CUSTOM 2', group: 'custom', line: 'A look of its own, starting from MONO'},
  {id: 20, name: 'CUSTOM 3', group: 'custom', line: 'A look of its own, starting from MONO'},
];

// Group heads for Looks: STANDARD stands alone with no head; a head over one item breaks kanso.
export const LOOK_GROUPS = [
  {id: 'standard', label: ''},
  {id: 'mono', label: 'Monochrome'},
  {id: 'color', label: 'Color'},
  {id: 'custom', label: 'Custom'},
];

export const lookById = id => LOOKS.find(l => l.id === Number(id)) || null;
export const lookByName = name => LOOKS.find(l => l.name === String(name || '').trim()) || null;
