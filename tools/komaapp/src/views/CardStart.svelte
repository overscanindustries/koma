<script>
  // Work from a memory card: the camera's settings file read from its card in this computer, edited
  // in the Camera menu, saved back as PARAM.DAT for the camera to load at its next start. Only a camera running Koma
  // writes the settings file; a refused file says so and points at setup, which needs the camera on USB.
  import {S, setBlock} from '../lib/settings.svelte.js';
  import {go} from '../lib/ui.svelte.js';
  import {inspectBlock, NOT_GR} from '../lib/words.js';
  import FullScreen from '../components/ui/FullScreen.svelte';
  import FileDrop from '../components/FileDrop.svelte';
  import BackLink from '../components/BackLink.svelte';

  const NO_FILE = 'A camera on the official firmware has no settings file.';
  let refused = $state(false);
  function take(b, name) {
    const info = inspectBlock(b, name, S.mapKV);
    refused = !info.ok && info.msg === NOT_GR;
    if (!info.ok) return refused ? info.msg + ' ' + NO_FILE : info.msg;
    setBlock(b, name, 'card');
    go('settings/camera');
    return '';
  }
</script>

<FullScreen labelledby="cs-title">
  <BackLink label="Connect a camera instead" onclick={() => go('home')} />
  <p class="micro">Memory card</p>
  <h1 id="cs-title" class="title-d">Work from a memory card</h1>
  <p class="lede">Choose PARAM.CUR at the top level of the card. Cameras running Koma write it.</p>
  <div class="drop">
    <FileDrop label="Choose the settings file" onfile={take} testid="card-file" primary
      hint={'Koma saves the changes as PARAM.DAT. The camera loads them at its next start.'} />
  </div>
  {#if refused}
    <p class="why" data-testid="card-refused"><a class="link" href="#/setup" data-testid="card-setup">Set up Koma with the camera on USB</a></p>
  {/if}
</FullScreen>

<style>
  .title-d {
    margin: var(--s3) 0 var(--s5);
    font-size: var(--fs-display); line-height: var(--lh-display); font-weight: 500; letter-spacing: -.02em;
  }
  .drop { margin-top: var(--s7); }
  .why { margin-top: var(--s4); }
</style>
