<script>
  // Your camera: connect it, read what it runs, back up its settings. Reads only:
  // GET /status, /komalog, /settings, and the newest photo when neither file says Koma (lib/onboard.svelte.js).
  // The read fits the plan to the camera: a camera already running Koma needs nothing written, so the full plan becomes
  // the short one (Welcome, Your camera, Pick a look, Done) at this same step; a camera without Koma in the short plan
  // gets the full one.
  import {cam} from '../../lib/camera.svelte.js';
  import {b64decode} from '../../lib/bridge.js';
  import {komaVersionText, komaOnCamera} from '../../lib/words.js';
  import {st, save, plan, next, back, readCamera, readPackage, packageMeta, pkgFacts, classify, modeFor, isKoma, komaRuns,
    keepBackup, settled, problemWords} from '../../lib/onboard.svelte.js';
  import {download} from '../../lib/onboardCard.js';
  import {W} from '../../lib/onboardWords.js';
  import Spread from '../../components/onboard/Spread.svelte';
  import SpecSheet from '../../components/ui/SpecSheet.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';
  import BackAction from '../../components/onboard/BackAction.svelte';
  import GrCamera from '../../components/onboard/GrCamera.svelte';

  // a camera this step already read (a reload, or Back) shows as read; the read runs again when the camera is on the cable
  let phase = $state(st.cam && st.backup ? 'done' : 'idle');   // idle | reading | done | error
  let pkg = $state(packageMeta());
  let lastConn = -1;
  // every wait past 10 s carries its elapsed time: looking counts from the step's start, reading from
  // the read's start
  const lookFrom = Date.now();
  let readFrom = $state(0);

  function fitPlan(kind) {
    if (st.plan === 'wizard' && st.mode === 'none') st.plan = 'koma';
    else if (st.plan === 'koma' && !komaRuns(kind) && st.mode) st.plan = 'wizard';
  }
  async function read() {
    if (phase === 'reading') return;
    phase = 'reading'; readFrom = Date.now();
    try {
      const [c, p] = await Promise.all([settled(() => readCamera()), readPackage().catch(() => null)]);
      pkg = pkgFacts(p);
      const {cur, ...facts} = c;
      st.cam = {...facts, kind: classify(c, pkg)};
      st.mode = modeFor(st.cam.kind);
      fitPlan(st.cam.kind);
      keepBackup(c);
      phase = 'done';
    } catch (e) { phase = 'error'; }
  }
  // Read on every new connection: the first one, and the one after the photo when the card had none.
  $effect(() => {
    if (cam.state === 'cam' && cam.conn !== lastConn) { lastConn = cam.conn; read(); }
  });

  const c = $derived(st.cam);
  const kind = $derived(c ? c.kind : null);
  const on = $derived(cam.state === 'cam');
  const short = $derived(st.plan === 'koma');
  // The short plan writes nothing; the full one needs this computer's package wherever it writes Koma.
  const usable = $derived(phase === 'done' && (short ? komaRuns(kind) : !!st.mode && (st.mode === 'none' || !!pkg)));
  // Why the camera is not there, when the bridge knows (the same words as Connect)
  const problemText = $derived(problemWords());

  const lead = $derived.by(() => {
    if (!c || (!on && phase !== 'done')) return W.camera.connectLead;
    if (kind === 'wrong-firmware') return W.camera.wrongFirmware(c.fw);
    if (kind === 'no-photo') return W.camera.noPhotoLead;
    if (kind === 'tag-unknown') return W.camera.tagUnknownLead;
    return kind === 'official' ? W.camera.lead.official : W.camera.lead.koma;
  });
  const lines = $derived(!c || (!on && phase !== 'done') ? [W.camera.connectLine]
    : kind === 'wrong-firmware' ? [W.camera.wrongFirmwareLine] : kind === 'no-photo' ? W.camera.noPhotoLines
    : kind === 'tag-unknown' ? [W.camera.noPhotoLines[0]] : []);

  // One fact, one place: the lead names the firmware, the rows give its version and Koma's. Koma's version
  // (words.js komaOnCamera): this computer's release when the camera's log names its build, "An earlier Koma build" for
  // one this package replaces, else the release PARAM.CUR's tag names, else the plain fact that Koma runs.
  const komaText = $derived.by(() => {
    if (!c || !komaRuns(kind)) return W.camera.komaNone;
    return komaOnCamera(c.tag, {kind, pkgVersion: pkg && pkg.version});
  });
  const rows = $derived.by(() => {
    if (!c || !(kind === 'official' || isKoma(kind))) return [];
    return [
      {label: W.camera.rows.camera, value: c.model, cam: true, testid: 'row-camera'},
      {label: W.camera.rows.firmware, value: c.fw, testid: 'row-firmware'},
      {label: W.camera.rows.koma, value: komaText, testid: 'row-koma'},
      // the settings are backed up once the file is saved (on Continue); until then they have been read
      ...(st.backup && st.backup.b64 ? [{label: W.camera.rows.settings, value: st.backup.saved ? W.camera.backedUp : W.camera.backupRead,
        testid: 'row-settings'}] : []),
    ];
  });
  const pending = $derived(!!(st.backup && st.backup.b64 && !st.backup.saved));

  function cont() {
    if (pending) {
      download(b64decode(st.backup.b64), st.backup.name);
      st.backup.saved = true;
      save();
    }
    next();
  }
</script>

{#snippet plugIn()}
  <GrCamera view="top" highlight={['power', 'usb-door']} callouts={[{id: 'power', n: 1, dir: 'up-right'}, {id: 'usb-door', n: 2, dir: 'right'}]} testid="setup-figure" />
{/snippet}

<Spread step="camera" index={st.step + 1 + ' / ' + plan().length} title={W.steps.camera} {lead} {lines}
  figure={!on && !c ? plugIn : null}>
  {#snippet body()}
    {#if rows.length}
      <!-- another build: its consequence under the sheet, as Update Koma states it -->
      <div class="facts">
        <SpecSheet {rows} label={W.steps.camera} testid="camera-facts" />
        {#if kind === 'koma-other'}<p class="small" data-testid="camera-line">{W.update.different}</p>{/if}
      </div>
    {/if}
    {#if phase === 'reading'}
      <StatusLine text={W.camera.reading} since={readFrom} testid="camera-status" />
    {:else if phase === 'error'}
      <StatusLine tone="bad" text={W.camera.readFailed} testid="camera-status"
        action={{label: W.camera.readAgain, onclick: read, testid: 'camera-read'}} />
    {:else if !on && !c && problemText}
      <StatusLine tone="bad" text={problemText.text} sub={problemText.sub} testid="camera-status" />
    {:else if !on && !c}
      <StatusLine text={W.camera.looking} since={lookFrom} testid="camera-status" />
    {:else if c && (kind === 'no-photo' || kind === 'tag-unknown') && on}
      <StatusLine text={kind === 'no-photo' ? W.camera.noPhoto : W.camera.tagUnknown} testid="camera-status"
        action={{label: W.camera.readAgain, onclick: read, testid: 'camera-read'}} />
    {:else if c && !short && !pkg && phase === 'done' && st.mode && st.mode !== 'none'}
      <StatusLine tone="bad" text={W.update.noPackage} testid="camera-status" />
    {/if}
    {#if c}
      <details class="details">
        <summary>{W.details}</summary>
        <pre>{[
          'Firmware (USB -VER): ' + (c.fw || '--'),
          'KOMALOG.TXT build: ' + (c.build || 'none'),
          'PARAM.CUR: ' + (c.kv === null ? 'none' : 'Koma settings version ' + c.kv + (c.tag ? ', firmware tag ' + c.tag : '')),
          c.photoName ? 'Newest photo ' + c.photoName + ': tag ' + (c.photoTag || 'none') : '',
          pkg ? 'This computer: ' + (komaVersionText(pkg.version) || 'Koma') + ', build ' + pkg.build : '',
          st.backup && st.backup.b64 ? 'Backup: ' + st.backup.name + ', 4096 bytes. ' + W.camera.backupNote : '',
        ].filter(Boolean).join('\n')}</pre>
      </details>
    {/if}
  {/snippet}
  {#snippet actions()}
    <BackAction onclick={back} />
    <button class="btn primary" class:onb-wait={!usable} type="button" onclick={cont} disabled={!usable} data-testid="setup-next">{pending ? W.camera.saveBackup : W.next}</button>
  {/snippet}
</Spread>

<style>
  .facts { display: grid; gap: 12px; }
</style>
