"""grdusb: client for an original GR DIGITAL in ORIGINAL USB mode. Read-only except the separate, gated stage-update
command (stage.py). Run: .venv/bin/python -m tools.grdusb"""
from __future__ import annotations

import argparse
import datetime
import json
import hashlib
import os
import re
import sys
import tempfile

from . import descriptors, mock
from .plan import make_plan, render
from .protocol import CommandRefused, ProtocolError, VersionRefused
from .session import CameraError, Session, SinkError
from .transferlog import LoggedTransport, TransferLog
from .transport import GateRefused, TransportError, enumerate_ricoh

EXIT_OK, EXIT_ERROR, EXIT_REFUSED = 0, 1, 2


def _version_arg(text: str) -> str:
    if not re.fullmatch(r"\d\d\.\d\d", text):
        raise argparse.ArgumentTypeError(f"{text!r} is not a version like 01.08")
    return text


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grdusb", description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--mock", action="store_true", help="use the simulated camera; proves self-consistency only")
    target.add_argument("--live", action="store_true", help="open a real camera; untested against hardware")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the USB bring-up and the exact requests the command would send, then exit; "
                             "touches no USB (not even enumeration)")
    parser.add_argument("--transfer-log", metavar="FILE",
                        help="timestamped transfer log (JSON lines). Default grdusb-<command>-<UTC time>.jsonl in the "
                             "current directory; never overwrites; if it cannot be created nothing is sent")
    parser.add_argument("--line-state-fallback", action="store_true",
                        help="UNVERIFIED, off by default, refused by ver: send vendor request 0x41/0x22 wValue 3 after "
                             "configuring, as Ricoh's Mac client does")
    parser.add_argument("--require-version", action="append", metavar="VV.VV", type=_version_arg,
                        help="version gate: send -VER first and refuse unless the firmware matches (repeatable; any "
                             "match passes). Keeps the main camera (02.40) from being hit when the spare (01.08) is "
                             "the target. Applies to every command except list")
    parser.add_argument("--require-camera-id", metavar="ID",
                        help="after the version check, send -GCID and refuse unless CameraID= equals ID; needs "
                             "--require-version")
    parser.add_argument("--mock-version", default="02.40", type=_version_arg,
                        help="firmware version the simulated camera reports")
    parser.add_argument("--mock-camera-id", default="0011223344556677")
    parser.add_argument("--mock-descriptors", default="original", choices=sorted(mock.DESCRIPTOR_VARIANTS))
    parser.add_argument("--mock-strings", default="good", choices=sorted(mock.STRING_VARIANTS))
    parser.add_argument("--mock-fault", default="none", choices=mock.FAULTS)
    parser.add_argument("--mock-log", help="write the commands the simulated camera received to this JSON file")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="print USB descriptors of attached Ricoh devices; sends no camera request")
    commands.add_parser("ver", help="sends exactly one request, -VER, reads one reply, disconnects, and tells you to "
                                    "watch the LCD")
    info = commands.add_parser("info", help="card-only: sends -GCID, -GSID, -TIME, -DCAP /EXT0")
    info.add_argument("--include-internal", action="store_true",
                      help="also send -DCAP /IROM (explicit probe of internal memory; off by default)")
    listing = commands.add_parser("ls", help="sends one -NLST for the directory, e.g. /EXT0/DCIM/100RICOH")
    listing.add_argument("path")
    download = commands.add_parser("get", help="sends one RETR for a card file; refuses /IROM (unless --allow-internal "
                                               "with --require-version) and overwriting")
    download.add_argument("path")
    download.add_argument("dest")
    download.add_argument("--allow-internal", action="store_true",
                          help="permit an /IROM path; needs at least one --require-version")
    many = commands.add_parser("get-many", help="one -NLST of a directory, then one RETR per regular file into a "
                                                "local directory, plus grdusb-manifest.json")
    many.add_argument("remote_dir")
    many.add_argument("dest_dir")
    many.add_argument("--allow-internal", action="store_true",
                      help="permit an /IROM directory; needs at least one --require-version")
    stage = commands.add_parser("stage-update", help="WRITES THE CARD: copies a pinned Ricoh payload set to the card "
                                                     "root and reads it back, as Ricoh's updater does. Card files "
                                                     "only; never flashes. Spare body only; see "
                                                     "tools/grdusb/README.md")
    stage.add_argument("--i-understand-this-writes-the-card", action="store_true",
                       help="required; also needs --require-version and --require-camera-id")
    from .stage import DEFAULT_SET, PAYLOAD_SETS
    stage.add_argument("--set", default=DEFAULT_SET, choices=sorted(PAYLOAD_SETS),
                       help=f"pinned payload set to stage (default {DEFAULT_SET})")
    stage.add_argument("--allow-older-update", action="store_true",
                       help="permit staging a version OLDER than the camera runs (a deliberate downgrade)")
    return parser


def _cmd_list(args) -> int:
    devices = [mock.DESCRIPTOR_VARIANTS[args.mock_descriptors]] if args.mock else enumerate_ricoh()
    if not devices:
        print("no Ricoh device (vendor 05ca) found")
    for device in devices:
        print("\n".join(descriptors.describe(device)))
        print(f"  note: {descriptors.mode_note(device)}")
        problems = descriptors.mismatches(device)
        print("  gate: " + ("pass; ver, info, ls and get would be allowed" if not problems
                            else "REFUSE; " + "; ".join(problems)))
    return EXIT_OK


def _open_mock(args):
    device = mock.DESCRIPTOR_VARIANTS[args.mock_descriptors]
    problems = descriptors.mismatches(device)
    manufacturer, product = mock.STRING_VARIANTS[args.mock_strings]
    problems += [] if problems else descriptors.string_mismatches(manufacturer, product)
    if problems:
        if args.mock_log:
            with open(args.mock_log, "w") as target:
                json.dump({"sent": [], "violations": [], "simulated": True}, target)
        raise GateRefused("descriptors do not match; nothing was sent: " + "; ".join(problems))
    return mock.MockTransport(args.mock_fault, args.mock_log, line_state=args.line_state_fallback,
                              version=args.mock_version, camera_id=args.mock_camera_id,
                              writable=args.command == "stage-update")


def _open(args):
    if args.mock:
        return _open_mock(args)
    from .transport import open_gated
    return open_gated(line_state=args.line_state_fallback)


def _gate(session: Session, args) -> str | None:
    """Version (and camera id) check, run first. Raises VersionRefused before anything else is sent."""
    if args.command == "list" or not (args.require_version or args.require_camera_id):
        return None
    firmware = session.version()
    if firmware not in args.require_version:
        raise VersionRefused(f"camera reports firmware {firmware}; required: {', '.join(args.require_version)}. "
                             "Nothing else was sent")
    if args.require_camera_id:
        seen = session.camera_id_value()
        if seen != args.require_camera_id:
            raise VersionRefused(f"camera id is {seen}; required: {args.require_camera_id}. Nothing else was sent")
    return firmware


def _cmd_ver(session: Session, firmware: str | None = None) -> int:
    firmware = firmware or session.version()
    print(f"-VER reply accepted: GR DIGITAL model code 9.17, firmware {firmware}")
    return EXIT_OK


def _cmd_info(session: Session, include_internal: bool) -> int:
    print(f"camera id: {session.camera_id()}")
    print(f"card id: {session.card_id()}")
    print(f"clock: {session.clock()}")
    for volume in ("/EXT0", "/IROM") if include_internal else ("/EXT0",):
        try:
            name, capacity, free = session.capacity(volume)
            print(f"{name}: capacity {capacity} bytes, free {free} bytes")
        except CameraError as error:
            print(f"{volume}: unavailable ({error})")
    return EXIT_OK


def _cmd_ls(session: Session, path: str) -> int:
    if path == "/":
        print(session.list_raw("/").decode("latin-1"))
        return EXIT_OK
    for entry in session.list_directory(path):
        kind = "dir " if entry.is_dir else "file"
        print(f"{kind} {entry.size:>10} {entry.modified} {entry.name}")
    return EXIT_OK


class _Download:
    """Destination checks and the temporary file happen before any USB access."""

    def __init__(self, dest: str):
        if os.path.exists(dest):
            raise FileExistsError(f"{dest} exists; not overwriting")
        self.dest = dest
        handle, self.temporary = tempfile.mkstemp(prefix=".grdusb-", dir=os.path.dirname(os.path.abspath(dest)))
        self.target = os.fdopen(handle, "wb")

    def commit(self) -> None:
        self.target.close()
        os.link(self.temporary, self.dest)

    def discard(self) -> None:
        try:
            self.target.close()
        except OSError:
            pass
        os.unlink(self.temporary)


MANIFEST = "grdusb-manifest.json"


def _cmd_get_many(session: Session, args) -> int:
    remote = args.remote_dir.rstrip("/")
    files = [e for e in session.list_directory(remote) if not e.is_dir]
    for entry in files:
        if "/" in entry.name or "\\" in entry.name or entry.name.startswith("."):
            raise CommandRefused(f"listing has an unsafe file name {entry.name!r}; no RETR was sent")
    # Listed names can repeat within one listing (8.3 aliases of AppleDouble sidecars). Each entry is still fetched, in
    # order; repeats are saved locally as NAME.dup1, NAME.dup2, ...
    seen, locals_, duplicates = {}, [], []
    for entry in files:
        count = seen.get(entry.name, 0)
        seen[entry.name] = count + 1
        locals_.append(entry.name if count == 0 else f"{entry.name}.dup{count}")
        if count:
            duplicates.append(entry.name)
    if len(set(locals_)) != len(locals_):
        raise CommandRefused("listing names collide even after .dupN renaming; no RETR was sent")
    targets = [os.path.join(args.dest_dir, name) for name in locals_] + [os.path.join(args.dest_dir, MANIFEST)]
    for target in targets:  # all checked before the first RETR
        if os.path.exists(target):
            raise CommandRefused(f"{target} exists; not overwriting. No RETR was sent")
    print(f"{remote}: {len(files)} regular file(s); directories skipped")
    if duplicates:
        print(f"note: {len(duplicates)} repeated name(s) in the listing; repeats saved as NAME.dupN")
    done, complete = [], False
    try:
        for entry, local in zip(files, locals_):
            download = _Download(os.path.join(args.dest_dir, local))
            digest, received = hashlib.sha256(), 0

            def sink(chunk, download=download, digest=digest):
                nonlocal received
                download.target.write(chunk)
                digest.update(chunk)
                received += len(chunk)

            try:
                session.retrieve(f"{remote}/{entry.name}", sink, args.allow_internal)
                download.commit()
            finally:
                download.discard()
            done.append({"remote": f"{remote}/{entry.name}", "local": local, "listed_size": entry.size,
                         "bytes_received": received, "sha256": digest.hexdigest()})
            print(f"{local}: {received} bytes")
        complete = True
    finally:
        session.incomplete = None if complete else f"INCOMPLETE: {len(done)} of {len(files)} files copied; see manifest"
        with open(os.path.join(args.dest_dir, MANIFEST), "x") as target:
            json.dump({"remote_dir": remote, "complete": complete, "files_listed": len(files),
                       "files_copied": len(done), "duplicates": duplicates, "files": done}, target, indent=2)
        print(f"manifest: {os.path.join(args.dest_dir, MANIFEST)}")
    return EXIT_OK


ANOMALY = ("STOPPED. The camera may retain transfer state (an unfinished command or open file handle). Nothing was "
           "retried or drained. Power cycle the camera now: switch it off, unplug the USB cable, wait a few seconds, "
           "then reconnect. Do not send another command until you have.")


def _log_path(args) -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return args.transfer_log or f"grdusb-{args.command}-{stamp}.jsonl"


def _execute(args, plan, log, download) -> int:
    transport = None
    session = None
    try:
        try:
            transport = LoggedTransport(_open(args), log)
            transport.bring_up()
        except GateRefused as error:
            log.event("refused", message=str(error))
            print(f"refused: {error}", file=sys.stderr)
            return EXIT_REFUSED
        except TransportError as error:
            log.event("bring_up_error", message=str(error))
            print(f"error: {error}. Resources released; if the camera looks wrong, power cycle it.", file=sys.stderr)
            return EXIT_ERROR
        session = Session(transport)
        try:
            firmware = _gate(session, args)
            if args.command == "ver":
                return _cmd_ver(session, firmware)
            if args.command == "info":
                return _cmd_info(session, args.include_internal)
            if args.command == "ls":
                return _cmd_ls(session, args.path)
            if args.command == "get-many":
                return _cmd_get_many(session, args)
            size = session.retrieve(args.path, download.target.write, args.allow_internal)
            download.commit()
            print(f"{args.dest}: {size} bytes")
            return EXIT_OK
        except CommandRefused as error:
            print(f"refused: {error}", file=sys.stderr)
            return EXIT_REFUSED
        except (ProtocolError, TransportError, SinkError) as error:
            log.event("anomaly", message=f"{type(error).__name__}: {error}")
            print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
            print(ANOMALY, file=sys.stderr)
            return EXIT_ERROR
        except CameraError as error:
            print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
            return EXIT_ERROR
        except OSError as error:  # e.g. saving the finished download; the camera side had completed normally
            print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
            return EXIT_ERROR
        finally:
            if plan.after and session.requests_attempted:
                print("\n".join(plan.after))
            args.incomplete = getattr(session, "incomplete", None)  # printed last, by main()
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception as error:  # release failures must not hide the real result
                print(f"warning: closing the device failed: {type(error).__name__}: {error}", file=sys.stderr)


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.mock:
        print(mock.NOTICE, file=sys.stderr)
    if args.command == "ver" and args.line_state_fallback:
        print("refused: --line-state-fallback is not allowed with ver; ver sends exactly one bulk request and no "
              "other control traffic. Nothing was sent", file=sys.stderr)
        return EXIT_REFUSED
    if args.command == "stage-update":  # the one write-capable command lives in its own module
        from . import stage
        return stage.run(args, _open, _log_path(args))
    try:
        plan = make_plan(args)
    except CommandRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    if args.dry_run:
        print("dry run: no USB access of any kind (nothing enumerated, opened or sent)")
        print("\n".join(render(plan)))
        return EXIT_OK
    if args.command == "list":
        return _cmd_list(args)
    if not (args.mock or args.live):
        print("refused: this command talks to a camera; pass --live for real hardware or --mock for the simulation. "
              "Nothing was sent", file=sys.stderr)
        return EXIT_REFUSED
    print("\n".join(render(plan, include_after=False)))
    download = None
    try:
        log = TransferLog(_log_path(args), args.command, args.mock)
    except OSError as error:
        print(f"error: cannot create the transfer log ({error}); nothing was sent", file=sys.stderr)
        return EXIT_ERROR
    try:
        if args.command == "get-many" and not os.path.isdir(args.dest_dir):
            print(f"error: {args.dest_dir} is not an existing directory; nothing was sent", file=sys.stderr)
            return EXIT_ERROR
        if args.command == "get":
            try:
                download = _Download(args.dest)
            except OSError as error:
                print(f"error: {error}; nothing was sent", file=sys.stderr)
                return EXIT_ERROR
        try:
            return _execute(args, plan, log, download)
        finally:
            if download is not None:
                download.discard()
    finally:
        print(f"transfer log: {log.path}", file=sys.stderr)
        try:
            log.close()
        except OSError as error:
            print(f"warning: could not finish the transfer log: {error}", file=sys.stderr)
        if getattr(args, "incomplete", None):  # never let a partial run look successful
            print(args.incomplete, file=sys.stderr)
