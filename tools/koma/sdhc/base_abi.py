"""The Koma 2 firmware ABI that widefs builds against: RAM words in the SDHC driver cave and the bytes that identify
a Koma 2 image at run time. Values are those of Koma 2 build 2b (sha256 75425a70...9680, tools/koma/firmware2).

  flag        u32  0 stock path, 1 SD 2.0 CCS=0, 2 SDHC (CCS=1); |0x80 = mismatch latched (card refused)
  real        u32  real card capacity in sectors
  limit       u32  served capacity in sectors (the driver guards refuse any I/O at or past it)
  fs_wide_ok  u32  0 = lockout active: the stock FS never mounts or formats a CCS card. widefs writes 1 only after
                   the sector FS is live (all patches applied).
  signature   bytes that identify a Koma 2 image; the widefs init does nothing unless all match (a Koma 1 camera has
              no SDHC driver, so the module must stay inert there).
"""
from __future__ import annotations

ABI = dict(
    flag=0x5753C, real=0x57544, limit=0x57548, fs_wide_ok=0x5754C,
    signature=[(0x112578, b"240K2\0\0\0"),                  # MakerNote version format slot ('240K2')
               (0xB3970, bytes.fromhex("f7a3fd8b")),         # mount_guard BL (the lockout)
               (0xB26E8, bytes.fromhex("f7a4feeb"))],        # fmt_guard BL
)
assert ABI["real"] == ABI["flag"] + 8 and ABI["limit"] == ABI["flag"] + 12 and ABI["fs_wide_ok"] == ABI["flag"] + 16
