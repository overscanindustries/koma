"""widefs phase 1: every RAM .text edit, in one table. Halfwords as in the big-endian 2.40 image.

DATA = (addr, before_hex, after_hex, group, why)      same-size byte substitution
BL   = (site, before_hex, handler, group, why)        4 bytes become a Thumb BL to a resident widefs handler

Groups (fullcap README section 2.1 / sector-sites.md section 1):
  A1  position unit -> sectors (sector-sites.md, 42 sites incl. M1 and the internal-memory twin 1275f4)
  A2  mount gates (cluster-count gate, size gate, size literal)
  A3  FAT mirror window count / loop counter u8 -> u32; FSInfo write-back with the live hint
  A4  mount free count: trust FSInfo when sane, else full scan (+ refuse FATSz > 0xffff: A7 not in phase 1)
  A5  allocator wraps to cluster 2 once
  A6  free-space reporting: clamp at the 0x56650 choke point, exact remaining shots, signed consumers
  A10 SD-spec FAT32 formatter for the SDHC card (replaces the stock format chain for that card only)
Every `before` is verified by Koma (Check records) against RAM before anything is written; the widefs init then
writes `after` only when an SDHC card is present (Base 2 flag == 2) or the build option widefs.always is on.
"""

DATA = [
    # ---------------- A1: sector-sites.md section 1 (address order)
    (0xB2014, "8999", "2101", "A1", "S1 b1ef4: [dffa0] += 1 sector (was += bps)"),
    (0xB215A, "899A", "2201", "A1", "S2 b2054: [dfff4] += 1"),
    (0xB22A2, "899A", "2201", "A1", "S3 b21ac: [dffa0] += 1"),
    (0xB240E, "025B", "46C0", "A1", "W7 b22f4: FAT mirror step pos += 3 sectors"),
    (0xB24C0, "8980", "2001", "A1", "I4 b24a4: FAT-dirty window index (LBA - fatStart)/3"),
    (0xB29B8, "6F81", "2101", "A1", "A1 b25a8 tail: partStart = startLBA (was *0x200)"),
    (0xB327A, "039B", "015B", "A1", "A5 b3078 (stock format): FAT fill pos += 32 sectors"),
    (0xB3760, "8992", "2201", "A1", "A2 b3478: FSInfo read pos = FSInfoSec + partStart"),
    (0xB38CA, "D023", "D123", "A1", "M1 (with the BL at 0xb38c6): bne -> mount error unless BytsPerSec == 512"),
    (0xB391C, "20FF302943601940" "1DC131791DC636B96A8988B0",
     "1C281DC131791DC6" "36B96A89024988B046C046C0", "A1",
     "A4 b3478 internal-memory check: dataStart (sectors) << 9 before the % clusterBytes test"),
    (0xB3AF2, "0240", "46C0", "A1", "W1 b3ad0: FAT window key = q*3 + fatStart"),
    (0xB3EF6, "6801", "2100", "A1", "B6a b3dc0: chain bound saturates (mov r1,#0)"),
    (0xB3EFC, "4341", "43C9", "A1", "B6b b3dc0: mvn r1,r1 -> 0xffffffff"),
    (0xB4074, "6801", "2100", "A1", "B7a b3f3c"),
    (0xB407A, "4341", "43C9", "A1", "B7b b3f3c"),
    (0xB4174, "680B", "2300", "A1", "B8a b40bc"),
    (0xB4176, "437B", "43DB", "A1", "B8b b40bc"),
    (0xB4786, "1889", "3371", "A1", "W3 (with the BL at 0xb4782): add r3,#0x71 after the helper"),
    (0xB47BA, "025B", "46C0", "A1", "W4 b4744: hit window = key..key+3"),
    (0xB47D4, "0240", "46C0", "A1", "W5 b4744: fold divisor 3"),
    (0xB47DE, "0240", "46C0", "A1", "W6 b4744: new key = idx*3 + fatStart"),
    (0xB4828, "0240", "46C0", "A1", "W2 b480c: window key"),
    (0xB4926, "8997", "2701", "A1", "A6 b4908 (stock format): backup VBR pos"),
    (0xB4A22, "8992", "2201", "A1", "A7 b4980 (stock format): FSInfo pos"),
    (0xB4A70, "8980", "2001", "A1", "A8 b4980 (stock format): FSInfo+1 pos"),
    (0xB4ACA, "8989", "2101", "A1", "A3 b4aa0: FSInfo refresh pos"),
    (0xB519C, "0949", "0109", "A1", "B1 b514c: (dfff4-e0004) sectors << 4 = entries"),
    (0xB5208, "0940", "0100", "A1", "B2 b514c: FAT32 root path"),
    (0xB5232, "0949", "0109", "A1", "B3 b514c: FAT12/16 root"),
    (0xB5A0A, "9A02", "2201", "A1", "B4 b58d4: dffb0 += 1 sector"),
    (0xB5B7C, "680A", "2200", "A1", "B9a b5a6c: chain bound saturates"),
    (0xB5B7E, "435A", "43D2", "A1", "B9b b5a6c"),
    (0xB5E7A, "1DC1", "1C01", "A1", "B5 b5dcc: SecPerClus load (1/3)"),
    (0xB5E7C, "31B9", "31A0", "A1", "B5 b5dcc (2/3)"),
    (0xB5E7E, "8889", "7B89", "A1", "B5 b5dcc: ldrb SecPerClus (3/3)"),
    (0xB5E86, "8980", "2001", "A1", "B5 b5dcc path 1: d2de4 + SPC - 1"),
    (0xB5E8E, "8980", "2001", "A1", "B5 b5dcc path 2: *p - 1"),
    (0xB7220, "8889", "1C11", "A1", "C1 b717c: r1 = SPC"),
    (0xB7224, "8980", "2001", "A1", "C1 b717c"),
    (0xB7230, "899B", "2301", "A1", "C2 b717c: dffa0 -= 1"),
    (0xB74C2, "6F6819006768" "1C081C21F0A0FBDA",
     "0A626F681880" "67681C1046C046C0", "A1",
     "C3 b7270 seek: dffa0 += r4>>9, dffcc += r4>>9 (needs M1)"),
    (0xCA16E, "8986", "2601", "A1", "I1 ca12c: FSInfo exception pos"),
    (0xCA2A2, "89A8", "2001", "A1", "I2 ca12c: FAT fold / 1"),
    (0xD2CA8, "89B8", "2001", "A1", "G1 d2c38: FAT32 Rsvd in sectors"),
    (0xD2CB2, "89B8", "2001", "A1", "G2 d2c38: FATSz32 in sectors"),
    (0xD2CBE, "89B8", "2001", "A1", "G3 d2c38: FAT12/16 Rsvd"),
    (0xD2CC8, "89B8", "2001", "A1", "G4 d2c38: FATSz16"),
    (0xD2CD2, "0140", "0900", "A1", "G5 d2c38: root dir sectors = RootEnt/16"),
    (0xD2DF8, "8980", "2001", "A1", "P1 d2de4: cluster -> sector (all 15 callers)"),
    (0xD3142, "F084FD9F", "1C0846C0", "A1", "P2 d311c: LBA = pos (all 4 callers)"),
    (0x127744, "89A0", "2001", "A1", "I3 1275f4 (internal memory): FAT fold / 1"),
    # ---------------- A2: mount gates
    (0xB38A6, "DC01", "E001", "A2", "card size gate: always the literal limit (stock: FATSz when slot index >= 11)"),
    (0xB396E, "2800", "2000", "A2", "cluster-count gate off (GR II 0x2006d440); Base 2 mount_guard keeps its own checks"),
    (0xB39B8, "7FFFFFFF", "FFFFFFFF", "A2", "size gate literal: TotSec < 2^32-1"),
    # ---------------- A3: FAT mirror counters u8 -> u32 (bug proven in fullcap section 7.1)
    (0xB2330, "06050E2D", "1C0546C0", "A3", "b22f4 FAT32 window count r5 = FATSz/3 (no & 0xff)"),
    (0xB234E, "06050E2D", "1C0546C0", "A3", "b22f4 FAT12/16 window count (no & 0xff)"),
    (0xB2356, "1C6806050E2D", "1C6D46C046C0", "A3", "b22f4 round up: r5 += 1 (no & 0xff)"),
    (0xB2418, "1C6006040E24", "1C6446C046C0", "A3", "b22f4 loop counter r4 += 1 (no & 0xff)"),
    # ---------------- A6: signed consumers read the clamped free-cluster slot out[2] instead of raw out[3]
    (0x4C3D2, "9803", "9802", "A6", "0x4c3b8: free*cluster signed test uses clamped out[2]"),
    (0x4C408, "9803", "9802", "A6", "0x4c3f0: same"),
    (0xE41DC, "9803", "9802", "A6", "0xe40bc movie time (mode 5): clamped out[2]"),
    (0xE421C, "9903", "9902", "A6", "0xe40bc movie time (mode 7): clamped out[2]"),
    (0xE41B0, "98039A05", "99039805", "A6", "0xe40bc stills: r1 = free-1, r0 = clusterBytes for shots_div"),
    (0xE41B8, "1C30F073FD63", "46C046C046C0", "A6", "0xe40bc stills: old mul/divide removed"),
    (0xE41D6, "F073FD55", "46C046C0", "A6", "0xe40bc stills (dir-overhead term): old divide removed"),
]

BL = [
    (0xB38C6, "89822A00", "h_bps", "A1", "M1: BytsPerSec == 512 test (Z set only for 512)"),
    (0xB4782, "9A003371", "h_b4744", "A1", "W3: FAT entry hit-test position fatStart + (off >> 9)"),
    (0xB4B80, "F01EF9A4", "fsinfo_hint", "A3", "b4aa0 FSInfo write-back: next-free = live hint 0x2dfdb4"),
    (0xB80BC, "F7F9FCA8", "mount_count", "A4", "b8090 mount: free count via trusted FSInfo or full scan"),
    (0xB4628, "F7FDFAA6", "alloc_wrap", "A5", "b45b8 free-cluster cache fill: b1b78 wraps to cluster 2 once"),
    (0x56666, "F061F88B", "vol_info", "A6", "0x56650 volume info: clamp free clusters / free bytes"),
    (0xE41B4, "43501C01", "shots_div", "A6", "0xe40bc stills: shots = (free-1) / (perShot / clusterBytes)"),
    (0xE41D2, "43411C30", "shots_div", "A6", "0xe40bc stills, dir-overhead term"),
    (0xB8442, "F7FAF8B1", "fmt_hook", "A10", "b83a4 format: SDHC card -> widefs formatter, else stock b25a8(2)"),
]


def data_rows():
    return [(a, bytes.fromhex(b), bytes.fromhex(c), g, w) for a, b, c, g, w in DATA]


def bl_rows():
    return [(a, bytes.fromhex(b), h, g, w) for a, b, h, g, w in BL]
