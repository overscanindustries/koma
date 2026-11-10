<script>
  // Edit look: the photo as shot against the look (a draggable divider), then the look's rows in the camera's order
  // with their stock value on each scale, the ADJ LEVER row, and Return to stock values. Edits stay here, one undo step
  // per gesture (Cmd/Ctrl+Z), until Apply to camera sends them and reads them back. The pending bar is the area's: it
  // counts and applies the edits of every look, this one and others, in one Apply. The look on the camera stays the look
  // on the camera; setting one is the gallery's job.
  import {S, B} from '../../lib/settings.svelte.js';
  import {A} from '../../lib/apply.svelte.js';
  import * as LS from '../../lib/looksStore.js';
  import {LK, stateOf, cameraState, edit, undoLast, changesOf, rowsFor, ADJ_ROW, lookIaOk, pendingCount}
    from '../../lib/looks.svelte.js';
  import {screen} from '../../lib/ui.svelte.js';
  import {W, opt} from '../../lib/looksWords.js';
  import BackLink from '../../components/BackLink.svelte';
  import Segmented from '../../components/ui/Segmented.svelte';
  import Split from '../../components/looks/Split.svelte';
  import Detent from '../../components/looks/Detent.svelte';
  import Pending from '../../components/looks/Pending.svelte';

  let {look} = $props();
  const id = $derived(look.id);
  const st = $derived(stateOf(id));
  const was = $derived(cameraState(id));
  const rows = $derived(rowsFor(st.kind));
  const sameType = $derived((st.kind & 3) === (was.kind & 3));
  const changes = $derived(changesOf(id));
  const pend = $derived(pendingCount());
  const on = $derived.by(() => { void S.rev; const c = B.orig ? LS.current(B.orig) : null; return !!(c && c.id === id); });
  const atStock = $derived(LS.sameValues(st, LS.stockState(id, st.adj)));
  const done = $derived(LK.status && LK.status.tone === 'ok' && (LK.status.id === id || (LK.status.ids || []).includes(id))
    && !pend ? LK.status.text : '');
  // Before the Look IA: no ADJ LEVER setting, and STANDARD is plain NORMAL with no values of its own.
  const iaOk = $derived(lookIaOk());
  const locked = $derived(id === LS.STD && !iaOk);
  let gesture = false;
  function slide(r, i) { edit(id, s => r.set(s, i), !gesture); gesture = true; }
  function set(r, i) { edit(id, s => r.set(s, i)); }
  function setAdj(v) { edit(id, s => { s.adj = v; }); }
  function toStock() { edit(id, s => { Object.assign(s, LS.stockState(id, s.adj)); }); }
  function changed(r) { return !sameType || r.index(st) !== r.index(was); }
  const toneOff = r => (st.kind & 3) === LS.TYPE.bw && r.key === 'p7' && st.p[LS.P.s6] === 0;

  function key(e) {
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'z' && !A.open) {
      if (undoLast(id)) e.preventDefault();
    }
  }

  // The photo stays in view while the rows scroll, pinned below whatever the shell pins at the top of the scrolling
  // page (its status line), measured rather than assumed: the shell's top line is not this area's to size.
  let section = $state();
  let pinTop = $state(0);
  $effect(() => {
    const main = section && section.closest('main');
    if (!main) return;
    const pinned = [...main.children].filter(el => !el.contains(section) && getComputedStyle(el).position === 'sticky');
    const measure = () => { pinTop = Math.round(pinned.reduce((h, el) => h + el.getBoundingClientRect().height, 0)); };
    measure();
    const ro = new ResizeObserver(measure);
    pinned.forEach(el => ro.observe(el));
    return () => ro.disconnect();
  });

  // Phone: once the rows scroll under it, the pinned photo shrinks to about 40 % of the window (the short-phone rule),
  // and the page's scroll padding equals what is pinned, so a row reached by keyboard is never cut under the photo.
  let pic = $state(), sentinel = $state();
  let shrunk = $state(false);
  $effect(() => {
    const main = section && section.closest('main');
    if (!main || !sentinel || !screen.phone) { shrunk = false; return; }
    const io = new IntersectionObserver(([e]) => { shrunk = !e.isIntersecting && e.boundingClientRect.top < e.rootBounds.top + 1; },
      {root: main, rootMargin: '-' + pinTop + 'px 0px 0px 0px'});
    io.observe(sentinel);
    return () => io.disconnect();
  });
  $effect(() => {
    const main = section && section.closest('main');
    if (!main || !pic || !screen.phone) return;
    const set = () => { main.style.scrollPaddingTop = (pinTop + Math.round(pic.getBoundingClientRect().height)) + 'px'; };
    set();
    const ro = new ResizeObserver(set);
    ro.observe(pic);
    return () => { ro.disconnect(); main.style.scrollPaddingTop = ''; };
  });
</script>

<svelte:window onkeydown={key} />

<section class="ed" aria-labelledby="ed-title" data-testid="look-editor" data-look={look.slug} bind:this={section}
  style:--ed-pin={pinTop + 'px'}>
  <BackLink label={W.back} href="#/looks" />
  <header class="ed-head">
    <h1 id="ed-title" class="title" tabindex="-1">{look.name}</h1>
    <p class="lede">{look.line}</p>
  </header>

  <i class="ed-top" bind:this={sentinel} aria-hidden="true"></i>
  <div class="ed-cols">
    {#if LK.src !== 'none'}
      <div class="ed-pic" class:on class:shrunk bind:this={pic}>
        <span class="rule" aria-hidden="true"></span>
        <Split {st} name={look.name} />
      </div>
    {/if}

    <!-- with no photo to draw on (the bundled sample did not decode) the rows take the photo's columns -->
    <div class="ed-ctl" class:solo={LK.src === 'none'}>
      {#if locked}
        <!-- before the Look IA: STANDARD is the camera's NORMAL, with no values of its own; the one line, no dead controls -->
        <p class="ed-note small" data-testid="look-locked">{W.stdOld}</p>
      {:else}
      <div class="ed-rows" role="group" aria-label={look.name}>
        {#each rows as r (r.key + r.label)}
          {@const v = r.index(st)}
          {#if r.kind === 'scale'}
            <Detent id={r.key} label={r.label} options={r.options} value={v} def={r.def(id)} changed={changed(r)}
              disabled={toneOff(r)} reason={toneOff(r) ? W.toneOff : ''} oninput={i => slide(r, i)}
              onchange={() => { gesture = false; }} />
          {:else if r.kind === 'list'}
            <div class="ed-row" data-row={r.key}>
              <label class="lab camlabel" for={'s-' + r.key}>{r.label}</label>
              <span class="ctl">
                {#if changed(r)}<i class="mark" aria-hidden="true"></i>{/if}
                <select id={'s-' + r.key} class="select camlabel" value={String(v)} onchange={e => set(r, Number(e.currentTarget.value))}>
                  {#each r.options as o, i}<option value={String(i)}>{opt(o)}</option>{/each}
                </select>
              </span>
            </div>
          {:else}
            <div class="ed-row" data-row={r.key}>
              <span class="lab camlabel" id={'l-' + r.key}>{r.label}</span>
              <span class="ctl">
                {#if changed(r)}<i class="mark" aria-hidden="true"></i>{/if}
                <Segmented name={'g-' + r.key} labelledby={'l-' + r.key} small value={v}
                  options={r.options.map((o, i) => ({value: i, label: o}))} onchange={i => set(r, i)} />
              </span>
            </div>
          {/if}
        {/each}
        {#if iaOk}
          <div class="ed-row adj" data-row="adj">
            <span class="lab camlabel" id="l-adj">{ADJ_ROW.label}</span>
            <span class="ctl">
              {#if st.adj !== was.adj}<i class="mark" aria-hidden="true"></i>{/if}
              <Segmented name="g-adj" labelledby="l-adj" small value={st.adj} datak="adj"
                options={[{value: false, label: ADJ_ROW.off}, {value: true, label: ADJ_ROW.on}]} onchange={setAdj} />
            </span>
            <p class="help small" id="h-adj">{W.adjHelp}</p>
          </div>
        {:else}
          <!-- before the Look IA: no ADJ LEVER setting, so no control; the reason, which names the row, in its place -->
          <div class="ed-row adj older" data-row="adj">
            <p class="small" data-testid="adj-older">{W.adjOld}</p>
          </div>
        {/if}
      </div>

      <div class="ed-stock">
        <button class="btn danger" type="button" onclick={toStock} disabled={atStock} aria-describedby="ed-stock-say"
          data-testid="return-to-stock">{W.stock}</button>
        <p class="small" id="ed-stock-say">{atStock ? W.atStock : W.stockSays(look.name)}</p>
      </div>
      {/if}
    </div>
  </div>
</section>

{#if pend}
  <div class="lk-bar" role="region" aria-label={'Changes to ' + look.name}><Pending {id} /></div>
{:else if done}
  <div class="lk-bar" role="region" aria-label={'Changes to ' + look.name}>
    <div class="lk-row"><div class="lk-bar-text"><span class="small say" role="status"><i class="dot ok"></i>{done}</span></div></div>
  </div>
{/if}
