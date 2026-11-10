<script>
  // A photograph or a live frame on --photo-bg: 1 px rule, radius 3, the image at or below its
  // native CSS size, never upscaled past `cap`. Nothing is drawn over the picture except what the caller puts in
  // `overlay` (the live indicator, and only that).
  //   fit 'frame' (default): the frame is the photograph's own size, shrunk to the column, never grown past native.
  //   fit 'well':  the matte fills the column at `ratio`; the image sits centred in it, at most `cap` wide (live view).
  //   status 'ok' | 'loading' (a flat --surf block at final size); dim: 0.6 opacity (stale, or the camera left)
  let {src = '', alt = '', ratio = '4 / 3', fit = 'frame', cap = null, status = 'ok', dim = false, imgEl = $bindable(),
    testid = undefined, imgattrs = {}, overlay, caption} = $props();
  let nat = $state({w: 0, h: 0});
  function loaded(e) { nat = {w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight}; }
  const maxW = $derived(fit === 'frame' ? (nat.w ? Math.min(nat.w, cap || nat.w) + 'px' : (cap ? cap + 'px' : null)) : null);
  const ar = $derived(fit === 'frame' && nat.w && nat.h ? nat.w + ' / ' + nat.h : ratio);
</script>

<figure class="pf {fit}" style:max-width={maxW}>
  {#if status === 'loading'}
    <div class="matte blank" style:aspect-ratio={ratio} aria-hidden="true"></div>
  {:else}
    <div class="matte" style:aspect-ratio={ar} class:dim>
      <img bind:this={imgEl} {src} {alt} onload={loaded} data-testid={testid} {...imgattrs}
        style:max-width={fit === 'well' && cap ? cap + 'px' : null}
        style:max-height={fit === 'well' && cap ? (cap * 3 / 4) + 'px' : null}>
      {@render overlay?.()}
    </div>
  {/if}
  {#if caption}<figcaption>{@render caption()}</figcaption>{/if}
</figure>

<style>
  .pf { margin: 0; width: 100%; min-width: 0; }
  .matte {
    position: relative; width: 100%; overflow: hidden;
    background: var(--photo-bg); border: 1px solid var(--line); border-radius: var(--r-img);
    display: grid; place-items: center;
    transition: opacity var(--t-fast) var(--ease);
  }
  .matte.blank { background: var(--surf); }
  .matte.dim { opacity: .6; }
  img { display: block; width: 100%; height: 100%; object-fit: contain; }
  .well img { width: 100%; height: 100%; }
  img:not([src]), img[src=""] { visibility: hidden; }
  figcaption {
    margin-top: var(--s2); color: var(--sub);
    font-size: var(--fs-small); line-height: var(--lh-small); font-variant-numeric: tabular-nums;
  }
</style>
