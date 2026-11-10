// Every user-facing string of the Webcam area and Use on your phone. Re-exported by
// words.js, so the copy audit reads it from there. Facts and their evidence: tools/komacam/README.md (OBS Virtual
// Camera, the approval path, no audio, the Zoom steps), pyvirtualcam's own messages (OBS 28, "not installed"),
// tools/komaset/bridge/webcamhost.py and phoneaccess.py.

// The device name the call app lists, by backend. The bridge reports the running backend's name
// itself; this is the fallback.
export function webcamDevice(backend) {
  return backend === 'extension' ? 'Koma GR' : 'OBS Virtual Camera';
}

const listOf = xs => (xs.length < 2 ? xs.join('') : xs.slice(0, -1).join(', ') + ' and ' + xs[xs.length - 1]);
const THIN = ' ';  // between a value and its unit, and around ×
// Where a copied command goes, by the platform the bridge's check reports.
const SHELL = {macos: 'Terminal', windows: 'Command Prompt'};

export const WC = {
  title: 'Webcam',
  row: 'Webcam',     // the switch's label where the page has no Webcam title of its own (Connect)
  off: 'OFF',
  on: 'ON',
  // the line "Shows up as “{device}” in Zoom, Meet and FaceTime.", with the copy control right after the name
  showsUp: ['Shows up as ', ' in Zoom, Meet and FaceTime.'],
  quote: d => '“' + d + '”',
  copyName: d => 'Copy “' + d + '”',
  copied: 'Copied.',
  previewAlt: 'The picture the call receives',
  // the camera streams but no virtual camera takes it: the frame is Koma's output, not what the call has
  previewAltDown: "Koma's picture, not yet reaching the call",
  previewDown: 'The call receives this once the virtual camera runs.',

  // phases (names, no terminal period) and states
  waking: 'Waking the camera',
  stopping: 'Stopping the webcam',
  // the camera gone mid-call: words.js STATE.lost, the app's one sentence for it
  noLive: 'The camera did not start live view.',
  noLiveSub: 'Live view needs Koma on the camera, turned on before USB is plugged in. Unplug, turn the camera off and on, then plug in again.',
  vcamDown: 'The virtual camera is not running.',
  vcamDownSub: n => (n === 1 ? 'Finish the step below.' : 'Finish the steps below.'),
  vcamFailed: 'The virtual camera did not start.',
  vcamFailedSub: 'The reason is under Details.',
  cliRunning: 'A webcam is already running from the command line.',
  cliRunningSub: 'Stop it there, then turn this on.',
  stoppingRace: 'The webcam was still stopping.',
  stoppingRaceSub: 'Turn it on again.',
  failed: 'The webcam did not start.',
  failedSub: 'The reason is under Details.',
  requestFailed: 'Koma did not answer. Try again.',

  // the spec sheet, shown while the camera streams and nothing needs saying: the two halves of the pipeline
  virtualDevice: 'Virtual device',
  lastFrame: 'Last frame',
  starting: 'Starting',
  notStarted: 'Not started',
  notAvailable: 'Not available',
  receiving: 'Receiving',
  ready: 'Ready',
  now: 'Now',
  secondsAgo: s => s + THIN + 's ago',
  noneYet: 'None yet',

  // what this computer is missing, in the order to do it; the head pairs with the call app's as a place
  setup: 'On this computer',
  setupPaired: 'On the computer',   // the same steps, read on a paired phone
  copyCommand: 'Copy command',
  step: {
    python: s => 'Install ' + listOf(s.packages || []) + ' for the Python Koma runs with.',
    obs_install: () => 'Install OBS Studio from obsproject.com.',
    obs_vcam: () => 'Open OBS, click Start Virtual Camera once, then quit OBS.',
    approve: s => 'Allow ' + s.name + ' in ' + s.path + '.',
    obs_update: () => 'Update OBS Studio. Its virtual camera needs version 28 or newer.',
    obs_busy: () => 'Click Stop Virtual Camera in OBS, or quit OBS.',
    v4l2: () => 'Load the v4l2loopback driver.',
    unsupported: () => 'Not supported on this system. The webcam runs on macOS, Windows and Linux.',
  },
  // under a step that carries a command, above its Copy command button
  runCommand: platform => 'Copy the command, then run it in ' + (SHELL[platform] || 'a terminal') + '.',
  obsLink: 'https://obsproject.com',

  // the call app
  inCall: 'In the call app',
  call1: 'Open the camera list.',
  call1Sub: 'In Zoom: Settings > Video > Camera.',
  call2: d => 'Choose “' + d + '”.',
  call2Sub: 'Zoom mirrors the self view. Others see the picture unmirrored.',
  audio: "The GR DIGITAL has no audio path over USB. Use this computer's microphone.",
  audioPaired: "The GR DIGITAL has no audio path over USB. Use the computer's microphone.",
  // on a paired phone, under the switch: the webcam is the computer's, not the phone's
  runsOnComputer: 'Runs on the computer.',

  // Symptom-first help, shown while the webcam is on. While it runs, Shoot waits and the bridge keeps live view for the
  // webcam alone; a page that reads the card (settings, a look) still pauses live view on the camera.
  help: 'Help',
  helpRows: [
    ['Camera not listed', 'Quit the call app and open it again.'],
    ['Black frame', 'Choose the camera again in the call app.'],
    ['Picture pauses', 'Wait for the other Koma page to finish reading the camera.'],
  ],

  // Shoot while the webcam holds the camera, and Connect while the camera is away
  shootTitle: 'Shoot',
  shootHeld: 'The webcam is on.',
  shootHeldSub: 'Shoot pauses while it runs.',
  turnOff: 'Turn the webcam off',
  waitsForCamera: 'The webcam resumes when the camera is back.',

  // Details
  details: 'Details',
  dFps: 'Frame rate',
  dFrames: 'Frames',
  dSource: 'Source',
  dims: (w, h) => w + THIN + '×' + THIN + h,
  dOutput: 'Output',
  dOutputVal: (w, h, fit) => w + THIN + '×' + THIN + h + ', '
    + ({letterbox: 'letterboxed', crop: 'cropped', stretch: 'stretched'}[fit] || fit),
  dRearms: 'Live view restarts',
  dReconnects: 'Reconnects',
  dKeepalives: 'Keepalives',
  dObs: 'OBS Browser source',
  dObsOff: port => 'Not served. Port ' + port + ' is in use.',
  dReaders: 'Readers',   // OBS's Browser source or the macOS extension reading the OBS source
  dVcamError: 'Virtual camera',
  dError: 'Error',
  fps: v => v.toFixed(1) + THIN + 'fps',
};

export const PH = {
  title: 'Use on your phone',
  row: 'Network access',
  rowSub: "Turn on, then scan the code with the phone's camera.",
  qrLabel: 'Pairing code for the phone',
  scan: "Scan with the phone's camera. It must be on the same Wi-Fi as this computer.",
  pairedNone: 'No phone paired.',
  paired: n => (n === 1 ? '1 phone paired.' : n + ' phones paired.'),
  revoke: 'Revoke',
  revokeSub: 'Signs out every phone and shows a new code.',
  revokeSubNone: 'Shows a new code.',
  revoked: 'Revoked. No phone has access.',
  opening: 'Opening network access',
  closing: 'Closing network access',
  noNetwork: 'This computer is not on a network.',
  noNetworkSub: 'Connect it to Wi-Fi, then turn this on.',
  failed: 'Network access did not start.',
  failedSub: 'The reason is under Details.',
  requestFailed: 'Koma did not answer. Try again.',
  details: 'Details',
  dLink: 'Pairing link',
  dLinkSub: 'Works once.',
  dError: 'Error',
  copyLink: 'Copy the pairing link',
  copied: 'Copied.',
  // on a paired phone
  // the address goes between the two parts, in tabular figures
  onPhone: ['This phone is paired with Koma on the computer at ', '.'],
  onPhoneSub: 'Access ends when it is revoked on the computer.',
  // the phone's own screen before it has access
  pairing: 'Pairing with Koma on the computer',
  stale: 'This code is not current.',
  staleSub: 'On the computer, open Settings > Use on your phone and scan the new code.',
  unreachable: 'Koma on the computer does not answer.',
  unreachableSub: 'Check that this phone is on the same network, then scan the code again.',
  computer: 'This link pairs a phone.',
  computerSub: 'Open it on the phone, or scan the code there.',
  openHere: 'Open Koma on this computer',
  notPaired: 'This phone is not paired with Koma.',
  revokedHere: 'Access was revoked on the computer.',
  notPairedSub: 'On the computer, open Settings > Use on your phone and scan the code.',
};
