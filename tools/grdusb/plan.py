"""What each command will send, decided before any USB access. Execution and --dry-run both use this."""
from __future__ import annotations

from dataclasses import dataclass, field

from .protocol import CommandRefused, fixed_command, path_command
from .transport import LINE_STATE_REQUEST, SETTLE_S

GET_NOTE = ("note: RETR is not guaranteed physically read-only. If the file's metadata is inconsistent, the camera's "
            "close path can write a filesystem repair to the card (derivation, research/usb-protocol.md note 3). "
            "Only read files on an expendable card, after copying the card in a reader.")
VER_NOTE = ("OBSERVE NOW: -VER arms a 3 s timer inside the camera, and its expiry can post a UI message (derivation, "
            "research/usb-protocol.md note 1). Watch the LCD and lamps beyond the 3 s timer and write down anything "
            "that changes. Do not run another grdusb command until you have finished observing.")
STRING_AND_CONFIG_STEPS = ("read string descriptors (control reads)", "GET_CONFIGURATION (control read)",
                           "SET_CONFIGURATION(1)", f"wait {SETTLE_S:g} s", "claim interface 0")


@dataclass(frozen=True)
class Plan:
    command: str
    requests: tuple[bytes, ...]
    bring_up: tuple[str, ...]
    notes: tuple[str, ...] = field(default=())  # shown before anything is sent
    after: tuple[str, ...] = field(default=())  # shown once a request has gone out


INTERNAL_NOTE = ("internal memory (/IROM) is being read on the version-gated body: --allow-internal was given with "
                 "--require-version {versions}. The repair-write caveat above applies to internal memory too.")
GATE_NOTE = ("version gate: the first request(s) below check the camera before anything else is sent; on a mismatch "
             "the run stops there. Required version(s): {versions}{camera}.")
GET_MANY_NOTE = ("get-many: the file names are unknown until the listing comes back, so the plan shows the single "
                 "-NLST; after it, one RETR per regular file in that directory follows (directories are skipped). "
                 "Existing destination files are refused before the first RETR.")


def _gated(args) -> bool:
    return args.command != "list" and bool(args.require_version or args.require_camera_id)


def gate_requests(args) -> list[bytes]:
    """-VER (and -GCID) sent first when --require-version / --require-camera-id are given. ver sends its own -VER."""
    requests = []
    if _gated(args) and args.command != "ver":
        requests.append(fixed_command("version"))
    if _gated(args) and args.require_camera_id:
        requests.append(fixed_command("camera_id"))
    return requests


def check_gate_options(args) -> None:
    if args.require_camera_id and not args.require_version:
        raise CommandRefused("--require-camera-id needs at least one --require-version (the id check follows the "
                             "version check). Nothing was sent")
    if args.command in ("get", "get-many") and args.allow_internal and not args.require_version:
        raise CommandRefused(f"{args.command} --allow-internal needs at least one --require-version VV.VV; reading "
                             "internal memory is only allowed on a version-gated run. Nothing was sent")


def make_plan(args) -> Plan:
    """Builds every request through the closed command builders, so a refused path fails here, before USB."""
    name = args.command
    if name == "list":
        return Plan(name, (), ())
    check_gate_options(args)
    plan = _make_plan(args, name)
    if not _gated(args):
        return plan
    camera = f", camera id {args.require_camera_id}" if args.require_camera_id else ""
    note = GATE_NOTE.format(versions=", ".join(args.require_version), camera=camera)
    gate = tuple(gate_requests(args))
    requests = plan.requests + gate if name == "ver" else gate + plan.requests  # ver's own -VER comes first
    return Plan(name, requests, plan.bring_up, (note,) + plan.notes, plan.after)


def _make_plan(args, name) -> Plan:
    steps = list(STRING_AND_CONFIG_STEPS)
    if args.line_state_fallback:
        steps.insert(4, "control 0x%02x/0x%02x wValue %d (opt-in, unverified)" % LINE_STATE_REQUEST[:3])
    if name == "ver":
        return Plan(name, (fixed_command("version"),), tuple(steps), (), (VER_NOTE,))
    if name == "info":
        requests = [fixed_command("camera_id"), fixed_command("card_id"), fixed_command("clock"),
                    path_command("capacity", "/EXT0")]
        notes = ["info is card-only: /IROM is never probed."]
        if args.include_internal:
            requests.append(path_command("capacity", "/IROM"))
            notes = ["--include-internal adds -DCAP /IROM, an explicit probe of internal memory."]
        return Plan(name, tuple(requests), tuple(steps), tuple(notes))
    if name == "ls":
        return Plan(name, (path_command("list", args.path),), tuple(steps))
    internal = getattr(args, "allow_internal", False)
    internal_notes = (INTERNAL_NOTE.format(versions=", ".join(args.require_version or ())),) if internal else ()
    if name == "get":
        return Plan(name, (path_command("retrieve", args.path, internal),), tuple(steps),
                    (GET_NOTE,) + internal_notes)
    if name == "get-many":
        if args.remote_dir == "/":
            raise CommandRefused("get-many needs a directory under /EXT0 or /IROM, not /")
        path_command("retrieve", args.remote_dir.rstrip("/") + "/X", internal)  # refuses /IROM without both flags
        return Plan(name, (path_command("list", args.remote_dir),), tuple(steps),
                    (GET_NOTE, GET_MANY_NOTE) + internal_notes)
    raise AssertionError(name)


def render(plan: Plan, include_after: bool = True) -> list[str]:
    def text(command: bytes) -> str:
        return command.decode("ascii").encode("unicode_escape").decode("ascii")

    lines = [f"command: {plan.command}"]
    if plan.bring_up:
        lines.append("USB bring-up, in order: " + "; ".join(plan.bring_up))
    else:
        lines.append("USB bring-up: none")
    if plan.requests:
        lines.append(f"requests, in order ({len(plan.requests)}); these and no others:")
        lines += [f"  {index}. {text(request)}" for index, request in enumerate(plan.requests, 1)]
    else:
        lines.append("requests: none")
    lines += list(plan.notes)
    if include_after:
        lines += [f"after the request is sent: {note}" for note in plan.after]
    return lines
