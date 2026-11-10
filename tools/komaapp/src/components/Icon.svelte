<script>
  // Line icons drawn for Koma: 20 x 20 grid, 1.5 px stroke at any size, square caps, mitred joins,
  // no fills, currentColor. Used in the phone tab bar, as chevrons on rows and disclosures, and on icon-only controls
  // (which carry their own aria-label). Never beside a heading, in body text, in the desktop nav or on an empty state.
  let {name, size = 20, label = ''} = $props();
  const P = {
    // the five destinations (phone tab bar)
    shoot: '<rect x="2.75" y="5.75" width="14.5" height="10"/><circle cx="10" cy="10.75" r="2.75"/><path d="M12.75 3.75h3"/>',
    photos: '<rect x="2.75" y="2.75" width="6" height="6"/><rect x="11.25" y="2.75" width="6" height="6"/><rect x="2.75" y="11.25" width="6" height="6"/><rect x="11.25" y="11.25" width="6" height="6"/>',
    looks: '<rect x="2.75" y="2.75" width="14.5" height="14.5"/><path d="M2.75 17.25C7 17.25 7 10 10 10s3-7.25 7.25-7.25"/>',
    webcam: '<rect x="2.75" y="5.25" width="10" height="9.5"/><path d="M12.75 8.5l4.5-2.5v8l-4.5-2.5"/>',
    settings: '<path d="M2.75 5.5h3.5M10.25 5.5h7M2.75 10h8.5M15.25 10h2M2.75 14.5h5.5M12.25 14.5h5"/><rect x="6.25" y="3.5" width="4" height="4"/><rect x="11.25" y="8" width="4" height="4"/><rect x="8.25" y="12.5" width="4" height="4"/>',
    // controls
    chevron: '<path d="M7.75 4.5l5.5 5.5-5.5 5.5"/>',
    down: '<path d="M4.5 7.75l5.5 5.5 5.5-5.5"/>',
    back: '<path d="M12.25 4.5L6.75 10l5.5 5.5"/>',
    play: '<path d="M6.75 4.5v11l8.5-5.5z"/>',
    pause: '<path d="M7.25 4.75v10.5M12.75 4.75v10.5"/>',
    download: '<path d="M10 2.75v10M5.75 8.5L10 12.75l4.25-4.25M3.75 16.75h12.5"/>',
    upload: '<path d="M10 12.75v-10M5.75 7L10 2.75 14.25 7M3.75 16.75h12.5"/>',
    copy: '<rect x="6.75" y="6.75" width="10.5" height="10.5"/><path d="M13.25 6.75v-4H2.75v10.5h4"/>',
    search: '<circle cx="8.5" cy="8.5" r="5.25"/><path d="M12.25 12.25l5 5"/>',
    x: '<path d="M4.75 4.75l10.5 10.5M15.25 4.75L4.75 15.25"/>',
    check: '<path d="M4.25 10.25l3.75 3.75 7.75-8"/>',
    undo: '<path d="M6.75 6.25H3V2.5"/><path d="M3.4 6A7 7 0 1 1 4.75 14.75"/>',
    sun: '<circle cx="10" cy="10" r="3.25"/><path d="M10 1.75v2.5M10 15.75v2.5M1.75 10h2.5M15.75 10h2.5M4.15 4.15l1.8 1.8M14.05 14.05l1.8 1.8M4.15 15.85l1.8-1.8M14.05 5.95l1.8-1.8"/>',
    moon: '<path d="M16.25 12.5A6.75 6.75 0 0 1 7.5 3.75a6.75 6.75 0 1 0 8.75 8.75z"/>',
    // D1 names, redrawn on the same grid so older screens keep working
    card: '<path d="M5.25 2.75h7.5l2.5 2.5v12H5.25z"/><path d="M8 2.75v3M10.5 2.75v3"/>',
    cable: '<path d="M8 2.75v3.5M12 2.75v3.5"/><rect x="5.75" y="6.25" width="8.5" height="5"/><path d="M10 11.25v2.5a3.5 3.5 0 0 1-3.5 3.5H5"/>',
    power: '<path d="M10 2.75v6.5"/><path d="M5.6 5.6a6.25 6.25 0 1 0 8.8 0"/>',
    clock: '<circle cx="10" cy="10" r="7.25"/><path d="M10 5.75V10l2.75 1.75"/>',
  };
</script>

<svg class="icon" width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"
  stroke-linecap="square" stroke-linejoin="miter" aria-hidden={label ? undefined : 'true'} role={label ? 'img' : undefined}
  aria-label={label || undefined} focusable="false">{@html P[name] || ''}</svg>

<style>
  .icon { flex: none; display: block; overflow: visible; }
  .icon :global(*) { vector-effect: non-scaling-stroke; }
</style>
