"""The earlier builders Koma reuses (vendored under koma/vendor, imported lazily so builds that don't need them
never read the firmware at import time)."""
from __future__ import annotations


def samples():
    from ..vendor import samples as s   # tone-r3 table, snap15 patches
    return s


def recipe_build():
    """The recipe-slots builder (vendored)."""
    from ..vendor import recipe_slots_build
    return recipe_slots_build
