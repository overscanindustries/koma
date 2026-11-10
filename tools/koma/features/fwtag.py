"""fwtag: stamp this package's version into the photo MakerNote firmware tag at boot.

The GR DIGITAL writes a 7-character firmware tag into every JPEG and DNG (MakerNote tag 0x2, ASCII, count 8). Stock
2.40 formats it from the printf format at 0x112578 ('Rev%4.4x' -> 'Rev0240'); each Koma firmware image replaces that
format with a fixed string ('Rev024M' flash-1, '240K2' build 2b, '240K1.0' the Koma 1.0 image). The firmware is
flashed rarely and the package is replaced often, so this init rewrites the slot in RAM with the package's own tag
(linker.photo_tag(): '240K1.0' for Koma 1.0). A Koma 1.1 package on the 1.0 firmware then tags its photos '240K1.1'.

Static facts (2.40 code):
  * 0x112120 zeroes the 8-byte stack buffer sp+0x78 (0x1121da..0x1121e0), runs sprintf(sp+0x78, <0x112578>, hdr+0x12)
    (0x1121f0 adr r1 -> 0x112578, bl 0x157c34 at 0x1121f4), copies 8 bytes to MakerNote +0x7a (0x1121f8..0x1121fe,
    bl 0x157bac) and declares tag 0x2 type 2 count 8 (0x1121ce..0x1121d6). So a tag is at most 7 characters + NUL.
  * The slot is 9 bytes (0x112578..0x112580), followed by 3 zero bytes before the literal pool at 0x112584. The
    payload runs from RAM at its load address, so 0x112578 is a RAM address; the format is read at every shot.
  * settingsio's KSIO sub-header copies 7 bytes from 0x112578 into PARAM.CUR every boot: with this feature before
    settingsio, PARAM.CUR carries the package's tag too.

What the init does (transient, 0 resident bytes): when the slot holds a known Koma or stock format (it starts with
'Rev' or '240K' and its 9th byte is NUL), write the tag, NUL-padded to 9 bytes. Anything else: nothing (the slot is not
what this feature was built against). The code that reads the slot is verified by Check records, never written.
Result byte 0x1b8e8 (Koma per-camera area, never imported): 'T' marker in 0x1b8e8, 1 = written, 2 = left alone.
"""
from __future__ import annotations

from .. import firmware as fw
from ..feature import Check, Feature, Settings

NAME = "fwtag"
SUMMARY = "stamp the package version into the photo MakerNote firmware tag (RAM slot 0x112578) at boot"
OPTIONS = {"tag": "firmware tag to write (<= 7 printable characters, no '%'); default linker.photo_tag(), '240K1.0'"}

SLOT, SLOT_LEN = 0x112578, 9
RESULT = 0x1B8E8                 # 2 bytes in the per-body KSIO reserve 0x1b8e8..0x1b8e9: 'T', 1 written / 2 left alone
CHECKS = [
    (0x1121CE, 0x34, "0x112120: tag 0x2 type 2 count 8, zeroed 8-byte buffer sp+0x78, adr r1 -> 0x112578, "
                     "sprintf 0x157c34, 8-byte copy 0x157bac to MakerNote +0x7a"),
    (0x112581, 3, "padding after the 9-byte format slot (literal pool starts at 0x112584)"),
]


def default_tag() -> str:
    from ..linker import photo_tag
    return photo_tag()


def check_tag(tag: str) -> bytes:
    b = tag.encode("ascii")
    if not 1 <= len(b) <= 7 or b"%" in b or any(c < 0x20 or c > 0x7E for c in b):
        raise ValueError(f"fwtag.tag {tag!r}: 1..7 printable ASCII characters, no '%'")
    return b + b"\0" * (SLOT_LEN - len(b))


def source(slot: bytes) -> str:
    return f"""
@ fwtag init (transient): r0 = service table, r1 = body base. Runs in MainCtrl at install, before settingsio.
ft_init:
    push {{r4, lr}}
    ldr r4, =0x{SLOT:x}
    movs r0, #2                 @ result: left alone
    ldrb r1, [r4, #8]
    cmp r1, #0
    bne ft_done                 @ 9th byte not NUL: not a slot this feature knows
    ldrb r1, [r4]
    cmp r1, #0x52               @ 'R': 'Rev%4.4x' (stock), 'Rev024M' (flash-1)
    bne 1f
    ldrb r1, [r4, #1]
    cmp r1, #0x65               @ 'e'
    bne ft_done
    ldrb r1, [r4, #2]
    cmp r1, #0x76               @ 'v'
    bne ft_done
    b ft_write
1:  cmp r1, #0x32               @ '2': '240K...' (Koma firmware tags)
    bne ft_done
    ldrb r1, [r4, #1]
    cmp r1, #0x34               @ '4'
    bne ft_done
    ldrb r1, [r4, #2]
    cmp r1, #0x30               @ '0'
    bne ft_done
    ldrb r1, [r4, #3]
    cmp r1, #0x4b               @ 'K'
    bne ft_done
ft_write:
    ldr r2, =tag
    movs r3, #0
2:  ldrb r1, [r2, r3]
    strb r1, [r4, r3]
    adds r3, #1
    cmp r3, #{SLOT_LEN}
    blo 2b
    movs r0, #1                 @ result: written
ft_done:
    ldr r1, =0x{RESULT:x}
    strb r0, [r1, #1]
    movs r0, #0x54              @ 'T'
    strb r0, [r1]
    pop {{r4}}
    pop {{r3}}
    bx r3
.ltorg
.p2align 2
tag:
    .byte {", ".join(f"0x{c:02x}" for c in slot)}
.p2align 2
"""


def make(opts):
    opts = dict(opts or {})
    tag = str(opts.get("tag", default_tag()))
    slot = check_tag(tag)
    o = {} if "tag" not in opts else {"tag": tag}       # default builds record no option (the tag follows the version)
    return Feature(
        name=NAME, version="1.0", summary=SUMMARY, options=o,
        source=source(slot), init="ft_init", transient=True,
        checks=[Check(a, fw.ram(a, n), why) for a, n, why in CHECKS],
        settings=[Settings(RESULT, RESULT + 2, True, "fwtag result ('T', 1 written / 2 left alone)")],
        ram_writes=[(SLOT, SLOT + SLOT_LEN, "MakerNote firmware tag format slot: its bytes differ per Koma firmware "
                                            "('Rev024M', '240K2', '240K1.0'), so no DATA record; read only by the "
                                            "shot-time header writer 0x112120, never during boot")],
        smoke=smoke_checks)


# ---------------------------------------------------------------- koma smoke

def smoke_checks(obs):
    """The smoke boots the module-loader image (stock format 'Rev%4.4x' in the slot): after install the slot holds this
    package's tag, so the next shot's MakerNote carries it."""
    want = check_tag(obs.feature_options.get(NAME, {}).get("tag", default_tag()))
    got = obs.ram(SLOT, SLOT_LEN)
    res = obs.ram(RESULT, 2)
    return [("slot 0x112578 holds the package tag", got == want, f"{got!r}"),
            ("result 'T' 1 (written)", res == b"T\x01", res.hex())]
