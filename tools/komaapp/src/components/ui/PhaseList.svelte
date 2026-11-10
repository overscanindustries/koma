<script>
  // Named phases: a segmented bar, the phase names, an elapsed counter
  // on the running phase past 10 s, a note column, and the POINT OF NO RETURN tag where interruption matters.
  //   phases: [{id, label, state: 'todo' | 'now' | 'done' | 'failed' | 'lost' | 'skipped',
  // since: Date.now at start | elapsed: '0:12' (a wait that spans a closed tab), note, tag (or mark)}]
  // The bar is always drawn: the active segment solid amber (amber once per phase: the running row's dot stays faint),
  // the failed segment --line-strong with the row's dot in --bad. A progress fill inside the active segment appears only
  // once a phase has a duration measured on the camera (lib/measured.js); nothing fills by guesswork.
  import {elapsed} from '../../lib/format.js';
  import {measured} from '../../lib/measured.js';
  let {phases = [], label = 'Progress', testid = undefined} = $props();

  let now = $state(Date.now());
  const running = $derived(phases.some(p => (p.state === 'now' || p.state === 'lost') && p.since));
  $effect(() => {
    if (!running) return;
    now = Date.now();
    const t = setInterval(() => { now = Date.now(); }, 500);
    return () => clearInterval(t);
  });
  const secs = p => p.since ? Math.max(0, Math.floor((now - p.since) / 1000)) : 0;
  const fill = p => { const m = measured(p.id); return m && p.state === 'now' && p.since ? Math.min(1, secs(p) / m) : null; };
  const active = p => p.state === 'now' || p.state === 'lost';
  const right = p => active(p) ? (p.elapsed ? p.elapsed : secs(p) >= 10 ? elapsed(secs(p)) : p.note || '') : p.note || '';
  const WORD = {done: 'finished', failed: 'failed', now: 'in progress', lost: 'waiting', todo: 'not started', skipped: 'skipped'};
</script>

<div class="pl" data-testid={testid}>
  <div class="bar" aria-hidden="true">
    {#each phases as p (p.id)}
      <i class={p.state} style:--f={fill(p) ?? ''} class:filled={fill(p) !== null}></i>
    {/each}
  </div>
  <ol aria-label={label}>
    {#each phases as p (p.id)}
      <li class={p.state} aria-current={active(p) ? 'step' : undefined} data-phase={p.id}>
        <span class="mk" aria-hidden="true"></span>
        <span class="lab">{p.label}{#if p.tag || p.mark}<span class="micro tag">{p.tag || p.mark}</span>{/if}<span class="sr-only">, {WORD[p.state] || ''}</span></span>
        <span class="t num">{right(p)}</span>
      </li>
    {/each}
  </ol>
</div>

<style>
  .bar { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(0, 1fr); gap: 2px; margin-bottom: var(--s3); }
  .bar i { height: 2px; background: var(--line); position: relative; overflow: hidden; }
  .bar i.done { background: var(--sub); }
  .bar i.now { background: var(--acc); }
  .bar i.now.filled { background: var(--line); }
  .bar i.now.filled::after { content: ''; position: absolute; inset: 0; background: var(--acc); transform-origin: left; transform: scaleX(var(--f)); }
  .bar i.failed, .bar i.skipped { background: var(--line-strong); }  /* failure is the row's dot, never a red fill */
  .bar i.lost { background: var(--faint); }
  ol { list-style: none; margin: 0; padding: 0; }
  li {
    display: grid; grid-template-columns: 6px minmax(0, 1fr) auto; align-items: center; column-gap: var(--s3);
    min-height: var(--spec-h); border-bottom: 1px solid var(--line);
    color: var(--faint); font-size: var(--fs-body); line-height: var(--lh-body);
  }
  li:first-child { border-top: 1px solid var(--line); }
  /* todo: a hollow ring (a filled --line-strong dot is about 1.6:1 on dark) */
  .mk { width: 6px; height: 6px; border-radius: 50%; border: 1px solid var(--line-strong); box-sizing: border-box; }
  li.done, li.skipped { color: var(--sub); }
  li.done .mk { background: var(--ok); border-color: var(--ok); }
  li.skipped .mk { background: var(--line-strong); }
  li.now, li.lost { color: var(--ink); }
  li.now .mk, li.lost .mk { background: var(--faint); border-color: var(--faint); }
  li.failed { color: var(--ink); }
  li.failed .mk { background: var(--bad); border-color: var(--bad); }
  .lab { display: flex; align-items: baseline; gap: var(--s3); flex-wrap: wrap; min-width: 0; }
  .tag { color: var(--sub); }
  .t { color: var(--sub); font-size: var(--fs-small); min-width: 3ch; text-align: right; }
</style>
