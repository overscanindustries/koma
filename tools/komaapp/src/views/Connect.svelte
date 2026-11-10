<script>
  // Connect: no camera yet in this tab. After the first connection a drop keeps the
  // app and the Shell says so (App.svelte). One state at a time, in this order: the phone's code form; a problem the
  // bridge names (USB library missing, two cameras, could not open, wrong USB mode; camera.svelte.js connectProblem);
  // found (the bridge is opening the camera); looking. The plug order is the instruction: on first, then USB (plugged in while off, the camera boots into a mode the bridge cannot use). The bridge not answering is
  // the Offline screen. Every word: words.js CONNECT. The figure: the top view, callout 1 on the power button, 2 on the
  // USB door, matching the steps (static: no highlight until a hold time is measured).
  import {cam, connectProblem} from '../lib/camera.svelte.js';
  import {go, screen} from '../lib/ui.svelte.js';
  import {lsSet} from '../lib/bridge.js';
  import * as SET from '../lib/settings.svelte.js';
  import {CONNECT} from '../lib/words.js';
  import FullScreen from '../components/ui/FullScreen.svelte';
  import StatusLine from '../components/ui/StatusLine.svelte';
  import Details from '../components/ui/Details.svelte';
  import Offline from '../components/ui/Offline.svelte';
  import GrCamera from '../components/onboard/GrCamera.svelte';
  import WebcamAway from './webcam/WebcamAway.svelte';
  import {webcamHolds} from '../lib/webcam.svelte.js';

  let code = $state('');
  function useCode(e) {
    e.preventDefault();
    const t = code.trim(), m = /[?&]token=([^&#\s]+)/.exec(t);
    const token = m ? decodeURIComponent(m[1]) : t;
    if (!token) return;
    lsSet('koma.token', token);
    location.search = '?token=' + encodeURIComponent(token);
  }

  const CALLOUTS = [{id: 'power', n: 1, dir: 'up-right'}, {id: 'usb-door', n: 2, dir: 'right'}];
  const problem = $derived(connectProblem());
  // Another /status holds the bridge's lock with no session yet: the bridge is opening the camera.
  const found = $derived(!!(cam.st && cam.st.busy && !cam.st.connected));
  const key = $derived(cam.forbidden ? 'code' : problem ? problem.kind + (problem.sub ? '-' + problem.sub : '') : found ? 'found' : 'looking');
  // The counter (and the 20 s ladder) start again whenever the shown state changes.
  let since = $state(Date.now()), shownKey = '';
  $effect(() => { const k = key; if (k !== shownKey) { shownKey = k; since = Date.now(); } });
  let now = $state(Date.now());
  $effect(() => { const t = setInterval(() => { now = Date.now(); }, 1000); return () => clearInterval(t); });
  const long = $derived(key === 'looking' && now - since >= 20000);

  const micro = $derived(problem && problem.kind === 'usb' ? CONNECT.usb.label : problem && problem.kind === 'mode' ? CONNECT.mode.label : 'No camera');
  // Under Details: the bridge's own words for the USB library problem, and the one command that fixes it from a checkout.
  const fix = $derived(problem && problem.kind === 'usb'
    ? (/libusb|backend/i.test(problem.message) ? CONNECT.usb.brew : /module|pyusb/i.test(problem.message) ? CONNECT.usb.pip : '')
    : '');
  const waiting = $derived.by(() => {
    void SET.S.rev;
    if (typeof SET.pendingCount === 'function') return SET.pendingCount();
    return SET.pending().length + (SET.unmappedChanged() > 0 ? 1 : 0);
  });
</script>

{#snippet figure()}
  <div class="fig"><GrCamera view="top" callouts={CALLOUTS} testid="connect-figure" /></div>
{/snippet}

{#if cam.state === 'off' && !cam.forbidden}
  <Offline />
{:else}
  <FullScreen labelledby="connect-title" aside={screen.phone ? undefined : figure}>
    <p class="micro">{micro}</p>
    <h1 id="connect-title" class="title-d">Connect your GR</h1>

    {#if screen.phone}
      <div class="fig phone"><GrCamera view="top" callouts={CALLOUTS} maxWidth={240} testid="connect-figure" /></div>
    {/if}

    <ol class="steps">
      {#each CONNECT.plugOrder as s, i}
        <li>
          <span class="n num" aria-hidden="true">{i + 1}</span>
          <div class="st">
            <h2 class="lead">{s.lead}</h2>
            <p class="small muted">{s.small}</p>
          </div>
        </li>
      {/each}
    </ol>

    <div class="state" data-state={key}>
      {#if cam.forbidden}
        <form class="code" onsubmit={useCode}>
          <label for="code" class="small muted">Code from the computer</label>
          <div class="row">
            <input id="code" class="input" bind:value={code} autocomplete="off" spellcheck="false" placeholder="The code or the whole address">
            <button class="btn primary" type="submit">Continue</button>
          </div>
          <p class="small muted">Koma printed this code on the computer when it started.</p>
        </form>
      {:else if problem && problem.kind === 'usb'}
        <StatusLine tone="bad" text={CONNECT.usb.text} sub={CONNECT.usb.sub} testid="connect-status" />
        <div class="more"><Details><pre class="raw">{problem.message}{fix ? '\n' + fix : ''}</pre></Details></div>
      {:else if problem && problem.kind === 'two'}
        <StatusLine tone="wait" text={CONNECT.two(problem.n)} {since} testid="connect-status" />
      {:else if problem && problem.kind === 'open'}
        <StatusLine tone="bad" text={CONNECT.open.text} sub={CONNECT.open[problem.sub]} testid="connect-status" />
        <div class="more"><Details><pre class="raw">{problem.message}</pre></Details></div>
      {:else if problem && problem.kind === 'mode'}
        <StatusLine tone="bad" text={CONNECT.mode.text(problem.mode)} sub={CONNECT.mode.sub} testid="connect-status" />
      {:else if found}
        <StatusLine tone="wait" text={CONNECT.found} {since} rule testid="connect-status" />
      {:else}
        <StatusLine tone="wait" text={CONNECT.looking} {since} rule testid="connect-status" />
        {#if long}
          <details class="details ladder">
            <summary>Still looking</summary>
            <ol>{#each CONNECT.ladder as l}<li>{l}</li>{/each}</ol>
          </details>
        {/if}
      {/if}
      {#if waiting > 0 && !cam.forbidden}<p class="small muted waits">{CONNECT.pendingWaits(waiting)}</p>{/if}
    </div>

    {#if webcamHolds()}<WebcamAway />{/if}

    <p class="alt"><button class="link" type="button" onclick={() => go('card')}>Work from a memory card instead</button></p>
  </FullScreen>
{/if}

<style>
  .title-d {
    margin-top: var(--s3);
    font-size: var(--fs-display); line-height: var(--lh-display); font-weight: 500; letter-spacing: -.02em;
  }
  .steps {
    list-style: none; margin: var(--s7) 0 0; padding: 0;
    display: grid; grid-template-columns: 1fr 1fr;
    border-top: 1px solid var(--line); border-bottom: 1px solid var(--line);
  }
  .steps li { display: grid; grid-template-columns: auto minmax(0, 1fr); column-gap: var(--s4); padding: var(--s5) var(--s5) var(--s5) 0; }
  .steps li + li { border-left: 1px solid var(--line); padding-left: var(--s5); }
  .n { font-size: var(--fs-display); line-height: 40px; font-weight: 400; font-stretch: 88%; color: var(--sub); }
  .st { display: grid; gap: var(--s1); align-content: start; padding-top: 2px; }
  .st p { max-width: 36ch; }
  .state { margin-top: var(--s6); min-height: 72px; }
  .more, .waits { margin-top: var(--s3); padding-left: calc(6px + var(--s3)); }
  .raw {
    margin: 0; white-space: pre-wrap; overflow-wrap: anywhere;
    font-family: var(--mono); font-size: var(--fs-small); line-height: var(--lh-small); color: var(--sub);
  }
  .code { display: grid; gap: var(--s2); max-width: 520px; }
  .code .row { display: flex; gap: var(--s2); }
  .code .input { flex: 1; }
  .ladder { margin: var(--s4) 0 0 calc(6px + var(--s3)); }
  .ladder ol { margin: var(--s2) 0 0; padding: 0 0 0 1.4em; color: var(--sub); font-size: var(--fs-small); line-height: 20px; }
  .alt { margin-top: var(--s6); }
  /* desktop: the figure's top meets the steps' top rule (micro-label, title and the steps' margin above it) */
  .fig { padding-top: calc(var(--lh-micro) + var(--s3) + var(--lh-display) + var(--s7)); }
  .fig.phone { padding-top: var(--s5); max-height: 240px; overflow: hidden; display: flex; justify-content: center; }
  @media (max-width: 720px) {
    .steps { grid-template-columns: 1fr; margin-top: var(--s5); }
    .steps li { padding: var(--s4) 0; }
    .steps li + li { border-left: 0; border-top: 1px solid var(--line); padding-left: 0; }
    .n { font-size: 36px; line-height: 32px; }
  }
</style>
