<script>
  // Looks: the IMG SET list as a gallery. The 21 looks in the camera's order, each drawn on the last photo (on the
  // bundled sample when that photo is black and white, or when there is none); the one on the camera carries the amber
  // rule. Choosing a card selects it; the bar at the bottom says what setting it does, holds its ADJ LEVER switch and
  // the two actions, Edit look and Set on the camera, and under it the area's pending changes. Setting a look restarts
  // the camera, so it is never one stray click away.
  import {S, B, readFromCamera} from '../../lib/settings.svelte.js';
  import {A} from '../../lib/apply.svelte.js';
  import {holder} from '../../lib/camera.svelte.js';
  import {setupEntry} from '../../lib/onboard.svelte.js';
  import {busyLine} from '../../lib/photos.svelte.js';
  import {when, elapsed, bytes} from '../../lib/format.js';
  import * as LS from '../../lib/looksStore.js';
  import {LK, stateOf, hasDraft, canWrite, blockedBy, pick, nameOf, loadSource, lookIaOk, edit, ADJ_ROW, gallerySrc,
    sampleForMono, release, releaseHolder} from '../../lib/looks.svelte.js';
  import {LOOKS, lookById} from '../../lib/looksCatalog.js';
  import {W} from '../../lib/looksWords.js';
  import TryOn from '../../components/looks/TryOn.svelte';
  import Pending from '../../components/looks/Pending.svelte';
  import SectionHead from '../../components/ui/SectionHead.svelte';
  import Segmented from '../../components/ui/Segmented.svelte';
  import Details from '../../components/ui/Details.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';

  const block = $derived.by(() => { void S.rev; return B.orig; });
  const koma = $derived(!!block && LS.koma(block));
  // Official firmware: a block without Koma, or no settings file on the card at all (a stock camera never writes one).
  const stock = $derived((!!block && !koma) || (!block && !!S.readError && S.readCode === 'no_param_cur'));
  const setup = $derived(setupEntry());
  const cur = $derived.by(() => { void S.rev; return block ? LS.current(block) : null; });
  const onId = $derived(cur && cur.id ? cur.id : 0);
  const ghost = $derived(cur && cur.ghost ? cur : null);
  const GHOST = 100;                                    // the ghost entry's radio value
  const sel = $derived(LK.sel === GHOST && !ghost ? onId || LS.STD : LK.sel || (ghost ? GHOST : onId || LS.STD));
  const selLook = $derived(sel === GHOST ? null : lookById(sel));
  const writable = $derived(canWrite() || A.open);       // while an Apply restarts the camera, it is not 'gone'
  const busy = $derived(A.open);
  const iaOk = $derived(lookIaOk());

  // The look's ADJ LEVER flag, as edited here when it has a draft. The cards show the tag, and the bar the switch, only
  // on a Koma with that setting (lookIaOk): builds before the Look IA have none, and the editor says so at the row.
  function adj(id) { return iaOk && stateOf(id).adj; }
  function choose(id) { LK.sel = id; if (LK.status && LK.status.id !== id) LK.status = null; }
  function setAdj(v) { if (selLook) edit(sel, st => { st.adj = v; }); }

  const groups = [['mono', LOOKS.filter(l => l.group === 'mono')], ['color', LOOKS.filter(l => l.group === 'color')],
    ['custom', LOOKS.filter(l => l.group === 'custom')]];
  const standard = LOOKS.find(l => l.group === 'standard');

  const drawn = $derived(gallerySrc());
  const lede = $derived(LK.src === 'none' ? W.noPhoto : LK.src !== 'photo' && LK.src !== 'sample' ? ''
    : drawn === 'photo' ? W.onLast(when(LK.date)) : W.onSample);
  const bare = $derived(LK.src === 'none');
  // why the sample: no photo on the card, or it could not be read. The camera held by live view, an interval, remote
  // control or the webcam is the shared busy line, with the action that frees it.
  const why = $derived(LK.src === 'sample' && LK.why !== 'busy' ? W.sampleWhy[LK.why] || '' : '');
  const held = $derived((LK.src === 'sample' && LK.why === 'busy') || !!release.by);
  const h = $derived(release.by || holder());
  const hold = $derived(busyLine(h));

  const strip = $derived.by(() => {
    void LK.rev;
    if (sel === GHOST) return {text: W.ghostState, edit: false, set: false};
    const name = nameOf(sel);
    // a write in progress, or the look just set; edits applied to a look that is not on the camera leave Set in place
    if (LK.status && LK.status.id === sel && LK.status.text && (LK.status.tone === 'wait' || sel === onId))
      return {text: LK.status.text, tone: LK.status.tone, edit: true, set: false};
    if (!writable) return {text: blockedBy() === 'store' ? W.oldStore : W.needsCamera, edit: true, set: false};
    if (sel === onId) return {text: hasDraft(sel) ? W.onCameraEdited : W.onCamera, edit: true, set: false, name};
    return {text: hasDraft(sel) ? W.editedRestarts : W.restarts, edit: true, set: true, name};
  });
  // "On the ADJ lever." after the sentence when the selected look's flag is on; the switch beside it changes it
  const onAdj = $derived(!!selLook && adj(sel) && !strip.tone);
  const showAdj = $derived(!!selLook && iaOk && !busy);
  // phone: Edit look alone sits on the text row, link-sized, not a full-width bar
  const solo = $derived(strip.edit && !!selLook && !strip.set);

  const details = $derived([
    sampleForMono() ? {k: W.whySample, v: W.whySampleText} : null,
    LK.src === 'photo' ? {k: W.detail.photo, v: LK.path} : null,
    LK.src === 'photo' ? {k: W.detail.size, v: bytes(LK.size)} : null,
    {k: W.detail.model, v: W.detail.modelText},
  ].filter(Boolean));

  // Back from a look's editor: that look in view again (the shell scrolls a new page to its top first).
  const back = LK.sel;
  $effect(() => {
    if (!back) return;
    requestAnimationFrame(() => document.querySelector('[data-testid=looks] label.card.sel')?.scrollIntoView({block: 'center'}));
  });
</script>

<section class="lk" aria-labelledby="lk-title" data-testid="looks">
  <header class="lk-head">
    <h1 id="lk-title" class="title" tabindex="-1">{W.title}</h1>
    {#if stock}
      <!-- no looks on this camera: nothing is drawn, so nothing to say about the photo -->
    {:else if LK.src === 'reading'}
      <p class="lk-status small" role="status"><i class="dot faint"></i>{W.readingPhoto}{#if LK.readingS >= 10}<span class="num"> · {elapsed(LK.readingS)}</span>{/if}</p>
    {:else if lede}
      <div class="lk-src small" data-testid="looks-source">
        <p><span>{lede}</span>{#if LK.src !== 'none'}<span class="micro">{W.approx}</span>{/if}</p>
        {#if held}
          <div class="hold" data-testid="looks-busy">
            <StatusLine tone="wait" text={release.by ? hold.stopping : hold.text}
              action={release.by ? null : {label: hold.action, onclick: () => releaseHolder(h), testid: 'busy-action'}} />
          </div>
        {:else if why}
          <p class="why">{#if LK.why === 'error'}<i class="dot bad"></i>{/if}<span>{why}</span>{#if LK.why === 'error'}<button
            class="btn quiet" type="button" onclick={() => loadSource(true)} data-testid="photo-read-again">{W.readAgain}</button>{/if}</p>
        {/if}
      </div>
    {/if}
  </header>

  {#if stock}
    <div class="lk-empty" data-testid="looks-stock">
      <p class="micro">{W.stockLabel}</p>
      <p>{W.stockText}</p>
      <p><a class="link" href={setup.href} data-testid="looks-setup">{setup.label}</a></p>
    </div>
  {:else if !block && S.reading}
    <p class="lk-status small" role="status"><i class="dot faint"></i>{W.readingLooks}</p>
  {:else if !block && S.readError}
    <div class="lk-status small" role="alert"><i class="dot bad"></i><span>{S.readError}</span>
      <button class="btn quiet" type="button" onclick={readFromCamera}>{W.readAgain}</button></div>
  {:else}
    <!-- bare: no photo to draw on (the bundled sample did not decode), so names and lines only, no empty frames -->
    <div class="lk-list" class:bare role="radiogroup" aria-labelledby="lk-title">
      <ul class="lk-grid">
        {@render card(standard)}
        {#if ghost}
          <li>
            <label class="card" class:on={true} class:sel={sel === GHOST} data-look="ghost">
              <input type="radio" name="look" class="sr-only" value={GHOST} checked={sel === GHOST}
                onchange={() => choose(GHOST)} aria-label={ghost.ghost + ', ' + W.ghostLine} aria-describedby="lk-g-on">
              <span class="rule" aria-hidden="true"></span>
              {#if !bare}<TryOn ghost={ghost.value} />{/if}
              <span class="cap"><span class="name camlabel">{ghost.ghost}</span></span>
              <span class="line small">{W.ghostLine}</span>
              <span class="sr-only" id="lk-g-on">{W.a11yOn}</span>
            </label>
          </li>
        {/if}
      </ul>
      {#each groups as [g, list]}
        <SectionHead label={W.groups[g]} />
        <ul class="lk-grid">
          {#each list as l (l.id)}{@render card(l)}{/each}
        </ul>
      {/each}
    </div>

    {#if LK.src === 'photo' || LK.src === 'sample'}
      <div class="lk-details"><Details items={details} testid="looks-details" /></div>
    {/if}
  {/if}
</section>

{#if block && koma}
  <div class="lk-bar" role="region" aria-label={W.galleryLabel} data-testid="looks-bar">
    <div class="lk-row" class:solo>
      <div class="lk-bar-text">
        <span class="camlabel">{sel === GHOST && ghost ? ghost.ghost : nameOf(sel)}</span>
        <span class="small say" role="status" aria-live="polite">{#if strip.tone}<i class="dot {strip.tone === 'ok' ? 'ok' : 'faint'}"></i>{/if}{strip.text}{#if onAdj}{' ' + W.onAdj}{/if}</span>
      </div>
      {#if showAdj}
        <div class="lk-adj">
          <span class="camlabel" id="lk-adj-l">{ADJ_ROW.label}</span>
          <Segmented name="lk-adj" labelledby="lk-adj-l" small value={stateOf(sel).adj} testid="bar-adj" datak="adj"
            options={[{value: false, label: ADJ_ROW.off}, {value: true, label: ADJ_ROW.on}]} onchange={setAdj} />
        </div>
      {/if}
      <div class="lk-bar-act">
        {#if strip.edit && selLook}<a class="btn quiet" href={'#/looks/' + selLook.slug} data-testid="edit-look">{W.editLook}</a>{/if}
        {#if strip.set}<button class="btn primary" type="button" onclick={() => pick(sel)} disabled={busy} data-testid="set-look">{W.setOnCamera}</button>{/if}
      </div>
    </div>
    <Pending />
  </div>
{/if}

{#snippet card(l)}
  {@const on = l.id === onId}
  {@const edited = hasDraft(l.id)}
  {@const a = adj(l.id)}
  <li>
    <label class="card" class:on class:sel={sel === l.id} data-look={l.slug}>
      <input type="radio" name="look" class="sr-only" value={l.id} checked={sel === l.id} onchange={() => choose(l.id)}
        aria-label={l.name + ', ' + l.line} aria-describedby={'lk-s-' + l.id}>
      <span class="rule" aria-hidden="true"></span>
      {#if !bare}<TryOn st={stateOf(l.id)} urgent={sel === l.id} />{/if}
      <span class="cap">
        <span class="name camlabel">{l.name}</span>
        {#if edited}<span class="micro">{W.tagEdited}</span>{/if}
        {#if a}<span class="tag micro" data-testid="adj-tag">{W.tagAdj}</span>{/if}
      </span>
      <span class="line small">{l.line}</span>
      <span class="sr-only" id={'lk-s-' + l.id}>{[on ? W.a11yOn : '', a ? W.a11yAdj : '', edited ? W.a11yEdited : ''].filter(Boolean).join(', ')}</span>
    </label>
  </li>
{/snippet}
