// Settings > Camera menu mirrors the camera's own menu, in the layout its MENU LAYOUT setting picks (Koma mode byte
// 0x8bb: 0 5-TAB, 1 STOCK+KOMA, 2 GR II; tools/koma/features/menu5.py). GR II is used when the setting cannot be read
// (a stock camera, a card file without Koma, an unknown value). Labels are the map's, which are the camera's own
// spellings, except where a layout renames a row on the camera.
//
// GR II (with the Look IA: one IMG SET picker, no
// RECIPE / EDIT RECIPE rows): SHOOTING, KEY CUST., SETUP.
// STOCK+KOMA (menu5.py SK_STRIP): STILL = the stock still page, KOMA =
// the Koma rows in topic pages, SETUP = the stock 30-row SETUP table in place.
// 5-TAB (menu5.py STRIP and the vendor menu5tab TABS it reuses): STILL, KOMA, PLAY, CUSTOM, SETUP; PLAY, CUSTOM and
// SETUP hold the stock SETUP rows split and renamed as the 5-tab module does.
// The KOMA tab follows menu5.layout: one topic per page (FOCUS, EXPOSURE, DISPLAY, then OTHER for the mode rows);
// the RECIPE page is gone with the Look IA.
//
// A camera row is one map field, or a group of fields the camera shows behind one row (PIC QUALITY/SIZE, IMG SET's
// detail rows). Action rows with no stored value (FORMAT, REG. MY SETTINGS, RESTORE DEFAULTS, INTERVAL, DATE SETTINGS'
// clock, KOMA VERSION) are not settings and are left out. Fields the camera stores but sets elsewhere (the flash
// button, the mode dial's scene, the M-mode dial values) follow in a quiet "Set on the camera" group at the end of the
// first tab that takes them, so nothing in the map is lost. MY SETTINGS 1 and 2 are the two saved slots that REG. MY
// SETTINGS writes; they follow the camera's tabs. Look parameters (recipe_*) belong to Looks (a later update) and
// Advanced.

const IMG_SET = ['img_set', 'setting1_contrast', 'setting1_sharpness', 'setting1_color_depth', 'setting2_contrast',
  'setting2_sharpness', 'setting2_color_depth', 'bw_contrast', 'bw_sharpness', 'te_contrast', 'te_sharpness',
  'te_tone', 'te_density'];
const ELSEWHERE = ['flash_mode', 'manual_aperture', 'manual_shutter', 'scene_mode', 'slow_shutter_limit', 'scene_flash_a',
  'scene_flash_b', 'text_density', 'text_size', 'movie_size', 'disp_mode'];

// The stock still page, in camera order (menus.json page 0; INTERVAL and RESTORE DEFAULTS are actions).
const STILL_ROWS = [['pic_quality', 'pic_size', 'pic_aspect'], ['focus'], ['photometry'], ['cont_mode'], IMG_SET,
  ['auto_bracket'], ['image_with_sound'], ['date_imprint'], ['exposure_comp'], ['white_balance', 'wb_detail'], ['iso'],
  ['flash_expo_comp']];

// The KOMA tab's topic pages (menu5.GROUPS order; each row's group is its feature's ROWS entry).
const KOMA_TOPICS = [
  ['KOMA: FOCUS', ['snap_dist', 'full_snap']],
  ['KOMA: EXPOSURE', ['auto_iso_max', 'auto_iso_slow_shutter', 'long_exp_limit']],
  ['KOMA: DISPLAY', ['highlight_alert', 'focus_peaking']],
  ['KOMA: OTHER', ['menu_layout', 'koma_features']],
];

// The stock SETUP table, rows 1..30 in order (menus.json page 8; FORMAT x2, DATE SETTINGS' clock and REG. MY SETTINGS
// are actions; DATE SETTINGS stores the date format).
const STOCK_SETUP = [['lcd_brightness'], ['dial_direction'], ['synchro_monitor'], ['auto_power_off'], ['beep_sound'],
  ['vol_settings'], ['lcd_confirm'], ['sequential_no'], ['date_format'], ['language'], ['video_out_mode'],
  ['shtg_stgs_warng'], ['usb_connection'], ['enlge_photo_icon'], ['color_space'], ['af_aux_light'], ['digital_zoom'],
  ['zoom_btn_set'], ['adj_btn_set1'], ['adj_btn_set2'], ['adj_btn_set3'], ['adj_btn_set4'], ['adj_shtr_btn_set'],
  ['start_setting'], ['raw_jpeg_setting'], ['cl_bkt_bw_te'], ['info_disp']];

const KOMA_TAB = {id: 'koma', label: 'KOMA', topics: KOMA_TOPICS};
const STILL_TAB = {id: 'still', label: 'STILL', rows: STILL_ROWS, elsewhere: ELSEWHERE};

export const LAYOUTS = {
  grii: {name: 'GR II', tabs: [
    {id: 'shooting', label: 'SHOOTING', rows: [
      ['focus'], ['snap_dist'], ['full_snap'], ['photometry'],
      ['pic_quality', 'pic_size', 'pic_aspect'], ['raw_jpeg_setting'], ['digital_zoom'], IMG_SET,
      ['date_imprint'], ['cont_mode'], ['auto_bracket'], ['cl_bkt_bw_te'], ['image_with_sound'],
      ['white_balance', 'wb_detail'], ['iso'], ['exposure_comp'], ['long_exp_limit'], ['flash_expo_comp'],
    ], elsewhere: ELSEWHERE},
    {id: 'keycust', label: 'KEY CUST.', rows: [
      ['start_setting'], ['adj_btn_set1'], ['adj_btn_set2'], ['adj_btn_set3'], ['adj_btn_set4'], ['adj_shtr_btn_set'],
      ['zoom_btn_set'], ['dial_direction'],
    ]},
    {id: 'setup', label: 'SETUP', rows: [
      ['usb_connection'], ['sequential_no'], ['lcd_brightness'], ['synchro_monitor'], ['beep_sound'], ['vol_settings'],
      ['auto_power_off'], ['auto_iso_max'], ['auto_iso_slow_shutter'], ['af_aux_light'], ['lcd_confirm'], ['info_disp'],
      ['shtg_stgs_warng'], ['highlight_alert'], ['focus_peaking'], ['enlge_photo_icon'], ['date_format'], ['language'],
      ['color_space'], ['video_out_mode'], ['menu_layout'], ['koma_features'],
    ]},
  ]},
  stockkoma: {name: 'STOCK + KOMA', tabs: [STILL_TAB, KOMA_TAB, {id: 'setup', label: 'SETUP', rows: STOCK_SETUP}]},
  fivetab: {name: '5-TAB', tabs: [STILL_TAB, KOMA_TAB,
    {id: 'play', label: 'PLAY', rows: [
      {n: ['lcd_confirm'], l: 'INSTANT REVIEW'}, {n: ['enlge_photo_icon'], l: 'ENLARGED ICON'}, ['video_out_mode'],
      ['usb_connection'],
    ]},
    {id: 'custom', label: 'CUSTOM', rows: [
      {n: ['start_setting'], l: 'START USER MODE'}, {n: ['adj_btn_set1'], l: 'ADJ SETTING 1'},
      {n: ['adj_btn_set2'], l: 'ADJ SETTING 2'}, {n: ['adj_btn_set3'], l: 'ADJ SETTING 3'},
      {n: ['adj_btn_set4'], l: 'ADJ SETTING 4'}, ['adj_shtr_btn_set'], {n: ['zoom_btn_set'], l: 'ZOOM BTN FN SET'},
      ['dial_direction'], {n: ['info_disp'], l: 'INFO DISPLAY'}, {n: ['shtg_stgs_warng'], l: 'SETTINGS WARNING'},
      {n: ['color_space'], l: 'COLOR SPACE'}, {n: ['raw_jpeg_setting'], l: 'RAW+JPEG QUALITY'}, ['digital_zoom'],
      {n: ['af_aux_light'], l: 'AF ASSIST LIGHT'}, ['cl_bkt_bw_te'],
    ]},
    {id: 'setup', label: 'SETUP', rows: [
      ['date_format'], ['language'], ['sequential_no'], ['lcd_brightness'], ['synchro_monitor'], ['auto_power_off'],
      {n: ['beep_sound'], l: 'SOUND EFFECTS'}, {n: ['vol_settings'], l: 'VOLUME'},
    ]},
  ]},
};
export const SLOT_TABS = [
  {id: 'my1', label: 'MY SETTINGS 1', page: 'my1'},
  {id: 'my2', label: 'MY SETTINGS 2', page: 'my2'},
];

// MENU LAYOUT byte -> layout id; anything else (or no Koma) -> GR II.
export function layoutFor(value) { return value === 0 ? 'fivetab' : value === 1 ? 'stockkoma' : 'grii'; }

const PAGE_TAB = {shooting: ['shooting', 'still'], key_custom: ['keycust', 'custom', 'setup'], setup: ['setup'],
  koma: ['setup', 'koma']};

// Build the menu from the parsed map: [{id, label, slot, groups: [{title, rows: [{key, label, fields: [f...]}]}]}].
// userField(f) says whether a field may be shown outside Advanced.
export function buildMenu(M, userField, layout = 'grii') {
  if (!M) return [];
  const L = LAYOUTS[layout] || LAYOUTS.grii;
  const placed = new Set();
  const take = names => names.map(n => M.byName[n]).filter(f => f && userField(f) && !placed.has(f.name))
    .map(f => { placed.add(f.name); return f; });
  const row = spec => {
    const names = Array.isArray(spec) ? spec : spec.n;
    return {key: names[0], label: Array.isArray(spec) ? '' : spec.l, fields: take(names)};
  };
  const tabs = L.tabs.map(t => {
    const groups = t.topics
      ? t.topics.map(([title, names]) => ({title, rows: names.map(n => row([n])).filter(r => r.fields.length)}))
        .filter(g => g.rows.length)
      : [{title: '', rows: t.rows.map(row).filter(r => r.fields.length)}];
    const elsewhere = take(t.elsewhere || []).map(f => ({key: f.name, label: '', fields: [f]}));
    return {id: t.id, label: t.label, slot: false, topics: !!t.topics, groups, elsewhere};
  });
  // Anything the map adds later that this table does not know yet: at the end of the first tab for its page.
  M.fields.forEach(f => {
    if (placed.has(f.name) || !userField(f) || f.page === 'my1' || f.page === 'my2' || /^recipe_/.test(f.name)) return;
    const t = (PAGE_TAB[f.page] || ['setup']).map(id => tabs.find(x => x.id === id)).find(Boolean) || tabs[tabs.length - 1];
    placed.add(f.name);
    if (t.topics && f.page === 'koma') {
      let g = t.groups.find(x => x.title === 'KOMA: OTHER');
      if (!g) t.groups.push(g = {title: 'KOMA: OTHER', rows: []});
      g.rows.push({key: f.name, label: '', fields: [f]});
    } else t.elsewhere.push({key: f.name, label: '', fields: [f]});
  });
  tabs.forEach(t => {
    if (t.elsewhere.length) t.groups.push({title: 'Set on the camera', rows: t.elsewhere});
    delete t.elsewhere; delete t.topics;
    t.groups = t.groups.filter(g => g.rows.length);
  });
  SLOT_TABS.forEach(s => {
    const fields = M.fields.filter(f => f.page === s.page && userField(f))
      .sort((a, b) => (a.order ?? Infinity) - (b.order ?? Infinity) || a.offset - b.offset);
    if (fields.length) tabs.push({id: s.id, label: s.label, slot: true, groups: [{title: '', rows: fields.map(f => ({key: f.name, label: '', fields: [f]}))}]});
  });
  return tabs.filter(t => t.groups.length);
}
