"""Feature registry. Each module here exposes NAME, SUMMARY, OPTIONS (name -> help) and make(opts) -> Feature."""
from __future__ import annotations

import importlib

REGISTRY = {"tone-r3": "tone_r3", "snap15": "snap15", "recipe-slots": "recipe_slots", "menu5": "menu5",
            "snapdist": "snapdist", "fullpress": "fullpress", "fastkeys": "fastkeys", "telemetry": "telemetry",
            "autoiso": "autoiso", "longexp": "longexp", "hlalert": "hlalert", "bwrecipes": "bwrecipes",
            "colourprobe": "colourprobe", "selcolour": "selcolour", "fx": "fx", "preview": "preview",
            "adjdiag": "adjdiag", "settingsio": "settingsio", "settingsio-menu": "settingsio_menu",
            "clockkeep": "clockkeep", "battlog": "battlog", "fwtag": "fwtag"}
REGISTRY["tether"] = "tether"   # USB tether
DEFAULT = ["tone-r3", "snap15", "recipe-slots"]
# Koma 0.1 drop 1. `koma build --drop1` keeps the registered features and reports the missing ones.
DROP1 = ["menu5", "snapdist", "fullpress", "fastkeys", "telemetry"]
# Drop 2: drop 1 + exposure tools + highlight alert (recipes join when bwrecipes lands).
DROP2 = DROP1[:-1] + ["autoiso", "longexp", "hlalert", "telemetry"]
# Drop 3 = Koma 0.1 release set: drop 2 + B&W recipes (rows 0x1b8c0..0x1b8ca). tone-r3, recipe-slots and snap15 are
# superseded (bwrecipes recipe 1 is R3; snapdist replaces snap15) and left out. telemetry last (transient init).
DROP3 = KOMA01 = ["menu5", "snapdist", "fullpress", "fastkeys", "autoiso", "longexp", "hlalert", "bwrecipes",
                  "telemetry"]
# Koma 0.2: the 0.1 set + selective colour. bwrecipes (feature name kept for its symbols) now carries B&W, SELECT and
# colour recipes; selcolour is the SELECT look's UV-plane pass (no rows of its own). telemetry last (transient init).
KOMA02 = ["menu5", "snapdist", "fullpress", "fastkeys", "autoiso", "longexp", "hlalert", "bwrecipes", "selcolour",
          "telemetry"]
# Koma 0.3: the 0.2 set + film GRAIN / VIGNETTE (fx: recipe sub-settings; the Y-plane pass on the same master-YUV hook
# as selcolour, chained after it) + the live-view recipe preview (level A by default; preview.curve / preview.warmth
# add levels B / C; preview.live=off or leaving the name out drops it). telemetry last (transient init).
KOMA03 = KOMA02[:-1] + ["fx", "preview", "telemetry"]
# Settings externalization: the 0.3 set + the KOMA
# rows MENU LAYOUT / KOMA FEATURES / SAVE TO CARD + the boot PARAM.DAT import / PARAM.CUR export (transient init, run
# after every other feature init so the export holds their writes). telemetry last.
KOMA03SIO = KOMA03[:-1] + ["settingsio-menu", "settingsio", "telemetry"]
# Boot import/export only (no rows): the 0.3 set + settingsio. The full koma03sio set exhausts the emulated pool at
# install; this one fits.
KOMA03IO = KOMA03[:-1] + ["settingsio", "telemetry"]
# Hardware probe: standalone, not a release set.
PROBE = ["colourprobe", "telemetry"]
# Koma 0.3 + clock keeper: clockkeep's transient init runs before telemetry's, so the telemetry CLOCK line
# (--opt telemetry.clock=on) reports this boot's result.
KOMA03CLOCK = KOMA03[:-1] + ["clockkeep", "telemetry"]
SETS = {"drop1": DROP1, "drop2": DROP2, "drop3": DROP3, "koma01": KOMA01, "koma02": KOMA02, "koma03": KOMA03, "koma03sio": KOMA03SIO, "koma03io": KOMA03IO,
        "koma03clock": KOMA03CLOCK, "probe": PROBE}
REGISTRY["liveview"] = "liveview"; SETS["koma03lv"] = KOMA03[:-1] + ["tether", "liveview", "telemetry"]  # USB live view -LVFR
REGISTRY["ksettings"] = "ksettings"   # USB live look editor -KSET/-KGET
REGISTRY["peaking"] = "peaking"   # focus peaking on the LCD, KOMA row PEAKING 0x1b8bf
# Koma 0.4 set: the 0.3 set + USB tether + live view + live look editor + focus
# peaking + the KOMA mode rows and boot PARAM.DAT import / PARAM.CUR export. settingsio stays after every other feature init; telemetry last.
SETS["koma04"] = KOMA03[:-1] + ["tether", "liveview", "ksettings", "peaking", "settingsio-menu", "settingsio",
                                   "telemetry"]
# waitdiag, the record-entry stall diagnostic (wait ring read through -KSTA; requires
# ksettings). Placed after ksettings and before settingsio-menu so settingsio still inits after every other feature and
# telemetry stays last. Drop it from this list to ship without it.
REGISTRY["waitdiag"] = "waitdiag"
_i = SETS["koma04"].index("settingsio-menu")
SETS["koma04"] = SETS["koma04"][:_i] + ["waitdiag"] + SETS["koma04"][_i:]
# stallfix: runtime-switchable fixes for the second-session tether stall
# (flag byte 0x1b8be, -KSET 2238,<v>); requires tether (fix 4 uses tether's arm_hook). After waitdiag, same reasons.
REGISTRY["stallfix"] = "stallfix"
_i = SETS["koma04"].index("settingsio-menu")
SETS["koma04"] = SETS["koma04"][:_i] + ["stallfix"] + SETS["koma04"][_i:]
# rootlist: the stock reader 0xb7954 bounds a root listing by
# RootEntCnt (0 on FAT32), so `-NLST /EXT0` on a FAT32 card lists nothing and hangs; two BL redirects use the cluster
# bound on FAT32 (FAT12/16 unchanged). No rows, no init. After stallfix, same reasons.
REGISTRY["rootlist"] = "rootlist"
_i = SETS["koma04"].index("settingsio-menu")
SETS["koma04"] = SETS["koma04"][:_i] + ["rootlist"] + SETS["koma04"][_i:]
# clockkeep: restores a lost RTC from /ATA1/KOMATIME.TXT at install. Transient init,
# no resident code. Right before settingsio: after settingsio-menu, so a bundle that inserts widefs + widefs_init before
# settingsio-menu has mounted an SDHC card before clockkeep reads it; settingsio still
# inits after it (its PARAM.CUR export holds clockkeep's writes) and telemetry stays last.
_i = SETS["koma04"].index("settingsio")
SETS["koma04"] = SETS["koma04"][:_i] + ["clockkeep"] + SETS["koma04"][_i:]
# battlog: battery diagnostic log, writes /ATA1/KOMABATT.TXT at power-off. Hooks the
# battery poll read and the power-off path; resident. Before settingsio-menu, same reasons as above.
_i = SETS["koma04"].index("settingsio-menu")
SETS["koma04"] = SETS["koma04"][:_i] + ["battlog"] + SETS["koma04"][_i:]
# fwtag: rewrites the photo MakerNote firmware tag slot 0x112578 at boot with the package's own
# tag (linker.photo_tag(), '240K1.0'), so a later package on the same firmware reports its own version. Transient,
# no resident code. Right before settingsio: settingsio copies the slot into PARAM.CUR's KSIO header at its init.
_i = SETS["koma04"].index("settingsio")
SETS["koma04"] = SETS["koma04"][:_i] + ["fwtag"] + SETS["koma04"][_i:]
# Fallback if koma04 does not fit the pool: koma04 without ksettings and peaking (and waitdiag, which needs ksettings).
SETS["koma04-lite"] = [f for f in SETS["koma04"] if f not in ("ksettings", "peaking", "waitdiag")]
# Per-set resident placement (`koma build --resident` overrides; unlisted sets: pool). koma04 links its resident block
# at a fixed low-RAM address so only the module occupies the KernPool at install.
SET_RESIDENT = {"koma04": "static"}


def module(name: str):
    if name not in REGISTRY:
        raise KeyError(f"unknown feature {name!r}; known: {', '.join(REGISTRY)}")
    return importlib.import_module(f"{__name__}.{REGISTRY[name]}")


def make(name: str, opts: dict | None = None):
    mod = module(name)
    opts = dict(opts or {})
    unknown = set(opts) - set(getattr(mod, "OPTIONS", {}))
    if unknown:
        raise KeyError(f"{name}: unknown option(s) {sorted(unknown)}; known: {sorted(getattr(mod, 'OPTIONS', {}))}")
    f = mod.make(opts)
    rows = getattr(mod, "ROWS", None)
    if rows and not f.rows:
        f.rows = [dict(r) for r in rows]
    return f

# Options every camera build must use (CP15 reads hang the real core).
CAMERA_OPTS = {"telemetry.cp15": "off"}

# KOMA FEATURES = ESSENTIALS (row 0x1b8bc, settingsio-menu): at boot the installer skips
# the inits and records of every feature not listed here (linker.essential_split also keeps what these require or share
# a hook chain with). SDHC is firmware (Koma 2 flash image), not a module feature, so it has no entry. menu5 stays so
# the mode rows are reachable to switch back; settingsio(-menu) and telemetry are the mode/diagnostic plumbing.
# fwtag: the photo tag names the running package in both modes.
ESSENTIAL = ["menu5", "settingsio", "settingsio-menu", "telemetry", "tether", "liveview", "stallfix", "fwtag"]
