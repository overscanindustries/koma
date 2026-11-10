<script>
  // Pick a look: the IMG SET list (21 looks, camera order; names, groups and one-liners from lib/looks.js), the
  // active one marked, the chosen one drawn on the sample photo beside the list (LookPreview: the Looks try-on). Picking
  // writes it to the camera's RAM over USB and reads it back (lib/onboardLook.js). No
  // optimistic state.
  import {onMount} from 'svelte';
  import {st, save, plan, next, back} from '../../lib/onboard.svelte.js';
  import {readStore, activeId, activate} from '../../lib/onboardLook.js';
  import {LOOKS, LOOK_GROUPS} from '../../lib/looks.js';
  import {W} from '../../lib/onboardWords.js';
  import {STATE} from '../../lib/words.js';
  import Spread from '../../components/onboard/Spread.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';
  import BackAction from '../../components/onboard/BackAction.svelte';
  import LookPreview from './LookPreview.svelte';

  let phase = $state('reading');   // reading | ready | setting | set | failed | busy | unsupported
  let active = $state(null);
  let pick = $state(null);
  let block = $state(null);        // the camera's look store, for the preview's values

  // A busy camera (its menu open: the bridge's 409 camera_busy) is not a camera without the feature.
  const busyErr = e => !!(e && e.data && e.data.code === 'camera_busy');
  async function load() {
    phase = 'reading';
    try {
      const b = await readStore();
      block = b;
      active = activeId(b);
      pick = pick || active || LOOKS[0].id;
      if (active) { st.look = {id: active, name: name(active), at: Date.now()}; save(); }   // Done shows the camera's look
      phase = 'ready';
    } catch (e) {
      phase = busyErr(e) ? 'busy' : e && (e.status === 501 || (e.data && e.data.code === 'not_supported')) ? 'unsupported' : 'failed';
    }
  }
  onMount(load);

  async function use() {
    const look = LOOKS.find(l => l.id === pick);
    phase = 'setting';
    try {
      if (await activate(pick)) { active = pick; st.look = {id: pick, name: look.name, at: Date.now()}; save(); phase = 'set'; }
      else phase = 'failed';
    } catch (e) { phase = busyErr(e) ? 'busy' : 'failed'; }
  }

  const name = id => (LOOKS.find(l => l.id === id) || {}).name || '';
  const chosen = $derived(LOOKS.find(l => l.id === pick));
  const changing = $derived(phase === 'ready' || phase === 'failed' || phase === 'busy' ? pick !== active : false);
</script>

{#snippet preview()}
  {#if chosen && phase !== 'unsupported'}<LookPreview {block} id={chosen.id} name={chosen.name} />{/if}
{/snippet}

<Spread step="look" index={st.step + 1 + ' / ' + plan().length} title={W.steps.look} lead={W.look.lead} lines={[W.look.line]}
  figure={phase === 'unsupported' ? null : preview}>
  {#snippet body()}
    {#if phase === 'unsupported'}
      <StatusLine text={W.look.notSupported} testid="look-status" />
    {:else}
      <div class="state">
        {#if phase === 'reading'}
          <StatusLine text={W.look.reading} testid="look-status" />
        {:else if phase === 'setting'}
          <StatusLine text={W.look.setting(name(pick))} testid="look-status" />
        {:else if phase === 'set' && pick === active}
          <StatusLine tone="ok" text={W.look.active(name(active))} sub={STATE.keepsLook} testid="look-status" />
        {:else if phase === 'failed' || phase === 'busy'}
          <StatusLine tone="bad" text={phase === 'busy' ? W.look.busy : W.look.failed} testid="look-status"
            action={{label: W.look.tryAgain, onclick: active === null ? load : use}} />
        {:else if chosen}
          <!-- the chosen look and what it does. The name stays: without it the line reads as STANDARD's caption, the
               first cell having no group head -->
          <p class="small line" aria-live="polite"><span class="cam nm">{chosen.name}</span>{chosen.line}</p>
        {/if}
      </div>
      <fieldset class="looks" disabled={phase === 'reading' || phase === 'setting'} data-testid="look-list">
        <legend class="sr-only">{W.steps.look}</legend>
        {#each LOOK_GROUPS as g (g.id)}
          {#if g.label}<p class="micro head">{g.label}</p>{/if}
          <div class="grid">
            {#each LOOKS.filter(l => l.group === g.id) as l (l.id)}
              <label class="cell" class:active={l.id === active} data-look={l.name}>
                <input type="radio" name="look" value={l.id} bind:group={pick} />
                <span class="cam">{l.name}</span>
              </label>
            {/each}
          </div>
        {/each}
      </fieldset>
    {/if}
  {/snippet}
  {#snippet actions()}
    <BackAction onclick={back} disabled={phase === 'setting'} />
    {#if changing}
      <button class="btn primary" type="button" onclick={use} data-testid="setup-use">{W.look.use(name(pick))}</button>
    {:else}
      <button class="btn primary" class:onb-wait={phase === 'reading' || phase === 'setting'} type="button" onclick={next}
        disabled={phase === 'reading' || phase === 'setting'} data-testid="setup-next">{W.next}</button>
    {/if}
  {/snippet}
</Spread>

<style>
  .looks { border: 0; margin: 0; padding: 0; display: grid; gap: 8px; min-width: 0; }
  .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 4px; }
  .cell { position: relative; display: flex; align-items: center; min-height: 40px; padding: 0 12px;
    border: 1px solid var(--line); border-radius: var(--r-ctl, 4px); background: var(--raise); cursor: pointer;
    color: var(--ink); transition: border-color var(--t-fast, .12s) var(--ease), background var(--t-fast, .12s) var(--ease); }
  .cell:hover { border-color: var(--line-strong); }
  .cell input { position: absolute; inset: 0; opacity: 0; margin: 0; cursor: pointer; }
  .cell:has(input:checked) { background: var(--ink); border-color: var(--ink); color: var(--bg); }
  .cell:has(input:focus-visible) { outline: 2px solid var(--ink); outline-offset: 2px; }
  /* the look the camera has now: the active state, in amber */
  .cell.active::before { content: ''; position: absolute; left: -1px; right: -1px; top: -1px; height: 2px; background: var(--acc); border-radius: 2px 2px 0 0; }
  .looks:disabled .cell { opacity: .45; cursor: default; }
  .state { min-height: 20px; display: grid; gap: 4px; align-content: start; }
  .line { display: flex; gap: 12px; align-items: baseline; min-height: 20px; line-height: 20px; }
  .nm { color: var(--ink); }
  /* group heads in the section-head rhythm: space above three to four times the space below, a rule */
  .head { margin-top: 32px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
  @media (max-width: 720px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .cell { min-height: 44px; } .head { margin-top: 24px; } }
</style>
