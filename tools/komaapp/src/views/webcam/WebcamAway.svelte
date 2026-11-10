<script>
  // On Connect while the webcam holds the camera and the camera has gone (unplugged or off mid-call): the switch, so the
  // webcam can be turned off from here, and one line. The webcam picks the camera up again by itself when it is back
  // (komacam's supervisor reconnects and re-arms; MOCK). Connect shows this while /status.webcam is true.
  import '../../styles/webcam.css';
  import {wc, setOn} from '../../lib/webcam.svelte.js';
  import {WC} from '../../lib/webcamWords.js';
  import Segmented from '../../components/ui/Segmented.svelte';

  // Only this switch's own OFF counts: wc.st may be left from an earlier visit to Webcam.
  let asked = $state(false);
  function change(on) { if (!on) asked = true; setOn(on); }
  const stopping = $derived(asked && !(wc.err && !wc.req));
</script>

<div class="wc wc-away" data-testid="webcam-away">
  <div class="wc-row">
    <span class="wc-row-label" id="wc-away-label">{WC.row}</span>
    <div class="wc-switch-ctl" data-testid="webcam-away-switch" data-on={stopping ? 'false' : 'true'}>
      <Segmented name="webcam-away" labelledby="wc-away-label" value={stopping ? 'off' : 'on'} onchange={x => change(x === 'on')}
        options={[{value: 'off', label: WC.off}, {value: 'on', label: WC.on}]} disabled={stopping} />
    </div>
  </div>
  <p class="small muted wc-row-sub" role="status">{stopping ? WC.stopping : WC.waitsForCamera}</p>
</div>
