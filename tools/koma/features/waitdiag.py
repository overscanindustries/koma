"""waitdiag: TEMPORARY diagnostic for the second-session record-entry stall (tether). Not a release feature.

Observed: after an earlier `-CCTL remtin` / `remtout` session, `remtin` + `diaset,4` spends ~5.6 s in state 9
and then ~10.0 s in record init (rec_dispatch [0x1fb9bc] = 0) before live view; a fresh boot takes 0.5 s. Static
analysis found no proven culprit, only 10 s waits reachable from record init (LCD
semaphores [0x1a8d14] / [0x1a8d18], [0x1a923c], [0x1a9210], [0x1f4c5c], the 0x67020 / 0x6706c handshakes, event-flag
waits) and designed one hook set that times the shared wait implementations. This is that design.

Wait ring (16 records x 12 words, resident): every wait whose timeout is >= 1000 ticks or infinite (unsigned compare, so
TMO_FEVR -1 counts) reserves a record at entry under a short IRQ/FIQ mask (own ARM helper, no stock call while masked),
runs the original call with IRQs as they were, then completes it under the mask. Completed records lasting >= 100 ticks
(1 tick = 1 ms, counter 0x1a8a00) are pinned; faster ones are freed at once. Record words:

    0 status   seq << 8 | state (0 free, 1 pending, 2 done)     6 timeout
    1 kind     (KINDS below)                                   7 entry tick
    2 caller   caller LR (see KINDS)                           8 exit tick (0 while pending)
    3 parent   kind-specific (flag wait: the wait mask)        9 return r0 (raw RTOS code for kinds 1, 3, 4, 5)
    4 task     [0x1a89a8] at entry                            10 detail (KINDS)
    5 object   semaphore / queue / flag handle                11 rec_dispatch [0x1fb9bc] at entry

With no free record, the victim is (in order) a done record older than STALE ticks, a pending one older than STALE,
then the shortest recent done record (counted as overflow). If every record is pending and recent the wait is not
recorded (overflow). A completion whose record was evicted counts as lost. So overflow = 0 is what makes "no wait
was recorded" mean something.

Semaphore provenance: for the candidate handle variables (HANDLES) the last successful acquire (wait return 0, kinds 1
and 6) and the last release (caller LR, tick). Both are stored with IRQ/FIQ masked (acquire inside wd_end, release
through wd_provm), so per handle the newest store wins. The release side is recorded on entry to 0x15f494, before the
stock release runs: it is the last ATTEMPTED release, not proof that the release succeeded. Phase words: when the main loop handled the last message and the
last slow one, the resolver at 0x28d68, the record-init handler 0x7347c (via 0x7ef96) and transition 0x73771 (via
0x7efa6), the last slow record handler, and the decoder 0xd23bc (last message / event and the last 0x4b).

Export: the whole block (BLOCK_WORDS words, big-endian) is appended to -KSTA through ksettings' extension pointer
`ksta_ext` (set by this feature's init); every 12-word chunk is copied with IRQs masked, so a record is never torn.

Hooks preserve r0-r3 as the wrapped call returned them, r4-r11 and sp, and pass the 5th (stack) argument of the
queue-receive and event-flag calls through unchanged. Waits under the 1000-tick filter are a tail call (no frame).
Six sites run before the loader hook (phase A); linker.EARLY_SITE_OK allows them for this feature only (hardware
SMC test: code stores are fetched fresh). Those wrappers are already running when the module installs, so a task may
sit preempted between the two halfwords of the stock BL. The linker therefore places each early hook's entry veneer
at a resident address = the stock target (mod 4096) (0x5d992 / 0x5d9ba / 0x5d7c8 / 0x5d8aa -> residues 0x458 / 0x494 /
0x244 / 0x2b0; 0x67178 and 0x672f4 both -> 0x020, two entries a multiple of 4096 apart): the new BL then has the
stock suffix halfword, the installer writes only the prefix halfword (one aligned STRH, BLHI record), and a suspended
task resumes into the stock target (that one call is not logged) instead of old-prefix + new-suffix garbage. The link
and the installer both refuse a build or install where the suffix would change. Leaving the feature out of the build
is the off switch.
"""
from __future__ import annotations

from .. import firmware as fw
from ..feature import Check, Feature, Hook

NAME = "waitdiag"
VERSION = "1.1"
SUMMARY = "diagnostic: ring of long RTOS waits + record-entry phase ticks, read through -KSTA (temporary)"
OPTIONS = {}

TICK = 0x1A8A00
TASK = 0x1A89A8
DISPATCH = 0x1FB9BC
HS = 0x1BEC44                    # 0x67020 / 0x6706c handshake words 0x1bec44 / 48 / 4c
MIN_TMO = 1000
PIN_TICKS = 100
STALE = 0x5000                   # 20480 ticks
NREC, RECW = 16, 12
HDR_WORDS = 60                   # header + phases + provenance + spare: the ring starts on a 12-word boundary
BLOCK_WORDS = HDR_WORDS + NREC * RECW          # 252
MAGIC = 0x5744 << 16 | 1 << 8 | BLOCK_WORDS    # 'WD', block version 1, words
HEADER = ["magic", "seq", "overflow", "stale_evict", "lost"]
PHASES = ["msg_idcode", "msg_t0", "msg_t1", "msgslow_idcode", "msgslow_t0", "msgslow_t1",
          "res_idcode", "res_msg_t0", "res_t0", "res_t1", "res_ret",
          "h7347c_t0", "h7347c_t1", "h7347c_ret", "slow_target", "slow_t0", "slow_t1",
          "t73770_t0", "t73770_t1", "t73770_ret", "dec_idret", "dec_tick", "dec4b_tick"]
PH_OFF = 4 * len(HEADER)                       # byte offset of the phase words
HANDLES = [0x1A8D14, 0x1A8D18, 0x1A9210, 0x1A923C, 0x1F4C5C, 0x1C324C]
PROV_OFF = PH_OFF + 4 * len(PHASES)            # byte offset: per handle acq_lr, acq_tick, rel_lr, rel_tick
RING_OFF = 4 * HDR_WORDS
assert PROV_OFF + 16 * len(HANDLES) <= RING_OFF
RECORD = ["status", "kind", "caller", "parent", "task", "object", "timeout", "entry", "exit", "ret", "detail",
          "dispatch"]
# kind: (name, hook site, original target, expected before-bytes, what caller / parent / detail mean)
KINDS = {
    1: ("sem_wait", 0x5D992, 0x15F458, "f101fd61", "caller of 0x5d978 ([r5+0x10])"),
    3: ("queue_send", 0x5D7C8, 0x15F244, "f101fd3c", "caller of 0x5d7a8 ([r6+0x10]); detail = message id"),
    4: ("queue_recv", 0x5D8AA, 0x15F2B0, "f101fd01", "caller of 0x5d878 ([r4+0x10]); detail = received id (ok)"),
    5: ("flag_wait", 0x15ED1C, 0x15FCB0, "f000ffc8", "caller of 0x15ecbc; parent = mask; detail = pattern (ok)"),
    6: ("sem_84bd0", 0x84BE8, 0x5D978, "f7d8fec6", "caller of 0x84bd0 (nested sem_wait record follows)"),
    7: ("hs_670d8", 0x670D8, 0x67020, "f7ffffa2", "caller of 0x670d0; detail = 44/48/4c nibbles entry<<12|exit"),
    8: ("hs_67178", 0x67178, 0x67020, "f7ffff52", "caller of 0x6716e; detail as hs_670d8"),
    9: ("hs_672f4", 0x672F4, 0x67020, "f7fffe94", "caller of 0x672e6; detail as hs_670d8"),
    10: ("hs_8e882", 0x8E882, 0x6706C, "f7d8fbf3", "caller of 0x8e838; detail as hs_670d8"),
}
SEM_REL = (0x5D9BA, 0x15F494, "f101fd6b")      # provenance only (no ring record)
PHASE_HOOKS = [(0x28B10, 0x28BDC, "f000f864", "h_msg", "main-loop message handler 0x28bdc"),
               (0x28D68, 0x4FC6E, "f026ff81", "h_res", "dial/key event resolver 0x4fc6e"),
               (0x7EF96, 0x157ACC, "f0d8fd99", "h_ev", "record loop handler call (bx r2), 0x7347d = record init"),
               (0x7EFA6, 0x157AC8, "f0d8fd8f", "h_tr", "record loop transition call (bx r0), 0x73771"),
               (0xD27C2, 0xD23BC, "f7fffdfb", "h_dec", "record dispatch decoder 0xd23bc (0x7205/0x720d -> 0x4b)")]


def _p(name):
    return 4 * PHASES.index(name)


def _filter(reg, scratch, nxt):
    return f"""    movs {scratch}, #125
    lsls {scratch}, {scratch}, #3       @ 1000
    cmp {reg}, {scratch}
    bhs 1f
    ldr r4, ={nxt}
    b wd_tail
1:"""


SRC = f"""
@ ---- ring hooks. Entry: push {{r4-r7, lr}} (20 B). Slow path frame (sub sp, #24): [0] copy of the original stack
@ argument 0, [4..16] r0-r3, [20] post mode; the original sp is then sp + 44.
h_semw:                         @ 0x5d992 -> 0x15f458(sem r0, tmo r1); r5 = TCB + 0xc0
    push {{r4, r5, r6, r7, lr}}
{_filter("r1", "r4", "next_semw")}
    sub sp, #24
    bl wd_save
    ldr r6, =next_semw
    movs r7, #0
    movs r2, r0
    movs r3, r1
    ldr r1, [r5, #0x10]
    movs r0, #1
    movs r4, #0
    movs r5, #0
    b wd_go

h_qsnd:                         @ 0x5d7c8 -> 0x15f244(q r0, msg r1, words r2, tmo r3); r6 = TCB + 0xc0
    push {{r4, r5, r6, r7, lr}}
{_filter("r3", "r4", "next_qsnd")}
    sub sp, #24
    bl wd_save
    ldrh r5, [r1, #6]
    ldr r1, [r6, #0x10]
    ldr r6, =next_qsnd
    movs r7, #0
    movs r2, r0
    movs r0, #3
    movs r4, #0
    b wd_go

h_qrcv:                         @ 0x5d8aa -> 0x15f2b0(q r0, buf r1, size r2, &len r3, tmo [sp]); r4 = TCB + 0xc0
    push {{r4, r5, r6, r7, lr}}
    ldr r5, [sp, #20]
    movs r6, #125
    lsls r6, r6, #3
    cmp r5, r6
    bhs 1f
    ldr r4, =next_qrcv
    b wd_tail
1:  ldr r5, [r4, #0x10]
    sub sp, #24
    bl wd_save
    movs r7, r1
    movs r6, #1
    str r6, [sp, #20]
    ldr r6, =next_qrcv
    movs r2, r0
    ldr r3, [sp, #44]
    movs r1, r5
    movs r0, #4
    movs r4, #0
    movs r5, #0
    b wd_go

h_flgw:                         @ 0x15ed1c -> 0x15fcb0(flg r0, mask r1, mode r2, &ptn r3, tmo [sp]); API lr at [sp+36]
    push {{r4, r5, r6, r7, lr}}
    ldr r5, [sp, #20]
    movs r6, #125
    lsls r6, r6, #3
    cmp r5, r6
    bhs 1f
    ldr r4, =next_flgw
    b wd_tail
1:  sub sp, #24
    bl wd_save
    movs r7, r3
    movs r6, #2
    str r6, [sp, #20]
    ldr r6, =next_flgw
    movs r4, r1
    movs r2, r0
    ldr r3, [sp, #44]
    ldr r1, [sp, #80]
    movs r0, #5
    movs r5, #0
    b wd_go

h_s84:                          @ 0x84be8 -> 0x5d978(sem r0, tmo r1) in 0x84bd0 (push {{r4,r7,lr}}: caller [sp+8])
    push {{r4, r5, r6, r7, lr}}
{_filter("r1", "r4", "next_s84")}
    sub sp, #24
    bl wd_save
    ldr r6, =next_s84
    movs r7, #0
    movs r2, r0
    movs r3, r1
    ldr r1, [sp, #52]
    movs r0, #6
    movs r4, #0
    movs r5, #0
    b wd_go

h_hs1:                          @ 0x670d8 -> 0x67020() in 0x670d0 (caller [sp+8]); waits only when [0x1bec44] == 1
    push {{r4, r5, r6, r7, lr}}
    bl wd_hs44
    beq 1f
    ldr r4, =next_hs1
    b wd_tail
1:  sub sp, #24
    bl wd_save
    ldr r6, =next_hs1
    ldr r1, [sp, #52]
    movs r0, #7
    b wd_hsgo

h_hs2:                          @ 0x67178 -> 0x67020() in 0x6716e (push {{r0-r7,lr}}; sub sp,#4: caller [sp+36])
    push {{r4, r5, r6, r7, lr}}
    bl wd_hs44
    beq 1f
    ldr r4, =next_hs2
    b wd_tail
1:  sub sp, #24
    bl wd_save
    ldr r6, =next_hs2
    ldr r1, [sp, #80]
    movs r0, #8
    b wd_hsgo

h_hs3:                          @ 0x672f4 -> 0x67020() in 0x672e6 (push {{r0-r7,lr}}; sub sp,#0x14: caller [sp+52])
    push {{r4, r5, r6, r7, lr}}
    bl wd_hs44
    beq 1f
    ldr r4, =next_hs3
    b wd_tail
1:  sub sp, #24
    bl wd_save
    ldr r6, =next_hs3
    ldr r1, [sp, #96]
    movs r0, #9
    b wd_hsgo

h_hs4:                          @ 0x8e882 -> 0x6706c() in 0x8e838 (caller [sp+8]); waits only while [0x1bec48] != 0
    push {{r4, r5, r6, r7, lr}}
    ldr r4, ={HS + 4:#x}
    ldr r4, [r4]
    cmp r4, #0
    bne 1f
    ldr r4, =next_hs4
    b wd_tail
1:  sub sp, #24
    bl wd_save
    ldr r6, =next_hs4
    ldr r1, [sp, #52]
    movs r0, #10
    b wd_hsgo

h_srel:                         @ 0x5d9ba -> 0x15f494(sem r0); r4 = TCB + 0xc0. Provenance only, then a tail call.
    push {{r4, r5, r6, r7, lr}}     @ recorded BEFORE the stock release: an attempted-release marker
    push {{r0, r1, r2, r3}}
    ldr r1, [r4, #0x10]
    movs r2, #8
    ldr r3, =wd_provm
    bl wd_call_r3
    pop {{r0, r1, r2, r3}}
    ldr r4, =next_srel
    b wd_tail

@ Z set when [0x1bec44] == 1 (0x67020 will wait). r4.
wd_hs44:
    ldr r4, ={HS:#x}
    ldr r4, [r4]
    cmp r4, #1
    bx lr

@ Tail call r4 with r0-r3 as entered: restores r4-r7 and lr from the entry push. Uses ip.
wd_tail:
    mov ip, r4
    ldr r4, [sp, #16]
    mov lr, r4
    pop {{r4, r5, r6, r7}}
    add sp, #4
    bx ip

@ Leaf: [sp+4..16] = r0-r3, [sp] = original stack argument 0 ([sp+44] in the caller's frame), [sp+20] = 0. r4.
wd_save:
    str r0, [sp, #4]
    str r1, [sp, #8]
    str r2, [sp, #12]
    str r3, [sp, #16]
    ldr r4, [sp, #44]
    str r4, [sp, #0]
    movs r4, #0
    str r4, [sp, #20]
    bx lr

@ Leaf: r0 = low nibbles of [0x1bec44] / [48] / [4c] (12 bits). r0-r2.
wd_hs:
    ldr r2, ={HS:#x}
    ldr r0, [r2]
    lsls r0, r0, #28
    lsrs r0, r0, #20
    ldr r1, [r2, #4]
    lsls r1, r1, #28
    lsrs r1, r1, #24
    orrs r0, r1
    ldr r1, [r2, #8]
    lsls r1, r1, #28
    lsrs r1, r1, #28
    orrs r0, r1
    bx lr

@ handshake: r0 kind, r1 caller, r6 next.
wd_hsgo:
    push {{r0, r1}}
    bl wd_hs
    lsls r5, r0, #12
    pop {{r0, r1}}
    movs r7, #0
    movs r4, #3
    str r4, [sp, #20]
    ldr r2, ={HS:#x}
    ldr r3, =10000
    movs r4, #0
    b wd_go

@ r0 kind, r1 caller, r2 object, r3 timeout, r4 parent, r5 detail, r6 next, r7 post pointer; frame as above.
wd_go:
    push {{r4, r5}}
    ldr r4, =wd_begin
    bl wd_call_r4
    add sp, #8
    movs r4, r0
    movs r5, r1
    ldr r0, [sp, #4]
    ldr r1, [sp, #8]
    ldr r2, [sp, #12]
    ldr r3, [sp, #16]
    bl wd_call_r6               @ the original call; [sp] = its stack argument
    str r0, [sp, #4]
    str r1, [sp, #8]
    str r2, [sp, #12]
    str r3, [sp, #16]
    movs r3, #0
    ldr r2, [sp, #20]
    cmp r2, #3
    bne 1f
    bl wd_hs
    movs r3, r0
    b 4f
1:  cmp r2, #0
    beq 4f
    ldr r0, [sp, #4]
    cmp r0, #0
    bne 4f
    cmp r2, #1
    bne 2f
    ldrh r3, [r7, #6]           @ received message id
    b 4f
2:  ldr r3, [r7]                @ event-flag pattern
4:  movs r0, r4
    beq 5f                      @ not recorded (overflow)
    movs r1, r5
    ldr r2, [sp, #4]
    ldr r6, =wd_end
    bl wd_call_r6
5:  ldr r0, [sp, #4]
    ldr r1, [sp, #8]
    ldr r2, [sp, #12]
    ldr r3, [sp, #16]
    add sp, #24
    pop {{r4, r5, r6, r7, pc}}

wd_call_r3:
    bx r3
wd_call_r4:
    bx r4
wd_call_r6:
    bx r6
wd_call_r7:
    bx r7
.ltorg

@ ---- phase hooks: no mask (one writer per word); r0-r3 as the call returned them.
h_msg:                          @ 0x28b10 -> 0x28bdc(msg r0, r1)
    push {{r4, r5, r6, r7, lr}}
    bl wd_idcode                @ r5 = id << 16 | [msg+0xc] << 8 | [msg+0xd]  (r0 = msg)
    ldr r4, =wd_block + {PH_OFF}
    str r5, [r4, #{_p("msg_idcode")}]
    ldr r6, ={TICK:#x}
    ldr r6, [r6]
    str r6, [r4, #{_p("msg_t0")}]
    movs r7, #0
    str r7, [r4, #{_p("msg_t1")}]
    ldr r7, =next_msg
    bl wd_call_r7
    ldr r7, ={TICK:#x}
    ldr r7, [r7]
    str r7, [r4, #{_p("msg_t1")}]
    subs r6, r7, r6
    cmp r6, #{PIN_TICKS}
    blo 1f
    str r5, [r4, #{_p("msgslow_idcode")}]
    ldr r6, [r4, #{_p("msg_t0")}]
    str r6, [r4, #{_p("msgslow_t0")}]
    str r7, [r4, #{_p("msgslow_t1")}]
1:  pop {{r4, r5, r6, r7, pc}}

@ Leaf: r5 = [r0+6] << 16 | [r0+0xc] << 8 | [r0+0xd]. r5, r6.
wd_idcode:
    ldrh r5, [r0, #6]
    lsls r5, r5, #16
    ldrb r6, [r0, #0xc]
    lsls r6, r6, #8
    orrs r5, r6
    ldrb r6, [r0, #0xd]
    orrs r5, r6
    bx lr

h_res:                          @ 0x28d68 -> 0x4fc6e() in 0x28bdc (r7 = msg)
    push {{r4, r5, r6, r7, lr}}
    push {{r0}}
    movs r0, r7
    bl wd_idcode
    pop {{r0}}
    ldr r4, =wd_block + {PH_OFF}
    str r5, [r4, #{_p("res_idcode")}]
    ldr r5, [r4, #{_p("msg_t0")}]
    str r5, [r4, #{_p("res_msg_t0")}]
    ldr r5, ={TICK:#x}
    ldr r5, [r5]
    str r5, [r4, #{_p("res_t0")}]
    movs r5, #0
    str r5, [r4, #{_p("res_t1")}]
    ldr r7, =next_res
    bl wd_call_r7
    ldr r5, ={TICK:#x}
    ldr r5, [r5]
    str r5, [r4, #{_p("res_t1")}]
    str r0, [r4, #{_p("res_ret")}]
    pop {{r4, r5, r6, r7, pc}}

h_ev:                           @ 0x7ef96 -> 0x157acc (bx r2): r2 = event handler, r0 = event, r1 = sp
    push {{r4, r5, r6, r7, lr}}
    movs r6, r2
    ldr r4, =wd_block + {PH_OFF}
    ldr r5, ={TICK:#x}
    ldr r5, [r5]
    ldr r7, =0x7347d
    cmp r6, r7
    bne 1f
    str r5, [r4, #{_p("h7347c_t0")}]
    movs r7, #0
    str r7, [r4, #{_p("h7347c_t1")}]
1:  ldr r7, =next_ev
    bl wd_call_r7
    push {{r0, r1, r2, r3}}
    ldr r7, ={TICK:#x}
    ldr r7, [r7]
    ldr r1, =0x7347d
    cmp r6, r1
    bne 2f
    str r7, [r4, #{_p("h7347c_t1")}]
    str r0, [r4, #{_p("h7347c_ret")}]
2:  movs r0, r6
    bl wd_slow
    pop {{r0, r1, r2, r3}}
    pop {{r4, r5, r6, r7, pc}}

h_tr:                           @ 0x7efa6 -> 0x157ac8 (bx r0): r0 = transition function
    push {{r4, r5, r6, r7, lr}}
    movs r6, r0
    ldr r4, =wd_block + {PH_OFF}
    ldr r5, ={TICK:#x}
    ldr r5, [r5]
    ldr r7, =0x73771
    cmp r6, r7
    bne 1f
    str r5, [r4, #{_p("t73770_t0")}]
    movs r7, #0
    str r7, [r4, #{_p("t73770_t1")}]
1:  ldr r7, =next_tr
    bl wd_call_r7
    push {{r0, r1, r2, r3}}
    ldr r7, ={TICK:#x}
    ldr r7, [r7]
    ldr r1, =0x73771
    cmp r6, r1
    bne 2f
    str r7, [r4, #{_p("t73770_t1")}]
    str r0, [r4, #{_p("t73770_ret")}]
2:  movs r0, #1
    lsls r0, r0, #31
    orrs r0, r6                 @ bit 31: a transition (0x7efa6), not a handler
    bl wd_slow
    pop {{r0, r1, r2, r3}}
    pop {{r4, r5, r6, r7, pc}}

@ Leaf: r0 = target tag, r4 = phase base, r5 = t0, r7 = t1: a call of >= PIN_TICKS goes to the slow slot. r1.
wd_slow:
    subs r1, r7, r5
    cmp r1, #{PIN_TICKS}
    blo 1f
    str r0, [r4, #{_p("slow_target")}]
    str r5, [r4, #{_p("slow_t0")}]
    str r7, [r4, #{_p("slow_t1")}]
1:  bx lr

h_dec:                          @ 0xd27c2 -> 0xd23bc(msg r0) -> event code (0x4b for 0x7205 / 0x720d)
    push {{r4, r5, r6, r7, lr}}
    ldrh r6, [r0, #6]
    ldr r7, =next_dec
    bl wd_call_r7
    ldr r4, =wd_block + {PH_OFF}
    lsls r6, r6, #16
    lsls r5, r0, #16
    lsrs r5, r5, #16
    orrs r6, r5
    str r6, [r4, #{_p("dec_idret")}]
    ldr r5, ={TICK:#x}
    ldr r5, [r5]
    str r5, [r4, #{_p("dec_tick")}]
    cmp r0, #0x4b
    bne 1f
    str r5, [r4, #{_p("dec4b_tick")}]
1:  pop {{r4, r5, r6, r7, pc}}

@ init (installer, before the records apply): register the -KSTA extension with ksettings.
wd_init:
    push {{lr}}
    ldr r0, =$ksettings.ksta_ext
    ldr r1, =wd_export
    str r1, [r0]
    pop {{r3}}
    bx r3
.ltorg

.p2align 2
.arm
@ wd_begin(r0 kind, r1 caller, r2 object, r3 timeout; [sp] parent, [sp+4] detail) -> r0 record or 0, r1 token.
@ Runs with IRQ/FIQ masked throughout; calls nothing.
wd_begin:
    stmfd sp!, {{r4-r11, lr}}
    mrs r11, cpsr
    orr ip, r11, #0xc0
    msr cpsr_c, ip
    ldr r4, =wd_block
    ldr r5, ={TICK:#x}
    ldr r5, [r5]
    add r6, r4, #{RING_OFF}
    mov r7, #0
    mvn r8, #0
    mov r9, #{NREC}
1:  ldr ip, [r6]
    ands lr, ip, #0xff
    beq 3f
    ldr r10, [r6, #28]
    sub r10, r5, r10
    cmp lr, #1
    bne 2f
    cmp r10, #{STALE:#x}
    movhs r10, #1
    mvnlo r10, #0
    b 5f
2:  cmp r10, #{STALE:#x}
    movhs r10, #0
    bhs 5f
    ldr r10, [r6, #32]
    ldr ip, [r6, #28]
    sub r10, r10, ip
    add r10, r10, #2
5:  cmp r10, r8
    movlo r8, r10
    movlo r7, r6
    add r6, r6, #{4 * RECW}
    subs r9, r9, #1
    bne 1b
    cmn r8, #1
    beq 8f
    mov r6, r7
    cmp r8, #2
    ldrhs ip, [r4, #8]
    addhs ip, ip, #1
    strhs ip, [r4, #8]
    ldrlo ip, [r4, #12]
    addlo ip, ip, #1
    strlo ip, [r4, #12]
3:  ldr ip, [r4, #4]
    add ip, ip, #1
    str ip, [r4, #4]
    mov ip, ip, lsl #8
    orr ip, ip, #1
    str ip, [r6]
    str r0, [r6, #4]
    str r1, [r6, #8]
    ldr r0, [sp, #36]
    str r0, [r6, #12]
    ldr r0, ={TASK:#x}
    ldr r0, [r0]
    str r0, [r6, #16]
    str r2, [r6, #20]
    str r3, [r6, #24]
    str r5, [r6, #28]
    mov r0, #0
    str r0, [r6, #32]
    str r0, [r6, #36]
    ldr r0, [sp, #40]
    str r0, [r6, #40]
    ldr r0, ={DISPATCH:#x}
    ldr r0, [r0]
    str r0, [r6, #44]
    mov r0, r6
    mov r1, ip
    b 9f
8:  ldr ip, [r4, #8]
    add ip, ip, #1
    str ip, [r4, #8]
    mov r0, #0
    mov r1, #0
9:  msr cpsr_c, r11
    ldmfd sp!, {{r4-r11, lr}}
    bx lr

@ wd_end(r0 record, r1 token, r2 return, r3 detail bits). IRQ/FIQ masked throughout; calls only wd_prov (own code).
wd_end:
    stmfd sp!, {{r4-r6, lr}}
    mrs r6, cpsr
    orr ip, r6, #0xc0
    msr cpsr_c, ip
    ldr ip, [r0]
    cmp ip, r1
    bne 7f
    ldr r5, ={TICK:#x}
    ldr r5, [r5]
    str r5, [r0, #32]
    str r2, [r0, #36]
    ldr ip, [r0, #40]
    orr ip, ip, r3
    str ip, [r0, #40]
    cmp r2, #0
    bne 3f
    ldr ip, [r0, #4]
    cmp ip, #1
    cmpne ip, #6
    bne 3f
    stmfd sp!, {{r0, r1}}
    ldr r1, [r0, #8]
    ldr r0, [r0, #20]
    mov r2, #0
    adr ip, wd_prov
    mov lr, pc                  @ lr = the ldmfd below (pc reads 8 ahead)
    bx ip
    ldmfd sp!, {{r0, r1}}
3:  ldr ip, [r0, #28]
    sub ip, r5, ip
    cmp ip, #{PIN_TICKS}
    movlo r1, #0
    bichs r1, r1, #0xff
    orrhs r1, r1, #2
    str r1, [r0]
    b 9f
7:  ldr ip, =wd_block
    ldr r1, [ip, #16]
    add r1, r1, #1
    str r1, [ip, #16]
9:  msr cpsr_c, r6
    ldmfd sp!, {{r4-r6, lr}}
    bx lr

@ wd_prov(r0 handle, r1 caller, r2 = 0 acquire / 8 release): store (caller, tick) for the first matching handle
@ variable. Leaf, no mask of its own (the acquire side runs inside wd_end's mask). r0-r3, ip.
wd_prov:
    stmfd sp!, {{r4, r5, lr}}
    adr r3, wd_hv
    ldr ip, =wd_block + {PROV_OFF}
    add ip, ip, r2
1:  ldr r4, [r3], #4
    cmp r4, #0
    beq 9f
    ldr r4, [r4]
    cmp r4, r0
    addne ip, ip, #16
    bne 1b
    ldr r5, ={TICK:#x}
    ldr r5, [r5]
    stmia ip, {{r1, r5}}
9:  ldmfd sp!, {{r4, r5, lr}}
    bx lr

@ wd_provm: wd_prov under an IRQ/FIQ mask (release side, from h_srel), so the tick read and the (caller, tick) store
@ are one step and a later releaser's record is never overwritten by an older tick. Restores the incoming mask.
wd_provm:
    stmfd sp!, {{r4, lr}}
    mrs r4, cpsr
    orr ip, r4, #0xc0
    msr cpsr_c, ip
    adr ip, wd_prov
    mov lr, pc                  @ lr = the msr below (pc reads 8 ahead)
    bx ip
    msr cpsr_c, r4
    ldmfd sp!, {{r4, lr}}
    bx lr

@ -KSTA extension (ksettings ksta_ext): wd_export(r0 = destination) -> r0 = bytes appended. Big-endian byte stores,
@ IRQ/FIQ masked per 12-word chunk (one ring record). Called in tDcx.
wd_export:
    stmfd sp!, {{r4-r6, lr}}
    ldr r1, =wd_block
    mov r4, #{BLOCK_WORDS // RECW}
1:  mrs r6, cpsr
    orr ip, r6, #0xc0
    msr cpsr_c, ip
    mov r2, #{RECW}
2:  ldr r3, [r1], #4
    mov ip, r3, lsr #24
    strb ip, [r0], #1
    mov ip, r3, lsr #16
    strb ip, [r0], #1
    mov ip, r3, lsr #8
    strb ip, [r0], #1
    strb r3, [r0], #1
    subs r2, r2, #1
    bne 2b
    msr cpsr_c, r6
    subs r4, r4, #1
    bne 1b
    mov r0, #{4 * BLOCK_WORDS}
    ldmfd sp!, {{r4-r6, lr}}
    bx lr
.ltorg
wd_hv:
{"".join(f"    .word {a:#x}{chr(10)}" for a in HANDLES)}    .word 0
.thumb
.p2align 2
wd_block:
    .word {MAGIC:#x}
    .space {4 * (BLOCK_WORDS - 1)}
"""
assert BLOCK_WORDS % RECW == 0 and RING_OFF % (4 * RECW) == 0


def _hooks():
    # `koma smoke` (grdemu model, boot + one shot) reaches only the event-flag wait and the 0x6706c handshake on the shot
    # (from camera-ready: also the three 0x67020 sites and the resolver 0x28d68); from then on the model never enters
    # 0x5d978 / 0x5d9a4 / the queue wrappers / the main loop 0x28af0 / the record loop dispatch. Those are SKIP there.
    hit = {0x15ED1C, 0x8E882}
    names = {1: "semw", 3: "qsnd", 4: "qrcv", 5: "flgw", 6: "s84", 7: "hs1", 8: "hs2", 9: "hs3", 10: "hs4"}
    hs = [Hook(site, target, f"h_{names[k]}", f"next_{names[k]}", f"waitdiag {name}: {what}",
               smoke_reachable=site in hit) for k, (name, site, target, _, what) in KINDS.items()]
    hs.append(Hook(SEM_REL[0], SEM_REL[1], "h_srel", "next_srel", "waitdiag sem release: provenance",
                   smoke_reachable=False))
    hs += [Hook(site, target, h, "next_" + h[2:], f"waitdiag phase: {why}", smoke_reachable=False)
           for site, target, _, h, why in PHASE_HOOKS]
    return hs


def before_bytes():
    """(site, expected before-bytes) for every hook: tests check them against the 2.40 image."""
    return [(s, bytes.fromhex(b)) for _, s, _, b, _ in KINDS.values()] + [(SEM_REL[0], bytes.fromhex(SEM_REL[2]))] + \
        [(s, bytes.fromhex(b)) for s, _, b, _, _ in PHASE_HOOKS]


def smoke_checks(obs):
    """`koma smoke`: find the block by its magic in RAM and report what the boot + shot recorded."""
    import struct
    ram = obs.ram(0x300000, 0x180000)
    at = ram.find(struct.pack(">I", MAGIC))
    while at >= 0 and at % 4:
        at = ram.find(struct.pack(">I", MAGIC), at + 1)
    if at < 0:
        return [("waitdiag block found in RAM", False, "magic not found")]
    w = struct.unpack(f">{BLOCK_WORDS}I", ram[at:at + 4 * BLOCK_WORDS])
    recs = [w[HDR_WORDS + i * RECW:HDR_WORDS + (i + 1) * RECW] for i in range(NREC)]
    live = [r for r in recs if r[0] & 0xFF]
    kinds = sorted({KINDS.get(r[1], ("?",))[0] for r in live})
    ph = {n: w[len(HEADER) + i] for i, n in enumerate(PHASES)}
    return [("waitdiag block found in RAM", True, f"at {0x300000 + at:#x}"),
            # report only: the model runs no wait of >= 1000 ticks after install (unit tests cover the ring)
            (f"waitdiag ring (report): seq={w[1]} overflow={w[2]} stale={w[3]} lost={w[4]} live={len(live)} "
             f"kinds={kinds}", True, ""),
            (f"waitdiag phases (report): res_t0={ph['res_t0']} res_ret={ph['res_ret']} msg_t0={ph['msg_t0']}", True, "")]


def make(opts):
    for site, b in before_bytes():
        if fw.ram(site, 4) != b:
            raise ValueError(f"waitdiag: before-bytes at {site:#x} differ from the expected {b.hex()}")
    return Feature(
        name=NAME, version=VERSION, summary=SUMMARY, options=opts, source=SRC, init="wd_init", smoke=smoke_checks,
        hooks=_hooks(), requires=["ksettings"],
        checks=[Check(0x5D978, fw.ram(0x5D978, 0x2C), "0x5d978 sem wait: r5 = TCB+0xc0, [r5+0x10] = caller lr"),
                Check(0x5D9A4, fw.ram(0x5D9A4, 0x28), "0x5d9a4 sem release: r4 = TCB+0xc0, [r4+0x10] = caller lr"),
                Check(0x5D7A8, fw.ram(0x5D7A8, 0x24), "0x5d7a8 queue send: r6 = TCB+0xc0, msg r1, tmo r3"),
                Check(0x5D878, fw.ram(0x5D878, 0x36), "0x5d878 queue receive: r4 = TCB+0xc0, tmo [sp]"),
                Check(0x15ECBC, fw.ram(0x15ECBC, 0x60), "0x15ecbc flag wait: push {r0-r7,lr}; sub sp,#4; tmo [sp]"),
                Check(0x84BD0, fw.ram(0x84BD0, 0x18), "0x84bd0: push {r4,r7,lr}, sem [0x1c324c]"),
                Check(0x67020, fw.ram(0x67020, 0x4C), "0x67020 / 0x6706c handshakes on 0x1bec44/48/4c"),
                Check(0x670D0, fw.ram(0x670D0, 8), "0x670d0: push {r4,r7,lr}"),
                Check(0x6716E, fw.ram(0x6716E, 0x0A), "0x6716e: push {r0-r7,lr}; sub sp,#4"),
                Check(0x672E6, fw.ram(0x672E6, 0x0E), "0x672e6: push {r0-r7,lr}; sub sp,#0x14"),
                Check(0x8E838, fw.ram(0x8E838, 4), "0x8e838: push {r4,r7,lr}"),
                Check(0x157AC8, fw.ram(0x157AC8, 6), "0x157ac8 / acc: bx r0 / bx r1 / bx r2")])
