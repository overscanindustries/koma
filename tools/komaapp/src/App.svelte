<script>
  import {cam, startPolling} from './lib/camera.svelte.js';
  import {S, loadMap} from './lib/settings.svelte.js';
  import {A} from './lib/apply.svelte.js';
  import {route, currentTheme, go} from './lib/ui.svelte.js';
  import Connect from './views/Connect.svelte';
  import FullScreen from './components/ui/FullScreen.svelte';
  import CardStart from './views/CardStart.svelte';
  import Shell from './views/Shell.svelte';
  import {onb, firstRunCheck} from './lib/onboard.svelte.js';
  import Onboard from './views/onboard/Onboard.svelte';
  import PhoneGate from './views/phone/PhoneGate.svelte';
  import {pair, pairFromUrl} from './lib/phone.svelte.js';
  import {webcamHolds} from './lib/webcam.svelte.js';

  pairFromUrl();
  startPolling();
  loadMap();

  $effect(() => {
    const t = currentTheme();
    document.documentElement.setAttribute('data-theme', t);
    const m = document.querySelector('meta[name="theme-color"]');
    if (m) m.setAttribute('content', t === 'light' ? '#f4f4f1' : '#161718');
  });

  // First run: lib/onboard.svelte.js decides whether setup opens; it needs the bridge answering (camera or not).
  $effect(() => { if (cam.state === 'cam' || cam.state === 'nocam') firstRunCheck(); });

  // The camera turned up while the card screen was open with no file chosen: go to the app.
  $effect(() => { if (cam.state === 'cam' && route.top === 'card' && S.source !== 'card') go('home'); });

  // Which screen (phone gate and onboarding first, in the markup below):
  //   app      a camera is connected; or one was connected earlier in this tab and went away (the Shell stays and says
  // so in one Disconnected line (stay on the screen); or an Apply waits
  // for the restarted camera; or the user works from a memory card file; or Webcam is open while the webcam
  //            holds a camera that went away mid-call (it says so there)
  //   card     the card-file start, chosen from Connect
  //   blank    before the bridge's first answer: the frame without content (the first /status can take 2 s on
  //            hardware while it opens the camera)
  //   connect  the full Connect page: no camera yet in this tab, or Koma itself not answering (Connect shows Offline)
  // The phone's screen only when the network listener refused for want of a token: a 403 from this computer's own
  // listener (an unexpected Host header) is not a phone.
  const screen = $derived(
    pair.state || cam.tokenRefused ? 'phone'
      : cam.state === 'cam' || A.open || S.source === 'card' || (cam.state === 'nocam' && cam.sawCamera)
        || (webcamHolds() && route.top === 'webcam') ? 'app'
      : route.top === 'card' ? 'card'
      : cam.state === 'unknown' ? 'blank' : 'connect');
</script>

{#if screen === 'phone'}
  <PhoneGate />
{:else if onb.view === 'setup'}
  <Onboard />
{:else if onb.view === 'gallery'}
  {#await import('./views/onboard/GrGallery.svelte') then G}<G.default />{/await}
{:else if screen === 'app'}
  <Shell />
{:else if screen === 'card'}
  <CardStart />
{:else if screen === 'connect'}
  <Connect />
{:else if screen === 'blank'}
  <FullScreen />
{/if}
