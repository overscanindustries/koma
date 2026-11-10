<script>
  // One option of a radio list: an ink ring and dot, the option's name, one sentence, and a
  // note on the right. A native radio underneath, so arrow keys, focus and screen readers work as for any radio group.
  // Wrap the rows in a <fieldset> with a <legend>; give every row of the group the same `name`.
  //   <RadioRow name="m-layout" value="1" checked label="5-TAB" text="Koma's own five tabs." cam onchange={...} />
  let {name, value, checked = false, disabled = false, label, text = '', note = '', cam = false, onchange = () => {},
    testid = undefined} = $props();
  const id = $derived('rr-' + name + '-' + value);
</script>

<label class="opt" class:off={disabled} for={id}>
  <input {id} type="radio" {name} {value} {checked} {disabled} data-k={testid} onchange={onchange}>
  <span class="ind" aria-hidden="true"></span>
  <span class="name" class:cam>{label}</span>
  <span class="d">{text}</span>
  {#if note}<span class="now small">{note}</span>{/if}
</label>

<style>
  .opt {
    position: relative; display: grid; grid-template-columns: 16px minmax(120px, 160px) minmax(0, 1fr) auto; align-items: center;
    column-gap: var(--s3); min-height: var(--row-h); padding: var(--s2) 0; border-bottom: 1px solid var(--line); cursor: pointer;
  }
  .opt input { position: absolute; opacity: 0; width: 1px; height: 1px; margin: 0; }
  .ind {
    width: 14px; height: 14px; border-radius: 50%; border: 1px solid var(--line-strong); background: var(--raise);
    display: grid; place-items: center; transition: border-color var(--t-fast) var(--ease);
  }
  .ind::after { content: ''; width: 6px; height: 6px; border-radius: 50%; background: transparent; transition: background-color var(--t-fast) var(--ease); }
  .opt input:checked ~ .ind { border-color: var(--ink); }
  .opt input:checked ~ .ind::after { background: var(--ink); }
  .opt input:focus-visible ~ .ind { outline: 2px solid var(--ink); outline-offset: 2px; }
  .opt:not(.off):hover .ind { border-color: var(--sub); }
  .name { color: var(--sub); }
  .opt input:checked ~ .name { color: var(--ink); }
  .d { color: var(--sub); font-size: var(--fs-body); }
  .now { color: var(--faint); white-space: nowrap; }
  .opt.off { cursor: default; }
  .opt.off .name, .opt.off .d, .opt.off .ind { opacity: .45; }
  @media (max-width: 720px) {
    .opt { grid-template-columns: 16px minmax(0, 1fr); row-gap: 2px; padding: var(--s3) 0; }
    .d, .now { grid-column: 2; }
  }
</style>
