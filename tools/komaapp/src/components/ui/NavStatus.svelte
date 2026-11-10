<script>
  // Identity and connection, the one place for both: under the wordmark in the nav column on
  // a desktop, in the one top line on a phone. ● GR DIGITAL (+ SIM when the bridge runs a simulated camera), the
  // connection word beneath on a desktop. On a phone the line is short: the word follows SIM whenever the camera is not
  // connected, so a drop is never told by the dot's colour alone; connected, the green dot stands for it (the word stays
  // for screen readers). Identical on every app screen. Data: GET /status (camera.model, connected, simulated, webcam).
  import {cam, cameraName} from '../../lib/camera.svelte.js';
  import {S} from '../../lib/settings.svelte.js';
  import {A} from '../../lib/apply.svelte.js';
  import {STATE} from '../../lib/words.js';
  let {compact = false} = $props();

  const on = $derived(cam.state === 'cam');
  const sim = $derived(!!(cam.st && cam.st.simulated));
  // The nav exists only while the app is up: a camera on the cable, an Apply waiting for its restart, card work, or a
  // camera that went away after it was connected in this tab (COHESION 1.5: "Looking" belongs to Connect).
  const webcam = $derived(on && !!(cam.st && cam.st.webcam));
  const word = $derived(on ? (webcam ? 'Webcam on' : 'Connected')
    : A.open ? 'Waiting for the camera'
    : S.source === 'card' ? 'Not connected'
    : cam.sawCamera ? STATE.disconnected : 'Looking');
</script>

<div class="ns" class:compact data-testid="camera-state">
  <p class="id">
    <i class="dot" class:on aria-hidden="true"></i>
    <span class="name">{cameraName()}</span>
    {#if sim}<span class="micro sim" title="The bridge runs a simulated camera">SIM</span>{/if}
  </p>
  <p class="word" class:sr-only={compact && on && !webcam}>{word}</p>
</div>

<style>
  .ns { display: grid; gap: 2px; min-width: 0; }
  .id { display: flex; align-items: center; gap: var(--s2); min-width: 0; }
  .dot { width: 6px; height: 6px; border-radius: 50%; flex: none; background: var(--faint); }
  .dot.on { background: var(--ok); }
  .name {
    font-size: var(--fs-cam); line-height: var(--lh-cam); font-stretch: 88%; font-weight: 560; letter-spacing: .06em;
    color: var(--ink); white-space: nowrap;
  }
  .sim { color: var(--faint); line-height: 1; }
  .word { padding-left: calc(6px + var(--s2)); color: var(--sub); font-size: var(--fs-small); line-height: var(--lh-small); }
  /* phone: one line, the word after SIM */
  .compact { display: flex; align-items: center; gap: var(--s3); }
  .compact .id { flex: none; }
  .compact .word { padding-left: 0; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
