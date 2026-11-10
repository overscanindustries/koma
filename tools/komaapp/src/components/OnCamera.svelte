<script>
  let {name = 'PARAM.DAT'} = $props();
</script>

<ol class="oncam">
  <li>Put the saved file on the memory card, at the top level, not inside a folder.</li>
  <li>Put the card back in the camera.</li>
  <li>Turn the camera on. It reads the new settings as it starts.</li>
  <li>Turn the camera off. This stores them.</li>
  <li>Turn it on again. The new settings are in use.</li>
</ol>
<details class="details">
  <summary>Details</summary>
  <pre>The file is named {name}. If the browser renamed it, change the name back.
The camera renames it to PARAM.OK when it took the settings, or PARAM.BAD when it did not.</pre>
</details>

<style>
  .oncam { margin: 0 0 14px; padding: 0; list-style: none; counter-reset: s; display: grid; gap: 0; }
  .oncam li { counter-increment: s; display: grid; grid-template-columns: 26px 1fr; padding: 9px 0; border-bottom: 1px solid var(--line); font-size: var(--fs-body); line-height: var(--lh-body); }
  .oncam li::before { content: counter(s); color: var(--faint); font-size: var(--fs-small); font-variant-numeric: tabular-nums; }
</style>
