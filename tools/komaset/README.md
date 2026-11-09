# komaset

`komaset.py` converts the camera's 4 KB settings block (`PARAM.CUR` or `PARAM.DAT`) to named plain text and back. It also compares two blocks and merges one camera's settings onto another's. It needs Python 3 and the standard library only. The field list is `settings-map.json`, and `--map FILE` overrides it.

The browser version is the Koma Setup page, served by the bridge ([`bridge/README.md`](bridge/README.md), [`web/README.md`](web/README.md)).

## Commands

```
python3 tools/komaset/komaset.py dump PARAM.CUR -o settings.txt
python3 tools/komaset/komaset.py apply settings.txt -o PARAM.DAT [--base PARAM.CUR] [--force]
python3 tools/komaset/komaset.py diff A.bin B.bin            # exit 1 if different
python3 tools/komaset/komaset.py merge --target target.cur --source source.cur -o PARAM.DAT [--include-excluded] [--force]
python3 tools/komaset/komaset.py check PARAM.bin             # exit nonzero on bad size, magic or checksum
python3 tools/komaset/komaset.py info                        # map coverage
```

## Text format

The text file is INI-like, and `;` starts a comment. Each page has a section such as `[shooting]` with lines like `name = value   ; 0x059 = 0x01`. Enums take a label (`AUTO-HI`) or raw hex. Bitfields take `FLAG_A|FLAG_C`. `[raw]` holds every unmapped byte range.

Without `--base`, `apply` starts from an all-`0xff` block, so the file needs `[raw]`. With `--base` it changes only the lines present. Bad values are listed and nothing is written unless `--force` skips them. The checksum is always recomputed: the sum of bytes `0x000..0xffb` as big-endian u16 halfwords, stored big-endian at `0xffc`.

## Merge

`merge` copies every byte from source to target except the magic, the checksum and the `exclude_on_merge` ranges, which hold camera-specific data. `--include-excluded` copies those too. It refuses if either magic is wrong or the Koma versions differ, unless `--force`.

To copy settings to a camera:

1. Copy `PARAM.CUR` off both cards.
2. Run `komaset merge --target target.cur --source source.cur -o PARAM.DAT`.
3. Put `PARAM.DAT` on the target camera's card only.
4. Boot the target, power off, then boot again. The first boot consumes the file.
5. Run `komaset diff` on the target's new `PARAM.CUR` against `source.cur`. Only camera-specific fields should differ.

## verify_session

```
python3 tools/komaset/verify_session.py <card-root-dir> [--expect-tag Rev024M] [--baseline PARAM.CUR]
```

It reads a copy of the card root and checks `KOMALOG.TXT`, `PARAM.CUR`, the import files (`PARAM.DAT`, `PARAM.OK`, `PARAM.BAD`) and the firmware tag of the newest JPEGs. It exits 0 when all is well, 1 when something needs attention, and 2 when `KOMALOG.TXT` or `PARAM.CUR` is missing.

`build_settings_map.py` regenerates `settings-map.json`.
