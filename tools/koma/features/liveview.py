"""liveview: pull one live-view frame over USB per `-LVFR <mode>,<seq>` (ORIGINAL-mode vendor session, tether held).

Transport: the dead `-FDWL` record (ID 0x1a) of the command table 0x1fb400 becomes
`-LVFR`. The stock data-phase state machine already lets ID 0x1a carry one data frame (0xcb834 state 4 and 0xcb9cc
state 6 continue for IDs 2, 10, 13, 14 and 26), so three record edits and one resident handler give
`150` + one data frame + `250`, exactly as `-PRET` (0xccbea) does:

    Pointer 0x1fb784  name     000cb64c ("-FDWL")       -> "-LVFR"
    Patch   0x1fb794  args     00000000                 -> 00000001 (parser 0xcbd64 reads `a,b` into 0x1fb3e8/ec)
    Pointer 0x1fb7a4  handler  000cd39f (bx lr, thumb)  -> lvfr_handler

The handler runs in tDcx (dispatcher 0xcc304 -> 0x157acc `bx r2`), signature h(char **reply, int *len):

    gate: [0x1f502f] == 1 (monitor running) and [0x1bd1c0] == 2 (tether-held still state), else 451, no data
    mode = a (0, 1 or 2; a > 2 with a & 0xf == 3 is mode 3, scale a >> 4; else 501; checked before the gate), seq = b
    mode 2 (OSD page pull, no gate: the menu stops the monitor and state-9 USB screens are OSD too):
        page = [0x1200c080] | 0x08000000 & ~3 (A 0x08422800, B 0x0842f280 -> flags bit 2; neither -> page A, bit 4)
        payload = 240 rows x 180 bytes (360 px, 4 bpp, high nibble left) + 16 x u32 palette ([0x12020100 + 4i],
        entry 0 sent as 0); header width 360, height 240, length 43,264, scale 16 (palette entries); then the tail
    sync (option, default on): 0x9e218() waits for the next monitor VD (timeout 300 ticks (0xff + 0x2d = 0x12c); non-zero = flags bit 1)
    gate again; v0 = [0x1f511c] (VD count); d = [0x1f501c] (display slot, stable for two VD periods); Y = [0x2cf640+4d], C = [0x2cf658+4d]
    resize 0x5e8f0: 640x244 stride 672 -> mode 0: 160x120 stride 192 | mode 1: 320x240 stride 352 (dst stride must be
    w + 32: 0x5e8f0 rewrites any other stride and returns -1; the movie thumb uses 160x120 at 0xc0)
    torn guard: [0x1f511c] - v0 >= 2 -> flags bit 3 (no retry)
    gate again; mode 1: JPEG 0x5e5cc(desc 320x240, scale, out) into OUT, limit 64 KiB; length < 0 or no FF D9 at out+L-2 -> 451
    payload = mode 0: the 19,200 Y bytes (120 rows of 160, packed from stride 192) | mode 1: the scan bytes (no header; the host adds SOI/DQT(scale)/SOF0/DHT/SOS)
    capped at 65,536 - 16 (flags bit 0 = truncated); copied with memcpy 0x157bac behind a 16-byte header at
    [0x1bd308]+8, then the -PRET state: [0x1bd2bc] = 0x8001, [0x1bd358] = [0x1bd35c] = total, [0x1bd344] = "250",
    [0x1bd2d0] = 4, reply "150".

    mode 3 (v1.3): gate, APO hold, VD sync, slot read as above, then NO resize: 0x5e5cc(desc) with Y/C = the ring slot,
        stride 672, 640x240 (the 244-line slot floors to 30 MCU rows of 8), out 0x8572000 (the resize scratch, unused
        in this mode), limit out + 65,512 so a found EOI always fits one chunk; scale = a >> 4, or the build's scale3
        when 0. Fail (length <= 0, > 65,520 or no FF D9) -> one re-encode at 2x the scale (flags bit 5), then 451.
        Torn guard spans the encode(s). Header width 640, height 240, scale = the scale used. Pixels are about 2:1 tall:
        display at 4:3.

Payload header (16 bytes, big-endian; frame bytes follow at +16):
    +0 'LVFR'  +4 u8 mode  +5 u8 flags (bit0 truncated, bit1 VD sync timed out, bit2 OSD page B, bit3 torn (modes 0/1:
    [0x1f511c] VD count advanced >= 2 between the slot read and the resize return; mode 3: the encode return), bit4 OSD
    base register unrecognised, bit5 mode 3 re-encoded at 2x scale)  +6 u16 seq  +8 u16 width  +10 u16 height  +12 u16 length (payload bytes after the header)
    +14 u16 scale (0 in mode 0; JPEG scale in mode 1; palette entry count 16 in mode 2)

Scratch memory (no pool allocation): the gap between the monitor ring end (CMEM_MONI_START 0x8470000 + 0xfff00 =
0x856ff00) and CMEM_STILL_TOP_NEW 0x85c5e00, which only movie mode (audio 0x856ff00, display copies
0x858f400/0x85a7a00) and non-live display paths (0xf144c's 0x8571d00, playback 0x112bdc's 0x8570000) use. The frames
lane's first proposal (Y 0x85c0000, C 0x860ec00, OUT 0x865d800) was dropped: 84,480 B from 0x85c0000 runs over
CMEM_STILL_THUMB_Y 0x85c5e00, and 0x860ec00 / 0x865d800 lie inside CMEM_STILL_MASTER_Y (0x8605f00..), which every shot
writes and the card writer may still read after live view resumes.

v1.2 (2026-11-07): after the gate passes (modes 0/1), the handler calls 0x290de(2), the Auto Power Off hold tether
issues (tether.py v1.3/v1.4), so a host that only pulls live frames keeps the camera on (hardware: APO 1 min fired
while the probe sent only -NLST/-LVFR). State 2 under USB exists only after tether
diverged, and tether's exits restore the setting.

Not touched: no flash or store-write routine, no settings byte, no menu row, no hook on stock code. Without tether
(state 9 under USB) modes 0/1 answer 451; mode 2 still answers.
"""
from __future__ import annotations

import struct

from .. import firmware as fw
from ..feature import Check, Feature, Patch, Pointer

NAME = "liveview"
VERSION = "1.3"
SUMMARY = ("USB live view: -LVFR <mode>,<seq> returns one resized live-view frame (Y 160x120 or JPEG 320x240 scan), "
           "the OSD page (mode 2: 360x240 4 bpp + 16 palette entries) or the full ring slot as JPEG (mode 3: 640x240 "
           "scan, non-square pixels, display at 4:3)")
OPTIONS = {"sync": "on (default) | off: wait for the next monitor VD (0x9e218) before reading the display slot",
           "scale": "JPEG quant scale for mode 1, 1..65535 (default 448; 1024 = base table unchanged; q = clamp(((scale*base >> 9)+1)>>1, 1, 255); movie mode's real DQT scales invert to 320..531)",
           "scale3": "JPEG quant scale for mode 3 when the host sends none (a >> 4 == 0), 64..65535 (default 448)"}

REC = 0x1FB784                   # -FDWL record (36 B): [0] name, [1] id 0x1a, [4] args flag, [8] handler
NAME_PTR, ARGS_FLAG, HANDLER_PTR = REC, REC + 0x10, REC + 0x20
NAME_BEFORE = bytes.fromhex("000cb64c")
ARGS_BEFORE, ARGS_AFTER = bytes(4), struct.pack(">I", 1)
HANDLER_BEFORE = bytes.fromhex("000cd39f")

MONI_RUN = 0x1F502F              # u8, 1 while the monitor runs (set 0x98814, cleared at capture 0x97644 / stop 0x98a32)
APP_STATE = 0x1BD1C0             # 2 = still record state (tether keeps it under USB)
SLOT = 0x1F501C                  # u32 display slot d (0x9931c)
RING_Y, RING_C = 0x2CF640, 0x2CF658
ARG_A = 0x1FB3E8                 # parser args a (mode), b (seq) at +4
USB_BUF = 0x1BD308               # -> 0x0bf844e0; payload at +8
HANDLE, TOTAL, CHUNK, FINAL, XSTATE = 0x1BD2BC, 0x1BD358, 0x1BD35C, 0x1BD344, 0x1BD2D0
REPLY_FLAG, REPLY_FLAG2 = 0x1BD2C2, 0x1BD2C0
VD_WAIT, RESIZE, JPEG, MEMCPY, STRLEN = 0x9E218, 0x5E8F0, 0x5E5CC, 0x157BAC, 0x158090

SCR_Y, SCR_C, SCR_OUT = 0x8572000, 0x8587000, 0x859C000   # 0x14a00 each for Y/C (352 x 240), 64 KiB out
SCR_END = SCR_OUT + 0x10000
GAP = (0x856FF00, 0x85C5E00)
SRC_W, SRC_H, SRC_STRIDE = 640, 244, 672
MODES = {0: (160, 120, 192), 1: (320, 240, 352)}          # dst w, h, stride (0x5e8f0 demands w + 32, else -1)
HDR = 16
MAX_PAYLOAD = 0x10000 - HDR
FLAG_TRUNC, FLAG_NOSYNC = 1, 2
# mode 2 (OSD page pull): the OSD page table (table 0x1fba14: pages, width 0x168, height 0x120 allocated, stride 0xb4)
OSD_BASE_REG = 0x1200C080        # OSD DMA base, written only by 0x16f3b0; read by the stock getter 0x16f3a4
OSD_PAGE_A, OSD_PAGE_B = 0x08422800, 0x0842F280
OSD_W, OSD_H, OSD_STRIDE = 360, 240, 0xB4                  # 4 bpp, high nibble = left pixel
OSD_BYTES = OSD_STRIDE * OSD_H                             # 43,200: stride == width / 2, rows already packed
VEN_PALETTE = 0x12020100         # palette i at +4i (i 1..63; 0x161f18), Y<<16 | Cb<<8 | Cr, Cb/Cr signed
OSD_PAL_N = 16                   # entries sent after the pixels (4 bytes each, big-endian, entry 0 = 0)
OSD_LEN = OSD_BYTES + 4 * OSD_PAL_N                        # 43,264 payload bytes
FLAG_PAGE_B, FLAG_PAGE_GUESS = 4, 16
FLAG_TORN = 8                    # modes 0/1: VD counter advanced by >= 2 across slot read + resize; mode 3: + encode
# mode 3 (v1.3): the display slot itself, 640x240 at stride 672, JPEG-encoded straight from the ring (no resize pass).
# Geometry: zoom-step-0 entries of the ring tables 0x193838 (w 640) / 0x193876 (h 244) / 0x1935c4 (stride 672); the
# encoder's 4:2:2 path counts MCUs as (w >> 4) * (h >> 3) (mode byte 2 at 0x162df4 -> 0x163224; 0x16325c mcuNum <= 0xffff), so
# 244 lines encode as 240. Encoder checks (0x162b6c, sdl_jpg_api.c): w % 16 == 0 (0x162d62), w <= stride (0x162d7c),
# stride % 4 == 0 (0x162d48), addresses 4-aligned and in SDRAM; no maximum width or height.
M3_W, M3_H = 640, 240
M3_ARG_MODE = 3                  # a & 0xf == 3; a >> 4 = JPEG scale override (0 = the build's scale3)
SCR_OUT3 = SCR_Y                 # mode 3 needs no resize scratch: out at 0x8572000, gap end 0x85c5e00 is overrun slack
M3_LIMIT = SCR_OUT3 + MAX_PAYLOAD - 8   # 0x5e2ec returns L <= limit - start + 5 when it finds EOI: always fits one chunk
FLAG_REENC = 0x20                # mode 3: the first encode failed / overran; re-encoded once at twice the scale
VD_COUNT = 0x1F511C              # u32 monitor VD counter
APO = 0x290DE                    # Auto Power Off control 0x290de(mode): 2 = off (hold); see tether.py v1.3/v1.4
S150 = "150 -LVFR\r\n"
S250 = "250 -LVFR\r\n"
S451 = "451 -LVFR off\r\n"
S501 = "501 -LVFR mode\r\n"


def _source(sync: bool, scale: int, scale3: int = 448) -> str:
    sync_src = f"""
    ldr r3, ={VD_WAIT + 1:#x}
    bl call_r3                  @ 0x9e218: wait for the next monitor VD; 0 = synced, -1 = timeout
    cmp r0, #0
    beq 1f
    movs r0, #{FLAG_NOSYNC}
    orrs r7, r0
1:  bl gate
    cmp r0, #0
    beq 13f
    b fail451
13:
""" if sync else ""
    return f"""
@ lvfr_handler(r0 = char **reply, r1 = int *len), run by the -FDWL/-LVFR record in tDcx.
@ Frame (sp): +0x00 resize descriptor (0x40), +0x40 JPEG descriptor (0x40), +0x80 JPEG out struct (0x20),
@ +0xa0 reply ptr, +0xa4 len ptr, +0xa8 VD count at the slot read. r4 = mode, r5 = seq, r6 = payload length, r7 = flags.
lvfr_handler:
    push {{r4, r5, r6, r7, lr}}
    sub sp, #0xb0
    str r0, [sp, #0xa0]
    str r1, [sp, #0xa4]
    ldr r0, ={HANDLE:#x}
    ldr r1, ={0x8001:#x}
    str r1, [r0]                @ RAM-sourced verb: the end-of-transfer close of 0x8001 is a no-op
    ldr r0, ={ARG_A:#x}
    ldr r4, [r0]                @ mode
    ldr r5, [r0, #4]            @ seq
    cmp r4, #2
    bne 1f
    b osd                       @ mode 2: OSD page, no monitor/state gate
1:  bls 1f                      @ (flags still from cmp r4, #2: mode 0 or 1)
    movs r0, #0xf
    ands r0, r4
    cmp r0, #{M3_ARG_MODE}
    bne 12f
    b m3                        @ v1.3: a & 0xf == 3 (a > 2 only, so a = 0/1/2 keep their paths)
12: b fail501
1:  bl gate
    cmp r0, #0
    beq 1f
    b fail451                   @ (conditional branches do not reach the tail)
1:  movs r0, #2
    ldr r3, ={APO + 1:#x}
    bl call_r3                  @ v1.2: hold Auto Power Off while the host pulls frames (state 2 under USB = tether)
    movs r7, #0
{sync_src}
    mov r0, sp                  @ zero the descriptors
    movs r1, #0
    movs r2, #0xa0
2:  subs r2, #4
    str r1, [r0, r2]
    bne 2b
    ldr r0, ={VD_COUNT:#x}
    ldr r0, [r0]
    str r0, [sp, #0xa8]         @ torn guard: VD count before the slot read
    ldr r0, ={SLOT:#x}
    ldr r0, [r0]
    cmp r0, #2
    bls 13f
    b fail451
13:
    lsls r0, r0, #2
    ldr r1, ={RING_Y:#x}
    ldr r1, [r1, r0]
    str r1, [sp, #0x00]         @ srcY
    ldr r1, ={RING_C:#x}
    ldr r1, [r1, r0]
    str r1, [sp, #0x04]         @ srcC
    ldr r1, ={SRC_STRIDE}
    str r1, [sp, #0x08]
    ldr r1, ={SCR_Y:#x}
    str r1, [sp, #0x14]         @ dstY
    ldr r1, ={SCR_C:#x}
    str r1, [sp, #0x18]         @ dstC
    ldr r1, ={SRC_W}
    str r1, [sp, #0x28]
    movs r1, #{SRC_H}
    str r1, [sp, #0x2c]
    cmp r4, #0
    bne 3f
    movs r1, #{MODES[0][2]}
    movs r2, #{MODES[0][0]}
    movs r3, #{MODES[0][1]}
    b 4f
3:  ldr r1, ={MODES[1][2]}
    ldr r2, ={MODES[1][0]}
    movs r3, #{MODES[1][1]}
4:  str r1, [sp, #0x1c]         @ dst stride
    str r2, [sp, #0x30]         @ dst w
    str r3, [sp, #0x34]         @ dst h
    mov r0, sp
    ldr r3, ={RESIZE + 1:#x}
    bl call_r3                  @ 0x5e8f0(desc): Y pass then C pass, waits for each
    cmp r0, #0
    beq 13f
    b fail451
13: ldr r0, ={VD_COUNT:#x}
    ldr r0, [r0]
    ldr r1, [sp, #0xa8]
    subs r0, r0, r1
    cmp r0, #2                  @ slot d is rewritten at VD k+2: >= 2 VDs since the slot read may have torn it
    blo 13f
    movs r0, #{FLAG_TORN}
    orrs r7, r0                 @ flag only, no retry
13:
    bl gate                     @ a shot may have started while we waited
    cmp r0, #0
    beq 13f
    b fail451
13:
    ldr r6, ={MODES[0][0] * MODES[0][1]}
    cmp r4, #0
    beq 5f
    add r0, sp, #0x40           @ JPEG descriptor: [0] Y [1] C [2] stride [5] w [6] h [7] start [8] limit
    ldr r1, ={SCR_Y:#x}
    str r1, [r0, #0x00]
    ldr r1, ={SCR_C:#x}
    str r1, [r0, #0x04]
    ldr r1, ={MODES[1][2]}
    str r1, [r0, #0x08]
    ldr r1, ={MODES[1][0]}
    str r1, [r0, #0x14]
    movs r1, #{MODES[1][1]}
    str r1, [r0, #0x18]
    ldr r1, ={SCR_OUT:#x}
    str r1, [r0, #0x1c]
    ldr r1, ={SCR_END:#x}
    str r1, [r0, #0x20]
    ldr r1, ={scale:#x}
    add r2, sp, #0x80
    ldr r3, ={JPEG + 1:#x}
    bl call_r3                  @ 0x5e5cc(desc, scale, out): scan + EOI bytes, returns the length or -1
    cmp r0, #0
    bgt 13f
    b fail451
13:
    ldr r1, ={SCR_OUT:#x}       @ the encoder skips its EOI scan on error/timeout and can still return a positive length:
    adds r1, r1, r0             @ require FF D9 at out+L-2
    subs r1, #2
    ldrb r2, [r1, #0]
    cmp r2, #0xff
    beq 14f
    b fail451
14: ldrb r2, [r1, #1]
    cmp r2, #0xd9
    beq 15f
    b fail451
15: movs r6, r0
5:  ldr r0, ={MAX_PAYLOAD}
    cmp r6, r0
    bls 6f
    movs r6, r0
    movs r0, #{FLAG_TRUNC}
    orrs r7, r0
6:  ldr r0, ={USB_BUF:#x}
    ldr r0, [r0]
    adds r0, #8                 @ payload area of the current half (header at +0)
    ldr r2, =0x4c564652         @ 'LVFR'
    str r2, [r0, #0]
    strb r4, [r0, #4]
    strb r7, [r0, #5]
    strh r5, [r0, #6]
    ldr r2, [sp, #0x30]
    strh r2, [r0, #8]
    ldr r2, [sp, #0x34]
    strh r2, [r0, #10]
    strh r6, [r0, #12]
    movs r2, #0
    cmp r4, #0
    beq 7f
    ldr r2, ={scale:#x}
7:  strh r2, [r0, #14]
    adds r0, #{HDR}
    cmp r4, #0
    bne 10f
    movs r5, r0                 @ mode 0: pack 120 rows of 160 from stride 192
    ldr r6, ={SCR_Y:#x}
    movs r7, #{MODES[0][1]}
11: movs r0, r5
    movs r1, r6
    movs r2, #{MODES[0][0]}
    ldr r3, ={MEMCPY + 1:#x}
    bl call_r3
    adds r5, #{MODES[0][0]}
    adds r6, #{MODES[0][2]}
    subs r7, #1
    bne 11b
    ldr r6, ={MODES[0][0] * MODES[0][1]}
    b send
10: ldr r1, ={SCR_OUT:#x}       @ mode 1: the scan bytes
    movs r2, r6
    ldr r3, ={MEMCPY + 1:#x}
    bl call_r3                  @ memcpy(payload + 16, out, n)
send:
    adds r6, #{HDR}
    ldr r0, ={TOTAL:#x}
    str r6, [r0]
    ldr r0, ={CHUNK:#x}
    str r6, [r0]
    ldr r0, ={FINAL:#x}
    ldr r1, =s250
    str r1, [r0]
    ldr r0, =s150
    bl set_reply
    movs r1, #4                 @ state 4: "150 sent" -> one data frame (ID 0x1a) -> 250
    b 9f

@ mode 2: the OSD page the OSD DMA scans out ([0x1200c080], written by 0x16f3b0 as (a & 0xeffffffc) | 0x08000000;
@ stock getter 0x16f3a4), 240 rows of 180 bytes (stride 0xb4 = 360 px at 4 bpp, so rows are already packed), then the
@ 16 palette registers 0x12020100 + 4i (entry 0 has no register and is sent as 0). No VD wait, no engine call.
osd:
    movs r7, #0
    ldr r0, ={OSD_BASE_REG:#x}
    ldr r0, [r0]
    ldr r1, ={0x08000000:#x}
    orrs r0, r1
    ldr r1, ={0x0FFFFFFC:#x}
    ands r0, r1
    ldr r6, ={OSD_PAGE_B:#x}
    cmp r0, r6
    bne 1f
    movs r7, #{FLAG_PAGE_B}
    b 2f
1:  ldr r6, ={OSD_PAGE_A:#x}
    cmp r0, r6
    beq 2f
    movs r7, #{FLAG_PAGE_GUESS}     @ base register names neither page: page A sent
2:  ldr r0, ={USB_BUF:#x}
    ldr r0, [r0]
    adds r0, #8
    ldr r2, =0x4c564652         @ 'LVFR'
    str r2, [r0, #0]
    strb r4, [r0, #4]
    strb r7, [r0, #5]
    strh r5, [r0, #6]
    ldr r2, ={OSD_W}
    strh r2, [r0, #8]
    movs r2, #{OSD_H}
    strh r2, [r0, #10]
    ldr r2, ={OSD_LEN}
    strh r2, [r0, #12]
    movs r2, #{OSD_PAL_N}
    strh r2, [r0, #14]
    adds r0, #{HDR}
    movs r5, r0                 @ r5 = pixel destination
    movs r1, r6
    ldr r2, ={OSD_BYTES}
    ldr r3, ={MEMCPY + 1:#x}
    bl call_r3                  @ memcpy(payload + 16, page, 43200)
    ldr r0, ={OSD_BYTES}
    adds r5, r5, r0             @ palette destination
    ldr r1, ={VEN_PALETTE:#x}
    movs r2, #0
    str r2, [r5, #0]            @ entry 0: no register (transparent)
    movs r2, #4
3:  ldr r0, [r1, r2]
    str r0, [r5, r2]
    adds r2, #4
    cmp r2, #{OSD_PAL_N * 4}
    blo 3b
    ldr r6, ={OSD_LEN}
    b send

@ mode 3 (v1.3): r4 = 3, r5 = seq, r6 = JPEG scale, r7 = flags; [sp, #0xac] = scan length.
@ The encoder reads the display slot directly: same JPEG descriptor as mode 1 but Y/C = ring slot, stride 672, 640x240,
@ out 0x8572000, limit out + 65,512. One retry at 2x scale if the encode fails, overruns or has no EOI.
m3:
    lsrs r6, r4, #4             @ host scale (a >> 4), 0 = the build's scale3
    bne 1f
    ldr r6, ={scale3:#x}
1:  movs r4, #{M3_ARG_MODE}
    bl gate
    cmp r0, #0
    beq 1f
    b fail451
1:  movs r0, #2
    ldr r3, ={APO + 1:#x}
    bl call_r3                  @ hold Auto Power Off, as modes 0/1
    movs r7, #0
{sync_src}
    mov r0, sp                  @ zero the descriptors
    movs r1, #0
    movs r2, #0xa0
2:  subs r2, #4
    str r1, [r0, r2]
    bne 2b
    ldr r0, ={VD_COUNT:#x}
    ldr r0, [r0]
    str r0, [sp, #0xa8]         @ torn guard: VD count before the slot read
    ldr r0, ={SLOT:#x}
    ldr r0, [r0]
    cmp r0, #2
    bls 13f
    b fail451
13: lsls r0, r0, #2
    ldr r1, ={RING_Y:#x}
    ldr r1, [r1, r0]
    str r1, [sp, #0x40]         @ JPEG [0] Y = ring slot
    ldr r1, ={RING_C:#x}
    ldr r1, [r1, r0]
    str r1, [sp, #0x44]         @ JPEG [1] C = ring slot (4:2:2 semi-planar, same stride: the resize's own input)
m3_enc:
    bl gate                     @ a shot may have started while we waited
    cmp r0, #0
    beq 13f
    b fail451
13: add r0, sp, #0x40
    ldr r1, ={SRC_STRIDE}
    str r1, [r0, #0x08]
    ldr r1, ={M3_W}
    str r1, [r0, #0x14]
    movs r1, #{M3_H}
    str r1, [r0, #0x18]
    ldr r1, ={SCR_OUT3:#x}
    str r1, [r0, #0x1c]
    ldr r1, ={M3_LIMIT:#x}
    str r1, [r0, #0x20]
    movs r1, r6
    add r2, sp, #0x80
    ldr r3, ={JPEG + 1:#x}
    bl call_r3                  @ 0x5e5cc(desc, scale, out)
    cmp r0, #0
    ble m3_retry
    ldr r1, ={MAX_PAYLOAD}
    cmp r0, r1
    bhi m3_retry                @ no FF D9 scan when the count ran past limit + 4: never send a cut frame
    ldr r1, ={SCR_OUT3:#x}
    adds r1, r1, r0
    subs r1, #2
    ldrb r2, [r1, #0]
    cmp r2, #0xff
    bne m3_retry
    ldrb r2, [r1, #1]
    cmp r2, #0xd9
    bne m3_retry
    str r0, [sp, #0xac]
    b m3_ok
m3_retry:
    movs r0, #{FLAG_REENC}
    tst r7, r0
    beq 1f
    b fail451                   @ second failure
1:  orrs r7, r0
    lsls r6, r6, #1
    ldr r0, ={0xFFFF:#x}
    cmp r6, r0
    bls 1f
    movs r6, r0
1:  b m3_enc
m3_ok:
    ldr r0, ={VD_COUNT:#x}
    ldr r0, [r0]
    ldr r1, [sp, #0xa8]
    subs r0, r0, r1
    cmp r0, #2                  @ the engine read the slot: >= 2 VDs since the slot read may have torn it
    blo 13f
    movs r0, #{FLAG_TORN}
    orrs r7, r0
13: ldr r0, ={USB_BUF:#x}
    ldr r0, [r0]
    adds r0, #8
    ldr r2, =0x4c564652         @ 'LVFR'
    str r2, [r0, #0]
    strb r4, [r0, #4]
    strb r7, [r0, #5]
    strh r5, [r0, #6]
    ldr r2, ={M3_W}
    strh r2, [r0, #8]
    movs r2, #{M3_H}
    strh r2, [r0, #10]
    ldr r2, [sp, #0xac]
    strh r2, [r0, #12]
    strh r6, [r0, #14]          @ the scale actually used
    adds r0, #{HDR}
    ldr r1, ={SCR_OUT3:#x}
    ldr r2, [sp, #0xac]
    ldr r3, ={MEMCPY + 1:#x}
    bl call_r3                  @ memcpy(payload + 16, out, L)
    ldr r6, [sp, #0xac]
    b send

fail501:
    ldr r0, =s501
    b 8f
fail451:
    ldr r0, =s451
8:  bl set_reply
    ldr r0, ={REPLY_FLAG:#x}
    movs r1, #1
    strb r1, [r0]
    ldr r0, ={REPLY_FLAG2:#x}
    movs r1, #0
    strb r1, [r0]
    movs r1, #3                 @ state 3: reply only
9:  ldr r0, ={XSTATE:#x}
    str r1, [r0]
    add sp, #0xb0
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3

@ r0 = reply string: *reply = r0, *len = strlen(r0). Reads the handler's frame (sp+8 after this push).
set_reply:
    push {{r4, lr}}
    movs r4, r0
    ldr r1, [sp, #0xa8]         @ reply ptr (frame +0xa0, +8 for this push)
    str r0, [r1]
    ldr r3, ={STRLEN + 1:#x}
    bl call_r3
    ldr r1, [sp, #0xac]
    str r0, [r1]
    pop {{r4}}
    pop {{r3}}
    bx r3

@ r0 = 0 when the monitor runs and the app state is 2 (tether-held still), else 1. Clobbers r0, r1.
gate:
    ldr r0, ={MONI_RUN:#x}
    ldrb r0, [r0]
    cmp r0, #1
    bne 1f
    ldr r0, ={APP_STATE:#x}
    ldr r0, [r0]
    cmp r0, #2
    bne 1f
    movs r0, #0
    bx lr
1:  movs r0, #1
    bx lr

call_r3:
    bx r3
.ltorg
.p2align 2
lvfr_name:
    .asciz "-LVFR"
.p2align 2
s150:
    .asciz "{S150.encode('unicode_escape').decode()}"
.p2align 2
s250:
    .asciz "{S250.encode('unicode_escape').decode()}"
.p2align 2
s451:
    .asciz "{S451.encode('unicode_escape').decode()}"
.p2align 2
s501:
    .asciz "{S501.encode('unicode_escape').decode()}"
"""


# (addr, length, why): stock bytes this feature depends on, verified at install, never written
CHECKS = [
    (REC + 4, 4, "-FDWL record id word = 0x1a (the ID the data phase accepts)"),
    (REC + 8, 8, "-FDWL record path / size words = 0"),
    (REC + 0x14, 12, "-FDWL record -CCTL/-ATTR/-SDIS words = 0"),
    (0xCB8A6, 8, "0xcb834 state 4: `cmp r2,#0xe / cmp r2,#0x1a` lets ID 26 start the data frame"),
    (0xCBA42, 8, "0xcb9cc state 6: `cmp r0,#0xe / cmp r0,#0x1a` lets ID 26 finish with [0x1bd344]"),
    (0xCC304, 0x10, "dispatcher 0xcc304: [0x1bd350] = 0x10000 (chunk limit)"),
    (0xCCCB4, 0x16, "-PRET success tail: reply 150, [0x1bd344] = 250, state 4 (the contract mirrored)"),
    (0x9931C, 2, "0x991fa: display slot d -> [0x1f5014+8] = 0x1f501c"),
    (0x98814, 2, "0x985a0: monitor-active 0x1f502f := 1"),
    (0x97644, 2, "capture: monitor-active 0x1f502f := 0"),
    (VD_WAIT, 8, "0x9e218 VD wait prologue (ldr r4, =0x1f50f8)"),
    (RESIZE, 4, "0x5e8f0 CamImageResize prologue"),
    (JPEG, 0x2A, "0x5e5cc JPEG encode wrapper (0x5e224 / 0x5e2ec / 0x5e478)"),
    (MEMCPY, 4, "0x157bac memcpy prologue"),
    (STRLEN, 4, "0x158090 strlen prologue"),
    (0x16F3A4, 6, "0x16f3a4 OSD base getter: ldr r1, =0x1200c080 ; ldr r1, [r1] ; str r1, [r0]"),
    (0x16F3DA, 0x10, "0x16f3b0 OSD base setter tail: (a & 0xeffffffc) | 0x08000000 -> [0x1200c080]"),
    (0x1FBA18, 0x14, "table 0x1fba14: OSD pages 0x08422800 / 0x0842f280, width 0x168, height 0x120, stride 0xb4"),
    (0x161F68, 8, "0x161f18 palette write: [0x12020100 + 4i] = value"),
    (APO, 6, "0x290de(mode) Auto Power Off control: push {lr}; cmp r0,#1; bne (mode 2 = off)"),
    (0x193838, 2, "ring width table, zoom step 0 = 640 (mode 3 source width)"),
    (0x193876, 2, "ring height table, zoom step 0 = 244 (mode 3 encodes 240 = 30 MCU rows)"),
    (0x1935C4, 2, "ring stride table, zoom step 0 = 672 (mode 3 encoder stride)"),
    (0x162D62, 0x36, "sdl_jpg_api 0x162b6c: dmaWidth % 16 and dmaWidth <= incpx asserts (640 <= 672)"),
]


def make(opts):
    opts = dict(opts or {})
    sync = str(opts.get("sync", "on")).lower()
    if sync not in ("on", "off"):
        raise ValueError(f"liveview.sync={sync!r}: use on or off")
    try:
        scale = int(str(opts.get("scale", 448)), 0)
    except ValueError:
        raise ValueError(f"liveview.scale={opts.get('scale')!r}: use an integer 1..65535") from None
    if not 1 <= scale <= 0xFFFF:
        raise ValueError(f"liveview.scale={scale}: use 1..65535")
    try:
        scale3 = int(str(opts.get("scale3", 448)), 0)
    except ValueError:
        raise ValueError(f"liveview.scale3={opts.get('scale3')!r}: use an integer 64..65535") from None
    if not 64 <= scale3 <= 0xFFFF:
        raise ValueError(f"liveview.scale3={scale3}: use 64..65535")
    return Feature(
        name=NAME, version=VERSION, summary=SUMMARY, options=opts, source=_source(sync == "on", scale, scale3),
        pointers=[Pointer(NAME_PTR, NAME_BEFORE, "lvfr_name", why="-FDWL record name -> \"-LVFR\""),
                  Pointer(HANDLER_PTR, HANDLER_BEFORE, "lvfr_handler", thumb=True,
                          why="-FDWL record handler (bx lr 0xcd39e) -> lvfr_handler")],
        patches=[Patch(ARGS_FLAG, ARGS_BEFORE, ARGS_AFTER, "-FDWL record [4]: parse `a,b` (mode, seq)")],
        checks=[Check(a, fw.ram(a, n), w) for a, n, w in CHECKS])


# `koma smoke` never attaches USB, so the handler is not on its path.
