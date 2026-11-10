<script>
  // Advanced: the engineer view of koma-setup.html, ported. The settings list and its coverage, both blocks with
  // magic, checksum and Koma status, a raw field editor with offsets, merge with force and include-excluded, the raw
  // diff, a hex dump, and a download with any file name. "This body" is the block the rest of the app works on, so
  // raw edits show in the pending bar and go out with Apply.
  import * as E from '../../engine/engine.js';
  import {S, B, blk, map, setBlock, touch, useMap, readFromCamera} from '../../lib/settings.svelte.js';
  import {connected} from '../../lib/camera.svelte.js';
  import {bridgeJson, b64decode, lsGet, lsSet} from '../../lib/bridge.js';
  import {USB_NAME, plural} from '../../lib/words.js';
  import SpecSheet from '../../components/ui/SpecSheet.svelte';

  // Every keyboard binding in the app, listed once. Shoot.svelte binds Space and, with Remote open,
  // the camera keys (lib/shoot.svelte.js RKEYS); PendingBar binds Cmd/Ctrl+Z; a sheet closes on Esc (<dialog>).
  // A new binding adds its row here.
  const KEYS = [
    {label: 'Space', value: 'Takes a photo on Shoot'},
    {label: 'Cmd+Z or Ctrl+Z', value: 'Undoes the last change waiting to be applied'},
    {label: 'Esc', value: 'Closes a sheet. With Remote open on Shoot, presses DISP'},
    {label: 'Arrow keys', value: "With Remote open on Shoot, presses the camera's arrow buttons"},
    {label: 'Enter', value: 'With Remote open on Shoot, presses MENU OK'},
  ];

  const h = E.h;
  let src = $state(null), srcName = $state(''), srcRev = $state(0);
  let tab = $state(lsGet('koma.tab') || ''), q = $state('');
  let errors = $state({});
  let mergeOut = $state([]), force = $state(false), incl = $state(false);
  let outName = $state('PARAM.DAT'), msg = $state(''), msgBad = $state(false);
  let rawOpen = $state(false), flash = $state(null);
  let mapMsg = $state('');

  const M = $derived.by(() => { void S.rev; return S.map === 'ready' ? map() : null; });
  const pages = $derived(M ? M.pages : []);
  $effect(() => { if (M && pages.indexOf(tab) < 0) tab = pages[0]; });
  const mapInfo = $derived.by(() => {
    if (!M) return S.mapNote ? 'Settings list rejected: ' + S.mapNote : 'No settings list';
    const cov = E.coverage().filter(Boolean).length;
    return (S.mapLabel || 'map') + ': firmware ' + M.firmware + ', ' + M.fields.length + ' fields on ' + M.pages.length +
      ' page(s), ' + cov + '/' + M.size + ' bytes mapped (' + (100 * cov / M.size).toFixed(1) + '%)';
  });

  function env(b) {
    if (!b) return null;
    const comp = E.computeChecksum(b), st = E.storedChecksum(b);
    return {size: b.length, magic: E.magicOk(b), stored: h(st, 8), comp: h(comp, 8), csOk: comp === st, kv: E.komaVersion(b), tag: E.ksioTag(b)};
  }
  const envThis = $derived.by(() => { void S.rev; return env(B.blk); });
  const envSrc = $derived.by(() => { void srcRev; return env(src); });

  const list = $derived.by(() => {
    void S.rev;
    if (!M || !B.blk) return [];
    const n = q.trim().toLowerCase();
    return M.fields.filter(f => n ? (f.name.toLowerCase().includes(n) || f.label.toLowerCase().includes(n)) : f.page === tab);
  });
  function tabCount(p) { void S.rev; return B.blk ? M.fields.filter(f => f.page === p && changed(f)).length : 0; }
  function changed(f) { for (let i = f.offset; i < f.end; i++) if (B.blk[i] !== B.orig[i]) return true; return false; }

  function commit(f, text) {
    E.bind(B.blk, B.orig);
    const e = E.setFieldText(f, text);
    if (e) errors[f.name] = e; else delete errors[f.name];
    touch();
  }
  function reset(f) { for (let i = f.offset; i < f.end; i++) B.blk[i] = B.orig[i]; delete errors[f.name]; touch(); }
  function resetAll() { if (B.orig) { B.blk.set(B.orig); errors = {}; touch(); } }

  function bitsOf(f) {
    void S.rev;
    const v = E.uintOf(f, B.blk), out = [];
    for (let i = 0; i < f.size * 8; i++) { const on = ((v >> BigInt(i)) & 1n) === 1n; if (f.bits[i] || on) out.push({i, on, name: f.bits[i] || 'BIT' + i}); }
    return out;
  }
  function bitSet(f, i, on) { let x = E.uintOf(f, B.blk); const bit = 1n << BigInt(i); x = on ? x | bit : x & ~bit; commit(f, '0x' + x.toString(16)); }
  function enumOpts(f) {
    void S.rev;
    const v = E.uintOf(f, B.blk);
    const keys = Object.keys(f.values).map(Number).sort((a, b) => a - b).map(k => ({v: String(k), t: f.values[k] + ' (0x' + h(k, f.size * 2) + ')'}));
    if (!(String(v) in f.values)) keys.push({v: String(v), t: 'raw 0x' + v.toString(16).padStart(f.size * 2, '0')});
    return {keys, cur: String(v)};
  }

  async function loadFile(e, which) {
    const f = e.currentTarget.files[0]; e.currentTarget.value = '';
    if (!f) return;
    const b = new Uint8Array(await f.arrayBuffer());
    if (b.length !== E.BLOCK_SIZE) { msg = f.name + ': ' + b.length + ' bytes, expected ' + E.BLOCK_SIZE + '. Rejected.'; msgBad = true; return; }
    msg = '';
    if (which === 'this') { setBlock(b, f.name, 'card'); errors = {}; mergeOut = []; }
    else { src = b; srcName = f.name; srcRev++; }
  }
  async function srcFromCamera() {
    try { src = b64decode((await bridgeJson('/settings', {timeout: 30000})).data); srcName = USB_NAME; srcRev++; msg = ''; }
    catch (e) { msg = 'Could not read the camera: ' + e.message; msgBad = true; }
  }
  async function mapFile(e) {
    const f = e.currentTarget.files[0]; e.currentTarget.value = '';
    if (!f) return;
    try { useMap(JSON.parse(await f.text()), f.name); mapMsg = ''; } catch (err) { mapMsg = 'Settings list rejected: ' + err.message; }
  }
  function merge() {
    mergeOut = [];
    if (!M || !B.blk || !src) return;
    const r = E.mergeInto(B.blk, src, force, incl);
    const lines = r.warnings.map(w => ({t: 'warning: ' + w, c: 'mut'})).concat(r.problems.map(p => ({t: (force ? 'warning: ' : 'error: ') + p, c: force ? 'mut' : 'bad'})));
    if (!r.ok) { mergeOut = lines.concat([{t: 'Not merged. Tick Force to override.', c: 'bad'}]); return; }
    errors = {};
    touch();
    mergeOut = lines.concat(r.lines.map(t => ({t, c: ''})));
  }
  function download() {
    if (!B.blk) return;
    const out = B.blk.slice(); E.setChecksum(out);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([out], {type: 'application/octet-stream'}));
    a.download = outName.trim() || 'PARAM.DAT';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    msg = 'Wrote ' + a.download + ' (4096 B, checksum 0x' + h(E.storedChecksum(out), 8) + ', ' + E.CS_ALGO + ').'; msgBad = false;
  }

  const diff = $derived.by(() => {
    void S.rev; void srcRev;
    if (!M || !B.blk || !src) return null;
    const rows = M.fields.filter(f => E.hexOf(E.rawBytes(f, B.blk)) !== E.hexOf(E.rawBytes(f, src)));
    const ranges = E.unmappedRanges(B.blk, src);
    const cs = E.storedChecksum(B.blk) !== E.storedChecksum(src);
    return {rows, ranges, cs};
  });

  const hex = $derived.by(() => {
    void S.rev;
    if (!rawOpen || !B.blk) return [];
    const own = new Array(B.blk.length).fill(null);
    if (M) M.fields.forEach(f => { for (let i = f.offset; i < Math.min(f.end, B.blk.length); i++) own[i] = f.name; });
    const lines = [];
    for (let p = 0; p < B.blk.length; p += 16) {
      const cells = [];
      let asc = '';
      for (let i = p; i < p + 16; i++) {
        cells.push({i, t: h(B.blk[i], 2), m: !!own[i], c: B.blk[i] !== B.orig[i], f: own[i], gap: i === p + 7});
        asc += B.blk[i] >= 32 && B.blk[i] < 127 ? String.fromCharCode(B.blk[i]) : '.';
      }
      lines.push({a: h(p, 3), cells, asc});
    }
    return lines;
  });
  function jump(name) {
    const f = M && M.byName[name]; if (!f) return;
    tab = f.page; lsSet('koma.tab', f.page); q = ''; flash = f.name;
    queueMicrotask(() => { const r = document.getElementById('af-' + f.name); if (r) r.scrollIntoView({block: 'center'}); });
    setTimeout(() => { flash = null; }, 1500);
  }
</script>

<div class="adv">
  <section class="card">
    <h2 class="micro">Settings list</h2>
    <p class="mono small" class:bad={!M}>{mapInfo}</p>
    {#if mapMsg}<p class="mono small bad">{mapMsg}</p>{/if}
    <label class="btn sm file">Choose settings list<input type="file" accept=".json,application/json" class="sr-only" onchange={mapFile}></label>
  </section>

  <div class="bodies">
    <section class="card">
      <h2 class="micro">This body</h2>
      {#if envThis}
        <p class="mono small">{S.name}: {envThis.size} B<br>magic <span class={envThis.magic ? 'ok' : 'bad'}>{envThis.magic ? 'ok' : 'BAD'}</span><br>
          checksum stored 0x{envThis.stored} computed 0x{envThis.comp} <span class={envThis.csOk ? 'ok' : 'bad'}>{envThis.csOk ? 'ok' : 'BAD'}</span><br>
          Koma {envThis.kv === null ? 'absent' : 'settings format ' + envThis.kv}{envThis.tag !== null ? ', KSIO tag ' + JSON.stringify(envThis.tag) : ''}<br>
          <span class="mut">The stored checksum is rewritten on download and apply.</span></p>
      {:else}<p class="mono small mut">None</p>{/if}
      <div class="acts">
        <label class="btn sm file">Load file<input type="file" class="sr-only" onchange={e => loadFile(e, 'this')}></label>
        {#if connected()}<button class="btn sm" type="button" onclick={readFromCamera}>Read camera</button>{/if}
      </div>
    </section>
    <section class="card">
      <h2 class="micro">Source body</h2>
      {#if envSrc}
        <p class="mono small">{srcName}: {envSrc.size} B<br>magic <span class={envSrc.magic ? 'ok' : 'bad'}>{envSrc.magic ? 'ok' : 'BAD'}</span><br>
          checksum stored 0x{envSrc.stored} computed 0x{envSrc.comp} <span class={envSrc.csOk ? 'ok' : 'bad'}>{envSrc.csOk ? 'ok' : 'BAD'}</span><br>
          Koma {envSrc.kv === null ? 'absent' : 'settings format ' + envSrc.kv}{envSrc.tag !== null ? ', KSIO tag ' + JSON.stringify(envSrc.tag) : ''}</p>
      {:else}<p class="mono small mut">None</p>{/if}
      <div class="acts">
        <label class="btn sm file">Load file<input type="file" class="sr-only" onchange={e => loadFile(e, 'src')}></label>
        {#if connected()}<button class="btn sm" type="button" onclick={srcFromCamera}>Read camera</button>{/if}
      </div>
    </section>
  </div>

  <section class="card">
    <h2 class="micro">Merge source into this body</h2>
    <div class="acts">
      <label class="chk"><input type="checkbox" bind:checked={force}> Force (Koma settings format or magic mismatch)</label>
      <label class="chk"><input type="checkbox" bind:checked={incl}> Include excluded (body-specific) ranges</label>
      <button class="btn sm" type="button" onclick={merge} disabled={!(M && B.blk && src)}>Merge</button>
    </div>
    {#if mergeOut.length}<div class="mono small out">{#each mergeOut as l}<div class={l.c}>{l.t}</div>{/each}</div>{/if}
  </section>

  {#if M && blk()}
    <section class="card editor">
      <div class="edhead">
        <div class="ptabs" role="tablist" aria-label="Map pages">
          {#each pages as p}
            {@const n = tabCount(p)}
            <button type="button" role="tab" aria-selected={!q.trim() && p === tab} onclick={() => { tab = p; lsSet('koma.tab', p); q = ''; }}>{p}{n ? ' (' + n + ')' : ''}</button>
          {/each}
        </div>
        <input class="input" type="search" placeholder="name or label" aria-label="Search fields" bind:value={q}>
      </div>
      {#if q.trim()}<p class="small mut">{plural(list.length, 'match', 'matches')} across all pages</p>{/if}
      <div class="fields">
        {#each list as f (f.name)}
          {@const ch = (void S.rev, changed(f))}
          <div class="field" class:changed={ch} class:flash={flash === f.name} id={'af-' + f.name}>
            <div class="fl">{#if ch}<span class="mark">* </span>{/if}{f.label} <span class="mut mono">{f.name}{q.trim() ? ' [' + f.page + ']' : ''}</span>
              {#if f.bodySpecific}<span class="badge">body-specific</span>{/if}
              {#if f.hidden}<span class="badge">hidden in tasks</span>{/if}
              {#if f.confidence}<span class="badge">{f.confidence}</span>{/if}</div>
            <div class="fc">
              {#if f.type === 'enum'}
                {@const o = enumOpts(f)}
                <select class="select" value={o.cur} onchange={e => commit(f, e.currentTarget.value)} aria-label={f.name}>
                  {#each o.keys as k}<option value={k.v}>{k.t}</option>{/each}
                </select>
              {:else if f.type === 'bitfield'}
                <span class="bits">{#each bitsOf(f) as b}<label class="chk"><input type="checkbox" checked={b.on} onchange={e => bitSet(f, b.i, e.currentTarget.checked)}> {b.name}</label>{:else}<span class="mut">No named bits</span>{/each}</span>
              {:else if f.type === 'bytes'}
                <input class="input mono hexin" value={(void S.rev, E.hexOf(E.rawBytes(f, B.blk)))} onchange={e => commit(f, e.currentTarget.value)} aria-label={f.name}>
              {:else}
                <input class="input num" type="number" step="1" min={f.range ? f.range[0] : undefined} max={f.range ? f.range[1] : undefined}
                  value={(void S.rev, E.renderValue(f, B.blk))} onchange={e => commit(f, e.currentTarget.value)} aria-label={f.name}>
                {#if f.unit} {f.unit}{/if}{#if f.range}<span class="mut"> [{f.range[0]}..{f.range[1]}]</span>{/if}
              {/if}
              {#if errors[f.name]}<div class="bad small">{errors[f.name]} (value not applied)</div>{/if}
            </div>
            <div class="meta mono">0x{h(f.offset, 3)} {(void S.rev, E.hexOf(E.rawBytes(f, B.blk)))}
              {#if ch}<button class="link" type="button" onclick={() => reset(f)}>Reset</button>{/if}</div>
          </div>
        {:else}<p class="mut">No fields.</p>{/each}
      </div>
    </section>

    <section class="card">
      <h2 class="micro">Diff (this body vs source)</h2>
      {#if !diff}<p class="small mut">Load both blocks.</p>
      {:else if !diff.rows.length && !diff.ranges.length && !diff.cs}<p class="small">Identical</p>
      {:else}
        {#if diff.rows.length}
          <div class="tw"><table class="small"><thead><tr><th>field</th><th>page</th><th>this</th><th>source</th><th>offset</th></tr></thead><tbody>
            {#each diff.rows as f}<tr><td>{f.name}{#if f.bodySpecific}<span class="badge">body-specific</span>{/if}</td><td>{f.page}</td>
              <td class="mono">{E.renderValue(f, B.blk)}</td><td class="mono">{E.renderValue(f, src)}</td><td class="mono">0x{h(f.offset, 3)}</td></tr>{/each}
          </tbody></table></div>
        {/if}
        {#if diff.ranges.length}
          <p class="small">Unmapped differing bytes:</p>
          <div class="tw"><table class="small"><thead><tr><th>range</th><th>size</th><th>this</th><th>source</th></tr></thead><tbody>
            {#each diff.ranges as [a, e]}
              {@const cut = e - a > 8}
              <tr><td class="mono">0x{h(a, 3)}-0x{h(e - 1, 3)}</td><td>{e - a} B</td>
                <td class="mono">{E.hexOf(B.blk.slice(a, Math.min(e, a + 8)))}{cut ? ' ...' : ''}</td><td class="mono">{E.hexOf(src.slice(a, Math.min(e, a + 8)))}{cut ? ' ...' : ''}</td></tr>
            {/each}
          </tbody></table></div>
        {/if}
        {#if diff.cs}<p class="small mut">Checksum differs: this 0x{h(E.storedChecksum(B.blk), 8)}, source 0x{h(E.storedChecksum(src), 8)}</p>{/if}
      {/if}
    </section>

    <section class="card">
      <h2 class="micro">Download</h2>
      <div class="acts">
        <input class="input mono" bind:value={outName} aria-label="File name">
        <button class="btn sm primary" type="button" onclick={download}>Download</button>
        <button class="btn sm" type="button" onclick={resetAll} disabled={!B.blk || !E.changeCount()}>Reset all</button>
        <span class="small mut">{plural((void S.rev, (E.bind(B.blk, B.orig), E.changeCount())), 'change', 'changes')}</span>
      </div>
    </section>

    <details class="card raw" bind:open={rawOpen}>
      <summary>Hex dump</summary>
      <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
      <pre class="hex" onclick={e => { const n = e.target.closest && e.target.closest('[data-f]'); if (n) jump(n.dataset.f); }}>{#each hex as l}<span class="a">{l.a}  </span>{#each l.cells as c}<span class="b" class:m={c.m} class:c={c.c} data-f={c.f || undefined}>{c.t}</span>{c.gap ? '  ' : ' '}{/each} <span class="a">{l.asc}</span>
{/each}</pre>
    </details>
  {/if}
  {#if msg}<p class="small" class:bad={msgBad} role="status">{msg}</p>{/if}

  <section class="card keys" aria-labelledby="adv-keys">
    <h2 class="micro" id="adv-keys">Keyboard</h2>
    <SpecSheet rows={KEYS} label="Keyboard" testid="keyboard" />
  </section>

</div>

<style>
  /* The engineer view. Sections on --bg with a rule above each, no panels;
     the one place where offsets, checksums and raw values are on the surface. */
  .adv { display: grid; gap: 0; }
  .card { display: grid; gap: var(--s3); padding: var(--s5) 0; border-top: 1px solid var(--line); min-width: 0; }
  .adv > .card:first-child { padding-top: 0; border-top: 0; }
  .card h2 { color: var(--sub); }
  .card summary { color: var(--ink); }
  .bodies { display: grid; grid-template-columns: 1fr 1fr; column-gap: var(--s6); border-top: 1px solid var(--line); }
  .bodies .card { border-top: 0; }
  .mono, .hex, table { font-family: var(--mono); font-size: 12px; line-height: 18px; }
  /* status in the engineer view: ink text, the 6 px dot carries the color */
  .ok, .bad { color: var(--ink); }
  .ok::before, .bad::before { content: ''; display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }
  .ok::before { background: var(--ok); }
  .bad::before { background: var(--bad); }
  .mut { color: var(--sub); }
  .acts { display: flex; gap: var(--s2) var(--s3); flex-wrap: wrap; align-items: center; }
  .file { cursor: pointer; justify-self: start; }
  .file:focus-within { outline: 2px solid var(--ink); outline-offset: 2px; }
  .chk { display: inline-flex; gap: 6px; align-items: center; font-size: var(--fs-small); }
  .out { padding: var(--s2) 10px; background: var(--sunk); border: 1px solid var(--line); border-radius: var(--r-ctl); }
  .edhead { display: flex; justify-content: space-between; gap: var(--s3); flex-wrap: wrap; align-items: center; }
  .edhead .input { height: 32px; }
  .ptabs { display: flex; flex-wrap: wrap; gap: 4px; }
  .ptabs button {
    height: 28px; background: var(--raise); border: 1px solid var(--line-strong); border-radius: var(--r-ctl); padding: 0 8px;
    font-family: var(--mono); font-size: 12px; color: var(--sub);
    transition: color var(--t-fast) var(--ease), border-color var(--t-fast) var(--ease);
  }
  .ptabs button:hover { color: var(--ink); border-color: var(--sub); }
  .ptabs button[aria-selected="true"] { color: var(--bg); background: var(--ink); border-color: var(--ink); }
  .fields { display: grid; border-top: 1px solid var(--line); }
  .field { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1.4fr) minmax(0, .8fr); gap: var(--s3); padding: var(--s2) 0; border-bottom: 1px solid var(--line); align-items: center; font-size: var(--fs-small); }
  .field.changed .fl { color: var(--ink); font-weight: 600; }
  .field.flash { background: color-mix(in srgb, var(--ink) 8%, transparent); }
  .mark { color: var(--ink); }
  .badge { margin-left: 6px; font-size: 11px; border: 1px solid var(--line-strong); border-radius: 2px; padding: 0 4px; color: var(--sub); }
  .fc .select, .fc .input { height: 30px; font-size: var(--fs-small); max-width: 100%; }
  .hexin { width: 100%; }
  .bits { display: flex; flex-wrap: wrap; gap: 4px var(--s3); }
  .meta { color: var(--sub); word-break: break-all; }
  .tw { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: 4px var(--s2) 4px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--sub); font-weight: 500; }
  /* the app's one chevron (app.css --chevron), not the browser's triangle */
  .raw summary { cursor: pointer; list-style: none; display: inline-flex; align-items: center; gap: 2px; justify-self: start; }
  .raw summary::-webkit-details-marker { display: none; }
  .raw summary::before {
    content: ''; width: 16px; height: 16px; flex: none; margin-left: -4px; background: currentColor;
    -webkit-mask: var(--chevron) center / 16px 16px no-repeat; mask: var(--chevron) center / 16px 16px no-repeat;
    transition: transform var(--t) var(--ease);
  }
  .raw[open] summary::before { transform: rotate(90deg); }
  .hex { margin: 0; overflow-x: auto; color: var(--sub); }
  .hex .b.m { color: var(--ink); cursor: pointer; }
  .hex .b.c { background: color-mix(in srgb, var(--ink) 20%, transparent); color: var(--ink); }
  @media (max-width: 720px) {
    .bodies { grid-template-columns: 1fr; }
    .bodies .card + .card { border-top: 1px solid var(--line); }
    .field { grid-template-columns: 1fr; gap: 4px; }
  }
</style>
