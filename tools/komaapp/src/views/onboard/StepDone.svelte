<script>
  // Done: the verified state, what changed, how to undo it. After an install or
  // update the end state reads as facts: Koma's version (confirmed from KOMALOG.TXT; the read-back under Details), the
  // look the camera holds (read back in Pick a look) and the backup. The short plan (a camera already on Koma) ends on
  // the look it set. The way on is a test photo; Finish goes to Overview.
  import {st, plan, back, finish, packageMeta} from '../../lib/onboard.svelte.js';
  import {W} from '../../lib/onboardWords.js';
  import {komaVersionNumber} from '../../lib/words.js';
  import Spread from '../../components/onboard/Spread.svelte';
  import SpecSheet from '../../components/ui/SpecSheet.svelte';
  import BackAction from '../../components/onboard/BackAction.svelte';

  const official = st.mode === 'official';
  const short = st.plan === 'koma';
  const v = komaVersionNumber((packageMeta() || {}).version);
  const lead = official ? W.done.leadOfficial : short ? W.done.leadKoma : W.done.lead;
  const lines = short ? [] : [official ? W.done.again : W.done.undo];
  const rows = official ? [] : [
    ...(v && !short ? [{label: W.done.rows.koma, value: v, testid: 'done-koma'}] : []),
    ...(st.look ? [{label: W.done.rows.look, value: st.look.name, cam: true, testid: 'done-look'}] : []),
    ...(st.backup && st.backup.b64 ? [{label: W.done.rows.settings, value: W.done.backedUp, testid: 'done-settings'}] : []),
  ];
  const c = st.confirmed;
  const detail = [
    c && c.build ? 'KOMALOG.TXT build ' + c.build + (c.stage ? ', stage ' + c.stage : '') + '. ' + W.done.readBack : '',
    c && c.tag ? 'Newest photo ' + c.photo + ': tag ' + c.tag : '',
  ].filter(Boolean).join('\n');
</script>

<Spread step="done" index={st.step + 1 + ' / ' + plan().length} title={W.steps.done} {lead} {lines}>
  {#snippet body()}
    {#if rows.length}<SpecSheet {rows} label={W.steps.done} testid="done-facts" />{/if}
    {#if detail}<details class="details"><summary>{W.details}</summary><pre>{detail}</pre></details>{/if}
  {/snippet}
  {#snippet actions()}
    <BackAction onclick={back} />
    {#if official}
      <button class="btn primary" type="button" onclick={() => finish('#/home')} data-testid="setup-finish">{W.done.finish}</button>
    {:else}
      <div class="act">
        <button class="link" type="button" onclick={() => finish('#/home')} data-testid="setup-home">{W.done.finish}</button>
        <button class="btn primary" type="button" onclick={() => finish('#/shoot')} data-testid="setup-finish">{W.done.test}</button>
      </div>
    {/if}
  {/snippet}
</Spread>

<style>
  .act { display: flex; gap: 24px; align-items: center; }
  @media (max-width: 720px) { .act { flex: 1 1 auto; justify-content: flex-end; gap: 16px; } }
</style>
