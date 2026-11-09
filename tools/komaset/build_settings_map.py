#!/usr/bin/env python3
"""Generate tools/komaset/settings-map.json: byte map of the GR DIGITAL 2.40 user-settings block (RAM 0x1b000, 4 KB).

Static source of truth for the host-side settings editor.
Run:  python3 tools/komaset/build_settings_map.py [--check-block FILE]
--check-block validates a 4096-byte block image (magic, checksum, every enum/range field) and prints mismatches.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "settings-map.json"

F = []          # fields
X = []          # exclude_on_merge


def hx(o):
    return "0x%03x" % o


def field(off, name, label, page, typ, *, size=1, values=None, rng=None, bits=None, unit=None, persist=True,
          body=False, ev="", conf="STATIC", help="", order=None, section=None, ui=None, default=None):
    f = {"name": name, "label": label, "page": page, "offset": hx(off), "size": size, "type": typ}
    if values is not None:
        f["values"] = {str(k): v for k, v in values.items()}
    if rng is not None:
        f["range"] = list(rng)
    if bits is not None:
        f["bits"] = {str(k): v for k, v in bits.items()}
    if unit:
        f["unit"] = unit
    if default is not None:
        f["default"] = default
    f.update({"persist": persist, "body_specific": body, "evidence": ev, "confidence": conf})
    if help:
        f["help"] = help
    if order is not None:
        f["order"] = order
    if section:
        f["section"] = section
    if ui:
        f["ui"] = ui
    F.append(f)
    return f


def excl(off, size, reason):
    X.append({"offset": hx(off), "size": size, "reason": reason})


# ---------------------------------------------------------------------------------------------------- value tables
QUALITY = {0: "RAW", 1: "FINE", 2: "NORMAL"}
SIZE = {9: "3264x2448", 6: "2592x1944", 2: "2048x1536", 1: "1280x960", 0: "640x480"}
ASPECT = {0: "4:3", 1: "3:2"}
FOCUS = {0: "MULTI AF", 6: "SPOT AF", 1: "MF", 2: "SNAP", 3: "INFINITY"}
METER = {2: "MULTI", 0: "CENTER", 1: "SPOT"}
CONT = {0: "OFF", 1: "CONT.", 2: "S-CONT", 3: "M-CONT"}
IMGSET = {0: "HARD", 1: "NORMAL", 2: "SOFT", 3: "B&W", 6: "B&W (TE)", 4: "SETTING1", 5: "SETTING2"}
BKT = {0: "OFF", 3: "ON 0.3", 1: "ON 0.5", 2: "WB-BKT", 4: "CL-BKT"}
ONOFF01 = {0: "OFF", 1: "ON"}
DATEIMP = {0: "OFF", 1: "DATE", 2: "TIME"}
EV = {1 + 2 * i: "%+.1f" % (-2.0 + i / 3.0) if i != 6 else " 0.0" for i in range(13)}
EV = {k: (v.replace("+0.0", " 0.0").replace("-0.0", " 0.0")) for k, v in EV.items()}
FLASHCOMP = {3 + i: ("%+.1f" % (-2.0 + i / 3.0)).replace("+0.0", " 0.0").replace("-0.0", " 0.0") for i in range(13)}
WB = {0: "AUTO", 1: "OUTDOORS", 2: "CLOUDY", 3: "INCAND.", 4: "FLUORES.", 5: "MANUAL", 7: "DETAIL"}
ISO = {0: "AUTO", 1: "ISO 64", 2: "ISO 100", 4: "ISO 200", 6: "ISO 400", 7: "ISO 800", 8: "ISO 1600"}
LEVEL5 = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5"}
TONE = {6: "SEPIA", 1: "RED", 2: "GREEN", 3: "BLUE", 4: "PURPLE"}
FLASH = {0: "OFF (I)", 1: "AUTO (I)", 2: "RED-EYE (I)", 3: "ON (I)", 17: "SLOW SYNC (I)"}
SCENE = {0: "(none / not SCENE)", 1: "PORTRAIT (I)", 2: "SPORTS (I)", 3: "LANDSCAPE (I)", 4: "NIGHTSCAPE (I)",
         5: "TEXT MODE", 6: "HIGH SENS (I)", 8: "scene 8 (U; SKEW COR or ZOOM MACRO)",
         9: "scene 9 (U)", 11: "scene 11 (U)", 12: "scene 12 (U)"}
ADJ = {0: "OFF", 10: "EXPO.CMP", 11: "WHT.BAL.", 5: "ISO", 1: "QUALITY", 2: "FOCUS", 3: "IMG SET", 4: "METERING",
       9: "CONT MDE", 6: "AUTO BKT", 7: "WITH SND", 13: "FLA COMP"}
LANG = {0: "JAPANESE", 1: "ENGLISH", 2: "DEUTSCH", 3: "FRANCAIS", 4: "ITALIANO", 5: "ESPANOL",
        6: "CHINESE (SIMPLIFIED)", 7: "CHINESE (TRADITIONAL)", 8: "KOREAN"}
MOVIE = {9: "320x240", 1: "160x120"}
TEXTSIZE = {9: "3264", 2: "2048"}
DENSITY = {1: "DEEP", 2: "NORMAL", 3: "LIGHT"}
SLOWLMT = {0: "OFF", 1: "1/2SEC.", 2: "1/4SEC.", 3: "1/8SEC."}
TIMEEXP = {0: "OFF", 1: "1 (I)", 2: "2 (I)", 3: "3 (I)", 4: "4 (I)"}
SHARP1X = {0: "SHARP", 1: "NORMAL", 2: "SOFT"}

S = "shooting"
H5 = "handbook/05 §3.3"

# ---------------------------------------------------------------------------------------------------- header, counters
field(0x000, "magic", "MAGIC 'MG1H'", "internal", "bytes", size=4, ev="0x3d4a4 checks 0x4d473148; 0x3d4a4 writes it",
      conf="HW", help="Block signature. A wrong value makes the camera reset every setting to factory defaults.",
      ui="hidden")
excl(0x000, 4, "Block magic; the editor rewrites it, never merges it.")
field(0x010, "battery_state", "BATTERY STATE (I)", "internal", "u8", rng=(0, 255),
      ev="0x3d4a4 default 0; no reader in the decompile export", conf="I", ui="hidden",
      help="Internal battery-gauge byte.")
field(0x011, "battery_flag", "BATTERY FLAG (I)", "internal", "u8", rng=(0, 255),
      ev="0x3d4a4 default 1; no reader found", conf="I", ui="hidden", help="Internal battery-gauge byte.")
for o, n, l in [(0x012, "battery_half_table", "HALF TABLE"), (0x013, "battery_near_end_table", "NEAR END TABLE"),
                (0x014, "battery_end_table", "END TABLE")]:
    field(o, n, l, "internal", "u8", rng=(0, 255), body=True, conf="STATIC", ui="hidden",
          ev="0x5290c learns it via 0x52814; printed by 0x1f1e0 ('%s = %%x'); 0xce010 clears" % l,
          help="Battery-gauge threshold learned by this body.")
field(0x015, "battery_reserved", "BATTERY (RESERVED)", "internal", "u8", rng=(0, 255), conf="I", ui="hidden",
      ev="0x3d4a4 default 0; no reader found", help="Unused battery byte.")
for o, n, l in [(0x016, "shot_count_battery", "SHOT COUNT"), (0x018, "half_count", "HALF COUNT"),
                (0x01a, "near_end_count", "NEAR END COUNT"), (0x01c, "reboot_count", "REBOOT COUNT")]:
    field(o, n, l, "internal", "u16", size=2, rng=(0, 65535), body=True, ui="hidden",
          ev="printed by 0x1f1e0 ('%s = %%d'); 0x28bdc / 0xce010 update" % l,
          help="Battery-life counter kept by this body.")
excl(0x010, 0x0e, "Battery-gauge tables and counters (0x010..0x01d) learned by this body's battery and power history.")
field(0x028, "release_count", "SHUTTER RELEASE COUNT", "internal", "u32", size=4, rng=(0, 100000), body=True,
      ui="hidden", conf="HW",
      ev="0x11efe8 increments per shot; 0x93222 writes it into the MakerNote; 0x3d4a4 clamps >100000; main body 5502",
      help="Total shots taken by this camera.")
field(0x02c, "counter_02c", "COUNTER 2 (I)", "internal", "u32", size=4, rng=(0, 100000), body=True, ui="hidden",
      conf="I", ev="0x93222 MakerNote word next to the release count; 0x1f09c version screen; clamped like 0x028",
      help="Second lifetime counter shown on the service version screen.")
excl(0x028, 8, "Lifetime counters of this body (release count and a second counter); copying them falsifies the MakerNote.")

# ---------------------------------------------------------------------------------------------------- image settings
field(0x030, "img_set", "IMG SET", S, "enum", values=IMGSET, default=1, order=5, section="Image", conf="HW",
      ev="still commit 0x5b4e4 table 0x1bd451 (cursor 1..7 -> 0,1,2,3,6,4,5); %s; main body = 3 (B&W)" % H5)
for o, n, l in [(0x031, "setting1_contrast", "SETTING1 CONTRAST"),
                   (0x032, "setting1_sharpness", "SETTING1 SHARPNESS"),
                   (0x033, "setting1_color_depth", "SETTING1 COLOR DEPTH"),
                   (0x034, "setting2_contrast", "SETTING2 CONTRAST"),
                   (0x035, "setting2_sharpness", "SETTING2 SHARPNESS"),
                   (0x036, "setting2_color_depth", "SETTING2 COLOR DEPTH")]:
    field(o, n, l, S, "enum", values=LEVEL5, default=3, order=5, section="Image",
          ev="detail screen 0x5b7e4/0x5b89c; readers 0x92cc8 (contrast), 0x91268 (sharpness), 0x92200 (colour); "
             "default 3 in 0x3d4a4")
field(0x037, "af_aux_light", "AF AUX. LIGHT", "setup", "enum", values={0: "ON", 1: "OFF"}, default=0, order=18,
      section="Shooting", ev="SETUP commit 0x6c4bc table 0x1beef2 (cursor1->0, cursor2->1); main body = 1",
      conf="HW")
field(0x038, "marker_1x", "MARKER 'U' (1.x GROUP)", "internal", "u8", rng=(0, 255), ui="hidden",
      ev="0x3d4a4: if != 'U' -> 0x039=0, START SETTING=OFF, My Settings defaults (0x296a8)", conf="HW",
      help="Version marker; if wrong the camera resets My Settings.")
field(0x039, "my_settings_registered", "MY SETTINGS REGISTERED (I)", "internal", "enum", values=ONOFF01, ui="hidden",
      ev="0x6d554 (REG. MY SETTINGS) writes 1 or 0; 0xe343c reads it", conf="STATIC",
      help="Remembers whether My Settings have been registered.")
field(0x03a, "legacy_03a", "LEGACY 0x03A", "internal", "u8", rng=(0, 255), ui="hidden", conf="I",
      ev="0x3d4a4 default 0; no other reference", help="Unused by 2.40.")
field(0x03b, "zoom_btn_set", "ZOOM BTN. SET.", "key_custom", "enum", values={0: "OFF", 1: "EXPO.CMP", 2: "WHT.BAL."},
      default=1, order=20, section="Buttons", conf="HW",
      ev="SETUP commit 0x6c4bc table 0x1beef5; 0x3d4a4 clamps >2 to 0; main body = 1")
for o in range(0x03c, 0x045):
    w = {0x043: "0x66bb8 / 0xdbe64 (factory flows) write it", 0x044: "0x66bb8 / 0xdbf1c (factory flows) write 1"}
    field(o, "legacy_%03x" % o, "LEGACY 0x%03X" % o, "internal", "u8", rng=(0, 255), ui="hidden", conf="I",
          ev="0x3d4a4 default 0; " + w.get(o, "no other reference"), help="Unused by 2.40 user code.")
field(0x046, "legacy_046", "LEGACY 0x046", "internal", "u8", rng=(0, 255), ui="hidden", conf="I",
      ev="0x3d4a4 default 1; no other reference", help="Unused by 2.40.")
field(0x047, "scene_flash_a", "FLASH (SCENE 5)", S, "enum", values=FLASH, order=None, section="Flash", conf="STATIC",
      ev="per-scene flash array 0x1b047+i for scene ids in table 0x1773f4 = {5, 8}; 0x6e364 / 0x6e3d8 / 0x6e3a8")
field(0x048, "scene_flash_b", "FLASH (SCENE 8)", S, "enum", values=FLASH, section="Flash", conf="STATIC",
      ev="per-scene flash array, second entry (scene id 8 from 0x1773f4)")

# ---------------------------------------------------------------------------------------------------- shooting core
field(0x050, "photometry", "PHOTOMETRY", S, "enum", values=METER, default=2, order=3, section="Exposure", conf="HW",
      ev="commit table 0x1bd41b (MULTI=2, CENTER=0, SPOT=1); validator 0x6e040 (bad -> 2); %s" % H5)
field(0x052, "cont_mode", "CONT. MODE", S, "enum", values=CONT, default=0, order=4, section="Drive", persist=False,
      ev="commit table 0x1bd416; validator 0x6e6d8; 0x3d4a4 zeroes it at boot when START SETTING is OFF")
field(0x053, "exposure_comp", "EXPOSURE COMP.", S, "enum", values=EV, default=13, order=10, section="Exposure",
      conf="HW", ev="commit table 0x1bd3ce (odd values 1..25 = -2.0..+2.0 in 1/3 EV); validator 0x6dc24 accepts "
                     "1..25, bad -> 13; main body = 13")
field(0x054, "white_balance", "WHITE BALANCE", S, "enum", values=WB, default=0, order=11, section="Colour", conf="HW",
      ev="commit table 0x1bd3dc; validator 0x6dca0 accepts 0..5, 7; load 0x5aa38 maps 7 -> 7th option DETAIL "
         "(label 0x191c20); main body = 1")
field(0x055, "date_imprint", "DATE IMPRINT", S, "enum", values=DATEIMP, default=0, order=9, section="Image",
      ev="commit table 0x1bd43c; validator 0x6e73c")
field(0x056, "language", "LANGUAGE", "setup", "enum", values=LANG, order=12, section="Display", conf="HW",
      ev="0x3d4a4: default from calibration 0x08010017 (>8 -> 0), clamps >8; index = 9-language string-table "
         "order (ja,en,de,fr,it,es,zh-hans,zh-hant,ko); main body = 1 (ENGLISH)")
excl(0x056, 1, "LANGUAGE: factory default comes from calibration byte 0x08010017 (sales region).")
field(0x057, "boot_media", "BOOT MEDIA FLAG", "internal", "enum", values={1: "1 (I: card)", 10: "10 (I: internal)"},
      ui="hidden", conf="STATIC", body=True,
      ev="0x1effa writes 1 or 10 at every boot from 0x506d2(0); 0x3d4a4 default 1",
      help="Written by the camera at every start; not a user setting.")
excl(0x057, 1, "Rewritten at every boot from the inserted media.")
field(0x058, "focus", "FOCUS", S, "enum", values=FOCUS, default=0, order=2, section="Focus", conf="HW",
      ev="commit table 0x1bd40b (MULTI=0, SPOT=6, MF=1, SNAP=2, INF=3); movie load 0x5aa38 inverse; "
         "validator 0x6decc accepts 0,1,2,3,6; main body = 6. Corrects handbook/05 ('6 probably infinity').")
field(0x059, "iso", "ISO SETTING", S, "enum", values=ISO, default=0, order=12, section="Exposure", conf="HW",
      ev="commit table 0x1bd41f (cursor 1..7 -> 0,1,2,4,6,7,8); validator 0x6dfbc; RAW caps 8 -> 7; main body = 0")
field(0x05a, "legacy_05a", "LEGACY 0x05A", "internal", "u8", rng=(0, 255), ui="hidden", conf="I",
      ev="0x3d4a4 default 0; no other reference", help="Unused by 2.40.")
field(0x05b, "legacy_05b", "LEGACY 0x05B", "internal", "u8", rng=(0, 255), ui="hidden", conf="I",
      ev="0x3d4a4 default 0; only the service preset 0x3e754 writes it", help="Unused by 2.40 user code.")
field(0x05c, "auto_bracket", "AUTO BRACKET", S, "enum", values=BKT, default=0, order=6, section="Drive",
      persist=False, ev="commit table 0x1bd42b (cursor 1..5 -> 0,3,1,2,4); validator 0x6e164 (no WB-BKT in B&W, "
                        "none in RAW); 0x3d4a4 zeroes it at every boot")
field(0x05d, "adj_btn_set2", "ADJ BTN. SET.2", "key_custom", "enum", values=ADJ, default=11, order=22,
      section="ADJ lever", conf="HW", ev="SETUP commit 0x6c4bc table 0x1beebf; 0x3d4a4 default 11 (WHT.BAL.)")
field(0x05e, "adj_state", "ADJ STATE", "internal", "u8", rng=(0, 4), persist=False, ui="hidden",
      ev="ADJ handler 0x767b4..0x7ed00 state; 0x3d4a4 zeroes it at every boot", conf="STATIC",
      help="Working state of the ADJ lever; reset at power-on.")
excl(0x05e, 1, "Transient ADJ state, reset at boot.")
field(0x05f, "legacy_05f", "LEGACY 0x05F", "internal", "u8", rng=(0, 2), ui="hidden", conf="STATIC",
      ev="0x6e218 clamps 0..2 but returns RAM 0x1bef75 instead", help="Unused stored copy of a 1.x item.")
field(0x060, "sharpness_1x", "SHARPNESS (1.x)", S, "enum", values=SHARP1X, default=1, section="Image", ui="hidden",
      ev="setter 0x6e084, validator 0x6e090 (bad -> 1), still passed to capture by 0x5509e; 1.x row 0x1c1264 "
         "SHARP/NORMAL/SOFT is in no 2.40 page", conf="STATIC",
      help="Sharpness item from firmware 1.x; 2.40 has no menu row for it.")
field(0x061, "beep_sound", "BEEP SOUND", "setup", "enum", values={0: "OFF (VOL muted)", 1: "ALL", 2: "SHUTTER"},
      default=1, order=7, section="Sound", conf="HW",
      ev="SETUP commit 0x6c4bc table 0x1bee90 (ALL=1, SHUTTER=2); forced 0 when VOL = mute; main body = 0, VOL 0")
for o in (0x062, 0x065):
    field(o, "legacy_%03x" % o, "LEGACY 0x%03X" % o, "internal", "u8", rng=(0, 255), ui="hidden", conf="I",
          ev="0x3d4a4 default %d; no other reference" % (0 if o == 0x062 else 0), help="Unused by 2.40.")
field(0x063, "video_out_mode", "VIDEO OUT MODE", "setup", "enum", values={0: "NTSC", 1: "PAL"}, order=13,
      section="Display", ev="SETUP commit table 0x1beeaf; validator 0x6e888; default from calibration 0x08010016",
      conf="HW")
excl(0x063, 1, "VIDEO OUT MODE: factory default from calibration byte 0x08010016 (sales region).")
field(0x064, "lcd_confirm", "LCD CONFIRM.", "setup", "enum",
      values={0: "OFF", 16: "0.5SEC.", 1: "1 SEC.", 2: "2 SEC.", 3: "3 SEC."}, order=9, section="Display",
      ev="SETUP commit table 0x1beea1 (0, 0x10, 1, 2, 3); default from calibration 0x08010019; main body = 1",
      conf="HW")
excl(0x064, 1, "LCD CONFIRM.: factory default from calibration byte 0x08010019.")
field(0x066, "sequential_no", "SEQUENTIAL NO.", "setup", "enum", values={1: "ON", 0: "OFF"}, default=1, order=10,
      section="Card", ev="SETUP commit table 0x1bee9e; file-name code 0x47b4e..0x4a450", conf="HW")
field(0x068, "pic_quality", "PIC QUALITY", S, "enum", values=QUALITY, default=2, order=1, section="Image", conf="HW",
      ev="PIC QUALITY/SIZE commit tables 0x1bd3e4 (quality), 0x1bd3fa (size), 0x1bd3ef (3:2); validator 0x6dd2c; "
         "main body = 1 (F3264, 2.80 bpp on the card)")
field(0x069, "pic_size", "PIC SIZE", S, "enum", values=SIZE, default=9, order=1, section="Image", conf="HW",
      ev="table 0x1bd3fa (cursor 1..10 -> 9,9,9,9,9,9,6,2,1,0); validator 0x6dda4 accepts 0,1,2,6,9")
field(0x06a, "image_with_sound", "IMAGE WITH SOUND", S, "enum", values=ONOFF01, default=0, order=8, section="Drive",
      persist=False, ev="commit table 0x1bd431; validator 0x6e658; zeroed at boot when START SETTING is OFF")
field(0x06b, "lcd_brightness", "LCD BRIGHTNESS", "setup", "u8", rng=(1, 9), order=3, section="Display", conf="HW",
      ev="0x4fa50 -> 0x4d594 (levels 1..9 pick per-body drive values 0x080100b5.. / 0x08010070..); default from "
         "calibration 0x0801001b; main body = 9")
excl(0x06b, 1, "LCD BRIGHTNESS: factory default from calibration byte 0x0801001b; levels map to per-body drive values.")
field(0x06c, "date_format", "DATE FORMAT", "setup", "enum",
      values={0: "Y/M/D (I)", 1: "M/D/Y (I)", 2: "D/M/Y (I)"}, default=0, order=11, section="Date",
      ev="validator 0x6e7d0 (0..2); DATE SETTINGS screens 0xdbfa8..0xdc4e2; 0x1412b6 puts a 4-digit year first "
         "when 0", conf="STATIC")
field(0x06d, "ae_backup_06d", "SCENE/AE BACKUP (I)", "internal", "u8", rng=(0, 255), ui="hidden", conf="I",
      ev="paired with 0x0ec (0x7e4f6 swaps, 0x6f06c restores); +3 indexes 0x1bf180 via 0x1f4c41; default 2",
      help="Internal backup value used when switching modes.")
field(0x06e, "legacy_06e", "LEGACY 0x06E", "internal", "u8", rng=(0, 255), ui="hidden", conf="I",
      ev="only RESTORE DEFAULTS 0x6ef88 writes it via 0x6e57c; no reader", help="Unused by 2.40.")
field(0x06f, "flash_mode", "FLASH", S, "enum", values=FLASH, section="Flash", conf="STATIC",
      ev="validator 0x6e3d8 accepts 0,1,2,3,0x11; default from calibration 0x08010018; value names I (UP key "
         "cycle not traced); main body = 2")
excl(0x06f, 1, "FLASH: factory default from calibration byte 0x08010018.")
field(0x070, "folder_number", "FOLDER NUMBER", "internal", "u16", size=2, rng=(1, 999), body=True, ui="hidden",
      conf="STATIC", ev="0x49c10/0x49cc0/0x49d1c (<999, ++); 0x3d4a4: 999 -> 1",
      help="Number of the current picture folder on the card.")
field(0x072, "file_number", "FILE NUMBER", "internal", "u16", size=2, rng=(0, 9999), body=True, ui="hidden",
      conf="HW", ev="0x47b4e/0x49c10 (9999 wrap); main body = 5601",
      help="Last file number used, for SEQUENTIAL NO.")
excl(0x070, 4, "Folder and file-number counters of this body; copying them can repeat file names.")
field(0x074, "manual_wb_r", "MANUAL WB R GAIN", S, "u16", size=2, rng=(16, 784), body=True, section="Colour",
      conf="STATIC", ev="written by the one-push measurement 0x9b108; clamped 0x10..0x310 in 0x9af40/0x174ab8",
      help="Red gain measured by the last manual white-balance reading on this camera.", ui="hidden")
field(0x076, "manual_wb_b", "MANUAL WB B GAIN", S, "u16", size=2, rng=(16, 784), body=True, section="Colour",
      conf="STATIC", ev="as 0x074, blue gain", help="Blue gain from the last manual white-balance reading.", ui="hidden")
excl(0x074, 4, "Manual WB gains measured through this body's sensor; meaningless on another body.")
field(0x07a, "time_exposure_1x", "TIME EXPOSURE (1.x)", S, "enum", values=TIMEEXP, persist=False, ui="hidden",
      ev="validator 0x6e0e0 (0..4); 1.x row TIME EXPOSURE in dead table 0x1c1240; 0x3d4a4 zeroes it at every boot",
      conf="STATIC", help="Long-exposure item from firmware 1.x; reset at every power-on.")
field(0x07c, "text_density", "DENSITY (TEXT)", S, "enum", values=DENSITY, default=2, section="Text mode",
      ev="text page load 0x5a650 (1 -> DEEP, 3 -> LIGHT, else NORMAL); 0x3d4a4 default 2", conf="STATIC")
field(0x07d, "scene_mode", "SCENE MODE", S, "enum", values=SCENE, default=5, section="Scene",
      ev="validator 0x6ea24 accepts 0..6,8,9,11,12; 5 = TEXT (0x6dda4/0x5a650); 3 forces infinity focus (0x6decc); "
         "labels 1..6 follow the scene label array 0x18da28 (stride 0x24) by index (I); 0x3d4a4 keeps 5 or 8 only",
      conf="I")
field(0x07e, "spot_af_flag", "SPOT AF FLAG", "internal", "enum", values={0: "MULTI", 1: "SPOT"}, ui="hidden",
      ev="0x6de68 writes 1 when focus = 6, 0 when focus = 0; 0x7799a", conf="HW",
      help="Derived from FOCUS; not set directly.")
field(0x07f, "factory_mode_flag", "FACTORY MODE FLAG", "internal", "enum", values=ONOFF01, ui="hidden", body=True,
      ev="service preset 0x3e754 writes it; boot 0x1e7ac -> 0x32a68(flag==1); power-off 0x28560", conf="STATIC",
      help="Factory/service test flag. Leave at 0.")
excl(0x07f, 1, "Service/test-mode flag set only by the factory adjustment flow.")
field(0x080, "init_copy_080", "DEFAULT COPY 0x080..0x099", "internal", "bytes", size=0x1a, ui="hidden", conf="I",
      ev="0x3d4a4 full-defaults path copies 26 bytes from RAM 0x2d2a90; no direct reader in the export",
      help="Unknown internal record.")
excl(0x080, 0x1a, "Unknown 26-byte record copied from RAM at factory reset; not understood.")
field(0x09c, "disp_mode", "DISP MODE", S, "u8", rng=(0, 4), default=0, section="Display", conf="EMU",
      ev="0x6e8c8/0x6e8d4; live copy RAM 0x1bef92 saved back at power-off; %s (five-step cycle)" % H5)
field(0x09d, "disp_aux_09d", "DISPLAY AUX 0x09D (I)", S, "u8", rng=(0, 2), default=1, section="Display", ui="hidden",
      ev="0x4e9cc clamps 0..2; stored in My Settings slot 0x14c", conf="STATIC",
      help="Second display state kept with My Settings; meaning unknown.")
field(0x09e, "auto_power_off", "AUTO POWER OFF", "setup", "enum", size=2,
      values={0: "OFF", 1: "1 MIN.", 2: "5 MIN.", 3: "30MIN."}, order=6, section="Power", conf="HW",
      ev="u16; SETUP commit table 0x1bee8b; 0x290de; default from calibration 0x0801001a")
excl(0x09e, 2, "AUTO POWER OFF: factory default from calibration byte 0x0801001a.")
field(0x0a0, "legacy_0a0", "LEGACY 0x0A0 (u16)", "internal", "u16", size=2, rng=(0, 65535), ui="hidden", conf="I",
      ev="0x3d4a4 / service preset 0x3e754 write 0; no reader", help="Unused by 2.40.")
field(0x0a2, "dirty_shutdown", "DIRTY-SHUTDOWN FLAG", "internal", "u16", size=2, rng=(0, 65535), persist=False,
      ui="hidden", ev="0x3d4a4/0x3d90c set 0xffff at boot; 0x32b8c clears on clean save; %s" % H5, conf="EMU",
      help="Set while the camera runs; tells the next start whether the last power-off was clean.")
excl(0x0a2, 2, "Dirty-shutdown flag, rewritten at every boot.")
field(0x0a4, "digital_zoom", "DIGITAL ZOOM", "setup", "enum", values={1: "ON", 0: "OFF"}, default=0, order=19,
      section="Shooting", ev="SETUP commit table 0x1beeef", conf="HW")
field(0x0a5, "color_space", "COLOR SPACE SET", "setup", "enum", values={0: "sRGB", 1: "Adobe RGB"}, default=0,
      order=17, section="Card", ev="SETUP commit table 0x1beeec; file prefix 0x499a8", conf="HW")
field(0x0a6, "wb_detail", "WB DETAIL", S, "u8", rng=(0, 16), default=5, section="Colour", conf="STATIC",
      ev="detail screen 0x5b764 (17 steps, 0x1c2e68 = 0x10); index into per-body gain tables 0x080103a0/0x080103c4 "
         "(0x174ab8); main body = 5")
field(0x0a7, "movie_size", "MOVIE SIZE", S, "enum", values=MOVIE, default=9, section="Movie",
      ev="movie load 0x5aa38 (9 -> 320); validator 0x6dda4 (1 or 9, else 9)", conf="STATIC")
field(0x0a8, "text_size", "SIZE (TEXT)", S, "enum", values=TEXTSIZE, default=2, section="Text mode",
      ev="text load 0x5a650 (9 -> 3264, else 2048); validator 0x6dda4", conf="STATIC")
field(0x0a9, "disp_aux_0a9", "DISPLAY AUX 0x0A9 (I)", "playback", "u8", rng=(0, 255), ui="hidden", conf="I",
      ev="0x6e8e0/0x6e8ec; 0x29c3a and 0xe4566 set it to 3 with DISP (0x09c) around playback",
      help="Display state used around playback; meaning unknown.")
PB = "PictBridge direct-print preference (I): DPS codes 0x51 paper size / 0x57 layout; readers 0x8bacc, 0x8bbd8, " \
     "0x8d30c, 0x8dc40; reset when marker 0x0da != 'U'"
for o, n in [(0x0ac, "paper_size"), (0x0b0, "pb_0b0"), (0x0b4, "layout"), (0x0b8, "pb_0b8"), (0x0bc, "pb_0bc"),
             (0x0c0, "pb_0c0")]:
    field(o, "print_" + n, "PRINT " + n.upper().replace("_", " "), "playback", "u32", size=4,
          rng=(0, 0xffffffff), ui="hidden", conf="I", ev=PB, help="Direct-print setting remembered for a printer.")
field(0x0c4, "adj_btn_set1", "ADJ BTN. SET.1", "key_custom", "enum", values=ADJ, default=10, order=21,
      section="ADJ lever", conf="HW", ev="SETUP commit 0x6c4bc table 0x1beeb2; 0x3d4a4 default 10 (EXPO.CMP); main = 4")
field(0x0c5, "manual_shutter", "SHUTTER SPEED (M/A)", S, "u8", rng=(0, 42), default=16, section="Exposure",
      ev="0x6f420/0x6f42c (clamp 0x2a; 0x22..0x2a = long exposures, 0x6f99c); 0x3d4a4 0 -> 16", conf="STATIC")
field(0x0c6, "manual_aperture", "APERTURE (M/A)", S, "u8", rng=(0, 12), default=1, section="Exposure",
      ev="0x6f48c/0x6f498 (clamp 12); used in mode 4", conf="STATIC")
field(0x0c7, "batt_display_quant", "BATTERY DISPLAY FLAG (I)", "internal", "u8", rng=(0, 1), ui="hidden", conf="I",
      ev="0x35f04/0x35fb0/0x36074/0x3c2b4 quantise a battery/time count when set", help="Internal flag.")
field(0x0c8, "slow_shutter_limit", "SLOW SHUTTER LMT", S, "enum", values=SLOWLMT, default=0, section="Scene",
      ev="scene pages 0x1c1fb4 row 7 / 0x1c2038 row 6 (map 0x1a91d3); load 0x5a744; table 0x1bd440", conf="STATIC")
field(0x0c9, "shtg_stgs_warng", "SHTG STGS WARNG.", "setup", "enum", values={1: "ON", 0: "OFF"}, default=1,
      order=14, section="Display", ev="SETUP commit table 0x1beee9; 0xf0310", conf="HW")
field(0x0ca, "flag_0ca", "INTERNAL FLAG 0x0CA", "internal", "u8", rng=(0, 1), ui="hidden", conf="I",
      ev="0x51b7c / 0x51bd4 write (param == 0); 0x32358 reads", help="Internal flag.")
field(0x0cb, "mode_0cb", "INTERNAL MODE 0x0CB", "internal", "u8", rng=(0, 255), ui="hidden", conf="I",
      ev="getter 0x694f4 / 0x6f680 (AUTO mode only); passed to 0x8ff00 by 0x54bd0; ==3 blocks WB-BKT",
      help="Internal image-mode byte, default 0.")
field(0x0cc, "vol_settings", "VOL. SETTINGS", "setup", "enum",
      values={0: "MUTE", 1: "LOW (I)", 2: "MEDIUM (I)", 3: "HIGH (I)"}, default=2, order=8, section="Sound",
      ev="SETUP commit table 0x1bee93 (cursor 1..4 -> 0..3); 0 forces BEEP 0; labels are icons {200}..{500}",
      conf="HW")
field(0x0ce, "transient_0ce", "TRANSIENT 0x0CE", "internal", "u8", rng=(0, 255), persist=False, ui="hidden",
      ev="0x3d4a4/0x3d90c set 0 every boot; readers 0x694e8, 0x40ad4, 0x4389c", conf="STATIC",
      help="Reset at power-on.")
field(0x0d0, "my_boot_value_u16", "MY SETTINGS BOOT VALUE (u16)", "internal", "u16", size=2, rng=(0, 65535),
      ui="hidden", conf="I", ev="My Settings load 0x298f0 writes u16(0x0d6); 0x3d408 uses it at boot (0x376fc)",
      help="Internal copy used when My Settings are applied.")
field(0x0d2, "mf_position", "MF POSITION", "internal", "u16", size=2, rng=(0, 65535), body=True, ui="hidden",
      conf="STATIC", ev="My Settings load copies slot 0x14e; 0x3d408 restores the focus position at boot; main = 0x04f9",
      help="Manual-focus lens position restored at power-on.")
excl(0x0d0, 4, "Focus-motor positions of this body's lens (0x0d0..0x0d3).")
field(0x0d4, "start_setting", "START SETTINGS", "setup", "enum", values={3: "OFF", 0: "SETTING1", 1: "SETTING2"},
      default=3, order=27, section="My Settings", conf="HW",
      ev="SETUP commit 0x6c4bc (cursor 2 -> 0, 3 -> 1, else 3); 0x298f0 uses it as the slot index")
field(0x0d5, "my_macro", "MACRO AT START", "internal", "enum", values=ONOFF01, ui="hidden", conf="STATIC",
      ev="My Settings load copies slot 0x146 (macro, RAM 0x1bef78); 0x3c654", help="Macro state applied at start.")
field(0x0d6, "my_boot_value", "MY SETTINGS VALUE 0x0D6", "internal", "u8", rng=(0, 255), ui="hidden", conf="I",
      ev="My Settings load copies slot 0x148 (RAM 0x1bef74, getter 0x6dfa4); 0x29e14/0x73770 apply it at boot",
      help="Internal value applied at start with My Settings.")
field(0x0d7, "usb_connection", "USB CONNECTION", "setup", "enum", values={1: "MASS STR", 0: "ORIGINAL"}, default=0,
      order=15, section="Connection", conf="HW",
      ev="SETUP commit table 0x1bee9b; 0x3d4a4 -> 0x1bd241 = 2 when set; forced 0 if 0x08010017 == 0xff")
field(0x0d8, "beep_saved", "BEEP (SAVED)", "internal", "enum", values={1: "ALL", 2: "SHUTTER"}, ui="hidden",
      conf="HW", ev="SETUP commit writes BEEP here too; 0xe343c restores BEEP from it when VOL leaves mute; main = 2",
      help="Remembers the BEEP choice while the volume is muted.")
field(0x0d9, "print_flag", "PRINT FLAG", "playback", "enum", values=ONOFF01, ui="hidden", conf="I",
      ev="0x8c5ac / 0x8bacc / 0x14be34 (PictBridge group)", help="Direct-print internal flag.")
field(0x0da, "marker_print", "MARKER 'U' (PRINT GROUP)", "internal", "u8", rng=(0, 255), ui="hidden", conf="HW",
      ev="0x3d4a4: != 'U' -> PictBridge words 0x0ac..0x0f0 reset", help="Version marker.")
field(0x0db, "legacy_0db", "LEGACY 0x0DB", "internal", "u8", rng=(0, 255), ui="hidden", conf="I",
      ev="0x3d4a4 default 1; no reader", help="Unused by 2.40.")
for o, n in [(0x0dc, "pb_0dc"), (0x0e0, "pb_0e0"), (0x0e4, "pb_0e4"), (0x0e8, "pb_0e8")]:
    field(o, "print_" + n, "PRINT " + n.upper().replace("_", " "), "playback", "u32", size=4, rng=(0, 0xffffffff),
          ui="hidden", conf="I", ev=PB, help="Direct-print setting remembered for a printer.")
field(0x0ec, "ae_backup_0ec", "SCENE/AE BACKUP 0x0EC (I)", "internal", "u8", rng=(0, 255), ui="hidden", conf="I",
      ev="0x0f = none; 0x7e4f6 / 0x6f06c / 0x29c94 swap it with 0x06d", help="Internal backup value.")
field(0x0ed, "enlge_photo_icon", "ENLGE PHOTO ICON", "setup", "enum", values={1: "ON", 0: "OFF"}, default=0,
      order=16, section="Display", ev="SETUP commit table 0x1bee98; 0x803a0..0x815ee", conf="HW")
field(0x0ef, "print_flags", "PRINT FLAGS", "playback", "bitfield", ui="hidden", conf="I",
      bits={2: "flag 2 (0x8d30c)", 3: "flag 3 (0x8d30c)", 5: "flag 5 (0x8bbd8)", 6: "flag 6 (0x8bbd8)",
            7: "flag 7 (0x8bbd8), default set"}, ev=PB + "; default 0x80", help="Direct-print option bits.")
field(0x0f0, "print_0f0", "PRINT 0x0F0", "playback", "u32", size=4, rng=(0, 0xffffffff), ui="hidden", conf="I",
      ev="0x4e010 stores RAM 0x2c5260; 0x8bbd8 restores it", help="Direct-print value.")
field(0x0f4, "rate_ctrl_0f4", "JPEG RATE CONTROL 0x0F4", "internal", "u8", rng=(0, 255), body=True, ui="hidden",
      conf="I", ev="0x3d4a4 zeroes 0x0f4..0x0fb together", help="Internal JPEG size-control state.")
field(0x0f5, "rate_ctrl_bias", "JPEG RATE-CONTROL BIAS", "internal", "bytes", size=7, body=True, ui="hidden",
      conf="HW", ev="0x10f7f4/0x10f85c learn per quality; %s; main body 40 43 42 at 0x0f8" % H5,
      help="Compression bias the camera learns while shooting.")
excl(0x0f4, 8, "JPEG rate-control state learned by this body while shooting (0x0f4..0x0fb).")
field(0x0fc, "pic_aspect", "PIC ASPECT (3:2)", S, "enum", values=ASPECT, default=0, order=1, section="Image",
      ev="PIC QUALITY/SIZE table 0x1bd3ef (RAW3:2, F3:2, N3:2 -> 1); 0x6f6d0 applies it only at size 9",
      conf="STATIC")

# ---------------------------------------------------------------------------------------------------- My Settings
MY = [  # (slot base, size, name, label, values|range, from)
    (0x102, 1, "cont_mode", "CONT. MODE", CONT, 0x052), (0x104, 1, "exposure_comp", "EXPOSURE COMP.", EV, 0x053),
    (0x106, 1, "white_balance", "WHITE BALANCE", WB, 0x054), (0x108, 1, "pic_size", "PIC SIZE", SIZE, 0x069),
    (0x10a, 1, "pic_quality", "PIC QUALITY", QUALITY, 0x068), (0x10c, 1, "focus", "FOCUS", FOCUS, 0x058),
    (0x10e, 1, "photometry", "PHOTOMETRY", METER, 0x050), (0x110, 1, "iso", "ISO SETTING", ISO, 0x059),
    (0x112, 1, "sharpness_1x", "SHARPNESS (1.x)", SHARP1X, 0x060),
    (0x114, 1, "time_exposure_1x", "TIME EXPOSURE (1.x)", TIMEEXP, 0x07a),
    (0x116, 1, "date_imprint", "DATE IMPRINT", DATEIMP, 0x055), (0x118, 1, "flash_mode", "FLASH", FLASH, 0x06f),
    (0x11a, 1, "text_density", "DENSITY (TEXT)", DENSITY, 0x07c),
    (0x11c, 1, "slow_shutter_limit", "SLOW SHUTTER LMT", SLOWLMT, 0x0c8),
    (0x11e, 1, "movie_size", "MOVIE SIZE", MOVIE, 0x0a7), (0x120, 1, "text_size", "SIZE (TEXT)", TEXTSIZE, 0x0a8),
    (0x144, 1, "scene_mode", "SCENE MODE", SCENE, 0x07d), (0x146, 1, "macro", "MACRO", ONOFF01, None),
    (0x148, 1, "boot_value", "VALUE 0x148 (I)", (0, 255), None), (0x14a, 1, "disp_mode", "DISP MODE", (0, 4), 0x09c),
    (0x14c, 1, "disp_aux", "DISPLAY AUX (I)", (0, 2), 0x09d),
    (0x156, 1, "img_set", "IMG SET", IMGSET, 0x030),
    (0x158, 1, "setting1_contrast", "SETTING1 CONTRAST", LEVEL5, 0x031),
    (0x15a, 1, "setting1_sharpness", "SETTING1 SHARPNESS", LEVEL5, 0x032),
    (0x15c, 1, "setting1_color_depth", "SETTING1 COLOR DEPTH", LEVEL5, 0x033),
    (0x15e, 1, "setting2_contrast", "SETTING2 CONTRAST", LEVEL5, 0x034),
    (0x160, 1, "setting2_sharpness", "SETTING2 SHARPNESS", LEVEL5, 0x035),
    (0x162, 1, "setting2_color_depth", "SETTING2 COLOR DEPTH", LEVEL5, 0x036),
    (0x164, 1, "wb_detail", "WB DETAIL", (0, 16), 0x0a6), (0x168, 1, "pic_aspect", "PIC ASPECT (3:2)", ASPECT, 0x0fc),
    (0x16a, 1, "image_with_sound", "IMAGE WITH SOUND", ONOFF01, 0x06a),
    (0x199, 1, "sepia_contrast_230", "LEGACY 2.30 CONTRAST 0x195", LEVEL5, 0x195),
    (0x19b, 1, "sepia_sharpness_230", "LEGACY 2.30 SHARPNESS 0x196", LEVEL5, 0x196),
    (0x19d, 1, "bw_contrast", "B&W CONTRAST", LEVEL5, 0x197), (0x19f, 1, "bw_sharpness", "B&W SHARPNESS", LEVEL5, 0x198),
    (0x1ab, 1, "te_contrast", "B&W (TE) CONTRAST", LEVEL5, 0x1a7),
    (0x1ad, 1, "te_sharpness", "B&W (TE) SHARPNESS", LEVEL5, 0x1a8),
    (0x1af, 1, "te_tone", "B&W (TE) TONE", TONE, 0x1a9), (0x1b1, 1, "te_density", "B&W (TE) DENSITY", LEVEL5, 0x1aa),
]
MYEV = "My Settings save 0x2978c / load 0x298f0 (slot = START SETTING 0x0d4, interleaved stride 2), defaults 0x296a8"
for slot in (0, 1):
    pg = "my%d" % (slot + 1)
    for i, (base, sz, n, l, vals, src) in enumerate(MY):
        o = base + slot
        kw = dict(values=vals) if isinstance(vals, dict) else dict(rng=vals)
        typ = "enum" if isinstance(vals, dict) else "u8"
        hidden = n in ("sharpness_1x", "time_exposure_1x", "boot_value", "disp_aux", "sepia_contrast_230",
                       "sepia_sharpness_230")
        field(o, "%s_%s" % (pg, n), "%s %s" % ("SETTING%d" % (slot + 1), l), pg, typ, order=i + 1,
              ev=MYEV + ("; copy of %s" % hx(src) if src is not None else ""), conf="STATIC",
              ui="hidden" if hidden else None,
              help="My Setting %d copy of %s." % (slot + 1, l) if hidden else "", **kw)
    # scene flash pair: 0x124 + 2*scene + slot
    for j in range(2):
        field(0x124 + 2 * j + slot, "%s_scene_flash_%s" % (pg, "ab"[j]), "SETTING%d FLASH (SCENE %d)" % (
            slot + 1, (5, 8)[j]), pg, "enum", values=FLASH, ev=MYEV + "; copy of %s" % hx(0x047 + j), conf="STATIC",
              order=40 + j)
    for base, n, l, src, h in [(0x14e, "mf_position", "MF POSITION", "0x0d2", "focus-motor position"),
                               (0x152, "boot_value_u16", "VALUE 0x152 (I)", "slot 0x148", "copy of slot byte 0x148"),
                               (0x16c, "manual_wb_r", "MANUAL WB R GAIN", "0x074", "manual WB red gain"),
                               (0x170, "manual_wb_b", "MANUAL WB B GAIN", "0x076", "manual WB blue gain")]:
        body = n in ("mf_position", "manual_wb_r", "manual_wb_b")
        field(base + 2 * slot, "%s_%s" % (pg, n), "SETTING%d %s" % (slot + 1, l), pg, "u16", size=2, rng=(0, 65535),
              body=body, ui="hidden", ev=MYEV + "; u16 per slot, %s" % src, conf="STATIC",
              order=50, help="My Setting %d %s." % (slot + 1, h))
excl(0x14e, 4, "My Settings focus-motor positions (lens-specific).")
excl(0x16c, 8, "My Settings manual WB gains (sensor-specific).")

# ---------------------------------------------------------------------------------------------------- 2.x additions
field(0x18c, "dial_direction", "DIAL DIRECTION", "key_custom", "enum", values={0: "LEFT", 1: "RIGHT"}, default=0,
      order=4, section="Buttons", conf="HW", ev="SETUP commit table 0x1beef9; marker group 0x18f (2.01)")
field(0x18d, "adj_shtr_btn_set", "ADJ SHTR BTN SET", "key_custom", "enum", values={0: "ON", 1: "OFF"}, default=1,
      order=25, section="ADJ lever", conf="HW", ev="SETUP commit table 0x1beeff (cursor default 2)")
field(0x18e, "synchro_monitor", "SYNCHRO. MONITOR", "setup", "enum", values={0: "ON", 1: "OFF"}, default=0, order=5,
      section="Display", conf="HW", ev="SETUP commit table 0x1beefc; 0x72a82")
field(0x18f, "marker_201", "MARKER 'U' (2.01 GROUP)", "internal", "u8", rng=(0, 255), ui="hidden", conf="HW",
      ev="0x3d4a4: 0x18f != 'U' or 0x190 != 0xAA -> 0x18c..0x18e, 0x193 reset", help="Version marker.")
field(0x190, "marker_201_aa", "MARKER 0xAA (2.01 GROUP)", "internal", "u8", rng=(0, 255), ui="hidden", conf="HW",
      ev="0x3d4a4 pairs it with 0x18f", help="Version marker.")
field(0x191, "adj_btn_set3", "ADJ BTN. SET.3", "key_custom", "enum", values=ADJ, default=5, order=23,
      section="ADJ lever", conf="HW", ev="SETUP commit table 0x1beecc; 0x3d4a4 default 5 (ISO)")
field(0x192, "adj_btn_set4", "ADJ BTN. SET.4", "key_custom", "enum", values=ADJ, default=0, order=24,
      section="ADJ lever", conf="HW", ev="SETUP commit table 0x1beed9; 0x3d4a4 default 0 (OFF)")
field(0x193, "raw_jpeg_setting", "RAW/JPEG SETTING", "setup", "enum", values={0: "FINE", 1: "NORMAL", 2: "N640"},
      default=0, order=28, section="Shooting", conf="HW", ev="SETUP commit table 0x1bef02")
field(0x194, "cl_bkt_bw_te", "CL-BKT B&W (TE)", "setup", "enum", values={0: "ON", 1: "OFF"}, default=0, order=29,
      section="Shooting", conf="HW", ev="SETUP commit table 0x1bef06; 0x6ebd0 / 0x6fa28 / 0x54bd0")
field(0x195, "sepia_contrast_230", "LEGACY 2.30 CONTRAST 0x195", "internal", "enum", values=LEVEL5, ui="hidden",
      conf="STATIC", ev="2.30 marker group 0x1a1; only migration and My Settings copy it; no 2.40 reader",
      help="Contrast byte from firmware 2.30 (sepia); unused by 2.40.")
field(0x196, "sepia_sharpness_230", "LEGACY 2.30 SHARPNESS 0x196", "internal", "enum", values=LEVEL5, ui="hidden",
      conf="STATIC", ev="as 0x195", help="Sharpness byte from firmware 2.30; unused by 2.40.")
field(0x197, "bw_contrast", "B&W CONTRAST", S, "enum", values=LEVEL5, default=3, order=5, section="Image", conf="HW",
      ev="detail screen 0x5b7e4 mode 3; reader 0x92cc8; 0x3d4a4 fixes 0 or >5 to 3; main body = 5")
field(0x198, "bw_sharpness", "B&W SHARPNESS", S, "enum", values=LEVEL5, default=3, order=5, section="Image",
      conf="HW", ev="detail screen mode 3; reader 0x91268; main body = 4")
field(0x1a1, "marker_230", "MARKER 'U' (2.30 GROUP)", "internal", "u8", rng=(0, 255), ui="hidden", conf="HW",
      ev="0x3d4a4: != 'U' -> 0x194..0x198 reset", help="Version marker.")
field(0x1a2, "marker_230_fix", "MARKER 'U' (2.30 FIX)", "internal", "u8", rng=(0, 255), ui="hidden", conf="HW",
      ev="0x3d4a4: != 'U' -> clamp 0x195..0x198, My Settings copies 0x199..0x1a0 = 3", help="Version marker.")
field(0x1a3, "marker_240", "MARKER 'U' (2.40 GROUP)", "internal", "u8", rng=(0, 255), ui="hidden", conf="HW",
      ev="0x3d4a4: != 'U' -> 0x1a5..0x1b2 reset", help="Version marker.")
field(0x1a5, "flash_expo_comp", "FLASH EXPO. COMP.", S, "enum", values=FLASHCOMP, default=9, order=13,
      section="Flash", conf="HW",
      ev="still row 13 commit table 0x1bd471 (cursor 1..13 -> 3..15); validator 0x6fa90 (3..15, bad -> 9); "
         "ADJ item 0x15 / display 0x80288. Corrects map.json 'te_option'.")
field(0x1a6, "info_disp", "INFO DISP", "setup", "enum", values={0: "ON", 1: "OFF"}, default=1, order=30,
      section="Display", conf="HW", ev="SETUP commit table 0x1bef06; consumer 0x72a82")
field(0x1a7, "te_contrast", "B&W (TE) CONTRAST", S, "enum", values=LEVEL5, default=3, order=5, section="Image",
      ev="detail screen 0x5b7e4 mode 4; reader 0x92cc8", conf="STATIC")
field(0x1a8, "te_sharpness", "B&W (TE) SHARPNESS", S, "enum", values=LEVEL5, default=3, order=5, section="Image",
      ev="detail screen mode 4; reader 0x91268", conf="STATIC")
field(0x1a9, "te_tone", "B&W (TE) TONE", S, "enum", values=TONE, default=6, order=5, section="Image", conf="STATIC",
      ev="detail screen 0x5b89c (UI 1..5 -> 6,1,2,3,4; labels SEPIA/RED/GREEN/BLUE/PURPLE 0x1928a4..0x1929a0); "
         "0x8ff0e accepts 1..6")
field(0x1aa, "te_density", "B&W (TE) DENSITY", S, "enum", values=LEVEL5, default=3, order=5, section="Image",
      ev="detail screen mode 4; 0x8ff0e (1..5, bad -> 3) indexes table 0x1f4c88", conf="STATIC")

# ---------------------------------------------------------------------------------------------------- service log
field(0x2f8, "error_log", "ERROR LOG", "internal", "bytes", size=0x30, body=True, ui="hidden", conf="STATIC",
      ev="0x38610 records error codes and three time stamps; shown by 0x1f1b8 (RIGHT+WIDE screen); "
         "0x3d4a4 clears 0x2f8..0x327", help="Service error history of this camera.")
excl(0x2f8, 0x30, "Service error log of this body.")
field(0x328, "error_log_legacy_tail", "ERROR LOG TAIL (OLDER FIRMWARE)", "internal", "bytes", size=0x10, body=True,
      ui="hidden", conf="HW",
      ev="no 2.40 reference (2.40 log ends at 0x327); main camera block holds two BCD time stamps here "
         "(14 17 14 31 10 05 20 00 / 17 17 14 31 10 05 20 00), so an earlier firmware wrote a longer log",
      help="Leftover service time stamps from older firmware.")
excl(0x328, 0x10, "Leftover service log time stamps of this body (not in the free tail despite earlier notes).")

# ---------------------------------------------------------------------------------------------------- Koma
K = "koma"
KEV = "tools/koma/linker.py (SET_MAGIC 0x1b8b0, version 0x1b8b4 = 1, ROW_BYTES 0x1b8b5..0x1b8cf)"
field(0x800, "rcp1_store_legacy", "RCP1 RECIPE STORE (KOMA <= 0.1)", "recipe", "bytes", size=0xb0, ui="hidden",
      conf="STATIC", ev="tools/koma/features/recipe_slots.py ('RCP1', active byte 0x1b806); not in the KOMA03 set",
      help="Recipe slots of the early Koma recipe-slots feature; unused by Koma 0.3.")
field(0x8b0, "koma_magic", "KOMA MAGIC", K, "bytes", size=4, ui="hidden", ev=KEV, conf="STATIC",
      help="Marks that Koma settings are present.")
field(0x8b4, "koma_version", "KOMA SETTINGS VERSION", K, "u8", rng=(0, 255), ui="hidden", ev=KEV, conf="STATIC",
      help="Layout version of the Koma settings (1).")
for o, n, l, vals, dflt, sec, src in [
        (0x8b5, "snap_dist", "SNAP DIST", ["1M", "1.5M", "2M", "2.5M", "5M", "INF"], 3, "Focus",
         "snapdist.py"),
        (0x8b6, "full_snap", "FULL SNAP", ["OFF", "ON"], 0, "Focus",
         "fullpress.py"),
        (0x8b7, "auto_iso_max", "AUTO-ISO MAX", ["STOCK", "200", "400", "800", "1600"], 0, "Exposure",
         "autoiso.py"),
        (0x8b8, "auto_iso_slow_shutter", "SLOW SHUTTER", ["STOCK", "1/250", "1/125", "1/60", "1/30", "1/15", "1/8",
                                                           "1/4", "1/2"], 0, "Exposure",
         "autoiso.py"),
        (0x8b9, "highlight_alert", "HIGHLIGHT ALRT", ["OFF", "ON"], 0, "Display",
         "hlalert.py"),
        (0x8ba, "long_exp_limit", "LONG EXP LIMIT", ["180S", "240S"], 0, "Exposure",
         "longexp.py")]:
    field(o, n, l, K, "enum", values=dict(enumerate(vals)), default=dflt, order=o - 0x8b4, section=sec,
          ev="tools/koma/features/%s ROWS (byte %s, default %d)" % (src, "0x1b%03x" % o, dflt), conf="STATIC")
field(0x8bb, "menu_layout", "MENU LAYOUT", K, "enum", values={0: "5-TAB", 1: "STOCK + KOMA", 2: "GR II"}, order=7,
      section="System", conf="I", ev="proposed Koma settings layout")
field(0x8bc, "koma_features", "KOMA FEATURES", K, "enum", values={0: "FULL", 1: "ESSENTIALS"}, order=8,
      section="System", conf="I", ev="proposed Koma settings layout")
SIO = "tools/koma/features/settingsio.py"
field(0x8bd, "save_to_card", "SAVE TO CARD", K, "enum", values={0: "NO", 1: "YES", 2: "SAVED", 3: "FAILED"},
      body=True, ui="hidden", conf="EMU", ev=SIO + " (row byte 0x1b8bd; on_change writes /ATA1/PARAM.DAT; reset to 0 at boot)",
      help="An on-camera action that writes the settings to the card. Not edited here; it resets to NO at every boot.")
field(0x8be, "stallfix_flags", "STALL FIX FLAGS", K, "u8", rng=(0, 255), body=True, ui="hidden", conf="STATIC",
      ev="tools/koma/features/stallfix.py (FLAGS 0x1b8be; high nibble 0xA = valid, low nibble = fix mask: 1 a201 no-op, "
         "2 no auto-retract timer, 4 LCD-mutex leak guard, 8 panel on at arm; any other value = default 0xd)",
      help="Diagnostic switches for the tether stall fixes. Set over USB with -KSET 2238,<value> (173 = default, 160 = all off).")
field(0x8bf, "focus_peaking", "PEAKING", K, "enum", values={0: "OFF", 1: "RED", 2: "YELLOW"}, default=0, order=23,
      section="Display", conf="EMU",
      ev="tools/koma/features/peaking.py ROWS (byte 0x1b8bf, default 0); read once per live-view frame by the VD-task "
         "hook 0x95af4",
      help="Outlines what is in focus on the screen.")
field(0x8d0, "settingsio_magic", "SETTINGS FILE MARKER 'KSIO'", K, "bytes", size=4, body=True, ui="hidden", conf="EMU",
      ev=SIO + " (KSIO sub-header at 0x1b8d0, written every boot)",
      help="Marks the block as written by a camera with settings save and load.")
field(0x8d4, "settingsio_version", "SETTINGS FILE VERSION", K, "u8", rng=(0, 255), body=True, ui="hidden", conf="EMU",
      ev=SIO + " (KSIO_VERSION = 1)", help="Format version of the camera's settings save and load.")
field(0x8d5, "settingsio_tag", "FIRMWARE TAG", K, "bytes", size=7, body=True, ui="hidden", conf="EMU",
      ev=SIO + " (7 bytes copied every boot from the MakerNote version format at 0x112578, e.g. 'Rev024M')",
      help="The firmware this camera was running when it last saved these settings.")
field(0x8dc, "settingsio_status", "LAST IMPORT", K, "enum", values={0: "NONE", 1: "OK", 2: "BAD", 3: "OK, tag differed"}, body=True,
      ui="hidden", conf="EMU", ev=SIO + " (0x1b8dc, set on each PARAM.DAT import attempt)",
      help="Whether the last settings file from the card was accepted.")
excl(0x8bd, 1, "SAVE TO CARD action state of this body's menu; never imported (the camera keeps it, the init resets it each boot).")
excl(0x8d0, 48, "Settings-io sub-header (KSIO magic, version, firmware tag, last-import status) and its reserve 0x8dd..0x8ff: "
                "describes the body, not the user's settings.")
sys.path.insert(0, str(HERE.parent))
try:
    from koma.features import bwrecipes as B          # noqa: E402
    BROWS = [(r["byte"] - 0x1b000, r["id"], r["label"], r["options"], r["default"], r.get("variants")) for r in B.ROWS]
    NREC, REC, RECLEN = B.N, B.REC - 0x1b000, B.RECLEN
except Exception as e:                                  # pragma: no cover - fallback keeps the map buildable
    raise SystemExit("cannot import tools/koma/features/bwrecipes.py: %s" % e)
for i, (o, rid, l, opts, dflt, var) in enumerate(BROWS):
    ev = "tools/koma/features/bwrecipes.py ROWS id %s (byte 0x1b%03x)" % (rid, o)
    if var:
        ev += "; label/options vary with the active recipe type (selector 0x1b908): " + "; ".join(
            "%s=%s" % (a[0], "/".join(a[1])) for a in var["alts"])
    field(o, "recipe_" + rid, l, K, "enum", values=dict(enumerate(opts)), default=dflt, order=9 + i,
          section="Recipe" if rid == "rcp_active" else "Edit recipe", ev=ev, conf="STATIC")
ST = 0x900
RH = "tools/koma/features/bwrecipes.py 'RCP6' store header at 0x1b900"
field(ST, "rcp6_magic", "RECIPE STORE MAGIC", "recipe", "bytes", size=4, ui="hidden", ev=RH + " (+0 'RCP6')",
      conf="STATIC", help="Marks the recipe store.")
for k, n, l, rng in [(4, "version", "STORE VERSION", (0, 255)), (5, "edit_owner", "EDIT-ROW OWNER ID", (0, 20)),
                     (6, "last_applied", "LAST APPLIED ID", (0, 20)), (7, "count", "RECORD COUNT", (0, 255)),
                     (8, "active_type", "ACTIVE TYPE", (0, 2)), (9, "select_colour", "SELECT COLOUR", (0, 6)),
                     (10, "select_range", "SELECT RANGE", (0, 1)), (11, "grain", "GRAIN (PUBLISHED)", (0, 3)),
                     (12, "vignette", "VIGNETTE (PUBLISHED)", (0, 3)), (13, "fx_owner", "FX ROW OWNER", (0, 20))]:
    field(ST + k, "rcp6_" + n, l, "recipe", "u8", rng=rng, ui="hidden", conf="STATIC", ev=RH + " (+%d)" % k,
          help="Recipe store header field, rewritten by Koma.")
field(ST + 14, "rcp6_pad", "STORE HEADER PAD", "recipe", "bytes", size=2, ui="hidden", conf="STATIC",
      ev=RH + " (+14..15 = 0)", help="Padding.")
for i in range(NREC):
    nm = B.RECIPES[i]["name"]
    field(REC + RECLEN * i, "recipe_record_%02d" % (i + 1), "RECIPE %d %s" % (i + 1, nm), "recipe", "bytes",
          size=RECLEN, ev="bwrecipes.py records: p0..p8, kind, 0xa5, xor(bytes 0..9)^0x5a", conf="STATIC",
          order=i + 1, help="Stored parameters of recipe %s." % nm)
field(REC + RECLEN * NREC, "recipe_fx_table", "RECIPE FX TABLE", "recipe", "bytes", size=NREC, conf="STATIC",
      ev="bwrecipes.py FX table at 0x1ba00: one byte per recipe, 0x50 | grain | vignette << 2", order=NREC + 1)

field(0xffc, "checksum", "CHECKSUM", "internal", "u32", size=4, rng=(0, 0xffffffff), ui="hidden", conf="HW",
      ev="0x3d9b8 sums big-endian u16 words over 0x000..0xffb into a u32; 0x4949c stores it with str (32-bit) at "
         "0xffc; verified on the main camera's block (IROM.DAT record 0x2a80)",
      help="Integrity sum; the editor recomputes it.")
excl(0xffc, 4, "Checksum; recomputed after any edit, never merged.")

for o, why in [(0x038, "1.x group marker"), (0x0da, "print group marker"), (0x18f, "2.01 group marker"),
               (0x190, "2.01 group marker (0xAA)"), (0x1a1, "2.30 group marker"), (0x1a2, "2.30 fix marker"),
               (0x1a3, "2.40 group marker")]:
    excl(o, 1, "Firmware-version marker (%s): copy only between bodies on the same firmware; a missing 'U' "
               "triggers migration defaults at boot." % why)
excl(0x0ce, 1, "Transient, reset at every boot.")
excl(0x8b0, 5, "Koma header (magic + layout version) is written by the Koma core at boot; merge only if both bodies "
               "run the same Koma settings version.")
excl(0x900, 16, "Koma recipe-store header (owner ids, published values); rewritten by Koma at boot and per shot.")


# ---------------------------------------------------------------------------------------------------- plain-language pass
# Owner-facing help and labels. The editor shows these; the inline strings above are engineering notes that this
# table replaces at build time (hidden fields keep their terse engineering help).
HELP = {
    "img_set": "The look of your pictures: a colour style, black and white, toned black and white, or one of your own SETTING1 or SETTING2 looks.",
    "setting1_contrast": "How punchy your SETTING1 look is. Five steps; 3 is the standard.",
    "setting1_sharpness": "How crisp the edges are in your SETTING1 look. Five steps; 3 is the standard.",
    "setting1_color_depth": "How strong the colours are in your SETTING1 look. Five steps; 3 is the standard.",
    "setting2_contrast": "How punchy your SETTING2 look is. Five steps; 3 is the standard.",
    "setting2_sharpness": "How crisp the edges are in your SETTING2 look. Five steps; 3 is the standard.",
    "setting2_color_depth": "How strong the colours are in your SETTING2 look. Five steps; 3 is the standard.",
    "af_aux_light": "Lights the lamp on the front of the camera so autofocus can work on dark subjects. Turn it off where a light would be distracting.",
    "zoom_btn_set": "What the zoom lever (T/W) changes while digital zoom is off. Assign it to exposure compensation or white balance for quick access, or OFF to leave it unused.",
    "scene_flash_a": "The flash mode the camera remembers for the TEXT scene.",
    "scene_flash_b": "The flash mode the camera remembers for one of the other scenes.",
    "photometry": "How the camera measures the light: across the whole frame, weighted to the centre, or on a small spot. Use SPOT for a subject lit very differently from its surroundings.",
    "cont_mode": "Keeps shooting pictures while you hold the shutter button. The camera switches this back to OFF when it starts, unless a My Setting restores it.",
    "exposure_comp": "Makes pictures brighter (+) or darker (-) than the camera's own reading. Use it when a scene is very bright or dark.",
    "white_balance": "Corrects the colour for the kind of light you are shooting in, so whites look white.",
    "date_imprint": "Prints the date, or the time, onto the picture itself.",
    "language": "The language of the menus and messages.",
    "focus": "How the camera focuses: autofocus over the frame or on a spot, manual focus, a fixed close SNAP distance, or infinity for distant scenes.",
    "iso": "How sensitive the camera is to light. AUTO lets the camera choose. Higher numbers let you shoot in dimmer light at faster shutter speeds, with more noise.",
    "auto_bracket": "Takes three pictures in a row with slightly different exposure, white balance or colour, so you can pick the best one. It returns to OFF when the camera starts.",
    "adj_btn_set1": "The first setting the ADJ lever offers for quick changes. Assign the setting you change most often, or OFF to leave the slot empty.",
    "adj_btn_set2": "The second setting the ADJ lever offers for quick changes. Assign it a setting you change often, or OFF to leave the slot empty.",
    "adj_btn_set3": "The third setting the ADJ lever offers for quick changes. Assign it a setting you change often, or OFF to leave the slot empty.",
    "adj_btn_set4": "The fourth setting the ADJ lever offers for quick changes. Assign it a setting you change often, or OFF to leave the slot empty.",
    "adj_shtr_btn_set": "With ON, half-pressing the shutter button confirms a change made with the ADJ lever or the zoom lever, so you can go straight on to shoot.",
    "dial_direction": "Which way you turn the up-down dial and ADJ lever to raise a value.",
    "beep_sound": "Which actions make a beep: everything, or only the shutter. Muting the volume in VOL. SETTINGS silences it too.",
    "video_out_mode": "The TV standard used for the video cable: NTSC or PAL. Match it to your TV.",
    "lcd_confirm": "How long the picture you just took stays on the screen. OFF skips the instant review.",
    "sequential_no": "ON keeps file numbers counting up across memory cards. OFF starts again at 0001 on each new card.",
    "pic_quality": "How pictures are saved: RAW (a DNG file plus a JPEG), or a FINE or NORMAL JPEG. FINE keeps more detail and uses more card space.",
    "pic_size": "How large the picture is. Bigger sizes keep more detail and take more card space.",
    "pic_aspect": "Crops full-size pictures to a 3:2 shape, like 35 mm film. It only applies at the largest picture size.",
    "image_with_sound": "Records up to 8 seconds of sound after each picture. It is switched off when the camera starts unless a My Setting restores it.",
    "lcd_brightness": "How bright the screen is, from 1 (dim) to 9 (bright).",
    "date_format": "The order of year, month and day wherever the date is shown or printed.",
    "flash_mode": "How the flash fires, chosen with the flash button: off, automatic, red-eye reduction, always on, or slow sync.",
    "text_density": "How dark text comes out when shooting in TEXT mode.",
    "scene_mode": "Which scene the SCENE position of the mode dial uses, such as TEXT.",
    "disp_mode": "Which shooting-screen layout the DISP. button last selected. The camera remembers it between sessions.",
    "auto_power_off": "Turns the camera off after this long without use, to save the battery.",
    "digital_zoom": "Lets the zoom lever enlarge the centre of the picture digitally once optical zoom runs out.",
    "color_space": "The range of colours saved in JPEGs. sRGB suits most uses; Adobe RGB is wider and meant for editing and printing. Adobe RGB file names start with an underscore.",
    "wb_detail": "A fine colour-temperature adjustment, used when white balance is set to DETAIL.",
    "movie_size": "The frame size of movies. The larger size looks better and uses more card space.",
    "text_size": "The picture size used in TEXT mode.",
    "manual_shutter": "The shutter speed used in manual (M) exposure. You normally change it with the dial while shooting.",
    "manual_aperture": "The aperture used in A and M exposure. You normally change it with the dial while shooting.",
    "slow_shutter_limit": "The slowest shutter speed the camera may choose in scene modes. A faster limit helps avoid blurry pictures in dim light.",
    "shtg_stgs_warng": "Shows a warning when the camera starts with shooting settings changed from their normal values, so you do not shoot with them by accident.",
    "vol_settings": "How loud the beeps and playback sound are. MUTE silences the camera.",
    "start_setting": "Loads SETTING1 or SETTING2 from My Settings each time the camera starts. OFF starts with the settings you last used.",
    "usb_connection": "How the camera appears to a computer: MASS STR as a plain card reader, or ORIGINAL for Ricoh software and printers.",
    "enlge_photo_icon": "Makes the icons on the shooting screen larger.",
    "synchro_monitor": "With ON, the screen stays dark until you half-press the shutter button.",
    "raw_jpeg_setting": "The quality of the JPEG saved alongside each RAW picture.",
    "cl_bkt_bw_te": "With ON, colour bracketing also records a toned black-and-white picture.",
    "info_disp": "While SYNCHRO. MONITOR keeps the screen dark, ON still shows the shooting information.",
    "flash_expo_comp": "Makes the flash stronger (+) or weaker (-). Use it when flash pictures come out too bright or too dark.",
    "bw_contrast": "How punchy black-and-white pictures look. Five steps; 3 is the standard.",
    "bw_sharpness": "How crisp the edges are in black-and-white pictures. Five steps; 3 is the standard.",
    "te_contrast": "How punchy toned black-and-white pictures look. Five steps; 3 is the standard.",
    "te_sharpness": "How crisp the edges are in toned black-and-white pictures. Five steps; 3 is the standard.",
    "te_tone": "The colour of the toning on toned black-and-white pictures.",
    "te_density": "How strong the toning colour is. Five steps; 3 is the standard.",
    # Koma (wording from the Koma feature modules)
    "snap_dist": "The distance the camera focuses at in SNAP focus. Pick the distance you usually shoot street subjects from.",
    "full_snap": "With ON, a quick full press of the shutter focuses at the SNAP distance and shoots at once, instead of waiting for autofocus.",
    "auto_iso_max": "The highest ISO that AUTO ISO may choose. STOCK keeps the camera's own limit.",
    "auto_iso_slow_shutter": "The slowest shutter speed AUTO ISO accepts before it raises the ISO instead. STOCK keeps the camera's own choice.",
    "highlight_alert": "Blinks the parts of the picture that are blown out, so you can lower the exposure.",
    "long_exp_limit": "The longest exposure allowed in M mode: the stock 180 seconds, or 240 seconds.",
    "menu_layout": "How the MENU button is organised: Koma's five tabs, the stock menu with Koma rows added, or a layout modelled on the GR II.",
    "koma_features": "FULL turns on every Koma feature. ESSENTIALS keeps only SDHC card support and small quality-of-life fixes.",
    "recipe_rcp_active": "The recipe applied to your next pictures. OFF uses the camera's normal look.",
    "recipe_rp_curve": "The basic tone curve of the recipe you are editing.",
    "recipe_rp_contrast": "Overall contrast of the recipe you are editing.",
    "recipe_rp_key": "Makes the recipe you are editing lighter (high key) or darker (low key) overall.",
    "recipe_rp_shadow": "Contrast in the dark areas of the recipe you are editing.",
    "recipe_rp_hilite": "Contrast in the bright areas of the recipe you are editing.",
    "recipe_rp_p5": "Colour strength, or the colour filter for black and white. The name and choices change with the recipe type.",
    "recipe_rp_p6": "Toning, warmth or colour range, depending on the recipe type.",
    "recipe_rp_p7": "Toning strength, tint or saturation, depending on the recipe type.",
    "recipe_rp_sharp": "Sharpness of the recipe you are editing.",
    "recipe_rp_save": "Copies the recipe you are editing into one of the three CUSTOM slots, then returns to OFF.",
    "recipe_rp_reset": "Choose YES to put the recipe you are editing back to its original values, then it returns to NO.",
    "recipe_rp_grain": "Adds film-like grain to the recipe you are editing.",
    "recipe_rp_vig": "Darkens the corners of the recipe you are editing, like an old lens.",
    "recipe_fx_table": "The grain and vignette amounts stored for each recipe. Edited through the recipe rows.",
}
LABEL = {"scene_flash_a": "FLASH (TEXT SCENE)", "manual_shutter": "SHUTTER SPEED", "manual_aperture": "APERTURE"}
SET_TEXT = {"my1": "Saved as part of MY SETTINGS 1.", "my2": "Saved as part of MY SETTINGS 2."}
# camera-menu order and section for the shooting page (contiguous per section)
SHOOT_ORDER = [("Image", ["pic_quality", "pic_size", "pic_aspect", "img_set", "setting1_contrast", "setting1_sharpness",
                          "setting1_color_depth", "setting2_contrast", "setting2_sharpness", "setting2_color_depth",
                          "bw_contrast", "bw_sharpness", "te_contrast", "te_sharpness", "te_tone", "te_density",
                          "date_imprint"]),
               ("Focus", ["focus"]),
               ("Exposure", ["photometry", "exposure_comp", "iso", "manual_shutter", "manual_aperture"]),
               ("Colour", ["white_balance", "wb_detail"]),
               ("Drive", ["cont_mode", "auto_bracket", "image_with_sound"]),
               ("Flash", ["flash_mode", "flash_expo_comp", "scene_flash_a", "scene_flash_b"]),
               ("Scene", ["scene_mode", "slow_shutter_limit"]),
               ("Text mode", ["text_density", "text_size"]),
               ("Movie", ["movie_size"]),
               ("Display", ["disp_mode"])]


# ---------------------------------------------------------------------------------------------------- checks + output
HW_KEEP = {"magic", "checksum", "marker_1x", "marker_print", "marker_201", "marker_201_aa", "marker_230",
           "marker_230_fix", "marker_240", "img_set", "pic_quality", "pic_size", "exposure_comp", "iso",
           "white_balance", "language", "rate_ctrl_bias"}


def build():
    # HW only where hardware corroborates the meaning (owner-known setting, card EXIF, or structure verified on the
    # main camera's block); a value merely seen in range on hardware stays STATIC.
    for f in F:
        if f["confidence"] == "HW" and f["name"] not in HW_KEEP:
            f["confidence"] = "STATIC"

    # plain-language pass: owner-facing help, labels, section and order
    base_label = {}
    for f in F:
        if f["name"] in LABEL:
            f["label"] = LABEL[f["name"]]
        if f["name"] in HELP:
            f["help"] = HELP[f["name"]]
        elif f["page"] in SET_TEXT:
            if f.get("ui") != "hidden":
                f["help"] = SET_TEXT[f["page"]]
            f["section"] = "My Settings " + f["page"][-1]
            lab = f["label"].split(" ", 1)[1]
            f["label"] = LABEL.get(f["name"].split("_", 1)[1], lab)
        if f["page"] == "recipe" and f["name"].startswith("recipe_record_"):
            f["help"] = "The stored values of recipe %s. Edited through the recipe rows, not by hand." % \
                f["label"].split(" ", 2)[2]
            f["section"] = "Recipe store"
        if f["name"] == "recipe_fx_table":
            f["section"] = "Recipe store"
    byname = {f["name"]: f for f in F}
    n = 0
    for sec, names in SHOOT_ORDER:
        for nm in names:
            n += 1
            byname[nm]["section"], byname[nm]["order"] = sec, n
    for f in F:
        if f.get("ui") != "hidden" and not (f.get("section") and f.get("order") is not None):
            raise SystemExit("visible field without section/order: %s" % f["name"])
    used = {}
    for f in F:
        o = int(f["offset"], 16)
        for b in range(o, o + f["size"]):
            if b in used:
                raise SystemExit("overlap at 0x%03x: %s / %s" % (b, used[b], f["name"]))
            used[b] = f["name"]
    names = [f["name"] for f in F]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise SystemExit("duplicate names: %s" % sorted(dup))
    F.sort(key=lambda f: int(f["offset"], 16))
    X.sort(key=lambda x: int(x["offset"], 16))
    meta = {"firmware": "2.40", "block_base": "0x1b000", "block_size": 4096,
            "magic": {"offset": 0, "bytes": "4d473148"},
            "checksum": {"algo": "u32-sum-of-u16be", "range": [0, 4092], "offset": 4092, "size": 4,
                         "endian": "big",
                         "note": "sum of big-endian u16 words over bytes 0..4091, stored as a big-endian u32 at "
                                 "4092 (0x3d9b8, 0x4949c); HW-verified on the main camera block"},
            "byte_order": "big-endian (ARM BE core); u16/u32 fields are big-endian",
            "generated": "2026-11-07",
            "coverage": {"mapped_bytes": len(used), "total_bytes": 4096}}
    return {"meta": meta, "fields": F, "exclude_on_merge": X}


def check_block(path, m):
    b = Path(path).read_bytes()
    assert len(b) == 4096, "need 4096 bytes"
    s = sum(struct.unpack(">2046H", b[:4092])) & 0xffffffff
    print("magic", b[:4], "checksum ok" if s == struct.unpack(">I", b[4092:])[0] else "CHECKSUM MISMATCH")
    for f in m["fields"]:
        o, n = int(f["offset"], 16), f["size"]
        v = int.from_bytes(b[o:o + n], "big")
        if f["type"] == "enum" and str(v) not in f["values"] and not all(x == 0xff for x in b[o:o + n]):
            print("  %s %s = %d not in enum" % (f["offset"], f["name"], v))
        if f["type"] in ("u8", "u16", "i8") and not (f["range"][0] <= v <= f["range"][1]) and \
                not all(x == 0xff for x in b[o:o + n]):
            print("  %s %s = %d out of range %s" % (f["offset"], f["name"], v, f["range"]))


if __name__ == "__main__":
    m = build()
    OUT.write_text(json.dumps(m, indent=1, ensure_ascii=False) + "\n")
    json.loads(OUT.read_text())
    print("wrote %s: %d fields, %d mapped bytes, %d exclusions" % (OUT, len(m["fields"]),
                                                                   m["meta"]["coverage"]["mapped_bytes"], len(X)))
    if len(sys.argv) > 2 and sys.argv[1] == "--check-block":
        check_block(sys.argv[2], m)
