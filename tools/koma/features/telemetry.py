"""telemetry: once per boot, at install, write /ATA1/KOMALOG.TXT (overwrite) with what the ADJ probe measured.

Port of the ADJ telemetry probe (tools/adj_probe.py) to an invisible Koma feature.
It has no hooks, patches or menu rows: only an init, and it is transient (code + log text stay in the body, which the
loader frees after install: 0 resident bytes). The init runs after the installer has allocated and relocated the
resident block (installer step 6), so the pool search sees Koma resident. The whole transient body (installer, records,
this feature's code) is still allocated at that moment, so POOL UNDER-reports post-install headroom by up to the body
size (layout.transient_bytes in the build manifest).

Log: fixed-length ASCII, `KOMA TLM V1` header, one `NAME =XXXXXXXX` line per field, `END`. A field never reached reads
`--------`. The whole record is rewritten (create truncates) after each stage, riskiest last:
  stage 1  plain reads: build id, resident base, resident canary, CPSR, SP, tick counter + timer candidates over a
           busy loop, pool search
  stage 2  generated code: self-modifying-code coherence and fresh-address execution in a fresh 1 KiB pool buffer
  stage 3  CP15 main ID, cache type and control (privileged mode only; skipped with telemetry.cp15=off)
If the log cannot be created or the write is short, stages 2 and 3 are skipped (nowhere to record them).

CANRY (static-resident builds): the installer writes a 16-byte canary after the
resident block at its fixed low-RAM address; telemetry checks it in stage 1 and again at the end of its init (the last
init, so after every other feature's init). Value: 0 = pool build (no canary); bit 31 = static build, bit 30 = canary
intact ('KSTA', build id = manifest's, length, checksum), bits 0-23 = resident length. Decoded as the line
`RES =<addr>/<len> CANARY=OK|BAD` (describe()["RES_LINE"]). RAM does not survive a power cycle, so the check covers one
boot: install to the end of the inits.

Decode: tools/koma_telemetry_decode.py KOMALOG.TXT
"""
from __future__ import annotations

import struct

from .. import firmware as fw
from ..feature import Check, Feature

NAME = "telemetry"
SUMMARY = "boot telemetry log /ATA1/KOMALOG.TXT (CPSR, CP15, ticks, pool headroom, SMC check)"
OPTIONS = {"cp15": "on (default) | off: skip the CP15 reads. A core without CP15 would hang at stage 3",
           "clock": "off (default) | on: add a CLOCK line = word 0x1b8e0, clockkeep's boot result (clockkeep.describe)"}

LOG_PATH = "/ATA1/KOMALOG.TXT"
HEADER = b"KOMA TLM V1\n"
TRAILER = b"END\n"
FIELDS = ["STAGE", "BUILD", "RES", "CANRY", "CPSR", "SP", "TICK0", "TICK1", "TMA0", "TMA1", "TMB0", "TMB1", "LOOPS",
          "POOL", "PTRY", "CBUF", "SMC1", "SMC2", "FRESH", "CP15", "MIDR", "CTYPE", "CTRL"]
LINE = 15                       # 'NAME =XXXXXXXX\n'
LOOPS = 0x40000                 # busy loop: ~1 M cycles, a few ms on the camera (probe used 0x100000)
POOL_CAP = 0x80000              # 512 KiB
POOL_MIN = 0x400
REFINE = 6
CODE_BUF = 1024
CP15_SKIPPED_USER, CP15_READ, CP15_SKIPPED_OPTION = 0, 1, 2
CANARY_STATIC, CANARY_OK, CANARY_LEN_MASK = 0x80000000, 0x40000000, 0xFFFFFF

# Firmware routines called by absolute address (the probe's and capability MVP's file API).
CREATE, WRITE, CLOSE = 0x55CF4, 0x560EC, 0x55F0C
TICK, TMA, TMB = 0x2358DC, 0x12010004, 0x1201000C


CLOCK_FIELD, CLOCK_WORD = "CLOCK", 0x1B8E0     # telemetry.clock=on: one extra line before END (clockkeep's result)


def fields_for(clock: bool = False) -> list:
    return FIELDS + [CLOCK_FIELD] if clock else FIELDS


def template(clock: bool = False) -> bytes:
    t = bytearray(HEADER)
    for n in fields_for(clock):
        t += n.ljust(5).encode() + b"=--------\n"
    t += TRAILER
    return bytes(t)


def field_index(name: str) -> int:
    return FIELDS.index(name)


def source(cp15: bool, clock: bool = False) -> str:
    f = {n: i for i, n in enumerate(fields_for(clock))}
    text = template(clock)

    def rec(name):
        return f"movs r1, #{f[name]}\n    bl rec"

    clock_src = f"""
    ldr r0, =0x{CLOCK_WORD:x}         @ clockkeep result word (its init ran before this one)
    ldr r0, [r0]
    {rec(CLOCK_FIELD)}""" if clock else ""
    if cp15:
        stage3 = f"""
    ldr r0, [sp, #24]           @ CPSR
    lsls r0, r0, #27
    lsrs r0, r0, #27
    cmp r0, #0x10
    beq cp_user                 @ USR mode: MRC would trap
    add r0, sp, #12
    adr r3, arm_cp15
    bl call_r3
    ldr r0, [sp, #12]
    {rec('MIDR')}
    ldr r0, [sp, #16]
    {rec('CTYPE')}
    ldr r0, [sp, #20]
    {rec('CTRL')}
    movs r0, #{CP15_READ}
    b cp_done
cp_user:
    movs r0, #{CP15_SKIPPED_USER}
cp_done:"""
        arm_cp15 = """
arm_cp15:                       @ r0 = 12-byte buffer. Reads only, no maintenance
    mrc p15, 0, r1, c0, c0, 0   @ main ID
    str r1, [r0]
    mrc p15, 0, r1, c0, c0, 1   @ cache type
    str r1, [r0, #4]
    mrc p15, 0, r1, c1, c0, 0   @ control: M bit 0, C bit 2, W bit 3, I bit 12
    str r1, [r0, #8]
    bx lr"""
    else:
        stage3 = f"""
    movs r0, #{CP15_SKIPPED_OPTION}    @ telemetry.cp15=off: no MRC in this build"""
        arm_cp15 = ""

    return f"""
@ telemetry init (transient): r0 = service table, r1 = body base. Runs once per install from the transient body,
@ IRQs on, before the records apply. Stores only to its own log text (in the body, freed after install), its stack frame and pool blocks it allocates and frees.
@ frame: sp+0 alloc out-slot, +4 refine counter, +8 tick0, +12..+20 timer temps / CP15 buffer, +24 CPSR
tlm_init:
    push {{r4, r5, r6, r7, lr}}
    sub sp, #28
    movs r7, r0                 @ r7 = service table for the whole init
    ldr r0, =$koma.manifest     @ resident base (manifest is resident offset 0; r1 is the body base: transient init)
    {rec('RES')}
    bl tlm_canary
    {rec('CANRY')}
    ldr r0, =$koma.manifest
    ldr r0, [r0, #8]            @ build id
    {rec('BUILD')}
    adr r3, arm_cpsr
    bl call_r3
    str r0, [sp, #24]
    {rec('CPSR')}
    add r0, sp, #48             @ SP at entry (28 locals + 20 pushed)
    {rec('SP')}
@ ---- ticks over a fixed busy loop
    ldr r5, =0x{TICK:x}         @ software tick (incremented by 0x1613ec from the timer IRQ)
    ldr r6, =0x{TMA:x}          @ timer-block candidates 0x12010004 / 0x1201000c; semantics unknown
    ldr r0, [r5]
    str r0, [sp, #8]
    ldr r0, [r6]
    str r0, [sp, #12]
    ldr r0, [r6, #8]
    str r0, [sp, #16]
    ldr r0, =0x{LOOPS:x}
busy:
    subs r0, #1
    bne busy
    ldr r4, [r5]
    ldr r5, [r6]
    ldr r6, [r6, #8]
    ldr r0, [sp, #8]
    {rec('TICK0')}
    movs r0, r4
    {rec('TICK1')}
    ldr r0, [sp, #12]
    {rec('TMA0')}
    movs r0, r5
    {rec('TMA1')}
    ldr r0, [sp, #16]
    {rec('TMB0')}
    movs r0, r6
    {rec('TMB1')}
    ldr r0, =0x{LOOPS:x}
    {rec('LOOPS')}
@ ---- largest pool allocation: halve from the cap, then {REFINE} refinement steps; each success is freed at once
    ldr r5, =0x{POOL_CAP:x}     @ r5 = candidate, then best
    movs r6, #0                 @ r6 = attempts
search:
    adds r6, #1
    movs r0, r5
    bl try_alloc
    cmp r0, #0
    bne found
    lsrs r5, r5, #1
    ldr r0, =0x{POOL_MIN:x}
    cmp r5, r0
    bhs search
    movs r5, #0
    b pool_done
found:
    lsrs r4, r5, #1             @ r4 = step
    movs r0, #{REFINE}
    str r0, [sp, #4]
refine:
    ldr r0, [sp, #4]
    cmp r0, #0
    beq pool_done
    subs r0, #1
    str r0, [sp, #4]
    adds r0, r5, r4
    lsrs r4, r4, #1
    ldr r1, =0x{POOL_CAP:x}
    cmp r0, r1
    bhi refine
    str r0, [sp, #8]
    adds r6, #1
    bl try_alloc
    cmp r0, #0
    beq refine
    ldr r5, [sp, #8]
    b refine
pool_done:
    movs r0, r5
    {rec('POOL')}
    movs r0, r6
    {rec('PTRY')}{clock_src}
    movs r0, #1
    {rec('STAGE')}
    bl emit
    cmp r0, #0
    beq stage2
    b finish                    @ log not writable: nowhere to record the riskier stages
@ ---- stage 2: generated code in a fresh pool buffer
stage2:
    ldr r0, [r7, #0x10]
    mov r1, sp
    ldr r2, ={CODE_BUF}
    ldr r3, [r7, #0x08]
    mov ip, r3
    movs r3, #0
    bl call_ip
    ldr r5, [sp, #0]
    cmp r0, #0
    bne no_buf
    cmp r5, #0
    bne have_buf
no_buf:
    movs r0, #0
    mvns r0, r0
    {rec('CBUF')}
    b stage2_emit
have_buf:
    movs r0, r5
    {rec('CBUF')}
    ldr r0, =0x205a             @ movs r0, #0x5a
    strh r0, [r5]
    ldr r0, =0x4770             @ bx lr
    strh r0, [r5, #2]
    adds r3, r5, #1
    bl call_r3
    {rec('SMC1')}
    movs r0, #0xa5              @ CPU store over the immediate byte only
    strb r0, [r5, #1]
    adds r3, r5, #1
    bl call_r3
    {rec('SMC2')}
    ldr r1, =512
    ldr r0, =0x203c             @ movs r0, #0x3c at an address that has never run code
    strh r0, [r5, r1]
    adds r1, #2
    ldr r0, =0x4770
    strh r0, [r5, r1]
    ldr r3, =513
    adds r3, r5, r3
    bl call_r3
    {rec('FRESH')}
    movs r0, r5
    ldr r3, [r7, #0x0c]
    bl call_r3                  @ pool_free(buffer)
stage2_emit:
    movs r0, #2
    {rec('STAGE')}
    bl emit
    cmp r0, #0
    beq stage3
    b finish
@ ---- stage 3: CP15 (riskiest: a core without CP15 traps here)
stage3:{stage3}
    {rec('CP15')}
    bl tlm_canary               @ again at the end of the last init
    {rec('CANRY')}
    movs r0, #3
    {rec('STAGE')}
    bl emit
finish:
    add sp, #28
    pop {{r4, r5, r6, r7}}
    pop {{r3}}
    bx r3

try_alloc:                      @ r0 = size, r7 = svc -> r0 = 1 if allocated (and freed at once), else 0
    push {{r4, lr}}
    sub sp, #8
    movs r2, r0
    movs r0, #0
    str r0, [sp, #0]
    ldr r0, [r7, #0x10]         @ pool handle
    mov r1, sp
    ldr r3, [r7, #0x08]         @ pool_alloc(pool, &p, size, 0 = no suspend)
    mov ip, r3
    movs r3, #0
    bl call_ip
    cmp r0, #0
    bne ta_fail
    ldr r0, [sp, #0]
    cmp r0, #0
    beq ta_fail
    ldr r3, [r7, #0x0c]         @ pool_free(p)
    bl call_r3
    movs r0, #1
    b ta_out
ta_fail:
    movs r0, #0
ta_out:
    add sp, #8
    pop {{r4}}
    pop {{r3}}
    bx r3

emit:                           @ create (truncates) / write / close the fixed-length record; r0 = 0 ok, 1 failed
    push {{r4, r5, lr}}
    adr r0, path
    movs r1, #2                 @ as stock callers; 0x55cf4 ignores it
    ldr r3, =0x{CREATE | 1:x}
    bl call_r3
    movs r4, r0
    adds r0, #1
    beq emit_fail
    movs r0, r4
    adr r1, text
    ldr r2, ={len(text)}
    ldr r3, =0x{WRITE | 1:x}
    bl call_r3
    movs r5, r0
    movs r0, r4
    ldr r3, =0x{CLOSE | 1:x}
    bl call_r3
    ldr r0, ={len(text)}
    cmp r5, r0
    bne emit_fail
    movs r0, #0
    b emit_out
emit_fail:
    movs r0, #1
emit_out:
    pop {{r4, r5}}
    pop {{r3}}
    bx r3

tlm_canary:                     @ r0 = CANRY value (see the module docstring). Clobbers r0-r3
.if KOMA_STATIC
    ldr r1, =$koma.canary       @ resident base + RES_LEN
    ldr r2, =$koma.manifest
    ldr r3, [r1, #0]
    ldr r0, =KOMA_CANARY_MAGIC
    cmp r3, r0
    bne cn_bad
    ldr r0, [r1, #4]
    ldr r3, [r2, #8]            @ the manifest's build id
    cmp r0, r3
    bne cn_bad
    ldr r0, [r1, #8]
    ldr r3, =K_RES_LEN
    cmp r0, r3
    bne cn_bad
    ldr r0, [r1, #0]
    ldr r3, [r1, #4]
    adds r0, r0, r3
    ldr r3, [r1, #8]
    adds r0, r0, r3
    mvns r0, r0
    ldr r3, [r1, #12]
    cmp r0, r3
    bne cn_bad
    ldr r1, =0x{CANARY_STATIC | CANARY_OK:x}
    b cn_out
cn_bad:
    ldr r1, =0x{CANARY_STATIC:x}
cn_out:
    ldr r0, =K_RES_LEN
    orrs r0, r1
.else
    movs r0, #0                 @ pool build: no canary
.endif
    bx lr

rec:                            @ r0 = value, r1 = field index: 8 hex digits into the log text. Clobbers r0-r3
    lsls r2, r1, #4
    subs r2, r2, r1             @ index * 15
    adr r1, text
    adds r1, r1, r2
    adds r1, #{len(HEADER) + 6}
    movs r2, #28
hx: movs r3, r0
    lsrs r3, r3, r2
    lsls r3, r3, #28
    lsrs r3, r3, #28
    cmp r3, #10
    blo hd
    adds r3, #7
hd: adds r3, #0x30
    strb r3, [r1]
    adds r1, #1
    subs r2, #4
    bpl hx
    bx lr

call_r3:
    bx r3
call_ip:
    bx ip
.p2align 2
.arm
arm_cpsr:
    mrs r0, cpsr
    bx lr
{arm_cp15}
.thumb
.ltorg
.p2align 2
path: .asciz "{LOG_PATH}"
.p2align 2
text:
""" + "\n".join("    .byte " + ", ".join(f"0x{x:02x}" for x in text[i:i + 16]) for i in range(0, len(text), 16)) + "\n"


def _cp15(opts) -> bool:
    # NOTE: MRC p15 hangs the real GR DIGITAL core (verified on a camera). Camera builds MUST pass telemetry.cp15=off.
    v = str(opts.get("cp15", "on")).lower()
    if v in ("on", "1", "yes", "true"):
        return True
    if v in ("off", "0", "no", "false"):
        return False
    raise ValueError(f"telemetry: cp15 must be on or off, not {v!r}")


def _clock(opts) -> bool:
    v = str(opts.get("clock", "off")).lower()
    if v in ("on", "1", "yes", "true"):
        return True
    if v in ("off", "0", "no", "false"):
        return False
    raise ValueError(f"telemetry: clock must be on or off, not {v!r}")


def make(opts):
    cp15, clock = _cp15(opts), _clock(opts)
    o = dict(cp15="on" if cp15 else "off")
    if clock:                       # only recorded when on: default builds keep their bytes and build id
        o["clock"] = "on"
    return Feature(
        name=NAME, version="1.0", summary=SUMMARY, options=o,
        source=source(cp15, clock), init="tlm_init", transient=True,
        checks=[Check(CREATE, fw.ram(CREATE, 4), "file create 0x55cf4 prologue (log)"),
                Check(WRITE, fw.ram(WRITE, 4), "file write 0x560ec prologue (log)"),
                Check(CLOSE, fw.ram(CLOSE, 4), "file close 0x55f0c prologue (log)")],
        smoke=smoke_checks)


# ---------------------------------------------------------------- decoding (shared with tools/koma_telemetry_decode.py)

def parse(data: bytes) -> dict:
    """Parse a log (complete or truncated). Unknown or damaged lines are skipped, never fatal."""
    data = bytes(data)
    out = dict(bytes=len(data), header_ok=data.startswith(HEADER), trailer_ok=data.endswith(TRAILER),
               fixed_length=len(data) in (len(template()), len(template(True))), fields={})
    for line in data.split(b"\n"):
        if len(line) == LINE - 1 and line[5:6] == b"=":
            name, val = line[:5].decode("ascii", "replace").strip(), line[6:14].decode("ascii", "replace")
            if name in FIELDS or name == CLOCK_FIELD:
                try:
                    out["fields"][name] = None if val == "--------" else int(val, 16)
                except ValueError:
                    out["fields"][name] = None
    return out


def describe(fields: dict) -> dict:
    g = fields.get
    hx = lambda v: None if v is None else f"{v:#010x}"   # noqa: E731
    d = {}
    d["STAGE"] = {None: "no stage completed (log only partly written?)",
                  1: "stage 1 only: the generated-code stage did not finish (crash/hang there, or log rewrite failed)",
                  2: "stage 2: the CP15 stage did not finish (a CP15 trap would stop here)",
                  3: "all stages complete"}.get(g("STAGE"), f"unexpected {g('STAGE')}")
    from ..linker import KOMA_VERSION       # the log holds only the id; the version is this tree's (module header)
    d["BUILD"] = None if g("BUILD") is None else f"{g('BUILD'):08X} (KOMA {KOMA_VERSION} {g('BUILD'):08X})"
    d["RES"] = hx(g("RES"))
    cn = g("CANRY")
    if cn is not None and g("RES") is not None:
        d["RES_LINE"] = (f"RES ={g('RES'):#010x}/{cn & CANARY_LEN_MASK} CANARY={'OK' if cn & CANARY_OK else 'BAD'}"
                         if cn & CANARY_STATIC else f"RES ={g('RES'):#010x} (pool build, no canary)")
    c = g("CPSR")
    if c is not None:
        modes = {0x10: "USR", 0x11: "FIQ", 0x12: "IRQ", 0x13: "SVC", 0x17: "ABT", 0x1B: "UND", 0x1F: "SYS"}
        d["CPSR"] = dict(raw=hx(c), mode=modes.get(c & 0x1F, hex(c & 0x1F)), irq_masked=bool(c & 0x80),
                         fiq_masked=bool(c & 0x40), privileged=(c & 0x1F) != 0x10)
    d["SP"] = hx(g("SP"))
    if g("TICK0") is not None and g("TICK1") is not None:
        d["TIMER"] = dict(software_tick_delta=(g("TICK1") - g("TICK0")) & 0xFFFFFFFF, busy_loop_iterations=g("LOOPS"),
                          tma_0x12010004=[hx(g("TMA0")), hx(g("TMA1"))], tmb_0x1201000c=[hx(g("TMB0")), hx(g("TMB1"))],
                          note="a TMA/TMB value that changes across the loop is a free-running counter")
    if g("POOL") is not None:
        d["POOL"] = dict(largest_ok_bytes=g("POOL"), attempts=g("PTRY"), at_cap=g("POOL") == POOL_CAP,
                         note="measured with Koma resident and the transient body still allocated: "
                                     "real post-install headroom is higher by up to the body size")
    if g("CBUF") is not None:
        d["SMC"] = dict(code_buffer=hx(g("CBUF")), first=hx(g("SMC1")), after_overwrite=hx(g("SMC2")),
                        verdict=("no buffer" if g("CBUF") == 0xFFFFFFFF else
                                 "stale (old instruction ran)" if g("SMC2") == 0x5A else
                                 "coherent (new immediate ran)" if g("SMC2") == 0xA5 else "unexpected"),
                        fresh_address=hx(g("FRESH")), fresh_verdict="ok" if g("FRESH") == 0x3C else "unexpected")
    cp = g("CP15")
    if cp is not None:
        if cp == CP15_SKIPPED_USER:
            d["CP15"] = "skipped: unprivileged mode"
        elif cp == CP15_SKIPPED_OPTION:
            d["CP15"] = "skipped: telemetry.cp15=off"
        else:
            ctrl = g("CTRL") or 0
            midr = g("MIDR") or 0
            d["CP15"] = dict(main_id=hx(g("MIDR")), implementer=f"{midr >> 24:#04x}", part=f"{(midr >> 4) & 0xFFF:#05x}",
                             cache_type=hx(g("CTYPE")), control=hx(g("CTRL")), mmu_or_mpu_M=bool(ctrl & 1),
                             dcache_C=bool(ctrl & 4), write_buffer_W=bool(ctrl & 8), icache_I=bool(ctrl & 0x1000))
    return d


# ---------------------------------------------------------------- koma smoke

def smoke_setup():
    return {}


def smoke_checks(obs):
    """The body holding the log text is freed after install, so read the file the init wrote to the emulated card."""
    data = obs.card_file(LOG_PATH) if hasattr(obs, "card_file") else None
    if data is None:
        return [("telemetry log written to the card", False, f"{LOG_PATH} not on the emulated card")]
    p = parse(data)
    fl = p["fields"]
    cp15 = obs.feature_options.get(NAME, {}).get("cp15", "on")
    want_cp = CP15_READ if cp15 == "on" else CP15_SKIPPED_OPTION
    out = [("log complete + fixed length", p["header_ok"] and p["trailer_ok"] and p["fixed_length"], f"{p['bytes']} B")]
    out.append(("log reached stage 3", fl.get("STAGE") == 3, f"STAGE={fl.get('STAGE')}"))
    out.append(("SMC coherent + fresh ok (model)", fl.get("SMC2") == 0xA5 and fl.get("FRESH") == 0x3C,
                f"SMC1={fl.get('SMC1')} SMC2={fl.get('SMC2')} FRESH={fl.get('FRESH')}"))
    out.append(("pool search found headroom", bool(fl.get("POOL")), f"POOL={fl.get('POOL')} PTRY={fl.get('PTRY')}"))
    cn = fl.get("CANRY")
    line = describe(fl).get("RES_LINE", "RES line missing")
    out.append((f"resident canary intact or pool build: {line}; POOL={fl.get('POOL')} PTRY={fl.get('PTRY')}",
                cn is not None and (cn == 0 or bool(cn & CANARY_OK)), f"CANRY={cn}"))
    out.append((f"CP15 field = {want_cp}", fl.get("CP15") == want_cp, f"CP15={fl.get('CP15')}"))
    return out
