<script>
  // Overview, the home the wordmark opens. The camera at rest: the last photo,
  // the look, three facts, one primary action, the photo count. Nothing runs on the camera from here; the reads
  // are in lib/overview.svelte.js. Identity and connection are not repeated: the nav column carries them.
  // The primary, by state, in this order: the webcam holds the camera (no primary: the busy line offers to turn it off);
  // no Koma on the camera (Set up Koma, or Continue setup); a setup left part way (Continue setup); an older build this
  // computer's package replaces (Update Koma, with live view as a quiet link); else Start live view.
  import {untrack} from 'svelte';
  import {cam, firmware, holder} from '../lib/camera.svelte.js';
  import {S, readFromCamera} from '../lib/settings.svelte.js';
  import {screen} from '../lib/ui.svelte.js';
  import {rest, load, readPhoto, komaFacts} from '../lib/overview.svelte.js';
  import {st, setupEntry, resumable} from '../lib/onboard.svelte.js';
  import {W} from '../lib/onboardWords.js';
  import {BUSY, komaOnCamera} from '../lib/words.js';
  import {setOn as setWebcam} from '../lib/webcam.svelte.js';
  import * as PH from '../lib/photos.svelte.js';
  import * as F from '../lib/format.js';
  import PageTitle from '../components/ui/PageTitle.svelte';
  import PhotoFrame from '../components/ui/PhotoFrame.svelte';
  import SpecSheet from '../components/ui/SpecSheet.svelte';
  import EmptyState from '../components/ui/EmptyState.svelte';
  import StatusLine from '../components/ui/StatusLine.svelte';
  import Details from '../components/ui/Details.svelte';
  import Loading from '../components/ui/Loading.svelte';

  const on = $derived(cam.state === 'cam');
  // The camera went away after it was here (the Shell's Disconnected line says so): the last Overview stays, its
  // camera actions gone. A tab that never had a camera shows the one empty line.
  const kept = $derived(!on && cam.sawCamera && S.source !== 'card');
  $effect(() => { void cam.conn; void S.map; if (on) untrack(load); });
  // Live view holds the card (another window): ask again every 5 s until the photos can be read.
  $effect(() => {
    if (!rest.busy || !on) return;
    const t = setInterval(readPhoto, 5000);
    return () => clearInterval(t);
  });

  const k = $derived(komaFacts());
  // Koma's version (words.js komaOnCamera): this computer's release when the camera's log names its build, "An earlier
  // Koma build" for one this package replaces, else the release the settings block's tag names, else that Koma runs.
  const version = $derived(k.state !== 'koma' ? null
    : komaOnCamera(k.tag, {kind: rest.kind, pkgVersion: rest.pkg && rest.pkg.version}));
  const spec = $derived([
    {label: 'Firmware', value: on ? firmware() : rest.fw, testid: 'spec-firmware'},  // -VER: Ricoh's number (VERSIONING.md)
    {label: 'Koma', value: k.state === 'koma' ? version : k.state === 'none' ? 'Not installed' : null, testid: 'spec-koma'},
  ]);
  const layout = $derived(k.state === 'koma' ? k.layout : null);

  // Who holds the camera, from the status poll; the busy line's words come from the one BUSY map
  const who = $derived(holder());
  const webcam = $derived(who === 'webcam');
  const busyText = $derived.by(() => {
    const b = BUSY[who] || BUSY.remote;
    const it = cam.st && cam.st.interval;
    return typeof b.text === 'function' ? b.text(it ? it.done : undefined, it ? it.count : undefined) : b.text;
  });
  const busyAction = $derived(webcam ? {label: BUSY.webcam.action, onclick: () => setWebcam(false), testid: 'overview-webcam-off'} : null);

  const entry = $derived(setupEntry());
  const primary = $derived(webcam ? 'none' : k.state === 'reading' ? 'reading' : k.state === 'error' ? 'error'
    : k.state === 'none' || resumable() ? 'setup' : rest.mode === 'update' ? 'update' : k.state === 'koma' ? 'live' : 'none');

  const photo = $derived(rest.photo);
  // One line: Last photo · 14:23 · F2.4 1/3 ISO 400 (time and facts joined with a middle dot; the date too
  // when the photo was not taken today). Exposure the way the camera prints it (EXPOSURE_ORDER).
  const expo = $derived(photo && photo.exif ? F.exposure(photo.exif) : '');
  const capText = $derived(photo ? ['Last photo', F.when(photo.date), expo].filter(Boolean).join(' · ') : '');
  const photoHref = $derived(photo ? '#/photos/' + encodeURIComponent(photo.path) : '');
  const details = $derived([
    {k: 'Photo', v: photo ? photo.name : ''},
    {k: 'Taken', v: photo ? F.dateTime(photo.date) : ''},
    {k: 'Size', v: photo ? F.bytes(photo.size) + (photo.width ? ', ' + photo.width + ' × ' + photo.height : '') : ''},
    {k: 'Firmware tag', v: k.state === 'koma' ? k.tag : ''},
    {k: 'Bridge', v: cam.st ? 'komabridge ' + cam.st.bridge_version + (cam.st.simulated ? ', simulated camera' : '') : ''},
  ]);
  // Photos not yet imported, when Photos has read the card on this connection; else the card's count
  const fresh = $derived(typeof PH.newCount === 'function' ? PH.newCount() : null);
  const countText = $derived(typeof fresh === 'number' && fresh > 0 ? (fresh === 1 ? '1 new photo' : fresh + ' new photos')
    : rest.count ? (rest.count === 1 ? '1 photo on the card' : rest.count + ' photos on the card') : '');

  function go(e) { e.preventDefault(); entry.onclick(); }
</script>

<section class="ov" aria-labelledby="page-title" data-testid="overview">
  <PageTitle title="Overview" hidden />

  {#if !on && !kept}
    <div class="solo">
      {#if S.source === 'card'}
        <EmptyState label="Memory card" text="Working from the card's settings file." testid="overview-card"
          action={{label: 'Camera menu', href: '#/settings/camera', kind: 'link'}} />
      {:else}
        <EmptyState label="No camera" text="The camera appears here once it is connected." />
      {/if}
    </div>
  {:else}
    <div class="grid">
      <div class="photo">
        {#if photo && (rest.photoState === 'ok' || rest.photoState === 'reading' || !on)}
          <a class="frame-link" href={photoHref} data-testid="last-photo-link">
            <PhotoFrame src={photo.url} alt={'The last photo on the card, taken at ' + F.time(photo.date) + '.'} testid="last-photo">
              {#snippet caption()}<span class="cap num" data-testid="last-caption">{capText}</span>{/snippet}
            </PhotoFrame>
          </a>
          {#if on && (rest.busy || webcam)}<div class="under"><StatusLine tone="wait" text={busyText} action={busyAction} testid="overview-busy" /></div>{/if}
        {:else if !on}
          <!-- nothing read before the drop: the Shell's line is the fact -->
        {:else if webcam || rest.photoState === 'busy'}
          <StatusLine tone="wait" text={busyText} action={busyAction} testid="overview-busy" />
        {:else if rest.photoState === 'reading' || rest.photoState === 'idle'}
          <PhotoFrame status="loading">
            {#snippet caption()}<span class="cap-line"><StatusLine tone="wait" text="Reading the last photo." since={rest.since || null} /></span>{/snippet}
          </PhotoFrame>
        {:else if rest.photoState === 'error'}
          <StatusLine tone="bad" text="The photos could not be read from the card." action={{label: 'Read again', onclick: readPhoto}} />
        {:else}
          <EmptyState label="No photos" text="The card holds no photos." testid="overview-empty" />
        {/if}
        {#if photo}<div class="more"><Details items={details} testid="overview-details" /></div>{/if}
      </div>

      <div class="side">
        {#if rest.look}
          <dl class="look">
            <dt class="micro">Look</dt>
            <dd class="name" data-testid="overview-look"><a class="look-link" href="#/looks">{rest.look}<span class="sr-only">. Change look</span></a></dd>
          </dl>
        {/if}
        <div class="inst" class:first={!rest.look}>
          <SpecSheet rows={spec} label="The camera" testid="overview-spec" />
          {#if layout}
            <!-- the third spec row, its value a link to Koma mode (the spec sheet has no link rows) -->
            <dl class="lay" data-testid="spec-layout"><dt>Menu layout</dt><dd><a class="link cam" href="#/settings/mode">{layout}</a></dd></dl>
          {/if}
        </div>
        {#if !on}
          <!-- no camera actions while it is away -->
        {:else if primary === 'setup'}
          <div class="act">
            <p><a class="btn primary" href={entry.href} onclick={go} data-testid="overview-primary">{entry.label}</a></p>
            {#if entry.resuming && st.stopped}<p class="small sub" data-testid="overview-stopped">{W.oncamera.stoppedLine}</p>{/if}
          </div>
        {:else if primary === 'update'}
          <p class="act two">
            <a class="btn primary" href="#/settings/update" data-testid="overview-primary">Update Koma</a>
            <a class="link" href="#/shoot" data-testid="overview-live">Start live view</a>
          </p>
        {:else if primary === 'live'}
          <p class="act"><a class="btn primary" href="#/shoot" data-testid="overview-primary">Start live view</a></p>
        {:else if primary === 'reading'}
          <div class="act"><Loading text="Reading the settings from the camera." /></div>
        {:else if primary === 'error'}
          <div class="act"><StatusLine tone="bad" text="The settings could not be read from the camera." action={{label: 'Read again', onclick: readFromCamera}} /></div>
        {/if}
        {#if countText}
          <p class="count"><a class="link" href="#/photos" data-testid="overview-photos">{countText}</a></p>
        {/if}
      </div>
    </div>
    {#if screen.phone}<p class="foot">Project Koma.</p>{/if}
  {/if}
</section>

<style>
  .ov { max-width: var(--content-max); }
  /* the photo in columns 1 to 7; one top-aligned instrument column beside it; below both, one open field */
  .grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); column-gap: var(--col-gap); align-items: start; }
  .photo { grid-column: 1 / span 7; min-width: 0; }
  .side { grid-column: 8 / span 5; min-width: 0; }
  .frame-link { display: block; color: inherit; text-decoration: none; border-radius: var(--r-img); }
  .frame-link:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
  .cap { display: block; color: var(--sub); }
  .name {
    margin-top: var(--s1);
    font-size: var(--fs-display); line-height: var(--lh-display); font-weight: 500; letter-spacing: -.02em;
    font-variant-numeric: tabular-nums; overflow-wrap: anywhere;
  }
  /* the look's name opens Looks: at display size an underline would be the loudest thing on the page, so it shows on
     hover and focus only */
  .look-link { color: inherit; text-decoration: none; text-underline-offset: .12em; text-decoration-thickness: 2px; }
  .look-link:hover, .look-link:focus-visible { text-decoration: underline; }
  .inst { margin-top: var(--s7); }
  .inst.first { margin-top: 0; }
  .lay {
    display: grid; grid-template-columns: var(--spec-label) minmax(0, 1fr); align-items: center; column-gap: var(--s4);
    min-height: var(--spec-h); padding: var(--s2) 0; border-bottom: 1px solid var(--line);
  }
  .lay dt { color: var(--sub); font-size: var(--fs-body); line-height: var(--lh-body); }
  .lay dd { min-width: 0; overflow-wrap: anywhere; }
  .act { margin-top: var(--s5); }
  .act.two { display: flex; align-items: center; gap: var(--s5); flex-wrap: wrap; }
  .sub { margin-top: var(--s2); color: var(--sub); max-width: 44ch; }
  .count { margin-top: var(--s4); }
  .under { margin-top: var(--s2); }
  .more { margin-top: var(--s3); }
  .cap-line { display: block; padding-top: var(--s1); color: var(--sub); }
  .solo { max-width: 60ch; }
  .foot { margin-top: var(--s7); color: var(--faint); font-size: var(--fs-small); line-height: var(--lh-small); }

  @media (max-width: 1080px) {
    .photo { grid-column: 1 / span 6; }
    .side { grid-column: 7 / span 6; }
  }
  @media (max-width: 720px) {
    .grid { grid-template-columns: minmax(0, 1fr); }
    .photo, .side { grid-column: 1; }
    .side { display: contents; }
    .look { order: -1; margin-bottom: var(--s5); }
    .inst, .inst.first { margin-top: var(--s6); }
    .act :global(.btn) { width: 100%; }
    .act.two { flex-direction: column; align-items: stretch; gap: var(--s3); }
  }
</style>
