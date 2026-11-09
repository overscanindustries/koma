# Internals

This is a reference for the original Ricoh GR DIGITAL (2005) running official firmware 2.40, for anyone who wants to build their own changes. It covers the memory map, boot chain, update-file format, settings and image pipeline, USB protocol and how Koma hooks features in. Addresses are 2.40 application runtime addresses unless a page says otherwise. Ricoh firmware is not redistributed; the tools work on your own copy of the official update file.

| Page | Contents |
|---|---|
| [firmware.md](firmware.md) | Hardware, flash layout and memory map, boot chain, update-file format and checksums, application tasks and dispatch |
| [settings-and-image.md](settings-and-image.md) | User settings block, `PARAM.DAT` import and export, factory adjustment store, tone tables, colour registers, JPEG output, tone and colour hook points |
| [usb.md](usb.md) | USB framing, command and reply reference, `-CCTL` remote control, Koma verbs, live view, CA-1 cable release |
| [writing-features.md](writing-features.md) | The Koma loader and package format, calling firmware functions, hook sites, hazards, recovery |
| [addresses.md](addresses.md) | Compact address table of useful functions, data and hook sites |

Status tags appear only where trusting an unverified fact could damage a camera. `HW` was seen on a camera, `EMU` ran only in an emulator, `STATIC` was read from the code and `INFERRED` is deduced.
