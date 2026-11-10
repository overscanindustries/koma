<script>
  // The Koma lockup: the コマ mark (komamark.py) before the KOMA word: wdth 110, wght 700, 0.14em, 15 px. This treatment exists nowhere else.
  // `version`: the Koma package version tag this app ships (240K1.0 form), set beside it at camera-string size. Nothing
  // reports that version yet, so no screen passes it; the lockup stays the word alone until it exists.
  let {version = ''} = $props();
</script>

<span class="mark"><svg class="glyph" viewBox="0 0 64 64" aria-hidden="true"><path fill="currentColor" d="M2.00 20.00 L28.00 20.00 L28.00 24.84 L2.00 24.84ZM22.50 20.00 L28.00 20.00 L28.00 44.00 L22.50 44.00ZM2.00 39.16 L28.00 39.16 L28.00 44.00 L2.00 44.00ZM34.00 20.00 L61.50 20.00 L61.50 24.84 L34.00 24.84ZM61.50 24.83 L49.18 37.61 L45.42 33.99 L58.90 20.00 L61.50 20.00Z"/><path fill="var(--acc)" d="M46.32 29.82 L55.53 44.00 L49.50 44.00 L42.08 32.58Z"/></svg><span class="word">KOMA</span>{#if version}<span class="ver">{version}</span>{/if}</span>

<style>
  .mark { display: inline-flex; align-items: center; gap: 10px; line-height: 1; }
  .glyph { width: 30px; height: 30px; flex: none; }
  .word {
    font-stretch: 110%; font-weight: 700; font-size: 15px; letter-spacing: .14em;
    margin-right: -.14em;  /* the last letter's tracking does not push the tag away */
  }
  .ver {
    font-size: var(--fs-cam); font-stretch: 88%; font-weight: 560; letter-spacing: .06em;
    color: var(--sub); font-variant-numeric: tabular-nums;
  }
</style>
