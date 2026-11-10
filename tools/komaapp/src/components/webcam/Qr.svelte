<script>
  // The pairing QR code as SVG, from the bridge's matrix (qrmini: rows of 0 and 1, no quiet zone). Dark modules on
  // paper in both themes: phone scanners expect that polarity, so these two colors are fixed, not themed.
  let {rows, label} = $props();
  const QUIET = 4;
  const n = $derived(rows.length + 2 * QUIET);
  const path = $derived.by(() => {
    let d = '';
    rows.forEach((r, y) => {
      for (let x = 0; x < r.length; x++) {
        if (r[x] !== '1') continue;
        let w = 1;
        while (x + w < r.length && r[x + w] === '1') w++;
        d += 'M' + (x + QUIET) + ' ' + (y + QUIET) + 'h' + w + 'v1h-' + w + 'z';
        x += w - 1;
      }
    });
    return d;
  });
</script>

<svg viewBox="0 0 {n} {n}" role="img" aria-label={label} shape-rendering="crispEdges" data-testid="phone-qr"
  data-modules={rows.length}>
  <rect width={n} height={n} fill="#f4f4f1"/>
  <path d={path} fill="#18191b"/>
</svg>
