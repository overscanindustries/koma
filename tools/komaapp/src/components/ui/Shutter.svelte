<script>
  // The shutter: an amber disc, 72 px on desktop and 56 px on the phone (size), the app's only amber control, on no panel. A 1 px amber
  // ring at a 4 px gap appears only while an interval runs; each frame of the interval dips the ring's opacity for
  // 120 ms (`beat` changes per frame). Space and Enter take a photo on Shoot (the view binds Space).
  let {disabled = false, onpress = () => {}, ring = false, beat = 0, label = 'Take photo', testid = 'shutter', size = 56} = $props();
  let dip = $state(false);
  let last = null;
  $effect(() => {
    const b = beat;
    if (last === null || b === last) { last = b; return; }
    last = b;
    if (!ring) return;
    dip = true;
    const t = setTimeout(() => { dip = false; }, 120);
    return () => clearTimeout(t);
  });
</script>

<button class="sh" class:ring class:dip style:--d={size + 'px'} type="button" onclick={onpress} {disabled} aria-label={label} data-testid={testid}>
  <span class="disc" aria-hidden="true"></span>
</button>

<style>
  .sh {
    position: relative; flex: none;
    width: var(--d, 56px); height: var(--d, 56px); padding: 0; border: 0; border-radius: 50%; background: none;
  }
  .disc {
    position: absolute; inset: 0; border-radius: 50%; background: var(--acc);
    transition: transform var(--t-fast) var(--ease), opacity var(--t-fast) var(--ease);
  }
  .sh:active:not(:disabled) .disc { transform: translateY(1px); }
  /* cannot fire: an unlit key (a dimmed amber reads as mud on the dark ground) */
  .sh:disabled:not(.ring) .disc { background: var(--raise); box-shadow: inset 0 0 0 1px var(--line-strong); }
  .sh:disabled { cursor: default; }
  .sh::after {
    content: ''; position: absolute; inset: -5px; border-radius: 50%; border: 1px solid var(--acc);
    opacity: 0; transition: opacity var(--t-fast) var(--ease);
  }
  .sh.ring::after { opacity: 1; }
  .sh.ring.dip::after { opacity: .2; }
  .sh:focus-visible { outline: 2px solid var(--ink); outline-offset: 8px; border-radius: 50%; }
</style>
