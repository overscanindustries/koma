<script>
  // The frame of the screens that stand without the nav: Connect, Offline, the card-file start. Wordmark top left, the
  // theme toggle top right, content in columns 1 to 7 of the grid (the right field stays empty: ma), the one project
  // line at the foot. Content starts at a fixed height, never centred.
  import Wordmark from '../Wordmark.svelte';
  import ThemeToggle from '../ThemeToggle.svelte';
  let {labelledby = undefined, children, aside} = $props();
</script>

<div class="fs">
  <header class="top">
    <Wordmark />
    <ThemeToggle />
  </header>
  <main class="body" aria-labelledby={labelledby}>
    <div class="col">{@render children?.()}</div>
    {#if aside}<div class="aside">{@render aside()}</div>{/if}
  </main>
  <footer class="foot">Project Koma. New firmware for the 2005 GR DIGITAL.</footer>
</div>

<style>
  .fs {
    min-height: 100vh; min-height: 100dvh;
    display: grid; grid-template-rows: auto 1fr auto;
    padding: 0 var(--gutter);
  }
  .top { height: 64px; display: flex; align-items: center; justify-content: space-between; }
  .body {
    width: 100%; max-width: var(--content-max);
    display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); column-gap: var(--col-gap);
    align-content: start; padding: 12vh 0 var(--s7);
  }
  .col { grid-column: 1 / span 7; min-width: 0; }
  .aside { grid-column: 8 / span 5; min-width: 0; }
  .foot { padding: var(--s5) 0; color: var(--faint); font-size: var(--fs-small); line-height: var(--lh-small); }
  @media (max-width: 1080px) { .col { grid-column: 1 / span 9; } .aside { grid-column: 10 / span 3; } }
  @media (max-width: 720px) {
    .top { height: 56px; }
    .body { grid-template-columns: minmax(0, 1fr); padding: var(--s6) 0 var(--s6); }
    .col, .aside { grid-column: 1; }
    .aside { order: -1; margin-bottom: var(--s5); }
  }
</style>
