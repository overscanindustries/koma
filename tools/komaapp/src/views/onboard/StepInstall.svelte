<script>
  // Install Koma / On the camera. A firmware write (Install from the official
  // firmware, Return to official firmware) is Ricoh's own updater at a keyed power-on, one press per screen, each citing
  // its source under Details (lib/onboardWords.js SOURCES.flash). An install then takes the card back to this computer
  // for Koma's file (the second trip of release/docs/install.md) and puts it back in the camera. Koma cannot see the
  // camera while the cable is out: each press is confirmed by the user moving on. From the press that starts the write
  // (MENU/OK) Stop here is disabled with its reason; while the camera writes there is no Stop here at all (the boxed rule
  // says why), and the one answer, The lamp has stopped, stays in view but disabled until the lamp has had time to stop
  // (flashMinMs), pointing at the line that says how long that is. The write is the status line's phase, with an
  // elapsed counter from the moment MENU/OK was pressed. One fact, one place: on MENU/OK the reason sits under POINT
  // OF NO RETURN; while the camera writes the boxed rule does, and the disabled Back points at it. The index line counts
  // every screen of the step, the card trip too (9 OF 10). With Koma going into the camera's internal memory (st.where
  // 'irom') the card trip is followed by one start that runs the installer ('run') and a third trip ('card3').
  import {onMount} from 'svelte';
  import {st, save, plan, next, back, exit, screenIds, FLASH_COMMIT, FLASH_RUNS, flashMinMs} from '../../lib/onboard.svelte.js';
  import {W, SOURCES} from '../../lib/onboardWords.js';
  import Spread from '../../components/onboard/Spread.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';
  import BackAction from '../../components/onboard/BackAction.svelte';
  import GrCamera from '../../components/onboard/GrCamera.svelte';
  import StepCard from './StepCard.svelte';

  const mode = st.mode || 'update';
  const ids = $derived(screenIds(mode, st.where));
  const n = $derived(ids.length);
  const sub = $derived(Math.min(st.sub, n - 1));
  const kind = $derived(ids[sub]);
  let now = $state(Date.now());
  onMount(() => { const t = setInterval(() => { now = Date.now(); }, 500); return () => clearInterval(t); });

  // The figure for each press screen: view, highlight, action, callouts (numbered in the order the step names them;
  // leaders never cross: GrCamera places them).
  const FIG = [
    null,
    {view: 'top', highlight: ['unlock', 'mode-dial'], action: 'turn', callouts: [{id: 'unlock', n: 1, dir: 'up-left'}, {id: 'mode-dial', n: 2, dir: 'right'}]},
    {view: 'back', highlight: ['up', 't'], action: 'press', callouts: [{id: 'up', n: 1, dir: 'right'}, {id: 't', n: 2, dir: 'right'}]},
    {view: 'back', highlight: ['play'], action: 'hold', hold: 3, callouts: [{id: 'up', n: 1, dir: 'right'}, {id: 't', n: 2, dir: 'right'}, {id: 'play', n: 3, dir: 'right'}]},
    {view: 'back', highlight: [], action: null, callouts: [{id: 'up', n: 1, dir: 'right'}, {id: 't', n: 2, dir: 'right'}, {id: 'play', n: 3, dir: 'right'}]},
    // ▶ and MENU/OK sit on one line: ▶'s leader meets its segment above the centre line, so the two run level and apart
    // (a 45-degree leader to MENU/OK would cross DISP.)
    {view: 'back', highlight: ['right', 'menu-ok'], action: 'press', callouts: [{id: 'right', n: 1, dir: 'right', at: [88.1, 33.3]}, {id: 'menu-ok', n: 2, dir: 'right'}]},
    {view: 'back', highlight: ['lamp'], action: null, callouts: [{id: 'lamp', n: 1, dir: 'up-right'}]},
    {view: 'top', highlight: ['power', 'mode-dial'], action: 'press', callouts: [{id: 'power', n: 1, dir: 'up-right'}, {id: 'mode-dial', n: 2, dir: 'right'}]},
  ];
  const isFlash = $derived(kind === 'flash');
  const fig = $derived(isFlash ? FIG[sub] : null);
  const s = $derived(isFlash ? W.oncamera.flash[sub] : kind === 'run' ? W.oncamera.run
    : kind === 'back' ? (st.where === 'irom' ? W.oncamera.backIrom : W.oncamera.back) : {lead: W.oncamera.nothing, lines: []});
  const commit = $derived(isFlash && sub === FLASH_COMMIT);   // MENU/OK starts the write
  const runs = $derived(isFlash && sub === FLASH_RUNS);       // the camera writes
  const written = $derived(runs && now - (st.flashFrom || now) >= flashMinMs());

  function cont() {
    if (sub >= n - 1) { next(); return; }
    st.sub = sub + 1;
    if (ids[st.sub] === 'flash' && st.sub === FLASH_RUNS && !st.flashFrom) st.flashFrom = Date.now();
    save();
  }
  function stop() { st.stopped = true; save(); exit(); }

  const source = $derived(isFlash ? SOURCES.flash[sub] : st.where === 'irom' ? SOURCES.irom : mode === 'install' ? SOURCES.card2 : SOURCES.card);
  const title = st.plan === 'wizard' ? W.steps.install : W.steps.oncamera;
  const index = st.step + 1 + ' / ' + plan().length;
  const screenText = $derived(n > 1 ? W.screen(sub + 1, n) : '');
</script>

{#snippet figure()}
  <GrCamera view={fig.view} highlight={fig.highlight} action={fig.action} hold={fig.hold || 0} callouts={fig.callouts}
    play={sub} testid="setup-figure" />
{/snippet}

{#if kind === 'card'}
  <!-- the second card trip of an install: Koma's file on, the update file off; read back like the first -->
  <StepCard trip={2} {title} {index} sub={screenText} step={'install-' + sub} onnext={cont} />
{:else if kind === 'card3'}
  <!-- the /IROM route's last trip: the installer's record read, the installer off the card -->
  <StepCard trip={3} {title} {index} sub={screenText} step={'install-' + sub} onnext={cont} />
{:else}
  <Spread step={'install-' + sub} {index} {title} lead={s.lead} lines={s.lines}
    sub={screenText} figure={fig ? figure : null}>
    {#snippet body()}
      {#if commit}
        <!-- the label and its reason, one unit -->
        <div class="nr">
          <p class="micro">{W.pointOfNoReturn}</p>
          <p id="stop-why" class="onb-reason">{W.oncamera.stopReason}</p>
        </div>
      {:else if runs}
        <div class="nr">
          <p class="micro">{W.pointOfNoReturn}</p>
          <p id="nr-rule" class="rule-box">{W.oncamera.rule}</p>
        </div>
        <div class="runs">
          <StatusLine text={W.oncamera.writing[mode]} since={st.flashFrom || null} testid="flash-status" />
          <p id="flash-takes" class="small">{W.oncamera.takes}</p>
        </div>
      {/if}
      {#if kind !== 'none'}
        <details class="details"><summary>{W.details}</summary><pre>{'Source: ' + source[0] + ' [' + source[1] + ']'}</pre></details>
      {/if}
    {/snippet}
    {#snippet actions()}
      <BackAction onclick={back} disabled={runs} describedby={runs ? 'nr-rule' : undefined} />
      <div class="act">
        {#if isFlash && sub < FLASH_RUNS}
          <button class="btn quiet" type="button" onclick={stop} disabled={commit}
            aria-describedby={commit ? 'stop-why' : undefined} data-testid="setup-stop">{W.oncamera.stopHere}</button>
        {/if}
        {#if runs}
          <button class="btn primary" type="button" onclick={cont} disabled={!written}
            aria-describedby={written ? undefined : 'flash-takes'} data-testid="setup-next">{W.oncamera.lampStopped}</button>
        {:else}
          <button class="btn primary" type="button" onclick={cont} data-testid="setup-next">{W.next}</button>
        {/if}
      </div>
    {/snippet}
  </Spread>
{/if}

<style>
  .nr { display: grid; gap: 8px; }
  .runs { display: grid; gap: 4px; }
  .runs .small { padding-left: calc(6px + var(--s3)); }
  .act { display: flex; gap: 12px; align-items: center; }
</style>
