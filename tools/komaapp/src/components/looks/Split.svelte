<script>
  // Before and after on one photo: the photo as shot on the left of a 1 px divider, the look on the right. Drag the
  // photo or the index mark under it; the mark is a slider for the keyboard (arrows, Home, End). Nothing else is
  // drawn over the picture. Drawn on the last photo whenever there is one, black and white or not (looks.svelte.js
  // editorSrc): the photo as shot against the look is the point here.
  import {LK, requestRender, sourceSize, sourcePixels, editorSrc} from '../../lib/looks.svelte.js';
  import {W} from '../../lib/looksWords.js';
  let {st, name} = $props();

  let box = $state(0);
  let x = $state(50);
  let before = $state(), after = $state();
  let ready = $state(false);
  const dpr = Math.min(3, Math.max(1, (typeof devicePixelRatio === 'number' && devicePixelRatio) || 1));

  const src = $derived(editorSrc());
  const size = $derived.by(() => {
    const s = sourceSize(src);
    if (!s || !box) return null;
    const inner = Math.max(1, box - 2), ih = Math.round((inner * 3) / 4);    // inside the frame's 1 px border
    const k = Math.min(inner / s.w, ih / s.h, 1);
    const cw = Math.max(1, Math.round(s.w * k)), ch = Math.max(1, Math.round(s.h * k));
    const pw = Math.max(1, Math.min(s.w, Math.round(cw * dpr))), ph = Math.max(1, Math.round((pw * s.h) / s.w));
    return {cw, ch, pw, ph, fh: ih + 2, off: Math.round((box - cw) / 2)};
  });

  function paint(c, img, z) {
    if (c.width !== z.pw) c.width = z.pw;
    if (c.height !== z.ph) c.height = z.ph;
    c.getContext('2d').putImageData(img, 0, 0);
  }
  $effect(() => {
    const z = size, c = before;
    if (!z || !c) return;
    const px = sourcePixels(z.pw, z.ph, src);
    if (px) paint(c, px, z);
  });
  $effect(() => {
    const z = size, c = after;
    if (!z || !c) return;
    return requestRender({st, src, w: z.pw, h: z.ph, urgent: true, done: img => { paint(c, img, z); ready = true; }});
  });

  // dragging
  let img = $state();
  let dragging = false;
  function at(e) {
    const r = img.getBoundingClientRect();
    x = Math.round(Math.max(0, Math.min(100, ((e.clientX - r.left) / r.width) * 100)) * 10) / 10;
  }
  function down(e) {
    if (e.button !== undefined && e.button !== 0) return;
    dragging = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    at(e);
  }
  function move(e) { if (dragging) at(e); }
  function up() { dragging = false; }
  function key(e) {
    const step = {ArrowLeft: -5, ArrowDown: -5, ArrowRight: 5, ArrowUp: 5, PageDown: -20, PageUp: 20}[e.key];
    if (step !== undefined) x = Math.max(0, Math.min(100, Math.round(x + step)));
    else if (e.key === 'Home') x = 0;
    else if (e.key === 'End') x = 100;
    else return;
    e.preventDefault();
  }
</script>

<figure class="split" bind:clientWidth={box}>
  <div class="frame" class:ready style:height={size ? size.fh + 'px' : undefined}>
    {#if size}
      <div class="img" bind:this={img} style:width={size.cw + 'px'} style:height={size.ch + 'px'}
        onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up} role="presentation">
        <canvas bind:this={before} aria-hidden="true"></canvas>
        <canvas bind:this={after} class="after" style:clip-path={'inset(0 0 0 ' + x + '%)'} aria-hidden="true"></canvas>
        <span class="line" style:left={x + '%'} aria-hidden="true"></span>
      </div>
    {/if}
  </div>
  <figcaption class="cap" style:padding-left={size ? size.off + 'px' : undefined} style:padding-right={size ? size.off + 'px' : undefined}>
    <span class="small side">{LK.src === 'photo' ? W.asShot : W.sample}</span>
    {#if size}
      <span class="grip" role="slider" tabindex="0" aria-label={W.splitLabel} aria-valuemin="0" aria-valuemax="100"
        aria-valuenow={Math.round(x)} aria-valuetext={Math.round(x) + '%'} style:left={size.off + (size.cw * x) / 100 + 'px'}
        onkeydown={key} onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up}
        data-testid="split-grip"><i></i></span>
    {/if}
    <span class="side r"><span class="camlabel">{name}</span><span class="micro">{W.approx}</span></span>
  </figcaption>
</figure>

<style>
  .split { margin: 0; }
  .frame {
    display: grid; place-items: center; overflow: hidden; width: 100%; aspect-ratio: 4 / 3;
    background: var(--surf); border: 1px solid var(--line); border-radius: var(--r-img);
  }
  .frame.ready { background: var(--photo-bg); }
  .img { position: relative; touch-action: pan-y; cursor: ew-resize; user-select: none; }
  canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
  /* the one mark drawn on a photograph: light on the dark photo matte in both themes. --photo-mark is the shared
     token (white in both themes); the fallback is that same white */
  .line { position: absolute; top: 0; bottom: 0; width: 1px; margin-left: -0.5px; background: var(--photo-mark, #fff); opacity: .9; pointer-events: none; }
  .cap {
    position: relative; display: flex; justify-content: space-between; align-items: baseline; gap: 16px;
    padding-top: 18px;
  }
  .side { color: var(--sub); }
  .r { display: inline-flex; align-items: baseline; gap: 10px; }
  .r .camlabel { color: var(--ink); }
  .grip {
    position: absolute; top: 0; width: 44px; height: 44px; margin-left: -22px; z-index: 1;
    display: grid; justify-items: center; align-content: start; cursor: ew-resize; touch-action: none;
    border-radius: var(--r-ctl);
  }
  /* an index mark under the divider, as on a camera's scale */
  .grip i { display: block; width: 0; height: 0; margin-top: 2px; border-left: 5px solid transparent;
    border-right: 5px solid transparent; border-bottom: 7px solid var(--ink); }
  .grip:focus-visible { outline: 2px solid var(--ink); outline-offset: -2px; }
</style>
