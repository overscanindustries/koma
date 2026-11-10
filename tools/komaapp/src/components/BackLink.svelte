<script>
  // Back navigation: sentence case at body size, a leading chevron, underlined at rest like
  // every link, solid on hover. The same place on every screen: above the page's first line.
  import Icon from './Icon.svelte';
  let {label, onclick, href = undefined} = $props();
</script>

{#if href}
  <a class="back" {href}><Icon name="back" size={16} /><span>{label}</span></a>
{:else}
  <button class="back" type="button" {onclick}><Icon name="back" size={16} /><span>{label}</span></button>
{/if}

<style>
  /* the Icon chevron at 16 px, the one size every row, disclosure and back link uses; pulled left by its own inset so
     the glyph, not its box, sits on the column's edge */
  .back :global(svg) { margin-left: -5px; }
  .back {
    display: inline-flex; align-items: center; gap: 4px; align-self: start; justify-self: start;
    min-height: 40px; background: none; border: 0; padding: 0; margin: -10px 0 var(--s5);
    color: var(--sub); font-size: var(--fs-body); line-height: var(--lh-body); font-weight: 400; text-decoration: none;
    transition: color var(--t-fast) var(--ease);
  }
  .back span {
    text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: .2em;
    text-decoration-color: color-mix(in srgb, currentColor 55%, transparent);
    transition: text-decoration-color var(--t-fast) var(--ease);
  }
  .back:hover { color: var(--ink); }
  .back:hover span, .back:focus-visible span { text-decoration-color: currentColor; }
</style>
