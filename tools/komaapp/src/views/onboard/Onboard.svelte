<script>
  // Setup and Install / Update Koma, full screen: no nav, no sheet, no scrim. The frame holds the
  // wordmark, the theme toggle and the way out; the step fills the rest (lib/onboard.svelte.js has the flow). From the
  // press that starts the camera's update until the camera has written it, there is no way out: Exit is not offered
  // (Stop here shows the reason in the step). On Done the step's own buttons are the way out, except in the short
  // plan (a camera already on Koma), where Exit stays on every screen. A withheld Exit keeps its
  // place (hidden, out of the tab order and the accessibility tree), so the theme toggle never moves.
  import '../../styles/onboard.css';
  import {st, stepId, exit, committed} from '../../lib/onboard.svelte.js';
  import {cam} from '../../lib/camera.svelte.js';
  import {W} from '../../lib/onboardWords.js';
  import Wordmark from '../../components/Wordmark.svelte';
  import ThemeToggle from '../../components/ThemeToggle.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';
  import StepWelcome from './StepWelcome.svelte';
  import StepCamera from './StepCamera.svelte';
  import StepCard from './StepCard.svelte';
  import StepInstall from './StepInstall.svelte';
  import StepConnect from './StepConnect.svelte';
  import StepLook from './StepLook.svelte';
  import StepDone from './StepDone.svelte';

  const id = $derived(stepId());
  const offline = $derived(cam.state === 'off');
  const locked = $derived(committed());
  const noExit = $derived(locked || (id === 'done' && st.plan !== 'koma'));
</script>

<div class="onb" data-testid="setup" data-plan={st.plan} data-step-id={id}>
  <header class="top">
    <Wordmark />
    <div class="r">
      <ThemeToggle compact />
      <button class="link" class:withheld={noExit} type="button" onclick={exit} disabled={noExit} data-testid="setup-exit">{W.exit}</button>
    </div>
  </header>
  {#if offline}
    <div class="off"><StatusLine tone="bad" text={W.offline} testid="setup-offline" /></div>
  {/if}
  <main class="main">
    {#key id}
      {#if id === 'welcome'}<StepWelcome />
      {:else if id === 'camera'}<StepCamera />
      {:else if id === 'card'}<StepCard />
      {:else if id === 'install'}<StepInstall />
      {:else if id === 'connect'}<StepConnect />
      {:else if id === 'look'}<StepLook />
      {:else if id === 'done'}<StepDone />
      {/if}
    {/key}
  </main>
</div>

<style>
  .onb { min-height: 100vh; min-height: 100dvh; background: var(--bg); color: var(--ink); display: flex; flex-direction: column; }
  .top { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 20px 28px; min-height: 80px; }
  .r { display: flex; align-items: center; gap: 24px; }
  .r .link { font-size: var(--fs-body, 14px); min-height: 40px; }
  .r .link.withheld { visibility: hidden; }
  .off { max-width: 1120px; width: 100%; margin: 0 auto; padding: 8px 28px 0; }
  .main { flex: 1; display: flex; flex-direction: column; }
  @media (max-width: 720px) {
    .top { padding: 12px 16px; border-bottom: 1px solid var(--line); min-height: 69px; }
    .r { gap: 16px; }
    .r .link { min-height: 44px; }
    .off { padding: 12px 16px 0; }
  }
</style>
