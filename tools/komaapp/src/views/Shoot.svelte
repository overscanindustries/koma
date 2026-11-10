<script>
  // Shoot: live view at 4:3, at most 640 x 480 CSS, its matte the picture's own size and left on the
  // column's text edge; one instrument strip of what the
  // camera reports; a 72 px shutter alone on its row; a hairline rail of Exposure, Interval and Remote.
  // Behaviour and every bridge call are lib/shoot.svelte.js (ported from koma-setup.html, unchanged).
  import {onMount} from 'svelte';
  import * as SH from '../lib/shoot.svelte.js';
  import {sh, XP, xVal, xLabel, xFieldLabel, xPick, ivText, NOT_AWAKE} from '../lib/shoot.svelte.js';
  import {cam, connected} from '../lib/camera.svelte.js';
  import {S} from '../lib/settings.svelte.js';
  import {go, screen} from '../lib/ui.svelte.js';
  import {STATE} from '../lib/words.js';
  import {trueMinus, elapsed, time} from '../lib/format.js';
  import {komaFacts} from '../lib/overview.svelte.js';
  import {setupEntry} from '../lib/onboard.svelte.js';
  import {fileNumber} from '../lib/photos.svelte.js';
  import {slugOf, lookBySlug} from '../lib/looksCatalog.js';
  import Icon from '../components/Icon.svelte';
  import Sheet from '../components/Sheet.svelte';
  import Shutter from '../components/ui/Shutter.svelte';
  import StatusLine from '../components/ui/StatusLine.svelte';
  import Segmented from '../components/ui/Segmented.svelte';
  import EmptyState from '../components/ui/EmptyState.svelte';
  import Details from '../components/ui/Details.svelte';

  let liveEl = $state(), osdEl = $state();
  let every = $state(10), count = $state(10);
  let panel = $state('');            // '' | 'interval' | 'remote' | 'adjust'
  let shotOpen = $state(null);
  let dip = $state(false);
  let osdWanted = $state(true);

  onMount(() => { SH.enter(); return () => SH.leave(); });
  $effect(() => { SH.attachLive(liveEl); });
  $effect(() => { SH.attachOsd(osdEl); });
  // the camera came, went, or is a new session
  $effect(() => { void cam.conn; void cam.state; SH.sync(); });
  // the Remote panel shows the camera's own screen over the picture
  $effect(() => { SH.osdToggle(panel === 'remote' && osdWanted); });

  const on = $derived(connected());
  const lost = $derived(!on && cam.state === 'nocam' && S.source !== 'card');
  // A stock camera (official firmware): its shutter cannot fire from here; live view and Remote work as they are.
  const stock = $derived(on && komaFacts().state === 'none');
  const setup = $derived(setupEntry());
  // after a wake that failed, the shutter waits for Try again
  const unarmed = $derived(sh.wakeFailed && !sh.armed);
  const canShoot = $derived(on && !sh.busy && !sh.ivRunning && !sh.starting && !stock && !unarmed);
  const canFocus = $derived(on && !sh.busy && !sh.ivRunning && !sh.starting && !unarmed);
  const ready = $derived(!!sh.x.cam && !sh.x.reading);
  const vals = $derived(Object.fromEntries(XP.map(c => [c.off, xVal(c.off)])));
  const ev = XP[0], iso = XP[1], wb = XP[2];
  const evText = $derived(ready ? trueMinus(xLabel(ev, vals[ev.off])) : '');
  const isoText = $derived(ready ? xLabel(iso, vals[iso.off]) : '');
  const wbText = $derived(ready ? xLabel(wb, vals[wb.off]) : '');
  const iv = $derived(sh.iv);
  // the look in the strip links to its editor when it is one of the 21 (a stock IMG SET value has none)
  const lookSlug = $derived(sh.look && lookBySlug(slugOf(sh.look.name)) ? slugOf(sh.look.name) : '');
  const live = $derived(sh.lvOn && sh.lvFps > 0 && !sh.lvPaused);
  const showInterval = $derived(panel === 'interval' || sh.ivRunning);

  // Shutter confirm: the preview dims to 0.6 and returns, 120 ms each way.
  function shutter() {
    if (!canShoot) return;
    dip = true; setTimeout(() => (dip = false), 120);
    SH.press(true);
  }
  function togglePanel(p) { panel = panel === p ? '' : p; }
  function key(e) {
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
    if (document.querySelector('dialog[open]')) return;
    const t = e.target, typing = /INPUT|SELECT|TEXTAREA/.test(t.tagName);
    if (e.key === ' ' && !typing && t.tagName !== 'BUTTON' && t.tagName !== 'SUMMARY' && t.tagName !== 'A') { e.preventDefault(); shutter(); return; }
    if (panel !== 'remote' || !sh.active || (typing && e.key !== 'Escape')) return;
    const code = SH.RKEYS[e.key];
    if (code === undefined) return;
    e.preventDefault();
    SH.tap(code);
  }
  function evIndex(v) { return ev.vals.indexOf(v); }
  // Waking for remote control: a named phase, an elapsed counter past 10 s (no fill: nothing is measured).
  const waking = $derived(sh.wakeS >= 0);
  const wakeText = $derived(!waking ? '' : sh.wakeS < 2 ? 'Starting remote control.' : 'Waking the camera for remote control.');
  const wakeSince = $derived(!waking ? null : Date.now() - sh.wakeS * 1000);
  // while the camera wakes, live view waits for it: one line, no Resume button
  const liveLine = $derived(lost ? '' : waking && !sh.lvSeen ? 'Live view starts after remote control.' : sh.lvOver);
  // One line under the shutter, in this order: the lost camera (its own status line says it, so nothing here), an
  // interval running, what the shutter is doing, else the hint (not on a stock camera, nor while the camera is unarmed).
  const triggerLine = $derived(lost ? '' : sh.ivRunning ? 'Interval running.' : sh.status ? sh.status
    : !screen.phone && !stock && !unarmed && on ? 'Space takes a photo.' : '');
  const wakeAction = $derived(sh.error === NOT_AWAKE ? {label: 'Try again', onclick: SH.sync, testid: 'wake-again'} : null);
  // the photo sheet: "Photo 3 · 14:32", the session's number and the time it arrived here
  const shotN = $derived(shotOpen ? sh.shots.length - sh.shots.indexOf(shotOpen) : 0);
  const shotTitle = $derived(shotOpen ? 'Photo ' + shotN + (shotOpen.at ? ' · ' + time(new Date(shotOpen.at)) : '') : 'Photo');

  // Remote on a phone: the picture stays pinned at the top while the buttons below drive it, under whatever the shell
  // pins at the top of the scrolling page (measured, as the look editor does).
  let shootEl = $state();
  let pinTop = $state(0);
  $effect(() => {
    const main = shootEl && shootEl.closest('main');
    if (!main) return;
    const pinned = [...main.children].filter(el => !el.contains(shootEl) && getComputedStyle(el).position === 'sticky');
    const measure = () => { pinTop = Math.round(pinned.reduce((h, el) => h + el.getBoundingClientRect().height, 0)); };
    measure();
    const ro = new ResizeObserver(measure);
    pinned.forEach(el => ro.observe(el));
    return () => ro.disconnect();
  });
  // A photo that takes long to land (the camera writes it first) carries its elapsed time past 10 s.
  let busySince = $state(0), tick = $state(0);
  $effect(() => {
    if (!sh.busy) { busySince = 0; return; }
    busySince = Date.now();
    const t = setInterval(() => { tick = Date.now(); }, 500);
    return () => clearInterval(t);
  });
  const busyS = $derived(busySince && tick ? Math.floor((tick - busySince) / 1000) : 0);
</script>

<svelte:window onkeydown={key} />

<h1 class="sr-only" tabindex="-1">Shoot</h1>

{#if !on && !lost}
  <div class="off">
    <EmptyState label="No camera" text="Shooting needs the camera on USB. Turn it on, then plug in the cable."
      action={S.source === 'card' ? {label: 'Back to the settings file', onclick: () => go('settings/camera'), kind: 'link'} : null} />
  </div>
{:else}
<div class="shoot" class:phone={screen.phone} class:remote={panel === 'remote'} bind:this={shootEl} style:--pin={pinTop + 'px'}>
  <div class="stage">
    <div class="wellpin">
    <div class="well" data-testid="live">
      <!-- the last frame before the camera left stays at 0.6 until the first new frame arrives -->
      <div class="frame" class:dim={dip || sh.lvPaused || (lost && !sh.held)}>
        {#if sh.held}<img class="held" src={sh.held} alt="The last frame before the camera left">{/if}
        <img bind:this={liveEl} class="live" alt="Live view from the camera" crossorigin="anonymous">
        <img bind:this={osdEl} class="osd" class:shown={panel === 'remote' && osdWanted && sh.osdOn} alt="The camera's screen">
        {#if live}<span class="lv micro" aria-hidden="true"><i></i>Live</span>{/if}
      </div>
    </div>
    </div>

    <!-- Away before the strip was ever read: no strip (the rail's line says the camera is gone). Read once, the
         values stay, faint. -->
    {#if ready || !lost}
      <div class="strip" class:stale={lost} role="group" aria-label="The camera's settings now">
        {#if ready}
          <span class="sv"><span class="num vv">{evText}</span><span class="u">EV</span></span>
          <span class="sv"><span class="p">ISO</span><span class="vv num">{isoText}</span></span>
          <span class="sv"><span class="p">WB</span><span class="vv">{wbText}</span></span>
          {#if sh.look}<span class="sv">{#if lookSlug}<a class="vv link" href={'#/looks/' + lookSlug} data-testid="strip-look">{sh.look.name}</a>{:else}<span class="vv">{sh.look.name}</span>{/if}</span>{/if}
        {:else if sh.x.err && !sh.x.cam}
          <span class="small muted">{sh.x.err}</span>
        {:else}
          <span class="small muted">Reading the camera's settings.</span>
        {/if}
      </div>
    {/if}

    <div class="vbar">
      {#if on && !(waking && !sh.lvOn)}<button class="btn quiet sm" type="button" onclick={SH.liveToggle} data-testid="live-toggle">
        {sh.lvOn ? 'Pause live view' : 'Resume live view'}</button>{:else}<span></span>{/if}
      <span class="lvline small muted" aria-live="polite">{liveLine}</span>
      <Details items={[{k: 'Live view', v: sh.lvRate || (sh.lvOn ? 'Starting' : 'Off')}]}>
        <div class="lvmode">
          <span class="small muted" id="lvmode-l">Live view picture</span>
          <Segmented name="lvmode" labelledby="lvmode-l" small value={sh.lvMode}
            options={[{value: 1, label: 'Picture'}, {value: 0, label: 'Grey'}]} onchange={v => SH.liveMode(v)} />
        </div>
      </Details>
    </div>

    {#if sh.shots.length}
      <ul class="thumbs" aria-label="Photos this session">
        {#each sh.shots as p (p.path)}
          <!-- an interval photo stays on the card (only a press from here brings its JPEG back): a quiet numbered frame -->
          <li><button type="button" class="th" class:cur={sh.cur === p} onclick={() => (shotOpen = p)}
            aria-label={'Open photo ' + (sh.shots.length - sh.shots.indexOf(p)) + ' of this session' + (p.src ? '' : ', ' + fileNumber(p.name))}>
            {#if p.src}<img src={p.src} alt="" decoding="async">
            {:else}<span class="fno num" aria-hidden="true">{fileNumber(p.name)}</span>{/if}
          </button></li>
        {/each}
      </ul>
    {/if}
  </div>

  <aside class="rail" aria-label="Camera controls">
    <div class="trigger">
      <div class="keys1">
        <Shutter onpress={shutter} disabled={!canShoot} ring={sh.ivRunning} beat={iv ? iv.done : 0} size={screen.phone ? 56 : 72} />
        <button class="btn quiet sm" type="button" onclick={() => SH.press(false)} disabled={!canFocus}>Focus</button>
      </div>
      {#if screen.phone && sh.ivRunning && iv}
        <p class="status small ivbar"><span class="num">{'Photo ' + iv.done + ' of ' + iv.count}</span>
          <button class="btn sm" type="button" onclick={SH.ivStop}>Stop</button></p>
      {:else}
        <p class="status small" role="status" aria-live="polite" data-testid="shoot-status">{screen.phone ? sh.status : triggerLine}{#if sh.busy && busyS >= 10}<span class="num el">{elapsed(busyS)}</span>{/if}</p>
      {/if}
    </div>

    {#if lost}
      <div class="line"><StatusLine tone="wait" text={STATE.lost} since={sh.lostAt || null} testid="shoot-lost" /></div>
    {:else if stock}
      <div class="line"><StatusLine tone="none" text="The shutter needs Koma on the camera." testid="shoot-stock"
        action={{label: setup.label, href: setup.href, kind: 'link', testid: 'shoot-setup'}} /></div>
    {:else if wakeText}
      <div class="line"><StatusLine tone="wait" text={wakeText} since={wakeSince} /></div>
    {/if}
    {#if sh.error}
      <div class="line">
        <StatusLine tone="bad" text={sh.error} action={wakeAction} />
        {#if sh.replies.length}
          <div class="more"><Details label="What the camera answered"><pre class="mono">{sh.replies.map(x => x.command + '  ->  ' + x.reply).join('\n')}</pre></Details></div>
        {/if}
      </div>
    {/if}

    <div class="rows">
      <section class="pp" class:open={panel === 'adjust'}>
        <button class="ph" type="button" aria-expanded={panel === 'adjust'} aria-controls="pp-adjust" onclick={() => togglePanel('adjust')}>
          <span class="pt">Exposure, ISO, WB</span><Icon name="down" size={16} /></button>
        {#if panel === 'adjust'}
          <div class="pb" id="pp-adjust">
            {#each XP as c}
              {#if !(c.when && !c.when(vals))}
                <div class="xr">
                  <span class="cam xl" id={'xl-' + c.k}>{xFieldLabel(c)}</span>
                  {#if c.kind === 'ev'}
                    <div class="xev">
                      <input type="range" min="0" max={c.vals.length - 1} step="1" value={Math.max(0, evIndex(vals[c.off]))} disabled={!ready}
                        aria-labelledby={'xl-' + c.k} aria-valuetext={trueMinus(xLabel(c, vals[c.off])) + ' EV'} data-testid="ev"
                        oninput={e => xPick(c.off, c.vals[Number(e.currentTarget.value)])}>
                      <output class="num">{trueMinus(xLabel(c, vals[c.off]))}</output>
                    </div>
                  {:else if c.vals.length <= 4}
                    <Segmented name={'x-' + c.k} labelledby={'xl-' + c.k} value={vals[c.off]} disabled={!ready} small
                      options={c.vals.map(v => ({value: v, label: xLabel(c, v)}))} onchange={v => xPick(c.off, v)} />
                  {:else}
                    <select class="select" aria-labelledby={'xl-' + c.k} disabled={!ready} value={String(vals[c.off])} onchange={e => xPick(c.off, Number(e.currentTarget.value))}>
                      {#each c.vals as v}<option value={String(v)}>{xLabel(c, v)}</option>{/each}
                    </select>
                  {/if}
                </div>
              {/if}
            {/each}
            {#if sh.x.msg}<p class="small muted" role="status">{sh.x.msg}</p>{/if}
            {#if sh.x.err && sh.x.cam}<StatusLine tone="bad" text={sh.x.err} />{/if}
            <p class="small muted">Changes apply at once and stay after a normal power-off.</p>
            <Details><p class="small muted">Exposure compensation was tested on the camera; ISO, white balance and metering were not.</p></Details>
          </div>
        {/if}
      </section>

      <section class="pp" class:open={showInterval}>
        <button class="ph" type="button" aria-expanded={showInterval} aria-controls="pp-interval" onclick={() => togglePanel('interval')}
          data-testid="interval-panel" disabled={sh.ivRunning}>
          <span class="pt">Interval</span><Icon name="down" size={16} /></button>
        {#if showInterval}
          <div class="pb" id="pp-interval">
            <!-- two rows in the rail's own grammar: the name left, the fields on one right edge, the unit after -->
            <div class="ivf">
              <div class="ivr">
                <label for="iv-every">Every</label>
                <input id="iv-every" class="input num" type="number" min="1" max="86400" step="1" bind:value={every} disabled={sh.ivRunning} data-testid="iv-every">
                <span class="u">s</span>
              </div>
              <div class="ivr">
                <label for="iv-count">Photos</label>
                <input id="iv-count" class="input num" type="number" min="1" max="9999" step="1" bind:value={count} disabled={sh.ivRunning} data-testid="iv-count">
                <span class="u"></span>
              </div>
            </div>
            <div class="ivgo" class:pinned={screen.phone && sh.ivRunning}>
              {#if sh.ivRunning}
                <button class="btn" type="button" onclick={SH.ivStop} data-testid="iv-btn">Stop</button>
              {:else}
                <button class="btn" type="button" onclick={() => SH.ivStart(Number(every), Number(count))} disabled={sh.busy || !on} data-testid="iv-btn">Start</button>
              {/if}
              {#if iv && iv.count}<p class="small num ivs" role="status" data-testid="iv-status">{ivText(iv)}</p>{/if}
            </div>
          </div>
        {/if}
      </section>

      <section class="pp" class:open={panel === 'remote'}>
        <button class="ph" type="button" aria-expanded={panel === 'remote'} aria-controls="pp-remote" onclick={() => togglePanel('remote')} data-testid="remote-panel">
          <span class="pt">Remote</span><Icon name="down" size={16} /></button>
        {#if panel === 'remote'}
          <div class="pb" id="pp-remote">
            <p class="small muted">These press the camera's buttons. Its menu includes FORMAT and RESTORE DEFAULTS.</p>
            <div class="osdrow">
              <span class="small" id="osd-l">Show the camera's screen</span>
              <Segmented name="osd" labelledby="osd-l" small value={osdWanted}
                options={[{value: false, label: 'OFF'}, {value: true, label: 'ON'}]} onchange={v => (osdWanted = v)} />
            </div>
            <div class="remote" role="group" aria-label="Camera buttons">
              <div class="dpad">
                <button class="k up" type="button" data-rkey="11" onclick={() => SH.tap(11)} disabled={!sh.active} aria-label="Up"><Icon name="down" size={16} /></button>
                <button class="k left" type="button" data-rkey="14" onclick={() => SH.tap(14)} disabled={!sh.active} aria-label="Left"><Icon name="back" size={16} /></button>
                <button class="k ok" type="button" data-rkey="23" onclick={() => SH.tap(23)} disabled={!sh.active}>MENU<br>OK</button>
                <button class="k right" type="button" data-rkey="13" onclick={() => SH.tap(13)} disabled={!sh.active} aria-label="Right"><Icon name="chevron" size={16} /></button>
                <button class="k down" type="button" data-rkey="12" onclick={() => SH.tap(12)} disabled={!sh.active} aria-label="Down"><Icon name="down" size={16} /></button>
              </div>
              <!-- on the dpad's three rows: W and T, then DISP, then DELETE -->
              <div class="keys">
                <button class="k" type="button" data-rkey="16" onclick={() => SH.tap(16)} disabled={!sh.active}>W</button>
                <button class="k" type="button" data-rkey="15" onclick={() => SH.tap(15)} disabled={!sh.active}>T</button>
                <button class="k wide" type="button" data-rkey="22" onclick={() => SH.tap(22)} disabled={!sh.active}>DISP</button>
                <button class="k wide" type="button" data-rkey="20" onclick={() => SH.tap(20)} disabled={!sh.active}>DELETE</button>
              </div>
            </div>
            <p class="small muted">PLAY and ADJ stay on the camera: PLAY can turn it off, ADJ can change its USB mode.</p>
            <!-- the keyboard keys for these are listed once, under Settings > Advanced -->
            <Details><p class="small muted">The camera accepts these presses. What its menus do with each has not been seen on hardware.</p></Details>
          </div>
        {/if}
      </section>
    </div>
  </aside>
</div>
{/if}

<Sheet open={!!shotOpen} title={shotTitle} wide onclose={() => (shotOpen = null)} testid="shot-sheet">
  {#if shotOpen && shotOpen.src}<img class="big" src={shotOpen.src} alt="Taken from this screen">
  {:else if shotOpen}<p class="muted" data-testid="shot-on-card">Interval photos stay on the card. Open them in <a class="link"
    href="#/photos" onclick={() => (shotOpen = null)}>Photos</a> after Shoot.</p>{/if}
  {#if shotOpen}<div class="more"><Details items={[{k: 'File', v: shotOpen.name}, {k: 'Folder', v: shotOpen.folder}]} /></div>{/if}
  {#snippet actions()}
    <button class="btn quiet" type="button" onclick={() => (shotOpen = null)}>Close</button>
    {#if shotOpen && shotOpen.src}<a class="btn primary" href={shotOpen.src} download={shotOpen.name}>Download</a>{/if}
  {/snippet}
</Sheet>

<style>
  .off { max-width: 60ch; }
  .shoot { display: grid; grid-template-columns: minmax(0, 640px) 288px; column-gap: var(--col-gap); align-items: start; max-width: var(--content-max); }
  .stage { min-width: 0; display: grid; gap: var(--s3); }

  /* the matte is the picture: 4:3, at most 640 x 480, on the column's left text edge */
  .well {
    position: relative; aspect-ratio: 4 / 3; width: min(100%, 640px, calc((100vh - 248px) * 4 / 3));
    background: var(--photo-bg); border: 1px solid var(--line); border-radius: var(--r-img); overflow: hidden;
  }
  .strip, .vbar { max-width: 640px; }
  .frame {
    position: relative; width: 100%; height: 100%;
    transition: opacity var(--t-fast) var(--ease);
  }
  .frame.dim { opacity: .6; }
  .live, .osd, .held { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
  .held { opacity: .6; }
  .live:not([src]) { visibility: hidden; }
  .osd { opacity: 0; transition: opacity var(--t) var(--ease); image-rendering: pixelated; }
  .osd.shown { opacity: 1; }
  .lv {
    position: absolute; top: 12px; left: 12px; display: inline-flex; align-items: center; gap: 6px;
    color: var(--on-photo); line-height: 1;
  }
  .lv i { width: 6px; height: 6px; border-radius: 50%; background: var(--acc); }

  /* the instrument strip: what the camera reports, self-naming values, no labels above them */
  .strip {
    display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--s2) var(--s6);
    min-height: 52px; padding: 13px var(--s5);
    background: var(--surf); border: 1px solid var(--line); border-radius: var(--r-panel);
  }
  .sv { display: inline-flex; align-items: baseline; gap: 6px; white-space: nowrap; }
  /* prefix and value at one size, the prefix in --sub; EV is a unit and stays small */
  .sv .vv, .sv .p { font-size: var(--fs-lead); line-height: var(--lh-lead); }
  .sv .p { color: var(--sub); }
  .sv .u { color: var(--sub); font-size: var(--fs-small); }
  .sv a.vv { color: var(--ink); }
  .strip.stale .sv, .strip.stale .sv .u, .strip.stale .sv .p { color: var(--faint); }
  .vbar { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; column-gap: var(--s4); min-height: 40px; }
  .lvline { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .vbar :global(details) { justify-self: end; }
  .vbar :global(details[open]) { grid-column: 1 / -1; justify-self: start; }
  .lvmode { display: flex; align-items: center; gap: var(--s3); }
  .thumbs { display: flex; gap: var(--s2); overflow-x: auto; padding: var(--s1) 0 var(--s2); list-style: none; margin: 0; }
  .thumbs li { flex: none; }
  .th { flex: none; height: 72px; aspect-ratio: 4 / 3; padding: 0; border: 1px solid var(--line); border-radius: var(--r-img); overflow: hidden; background: var(--photo-bg); }
  .th img { width: 100%; height: 100%; object-fit: contain; }
  .th { display: grid; place-items: center; }
  .fno { color: var(--on-photo); font-size: var(--fs-small); line-height: var(--lh-small); }
  .th.cur { outline: 1px solid var(--ink); outline-offset: 1px; }
  .big { width: 100%; height: auto; border-radius: var(--r-img); background: var(--photo-bg); }
  .more { margin-top: var(--s3); }

  /* the rail: on --bg, a 1 px rule on its left, hairlines between rows, no panels */
  .rail { min-width: 0; border-left: 1px solid var(--line); padding-left: var(--s5); align-self: stretch; }
  /* the shutter alone on its row at the rail's text edge, Focus centred to its right, one line under it */
  .trigger { display: grid; gap: var(--s3); padding-bottom: var(--s6); }
  .keys1 { display: flex; align-items: center; gap: var(--s5); }
  .status { min-height: var(--lh-small); color: var(--sub); }
  .status .el { margin-left: var(--s2); }
  .line { padding-bottom: var(--s4); }
  .rows { border-top: 1px solid var(--line); }
  .pp { border-bottom: 1px solid var(--line); }
  .ph {
    width: 100%; display: flex; align-items: center; gap: var(--s3);
    min-height: var(--row-h); padding: 0; background: none; border: 0; color: var(--ink); text-align: left;
  }
  .ph:disabled { cursor: default; }
  .ph .pt { font-size: var(--fs-body); font-weight: 500; }
  .ph :global(svg) { margin-left: auto; transition: transform var(--t) var(--ease); color: var(--faint); }
  .pp.open .ph :global(svg) { transform: rotate(180deg); }
  .ph:focus-visible { outline-offset: -2px; }
  .pb { padding: var(--s1) 0 var(--s5); display: grid; gap: var(--s4); }
  .xr { display: grid; gap: var(--s2); }
  .xl { color: var(--sub); }
  .xev { display: flex; align-items: center; gap: var(--s3); }
  .xev input { flex: 1; accent-color: var(--ink); min-width: 0; }
  .xev output { min-width: 40px; text-align: right; font-size: var(--fs-body); }
  /* Every [4] s / Photos [6]: hairline rows, the fields on one right edge, the unit in its own narrow column */
  .ivf { display: grid; margin-top: calc(-1 * var(--s1)); }
  /* 4 px keeps a field off the rule at either size; one rule between the rows, none boxing a gap below them */
  .ivr {
    display: grid; grid-template-columns: minmax(0, 1fr) 72px 12px; align-items: center; column-gap: var(--s2);
    min-height: var(--row-h); padding: var(--s1) 0;
  }
  .ivr + .ivr { border-top: 1px solid var(--line); }
  .ivr .input { width: 72px; }
  .ivr .u { color: var(--sub); font-size: var(--fs-small); line-height: var(--lh-small); }
  /* phone, while an interval runs: the pinned bar is the one place for the count and Stop */
  .ivgo.pinned { display: none; }
  .ivgo { display: flex; align-items: center; gap: var(--s3); flex-wrap: wrap; }
  .ivs { color: var(--sub); }
  .osdrow { display: flex; align-items: center; justify-content: space-between; gap: var(--s3); }
  /* The camera's keys in the camera's spelling at --fs-cam; 40 px targets on a desktop, 44 on a phone (--key) */
  .remote { --key: 40px; display: grid; grid-template-columns: auto minmax(0, 1fr); gap: var(--s4); align-items: start; }
  .phone .remote { --key: 44px; }
  .dpad { display: grid; grid-template-columns: repeat(3, var(--key)); grid-template-rows: repeat(3, var(--key)); gap: 4px; }
  .k {
    border: 1px solid var(--line-strong); border-radius: var(--r-ctl); background: var(--raise); color: var(--ink);
    font-size: var(--fs-cam); line-height: var(--lh-cam); font-stretch: 88%; font-weight: 560; letter-spacing: .06em;
    display: grid; place-items: center; padding: 0;
    transition: border-color var(--t-fast) var(--ease), transform var(--t-fast) var(--ease);
  }
  .k:hover:not(:disabled) { border-color: var(--sub); }
  .k:active:not(:disabled) { transform: translateY(1px); }
  .k:disabled { opacity: .45; }
  .dpad .up { grid-area: 1 / 2; } .dpad .up :global(svg) { transform: rotate(180deg); }
  .dpad .left { grid-area: 2 / 1; } .dpad .ok { grid-area: 2 / 2; letter-spacing: 0; } .dpad .right { grid-area: 2 / 3; }
  .dpad .down { grid-area: 3 / 2; }
  .keys { display: grid; grid-template-columns: 1fr 1fr; grid-auto-rows: var(--key); gap: 4px; }
  .keys .wide { grid-column: 1 / -1; }

  @media (max-width: 1080px) { .shoot { grid-template-columns: minmax(0, 640px) 256px; } }

  /* phone: live view full width, the strip under it, the shutter pinned above the tab bar, the rows below */
  .shoot.phone { grid-template-columns: minmax(0, 1fr); row-gap: var(--s5); padding-bottom: calc(73px + var(--s5)); }
  .phone .well { max-height: none; width: 100%; }
  .phone .strip { display: grid; grid-template-columns: 1fr 1fr; padding: 10px var(--s4); gap: var(--s1) var(--s4); }
  .phone .rail { border-left: 0; padding-left: 0; }
  .phone .trigger {
    position: fixed; z-index: 7; left: 0; right: 0; bottom: calc(var(--tabbar-h) + env(safe-area-inset-bottom) + 1px);
    display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 0 var(--s3);
    padding: var(--s2) var(--gutter); background: var(--bg); border-top: 1px solid var(--line);
  }
  .phone .keys1 { display: contents; }
  .phone .keys1 :global(.sh) { grid-column: 2; grid-row: 1; }
  .phone .keys1 .btn { grid-column: 1; grid-row: 1; justify-self: start; }
  .phone .status { grid-column: 3; grid-row: 1; text-align: right; }
  .phone .ivbar { display: flex; align-items: center; justify-content: flex-end; gap: var(--s2); color: var(--ink); }
  /* phone, Remote open: the picture pinned at the top at about 45 % width, the buttons scrolling up under it */
  .phone.remote .stage { display: contents; }
  .phone.remote .wellpin {
    position: sticky; top: var(--pin, 0px); z-index: 3; align-self: start;
    margin: 0 calc(-1 * var(--gutter)); padding: var(--s2) var(--gutter);
    background: var(--bg); border-bottom: 1px solid var(--line);
  }
  .phone.remote .well { width: 45%; margin: 0 auto; }
</style>
