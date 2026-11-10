<script>
  // Back up & restore. Back up reads the camera, then saves its whole settings
  // block to this computer. Restore states its effect beside the button, shows what changes, then runs the Apply
  // flow, which reads every value back. The bytes that belong to the body (counters, battery, boot values) are never
  // rolled back.
  import {S, B, adopt, readFromCamera, readBlock, readFailText, saveBlock, withChecksum, pending, map, userField} from '../../lib/settings.svelte.js';
  import {startApply} from '../../lib/apply.svelte.js';
  import {connected} from '../../lib/camera.svelte.js';
  import {komaFacts, rest} from '../../lib/overview.svelte.js';
  import {setupEntry} from '../../lib/onboard.svelte.js';
  import SpecSheet from '../../components/ui/SpecSheet.svelte';
  import {inspectBlock, today, clean, showValue, differs, plural, komaOnCamera} from '../../lib/words.js';
  import FileDrop from '../../components/FileDrop.svelte';
  import Sheet from '../../components/Sheet.svelte';
  import OnCamera from '../../components/OnCamera.svelte';
  import SectionHead from '../../components/ui/SectionHead.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';
  import Loading from '../../components/ui/Loading.svelte';
  import ErrorState from '../../components/ui/ErrorState.svelte';
  import Details from '../../components/ui/Details.svelte';

  const card = $derived(S.source === 'card');
  let restore = $state(null);   // {bytes, name, info}
  let savedNote = $state('');
  let saveErr = $state('');
  let saving = $state(false);
  let cardSaved = $state(false);
  const has = $derived.by(() => { void S.rev; return !!B.orig; });
  // What is being saved, from facts already read (no new read). The camera's name is the nav's (NavStatus).
  const k = $derived(komaFacts());
  const kText = $derived(k.state === 'koma' ? komaOnCamera(k.tag, {kind: rest.kind, pkgVersion: rest.pkg && rest.pkg.version})
    : k.state === 'none' ? 'Not installed' : null);
  const facts = $derived(card || !kText ? [] : [{label: 'Koma', value: kText}]);
  const NO_KOMA = 'Backups need Koma on the camera.';  // the stock camera's cause, said for this page
  const readText = $derived(S.readKind === 'stock' ? NO_KOMA : S.readError);
  const setup = $derived(S.readKind === 'stock' ? setupEntry() : null);
  const readAction = $derived(S.readKind === 'stock' ? (setup ? {...setup, kind: 'link', testid: 'setup-entry'} : null)
    : {label: 'Try again', onclick: readFromCamera});

  // Read first, so the backup is what the camera holds now. With edits pending the loaded block stays as it is (the
  // edits sit on it) and the fresh read goes straight to the file.
  async function backup() {
    savedNote = ''; saveErr = '';
    let bytes = B.orig;
    if (!card) {
      saving = true;
      try {
        if (pending().length) bytes = (await readBlock()).bytes;
        else { await readFromCamera(); if (S.readError) throw Object.assign(new Error(S.readError), {plain: true}); bytes = B.orig; }
      } catch (e) {
        saveErr = S.readKind === 'stock' ? NO_KOMA : readFailText(e, S.readKind);
        return;
      } finally { saving = false; }
    }
    const used = bytes && await saveBlock(withChecksum(bytes), 'GR-settings-' + today() + '.dat');
    if (used) savedNote = 'Saved as ' + used + '.';
  }
  function pick(b, n) {
    const info = inspectBlock(b, n, S.mapKV);
    if (!info.ok) return info.msg;
    restore = {bytes: b, name: n, info};
    savedNote = '';
    return '';
  }
  // What a restore would change on this camera, against the block read from it (when it was read).
  const diff = $derived.by(() => {
    void S.rev;
    const M = map();
    if (!restore || !M || !B.orig) return null;
    return M.fields.filter(f => userField(f) && differs(f, B.orig, restore.bytes));
  });
  // The rows are worked out against the camera's block at send time; done is the whole-block rule (apply.svelte.js).
  // The camera holding other settings than were read: Restore to this GR reads it and restores again.
  function toCamera() {
    savedNote = '';
    startApply({bytes: restore.bytes, base: B.orig, title: 'Restore to camera',
      onDone: now => { adopt(now); restore = null; }, rereadLabel: 'Restore to this GR',
      onReread: async () => { await readFromCamera(); if (!S.readError && restore) toCamera(); }});
  }
  async function toCard() { savedNote = ''; if (await saveBlock(withChecksum(restore.bytes), 'PARAM.DAT')) cardSaved = true; }
  const effect = $derived(!diff ? 'Writes the backup to this camera.'
    : diff.length ? 'Writes ' + plural(diff.length, 'setting', 'settings') + ' to this camera.'
    : 'Every listed setting already matches.');
</script>

<section aria-labelledby="bk-h">
  <SectionHead label="Back up" id="bk-h" first />
  <p class="lead-in">Saves a copy on this computer.</p>
  {#if has}
    {#if facts.length}<div class="facts"><SpecSheet rows={facts} label="What the backup holds" /></div>{/if}
    <div class="row">
      <button class="btn" class:primary={!restore} type="button" onclick={backup} data-testid="backup"
        disabled={saving || (!card && !connected())}>Save backup</button>
      {#if saving}<Loading text="Reading the settings from the camera." />
      {:else if saveErr}<StatusLine tone="bad" text={saveErr} />
      {:else if savedNote}<StatusLine tone="ok" text={savedNote} testid="backup-saved" />{/if}
    </div>
  {:else if S.reading}
    <Loading text="Reading the settings from the camera." />
  {:else if S.readError}
    <ErrorState text={readText} action={readAction} testid="read-error" />
  {/if}
</section>

<section aria-labelledby="rs-h">
  <SectionHead label="Restore" id="rs-h" />
  <p class="lead-in">{card ? 'The camera loads the backup at its next start.' : 'The camera restarts to load the backup.'}</p>
  {#if !restore}
    <FileDrop label="Choose a backup" hint="Backups end in .dat and are named like GR-settings-2026-11-07.dat." onfile={pick} testid="restore-file" />
  {:else}
    <div class="picked">
      <p>{restore.info.summary}</p>
      {#if restore.info.warn}<p class="small muted">{restore.info.warn}</p>{/if}
      <p><button class="link small" type="button" onclick={() => (restore = null)}>Choose another backup</button></p>
    </div>
    {#if diff && diff.length}
      <ul class="changes" aria-label="What changes">
        {#each diff as f (f.name)}
          <li><span class="cam">{clean(f.label)}</span>
            <span class="vals"><span class="old">{showValue(f, B.orig)}</span><span class="to">to</span><span>{showValue(f, restore.bytes)}</span></span></li>
        {/each}
      </ul>
    {/if}
    <div class="row go">
      {#if card}
        <button class="btn primary" type="button" onclick={toCard}>Save to card</button>
      {:else}
        <button class="btn primary" type="button" onclick={toCamera} disabled={!connected()}>Restore to camera</button>
        <button class="btn" type="button" onclick={toCard}>Save to card instead</button>
        <span class="small muted effect">{effect}</span>
      {/if}
    </div>
    <div class="more">
      <Details label="What a restore leaves alone">
        <p class="small muted">The counters that belong to this body (file numbers, shots taken), its battery history, the
          values it sets each time it starts, the record of its last settings import, and the checksum.</p>
      </Details>
    </div>
  {/if}
</section>

<Sheet open={cardSaved} title="Finish on the camera" onclose={() => (cardSaved = false)}>
  <OnCamera />
  {#snippet actions()}<button class="btn primary" type="button" onclick={() => (cardSaved = false)}>Done</button>{/snippet}
</Sheet>

<style>
  section { max-width: 640px; }
  .lead-in { color: var(--sub); margin-bottom: var(--s4); max-width: 60ch; }
  .row { display: flex; gap: var(--s3) var(--s4); flex-wrap: wrap; align-items: center; }
  .more { margin-top: var(--s3); }
  .facts { margin-bottom: var(--s5); }
  .picked { display: grid; gap: var(--s1); padding: var(--s3) 0 var(--s4); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
  .changes { list-style: none; margin: var(--s4) 0 0; padding: 0; }
  .changes li { display: flex; justify-content: space-between; gap: var(--s4); align-items: center; min-height: var(--spec-h); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .vals { display: inline-flex; gap: var(--s2); font-size: var(--fs-body); }
  .old { color: var(--sub); }
  .to { color: var(--faint); font-size: var(--fs-small); }
  .go { margin-top: var(--s5); }
  .effect { flex-basis: 100%; }
</style>
