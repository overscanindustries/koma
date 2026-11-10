<script>
  // The one loading and status indicator: a 6 px dot, one sentence, at most one action.
  //   tone     'wait'  faint dot: looking, reading, waiting, lost       'ok'  done, after read-back
  //            'bad'   failed (the text stays ink; the dot carries it)  'none' no dot
  //            ('work', 'hold', 'faint' are read as 'wait')
  // since    Date.now when the phase began: past 10 s the line counts. While it counts, the sentence drops its
  // period and the counter follows it: "Looking for the camera · 0:21"
  //   rule     a 1 px indeterminate rule under the line (Loading)
  //   progress 0..1 from a count the bridge reports: the rule becomes determinate (a 1 px fill), never guessed
  //   action   {label, onclick | href, kind: 'quiet' | 'primary' | 'link', testid, disabled}
  //   lead     the sentence at the lead size (a full-screen state with nothing else on it)
  import {elapsed} from '../../lib/format.js';
  let {tone = 'wait', text = '', sub = '', since = null, rule = false, progress = null, action = null, lead = false,
    testid = undefined, children} = $props();

  const TONE = {work: 'wait', hold: 'wait', faint: 'wait'};
  const t = $derived(TONE[tone] || tone);
  let now = $state(Date.now());
  $effect(() => {
    if (since == null) return;
    now = Date.now();
    const i = setInterval(() => { now = Date.now(); }, 500);
    return () => clearInterval(i);
  });
  const secs = $derived(since == null ? 0 : Math.max(0, Math.floor((now - since) / 1000)));
  const counting = $derived(secs >= 10);
  const shown = $derived(counting ? String(text).replace(/\.\s*$/, '') : text);
  const p = $derived(progress == null || !Number.isFinite(Number(progress)) ? null : Math.max(0, Math.min(1, Number(progress))));
  const cls = $derived(action && action.kind === 'primary' ? 'btn primary' : action && action.kind === 'link' ? 'link' : 'btn quiet');
</script>

<div class="sl {t}" class:lead role={t === 'bad' ? 'alert' : 'status'} data-testid={testid}>
  <span class="dot" aria-hidden="true"></span>
  <div class="body">
    <p class="t">{shown}{#if counting}<span class="el num">{' · ' + elapsed(secs)}</span>{/if}</p>
    {#if sub}<p class="s">{sub}</p>{/if}
    {@render children?.()}
    {#if p !== null}
      <i class="rule det" aria-hidden="true" style:--p={p}></i>
    {:else if rule}
      <i class="rule ind" aria-hidden="true"></i>
    {/if}
  </div>
  {#if action}
    {#if action.href}
      <a class={cls + ' act'} href={action.href} data-testid={action.testid}>{action.label}</a>
    {:else}
      <button class={cls + ' act'} type="button" onclick={action.onclick} disabled={action.disabled} data-testid={action.testid}>{action.label}</button>
    {/if}
  {/if}
</div>

<style>
  .sl {
    display: grid; grid-template-columns: 6px minmax(0, 1fr) auto; column-gap: var(--s3); align-items: start;
    max-width: 72ch;
  }
  .dot { width: 6px; height: 6px; border-radius: 50%; margin-top: 7px; background: var(--faint); }
  .ok .dot { background: var(--ok); }
  .bad .dot { background: var(--bad); }
  .none .dot { visibility: hidden; }
  .body { min-width: 0; display: grid; gap: var(--s1); }
  .t { font-size: var(--fs-body); line-height: var(--lh-body); color: var(--ink); max-width: 60ch; }
  .el { color: var(--sub); white-space: nowrap; }
  .lead .t { font-size: var(--fs-lead); line-height: var(--lh-lead); }
  .lead .dot { margin-top: 9px; }
  .s { font-size: var(--fs-small); line-height: var(--lh-small); color: var(--sub); max-width: 60ch; }
  /* the action centres on the sentence's first line, not on the whole body (a sub-line or rule below it) */
  .act { align-self: start; margin-left: var(--s2); margin-top: calc((var(--lh-body) - var(--ctl-h)) / 2); }
  .lead .act { margin-top: calc((var(--lh-lead) - var(--ctl-h)) / 2); }
  .act.link { align-self: start; margin-top: 0; }
  .rule {
    display: block; position: relative; height: 1px; margin-top: var(--s2); max-width: 240px;
    background: var(--line); overflow: hidden;
  }
  .rule.ind::after {
    content: ''; position: absolute; top: 0; bottom: 0; left: 0; width: 25%;
    background: var(--sub); animation: sweep 1.6s linear infinite;
  }
  .rule.det::after {
    content: ''; position: absolute; inset: 0; background: var(--sub);
    transform-origin: left; transform: scaleX(var(--p)); transition: transform var(--t) var(--ease);
  }
  @keyframes sweep { from { transform: translateX(-100%); } to { transform: translateX(400%); } }
  @media (prefers-reduced-motion: reduce) { .rule.ind { display: none; } }
  @media (max-width: 720px) {
    .sl { grid-template-columns: 6px minmax(0, 1fr); }
    .sl .act { grid-column: 2; justify-self: start; margin: var(--s3) 0 0; }
    .sl .act.link { margin-top: 0; }
  }
</style>
