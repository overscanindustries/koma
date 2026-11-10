<script>
  // Webcam: one switch whose row is the decided line with the device name the call app lists,
  // the preview, two facts while the camera streams (the two halves of the pipeline: the virtual device, the last
  // frame), what this computer is missing, the call app's two steps, and symptom-first help while it runs. One fact,
  // one place: while something needs saying (waking, no live view, the camera gone, the virtual camera not running),
  // the status line says it and the spec sheet steps aside. While the computer still misses something and the webcam
  // is off, those steps come first: they are the next thing to do. The bridge runs the webcam itself (webcamhost.py),
  // so it keeps going when this page closes or another area opens. MOCK only.
  import '../../styles/webcam.css';
  import {bridgeUrl, TOKEN} from '../../lib/bridge.js';
  import {wc, enter, leave, setOn, view} from '../../lib/webcam.svelte.js';
  import {WC} from '../../lib/webcamWords.js';
  import {STATE} from '../../lib/words.js';
  import PageTitle from '../../components/ui/PageTitle.svelte';
  import Segmented from '../../components/ui/Segmented.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';
  import SectionHead from '../../components/ui/SectionHead.svelte';
  import SpecSheet from '../../components/ui/SpecSheet.svelte';
  import Details from '../../components/ui/Details.svelte';
  import CopyButton from '../../components/ui/CopyButton.svelte';

  $effect(() => { enter(); return leave; });

  const st = $derived(wc.st || {});
  const v = $derived(view(wc.st, wc.req));

  // The status line counts from when the shown phase began; it shows the count past 10 s.
  $effect(() => { if (v.phase !== wc.phase) { wc.phase = v.phase; wc.phaseT0 = Date.now(); } });

  const status = $derived.by(() => {
    if (wc.err) return {tone: 'bad', text: WC.requestFailed};
    if (v.phase === 'stop') return {tone: 'wait', text: WC.stopping, since: wc.phaseT0};
    if (st.error && !v.on) {
      const code = st.error.code;
      return code === 'cli_running' ? {tone: 'bad', text: WC.cliRunning, sub: WC.cliRunningSub}
        : code === 'stopping' ? {tone: 'bad', text: WC.stoppingRace, sub: WC.stoppingRaceSub}
        : {tone: 'bad', text: WC.failed, sub: WC.failedSub};
    }
    // the camera went away mid-call: disconnected mid-action (faint dot, one action)
    if (v.phase === 'nocam') return {tone: 'wait', text: STATE.lost};
    if (v.phase === 'nolive') return {tone: 'bad', text: WC.noLive, sub: WC.noLiveSub};
    if (v.phase === 'waking') return {tone: 'wait', text: WC.waking, since: wc.phaseT0};
    if (v.vcamDown) {
      return v.steps.length ? {tone: 'bad', text: WC.vcamDown, sub: WC.vcamDownSub(v.steps.length)}
        : {tone: 'bad', text: WC.vcamFailed, sub: WC.vcamFailedSub};
    }
    return null;
  });

  const running = $derived(!!(st.on && !st.stopping));
  const spec = $derived(v.phase === 'live' && !status);
  const preview = $derived(running ? bridgeUrl('/webcam/preview?s=' + st.session) : '');
  const obsUrl = $derived(st.mjpeg ? 'http://127.0.0.1:' + st.mjpeg.port + '/' : '');
  const stepText = s => WC.step[s.id](s);
  // What is missing comes before the switch while the webcam is off: it is what to do first. Once on, the steps
  // follow the status line that names them.
  const stepsFirst = $derived(v.steps.length > 0 && !v.on);
  const showHelp = $derived(running && !v.vcamDown && !v.steps.length);
  let nameCopied = $state(false);
  // The quoted name and its copy control stay on one line. A long name (a v4l2 card label is anyone's text) keeps only
  // its last word with the control, so it can still wrap at 390 px.
  const quoted = $derived.by(() => {
    const d = v.device || '', i = d.lastIndexOf(' ');
    if (d.length <= 24 || i < 0) return ['', WC.quote(d)];
    const q = WC.quote(d);
    return [q.slice(0, i + 2), q.slice(i + 2)];
  });
  const specRows = $derived([
    {label: WC.virtualDevice, value: v.vdev, testid: 'spec-device'},
    {label: WC.lastFrame, value: v.last, testid: 'spec-last'},
  ]);
  const helpRows = WC.helpRows.map(([label, value]) => ({label, value}));
  const details = $derived([
    ...(running ? [
      {k: WC.dFps, v: WC.fps(st.fps_in || 0)},
      {k: WC.dFrames, v: String(st.frames || 0)},
      st.source && {k: WC.dSource, v: WC.dims(st.source[0], st.source[1])},
      st.output && {k: WC.dOutput, v: WC.dOutputVal(st.output[0], st.output[1], st.fit)},
      {k: WC.dRearms, v: String(st.rearms || 0)},
      {k: WC.dReconnects, v: String(st.reconnects || 0)},
      {k: WC.dKeepalives, v: String(st.keepalives || 0)},
      st.mjpeg && !TOKEN && (st.mjpeg.ok ? {k: WC.dObs, v: obsUrl, copy: obsUrl} : {k: WC.dObs, v: WC.dObsOff(st.mjpeg.port)}),
      st.mjpeg && !TOKEN && st.mjpeg.ok && {k: WC.dReaders, v: String(st.readers || 0)},
      st.vcam && st.vcam.error && {k: WC.dVcamError, v: st.vcam.error},
    ] : []),
    st.error && {k: WC.dError, v: st.error.text},
  ].filter(Boolean));
</script>

<section class="wc" aria-labelledby="wc-title" data-testid="webcam">
  <PageTitle id="wc-title" title={WC.title} />

  <div class="wc-grid">
    <div class="wc-main">
      <div class="wc-top">
        {#if stepsFirst}{@render setup(true)}{/if}

        <div class="wc-row wc-switch">
          <p class="wc-row-text" data-testid="webcam-device">
            <!-- the copy control hugs the name it copies; the two never part at a line end -->
            {#if v.device}{WC.showsUp[0]}{quoted[0]}<span class="wc-nowrap">{quoted[1]}<CopyButton text={v.device}
              label={WC.copyName(v.device)} inline bind:copied={nameCopied} testid="copy-device" /></span>{WC.showsUp[1]}<span
              class="wc-copied small muted" aria-live="polite" data-testid="copy-device-done">{nameCopied ? WC.copied : ''}</span
              >{:else}{WC.row}{/if}
          </p>
          <div class="wc-switch-ctl" data-testid="webcam-switch" data-on={v.on ? 'true' : 'false'}
            data-live={v.flowing && v.on ? 'true' : 'false'}>
            <Segmented name="webcam" labelledby="wc-title" value={v.on ? 'on' : 'off'} onchange={x => setOn(x === 'on')}
              options={[{value: 'off', label: WC.off}, {value: 'on', label: WC.on}]} live={v.flowing ? 'on' : undefined}
              disabled={v.stopping || !!wc.req} />
          </div>
        </div>
        {#if TOKEN}<p class="small muted wc-row-sub" data-testid="webcam-runs-on">{WC.runsOnComputer}</p>{/if}

        {#if status}<div class="wc-status"><StatusLine {...status} testid="webcam-status" /></div>{/if}

        {#if spec}
          <div class:wc-stale={v.stale}><SpecSheet rows={specRows} top={false} testid="webcam-spec" /></div>
        {/if}
      </div>

      <div class="wc-rest">
        {#if v.steps.length && !stepsFirst}{@render setup(false)}{/if}

        <!-- the call app's steps once nothing is left to install (the switch cannot work before) -->
        {#if !stepsFirst}
          <SectionHead label={WC.inCall} />
          <ol class="wc-steps" data-testid="webcam-call">
            <li><div><p>{WC.call1}</p><p class="small muted">{WC.call1Sub}</p></div></li>
            <li><div><p>{WC.call2(v.device)}</p><p class="small muted">{WC.call2Sub}</p></div></li>
          </ol>
          <p class="small muted wc-audio">{TOKEN ? WC.audioPaired : WC.audio}</p>
        {/if}

        {#if showHelp}
          <SectionHead label={WC.help} />
          <div class="wc-help"><SpecSheet rows={helpRows} top={false} testid="webcam-help" /></div>
        {/if}

        {#if details.length}
          <div class="wc-foot"><Details label={WC.details} items={details} testid="webcam-details" /></div>
        {/if}
      </div>
    </div>

    {#if preview}
      <div class="wc-side">
        <!-- komacam's output. While no virtual camera takes it, the call has nothing: the caption says so -->
        <figure class="wc-preview" data-testid="webcam-preview">
          <div class="wc-frame"><img src={preview} alt={v.vcamDown ? WC.previewAltDown : WC.previewAlt} width="1280"
            height="720"></div>
          {#if v.vcamDown}<figcaption class="small muted" data-testid="webcam-preview-caption">{WC.previewDown}</figcaption>{/if}
        </figure>
      </div>
    {/if}
  </div>
</section>

<!-- What the computer that runs Koma is missing, in the order to do it. A command a person runs on Windows shows in
     full above its Copy command: Command Prompt users read what they paste. -->
{#snippet setup(first)}
  <SectionHead label={TOKEN ? WC.setupPaired : WC.setup} {first} />
  <ol class="wc-steps" class:one={v.steps.length === 1} data-testid="webcam-setup">
    {#each v.steps as s (s.id)}
      <li data-step={s.id}>
        <div>
          {#if s.id === 'obs_install'}
            <p>{WC.step.obs_install().split('obsproject.com')[0]}<a class="link" href={WC.obsLink} target="_blank"
              rel="noopener noreferrer">obsproject.com</a>.</p>
          {:else}
            <p>{stepText(s)}</p>
          {/if}
          {#if s.command}
            <p class="small muted">{WC.runCommand(s.platform)}</p>
            {#if s.platform === 'windows'}<p class="mono small wc-cmd" data-testid={'cmd-' + s.id}>{s.command}</p>{/if}
            <CopyButton text={s.command} words={WC.copyCommand} done={WC.copied} testid={'copy-' + s.id} />
          {/if}
        </div>
      </li>
    {/each}
  </ol>
{/snippet}
