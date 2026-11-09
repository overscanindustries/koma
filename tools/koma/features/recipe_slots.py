"""recipe-slots: 5 recipe slots in the settings tail 0x1b800..0x1b8af + per-shot hook at 0x96436 (still develop).

Port of the earlier recipe-slots builder. Reused verbatim from that file: the slot/tail format, the slot defaults, the
curves, the before-byte CHECKS, and the Thumb routines tail_init / active_slot / recall / hook plus their tables.
Replaced by Koma: that module's own installer (verify / BL patch / IRQ mask) -> Koma records, and the hook's hard
`ldr r3, =0x8ff0f` -> `ldr r3, =hook_next` so the hook can chain with other features on the same site.
"""
from __future__ import annotations

from ..feature import Check, Feature, Hook, Settings
from . import _legacy

NAME = "recipe-slots"
SUMMARY = "5 recipe slots (IMG SET/contrast/sharpness/depth/curve/colour regs), per-shot hook 0x96436"
OPTIONS = {"slot": "active slot forced at boot: 1..5, or 'keep' to keep the tail's byte (menu lane). Default 1",
           "reseed": "1: module slot data overwrites the tail every boot; 0: keep user-edited slots. "
                     "Default 1 when a slot is forced, 0 for 'keep'"}

INIT = """
@ ---------------- Koma init: seed the tail, recall the active slot (was the old installer's middle)
koma_init:
    push {lr}
    bl tail_init
    bl recall
    pop {r3}
    bx r3
call_r3:
    bx r3
"""


def _cut(src, start, end=None):
    i = src.index(start)
    j = src.index(end, i) if end else len(src)
    return src[i:j]


def ported_source(force: int, reseed: int) -> str:
    rb = _legacy.recipe_build()
    src = rb.source(force, reseed)
    equs = _cut(src, ".equ TAIL", "@ ---------------- installer")
    routines = _cut(src, "@ ---------------- tail:", "code_end:")
    tables = _cut(src, ".p2align 2\nfieldtab:")
    assert routines.count("ldr r3, =0x8ff0f") == 1, "recipe hook no longer calls 0x8ff0f the way this port expects"
    routines = routines.replace("ldr r3, =0x8ff0f", "ldr r3, =hook_next")
    assert tables.rstrip().endswith("curves:"), "recipe source no longer ends with the curves label"
    tables = tables.rstrip()[:-len("curves:")]
    return equs + routines + INIT + ".ltorg\n" + tables


def make(opts):
    rb = _legacy.recipe_build()
    slot = str(opts.get("slot", "1"))
    if slot == "keep":
        force = 0xFF
    elif slot.isdigit() and 1 <= int(slot) <= rb.NSLOTS:
        force = int(slot)
    else:
        raise ValueError(f"recipe-slots: slot must be 1..{rb.NSLOTS} or 'keep', not {slot!r}")
    reseed = int(opts.get("reseed", 0 if force == 0xFF else 1))
    if reseed not in (0, 1):
        raise ValueError("recipe-slots: reseed must be 0 or 1")
    return Feature(
        name=NAME, version="1.0", summary=SUMMARY, options=dict(slot=slot, reseed=reseed),
        source=ported_source(force, reseed),
        blobs={"curves": b"".join(rb.CURVE_BYTES)},
        init="koma_init",
        hooks=[Hook(rb.SITE, 0x8FF0E, "hook", "hook_next", "still develop 0x96350: mono/fixed-chroma writer 0x8ff0e")],
        checks=[Check(a, bytes.fromhex(h), w) for a, h, w in rb.CHECKS],
        settings=[Settings(rb.TAIL, rb.TAIL + rb.TAIL_LEN, True, "RCP1 slot store, active byte 0x1b806"),
                  Settings(0x1B030, 0x1B037, False, "recall: IMG SET + SETTING1/2 contrast/sharpness/depth",
                           values={0x1B030: range(7), **{a: range(1, 6) for a in range(0x1B031, 0x1B037)}}),
                  Settings(0x1B197, 0x1B199, False, "recall: B&W contrast/sharpness", values=range(1, 6)),
                  Settings(0x1B1A7, 0x1B1A9, False, "recall: TE contrast/sharpness", values=range(1, 6))],
        exports=["active_slot"],
        smoke=smoke_checks)


def smoke_setup():
    return {}


def smoke_checks(obs):
    """Slot 1 (BW-R3) expectations; other slots only get the generic checks."""
    out = []
    if obs.feature_options.get("recipe-slots", {}).get("slot") != "1":
        return out
    ups = obs.cc2_uploads
    out.append(("recall: IMG SET B&W, contrast 5", obs.ram8(0x1B030) == 3 and obs.ram8(0x1B197) == 5,
                f"0x1b030={obs.ram8(0x1B030)} 0x1b197={obs.ram8(0x1B197)} active={obs.ram8(0x1B806)}"))
    out.append(("hook uploaded R3 last", bool(ups) and ups[-1][0].startswith("9aad2861"),
                f"uploads={[(h[:8], n) for h, n in ups]}"))
    out.append(("CC2 cache invalidated (0xff)", obs.ram8(0x1F4DB3) == 0xFF, f"0x1f4db3={obs.ram8(0x1F4DB3):#x}"))
    return out
