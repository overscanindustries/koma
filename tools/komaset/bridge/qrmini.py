"""qrmini: a minimal QR code encoder (stdlib only) for komabridge --lan, so a phone can open the bridge's URL by camera.

Byte mode, error correction level M, versions 1..6 (up to 106 bytes; a LAN URL with its token is about 60), mask 0
(any mask is valid; the penalty-based choice only helps marginal scanners). After ISO/IEC 18004 and Project Nayuki's
reference implementation (MIT), cut down to what this needs. test_komabridge.py decodes the output with OpenCV where it
is installed.
"""
from __future__ import annotations

# Level M, versions 1..6: (EC codewords per block, blocks, data codewords per block). Equal-size blocks up to version 6.
_M_BLOCKS = {1: (10, 1, 16), 2: (16, 1, 28), 3: (26, 1, 44), 4: (18, 2, 32), 5: (24, 2, 43), 6: (16, 4, 27)}
_FORMAT_M = 0  # format-info bits of level M (L 1, M 0, Q 3, H 2)


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list:
    result, root = [0] * (degree - 1) + [1], 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 2)
    return result


def _rs_remainder(data: list, divisor: list) -> list:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _codewords(text: bytes) -> tuple[int, list]:
    for version, (ec, blocks, per) in _M_BLOCKS.items():
        capacity = blocks * per
        if 4 + 8 + 8 * len(text) <= capacity * 8:
            break
    else:
        raise ValueError(f"{len(text)} bytes is too long for a version 1..6 QR code")
    bits = [0, 1, 0, 0] + [(len(text) >> i) & 1 for i in range(7, -1, -1)]
    for b in text:
        bits += [(b >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, capacity * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(data) < capacity:
        data.append(pad)
        pad ^= 0xEC ^ 0x11
    div = _rs_divisor(ec)
    chunks = [data[i * per:(i + 1) * per] for i in range(blocks)]
    eccs = [_rs_remainder(c, div) for c in chunks]
    out = [c[i] for i in range(per) for c in chunks] + [e[i] for i in range(ec) for e in eccs]
    return version, out


def matrix(text: str) -> list:
    """The QR symbol as rows of booleans (True = dark), without the quiet zone."""
    version, words = _codewords(text.encode("utf-8"))
    size = 17 + 4 * version
    dark = [[False] * size for _ in range(size)]
    fixed = [[False] * size for _ in range(size)]

    def put(x, y, on):
        dark[y][x], fixed[y][x] = on, True

    for i in range(size):  # timing patterns
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):  # finders with their separators
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    put(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    if version >= 2:  # one alignment pattern up to version 6
        c = size - 7
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                put(c + dx, c + dy, max(abs(dx), abs(dy)) != 1)
    mask = 0
    data = _FORMAT_M << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    fmt = (data << 10 | rem) ^ 0x5412
    bit = lambda i: (fmt >> i) & 1 == 1  # noqa: E731
    for i in range(6):
        put(8, i, bit(i))
    put(8, 7, bit(6))
    put(8, 8, bit(7))
    put(7, 8, bit(8))
    for i in range(9, 15):
        put(14 - i, 8, bit(i))
    for i in range(8):
        put(size - 1 - i, 8, bit(i))
    for i in range(8, 15):
        put(8, size - 15 + i, bit(i))
    put(8, size - 8, True)  # the dark module
    i, right = 0, size - 1
    while right >= 1:  # zigzag data placement, two columns at a time, skipping the vertical timing column
        if right == 6:
            right = 5
        for vert in range(size):
            for j in (0, 1):
                x = right - j
                y = size - 1 - vert if ((right + 1) & 2) == 0 else vert
                if not fixed[y][x]:
                    if i < len(words) * 8:
                        dark[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
                    if (x + y) % 2 == 0:  # mask 0
                        dark[y][x] = not dark[y][x]
        right -= 2
    return dark


def terminal(text: str, quiet: int = 2) -> str:
    """Two module rows per text line (upper half block, ANSI black/white), readable on light or dark terminals."""
    m = matrix(text)
    n = len(m) + 2 * quiet
    grid = [[False] * n for _ in range(quiet)] + [[False] * quiet + r + [False] * quiet for r in m] + \
           [[False] * n for _ in range(quiet + 1)]
    lines = []
    for y in range(0, n, 2):
        row = ""
        for x in range(n):
            top, bottom = grid[y][x], grid[y + 1][x]
            row += f"\x1b[{30 if top else 97}m\x1b[{40 if bottom else 107}m▀"
        lines.append(row + "\x1b[0m")
    return "\n".join(lines)
