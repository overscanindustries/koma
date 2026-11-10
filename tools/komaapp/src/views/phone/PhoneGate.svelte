<script>
  // A phone without access: pairing from the QR code's address, a code that was already used, Koma not answering, or
  // a token that was revoked (every request answers 403). Full screen, the wordmark, one status line.
  import '../../styles/webcam.css';
  import {pair} from '../../lib/phone.svelte.js';
  import {cam} from '../../lib/camera.svelte.js';
  import {lsGet} from '../../lib/bridge.js';
  import {PH} from '../../lib/webcamWords.js';
  import Wordmark from '../../components/Wordmark.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';

  // A phone that kept a token the bridge now refuses was paired once: its access was revoked on the computer.
  const hadToken = !!lsGet('koma.token');
  const s = $derived(pair.state === 'pairing' ? {tone: 'wait', text: PH.pairing}
    : pair.state === 'failed' && pair.why === 'stale' ? {tone: 'bad', text: PH.stale, sub: PH.staleSub}
    : pair.state === 'failed' && pair.why === 'computer' ? {tone: 'wait', text: PH.computer, sub: PH.computerSub}
    : pair.state === 'failed' ? {tone: 'bad', text: PH.unreachable, sub: PH.unreachableSub}
    : hadToken && cam.tokenRefused ? {tone: 'wait', text: PH.revokedHere, sub: PH.notPairedSub}
    : {tone: 'wait', text: PH.notPaired, sub: PH.notPairedSub});
</script>

<div class="wc wc-gate" data-testid="phone-gate">
  <header><Wordmark /></header>
  <main class="wc-gate-body" aria-labelledby="gate-title">
    <h1 id="gate-title" class="sr-only">{PH.title}</h1>
    <StatusLine {...s} testid="phone-gate-status" />
    {#if pair.why === 'computer'}<p class="wc-gate-alt"><a class="link" href={(pair.local || './') + '#/home'}
      onclick={() => { pair.state = ''; pair.why = ''; }}>{PH.openHere}</a></p>{/if}
  </main>
</div>
