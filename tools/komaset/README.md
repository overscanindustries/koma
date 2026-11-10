# komaset

Host CLI that converts the camera's raw 4 KB settings block (`PARAM.CUR` / `PARAM.DAT`, RAM image `0x1b000..0x1bfff`) to and from named plain text, diffs two bodies, and merges one body's settings onto another's dump. Python 3, stdlib only.

The field list lives in `settings-map.json` next to the script. `testdata/sample-map.json` is a tiny map used by the tests. Pass `--map FILE` to use another one.


**Web app (Koma Setup) quickstart:** `python3 tools/komaset/bridge/komabridge.py --real` (camera powered on before the USB cable goes in) or `--mock --mock-tether --mock-hw` (simulated, with timings measured on the camera), then open http://localhost:8765/. What is proven on hardware and what only against the mock: [`bridge/README.md`](bridge/README.md) ("Hardware status") and [`web/README.md`](web/README.md).

## Usage

```
python3 tools/komaset/komaset.py dump PARAM.CUR -o settings.txt
python3 tools/komaset/komaset.py apply settings.txt -o PARAM.DAT [--base PARAM.CUR] [--force]
python3 tools/komaset/komaset.py diff A.bin B.bin            # exit 1 if different
python3 tools/komaset/komaset.py merge --target spare.cur --source main.cur -o PARAM.DAT [--include-excluded] [--force]
python3 tools/komaset/komaset.py check PARAM.bin             # exit nonzero on bad size/magic/checksum
python3 tools/komaset/komaset.py info                        # map coverage
```

`--map` works before or after the subcommand. Tests: `python3 tools/komaset/test_komaset.py`.

## Text format

INI-like. `;` starts a comment.

- `[meta]`: informational (firmware, magic ok, checksum ok with stored and computed values, Koma version). Ignored by `apply`.
- One section per page, in map order (`[shooting]`, `[setup]`, ...). Lines are `name = value   ; 0x059 = 0x01`.
  - enum: label (`AUTO-HI`) or raw hex (`0x01`, must be a known value). Labels are case-insensitive.
  - u8/u16/i8: decimal or `0x` integers, checked against the map `range`.
  - bitfield: `FLAG_A|FLAG_C`, `NONE`, or a raw integer mask.
  - bytes: space-separated hex.
- `[raw]`: every unmapped byte range, 16 bytes per line: `0x1b3-0x1c2 = ff ff ...` (offsets relative to block base, end inclusive). Includes the magic.

`apply` without `--base` starts from an all-`0xff` block, so it needs the `[raw]` section (and refuses if the magic is missing). With `--base` it overlays only the lines present, so a partial file works. Every value is validated; offending lines are listed and nothing is written unless `--force` (which skips the bad values). The checksum is always recomputed.

## Checksum

Verified on a real settings block. Firmware routine `0x3d9b8` sums bytes `0x000..0xffb` as big-endian u16 halfwords into a 32-bit accumulator, and `0x4949c` stores the result as a big-endian u32 at `0xffc..0xfff` (map: `u32-sum-of-u16be`, range `[0, 4092]`, offset 4092, size 4, endian big). Evidence: real block `testdata/main-2026-11-06.cur` has stored bytes `07 44 90 a0`, and the u16-BE sum is `0x074490a0`. All multi-byte fields (u16, u32) are big-endian. `check` fails if the stored checksum does not match; the `[raw]` section excludes all 4 checksum bytes.

Non-numeric-range note: a stored value outside a field's range or enum list (for example erased `0xff`) is dumped as raw hex (`0xff`) and `apply` accepts `0x` values verbatim, so dump then apply reproduces the block byte for byte.

## Merge

Copies every byte from source onto target except: magic, checksum (recomputed), and `exclude_on_merge` ranges (disable with `--include-excluded`). It prints what was skipped and why. It refuses if the Koma header (`KOMA` at `0x8b0`, version at `0x8b4`) differs between the two, or either magic is wrong, unless `--force`. When both files carry the KSIO sub-header (`KSIO` at `0x8d0`, 7-byte firmware tag at `0x8d5`) and the tags differ, it only warns and copies (no `--force` needed): `warning: These cameras run different firmware tags (Rev024M, 240K2...). Settings still copy; Koma version matches.` (source tag first). The main (`Rev024M`, the generation 1 image) and the spare (`240K2`, the SDHC Koma firmware) run the same Koma module, and the layout is governed by the Koma settings version, which still refuses. The camera imports such a file with KSIO status 3 (OK, tag differed). If only one has KSIO it warns and continues. The web page (copy task: a warning notice, Copy anyway only for a Koma version difference) and the bridge (`POST /settings`: the warning in `warnings`; 422 `koma_mismatch` for a Koma version difference against the camera's `PARAM.CUR` unless `force:true`) apply the same rule.

Every command accepts a block file of any name: `PARAM.CUR`, `PARAM.SAV` (written by the on-camera SAVE TO CARD row, which no longer writes `PARAM.DAT`) or `PARAM.DAT`.

## Blind mirror procedure

1. Copy `PARAM.SAV` (from the SAVE TO CARD row) or `PARAM.CUR` off the main camera's card.
2. `komaset merge --target spare.cur --source main.cur -o PARAM.DAT` (spare.cur is the spare's own `PARAM.CUR`).
3. Write `PARAM.DAT` only to the target (spare) body's card; never leave a `PARAM.DAT` on the source body's card, it re-imports at its next boot and reverts later changes. Write it and boot the spare.
4. Power off, then boot again (the first boot consumes the file).
5. Read the spare's `PARAM.CUR` back and `komaset diff` it against main.cur; only excluded and body-specific fields should differ.

## verify_session: read a card after a blind session

`python3 tools/komaset/verify_session.py <card-root-dir> [--expect-tag Rev024M] [--baseline PARAM.CUR]`

Copy the card root to the Mac and run this on the copy. It only reads the directory. It reports, each ending in a one-line verdict (OK / ATTENTION / MISSING): `KOMALOG.TXT` (build id, STAGE, POOL against the emulator 1,888 B and hardware 6,656 B / 3,904 B figures; POOL above 1,500 B is OK), `PARAM.CUR` (size, magic, checksum, KSIO version/tag/status decoded as NONE/OK/BAD, optional `diff` summary against `--baseline`), the import files (`PARAM.DAT` as DONE marker / unconsumed full block / deleted, `PARAM.OK`, `PARAM.BAD` with its reason), and the newest JPEGs' MakerNote firmware tag (via `tools/spare_test/metadata.py`). Exit code 0: nothing needs attention; 1: some section is ATTENTION; 2: no ATTENTION but `KOMALOG.TXT` or `PARAM.CUR` is missing. A sample card is in `testdata/session-sample/` (synthetic; its `PARAM.CUR` is `main-2026-11-06.cur` with a KSIO header, tag `Rev024M`, status 1 stamped in). Tests: `python3 tools/komaset/test_verify_session.py`.
