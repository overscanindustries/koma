<script>
  // One per page: the h1 and an optional lede of at most two sentences. `hidden` keeps the h1 for
  // screen readers only (Overview, where the look name is the first visible line). `actions`: a snippet set to the
  // right of the title on the same baseline (one quiet button at most). The h1 takes focus on route change.
  let {title, lede = '', hidden = false, id = 'page-title', actions} = $props();
</script>

{#if hidden}
  <h1 {id} class="sr-only" tabindex="-1">{title}</h1>
{:else}
  <header class="pt">
    <div class="row">
      <h1 {id} class="h1" tabindex="-1">{title}</h1>
      {#if actions}<div class="acts">{@render actions()}</div>{/if}
    </div>
    {#if lede}<p class="lede">{lede}</p>{/if}
  </header>
{/if}

<style>
  .pt { display: grid; gap: var(--lede-gap); margin-bottom: var(--s6); }
  .row { display: flex; align-items: baseline; justify-content: space-between; gap: var(--s4); flex-wrap: wrap; min-height: var(--lh-h1); }
  .acts { display: flex; gap: var(--s2); align-items: center; }
  h1 { max-width: 30ch; }
</style>
