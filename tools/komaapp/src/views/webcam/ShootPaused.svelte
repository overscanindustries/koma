<script>
  // Shoot while the webcam holds the camera (Shell shows this in Shoot's place while /status.webcam is true). Shoot would
  // take live view and end remote control, which freezes the call for a re-arm (about 20 s on a second session); the bridge refuses that anyway (webcamhost.refuse). Name the holder, one action.
  // Shoot comes back by itself once the webcam has stopped.
  import '../../styles/webcam.css';
  import {wc, setOn} from '../../lib/webcam.svelte.js';
  import {WC} from '../../lib/webcamWords.js';
  import StatusLine from '../../components/ui/StatusLine.svelte';

  // Only this page's own click counts: wc.st and wc.err may be left from an earlier visit to Webcam.
  let asked = $state(false);
  function off() { asked = true; t0 = Date.now(); setOn(false); }
  const failed = $derived(asked && !wc.req && wc.err);
  const stopping = $derived(asked && !failed);
  let t0 = $state(0);
  const status = $derived(failed ? {tone: 'bad', text: WC.requestFailed}
    : stopping ? {tone: 'wait', text: WC.stopping, since: t0}
    : {tone: 'wait', text: WC.shootHeld, sub: WC.shootHeldSub});
</script>

<section class="wc wc-held" aria-labelledby="held-title" data-testid="shoot-webcam-on">
  <!-- hidden as Shoot's own title is: the nav's active item names the page; the status line opens the column -->
  <h1 id="held-title" class="sr-only" tabindex="-1">{WC.shootTitle}</h1>
  <div class="wc-grid">
    <div class="wc-main">
      <StatusLine {...status} testid="shoot-webcam-status" />
      <div class="wc-actions">
        <button class="btn quiet" type="button" onclick={off} disabled={stopping}
          data-testid="shoot-webcam-off">{WC.turnOff}</button>
      </div>
    </div>
  </div>
</section>
