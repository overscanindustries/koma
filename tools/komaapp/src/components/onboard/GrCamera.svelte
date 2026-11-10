<script>
  // The GR DIGITAL as line art (lib/grModel.js), in the tradition of a Japanese camera manual: two stroke weights,
  // no fills but the LCD, the instructed control in amber, numbered callouts. README.md in this folder documents it.
  //   <GrCamera view="back" highlight={['play']} action="hold" hold={3} callouts={[{id: 'play', n: 4}]} />
  import {VIEWS, controlOf} from '../../lib/grModel.js';

  let {
    view = 'back',            // 'front' | 'back' | 'top'
    highlight = [],           // control ids drawn in amber (at most two are drawn)
    action = null,            // 'press' | 'turn' | 'slide' | 'hold' | 'power' | 'extend' | null
    hold = 0,                 // seconds, for action 'hold': the ring fills over this time, the count sits beside it
    lens = 'retracted',       // 'retracted' | 'extended': the lens at rest
    callouts = [],            // [{id, n, dir?: 'left'|'right'|'up-left'|'up-right'|'down-left'|'down-right', extra?: mm,
                              //   at?: [x, y] mm, a point on the control other than its centre}]
    label = '',               // accessible name; the default names the view and the highlighted controls
    play = 0,                 // change it to run the animation again
    reduced = null,           // null follows prefers-reduced-motion; true / false force it (gallery)
    maxWidth = 360,
    testid = undefined,
  } = $props();

  const full = $derived(VIEWS[view] || VIEWS.back);
  // The top view keeps room above the front only when the lens is (or becomes) extended.
  const model = $derived(full.vbTight && !(lens === 'extended' || action === 'power' || action === 'extend') ? {...full, vb: full.vbTight} : full);
  const hl = $derived(highlight.slice(0, 2).map(id => controlOf(view, id)).filter(Boolean));
  const hlIds = $derived(new Set(hl.map(c => c.id)));

  let mq = $state(typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches);
  $effect(() => {
    if (typeof matchMedia !== 'function') return;
    const q = matchMedia('(prefers-reduced-motion: reduce)');
    const on = e => { mq = e.matches; };
    q.addEventListener('change', on);
    return () => q.removeEventListener('change', on);
  });
  const still = $derived(reduced === null ? mq : reduced);
  // Reduced motion: an action whose end state matters (a hold, the lens coming out) becomes two numbered frames side
  // by side; a press, turn or slide stays one still frame, the highlighted control being the instruction.
  const twoFrames = $derived(still && (action === 'hold' || action === 'power' || action === 'extend'));

  // ---------------------------------------------------------------- size and callouts
  // Callouts, dots and the press offset are true pixels at any width: 16 px circle, 2 px dot, 1 px press. The hold count
  // sits under its callout circle (COUNT_PX of room).
  const R = 8, GAP = 6, SIDE_PX = GAP + 2 * R + 3, COUNT_PX = 15;
  const STAGGER_MM = 3;   // the largest `extra` a figure uses (two circles side by side above the top plate)
  const BODY_TOP = {back: -3.6, front: -3.6, top: -1.4};
  const BODY_BOTTOM = {back: 58, front: 58, top: 25};
  let figW = $state(0);
  const items = $derived(callouts.map(c => {
    const ctl = controlOf(view, c.id);
    if (!ctl || !ctl.at) return null;
    const [tx, ty] = c.at || ctl.at;
    return {...c, tx, ty, dir: c.dir || (tx < model.vb[0] + model.vb[2] / 2 ? 'left' : 'right')};
  }).filter(Boolean));
  // the held control's callout carries the count
  const holdIds = $derived(new Set(action === 'hold' && hold > 0 ? hl.filter(c => c.ring).map(c => c.id) : []));
  let phoneMq = $state(typeof matchMedia === 'function' && matchMedia('(max-width: 720px)').matches);
  $effect(() => {
    if (typeof matchMedia !== 'function') return;
    const q = matchMedia('(max-width: 720px)');
    const on = e => { phoneMq = e.matches; };
    q.addEventListener('change', on);
    return () => q.removeEventListener('change', on);
  });
  const frameW = $derived(twoFrames && phoneMq ? (figW - 16) / 2 : figW);
  // Room for callouts on both sides, always: every view is 112 mm wide, so every figure of a sequence (and of a page)
  // draws the camera at one scale, as a manual spread does.
  const sidePx = 2 * SIDE_PX;
  const scale = $derived(frameW > sidePx + 40 ? (frameW - sidePx) / model.vb[2] : 3);   // px per mm
  const mm = $derived(1 / scale);
  const simple = $derived(scale < 1.6);   // knurling turns to noise below about 170 px wide

  const placed = $derived.by(() => {
    const out = [];
    const [x0, , w] = model.vb;
    const rc = R / scale, gap = GAP / scale, step = 2 * rc + 4 / scale, countH = COUNT_PX / scale;
    for (const side of ['left', 'right']) {
      const s = side === 'right' ? 1 : -1;
      const row = items.filter(c => c.dir === side).sort((a, b) => a.ty - b.ty);
      let last = -Infinity, lastCount = false;
      for (const c of row) {
        const cy = Math.max(c.ty, last + step + (lastCount ? countH : 0));
        const count = holdIds.has(c.id);
        last = cy; lastCount = count;
        const cx = side === 'right' ? x0 + w + gap + rc : x0 - gap - rc;
        const edge = cx - s * rc;
        // A circle pushed below its control leaves at 45 degrees up to the control's height, then runs level into it.
        // In order of height that never crosses another leader (a level shoulder at the circle would).
        const dy = cy - c.ty, bx = edge - s * dy;
        const d = dy > 0 && s * (bx - c.tx) > 0 ? `M${edge} ${cy}L${bx} ${c.ty}L${c.tx} ${c.ty}` : `M${edge} ${cy}L${c.tx} ${c.ty}`;
        out.push({...c, cx, cy, d, count});
      }
    }
    for (const c of items.filter(c => c.dir.includes('-'))) {
      const sy = c.dir.startsWith('up') ? -1 : 1, sx = c.dir.endsWith('left') ? -1 : 1;
      // 45 degrees, until the circle clears the body's silhouette
      const clear = sy < 0 ? c.ty - (BODY_TOP[view] - gap - rc) : (BODY_BOTTOM[view] + gap + rc) - c.ty;
      const L = Math.max(clear, 4) + (c.extra || 0);
      const cx = c.tx + sx * L, cy = c.ty + sy * L;
      out.push({...c, cx, cy, d: `M${cx - sx * rc * 0.7071} ${cy - sy * rc * 0.7071}L${c.tx} ${c.ty}`, count: holdIds.has(c.id)});
    }
    return out;
  });
  const vb = $derived.by(() => {
    const [x0, y0, w, h] = model.vb;
    const side = SIDE_PX / scale, rc = (R + 1) / scale, gap = GAP / scale;
    // The room an upward callout needs (one staggered circle included) is kept on every figure of the view, so the body
    // sits at one height from screen to screen whether or not a callout uses it.
    let t = Math.max(0, y0 - (BODY_TOP[view] - gap - R / scale - rc - STAGGER_MM)), b = 0;
    for (const c of placed) {
      t = Math.max(t, y0 - (c.cy - rc));
      b = Math.max(b, c.cy + rc + (c.count ? COUNT_PX / scale : 0) - (y0 + h));
    }
    return [x0 - side, y0 - t, w + 2 * side, h + t + b];
  });

  // ---------------------------------------------------------------- animation state
  // The still drawing shows first; after a beat the action runs twice, then holds still. Nothing moves at rest.
  let phase = $state('rest');
  let ext = $state(false);
  $effect(() => {
    void play; void action; void view; void highlight.join();
    phase = 'rest';
    ext = lens === 'extended';
    if (still || !action) return;
    const t1 = setTimeout(() => { phase = 'go'; }, 260);
    const t2 = action === 'power' || action === 'extend' ? setTimeout(() => { ext = true; }, action === 'power' ? 760 : 380) : null;
    return () => { clearTimeout(t1); if (t2) clearTimeout(t2); };
  });

  function kindFor(c) {
    if (!action || action === 'hold' || action === 'extend') return '';
    if (action === 'power') return c.id === 'power' ? 'press' : '';
    if (action === 'turn' || action === 'slide') return c.anim === 'turn' ? 'turn' : c.anim === 'slide' ? 'slide' : 'press';
    return 'press';
  }

  const VIEW_NAME = {front: 'Front of the GR DIGITAL', back: 'Back of the GR DIGITAL', top: 'Top of the GR DIGITAL'};
  const titleText = $derived(label || VIEW_NAME[view] + (hl.length ? '. ' + hl.map(c => c.name).join(' and ') + ' highlighted.' : '.'));
  const uid = 'gr' + Math.random().toString(36).slice(2, 8);
  const fs = $derived(11 / scale);   // --fs-micro in mm
  const frames = $derived(twoFrames ? [{n: 1, after: false}, {n: 2, after: true}] : [{n: 0, after: null}]);
  const grows = $derived(action === 'power' || action === 'extend');
</script>

<figure class="gr" class:two={twoFrames} style:--gr-max="{maxWidth}px"
  bind:clientWidth={figW} data-testid={testid} data-view={view}>
  {#each frames as fr (fr.n)}
    {@const isExt = fr.after === null ? ext : grows ? fr.after : lens === 'extended'}
    <div class="frame">
      <svg viewBox={vb.join(' ')} role="img" aria-labelledby={uid + fr.n} style:--mm={mm}
        class:go={phase === 'go' && fr.after === null} class:after={fr.after === true}>
        <title id={uid + fr.n}>{titleText}{fr.n ? ' Frame ' + fr.n + ' of 2.' : ''}</title>
        <g class="body">
          {#each model.body as p}
            {#if !p.group || (p.group === 'extended') === isExt}
              <path d={p.d} class={p.w === 'major' ? 'maj' : 'det'} />
            {/if}
          {/each}
        </g>
        {#each model.controls as c (c.id)}
          {@const on = hlIds.has(c.id)}
          {@const kind = on ? kindFor(c) : ''}
          <g class="ctl {kind ? 'k-' + kind : ''}" class:on data-control={c.id}
            style:--ox={c.origin ? (c.origin[0] - vb[0]) + 'px' : null} style:--oy={c.origin ? (c.origin[1] - vb[1]) + 'px' : null}>
            {#each c.parts as p}
              {#if !(p.spin && simple)}
                <path d={p.d} class={p.w === 'major' ? 'maj' : 'det'} class:lcd={p.lcd} class:spin={p.spin} />
              {/if}
            {/each}
            {#if on}<path d={c.hl} class="hl" />{/if}
          </g>
        {/each}

        {#if action === 'hold' && hold > 0}
          {#each hl.filter(c => c.ring) as c (c.id)}
            {@const C = 2 * Math.PI * c.ring}
            <circle class="track" cx={c.at[0]} cy={c.at[1]} r={c.ring} />
            <circle class="ring" class:full={fr.after === true} cx={c.at[0]} cy={c.at[1]} r={c.ring} style:--c={C}
              style:--hold="{hold}s" stroke-dasharray={C} transform="rotate(-90 {c.at[0]} {c.at[1]})" />
          {/each}
        {/if}

        {#each placed as c (c.id + ':' + c.n)}
          <g class="callout">
            <path d={c.d} />
            <circle class="dot" cx={c.tx} cy={c.ty} r={mm} />
            <circle class="ball" cx={c.cx} cy={c.cy} r={R * mm} />
            <text x={c.cx} y={c.cy} font-size={fs} text-anchor="middle" dominant-baseline="central">{c.n}</text>
            <!-- the hold count, under the held control's circle, outside the body (U+2009): on the drawing and on the
                 after frame; the before frame has nothing held yet, and its empty ring track marks the start -->
            {#if c.count && fr.after !== false}<text class="count" x={c.cx} y={c.cy + (R + 3) * mm} font-size={fs}
              text-anchor="middle" dominant-baseline="hanging">{hold + '\u2009s'}</text>{/if}
          </g>
        {/each}
      </svg>
      {#if fr.n}<span class="fn" aria-hidden="true">{fr.n}</span>{/if}
    </div>
  {/each}
</figure>

<style>
  .gr { margin: 0; width: 100%; max-width: var(--gr-max); display: grid; gap: 12px; }
  /* reduced motion: the two numbered frames stack in the figure column; side by side on a phone */
  .gr.two { gap: 24px; }
  @media (max-width: 720px) { .gr.two { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; } }
  .frame { display: grid; gap: 6px; justify-items: start; min-width: 0; }
  svg { display: block; width: 100%; height: auto; overflow: visible; }
  path, circle { vector-effect: non-scaling-stroke; fill: none; stroke-linecap: butt; stroke-linejoin: round; }
  .maj { stroke: var(--sub); stroke-width: 1.25px; }
  .det { stroke: var(--sub); stroke-width: .75px; }
  .lcd { fill: var(--sunk); }
  .hl { stroke: var(--acc); stroke-width: 1.5px; }

  /* press 1 px, turn 15 degrees, slide 2 px: 120 ms each way, twice, then still */
  .ctl, .spin { transform-box: view-box; }
  .go .k-press { animation: gr-press var(--t-fast) var(--ease, ease) 2; }
  .go .k-turn .spin { transform-origin: var(--ox) var(--oy); animation: gr-turn var(--t-fast) var(--ease, ease) 2; }
  .go .k-slide .spin { animation: gr-slide var(--t-fast) var(--ease, ease) 2; }
  .after .k-press { transform: translateY(calc(var(--mm) * 1px)); }
  .after .k-turn .spin { transform-origin: var(--ox) var(--oy); transform: rotate(15deg); }
  .after .k-slide .spin { transform: translateX(calc(var(--mm) * 2px)); }
  @keyframes gr-press { 50% { transform: translateY(calc(var(--mm) * 1px)); } }
  @keyframes gr-turn { 50% { transform: rotate(15deg); } }
  @keyframes gr-slide { 50% { transform: translateX(calc(var(--mm) * 2px)); } }

  /* hold: a 1 px ring that fills over the hold time, the count beside it */
  .track { stroke: var(--line); stroke-width: 1px; }
  .ring { stroke: var(--acc); stroke-width: 1px; stroke-dashoffset: calc(var(--c) * 1px); }
  .go .ring { animation: gr-fill var(--hold) linear forwards; }
  .ring.full { stroke-dashoffset: 0; }
  @keyframes gr-fill { to { stroke-dashoffset: 0; } }
  .count { fill: var(--ink); font-family: var(--font); font-weight: 560; font-variant-numeric: tabular-nums; }

  .callout path { stroke: var(--ink); stroke-width: 1px; }
  .callout .dot { fill: var(--ink); stroke: none; }
  .callout .ball { stroke: var(--ink); stroke-width: 1px; fill: var(--bg); }
  .callout text { fill: var(--ink); font-family: var(--font); font-weight: 560; font-stretch: 88%; font-variant-numeric: tabular-nums; }

  .fn { font-size: var(--fs-micro, 11px); line-height: 16px; font-weight: 560; font-stretch: 88%; letter-spacing: .06em;
    color: var(--sub); font-variant-numeric: tabular-nums; }

  @media (prefers-reduced-motion: reduce) {
    .go .k-press, .go .k-turn .spin, .go .k-slide .spin, .go .ring { animation: none; }
  }
</style>
