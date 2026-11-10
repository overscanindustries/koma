<script>
  // The pending bar: edits collect here, each one undoable, then one Apply.
  // Pinned to the bottom on --bg with a 1 px rule above it: N CHANGES, what Apply does (or why it cannot run) ·
  // Review · Apply to camera. Cmd/Ctrl+Z undoes the last edit, the same action as an Undo in Review.
  import {S, B, blk, orig, pending, pendingCount, userField, undoField, undoAll, undoLast, withChecksum, adopt, rereadKeepEdits} from '../lib/settings.svelte.js';
  import {komaVersion} from '../engine/engine.js';
  import {startApply, A} from '../lib/apply.svelte.js';
  import {connected} from '../lib/camera.svelte.js';
  import {clean, showValue, saveBytes, STATE} from '../lib/words.js';
  import Sheet from './Sheet.svelte';
  import OnCamera from './OnCamera.svelte';

  // Named rows are the camera menu's own settings. Everything else changed in Advanced (fields the menus do not show,
  // whose engineering labels carry offsets, and unmapped bytes) is one line, with no count (gate 3); Advanced lists it.
  const all = $derived.by(() => { void S.rev; return pending(); });
  const list = $derived(all.filter(userField));
  const n = $derived(pendingCount());
  const raw = $derived(n > list.length);
  const card = $derived(S.source === 'card');
  // A Koma setting on a camera without Koma: the camera cannot take it, so Apply is not offered.
  const needsKoma = $derived.by(() => { void S.rev; return !card && !!B.orig && komaVersion(B.orig) === null && all.some(f => f.page === 'koma'); });
  let review = $state(false);
  let saved = $state(false);

  // The read-back rows are the named settings; the whole-block rule checks everything else (apply.svelte.js).
  // The camera holds other settings (changed on its body, or another GR): Apply to this GR reads it, keeps the edits
  // on top and applies them.
  function apply() {
    review = false;
    startApply({bytes: withChecksum(B.blk), fields: list.slice(), base: B.orig, edits: true, title: 'Apply to camera',
      onDone: adopt, onReread: applyHere});
  }
  async function applyHere() {
    await rereadKeepEdits();
    if (!S.readError && pendingCount() > 0) apply();
  }
  async function save() {
    review = false;
    if (await saveBytes(withChecksum(B.blk), 'PARAM.DAT')) saved = true;
  }
  function key(e) {
    if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey || e.key.toLowerCase() !== 'z' || n === 0) return;
    if (/INPUT|TEXTAREA/.test(e.target.tagName) && e.target.type !== 'range' && e.target.type !== 'radio' && e.target.type !== 'checkbox') return;
    if (document.querySelector('dialog[open]') && !review) return;
    e.preventDefault();
    undoLast();
  }
</script>

<svelte:window onkeydown={key} />

{#if n > 0}
  <div class="pbar" role="region" aria-label="Pending changes" data-testid="pending-bar">
    <span class="count micro" aria-live="polite"><span class="num">{n}</span> <span class="word">{n === 1 ? 'change' : 'changes'}</span></span>
    {#if needsKoma}<span class="small muted nk">Koma settings need Koma on the camera.</span>
    {:else if !card && !connected() && !A.open}<span class="small muted nk" data-testid="apply-why">The camera is not connected.</span>
    {:else if !card}<span class="small muted how" data-testid="apply-how">{STATE.applyRestarts}</span>{/if}
    <span class="sp"></span>
    <button class="btn quiet" type="button" onclick={() => (review = true)}>Review</button>
    {#if card}
      <button class="btn primary" type="button" onclick={save}>Save to card</button>
    {:else}
      <button class="btn primary" type="button" onclick={apply} disabled={!connected() || needsKoma}>Apply to camera</button>
    {/if}
  </div>
{/if}

<Sheet open={review} title={n === 1 ? '1 change' : n + ' changes'} onclose={() => (review = false)} testid="review-sheet">
  <ul class="changes">
    {#each list as f (f.name)}
      <li>
        <span class="cam lab">{clean(f.label)}</span>
        <span class="vals"><span class="old">{showValue(f, orig())}</span><span class="to" aria-label="becomes">to</span><span class="new">{showValue(f, blk())}</span></span>
        <button class="btn quiet sm" type="button" onclick={() => undoField(f)} aria-label={'Undo ' + clean(f.label)}>Undo</button>
      </li>
    {/each}
    {#if raw}<li class="note small muted" data-testid="review-raw">Raw edits made in Advanced are included.</li>{/if}
    {#if needsKoma}<li class="note small muted">Koma settings need Koma on the camera. Undo them to apply the rest.</li>{/if}
  </ul>
  <p class="small muted sent">{card ? 'Saves the settings file for the camera to load at its next start.' : 'Every value is read back.'}</p>
  {#snippet actions()}
    <button class="btn quiet" type="button" onclick={() => { undoAll(); review = false; }}>Undo all</button>
    <span class="grow"></span>
    {#if card}
      <button class="btn primary" type="button" onclick={save} disabled={!n}>Save to card</button>
    {:else}
      <button class="btn primary" type="button" onclick={apply} disabled={!n || !connected() || needsKoma}>Apply to camera</button>
    {/if}
  {/snippet}
</Sheet>

<Sheet open={saved} title="Finish on the camera" onclose={() => (saved = false)}>
  <OnCamera />
  {#snippet actions()}<button class="btn primary" type="button" onclick={() => (saved = false)}>Done</button>{/snippet}
</Sheet>

<style>
  .pbar {
    position: sticky; bottom: 0; z-index: 6;
    display: flex; align-items: center; gap: var(--s3);
    margin: var(--s6) 0 0; padding: var(--s3) 0 calc(var(--s3) + env(safe-area-inset-bottom));
    background: var(--bg); border-top: 1px solid var(--line);
    animation: in var(--t) var(--ease);
  }
  @keyframes in { from { opacity: 0; transform: translateY(4px); } }
  .count { color: var(--ink); }
  .nk { color: var(--sub); }
  .sp, .grow { flex: 1; }
  .changes { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--line); }
  .changes li {
    display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; column-gap: var(--s4);
    min-height: var(--row-h); padding: var(--s1) 0; border-bottom: 1px solid var(--line);
  }
  .changes li.note { display: block; padding: var(--s3) 0; }
  .vals { display: inline-flex; gap: var(--s2); align-items: baseline; font-size: var(--fs-body); }
  .old { color: var(--sub); }
  .to { color: var(--faint); font-size: var(--fs-small); }
  .sent { margin-top: var(--s4); }
  .how { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  @media (max-width: 720px) {
    .pbar { gap: var(--s2); }
    .pbar .nk { display: none; }
    /* the count's number stays; the word gives its room to the line that says what Apply does */
    .pbar .word { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
    .how { white-space: normal; line-height: var(--lh-small); }
    .changes li { grid-template-columns: 1fr auto; row-gap: var(--s1); padding: var(--s2) 0; }
    .changes .vals { grid-column: 1; }
    .changes li .btn { grid-row: 1 / span 2; grid-column: 2; }
  }
</style>
