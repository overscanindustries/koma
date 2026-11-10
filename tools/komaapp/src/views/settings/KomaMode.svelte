<script>
  // Koma mode: MENU LAYOUT and KOMA FEATURES, each a list of the camera's options with one sentence
  // apiece. The choice goes to the pending bar like any other setting. On a stock camera the choices stay visible,
  // disabled, with the reason and the way to Koma (setup).
  import {S, B, map, setField, undoField, blk} from '../../lib/settings.svelte.js';
  import {uintOf, komaVersion} from '../../engine/engine.js';
  import {clean, differs, showValue} from '../../lib/words.js';
  import EmptyState from '../../components/ui/EmptyState.svelte';
  import RadioRow from '../../components/ui/RadioRow.svelte';
  import {setupEntry} from '../../lib/onboard.svelte.js';

  // Same descriptions as the Setup page's Koma mode task.
  const MODE_TEXT = {
    '5-TAB': "Koma's own five tabs: STILL, KOMA, PLAY, CUSTOM, SETUP.",
    'STOCK+KOMA': 'The two original tabs, plus one KOMA tab for the new features.',
    'GRII': 'Laid out as on the GR II, Koma features included.',
    'FULL': 'Looks, shooting features and the KOMA menu.',
    'ESSENTIALS': 'Large-card (SDHC) support and small fixes only. The menus stay stock.',
  };
  const fields = $derived.by(() => { void S.rev; const M = map(); return M ? ['menu_layout', 'koma_features'].map(n => M.byName[n]).filter(f => f && f.type === 'enum') : []; });
  const keys = f => Object.keys(f.values).map(Number).sort((a, b) => a - b);
  const cur = f => { void S.rev; return String(uintOf(f, B.blk)); };
  const was = f => { void S.rev; return String(uintOf(f, B.orig)); };
  const noKoma = $derived.by(() => { void S.rev; return B.orig ? komaVersion(B.orig) === null : false; });
  const setup = $derived(noKoma ? setupEntry() : null);
</script>

{#if !fields.length}
  <EmptyState label="No modes" text="This Koma version has no modes." />
{:else}
  {#if noKoma}
    <p class="reason" data-testid="needs-koma">Changing the mode needs Koma on the camera.
      {#if setup}{#if setup.href}<a class="link" href={setup.href} data-testid="setup-entry">{setup.label}</a>
      {:else}<button class="link" type="button" onclick={setup.onclick} data-testid="setup-entry">{setup.label}</button>{/if}{/if}</p>
  {/if}
  {#each fields as f, gi (f.name)}
    {@const ch = (void S.rev, differs(f, B.orig, B.blk))}
    <fieldset class="group" class:first={gi === 0}>
      <legend class="micro">{clean(f.label)}</legend>
      {#if !noKoma && !Object.prototype.hasOwnProperty.call(f.values, cur(f))}
        <p class="small muted note">The camera is set to an option not in this list.</p>
      {/if}
      <div class="opts">
        {#each keys(f) as k}
          {@const lab = clean(f.values[k])}
          <RadioRow name={'m-' + f.name} value={String(k)} checked={!noKoma && String(k) === cur(f)} disabled={noKoma}
            label={lab} cam text={MODE_TEXT[lab.toUpperCase().replace(/\s+/g, '')] || ''} testid={f.name + ':' + k}
            note={String(k) === was(f) && ch ? 'On the camera now' : ''} onchange={() => setField(f, String(k))} />
        {/each}
      </div>
      {#if ch}
        <p class="chg small">Changes to {showValue(f, blk())}.
          <button class="btn quiet sm" type="button" onclick={() => undoField(f)}>Undo</button></p>
      {/if}
    </fieldset>
  {/each}
{/if}

<style>
  .reason { color: var(--ink); max-width: 60ch; margin-bottom: var(--s5); }
  .group { border: 0; margin: var(--head-above) 0 0; padding: 0; min-width: 0; }
  .group.first { margin-top: 0; }
  legend { padding: 0 0 var(--s2); margin: 0; width: 100%; border-bottom: 1px solid var(--line); }
  .note { margin: var(--s3) 0 0; }
  .opts { display: grid; }
  .chg { margin-top: var(--s3); color: var(--sub); display: flex; align-items: center; gap: var(--s2); }
</style>
