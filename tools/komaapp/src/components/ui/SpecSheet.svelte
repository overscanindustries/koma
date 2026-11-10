<script>
  // A definition list of facts, the same grammar as Row: label left in --sub (sentence case or the
  // camera's case, never micro), the value from one x in ink and tabular figures, 1 px rules, 40 px rows.
  //   rows: [{label, value, cam: true when the value is a camera string, testid}]
  // A row with no value is left out, never shown blank.
  let {rows = [], label = undefined, top = true, testid = undefined} = $props();
  const shown = $derived(rows.filter(r => r && r.value !== null && r.value !== undefined && r.value !== ''));
</script>

<dl class="spec" class:top aria-label={label} data-testid={testid}>
  {#each shown as r (r.label)}
    <div class="r" data-testid={r.testid}>
      <dt>{r.label}</dt>
      <dd class:cam={r.cam} class="num">{r.value}</dd>
    </div>
  {/each}
</dl>

<style>
  .spec.top { border-top: 1px solid var(--line); }
  /* 40 px for one line; a value that wraps keeps 8 px off each rule */
  .r {
    display: grid; grid-template-columns: var(--spec-label) minmax(0, 1fr); align-items: center; column-gap: var(--s4);
    min-height: var(--spec-h); padding: var(--s2) 0; border-bottom: 1px solid var(--line);
  }
  dt { color: var(--sub); font-size: var(--fs-body); line-height: var(--lh-body); }
  dd { color: var(--ink); font-size: var(--fs-body); line-height: var(--lh-body); min-width: 0; overflow-wrap: anywhere; }
  dd.cam { font-size: var(--fs-cam); line-height: var(--lh-cam); font-stretch: 88%; font-weight: 560; letter-spacing: .06em; }
</style>
