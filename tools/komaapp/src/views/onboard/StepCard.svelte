<script>
  // Prepare card. The card sits in this
  // computer's reader; Koma writes it there and reads every file back (lib/onboardCard.js). Nothing goes over USB.
  // trip 2: the second card trip of an install, inside Install Koma (lib/onboard.svelte.js cardJob): Koma's file on,
  // the update file off. trip 3: the /IROM route's last trip (st.where 'irom'): read the installer's record, take the
  // installer off. On the trip that writes Koma's file the user picks where Koma goes (lib/onboard.svelte.js screenIds).
  import {onMount, untrack} from 'svelte';
  import {st, save, plan, next, back, cardJob, installInfo, readPackage, packageMeta, sendStock} from '../../lib/onboard.svelte.js';
  import {canWrite, chooseCard, writeCard, verifyFileList, download} from '../../lib/onboardCard.js';
  import {W, SOURCES} from '../../lib/onboardWords.js';
  import {komaVersionText} from '../../lib/words.js';
  import Spread from '../../components/onboard/Spread.svelte';
  import SpecSheet from '../../components/ui/SpecSheet.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';
  import PhaseList from '../../components/ui/PhaseList.svelte';
  import BackAction from '../../components/onboard/BackAction.svelte';
  import RadioRow from '../../components/ui/RadioRow.svelte';

  // sub: the screen inside Install Koma on the second trip (9 OF 10), shown on the index line
  let {trip = 1, title = W.steps.card, index = '', sub = '', step = 'card', onnext = next} = $props();

  const mode = st.mode || 'update';
  const rec = $derived(trip === 3 ? 'card3' : trip === 2 ? 'card2' : 'card');
  // the trip that writes Koma's file offers where it goes
  const pkgTrip = $derived((mode === 'install' && trip === 2) || (mode === 'update' && trip === 1));
  const irom = $derived(pkgTrip && st.where === 'irom');
  const fsRoute = canWrite();
  let info = $state(null);
  let pkg = $state(packageMeta());
  // a card this trip already wrote and read back (a reload, or Back and forth) shows as ready
  let phase = $state(untrack(() => st[rec]) ? 'ready' : 'idle');   // idle | prep | write | verify | ready | failed | saved
  let failedAt = $state('');                        // card (refused before writing) | prep | write | verify
  let why = $state('');
  let names = $state([]);
  let detail = $state('');
  let stock = $state('');                         // '' | 'checking' | 'bad'
  let t0 = $state(0);
  let folder = $state();
  let ricoh = $state();

  onMount(load);
  async function load() {
    try {
      info = await installInfo(true);
      const p = await readPackage();
      pkg = p ? {version: p.version, build: p.build} : null;
    } catch (e) { why = 'fetch'; phase = 'failed'; failedAt = 'prep'; }
  }

  const flashTrip = $derived((mode === 'install' && trip === 1) || mode === 'official');
  // the bridge builds the firmware and the package from Ricoh's file
  const needsStock = $derived(!!info && (flashTrip || pkgTrip) && !info.firmware.stock);
  const version = $derived(pkg && pkg.version ? pkg.version : '');
  const versionText = $derived(komaVersionText(version) || 'Koma');

  function pickWhere(w) {
    if (st.where === w) return;
    st.where = w; st[rec] = null; st.card3 = null; save();
    phase = 'idle'; why = ''; names = []; detail = '';
  }
  function fail(at, e) {
    phase = 'failed'; failedAt = at;
    why = (e && e.why) || 'write'; names = (e && e.names) || []; detail = (e && (e.detail || e.message)) || '';
  }
  async function job() {
    try { return await cardJob(mode, trip); }
    catch (e) { fail('prep', {why: 'fetch', detail: e.message}); return null; }
  }
  async function pick() {
    let dir;
    try { dir = await chooseCard(); } catch (e) { return; }   // the user closed the picker
    why = ''; names = []; detail = '';
    phase = 'prep';
    const j = await job();
    if (!j) return;
    try {
      const res = await writeCard(dir, j, p => { phase = p; t0 = Date.now(); });
      done(res, dir.name);
    } catch (e) { fail(e.stage === 'card' || e.stage === 'check' ? 'card' : e.stage === 'verify' ? 'verify' : 'write', e); }
  }
  function done(res, name) {
    st[rec] = {at: Date.now(), folder: name || '', files: res.files, removed: res.removed};
    save();
    phase = 'ready';
  }
  // Download route (no File System Access): save the files, then read them back from the folder the user chooses,
  // including that Koma's old files are gone (the user deletes them; a refusal names the one still there).
  async function saveFiles() {
    const j = await job();
    if (!j) return;
    for (const f of j.files) download(f.bytes, f.name);
    phase = 'saved'; t0 = Date.now();
  }
  async function checkFolder(e) {
    const input = e.currentTarget, list = Array.from(input.files || []);
    input.value = '';
    if (!list.length) return;
    phase = 'verify'; t0 = Date.now(); why = '';
    const j = await job();
    if (!j) return;
    try { done(await verifyFileList(list, j), String(list[0].webkitRelativePath || '').split('/')[0]); }
    catch (err) { fail(err.stage === 'card' || err.stage === 'check' ? 'card' : err.stage === 'verify' ? 'verify' : 'write', err); }
  }
  async function takeRicoh(e) {
    const f = e.currentTarget.files && e.currentTarget.files[0];
    e.currentTarget.value = '';
    if (!f) return;
    stock = 'checking';
    const ok = await sendStock(f);
    stock = ok ? '' : 'bad';
    if (ok) info = await installInfo(true);
  }

  // the backup is a phase only when there was one (nothing to back up: no phase, rather than a skipped one)
  const phases = $derived([
    ...(trip === 1 && st.backup && st.backup.b64 ? [{id: 'backup', label: W.card.phases.backup, state: 'done'}] : []),
    {id: 'write', label: W.card.phases.write, since: t0 || null,
      // the download route: the user copies the files, so writing stays open until the read-back
      state: phase === 'write' || phase === 'saved' || (trip === 3 && !fsRoute && phase === 'idle') ? 'now' : phase === 'failed' && failedAt === 'write' ? 'failed'
        : phase === 'verify' || phase === 'ready' ? 'done' : 'todo'},
    {id: 'verify', label: W.card.phases.verify, since: t0 || null,
      state: phase === 'verify' ? 'now' : phase === 'ready' ? 'done' : phase === 'failed' && failedAt === 'verify' ? 'failed' : 'todo'},
  ]);
  const rows = $derived(mode === 'none' ? [] : trip === 3 ? [
    {label: W.card.reads, value: W.card.record},
    {label: W.card.rows.removes, value: W.card.removes.installer},
    {label: W.card.rows.stays, value: W.card.stays},
  ] : [
    {label: W.card.rows.writes, value: mode === 'official' ? W.card.writes.official : flashTrip ? W.card.writes.firmware
      : irom ? W.card.writes.installer(versionText) : versionText},
    ...(mode === 'official' ? [{label: W.card.rows.removes, value: W.card.removes.koma}]
      : mode === 'install' && trip === 2 ? [{label: W.card.rows.removes, value: W.card.removes.updateFile}] : []),
    {label: W.card.rows.stays, value: W.card.stays},
  ]);
  const failText = $derived.by(() => {
    if (why === 'still') return (fsRoute ? W.card.whyNot.still : W.card.stillCheck)(names);
    const t = W.card.whyNot[why] || W.card.whyNot.write;
    return typeof t === 'function' ? t(names) : t;
  });
  const lead = $derived(mode === 'none' ? W.card.nothing : needsStock ? W.card.stockLead[pkgTrip ? 'package' : mode === 'official' ? 'official' : 'install']
    : trip === 3 ? (!fsRoute && phase !== 'ready' ? W.card.clearLead : W.oncamera.card3.lead)
    : !fsRoute && phase !== 'ready' ? W.card.saveLead : trip === 2 ? W.oncamera.card.lead : W.card.lead);
  // Every card trip needs a card of 2 GB or less: Ricoh's updater reads SD only, and the Koma firmware's loader runs
  // before any SDHC card is mounted, so a GRMOD.BIN (Koma's, or the /IROM installer) on a larger card is never read
  // (docs/how-it-works.md, HW on 64 GB cards).
  const lines = $derived(mode === 'none' ? [] : needsStock ? [W.card.stockLine]
    : [...(trip === 3 ? W.oncamera.card3.lines : trip === 2 ? W.oncamera.card.lines : W.card.lines),
      ...(!fsRoute && phase === 'saved' ? [W.card.checkLine] : [])]);
  const busy = $derived(phase === 'prep' || phase === 'write' || phase === 'verify');
  const record = $derived(st[rec]);
  const src = $derived(trip === 3 || irom ? SOURCES.irom : trip === 2 ? SOURCES.card2 : SOURCES.card);
  const detailText = $derived([
    record ? 'Card folder: ' + (record.folder || '--') : '',
    ...(record ? record.files.map(f => f.name + ': ' + f.bytes + ' bytes, SHA-256 ' + f.sha.slice(0, 8) + ', read back') : []),
    record && record.removed.length ? 'Removed: ' + record.removed.join(', ') : '',
    trip === 3 ? 'Reads IRODUMP.TXT (the installer’s record: result=installed or same, the copy valid and Koma ' + version + '’s size, end=1); removes GRMOD.BIN (the installer) and KOMAPKG.BIN, so the card no longer hides /IROM.'
      : irom ? 'Writes GRMOD.BIN (the /IROM installer, irodump) and KOMAPKG.BIN (Koma ' + version + ')' + (mode === 'install' ? '; removes QRFLECT3' : '') + ', and an old IRODUMP.TXT, KOMALOG.TXT, KOMARM.TXT and KOMALS.TXT.'
      : mode === 'install' && trip === 1 ? 'Writes QRFLECT3 (the Koma 1.0 firmware: SD/SDHC driver and /IROM fallback, tag 240K1.0, built here from Ricoh’s file), alone on the card; removes an old GRMOD.BIN, so the first start after the update runs the firmware by itself.'
      : mode === 'install' ? 'Writes GRMOD.BIN (Koma ' + version + '); removes QRFLECT3 and KOMALOG.TXT, so the log after the restart is new.'
      : mode === 'update' ? 'Writes GRMOD.BIN (Koma ' + version + '); removes KOMALOG.TXT, so the log after the restart is new.'
      : mode === 'official' ? 'Writes QRFLECT3 (Ricoh 2.40); removes GRMOD.BIN, KOMALOG.TXT and PARAM.CUR (backed up first).' : '',
    needsStock ? 'Ricoh’s file: gr-240.exe from Ricoh’s GR DIGITAL 2.40 update, or the qrflect3 inside it.' : '',
    trip === 1 && st.backup && st.backup.b64 ? 'Backup: ' + st.backup.name : '',
    detail,
    'Source: ' + src[0] + ' [' + src[1] + ']',
  ].filter(Boolean).join('\n'));
</script>

<Spread {step} index={index || st.step + 1 + ' / ' + plan().length} {sub} {title} {lead} {lines}>
  {#snippet body()}
    {#if mode !== 'none'}
      {#if pkgTrip && !needsStock}
        <fieldset class="where" disabled={busy} data-testid="card-where">
          <legend class="micro">{W.card.where}</legend>
          <RadioRow name="where" value="card" checked={st.where !== 'irom'} label={W.card.whereCard.label}
            text={W.card.whereCard.text} onchange={() => pickWhere('card')} testid="where-card" />
          <RadioRow name="where" value="irom" checked={st.where === 'irom'} label={W.card.whereIrom.label}
            text={W.card.whereIrom.text} onchange={() => pickWhere('irom')} testid="where-irom" />
        </fieldset>
      {/if}
      {#if !needsStock}<SpecSheet {rows} label={title} testid="card-facts" />{/if}
      <!-- after the sheet's last hairline, 32 px before the phase bar, so two rules never read as one double rule -->
      <div class:apart={!needsStock}><PhaseList {phases} label={title} testid="card-phases" /></div>
      <div class="slot">
        {#if phase === 'ready'}
          <StatusLine tone="ok" text={trip === 3 ? W.card.readyIrom : W.card.ready} testid="card-status" />
        {:else if phase === 'failed'}
          <StatusLine tone="bad" text={failText} testid="card-status" />
        {:else if stock === 'checking'}
          <StatusLine text={W.card.stockChecking} testid="card-status" />
        {:else if stock === 'bad'}
          <StatusLine tone="bad" text={W.card.stockBad} testid="card-status" />
        {/if}
      </div>
      <details class="details"><summary>{W.details}</summary><pre>{detailText}</pre></details>
    {/if}
    <input class="sr-only" type="file" tabindex="-1" aria-hidden="true" bind:this={folder} onchange={checkFolder} webkitdirectory multiple />
    <input class="sr-only" type="file" tabindex="-1" aria-hidden="true" bind:this={ricoh} onchange={takeRicoh} />
  {/snippet}
  {#snippet actions()}
    <BackAction onclick={back} disabled={busy} />
    {#if mode === 'none' || phase === 'ready'}
      <button class="btn primary" type="button" onclick={onnext} data-testid="setup-next">{W.next}</button>
    {:else if needsStock}
      <button class="btn primary" class:onb-wait={stock === 'checking'} type="button" onclick={() => ricoh.click()}
        disabled={stock === 'checking'} data-testid="setup-stock">{W.card.stockChoose}</button>
    {:else if !fsRoute}
      {#if trip === 3 || phase === 'saved' || phase === 'failed' || phase === 'verify'}
        <button class="btn primary" class:onb-wait={busy} type="button" onclick={() => folder.click()} disabled={busy} data-testid="setup-check">{W.card.check}</button>
      {:else}
        <button class="btn primary" class:onb-wait={busy || !info} type="button" onclick={saveFiles} disabled={busy || !info}
          data-testid="setup-save">{W.card.save}</button>
      {/if}
    {:else}
      <button class="btn primary" class:onb-wait={busy || !info} type="button" onclick={pick} disabled={busy || !info}
        data-testid="setup-choose">{phase === 'failed' ? W.card.chooseAgain : W.card.choose}</button>
    {/if}
  {/snippet}
</Spread>

<style>
  /* the status line's place is kept, so Details and the action row do not move when it appears */
  .slot { min-height: 40px; display: grid; align-content: start; }
  .apart { margin-top: 8px; }
  .where { border: 0; margin: 0 0 24px; padding: 0; min-width: 0; }
  .where legend { padding: 0; margin-bottom: 8px; }
</style>
