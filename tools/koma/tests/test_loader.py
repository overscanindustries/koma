"""make-loader: builds the flash-1 image from the stock 2.40 file; refuses anything else. The stock file is the user's
(firmware.source(): --firmware / KOMA_FIRMWARE / the checkout copy); package byte-identity is in test_provenance."""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from _util import fw
from koma import loader as L
from koma.cli import main


def _stock(tmp_path):
    """The verified stock container, copied under a neutral name (never QRFLECT*) so the CLI path is exercised."""
    p = tmp_path / "stock-240.bin"
    p.write_bytes(fw.stock())
    return p


def test_make_loader_builds_from_stock_qrflect3(tmp_path, capsys):
    out = tmp_path / "loader.bin"
    assert main(["make-loader", str(_stock(tmp_path)), "-o", str(out)]) == 0
    data = out.read_bytes()
    assert hashlib.sha256(data).hexdigest() == L.OUTPUT_SHA256
    text = capsys.readouterr().out
    assert L.OUTPUT_SHA256 in text and "next: copy to the card root as QRFLECT3" in text


def test_make_loader_builds_from_zip(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Firmware rel 2.40/qrflect3", fw.stock())
    src = tmp_path / "grd240.zip"
    src.write_bytes(buf.getvalue())
    assert L.make(src, tmp_path / "from-zip.bin") == L.OUTPUT_SHA256


def test_make_loader_rejects_wrong_input(tmp_path):
    bad = bytearray(fw.stock())
    bad[0x1000] ^= 1
    src = tmp_path / "not-stock.bin"
    src.write_bytes(bytes(bad))
    out = tmp_path / "out.bin"
    assert main(["make-loader", str(src), "-o", str(out)]) == 2
    assert not out.exists()
    with pytest.raises(L.LoaderError, match=L.STOCK_SHA256):   # the refusal prints the expected hash
        L.make(src, out)
    assert not out.exists()
    with pytest.raises(L.LoaderError, match=L.STOCK_SHA256):
        L.build(bytes(bad))
    # the already-patched image is not stock either
    patched = tmp_path / "patched.bin"
    patched.write_bytes(L.build(fw.stock()))
    with pytest.raises(L.LoaderError, match="not the stock"):
        L.read_stock(patched)


def test_make_loader_checksum_matches_stock_header():
    assert L._header(fw.stock())[1] == 0x346F08ED


def test_module_loader_image_is_cave_and_hook_only():
    """What `koma smoke` boots: the flash-1 edits minus the Rev024M marker (differs only there and in the checksum)."""
    a, b = fw.loader_image(), L.build(fw.stock())
    assert hashlib.sha256(a).hexdigest() == L.MODULE_LOADER_SHA256
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    assert diff == [0x32, 0x33] + list(range(0xF677B, 0xF6780)), [hex(i) for i in diff]


def test_make_firmware_builds_the_koma10_image(tmp_path, capsys):
    out = tmp_path / "koma-firmware-1.0.bin"
    assert main(["make-firmware", str(_stock(tmp_path)), "-o", str(out)]) == 0
    data = out.read_bytes()
    assert hashlib.sha256(data).hexdigest() == L.KOMA10_SHA256
    assert data[0x200 + 0x112578 - 0x1C000:][:9] == b"240K1.0\0\0" and L.KOMA10_TAG == "240K1.0"
    stock = fw.stock()
    assert data[:0x30] == stock[:0x30] and data[0x34:0x200] == stock[0x34:0x200]   # Ricoh header (02.40) kept
    assert L.KOMA10_SHA256 in capsys.readouterr().out
    lit = sum(len(d) for _, d in L.KOMA10_DELTA if isinstance(d, bytes))
    assert lit < 4096                                   # only Koma's own bytes are stored; stock code is referenced
    bad = bytearray(stock)
    bad[0x1000] ^= 1
    with pytest.raises(L.LoaderError, match=L.STOCK_SHA256):
        L.build_koma10(bytes(bad))


PACKAGE_DELTA = Path(__file__).resolve().parents[3] / "dist-release" / "koma-1.0.0.grmod.delta"
PACKAGE_SHA256 = "dd67a69ec0de60997cc741351261b611a90142e09b9b1815ea3f8a315a0840c6"   # komainstall.PACKAGE_SHA256


def test_make_package_rebuilds_the_release_package(tmp_path, capsys):
    out = tmp_path / "GRMOD.BIN"
    assert main(["make-package", str(_stock(tmp_path)), "--delta", str(PACKAGE_DELTA), "-o", str(out)]) == 0
    assert hashlib.sha256(out.read_bytes()).hexdigest() == PACKAGE_SHA256
    assert PACKAGE_SHA256 in capsys.readouterr().out
    # no window of DELTA_MIN_COPY stock bytes (other than 0x00/0xFF fill) is stored literally
    stock, delta = fw.stock(), PACKAGE_DELTA.read_bytes()
    k = L.DELTA_MIN_COPY
    windows = {delta[i:i + k] for i in range(len(delta) - k + 1)}
    payload = stock[L.HEADER_SIZE:]
    shared = {payload[s:s + k] for s in range(len(payload) - k + 1)} & windows
    assert not [w for w in shared if w.strip(b"\0\xff")]


def test_package_delta_round_trip_and_refusals(tmp_path):
    stock = fw.stock()
    payload = stock[L.HEADER_SIZE:]
    target = b"KOMA" + payload[0x1000:0x1040] + bytes(20) + b"own bytes" + payload[0x50000:0x50010]
    delta = L.encode_delta(stock, target)
    assert L.apply_delta(stock, delta) == target
    st = L.delta_stats(delta)
    assert (st["copied"], st["zero"], st["literal"]) == (0x50, 20, 13)
    with pytest.raises(L.LoaderError, match="expected"):
        L.apply_delta(stock, delta, "0" * 64)
    bad = bytearray(delta)
    bad[-1] ^= 1
    with pytest.raises(L.LoaderError, match="not the one the delta names"):
        L.apply_delta(stock, bytes(bad))
    other = bytearray(stock)
    other[0x1000] ^= 1
    with pytest.raises(L.LoaderError, match=L.STOCK_SHA256):
        L.apply_delta(bytes(other), delta)
