<script>
  import {route, NAV, go, screen} from '../lib/ui.svelte.js';
  import {cam, connected} from '../lib/camera.svelte.js';
  import {S, readFromCamera, clearBlock} from '../lib/settings.svelte.js';
  import * as SET from '../lib/settings.svelte.js';
  import {A} from '../lib/apply.svelte.js';
  import Icon from '../components/Icon.svelte';
  import Wordmark from '../components/Wordmark.svelte';
  import ThemeToggle from '../components/ThemeToggle.svelte';
  import NavStatus from '../components/ui/NavStatus.svelte';
  import StatusLine from '../components/ui/StatusLine.svelte';
  import Disconnected from '../components/ui/Disconnected.svelte';
  import ApplySheet from '../components/ApplySheet.svelte';
  import Shoot from './Shoot.svelte';
  import Overview from './Overview.svelte';
  import Settings from './Settings.svelte';
  import Photos from './photos/Photos.svelte';
  import Looks from './looks/Looks.svelte';
  import Webcam from './webcam/Webcam.svelte';
  import ShootPaused from './webcam/ShootPaused.svelte';
  import {webcamHolds} from '../lib/webcam.svelte.js';

  // The shell: no header band. Identity and connection live once, in NavStatus under the wordmark
  // (desktop) or in the one top line (phone). The view fades in over 180 ms on a route change; focus goes to its h1.
  let mainEl = $state(), viewEl = $state();
  let last = '';
  const still = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  $effect(() => {
    const key = route.top + '/' + route.sub;
    if (key !== last) {
      const first = !last;
      last = key;
      if (!first && mainEl) {
        mainEl.scrollTo(0, 0);
        const h = mainEl.querySelector('h1'); if (h) h.focus({preventScroll: true});
        if (viewEl && viewEl.animate && !(still && still.matches))
          viewEl.animate([{opacity: 0}, {opacity: 1}], {duration: 180, easing: 'cubic-bezier(.2, .7, .2, 1)'});
      }
    }
  });

  function useCamera() { clearBlock(); readFromCamera(); }
  const cardWithCamera = $derived(S.source === 'card' && connected());
  // The camera went away after it was connected in this tab: the screen stays, one line says so (App.svelte). Shoot
  // and a webcam call that lost its camera say it in their own line, so not twice.
  const lost = $derived(cam.state === 'nocam' && !A.open && S.source !== 'card' && route.top !== 'shoot'
    && !(route.top === 'webcam' && webcamHolds()));
  // Edits waiting to be applied, as a numeral beside Settings (the Camera menu tabs' grammar: SHOOTING 1). No banner.
  const waiting = $derived.by(() => {
    void S.rev;
    if (typeof SET.pendingCount === 'function') return SET.pendingCount();
    return SET.pending().length + (SET.unmappedChanged() > 0 ? 1 : 0);
  });
</script>

<div class="shell" class:phone={screen.phone}>
  <a class="skip" href="#main" onclick={e => { e.preventDefault(); mainEl && mainEl.focus(); }}>Skip to content</a>
  <nav class="nav" aria-label="Main">
    {#if !screen.phone}
      <div class="brand">
        <a class="home" href="#/home" aria-current={route.top === 'home' ? 'page' : undefined} data-testid="home-link">
          <Wordmark /><span class="sr-only"> Overview</span></a>
        <NavStatus />
      </div>
    {/if}
    <ul>
      {#each NAV as n}
        <li>
          <a href={'#/' + n.id} aria-current={route.top === n.id ? 'page' : undefined} data-nav={n.id}>
            {#if screen.phone}<Icon name={n.id} size={20} />{/if}<span>{n.label}{#if n.id === 'settings' && waiting > 0}<span class="count num"><span class="sr-only">, </span>{waiting}<span class="sr-only"> waiting</span></span>{/if}</span>
          </a>
        </li>
      {/each}
    </ul>
    {#if !screen.phone}
      <div class="nav-foot">
        <ThemeToggle />
        <p>Project Koma. Community firmware for the GR DIGITAL.</p>
      </div>
    {/if}
  </nav>

  <main class="main" id="main" tabindex="-1" bind:this={mainEl}>
    {#if screen.phone}
      <div class="topline">
        <a class="home" href="#/home" aria-current={route.top === 'home' ? 'page' : undefined} data-testid="home-link">
          <Wordmark /><span class="sr-only"> Overview</span></a>
        <NavStatus compact />
      </div>
    {/if}
    {#if cardWithCamera}
      <div class="cardcam">
        <StatusLine tone="ok" text="A camera is connected. These settings come from a memory card file."
          action={{label: 'Use the camera', onclick: useCamera}} />
      </div>
    {/if}
    {#if lost}
      <div class="cardcam" data-testid="shell-disconnected"><Disconnected /></div>
    {/if}
    <div class="frame" bind:this={viewEl}>
    <div class="view">
      {#if route.top === 'settings'}
        <Settings />
      {:else if route.top === 'photos'}
        <Photos />
      {:else if route.top === 'looks'}
        <Looks />
      {:else if route.top === 'webcam'}
        <Webcam />
      {:else if route.top === 'shoot'}
        {#if webcamHolds()}<ShootPaused />{:else}<Shoot />{/if}
      {:else}
        <Overview />
      {/if}
    </div>
    </div>
  </main>
</div>

<ApplySheet />

<style>
  .shell {
    display: grid;
    grid-template-columns: var(--nav-w) minmax(0, 1fr);
    height: 100vh; height: 100dvh;
  }
  .skip { position: absolute; left: -999px; top: 8px; z-index: 50; background: var(--ink); color: var(--bg); padding: 8px 12px; border-radius: var(--r-ctl); }
  .skip:focus { left: 8px; }

  /* the nav column: text only, on --bg, a 1 px rule on its right edge */
  .nav {
    display: flex; flex-direction: column; min-height: 0;
    border-right: 1px solid var(--line);
    background: var(--bg);
  }
  .brand {
    display: grid; gap: var(--s4); justify-items: start;
    padding: 40px var(--s5) var(--s5);
    border-bottom: 1px solid var(--line);
  }
  .home { color: var(--ink); text-decoration: none; display: inline-block; padding: 4px 0; margin: -4px 0; }
  .home:hover { color: var(--sub); }
  .nav ul { list-style: none; margin: 0; padding: var(--s3) 0 0; display: grid; }
  .nav a[data-nav] {
    position: relative;
    display: flex; align-items: center; gap: var(--s3);
    height: 40px; padding: 0 var(--s5);
    color: var(--sub); text-decoration: none;
    font-size: var(--fs-body); line-height: var(--lh-body); font-weight: 500;
    transition: color var(--t-fast) var(--ease);
  }
  .nav a[data-nav]:hover { color: var(--ink); }
  .nav a[data-nav][aria-current] { color: var(--ink); }
  .nav a[data-nav][aria-current]::before {
    content: ''; position: absolute; left: 0; top: 10px; bottom: 10px; width: 2px; background: var(--acc);
  }
  .nav a[data-nav]:focus-visible { outline-offset: -2px; }
  .nav-foot { margin-top: auto; padding: 0 var(--s5) var(--s5); display: grid; gap: var(--s4); justify-items: start; }
  .count { margin-left: var(--s2); color: var(--sub); font-size: var(--fs-small); font-weight: 400; font-variant-numeric: tabular-nums; }
  .phone .count { margin-left: 3px; font-size: 11px; }
  .nav-foot p { font-size: var(--fs-small); line-height: var(--lh-small); color: var(--faint); text-wrap: pretty; }

  .main { overflow: auto; min-width: 0; position: relative; outline: none; display: flex; flex-direction: column; }
  .frame { flex: 1; display: flex; flex-direction: column; }
  .view { flex: 1; width: 100%; max-width: calc(var(--content-max) + 2 * var(--gutter)); padding: 40px var(--gutter) var(--s7); min-width: 0; }
  .cardcam { flex: none; padding: var(--s5) var(--gutter) 0; max-width: calc(var(--content-max) + 2 * var(--gutter)); }

  /* phone: one top line, content, the tab bar at the bottom (56 px plus the safe area) */
  .shell.phone { grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(0, 1fr) auto; }
  .phone .nav {
    order: 2; flex-direction: row; border-right: 0; border-top: 1px solid var(--line);
    padding: 0 0 env(safe-area-inset-bottom);
  }
  .phone .nav ul { display: flex; width: 100%; padding: 0; }
  .phone .nav li { flex: 1; min-width: 0; }
  .phone .nav a[data-nav] {
    flex-direction: column; justify-content: center; gap: 4px;
    height: var(--tabbar-h); padding: 0 2px;
    font-size: 11px; line-height: 12px; font-weight: 500;
  }
  .phone .nav a[data-nav][aria-current]::before { left: 28%; right: 28%; top: -1px; bottom: auto; width: auto; height: 2px; }
  .phone .main { order: 1; }
  .topline {
    display: flex; align-items: center; justify-content: space-between; gap: var(--s4);
    flex: none;  /* a long page must not squeeze the line (it shrank to its text height) */
    height: 52px; padding: 0 var(--gutter); border-bottom: 1px solid var(--line);
  }
  .phone .view { padding: var(--s5) var(--gutter) var(--s6); }
</style>
