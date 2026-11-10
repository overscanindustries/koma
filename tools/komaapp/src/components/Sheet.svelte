<script>
  // A modal sheet on <dialog>: the one shadow in the app, 180 ms fade and an 8 px rise, Esc closes
  // unless `locked` (a phase that must not be interrupted). The browser traps focus.
  let {open = false, title, onclose = () => {}, locked = false, wide = false, children, actions, testid = undefined} = $props();
  let dlg = $state();
  const id = 'sh-' + Math.random().toString(36).slice(2, 8);

  $effect(() => {
    if (!dlg) return;
    if (open && !dlg.open) { dlg.showModal(); }
    else if (!open && dlg.open) dlg.close();
  });
  function cancel(e) { e.preventDefault(); if (!locked) onclose(); }
  function backdrop(e) { if (e.target === dlg && !locked) onclose(); }
</script>

<dialog bind:this={dlg} class="sheet" class:wide aria-labelledby={id} oncancel={cancel} onclick={backdrop} data-testid={testid}>
  <div class="in">
    <h2 {id} class="h">{title}</h2>
    <div class="content">{@render children?.()}</div>
    {#if actions}<div class="actions">{@render actions()}</div>{/if}
  </div>
</dialog>

<style>
  .sheet {
    width: min(560px, calc(100vw - 32px)); max-height: min(86vh, 760px);
    padding: 0; border: 1px solid var(--line-strong); border-radius: var(--r-panel);
    background: var(--surf); color: var(--ink); box-shadow: var(--shadow);
  }
  .sheet.wide { width: min(720px, calc(100vw - 32px)); }
  .sheet::backdrop { background: var(--scrim); }
  .sheet[open] { animation: rise var(--t) var(--ease); }
  .sheet[open]::backdrop { animation: fade var(--t) var(--ease); }
  @keyframes rise { from { opacity: 0; transform: translateY(8px); } }
  @keyframes fade { from { opacity: 0; } }
  .in { display: flex; flex-direction: column; max-height: inherit; }
  .h { font-size: var(--fs-title); line-height: var(--lh-title); font-weight: 500; letter-spacing: -.01em; padding: var(--s5) var(--s5) 0; }
  .content { padding: var(--s4) var(--s5) var(--s2); overflow: auto; }
  .actions {
    display: flex; gap: var(--s2); justify-content: flex-end; flex-wrap: wrap; align-items: center;
    padding: var(--s4) var(--s5) var(--s5); border-top: 1px solid var(--line); margin-top: var(--s3);
  }
  @media (max-width: 720px) {
    .sheet { width: 100vw; max-width: 100vw; margin: auto 0 0; border-radius: var(--r-panel) var(--r-panel) 0 0; max-height: 88vh; border-bottom: 0; }
    .h { padding: var(--s5) var(--gutter) 0; }
    .content { padding: var(--s3) var(--gutter) var(--s2); }
    .actions { padding: var(--s3) var(--gutter) calc(var(--s4) + env(safe-area-inset-bottom)); }
    .actions :global(.btn) { flex: 1; }
  }
</style>
