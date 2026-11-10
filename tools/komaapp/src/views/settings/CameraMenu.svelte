<script>
  import {tick} from 'svelte';
  import {S, B, map, userField, undoField, orig, blk} from '../../lib/settings.svelte.js';
  import {buildMenu, layoutFor} from '../../lib/cameraMenu.js';
  import {komaVersion, uintOf} from '../../engine/engine.js';
  import {clean, showValue, differs, plainHelp, STATE} from '../../lib/words.js';
  import {lsGet, lsSet} from '../../lib/bridge.js';
  import {currentName, storeOk, lookIA} from '../../lib/looksStore.js';
  import {W as LW} from '../../lib/looksWords.js';
  import FieldControl from '../../components/FieldControl.svelte';
  import Icon from '../../components/Icon.svelte';
  import Segmented from '../../components/ui/Segmented.svelte';
  import EmptyState from '../../components/ui/EmptyState.svelte';

  const ROW_LABEL = {pic_quality: 'PIC QUALITY/SIZE'};
  // Set in Koma mode, where each option has its sentence: the row shows the value and links there.
  const IN_MODE = new Set(['menu_layout', 'koma_features']);
  // Shoot changes these on the camera as it runs.
  const LIVE = new Set(['exposure_comp', 'iso', 'white_balance']);
  // The layout the camera's MENU LAYOUT setting picks (as loaded, so an unapplied change does not move the tabs).
  function setting(name, b) {
    const M = map(), f = M && M.byName[name];
    return f && b && komaVersion(b) !== null ? Number(uintOf(f, b)) : null;
  }
  const layout = $derived.by(() => { void S.rev; return layoutFor(setting('menu_layout', B.orig)); });
  const tabs = $derived.by(() => { void S.rev; return S.map === 'ready' ? buildMenu(map(), userField, layout) : []; });
  // With Koma, IMG SET is the look list: the row shows the look the camera uses
  // (STANDARD for plain NORMAL, a stock value as its ghost entry) and links into Looks, where looks are set and edited.
  // A Koma with an older look store keeps the stock IMG SET row: Looks does not write that store.
  const look = $derived.by(() => { void S.rev; return B.blk && storeOk(B.blk) ? currentName(B.blk) : ''; });
  // With the Look IA the camera's list holds the 21 looks alone: the stock entries whose values sit behind IMG SET
  // (SETTING1/2, B&W, B&W (TE)) are hidden from it and their detail screens with them. The row then holds IMG SET alone; builds before the Look IA still list them, so they stay there.
  const lookIa = $derived.by(() => { void S.rev; return !!B.blk && lookIA(B.blk); });
  function fieldsOf(r) { return lookIa && r.fields[0].name === 'img_set' ? r.fields.slice(0, 1) : r.fields; }
  let tab = $state(lsGet('koma.menuTab') || 'shooting');
  let q = $state('');
  let changedOnly = $state(false);
  let open = $state(null);

  $effect(() => { if (tabs.length && !tabs.some(t => t.id === tab)) tab = tabs[0].id; });

  function rowChanged(r) { void S.rev; return B.blk ? fieldsOf(r).some(f => differs(f, B.orig, B.blk)) : false; }
  function rowValue(r) {
    void S.rev;
    if (!B.blk) return '';
    const main = r.fields[0];
    if (main.name === 'img_set' && look) return look;
    if (r.fields.length > 1 && main.name === 'pic_quality') return r.fields.slice(0, 2).map(f => showValue(f, B.blk)).join(' · ');
    return showValue(main, B.blk);
  }
  function rowLabel(r) { return r.label || ROW_LABEL[r.key] || clean(r.fields[0].label); }
  function matches(r, needle) {
    // the help the row shows: with Koma's looks, IMG SET's own line, not the stock list's
    return fieldsOf(r).some(f => [clean(f.label), f.name === 'img_set' && look ? LW.imgSetHelp : f.help, ROW_LABEL[f.name]]
      .some(t => String(t || '').toLowerCase().includes(needle)));
  }

  const needle = $derived(q.trim().toLowerCase());
  // What is listed: the current tab, or every tab while searching; Changed only narrows either.
  const shown = $derived.by(() => {
    void S.rev;
    const src = needle ? tabs : tabs.filter(t => t.id === tab);
    return src.map(t => ({...t, groups: t.groups.map(g => ({...g, rows: g.rows.filter(r =>
      (!needle || matches(r, needle)) && (!changedOnly || rowChanged(r)))})).filter(g => g.rows.length)}))
      .filter(t => t.groups.length);
  });
  const total = $derived(shown.reduce((n, t) => n + t.groups.reduce((m, g) => m + g.rows.length, 0), 0));
  function tabChanges(t) { void S.rev; return t.groups.reduce((n, g) => n + g.rows.filter(rowChanged).length, 0); }

  function pick(id) { tab = id; lsSet('koma.menuTab', id); open = null; }
  function tabKey(e, i) {
    const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const n = tabs[(i + d + tabs.length) % tabs.length];
    pick(n.id);
    queueMicrotask(() => document.getElementById('tab-' + n.id)?.focus());
  }
  // An opened row brings its controls into view, clear of the pending bar; so does the first edit, which brings the
  // bar up under it.
  async function reveal(key) {
    await tick();
    document.getElementById('p-' + key)?.scrollIntoView({block: 'nearest'});
  }
  function toggle(key) {
    open = open === key ? null : key;
    if (open) reveal(open);
  }
  let hadEdits = false;
  $effect(() => {
    void S.rev;
    const edits = !!B.blk && !!B.orig && B.blk.some((x, i) => x !== B.orig[i]);
    if (edits && !hadEdits && open) reveal(open);
    hadEdits = edits;
  });

  // The tab strip scrolls sideways on a phone: fade the edge that has more tabs behind it.
  let strip = $state(), fadeL = $state(false), fadeR = $state(false);
  function edges() {
    if (!strip) return;
    fadeL = strip.scrollLeft > 2;
    fadeR = strip.scrollLeft + strip.clientWidth < strip.scrollWidth - 2;
  }
  $effect(() => {
    void tabs.length; void tab;
    if (!strip) return;
    const on = strip.querySelector('[aria-selected="true"]');
    if (on && strip.scrollWidth > strip.clientWidth) on.scrollIntoView({block: 'nearest', inline: 'nearest'});
    edges();
    const ro = new ResizeObserver(edges); ro.observe(strip);
    return () => ro.disconnect();
  });
</script>

<div class="cm">
  <div class="bar">
    <div class="tabs" role="tablist" aria-label="Camera menu tabs" class:dim={!!needle} class:fadeL class:fadeR
      bind:this={strip} onscroll={edges} data-layout={layout}>
      {#each tabs as t, i}
        {@const n = tabChanges(t)}
        <button id={'tab-' + t.id} role="tab" type="button" class:slot={t.slot} aria-selected={!needle && tab === t.id}
          tabindex={tab === t.id ? 0 : -1} aria-controls="cm-list" onclick={() => { q = ''; pick(t.id); }} onkeydown={e => tabKey(e, i)}>
          {t.label}{#if n}<span class="n num" aria-label={n + ' changed'}>{n}</span>{/if}
        </button>
      {/each}
    </div>
    <div class="tools">
      <label class="search">
        <Icon name="search" size={16} />
        <span class="sr-only">Search settings</span>
        <input type="search" class="input" placeholder="Search" bind:value={q} data-testid="menu-search">
      </label>
      <Segmented name="cm-filter" label="Show" small value={changedOnly} testid="changed-only"
        options={[{value: false, label: 'All'}, {value: true, label: 'Changed'}]} onchange={v => (changedOnly = v)} />
    </div>
  </div>

  <div id="cm-list" role={needle ? 'region' : 'tabpanel'} aria-label={needle ? 'Search results' : undefined}
    aria-labelledby={needle ? undefined : 'tab-' + tab} class="list">
    {#if needle}<p class="count micro" aria-live="polite">{total} {total === 1 ? 'setting' : 'settings'}</p>{/if}
    {#each shown as t (t.id)}
      {#if needle}<h2 class="tabname micro">{t.label}</h2>{/if}
      {#each t.groups as g}
        {#if g.title}<h2 class="group micro">{g.title}</h2>{/if}
        <ul class="rows">
          {#each g.rows as r (t.id + r.key)}
            {@const ch = rowChanged(r)}
            {@const id = t.id + '-' + r.key}
            {@const fs = fieldsOf(r)}
            <li class="row" class:open={open === id} class:changed={ch} data-row={r.key}>
              <button class="head" type="button" aria-expanded={open === id} aria-controls={'p-' + id} onclick={() => toggle(id)}>
                <span class="camlabel">{rowLabel(r)}</span>
                <span class="val">{#if ch}<i class="mark" aria-label="Changed"></i>{/if}{rowValue(r)}</span>
                <span class="chev" aria-hidden="true"><Icon name="down" size={16} /></span>
              </button>
              {#if open === id}
                <div class="body" id={'p-' + id}>
                  {#each fs as f, k}
                    {@const fch = (void S.rev, B.blk && differs(f, B.orig, B.blk))}
                    {@const sub = fs.length > 1 && clean(f.label).toUpperCase() !== clean(rowLabel(r)).toUpperCase()}
                    <div class="field">
                      {#if sub}<span class="sub camlabel" id={'l-' + id + '-' + k}>{clean(f.label)}</span>{/if}
                      {#if f.name === 'img_set' && look}<p class="help">{LW.imgSetHelp}</p>
                      {:else if f.help && (k === 0 || fs.length > 1)}<p class="help">{plainHelp(f.help)}</p>{/if}
                      {#if LIVE.has(f.name)}<p class="small muted live">{STATE.shootLive}</p>{/if}
                      <div class="ctl">
                        <span class="sr-only" id={'l1-' + id + '-' + k}>{clean(f.label)}</span>
                        {#if f.name === 'img_set' && look}
                          <span class="ro" data-testid="img-set-look">{look}</span><a class="link small" href="#/looks" data-testid="img-set-link">{LW.changeInLooks}</a>
                        {:else if IN_MODE.has(f.name)}
                          <span class="ro">{showValue(f, blk())}</span><a class="link small" href="#/settings/mode" data-testid={f.name + '-link'}>Change in Koma mode</a>
                        {:else}
                          <FieldControl {f} labelId={sub ? 'l-' + id + '-' + k : 'l1-' + id + '-' + k} />
                        {/if}
                        {#if fch}
                          <span class="was">Was {showValue(f, orig())}</span>
                          <button class="btn quiet sm" type="button" onclick={() => undoField(f)}>Undo</button>
                        {/if}
                      </div>
                      {#if S.errors[f.name]}<p class="err" role="alert">{S.errors[f.name]}</p>{/if}
                    </div>
                  {/each}
                </div>
              {/if}
            </li>
          {/each}
        </ul>
      {/each}
    {:else}
      <div class="empty">
        <EmptyState label={needle ? 'No match' : changedOnly ? 'No changes' : 'No settings'}
          text={needle ? 'No setting matches "' + q.trim() + '".' : changedOnly ? 'Nothing on this tab differs from the camera.' : 'This tab has no settings.'} />
      </div>
    {/each}
  </div>
</div>

<style>
  .bar {
    display: flex; align-items: flex-end; justify-content: space-between; gap: var(--s3) var(--s5); flex-wrap: wrap;
    border-bottom: 1px solid var(--line);
  }
  .tabs { display: flex; overflow-x: auto; scrollbar-width: none; max-width: 100%; scroll-snap-type: x proximity; }
  .tabs::-webkit-scrollbar { display: none; }
  .tabs button { scroll-snap-align: start; }
  .tabs.fadeR { -webkit-mask-image: linear-gradient(to right, #000 calc(100% - 44px), transparent); mask-image: linear-gradient(to right, #000 calc(100% - 44px), transparent); }
  .tabs.fadeL { -webkit-mask-image: linear-gradient(to left, #000 calc(100% - 44px), transparent); mask-image: linear-gradient(to left, #000 calc(100% - 44px), transparent); }
  .tabs.fadeL.fadeR { -webkit-mask-image: linear-gradient(to right, transparent, #000 44px, #000 calc(100% - 44px), transparent); mask-image: linear-gradient(to right, transparent, #000 44px, #000 calc(100% - 44px), transparent); }
  /* tabs keep the camera's spelling and case; the active one carries the 2 px amber rule */
  .tabs button {
    position: relative; background: none; border: 0; height: 44px; padding: 0 var(--s4); white-space: nowrap;
    color: var(--sub); font-size: var(--fs-cam); line-height: var(--lh-cam); font-stretch: 88%; font-weight: 560; letter-spacing: .06em;
    display: inline-flex; align-items: center; gap: var(--s2);
    transition: color var(--t-fast) var(--ease);
  }
  .tabs button:first-child { padding-left: 0; }
  .tabs button:hover { color: var(--ink); }
  .tabs button[aria-selected="true"] { color: var(--ink); }
  .tabs button[aria-selected="true"]::after { content: ''; position: absolute; left: var(--s4); right: var(--s4); bottom: -1px; height: 2px; background: var(--acc); }
  .tabs button:first-child[aria-selected="true"]::after { left: 0; }
  .tabs button.slot { color: var(--faint); }
  .tabs button.slot:hover, .tabs button.slot[aria-selected="true"] { color: var(--ink); }
  .tabs button.slot:first-of-type, .tabs button:not(.slot) + .slot { margin-left: var(--s4); border-left: 1px solid var(--line); }
  .tabs button:focus-visible { outline-offset: -2px; }
  .tabs.dim button { color: var(--faint); }
  .n { color: var(--sub); font-size: var(--fs-small); letter-spacing: 0; font-stretch: 100%; font-weight: 400; font-variant-numeric: tabular-nums; }
  .tools { display: flex; align-items: center; gap: var(--s3); padding-bottom: var(--s2); }
  .search { position: relative; display: flex; align-items: center; color: var(--faint); }
  .search :global(svg) { position: absolute; left: 10px; pointer-events: none; }
  .search input { padding-left: 32px; width: 200px; height: 32px; }
  .count { margin: var(--s4) 0 0; }
  .tabname { margin: var(--s6) 0 0; padding-bottom: var(--s2); color: var(--ink); }
  .group { margin: var(--s6) 0 0; padding-bottom: var(--s2); }
  .rows { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--line); }
  .bar + .list > .rows:first-child, .list > .rows:first-child { border-top: 0; }
  .row { border-bottom: 1px solid var(--line); }
  .row.open { border-bottom-color: var(--line-strong); }
  .head {
    width: 100%; display: grid; grid-template-columns: minmax(0, 1fr) auto 16px; align-items: center; column-gap: var(--s4);
    min-height: var(--row-h); background: none; border: 0; padding: 0; text-align: left; color: var(--ink);
  }
  .head:hover .chev, .head:hover .val { color: var(--ink); }
  .head:focus-visible { outline-offset: -2px; }
  .val { color: var(--sub); font-size: var(--fs-body); font-variant-numeric: tabular-nums; display: inline-flex; align-items: center; gap: var(--s2); text-align: right; transition: color var(--t-fast) var(--ease); }
  .changed .val { color: var(--ink); }
  /* open, the control below shows the value: the head does not say it again */
  .row.open .val { visibility: hidden; }
  .mark { width: 6px; height: 6px; border-radius: 50%; background: var(--ink); flex: none; }
  .chev { color: var(--faint); transition: transform var(--t) var(--ease), color var(--t-fast) var(--ease); display: grid; }
  .open .chev { transform: rotate(180deg); }
  .body { padding: var(--s1) 0 var(--s5); display: grid; gap: var(--s4); animation: open var(--t) var(--ease); scroll-margin-bottom: 72px; }
  @keyframes open { from { opacity: 0; transform: translateY(-2px); } }
  .field { display: grid; gap: var(--s2); }
  .sub { color: var(--sub); }
  .help { color: var(--sub); font-size: var(--fs-body); max-width: 60ch; }
  .help + .live { margin-top: calc(-1 * var(--s1)); }
  .ctl { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s2) var(--s4); }
  .ro { font-size: var(--fs-cam); font-stretch: 88%; font-weight: 560; letter-spacing: .06em; }
  .was { color: var(--sub); font-size: var(--fs-small); }
  .empty { padding: var(--s6) 0; }
  @media (max-width: 720px) {
    .body { scroll-margin-bottom: calc(72px + var(--tabbar-h) + env(safe-area-inset-bottom)); }
    .bar { margin: 0 calc(-1 * var(--gutter)); padding: 0 var(--gutter); }
    .tools { width: 100%; padding: var(--s2) 0; }
    .search { flex: 1; }
    .search input { width: 100%; height: 40px; }
    .tabs { order: 2; margin: 0 calc(-1 * var(--gutter)); padding: 0 var(--gutter); max-width: calc(100% + 2 * var(--gutter)); scroll-padding-inline: var(--gutter); }
  }
</style>
