<script>
  // Error: the StatusLine with the failed dot. Cause. Consequence. Action: two or three short
  // sentences and one quiet action. No banner, no modal, no red text.
  //   <ErrorState text="The settings could not be sent. Nothing on the camera changed." action={{label: 'Try again', onclick}} />
  import StatusLine from './StatusLine.svelte';
  let {text, sub = '', action = null, testid = undefined} = $props();
</script>

<StatusLine tone="bad" {text} {sub} {action} {testid} />
