<script>
  // One step, laid out as a camera-manual spread: columns 1 to 7 hold the step index, the step name,
  // one instruction, at most three short lines, the step's own body and the action row; columns 8 to 12 hold the one
  // figure, aligned to the instruction. On a phone: figure, index, name, instruction, body; actions pinned to the bottom.
  // The action row keeps one place on every step of a flow: the content block has a fixed minimum height that every
  // step but Pick a look fits in. On desktop the content block is the scroll container, capped at what the window
  // leaves above the action row: a longer step (Pick a look) scrolls inside it and stops at the row's rule, never under
  // it; while more lies below, its foot fades out over 32 px, so the cut reads as a cut. On a phone the page scrolls and
  // the row is pinned to the foot of the screen.
  let {index = '', sub = '', title, lead = '', lines = [], body = null, figure = null, actions = null, testid = undefined, step = ''} = $props();
  let h1 = $state();
  // Each new screen moves focus to its name, so a screen reader announces where the flow is.
  $effect(() => { void step; void title; if (h1) h1.focus({preventScroll: true}); });
  // more: the content block holds more than it shows below its foot
  let box = $state(), more = $state(false);
  function measure() { more = !!box && box.scrollHeight - box.scrollTop - box.clientHeight > 2; }
  $effect(() => {
    if (!box) return;
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(box);
    for (const c of box.children) ro.observe(c);
    return () => ro.disconnect();
  });
</script>

<section class="spread" class:fig={!!figure} aria-labelledby="onb-title" data-testid={testid} data-step={step}>
  {#if figure}<div class="right">{@render figure()}</div>{/if}
  <div class="left">
    <div class="content" class:more bind:this={box} onscroll={measure}>
      <!-- the progress, in one place: the step, and inside a multi-screen step the screen (2 OF 8) -->
      {#if index}<p class="micro num idx"><span data-testid="step-index">{index}</span>{#if sub}<span class="sub" data-testid="step-sub">{sub}</span>{/if}</p>{/if}
      <h1 id="onb-title" class="h1" tabindex="-1" bind:this={h1}>{title}</h1>
      {#if lead}<p class="onb-lead lead" data-testid="step-lead">{lead}</p>{/if}
      {#if lines.length}
        <div class="lines">{#each lines as l}<p class="small">{#if typeof l === 'string'}{l}{:else}{l.text}<a class="link"
          href={l.href} target="_blank" rel="noopener noreferrer">{l.label}</a>.{/if}</p>{/each}</div>
      {/if}
      {#if body}<div class="body">{@render body()}</div>{/if}
    </div>
    {#if actions}<div class="actions">{@render actions()}</div>{/if}
  </div>
</section>

<style>
  .spread {
    /* the window's height left for the content block: the header (80), the top padding, the action row (112) */
    --pad-top: clamp(24px, 8vh, 88px);
    --avail: calc(100vh - 80px - var(--pad-top) - 112px);
    display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); column-gap: 24px;
    width: 100%; max-width: 1120px; margin: 0 auto; padding: var(--pad-top) 28px 0;
  }
  @supports (height: 100dvh) { .spread { --avail: calc(100dvh - 80px - var(--pad-top) - 112px); } }
  .left { grid-column: 1 / span 7; grid-row: 1; display: flex; flex-direction: column; min-width: 0; }
  .right { grid-column: 8 / span 5; grid-row: 1; padding-top: 80px; min-width: 0; display: flex; justify-content: flex-start; align-items: flex-start; }
  /* 4 px of room on every side, so a focused control's outline is not clipped by the scroll container */
  .content {
    min-height: min(560px, var(--avail)); max-height: max(320px, var(--avail)); overflow-x: hidden; overflow-y: auto;
    display: flex; flex-direction: column; padding: 4px; margin: -4px; overscroll-behavior: contain;
  }
  .content > * { flex-shrink: 0; }
  .content.more { mask-image: linear-gradient(to bottom, #000 calc(100% - 32px), transparent); }
  .idx { margin-bottom: 8px; }
  .lead { margin-top: 24px; }
  .lines { margin-top: 8px; display: grid; gap: 4px; }
  .lines p { max-width: 60ch; }
  .idx .sub { margin-left: 24px; }
  .body { margin-top: 32px; display: grid; gap: 24px; align-content: start; }
  /* the action row carries a 1 px top edge: content scrolled above it stops at a rule, and the row reads as
     the page's foot rather than buttons afloat */
  .actions {
    position: relative; z-index: 2; background: var(--bg); border-top: 1px solid var(--line);
    display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;
    padding: 24px 0 32px; margin-top: 16px;
  }

  @media (max-width: 720px) {
    .spread { grid-template-columns: minmax(0, 1fr); padding: 16px 16px 0; }
    .left, .right { grid-column: 1; }
    .right { grid-row: 1; padding-top: 0; justify-content: center; margin-bottom: 24px; }
    .right :global(figure) { max-height: 240px; }
    .left { grid-row: 2; }
    .content, .content.more { min-height: 0; max-height: none; overflow: visible; margin: 0; padding: 0 0 24px; mask-image: none; }
    .left { padding-bottom: 96px; }
    .lead { margin-top: 16px; }
    .body { margin-top: 24px; }
    .actions {
      position: fixed; left: 0; right: 0; bottom: 0; margin: 0; padding: 12px 16px calc(12px + env(safe-area-inset-bottom));
      border-top: 1px solid var(--line); flex-wrap: nowrap;
    }
    .actions :global(.btn.primary) { flex: 1 1 auto; min-height: 44px; }
  }
</style>
