<script>
  // One contact-sheet cell: the camera's EXIF thumbnail (160x120 on the camera's JPEGs) at its native size on the photo matte, never cropped or upscaled; a flat block until it is read.
  // Under it one line: the selection box with the file number and the time as its label, then the NEW / IMPORTED state
  // as a micro-tag beside the label, never inside it (labels are not targets). The frame opens the
  // full view. Shift-click on the box selects the run from the last box toggled (photos.svelte.js pick).
  import {thumbs, selected, tkey, watch, thumbFailed, fileNumber, pick} from '../../lib/photos.svelte.js';
  import {time as timeOf} from '../../lib/format.js';
  import {PHOTOS as W} from '../../lib/words.js';

  let {p, tag = null, onopen} = $props();
  let el = $state();
  $effect(() => { if (el) return watch(el, p); });

  const t = $derived(thumbs.get(tkey(p)));
  const num = $derived(fileNumber(p.name));
  const time = $derived(timeOf(p.date));
  const sel = $derived(selected.has(p.path));
  const tagId = $derived('tag-' + p.path.replace(/[^A-Za-z0-9_-]/g, '-'));
  // click, not change: the click carries the Shift key. The box's own state is the photo's new state.
  function toggle(e) { pick(p, e.currentTarget.checked, e.shiftKey); }
</script>

<li class="cell" class:sel bind:this={el} data-photo={p.path}>
  <button type="button" class="frame" class:shown={t && t.state === 'ok'} onclick={() => onopen(p)}
    aria-label={W.openPhoto(num, time)}>
    {#if t && t.state === 'ok'}<img src={t.url} alt="" decoding="async" onerror={() => thumbFailed(p)}>{/if}
  </button>
  <div class="cap">
    <label class="pick">
      <input type="checkbox" checked={sel} onclick={toggle} aria-describedby={tag ? tagId : undefined}>
      <span class="nt num">{num}<span class="sep" aria-hidden="true">{' · '}</span><span class="sr-only">{', '}</span>{time}</span>
    </label>
    {#if tag}<span class="tag micro" class:new={tag === 'new'} id={tagId}>{tag === 'new' ? W.tagNew : W.tagImported}</span>{/if}
  </div>
</li>

<style>
  .cell { display: grid; grid-template-rows: auto 1fr; min-width: 0; }
  .frame {
    width: 160px; height: 120px; padding: 0; border: 0; border-radius: 0;
    background: var(--surf); display: grid; place-items: center; overflow: hidden; cursor: pointer;
  }
  .frame.shown { background: var(--photo-bg); }
  .frame img { max-width: 100%; max-height: 100%; }
  .frame:focus-visible { outline: 2px solid var(--ink); outline-offset: 0; }
  /* Selected: a 1 px ink line one pixel clear of the picture (inside the 2 px gap), so it reads on dark frames in
     both themes and never covers the photograph. */
  .sel .frame { outline: 1px solid var(--ink); outline-offset: 1px; }
  .sel .frame:focus-visible { outline-width: 2px; outline-offset: 0; }

  /* The caption line holds its 40 px (44 on a phone) so the label's target never reaches the next row. The label
     takes that whole height through ::after; the tag beside it (on a phone, on the line below, under the number) is
     painted over it and takes no clicks. */
  .cap {
    display: flex; flex-wrap: wrap; align-items: flex-start; align-content: flex-start; column-gap: 6px;
    min-height: 40px; padding-top: 6px;
    font-size: var(--fs-small); line-height: var(--lh-small); color: var(--sub);
  }
  .pick { position: relative; display: inline-flex; align-items: flex-start; gap: 6px; cursor: pointer; user-select: none; }
  .pick::after { content: ''; position: absolute; inset: -6px 0 -18px; }
  /* The box has no fill, so its border is all there is of it: --faint at rest (5:1 on the page, a control that reads;
     --line-strong would be about 1.6:1), --sub while the hand is on the cell or its frame has focus, ink when ticked. */
  .pick input {
    appearance: none; -webkit-appearance: none; margin: 2px 0 0; flex: none;
    width: 12px; height: 12px; border: 1px solid var(--faint); border-radius: 0; background: transparent;
    cursor: pointer; transition: background-color var(--t-fast), border-color var(--t-fast), box-shadow var(--t-fast);
  }
  .cell:hover .pick input, .frame:focus-visible ~ .cap .pick input { border-color: var(--sub); }
  .pick input:checked { background: var(--ink); border-color: var(--ink); box-shadow: inset 0 0 0 2px var(--bg); }
  .pick input:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
  .nt { white-space: nowrap; }
  .sel .nt { color: var(--ink); }
  /* One uppercase voice at --sub; IMPORTED, a done state, recedes to --faint. */
  .tag { position: relative; color: var(--faint); white-space: nowrap; line-height: var(--lh-small); cursor: default; }
  .tag.new { color: var(--sub); }

  @media (max-width: 720px) {
    .frame { width: 100%; height: auto; aspect-ratio: 4 / 3; }
    .cap { min-height: 44px; }
    .pick::after { bottom: -22px; }
    /* Under the number, not under the box: 12 px box + 6 px gap. */
    .tag { flex-basis: 100%; padding-left: 18px; }
  }
</style>
