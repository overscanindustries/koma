#!/usr/bin/env python3
"""Build the Koma Setup demo fixtures and embed them in ../koma-setup.html.

  demo-main.cur   a real camera block (PARAM.CUR, stock 2.40). Copied from
                  --main if given, else the existing file is reused.
  demo-spare.cur  demo-main.cur with 9 user settings and two camera-specific counters changed,
                  checksum recomputed.

The page embeds ../../settings-map.json (the real map) as its built-in settings list, plus both
blocks, so it works from file:// and ?demo=copy|edit|mode|backup runs end to end.

Checksum (verified on a real block): sum of big-endian u16 halfwords over
0x000..0xffb, stored as a 4-byte big-endian word at 0xffc. Rerun this script whenever
settings-map.json changes so the embedded copy stays current.

It also refreshes the "Set up a card" and phone-app pieces:
  - the expected SHA-256 of the Koma module, read from the module's SHA256SUMS file, embedded
    between the MODULE-EMBED markers (the page prefers the live SHA256SUMS when it can fetch it);
  - the app icon (an SVG plus PNGs drawn here, stdlib only) between the ICON-EMBED markers;
  - ../manifest.webmanifest, with theme colours taken from the page's :root tokens.

Run: python3 tools/komaset/web/testdata/make_demo.py [--main path/to/PARAM.CUR]
"""
import argparse
import base64
import json
import re
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE.parent
MAP = WEB.parent / "settings-map.json"
CS_OFF = 0xFFC
REPO = HERE.parents[3]
MODULE_NAME = "koma04.grmod"
SUMS = REPO / "analysis" / "koma-0.4-candidate" / "SHA256SUMS"

# demo-spare differs from demo-main in these user settings (stored values, from the map's "values" keys).
SPARE_SETTINGS = {"iso": 7, "photometry": 2, "white_balance": 0, "focus": 2, "pic_quality": 0,
                  "bw_contrast": 3, "beep_sound": 1, "adj_btn_set1": 5, "exposure_comp": 11}
# Camera-specific bytes that also differ, so the "kept" list has something to show.
SPARE_RAW = {0x028: "00 00 06 0e", 0x070: "00 65 00 d4"}
# Two edits pre-applied by ?demo=edit.
EDIT_DEMO = [["iso", "6"], ["photometry", "1"]]


def checksum(b):
    return sum(int.from_bytes(b[i:i + 2], "big") for i in range(0, CS_OFF, 2)) & 0xFFFFFFFF


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", help="real PARAM.CUR to use as demo-main.cur")
    a = ap.parse_args()
    main_p = HERE / "demo-main.cur"
    if a.main:
        main_p.write_bytes(Path(a.main).read_bytes())
    main_b = main_p.read_bytes()
    assert len(main_b) == 4096 and main_b[:4] == b"MG1H"
    assert int.from_bytes(main_b[CS_OFF:], "big") == checksum(main_b), "main block checksum mismatch"

    m = json.loads(MAP.read_text())
    by = {f["name"]: f for f in m["fields"]}
    spare = bytearray(main_b)
    for name, v in SPARE_SETTINGS.items():
        f = by[name]
        assert f["type"] == "enum" and f.get("size", 1) == 1 and str(v) in f["values"], name
        off = int(f["offset"], 16)
        assert spare[off] != v, name + " already has that value"
        spare[off] = v
    for off, hx in SPARE_RAW.items():
        bb = bytes.fromhex(hx)
        spare[off:off + len(bb)] = bb
    spare[CS_OFF:] = checksum(spare).to_bytes(4, "big")
    (HERE / "demo-spare.cur").write_bytes(bytes(spare))
    print("wrote demo-spare.cur:", len(SPARE_SETTINGS), "settings changed")

    html_p = WEB / "koma-setup.html"
    html = html_p.read_text()
    blob = ('<script type="application/json" id="demoData">' +
            json.dumps({"map": m, "main": base64.b64encode(main_b).decode(),
                        "spare": base64.b64encode(bytes(spare)).decode(), "editDemo": EDIT_DEMO},
                       separators=(",", ":"), ensure_ascii=True).replace("</", "<\\/") + "</script>")
    pat = re.compile(r"<!-- DEMO-EMBED START -->.*?<!-- DEMO-EMBED END -->", re.S)
    if not pat.search(html):
        raise SystemExit("koma-setup.html has no DEMO-EMBED markers")
    html_p.write_text(pat.sub(lambda _: "<!-- DEMO-EMBED START -->\n" + blob + "\n<!-- DEMO-EMBED END -->", html))
    print("embedded settings-map.json (%d fields) and both blocks into koma-setup.html" % len(m["fields"]))
    embed_module(html_p)
    embed_icons(html_p)


def sub_marker(html, name, body):
    pat = re.compile(r"<!-- %s START -->.*?<!-- %s END -->" % (name, name), re.S)
    if not pat.search(html):
        raise SystemExit("koma-setup.html has no %s markers" % name)
    return pat.sub(lambda _: "<!-- %s START -->\n%s\n<!-- %s END -->" % (name, body, name), html)


def module_sha():
    for line in SUMS.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == MODULE_NAME:
            return parts[0].lower()
    raise SystemExit("%s has no line for %s" % (SUMS, MODULE_NAME))


def embed_module(html_p):
    sha = module_sha()
    blob = ('<script type="application/json" id="moduleData">' +
            json.dumps({"name": MODULE_NAME, "sha256": sha, "from": "analysis/koma-0.4-candidate/SHA256SUMS"},
                       separators=(",", ":")) + "</script>")
    html_p.write_text(sub_marker(html_p.read_text(), "MODULE-EMBED", blob))
    print("embedded the expected module hash", sha)


def tokens(html):
    root = re.search(r":root\{(.*?)\}", html, re.S).group(1)
    return dict(re.findall(r"--([a-z0-9-]+):([^;]+);", root))


def rgb(hx):
    hx = hx.strip().lstrip("#")
    return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))


# The icon: a lens (ring and centre dot) in the page colour on the accent colour. Full-bleed square so the
# platforms can round or mask it (the ring stays inside the maskable safe zone, radius 40%).
RING_R, RING_W, DOT_R = 0.30, 0.07, 0.08


def png(n, bg, fg):
    def cov(sd):  # signed distance in pixels -> coverage
        return max(0.0, min(1.0, 0.5 - sd))
    rows = []
    for y in range(n):
        row = bytearray(b"\x00")
        for x in range(n):
            dx, dy = (x + 0.5) / n - 0.5, (y + 0.5) / n - 0.5
            d = (dx * dx + dy * dy) ** 0.5
            a = max(cov((abs(d - RING_R) - RING_W / 2) * n), cov((d - DOT_R) * n))
            row += bytes(round(b + (f - b) * a) for b, f in zip(bg, fg))
        rows.append(bytes(row))
    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def svg(bg, fg):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="22" '
            'fill="%s"/><circle cx="50" cy="50" r="%g" fill="none" stroke="%s" stroke-width="%g"/>'
            '<circle cx="50" cy="50" r="%g" fill="%s"/></svg>' % (bg, RING_R * 100, fg, RING_W * 100, DOT_R * 100, fg))


def data_uri(mime, b):
    return "data:%s;base64,%s" % (mime, base64.b64encode(b).decode())


def embed_icons(html_p):
    html = html_p.read_text()
    t = tokens(html)
    accent, page = t["accent"].strip(), t["bg"].strip()
    icon_svg = data_uri("image/svg+xml", svg(accent, page).encode())
    p180, p192, p512 = (data_uri("image/png", png(n, rgb(accent), rgb(page))) for n in (180, 192, 512))
    links = ('<link rel="icon" type="image/svg+xml" href="%s">\n<link rel="apple-touch-icon" href="%s">' % (icon_svg, p180))
    html_p.write_text(sub_marker(html, "ICON-EMBED", links))
    manifest = {
        "name": "Koma Setup", "short_name": "Koma Setup", "id": "./", "start_url": "./", "scope": "./",
        "display": "standalone", "theme_color": page, "background_color": page,
        "description": "Set up, change and copy the settings of a Ricoh GR running Koma.",
        "icons": [{"src": icon_svg, "sizes": "any", "type": "image/svg+xml"},
                  {"src": p192, "sizes": "192x192", "type": "image/png", "purpose": "any"},
                  {"src": p512, "sizes": "512x512", "type": "image/png", "purpose": "any"},
                  {"src": p512, "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]}
    (WEB / "manifest.webmanifest").write_text(json.dumps(manifest, indent=1) + "\n")
    print("wrote manifest.webmanifest and embedded the icon (theme %s, accent %s)" % (page, accent))


if __name__ == "__main__":
    main()
