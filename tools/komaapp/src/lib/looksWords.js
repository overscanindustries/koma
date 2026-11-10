// Every string the Looks area shows, in one place for the copy audit (lives beside
// words.js). Camera strings keep the camera's spelling; prose is sentence case;
// "the camera", never "your camera"; titles take no period, sentences do.
import {plural} from './words.js';

export const W = {
  title: 'Looks',
  approx: 'Approximate',

  // what the cards are drawn on (the APPROXIMATE tag follows this line, so it qualifies the drawing). `time` is the
  // photo's time, with its date when it was not taken today ("14:32", "2026/11/02 · 14:32").
  onLast: time => (time ? 'Drawn on the last photo, taken ' + time + '.' : 'Drawn on the last photo.'),
  onSample: 'Drawn on a sample photo.',
  // why the sample, when the reason is not the camera being busy (that line is words.js BUSY, with its action)
  sampleWhy: {
    none: 'The card holds no photos.',
    error: 'The last photo could not be read.',
    card: '',
  },
  // the last photo is black and white: the gallery draws on the sample, and says why under Details
  whySample: 'Why a sample',
  whySampleText: "The last photo is black and white, so the filters and SELECT's color would not show.",
  readAgain: 'Read again',               // one verb for every read that failed: the photo, the settings, the Apply check
  readingPhoto: 'Reading the last photo.',
  noPhoto: 'No photo to draw the looks on.',
  readingLooks: 'Reading the looks from the camera.',

  groups: {mono: 'Monochrome', color: 'Color', custom: 'Custom'},
  tagAdj: 'ADJ',
  tagEdited: 'Edited',
  ghostLine: "The camera's stock setting, not a look",

  // the selected look, pinned at the bottom of the gallery
  galleryLabel: 'Selected look',
  onCamera: 'On the camera.',
  onCameraEdited: 'On the camera, without the edits made here.',
  restarts: 'The camera restarts to load it.',
  editedRestarts: 'The camera restarts to load it with the edits made here.',
  ghostState: 'On the camera. Nothing to edit.',
  needsCamera: 'Connect the camera to set a look.',
  editLook: 'Edit look',
  setOnCamera: 'Set on the camera',
  setting: 'Setting it on the camera.',
  isSet: 'Now on the camera.',
  a11yOn: 'On the camera',
  a11yAdj: 'ADJ lever on',
  a11yEdited: 'Edited, not applied',

  // the selected look's ADJ LEVER flag, in the bar's sentence
  onAdj: 'On the ADJ lever.',

  // no looks on this camera (the action is onboard.svelte.js setupEntry: Set up Koma / Continue setup)
  stockLabel: 'Official firmware',
  stockText: 'The looks need Koma on the camera.',
  // the Koma on the camera is older than these looks: the fact alone. An Update Koma link returns only once the bundled
  // package carries the Look IA (no package does today).
  oldStore: 'The Koma on the camera stores looks in an older form.',

  // the Apply sheet, for a look: its title and its done line (the phases are apply.svelte.js STEPS; the keep line
  // after setting a look is words.js STATE.keepsLook). `to`: one look's name, or "2 looks"
  settingTitle: name => 'Setting ' + name + ' on the camera',
  applyingTitle: (n, to) => 'Applying ' + plural(n, 'change', 'changes') + ' to ' + to,
  isActive: name => name + ' is active.',
  appliedTo: to => 'Applied to ' + to + '.',
  keepsChanges: n => 'Turn the camera off once so it keeps ' + (n === 1 ? 'the change.' : 'the changes.'),
  looksN: m => plural(m, 'look', 'looks'),

  // the editor
  back: 'Looks',
  asShot: 'As shot',
  sample: 'Sample',
  splitLabel: 'Before and after',
  adjHelp: 'The ADJ lever steps through the looks set to ON.',
  // the Koma before the Look IA (builds before the Look IA): no ADJ LEVER setting, STANDARD is the camera's stock NORMAL
  adjOld: 'The Koma on the camera has no ADJ LEVER setting.',
  stdOld: 'The Koma on the camera keeps STANDARD as the stock NORMAL.',
  toneOff: 'TONING is OFF.',
  stock: 'Return to stock values',
  stockSays: name => 'Puts ' + name + ' back to its stock values here. Apply sends them.',
  atStock: 'At stock values.',
  // the area-wide pending bar: "3 changes in 2 looks"; one look: "2 changes to NOIR"
  changes: n => plural(n, 'change', 'changes'),
  changesIn: (n, m, name) => plural(n, 'change', 'changes') + (m === 1 ? ' to ' + name : ' in ' + plural(m, 'look', 'looks')),
  review: 'Review',
  apply: 'Apply to camera',
  // n: the number of changes ("1 change... load it", "2 changes... load them")
  applyRestarts: n => 'The camera restarts to load ' + (n === 1 ? 'it.' : 'them.'),
  applyStores: (n, other) => 'The camera restarts to store ' + (n === 1 ? 'it. ' : 'them. ') + other + ' stays in use.',
  reviewTitle: (n, m, name) => plural(n, 'change', 'changes') + (m === 1 ? ' to ' + name : ' in ' + plural(m, 'look', 'looks')),
  undo: 'Undo',
  undoAll: 'Undo all',
  to: 'to',
  applied: 'Applied. Every value matches what was sent.',
  editNeedsCamera: 'Connect the camera to apply edits.',

  details: 'Details',
  detail: {
    photo: 'Last photo',
    size: 'Size',
    model: 'Try-on',
    modelText: "Drawn in this browser from Koma's own tone curves, filters, toning, color matrix, grain and vignette",
  },

  // the Camera menu's IMG SET row on a Koma camera (views/settings/CameraMenu.svelte)
  imgSetHelp: 'The look the camera shoots with.',
  changeInLooks: 'Change in Looks',
};

// Camera option strings with a true minus: "-2" -> "−2".
export function opt(t) { return String(t).replace(/^-(?=\d)/, '−'); }
