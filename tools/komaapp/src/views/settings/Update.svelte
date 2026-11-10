<script>
  // Update Koma. The guided Install and Update flow (views/onboard/UpdateKoma.svelte) reads the camera and this computer's package, offers the one action, and keeps
  // Return to official firmware one click away; the work runs full screen at #/setup. One primary button, nothing beside
  // it. The pre-1.0 card setup page (koma-setup.html, /legacy) is not linked: Koma 1.0's setup replaces it.
  import UpdateKoma from '../onboard/UpdateKoma.svelte';
</script>

<section class="up" data-testid="update">
  <UpdateKoma />
</section>

<style>
  .up { max-width: 640px; }
</style>
