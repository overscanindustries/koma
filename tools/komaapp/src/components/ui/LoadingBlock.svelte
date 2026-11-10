<script>
  // A flat --surf block at the final size of what is loading (a photo frame, a grid cell). No shimmer, no icon.
  // ratio: CSS aspect-ratio, e.g. '4 / 3'. The label goes in a StatusLine or caption beside it, never on it.
  let {ratio = '4 / 3', width = '100%', radius = 'var(--r-img)'} = $props();
</script>

<div class="blk" style:aspect-ratio={ratio} style:width style:border-radius={radius} aria-hidden="true"></div>

<style>
  .blk { background: var(--surf); border: 1px solid var(--line); max-width: 100%; }
</style>
