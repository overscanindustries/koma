# Internals

Reference chapters on the original Ricoh GR DIGITAL (2005), firmware 2.40, and its Monitor 1.00 bootloader: what the firmware does, where, and how well the evidence supports it, plus the mechanisms Koma uses to change it. State as of 2026-11-08.

Each chapter opens with a short summary, then reference sections, an "Open questions" list for what is still unknown, and an "Evidence" list of the analysis files its claims rest on. Addresses are firmware 2.40 runtime addresses unless a chapter says otherwise.

## Chapters

| # | Chapter | Covers | Confidence |
|---|---|---|---|
| 01 | [Hardware and memory map](01-hardware-and-memory-map.md) | Processors, OneNAND layout, RAM and peripheral map | Medium: map read from code; chip parts identified by driver match |
| 02 | [Boot chain and Monitor](02-boot-chain-and-monitor.md) | Reset, Monitor 1.00, header check, handoff, card RAM loader, console | Medium: Monitor bytes captured from a camera; its branches run only in the emulator |
| 03 | [Firmware image and update format](03-firmware-image-and-update-format.md) | Reflector container, types, checksums, `qrflect3`/`qrflect7`, the SD updater | High: format read from every official file; official and modified updates run on both bodies |
| 04 | [Application architecture](04-application-architecture.md) | Tasks, queues, `MainCtrl`, CPU1, keys, UI, capture and shutdown paths | Medium: static and emulated; CPU1 is a black box |
| 05 | [Settings and adjustment store](05-settings-and-adjustment-store.md) | User settings block, marker groups, factory calibration store | Medium: layout static and emulated, checked against camera-written blocks |
| 06 | [Image pipeline and colour](06-image-pipeline-and-colour.md) | Tone tables, selectors, colour registers, JPEG | Medium: colour-register effects measured on a camera; tone input encoding unknown |
| 07 | [USB protocol](07-usb-protocol.md) | ORIGINAL-mode commands and replies, `-CCTL`, Koma verbs, CA-1 decoder, stock boot options, host tools | High for commands used on hardware; static for the rest |
| 08 | [Card scripts and service terminal](08-card-scripts-and-service-terminal.md) | `SCRIPT.MG1`, `adjcom`, the service engine, the dump script | Medium: one stock script run on a camera; the rest static or emulated |
| 09 | [Recovery and failure modes](09-recovery-and-failure-modes.md) | Known failures, how each is detected and recovered | High for removing a package and for a keyed restore over generation 1; low for flash-level recovery (emulator only) |
| 10 | [Emulator and tooling](10-emulator-and-tooling.md) | The `koma` tool, its checks, the camera model, research tools | High for what the tools do; their output is emulator evidence |
| 11 | [Modification playbook](11-modification-playbook.md) | Generation 1 and generation 2 Koma firmware, the package and record model, hook points, platform rules, feature catalogue | Medium-high: loader, /IROM package and several features proven on hardware; many features emulated only |
| 12 | [Version history](12-version-history.md) | The seven recovered releases, what changed, the Ricoh ARM family | High: byte-level diffs of verified official files |
| 13 | [Display, live view and USB streaming](13-display-live-view-and-usb-streaming.md) | Frame ring, resize and JPEG engines, USB data path, file slots, tether and `-LVFR` live view | High for tether and live view (both bodies); static for engine internals |

Also:

- [Address index](address-index.md): every address used in the chapters, by area.
- [Glossary](glossary.md): terms and abbreviations.
- [Diagrams](diagrams/README.md): boot chain, update state machine, flash layout, tone chain, CA-1 decoder, USB session, settings markers, version timeline, ARM family, module loader.

## Status tags

Every claim carries one of `HW`, `EMU`, `STATIC` or `INFERRED`, defined in [features](evidence-ledger.md). Qualifiers name the body, firmware or date, as in `HW 2026-11-08 both`, `HW spare` or `HW main`. Unknowns carry no tag; they are listed under "Open questions". A result never moves up a class by repetition: something that passed in the emulator a hundred times is still `EMU`. "Bounded negative" means a search that found nothing within a stated boundary. When sources disagree, hardware wins, then emulator runs of the captured Monitor, then other emulator runs, then static reading.

## Bodies and firmware

Which firmware and package each body runs is in [features](evidence-ledger.md) and [versioning](../versioning.md). Details of the Koma firmware are in [11](11-modification-playbook.md).

Base firmware: application 2.40 (official `qrflect3`, SHA-256 `de00e03d563fd1bdd0a2a925761b1a029993425049841119e3e83eef990c783b`), Monitor 1.00.

No camera IDs, serial numbers, MakerNote tag 5 values or photo content appear in these chapters. Ricoh firmware is not redistributed; the tools work on the user's own copy of the official update file.
