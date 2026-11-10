<script>
  // The chosen look drawn on the bundled sample photo, beside the list on Pick a look. The same try-on the Looks gallery
  // runs (lib/looksRender.js: makePlan, render; APPROXIMATE until measured against the camera), drawn here directly: the
  // Looks area's store reads the newest photo from the camera, and setup shows every newcomer the same picture. The
  // sample counts as made with STANDARD; a black and white sample as the stock B&W setting (looks.svelte.js afterSource).
  //   block  the camera's look store (onboardLook.js readStore), or null: the looks at their stock values
  //   id     the look to draw
  import {onMount} from 'svelte';
  import * as LS from '../../lib/looksStore.js';
  import {makePlan, render, colorOf, ghostState} from '../../lib/looksRender.js';
  import sampleUrl from '../../assets/looks/sample.jpg';
  let {block = null, id = 0, name = ''} = $props();

  let box = $state(0), cv = $state(), ready = $state(false), failed = $state(false);
  let src = null;          // {img, w, h, data, mono, cast, source}
  let srcRev = $state(0);
  const dpr = Math.min(2, Math.max(1, (typeof devicePixelRatio === 'number' && devicePixelRatio) || 1));

  async function loadSample() {
    try {
      const r = await fetch(sampleUrl);
      if (!r.ok) throw new Error('http ' + r.status);
      const blob = await r.blob();
      const img = typeof createImageBitmap === 'function' ? await createImageBitmap(blob)
        : await new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = no; i.src = URL.createObjectURL(blob); });
      src = {img, w: img.width, h: img.height, px: new Map()};
      const t = pixels(160, Math.max(1, Math.round(160 * src.h / src.w)));
      const c = colorOf(t.data, t.width * t.height);
      src.mono = c.mono; src.cast = c.mono && c.cast;
      src.source = c.mono ? ghostState(3, null) : null;
      srcRev++;
    } catch (e) { failed = true; }
  }
  function pixels(w, h) {
    const k = w + 'x' + h;
    if (src.px.has(k)) return src.px.get(k);
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const g = c.getContext('2d', {willReadFrequently: true});
    g.imageSmoothingEnabled = true; g.imageSmoothingQuality = 'high';
    g.drawImage(src.img, 0, 0, w, h);
    const d = g.getImageData(0, 0, w, h);
    src.px.set(k, d);
    return d;
  }
  onMount(loadSample);

  const size = $derived.by(() => {
    void srcRev;
    if (!src || !box) return null;
    const k = Math.min(box / src.w, (box * 3) / 4 / src.h, 1);
    const cw = Math.max(1, Math.round(src.w * k)), ch = Math.max(1, Math.round(src.h * k));
    const pw = Math.max(1, Math.min(src.w, Math.round(cw * dpr))), ph = Math.max(1, Math.round((pw * src.h) / src.w));
    return {cw, ch, pw, ph};
  });
  const target = $derived(!id ? null : block && LS.storeOk(block) ? LS.lookState(block, id) : LS.stockState(id, false));

  $effect(() => {
    const z = size, c = cv, t = target;
    if (!z || !c || !t) return;
    const frame = requestAnimationFrame(() => {
      const px = pixels(z.pw, z.ph);
      const out = new ImageData(z.pw, z.ph);
      render(makePlan(t, src.source, src.mono, src.cast), px.data, out.data, z.pw, z.ph);
      if (c.width !== z.pw) c.width = z.pw;
      if (c.height !== z.ph) c.height = z.ph;
      c.getContext('2d').putImageData(out, 0, 0);
      ready = true;
    });
    return () => cancelAnimationFrame(frame);
  });
</script>

{#if !failed}
  <figure class="lp" aria-label={name ? name + ' on a sample photo' : 'A sample photo'} data-testid="look-preview">
    <div class="frame" class:ready bind:clientWidth={box}>
      {#if size}<canvas bind:this={cv} style:width={size.cw + 'px'} style:height={size.ch + 'px'} aria-hidden="true"></canvas>{/if}
    </div>
    <figcaption class="micro">Approximate</figcaption>
  </figure>
{/if}

<style>
  .lp { margin: 0; width: 100%; display: grid; gap: 8px; }
  .frame {
    aspect-ratio: 4 / 3; width: 100%; display: grid; place-items: center; overflow: hidden;
    background: var(--surf); border-radius: var(--r-img); border: 1px solid var(--line);
  }
  .frame.ready { background: var(--photo-bg); }
  canvas { display: block; visibility: hidden; }
  .ready canvas { visibility: visible; }
</style>
