<script>
  // A detent scale, as on the camera's own screen: one tick per step, the look's stock value as the long tick, an
  // index needle, the value in the camera's words on the right. A native range input underneath: arrows step, Home and
  // End go to the ends, and the camera's option text is what a screen reader hears.
  import {opt} from '../../lib/looksWords.js';
  let {id, label, options, value, def = -1, changed = false, disabled = false, reason = '', oninput, onchange} = $props();
</script>

<div class="det" class:changed class:off={disabled} data-row={id}>
  <label class="lab camlabel" for={'d-' + id}>{label}</label>
  <div class="track" style:--n={options.length}>
    <span class="axis" aria-hidden="true">
      {#each options as _, i}<i class:def={i === def}></i>{/each}
    </span>
    <input id={'d-' + id} type="range" min="0" max={options.length - 1} step="1" {value} {disabled}
      aria-valuetext={opt(options[value])} aria-describedby={reason ? 'r-' + id : undefined}
      oninput={e => oninput?.(Number(e.currentTarget.value))} onchange={e => onchange?.(Number(e.currentTarget.value))}>
  </div>
  <span class="val camlabel num" aria-hidden="true">{#if changed}<i class="mark"></i>{/if}{opt(options[value])}</span>
  {#if reason}<p class="why small" id={'r-' + id}>{reason}</p>{/if}
</div>

<style>
  .det {
    display: grid; grid-template-columns: 104px minmax(0, 1fr) 72px; align-items: center; column-gap: var(--s3);
    min-height: var(--row-h); border-bottom: 1px solid var(--line);
  }
  .lab { color: var(--sub); }
  .val { justify-self: end; display: inline-flex; align-items: center; gap: 7px; color: var(--ink); white-space: nowrap; }
  .mark { width: 5px; height: 5px; border-radius: 50%; background: var(--ink); }
  .off .lab, .off .val, .off .track { opacity: .45; }
  .why { grid-column: 1 / -1; color: var(--sub); margin: -6px 0 10px; }
  .track { position: relative; height: 32px; }
  /* the scale: a hairline axis with one tick per step; the thumb's half width (6 px) insets both ends */
  .axis {
    position: absolute; left: 6px; right: 6px; top: 50%; height: 0; display: flex; justify-content: space-between;
    border-top: 1px solid var(--line-strong); pointer-events: none;
  }
  .axis i { display: block; width: 1px; height: 5px; margin-top: -3px; background: var(--line-strong); }
  .axis i.def { height: 11px; margin-top: -6px; background: var(--sub); }
  input {
    -webkit-appearance: none; appearance: none; position: absolute; inset: 0; width: 100%; height: 32px; margin: 0;
    background: transparent; cursor: pointer;
  }
  input:disabled { cursor: default; }
  input::-webkit-slider-runnable-track { height: 32px; background: transparent; border: 0; }
  input::-moz-range-track { height: 32px; background: transparent; border: 0; }
  /* the index: a 2 px needle in a 12 px hit area */
  input::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none; width: 12px; height: 22px; margin-top: 5px; border: 0; border-radius: 0;
    background: linear-gradient(to right, transparent 5px, var(--ink) 5px, var(--ink) 7px, transparent 7px);
  }
  input::-moz-range-thumb {
    width: 12px; height: 22px; border: 0; border-radius: 0;
    background: linear-gradient(to right, transparent 5px, var(--ink) 5px, var(--ink) 7px, transparent 7px);
  }
  input:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; border-radius: 2px; }
  @media (max-width: 720px) {
    .det { grid-template-columns: minmax(0, 1fr) auto; row-gap: 0; padding-top: var(--s2); }
    .lab { grid-row: 1; grid-column: 1; }
    .val { grid-row: 1; grid-column: 2; }
    .track { grid-row: 2; grid-column: 1 / -1; height: 40px; }
    .why { grid-row: 3; margin-top: -2px; }
    input { height: 40px; }
    input::-webkit-slider-runnable-track { height: 40px; }
    input::-moz-range-track { height: 40px; }
    input::-webkit-slider-thumb { margin-top: 9px; }
  }
</style>
