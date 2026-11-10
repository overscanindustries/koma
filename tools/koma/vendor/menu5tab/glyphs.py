# Vendored from analysis/menu-5tab/glyphs.py (menu glyph strings). Edits: imports, firmware/data paths, writers removed.
"""English menu strings as firmware glyph strings (font page 1; analysis/menu-redesign/font/README.md).
enc() matches menu-redesign variants.enc. label_table_src() emits a 9-language table (all slots -> one string)."""

P1 = {ch: 0x02 + i for i, ch in enumerate(" !&'()+-./0123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ")}
P1.update({"*": 0x31, "∞": 0x3B})


def enc(s):
    return b"".join(bytes([1, P1[c]]) for c in s) + b"\0\0"


def str_src(name, s):
    return f"{name}:\n    .byte {', '.join(str(b) for b in enc(s))}\n"


def label_table_src(name, s):
    return (str_src(name + "_s", s) + ".p2align 2\n" + f"{name}:\n    .word " + ", ".join(["&" + name + "_s"] * 9) + "\n")
