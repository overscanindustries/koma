<script>
  // Back, in the action row: one step back, never to the start. Sentence case, underlined at rest.
  // describedby: the id of the line that says why Back is disabled.
  import Icon from '../Icon.svelte';
  import {W} from '../../lib/onboardWords.js';
  let {onclick, disabled = false, describedby = undefined} = $props();
</script>

<button class="back" type="button" {onclick} {disabled} aria-describedby={describedby} data-testid="setup-back"><Icon name="back" size={14} /><span>{W.back}</span></button>

<style>
  .back {
    display: inline-flex; align-items: center; gap: 6px; min-height: 40px; padding: 0;
    background: none; border: 0; color: var(--sub); font-size: var(--fs-body, 14px); line-height: 20px;
    transition: color var(--t-fast, .12s) var(--ease);
  }
  .back span { text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: .2em;
    text-decoration-color: color-mix(in srgb, currentColor 55%, transparent); }
  .back:hover:not(:disabled) { color: var(--ink); }
  .back:hover:not(:disabled) span { text-decoration-color: currentColor; }
  .back:disabled { opacity: .45; }
  @media (max-width: 720px) { .back { min-height: 44px; } }
</style>
