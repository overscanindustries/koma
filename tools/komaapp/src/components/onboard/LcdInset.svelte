<script>
  // "On the camera now": what the camera's own screen shows at this step. Only states that are traced
  // are drawn: 'dark' (the camera is off: an unlit 4:3 panel). The firmware's bitmap-font sprite (tools/grdui/fontdump.py)
  // has not been rendered yet, so no camera strings are drawn; a step whose screen wording is not recorded has no inset.
  // The camera's state is also written under the panel, so an unlit panel never reads as an empty box.
  // Not shown in this pass: where the camera is off nothing on its screen matters, and the screens that matter (the
  // update question, the write) have no recorded pixels or strings yet. Kept for the day they land (QA-ONBOARD.md).
  import {W} from '../../lib/onboardWords.js';
  let {state = 'dark', label = ''} = $props();
</script>

<figure class="inset" data-state={state}>
  <figcaption class="micro">{W.onCamera}</figcaption>
  <div class="lcd" role="img" aria-label={label}></div>
  {#if state === 'dark'}<p class="cap" aria-hidden="true">{W.screenOff}</p>{/if}
</figure>

<style>
  .inset { margin: 0; display: grid; gap: 8px; justify-items: start; }
  .lcd { width: 160px; aspect-ratio: 4 / 3; background: var(--sunk); border: 1px solid var(--line); border-radius: var(--r-img, 3px); }
  .cap { font-size: var(--fs-small, 12.5px); line-height: 16px; color: var(--sub); }
  @media (max-width: 720px) { .inset { justify-items: stretch; } .lcd { width: 100%; } }
</style>
