<script>
  // Empty: a micro-label, one sentence in the camera's voice, one action when one exists.
  // No box, no icon, no illustration. Top-aligned in its column, never centred: the caller places it
  //   <EmptyState label="NO PHOTOS" text="The card holds no photos." />
  //   action {label, onclick | href, kind: 'quiet' | 'primary' | 'link', testid}
  let {label, text, action = null, testid = undefined} = $props();
  const cls = $derived(action && action.kind === 'primary' ? 'btn primary' : action && action.kind === 'link' ? 'link' : 'btn quiet');
</script>

<div class="empty" data-testid={testid}>
  {#if label}<p class="micro">{label}</p>{/if}
  <p class="t">{text}</p>
  {#if action}
    <p class="a">
      {#if action.href}<a class={cls} href={action.href} data-testid={action.testid}>{action.label}</a>
      {:else}<button class={cls} type="button" onclick={action.onclick} data-testid={action.testid}>{action.label}</button>{/if}
    </p>
  {/if}
</div>

<style>
  .empty { display: grid; gap: var(--s2); justify-items: start; max-width: 60ch; }
  .t { color: var(--ink); font-size: var(--fs-body); line-height: var(--lh-body); }
  .a { margin-top: var(--s2); }
</style>
