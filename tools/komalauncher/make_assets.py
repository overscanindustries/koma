#!/usr/bin/env python3
"""Makes the launcher's committed assets from two sources: the Archivo the app already ships
(tools/komaapp/node_modules/@fontsource-variable/archivo, OFL) and the Koma mark (komamark.py).

    cd tools/komaapp && npm ci          # Archivo, once
    python3 tools/komalauncher/make_assets.py [--preview DIR] [--icons-only]

Needs fontTools (with brotli), Pillow, rsvg-convert, and on macOS iconutil. Writes:
  komalauncher/assets/fonts/KomaWordmark.ttf, KomaText.ttf, KomaTextMedium.ttf, OFL.txt
      Archivo instances renamed so a native window can ask for them by name: the wordmark (wdth 110, wght 700),
      body text (wdth 100, wght 400), button text (wdth 100, wght 500). The digits map to Archivo's tabular
      figures (its tnum forms; the default figures are proportional), so the elapsed counter and the version
      are tabular in AppKit and in Tk, which cannot ask for the feature.
  komalauncher/assets/tray-template.png, tray-template@2x.png      macOS menu bar (template: black and alpha)
  komalauncher/assets/tray-light-{32,64}.png, tray-dark-{32,64}.png Windows and Linux trays (light for dark bars)
  komalauncher/assets/icon-256.png                                  the startup window's icon (Tk)
  packaging/deferred/launcher-bundles/{macos/Koma.icns, windows/koma.ico, linux/koma.png, linux/koma.svg}
      (the double-click downloads, parked until after 1.0; see that folder)

Every icon carries the same mark: マ, the second half of コマ, from komamark.py (geometry is not drawn here). The
tray icons are the マ in one colour, no tile, cropped to a 48-unit square so it fills about 80 % of the box. The app
icon is the マ in ink and amber on a dark tile: a squircle for the macOS icns, a rounded square elsewhere.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import komamark   # beside this file

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "komalauncher" / "assets"
PACK = HERE.parent.parent / "packaging" / "deferred" / "launcher-bundles"   # parked until after 1.0
ARCHIVO = HERE.parent / "komaapp" / "node_modules" / "@fontsource-variable" / "archivo"

INK_DARK = "#e7e5e0"     # --ink, dark theme: the マ on the dark tile, the tray マ on a dark bar
INK_LIGHT = "#18191b"    # --ink, light theme: the tray マ on a light bar
TILE = "#161718"         # --bg, dark theme
TILE_EDGE = "#35373b"    # --line, dark theme: the tile's edge on a dark Dock

def squircle(size: float, inset: float) -> str:
    """The macOS icon body (824 of 1024, corner radius 185.4 on Apple's grid) with continuous corners: each corner is
    three cubic curves spanning 1.5287 radii along both edges (the constants of UIKit's continuous rounded rect)."""
    a, b = inset, size - inset
    r = (b - a) * 185.4 / 824

    def corner(cx, cy, sx, sy, rev):
        """One corner, walked clockwise: (cx, cy) the box corner, (sx, sy) the directions into the box, u along x and
        v along y in radii. The top-right and bottom-left corners run from the horizontal edge to the vertical one,
        the other two the other way round (rev)."""
        k = [(1.52866483, 0.0), (1.08849296, 0.0), (0.86840694, 0.0), (0.63149399, 0.07491100),
             (0.37282383, 0.16905899), (0.16905899, 0.37282383), (0.07491100, 0.63149399), (0.0, 0.86840694),
             (0.0, 1.08849296), (0.0, 1.52866483)]
        return [(cx + sx * u * r, cy + sy * v * r) for u, v in (k[::-1] if rev else k)]

    segs = []
    for cx, cy, sx, sy, rev in ((b, a, -1, 1, False), (b, b, -1, -1, True), (a, b, 1, -1, False), (a, a, 1, 1, True)):
        segs.append(corner(cx, cy, sx, sy, rev))
    d = f"M{segs[0][0][0]:.2f} {segs[0][0][1]:.2f}"
    for i, pts in enumerate(segs):
        if i:
            d += f" L{pts[0][0]:.2f} {pts[0][1]:.2f}"
        for j in range(1, 10, 3):
            (x1, y1), (x2, y2), (x3, y3) = pts[j], pts[j + 1], pts[j + 2]
            d += f" C{x1:.2f} {y1:.2f} {x2:.2f} {y2:.2f} {x3:.2f} {y3:.2f}"
    return d + " Z"


def rounded_square(size: float, inset: float, radius: float) -> str:
    a, b, r = inset, size - inset, radius
    return (f"M{a + r} {a} H{b - r} A{r} {r} 0 0 1 {b} {a + r} V{b - r} A{r} {r} 0 0 1 {b - r} {b} "
            f"H{a + r} A{r} {r} 0 0 1 {a} {b - r} V{a + r} A{r} {r} 0 0 1 {a + r} {a} Z")


def svg(size: int, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
            f'viewBox="0 0 {size} {size}">{body}</svg>')


# -------------------------------------------------------------------------------------------------- icons

MA_CROP = "8 11 48 48"   # komamark's マ spans x 10-54, y 14-52 of 64: this crop leaves it about 80 % of the box
MA_CENTRE = (32, 33)     # the centre of that span


def ma_mono(colour: str) -> str:
    ink, tick = komamark.paths("ma")
    return f'<path fill="{colour}" d="{ink}"/><path fill="{colour}" d="{tick}"/>'


def tray_svg(px: int, colour: str) -> str:
    """The tray マ in one colour, no tile, cropped to MA_CROP, px square."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{px}" height="{px}" viewBox="{MA_CROP}">'
            f'{ma_mono(colour)}</svg>')



# app icon: tile shape and mark size per pixel size
def app_icon_svg(px: int, shape: str = "mac") -> str:
    if shape == "mac":
        inset = px * 100 / 1024
        if px <= 16:
            inset = 1
        elif px <= 32:
            inset = px * 2 / 32
        body = squircle(px, inset)
    else:
        inset = 0 if px <= 32 else px * 0.04
        body = rounded_square(px, inset, (px - 2 * inset) * 0.2)
    tile_size = px - 2 * inset
    # optical sizes: the マ is 44 units wide; small icons let it fill more of the tile so it holds
    width = tile_size * (0.66 if px <= 32 else 0.58 if px <= 64 else 0.52)
    edge = max(1.0, px / 512)
    shadow = ""
    if shape == "mac" and px >= 128:
        blur, dy = px * 10 / 1024, px * 10 / 1024
        shadow = (f'<defs><filter id="s" x="-20%" y="-20%" width="140%" height="140%">'
                  f'<feGaussianBlur stdDeviation="{blur:.2f}"/></filter></defs>'
                  f'<path d="{body}" fill="#000" opacity=".28" transform="translate(0 {dy:.2f})" filter="url(#s)"/>')
    tile = f'<path d="{body}" fill="{TILE}"/>'
    rim = f'<path d="{body}" fill="none" stroke="{TILE_EDGE}" stroke-width="{edge:.2f}"/>' if px >= 32 else ""
    ink, tick = komamark.paths("ma")
    k = (f'<g transform="translate({px / 2:.3f} {px / 2:.3f}) scale({width / 44:.5f}) '
         f'translate(-{MA_CENTRE[0]} -{MA_CENTRE[1]})"><path fill="{INK_DARK}" d="{ink}"/>'
         f'<path fill="{komamark.ACC}" d="{tick}"/></g>')
    return svg(px, shadow + tile + rim + k)


def render(svg_text: str, out: Path, px: int):
    if not shutil.which("rsvg-convert"):
        raise SystemExit("rsvg-convert is needed (brew install librsvg)")
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsvg-convert", "-w", str(px), "-h", str(px), "-o", str(out)], input=svg_text.encode(),
                   check=True)


def template_png(svg_text: str, out: Path, px: int):
    """A macOS template image: the マ in black, coverage in alpha."""
    render(svg_text, out, px)
    from PIL import Image
    im = Image.open(out).convert("RGBA")
    alpha = im.getchannel("A")
    black = Image.new("RGBA", im.size, (0, 0, 0, 0))
    black.putalpha(alpha)
    black.save(out, optimize=True)


def make_icons(preview: Path | None):
    ASSETS.mkdir(parents=True, exist_ok=True)
    template_png(tray_svg(20, "#000"), ASSETS / "tray-template.png", 20)
    template_png(tray_svg(40, "#000"), ASSETS / "tray-template@2x.png", 40)
    for px in (32, 64):
        render(tray_svg(px, INK_DARK), ASSETS / f"tray-light-{px}.png", px)
        render(tray_svg(px, INK_LIGHT), ASSETS / f"tray-dark-{px}.png", px)
    render(app_icon_svg(256, "square"), ASSETS / "icon-256.png", 256)

    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Koma.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = base * scale
                name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
                render(app_icon_svg(px, "mac"), iconset / name, px)
        if shutil.which("iconutil"):
            (PACK / "macos").mkdir(parents=True, exist_ok=True)
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(PACK / "macos" / "Koma.icns")],
                           check=True)
        else:
            print("iconutil missing (not macOS): packaging/macos/Koma.icns not rebuilt", file=sys.stderr)
        if preview:
            for name in ("icon_512x512@2x.png", "icon_128x128.png", "icon_32x32.png", "icon_16x16.png"):
                shutil.copy(iconset / name, preview / f"app-icon-{name.replace('icon_', '')}")

        sizes = (16, 20, 24, 32, 40, 48, 64, 256)
        images = []
        for px in sizes:
            out = Path(tmp) / f"win-{px}.png"
            render(app_icon_svg(px, "square"), out, px)
            images.append(Image.open(out).convert("RGBA"))
        (PACK / "windows").mkdir(parents=True, exist_ok=True)
        images[-1].save(PACK / "windows" / "koma.ico", format="ICO", sizes=[(s, s) for s in sizes],
                        append_images=images[:-1])
    (PACK / "linux").mkdir(parents=True, exist_ok=True)
    render(app_icon_svg(512, "square"), PACK / "linux" / "koma.png", 512)
    (PACK / "linux" / "koma.svg").write_text(app_icon_svg(512, "square") + "\n", encoding="utf-8")
    if preview:
        _previews(preview)


def _previews(out: Path):
    """The tray マ on a dark and a light menu bar strip at 2x for review: on macOS beside the menu bar's own
    neighbours (SF Symbols wifi, battery, magnifying glass at the bar's size, and the clock in the menu bar font),
    drawn offscreen by AppKit; elsewhere beside Koma Text."""
    out.mkdir(parents=True, exist_ok=True)
    for name, dark in (("dark", True), ("light", False)):
        for dimmed in (False, True):   # dimmed: while Koma is not running (NSStatusBarButton appearsDisabled)
            path = out / f"tray-menubar-{name}{'-dimmed' if dimmed else ''}@2x.png"
            try:
                _menubar_appkit(path, dark, dimmed)
            except ImportError:
                _menubar_pil(path, dark, dimmed)
    for name in ("tray-light-64.png", "tray-dark-64.png"):
        shutil.copy(ASSETS / name, out / f"tray-{name.split('-')[1]}-64.png")


def _menubar_appkit(path: Path, dark: bool, dimmed: bool = False):
    from AppKit import (NSBezierPath, NSBitmapImageFileTypePNG, NSBitmapImageRep, NSColor,
                        NSCompositingOperationSourceAtop, NSCompositingOperationSourceOver, NSDeviceRGBColorSpace,
                        NSFont, NSFontAttributeName,
                        NSFontWeightRegular, NSForegroundColorAttributeName, NSGraphicsContext, NSImage,
                        NSImageSymbolConfiguration, NSRectFillUsingOperation)
    from Foundation import NSAttributedString, NSMakeRect
    width, height = 320, 24
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
        None, width * 2, height * 2, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
    rep.setSize_((width, height))
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    bar = (0.12, 0.12, 0.13) if dark else (0.93, 0.93, 0.93)
    ink = NSColor.colorWithSRGBRed_green_blue_alpha_(*((0.92,) * 3 if dark else (0.08,) * 3), 1)
    NSColor.colorWithSRGBRed_green_blue_alpha_(*bar, 1).setFill()
    NSBezierPath.fillRect_(NSMakeRect(0, 0, width, height))

    def tinted(image, size):
        canvas = NSImage.alloc().initWithSize_(size)
        canvas.lockFocus()
        image.drawInRect_(NSMakeRect(0, 0, *size))
        ink.set()
        NSRectFillUsingOperation(NSMakeRect(0, 0, *size), NSCompositingOperationSourceAtop)
        canvas.unlockFocus()
        return canvas

    k = NSImage.alloc().initWithContentsOfFile_(str(ASSETS / "tray-template@2x.png"))
    k.setSize_((20, 20))
    x = 12
    tinted(k, (20, 20)).drawInRect_fromRect_operation_fraction_(NSMakeRect(x, 2, 20, 20), NSMakeRect(0, 0, 0, 0),
                                                                 NSCompositingOperationSourceOver,
                                                                 0.5 if dimmed else 1.0)
    x += 20 + 16
    config = NSImageSymbolConfiguration.configurationWithPointSize_weight_(15, NSFontWeightRegular)
    for symbol in ("wifi", "battery.75percent", "magnifyingglass"):
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, None)
        if image is None:
            continue
        image = image.imageWithSymbolConfiguration_(config)
        size = image.size()
        tinted(image, (size.width, size.height)).drawInRect_(
            NSMakeRect(x, (height - size.height) / 2, size.width, size.height))
        x += size.width + 16
    clock = NSAttributedString.alloc().initWithString_attributes_(
        "Sun 8 Nov  15:42", {NSFontAttributeName: NSFont.menuBarFontOfSize_(0), NSForegroundColorAttributeName: ink})
    clock.drawAtPoint_((x, (height - clock.size().height) / 2))
    NSGraphicsContext.restoreGraphicsState()
    rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(str(path), True)


def _menubar_pil(path: Path, dark: bool, dimmed: bool = False):
    from PIL import Image, ImageDraw, ImageFont
    k = Image.open(ASSETS / "tray-template@2x.png").getchannel("A")
    if dimmed:
        k = k.point(lambda a: a // 2)
    bar, ink = ((30, 30, 32), (235, 235, 235)) if dark else ((236, 236, 236), (20, 20, 20))
    strip = Image.new("RGB", (360, 48), bar)
    strip.paste(Image.new("RGB", k.size, ink), (24, 4), k)
    try:
        font = ImageFont.truetype(str(ASSETS / "fonts" / "KomaText.ttf"), 26)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(strip).text((84, 10), "Sun 8 Nov  15:42", fill=ink, font=font)
    strip.save(path)


# -------------------------------------------------------------------------------------------------- fonts

def tabular_figures(font) -> int:
    """Points the cmap's digits at the font's own tabular forms (the tnum feature's single substitutions); returns how
    many were remapped."""
    gsub = font["GSUB"].table
    mapping: dict = {}
    for record in gsub.FeatureList.FeatureRecord:
        if record.FeatureTag != "tnum":
            continue
        for index in record.Feature.LookupListIndex:
            for table in gsub.LookupList.Lookup[index].SubTable:
                table = getattr(table, "ExtSubTable", table)
                mapping.update(getattr(table, "mapping", None) or {})
    count = 0
    for table in font["cmap"].tables:
        for code in range(0x30, 0x3A):
            name = table.cmap.get(code)
            if name in mapping:
                table.cmap[code] = mapping[name]
                count += 1
    return count


def make_fonts():
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    src = ARCHIVO / "files" / "archivo-latin-wdth-normal.woff2"
    if not src.is_file():
        raise SystemExit(f"{src} is missing: run `npm ci` in tools/komaapp first")
    out = ASSETS / "fonts"
    out.mkdir(parents=True, exist_ok=True)
    text = "".join(chr(c) for c in range(0x20, 0x7f)) + " · ’‘“”…−–"
    for family, ps, wdth, wght, style in (("Koma Wordmark", "KomaWordmark", 110, 700, "Bold"),
                                           ("Koma Text", "KomaText", 100, 400, "Regular"),
                                           ("Koma Text Medium", "KomaTextMedium", 100, 500, "Regular")):
        font = instantiateVariableFont(TTFont(str(src)), {"wdth": wdth, "wght": wght})
        font.flavor = None
        names = font["name"]
        for rec in list(names.names):
            if rec.nameID in (1, 2, 3, 4, 6, 16, 17, 21, 22, 25):
                names.removeNames(nameID=rec.nameID)
        for nid, value in ((1, family), (2, style), (3, f"{ps};Archivo instance"), (4, f"{family} {style}".strip()),
                           (6, ps)):
            names.setName(value, nid, 3, 1, 0x409)
            names.setName(value, nid, 1, 0, 0)
        options = subset.Options()
        options.layout_features = ["kern", "liga", "ccmp", "locl", "mark", "mkmk", "tnum"]
        options.name_IDs = ["*"]
        options.notdef_outline = True
        sub = subset.Subsetter(options)
        sub.populate(text=text)
        sub.subset(font)
        tabular_figures(font)
        font.save(str(out / f"{ps}.ttf"))
    license_text = (ARCHIVO / "LICENSE").read_text(encoding="utf-8")
    (out / "OFL.txt").write_text("Koma Wordmark, Koma Text and Koma Text Medium are instances of Archivo, renamed.\n\n"
                                 + license_text, encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preview", help="also write review images (app icon sizes, tray マ on menu bars) here")
    ap.add_argument("--icons-only", action="store_true")
    a = ap.parse_args(argv)
    if not a.icons_only:
        make_fonts()
    make_icons(Path(a.preview) if a.preview else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
