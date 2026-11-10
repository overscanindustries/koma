<script>
  // Connect USB: the camera on first, then the cable. Phases Waiting for the camera and Confirming (4.4).
  // install / update: Koma's KOMALOG.TXT names the build that started, at the last start stage, and it must be this
  // computer's. official: the newest photo is new and carries the official firmware's tag (lib/onboard.svelte.js
  // confirm). The one step that moves on by itself: when the check passes while the step is open, the
  // next step follows after one beat; Done carries the verified sentence. Arriving here already confirmed (Back from
  // Pick a look) stays here.
  import {onMount} from 'svelte';
  import {cam} from '../../lib/camera.svelte.js';
  import {komaVersionText} from '../../lib/words.js';
  import {st, save, plan, next, back, confirm, packageMeta, readPackage, pkgFacts, settled, problemWords} from '../../lib/onboard.svelte.js';
  import {W, SOURCES} from '../../lib/onboardWords.js';
  import Spread from '../../components/onboard/Spread.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';
  import PhaseList from '../../components/ui/PhaseList.svelte';
  import BackAction from '../../components/onboard/BackAction.svelte';
  import GrCamera from '../../components/onboard/GrCamera.svelte';

  const mode = st.mode || 'update';
  const official = mode === 'official';
  let phase = $state(st.confirmed ? 'done' : 'wait');   // wait | confirm | done | failed
  let why = $state('');
  let now = $state(Date.now());
  let pkg = $state(packageMeta());
  if (!st.waitFrom) { st.waitFrom = Date.now(); save(); }
  let confirmFrom = $state(0);
  let beat = 0;

  onMount(() => {
    const t = setInterval(() => { now = Date.now(); }, 500);
    if (!pkg && mode !== 'official') readPackage().then(p => { pkg = pkgFacts(p); }).catch(() => {});
    return () => { clearInterval(t); clearTimeout(beat); };
  });

  // A check asked for while one runs (the camera came back meanwhile) runs right after it.
  let again = false;
  async function check() {
    if (phase === 'done') return;
    if (phase === 'confirm') { again = true; return; }
    phase = 'confirm'; why = ''; confirmFrom = Date.now();
    try {
      const r = await settled(confirm);
      if (r.ok) {
        st.confirmed = {...r, at: Date.now()}; save(); phase = 'done'; again = false;
        beat = setTimeout(next, 180);
        return;
      }
      why = r.why; phase = 'failed';
    } catch (e) { why = 'read'; phase = 'failed'; }
    if (again && cam.state === 'cam') { again = false; check(); }
  }
  // When the camera is on the cable and nothing is confirmed yet, check it.
  $effect(() => { if (mode !== 'none' && cam.state === 'cam' && phase === 'wait') check(); });
  // Every new connection (the camera restarted) is checked again.
  let lastConn = cam.conn;
  $effect(() => { if (mode !== 'none' && cam.state === 'cam' && cam.conn !== lastConn) { lastConn = cam.conn; check(); } });
  // Nothing to confirm: the camera appearing is the step's end.
  let wasOn = cam.state === 'cam';
  $effect(() => {
    const on = cam.state === 'cam';
    if (mode === 'none' && on && !wasOn) beat = setTimeout(next, 180);
    wasOn = on;
  });

  const waited = $derived(now - st.waitFrom);
  const late = $derived(phase === 'wait' && cam.state !== 'cam' && waited > 60000);
  // the wait counts from the step's own start, saved, so a reload keeps the count
  const phases = $derived([
    {id: 'wait', label: W.connect.phases.wait, since: st.waitFrom || null, state: phase === 'wait' ? 'now' : 'done'},
    {id: 'confirm', label: W.connect.phases.confirm, since: confirmFrom || null,
      state: phase === 'confirm' ? 'now' : phase === 'done' ? 'done' : phase === 'failed' ? 'failed' : 'todo'},
  ]);
  // why the camera is not there, when the bridge knows: the same words as Connect, in place of the late line
  const problemText = $derived(phase === 'wait' ? problemWords() : null);
  const v = $derived(pkg && pkg.version ? pkg.version : '');
  const vText = $derived(komaVersionText(v));
  const lead = $derived(mode === 'none' ? (cam.state === 'cam' ? W.connect.connected : W.connect.lead)
    : phase === 'done' ? (official ? W.connect.official : W.connect.running(vText))
    : official ? W.connect.leadOfficial : W.connect.lead);
  const lines = $derived(mode === 'none' ? (cam.state === 'cam' ? [] : [W.connect.line])
    : phase === 'done' ? []
    : [official ? W.connect.lineOfficial : W.connect.line]);
  const failText = $derived({noKoma: W.connect.noKoma, otherBuild: W.connect.otherBuild, incomplete: W.connect.incomplete,
    noNewPhoto: W.connect.noNewPhoto, stillKoma: W.connect.stillKoma, tagUnknown: W.camera.tagUnknown}[why] || W.camera.readFailed);
  const canGo = $derived(mode === 'none' ? cam.state === 'cam' : phase === 'done');
  // Power left of the shutter on the top plate: both leaders run up and right, side by side and clear of the hot shoe
  // (up and left from the power button would cut its corner); the shutter's circle sits one step further out.
  const callouts = $derived(official
    ? [{id: 'power', n: 1, dir: 'up-right'}, {id: 'shutter', n: 2, dir: 'up-right', extra: 3}, {id: 'usb-door', n: 3, dir: 'right'}]
    : [{id: 'power', n: 1, dir: 'up-right'}, {id: 'usb-door', n: 2, dir: 'right'}]);
  const detailText = $derived([
    st.confirmed && st.confirmed.build ? 'KOMALOG.TXT build: ' + st.confirmed.build + (st.confirmed.stage ? ', stage ' + st.confirmed.stage : '') : '',
    why === 'otherBuild' || why === 'incomplete' ? 'Not confirmed: ' + why : '',
    pkg && pkg.build ? 'This computer: ' + (vText || 'Koma') + ', build ' + pkg.build : '',
    st.confirmed && st.confirmed.tag ? 'Newest photo ' + st.confirmed.photo + ': tag ' + st.confirmed.tag : '',
    'Source: ' + (official ? officialSource()[0] + ' [' + officialSource()[1] + ']' : SOURCES.connect[0] + ' [' + SOURCES.connect[1] + ']'),
  ].filter(Boolean).join('\n'));
  function officialSource() { return st.cam && st.cam.gen === 1 ? SOURCES.official[1] : SOURCES.official.other; }
</script>

{#snippet figure()}
  <GrCamera view="top" highlight={official ? ['power', 'shutter'] : ['power', 'usb-door']} {callouts} testid="setup-figure" />
{/snippet}

<Spread step="connect" index={st.step + 1 + ' / ' + plan().length} title={W.steps.connect} {lead} {lines}
  figure={phase !== 'done' && !(mode === 'none' && cam.state === 'cam') ? figure : null}>
  {#snippet body()}
    {#if mode !== 'none'}
      <PhaseList {phases} label={W.steps.connect} testid="connect-phases" />
      {#if phase === 'failed'}
        <StatusLine tone="bad" text={failText} testid="connect-status"
          action={{label: W.connect.readAgain, onclick: check, disabled: cam.state !== 'cam'}} />
      {:else if problemText}
        <StatusLine tone="bad" text={problemText.text} sub={problemText.sub} testid="connect-status" />
      {:else if late}
        <StatusLine text={W.connect.late} testid="connect-status" />
      {/if}
      <details class="details"><summary>{W.details}</summary><pre>{detailText}</pre></details>
    {/if}
  {/snippet}
  {#snippet actions()}
    <BackAction onclick={back} />
    <button class="btn primary" class:onb-wait={!canGo} type="button" onclick={next} disabled={!canGo} data-testid="setup-next">{W.next}</button>
  {/snippet}
</Spread>
