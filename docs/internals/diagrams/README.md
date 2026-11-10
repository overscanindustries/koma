# Diagrams

Ten standalone diagrams (diagram 1 is split in two). Each has `.html` (page with title, caption and sources), `.svg` (diagram only) and `.png` (2x render of the SVG). Every label comes from the internals chapters or the analysis files named in each page's footer; nothing here is new evidence. Tags follow [the status tags](../evidence-ledger.md): `HW`, `EMU`, `STATIC`, `INFERRED`.

The SVG and HTML files load Instrument Serif, Geist and Geist Mono from Google Fonts; offline, a fallback face is used and text widths shift slightly. The PNGs were rendered from the SVGs with headless Chrome. To change a diagram, edit the SVG and the matching `<svg>` block in the HTML page, then re-render the PNG at twice the `viewBox` size.

| # | Files | Shows | Used in |
|---|---|---|---|
| 1a | `01a-boot-chain.*` | Reset, Monitor 1.00, boot control `0xb7c`, main-info header check, flash load or card RAM loader, application, update gate | [02](../02-boot-chain-and-monitor.md) |
| 1b | `01b-update-state-machine.*` | Gate `0x1eb22`, prompt `0x18`, checking `0x1c`, updating `0x19`, type-2 and type-5 drivers, error screens, exits, the point of no return | [03](../03-firmware-image-and-update-format.md), [09](../09-recovery-and-failure-modes.md) |
| 2 | `02-flash-layout.*` | OneNAND blocks 0 to `0x1ff`: Monitor, replace table, main body, main info `0x3c`, store `0x3d`, extend data, `/IROM`; which update file rewrites each | [01](../01-hardware-and-memory-map.md), [03](../03-firmware-image-and-update-format.md) |
| 3 | `03-bw-tone-chain.*` | IMG SET row, `0x1b030`, detail `0x5b7e4`, `0x1b197`, `0x92cc8`, `0x92050`, `0x8ffc8`, LUTs `0x193b30..`, shutter route; the emulated slot `0xe` gate that Koma does not use | [06](../06-image-pipeline-and-colour.md) |
| 4 | `04-ca1-vbus-decoder.*` | Pulse-width windows, states S0 to S4, RL1/RL2/release events, 500 ms PC-mode lock-out | [07](../07-usb-protocol.md) |
| 5 | `05-usb-session.*` | Frame layout, bring-up, command and reply, download (`150`, data, `250`), upload | [07](../07-usb-protocol.md) |
| 6 | `06-settings-marker-timeline.*` | Marker groups by first version, and which groups 2.40 initialises over each older block | [05](../05-settings-and-adjustment-store.md), [12](../12-version-history.md) |
| 7 | `07-version-timeline.*` | 1.08 to 2.40, dates, payload sizes, held or missing, headlines | [12](../12-version-history.md) |
| 8 | `08-arm-family-tree.*` | Models by load-address era, percent identical code, 500G fork from GR 2.01, M32R generation | [12](../12-version-history.md) |
| 9 | `09-module-loader.*` | Generation 1 loader: hook `0x281ee`, cave `0x57f78`, `GRMOD.BIN` header, service table, per-image hook sites | [11](../11-modification-playbook.md) |

## Notes

- 1b: the type-5 step never returned in the emulator, so its success exit is not observed.
- 3: the stock tables are byte-identical 1.08 to 2.40 (`STATIC`). The slot `0xe` gate ran only in the emulator; Koma uploads curves from a package instead.
- 6: 2.20, 2.21 and 2.30 are not held; their dating is bounded (dashed bars). Based on default settings blocks.
- 7: releases are spaced by order, not by date.
- 8: percentages are identical-code share from the project's matcher; lineage arrows and the fork are `INFERRED`, plain lines are similarity only.
- 9: the loader drawn is the generation 1 loader (`HW`, both bodies). Generation 2 adds a fallback to `/IROM/GRMOD.BIN` when no card file opens, which the diagram does not draw; see [11](../11-modification-playbook.md).
