"""stage-update: copies one of Ricoh's pinned update payload sets to the SD card over USB, as FirmUpdate.exe does, and reads them back.

This is the only write-capable module in grdusb. The read-only client modules (cli, plan, session, protocol, ...) still
cannot build a write command; this module holds the two write verbs, their fixed targets and the host data frame. It
writes card files only. There is no flash command in this protocol: the camera flashes later, at a keyed power-on.
Built for the owner's expendable spare body; the expected camera version comes from --require-version. Spec: research/usb-protocol.md "Firmware update sequence".
"""
from __future__ import annotations

import hashlib
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from .protocol import (CHANNEL_DATA, ETX, MAX_DATA_PAYLOAD, SOF, CommandRefused, ProtocolError, fixed_command,
                       parse_capacity, path_command, reply_code)
from .session import CameraError, Session, ShortWrite, SinkError
from .transferlog import LoggedTransport, TransferLog
from .transport import GateRefused, TransportError

EXIT_OK, EXIT_ERROR, EXIT_REFUSED = 0, 1, 2
ROOT = Path(__file__).resolve().parents[2]
CONFIRM_FLAG = "--i-understand-this-writes-the-card"
FREE_MARGIN = 1 << 20  # bytes beyond the two files; covers cluster slack with room to spare
MAX_SIZE_DIGITS = 8  # the firmware parser takes a size field of 1 to 8 characters (D 0xcbd64)
STOR_OPEN = "150 data connection for -STOR."  # D 0xcc598, string 0xcc5fc
STOR_DONE = "250 -STOR command success."  # D 0xcbc30, string 0xcbd3b
DELE_CODES = ("250", "500")  # "250 DELE command success." / "500 DELE command failure" (D 0xcc628)
DANGEROUS_NAMES = ("QUICKVER.UP", "SCRIPT.MG1")


@dataclass(frozen=True)
class Payload:
    source: str  # Ricoh's file name in the set's directory
    target: str  # card path
    sha256: str  # pinned; nothing else is accepted


DIR_240 = ROOT / "firmware" / "extracted" / "GR DIGITAL Update" / "Firmware rel 2.40"
DIR_231 = (ROOT / "firmware" / "original" / "history" / "gr-231-win-en" / "x" / "GR DIGITAL Update"
           / "Firmware rel 2.31")
# Pins: qrflect3 2.40 from the task brief; qrflect7 2.40 as in research/recovery-analysis.md (and the manifests under
# analysis/script-monitor-entry); 2.31 per research/downgrade-plan.md. No option exists to pass a different file.
P240_3 = Payload("qrflect3", "/EXT0/QRFLECT3", "de00e03d563fd1bdd0a2a925761b1a029993425049841119e3e83eef990c783b")
P240_7 = Payload("qrflect7", "/EXT0/QRFLECT7", "1dd1a9d9cb2d5a8386b059722c01ac025f10dbe5f507c7cdb9f5d486f9c21c3f")
P231_3 = Payload("qrflect3", "/EXT0/QRFLECT3", "8cbf2d3a97376efe32dc9beceee9aed3a85a2e817b28ffd702330c688b91731c")


@dataclass(frozen=True)
class PayloadSet:
    name: str
    directory: Path
    payloads: tuple


PAYLOAD_SETS = {s.name: s for s in (PayloadSet("official-2.40-pair", DIR_240, (P240_3, P240_7)),
                                    PayloadSet("official-2.40-qrflect3", DIR_240, (P240_3,)),
                                    PayloadSet("official-2.31-qrflect3", DIR_231, (P231_3,)))}
DEFAULT_SET = "official-2.40-pair"
# Every QRFLECT0..9 name is deleted before staging (the camera flashes every one it finds); only the set is stored.
SWEEP_NAMES = tuple(f"QRFLECT{n}" for n in range(10))
SWEEP_TARGETS = tuple(f"/EXT0/{name}" for name in SWEEP_NAMES)
STORE_TARGETS = tuple(sorted({p.target for s in PAYLOAD_SETS.values() for p in s.payloads}))

RICOH_NOTE = (
    "Next steps on the camera, from Ricoh's updater (dialog 132, research/usb-protocol.md step 10): switch the camera "
    "off and unplug the USB cable. Set the mode dial to Movie. Hold UP and TELE (zoom), then press and hold PLAY until "
    "the LCD lights. Then press RIGHT, then MENU/OK, and follow the screen. The AF and flash lamps blink; playback "
    "appears after about 2 minutes. Never cut the power while the lamp blinks.")


def nothing_flashed(loaded: list["Loaded"], set_name: str) -> str:
    names = " and ".join(item.payload.target.rsplit("/", 1)[1] for item in loaded)
    return (f"Nothing has been flashed. This command only wrote {names} (set {set_name}) to the SD card; the camera "
            "flashes only if you do the keyed power-on above.")


class StageStop(Exception):
    """Stop now: send nothing more."""


class StageRefused(StageStop):
    """A gate refused. No write command has been sent."""


@dataclass
class Loaded:
    payload: Payload
    data: bytes


def _version_tuple(text: str) -> tuple[int, int]:
    major, minor = text.split(".")
    return int(major), int(minor)


def load_payloads(set_name: str = DEFAULT_SET, read=Path.read_bytes) -> list[Loaded]:
    """Reads the named set's files from their existing paths and refuses unless each hash matches its pin. `read`
    exists so tests can feed wrong bytes without writing an update-named file anywhere."""
    if set_name not in PAYLOAD_SETS:
        raise CommandRefused(f"unknown payload set {set_name!r}; choose from {', '.join(PAYLOAD_SETS)}")
    payload_set, loaded = PAYLOAD_SETS[set_name], []
    for payload in payload_set.payloads:
        path = payload_set.directory / payload.source
        try:
            data = read(path)
        except OSError as error:
            raise CommandRefused(f"cannot read {path} ({error}); nothing was sent") from error
        digest = hashlib.sha256(data).hexdigest()
        if digest != payload.sha256:
            raise CommandRefused(f"{path} has SHA-256 {digest}, pinned {payload.sha256}; nothing was sent")
        if not data or len(str(len(data))) > MAX_SIZE_DIGITS:
            raise CommandRefused(f"{path}: size {len(data)} does not fit the camera's size field; nothing was sent")
        loaded.append(Loaded(payload, data))
    return loaded


def target_version(loaded: list[Loaded]) -> str:
    """Ricoh takes the update's version from bytes 0x12, 0x13 of qrflect3 (BCD), e.g. 02 40 -> 02.40."""
    data = loaded[0].data
    return f"{data[0x12]:02x}.{data[0x13]:02x}"


def already_updated(versions, target: str, allow_older: bool) -> list[str]:
    """Versions Ricoh's check would refuse: at or above the update's. --allow-older-update lifts that for cameras
    strictly above the update (a deliberate downgrade); an equal version stays refused."""
    wanted = _version_tuple(target)
    return [v for v in versions if _version_tuple(v) == wanted or (_version_tuple(v) > wanted and not allow_older)]


def check_options(args, loaded: list[Loaded]) -> None:
    """Every refusal that needs no camera. Runs before the log is created and before any USB access."""
    required = args.require_version or []
    if not required:
        raise CommandRefused("stage-update needs --require-version VV.VV (the firmware the camera must be running); "
                             "nothing was sent")
    if not args.require_camera_id:
        raise CommandRefused("stage-update needs --require-camera-id ID (the spare's -GCID value); nothing was sent")
    if not args.i_understand_this_writes_the_card:
        raise CommandRefused(f"stage-update writes files to the SD card; pass {CONFIRM_FLAG} to confirm. "
                             "Nothing was sent")
    target = target_version(loaded)
    updated = already_updated(required, target, args.allow_older_update)
    if updated:
        raise CommandRefused(f"--require-version {', '.join(updated)} is at or above the update's {target}; such a "
                             "camera is already updated (Ricoh's check). Staging an OLDER version needs "
                             "--allow-older-update. Nothing was sent")


def delete_command(target: str) -> bytes:
    if target not in SWEEP_TARGETS:
        raise CommandRefused(f"delete is only built for {', '.join(SWEEP_TARGETS)}, not {target!r}")
    return f"DELE {target}\r\n".encode("ascii")


def store_command(size: int, target: str) -> bytes:
    """-STOR <size> <path>: the firmware parser reads the size field (to a space) before the path (D 0xcbd64)."""
    if target not in STORE_TARGETS:
        raise CommandRefused(f"store is only built for {', '.join(STORE_TARGETS)}, not {target!r}")
    if not (1 <= size and len(str(size)) <= MAX_SIZE_DIGITS):
        raise CommandRefused(f"size {size} out of range")
    return f"-STOR {size} {target}\r\n".encode("ascii")


def encode_data_frame(payload: bytes) -> bytes:
    """Host to camera, channel 2: same framing as a command frame. At most 65536 payload bytes (D)."""
    if not 1 <= len(payload) <= MAX_DATA_PAYLOAD:
        raise CommandRefused(f"data frame payload {len(payload)} out of range")
    frame = struct.pack("<BBHI", SOF, CHANNEL_DATA, 0, len(payload)) + payload + bytes([ETX])
    return frame + bytes(-len(frame) & 3)


def chunks(data: bytes):
    return [data[offset:offset + MAX_DATA_PAYLOAD] for offset in range(0, len(data), MAX_DATA_PAYLOAD)]


def planned_wire(loaded: list[Loaded]) -> list[str]:
    """Everything the host sends on a successful run, in order; data frames appear as <data N>."""
    def text(command: bytes) -> str:
        return command.decode("ascii")

    wire = [text(fixed_command("version")), text(fixed_command("camera_id")), text(path_command("capacity", "/EXT0")),
            text(path_command("list", "/EXT0"))]
    wire += [text(delete_command(target)) for target in SWEEP_TARGETS]
    for item in loaded:
        wire.append(text(store_command(len(item.data), item.payload.target)))
        wire += [f"<data {len(chunk)}>" for chunk in chunks(item.data)]
    wire += [text(path_command("retrieve", item.payload.target)) for item in loaded]
    wire += [text(path_command("capacity", "/EXT0")), text(path_command("list", "/EXT0"))]
    return wire


def staged_names(loaded: list[Loaded]) -> list[str]:
    return [item.payload.target.rsplit("/", 1)[1] for item in loaded]


def render_plan(args, loaded: list[Loaded]) -> list[str]:
    lines = [f"command: stage-update, set {args.set} (WRITES the SD card: {' and '.join(staged_names(loaded))} at the "
             "card root, after deleting every QRFLECT0..9; never flashes)",
             "payloads (SHA-256 checked against the pins):"]
    lines += [f"  {item.payload.source} -> {item.payload.target}: {len(item.data)} bytes, {item.payload.sha256}"
              for item in loaded]
    older = "; --allow-older-update given: a newer camera may be downgraded" if args.allow_older_update else ""
    lines.append(f"gates: firmware in {', '.join(args.require_version)}, "
                 f"{'not equal to' if args.allow_older_update else 'below'} {target_version(loaded)}{older}; camera id "
                 f"{args.require_camera_id}; -DCAP /EXT0 200 with free >= {sum(len(i.data) for i in loaded)} + "
                 f"{FREE_MARGIN} bytes; card root free of {', '.join(DANGEROUS_NAMES)} and QRFLECT names other than "
                 f"QRFLECT0..9; after staging the card root's QRFLECT files must be exactly "
                 f"{', '.join(staged_names(loaded))}")
    lines.append("requests, in order; these and no others (data frames grouped):")
    wire, grouped = planned_wire(loaded), []
    for item in wire:
        if item.startswith("<data") and grouped and grouped[-1][0] == "data":
            grouped[-1][1].append(int(item[6:-1]))
        elif item.startswith("<data"):
            grouped.append(("data", [int(item[6:-1])]))
        else:
            grouped.append(("cmd", item))
    for index, (kind, value) in enumerate(grouped, 1):
        if kind == "cmd":
            lines.append(f"  {index}. {value.encode('unicode_escape').decode('ascii')}")
        else:
            lines.append(f"  {index}. {len(value)} data frame(s) on channel 2, {sum(value)} bytes "
                         f"(at most {MAX_DATA_PAYLOAD} each); then expect '{STOR_DONE}'")
    lines.append("stop rules: any unexpected reply, short write, timeout or read-back mismatch stops at once; nothing "
                 "is retried and no further command is sent.")
    return lines


class WriteSession(Session):
    """Session plus the two write verbs. Records every request and its first reply for the stop report."""

    def __init__(self, transport):
        super().__init__(transport)
        self.history: list[dict] = []

    def _ask(self, command: bytes) -> str:
        entry = {"sent": command.decode("ascii").rstrip("\r\n"), "reply": None}
        self.history.append(entry)
        reply = super()._ask(command)
        entry["reply"] = reply.rstrip("\r\n")
        return reply

    def delete(self, target: str) -> str:
        reply = self._ask(delete_command(target))
        if reply_code(reply) not in DELE_CODES:
            raise StageStop(f"unexpected reply to DELE {target}: {reply.strip()!r}")
        return reply.strip()

    def store(self, target: str, data: bytes) -> None:
        reply = self._ask(store_command(len(data), target))
        if reply.rstrip("\r\n") != STOR_OPEN:
            raise StageStop(f"unexpected reply to -STOR {len(data)} {target}: {reply.strip()!r} (expected "
                            f"'{STOR_OPEN}')")
        entry = {"sent": f"data for {target}", "frames": 0, "bytes": 0, "of": len(data), "reply": None}
        self.history.append(entry)
        for chunk in chunks(data):
            frame = encode_data_frame(chunk)
            self.requests_attempted += 1
            entry["frames"] += 1  # counted before the write: a failed write may still have put bytes on the wire
            written = self._transport.write(frame)
            if written != len(frame):
                raise ShortWrite(f"bulk write accepted {written!r} of {len(frame)} bytes in data frame "
                                 f"{entry['frames']} for {target}")
            entry["bytes"] += len(chunk)
        closing = self._reply()
        entry["reply"] = closing.rstrip("\r\n")
        if closing.rstrip("\r\n") != STOR_DONE:
            raise StageStop(f"unexpected reply after the data for {target}: {closing.strip()!r} (expected "
                            f"'{STOR_DONE}')")


class Stager:
    def __init__(self, session: WriteSession, args, loaded: list[Loaded], out=print):
        self.session, self.args, self.loaded, self.out = session, args, loaded, out
        self.card = {item.payload.target: "untouched" for item in loaded}
        self.extra: list[str] = []  # QRFLECT names on the card after staging that are not in the set

    def gate(self) -> None:
        firmware = self.session.version()
        if firmware not in self.args.require_version:
            raise StageRefused(f"camera reports firmware {firmware}; required: {', '.join(self.args.require_version)}")
        target = target_version(self.loaded)
        if already_updated([firmware], target, self.args.allow_older_update):
            raise StageRefused(f"camera firmware {firmware} is at or above the update's {target}: already updated "
                               "(a downgrade needs --allow-older-update)")
        seen = self.session.camera_id_value()
        if seen != self.args.require_camera_id:
            raise StageRefused(f"camera id is {seen}; required: {self.args.require_camera_id}")
        self.out(f"gate passed: firmware {firmware}, camera id {seen}")

    def capacity(self) -> tuple[int, int]:
        reply = self.session._ask(path_command("capacity", "/EXT0"))
        if reply_code(reply) != "200":
            raise StageRefused(f"-DCAP /EXT0 answered {reply.strip()!r}: no usable SD card (Ricoh: 'Insert the SD "
                               "memory card')")
        name, capacity, free = parse_capacity(reply.strip())
        if name.upper() != "/EXT0":
            raise StageStop(f"-DCAP /EXT0 answered for volume {name!r}")
        return capacity, free

    def check_root(self):
        entries = self.session.list_directory("/EXT0")
        bad = []
        for entry in entries:
            upper = entry.name.upper()
            if upper in DANGEROUS_NAMES:
                bad.append(entry.name)
            elif upper.startswith("QRFLECT") and (upper not in SWEEP_NAMES or entry.is_dir):
                bad.append(entry.name + ("/" if entry.is_dir else ""))
        if bad:
            raise StageRefused(f"card root holds {', '.join(bad)}; remove it in a card reader first (QUICKVER.UP makes "
                               "the camera flash without checks; SCRIPT.MG1 runs at boot)")
        return entries

    def run(self) -> None:
        self.gate()
        capacity, free_before = self.capacity()
        total = sum(len(item.data) for item in self.loaded)
        if free_before < total + FREE_MARGIN:
            raise StageRefused(f"card free space {free_before} bytes is below {total} + {FREE_MARGIN} margin (Ricoh: "
                               "'Format the SD memory card')")
        self.out(f"/EXT0: capacity {capacity} bytes, free {free_before} bytes")
        self.check_root()
        for target in self.card:  # the sweep may delete any QRFLECT file; the staged ones are what we track
            self.card[target] = "may have been deleted (DELE sweep started)"
        for target in SWEEP_TARGETS:
            reply = self.session.delete(target)
            self.out(f"DELE {target}: {reply} (recorded; 250 or 500 both accepted, as Ricoh ignores it)")
        for item in self.loaded:
            target = item.payload.target
            self.card[target] = "missing, empty or partial (-STOR opens with truncate; upload incomplete)"
            self.session.store(target, item.data)
            self.card[target] = "written (camera said 250), not yet read back"
            self.out(f"stored {target}: {len(item.data)} bytes")
        for item in self.loaded:
            target, received = item.payload.target, bytearray()
            size = self.session.retrieve(target, received.extend)
            if size != len(item.data) or bytes(received) != item.data:
                first = next((i for i, (a, b) in enumerate(zip(received, item.data)) if a != b),
                             min(len(received), len(item.data)))
                self.card[target] = "present but its read-back does NOT match Ricoh's file"
                raise StageStop(f"read-back of {target} does not match: {len(received)} bytes received (announced "
                                f"{size}), {len(item.data)} expected, first difference at offset {first}. Not retried")
            self.card[target] = "verified: read-back matches Ricoh's file byte for byte"
            self.out(f"verified {target}: {size} bytes match")
        _, free_after = self.capacity()
        entries = self.session.list_directory("/EXT0")
        self.out(f"/EXT0 free: {free_before} -> {free_after} bytes (change {free_after - free_before:+d})")
        self.out("/EXT0 listing after staging:")
        for entry in entries:
            self.out(f"  {'dir ' if entry.is_dir else 'file'} {entry.size:>10} {entry.modified} {entry.name}")
        found = sorted(entry.name.upper() + ("/" if entry.is_dir else "") for entry in entries
                       if entry.name.upper().startswith("QRFLECT"))
        if found != sorted(staged_names(self.loaded)):
            self.extra = [name for name in found if name not in staged_names(self.loaded)]
            raise StageStop(f"card root QRFLECT files are {', '.join(found) or 'none'}, not exactly the staged set "
                            f"{', '.join(staged_names(self.loaded))}; the camera flashes every QRFLECT file it finds")

    def report(self) -> list[str]:
        lines = ["sent, in order:"]
        for index, entry in enumerate(self.session.history, 1):
            if "frames" in entry:
                lines.append(f"  {index}. {entry['sent']}: {entry['frames']} frame(s) attempted, {entry['bytes']} of "
                             f"{entry['of']} bytes accepted -> {entry['reply'] or 'no closing reply'}")
            else:
                lines.append(f"  {index}. {entry['sent']} -> {entry['reply'] or 'no reply'}")
        lines.append("card state:")
        lines += [f"  {target}: {state}" for target, state in self.card.items()]
        if self.extra:
            lines.append(f"  Unexpected on the card root: {', '.join(self.extra)}. Remove in a card reader before any "
                         "keyed power-on.")
        elif all(state.startswith("verified") for state in self.card.values()):
            lines.append("  All staged files verified and the card root holds exactly that set; the card now holds "
                         "what Ricoh's updater leaves on it.")
        elif any(state != "untouched" for state in self.card.values()):
            lines.append(f"  Before any keyed power-on, delete {' and '.join(staged_names(self.loaded))} from the card root "
                         "in a card reader "
                         "(or reformat the card). The camera checks size and checksum before erasing flash "
                         "(research/usb-protocol.md 'What the camera does'), but do not rely on that.")
        else:
            lines.append("  No write or delete command was sent; the card is as it was.")
        return lines


ANOMALY = ("STOPPED. The camera may retain transfer state. Nothing was retried or drained. Power cycle the camera: "
           "switch it off, unplug the USB cable, wait a few seconds. Do not send another command until you have.")


def run(args, open_transport, log_path: str, err=sys.stderr) -> int:
    """Called by cli.main for stage-update. Every refusal that needs no camera happens before the log or USB."""
    try:
        loaded = load_payloads(args.set)
        check_options(args, loaded)
    except CommandRefused as error:
        print(f"refused: {error}", file=err)
        return EXIT_REFUSED
    if args.dry_run:
        print("dry run: no USB access of any kind (nothing enumerated, opened or sent)")
        print("\n".join(render_plan(args, loaded)))
        return EXIT_OK
    if not (args.mock or args.live):
        print("refused: stage-update talks to a camera; pass --live for real hardware or --mock for the simulation. "
              "Nothing was sent", file=err)
        return EXIT_REFUSED
    print("\n".join(render_plan(args, loaded)))
    try:
        log = TransferLog(log_path, args.command, args.mock)
    except OSError as error:
        print(f"error: cannot create the transfer log ({error}); nothing was sent", file=err)
        return EXIT_ERROR
    transport = None
    try:
        try:
            transport = LoggedTransport(open_transport(args), log)
            transport.bring_up()
        except GateRefused as error:
            log.event("refused", message=str(error))
            print(f"refused: {error}", file=err)
            return EXIT_REFUSED
        except TransportError as error:
            log.event("bring_up_error", message=str(error))
            print(f"error: {error}. Resources released; nothing was written.", file=err)
            return EXIT_ERROR
        stager = Stager(WriteSession(transport), args, loaded)
        try:
            stager.run()
        except StageRefused as error:
            log.event("refused", message=str(error))
            print(f"refused: {error}. Nothing further was sent", file=err)
            print("\n".join(stager.report()), file=err)
            return EXIT_REFUSED
        except (StageStop, ProtocolError, TransportError, SinkError, CameraError) as error:
            log.event("anomaly", message=f"{type(error).__name__}: {error}")
            print(f"error: {type(error).__name__}: {error}", file=err)
            print("\n".join(stager.report()), file=err)
            print(ANOMALY, file=err)
            return EXIT_ERROR
        print("\n".join(stager.report()))
        print(RICOH_NOTE)
        print(nothing_flashed(loaded, args.set))
        return EXIT_OK
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception as error:
                print(f"warning: closing the device failed: {type(error).__name__}: {error}", file=err)
        print(f"transfer log: {log.path}", file=err)
        try:
            log.close()
        except OSError as error:
            print(f"warning: could not finish the transfer log: {error}", file=err)
