<script>
  // The full view: the photo on the photo matte at or below its own size, never upscaled. To
  // the right (below on a phone): the file number, then the readout read from the file's own EXIF: the exposure on one
  // line (aperture, shutter and ISO in EXPOSURE_ORDER, whatever the file carries) and the date and time under it, values
  // that name themselves, so no labels; then the one fact that needs a label, whether it is
  // imported. No look row: the files carry no look. The playback keys move between photos; file name, card folder,
  // size, pixels and where the copy is are under Details. Left and right arrows move, and so does a sideways swipe on
  // the photo; Esc closes. The selection box beside Download is the contact sheet's own.
  import {TOKEN} from '../../lib/bridge.js';
  import {ph, loadFull, imported, selected, pick, keyOf, fileNumber, saveUrl, mb, busyLine, release}
    from '../../lib/photos.svelte.js';
  import {exposureParts} from '../../lib/photosExif.js';
  import {date as fmtDate, time as fmtTime, when} from '../../lib/format.js';
  import {PHOTOS, STATE} from '../../lib/words.js';
  import Icon from '../../components/Icon.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';

  let {list = [], index = $bindable(-1), onclose} = $props();
  const W = PHOTOS.view;

  let dlg = $state();
  let st = $state({state: 'idle', full: null});
  let dir = 1;
  const p = $derived(index >= 0 && index < list.length ? list[index] : null);

  $effect(() => {
    if (!dlg) return;
    if (p && !dlg.open) { dlg.showModal(); dlg.focus(); }
    else if (!p && dlg.open) dlg.close();
  });

  let attempt = $state(0);
  // Past 10 s a wait shows its elapsed time: the line counts from when this photo's read began.
  let t0 = $state(0);
  $effect(() => {
    const cur = p;
    void attempt;
    if (!cur) return;
    let gone = false;
    t0 = Date.now();
    st.state = 'loading';
    st.full = null;
    loadFull(cur).then(f => {
      if (gone) return;
      st.full = f;
      st.state = 'ok';
      const nxt = list[index + dir];
      if (nxt) loadFull(nxt).catch(() => {});  // the next one in the direction of travel, so it opens at once
    }).catch(e => { if (!gone) st.state = e && e.busy ? 'busy' : 'bad'; });
    return () => { gone = true; };
  });

  function go(d) {
    const j = index + d;
    if (j < 0 || j >= list.length) return;
    dir = d;
    index = j;
  }
  function onkey(e) {
    if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); go(1); }
  }
  function cancel(e) { e.preventDefault(); onclose(); }

  // A sideways swipe on the photo: more than 40 px across with under 30 px of drift up or down moves one photo. The
  // well lets vertical panning through (touch-action: pan-y), so the page still scrolls on a phone.
  let sw = null;
  function down(e) { if (e.isPrimary && e.pointerType !== 'mouse') sw = {x: e.clientX, y: e.clientY}; }
  function up(e) {
    if (!sw) return;
    const dx = e.clientX - sw.x, dy = Math.abs(e.clientY - sw.y);
    sw = null;
    if (Math.abs(dx) > 40 && dy < 30) go(dx < 0 ? 1 : -1);
  }

  const num = $derived(p ? fileNumber(p.name) : '');
  const x = $derived(st.full ? st.full.exif : null);
  const day = $derived(fmtDate(x && x.taken ? x.taken : p ? p.date : ''));
  const time = $derived(fmtTime(x && x.taken ? x.taken : p ? p.date : ''));
  // The line under the exposure: the time alone for a photo taken today, else date and time (format.js when).
  const taken = $derived(when(x && x.taken ? x.taken : p ? p.date : ''));
  const parts = $derived(exposureParts(x));
  const where = $derived(p && imported.has(keyOf(p)) ? imported.get(keyOf(p)) : null);
  const pixels = $derived(x && x.width && x.height ? [x.width, x.height] : p && p.width ? [p.width, p.height] : null);
  // The position readout keeps one width for the whole card, so the keys beside it never move.
  const posWidth = $derived(String(list.length).length * 2 + 3);
  const busy = $derived(busyLine());
  const sel = $derived(!!p && selected.has(p.path));
</script>

<dialog bind:this={dlg} class="pv" aria-labelledby="pv-title" oncancel={cancel} onkeydown={onkey} tabindex="-1"
  data-testid="photo-view">
  {#if p}
    <!-- the swipe is a shortcut; the arrow keys and the two buttons do the same for every input -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="well" onpointerdown={down} onpointerup={up} onpointercancel={() => (sw = null)}>
      {#if st.state === 'ok'}
        <img src={st.full.url} alt={W.alt(num, day, time)} data-testid="full-photo">
      {/if}
    </div>

    <div class="info">
      <div class="top">
        <h2 id="pv-title" class="num t"><span class="sr-only">{W.titleWord}</span>{num}</h2>
        <button class="close" type="button" onclick={onclose} aria-label={W.close} data-testid="view-close"><Icon name="x" size={20} /></button>
      </div>

      <!-- The exposure keeps its line while the file is read, so paging never moves what is below it; a file read
           with no exposure in it gives the line up. -->
      <div class="readout">
        <div class="exw" class:held={st.state !== 'ok' || parts.length > 0}>
          {#if parts.length}<p class="ex num" data-testid="view-exposure">{#each parts as v}<span>{v}</span>{/each}</p>{/if}
        </div>
        <p class="when num" data-testid="view-taken"><span class="sr-only">{W.taken} </span>{taken}</p>
      </div>

      <dl class="spec" data-testid="view-spec">
        <div class="row"><dt>{W.imported}</dt><dd data-testid="view-imported">{where === null ? W.notYet : W.yes}</dd></div>
      </dl>

      {#if st.state === 'loading'}
        <StatusLine rule text={W.reading} since={t0} />
      {:else if st.state === 'bad'}
        <StatusLine tone="bad" text={W.failed} action={{label: W.tryAgain, onclick: () => attempt++}} />
      {:else if st.state === 'busy'}
        <StatusLine text={ph.list === 'stopping' ? busy.stopping : busy.text} rule={ph.list === 'stopping'}
          action={ph.list === 'stopping' ? null : {label: busy.action, onclick: () => release().then(() => attempt++),
            testid: 'view-busy-action'}} />
      {/if}

      <div class="acts">
        <div class="keys">
          <button class="btn quiet key" type="button" onclick={() => go(-1)} disabled={index <= 0}
            aria-label={W.previous} data-testid="view-prev"><Icon name="back" size={20} /></button>
          <span class="pos num" style="min-width:{posWidth}ch" data-testid="view-pos">{index + 1} / {list.length}</span>
          <button class="btn quiet key" type="button" onclick={() => go(1)} disabled={index >= list.length - 1}
            aria-label={W.next} data-testid="view-next"><Icon name="chevron" size={20} /></button>
        </div>
        <div class="save">
          <label class="pick small">
            <input type="checkbox" checked={sel} onclick={e => pick(p, e.currentTarget.checked)} data-testid="view-select">
            <span>{PHOTOS.selectedLabel}</span>
          </label>
          <button class="btn quiet" type="button" disabled={st.state !== 'ok'} onclick={() => saveUrl(st.full.url, p.name)}
            data-testid="view-download">{W.download}</button>
        </div>
      </div>
      {#if TOKEN}<p class="small muted keep" data-testid="view-save-note">{STATE.saveToPhotos}</p>{/if}

      <details class="details">
        <summary>{W.details}</summary>
        <dl class="tech small">
          <div><dt>{W.file}</dt><dd>{p.name}</dd></div>
          <div><dt>{W.cardFolder}</dt><dd>{p.folder}</dd></div>
          <div><dt>{W.size}</dt><dd class="num">{mb(p.size)}<span class="unit">{PHOTOS.mb}</span></dd></div>
          {#if pixels}<div><dt>{W.pixels}</dt><dd class="num">{pixels[0]} × {pixels[1]}</dd></div>{/if}
          {#if where}<div><dt>{W.copy}</dt><dd class="path" data-testid="view-copy">{where}</dd></div>{/if}
        </dl>
      </details>
    </div>
  {/if}
</dialog>

<style>
  .pv {
    position: fixed; inset: 0; width: 100%; height: 100%; max-width: none; max-height: none;
    margin: 0; padding: 0; border: 0; background: var(--bg); color: var(--ink); outline: none;
  }
  .pv[open] { display: grid; grid-template-columns: minmax(0, 1fr) 380px; animation: fade var(--t) var(--ease); }
  .pv::backdrop { background: var(--bg); }
  @keyframes fade { from { opacity: 0; } }

  .well { background: var(--photo-bg); display: grid; place-items: center; padding: 32px; min-height: 0; min-width: 0;
    touch-action: pan-y; }
  .well img { max-width: 100%; max-height: 100%; border-radius: var(--r-img); }

  .info {
    border-left: 1px solid var(--line); padding: 16px 28px 28px; overflow: auto;
    display: flex; flex-direction: column; min-height: 0;
  }
  .top { display: flex; align-items: center; justify-content: space-between; min-height: 40px; }
  .top .t { font-size: var(--fs-title, 22px); line-height: var(--lh-title, 28px); font-weight: 500; letter-spacing: -.01em; }
  .close {
    width: 40px; height: 40px; margin-right: -10px; display: grid; place-items: center;
    background: none; border: 1px solid transparent; border-radius: var(--r-ctl); color: var(--sub);
    transition: color var(--t-fast), border-color var(--t-fast);
  }
  .close:hover { color: var(--ink); border-color: var(--line); }

  /* The readout under the number: the exposure in --fs-lead, the date and time under it in --fs-small, no labels. */
  .readout { margin-top: 4px; }
  .exw.held { min-height: 24px; }
  .ex { display: flex; flex-wrap: wrap; gap: 0 14px; font-size: var(--fs-lead, 16px); line-height: var(--lh-lead, 24px);
    font-weight: 500; color: var(--ink); }
  .when { margin-top: 4px; font-size: var(--fs-small); line-height: var(--lh-small); color: var(--sub); }

  .spec { margin: 24px 0 0; border-top: 1px solid var(--line); }
  .row { display: grid; grid-template-columns: 144px minmax(0, 1fr); align-items: baseline; min-height: 40px;
    padding: 10px 0; border-bottom: 1px solid var(--line); }
  dt { color: var(--sub); font-size: var(--fs-body); line-height: var(--lh-body); }
  dd { margin: 0; font-size: var(--fs-body); line-height: var(--lh-body); color: var(--ink); }
  .info > :global(.sl) { margin-top: 24px; }

  /* Playback keys: two icon-only quiet keys around the position, the one download beside them. */
  .acts { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 16px; margin-top: 24px; }
  .keys { display: flex; align-items: center; gap: 8px; }
  .keys .key { width: 40px; height: 40px; padding: 0; }
  .pos { color: var(--ink); font-size: var(--fs-body); line-height: var(--lh-body); text-align: center; }
  .save { display: flex; align-items: center; gap: var(--s4); }
  /* The contact sheet's box: 12 px, no fill, its border all there is of it; ink when ticked. */
  .pick { position: relative; display: inline-flex; align-items: center; gap: 6px; color: var(--sub); cursor: pointer;
    user-select: none; min-height: 40px; }
  .pick input {
    appearance: none; -webkit-appearance: none; margin: 0; flex: none; width: 12px; height: 12px;
    border: 1px solid var(--faint); border-radius: 0; background: transparent; cursor: pointer;
    transition: background-color var(--t-fast), border-color var(--t-fast), box-shadow var(--t-fast);
  }
  .pick:hover input { border-color: var(--sub); }
  .pick input:checked { background: var(--ink); border-color: var(--ink); box-shadow: inset 0 0 0 2px var(--bg); }
  .pick input:checked + span { color: var(--ink); }
  .pick input:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
  .keep { margin-top: var(--s2); }

  .details { margin-top: 24px; }
  .tech { margin: 10px 0 0; display: grid; gap: 6px; color: var(--sub); }
  .tech div { display: grid; grid-template-columns: 144px minmax(0, 1fr); }
  .tech dt, .tech dd { font-size: var(--fs-small); line-height: var(--lh-small); color: var(--sub); }
  .tech dd { color: var(--ink); overflow-wrap: anywhere; }
  .unit { margin-left: 0.2em; color: var(--sub); }

  @media (max-width: 720px) {
    .pv[open] { grid-template-columns: minmax(0, 1fr); grid-template-rows: auto auto; overflow: auto; align-content: start; }
    .well { order: 2; padding: 0; aspect-ratio: 4 / 3; width: 100%; }
    .well img { border-radius: 0; }
    .info { display: contents; }
    .top { order: 1; padding: 2px 16px; border-bottom: 1px solid var(--line); }
    .close { margin-right: -10px; width: 44px; height: 44px; }
    .readout, .spec, .acts, .info > :global(.sl), .details, .keep { margin-left: 16px; margin-right: 16px; }
    .readout { order: 3; margin-top: 12px; }
    .spec { order: 4; margin-top: 16px; }
    .info > :global(.sl) { order: 5; margin-top: 16px; }
    .acts { order: 6; margin-top: 16px; }
    .keep { order: 6; }
    .pick { min-height: 44px; }
    .keys .key { width: 44px; height: 44px; }
    .acts .btn { height: 44px; }
    .details { order: 7; margin-top: 24px; margin-bottom: calc(24px + env(safe-area-inset-bottom)); }
    .row { grid-template-columns: 128px minmax(0, 1fr); min-height: 48px; padding: 14px 0; }
    .tech div { grid-template-columns: 128px minmax(0, 1fr); }
  }
</style>
