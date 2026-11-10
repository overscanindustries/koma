<script>
  // Settings -> Update Koma: what the camera runs, what this computer has, one primary action by state, and
  // Return to official firmware, always one click away while Koma runs. The work happens full screen (Onboard.svelte).
  //   official           setup (the full plan: the Koma firmware, then Koma); Start again beside a setup left part way
  //   koma-fw            Install: Koma's file on the card (the Koma firmware is already on the camera)
  //   koma-older         Update: a build this computer's package replaces (an earlier build, or an older release)
  //   koma-current       nothing to update
  //   koma-other/unknown the camera keeps its build (lib/onboard.svelte.js modeFor: nothing proves which is newer)
  //   no-photo, tag-unknown  the firmware could not be read from a photo: take one, check again
  import '../../styles/onboard.css';
  import {cam} from '../../lib/camera.svelte.js';
  import {bridgeJson, b64decode} from '../../lib/bridge.js';
  import {komaVersionText, komaOnCamera} from '../../lib/words.js';
  import {st, open, readCamera, readPackage, pkgFacts, classify, modeFor, isKoma, komaRuns, backupName, settled, resumable,
    resume, setupEntry} from '../../lib/onboard.svelte.js';
  import {download} from '../../lib/onboardCard.js';
  import {W} from '../../lib/onboardWords.js';
  import SpecSheet from '../../components/ui/SpecSheet.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';

  let phase = $state('idle');   // idle | reading | ready | error
  let c = $state(null);
  let pkg = $state(null);
  let lastConn = -1;

  async function read() {
    phase = 'reading';
    try {
      const [cc, p] = await Promise.all([settled(() => readCamera()), readPackage().catch(() => null)]);
      pkg = pkgFacts(p);
      c = {...cc, kind: classify(cc, pkg)};
      phase = 'ready';
    } catch (e) { phase = 'error'; }
  }
  $effect(() => { if (cam.state === 'cam' && cam.conn !== lastConn) { lastConn = cam.conn; read(); } });

  const kind = $derived(c ? c.kind : null);
  const koma = $derived(isKoma(kind));
  const ready = $derived(phase === 'ready');
  const hereText = $derived(pkg ? komaVersionText(pkg.version) || 'Koma' : '');
  // Koma's version on the camera (words.js komaOnCamera): this computer's release when the camera's log names its build,
  // "An earlier Koma build" for one this package replaces, else the release PARAM.CUR's tag names, else that Koma runs
  const camVersion = $derived.by(() => {
    if (!c || !komaRuns(kind)) return '';
    const v = komaOnCamera(c.tag, {kind, pkgVersion: pkg && pkg.version});
    return /^\d/.test(v) ? 'Koma ' + v : v;
  });
  const camText = $derived(!c || kind === 'no-photo' || kind === 'tag-unknown' ? '' : komaRuns(kind) ? camVersion
    : kind === 'wrong-firmware' ? W.camera.wrongFirmware(c.fw) : W.camera.komaNone);
  // The same build on both: one row (the package header's number can differ from the camera's release tag, and one
  // build never shows two numbers).
  const rows = $derived(c ? [
    {label: W.update.rows.camera, value: camText, testid: 'upd-camera'},
    kind === 'koma-current' ? null : {label: W.update.rows.here, value: pkg ? hereText : W.update.noPackage, testid: 'upd-here'},
  ].filter(Boolean) : []);
  // one primary per state
  const stock = $derived(ready && kind === 'official');
  const unread = $derived(ready && (kind === 'no-photo' || kind === 'tag-unknown'));
  // the package's minimum Koma firmware generation against the camera's (internal; said in words, never as a number)
  const needsFw = $derived(!!(ready && pkg && c && c.gen && (pkg.minFirmware || 1) > c.gen));
  const action = $derived(ready && pkg && !needsFw ? (kind === 'koma-fw' ? 'install' : kind === 'koma-older' ? 'update' : null) : null);
  const entry = $derived(setupEntry());
  // Return to official firmware has been done over the first Koma firmware only
  const untried = $derived(koma && c && c.gen !== 1);
  // a left update or return goes on from here; a left setup goes on from the setup button (stock) or from Overview
  const otherResume = $derived(resumable() && st.plan !== 'wizard' && st.plan !== 'koma');

  function backup(extra) {
    if (c && c.cur) {
      const name = backupName();
      download(b64decode(c.cur), name);
      extra.backup = {name, at: Date.now(), b64: c.cur, saved: true};
    } else extra.backup = {skipped: true};
    const {cur, ...facts} = c;
    extra.cam = facts;
    extra.pkg = pkg;
    return extra;
  }
  function install() { open('update', backup({mode: modeFor(kind)})); }
  async function official() {
    let before = null;
    try { const d = await bridgeJson('/photos', {timeout: 60000}); before = d.photos && d.photos[0] ? d.photos[0].path : null; }
    catch (e) { /* no photos to compare with: any photo after the update is new */ }
    open('official', backup({mode: 'official', before}));
  }
  function go(e, fn) { e.preventDefault(); fn(); }
</script>

<section class="upd" data-testid="update-koma">
  {#if cam.state !== 'cam'}
    <StatusLine text={W.update.notConnected} testid="update-status" />
  {:else if phase === 'reading'}
    <StatusLine text={W.camera.reading} testid="update-status" />
  {:else if phase === 'error'}
    <StatusLine tone="bad" text={W.camera.readFailed} testid="update-status" action={{label: W.camera.readAgain, onclick: read}} />
  {/if}

  {#if rows.length}<SpecSheet {rows} label="Update Koma" testid="update-facts" />{/if}

  {#if ready && kind === 'koma-current'}
    <p class="small" data-testid="update-line">{W.update.same(camVersion)}</p>
  {:else if ready && (kind === 'koma-other' || kind === 'koma-unknown')}
    <p class="small" data-testid="update-line">{W.update.different}</p>
  {:else if needsFw && (kind === 'koma-fw' || kind === 'koma-older')}
    <p class="small" data-testid="update-line">{W.update.needsFirmware}</p>
  {:else if unread}
    <StatusLine text={W.camera.tagUnknownLead} testid="update-status" action={{label: W.update.checkAgain, onclick: read, testid: 'update-check'}} />
  {/if}

  {#if stock}
    <div class="act">
      <a class="btn primary" href={entry.href} onclick={e => go(e, entry.onclick)}
        data-testid={entry.resuming ? 'update-resume' : 'update-setup'}>{entry.label}</a>
      {#if entry.resuming}
        <div class="effect">
          {#if st.stopped}<p class="small">{W.oncamera.stoppedLine}</p>{/if}
          <p><a class="link small" href="#/setup" onclick={e => go(e, () => open('wizard'))} data-testid="update-restart">{W.update.startAgain}</a></p>
        </div>
      {/if}
    </div>
  {:else if action}
    <div class="act">
      <button class="btn primary" type="button" onclick={install} data-testid="update-install">{action === 'install' ? W.update.install : W.update.update}</button>
      <p class="small effect">{W.update.installEffect}</p>
    </div>
  {/if}

  {#if ready && koma}
    <div class="act danger-row">
      <button class="btn quiet danger" type="button" onclick={official} data-testid="update-official">{W.update.official}</button>
      <div class="effect">
        <p class="small">{W.update.officialEffect}</p>
        {#if untried}<p class="small" data-testid="update-untried">{W.update.officialUntried}</p>{/if}
      </div>
    </div>
  {/if}

  {#if otherResume}
    <div class="resume">
      <p class="small">{W.update.resumeLine[st.plan]}{#if st.stopped}{' ' + W.oncamera.stoppedLine}{/if}</p>
      <p><button class="link small" type="button" onclick={resume} data-testid="update-resume">{W.update.resume[st.plan]}</button></p>
    </div>
  {/if}

  <!-- setup from the start: in view where nothing else leads to it (the camera unread, or its firmware unknown); under
       Details where Koma runs; the setup button already says it on a stock camera -->
  {#if !c || unread || kind === 'wrong-firmware'}
    <p><a class="link small" href="#/setup" onclick={e => go(e, () => open('wizard'))} data-testid="update-restart">{W.update.restart}</a></p>
  {/if}

  {#if c}
    <details class="details"><summary>{W.details}</summary>
      <pre>{[
        'Firmware (USB -VER): ' + (c.fw || '--'),
        'KOMALOG.TXT build: ' + (c.build || 'none'),
        'PARAM.CUR: ' + (c.kv === null ? 'none' : 'Koma settings version ' + c.kv + (c.tag ? ', firmware tag ' + c.tag : '')),
        c.photoName ? 'Newest photo ' + c.photoName + ': tag ' + (c.photoTag || 'none') : '',
        pkg ? 'This computer: ' + (kind === 'koma-current' ? '' : hereText + ', ') + 'build ' + pkg.build : '',
        pkg && pkg.supersedes.length ? 'Replaces builds: ' + pkg.supersedes.join(', ') : '',
      ].filter(Boolean).join('\n')}</pre>
      {#if koma}<p class="more"><a class="link small" href="#/setup" onclick={e => go(e, () => open('wizard'))} data-testid="update-restart">{W.update.restart}</a></p>{/if}
    </details>
  {/if}
</section>

<style>
  .upd { display: grid; gap: 24px; max-width: 640px; align-content: start; }
  .act { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 16px; align-items: center; }
  .danger-row { padding-top: 24px; border-top: 1px solid var(--line); }
  .effect { max-width: 44ch; display: grid; gap: 4px; }
  .resume { display: grid; gap: 4px; }
  .more { margin-top: 12px; }
  @media (max-width: 720px) { .act { grid-template-columns: minmax(0, 1fr); gap: 8px; } .act :global(.btn) { min-height: 44px; } }
</style>
