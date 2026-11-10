"""Build backend for the `koma-gr` Python package (PEP 517; pyproject.toml names it with backend-path).

    python3 -m pip wheel . --no-deps -w dist        or        python3 -m build
    python3 packaging/koma_build.py                 the wheel and the sdist into dist/, no pip needed

It needs nothing but the standard library. The wheel holds one top-level package, koma_gr/, and under it the files the
launcher, the bridge and the app use, at their repository paths (koma_gr/tools/komaset/bridge/komabridge.py, ...):
every module finds the others from its own location (tools/komalauncher/komalauncher/paths.py,
tools/komaset/bridge/komabridge.py, komainstall.py), so an installed copy and a checkout share one path logic. The
`koma` command is koma_gr.cli:main.

The Koma package the app writes to the card comes from wherever tools/komaset/bridge/komainstall.py says it is: this
backend loads that module and takes every file its module-level paths name inside the repository (PACKAGE_DELTA, and
any other file constant added beside it), refusing to build when the package delta's SHA-256 is not komainstall's
PACKAGE_DELTA_SHA256. So the release artifacts get into the wheel by pointing komainstall at them. Ricoh's own files are
never taken (Koma ships no Ricoh code): anything under firmware/, named like an update file or like a firmware image
built from one, is skipped.

Run it on Python 3.10 or newer. The build is reproducible: fixed timestamps, sorted entries.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import os
import re
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "koma-gr"
DIST = "koma_gr"
PACKAGE_DIR = "koma_gr"
REQUIRES_PYTHON = ">=3.10"
SUMMARY = "Koma for the 2005 Ricoh GR DIGITAL: 21 looks, camera settings, photo import, tethered shooting and webcam over USB"
HOMEPAGE = "https://github.com/overscanindustries/koma"
EPOCH = 1762646400  # 2025-11-09 00:00 UTC; every entry carries it so two builds of one tree are identical
DEPENDENCIES = [
    "pyusb>=1.2",
    "libusb-package>=1.0.26; sys_platform == 'win32'",  # Windows has no system libusb; this wheel carries the DLL
]
EXTRAS = {
    "tray": ["pyobjc-framework-Cocoa>=10; sys_platform == 'darwin'",
             "pystray>=0.19; sys_platform != 'darwin'", "Pillow>=10; sys_platform != 'darwin'"],
    "webcam": ["pyvirtualcam>=0.11", "numpy>=1.24", "Pillow>=10"],
}

# (glob relative to the repository root, required). Tests stay out (test_*.py), and so does anything only the
# repository's research uses.
FILES = [
    ("tools/komalauncher/komalauncher/*.py", True),
    ("tools/komalauncher/komalauncher/assets/*.png", True),
    ("tools/komalauncher/komalauncher/assets/*.rules", True),        # Linux: koma --install-udev-rule
    ("tools/komalauncher/komalauncher/assets/fonts/*", True),
    ("tools/komaset/bridge/*.py", True),
    ("tools/komaset/bridge/testdata/osd-*.bin", True),               # the simulated camera (--mock)
    ("tools/komaset/bridge/testdata/osd-pages.json", True),
    ("tools/komaset/komaset.py", True),
    ("tools/komaset/verify_session.py", True),                       # the bridge's selftest reads KOMALOG with it
    ("tools/komaset/settings-map.json", True),
    # tools/komaset/web/koma-setup.html (/legacy, the pre-1.0 page that installs the 0.4 candidate) stays out: Koma 1.0's
    # app replaces it, and an installed bridge answers /legacy with 404 like any other missing file.
    ("tools/komaset/web/sw.js", True),
    ("tools/komaset/web/manifest.webmanifest", True),
    ("tools/komaset/web/testdata/demo-main.cur", True),              # the simulated camera's PARAM.CUR (--mock)
    ("tools/grdusb/*.py", True),
    ("tools/komacam/komacam.py", True),                              # the webcam (webcamhost runs it)
    ("tools/koma/__init__.py", True),
    ("tools/koma/loader.py", True),                                  # komainstall builds the card's firmware with it
    ("tools/komaapp/dist/**/*", True),                               # the app (`npm run build` in tools/komaapp)
]
SKIP_PARTS = {"__pycache__", ".DS_Store"}
# Ricoh's update files, and firmware images built from them (they hold Ricoh's code): never in a Koma download. The app
# builds the Koma firmware on the user's computer from the user's own copy of Ricoh's file (komainstall.firmware_image).
RICOH = re.compile(r"^(qrflect\d*|gr-\d+\.exe|.*firmware.*\.bin|.*\.type\d\.bin)$", re.I)
SDIST_EXTRA = ["pyproject.toml", "packaging/koma_build.py", "LICENSE", "README.md"]  # README.md: the long description


class BuildError(SystemExit):
    pass


# ------------------------------------------------------------------------------------------------ what goes in

def version() -> str:
    """komalauncher.VERSION, the one place the launcher's version is set (read, not imported)."""
    text = (ROOT / "tools/komalauncher/komalauncher/__init__.py").read_text(encoding="utf-8")
    found = re.search(r'^VERSION:[^=]*=\s*"([0-9][0-9A-Za-z.+-]*)"', text, re.M)
    if not found:
        raise BuildError("koma_build: komalauncher.VERSION is not set")
    return found.group(1)


def _skip(path: Path) -> bool:
    return bool(SKIP_PARTS & set(path.parts)) or path.name.startswith("test_") or path.name.startswith(".")


def install_files(root: Path = ROOT) -> list[Path]:
    """The files komainstall reads for the card: its module-level Paths that are files inside the repository, outside
    firmware/ and not named like a Ricoh file or a firmware image (RICOH)."""
    spec = importlib.util.spec_from_file_location("_koma_build_komainstall", root / "tools/komaset/bridge/komainstall.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo = Path(getattr(module, "REPO")).resolve()
    out = []
    for name, value in sorted(vars(module).items()):
        if not isinstance(value, Path) or name in ("HERE", "TOOLS", "REPO"):
            continue
        path = value.resolve()
        try:
            rel = path.relative_to(repo)
        except ValueError:
            continue
        if rel.parts[:1] == ("firmware",) or RICOH.match(path.name) or not path.is_file():
            continue
        out.append(root / rel)
    package = Path(getattr(module, "PACKAGE_DELTA")).resolve()
    if not package.is_file():
        raise BuildError(f"koma_build: komainstall.PACKAGE_DELTA is not there: {package}")
    want = getattr(module, "PACKAGE_DELTA_SHA256", None)
    got = hashlib.sha256(package.read_bytes()).hexdigest()
    if want and got != want:
        raise BuildError(f"koma_build: {package.name} is not komainstall.PACKAGE_DELTA_SHA256 ({got} != {want}); the "
                         "bridge would refuse to build the package from it")
    return out


def files(root: Path = ROOT) -> list[Path]:
    found: dict[str, Path] = {}
    for pattern, required in FILES:
        matches = [p for p in sorted(root.glob(pattern)) if p.is_file() and not _skip(p.relative_to(root))]
        if required and not matches:
            hint = " (build the app: cd tools/komaapp && npm ci && npm run build)" if "komaapp" in pattern else ""
            raise BuildError(f"koma_build: nothing matches {pattern}{hint}")
        for p in matches:
            found[p.relative_to(root).as_posix()] = p
    for p in install_files(root):
        found[p.relative_to(root).as_posix()] = p
    return [found[k] for k in sorted(found)]


# ------------------------------------------------------------------------------------------------ metadata

RAW_BASE = "https://raw.githubusercontent.com/overscanindustries/koma/main/"
BLOB_BASE = HOMEPAGE + "/blob/main/"
PROJECT_URLS = [
    ("Homepage", HOMEPAGE),
    ("Source", HOMEPAGE),
    ("Issues", HOMEPAGE + "/issues"),
    ("Changelog", BLOB_BASE + "CHANGELOG.md"),
    ("Documentation", HOMEPAGE + "/tree/main/docs"),
]
CLASSIFIERS = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Environment :: Web Environment",
    "Environment :: MacOS X",
    "Intended Audience :: End Users/Desktop",
    "Operating System :: MacOS",
    "Operating System :: Microsoft :: Windows",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Multimedia :: Graphics :: Capture :: Digital Camera",
    # No "License ::" classifier: Metadata-Version 2.4 states the licence as License-Expression (PEP 639), and PyPI
    # rejects an upload that carries both.
]
LICENSE_FILES = ["LICENSE"]


def metadata(ver: str, readme: str | None = None) -> str:
    """Core metadata 2.4 (PKG-INFO / METADATA). The long description is README.md with its relative links and images
    made absolute (absolute_links), so PyPI shows the same page GitHub does."""
    lines = ["Metadata-Version: 2.4", f"Name: {NAME}", f"Version: {ver}", f"Summary: {SUMMARY}",
             "License-Expression: MIT"]
    lines += [f"License-File: {name}" for name in LICENSE_FILES]
    lines.append(f"Requires-Python: {REQUIRES_PYTHON}")
    lines += [f"Project-URL: {label}, {url}" for label, url in PROJECT_URLS]
    lines += [f"Classifier: {c}" for c in CLASSIFIERS]
    lines += [f"Requires-Dist: {d}" for d in DEPENDENCIES]
    for extra, deps in EXTRAS.items():
        lines.append(f"Provides-Extra: {extra}")
        for dep in deps:
            name, _, marker = dep.partition(";")
            marker = f"({marker.strip()}) and extra == '{extra}'" if marker else f"extra == '{extra}'"
            lines.append(f"Requires-Dist: {name.strip()}; {marker}")
    lines.append("Description-Content-Type: text/markdown")
    if readme is None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
    return "\n".join(lines) + "\n\n" + absolute_links(readme)


# ------------------------------------------------------------------------------------------------ README for PyPI

_IMAGE_EXT = re.compile(r"\.(png|jpe?g|gif|svg|webp|avif)$", re.I)
_MD_LINK = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*(<[^>]*>|[^\s)]+)((?:\s+\"[^\"]*\")?\s*)\)")
_MD_REF = re.compile(r"^(\s{0,3}\[[^\]]+\]:\s*)(<[^>]*>|\S+)(.*)$")
_HTML_ATTR = re.compile(r"""(\b(?:src|href|srcset)\s*=\s*)(["'])(.*?)\2""", re.I)
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def _relative(target: str) -> bool:
    return bool(target) and not re.match(r"^([a-z][a-z0-9+.-]*:|//|#|/)", target, re.I)


def _absolute(target: str, image: bool) -> str:
    if not _relative(target):
        return target
    path = target[2:] if target.startswith("./") else target
    if image or _IMAGE_EXT.search(path.split("#", 1)[0].split("?", 1)[0]):
        return RAW_BASE + path
    return BLOB_BASE + path


def _srcset(value: str) -> str:
    out = []
    for candidate in value.split(","):
        parts = candidate.strip().split(None, 1)
        if parts:
            out.append(" ".join([_absolute(parts[0], True)] + parts[1:]))
    return ", ".join(out)


def _html(match: re.Match) -> str:
    attr, quote, value = match.group(1), match.group(2), match.group(3)
    name = attr.split("=")[0].strip().lower()
    if name == "srcset":
        value = _srcset(value)
    else:
        value = _absolute(value, image=(name == "src"))
    return f"{attr}{quote}{value}{quote}"


def _md(match: re.Match) -> str:
    bang, text, target, title = match.groups()
    bracketed = target.startswith("<") and target.endswith(">")
    inner = target[1:-1] if bracketed else target
    inner = _absolute(inner, image=bool(bang))
    return f"{bang}[{text}]({'<' + inner + '>' if bracketed else inner}{title})"


def absolute_links(markdown: str) -> str:
    """README.md for PyPI: every relative Markdown link, reference definition and HTML src/href/srcset made absolute
    on GitHub: images (and links to image files) to RAW_BASE, everything else to BLOB_BASE. Absolute URLs, anchors
    (#...) and fenced code blocks are left alone."""
    out, fence = [], None
    for line in markdown.splitlines(keepends=True):
        opened = _FENCE.match(line)
        if fence:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence):
                fence = None
            out.append(line)
            continue
        if opened:
            fence = opened.group(1)
            out.append(line)
            continue
        ref = _MD_REF.match(line.rstrip("\n"))
        if ref and not line.lstrip().startswith("!["):
            target = ref.group(2)
            bracketed = target.startswith("<") and target.endswith(">")
            inner = _absolute(target[1:-1] if bracketed else target, image=False)
            line = ref.group(1) + ("<" + inner + ">" if bracketed else inner) + ref.group(3) + ("\n" if line.endswith("\n") else "")
        else:
            line = _MD_LINK.sub(_md, line)
            line = _HTML_ATTR.sub(_html, line)
        out.append(line)
    return "".join(out)


CLI = '''"""The `koma` command: the Koma launcher (tools/komalauncher), run from this package's copy of the repository
layout (packaging/koma_build.py)."""
import sys
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parent / "tools" / "komalauncher"


def main():
    if str(LAUNCHER) not in sys.path:
        sys.path.insert(0, str(LAUNCHER))
    from komalauncher.__main__ import main as launcher_main
    return launcher_main()


if __name__ == "__main__":
    sys.exit(main())
'''

INIT = '''"""Koma for the Ricoh GR DIGITAL: the launcher, the USB bridge and the app, at their repository paths under this
package. The command is `koma` (koma_gr.cli)."""
'''


# ------------------------------------------------------------------------------------------------ archives

def _zinfo(name: str, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2025, 11, 9, 0, 0, 0))
    info.external_attr = (0o100755 if executable else 0o100644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None) -> str:
    ver = version()
    name = f"{DIST}-{ver}-py3-none-any.whl"
    info_dir = f"{DIST}-{ver}.dist-info"
    entries: list[tuple[str, bytes]] = [(f"{PACKAGE_DIR}/__init__.py", INIT.encode()),
                                        (f"{PACKAGE_DIR}/cli.py", CLI.encode())]
    for path in files():
        entries.append((f"{PACKAGE_DIR}/{path.relative_to(ROOT).as_posix()}", path.read_bytes()))
    entries += [
        (f"{info_dir}/METADATA", metadata(ver).encode()),
        (f"{info_dir}/WHEEL", b"Wheel-Version: 1.0\nGenerator: koma_build\nRoot-Is-Purelib: true\nTag: py3-none-any\n"),
        (f"{info_dir}/entry_points.txt", b"[console_scripts]\nkoma = koma_gr.cli:main\n"),
        (f"{info_dir}/top_level.txt", b"koma_gr\n"),
    ]
    entries += [(f"{info_dir}/licenses/{name}", (ROOT / name).read_bytes()) for name in LICENSE_FILES]
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for arc, data in entries:
        writer.writerow([arc, _record_hash(data), len(data)])
    writer.writerow([f"{info_dir}/RECORD", "", ""])
    entries.append((f"{info_dir}/RECORD", record.getvalue().encode()))
    out = Path(wheel_directory) / name
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w") as zf:
        for arc, data in entries:
            zf.writestr(_zinfo(arc), data)
    return name


def build_sdist(sdist_directory, config_settings=None) -> str:
    ver = version()
    base = f"{DIST}-{ver}"
    paths = sorted({p.relative_to(ROOT).as_posix() for p in files()} | set(SDIST_EXTRA))
    out = Path(sdist_directory) / f"{base}.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    pkg_info = metadata(ver).encode()

    def add(tf, arc, data, mode=0o644):
        info = tarfile.TarInfo(f"{base}/{arc}")
        info.size, info.mtime, info.mode = len(data), EPOCH, mode
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        tf.addfile(info, io.BytesIO(data))

    import gzip
    with open(out, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=EPOCH, filename="") as gz, \
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tf:
        add(tf, "PKG-INFO", pkg_info)
        for arc in paths:
            add(tf, arc, (ROOT / arc).read_bytes())
    return out.name


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None) -> str:
    ver = version()
    info_dir = Path(metadata_directory) / f"{DIST}-{ver}.dist-info"
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / "METADATA").write_text(metadata(ver), encoding="utf-8")
    (info_dir / "entry_points.txt").write_text("[console_scripts]\nkoma = koma_gr.cli:main\n", encoding="utf-8")
    return info_dir.name


def get_requires_for_build_wheel(config_settings=None) -> list[str]:
    return []


def get_requires_for_build_sdist(config_settings=None) -> list[str]:
    return []


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Build the koma-gr wheel and sdist (no pip needed).")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--list", action="store_true", help="print the files the wheel carries, then exit")
    a = ap.parse_args(argv)
    if a.list:
        for p in files():
            print(p.relative_to(ROOT).as_posix())
        return 0
    for built in (build_wheel(a.out), build_sdist(a.out)):
        path = Path(a.out) / built
        print(f"{path}  {path.stat().st_size} B  sha256 {hashlib.sha256(path.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
