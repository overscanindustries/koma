"""Koma feature format: what one feature declares. Pure data; the linker (linker.py) turns a list of these into one GRMOD.

A feature is a Python module in tools/koma/features/ that exposes `make(opts: dict) -> Feature`.

Address conventions: every address is a 2.40 runtime address (payload loaded at 0x1c000). Ranges are [start, end).
"""
from __future__ import annotations

from dataclasses import dataclass, field

SETTINGS_BLOCK = (0x1B000, 0x1BFFC)   # user-settings block; 0x1bffc holds the u16 sum the firmware recomputes on save


@dataclass(frozen=True)
class Patch:
    """Data patch: RAM [addr, addr+len) must equal `before`; the installer writes `after` (IRQ/FIQ masked)."""
    addr: int
    before: bytes
    after: bytes
    why: str = ""

    def __post_init__(self):
        if not self.before or len(self.before) != len(self.after):
            raise ValueError(f"patch {self.addr:#x}: before/after must be non-empty and the same length")
        if len(self.before) > 0xFFFF:
            raise ValueError(f"patch {self.addr:#x}: longer than 0xffff; split it")

    @property
    def end(self):
        return self.addr + len(self.before)


@dataclass(frozen=True)
class Check:
    """Before-only dependency: RAM must equal `before` or nothing installs. Never written."""
    addr: int
    before: bytes
    why: str = ""

    @property
    def end(self):
        return self.addr + len(self.before)


@dataclass(frozen=True)
class Hook:
    """Redirect the Thumb `bl target` at `site` to the feature's `handler` label.

    Wrap convention: the handler is entered exactly as `target` would be (same r0-r3, stack, lr) and must call the
    next link through the linker-defined symbol `next` (load it with `ldr rX, =<next>` and `bx` through a helper),
    then return that call's result (or its own). `next` is the original target when this feature is the only one on
    the site, or the next feature's handler when several features hook the same site (chain order = build order).
    Preserve r4-r11 like any AAPCS callee.
    """
    site: int
    target: int
    handler: str
    next: str
    why: str = ""
    smoke_reachable: bool = True     # False: `koma smoke` never visits this path (menu, playback, ...): SKIP, not FAIL
    rewrites_r0: bool = False        # True: the handler deliberately passes a different r0 to `next` (selfcheck allows)

    @property
    def end(self):
        return self.site + 4


@dataclass(frozen=True)
class Redirect:
    """Exclusive BL redirect: the 4 bytes at `site` (any instruction pair, verified against `before`) become a Thumb
    `bl handler`. Unlike Hook there is no chain and no wrap convention: the handler replaces whatever was there
    (menu5 uses it for `subs;cmp` pairs and for call sites whose original it does not always call)."""
    site: int
    before: bytes
    handler: str
    why: str = ""

    def __post_init__(self):
        if len(self.before) != 4:
            raise ValueError(f"redirect {self.site:#x}: before must be 4 bytes")

    @property
    def end(self):
        return self.site + 4


@dataclass(frozen=True)
class Pointer:
    """32-bit word at `addr` (4-aligned, verified against `before`) := runtime address of resident `label`
    (+1 when `thumb`, for function pointers)."""
    addr: int
    before: bytes
    label: str
    thumb: bool = False
    why: str = ""

    def __post_init__(self):
        if len(self.before) != 4:
            raise ValueError(f"pointer {self.addr:#x}: before must be 4 bytes")

    @property
    def end(self):
        return self.addr + 4


@dataclass(frozen=True)
class Settings:
    """Settings-block bytes [start, end). owned=True: this feature's private store (exclusive);
    owned=False: stock fields the feature writes at runtime (shared; overlaps with another feature's owned range are
    conflicts, overlaps with another feature's writes are warnings). A stock write (owned=False) must declare the
    stock-valid values it may write: `values` is a collection of ints for every byte of the range, or a dict
    {addr: collection} covering every byte. The linker refuses an owned=False range without it."""
    start: int
    end: int
    owned: bool = True
    why: str = ""
    values: object = None
    # bulk=True: a whole-block copy of externally supplied bytes (settingsio's PARAM.DAT import). No per-byte value
    # list is possible; the linker allows it only for the features in linker.BULK_SETTINGS_WRITERS.
    bulk: bool = False

    def allowed(self, addr):
        if isinstance(self.values, dict):
            return self.values.get(addr)
        return self.values


@dataclass
class Feature:
    name: str
    version: str
    summary: str
    patches: list[Patch] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    hooks: list[Hook] = field(default_factory=list)
    redirects: list[Redirect] = field(default_factory=list)
    pointers: list[Pointer] = field(default_factory=list)
    settings: list[Settings] = field(default_factory=list)
    # Thumb source (llvm-mc syntax, arm7tdmi). Labels and .equ/.set names are private to the feature: the linker
    # prefixes them. Reference another feature's exported symbol as `$<feature>.<symbol>` (requires a dependency),
    # and Koma core symbols as `$koma.<symbol>` (see linker.CORE_EXPORTS). `@` starts a comment.
    source: str = ""
    exports: list[str] = field(default_factory=list)
    init: str | None = None          # label called once by the installer after all checks pass, before patching
    # Any feature with source is resident: its code, tables and blobs are copied to the resident pool block.
    blobs: dict[str, bytes] = field(default_factory=dict)   # label -> bytes, placed 4-aligned after the code
    requires: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    options: dict = field(default_factory=dict)   # the resolved build options, recorded in the manifest
    smoke: object = None             # optional callable(obs) -> [(check, ok, detail)], see smoke.py
    # KOMA page rows (module-level ROWS of the feature file, attached by features.make; format in CONTRACT-0.1.md):
    # dict(id, label, options, byte, default). The core writes defaults at install when the magic is missing.
    rows: list = field(default_factory=list)
    # Late source: callable(rows) -> str, called by the linker with every selected feature's rows (build order,
    # each row dict + "feature" + "on_change" label or None). Replaces `source`. Used by the menu (menu5).
    late_source: object = None
    # transient=True: init-only feature. Its code, data and blobs stay in the transient body (freed after install)
    # instead of the resident block. Needs `init`; no hooks, redirects, pointers, exports or rows. `ldr rX, =label`
    # works for its own labels and for resident ones ($koma.manifest, ...) through transient relocations.
    transient: bool = False
    # Layout overlays (menu5's MENU LAYOUT tables): data-only
    # sources, one per value of the settings byte `overlay_select`. Overlay 0 is assembled in place at the feature's
    # resident label `ovl` (the default; it must be the largest or the linker pads the area); overlays 1.. stay in the
    # transient body with every label aliased into that area (`name` = ovl + its offset in the overlay), and a
    # linker-generated transient init (run just before `init`) copies overlay[byte] over the area when 1 <= byte < n.
    # Only data: no code, no references to another overlay's labels. Resident code may use any overlay's labels, but
    # only while that overlay is the selected one.
    overlays: list = field(default_factory=list)
    overlay_select: int | None = None
