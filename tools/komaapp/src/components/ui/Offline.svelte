<script>
  // Offline: Koma does not answer. Full screen, the wordmark, one status line, no nav. Who is not
  // running, and how to start it on this computer; the status poll keeps running, so the app returns on its own. A phone
  // (a pairing token is set) is not the computer Koma runs on: its first line says "the computer".
  import FullScreen from './FullScreen.svelte';
  import StatusLine from './StatusLine.svelte';
  import {STATE} from '../../lib/words.js';
  import {lsGet} from '../../lib/bridge.js';
  import {pair} from '../../lib/phone.svelte.js';
  const since = Date.now();
  const plat = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
  const where = /^Mac/i.test(plat) || /^macOS/i.test(plat) ? 'mac' : /^Win/i.test(plat) ? 'win' : 'other';
  const phone = $derived(!!(lsGet('koma.token') || pair.state));
</script>

<FullScreen labelledby="off-title">
  <h1 id="off-title" class="sr-only">Koma is not running</h1>
  <StatusLine tone="wait" lead text={phone ? STATE.offlinePhone : STATE.offline}
    sub={STATE.offlineWhere[where]} {since} testid="offline" />
</FullScreen>
