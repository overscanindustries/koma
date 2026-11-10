# Vendored from analysis/menu-5tab/menu5v2.py (five-tab menu v2 header band). Edits: imports, firmware/data paths, writers removed.
"""5-tab menu module v2: v1 (menu5.py) plus an opaque header band that hides the stock tab artwork.

Hardware bug in v1 (hw-photos/IMG_3374..3378): the menu background is a JPEG on the VIDEO plane, chosen by the
renderer at 0x14d7ea..0x14d840 and loaded by 0x8e3d0(index) -> 0xd76a0 -> 0x81cb8 (hardware JPEG decoder):
  firmware tab 1 (mode app): index = byte 0x235558[page]  (still pages -> 1 = 0x1c35e0, playback page -> 4 = 0x1d3941)
  firmware tab 2 (SETUP app): index 2 = 0x1c8d29, or 8 = 0x1ce349 when entered from playback (page 5)
Each 480x360 JPEG carries the stock two-tab header: a dark-blue active tab panel and a grey inactive tab, with the
camera (or play) pictogram on the left tab and the wrench on the right one. The OSD strip of v1 draws text only, so
its transparent pixels (index 0) show that artwork, and the inactive labels (style 4, dark text) vanish over the dark
panel. The emulator never showed this because grdemu has no JPEG engine (see videoplane.py).

Fix: `strip` first fills the header band of the OSD layer, rect (0, 0, 352, 33), with palette index 5 (dark grey
0x200000, no bits 24-25 set, like the opaque yellow highlight 0xb; index 5 is also a stock fill colour, the body
frame line at 0x14cc33), then draws the five labels as in v1. This covers the
whole tab artwork in every background (indexes 1, 2, 4, 8; the art ends at OSD row 33) whatever the video plane
holds, needs no new patch site, and leaves the stock path untouched when the module is absent. A first build used index 7 (grey
0x6d0000): the inactive labels (style 4) are drawn in that same grey and disappeared, so the band is dark instead,
giving the grey labels ~3.8:1 contrast and the yellow box full contrast. Row 33 is left alone:
the rows-state 'back to header' marker (image 0x9b at y 33) is drawn before the strip.
KOMA row value -> V0.2 so the card file in use can be told apart on the camera.
"""
from __future__ import annotations

from . import menu5 as M  # noqa: F401

BAND = dict(x=0, y=0, w=352, h=33, colour=5)

ANCHOR = """    sub sp, #32                         @ sp+0 element (0x14), sp+0x14 rect (8)
    movs r7, r0
"""
BAND_SRC = ANCHOR + f"""    mov r1, sp                          @ v2: opaque header band over the stock tab artwork (video plane)
    movs r0, #{BAND['x']}
    strh r0, [r1, #0x14]
    movs r0, #{BAND['y']}
    strh r0, [r1, #0x16]
    ldr r0, ={BAND['w']}
    strh r0, [r1, #0x18]
    movs r0, #{BAND['h']}
    strh r0, [r1, #0x1a]
    movs r0, r7
    add r1, sp, #0x14
    movs r2, #{BAND['colour']}
    ldr r3, =0x174c0d                   @ fill rect (layer, rect, colour index)
    bl call_r3
"""
