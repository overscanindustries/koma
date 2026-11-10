// Every user-facing string of setup, Install and Update Koma, in one place for the copy audit. Voice:
// Style: object as subject, imperative steps, past tense for done, "the camera", no reassurance).
// The on-camera procedure strings cite their sources in SOURCES below; they show under Details on each screen.
// Two file names appear outside Details on purpose, each because the name is the instruction: the update file a card
// refusal names ("The card holds QRFLECT7.") and a file the user must delete on the download route ("The card still
// holds KOMALOG.TXT.").

import {EARLIER_BUILD, INSTALLED} from './words.js';

const NB = ' ';   // keeps GR DIGITAL on one line
const NNB = '\u202F';  // value and unit: a narrow no-break space, so 2 GB never breaks
// The plug order on two lines, broken at the sentence: "Then plug" never splits, so a balanced lead breaks after "on."
const PLUG = 'Turn the camera on. Then' + NB + 'plug in the USB cable.';

export const W = {
  steps: {welcome: 'Welcome', camera: 'Your camera', card: 'Prepare card', install: 'Install Koma', oncamera: 'On the camera',
    connect: 'Connect USB', look: 'Pick a look', done: 'Done'},
  exit: 'Exit',
  setup: 'Set up Koma',
  back: 'Back',
  offline: 'Koma is not running on this computer. Start it again. This page reconnects when it does.',
  next: 'Continue',
  details: 'Details',
  // LcdInset (not shown in this pass: no update screen's pixels or strings are recorded)
  onCamera: 'On the camera now',
  screenOff: 'Screen off',
  pointOfNoReturn: 'Point of no return',
  // the screen inside a multi-screen step, on the index line (4 / 7  2 OF 8); not every screen is a press
  screen: (i, n) => i + ' of ' + n,

  // Welcome, by what the camera runs (st.cam.kind): stock (or no camera yet), the Koma firmware without Koma's file, Koma
  welcome: {
    lead: 'Koma gives the' + NB + 'GR' + NB + 'DIGITAL 21 looks, snap focus, focus peaking and large SD cards.',
    lines: ['The Koma firmware goes on the camera once. Everything else is one file on the card.',
      'The card goes in this computer twice. The camera writes for 2 to 3' + NB + 'minutes in between.'],
    linesKomaFw: ['The Koma firmware is already on this camera. Setup puts Koma’s file on the card.'],
    linesKoma: ['Koma is on this camera.', 'Setup backs up its settings, then picks the look it starts with.'],
    need: 'You need',
    needs: ['The camera with a charged battery', 'An SD card of 2' + NNB + 'GB or smaller for setup, and a card reader'],
    begin: 'Begin',
  },

  camera: {
    connectLead: PLUG,
    connectLine: 'Plugged in while off, the camera starts in a mode Koma cannot use.',
    looking: 'Looking for the camera.',
    reading: 'Reading the camera.',
    readFailed: 'The camera could not be read. Check the cable, then read again.',
    readAgain: 'Read again',
    rows: {camera: 'Camera', firmware: 'Firmware', koma: 'Koma', settings: 'Settings'},
    komaNone: 'Not installed',
    komaInstalled: INSTALLED,     // Koma runs, and nothing names its release (Details has the build and the tag)
    komaEarlier: EARLIER_BUILD,   // a build made before the first release: never its number (VERSIONING.md)
    backedUp: 'Backed up',
    backupRead: 'Ready to back up',          // read from the camera, saved as a file on Continue
    // the Koma firmware's generation is internal and never shown (VERSIONING.md); a mismatch is update.needsFirmware
    lead: {official: 'The camera runs the official firmware.', koma: 'The camera runs the Koma firmware.'},
    saveBackup: 'Save backup and continue',
    wrongFirmware: v => 'This camera reports firmware ' + v + '. Koma needs 2.40.',
    wrongFirmwareLine: 'Ricoh’s own update to 2.40 comes first.',
    noPhotoLead: 'Take one photo with the camera.',
    noPhotoLines: ['Unplug the cable, take the photo, then plug the cable in again.'],
    noPhoto: 'The card holds no photo.',
    tagUnknownLead: 'Take a new photo with the camera.',
    tagUnknown: 'Koma could not read the firmware from the newest photo.',
    backupNote: 'The settings file is kept in this browser and saved as a download on Continue.',
  },

  card: {
    lead: 'Turn the camera off and unplug it. Put its card in this computer.',
    lines: ['Use an SD card of 2' + NNB + 'GB or smaller. The camera cannot read setup files from a larger card.'],
    choose: 'Choose the card',
    chooseAgain: 'Choose the card again',
    rows: {writes: 'Writes', removes: 'Removes', stays: 'Stays'},
    writes: {firmware: 'The Koma firmware', official: 'The official firmware 2.40', installer: v => v + ' and its installer'},
    removes: {updateFile: 'The firmware update file', koma: 'Koma’s files', installer: 'The installer'},
    reads: 'Reads',
    record: 'The installer’s record',
    // where Koma goes, chosen on the trip that writes Koma's file (st.where)
    where: 'Where Koma goes',
    whereCard: {label: 'On this card', text: 'Koma starts when this card is in the camera.'},
    whereIrom: {label: 'In the camera', text: 'Koma starts with any card, 64' + NNB + 'GB too. One more card trip.'},
    stays: 'Photos and other files',
    phases: {backup: 'Backing up settings', write: 'Writing card', verify: 'Reading back'},
    ready: 'The card is ready.',
    readyIrom: 'Koma is in the camera. The installer is off the card.',
    whyNot: {
      dcim: 'This is the DCIM folder. Choose the top level of the card.',
      'not-card': 'This folder is not the top level of a camera card. Choose the card itself.',
      'update-files': names => 'The card holds ' + names.join(' and ') + '. The camera would read it during the update. Remove it, then choose the card again.',
      permission: 'Koma cannot write to this folder. Choose the card again and allow changes.',
      full: 'The card is full. Make room on it, then choose it again.',
      write: 'Writing the card failed. Check that its lock switch is off, then choose it again.',
      missing: 'A file is missing from the card. Choose the card again to write it once more.',
      differs: 'The card does not hold what was written. Choose the card again to write it once more.',
      still: names => 'The card still holds ' + names.join(' and ') + '. Delete it, then choose the card again.',
      fetch: 'This computer could not prepare the files. Restart Koma, then try again.',
      'irom-none': 'The card has no finished record from the installer. Put it in the camera, turn the camera on for 1' + NB + 'minute, then choose the card again.',
      'irom-unmounted': 'The camera’s internal memory could not be opened. Nothing was written to it. Go back and choose On this card.',
      'irom-verify': 'The copy in the camera did not read back and was removed. Put the card in the camera, turn it on for 1' + NB + 'minute, then choose the card again.',
      'irom-refused': 'The installer did not copy Koma. Go back and write the card again.',
    },
    // a browser without the File System Access API (every card job writes one file)
    saveLead: 'Save the file, then copy it to the top level of the card.',
    save: 'Save the file',
    check: 'Check the card',
    clearLead: 'Delete GRMOD.BIN and KOMAPKG.BIN from the card, then check the card.',
    checkLine: 'Then choose the card.',
    stillCheck: names => 'The card still holds ' + names.join(' and ') + '. Delete it, then check the card again.',
    // Ricoh's file, when the bridge has none
    stockLead: {install: 'Koma builds the Koma firmware from Ricoh’s 2.40 update file.',
      package: 'Koma builds the Koma file from Ricoh’s 2.40 update file.',
      official: 'The return to official firmware needs Ricoh’s 2.40 update file.'},
    // a line with a link: Spread renders {text, label, href} as text, then the link
    stockLine: {text: 'Choose the GR' + NB + 'DIGITAL 2.40 update file. ', label: 'Download it from Ricoh',
      href: 'https://www.ricoh-imaging.co.jp/english/r_dc/download/firmware/grd/win.html'},
    stockChoose: 'Choose Ricoh’s file',
    stockChecking: 'Checking the file.',
    stockBad: 'This is not Ricoh’s GR' + NB + 'DIGITAL 2.40 update file.',
    nothing: 'The card needs nothing.',
  },

  oncamera: {
    // the keyed power-on: one press per screen (sources: SOURCES.flash)
    flash: [
      {lead: 'Put the card back in the camera.', lines: ['Keep the camera off and the USB cable out.']},
      {lead: 'Set the mode dial to Movie.', lines: ['Hold the unlock button and turn the dial.']},
      {lead: 'Press and hold ▲ and the enlarged view button.', lines: ['Keep holding both.']},
      {lead: 'Press and hold the playback button as well.', lines: ['Hold all three until the screen lights.']},
      {lead: 'Release all three buttons.', lines: ['Wait 5 seconds. The screen shows the update question.']},
      {lead: 'Press ▶, then MENU/OK.', lines: ['▶ answers yes. MENU/OK confirms.']},
      {lead: 'Do not touch the camera while the lamp blinks.', lines: []},
      {lead: 'When the lamp has stopped, turn the camera off.', lines: ['Set the mode dial back to Still Image.']},
    ],
    card: {lead: 'Put the card in this computer again.', lines: []},
    card3: {lead: 'Put the card in this computer again.', lines: []},
    run: {lead: 'Put the card in the camera. Turn it on, wait 1' + NB + 'minute, then turn it off.',
      lines: ['Keep the USB cable out.']},
    back: {lead: 'Put the card back in the camera.', lines: ['Keep the camera off.']},
    backIrom: {lead: 'Put a card in the camera.', lines: ['Keep the camera off. Koma now starts from the camera’s internal memory.']},
    // the write, named as its phase on the status line (with its elapsed counter)
    writing: {install: 'Writing the Koma firmware', official: 'Writing the official firmware'},
    takes: 'The camera takes 2 to 3' + NB + 'minutes.',
    rule: 'Keep the camera on. Do not open the battery cover.',
    lampStopped: 'The lamp has stopped',
    stopHere: 'Stop here',
    stopReason: 'The update cannot be stopped once MENU/OK is pressed.',
    stoppedLine: 'Nothing was written to the camera. The card still holds the update file.',
    nothing: 'There is nothing to do on the camera.',
  },

  connect: {
    lead: PLUG,
    line: 'Plugged in while off, the camera starts in a mode Koma cannot use.',
    leadOfficial: 'Turn the camera on and take one photo.',
    lineOfficial: 'Then plug in the USB cable.',
    phases: {wait: 'Waiting for the camera', confirm: 'Confirming'},
    late: 'The camera has not come back. Reconnect it and keep waiting.',
    running: v => (v || 'Koma') + ' is running.',   // v: komaVersionText of the package
    official: 'The official firmware is running.',
    noKoma: 'Koma did not start on the camera. Check that the card is in it, then turn the camera off and on.',
    otherBuild: 'The camera started another Koma build. Check that the card is in it, then turn the camera off and on.',
    incomplete: 'Koma’s start record on the card is incomplete. Turn the camera off and on.',
    noNewPhoto: 'No new photo on the card. Take one photo, then read again.',
    stillKoma: 'The newest photo was made by the Koma firmware. Go through the steps on the camera again.',
    readAgain: 'Read again',
    connected: 'The camera is connected.',
  },

  look: {
    lead: 'Choose the look the camera starts with.',
    line: 'Change it any time in Looks, or with IMG SET on the camera.',
    use: name => 'Use ' + name,
    setting: name => 'Setting ' + name + ' on the camera.',
    active: name => name + ' is active.',
    failed: 'The look could not be set. Check the cable, then try again.',
    busy: 'The camera is busy. Close its menu, then try again.',
    notSupported: 'This Koma cannot set looks over USB. Choose one on the camera with IMG SET.',
    reading: 'Reading the looks from the camera.',
    tryAgain: 'Try again',
  },

  done: {
    lead: 'Koma is running on the camera.',
    leadKoma: 'Koma is set up.',
    leadOfficial: 'The camera runs the official firmware 2.40.',
    readBack: 'Read back matches.',
    rows: {koma: 'Koma', look: 'Look', settings: 'Settings'},
    backedUp: 'Backed up',
    undo: 'Return to the official firmware under Settings > Update Koma.',
    again: 'Install Koma again under Settings > Update Koma.',
    test: 'Take a test photo',
    finish: 'Finish',
  },

  update: {
    rows: {camera: 'On the camera', here: 'On this computer'},
    notConnected: 'Connect the camera to compare its Koma with this computer’s.',
    same: v => 'This camera runs ' + (v || 'this computer’s Koma') + '. Nothing to update.',
    checkAgain: 'Check again',
    startAgain: 'Start again',
    different: 'This camera runs a different Koma build from this computer’s. It stays as it is.',
    needsFirmware: 'Needs a newer Koma firmware.',
    install: 'Install',
    update: 'Update',
    installEffect: 'Backs up the settings, then writes the card in this computer.',
    official: 'Return to official firmware',
    officialEffect: 'Writes Ricoh’s 2.40 firmware to the card. The camera then runs without Koma.',
    officialUntried: 'This return has been tested on the first Koma firmware only.',
    restart: 'Run setup from the start',
    resume: {wizard: 'Continue setup', koma: 'Continue setup', update: 'Continue the update', official: 'Continue the return'},
    resumeLine: {update: 'An update was left part way.', official: 'A return to official firmware was left part way.'},
    noPackage: 'This computer has no Koma file to install.',
  },
};

// Sources for every on-camera step, shown under Details (tag: HW proven on a camera, STATIC traced from the firmware
// or Ricoh's documents, U not traced or not verified).
export const SOURCES = {
  flash: [
    ['Ricoh, GR DIGITAL firmware update procedure, card-reader method, step 4 (firmware/pc-software/docs-update-pages)', 'STATIC'],
    ['Ricoh, same procedure, step 4: mode selector to Movie; camera manual p. 16: hold the unlock button while turning the mode dial', 'STATIC'],
    ['Ricoh, same procedure, step 5: Flash/Up arrow and Enlarged view buttons; the update gate reads UP (0xb) and TELE (0xf)', 'HW'],
    ['Ricoh, same procedure, step 5: hold Playback until the LCD lights; held 3 s', 'HW'],
    ['Release the held keys first (0x513c0 sends nothing while two keys are down; Playback held past 300 polls powers off); release, wait 5 s. Screen 0x18 asks whether to update; its wording has never been recorded (the update screens were never rendered): not verified', 'U'],
    ['On screen 0x18 RIGHT sets yes and releasing MENU/OK answers; the first destructive operation follows, and no cancel exists after the prompt: RIGHT, then MENU/OK', 'HW'],
    ['The green lamp blinks for about 2 to 3 minutes. Ricoh updater dialog: the AF and flash lamps blink, never cut the power while the lamp blinks. Continue is offered after 2:30.', 'HW'],
    ['Ricoh updater dialog: playback appears after about 2 minutes, so the camera is on when the lamp stops (success has no update screen; power off with POWER if still on). Camera manual p. 16 (mode dial, Still Image mode)', 'STATIC'],
  ],
  card2: ['docs/install.md section 2: flash QRFLECT3 alone, then delete it and copy GRMOD.BIN to the card root. One card holding both flashed and then ran the module once: one observation, not the documented procedure', 'STATIC'],
  connect: ['Plug order: the camera on first, then USB (komabridge README); confirmation reads KOMALOG.TXT over USB (RETR): the build id and STAGE 3', 'HW'],
  irom: ['docs/install.md "Internal memory": the installer (irodump.grmod) as GRMOD.BIN and the package as KOMAPKG.BIN on a 2 GB card, power on 60 s, power off; IRODUMP.TXT result=installed, dst_valid=1, end=1. Verified on a camera with an earlier Koma build; with the Koma 1.0 package: not yet run on a camera', 'HW'],
  card: ['Card files at the top level (Ricoh procedure step 3; the loader reads /ATA1/GRMOD.BIN). Install: QRFLECT3 alone, then GRMOD.BIN on a second trip (docs/install.md section 2)', 'STATIC'],
  official: {
    1: ['Official 2.40 QRFLECT3 over the loader Koma firmware (flash-1) by the same keyed power-on, then a normal boot and a photo tagged Rev0240', 'HW'],
    other: ['Official 2.40 QRFLECT3 by the same keyed power-on: observed over the loader Koma firmware (flash-1) only. Over the SDHC Koma firmware: not verified', 'U'],
  },
};
