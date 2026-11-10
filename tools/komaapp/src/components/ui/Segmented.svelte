<script>
  // Segmented: two to four joined quiet buttons in camera strings, the chosen one inverted. Every
  // on/off setting is OFF | ON with this; there is no pill switch. Radio inputs underneath, so arrow keys move.
  //   options: [{value, label, disabled}]   value: the chosen value   onchange(value)
  //   live: the value whose segment turns amber while chosen (the Webcam ON segment while frames flow, nothing else)
  //   datak: a prefix for data-k attributes on the inputs (tests address options by it)
  let {name, options = [], value, onchange = () => {}, labelledby = undefined, label = undefined, disabled = false,
    small = false, live = undefined, datak = undefined, testid = undefined} = $props();
</script>

<div class="seg" class:sm={small} role="radiogroup" aria-labelledby={labelledby} aria-label={labelledby ? undefined : label}
  data-testid={testid}>
  {#each options as o (String(o.value))}
    <label class:live={live !== undefined && o.value === live}>
      <input type="radio" {name} value={String(o.value)} checked={o.value === value} disabled={disabled || o.disabled}
        data-k={datak ? datak + ':' + o.value : undefined} onchange={() => onchange(o.value)}>
      <span>{o.label}</span>
    </label>
  {/each}
</div>
