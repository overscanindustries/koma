# Evidence

How claims in these docs are backed and how to check them.

## Evidence classes

Every claim carries one of the tags defined in [features](evidence-ledger.md#status-tags): `HW`, `EMU`, `STATIC` or `INFERRED`. Older evidence folders use the same classes under other labels: `O-hw` and `O-emu` for observed on hardware or in the emulator, `D` for read from code or a manifest, `I` for inferred, `U` for unknown. An emulator result never becomes `HW`; only a camera run, or files a camera wrote, does.

## Where evidence lives

- **[`analysis/`](../../analysis/README.md)** holds the records: static reverse-engineering, emulator runs, builds and hardware test records, one folder per topic. A folder usually has a `README.md` (question, method, result, limits), JSON reports, logs and the producing scripts. Hardware test records are in [`analysis/hw-tests/`](../../analysis/hw-tests).
- **[`evidence/`](../../evidence)** holds a few cross-cutting JSON records (checksum, emulated dump and script runs).
- **[`archive/`](../../archive)** holds the history: the old status log, research logs, the earlier handbook and design notes. It is not part of the reading path, but some records (for example the first flash and the first restore) are only written down there.

Hardware records cite card files (`KOMALOG.TXT`, `PARAM.CUR`, JPEGs) by name. Many of those files are not in the repository; the record's Results text is then the citable source.

## Hash pinning

Evidence folders are pinned: manifests and reports record file SHA-256s, and `source-snapshots/` or `source-before/` folders keep the producing source named by its hash, so a saved result can be re-checked against the exact code that made it. For that reason:

- Folders under `analysis/` are never moved, renamed or rewritten. New work goes in a new folder.
- Verifiers also pin hashes of tool sources, so editing a file under `tools/` can break a pinned check even when behaviour is unchanged.

## Verifying

```
.venv/bin/python tools/check.py verify
```

runs the read-only checks of retained evidence: the thirteen `tools/verify_*.py` checks through `tools/verify_saved_evidence.py`, plus the B&W prototype check `tools.grdlook.verify_bw_feature`. They read saved reports and fail on any mismatch, timeout or missing input; they do not run firmware or rewrite anything. Rules and the individual commands: [tools/VERIFYING.md](../../tools/VERIFYING.md).

Some inputs are not in git (below), so a plain clone cannot run every check. In a secondary worktree, `check.py` reads them from the reference checkout.

Package builds can be checked directly: rebuilding `koma04w` and the SDHC Koma firmware image from this tree reproduces the recorded hashes ([install](../install.md)), and `tools/koma/koma inspect` re-checks any package's before-bytes against 2.40.

## What is never committed

From [analysis/EVIDENCE-POLICY.md](../../analysis/EVIDENCE-POLICY.md):

- `analysis/camera-dumps/`: a raw capture of a camera, which includes device-specific data and part of internal storage, and everything extracted directly from it (the captured Monitor binaries, ELF wrappers, Ghidra databases).
- `analysis/spare-dumps/`: device files and USB logs of the spare body, including its camera ID.
- Tracked reports may cite those paths but must not quote a camera ID, MakerNote tag `0x5` or other unit values.

Some tracked REA records quote small excerpts of the captured Monitor (about 1.4 KB of 64.5 KB, no device-specific bytes); the policy lists them and how to strip them.

## Firmware inputs

| File | SHA-256 | Origin |
|---|---|---|
| `firmware/original/gr-240.exe` | `e9257cbd…a555` | Ricoh's Windows updater for 2.40 (LHa self-extractor) |
| `firmware/extracted/GR DIGITAL Update/Firmware rel 2.40/qrflect3` | `de00e03d…c783b` | Extracted from `gr-240.exe` |
| `firmware/derived/grd240-armbe.bin` | `60b0a387…87a4` | The `qrflect3` payload without its 512-byte header |
| `firmware/original/history/` | per folder | Earlier Ricoh releases 1.08-2.31 and update documents recovered from archives ([fw-history](../../analysis/fw-history/README.md), [internals 12](12-version-history.md)) |

Every Koma build input is checked by these hashes; `tools/koma` refuses any other file. These are Ricoh's files. `scripts/export-release.sh` leaves them out of an exported tree and checks it for Ricoh files; the tools then take your own copy through `--firmware` or `KOMA_FIRMWARE` ([building](../building.md#firmware-input)).
