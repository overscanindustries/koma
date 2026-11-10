<script>
  // Choose or drop one settings file. A button and one sentence on a row
  // between two hairlines, the Row grammar: no dashed box, no radius. A file dropped anywhere on the page while this is
  // on it is taken; during the drag the row's hairlines turn ink, since the whole page is the target. The file's
  // technical name stays under Details. onfile(bytes, name) returns an error string to refuse it, or '' to take it.
  //   label    the button: 'Choose a backup', 'Choose the settings file' (`title` is the older name of this prop)
  //   primary  only where choosing a file is the page's next action (Work from a memory card)
  //   hint     the Details text: the file's name on the card and where it sits
  import StatusLine from './ui/StatusLine.svelte';
  import Details from './ui/Details.svelte';
  let {label = '', title = '', hint = '', onfile, testid = undefined, primary = false} = $props();
  let input = $state(), over = $state(false), msg = $state('');
  let quiet = 0;

  async function take(f) {
    if (!f) return;
    try {
      const b = new Uint8Array(await f.arrayBuffer());
      msg = onfile(b, f.name) || '';
    } catch (e) { msg = 'This file could not be read.'; }
  }
  // A drag that carries files, outside an open sheet: the page takes it instead of the browser opening the file.
  const carriesFiles = e => !!(e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files'))
    && !document.querySelector('dialog[open]');
  // dragover repeats every 50 ms or so while a file is over the page; when it stops, the drag has left.
  function dragover(e) {
    if (!carriesFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    over = true;
    clearTimeout(quiet); quiet = setTimeout(() => { over = false; }, 200);
  }
  function drop(e) {
    if (!carriesFiles(e)) return;
    e.preventDefault();
    clearTimeout(quiet); over = false;
    take(e.dataTransfer.files[0]);
  }
  $effect(() => () => clearTimeout(quiet));
</script>

<svelte:window ondragover={dragover} ondrop={drop} />

<div class="filedrop">
  <div class="fd" class:over data-testid={testid ? testid + '-row' : undefined}>
    <button class="btn" class:primary type="button" onclick={() => input.click()}>{label || title}</button>
    <p class="small muted or">Or drop the file anywhere on this page.</p>
    <input bind:this={input} type="file" class="sr-only" tabindex="-1" aria-hidden="true" data-testid={testid}
      onchange={() => { take(input.files[0]); input.value = ''; }}>
  </div>
  {#if msg}<div class="msg"><StatusLine tone="bad" text={msg} /></div>{/if}
  {#if hint}<div class="hint"><Details><pre>{hint}</pre></Details></div>{/if}
</div>

<style>
  .filedrop { min-width: 0; }
  .fd {
    display: flex; align-items: center; flex-wrap: wrap; gap: var(--s2) var(--s4);
    padding: var(--s3) 0;
    border-top: 1px solid var(--line); border-bottom: 1px solid var(--line);
    transition: border-color var(--t-fast) var(--ease);
  }
  .fd.over { border-color: var(--ink); }
  .or { min-width: 0; }
  .hint { margin-top: var(--s2); }
  .msg { margin-top: var(--s3); }
  /* a touch screen has no file drag */
  @media (pointer: coarse) { .or { display: none; } }
</style>
