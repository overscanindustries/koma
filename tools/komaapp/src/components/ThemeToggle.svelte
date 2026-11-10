<script>
  // The theme control on a desktop: icon only, at the foot of the nav column and on the
  // full-screen pages. It flips dark and light; Settings > Appearance also offers System. 40 px target.
  import {currentTheme, toggleTheme} from '../lib/ui.svelte.js';
  import Icon from './Icon.svelte';
  const dark = $derived(currentTheme() === 'dark');
</script>

<button class="toggle" type="button" onclick={toggleTheme}
  aria-label={dark ? 'Use the light theme' : 'Use the dark theme'} title={dark ? 'Light theme' : 'Dark theme'} data-testid="theme-toggle">
  <Icon name={dark ? 'sun' : 'moon'} size={18} />
</button>

<style>
  .toggle {
    display: inline-grid; place-items: center; width: 40px; height: 40px; margin: -10px -11px;
    background: none; border: 1px solid transparent; border-radius: var(--r-ctl);
    color: var(--sub);
    transition: color var(--t-fast) var(--ease), border-color var(--t-fast) var(--ease);
  }
  .toggle:hover { color: var(--ink); border-color: var(--line); }
</style>
