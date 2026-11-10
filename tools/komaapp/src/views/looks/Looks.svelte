<script>
  // The Looks area: #/looks is the gallery, #/looks/<look> its editor. Reads the camera's settings the
  // way Settings does (once per connection, never over pending edits), then the newest photo for the try-on.
  import '../../styles/looks.css';
  import {route} from '../../lib/ui.svelte.js';
  import {cam, connected} from '../../lib/camera.svelte.js';
  import {S, B, readFromCamera, pending} from '../../lib/settings.svelte.js';
  import {koma} from '../../lib/looksStore.js';
  import {A} from '../../lib/apply.svelte.js';
  import {LK, loadSource, settleStatus, rebaseDrafts} from '../../lib/looks.svelte.js';
  import {lookBySlug} from '../../lib/looksCatalog.js';
  import Gallery from './Gallery.svelte';
  import Editor from './Editor.svelte';

  $effect(() => {
    if (!connected() || S.source === 'card' || S.reading || S.map !== 'ready' || A.open) return;
    if (S.readError && S.readFailConn === cam.conn) return;            // failed on this connection: Read again asks
    if (S.source !== 'camera' || (S.readConn !== cam.conn && pending().length === 0)) readFromCamera();
  });

  // the photo, after the settings: how the photo was made decides how the looks are drawn on it. Looked at again each
  // time Looks opens (a photo taken in Shoot since), and on a new connection; read again only when it changed.
  let checked = false;
  $effect(() => {
    if (A.open) return;
    if (S.source === 'card') { if (!(LK.src === 'sample' && LK.why === 'card')) loadSource(); return; }
    if (!connected() || S.reading || (S.source !== 'camera' && !S.readError)) return;
    if (B.orig && S.source === 'camera' && !koma(B.orig)) return;      // official firmware: no looks to draw
    if (!B.orig && S.readCode === 'no_param_cur') return;               //... nor a settings file
    if (LK.conn !== cam.conn || LK.src === 'idle' || !checked) { checked = true; loadSource(); }
  });

  $effect(() => { if (!A.open) settleStatus(); });

  // the block changed (read again, another GR, an Apply): drafts follow what the camera now holds
  $effect(() => { void S.rev; rebaseDrafts(); });

  // official firmware has no looks to edit: the gallery's empty state stands for the editor too
  const stock = $derived.by(() => { void S.rev; return B.orig ? !koma(B.orig) : S.readCode === 'no_param_cur'; });
  const look = $derived(route.sub && !stock ? lookBySlug(route.sub) : null);
</script>

{#if look}
  {#key look.id}<Editor {look} />{/key}
{:else}
  <Gallery />
{/if}
