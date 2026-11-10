<script>
  // Loading: the StatusLine with a faint dot and a 1 px indeterminate rule. A present participle with
  // the object named: "Reading the card." Past 10 s it carries the elapsed counter.
  import StatusLine from './StatusLine.svelte';
  let {text, since = null, testid = undefined} = $props();
  const start = Date.now();
  const t0 = $derived(since ?? start);
</script>

<StatusLine tone="wait" {text} since={t0} rule {testid} />
