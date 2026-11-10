<script>
  // One look drawn on the photo, in a 4:3 frame on the photo matte. The photo is contained, never drawn above its
  // native size (a smaller photo sits centred in the matte), and rendered at the screen's pixel density up to the
  // photo's own pixels. While it renders: a flat block at the final size. Drawn on what the gallery draws on: the last
  // photo, or the sample when that photo is black and white (looks.svelte.js gallerySrc).
  import {requestRender, sourceSize, gallerySrc} from '../../lib/looks.svelte.js';
  let {st = null, ghost = undefined, urgent = false} = $props();

  let box = $state(0);
  let cv = $state();
  let ready = $state(false);
  const dpr = Math.min(3, Math.max(1, (typeof devicePixelRatio === 'number' && devicePixelRatio) || 1));

  const src = $derived(gallerySrc());
  const size = $derived.by(() => {
    const s = sourceSize(src);
    if (!s || !box) return null;
    const k = Math.min(box / s.w, (box * 3) / 4 / s.h, 1);
    const cw = Math.max(1, Math.round(s.w * k)), ch = Math.max(1, Math.round(s.h * k));
    const pw = Math.max(1, Math.min(s.w, Math.round(cw * dpr))), ph = Math.max(1, Math.round((pw * s.h) / s.w));
    return {cw, ch, pw, ph};
  });

  $effect(() => {
    const z = size, c = cv;
    if (!z || !c) return;
    ready = false;
    return requestRender({st, ghost, src, w: z.pw, h: z.ph, urgent, done: img => {
      if (c.width !== z.pw) c.width = z.pw;
      if (c.height !== z.ph) c.height = z.ph;
      c.getContext('2d').putImageData(img, 0, 0);
      ready = true;
    }});
  });
</script>

<div class="frame" class:ready bind:clientWidth={box}>
  {#if size}<canvas bind:this={cv} style:width={size.cw + 'px'} style:height={size.ch + 'px'} aria-hidden="true"></canvas>{/if}
</div>

<style>
  .frame {
    aspect-ratio: 4 / 3; width: 100%;
    display: grid; place-items: center; overflow: hidden;
    background: var(--surf); border-radius: var(--r-img);
    border: 1px solid var(--frame-line, var(--line));
    transition: border-color var(--t-fast) var(--ease);
  }
  .frame.ready { background: var(--photo-bg); }
  canvas { display: block; visibility: hidden; }
  .ready canvas { visibility: visible; }
</style>
