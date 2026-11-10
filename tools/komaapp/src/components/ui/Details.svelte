<script>
  // Details: a link-style disclosure, closed by default, at the bottom of its section. The one place
  // for file names, byte counts, rates, versions and hashes (truncated to 8 characters, with Copy).
  //   items: [{k, v, copy: full text to copy}]  rendered as a small two-column list; children for anything else
  import CopyButton from './CopyButton.svelte';
  let {label = 'Details', items = [], open = $bindable(false), testid = undefined, children} = $props();
</script>

<details class="details" bind:open data-testid={testid}>
  <summary>{label}</summary>
  <div class="in">
    {#if items.length}
      <dl>
        {#each items.filter(i => i && i.v !== null && i.v !== undefined && i.v !== '') as i (i.k)}
          <div class="r">
            <dt>{i.k}</dt>
            <dd class="num"><span>{i.v}</span>{#if i.copy}<CopyButton text={i.copy} label={'Copy ' + i.k} />{/if}</dd>
          </div>
        {/each}
      </dl>
    {/if}
    {@render children?.()}
  </div>
</details>

<style>
  .in { padding-top: var(--s2); display: grid; gap: var(--s2); max-width: 60ch; }
  dl { display: grid; }
  .r { display: grid; grid-template-columns: minmax(96px, auto) minmax(0, 1fr); column-gap: var(--s4); padding: 2px 0; }
  dt, dd { font-size: var(--fs-small); line-height: var(--lh-small); }
  dt { color: var(--faint); }
  dd { color: var(--sub); overflow-wrap: anywhere; display: flex; align-items: center; }  /* CopyButton keeps its glyph 6 px from the value */
</style>
