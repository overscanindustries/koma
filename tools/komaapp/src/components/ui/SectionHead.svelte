<script>
  // A section head: micro-label over a 1 px rule; 48 px above, 12 below (32 / 8 on a phone).
  // `first` drops the space above (the first section under a page title). `aside`: a snippet at the right end of the
  // rule line, e.g. a count. `level` 2 or 3.
  let {label, id = undefined, first = false, level = 2, aside} = $props();
</script>

<div class="sh" class:first>
  <svelte:element this={'h' + level} {id} class="micro">{label}</svelte:element>
  {#if aside}<span class="aside">{@render aside()}</span>{/if}
</div>

<style>
  .sh {
    display: flex; align-items: baseline; justify-content: space-between; gap: var(--s4);
    margin: var(--head-above) 0 var(--head-below);
    padding-bottom: var(--s2);
    border-bottom: 1px solid var(--line);
  }
  .sh.first { margin-top: 0; }
  .aside { font-size: var(--fs-small); line-height: var(--lh-small); color: var(--sub); font-variant-numeric: tabular-nums; }
</style>
