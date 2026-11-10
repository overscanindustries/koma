// Routing (hash based, so no address ever collides with a bridge endpoint such as GET /settings), theme, and the
// phone breakpoint.
import {lsGet, lsSet} from './bridge.js';

export const NAV = [
  {id: 'shoot', label: 'Shoot'},
  {id: 'photos', label: 'Photos'},
  {id: 'looks', label: 'Looks'},
  {id: 'webcam', label: 'Webcam'},
  {id: 'settings', label: 'Settings'},
];
export const SETTINGS_NAV = [
  {id: 'camera', label: 'Camera menu'},
  {id: 'mode', label: 'Koma mode'},
  {id: 'backup', label: 'Back up & restore'},
  {id: 'copy', label: 'Copy to another GR'},
  {id: 'update', label: 'Update Koma'},
  {id: 'phone', label: 'Use on your phone'},
  {id: 'advanced', label: 'Advanced', divider: true},
];

function parse() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  // The app opens on Overview (home, reached by the wordmark; not a nav item), never on Shoot: live view and remote
  // control start only when the user opens Shoot.
  const top = NAV.some(n => n.id === parts[0]) || parts[0] === 'card' || parts[0] === 'home' ? parts[0] : 'home';
  const sub = top === 'settings' && SETTINGS_NAV.some(n => n.id === parts[1]) ? parts[1]
    : top === 'looks' && parts[1] ? parts[1] : '';  // #/looks/<look>: that look's editor
  return {top, sub};
}

export const route = $state(parse());
window.addEventListener('hashchange', () => { Object.assign(route, parse()); });
export function go(path) { location.hash = '#/' + path; }

// Theme: dark by default, light when the system asks for it, and the user's own choice once made.
const THEME_KEY = 'koma.theme';
export const theme = $state({choice: lsGet(THEME_KEY) || '', system: 'dark'});
const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
theme.system = mq && mq.matches ? 'light' : 'dark';
if (mq && mq.addEventListener) mq.addEventListener('change', e => { theme.system = e.matches ? 'light' : 'dark'; });
export function currentTheme() { return theme.choice === 'light' || theme.choice === 'dark' ? theme.choice : theme.system; }
export function toggleTheme() {
  theme.choice = currentTheme() === 'dark' ? 'light' : 'dark';
  lsSet(THEME_KEY, theme.choice);
}
// Settings > Appearance: 'dark', 'light' or 'system' (follow the computer).
export function setTheme(choice) {
  theme.choice = choice === 'light' || choice === 'dark' ? choice : '';
  lsSet(THEME_KEY, theme.choice);
}

// Phone layout at 720 px and below.
const phoneMq = window.matchMedia ? window.matchMedia('(max-width: 720px)') : null;
export const screen = $state({phone: !!(phoneMq && phoneMq.matches)});
if (phoneMq && phoneMq.addEventListener) phoneMq.addEventListener('change', e => { screen.phone = e.matches; });
