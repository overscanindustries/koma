<script>
  // The control for one map field: a segmented control for up to four short options, a select for
  // longer lists, OFF | ON per bit of a bitfield (there is no pill switch), a stepper for numbers. Writes go through
  // settings.setField (the engine's codec).
  import {uintOf, renderValue} from '../engine/engine.js';
  import {S, B, setField} from '../lib/settings.svelte.js';
  import {clean} from '../lib/words.js';
  let {f, labelId, disabled = false} = $props();

  const cur = $derived.by(() => { void S.rev; return B.blk ? uintOf(f, B.blk) : 0n; });
  const keys = $derived(f.type === 'enum' ? Object.keys(f.values).map(Number).sort((a, b) => a - b) : []);
  const known = $derived(f.type === 'enum' && Object.prototype.hasOwnProperty.call(f.values, String(cur)));
  const segment = $derived(f.type === 'enum' && keys.length > 0 && keys.length <= 4
    && keys.reduce((n, k) => n + clean(f.values[k]).length, 0) <= 26 && known);
  const lo = $derived(f.range ? f.range[0] : f.type === 'i8' ? -128 : 0);
  const hi = $derived(f.range ? f.range[1] : f.type === 'i8' ? 127 : Math.pow(2, f.size * 8) - 1);
  const num = $derived.by(() => { void S.rev; return f.type === 'enum' || f.type === 'bitfield' || !B.blk ? 0 : Number(renderValue(f, B.blk)); });
  const bits = $derived(f.type === 'bitfield' ? Object.keys(f.bits).map(Number).sort((a, b) => a - b) : []);
  const set = t => setField(f, t);
  function bit(i, on) { let x = uintOf(f, B.blk); const b = 1n << BigInt(i); x = on ? x | b : x & ~b; set('0x' + x.toString(16)); }
  const isOn = i => ((cur >> BigInt(i)) & 1n) === 1n;
</script>

{#if f.type === 'enum' && segment}
  <div class="seg" role="radiogroup" aria-labelledby={labelId}>
    {#each keys as k}
      <label><input type="radio" name={'c-' + f.name} value={String(k)} checked={BigInt(k) === cur} {disabled}
        data-k={f.name + ':' + k} onchange={() => set(String(k))}><span>{clean(f.values[k])}</span></label>
    {/each}
  </div>
{:else if f.type === 'enum'}
  <select class="select" aria-labelledby={labelId} data-k={f.name} {disabled} value={String(cur)} onchange={e => set(e.currentTarget.value)}>
    {#each keys as k}<option value={String(k)}>{clean(f.values[k])}</option>{/each}
    {#if !known}<option value={String(cur)} disabled>Not listed</option>{/if}
  </select>
{:else if f.type === 'bitfield'}
  <div class="bits" role="group" aria-labelledby={labelId}>
    {#each bits as i}
      {@const id = 'b-' + f.name + '-' + i}
      <div class="bit">
        <span class="cam" id={id}>{clean(f.bits[i])}</span>
        <div class="seg sm" role="radiogroup" aria-labelledby={id}>
          <label><input type="radio" name={id} checked={!isOn(i)} {disabled} data-k={f.name + ':' + i + ':0'} onchange={() => bit(i, false)}><span>OFF</span></label>
          <label><input type="radio" name={id} checked={isOn(i)} {disabled} data-k={f.name + ':' + i} onchange={() => bit(i, true)}><span>ON</span></label>
        </div>
      </div>
    {/each}
  </div>
{:else}
  <div class="stepper" role="group" aria-labelledby={labelId}>
    <button type="button" aria-label="Decrease" disabled={disabled || num <= lo} data-k={f.name + ':-'} onclick={() => set(String(Math.max(lo, num - 1)))}>−</button>
    <input class="num" type="text" inputmode="numeric" value={String(num)} aria-labelledby={labelId} {disabled} data-k={f.name}
      onchange={e => set(e.currentTarget.value)}>
    <button type="button" aria-label="Increase" disabled={disabled || num >= hi} data-k={f.name + ':+'} onclick={() => set(String(Math.min(hi, num + 1)))}>+</button>
    {#if f.unit}<span class="unit">{f.unit}</span>{/if}
  </div>
{/if}

<style>
  .select { min-width: 180px; max-width: 100%; }
  .bits { display: grid; width: 100%; max-width: 420px; border-top: 1px solid var(--line); }
  .bit { display: flex; align-items: center; justify-content: space-between; gap: var(--s4); min-height: var(--row-h); border-bottom: 1px solid var(--line); }
  .stepper {
    display: inline-flex; align-items: center; height: var(--ctl-h);
    border: 1px solid var(--line-strong); border-radius: var(--r-ctl); background: var(--raise);
  }
  .stepper button {
    width: 36px; height: 100%; border: 0; background: none; color: var(--sub); font-size: 17px; line-height: 1;
    transition: color var(--t-fast) var(--ease);
  }
  .stepper button:hover:not(:disabled) { color: var(--ink); }
  .stepper button:disabled { opacity: .45; }
  .stepper input {
    width: 56px; height: 100%; text-align: center; border: 0; border-left: 1px solid var(--line); border-right: 1px solid var(--line);
    background: var(--sunk); font-size: var(--fs-body);
  }
  .unit { padding: 0 10px; color: var(--sub); font-size: var(--fs-small); }
</style>
