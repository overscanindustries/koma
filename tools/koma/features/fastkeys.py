"""fastkeys: faster key scan and auto-repeat. Always on, no menu row.

Two patches, `fastrepeat2` + `fastpoll20`:
  fastrepeat2  three .text immediates, the per-app auto-repeat setters (delay_ms, rate_ms) 0x4fcd6 (600,400) and the
               twins 0x66ef4 / 0x6d29c (500,200) -> (300,100): first repeat after 7 scans, then every 3.
  fastpoll20   init code: posts the firmware's own message 0x7112 (builder 0x32aee) so tOpeMain scans keys every 20
               ticks instead of 50, rescaling every scan-counted timer. Guards as in the original: qOPE exists and the
               period is still the stock 50.
v1.1: the scan period word 0x2c9e58 is ALSO the timeout of
every CPU1 (sub-CPU) exchange: 0xca524 `ldr r1, [0x2c9e3c + 0x1c]` at 0xca55e, then sem wait 0x5d978(sem, r1). The
callers retry without bound (0xca76a loops while 0xca524 returns -1; 0x51a18 loops on 0xca76a). fastpoll20 therefore
cut the CPU1 reply budget from 50 to 20 ticks for every frame (up to 55 bytes, REQ_SET_CFG_PORT_*), and a frame CPU1
cannot answer in 20 ticks retries forever: the calling task never returns (tOpeMain: every key dead, no reset).
Option cpu1=stock (default) pins that timeout at the stock 50 ticks (`movs r1, #50`), independent of the scan period.
"""
from __future__ import annotations

from ..feature import Check, Feature, Patch
from .. import firmware as fw

NAME = "fastkeys"
SUMMARY = "key scan 50 -> 20 ticks (fastpoll20) + faster auto-repeat (fastrepeat2); CPU1 timeout kept at 50"
OPTIONS = {"poll": "on (default): scan period 20 | off: stock 50-tick scan (fastrepeat2 only)",
           "cpu1": "stock (default): CPU1 exchange timeout pinned at the stock 50 ticks | scan: tied to the scan "
                   "period as in stock code (0.2 behaviour: 20 ticks with poll=on)"}

PERIOD = 20
STOCK_PERIOD = 50
# (addr, before: stock byte count or short hex, after, why), resolved by fw.pinned
PATCHES = [
    (0x4FCD8, 8, "21642096004046c0", "setter 0x4fcd6: (600,400) -> (300,100)"),
    (0x66EFC, "21c820ff30f5", "216420960040", "setter 0x66ef4: (500,200) -> (300,100)"),
    (0x6D2A4, "21c820ff30f5", "216420960040", "setter 0x6d29c: (500,200) -> (300,100)"),
]
PATCHES_SHA256 = "557834e6f0980949866756590bf042480067957db05e34f7ff5c363b6df310ae"
# 0xca55c: ldr r0, =0x2c9e3c ; 0xca55e: ldr r1, [r0, #0x1c] (scan period = sem timeout) ; 0xca560: ldr r0, =0x1fb3b4
# r0 is reloaded by the next instruction, so `movs r1, #50` is a drop-in (flags are not read before the bl).
CPU1_TIMEOUT = (0xCA55C, "4813" "69c1" "4813", "4813" f"21{STOCK_PERIOD:02x}" "4813",
                "0xca524 CPU1 exchange: sem timeout = scan period -> constant 50 ticks (stock value)")
F32AEE = 0x32AEE
# koma smoke only: the model's tOpeMain is inflated by a bit-banged serial loop that never gets an ACK, so a
# 2.5x scan rate starves MainCtrl and the smoke's menu keys never finish.
# The smoke stubs the post during install and checks that init asked for period 20.
SMOKE_STUBS = {F32AEE: "fastpoll20 post (model artifact: scan rate x2.5 starves MainCtrl in the model)"}

SRC = f"""
koma_init:
    push {{r4, lr}}
    ldr r1, q                   @ qOPE handle must exist (tOpeMain queue created)
    ldr r1, [r1]
    cmp r1, #0
    beq out
    ldr r1, period_now          @ only from the stock period
    ldr r1, [r1]
    cmp r1, #50
    bne out
    movs r0, #{PERIOD}
    ldr r3, f32aee              @ 0x32aee(period): posts 0x7112 {{+0xc = period}} to qOPE; tOpeMain applies it
    bl call_r3
out:
    pop {{r4}}
    pop {{r3}}
    bx r3
call_r3:
    bx r3
.ltorg
.p2align 2
q: .word 0x002c9e28
period_now: .word 0x002c9e58
f32aee: .word 0x{F32AEE | 1:08x}
"""


def _opt(opts, key, allowed):
    v = (opts or {}).get(key, allowed[0])
    if v not in allowed:
        raise ValueError(f"fastkeys.{key}={v!r}: use one of {', '.join(allowed)}")
    return v


def make(opts):
    poll = _opt(opts, "poll", ("on", "off"))
    cpu1 = _opt(opts, "cpu1", ("stock", "scan"))
    rows = list(PATCHES)
    if cpu1 == "stock" and poll == "on":       # poll=off leaves the period (and so the timeout) at the stock 50
        rows.append(CPU1_TIMEOUT)
    checks = [Check(0xCA524, fw.ram(0xCA524, 8), "0xca524 CPU1 exchange prologue (timeout read at 0xca55e)")]
    src, init = (SRC, "koma_init") if poll == "on" else ("", None)
    if poll == "on":
        checks.append(Check(F32AEE, fw.ram(F32AEE, 4), "0x32aee: key-scan period message builder (called by init)"))
    return Feature(
        name=NAME, version="1.1", summary=SUMMARY, options=opts, source=src, init=init,
        patches=[Patch(a, b, bytes.fromhex(n), w) for a, b, n, w in fw.pinned(rows, PATCHES_SHA256)],
        checks=checks, smoke=lambda obs: smoke_checks(obs, PERIOD if poll == "on" else None))


def smoke_checks(obs, period=PERIOD):
    asked = [c[2] for c in obs.calls if c[0] == NAME and c[1] == F32AEE]
    want = [period] if period else []
    return [(f"init asked for scan period {period} (post stubbed in the model)" if period else
             "poll=off: init never posts a scan period", asked == want, f"0x32aee r0={asked}")]
