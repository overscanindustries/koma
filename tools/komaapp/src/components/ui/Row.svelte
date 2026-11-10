<script>
  // A row: label left, value right, a 1 px rule, an optional chevron; 44 px (48 on a phone).
  // With `href` it is a link, with `onclick` a button, else plain text. One control per row.
  //   cam: the label is a camera string (IMG SET)
  //   spec: the value starts at the spec-sheet x (144 px) instead of sitting right, so a list of rows lines up with a
  //         SpecSheet above it (Overview's destinations)
  //   children: a snippet in place of the value
  import Icon from '../Icon.svelte';
  let {label, value = '', href = undefined, onclick = undefined, chevron = !!(href || onclick), cam = false,
    spec = false, current = false, testid = undefined, children} = $props();
</script>

{#snippet inner()}
  <span class="l" class:cam>{label}</span>
  <span class="v">{#if children}{@render children()}{:else}{value}{/if}</span>
  {#if chevron}<span class="chev" aria-hidden="true"><Icon name="chevron" size={16} /></span>{:else}<span></span>{/if}
{/snippet}

{#if href}
  <a class="row act" class:spec {href} aria-current={current ? 'page' : undefined} data-testid={testid}>{@render inner()}</a>
{:else if onclick}
  <button class="row act" class:spec type="button" {onclick} data-testid={testid}>{@render inner()}</button>
{:else}
  <div class="row" class:spec data-testid={testid}>{@render inner()}</div>
{/if}

<style>
  .row {
    display: grid; grid-template-columns: minmax(0, auto) minmax(0, 1fr) auto; align-items: center; column-gap: var(--s4);
    min-height: var(--row-h); width: 100%;
    padding: 0; background: none; border: 0; border-bottom: 1px solid var(--line);
    color: var(--ink); text-decoration: none; text-align: left; font: inherit;
  }
  .row.spec { grid-template-columns: var(--spec-label) minmax(0, 1fr) auto; }
  .l { font-size: var(--fs-body); line-height: var(--lh-body); font-weight: 500; white-space: nowrap; }
  .l.cam { font-size: var(--fs-cam); line-height: var(--lh-cam); font-stretch: 88%; font-weight: 560; letter-spacing: .06em; }
  .v { color: var(--sub); font-size: var(--fs-body); line-height: var(--lh-body); text-align: right; min-width: 0; }
  .spec .v { text-align: left; }
  .chev { color: var(--faint); display: grid; transition: color var(--t-fast) var(--ease), transform var(--t-fast) var(--ease); }
  .act { cursor: pointer; }
  .act:hover .chev, .act:focus-visible .chev { color: var(--ink); transform: translateX(2px); }
  .act:hover .v { color: var(--ink); }
  .act:focus-visible { outline-offset: -2px; }
</style>
