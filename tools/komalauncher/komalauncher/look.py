"""The startup window's colours and measurements, shared by the macOS window (ui_mac.py, AppKit, in points) and the
Windows and Linux window (ui_tray.py, Tk, in 96-dpi pixels times the display's scale), so the two cannot drift.

The window: text on --bg, wordmark, one status line, version bottom-left, one action; sizes sit on a 4 pt grid.

    32  wordmark                                    the dot and the wordmark's K share the left ink edge PAD (32)
    48  status text, Details chevron                the text column's ink edge TEXT_X (48): the stems of the status
                                                    text's first letter and the chevron's point
        version (bottom-left)       [ Try again ]   32 from the right and 32 from the bottom; the version's baseline
                                                    on the button title's
"""
from __future__ import annotations

import math

TOKENS = {  # dark, light
    "bg": ("#161718", "#f4f4f1"),
    "ink": ("#e7e5e0", "#18191b"),
    "sub": ("#a3a5a7", "#4f5054"),
    "faint": ("#85888c", "#67686c"),
    "line": ("#35373b", "#d2d2ce"),
    "raise": ("#25262a", "#ffffff"),
    "ok": ("#6faa6a", "#2f7a3a"),
    "bad": ("#d9674f", "#a83d2a"),
}

W, H = 480, 280                 # the window
PAD = 32                        # left and right inset: the dot's left edge, the wordmark K's left ink
TEXT_X = PAD + 16               # the text column's left ink edge
WORDMARK_SIZE = 15              # Archivo wdth 110, wght 700, 15 px, tracked 0.14 em
WORDMARK_TRACK = 0.14
WORDMARK_BASELINE = 65
STATUS_BASELINE = 145           # the status text's first baseline; 14/20 lines, two at most
LINE = 20
DETAILS_GAP = 26                # the status text's last baseline to the Details link's baseline
DETAILS_LINE = 20               # the Details baseline to its content's baseline
DETAILS_TEXT_X = TEXT_X + 12    # where "Details" and the log path begin, after the chevron
BUTTON_H = 36
BUTTON_MARGIN = 32              # the button's right and bottom margins: equal, the space below never the smaller
BUTTON_TOP = H - BUTTON_MARGIN - BUTTON_H
BUTTON_PAD_X = 16               # title to the button's sides
BUTTON_SIZE, TEXT_SIZE, SMALL_SIZE = 13.5, 14, 12.5   # --fs-btn, --fs-body, --fs-small

# Per em, measured from the Archivo instances (make_assets.py, fontTools): cap height and x-height are the same in all
# three; the left side bearing is that of a stem (K, P, R, D), the letters the column's edge aligns on.
CAP_HEIGHT = 0.686
X_HEIGHT = 0.526
STEM_BEARING = {"Koma Wordmark": 0.082, "Koma Text": 0.086, "Koma Text Medium": 0.081, "DejaVu Sans": 0.098}
OTHER_BEARING = 0.08            # a font not measured here (Segoe UI, the system font when Archivo is missing)
# Koma Text's tabular figures (the default figures since make_assets.py points the digits at the tnum forms): the
# version's first digit is placed by its own bearing, so its ink starts on PAD like the wordmark's K
DIGIT_BEARING = {"0": 0.046, "1": 0.094, "2": 0.049, "3": 0.041, "4": 0.035, "5": 0.044, "6": 0.049, "7": 0.05,
                 "8": 0.047, "9": 0.041}


def bearing(family: str, size: float, first: str = "K") -> float:
    """From where a line starts to its first letter's left ink: a stem's bearing, or a figure's for a digit."""
    if first in DIGIT_BEARING and family == "Koma Text":
        return DIGIT_BEARING[first] * size
    return STEM_BEARING.get(family, OTHER_BEARING) * size


def fit_middle(value: str, measure, width: float) -> str:
    """value, or value cut in the middle with an ellipsis so that it measures at most width (a long log path keeps
    its folder's start and the file's name)."""
    if measure(value) <= width:
        return value
    for keep in range(len(value) - 1, 0, -1):
        candidate = value[: (keep + 1) // 2] + "…" + value[len(value) - keep // 2:]
        if measure(candidate) <= width:
            return candidate
    return "…"

# The chevron (1.5 px, square caps, mitre joins): its points as offsets from the left ink edge, so the
# point of both the closed (>) and the open (v) chevron lands on TEXT_X. A square cap reaches past an end point by
# half the stroke along the line and half across it, at 45 degrees: 0.75 * sqrt(2) in x.
CHEVRON_STROKE = 1.5
_CAP_X = CHEVRON_STROKE / 2 * math.sqrt(2)


def chevron_points(left: float, middle: float, open_: bool, s: float = 1.0) -> list[tuple[float, float]]:
    """The chevron's three points, its leftmost ink at `left`, centred on `middle` (the x-height's centre); s scales."""
    if open_:   # v: 6 wide, 3 high
        x = left + (_CAP_X + 3) * s
        return [(x - 3 * s, middle - 1.5 * s), (x, middle + 1.5 * s), (x + 3 * s, middle - 1.5 * s)]
    x = left + (_CAP_X + 1.5) * s   # >: 3 wide, 6 high
    return [(x - 1.5 * s, middle - 3 * s), (x + 1.5 * s, middle), (x - 1.5 * s, middle + 3 * s)]


def title_baseline(top: float, height: float, size: float) -> float:
    """A button title's baseline: its cap height centred in the button (the version's baseline sits on it too)."""
    return top + height / 2 + CAP_HEIGHT * size / 2
