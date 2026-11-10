<script>
  // The Looks area's pending changes, area-wide: every look with edits made here, in one line, one
  // Review and one Apply. "3 changes in 2 looks · Review · Apply to camera". Apply sends every look's edits in one block,
  // one restart, one read-back (looks.svelte.js applyAll). Review groups the rows under each look's name, each row
  // undoable. Rendered inside the area's pinned bar (.lk-bar), under the selected look in the gallery, alone in the
  // editor. `id`: the look on screen (the editor's), for the done line there.
  import {S, B} from '../../lib/settings.svelte.js';
  import {A} from '../../lib/apply.svelte.js';
  import * as LS from '../../lib/looksStore.js';
  import {drafted, canWrite, blockedBy, applyAll, dropAll} from '../../lib/looks.svelte.js';
  import {W, opt} from '../../lib/looksWords.js';
  import Sheet from '../Sheet.svelte';

  let {id = 0} = $props();

  const list = $derived(drafted());
  const n = $derived(list.reduce((a, x) => a + x.changes.length, 0));
  const m = $derived(list.length);
  const one = $derived(m === 1 ? list[0].name : '');
  const writable = $derived(canWrite() || A.open);       // while an Apply restarts the camera, it is not 'gone'
  // The look on the camera: when it is among the edited looks the camera restarts to load them; otherwise the edits
  // are stored and the look in use stays.
  const cur = $derived.by(() => { void S.rev; return B.orig ? LS.current(B.orig) : null; });
  const inUse = $derived.by(() => { void S.rev; return B.orig ? LS.currentName(B.orig) : ''; });
  const loads = $derived(!inUse || list.some(x => cur && x.id === cur.id));
  const say = $derived(!writable ? (blockedBy() === 'store' ? W.oldStore : W.editNeedsCamera)
    : loads ? W.applyRestarts(n) : W.applyStores(n, inUse));
  let review = $state(false);
  $effect(() => { if (!n) review = false; });
  function apply() { review = false; applyAll(id); }
</script>

{#if n}
  <div class="lk-row pend" data-testid="look-changes">
    <div class="lk-bar-text">
      <span class="micro num" aria-live="polite">{W.changesIn(n, m, one)}</span>
      <span class="small say" class:why={!writable}>{say}</span>
    </div>
    <div class="lk-bar-act">
      <button class="btn quiet" type="button" onclick={() => (review = true)} data-testid="review-looks">{W.review}</button>
      {#if writable}<button class="btn primary" type="button" onclick={apply} disabled={A.open} data-testid="apply-look">{W.apply}</button>{/if}
    </div>
  </div>
{/if}

<Sheet open={review} title={W.reviewTitle(n, m, one)} onclose={() => (review = false)} testid="look-review">
  {#each list as g (g.id)}
    <section class="ed-group">
      {#if m > 1}<h3 class="camlabel">{g.name}</h3>{/if}
      <ul class="ed-changes">
        {#each g.changes as c (c.key)}
          <li>
            <span class="camlabel">{c.label}</span>
            <span class="vals">{#if c.from}<span class="old">{opt(c.from)}</span><span class="to">{W.to}</span>{/if}<span class="new">{opt(c.to)}</span></span>
            <button class="btn quiet sm" type="button" onclick={c.undo} aria-label={W.undo + ' ' + c.label + (m > 1 ? ', ' + g.name : '')}>{W.undo}</button>
          </li>
        {/each}
      </ul>
    </section>
  {/each}
  {#snippet actions()}
    <button class="btn quiet" type="button" onclick={() => { dropAll(); review = false; }}>{W.undoAll}</button>
    <span class="grow"></span>
    {#if writable}<button class="btn primary" type="button" onclick={apply} disabled={A.open}>{W.apply}</button>{/if}
  {/snippet}
</Sheet>
