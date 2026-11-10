<script>
  // Welcome: what Koma is, what setup will ask of the user, and Begin. The figure is the camera
  // itself, quiet (no control marked: nothing happens on the camera yet), so the first screen has the spread's shape
  // every later step keeps. The facts follow what the camera runs, when setup already knows (st.cam, from the first-run read):
  // the official firmware or no camera yet (the newcomer: what to have at hand, the two card trips and the write), the
  // Koma firmware without Koma's file (one card trip), or Koma (setup backs up and picks a look).
  import {st, plan, next, komaRuns} from '../../lib/onboard.svelte.js';
  import {W} from '../../lib/onboardWords.js';
  import Spread from '../../components/onboard/Spread.svelte';
  import SpecSheet from '../../components/ui/SpecSheet.svelte';
  import GrCamera from '../../components/onboard/GrCamera.svelte';

  const kind = st.cam ? st.cam.kind : null;
  const koma = komaRuns(kind) || st.plan === 'koma';
  const lines = koma ? W.welcome.linesKoma : kind === 'koma-fw' ? W.welcome.linesKomaFw : W.welcome.lines;
  const needs = koma ? [] : W.welcome.needs.map((v, i) => ({label: i ? '' : W.welcome.need, value: v, testid: 'welcome-need-' + i}));
</script>

{#snippet camera()}
  <GrCamera view="top" testid="setup-figure" />
{/snippet}

<Spread step="welcome" index={st.step + 1 + ' / ' + plan().length} title={W.steps.welcome} lead={W.welcome.lead} {lines}
  figure={camera}>
  {#snippet body()}
    {#if needs.length}<SpecSheet rows={needs} label={W.welcome.need} testid="welcome-needs" />{/if}
  {/snippet}
  {#snippet actions()}
    <span class="onb-ph" aria-hidden="true"></span>
    <button class="btn primary" type="button" onclick={next} data-testid="setup-next">{W.welcome.begin}</button>
  {/snippet}
</Spread>
