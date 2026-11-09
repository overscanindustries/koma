# Firmware inputs

Ricoh's firmware is not in this repository. Download the 2.40 update, `gr-240.exe`, from [Ricoh's firmware page](https://www.ricoh-imaging.co.jp/english/r_dc/download/firmware/grd/win.html); `tools/koma` checks it by SHA-256 and refuses anything else.

Pass it with `--firmware PATH` or the `KOMA_FIRMWARE` environment variable. Otherwise the tools look at the paths below. Everything in `firmware/` except this file is git-ignored.

| Path | SHA-256 | How to get it |
|---|---|---|
| `firmware/original/gr-240.exe` | `e9257cbd89cd3f07163ef8700a9143121ecb69ecee94c1c935c7b7486fafa555` | Ricoh's 2.40 Windows updater |
| `firmware/extracted/GR DIGITAL Update/Firmware rel 2.40/qrflect3` | `de00e03d563fd1bdd0a2a925761b1a029993425049841119e3e83eef990c783b` | Extract `gr-240.exe`, for example with `7z x` |
| `firmware/extracted/GR DIGITAL Update/Firmware rel 2.40/qrflect7` | `1dd1a9d9cb2d5a8386b059722c01ac025f10dbe5f507c7cdb9f5d486f9c21c3f` | Extracted next to `qrflect3`; used only by `grdusb stage-update` |
| `firmware/derived/grd240-armbe.bin` | `60b0a387126347a37f7d2217bb0b36b5a70fc33ce28959a8244b07757b9887a4` | `tail -c +513 qrflect3 > grd240-armbe.bin` |

`tools/koma` needs only `qrflect3`, or `gr-240.exe` or a zip holding it.
