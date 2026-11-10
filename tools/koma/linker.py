"""Koma linker: merge features into one GRMOD body.

Body layout (all offsets from the body start; the loader passes the body base in r1):

  0x00  boot header (transient)          'KMOD', u16 format, u16 flags, O_RECS, O_RELOCS, O_INITS, O_RES, RES_LEN, 0
  0x20  installer (transient, Thumb)     entry point
        records (transient)              CHECK / DATA / BL / PTR records, then u32 0
        inits (transient)                u32 n, then n resident offsets
  O_RES resident segment                 manifest, feature code + data + blobs (copied to its own pool block)
  O_RELOCS relocation table (transient)  u32 n, then n resident offsets of words that hold resident offsets
                                         (static build: u32 0, then 'KSTA', u32 address; see below)

The installer (installer.py) verifies every record's before-bytes, allocates RES_LEN from KernPool, copies the
resident segment there, relocates it, runs feature inits, then with IRQ/FIQ masked applies every record and marks the
manifest installed. It always returns 0 so the loader frees the transient body; only the resident segment stays.
Any failure before the first write frees what it took and returns 0 with RAM untouched.

Record: u32 addr, u16 kind, u16 len, u32 next (bytes to the next record), before[pad4], payload
  kind 0 CHECK  no payload
  kind 1 DATA   after[pad4]
  kind 2 BL     u32 resident offset of the handler -> Thumb BL at addr (len 4)
  kind 3 PTR    u32 resident offset -> 32-bit word (resident base + offset) at addr (len 4, 4-aligned)
  kind 4 BLHI   as BL, but the installer writes only the prefix (high) halfword: early sites (EARLY_SITE_OK), whose
                entry veneer the linker places so that the BL suffix (low) halfword stays the stock one

Resident placement (link(resident=...)): "pool" (default) as above. A static build (header flag 2, `koma build
--resident static[:ADDR]`) links the resident segment at a fixed address in the low-RAM gap (STATIC_SPAN): every resident word already holds ADDR + offset, the relocation list is empty
(followed by the 'KSTA', ADDR trailer), and the installer copies the segment to ADDR without a pool allocation, then
writes the 16-byte canary {'KSTA', build id, resident length, ~(sum of the three)} at ADDR + resident length.
Only the module itself (installer, records, resident image, freed after install) then sits in the KernPool.

Resident manifest (resident offset 0; published at 0x58194 when publish is on):
  +0x00 'KOMA'  +0x04 u16 format, u16 nfeatures  +0x08 u32 build id  +0x0c u32 status (1 = installed)
  +0x10 char version[24] (NUL padded, "KOMA V1.0.0 1A2B3C4D G1": package version, build id, minimum Koma firmware
        generation; docs/versioning.md. Neither loader reads it: both check only the GRMD container)
  +0x28 nfeatures x { char name[16], char version[8], u32 hash32, u32 resident bytes }
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import re
import struct
from dataclasses import dataclass, field

from . import firmware as fw
from .asm import AsmError, assemble, bl_target, thumb_bl
from .feature import SETTINGS_BLOCK, Check, Feature, Patch, Settings
from .installer import INSTALLER_SRC

from .vendor import grmod   # pack_module / parse_module: the loader's container

KOMA_VERSION = "1.0.0"            # Koma package X.Y.Z (docs/versioning.md), the public release
RICOH_VERSION = "2.40"            # the Ricoh firmware every Koma build patches
# Internal compatibility field: the lowest Koma firmware generation this package runs on (1 = the flash-1 card-module
# loader, 2 = adds SDHC and the /IROM fallback). Never shown to users; the app says "Needs a newer Koma firmware."
MIN_FIRMWARE_GEN = 1


def menu_version(version: str = KOMA_VERSION) -> str:
    """KOMA menu version row (<= 9 chars): Ricoh 2.40 + Koma major.minor, e.g. "2.40K1.0" (VERSIONING.md)."""
    major, minor = version.split(".")[:2]
    return f"{RICOH_VERSION}K{major}.{minor}"


def photo_tag(version: str = KOMA_VERSION) -> str:
    """Photo MakerNote firmware tag (7 chars): "240K1.0" (Ricoh 2.40 + K + Koma major.minor; VERSIONING.md)."""
    major, minor = version.split(".")[:2]
    tag = f"{RICOH_VERSION.replace('.', '')}K{major}.{minor}"
    assert len(tag) <= 7, tag
    return tag

BODY_MAGIC, MANIFEST_MAGIC = b"KMOD", b"KOMA"
FORMAT = 1
K_CHECK, K_DATA, K_BL, K_PTR, K_BLHI = 0, 1, 2, 3, 4
KIND_NAMES = {K_CHECK: "check", K_DATA: "data", K_BL: "bl", K_PTR: "ptr", K_BLHI: "blhi"}
BL_KINDS = ("bl", "blhi")         # manifest / inspector kind names whose final site bytes are a Thumb BL
HDR_LEN, MANIFEST_HDR, FEAT_ENTRY = 0x20, 0x28, 32
VERSION_LEN = 24
CORE_EXPORTS = {"manifest": "koma_manifest", "version": "koma_version", "status": "koma_status",
                "ess": "koma_ess",    # ess: 1 = ESSENTIALS, stored by the installer (installer.py step 3)
                "canary": "koma_canary"}   # static builds: the canary right after the resident segment
# Features whose hooks must head every hook chain they join (chain order = build order). menu5's GR II load / commit
# wrappers swap the row map around the stock call, so they must run before bwrecipes' IMG SET hooks on the same sites,
# and under ESSENTIALS the chain's BL record (owned by chain[0]) must belong to the essential menu5, not bwrecipes.
CHAIN_FIRST = {"menu5": "GR II load / commit hooks must run first (list menu5 before every feature sharing the site)"}
FLAG_PUBLISH = 1
FLAG_STATIC = 2
# Static resident: the low-RAM gap between the KernPool end and the update
# validator ceiling. No literal pointer and no CPU or engine-register write into it (or its 0x08000000 alias) in the
# 2.40 image scan and the koma03 emulator write-watch (boot, shot, menu, playback, USB ORIGINAL, power-off). Inferred
# unused (I), not proven on hardware: the build's canary check settles it.
STATIC_SPAN = (0x3DADEC, 0x422800)
STATIC_DEFAULT = (STATIC_SPAN[0] + 31) & ~31          # 0x3dae00
CANARY_MAGIC, CANARY_LEN = 0x4B535441, 16             # 'KSTA'


def parse_resident(spec) -> int | None:
    """`--resident` value -> None (pool) or the static address. "pool" | "static" | "static:ADDR"."""
    if spec is None or isinstance(spec, int):
        return spec
    kind, _, addr = str(spec).strip().lower().partition(":")
    if kind == "pool" and not addr:
        return None
    if kind == "static":
        try:
            return int(addr, 0) if addr else STATIC_DEFAULT
        except ValueError:
            pass
    raise LinkError([f"--resident {spec!r}: use pool, static or static:ADDR"])

# ------------------------------------------------------------------ Koma settings
SET_MAGIC, SET_VERSION_ADDR, SET_VERSION = 0x1B8B0, 0x1B8B4, 1
# KOMA FEATURES mode byte (settingsio-menu row). A build with that row gets the
# ESSENTIALS gate: features outside features.ESSENTIAL (after promotion, essential_split) have their inits and records
# skipped by the installer when the byte, latched by the core settings init, is 1.
ESS_BYTE = 0x1B8BC
ROW_BYTES = (0x1B8B5, 0x1B8D0)            # bytes a ROWS entry may use (0x1b8b0..0x1b8cf verified unused by stock)
ROW_LABEL_MAX, ROW_LABEL_SOFT, ROW_OPT_MAX = 16, 14, 9   # 16 = stock label column; 14 = contract/naming target
GLYPHS = set(" !&'()+-./0123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ*\u221e")   # vendor/menu5tab/glyphs.py P1

# ------------------------------------------------------------------ lint
# (a) Flash / store-write routines (2.40 application addresses). No feature may load their address (ldr =, .word, .equ), BL to them from patch bytes,
#     or hook a call to them.
FORBIDDEN_CALLS = {
    0x4949C: "user-settings save to NAND (power-off save)",
    0x27C5C: "UEE00 adjustment-store commit", 0x63D9E: "block 0x3d store erase/program",
    0x63C68: "updater: erase main-info", 0x63B88: "updater: program main", 0x63C80: "updater: program main-info",
    0x63598: "NAND erase (replace-table)", 0x636DC: "NAND program (replace-table)", 0x6339C: "NAND spare take",
    0x631A4: "replace-table write", 0x63620: "NAND sector copy", 0x6325C: "NAND blocks 1..0xc writer",
    0x639E2: "block 0 (Monitor) writer", 0x63AC0: "block 0 (Monitor info) writer", 0x63E9C: "type-6 block writer",
    0x63F94: "type-6 block writer", 0x670D0: "blocks 0x54.. writer", 0x67134: "blocks 0x54.. writer",
    0x674BC: "NAND restore", 0x64228: "NAND self-test (erases)", 0x64838: "OneNAND program setup",
    0x649A8: "OneNAND erase", 0x64B88: "OneNAND erase", 0x647C8: "OneNAND lock/unlock", 0x64764: "OneNAND lock/unlock",
    0x64D24: "OneNAND erase wrapper", 0x64F04: "OneNAND program", 0x28194: "near-whole-device erase",
    0xD17D4: "caller of the whole-device erase", 0xD6C58: "application direct flash programmer",
    0xD6A62: "type-5 script reader (adjustment writes)", 0x5D18C: "firmware updater", 0xD6DEC: "updater driver",
}
# (b) exception to "a stock settings byte may only receive a value stock also writes": features allowed a bulk
#     Settings(bulk=True) range (no per-byte values). settingsio copies a validated PARAM.DAT over the block at boot;
#     its envelope checks plus the host byte-map validation are the guard, and stock 0x3d4a4 coerces at the next load.
BULK_SETTINGS_WRITERS = {"settingsio": "boot import of a validated /ATA1/PARAM.DAT"}
PHASE_A = fw.DATA / "phase-a-lines32.json"   # adj-cache-exposure report, phase A line set
# Early-site exceptions (feature -> {site: why}). The phase-A rule guards against a stale I-cache line; hardware
# telemetry (SMC2 = A5, FRESH = 3C) shows a CPU store to already-executed code is fetched fresh. Only the temporary waitdiag diagnostic may patch these shared RTOS wrappers.
#
# Suspended-BL hazard and the "suffix unchanged" rule (every site here). A Thumb BL is two halfwords: the prefix
# F000|off[22:12] sets LR = site+4 + (off[22:12] << 12), the suffix F800|off[11:1] jumps to LR + (off[11:1] << 1). These
# sites already run when the loader installs, so a task may be preempted between the two halves; it resumes with the
# OLD prefix's LR. Writing both halves would then send it to old-prefix + new-suffix, an unrelated address (0x5d992:
# 0x15ea0c instead of 0x3e3a0c). So each early hook's entry is placed at a resident address E with E = stock target
# (mod 4096): off_new - off_old = E - old is a multiple of 4096, bits [11:1] agree, the suffix halfword is identical,
# and the record is K_BLHI: one aligned STRH of the prefix (atomic, IRQ/FIQ masked as before). A task suspended between
# the halves executes old prefix + same suffix = the stock target (that call is simply not logged); every later fetch
# sees the new pair. The link refuses unless the suffix is unchanged (needs a static resident: the address is fixed),
# and the installer re-checks it against RAM before any write. The entry is a veneer (_early_veneer) placed between
# features; it touches no r0-r3, lr or stack.
EARLY_SITE_OK = {"waitdiag": {0x5D992: "sem wait wrapper", 0x5D9BA: "sem release wrapper",
                              0x5D7C8: "queue send wrapper", 0x5D8AA: "queue receive wrapper",
                              0x67178: "handshake 0x6716e", 0x672F4: "handshake 0x672e6"}}


@functools.lru_cache(maxsize=1)
def phase_a_lines() -> frozenset:
    """32-byte lines executed between reset and module entry (emulator, adj-cache-exposure phase A)."""
    return frozenset(int(x, 16) for x in json.loads(PHASE_A.read_text())["phase_A_lines32"])


_NUM = r"(?:0x[0-9a-fA-F]+|\d+)"
_NUMEXPR = re.compile(rf"(?<![\w$])({_NUM})(?:\s*([+|])\s*({_NUM}))?(?![\w])")


def _source_constants(src: str):
    """Numbers a feature's source loads or stores as data: `ldr rX, =N`, `.word`/`.long`, `.equ/.set name, N`."""
    out = []
    for n, line in enumerate(src.splitlines(), 1):
        code = _strip_comment(line)
        m = re.search(r"=\s*(.*)$", code) if re.search(r"\bldr\b", code) else None
        m = m or re.search(r"\.(?:word|long|4byte)\s+(.*)$", code) or re.search(r"\.(?:equ|set)\s+\w+\s*,\s*(.*)$", code)
        if not m:
            continue
        for x in _NUMEXPR.finditer(m.group(1)):
            a = int(x.group(1), 0)
            out.append((n, a))
            if x.group(3):
                b = int(x.group(3), 0)
                out.append((n, a + b if x.group(2) == "+" else a | b))
    return out


def lint(features: list[Feature]) -> list[str]:
    """Build-time safety rules. Fast: one pass over sources, patch bytes and write sites."""
    errors = []
    lines = phase_a_lines()
    for f in features:
        src = f.source or ""
        for n, v in _source_constants(src):
            if v & ~1 in FORBIDDEN_CALLS:
                errors.append(f"{f.name}: source line {n} references {v:#x} ({FORBIDDEN_CALLS[v & ~1]}); "
                              f"Koma never calls flash/store-write routines")
        for p in f.patches:
            for i in range(0, len(p.after) - 3, 2):
                t = bl_target(p.addr + i, p.after[i:i + 4])
                if t is not None and t & ~1 in FORBIDDEN_CALLS:
                    errors.append(f"{f.name}: patch {p.addr:#x} writes a bl to {t:#x} ({FORBIDDEN_CALLS[t & ~1]})")
        for h in f.hooks:
            if h.target & ~1 in FORBIDDEN_CALLS:
                errors.append(f"{f.name}: hook {h.site:#x} wraps a call to {h.target:#x} "
                              f"({FORBIDDEN_CALLS[h.target & ~1]})")
        sites = [(p.addr, p.end, f"patch {p.addr:#x}") for p in f.patches]
        sites += [(h.site, h.end, f"hook {h.site:#x}") for h in f.hooks if not h.early_ok]
        sites += [(r.site, r.end, f"redirect {r.site:#x}") for r in f.redirects]
        sites += [(q.addr, q.end, f"pointer {q.addr:#x}") for q in f.pointers]
        early_ok = EARLY_SITE_OK.get(f.name, {})
        for a0, a1, what in sites:
            if a0 in early_ok:
                continue
            hit = next((ln for ln in range(a0 & ~31, a1, 32) if ln in lines), None)
            if hit is not None:
                errors.append(f"{f.name}: {what} is in line {hit:#x}, which executes before the loader hook 0x281ee "
                              f"(adj-cache-exposure phase A); patch a later site")
        for st in f.settings:
            if st.owned:
                continue
            if st.bulk:
                if f.name not in BULK_SETTINGS_WRITERS:
                    errors.append(f"{f.name}: bulk settings write {st.start:#x}..{st.end:#x} is reserved for "
                                  f"{sorted(BULK_SETTINGS_WRITERS)}")
                continue
            if st.values is None:
                errors.append(f"{f.name}: writes stock settings {st.start:#x}..{st.end:#x} without declaring the "
                              f"allowed values (Settings(values=...))")
                continue
            for a in range(st.start, st.end):
                vals = st.allowed(a)
                if not vals or any(not 0 <= int(v) <= 0xFF for v in vals):
                    errors.append(f"{f.name}: stock setting {a:#x} has no valid allowed-value set")
    return errors


def collect_rows(features: list[Feature]):
    """Every selected feature's ROWS, validated, sorted by `order` (default: the byte), then byte. A row with
    `needs=<feature>` is left out when that feature is not in the build. Each row keeps its `group` (KOMA tab page: recipe,
    recipe-edit, focus, exposure, display; missing or unknown -> the OTHER page, laid out by menu5).
    Returns (rows, errors, warnings)."""
    rows, errors, warnings, seen_b, seen_id = [], [], [], {}, {}
    names = {f.name for f in features}
    for f in features:
        for r in f.rows:
            rid = r.get("id", "?")
            if r.get("needs") and r["needs"] not in names:    # row only shown when that feature is in the build
                continue
            where = f"{f.name} row {rid!r}"
            try:
                label, opts, byte, default = r["label"], list(r["options"]), int(r["byte"]), int(r["default"])
            except (KeyError, TypeError, ValueError) as e:
                errors.append(f"{where}: needs id, label, options, byte, default ({e})")
                continue
            if not ROW_BYTES[0] <= byte < ROW_BYTES[1]:
                errors.append(f"{where}: byte {byte:#x} outside {ROW_BYTES[0]:#x}..{ROW_BYTES[1] - 1:#x}")
            if byte in seen_b:
                errors.append(f"{where}: byte {byte:#x} also used by {seen_b[byte]}")
            if rid in seen_id:
                errors.append(f"{where}: id also used by {seen_id[rid]}")
            seen_b[byte], seen_id[rid] = where, where
            if not 1 <= len(opts) <= 255 or not 0 <= default < len(opts):
                errors.append(f"{where}: needs 1..255 options and 0 <= default < {len(opts)}")
            if len(label) > ROW_LABEL_MAX:
                errors.append(f"{where}: label {label!r} longer than {ROW_LABEL_MAX}")
            elif len(label) > ROW_LABEL_SOFT:
                warnings.append(f"{where}: label {label!r} is over {ROW_LABEL_SOFT} chars (fits the stock column)")
            for t in [label] + opts:
                bad = sorted(set(t) - GLYPHS)
                if bad:
                    errors.append(f"{where}: {t!r} uses characters the menu font lacks: {bad}")
            for o in opts:
                if len(o) > ROW_OPT_MAX or not o:
                    errors.append(f"{where}: option {o!r} must be 1..{ROW_OPT_MAX} chars")
            on = ns(f.name) + "on_change" if "on_change" in f.exports else None
            group = str(r.get("group") or "other")
            rows.append(dict(id=rid, label=label, options=opts, byte=byte, default=default, feature=f.name,
                             on_change=on, group=group, order=int(r.get("order", byte))))
    rows.sort(key=lambda r: (r["order"], r["byte"]))      # display order: `order` (default: the byte), then byte
    return rows, errors, warnings


def essential_split(features: list[Feature]):
    """ESSENTIALS gate: (gated feature names, warnings). Essential = features.ESSENTIAL in the build, closed over
    `requires` and over hook chains (a site shared by an essential and a gated feature makes the whole chain essential:
    the chain's `next` words are link-time constants, so it cannot be cut at install)."""
    from .features import ESSENTIAL
    by = {f.name: f for f in features}
    ess = {n for n in by if n in ESSENTIAL}
    chains: dict[int, set] = {}
    for f in features:
        for h in f.hooks:
            chains.setdefault(h.site, set()).add(f.name)
    warnings, changed = [], True
    while changed:
        changed = False
        for n in sorted(ess):
            for q in by[n].requires:
                if q in by and q not in ess:
                    ess.add(q)
                    warnings.append(f"ESSENTIALS: {q} kept (required by {n})")
                    changed = True
        for site, members in sorted(chains.items()):
            if members & ess and members - ess:
                warnings.append(f"ESSENTIALS: {sorted(members - ess)} kept (hook chain at {site:#x} with "
                                f"{sorted(members & ess)})")
                ess |= members
                changed = True
    return {n for n in by if n not in ess}, warnings


def settings_init_src(rows) -> str:
    """Core install-time init: magic 'KOMA' at 0x1b8b0 missing (or version byte stale) -> every row's default, then
    the version byte, then the magic. Rows whose byte is out of range are reset to their default either way.
    koma_ess (KOMA FEATURES as the installer read it before allocating; 1 = ESSENTIALS) is stored by the installer,
    not here: the resident block's size depends on it, so the installer is its only writer."""
    s = ["koma_settings_init:", "    push {r4, lr}", f"    ldr r4, =0x{SET_MAGIC:x}", "    adr r1, koma_set_magic",
         "    movs r2, #0", "koma_si_cmp:", "    ldrb r0, [r4, r2]", "    ldrb r3, [r1, r2]", "    cmp r0, r3",
         "    bne koma_si_far", "    adds r2, #1", "    cmp r2, #4", "    blo koma_si_cmp",
         "    ldrb r0, [r4, #4]", f"    cmp r0, #{SET_VERSION}", "    beq koma_si_rep",
         # the repair list below can outgrow a conditional branch (+-256 B; 31 rows with Look IA's ADJ LEVER row)
         "koma_si_far:", "    b koma_si_fresh", "koma_si_rep:"]
    for i, r in enumerate(rows):          # magic present: only repair out-of-range bytes
        off = r["byte"] - SET_MAGIC
        s += [f"    ldrb r0, [r4, #{off}]", f"    cmp r0, #{len(r['options'])}", f"    blo koma_si_ok{i}",
              f"    movs r0, #{r['default']}", f"    strb r0, [r4, #{off}]", f"koma_si_ok{i}:"]
    s += ["    b koma_si_out", "koma_si_fresh:"]
    for r in rows:
        s += [f"    movs r0, #{r['default']}", f"    strb r0, [r4, #{r['byte'] - SET_MAGIC}]"]
    s += [f"    movs r0, #{SET_VERSION}", "    strb r0, [r4, #4]", "    adr r1, koma_set_magic", "    movs r2, #3",
          "koma_si_mw:", "    ldrb r0, [r1, r2]", "    strb r0, [r4, r2]", "    subs r2, #1", "    bpl koma_si_mw",
          "koma_si_out:"]
    s += ["    pop {r4}", "    pop {r3}", "    bx r3", ".p2align 2", "koma_set_magic:",
          '    .ascii "KOMA"', ".ltorg", ".p2align 2", "koma_ess:", "    .word 0"]
    return "\n".join(s)


class LinkError(Exception):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("\n".join("  - " + p for p in self.problems))


@dataclass
class Record:
    kind: int
    addr: int
    before: bytes
    after: bytes = b""
    target: str = ""         # BL / PTR: assembler label (resident) the record points at
    addend: int = 0          # PTR: +1 for a Thumb function pointer
    owner: str = ""
    why: str = ""


@dataclass
class LinkResult:
    module: bytes
    body: bytes
    manifest: dict
    source: str
    symbols: dict
    warnings: list = field(default_factory=list)


# ------------------------------------------------------------------ conflicts

def _overlap(a0, a1, b0, b1):
    return a0 < b1 and b0 < a1


def order_features(features: list[Feature]) -> list[Feature]:
    """Dependencies first, otherwise the given order. Raises on missing deps, declared conflicts and cycles."""
    names = [f.name for f in features]
    problems = []
    if len(set(names)) != len(names):
        problems.append(f"feature listed twice: {sorted(n for n in set(names) if names.count(n) > 1)}")
    by = {f.name: f for f in features}
    for f in features:
        for r in f.requires:
            if r not in by:
                problems.append(f"{f.name} requires {r}, which is not selected (add it to --features)")
        for c in f.conflicts:
            if c in by:
                problems.append(f"{f.name} declares a conflict with {c}")
    if problems:
        raise LinkError(problems)
    out, state = [], {}

    def visit(f, path):
        if state.get(f.name) == 2:
            return
        if state.get(f.name) == 1:
            raise LinkError([f"dependency cycle: {' -> '.join(path + [f.name])}"])
        state[f.name] = 1
        for r in f.requires:
            visit(by[r], path + [f.name])
        state[f.name] = 2
        out.append(f)
    for f in features:
        visit(f, [])
    return out


def check_conflicts(features: list[Feature], image_check: bool = True):
    """Build-time conflict detection. Returns (errors, warnings); never raises."""
    errors, warnings = [], []
    writes, reads = [], []     # (start, end, feature, what, bytes-or-None)
    for f in features:
        for p in f.patches:
            writes.append((p.addr, p.end, f.name, f"patch {p.addr:#x}+{len(p.before)}", None))
            reads.append((p.addr, p.end, f.name, f"patch {p.addr:#x}", p.before))
        for h in f.hooks:
            writes.append((h.site, h.end, f.name, f"hook {h.site:#x}", ("hook", h.target)))
        for r in f.redirects:
            writes.append((r.site, r.end, f.name, f"redirect {r.site:#x}", None))
            reads.append((r.site, r.end, f.name, f"redirect {r.site:#x}", r.before))
        for q in f.pointers:
            writes.append((q.addr, q.end, f.name, f"pointer {q.addr:#x}", None))
            reads.append((q.addr, q.end, f.name, f"pointer {q.addr:#x}", q.before))
        for c in f.checks:
            reads.append((c.addr, c.end, f.name, f"check {c.addr:#x}", c.before))

    # write vs write
    for i, (a0, a1, fa, wa, ka) in enumerate(writes):
        for (b0, b1, fb, wb, kb) in writes[i + 1:]:
            if not _overlap(a0, a1, b0, b1):
                continue
            if ka and kb and ka[0] == kb[0] == "hook" and a0 == b0 and fa != fb:
                if ka[1] != kb[1]:
                    errors.append(f"{fa} {wa} and {fb} {wb} disagree on the original call target "
                                  f"({ka[1]:#x} vs {kb[1]:#x})")
                continue           # same site, same target: chained
            if fa == fb:
                errors.append(f"{fa}: {wa} overlaps its own {wb}")
            else:
                errors.append(f"{fa} {wa} overlaps {fb} {wb}")
    # write vs another feature's read (dependency on bytes someone else changes)
    hooked = {(f.name, h.site) for f in features for h in f.hooks}
    for (a0, a1, fa, wa, ka) in writes:
        for (b0, b1, fb, wb, _) in reads:
            if fa == fb or not _overlap(a0, a1, b0, b1) or wb.startswith(("patch", "redirect", "pointer")):
                continue
            if ka and ka[0] == "hook" and (fb, a0) in hooked:
                continue           # fb chains on the same site: its check of the site bytes is satisfied pre-install
            errors.append(f"{fb} {wb} depends on bytes that {fa} {wa} changes")
    # read vs read: overlapping before-bytes must agree
    for i, (a0, a1, fa, wa, ba) in enumerate(reads):
        for (b0, b1, fb, wb, bb) in reads[i + 1:]:
            if not _overlap(a0, a1, b0, b1):
                continue
            lo, hi = max(a0, b0), min(a1, b1)
            if ba[lo - a0:hi - a0] != bb[lo - b0:hi - b0]:
                errors.append(f"{fa} {wa} and {fb} {wb} expect different bytes at {lo:#x}..{hi:#x}")
    # reserved ranges
    for (a0, a1, fa, wa, _) in writes:
        for (r0, r1), what in fw.RESERVED:
            if _overlap(a0, a1, r0, r1):
                errors.append(f"{fa} {wa} overlaps the {what} {r0:#x}..{r1:#x}")
    # before-bytes against the 2.40 image
    if image_check:
        for (a0, a1, fa, wa, b) in reads:
            if fw.in_payload(a0, a1 - a0):
                if fw.ram(a0, a1 - a0) != b:
                    errors.append(f"{fa} {wa}: before-bytes differ from the 2.40 image")
            else:
                warnings.append(f"{fa} {wa}: outside the payload, before-bytes only checked on the camera")
        for f in features:
            for h in f.hooks:
                if not fw.in_payload(h.site, 4):
                    errors.append(f"{f.name} hook {h.site:#x}: outside the payload")
                elif bl_target(h.site, fw.ram(h.site, 4)) != h.target:
                    errors.append(f"{f.name} hook {h.site:#x}: image has no `bl {h.target:#x}` there "
                                  f"({fw.ram(h.site, 4).hex()})")
    for f in features:
        for h in f.hooks:
            if h.site & 1:
                errors.append(f"{f.name} hook {h.site:#x}: site must be halfword aligned")
        for r in f.redirects:
            if r.site & 1:
                errors.append(f"{f.name} redirect {r.site:#x}: site must be halfword aligned")
        for q in f.pointers:
            if q.addr & 3:
                errors.append(f"{f.name} pointer {q.addr:#x}: must be 4-aligned")
        if not f.source.strip() and not f.late_source and (f.redirects or f.pointers):
            errors.append(f"{f.name}: declares redirects/pointers but has no source")
    # init-written image RAM (Feature.ram_writes): inside the payload, clear of every record of every feature
    rec_spans = [(getattr(x, "addr", getattr(x, "site", None)), x.end, f.name) for f in features
                 for x in f.patches + f.checks + f.hooks + f.redirects + f.pointers]
    for f in features:
        for a0, a1, why in f.ram_writes:
            if not (a0 < a1 and fw.in_payload(a0, a1 - a0)) or not why:
                errors.append(f"{f.name} ram_writes {a0:#x}..{a1:#x}: must be a non-empty payload range with a reason")
            for b0, b1, fb in rec_spans:
                if _overlap(a0, a1, b0, b1):
                    errors.append(f"{f.name} ram_writes {a0:#x}..{a1:#x} overlaps a {fb} record {b0:#x}..{b1:#x}")
    # settings
    owned, touched = [(SET_MAGIC, SET_VERSION_ADDR + 1, "koma (settings magic/version)")], []
    for f in features:
        for s in f.settings + [Settings(r["byte"], r["byte"] + 1, True) for r in f.rows if "byte" in r]:
            if not (SETTINGS_BLOCK[0] <= s.start < s.end <= SETTINGS_BLOCK[1]):
                errors.append(f"{f.name} settings {s.start:#x}..{s.end:#x} not inside {SETTINGS_BLOCK[0]:#x}.."
                              f"{SETTINGS_BLOCK[1]:#x}")
            if s.bulk and not s.owned:
                continue           # bulk import (lint-checked): overlaps every store by design
            (owned if s.owned else touched).append((s.start, s.end, f.name))
    for i, (a0, a1, fa) in enumerate(owned):
        for (b0, b1, fb) in owned[i + 1:]:
            if _overlap(a0, a1, b0, b1):
                errors.append(f"settings {fa} {a0:#x}..{a1:#x} and {fb} {b0:#x}..{b1:#x} both claim the same bytes")
        for (b0, b1, fb) in touched:
            if fb != fa and _overlap(a0, a1, b0, b1):
                errors.append(f"settings: {fb} writes {b0:#x}..{b1:#x} inside {fa}'s store {a0:#x}..{a1:#x}")
    for i, (a0, a1, fa) in enumerate(touched):
        for (b0, b1, fb) in touched[i + 1:]:
            if fa != fb and _overlap(a0, a1, b0, b1):
                warnings.append(f"settings: {fa} and {fb} both write stock field(s) {max(a0, b0):#x}..{min(a1, b1):#x}"
                                f" (last writer wins)")
    return errors, warnings


# ------------------------------------------------------------------ namespacing

_IDENT = re.compile(r"(?<![\w.$])([A-Za-z_][A-Za-z0-9_]*)(?![\w$])")
_EXTERN = re.compile(r"\$([a-z0-9][a-z0-9-]*)\.([A-Za-z_][A-Za-z0-9_]*)")
_LABEL = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:")
_EQU = re.compile(r"^\s*\.(?:equ|set)\s+([A-Za-z_][A-Za-z0-9_]*)\s*,", re.I)


def ns(feature: str) -> str:
    return "kf_" + re.sub(r"[^A-Za-z0-9]", "_", feature) + "__"


def local_names(src: str) -> set[str]:
    names = set()
    for line in src.splitlines():
        code = _strip_comment(line)
        rest = code
        while True:
            m = _LABEL.match(rest)
            if not m:
                break
            names.add(m.group(1))
            rest = rest[m.end():]
        m = _EQU.match(rest)
        if m:
            names.add(m.group(1))
    return names


def _strip_comment(line: str) -> str:
    out, q = [], False
    for ch in line:
        if ch == '"':
            q = not q
        if not q and ch == "@":
            break
        out.append(ch)
    return "".join(out)


def mangle_source(feature: Feature, extern, names: set | None = None) -> str:
    """Prefix the feature's private names; resolve `$feat.sym` through extern(feat, sym) -> label."""
    names = names or (local_names(feature.source) | {h.next for h in feature.hooks} | set(feature.blobs))
    pre = ns(feature.name)
    out = []
    for line in feature.source.splitlines():
        code = _strip_comment(line)
        comment = line[len(code):]
        code = _EXTERN.sub(lambda m: extern(m.group(1), m.group(2)), code)
        # labels, then one op token, then operands; the op token (mnemonic/directive) is never renamed
        labels = ""
        while True:
            m = _LABEL.match(code)
            if not m:
                break
            labels += code[:m.start(1)] + pre + m.group(1) + ":"
            code = code[m.end():]
        m = re.match(r"(\s*)(\S+)(.*)$", code)
        if m:
            op, operands = m.group(1) + m.group(2), m.group(3)
            operands = _rename(operands, names, pre)
            code = op + operands
        out.append(labels + code + comment)
    return "\n".join(out)


def _rename(text, names, pre):
    parts = text.split('"')
    for i in range(0, len(parts), 2):          # outside string literals only
        parts[i] = _IDENT.sub(lambda m: pre + m.group(1) if m.group(1) in names else m.group(1), parts[i])
    return '"'.join(parts)


# ------------------------------------------------------------------ helpers

def _pad4(b: bytes) -> bytes:
    return bytes(b) + bytes(-len(b) % 4)


def _bytes_asm(b: bytes, files: dict, stem: str) -> str:
    if len(b) > 256:
        name = f"{stem}.bin"
        files[name] = bytes(b)
        return f'    .incbin "{name}"'
    return "\n".join("    .byte " + ", ".join(f"0x{x:02x}" for x in b[i:i + 16]) for i in range(0, len(b), 16))


def _cstr(s: str, n: int) -> bytes:
    b = s.encode("ascii")
    if len(b) >= n:
        raise LinkError([f"string {s!r} longer than {n - 1} bytes"])
    return b + bytes(n - len(b))


def feature_hash(f: Feature) -> str:
    # Hashes the assembly text verbatim, `@` comments included, and the hash lands in the module body: editing a
    # comment inside a feature's source changes the build id and the package SHA-256.
    h = hashlib.sha256()
    h.update(f"{f.name}\0{f.version}\0{sorted(f.options.items())}\0{f.source}\0{f.init}".encode())
    for p in f.patches:
        h.update(struct.pack(">I", p.addr) + p.before + p.after)
    for c in f.checks:
        h.update(struct.pack(">I", c.addr) + c.before)
    for k in f.hooks:
        h.update(struct.pack(">II", k.site, k.target) + k.handler.encode() + k.next.encode())
    for k, v in sorted(f.blobs.items()):
        h.update(k.encode() + v)
    for r in f.redirects:
        h.update(struct.pack(">I", r.site) + r.before + r.handler.encode())
    for q in f.pointers:
        h.update(struct.pack(">IB", q.addr, q.thumb) + q.before + q.label.encode())
    h.update(repr(f.rows).encode() + bytes([f.transient]))
    if f.overlays:
        h.update(repr((f.overlay_select, f.overlays)).encode())
    return h.hexdigest()


# ------------------------------------------------------------------ link

def link(features: list[Feature], publish: bool = True, image_check: bool = True, resident=None) -> LinkResult:
    """resident: None / "pool" (KernPool block, relocated at install) or a static address / "static[:ADDR]"
    (linked at that address, no relocation list, no pool block; see the module docstring)."""
    static = parse_resident(resident)
    features = order_features(features)
    rows, errors, warnings = collect_rows(features)
    gated: set = set()
    if any(r["byte"] == ESS_BYTE for r in rows):
        gated, w = essential_split(features)
        warnings += w
    for r in rows:
        r["gated"] = r["feature"] in gated      # menu5: the row shows and stores; its on_change is cleared
    if not errors:
        features = [dataclasses.replace(f, source=f.late_source(rows)) if f.late_source else f for f in features]
    e2, w2 = check_conflicts(features, image_check=image_check)
    errors += e2 + lint(features)
    warnings += w2
    for f in features:
        if (f.hooks or f.redirects or f.pointers) and not f.source.strip():
            errors.append(f"{f.name}: declares hooks but has no source")
        if f.init and not f.source.strip():
            errors.append(f"{f.name}: declares init but has no source")
        if len(f.name) > 15 or len(f.version) > 7:
            errors.append(f"{f.name}: name <= 15 and version <= 7 characters")
    if errors:
        raise LinkError(errors)
    by = {f.name: f for f in features}
    position = {f.name: i for i, f in enumerate(features)}

    def extern_for(user):
        def extern(feat, sym):
            if feat == "koma":
                if sym not in CORE_EXPORTS:
                    raise LinkError([f"{user.name}: unknown core symbol $koma.{sym} (have {sorted(CORE_EXPORTS)})"])
                return CORE_EXPORTS[sym]
            if feat not in by:
                raise LinkError([f"{user.name}: references ${feat}.{sym} but {feat} is not selected"])
            if feat not in user.requires:
                raise LinkError([f"{user.name}: references ${feat}.{sym} without `requires=[{feat!r}]`"])
            if sym not in by[feat].exports:
                raise LinkError([f"{user.name}: ${feat}.{sym} is not exported by {feat}"])
            return ns(feat) + sym
        return extern

    # hook chains: per site, handlers in build order; each handler's `next` is the next handler or the original
    chains: dict[int, list] = {}
    for f in features:
        for h in f.hooks:
            chains.setdefault(h.site, []).append((f, h))
    for site, chain in sorted(chains.items()):
        names = [f.name for f, _ in chain]
        for n, why in CHAIN_FIRST.items():
            if n in names and names[0] != n:
                raise LinkError([f"hook chain at {site:#x}: {n} must be first, build order gives {names} ({why})"])
    next_equ = {}
    records = []
    early = []              # early-site hooks: entry veneer at a resident address congruent to the stock target
    for f in features:
        for c in f.checks:
            records.append(Record(K_CHECK, c.addr, c.before, owner=f.name, why=c.why))
        for p in f.patches:
            records.append(Record(K_DATA, p.addr, p.before, p.after, owner=f.name, why=p.why))
    for site, chain in sorted(chains.items()):
        for i, (f, h) in enumerate(chain):
            nxt = f"{ns(chain[i + 1][0].name)}{chain[i + 1][1].handler} + 1" if i + 1 < len(chain) else f"0x{h.target | 1:x}"
            next_equ[(f.name, h.next)] = nxt
        f0, h0 = chain[0]
        why = " -> ".join(f"{f.name}.{h.handler}" for f, h in chain) + f" -> {h0.target:#x}"
        before = fw.ram(site, 4) if image_check else _bl_before(chain)
        if site in EARLY_SITE_OK.get(f0.name, {}):
            label = f"koma_ven_{site:x}"
            early.append(dict(site=site, stock=h0.target, label=label, handler=ns(f0.name) + h0.handler,
                              gated=f0.name in gated, owner=f0.name, before=before))
            records.append(Record(K_BLHI, site, before, owner=f0.name, target=label,
                                  why=why + " (prefix-only: congruent entry veneer)"))
        else:
            records.append(Record(K_BL, site, before, owner=f0.name, target=ns(f0.name) + h0.handler, why=why))
    for f in features:
        for r in f.redirects:
            if r.site in EARLY_SITE_OK.get(f.name, {}):
                raise LinkError([f"{f.name} redirect {r.site:#x}: early sites must be hooks (a BL whose suffix stays)"])
            records.append(Record(K_BL, r.site, r.before, owner=f.name, target=ns(f.name) + r.handler,
                                  why=f"redirect -> {f.name}.{r.handler}: {r.why}"))
        for q in f.pointers:
            records.append(Record(K_PTR, q.addr, q.before, owner=f.name, target=ns(f.name) + q.label,
                                  addend=1 if q.thumb else 0, why=f"pointer -> {f.name}.{q.label}: {q.why}"))
    if publish:
        records.append(Record(K_DATA, fw.PUBLISH, fw.ram(fw.PUBLISH, 4), MANIFEST_MAGIC, owner="koma",
                              why="publish magic"))
        records.append(Record(K_PTR, fw.PUBLISH + 4, fw.ram(fw.PUBLISH + 4, 4), target="koma_manifest", owner="koma",
                              why="publish manifest pointer"))
    # checks first (cheap early reject), then the records of gated features last: the installer stops applying at
    # koma_recs_gated when ESSENTIALS is latched
    records.sort(key=lambda r: (0 if r.kind == K_CHECK else 2 if r.owner in gated else 1, r.addr))

    for f in features:
        if f.transient and (not f.init or f.hooks or f.redirects or f.pointers or f.exports or f.rows):
            raise LinkError([f"{f.name}: transient=True needs an init and no hooks, redirects, pointers, exports "
                             f"or rows"])
    resident_feats = [f for f in features if f.source.strip() and not f.transient]
    has_resident = bool(resident_feats) or publish or bool(rows)
    files: dict[str, bytes] = {}

    # ---------------- source
    s = []
    s.append(f".equ KOMA_IMAGE_VERSION, 0x{fw.IMAGE_VERSION:x}")
    s.append(f".equ KOMA_ABI, {fw.ABI}")
    s.append("koma_body:")
    s.append("    .ascii \"KMOD\"")
    s.append(f"    .hword {FORMAT}, {(FLAG_PUBLISH if publish else 0) | (FLAG_STATIC if static is not None else 0)}")
    s.append("    .word koma_recs - koma_body, koma_relocs - koma_body, koma_inits - koma_body")
    s.append("    .word koma_res - koma_body, koma_res_end - koma_res, 0")   # +28 O_TRELOCS, patched after assembly
    s.append(INSTALLER_SRC)
    s.append(".ltorg\n.p2align 2\nkoma_recs:")
    first_gated = next((i for i, r in enumerate(records) if r.kind != K_CHECK and r.owner in gated), None)
    s.insert(0, ".equ KOMA_GATE_OFF, koma_recs_gated - koma_body" if first_gated is not None
             else ".equ KOMA_GATE_OFF, 0x7fffffff")
    s.insert(0, ".equ KOMA_ESS_OFF, koma_ess - koma_res" if gated else ".equ KOMA_ESS_OFF, 0")
    for i, r in enumerate(records):
        if i == first_gated:
            s.append("koma_recs_gated:")
        n = len(r.before)
        size = 12 + len(_pad4(r.before)) + (len(_pad4(r.after)) if r.kind == K_DATA else
                                             4 if r.kind in (K_BL, K_PTR, K_BLHI) else 0)
        s.append(f"    .word 0x{r.addr:08x}\n    .hword {r.kind}, {n}\n    .word {size}    @ {r.owner}: {r.why}")
        s.append(_bytes_asm(_pad4(r.before), files, f"rec{i}b"))
        if r.kind == K_DATA:
            s.append(_bytes_asm(_pad4(r.after), files, f"rec{i}a"))
        elif r.kind in (K_BL, K_PTR, K_BLHI):
            s.append(f"    .word {r.target} + {r.addend} - koma_res")
    s.append("    .word 0\nkoma_inits:")
    ovf = [f for f in features if _has_ovl_init(f)]
    inits = ["koma_settings_init"] if rows else []
    for f in features:
        inits += ([ns(f.name) + "ovl_init"] if f in ovf else []) + ([ns(f.name) + f.init] if f.init else [])
    s.append(f"    .word {len(inits)}")
    trans = {ns(f.name) + f.init for f in features if f.init and f.transient} | {ns(f.name) + "ovl_init" for f in ovf}
    for lbl in inits:
        s.append(f"    .word {lbl} - koma_body + 0x80000000" if lbl in trans else f"    .word {lbl} - koma_res")
    gated_inits = {ns(f.name) + x for f in features if f.name in gated for x in (f.init, "ovl_init") if x}
    s.append("koma_igate:")                       # transient, one byte per init: 1 = skipped when ESSENTIALS
    s.append(f"    .byte {', '.join('1' if lbl in gated_inits else '0' for lbl in inits) or '0'}")
    s.append(".p2align 2")
    for f in features:
        if f.transient:
            s.append(_feature_src(f, position[f.name], next_equ, extern_for, files))
        elif f.overlays:
            s.append(_overlay_transient_src(f, position[f.name], extern_for))
    s.append(".p2align 2\nkoma_res:")
    if has_resident:
        s.append("koma_manifest:\n    .ascii \"KOMA\"")
        s.append(f"    .hword {FORMAT}, {len(features)}")
        s.append("koma_build_id:\n    .word 0\nkoma_status:\n    .word 0\nkoma_version:")
        s.append(f"    .space {VERSION_LEN}")
        for f in features:
            s.append(f"koma_feat_{position[f.name]}:")
            s.append(_bytes_asm(_cstr(f.name, 16) + _cstr(f.version, 8), files, f"fe{position[f.name]}"))
            s.append(f"    .word 0x{int(feature_hash(f)[:8], 16):08x}")
            s.append("    .word 0" if f.transient else f"    .word kf_end_{position[f.name]} - kf_start_{position[f.name]}")
    if rows:
        s.append("\n@ ======== core: Koma settings init\n.p2align 2")
        s.append(settings_init_src(rows))
    anchors = []            # early-site veneer slots between resident features: (label, gated region)

    def anchor(g):
        if early:
            anchors.append((f"koma_va_{len(anchors)}", g))
            s.append(f".p2align 2\n{anchors[-1][0]}:")
    for f in features:
        if not f.transient and f.name not in gated:
            anchor(False)
            s.append(_feature_src(f, position[f.name], next_equ, extern_for, files))
    anchor(False)
    s.append(".p2align 2\nkoma_res_gated:")      # ESSENTIALS: the installer allocates and copies only up to here
    for f in features:
        if not f.transient and f.name in gated:
            anchor(True)
            s.append(_feature_src(f, position[f.name], next_equ, extern_for, files))
    anchor(True)
    ven_placeholder = "".join(f"{e['label']}:\n" for e in early) + "@ (pass 0: veneer labels, no bytes)"
    if early:
        s.append(ven_placeholder)
    s.append(".p2align 2\nkoma_res_end:\nkoma_canary:\nkoma_relocs:")
    s.insert(0, f".equ KOMA_STATIC, {0 if static is None else 1}\n.equ KOMA_STATIC_ADDR, 0x{static or 0:x}\n"
                f".equ KOMA_CANARY_MAGIC, 0x{CANARY_MAGIC:x}\n")
    s.insert(0, f".equ KOMA_GATED, {1 if gated else 0}\n.equ KOMA_SET_MAGIC, 0x{SET_MAGIC:x}\n"
                f".equ KOMA_SET_VERSION, {SET_VERSION}\n.equ KOMA_ESS_REL, {ESS_BYTE - SET_MAGIC}")
    pads = {f.name: 0 for f in features if f.overlays}
    for _ in range(2):          # pass 2 only when a later overlay is larger than overlay 0: pad the area
        source = "".join(f".equ KOMA_PAD_{position[n]}, {v}\n" for n, v in pads.items()) + "\n".join(s) + "\n"
        try:
            code, syms, relocs = assemble(source, files)
        except AsmError as e:
            raise LinkError([f"assembly failed:\n{e}"]) from None
        grow = {}
        for f in features:
            if f.overlays:
                p = ns(f.name)
                area = syms[p + "ovl_end"] - syms[p + "ovl"]
                need = max([syms[p + f"ovk{k}_end"] - syms[p + f"ovk{k}"] for k in range(1, len(f.overlays))] + [0])
                if need > area:
                    grow[f.name] = pads[f.name] + need - area
        if not grow:
            break
        pads.update(grow)
    else:
        raise LinkError(["overlay padding did not converge"])
    if early:
        if static is None:
            raise LinkError([f"early-site hooks {[hex(e['site']) for e in early]} need a static resident (--resident "
                             f"static): the entry address must be known at link time (EARLY_SITE_OK)"])
        plan = _plan_veneers(early, anchors, syms, syms["koma_res"], static)
        s[s.index(ven_placeholder)] = ""
        for (lbl, _), text in zip(anchors, _veneer_src(plan, anchors)):
            s[s.index(f".p2align 2\n{lbl}:")] = f".p2align 2\n{lbl}:\n{text}"
        source = "".join(f".equ KOMA_PAD_{position[n]}, {v}\n" for n, v in pads.items()) + "\n".join(s) + "\n"
        try:
            code, syms, relocs = assemble(source, files)
        except AsmError as e:
            raise LinkError([f"assembly failed (early-site veneers):\n{e}"]) from None
    if syms.get("koma_relocs") != len(code):
        raise LinkError([f"layout: koma_relocs {syms.get('koma_relocs')} != code length {len(code)}"])

    # ---------------- relocations: only resident words, only pointing into the resident segment
    body = bytearray(code)
    o_res, res_len = syms["koma_res"], syms["koma_res_end"] - syms["koma_res"]
    problems, rel, trel = [], [], []
    tspans = [(syms[f"kf_start_{position[f.name]}"], syms[f"kf_end_{position[f.name]}"]) for f in features if f.transient]
    tspans += [(syms[f"kt_start_{position[f.name]}"], syms[f"kt_end_{position[f.name]}"]) for f in features
               if f.overlays and not f.transient]
    for off in relocs:
        val = struct.unpack_from(">I", body, off)[0]
        if any(a <= off < b for a, b in tspans):
            if off % 4:
                problems.append(f"transient word at {off:#x} ({_sym_at(syms, off)}) is not 4-aligned")
            elif not ((o_res <= (val & ~1) <= o_res + res_len) or any(a <= (val & ~1) < b for a, b in tspans)):
                problems.append(f"transient word at {off:#x} ({_sym_at(syms, off)}) points outside its own code "
                                f"and the resident segment ({val:#x})")
            trel.append(off)
            continue
        if not o_res <= off < o_res + res_len:
            problems.append(f"absolute reference at body {off:#x} outside the resident segment "
                            f"({_sym_at(syms, off)}): use offsets in transient code")
            continue
        if off % 4:
            problems.append(f"relocated word at {off:#x} ({_sym_at(syms, off)}) is not 4-aligned")
        if not o_res <= (val & ~1) <= o_res + res_len:
            problems.append(f"word at {off:#x} ({_sym_at(syms, off)}) points outside the resident segment ({val:#x})")
        struct.pack_into(">I", body, off, val - o_res)
        rel.append(off - o_res)
    if problems:
        raise LinkError(problems)
    ess_len = syms["koma_res_gated"] - o_res
    dangling = []           # ESSENTIALS: words in the kept prefix that point into the gated (unallocated) tail
    if gated:
        m5 = [(syms[f"kf_start_{position[f.name]}"], syms[f"kf_end_{position[f.name]}"]) for f in features
              if f.name == "menu5"]
        for r_off in rel:
            v = struct.unpack_from(">I", body, o_res + r_off)[0]
            if r_off < ess_len and (v & ~1) >= ess_len:
                # menu5's own words are allowed (by address: _sym_at can name an .equ such as K_RES_ESS instead)
                if any(a <= o_res + r_off < b for a, b in m5):
                    continue
                dangling.append(f"{_sym_at(syms, o_res + r_off)} -> {_sym_at(syms, o_res + (v & ~1))}")
        bad = [d for d in dangling if not d.startswith(ns("menu5"))]
        if bad:
            raise LinkError([f"ESSENTIALS: kept word points into a gated feature: {d}" for d in bad])
    nrel = len(rel)
    early_info = []
    if static is not None:
        problems = _static_problems(static, res_len, records, syms, o_res)
        if problems:
            raise LinkError(problems)
        for r_off in rel:                    # bake: every resident word holds ADDR + offset; no list to apply
            v = struct.unpack_from(">I", body, o_res + r_off)[0]
            struct.pack_into(">I", body, o_res + r_off, v + static)
        rel = []
        body += struct.pack(">III", 0, CANARY_MAGIC, static)
        early_info = _check_early(early, body, syms, o_res, static)
    else:
        body += struct.pack(">I", len(rel)) + b"".join(struct.pack(">I", r) for r in rel)
    if tspans:
        struct.pack_into(">I", body, 28, len(body))                  # header O_TRELOCS
        body += struct.pack(">I", len(trel)) + b"".join(struct.pack(">I", r) for r in trel)

    # ---------------- build id + version string (id = sha256 of the body with both fields zero)
    # (the trailing " G<n>" is MIN_FIRMWARE_GEN; it fits the 24-byte field with its NUL: 23 characters)
    build_id, version = 0, f"KOMA V{KOMA_VERSION} G{MIN_FIRMWARE_GEN}"
    if has_resident:
        bid = int(hashlib.sha256(bytes(body)).hexdigest()[:8], 16)
        version = f"KOMA V{KOMA_VERSION} {bid:08X} G{MIN_FIRMWARE_GEN}"
        struct.pack_into(">I", body, syms["koma_build_id"], bid)
        body[syms["koma_version"]:syms["koma_version"] + VERSION_LEN] = _cstr(version, VERSION_LEN)
        build_id = bid
    if len(body) > fw.MAXLEN:
        raise LinkError([f"body is {len(body)} bytes, over the loader's {fw.MAXLEN} limit; drop a feature"])
    module = grmod.pack_module(bytes(body), syms["koma_entry"])
    assert grmod.parse_module(module)["ok"]

    manifest = dict(
        koma_version=KOMA_VERSION, version_string=version, version_short=f"V{KOMA_VERSION}",
        menu_version=menu_version(), photo_tag=photo_tag(), min_firmware_gen=MIN_FIRMWARE_GEN,
        build_id=f"{build_id:08X}", format=FORMAT, publish=publish,
        publish_addr=f"{fw.PUBLISH:#x}" if publish else None,
        module=dict(bytes=len(module), sha256=hashlib.sha256(module).hexdigest(),
                    body_bytes=len(body), entry=f"{syms['koma_entry']:#x}", maxlen=fw.MAXLEN),
        layout=dict(installer=[f"{syms['koma_entry']:#x}", f"{syms['koma_recs']:#x}"],
                    records=[f"{syms['koma_recs']:#x}", f"{syms['koma_inits']:#x}"],
                    resident=[f"{o_res:#x}", f"{o_res + res_len:#x}"], resident_bytes=res_len,
                    relocs=len(rel), transient_bytes=len(body) - res_len,
                    resident_mode="pool" if static is None else "static",
                    **({} if static is None else dict(
                        static_addr=f"{static:#x}", static_end=f"{static + res_len:#x}",
                        canary=[f"{static + res_len:#x}", f"{static + res_len + CANARY_LEN:#x}"],
                        static_span=[f"{STATIC_SPAN[0]:#x}", f"{STATIC_SPAN[1]:#x}"], words_baked=nrel)),
                    pool_peak_bytes=len(module) + (0 if static is not None else res_len)),   # module + resident (D)
        essentials=dict(gate=any(r["byte"] == ESS_BYTE for r in rows), gated=sorted(gated),
                        resident_bytes_full=res_len, resident_bytes_essentials=ess_len, dangling=dangling),
        features=[dict(name=f.name, version=f.version, summary=f.summary, options=f.options,
                       sha256=feature_hash(f), resident_bytes=0 if f.transient else syms[f"kf_end_{position[f.name]}"]
                       - syms[f"kf_start_{position[f.name]}"], transient=f.transient,
                       transient_code_bytes=syms[f"kf_end_{position[f.name]}"] - syms[f"kf_start_{position[f.name]}"]
                       if f.transient else 0,
                       resident_offset=f"{syms[f'kf_start_{position[f.name]}'] - o_res:#x}",
                       patches=[dict(addr=f"{p.addr:#x}", len=len(p.before), why=p.why) for p in f.patches],
                       checks=[dict(addr=f"{c.addr:#x}", len=len(c.before), why=c.why) for c in f.checks],
                       redirects=[dict(site=f"{r.site:#x}", handler=r.handler, why=r.why) for r in f.redirects],
                       pointers=[dict(addr=f"{q.addr:#x}", label=q.label, thumb=q.thumb, why=q.why)
                                 for q in f.pointers],
                       hooks=[dict(site=f"{h.site:#x}", target=f"{h.target:#x}", handler=h.handler,
                                   handler_offset=f"{syms[ns(f.name) + h.handler] - o_res:#x}",
                                   chain_next=next_equ[(f.name, h.next)], why=h.why,
                                   **({"rewrites_r0": True} if h.rewrites_r0 else {}),
                                   **({"early_ok": h.early_ok} if h.early_ok else {})) for h in f.hooks],
                       settings=[dict(range=[f"{x.start:#x}", f"{x.end:#x}"], owned=x.owned, why=x.why)
                                 for x in f.settings] + [dict(range=[f"{r['byte']:#x}", f"{r['byte'] + 1:#x}"],
                                                              owned=True, why=f"KOMA row {r.get('id')}")
                                                         for r in f.rows if "byte" in r],
                       init=f.init, requires=f.requires, conflicts=f.conflicts,
                       **({"ram_writes": [dict(range=[f"{a0:#x}", f"{a1:#x}"], why=w) for a0, a1, w in f.ram_writes]}
                          if f.ram_writes else {}),
                       **({"overlays": dict(select=f"{f.overlay_select:#x}" if f.overlay_select else None,
                                            area_bytes=syms[ns(f.name) + "ovl_end"] - syms[ns(f.name) + "ovl"],
                                            bytes=[syms[ns(f.name) + "ovl_end"] - syms[ns(f.name) + "ovl"]
                                                   - pads[f.name]] + [syms[ns(f.name) + f"ovk{k}_end"]
                                                                      - syms[ns(f.name) + f"ovk{k}"]
                                                                      for k in range(1, len(f.overlays))])}
                          if f.overlays else {}))
                  for f in features],
        records=[dict(kind=KIND_NAMES[r.kind], addr=f"{r.addr:#x}", len=len(r.before), owner=r.owner, why=r.why,
                      **({"target": r.target, "target_offset": f"{syms[r.target] + r.addend - o_res:#x}"}
                         if r.target else {}))
                 for r in records],
        early_sites=early_info,
        settings_core=dict(magic=f"{SET_MAGIC:#x}", version_addr=f"{SET_VERSION_ADDR:#x}", version=SET_VERSION,
                           range=[f"{SET_MAGIC:#x}", f"{SET_VERSION_ADDR + 1:#x}"], init=bool(rows)),
        rows=[dict(r, byte=f"{r['byte']:#x}") for r in rows],
        warnings=warnings)
    return LinkResult(module=module, body=bytes(body), manifest=manifest, source=source, symbols=syms,
                      warnings=warnings)


def _static_problems(addr, res_len, records, syms, o_res):
    """A static resident address must be word-aligned, keep the segment and its canary inside STATIC_SPAN, and keep
    every BL record's handler within Thumb BL range (+-4 MB) of its site (the installer's range pass would refuse the
    install on the camera; here it refuses the build)."""
    out = []
    if addr % 4:
        out.append(f"static resident address {addr:#x} is not 4-aligned")
    if addr < STATIC_SPAN[0] or addr + res_len + CANARY_LEN > STATIC_SPAN[1]:
        out.append(f"static resident {addr:#x}..{addr + res_len + CANARY_LEN:#x} (segment {res_len} B + canary) is "
                   f"outside the clean span {STATIC_SPAN[0]:#x}..{STATIC_SPAN[1]:#x}")
    for r in records:
        if r.kind in (K_BL, K_BLHI):
            disp = addr + syms[r.target] + r.addend - o_res - (r.addr + 4)
            if not -0x400000 <= disp < 0x400000:
                out.append(f"bl {r.addr:#x} -> {addr + syms[r.target] - o_res:#x}: out of Thumb BL range")
    return out


def _feature_src(f, i, next_equ, extern_for, files):
    s = [f"\n@ ======== feature {f.name} {f.version}{' (transient)' if f.transient else ''}\n.p2align 2\nkf_start_{i}:"]
    if f.source.strip():
        for h in f.hooks:
            s.append(f".equ {ns(f.name)}{h.next}, {next_equ[(f.name, h.next)]}")
        s.append(mangle_source(f, extern_for(f), _ovl_names(f)))
        s.append(".ltorg")
        if f.overlays:
            p = ns(f.name)
            s.append(f".p2align 2\n{p}ovl:")
            s.append(mangle_source(dataclasses.replace(f, source=f.overlays[0]), extern_for(f), _ovl_names(f)))
            s.append(f".p2align 2\n    .space KOMA_PAD_{i}\n{p}ovl_end:")
        for lbl, data in f.blobs.items():
            s.append(f".p2align 2\n{ns(f.name)}{lbl}:")
            s.append(_bytes_asm(data, files, f"blob{i}_{lbl}"))
    s.append(f".p2align 2\nkf_end_{i}:")
    return "\n".join(s)


def _has_ovl_init(f):
    return bool(f.overlays) and len(f.overlays) > 1 and f.overlay_select is not None and not f.transient


def _ovl_rename(k, src):
    """Overlay k >= 1: label definitions `name:` -> `ov<k>_name:` (their place in the body); returns (text, names)."""
    out, names = [], []
    for line in src.splitlines():
        code = _strip_comment(line)
        rest, pre = code, ""
        while True:
            m = _LABEL.match(rest)
            if not m:
                break
            pre += rest[:m.start(1)] + f"ov{k}_{m.group(1)}:"
            names.append(m.group(1))
            rest = rest[m.end():]
        out.append(pre + rest + line[len(code):])
    return "\n".join(out), names


def _ovl_names(f):
    names = local_names(f.source) | {h.next for h in f.hooks} | set(f.blobs)
    if f.overlays:
        names |= local_names(f.overlays[0]) | {"ovl", "ovl_end", "ovl_init", "ovl_tab"}
        for k, src in enumerate(f.overlays[1:], 1):
            _, ln = _ovl_rename(k, src)
            names |= set(ln) | {f"ov{k}_{n}" for n in ln} | {f"ovk{k}", f"ovk{k}_end"} | local_names(src)
    return names


def _overlay_transient_src(f, i, extern_for):
    """Overlays 1.. in the transient body, their labels aliased into the resident area, and the copy init
    (r0 = svc, r1 = body base; reads the select byte, copies overlay[byte] over `ovl` when 1 <= byte < n)."""
    n = len(f.overlays)
    s = [f"\n@ ======== feature {f.name}: layout overlays (transient)\n.p2align 2\nkt_start_{i}:"]
    t = []
    if _has_ovl_init(f):
        t += ["ovl_init:", "    push {r4, lr}", f"    ldr r0, =0x{f.overlay_select:x}", "    ldrb r0, [r0]",
              f"    cmp r0, #{n}", "    bhs 9f", "    cmp r0, #0", "    beq 9f", "    lsls r0, r0, #3",
              "    ldr r1, =ovl_tab", "    adds r1, r1, r0", "    ldr r3, [r1, #4]", "    ldr r1, [r1, #0]",
              "    ldr r2, =ovl", "1:  subs r3, #4", "    bmi 9f", "    ldr r0, [r1, r3]", "    str r0, [r2, r3]",
              "    b 1b", "9:  pop {r4}", "    pop {r3}", "    bx r3", ".ltorg", ".p2align 2", "ovl_tab:",
              "    .word 0, 0"] + [f"    .word ovk{k}, ovk{k}_end - ovk{k}" for k in range(1, n)]
    for k, src in enumerate(f.overlays[1:], 1):
        text, ln = _ovl_rename(k, src)
        t += [".p2align 2", f"ovk{k}:", text, ".p2align 2", f"ovk{k}_end:"]
        t += [f".set {x}, ovl + (ov{k}_{x} - ovk{k})" for x in ln]
    s.append(mangle_source(dataclasses.replace(f, source="\n".join(t)), extern_for(f), _ovl_names(f)))
    s.append(f".ltorg\n.p2align 2\nkt_end_{i}:")
    return "\n".join(s)


# ------------------------------------------------------------------ early-site veneers (EARLY_SITE_OK)
# Entry veneer at E = stock target (mod 4096), then on to the chain's first handler H. Neither form touches r0-r3, lr,
# sp or the stack; "b" leaves every register alone, "long" uses ip (r12), the AAPCS intra-call scratch register that
# veneers may corrupt (the waitdiag handlers already clobber ip in wd_tail, and the stock callees are AAPCS functions).
#   b     (H within the Thumb B range of E):   E: b H
#   long  (otherwise; ARMv4T-safe, no ldr-pc interworking needed):
#         E (4-aligned): bx pc ; mov r8, r8 ; .arm ldr ip, [pc, #0] ; bx ip ; .word H | 1
#         E = 2 (mod 4): one `mov r8, r8` first, so `bx pc` sits on a word boundary (bx pc from a halfword-only address
#         is UNPREDICTABLE)
VENEER_LONG = bytes.fromhex("477846c0e59fc000e12fff1c")       # + .word H|1 (big-endian)
VENEER_NOP = bytes.fromhex("46c0")


def _veneer_size(kind, entry):
    if kind == "b":
        return ((entry + 2 + 3) & ~3) - entry
    return (2 if entry % 4 else 0) + 16


def _plan_veneers(early, anchors, syms, o_res, static):
    """Choose a slot (anchor between resident features, same ESSENTIALS region as the owner) for every early veneer,
    minimising the total padding. Pass-0 layout (empty slots) + shift simulation: everything between slots only
    moves by multiples of 4 (veneer blocks end word aligned; no source aligns beyond 4), so the simulated addresses
    are exact; _check_early re-derives them from the assembled bytes and refuses the build on any mismatch.
    Returns {anchor index: [(early entry, pad, kind)]}."""
    a0 = [syms[lbl] - o_res for lbl, _ in anchors]
    h0 = {e["site"]: syms[e["handler"]] - o_res for e in early}

    def sweep(assign):
        kinds = {e["site"]: "b" for e in early}
        while True:
            shift, out, blocks, entries = 0, {}, [], {}
            for k in range(len(anchors)):
                pos = a0[k] + shift
                start = pos
                for e in assign.get(k, []):
                    pad = (e["stock"] - (static + pos)) & 0xFFF
                    entry = pos + pad
                    kind = kinds[e["site"]]
                    out.setdefault(k, []).append((e, pad, kind))
                    entries[e["site"]] = entry
                    pos = entry + _veneer_size(kind, entry)
                blocks.append((a0[k], pos - start))
                shift += pos - start
            changed = False
            for e in early:
                if e["site"] not in entries:
                    continue
                h = h0[e["site"]] + sum(n for a, n in blocks if a <= h0[e["site"]])
                d = h - (entries[e["site"]] + 4)
                if kinds[e["site"]] == "b" and not -2048 <= d <= 2046:
                    kinds[e["site"]] = "long"
                    changed = True
            if not changed:
                return out, sum(p for v in out.values() for _, p, _ in v)

    # Branch and bound over (anchor order x veneer order): walk the anchors in address order, at each either move on or
    # place one more remaining veneer there (cheapest pad first); prune when the padding so far reaches the best total.
    # The kind used while searching is a guess (b when the handler is in range at the current shift); sweep() then
    # recomputes the exact layout and kinds for the chosen assignment.
    n_a = len(anchors)
    best = [None, None]
    order = sorted(early, key=lambda e: e["site"])

    def dfs(k, shift, rem, cost, assign):
        if best[0] is not None and cost >= best[0]:
            return
        if not rem:
            best[0], best[1] = cost, {kk: list(v) for kk, v in assign.items()}
            return
        if k == n_a:
            return
        pos = a0[k] + shift
        opts = []
        for e in rem:
            if anchors[k][1] != e["gated"]:
                continue
            pad = (e["stock"] - (static + pos)) & 0xFFF
            entry = pos + pad
            d = h0[e["site"]] + shift - (entry + 4)
            opts.append((pad, e, _veneer_size("b" if -2048 <= d <= 2046 else "long", entry)))
        for pad, e, size in sorted(opts, key=lambda x: (x[0], x[1]["site"])):
            assign.setdefault(k, []).append(e)
            dfs(k, shift + pad + size, [x for x in rem if x is not e], cost + pad, assign)
            assign[k].pop()
            if not assign[k]:
                del assign[k]
        dfs(k + 1, shift, rem, cost, assign)

    dfs(0, 0, order, 0, {})
    if best[1] is None:
        raise LinkError(["early-site veneers: no slot in the owner's resident region"])
    return sweep(best[1])[0]


def _veneer_src(plan, anchors):
    out = []
    for k in range(len(anchors)):
        t = []
        for e, pad, kind in plan.get(k, []):
            if pad:
                t.append(f"    .space {pad}")
            t.append(f"{e['label']}:            @ early site {e['site']:#x}: stock {e['stock']:#x}, entry = stock (mod 4096)")
            if kind == "b":
                t.append(f"    b {e['handler']}")
            else:
                t.append(".p2align 1\n    mov r8, r8" if (e["stock"] & 2) else "")
                t += ["    bx pc", "    mov r8, r8", ".arm", "    ldr ip, [pc, #0]", "    bx ip",
                      f"    .word {e['handler']} + 1", ".thumb"]
            t.append(".p2align 2")
        out.append("\n".join(x for x in t if x))
    return out


def veneer_target(code: bytes, off: int, addr: int) -> tuple[str, int] | None:
    """Decode an early-site entry veneer at code[off:] (runtime address addr) -> (kind, Thumb handler address)."""
    hw = int.from_bytes(code[off:off + 2], "big")
    if hw & 0xF800 == 0xE000:
        d = (hw & 0x7FF) << 1
        if d & 0x800:
            d -= 0x1000
        return "b", addr + 4 + d
    o = off + (2 if code[off:off + 2] == VENEER_NOP and addr % 4 == 2 else 0)
    if code[o:o + 12] == VENEER_LONG:
        return "long", int.from_bytes(code[o + 12:o + 16], "big") & ~1
    return None


def bl_halves(site: int, target: int) -> tuple[int, int]:
    """(prefix, suffix) halfwords of a Thumb BL site -> target (ARMv4T/v5 two-instruction BL, H=10 then H=11)."""
    b = thumb_bl(site, target)
    return int.from_bytes(b[:2], "big"), int.from_bytes(b[2:], "big")


def _check_early(early, body, syms, o_res, static):
    """Refuse the build unless every early site's new BL keeps the stock suffix halfword and its veneer reaches the
    handler. Returns the manifest's early_sites list."""
    out, problems = [], []
    for e in early:
        entry = static + syms[e["label"]] - o_res
        handler = static + syms[e["handler"]] - o_res
        old_hi, old_lo = int.from_bytes(e["before"][:2], "big"), int.from_bytes(e["before"][2:], "big")
        new_hi, new_lo = bl_halves(e["site"], entry)
        v = veneer_target(body, syms[e["label"]], entry)
        mixed = bl_target(e["site"], e["before"][:2] + new_lo.to_bytes(2, "big"))
        if bl_target(e["site"], e["before"]) != e["stock"]:
            problems.append(f"early site {e['site']:#x}: before-bytes are not bl {e['stock']:#x}")
        if entry & 0xFFF != e["stock"] & 0xFFF or new_lo != old_lo:
            problems.append(f"early site {e['site']:#x}: entry {entry:#x} not = stock {e['stock']:#x} (mod 4096); "
                            f"the BL suffix would change ({old_lo:04x} -> {new_lo:04x}); refusing a two-halfword patch")
        if v is None or v[1] != handler:
            problems.append(f"early site {e['site']:#x}: veneer at {entry:#x} does not reach {handler:#x} ({v})")
        if mixed != e["stock"]:
            problems.append(f"early site {e['site']:#x}: old prefix + new suffix = {mixed} != stock {e['stock']:#x}")
        out.append(dict(site=f"{e['site']:#x}", stock_target=f"{e['stock']:#x}", entry=f"{entry:#x}",
                        handler=f"{handler:#x}", veneer=v[0] if v else None, residue=f"{entry & 0xFFF:#05x}",
                        prefix=[f"{old_hi:04x}", f"{new_hi:04x}"], suffix=f"{new_lo:04x}", suffix_unchanged=new_lo == old_lo,
                        suspended_old_prefix_target=f"{mixed:#x}" if mixed is not None else None))
    if problems:
        raise LinkError(problems)
    return out


def _bl_before(chain):
    return fw.ram(chain[0][1].site, 4)


def _sym_at(syms, off):
    best = max(((v, k) for k, v in syms.items() if v <= off), default=(0, "?"))
    return f"{best[1]}+{off - best[0]:#x}"
