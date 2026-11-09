"""Boundary stubs. Each replaces real firmware or hardware behaviour and is listed in every report."""
from __future__ import annotations

import errno
import os
import struct
from pathlib import Path

CPU1_XFER = 0xca524


class Cpu1:
    """Stub at FUN_000ca524, the firmware's request/response exchange with the separate CPU1 microcontroller.
    Skipped real code: SPI setup 0x23fcc, per-byte GPIO-edge handshake on pin 0x3c (0xca60c), reply copy.
    CPU1 firmware is not in our image, so every reply below is fabricated from how the requesting code uses it.
    Request frame per FUN_000ca76a: [op+size, reg_hi, reg_lo, data...]. Register names from table 0x181676."""
    # register -> reply bytes; justification in comment. Unlisted GET registers answer zeros and are recorded.
    REPLIES = {
        0xf202: b"\xc0",        # REQ_GET_UNREG_VOLT: above any threshold; real scale unknown
        0xf21a: b"\xc0",        # REQ_GET_BATT_VOLT: read by 0x521dc
        0xf232: b"\x80",        # REQ_GET_BATT_TEMP
        0xf27a: b"\x80",        # REQ_GET_AF_TEMP: read by 0x5225c
        0xf10e: b"\x01\x00",    # REQ_GET_VERSION: fabricated CPU1 version
        0xf113: b"\x00",        # REQ_GET_COLDSTART
        0xf201: b"\x08",        # REQ_GET_BOOT_TRIGGER: bit 3 maps to trigger 8 in 0x51a64; meaning unconfirmed
        0xf2f1: b"\x10",        # REQ_GET_RTC_XSTP: 0x51b54 tests the returned bit
        0xf345: b"\x00",        # REQ_GET_BATT_TYPE
    }

    def __init__(self, machine, keys):
        self.m = machine
        self.keys = keys
        self.log = []
        self.unmodelled = set()
        self.stub_notes = [{
            "name": "cpu1_exchange", "address": hex(CPU1_XFER),
            "skips": "SPI transfer and handshake with CPU1; all CPU1 replies are fabricated",
        }]
        machine.at(CPU1_XFER, self._xfer)

    def _xfer(self, m):
        ptr, length = m.r(0), m.r(1)
        req = bytes(m.uc.mem_read(ptr, length))
        reply = None
        reg = None
        if length >= 3 and not (req[0] & 0x80):
            reg = (req[1] << 8) | req[2]
            write = bool(req[0] & 0x40)
            if not write:
                data = self._get(reg, length - 3)
                reply = req[:3] + data
        self.log.append((req, reg))
        m.event("CPU1", f"req={req.hex()} reply={(reply or req).hex()}")
        if reply:
            m.uc.mem_write(ptr, reply)
        m.return_from_stub(0)

    def _get(self, reg, size):
        if reg == 0xf1d2:       # REQ_GET_HKEY_STATUS. Decode at 0x5129c..; see set_keys in boot config
            return self.key_bytes(size)
        data = self.REPLIES.get(reg)
        if data is None:
            self.unmodelled.add(hex(reg))
            data = bytes(size)
        return (data + bytes(size))[:size]

    KEY_TABLE = 0x18108c       # records of 0x24 bytes: type, byte index, mask, polarity, name. Decode: 0x5110c
    KEY_NAMES = {"MODE0": 0, "MODE1": 1, "MODE2": 2, "MODE3": 3, "POWER": 6, "PLAY": 7, "USB": 8, "UP": 0xb,
                 "DOWN": 0xc, "RIGHT": 0xd, "LEFT": 0xe, "TELE": 0xf, "WIDE": 0x10, "MENU": 0x13, "ENTER": 0x17}

    def key_bytes(self, size):
        """Encode key states into REQ_GET_HKEY_STATUS bytes. Per 0x5110c: polarity 0 is pressed when the bit is
        clear, polarity 1 is pressed when set, polarity 2 reads 0. Unlisted keys are released. Mode dial defaults
        to MODE0+MODE2, which 0x51158 maps to mode value 1 (accepted by the update key test 0x32540)."""
        data = bytearray(size)
        for i in range(0x2a):
            rec = bytes(self.m.uc.mem_read(self.KEY_TABLE + i * 0x24, 4))
            _, index, mask, pol = rec
            if index >= size:
                continue
            state = self.keys.get(i, 1 if i in (0, 2) else 0)
            if (pol == 0 and not state) or (pol == 1 and state):
                data[index] |= mask
        return bytes(data)

    def set_key(self, name_or_index, state):
        idx = self.KEY_NAMES.get(name_or_index, name_or_index)
        self.keys[idx] = state
        self.m.event("INPUT", f"key {name_or_index} state={state}")


class FileLayer:
    """Stub at the firmware's file API, /ATA1 paths only. Skipped real code: FAT layer under 0x55dc0 (0xb6xxx), the
    /ATA1 mounted flag 0x2bce20, SD host driver and card init. Other paths (/IROM) run the real code.
    The host directory is read-only: writes stay in memory for the run and are logged.
    Signatures from decompilation of 0x55dc0, 0x55cf4, 0x55f0c, 0x56024, 0x561a8, 0x560ec, 0x56560, 0x55d80."""
    FD_BASE = 0x7000
    OPEN, CREATE, CLOSE, READ, READ2, WRITE, FSTAT, REMOVE = (0x55dc0, 0x55cf4, 0x55f0c, 0x56024, 0x561a8, 0x560ec,
                                                              0x56560, 0x55d80)

    def __init__(self, machine, sdcard, present, preload=None):
        self.m = machine
        self.present = present
        self.ops = []
        self.fds = {}
        self.files = {}
        if sdcard and present:
            for p in Path(sdcard).iterdir():
                if p.is_file():
                    self.files[p.name.upper()] = bytes(p.read_bytes())
        if preload and present:
            for name, data in preload.items():
                self.files[name.upper()] = bytes(data)
        self.stub_notes = [{
            "name": "ata1_file_layer", "address": "0x55dc0 family",
            "skips": "FAT filesystem, SD host driver, card detection and mounted flag for /ATA1",
        }]
        for addr, fn in ((self.OPEN, self._open), (self.CREATE, self._create), (self.CLOSE, self._close),
                         (self.READ, self._read), (self.READ2, self._read), (self.WRITE, self._write),
                         (self.FSTAT, self._fstat), (self.REMOVE, self._remove)):
            machine.at(addr, fn)

    def _log(self, text):
        self.ops.append(text)
        self.m.event("FILE", text)

    @staticmethod
    def _name(path):
        return path[6:].upper()

    def _is_card(self, path):
        return path.upper().startswith("/ATA1")

    def _open(self, m):
        path = m.cstr(m.r(0))
        flags = m.r(1)
        if not self._is_card(path):
            return
        name = self._name(path)
        if flags & 0x200:
            return self._create(m)
        if not self.present or name not in self.files:
            self._log(f"open {path} flags={flags:#x} -> -1")
            return m.return_from_stub(0xffffffff)
        fd = self.FD_BASE + len(self.fds)
        self.fds[fd] = [name, 0]
        self._log(f"open {path} flags={flags:#x} -> fd {fd:#x} size={len(self.files[name])}")
        m.return_from_stub(fd)

    def _create(self, m):
        path = m.cstr(m.r(0))
        if not self._is_card(path):
            return
        if not self.present:
            self._log(f"create {path} -> -1")
            return m.return_from_stub(0xffffffff)
        name = self._name(path)
        self.files[name] = b""
        fd = self.FD_BASE + len(self.fds)
        self.fds[fd] = [name, 0]
        self._log(f"create {path} -> fd {fd:#x} (in-memory)")
        m.return_from_stub(fd)

    def _close(self, m):
        fd = m.r(0)
        if fd in self.fds:
            self._log(f"close fd {fd:#x} ({self.fds[fd][0]})")
            del self.fds[fd]
            m.return_from_stub(0)

    def _read(self, m):
        fd, buf, n = m.r(0), m.r(1), m.r(2)
        if fd not in self.fds:
            return
        name, pos = self.fds[fd]
        chunk = self.files[name][pos:pos + n]
        m.uc.mem_write(buf, chunk)
        self.fds[fd][1] = pos + len(chunk)
        self._log(f"read fd {fd:#x} ({name}) pos={pos} want={n} got={len(chunk)}")
        m.return_from_stub(len(chunk))

    def _write(self, m):
        fd, buf, n = m.r(0), m.r(1), m.r(2)
        if fd not in self.fds:
            return
        name, pos = self.fds[fd]
        data = bytes(m.uc.mem_read(buf, n))
        self.files[name] = self.files[name][:pos] + data + self.files[name][pos + n:]
        self.fds[fd][1] = pos + n
        self._log(f"write fd {fd:#x} ({name}) n={n} (in-memory)")
        m.return_from_stub(n)

    def _fstat(self, m):
        fd, st = m.r(0), m.r(1)
        if fd not in self.fds:
            return
        size = len(self.files[self.fds[fd][0]])
        m.uc.mem_write(st, bytes(0x1c) + struct.pack(">I", size))
        self._log(f"fstat fd {fd:#x} size={size}")
        m.return_from_stub(0)

    def _remove(self, m):
        path = m.cstr(m.r(0))
        if not self._is_card(path):
            return
        name = self._name(path)
        hit = self.files.pop(name, None) is not None
        self._log(f"remove {path} -> {'0' if hit else '-1'} (in-memory)")
        m.return_from_stub(0 if hit else 0xffffffff)
