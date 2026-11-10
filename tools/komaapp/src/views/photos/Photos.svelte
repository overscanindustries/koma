<script>
  // Photos. A contact sheet of the card's photos grouped by day (GET /photos: the DCIM folders
  // only), each cell the camera's own EXIF thumbnail; a full view with the file's EXIF; Download selected; and Import
  // new, which the bridge runs into a folder on the computer that runs Koma and remembers (photoimport.py). MOCK:
  // proven against the simulated camera only. The bridge calls underneath are HW (GET /photos, RETR, ?thumb=1, 409
  // remote_on); the import copies on the host side and sends nothing new to the camera.
  import {onMount, untrack} from 'svelte';
  import {cam} from '../../lib/camera.svelte.js';
  import {screen} from '../../lib/ui.svelte.js';
  import {TOKEN} from '../../lib/bridge.js';
  import {ph, imported, selected, open, close, readList, busyLine, release, keyOf, dayText, newPhotos, byDay, samplePath,
    pickDay, downloadSelected, startImport, stopImport} from '../../lib/photos.svelte.js';
  import {bytes} from '../../lib/format.js';
  import {PHOTOS as W, STATE} from '../../lib/words.js';
  import Thumb from '../../components/photos/Thumb.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';
  import SectionHead from '../../components/ui/SectionHead.svelte';
  import Details from '../../components/ui/Details.svelte';
  import PhotoView from './PhotoView.svelte';
  import FolderSheet from './FolderSheet.svelte';

  onMount(() => { open(); return close; });
  // A new connection (the camera came back, maybe with another card): read it again.
  let conn = cam.conn;
  $effect(() => { if (cam.conn !== conn) { conn = cam.conn; untrack(open); } });
  // Remote control went off elsewhere (another tab left Shoot, the webcam stopped): read the card again.
  $effect(() => { if (ph.list === 'busy' && cam.st && cam.st.tether === false && !cam.st.webcam) untrack(readList); });

  // The contact sheet keeps a fixed 160 x 120 module on a desktop: as many columns as fit the content width (at most
  // 1120 px), and the page as wide as the columns, so the actions sit on the sheet's right edge.
  let root = $state();
  let avail = $state(1120);
  $effect(() => {
    const host = root && root.parentElement;
    if (!host || typeof ResizeObserver === 'undefined') return;
    const cs = getComputedStyle(host);
    avail = host.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const ro = new ResizeObserver(([e]) => { avail = e.contentRect.width; });
    ro.observe(host);
    return () => ro.disconnect();
  });
  const cols = $derived(Math.max(1, Math.floor((Math.min(avail, 1120) + 2) / 162)));

  const days = $derived(byDay());
  const fresh = $derived(newPhotos());
  const anyImported = $derived(ph.photos.some(p => imported.has(keyOf(p))));
  // Per-frame state: on a card that holds both, NEW marks what is not yet imported and an
  // unmarked frame is imported; on a card that is all imported, the day heads say so and no frame carries a tag; on a
  // card with nothing imported the button already says how many are new. Frames are never hidden.
  const mixed = $derived(anyImported && fresh.length > 0);
  const allImported = $derived(anyImported && fresh.length === 0);
  const tagOf = p => (mixed && !imported.has(keyOf(p)) ? 'new' : null);
  // A phone paired with Koma: the import folder is on the computer, and what the phone wants is the files themselves.
  const paired = $derived(!!TOKEN || (!!ph.imp && ph.imp.choose === false));
  const job = $derived(ph.job);
  const running = $derived(!!job && job.state === 'running');
  const nSel = $derived(selected.size);
  const dl = $derived(ph.dl);
  // The action area stays while the camera is held elsewhere, so nothing moves; only the import waits for it.
  const showAct = $derived(ph.photos.length > 0 && ph.list !== 'error');
  const free = $derived((ph.list === 'ok' || ph.list === 'reading') && cam.state === 'cam');
  const canImport = $derived(!!ph.imp && free && !running && fresh.length > 0);
  const dlBusy = $derived(!free || (!!dl && dl.state === 'running'));
  const hasBtns = $derived((nSel > 0 && !screen.phone) || canImport);
  const showSheet = $derived(ph.photos.length > 0 && ph.list !== 'error');
  const busy = $derived(busyLine());

  // How an import ended. The status line carries what was imported, and what still waits on the camera (the Import
  // button is then the action); the rest goes under Details.
  const checked = $derived(job ? job.copied + job.already : 0);
  const skippedBy = why => (job ? job.skipped.filter(s => s.why === why).length : 0);
  const jobEnd = $derived.by(() => {
    if (!job || running) return null;
    const c = checked, n = job.total, why = job.stop && job.stop.why;
    if (job.state === 'done') {
      if (!n) return {tone: 'ok', text: W.job.nothing};
      const inc = skippedBy('incomplete'), unr = skippedBy('unreadable'), out = [];
      if (c) out.push(W.job.done(c));
      if (inc) out.push(W.job.incomplete(inc));
      if (unr && !c) out.push(W.job.unreadable(unr));
      return {tone: c ? 'ok' : 'wait', text: out.join(' ')};
    }
    if (job.state === 'stopped') return {tone: 'wait', text: (W.job.stopped[why] || W.job.stopped.cancelled)(c, n)};
    return {tone: 'bad', text: (W.job.failed[why] || W.job.failed.other)(c, n)};
  });
  const jobDetails = $derived.by(() => {
    if (!jobEnd) return [];
    // A count only where no file line follows: renamed and skipped files are named one by one.
    const out = [];
    if (job.copied) out.push(W.folder.help(samplePath(fresh[0] || ph.photos[0])));
    if (job.already) out.push(W.job.already(job.already));
    for (const r of job.renamed) out.push(W.job.renamedAs(r.name, r.saved_as));
    for (const s of job.skipped) out.push((W.job.skipped[s.why] || W.job.skipped.unreadable)(s.name));
    if (job.state === 'failed' && job.stop && job.stop.detail) out.push(job.stop.detail);
    return out;
  });
  // The import's clock is the bridge's: the line counts from when the job began.
  const jobSince = $derived(running ? Date.now() - (job.elapsed_s || 0) * 1000 : null);
  const copiedText = $derived(running ? W.job.copied(checked, job.total) + (job.bytes_total >= 1e6
    ? ' · ' + bytes(job.bytes_done) + ' ' + W.job.of + ' ' + bytes(job.bytes_total) : '') : '');

  // The full view and the folder sheet
  let viewIndex = $state(-1);
  let lastOpened = '';
  function openPhoto(p) { lastOpened = p.path; viewIndex = ph.photos.findIndex(q => q.path === p.path); }
  function closeView() {
    const back = viewIndex >= 0 && ph.photos[viewIndex] ? ph.photos[viewIndex].path : lastOpened;
    viewIndex = -1;
    if (/^#\/photos\/./.test(location.hash)) {
      try { history.replaceState(null, '', '#/photos'); } catch (e) { /* the address keeps the photo */ }
    }
    queueMicrotask(() => {
      const el = root && [...root.querySelectorAll('[data-photo]')].find(e => e.dataset.photo === back);
      const btn = el && el.querySelector('.frame');
      if (btn) { btn.focus({preventScroll: true}); btn.scrollIntoView({block: 'nearest'}); }
    });
  }
  // #/photos/<path> (Overview's newest photo links here): that photo opens in the full view once the card is read.
  let wanted = (() => {
    const m = /^#\/photos\/(.+)$/.exec(location.hash);
    try { return m ? decodeURIComponent(m[1]) : ''; } catch (e) { return ''; }
  })();
  $effect(() => {
    if (!wanted || ph.list !== 'ok') return;
    const p = ph.photos.find(q => q.path === wanted);
    wanted = '';
    if (p) untrack(() => openPhoto(p));
  });

  let folderOpen = $state(false);
  const sampleDay = $derived(((fresh[0] || ph.photos[0] || {}).date || '').slice(0, 10));
  let releasing = $state('');
  function stopHolder() {
    releasing = busy.stopping;
    release().finally(() => { releasing = ''; });
  }
  // A download's line lasts until the selection changes.
  let lastSel = 0;
  $effect(() => { if (nSel !== lastSel) { lastSel = nSel; if (ph.dl && ph.dl.state !== 'running') ph.dl = null; } });
  const hasStatus = $derived((ph.list !== 'ok' && ph.list !== 'idle' && !(ph.list === 'reading' && ph.photos.length)
      && !(ph.list === 'error' && cam.state !== 'cam'))
    || !!job && (running || !!jobEnd) || (!!dl && !screen.phone));
  const download = () => downloadSelected(screen.phone);
</script>

<section class="photos" class:phone={screen.phone} style="--cols:{cols}" aria-labelledby="ph-title" data-testid="photos"
  bind:this={root}>
  <header class="head">
    <h1 id="ph-title" class="title" tabindex="-1">{W.title}</h1>
    {#if showAct}
      <div class="act">
        <!-- The destination line never moves: on a desktop the button row above it keeps its height when it
             holds nothing; on a phone the line comes first and the button row closes. -->
        <div class="btns" class:none={!hasBtns}>
          {#if canImport && paired}
            <button class="link small" type="button" onclick={startImport} data-testid="import">{W.importToComputer(fresh.length)}</button>
          {/if}
          {#if nSel && !screen.phone}
            <span class="small muted" data-testid="downloads-note">{STATE.downloadsNote}</span>
            <button class="link small" type="button" onclick={() => selected.clear()} data-testid="deselect">{W.deselect}</button>
            <button class="btn" class:primary={paired} class:quiet={!paired} type="button" onclick={download}
              disabled={dlBusy} data-testid="download-selected">{W.downloadN(nSel)}</button>
          {/if}
          {#if canImport && !paired}
            <button class="btn primary" type="button" onclick={startImport} data-testid="import">{W.importN(fresh.length)}</button>
          {/if}
        </div>
        {#if ph.imp}
          <p class="dest small">
            <span class="path num" data-testid="dest-sample">{paired ? STATE.onComputer : STATE.toFolder}{ph.imp.display}</span>
            {#if ph.imp.choose && !paired}<button class="link" type="button" onclick={() => (folderOpen = true)} data-testid="choose-folder">{W.chooseFolder}</button>{/if}
          </p>
        {/if}
      </div>
    {/if}
  </header>

  {#if hasStatus}<div class="lines" data-testid="photos-lines">
    {#if ph.list === 'reading' && !ph.photos.length}
      <StatusLine rule text={W.reading} since={ph.readT0} testid="photos-status" />
    {:else if ph.list === 'stopping'}
      <StatusLine rule text={releasing || busy.stopping} since={ph.readT0} testid="photos-status" />
    {:else if ph.list === 'busy'}
      <StatusLine text={busy.text} action={{label: busy.action, onclick: stopHolder, testid: 'busy-action'}} testid="photos-status" />
    {:else if ph.list === 'error' && cam.state === 'cam'}
      <StatusLine tone="bad" text={W.readError[ph.err] || W.readError.read} action={{label: W.readAgain, onclick: readList}}
        testid="photos-status" />
    {/if}

    {#if running}
      {#if job.phase === 'reading'}
        <StatusLine rule text={W.job.reading} since={jobSince} action={{label: W.job.stop, onclick: stopImport}}
          testid="import-status" />
      {:else}
        <StatusLine text={copiedText} since={jobSince} progress={job.bytes_total ? job.bytes_done / job.bytes_total : 0}
          action={{label: W.job.stop, onclick: stopImport}} testid="import-status" />
      {/if}
    {:else if jobEnd}
      <StatusLine tone={jobEnd.tone} text={jobEnd.text} testid="import-status" />
      {#if jobDetails.length}
        <div class="jobd">
          <Details label={W.view.details} testid="import-details">
            <ul class="small">{#each jobDetails as line}<li>{line}</li>{/each}</ul>
          </Details>
        </div>
      {/if}
    {/if}

    {#if dl && !screen.phone}{@render dlLine()}{/if}
  </div>{/if}

  <!-- Download selected: the same status line on a desktop (under the import's) and in a phone's bottom bar. On a
       phone the bar's one button is the failure's action, so the line carries none there. -->
  {#snippet dlLine()}
    {#if dl.state === 'running'}
      <StatusLine text={W.downloading(dl.i, dl.n)} since={dl.t0} testid="download-status" />
    {:else if dl.state === 'done'}
      <StatusLine tone="ok" text={W.downloaded(dl.n)} testid="download-status" />
    {:else if dl.state === 'busy'}
      <StatusLine text={W.downloadStopped(dl.i - 1, dl.n)} testid="download-status" />
    {:else if dl.state === 'failed'}
      <StatusLine tone="bad" text={W.downloadFailed(dl.i, dl.n)} testid="download-status"
        action={screen.phone ? null : {label: W.tryAgain, onclick: download, testid: 'download-again'}} />
    {/if}
  {/snippet}

  {#if ph.list === 'ok' && !ph.photos.length}
    <div class="empty" data-testid="photos-empty">
      <p class="micro">{W.empty.label}</p>
      <p class="et">{W.empty.text}</p>
    </div>
  {:else if showSheet}
    {#each days as d (d.day)}
      {@const allSel = d.photos.every(p => selected.has(p.path))}
      <section class="day" aria-labelledby={'day-' + d.day}>
        <SectionHead label={dayText(d.day)} id={'day-' + d.day}>
          {#snippet aside()}
            <span class="cnt num">{d.photos.length}<span class="sr-only">{W.dayCount(d.photos.length)}</span>{#if allImported}<span
              class="sep">{' · '}</span>{W.tagImported}{/if}</span>
            <button class="link daysel" type="button" onclick={() => pickDay(d.photos, !allSel)}
              aria-label={(allSel ? W.deselect : W.select) + ' ' + dayText(d.day)} data-testid="day-select">{allSel ? W.deselect : W.select}</button>
          {/snippet}
        </SectionHead>
        <ul class="sheet" role="list">
          {#each d.photos as p (p.path)}<Thumb {p} tag={tagOf(p)} onopen={openPhoto} />{/each}
        </ul>
      </section>
    {/each}
  {/if}

  <!-- Phone: the selection pinned above the tab bar. A download's status line takes its own row above the selection
       row, so the count, Deselect and the button stay where the thumb left them while the line comes and goes. -->
  {#if screen.phone && (nSel || dl)}
    <div class="selbar" role="region" aria-label={W.selected(nSel)} data-testid="sel-bar">
      {#if dl}{@render dlLine()}{/if}
      {#if nSel}
        <div class="selrow">
          <span class="selc" data-testid="sel-count" role="status"><span class="num">{nSel}</span> {W.selectedWord}</span>
          <button class="link" type="button" onclick={() => selected.clear()}>{W.deselect}</button>
          <button class="btn" class:primary={paired} class:quiet={!paired} type="button" onclick={download}
            disabled={dlBusy} data-testid="download-selected">{dl && dl.state === 'failed' ? W.tryAgain : W.view.download}</button>
        </div>
      {/if}
    </div>
  {/if}
</section>

<PhotoView list={ph.photos} bind:index={viewIndex} onclose={closeView} />
<FolderSheet open={folderOpen} day={sampleDay} onclose={() => (folderOpen = false)} />

<style>
  .photos { max-width: calc(var(--cols) * 162px - 2px); padding-top: var(--s1); }
  h1:focus { outline: none; }

  .head { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: var(--s4) var(--s6); }
  .head h1 { padding-top: var(--s1); }
  .act { display: grid; justify-items: end; gap: var(--s2); margin-left: auto; }
  .btns { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: var(--s2) var(--s4);
    min-height: var(--ctl-h); }
  .dest { display: flex; flex-wrap: wrap; justify-content: flex-end; align-items: baseline; gap: var(--s1) var(--s3);
    color: var(--sub); }
  .path { overflow-wrap: anywhere; text-align: right; }
  /* Text links keep their look but get the target height: 40 px here, 44 on a phone. */
  .dest .link, .btns .link { padding: var(--s3) 0; margin: calc(-1 * var(--s3)) 0; }

  .lines { display: grid; gap: var(--s4); margin-top: var(--s5); }
  /* The result's Details sit under its sentence, on the sentence's edge (past the dot's gutter); one fact a line. */
  .jobd { margin: calc(-1 * var(--s2)) 0 0 calc(6px + var(--s3)); }
  .jobd ul { list-style: none; margin: 0; padding: 0; color: var(--sub); display: grid; gap: var(--s1); }

  .empty { margin-top: var(--s7); display: grid; gap: var(--s2); }
  .et { color: var(--ink); }

  /* Day heads are SectionHeads: the date, then the count and Select at the rule's right end. A day after a day sits
     closer: the caption row above already leaves part of its 40 px target empty. */
  .day + .day :global(.sh) { margin-top: var(--s5); }
  .cnt { color: var(--faint); }
  .sep { color: var(--faint); }
  .daysel { min-width: 3.9em; text-align: right; margin-left: var(--s4); padding: var(--s3) 0; margin-block: calc(-1 * var(--s3)); font-size: var(--fs-small); }
  .sheet { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(var(--cols), 160px);
    column-gap: 2px; row-gap: var(--s2); }

  .selbar {
    position: sticky; bottom: 0; z-index: 4; margin: var(--s5) calc(-1 * var(--gutter)) 0; padding: var(--s2) var(--gutter);
    display: grid; gap: var(--s1);
    background: var(--bg); border-top: 1px solid var(--line);
  }
  .selbar > :global(.sl) { padding-top: var(--s1); }
  .selrow { display: flex; align-items: center; gap: var(--s4); }
  .selrow .selc { flex: 1; min-width: 0; }
  .phone .dest .link, .selrow .link, .phone .daysel { padding: 14px 0; margin-block: -14px; }

  /* Phone: one column of text, the sheet three across with the thumbnail scaled to the cell. */
  .phone { max-width: none; padding-top: 2px; }
  .phone .head { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s4); }
  .phone .head h1 { padding-top: 0; }
  .phone .act { justify-items: stretch; margin-left: 0; gap: var(--s4); }
  .phone .dest { order: -1; justify-content: flex-start; }
  .phone .btns { justify-content: stretch; }
  .phone .btns.none { display: none; }
  .phone .btns .btn.primary { flex: 1; }
  .phone .path { text-align: left; }
  .phone .lines { margin-top: var(--s4); }
  .phone .empty { margin-top: var(--s6); }
  .phone .day + .day :global(.sh) { margin-top: var(--s2); }
  .phone .sheet { grid-template-columns: repeat(3, minmax(0, 1fr)); }
</style>
