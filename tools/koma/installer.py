"""Koma installer: the transient Thumb code at the module entry. Position independent, no relocations (it uses only
`label - koma_body` constants plus the body base the loader passes in r1). Layout and record format: linker.py.

Order (all-or-nothing):
  1. ABI == 1 and image version word == 0x240, else return.
  2. Verify pass: every record's before-bytes against RAM. Any mismatch: return, nothing written.
  3. Decide ESSENTIALS from the settings block (magic 'KOMA', version 1, KOMA FEATURES byte 0x1b8bc == 1; the same
     test the core settings init applies, so they agree) in a build with gated features. Allocate RES_LEN, or under
     ESSENTIALS only RES_ESS = the resident prefix before koma_res_gated (the linker places every gated feature's
     resident code after the essential ones), from KernPool (svc +0x08/+0x10); must succeed and be 4-aligned.
     Static build (KOMA_STATIC, linker `resident=static`): no allocation; the resident base is KOMA_STATIC_ADDR.
  4. Range pass: every BL / BLHI record's displacement to (resident base + handler) within +-4 MB, else free and return.
     A BLHI record (early site, linker EARLY_SITE_OK) must also leave the BL suffix halfword in RAM unchanged: the
     new suffix is computed and compared with the halfword at addr + 2; any difference refuses the install here,
     before the first write.
  5. Copy the allocated length of the resident segment, apply the relocations of the words inside it (word += resident
     base; words past the prefix are not there under ESSENTIALS), store koma_ess (1 = ESSENTIALS). Still nothing
     outside the pool block (static build: the copy goes to KOMA_STATIC_ADDR, whose words the linker already
     relocated, so the list is empty; then the 16-byte canary {'KSTA', build id, RES_LEN, ~(sum of the three)} is
     written at KOMA_STATIC_ADDR + RES_LEN).
     5b. Transient relocations (transient=True features: init-only code that stays in the body and is freed).
  6. Run feature inits (r0 = svc, r1 = resident base). Inits may write only their own declared state.
     An init entry with bit 31 set is a body offset (transient feature), else a resident offset.
     ESSENTIALS gate (linker.essential_split): an init whose koma_igate byte is 1 is skipped when the resident byte
     koma_ess (stored in step 5) is 1. Those features' resident code was never copied.
  7. irq_set(0xc0) (svc +0x28), apply every DATA / BL / BLHI / PTR record, restore the mask, status word := 1.
     BL: two aligned STRH, prefix (high) halfword first. BLHI: the prefix STRH only (one atomic store; the suffix in
     RAM already is the new one, checked in pass 4), so a task suspended between the two halves of the stock BL
     resumes into the stock target.
     With ESSENTIALS latched, applying stops at koma_recs_gated (the gated features' records are sorted last).
  8. Return 0 always: the loader frees the transient body; the resident block stays.
A failure frees the resident pool block (pool build); a static build has none to free.
"""

INSTALLER_SRC = r"""
.equ K_RES_LEN, koma_res_end - koma_res
.equ K_RES_ESS, koma_res_gated - koma_res
@ ---------------- installer: r0 = service table, r1 = body base, r2 = body length ----------------
.p2align 2
koma_entry:
    push {r4, r5, r6, r7, lr}
    sub sp, #24                 @ sp+0 svc, +4 body, +8 resident base (0 = none), +12 old irq mask, +16 res bytes,
                                @ +20 ESSENTIALS (1)
    str r0, [sp, #0]
    str r1, [sp, #4]
    movs r2, #0
    str r2, [sp, #8]
    ldr r2, [r0, #0]
    cmp r2, #KOMA_ABI
    bne k_f1
    ldr r2, [r0, #4]
    ldr r3, =KOMA_IMAGE_VERSION
    cmp r2, r3
    bne k_f1
@ ---- 2. verify every before-byte
    ldr r4, =koma_recs - koma_body
    ldr r1, [sp, #4]
    adds r4, r4, r1
k_v:
    ldr r5, [r4, #0]
    cmp r5, #0
    beq k_v_done
    ldrh r6, [r4, #6]
    movs r7, r4
    adds r7, #12                @ before bytes
    movs r2, #0
k_vc:
    cmp r2, r6
    bhs k_vn
    ldrb r0, [r5, r2]
    ldrb r3, [r7, r2]
    cmp r0, r3
    bne k_f1
    adds r2, #1
    b k_vc
k_vn:
    ldr r0, [r4, #8]
    adds r4, r4, r0
    b k_v
k_v_done:
@ ---- 3. resident block (ESSENTIALS: only the prefix before the gated features)
.if KOMA_GATED
    bl k_reslen
    str r1, [sp, #20]
.else
    ldr r2, =K_RES_LEN
.endif
    str r2, [sp, #16]
    cmp r2, #0
    beq k_ranged
.if KOMA_STATIC
    ldr r0, =KOMA_STATIC_ADDR   @ fixed address in the low-RAM gap: no pool block
    str r0, [sp, #8]
    b k_ranges
.endif
    ldr r3, [sp, #0]
    ldr r0, [r3, #0x10]         @ pool handle
    ldr r7, [r3, #0x08]         @ pool_alloc(pool, &p, size, 0)
    mov ip, r7
    add r1, sp, #8
    movs r3, #0
    bl k_call_ip
    cmp r0, #0
    beq k_alloc_ok
    movs r0, #0
    str r0, [sp, #8]
    b k_f1
k_alloc_ok:
    ldr r0, [sp, #8]
    cmp r0, #0
    beq k_f1
    lsls r1, r0, #30
    beq k_ranges                @ 4-aligned
k_f1:
    b k_fail                    @ trampoline: conditional branches reach only +-256 B
k_ranges:
@ ---- 4. BL ranges against the real resident base
    ldr r4, =koma_recs - koma_body
    ldr r1, [sp, #4]
    adds r4, r4, r1
k_r:
    ldr r5, [r4, #0]
    cmp r5, #0
    beq k_ranged
    ldrh r0, [r4, #4]
    cmp r0, #2
    beq k_rbl
    cmp r0, #4
    bne k_rn
k_rbl:
    bl k_payload
    ldr r0, [r1, #0]
    ldr r2, [sp, #8]
    adds r0, r0, r2             @ handler address
    adds r1, r5, #4
    subs r7, r0, r1             @ displacement
    ldr r1, =0x400000
    adds r0, r7, r1
    lsrs r0, r0, #23
    bne k_f1
    ldrh r0, [r4, #4]
    cmp r0, #4
    bne k_rn
    asrs r0, r7, #1             @ BLHI: the new suffix F800 | disp[11:1] must equal the halfword in RAM
    ldr r1, =0x7ff
    ands r0, r1
    ldr r1, =0xf800
    orrs r0, r1
    ldrh r1, [r5, #2]
    cmp r0, r1
    bne k_f1
k_rn:
    ldr r0, [r4, #8]
    adds r4, r4, r0
    b k_r
k_ranged:
@ ---- 5. copy + relocate
    ldr r0, [sp, #8]
    cmp r0, #0
    beq k_trel
    ldr r1, =koma_res - koma_body
    ldr r2, [sp, #4]
    adds r1, r1, r2
    ldr r2, [sp, #16]
    movs r3, #0
k_cp:
    ldr r5, [r1, r3]
    str r5, [r0, r3]
    adds r3, #4
    cmp r3, r2
    blo k_cp
    ldr r1, =koma_relocs - koma_body
    ldr r2, [sp, #4]
    adds r1, r1, r2
    ldr r2, [r1, #0]
    adds r1, #4
k_rl:
    cmp r2, #0
    beq k_essw
    ldr r3, [r1, #0]
.if KOMA_GATED
    ldr r5, [sp, #16]
    cmp r3, r5
    bhs k_rl_n                  @ word in the gated tail: not allocated (ESSENTIALS)
.endif
    ldr r5, [r0, r3]
    adds r5, r5, r0
    str r5, [r0, r3]
k_rl_n:
    adds r1, #4
    subs r2, #1
    b k_rl
k_essw:
.if KOMA_STATIC
    ldr r1, =K_RES_LEN          @ canary at base + RES_LEN: 'KSTA', build id, length, ~(sum)
    adds r1, r1, r0
    ldr r2, =KOMA_CANARY_MAGIC
    str r2, [r1, #0]
    ldr r3, [r0, #8]            @ koma_build_id (manifest +8)
    str r3, [r1, #4]
    adds r2, r2, r3
    ldr r3, =K_RES_LEN
    str r3, [r1, #8]
    adds r2, r2, r3
    mvns r2, r2
    str r2, [r1, #12]
.endif
.if KOMA_GATED
    ldr r2, [sp, #20]
    cmp r2, #0
    beq k_trel                  @ FULL: koma_ess stays 0 (as linked)
    ldr r2, =KOMA_ESS_OFF
    movs r3, #1
    strb r3, [r0, r2]
.endif
@ ---- 5b. transient relocations (transient=True features): body word holds a body offset; resident segment
@      targets -> resident base + (v - O_RES), others -> body base + v. Writes only the transient body.
k_trel:
    ldr r1, [sp, #4]
    ldr r0, [r1, #28]           @ header O_TRELOCS (0 = none)
    cmp r0, #0
    beq k_inits0
    adds r1, r1, r0
    ldr r2, [r1, #0]
    adds r1, #4
k_tl:
    cmp r2, #0
    beq k_inits0
    ldr r3, [r1, #0]
    ldr r0, [sp, #4]
    adds r3, r3, r0             @ word address in the body
    ldr r5, [r3, #0]
    ldr r7, =koma_res - koma_body
    subs r5, r5, r7
    blo k_tb
    ldr r7, =K_RES_LEN
    cmp r5, r7
    bhi k_tb0
    ldr r0, [sp, #8]
    adds r5, r5, r0
    b k_tw
k_tb0:
    ldr r7, =koma_res - koma_body
k_tb:
    adds r5, r5, r7             @ back to the body offset
    adds r5, r5, r0             @ + body base
k_tw:
    str r5, [r3, #0]
    adds r1, #4
    subs r2, #1
    b k_tl
k_inits0:
    b k_inits
@ ---- 6. feature inits
k_inits:
    ldr r4, =koma_inits - koma_body
    ldr r1, [sp, #4]
    adds r4, r4, r1
    ldr r5, =koma_igate - koma_body
    adds r5, r5, r1
    ldr r6, [r4, #0]
    adds r4, #4
k_in:
    cmp r6, #0
    beq k_apply
    ldrb r0, [r5, #0]
    adds r5, #1
    cmp r0, #0
    beq k_in_go
    bl k_ess
    beq k_in_next               @ ESSENTIALS: gated init skipped
k_in_go:
    ldr r3, [r4, #0]
    ldr r1, [sp, #8]
    cmp r3, #0
    bge k_in_res
    lsls r3, r3, #1             @ bit 31: transient init, offset from the body base
    lsrs r3, r3, #1
    ldr r1, [sp, #4]
k_in_res:
    adds r3, r3, r1
    adds r3, #1
    ldr r0, [sp, #0]
    bl k_call_r3
k_in_next:
    adds r4, #4
    subs r6, #1
    b k_in
@ ---- 7. apply, IRQ/FIQ masked
k_apply:
    ldr r0, [sp, #0]
    ldr r3, [r0, #0x28]         @ irq_set (ARM)
    movs r0, #0xc0
    bl k_call_r3
    str r0, [sp, #12]
    ldr r4, =koma_recs - koma_body
    ldr r1, [sp, #4]
    adds r4, r4, r1
k_a:
    ldr r5, [r4, #0]
    cmp r5, #0
    beq k_a_done
    ldr r0, [sp, #4]
    subs r0, r4, r0
    ldr r2, =KOMA_GATE_OFF
    cmp r0, r2
    blo k_a_go
    bl k_ess
    beq k_a_done                @ ESSENTIALS: the gated features' records are not applied
k_a_go:
    ldrh r6, [r4, #6]
    bl k_payload
    ldrh r0, [r4, #4]
    cmp r0, #1
    beq k_data
    cmp r0, #2
    beq k_bl
    cmp r0, #3
    beq k_ptr
    cmp r0, #4
    beq k_bl
    b k_an
k_data:
    movs r2, #0
k_dc:
    cmp r2, r6
    bhs k_an
    ldrb r0, [r1, r2]
    strb r0, [r5, r2]
    adds r2, #1
    b k_dc
k_bl:
    ldr r0, [r1, #0]
    ldr r2, [sp, #8]
    adds r0, r0, r2
    adds r2, r5, #4
    subs r7, r0, r2             @ displacement (range-checked in pass 4)
    asrs r0, r7, #12
    ldr r1, =0x7ff
    ands r0, r1
    ldr r2, =0xf000
    orrs r0, r2                 @ BL high half
    asrs r3, r7, #1
    ands r3, r1
    ldr r2, =0xf800
    orrs r3, r2                 @ BL low half
    strh r0, [r5, #0]
    ldrh r2, [r4, #4]
    cmp r2, #4
    beq k_an                    @ BLHI: prefix only (suffix unchanged, pass 4)
    strh r3, [r5, #2]
    b k_an
k_ptr:
    ldr r0, [r1, #0]
    ldr r2, [sp, #8]
    adds r0, r0, r2
    str r0, [r5, #0]
k_an:
    ldr r0, [r4, #8]
    adds r4, r4, r0
    b k_a
k_a_done:
    ldr r1, [sp, #0]
    ldr r3, [r1, #0x28]
    ldr r0, [sp, #12]
    bl k_call_r3                @ restore the mask
    ldr r0, [sp, #8]
    cmp r0, #0
    beq k_ret0
    movs r1, #1
    str r1, [r0, #12]           @ manifest status = installed
    b k_ret0
k_fail:
.if KOMA_STATIC == 0
    ldr r0, [sp, #8]
    cmp r0, #0
    beq k_ret0
    ldr r1, [sp, #0]
    ldr r3, [r1, #0x0c]         @ pool_free(p)
    bl k_call_r3
.endif
k_ret0:
    movs r0, #0                 @ the loader frees the transient body
    add sp, #24
    pop {r4, r5, r6, r7}
    pop {r3}
    bx r3
@ r1 = payload address of the record at r4 (leaf; touches r1 only)
k_payload:
    ldrh r1, [r4, #6]
    adds r1, #3
    lsrs r1, r1, #2
    lsls r1, r1, #2
    adds r1, #12
    adds r1, r4, r1
    bx lr
@ Z set when KOMA FEATURES = ESSENTIALS is latched in the resident block (leaf; touches r0, r2)
k_ess:
    ldr r0, [sp, #8]
    ldr r2, =KOMA_ESS_OFF
    ldrb r0, [r0, r2]
    cmp r0, #1
    bx lr
@ r2 = resident bytes to allocate, r1 = 1 (ESSENTIALS): K_RES_ESS when the build has gated features and the settings
@ block holds a valid Koma tail with KOMA FEATURES = 1, else K_RES_LEN, r1 = 0 (leaf; touches r0-r3)
.if KOMA_GATED
k_reslen:
    ldr r2, =K_RES_LEN
    ldr r1, =KOMA_SET_MAGIC
    movs r0, #KOMA_GATED
    cmp r0, #0
    beq 1f
    ldr r0, [r1, #0]
    ldr r3, =0x4b4f4d41         @ 'KOMA' (big-endian word)
    cmp r0, r3
    bne 1f
    ldrb r0, [r1, #4]
    cmp r0, #KOMA_SET_VERSION
    bne 1f
    ldrb r0, [r1, #KOMA_ESS_REL]
    cmp r0, #1
    bne 1f
    ldr r2, =K_RES_ESS
    movs r1, #1
    bx lr
1:  movs r1, #0
    bx lr
.endif
k_call_r3:
    bx r3
k_call_ip:
    bx ip
"""
