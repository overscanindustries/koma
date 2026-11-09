"""make-loader: builds the flash-1 image from the stock 2.40 file; refuses anything else. The stock file is the user's
(firmware.source(): --firmware / KOMA_FIRMWARE / the checkout copy)."""
from __future__ import annotations

import hashlib
import io
import zipfile

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
