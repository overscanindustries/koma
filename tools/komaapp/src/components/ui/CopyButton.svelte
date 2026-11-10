<script module>
  // Works on a phone's plain-http page too, where navigator.clipboard is missing.
  export async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
    } catch (e) { /* fall through */ }
    try {
      const ta = document.createElement('textarea');
      ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      return ok;
    } catch (e) { return false; }
  }
</script>

<script>
  // Copies text (promoted from the Webcam area). Icon-only beside a value (aria-label says what), or a quiet button
  // with its own words. "Copied." shows for two seconds. A 36 px target around a 16 px glyph, 44 px on a phone.
  // inline: the icon sits inside a sentence, right after the value it copies; the sentence then carries "Copied."
  // at its own end (bind:copied), so the word never splits the sentence.
  import Icon from '../Icon.svelte';
  let {text, label, words = '', done = 'Copied.', testid = undefined, inline = false, copied = $bindable(false)} = $props();
  let timer = null;
  async function go() {
    copied = await copyText(text);
    clearTimeout(timer); timer = setTimeout(() => { copied = false; }, 2000);
  }
</script>

{#if words}
  <button class="btn quiet" type="button" onclick={go} data-testid={testid}>{words}</button>
{:else}
  <button class="cb" class:inline type="button" onclick={go} aria-label={label} title={label} data-testid={testid}><Icon name="copy" size={16} /></button>
{/if}{#if !inline}<span class="copied" aria-live="polite">{copied ? done : ''}</span>{/if}

<style>
  /* the glyph sits 6 px from the text; the negative margins keep the line height */
  .cb {
    display: inline-flex; align-items: center; justify-content: center; vertical-align: middle;
    width: 36px; height: 36px; margin: -8px 0 -8px -4px; padding: 0; border: 1px solid transparent;
    border-radius: var(--r-ctl); background: none; color: var(--sub);
    transition: color var(--t-fast) var(--ease), border-color var(--t-fast) var(--ease);
  }
  .cb:hover { color: var(--ink); border-color: var(--line); }
  /* inside a sentence: inline boxes all the way down, so the sentence reads and copies as one line of text */
  .cb.inline { display: inline-block; text-align: center; line-height: 0; margin-right: -6px; }
  .cb.inline :global(svg) { display: inline-block; vertical-align: middle; }
  .copied { margin-left: 6px; font-size: var(--fs-small); line-height: var(--lh-small); color: var(--sub); }
  .copied:empty { margin-left: 0; }  /* the live region stays in place, empty and without width */
  @media (max-width: 720px) {
    .cb { width: 44px; height: 44px; margin: -12px 0 -12px -8px; }
    .cb.inline { margin-right: -10px; }
  }
</style>
