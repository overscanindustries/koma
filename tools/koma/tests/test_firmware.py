"""Stock-firmware selection: --firmware / KOMA_FIRMWARE / checkout default; every form verified; clear errors."""
from __future__ import annotations

import hashlib
import os

import pytest

from _util import fw
from koma.cli import main


def _with(path):
    """Run with firmware.use(path), restoring the process default afterwards."""
    class _Ctx:
        def __enter__(self):
            fw.use(path)

        def __exit__(self, *a):
            fw.use(None)
    return _Ctx()


def test_payload_is_the_verified_240_payload():
    assert hashlib.sha256(fw.payload()).hexdigest() == fw.PAYLOAD_SHA256
    assert len(fw.payload()) == fw.PAYLOAD_BYTES


def test_bare_payload_builds_but_cannot_smoke(tmp_path):
    p = tmp_path / "payload.bin"
    p.write_bytes(fw.payload())
    with _with(p):
        assert fw.ram(0x96436, 4) == fw.payload()[0x96436 - fw.LOAD:][:4]
        with pytest.raises(fw.FirmwareError, match="bare 2.40 payload"):
            fw.stock()


def test_wrong_file_is_refused(tmp_path, capsys):
    p = tmp_path / "other.bin"
    p.write_bytes(b"\0" * 4096)
    out = tmp_path / "x.grmod"
    with _with(p):
        with pytest.raises(fw.FirmwareError, match="not the stock"):
            fw.payload()
    assert main(["--firmware", str(p), "build", "--features", "snapdist", "-o", str(out)]) == 2
    assert not out.exists()
    fw.use(None)


def test_missing_file_and_env(tmp_path):
    with _with(tmp_path / "nope.bin"):
        with pytest.raises(fw.FirmwareError, match="no such file"):
            fw.source()
    old = os.environ.get(fw.ENV)
    os.environ[fw.ENV] = str(tmp_path / "nope2.bin")
    try:
        fw.use(None)
        with pytest.raises(fw.FirmwareError, match=fw.ENV):
            fw.source()
    finally:
        if old is None:
            del os.environ[fw.ENV]
        else:
            os.environ[fw.ENV] = old
        fw.use(None)


def test_no_firmware_anywhere_says_how_to_fix(tmp_path):
    saved, old = fw.DEFAULT_FIRMWARE, os.environ.pop(fw.ENV, None)
    fw.DEFAULT_FIRMWARE = (tmp_path / "absent",)
    try:
        fw.use(None)
        with pytest.raises(fw.FirmwareError, match="--firmware PATH"):
            fw.source()
    finally:
        fw.DEFAULT_FIRMWARE = saved
        if old is not None:
            os.environ[fw.ENV] = old
        fw.use(None)


def test_vendored_data_has_no_stock_tables():
    """Shipped data never carries Ricoh's own tables (the stock-c5 column was dropped from tone-pinned.csv)."""
    assert (fw.DATA / "tone-pinned.csv").read_text().splitlines()[0] == "index,sib-R3-18c3c4"
    names = {p.name for p in fw.DATA.rglob("*.csv")}
    assert not [n for n in names if n.startswith("stock")], names
