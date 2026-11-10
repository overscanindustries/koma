<script>
  // Copy to another GR: the first camera's settings onto the second, keeping what belongs to the second camera
  // (exclude_on_merge). Same rules as the Setup page's copy task: mergeInto(merged, C.from, true, false); a differing
  // Koma version needs "Copy anyway"; a differing firmware tag is a warning only. Step 2 reads the second GR by itself
  // the moment a new camera is on the cable; the flow lives in lib/copy.svelte.js, so it outlives this screen.
  import * as E from '../../engine/engine.js';
  import {S, map, userField, adopt, saveBlock} from '../../lib/settings.svelte.js';
  import {startApply} from '../../lib/apply.svelte.js';
  import {cam, connected} from '../../lib/camera.svelte.js';
  import {C, readCam, accept, resetCopy} from '../../lib/copy.svelte.js';
  import {clean, showValue, differs, plural, plainReason} from '../../lib/words.js';
  import FileDrop from '../../components/FileDrop.svelte';
  import Sheet from '../../components/Sheet.svelte';
  import OnCamera from '../../components/OnCamera.svelte';
  import StatusLine from '../../components/ui/StatusLine.svelte';
  import Details from '../../components/ui/Details.svelte';
  import SectionHead from '../../components/ui/SectionHead.svelte';
  import SpecSheet from '../../components/ui/SpecSheet.svelte';
  import Loading from '../../components/ui/Loading.svelte';

  const PAGE = {shooting: 'SHOOTING', key_custom: 'KEY CUST.', setup: 'SETUP', koma: 'SETUP', my1: 'MY SETTINGS 1', my2: 'MY SETTINGS 2', playback: 'PLAYBACK', recipe: 'LOOKS'};
  const PAGE_ORDER = ['shooting', 'key_custom', 'setup', 'koma', 'my1', 'my2'];

  let cardSaved = $state(false);

  // Step 2: a camera on a connection other than the first GR's is read once, by itself. The manual read is the
  // fallback after that read failed, never offered while the first GR is still the one on the cable.
  $effect(() => {
    if (C.step !== 2 || C.to || C.reading || !connected()) return;
    if (cam.conn === C.fromConn || cam.conn === C.autoConn) return;
    C.autoConn = cam.conn;
    readCam('to', true);
  });
  const manualTo = $derived(connected() && !C.reading && cam.conn !== C.fromConn && C.autoFailConn === cam.conn);

  const review = $derived.by(() => {
    if (C.step !== 3 || !C.from || !C.to || S.map !== 'ready') return null;
    const M = map();
    const merged = C.to.slice();
    E.mergeInto(merged, C.from, true, false);
    const versionDiff = E.komaVersion(C.from) !== E.komaVersion(C.to);
    const tagDiff = E.tagDiffers(C.from, C.to);
    const changes = M.fields.filter(f => userField(f) && differs(f, C.to, merged))
      .sort((a, b) => PAGE_ORDER.indexOf(a.page) - PAGE_ORDER.indexOf(b.page) || (a.order ?? 1e9) - (b.order ?? 1e9) || a.offset - b.offset);
    const groups = [];
    changes.forEach(f => { const p = PAGE[f.page] || 'OTHER'; let g = groups.find(x => x.p === p); if (!g) groups.push(g = {p, rows: []}); g.rows.push(f); });
    const shown = new Array(M.size).fill(false);
    changes.forEach(f => { for (let i = f.offset; i < f.end; i++) shown[i] = true; });
    for (let i = M.csOff; i < M.csOff + M.csSize; i++) shown[i] = true;
    let other = 0; for (let i = 0; i < M.size; i++) if (!shown[i] && merged[i] !== C.to[i]) other++;
    const overlaps = (a, n, b, m) => a < b + m && b < a + n;
    const keptAll = M.exclude.filter(e => !overlaps(e.off, e.size, M.magicOff, M.magic.length) && !overlaps(e.off, e.size, M.csOff, M.csSize)).map(e => {
      const names = M.fields.filter(f => overlaps(f.offset, f.size, e.off, e.size)).map(f => clean(f.label));
      let same = true; for (let i = e.off; i < e.off + e.size; i++) if (C.from[i] !== C.to[i]) same = false;
      return {name: names.length ? names.slice(0, 3).join(', ') + (names.length > 3 ? ' and more' : '') : 'Camera data', why: plainReason(e.reason), same};
    });
    const kept = keptAll.filter(k => !k.same);
    return {merged, versionDiff, tagDiff, tagText: tagDiff ? E.tagWarning(C.from, C.to) : '', changes, groups, other, kept, keptSame: keptAll.length - kept.length, keptAll};
  });
  const blocked = $derived(!review || (review.versionDiff && !C.anyway));

  // The camera on the cable must be the one chosen under To, or its camera-specific settings would be wrong: the
  // Apply runner checks it before sending (base).
  function send() {
    startApply({bytes: review.merged, fields: review.changes, base: C.to, title: 'Copy to this GR', force: C.anyway,
      differentMsg: 'The camera on the cable is not the one chosen under To. Connect that camera and try again. Nothing was sent.',
      onDone: now => { adopt(now); C.done = true; }});
  }
  async function toCard() { if (review && await saveBlock(review.merged, 'PARAM.DAT')) cardSaved = true; }
  const STEPS = ['From', 'To', 'Review'];
</script>

<ol class="dots" aria-label="Steps">
  {#each STEPS as s, i}
    <li class:on={C.step === i + 1} class:done={C.step > i + 1} aria-current={C.step === i + 1 ? 'step' : undefined}>
      <span class="num n">{i + 1}</span><span>{s}</span>
    </li>
  {/each}
</ol>

{#if C.step === 1}
  <section class="step" aria-labelledby="cp1">
    <h2 id="cp1">The GR to copy from</h2>
    {#if C.from}
      <SpecSheet label="The GR to copy from" testid="copy-from" rows={[{label: 'Runs', value: C.fromInfo.runs}, {label: 'Settings', value: C.fromInfo.how}]} />
      <p><button class="link small" type="button" onclick={() => { C.from = null; C.to = null; C.fromConn = -1; }}>Choose another</button></p>
    {:else}
      {#if connected()}
        <div class="row"><button class="btn primary" type="button" onclick={() => readCam('from')} disabled={!!C.reading}>Read the connected camera</button>
          {#if C.reading === 'from'}<Loading text="Reading the settings from the camera." />{/if}</div>
      {/if}
      <FileDrop label="Choose the settings file" hint="The camera writes this as PARAM.CUR, at the top level of its memory card."
        onfile={(b, n) => accept('from', b, n)} testid="copy-from-file" />
    {/if}
    {#if C.from}<div class="nav"><span></span><button class="btn primary" type="button" onclick={() => (C.step = 2)}>Next</button></div>{/if}
  </section>
{:else if C.step === 2}
  <section class="step" aria-labelledby="cp2">
    <h2 id="cp2">The GR to copy to</h2>
    {#if C.to}
      <SpecSheet label="The GR to copy to" testid="copy-to" rows={[{label: 'Runs', value: C.toInfo.runs}, {label: 'Settings', value: C.toInfo.how}]} />
      <p><button class="link small" type="button" onclick={() => { C.to = null; C.autoConn = cam.conn; }}>Choose another</button></p>
    {:else}
      <p class="muted">{C.fromConn >= 0 ? 'Unplug this GR. Turn the other one on, then plug it in.' : 'Turn the other GR on, then plug it in.'}</p>
      {#if C.reading === 'to'}<Loading text="Reading the settings from the camera." />
      {:else if cam.state === 'nocam'}<StatusLine tone="wait" text="Waiting for the other GR." testid="copy-waiting" />
      {:else if manualTo}
        <div class="row"><button class="btn primary" type="button" onclick={() => readCam('to')}>Read the connected camera</button></div>
      {/if}
      <FileDrop label="Choose the settings file" hint="The camera writes this as PARAM.CUR, at the top level of its memory card."
        onfile={(b, n) => accept('to', b, n)} testid="copy-to-file" />
    {/if}
    <div class="nav"><button class="btn quiet" type="button" onclick={() => (C.step = 1)}>Back</button>
      {#if C.to}<button class="btn primary" type="button" onclick={() => (C.step = 3)}>Next</button>{/if}</div>
  </section>
{:else if review && C.done}
  <section class="step" aria-label="Copied">
    <StatusLine tone="ok" text="Copied. The second GR matches." testid="copy-done"
      action={{label: 'Copy again', onclick: resetCopy, kind: 'link', testid: 'copy-again'}} />
  </section>
{:else if review}
  <section class="step" aria-labelledby="cp3">
    <h2 id="cp3">{review.changes.length ? plural(review.changes.length, 'setting changes', 'settings change') : 'Nothing to change'}</h2>
    <p class="muted">{review.changes.length ? plural(review.kept.length, 'setting belongs', 'settings belong') + ' to the second GR and ' + (review.kept.length === 1 ? 'is' : 'are') + ' kept.' : 'Both cameras already have the same settings.'}</p>
    {#if review.versionDiff}
      <StatusLine tone="bad" text="These cameras run different Koma versions. Settings the second GR does not have are skipped."
        action={C.anyway ? null : {label: 'Copy anyway', onclick: () => (C.anyway = true), testid: 'copy-anyway'}} testid="copy-version" />
    {/if}
    {#if review.tagDiff}<p class="small muted">{review.tagText}</p>{/if}
    {#each review.groups as g, gi}
      <div class="grp" class:lead={gi === 0}>
        <SectionHead label={g.p} level={3} first={gi === 0} />
        <ul class="changes">
          {#each g.rows as f}
            <li><span class="cam">{clean(f.label)}</span>
              <span class="vals"><span class="old">{showValue(f, C.to)}</span><span class="to">to</span><span>{showValue(f, review.merged)}</span></span></li>
          {/each}
        </ul>
      </div>
    {/each}
    {#if review.other}<p class="small muted">Values outside the camera menu are copied too.</p>{/if}
    {#if review.keptAll.length}
      <div class="kept"><Details label={'Kept on the second GR' + (review.kept.length ? ' · ' + review.kept.length : '')}>
        <ul class="changes">
          {#each review.kept as k}<li class="k"><span>{k.name}</span><span class="muted small">{k.why}</span></li>{/each}
          {#if review.keptSame}<li class="k muted small">{review.keptSame} more {review.keptSame === 1 ? 'is' : 'are'} already the same on both.</li>{/if}
        </ul>
      </Details></div>
    {/if}
    <!-- the effect, said once, just above the buttons it belongs to -->
    {#if connected() && review.changes.length}<p class="small muted" data-testid="copy-effect">Writes {plural(review.changes.length, 'setting', 'settings')} to the connected GR and restarts it.</p>{/if}
    <div class="nav"><button class="btn quiet" type="button" onclick={() => (C.step = 2)}>Back</button>
      <span class="acts">
        <button class="btn" class:primary={!connected()} type="button" onclick={toCard} disabled={blocked}>Save to card</button>
        {#if connected()}<button class="btn primary" type="button" onclick={send} disabled={blocked || !review.changes.length}>Send to the connected GR</button>{/if}
      </span></div>
  </section>
{:else}
  <StatusLine tone="bad" text="The settings list did not load." />
{/if}
{#if C.err}<div class="cerr"><StatusLine tone="bad" text={C.err} /></div>{/if}

<Sheet open={cardSaved} title="Finish on the other camera" onclose={() => (cardSaved = false)}>
  <OnCamera />
  {#snippet actions()}<button class="btn primary" type="button" onclick={() => (cardSaved = false)}>Done</button>{/snippet}
</Sheet>

<style>
  /* the three steps: the index is the progress; the current one in ink with the amber rule. The strip and the step
     below it share one right edge (680 px). */
  .dots { list-style: none; margin: 0 0 var(--s6); padding: 0; display: flex; border-bottom: 1px solid var(--line); max-width: 680px; }
  .dots li { display: flex; align-items: baseline; gap: var(--s2); padding: 0 var(--s5) var(--s3) 0; position: relative; color: var(--faint); font-size: var(--fs-body); }
  .dots .n { font-size: var(--fs-small); color: inherit; }
  .dots li.on { color: var(--ink); }
  .dots li.on::after { content: ''; position: absolute; left: 0; right: var(--s5); bottom: -1px; height: 2px; background: var(--acc); }
  .dots li.done { color: var(--sub); }
  .step { display: grid; gap: var(--s4); max-width: 680px; }
  h2 { font-size: var(--fs-title); line-height: var(--lh-title); font-weight: 500; letter-spacing: -.01em; }
  .row { display: flex; gap: var(--s3) var(--s4); align-items: center; flex-wrap: wrap; }
  .nav { display: flex; justify-content: space-between; align-items: center; gap: var(--s3); margin-top: var(--s4); padding-top: var(--s4); border-top: 1px solid var(--line); flex-wrap: wrap; }
  .acts { display: flex; gap: var(--s3); flex-wrap: wrap; align-items: center; justify-content: flex-end; }
  .changes { list-style: none; margin: 0; padding: 0; }
  .changes li { display: flex; justify-content: space-between; align-items: center; gap: var(--s4); min-height: var(--spec-h); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .vals { display: inline-flex; gap: var(--s2); font-size: var(--fs-body); }
  .old { color: var(--sub); }
  .to { color: var(--faint); font-size: var(--fs-small); }
  .k { display: grid !important; gap: 2px; padding: var(--s2) 0; }
  /* the first list head sits 40 px under the summary (16 of grid gap and 24 here), about 3x its 12 below */
  .grp.lead { margin-top: var(--s5); }
  .kept { margin-top: var(--s2); }
  .cerr { margin-top: var(--s4); }
</style>
