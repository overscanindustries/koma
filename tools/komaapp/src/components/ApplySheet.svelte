<script>
  // Apply to camera, as it runs: the named phases with an elapsed counter past
  // 10 s, the failed phase marked, one status line, the settings read back. Nothing is called done before the read-back
  // (lib/apply.svelte.js holds the rule).
  import {A, STEPS, retry, sendAnyway, keepWaiting, reread, close} from '../lib/apply.svelte.js';
  import {STATE} from '../lib/words.js';
  import Sheet from './Sheet.svelte';
  import Icon from './Icon.svelte';
  import PhaseList from './ui/PhaseList.svelte';
  import StatusLine from './ui/StatusLine.svelte';
  import Details from './ui/Details.svelte';

  const order = STEPS.map(s => s.id);
  const at = $derived(A.step === 'done' ? order.length : A.step === 'failed' ? order.indexOf(A.failedAt) : order.indexOf(A.step));
  // When each phase began, for its counter.
  let starts = $state({});
  let prev = '';
  $effect(() => {
    const s = A.step;
    if (s === prev) return;
    prev = s;
    if (order.includes(s)) starts = {...starts, [s]: Date.now()};
  });
  function phaseState(i) {
    if (A.step === 'done' || i < at) return 'done';
    if (i === at) return A.step === 'failed' ? (A.failedAt === 'wait' ? 'lost' : 'failed') : 'now';
    return 'todo';
  }
  const phases = $derived(STEPS.map((s, i) => ({id: 'apply-' + s.id, label: A.steps[s.id] || s.label, state: phaseState(i),
    since: phaseState(i) === 'now' || phaseState(i) === 'lost' ? starts[s.id] || null : null})));
  const running = $derived(A.step !== 'done' && A.step !== 'failed');
  const lost = $derived(A.step === 'failed' && A.failedAt === 'wait');
  // While a phase runs, its amber marker and counter are the state: no line repeats the phase name. A line appears for
  // done, lost and failed only; the lost line carries no second counter (the phase row has it).
</script>

<Sheet open={A.open} title={A.title} locked={running} onclose={close} testid="apply-sheet">
  <PhaseList {phases} label="Progress" />

  {#if !running}
    <div class="msg" data-testid="apply-status">
      {#if A.step === 'done'}
        <StatusLine tone="ok" text={A.done || STATE.applied} sub={A.note || STATE.keeps} />
      {:else if lost}
        <StatusLine tone="wait" text={STATE.notBack} />
      {:else}
        <StatusLine tone="bad" text={A.message} sub={A.sub} />
      {/if}
    </div>
  {/if}

  {#if A.results.length}
    <ul class="results" aria-label="Settings read back">
      {#each A.results as r}
        <li class:no={!r.ok} data-result={r.name}>
          <span class="cam">{r.label}</span>
          <span class="val">{r.ok ? r.got : r.got + ', not ' + r.want}</span>
          <span class="icon" role="img" aria-label={r.ok ? 'Confirmed' : 'Not taken'}><Icon name={r.ok ? 'check' : 'x'} size={14} /></span>
        </li>
      {/each}
    </ul>
  {/if}

  {#if A.detail}
    <div class="more"><Details><pre class="mono">{A.detail}</pre></Details></div>
  {/if}

  {#snippet actions()}
    {#if A.step === 'failed'}
      {#if A.canForce}<button class="btn" type="button" onclick={sendAnyway}>Send anyway</button>{/if}
      <button class="btn" type="button" onclick={close}>Close</button>
      {#if A.canReread}<button class="btn primary" type="button" onclick={reread}>{A.rereadLabel}</button>
      {:else if lost}<button class="btn primary" type="button" onclick={keepWaiting}>Keep waiting</button>
      {:else}<button class="btn primary" type="button" onclick={retry}>{A.retryFrom === 'send' ? 'Send again' : 'Try again'}</button>{/if}
    {:else if A.step === 'done'}
      <button class="btn primary" type="button" onclick={close}>Done</button>
    {:else}
      <span class="small muted">Keep the camera on and the cable connected.</span>
    {/if}
  {/snippet}
</Sheet>

<style>
  .msg { padding: var(--s5) 0 var(--s2); }
  .results { list-style: none; margin: var(--s3) 0 0; padding: 0; border-top: 1px solid var(--line); }
  .results li {
    display: grid; grid-template-columns: minmax(0, 1fr) auto 16px; column-gap: var(--s4); align-items: center;
    min-height: var(--spec-h); border-bottom: 1px solid var(--line);
  }
  .results .val { color: var(--sub); font-size: var(--fs-body); }
  .results .icon { color: var(--sub); display: grid; place-items: center; }
  .results li.no .val { color: var(--ink); }
  .results li.no .icon { color: var(--bad); }
  .more { margin-top: var(--s3); }
</style>
