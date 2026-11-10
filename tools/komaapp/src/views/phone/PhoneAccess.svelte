<script>
  // Settings > Use on your phone: one row OFF | ON. ON shows the QR code with a one-time
  // pairing code, the address beneath it in tabular figures, and one Revoke. Off by default. On a paired phone the
  // section only says so: access is managed on the computer. MOCK only (never tried with a real phone).
  import '../../styles/webcam.css';
  import {TOKEN} from '../../lib/bridge.js';
  import {ph, enter, leave, setOn, revoke} from '../../lib/phone.svelte.js';
  import {PH, WC} from '../../lib/webcamWords.js';
  import Segmented from '../../components/ui/Segmented.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';
  import Details from '../../components/ui/Details.svelte';
  import CopyButton from '../../components/ui/CopyButton.svelte';
  import Qr from '../../components/webcam/Qr.svelte';

  $effect(() => { enter(); return leave; });

  const st = $derived(ph.st || {});
  const on = $derived(ph.req === 'on' || (!!st.on && ph.req !== 'off'));
  const status = $derived.by(() => {
    if (ph.err) return {tone: 'bad', text: PH.requestFailed};
    if (ph.req === 'on') return {tone: 'wait', text: PH.opening};
    if (ph.req === 'off') return {tone: 'wait', text: PH.closing};
    if (st.error && !st.on) {
      return st.error.code === 'no_network' ? {tone: 'bad', text: PH.noNetwork, sub: PH.noNetworkSub}
        : {tone: 'bad', text: PH.failed, sub: PH.failedSub};
    }
    return null;
  });
  const paired = $derived(st.paired ? {tone: 'ok', text: PH.paired(st.paired)}
    : ph.revoked ? {tone: 'wait', text: PH.revoked} : {tone: 'wait', text: PH.pairedNone});
  const errItems = $derived(st.error && !st.on ? [{k: PH.dError, v: st.error.text}] : []);
</script>

<div class="wc wc-calm" data-testid="phone-access">
  {#if TOKEN}
    <StatusLine tone="ok" text={PH.onPhone[0] + location.host + PH.onPhone[1]} sub={PH.onPhoneSub} testid="phone-paired-here" />
  {:else}
    <div class="wc-row">
      <span class="wc-row-label" id="ph-switch">{PH.row}</span>
      <div class="wc-switch-ctl" data-testid="phone-switch" data-on={on ? 'true' : 'false'}>
        <Segmented name="phone" labelledby="ph-switch" value={on ? 'on' : 'off'} onchange={x => setOn(x === 'on')}
          options={[{value: 'off', label: WC.off}, {value: 'on', label: WC.on}]} disabled={!!ph.req} />
      </div>
    </div>
    <!-- At rest, how pairing goes; once on, the code says it under itself. -->
    {#if !(st.on && st.qr)}<p class="small muted wc-row-sub" data-testid="phone-row-sub">{PH.rowSub}</p>{/if}

    {#if status}<div class="wc-status"><StatusLine {...status} testid="phone-status" /></div>{/if}

    {#if st.on && st.qr}
      <div class="wc-qr">
        <Qr rows={st.qr} label={PH.qrLabel} />
        <div>
          <p class="wc-addr" data-testid="phone-address">{st.address}</p>
          <p class="small muted" data-testid="phone-scan">{PH.scan}</p>
        </div>
      </div>
      <div class="wc-status"><StatusLine {...paired} testid="phone-paired" /></div>
      <div class="wc-actions">
        <button class="btn quiet" type="button" onclick={revoke} disabled={!!ph.req} data-testid="phone-revoke">{PH.revoke}</button>
        <span class="small muted">{st.paired ? PH.revokeSub : PH.revokeSubNone}</span>
      </div>
    {/if}

    {#if (st.on && st.url) || errItems.length}
      <div class="wc-foot">
        <Details label={PH.details} items={errItems} testid="phone-details">
          {#if st.on && st.url}
            <p class="small wc-dlink"><span class="wc-dk">{PH.dLink}</span><span><span class="num">{st.url}</span><CopyButton
              text={st.url} label={PH.copyLink} done={PH.copied} /> <span class="muted">{PH.dLinkSub}</span></span></p>
          {/if}
        </Details>
      </div>
    {/if}
  {/if}
</div>
