<script>
  // Disconnected. Two uses:
  //   <Disconnected />  the Shell's one line once the camera has gone away after it was connected in this tab: the
  //                     bridge's named problem (bad dot, words.js CONNECT), else STATE.lost with a counter from the drop,
  //                     plus the pending-edits line when edits wait to be applied.
  //   <Disconnected text="The camera has not come back. Reconnect it and keep waiting." action={{label, onclick}} since />
  //                     a screen's own mid-action line (a faint dot, one action). No reassurance ("Nothing is lost" can
  //                     be false after the point of no return).
  import StatusLine from './StatusLine.svelte';
  import {connectProblem} from '../../lib/camera.svelte.js';
  import * as SET from '../../lib/settings.svelte.js';
  import {STATE, CONNECT} from '../../lib/words.js';
  import {route} from '../../lib/ui.svelte.js';
  let {text = '', action = null, since = null, testid = 'disconnected'} = $props();

  const problem = $derived(text ? null : connectProblem());
  const line = $derived.by(() => {
    if (text) return {key: 'own', tone: 'wait', text, sub: ''};
    const p = problem;
    if (!p) return {key: 'lost', tone: 'wait', text: STATE.lost, sub: ''};
    if (p.kind === 'usb') return {key: 'usb', tone: 'bad', text: CONNECT.usb.text, sub: CONNECT.usb.sub};
    if (p.kind === 'two') return {key: 'two', tone: 'wait', text: CONNECT.two(p.n), sub: ''};
    if (p.kind === 'open') return {key: 'open-' + p.sub, tone: 'bad', text: CONNECT.open.text, sub: CONNECT.open[p.sub]};
    return {key: 'mode', tone: 'bad', text: CONNECT.mode.text(p.mode), sub: CONNECT.mode.sub};
  });
  // The counter runs from the moment the shown state began; a new state starts it again.
  let t0 = $state(Date.now()), lastKey = '';
  $effect(() => { const k = line.key; if (k !== lastKey) { lastKey = k; t0 = Date.now(); } });
  // Edits made before the drop wait for the camera (settings.svelte.js; pendingCount once the settings package adds it).
  // On Settings the pending bar already carries it.
  const waiting = $derived.by(() => {
    if (text || route.top === 'settings') return 0;
    void SET.S.rev;
    if (typeof SET.pendingCount === 'function') return SET.pendingCount();
    return SET.pending().length + (SET.unmappedChanged() > 0 ? 1 : 0);
  });
</script>

<StatusLine tone={line.tone} text={line.text} sub={line.sub} {action}
  since={text ? since : line.tone === 'wait' ? t0 : null} {testid}>
  {#if waiting > 0}<p class="small muted">{CONNECT.pendingWaits(waiting)}</p>{/if}
</StatusLine>
