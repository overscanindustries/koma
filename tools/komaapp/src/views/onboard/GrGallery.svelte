<script>
  // The GR line-art model for review (#/gr, not linked from the app): every view, every control, every action, three
  // sizes, both themes (the theme toggle), and the reduced-motion frames.
  import GrCamera from '../../components/onboard/GrCamera.svelte';
  import ThemeToggle from '../../components/ThemeToggle.svelte';
  import Wordmark from '../../components/Wordmark.svelte';
  import {VIEWS, CONTROL_IDS, controlOf} from '../../lib/grModel.js';
  import '../../styles/onboard.css';

  const ACTIONS = ['', 'press', 'turn', 'hold', 'power', 'extend'];
  let sel = $state({back: 'play', front: 'lens', top: 'mode-dial'});
  let action = $state('press');
  let play = $state(0);
  let reduced = $state(false);
  let callouts = $state(true);
  const SIZES = [{w: 360, n: 'Figure, 360 px'}, {w: 200, n: '200 px'}, {w: 120, n: '120 px'}];
</script>

<div class="gal" data-testid="gr-gallery">
  <header class="top">
    <Wordmark />
    <ThemeToggle />
  </header>
  <main class="wrap">
    <h1 class="h1" tabindex="-1">GR DIGITAL line art</h1>
    <p class="small intro">107 × 58 × 25 mm. Geometry and sources: src/lib/grModel.js and src/components/onboard/README.md.</p>

    <div class="controls" role="group" aria-label="Animation">
      <fieldset class="seg">
        <legend class="micro">Action</legend>
        {#each ACTIONS as a}
          <label><input type="radio" name="act" value={a} bind:group={action} onchange={() => play++}><span>{a || 'none'}</span></label>
        {/each}
      </fieldset>
      <button class="btn quiet" type="button" onclick={() => play++}>Run again</button>
      <label class="chk"><input type="checkbox" bind:checked={reduced}> Reduced motion</label>
      <label class="chk"><input type="checkbox" bind:checked={callouts}> Callouts</label>
    </div>

    {#each Object.keys(VIEWS) as view}
      <section class="view" aria-labelledby="v-{view}">
        <h2 id="v-{view}" class="micro">{view}</h2>
        <div class="row">
          {#each SIZES as s}
            <div class="cell" style:width="{s.w}px">
              <GrCamera {view} highlight={[sel[view]]} action={action || null} hold={action === 'hold' ? 3 : 0} {play}
                reduced={reduced} maxWidth={s.w} callouts={callouts && s.w > 150 ? [{id: sel[view], n: 1}] : []} />
              <p class="small">{s.n}</p>
            </div>
          {/each}
        </div>
        <fieldset class="picks">
          <legend class="small">Highlight</legend>
          {#each CONTROL_IDS[view] as id}
            <label class="pick"><input type="radio" name={'h-' + view} value={id} bind:group={sel[view]} onchange={() => play++}>
              <span>{controlOf(view, id).name}</span></label>
          {/each}
        </fieldset>
      </section>
    {/each}
  </main>
</div>

<style>
  .gal { min-height: 100vh; background: var(--bg); }
  .top { display: flex; justify-content: space-between; align-items: center; padding: 24px 28px; }
  .wrap { max-width: 1120px; margin: 0 auto; padding: 24px 28px 96px; display: grid; gap: 24px; }
  .intro { margin-top: -16px; }
  .controls { display: flex; flex-wrap: wrap; gap: 16px 24px; align-items: center; }
  .seg { border: 0; padding: 0; margin: 0; display: flex; gap: 2px; align-items: center; flex-wrap: wrap; }
  .seg legend { float: left; margin-right: 12px; padding: 0; }
  .seg label { position: relative; }
  .seg input { position: absolute; opacity: 0; inset: 0; margin: 0; }
  .seg span { display: block; padding: 6px 10px; border: 1px solid var(--line-strong); border-radius: var(--r-ctl); font-size: 13px; color: var(--sub); }
  .seg input:checked + span { background: var(--ink); color: var(--bg); border-color: var(--ink); }
  .seg input:focus-visible + span { outline: 2px solid var(--ink); outline-offset: 2px; }
  .chk { display: inline-flex; gap: 8px; align-items: center; font-size: 13.5px; color: var(--sub); }
  .view { display: grid; gap: 16px; padding-top: 48px; border-top: 1px solid var(--line); }
  .row { display: flex; flex-wrap: wrap; gap: 32px; align-items: flex-end; }
  .cell { display: grid; gap: 8px; }
  .picks { border: 0; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 4px 16px; }
  .picks legend { padding: 0; margin-bottom: 8px; }
  .pick { display: inline-flex; gap: 6px; align-items: center; font-size: 13px; color: var(--sub); min-height: 28px; }
  @media (max-width: 720px) { .wrap, .top { padding-left: 16px; padding-right: 16px; } .cell { max-width: 100%; } }
</style>
