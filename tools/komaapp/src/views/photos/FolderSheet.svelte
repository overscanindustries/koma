<script>
  // Choose folder: where Import new copies to. The browser cannot write to a folder of its own choosing, so the
  // bridge does (POST /photos/folder): it makes the folder when the folder above it exists, checks it can write
  // there, and remembers it. Refusals come back as a code, worded here.
  import {untrack} from 'svelte';
  import Sheet from '../../components/Sheet.svelte';
  import {ph, chooseFolder} from '../../lib/photos.svelte.js';
  import {PHOTOS} from '../../lib/words.js';

  let {open = false, day = '', onclose} = $props();
  const W = PHOTOS.folder;
  let text = $state('');
  let err = $state('');
  let busy = $state(false);
  let input = $state();
  // The live sample under the field, from what is typed (Capture One's destination sample).
  const sample = $derived.by(() => {
    const sep = ph.imp ? ph.imp.sep : '/', base = text.trim().replace(/[\\/]+$/, '');
    const d = String(day || '').split('-');
    return base ? base + sep + (d.length === 3 ? d.join(sep) + sep : '') : '';
  });

  $effect(() => {
    if (!open) return;
    text = untrack(() => (ph.imp ? ph.imp.display : ''));  // a poll that refreshes ph.imp never resets the typing
    err = '';
    queueMicrotask(() => { if (input) { input.focus(); input.select(); } });
  });

  async function save(e) {
    e.preventDefault();
    if (busy) return;
    busy = true;
    const code = await chooseFolder(text);
    busy = false;
    if (code) err = W.refused[code] || W.refused.other;
    else onclose();
  }
</script>

<Sheet {open} title={W.title} {onclose} testid="folder-sheet">
  <form class="f" id="folder-form" onsubmit={save} novalidate>
    <label class="lab small" for="folder-path">{W.label}</label>
    <input id="folder-path" class="input num" type="text" bind:value={text} bind:this={input} autocomplete="off"
      autocapitalize="off" spellcheck="false" aria-describedby="folder-help folder-err" aria-invalid={err ? 'true' : undefined}>
    <!-- One line under the field: the refusal while there is one, the live sample otherwise. -->
    <div class="under">
      <p id="folder-help" class="small muted help" hidden={!!err}>{#if sample}{W.help(sample)}{/if}</p>
      <p id="folder-err" class="small err-line" role="alert">{#if err}<i class="dot" aria-hidden="true"></i>{err}{/if}</p>
    </div>
  </form>
  {#snippet actions()}
    <button class="btn quiet" type="button" onclick={onclose}>{W.cancel}</button>
    <button class="btn primary" type="submit" form="folder-form" disabled={busy} data-testid="folder-use">{W.use}</button>
  {/snippet}
</Sheet>

<style>
  .f { display: grid; gap: 8px; }
  .lab { color: var(--sub); }
  .input { width: 100%; height: 40px; font-size: var(--fs-body); }
  .help { min-height: var(--lh-small); line-height: var(--lh-small); overflow-wrap: anywhere; }
  .help[hidden] { display: none; }
  /* The refusal is the status line's form: the dot hangs in an 18 px gutter on the first line of the sentence. */
  .err-line { position: relative; padding-left: 18px; line-height: var(--lh-small); color: var(--ink); }
  .under { min-height: var(--lh-small); }
  .dot { position: absolute; left: 0; top: 5px; width: 6px; height: 6px; border-radius: 50%; background: var(--bad); }
</style>
