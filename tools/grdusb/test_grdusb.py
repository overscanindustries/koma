"""Tests through the real CLI against the SIMULATED camera. They prove self-consistency with our reading of the
protocol, not camera behaviour. Run: .venv/bin/python -m unittest tools.grdusb.test_grdusb -v"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = Path(__file__).resolve().parent


def run(*args):
    return subprocess.run([sys.executable, "-m", "tools.grdusb", *args], cwd=ROOT, capture_output=True, text=True,
                          timeout=60)


def setUpModule():
    print("NOTE: every test below runs against a SIMULATED camera. Results do not establish camera behaviour.", flush=True)


class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.dir.name, "log.json")
        self.tlog = os.path.join(self.dir.name, "transfers.jsonl")
        self.addCleanup(self.dir.cleanup)

    def mock(self, *args, fault="none", descriptors="original", strings="good", fresh_log=True):
        if fresh_log and os.path.exists(self.tlog):
            os.unlink(self.tlog)
        result = run("--mock", "--mock-log", self.log, "--transfer-log", self.tlog, "--mock-fault", fault,
                     "--mock-descriptors", descriptors, "--mock-strings", strings, *args)
        self.assertIn("SIMULATED CAMERA", result.stderr)
        return result

    def record(self):
        with open(self.log) as source:
            return json.load(source)

    def sent(self):
        record = self.record()
        self.assertEqual(record["violations"], [])
        self.assertTrue(record["simulated"])
        return record["sent"]

    def transfers(self):
        with open(self.tlog) as source:
            return [json.loads(line) for line in source]

    def get(self, name, fault="none"):
        dest = os.path.join(self.dir.name, f"{fault}-{name}")
        return self.mock("get", f"/EXT0/DCIM/100RICOH/{name}", dest, fault=fault), dest

    FULL_BRING_UP = ["get_configuration", "set_configuration(1)", "get_configuration", "settle 2.0",
                     "claim_interface(0)"]

    def test_always_configures_and_settles_before_traffic(self):
        result = self.mock("info")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record()["events"], self.FULL_BRING_UP + ["release_interface(0)", "dispose_resources"])

    def test_bulk_before_configuration_is_rejected(self):
        result = self.mock("info", fault="skip-configure")
        self.assertEqual(result.returncode, 1)
        self.assertIn("before set_configuration", result.stderr)
        self.assertEqual(self.record()["violations"], ["write before configuration"])

    def test_line_state_request_is_opt_in(self):
        self.mock("info")
        self.assertFalse([e for e in self.record()["events"] if e.startswith("control")])
        result = self.mock("--line-state-fallback", "info")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record()["events"][:5], ["get_configuration", "set_configuration(1)", "get_configuration",
                                                       "settle 2.0", "control 0x41/0x22 wValue 3"])

    def test_stray_empty_reads_are_tolerated(self):
        result = self.mock("info", fault="stray-empty")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("camera id:", result.stdout)

    def test_only_empty_reads_time_out_cleanly(self):
        result = self.mock("ver", fault="empty-forever")
        self.assertEqual(result.returncode, 1)
        self.assertIn("error: TransportTimeout", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.sent(), ["-VER\r\n"])

    def test_exact_multiple_frame_needs_zlp(self):
        result, dest = self.get("EXACT.JPG")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(Path(dest).read_bytes()), 503)
        result, dest = self.get("EXACT.JPG", fault="missing-zlp")
        self.assertEqual(result.returncode, 1)
        self.assertIn("error: TransportTimeout", result.stderr)
        self.assertFalse(os.path.exists(dest))

    def test_full_65536_frame_has_no_zlp(self):
        for fault in ("none", "missing-zlp"):
            with self.subTest(fault=fault):
                result, dest = self.get("FULL.JPG", fault=fault)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(Path(dest).read_bytes()), 65536)
                os.unlink(dest)

    def test_info(self):
        result = self.mock("info")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("/EXT0: capacity 1024000 bytes, free 512000 bytes", result.stdout)
        self.assertEqual(self.sent(), ["-GCID\r\n", "-GSID\r\n", "-TIME\r\n", "-DCAP /EXT0\r\n"])

    def test_ls(self):
        result = self.mock("ls", "/EXT0/DCIM")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"dir .* 100RICOH")
        self.assertNotRegex(result.stdout, r" \.\.?\n")
        result = self.mock("ls", "/EXT0/DCIM/100RICOH")
        self.assertRegex(result.stdout, r"file +179200 2007-10-24 12:30:00 RIMG0001.JPG")

    def test_ls_root_and_empty(self):
        self.assertIn("/EXT0", self.mock("ls", "/").stdout)
        result = self.mock("ls", "/IROM")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.rstrip().endswith("these and no others:\n  1. -NLST /IROM\\r\\n"))

    def test_get_multi_frame(self):
        dest = os.path.join(self.dir.name, "a.jpg")
        result = self.mock("get", "/EXT0/DCIM/100RICOH/RIMG0001.JPG", dest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(dest).read_bytes(), bytes(range(256)) * 700)
        self.assertEqual(self.sent(), ["RETR /EXT0/DCIM/100RICOH/RIMG0001.JPG\r\n"])

    def test_get_refuses_overwrite(self):
        dest = os.path.join(self.dir.name, "a.jpg")
        Path(dest).write_bytes(b"keep")
        result = self.mock("get", "/EXT0/DCIM/100RICOH/RIMG0001.JPG", dest)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(Path(dest).read_bytes(), b"keep")
        self.assertFalse(os.path.exists(self.log) and self.record()["events"], "nothing may touch the camera")

    def test_failed_get_leaves_no_file(self):
        for fault in ("short-transfer", "long-transfer", "open-error", "timeout", "bad-trailer"):
            with self.subTest(fault=fault):
                dest = os.path.join(self.dir.name, f"{fault}.jpg")
                result = self.mock("get", "/EXT0/DCIM/100RICOH/RIMG0001.JPG", dest, fault=fault)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual([n for n in os.listdir(self.dir.name) if n not in ("log.json", "transfers.jsonl")], [])

    ANOMALIES = ("timeout", "truncated-frame", "bad-start", "bad-reserved", "bad-trailer", "bad-channel", "oversize",
                 "garbage-reply", "data-as-reply")

    def test_malformed_replies_stop_after_first_command_with_power_cycle_advice(self):
        for command, first, extra in (("info", "-GCID\r\n", ()), ("ver", "-VER\r\n", ("wrong-model",))):
            for fault in self.ANOMALIES + extra:
                with self.subTest(command=command, fault=fault):
                    result = self.mock(command, fault=fault)
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertRegex(result.stderr, r"error: (ProtocolError|TransportTimeout)")
                    self.assertIn("Power cycle the camera", result.stderr)
                    self.assertIn("Nothing was retried", result.stderr)
                    self.assertEqual(self.sent(), [first])
                    self.assertEqual(self.record()["events"][-2:], ["release_interface(0)", "dispose_resources"])

    def test_short_reads_reassemble(self):
        result = self.mock("ver", fault="short-read")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("firmware 02.40", result.stdout)

    def test_bad_listing(self):
        result = self.mock("ls", "/EXT0", fault="bad-listing")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a multiple of 32", result.stderr)

    def test_no_card_is_reported(self):
        result = self.mock("info", fault="no-card")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("/EXT0: unavailable", result.stdout)
        self.assertNotIn("/IROM:", result.stdout)

    def test_descriptor_mismatch_sends_nothing(self):
        for variant in ("mass-storage", "ptp", "wrong-vendor", "wrong-class", "wrong-endpoints", "wrong-packet"):
            for command in (("ver",), ("info",), ("ls", "/EXT0"), ("get", "/EXT0/x", os.path.join(self.dir.name, "x"))):
                with self.subTest(variant=variant, command=command[0]):
                    result = self.mock(*command, descriptors=variant)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("nothing was sent", result.stderr)
                    self.assertEqual(self.sent(), [])

    def test_string_mismatch_sends_nothing(self):
        result = self.mock("ver", strings="wrong")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.record().get("events", []), [])

    def test_list_reports_gate(self):
        self.assertIn("gate: pass", self.mock("list").stdout)
        self.assertIn("gate: REFUSE", self.mock("list", descriptors="mass-storage").stdout)

    def test_bad_paths_refused_before_any_usb(self):
        for path in ("/EXT0/a b", "/EXT0/a,b", "/EXT0/a\r\nDELE /EXT0/x", "/ATA1/x", "relative", "/EXT0/../x",
                     "/EXT0/" + "a" * 300, "/EXT0/café"):
            with self.subTest(path=path):
                result = self.mock("ls", path)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(os.path.exists(self.log), "refused before the camera was touched")
                self.assertFalse(os.path.exists(self.tlog))

    # ---- ver ----
    def test_ver_sends_one_request_reads_one_reply_and_disconnects(self):
        result = self.mock("ver")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sent(), ["-VER\r\n"])
        self.assertEqual(self.record()["events"][-2:], ["release_interface(0)", "dispose_resources"])
        self.assertIn("firmware 02.40", result.stdout)
        self.assertIn("beyond the 3 s timer", result.stdout)
        self.assertIn("Do not run another grdusb command", result.stdout)
        self.assertLess(result.stdout.index("-VER reply accepted"), result.stdout.index("OBSERVE NOW"))

    def test_ver_note_prints_after_the_request_not_before(self):
        result = self.mock("ver")
        self.assertEqual(result.stdout.count("OBSERVE NOW"), 1)

    def test_ver_note_also_prints_when_the_reply_is_bad(self):
        result = self.mock("ver", fault="wrong-model")
        self.assertEqual(result.returncode, 1)
        self.assertIn("OBSERVE NOW", result.stdout)

    def test_ver_refuses_line_state_fallback_everywhere(self):
        for extra in ((), ("--dry-run",)):
            result = run("--mock", "--mock-log", self.log, "--transfer-log", self.tlog, "--line-state-fallback",
                         *extra, "ver")
            self.assertEqual(result.returncode, 2)
            self.assertIn("not allowed with ver", result.stderr)
            self.assertFalse(os.path.exists(self.tlog))
        self.assertFalse(os.path.exists(self.log))

    def test_transfer_log_records_every_transfer_with_timestamps(self):
        self.mock("ver")
        records = self.transfers()
        kinds = [r["event"] for r in records]
        self.assertEqual(kinds, ["log_open", "bring_up_begin", "bring_up_done", "write", "read", "close_done",
                                 "log_close"])
        write, read = records[3], records[4]
        self.assertEqual(bytes.fromhex(write["hex"])[8:13], b"-VER\r")
        self.assertEqual(write["accepted"], write["bytes"])
        self.assertIn(b"200 9.17 02.40", bytes.fromhex(read["hex"]))
        self.assertTrue(all("wall" in r and "mono" in r and r["wall"].endswith("+00:00") for r in records))
        self.assertEqual([r["seq"] for r in records], list(range(1, len(records) + 1)))

    def test_transfer_log_keeps_failures(self):
        self.mock("ver", fault="timeout")
        kinds = [r["event"] for r in self.transfers()]
        self.assertIn("read_failed", kinds)
        self.assertIn("anomaly", kinds)
        self.assertEqual(kinds[-2:], ["close_done", "log_close"])

    def test_transfer_log_is_never_overwritten_and_failure_sends_nothing(self):
        Path(self.tlog).write_text("keep")
        result = self.mock("ver", fresh_log=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot create the transfer log", result.stderr)
        self.assertEqual(Path(self.tlog).read_text(), "keep")
        self.assertFalse(os.path.exists(self.log), "the camera must not be touched when logging is impossible")

    # ---- dry run and exact request lists ----
    def dry(self, *args):
        result = run("--dry-run", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no USB access of any kind", result.stdout)
        return [line.split(". ", 1)[1].encode().decode("unicode_escape") for line in result.stdout.splitlines()
                if line.startswith("  ") and ". " in line]

    def test_dry_run_matches_what_the_mock_receives(self):
        get_dest = os.path.join(self.dir.name, "p.jpg")
        cases = [(("ver",), ["-VER\r\n"]),
                 (("info",), ["-GCID\r\n", "-GSID\r\n", "-TIME\r\n", "-DCAP /EXT0\r\n"]),
                 (("info", "--include-internal"), ["-GCID\r\n", "-GSID\r\n", "-TIME\r\n", "-DCAP /EXT0\r\n",
                                                  "-DCAP /IROM\r\n"]),
                 (("ls", "/EXT0/DCIM"), ["-NLST /EXT0/DCIM\r\n"]),
                 (("get", "/EXT0/DCIM/100RICOH/RIMG0001.JPG", get_dest), ["RETR /EXT0/DCIM/100RICOH/RIMG0001.JPG\r\n"])]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(self.dry(*command), expected)
                self.assertFalse(os.path.exists(get_dest))
                self.assertEqual(self.mock(*command).returncode, 0)
                self.assertEqual(self.sent(), expected)
                if os.path.exists(get_dest):
                    os.unlink(get_dest)

    def test_dry_run_touches_nothing(self):
        for command in (("list",), ("ver",), ("info",), ("ls", "/EXT0")):
            result = run("--dry-run", "--live", "--transfer-log", self.tlog, *command)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(os.path.exists(self.tlog))
            self.assertNotIn("Traceback", result.stderr)
            self.assertNotIn("found", result.stdout, "list --dry-run must not enumerate")

    def test_dry_run_reports_opt_in_control_request_and_refuses_bad_paths(self):
        result = run("--dry-run", "--line-state-fallback", "info")
        self.assertIn("control 0x41/0x22 wValue 3 (opt-in, unverified)", result.stdout)
        self.assertNotIn("control 0x41", run("--dry-run", "info").stdout)
        result = run("--dry-run", "get", "/IROM/SCRIPT.MG1", "x")
        self.assertEqual(result.returncode, 2)

    def test_no_command_sends_ver_unless_it_is_ver(self):
        for command in (("info",), ("info", "--include-internal"), ("ls", "/EXT0"), ("ls", "/")):
            with self.subTest(command=command):
                self.assertEqual(self.mock(*command).returncode, 0)
                self.assertNotIn("-VER\r\n", self.sent())

    # ---- info and /IROM ----
    def test_info_is_card_only_by_default(self):
        result = self.mock("info")
        self.assertNotIn("/IROM:", result.stdout)
        self.assertNotIn("/IROM", " ".join(self.sent()))
        self.assertIn("never probed", result.stdout)

    def test_info_internal_is_explicit(self):
        result = self.mock("info", "--include-internal")
        self.assertIn("/IROM: capacity", result.stdout)
        self.assertIn("-DCAP /IROM\r\n", self.sent())

    # ---- get and /IROM ----
    def test_get_refuses_irom_before_any_usb(self):
        for path in ("/IROM", "/IROM/SCRIPT.MG1", "/IROM/SYSTEM/SYSTEM.PRM"):
            with self.subTest(path=path):
                if os.path.exists(self.log):
                    os.unlink(self.log)
                dest = os.path.join(self.dir.name, "irom.bin")
                result = self.mock("get", path, dest)
                self.assertEqual(result.returncode, 2)
                self.assertIn("refuses /IROM", result.stderr)
                self.assertIn("filesystem write a repair", result.stderr)
                self.assertFalse(os.path.exists(self.log))
                self.assertFalse(os.path.exists(dest))

    def test_get_prints_the_repair_caveat_before_sending(self):
        result, dest = self.get("RIMG0001.JPG")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("can write a filesystem repair", result.stdout)
        self.assertIn("docs/internals/07-usb-protocol.md", result.stdout)

    # ---- anomalies during a transfer ----
    def test_anomaly_mid_transfer_says_power_cycle_and_does_not_retry(self):
        for fault in ("mid-timeout", "mid-bad-frame", "short-transfer", "long-transfer"):
            with self.subTest(fault=fault):
                result, dest = self.get("RIMG0001.JPG", fault=fault)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("may retain transfer state", result.stderr)
                self.assertIn("Power cycle the camera", result.stderr)
                self.assertEqual(self.sent(), ["RETR /EXT0/DCIM/100RICOH/RIMG0001.JPG\r\n"])
                self.assertFalse(os.path.exists(dest))
                self.assertEqual(self.record()["events"][-1], "dispose_resources")

    def test_sink_failure_mid_transfer_says_power_cycle(self):
        import resource
        import signal

        def limit():
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (100000, 100000))

        dest = os.path.join(self.dir.name, "big.jpg")
        result = subprocess.run([sys.executable, "-m", "tools.grdusb", "--mock", "--mock-log", self.log,
                                 "--transfer-log", self.tlog, "get", "/EXT0/DCIM/100RICOH/RIMG0001.JPG", dest],
                                cwd=ROOT, capture_output=True, text=True, timeout=60, preexec_fn=limit)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("SinkError", result.stderr)
        self.assertIn("Power cycle the camera", result.stderr)
        self.assertFalse(os.path.exists(dest))

    def test_well_formed_camera_failure_is_not_an_anomaly(self):
        result, dest = self.get("RIMG0001.JPG", fault="open-error")
        self.assertEqual(result.returncode, 1)
        self.assertIn("CameraError", result.stderr)
        self.assertNotIn("Power cycle", result.stderr)

    # ---- short writes ----
    def test_short_write_is_a_failure(self):
        for command in (("ver",), ("info",), ("ls", "/EXT0")):
            with self.subTest(command=command):
                result = self.mock(*command, fault="short-write")
                self.assertEqual(result.returncode, 1)
                self.assertIn("ShortWrite", result.stderr)
                self.assertIn("accepted", result.stderr)
                self.assertIn("Power cycle the camera", result.stderr)
                self.assertEqual(len(self.sent()), 1, "no further request after a short write")

    # ---- bring-up ----
    def test_bring_up_failures_release_everything(self):
        for fault, step in (("configure-fails", "set_configuration(1)"), ("claim-fails", "claim_interface(0)")):
            with self.subTest(fault=fault):
                result = self.mock("ver", fault=fault)
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"bring-up failed during {step}", result.stderr)
                self.assertIn("no command was sent", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.sent(), [])
                events = self.record()["events"]
                self.assertEqual(events[-1], "dispose_resources")
                self.assertEqual("release_interface(0)" in events, False)

    def test_already_configured_elsewhere_is_refused_before_set_configuration(self):
        result = self.mock("ver", fault="already-config-2")
        self.assertEqual(result.returncode, 2)
        self.assertIn("only GET_CONFIGURATION was sent", result.stderr)
        self.assertNotIn("nothing was sent", result.stderr)
        events = self.record()["events"]
        self.assertNotIn("set_configuration(1)", events)
        self.assertEqual(events[0], "get_configuration")
        self.assertEqual(events[-1], "dispose_resources")

    def test_refusal_after_set_configuration_does_not_claim_nothing_was_sent(self):
        result = self.mock("ver", fault="config-sticks-at-2")
        self.assertEqual(result.returncode, 2)
        self.assertIn("after set_configuration(1) was sent", result.stderr)
        self.assertNotIn("nothing was sent", result.stderr)
        self.assertIn("set_configuration(1)", self.record()["events"])
        self.assertEqual(self.record()["events"][-1], "dispose_resources")
        self.assertEqual(self.sent(), [])

    def test_without_target_flag_refuses(self):
        result = run("info")
        self.assertEqual(result.returncode, 2)
        self.assertIn("pass --live", result.stderr)


class Boundary(unittest.TestCase):
    """The client code must hold no way to emit a state-changing command, and no vendor code or data."""
    CLIENT = ("protocol.py", "session.py", "transport.py", "cli.py", "descriptors.py", "plan.py", "transferlog.py")

    def test_no_forbidden_verbs_in_client(self):
        forbidden = r"-STOR|DELE|RMD|RNFR|RNTO|-FMT|-CCTL|-FUPL|-FDWL|-SDIS|-PWOF|-ATTR|-ABOR|\bCWD\b|\bMKD\b"
        for name in self.CLIENT:
            text = (PKG / name).read_text()
            self.assertIsNone(re.search(forbidden, text.replace("a\\r\\nDELE", "")), name)

    def test_command_builders_are_closed(self):
        from tools.grdusb.protocol import CHANNEL_DATA, CommandRefused, encode_frame, fixed_command, path_command
        for name in ("delete", "store", "format", "-STOR"):
            with self.assertRaises(CommandRefused):
                fixed_command(name)
            with self.assertRaises(CommandRefused):
                path_command(name, "/EXT0/x")
        with self.assertRaises(CommandRefused):
            encode_frame(CHANNEL_DATA, b"x")

    def test_no_reset_or_clear_halt_and_control_only_in_transport(self):
        for name in self.CLIENT:
            text = (PKG / name).read_text()
            self.assertIsNone(re.search(r"\.reset\(|clear_halt|reset_device|DeviceReset", text), name)
            if name != "transport.py":
                self.assertNotIn("ctrl_transfer", text, name)

    def test_bring_up_with_injected_timing(self):
        from tools.grdusb.transport import GateRefused, configure_device

        class Config:
            def __init__(self, value):
                self.bConfigurationValue = value

        class Device:
            def __init__(self, active=1):
                self.calls, self.active = [], active

            def set_configuration(self, value):
                self.calls.append(("configure", value))

            def get_active_configuration(self):
                return Config(self.active)

            def ctrl_transfer(self, *args):
                self.calls.append(("control", args))

        log = []
        device = Device()
        configure_device(device, lambda d, n: log.append(("claim", n)), lambda s: log.append(("sleep", s)), 2.0)
        self.assertEqual(device.calls, [("configure", 1)])
        self.assertEqual(log, [("sleep", 2.0), ("claim", 0)])
        log.clear()
        device = Device()
        configure_device(device, lambda d, n: log.append(("claim", n)), lambda s: log.append(("sleep", s)), 2.0, True)
        self.assertEqual(device.calls, [("configure", 1), ("control", (0x41, 0x22, 3, 0, None))])
        with self.assertRaises(GateRefused):
            configure_device(Device(2), lambda d, n: None, lambda s: None, 2.0)

    def test_version_is_requested_from_exactly_one_place(self):
        calls = [name for name in self.CLIENT if name != "protocol.py"
                 for _ in re.finditer(r'fixed_command\("version"\)|\.version\(\)', (PKG / name).read_text())]
        self.assertEqual(sorted(calls), ["cli.py", "cli.py", "plan.py", "plan.py", "session.py"],
                         "plan (ver, version gate), ver command, version gate and Session.version only")
        self.assertNotIn("identify", (PKG / "session.py").read_text())

    def test_bring_up_gate_ordering_and_messages(self):
        from tools.grdusb.transport import GateRefused, TransportError, configure_device

        class Config:
            def __init__(self, value):
                self.bConfigurationValue = value

        class Dev:
            def __init__(self, before, after=1, fail=None):
                self.calls, self.value, self.after, self.fail = [], before, after, fail

            def get_active_configuration(self):
                self.calls.append("get")
                return Config(self.value)

            def set_configuration(self, value):
                self.calls.append("set")
                if self.fail == "set":
                    raise RuntimeError("boom")
                self.value = self.after

        claimed = []
        device = Dev(2)
        with self.assertRaises(GateRefused) as caught:
            configure_device(device, lambda d, n: claimed.append(n), lambda s: None, 0)
        self.assertEqual(device.calls, ["get"])
        self.assertIn("only GET_CONFIGURATION was sent", str(caught.exception))
        self.assertNotIn("nothing was sent", str(caught.exception))
        device = Dev(0, after=2)
        with self.assertRaises(GateRefused) as caught:
            configure_device(device, lambda d, n: claimed.append(n), lambda s: None, 0)
        self.assertEqual(device.calls, ["get", "set", "get"])
        self.assertIn("after set_configuration(1) was sent", str(caught.exception))
        self.assertNotIn("nothing was sent", str(caught.exception))
        self.assertEqual(claimed, [])
        with self.assertRaises(TransportError) as caught:
            configure_device(Dev(0, fail="set"), lambda d, n: None, lambda s: None, 0)
        self.assertIn("bring-up failed during set_configuration(1)", str(caught.exception))
        # an unconfigured device that libusb reports as an error counts as configuration 0
        class Unconfigured(Dev):
            def get_active_configuration(self):
                if self.value == 0:
                    raise LookupError("not configured")
                return super().get_active_configuration()
        device = Unconfigured(0)
        configure_device(device, lambda d, n: claimed.append(n), lambda s: None, 0, False, (LookupError,))
        self.assertEqual(claimed, [0])

    def test_usb_transport_close_and_write_with_fake_libusb(self):
        from tools.grdusb.transport import TransportError, TransportTimeout, UsbTransport

        class USBError(Exception):
            pass

        class USBTimeoutError(USBError):
            pass

        class Core:
            pass
        Core.USBError, Core.USBTimeoutError = USBError, USBTimeoutError

        class Util:
            def __init__(self, fail_release=False):
                self.calls, self.fail_release = [], fail_release

            def claim_interface(self, device, number):
                self.calls.append("claim")

            def release_interface(self, device, number):
                self.calls.append("release")
                if self.fail_release:
                    raise USBError("release failed")

            def dispose_resources(self, device):
                self.calls.append("dispose")

        class Dev:
            def __init__(self, result=None, error=None):
                self.result, self.error = result, error

            def write(self, endpoint, data, timeout):
                if self.error:
                    raise self.error
                return self.result

            def get_active_configuration(self):
                raise USBError("unconfigured")

            def set_configuration(self, value):
                raise USBError("stall")

        util = Util()
        transport = UsbTransport(Dev(), Core, util, sleep=lambda s: None)
        with self.assertRaises(TransportError):  # set_configuration fails; the handle must still be released
            transport.bring_up()
        transport.close()
        self.assertEqual(util.calls, ["dispose"])  # never claimed, so no release call
        util = Util(fail_release=True)
        transport = UsbTransport(Dev(), Core, util, sleep=lambda s: None)
        transport._claimed = True
        with self.assertRaises(USBError):
            transport.close()
        self.assertEqual(util.calls, ["release", "dispose"], "dispose must run even when release fails")
        self.assertEqual(UsbTransport(Dev(result=7), Core, Util()).write(b"x" * 8), 7)
        with self.assertRaises(TransportTimeout):
            UsbTransport(Dev(error=USBTimeoutError("t")), Core, Util()).write(b"x")

    def test_open_gated_releases_the_handle_when_strings_fail_or_mismatch(self):
        from tools.grdusb import mock
        from tools.grdusb.transport import GateRefused, TransportError, open_gated

        def endpoint(e):
            return type("E", (), {"bEndpointAddress": e.address, "bmAttributes": e.attributes,
                                  "wMaxPacketSize": e.max_packet})()

        spec = mock.DESCRIPTOR_VARIANTS["original"]

        class Interface(list):
            bInterfaceNumber = 0
            bInterfaceClass, bInterfaceSubClass, bInterfaceProtocol = 0xff, 0, 0

        class Config(list):
            bConfigurationValue = 1

        class Dev:
            idVendor, idProduct, bcdUSB, bNumConfigurations, bus, address = 0x05ca, 0x2218, 0x200, 2, 1, 2

            def __init__(self, manufacturer, product):
                self._m, self._p = manufacturer, product

            def __iter__(self):
                return iter([Config([Interface(endpoint(e) for e in spec.configurations[0].interfaces[0].endpoints)])])

            @property
            def manufacturer(self):
                if isinstance(self._m, Exception):
                    raise self._m
                return self._m

            @property
            def product(self):
                return self._p

        class Core:
            devices = []

            @classmethod
            def find(cls, **kwargs):
                return cls.devices

        class Util:
            disposed = 0

            @classmethod
            def dispose_resources(cls, device):
                cls.disposed += 1

        Core.devices = [Dev("Ricoh Company Ltd.", "Caplio R5")]
        with self.assertRaises(GateRefused) as caught:
            open_gated(usb_core=Core, usb_util=Util, backend=object())
        self.assertEqual(Util.disposed, 1)
        self.assertNotIn("nothing was sent", str(caught.exception))
        self.assertIn("no configuration change and no command was sent", str(caught.exception))
        Core.devices = [Dev(OSError("string read failed"), "GR DIGITAL")]
        with self.assertRaises(TransportError):
            open_gated(usb_core=Core, usb_util=Util, backend=object())
        self.assertEqual(Util.disposed, 2)
        Core.devices = [Dev("a", "b"), Dev("a", "b")]
        with self.assertRaises(GateRefused):
            open_gated(usb_core=Core, usb_util=Util, backend=object())
        self.assertEqual(Util.disposed, 2, "two devices: nothing opened, nothing to release")
        Core.devices = [Dev("Ricoh Company Ltd.", "GR DIGITAL")]
        self.assertIsNotNone(open_gated(usb_core=Core, usb_util=Util, backend=object()))

    def test_session_treats_short_or_missing_write_counts_as_failure(self):
        from tools.grdusb.session import Session, ShortWrite

        class Transport:
            def __init__(self, result):
                self.result, self.reads = result, 0

            def write(self, data):
                return self.result(data) if callable(self.result) else self.result

            def read(self):
                self.reads += 1
                return b""

        for result in (lambda data: len(data) - 1, 0, None):
            transport = Transport(result)
            with self.assertRaises(ShortWrite):
                Session(transport).version()
            self.assertEqual(transport.reads, 0, "no reply is awaited after a short write")

    def test_session_wraps_sink_failure(self):
        from tools.grdusb.mock import MockTransport
        from tools.grdusb.session import Session, SinkError

        transport = MockTransport()
        transport.bring_up()
        session = Session(transport)

        def broken(_data):
            raise OSError(28, "No space left on device")

        with self.assertRaises(SinkError):
            session.retrieve("/EXT0/DCIM/100RICOH/RIMG0001.JPG", broken)

    def test_no_binaries_bundled(self):
        names = {p.name.lower() for p in PKG.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        self.assertFalse({n for n in names if n.endswith((".dll", ".exe", ".bin", ".elf")) or n.startswith("qrflect")})


class Gate(unittest.TestCase):
    """--require-version, --require-camera-id, get --allow-internal and get-many, against the simulated camera."""
    setUp, mock, record, sent, transfers, dry = (Cli.setUp, Cli.mock, Cli.record, Cli.sent, Cli.transfers,
                                                 Cli.dry)

    def test_version_match_passes_and_ver_sends_one_ver(self):
        result = self.mock("--require-version", "01.08", "--require-version", "02.40", "info")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sent(), ["-VER\r\n", "-GCID\r\n", "-GSID\r\n", "-TIME\r\n", "-DCAP /EXT0\r\n"])
        result = self.mock("--require-version", "02.40", "ver")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("firmware 02.40", result.stdout)
        self.assertEqual(self.sent(), ["-VER\r\n"])

    def test_version_mismatch_refuses_and_sends_nothing_else(self):
        for command in (("info",), ("ls", "/EXT0"), ("ver",)):
            with self.subTest(command=command):
                result = self.mock("--require-version", "01.08", *command)
                self.assertEqual(result.returncode, 2)
                self.assertIn("firmware 02.40", result.stderr)
                self.assertIn("01.08", result.stderr)
                self.assertEqual(self.sent(), ["-VER\r\n"])
                self.assertEqual(self.record()["events"][-2:], ["release_interface(0)", "dispose_resources"])
        result = self.mock("--require-version", "01.08", "--mock-version", "01.08", "info")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_camera_id_gate(self):
        result = self.mock("--require-version", "02.40", "--require-camera-id", "0011223344556677", "ls", "/EXT0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sent(), ["-VER\r\n", "-GCID\r\n", "-NLST /EXT0\r\n"])
        result = self.mock("--require-version", "02.40", "--require-camera-id", "DEADBEEF", "ls", "/EXT0")
        self.assertEqual(result.returncode, 2)
        self.assertIn("0011223344556677", result.stderr)
        self.assertEqual(self.sent(), ["-VER\r\n", "-GCID\r\n"])
        os.unlink(self.log)
        result = self.mock("--require-camera-id", "0011223344556677", "ls", "/EXT0")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(os.path.exists(self.log), "refused before any USB access")

    def test_bad_version_syntax_is_rejected(self):
        self.assertEqual(run("--mock", "--require-version", "1.8", "ver").returncode, 2)

    def test_dry_run_lists_gating_requests_in_order(self):
        self.assertEqual(self.dry("--require-version", "01.08", "info"),
                         ["-VER\r\n", "-GCID\r\n", "-GSID\r\n", "-TIME\r\n", "-DCAP /EXT0\r\n"])
        self.assertEqual(self.dry("--require-version", "01.08", "--require-camera-id", "AB", "ls", "/EXT0"),
                         ["-VER\r\n", "-GCID\r\n", "-NLST /EXT0\r\n"])
        self.assertEqual(self.dry("--require-version", "01.08", "ver"), ["-VER\r\n"])
        self.assertEqual(self.dry("--require-version", "01.08", "--require-camera-id", "AB", "ver"),
                         ["-VER\r\n", "-GCID\r\n"])

    def test_irom_get_needs_both_flags(self):
        dest = os.path.join(self.dir.name, "one.bin")
        path = "/IROM/DATA/ONE.BIN"
        for args in (("get", path, dest), ("--require-version", "02.40", "get", path, dest),
                     ("get", "--allow-internal", path, dest)):
            with self.subTest(args=args):
                result = self.mock(*args)
                self.assertEqual(result.returncode, 2)
                self.assertRegex(result.stderr, r"--allow-internal")
                self.assertRegex(result.stderr, r"--require-version")
                self.assertFalse(os.path.exists(dest))
                self.assertFalse(os.path.exists(self.log))
        result = self.mock("--require-version", "02.40", "get", "--allow-internal", path, dest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(dest).read_bytes(), b"internal-one")
        self.assertEqual(self.sent(), ["-VER\r\n", f"RETR {path}\r\n"])
        self.assertIn("version-gated body", result.stdout)
        self.assertIn("can write a filesystem repair", result.stdout)
        again = self.mock("--require-version", "02.40", "get", "--allow-internal", path, dest)
        self.assertEqual(again.returncode, 1)
        self.assertIn("not overwriting", again.stderr)

    def test_irom_get_with_wrong_version_sends_no_retr(self):
        dest = os.path.join(self.dir.name, "one.bin")
        result = self.mock("--require-version", "01.08", "get", "--allow-internal", "/IROM/DATA/ONE.BIN", dest)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.sent(), ["-VER\r\n"])
        self.assertFalse(os.path.exists(dest))

    def test_get_many(self):
        out = os.path.join(self.dir.name, "out")
        os.mkdir(out)
        result = self.mock("--require-version", "02.40", "get-many", "/EXT0/DCIM/MANY", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sent(), ["-VER\r\n", "-NLST /EXT0/DCIM/MANY\r\n", "RETR /EXT0/DCIM/MANY/A.JPG\r\n",
                                       "RETR /EXT0/DCIM/MANY/B.JPG\r\n"])
        self.assertEqual(sorted(os.listdir(out)), ["A.JPG", "B.JPG", "grdusb-manifest.json"])
        manifest = json.loads(Path(out, "grdusb-manifest.json").read_text())
        self.assertTrue(manifest["complete"])
        import hashlib
        self.assertEqual([(f["remote"], f["listed_size"], f["bytes_received"], f["sha256"]) for f in manifest["files"]],
                         [("/EXT0/DCIM/MANY/A.JPG", 3, 3, hashlib.sha256(b"aaa").hexdigest()),
                          ("/EXT0/DCIM/MANY/B.JPG", 4, 4, hashlib.sha256(b"bbbb").hexdigest())])
        again = self.mock("get-many", "/EXT0/DCIM/MANY", out)
        self.assertEqual(again.returncode, 2)
        self.assertEqual(self.sent(), ["-NLST /EXT0/DCIM/MANY\r\n"])

    def test_get_many_duplicate_listed_names_all_saved(self):
        out = os.path.join(self.dir.name, "out")
        os.mkdir(out)
        result = self.mock("get-many", "/EXT0/DCIM/MANY", out, fault="dup-names")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("INCOMPLETE", result.stderr)
        alias = "/EXT0/DCIM/MANY/_R001~39.JPG"
        self.assertEqual(self.sent().count(f"RETR {alias}\r\n"), 3)
        names = ["A.JPG", "_R001~39.JPG", "_R001~39.JPG.dup1", "_R001~39.JPG.dup2", "B.JPG"]
        self.assertEqual(sorted(os.listdir(out)), sorted(names + ["grdusb-manifest.json"]))
        manifest = json.loads(Path(out, "grdusb-manifest.json").read_text())
        self.assertTrue(manifest["complete"])
        self.assertEqual([f["local"] for f in manifest["files"]], names)
        self.assertEqual(manifest["duplicates"], ["_R001~39.JPG", "_R001~39.JPG"])
        self.assertEqual([f["remote"] for f in manifest["files"]].count(alias), 3)
        self.assertTrue(all(f["bytes_received"] == f["listed_size"] for f in manifest["files"]))
        self.assertEqual(Path(out, "_R001~39.JPG.dup2").read_bytes(), b"sidecr")
        again = self.mock("get-many", "/EXT0/DCIM/MANY", out, fault="dup-names")
        self.assertEqual(again.returncode, 2)
        self.assertNotIn("RETR", "".join(self.sent()))

    def test_get_many_preexisting_dup_destination_refused_before_retr(self):
        out = os.path.join(self.dir.name, "out")
        os.mkdir(out)
        Path(out, "_R001~39.JPG.dup1").write_bytes(b"old")
        result = self.mock("get-many", "/EXT0/DCIM/MANY", out, fault="dup-names")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("RETR", "".join(self.sent()))

    def test_get_many_early_stop_reports_incomplete_and_fails(self):
        out = os.path.join(self.dir.name, "out")
        os.mkdir(out)
        result = self.mock("get-many", "/EXT0/DCIM/MANY", out, fault="mid-timeout")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip().splitlines()[-1], "INCOMPLETE: 0 of 2 files copied; see manifest")
        manifest = json.loads(Path(out, "grdusb-manifest.json").read_text())
        self.assertFalse(manifest["complete"])
        self.assertEqual(manifest["files"], [])

    def test_get_many_irom_gating_and_dry_run(self):
        out = os.path.join(self.dir.name, "out")
        os.mkdir(out)
        self.assertEqual(self.mock("get-many", "/IROM/DATA", out).returncode, 2)
        self.assertFalse(os.path.exists(self.log))
        self.assertEqual(self.dry("--require-version", "01.08", "get-many", "--allow-internal", "/IROM/DATA", out),
                         ["-VER\r\n", "-NLST /IROM/DATA\r\n"])
        result = self.mock("--require-version", "02.40", "get-many", "--allow-internal", "/IROM/DATA", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("version-gated body", result.stdout)
        self.assertEqual(sorted(os.listdir(out)), ["ONE.BIN", "TWO.BIN", "grdusb-manifest.json"])
        self.assertEqual(self.mock("--require-version", "01.08", "get-many", "--allow-internal", "/IROM/DATA",
                                   os.path.join(self.dir.name, "nodir")).returncode, 1)

    def test_get_many_needs_existing_dest_dir(self):
        result = self.mock("get-many", "/EXT0/DCIM/MANY", os.path.join(self.dir.name, "missing"))
        self.assertEqual(result.returncode, 1)
        self.assertFalse(os.path.exists(self.log))


SPARE = ("--require-version", "01.08", "--require-camera-id", "0011223344556677")
STAGE = ("stage-update", "--i-understand-this-writes-the-card")


class StageUpdate(unittest.TestCase):
    """stage-update against the simulated camera with a writable card. No update-named file is created on disk."""
    setUp, mock, record, sent, transfers, dry = (Cli.setUp, Cli.mock, Cli.record, Cli.sent, Cli.transfers, Cli.dry)

    @classmethod
    def setUpClass(cls):
        from tools.grdusb import stage
        cls.stage = stage
        if not all((s.directory / p.source).exists() for s in stage.PAYLOAD_SETS.values() for p in s.payloads):
            raise unittest.SkipTest("Ricoh's update files are not in firmware/ (see firmware/README.md)")
        cls.loaded = stage.load_payloads()
        cls.wire = stage.planned_wire(cls.loaded)

    def spare(self, *extra, fault="none", version="01.08"):
        return self.mock("--mock-version", version, *SPARE, *STAGE, *extra, fault=fault)

    def assert_stopped_after(self, result, last, code=1):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        sent = self.sent()
        self.assertEqual(sent, self.wire[:len(sent)], "a prefix of the planned requests, then nothing")
        self.assertEqual(sent[-1], last)
        self.assertIn("sent, in order:", result.stderr)
        self.assertIn("card state:", result.stderr)
        self.assertNotIn("Nothing has been flashed", result.stdout)
        self.assertEqual(self.record()["events"][-2:], ["release_interface(0)", "dispose_resources"])
        return sent

    # ---- happy path, dry run, log ----
    def test_happy_path_mirrors_ricoh_and_verifies(self):
        result = self.spare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sent(), self.wire)
        self.assertEqual(self.wire[:4], ["-VER\r\n", "-GCID\r\n", "-DCAP /EXT0\r\n", "-NLST /EXT0\r\n"])
        self.assertEqual(self.wire[4:14], [f"DELE /EXT0/QRFLECT{n}\r\n" for n in range(10)])
        self.assertEqual(self.wire[14], "-STOR 2202472 /EXT0/QRFLECT3\r\n")
        self.assertEqual(self.wire[-4:], ["RETR /EXT0/QRFLECT3\r\n", "RETR /EXT0/QRFLECT7\r\n", "-DCAP /EXT0\r\n",
                                          "-NLST /EXT0\r\n"])
        frames = [int(w[6:-1]) for w in self.wire if w.startswith("<data")]
        self.assertTrue(all(1 <= f <= 65536 for f in frames))
        self.assertEqual(sum(frames), 2202472 + 31914)
        self.assertIn("verified /EXT0/QRFLECT3: 2202472 bytes match", result.stdout)
        self.assertIn("verified /EXT0/QRFLECT7: 31914 bytes match", result.stdout)
        self.assertIn("(change -2234386)", result.stdout)
        self.assertRegex(result.stdout, r"file\s+2202472 .* QRFLECT3")
        self.assertIn("Set the mode dial to Movie", result.stdout)
        self.assertIn("Never cut the power while the lamp blinks", result.stdout)
        self.assertIn("Nothing has been flashed", result.stdout)
        self.assertTrue(any(t["event"] == "write" for t in self.transfers()))

    def test_existing_update_files_are_deleted_first(self):
        result = self.spare(fault="card-old-update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sent(), self.wire)
        self.assertIn("DELE /EXT0/QRFLECT3: 250 DELE command success.", result.stdout)

    def test_stale_qrflect_files_are_removed_and_the_exact_set_remains(self):
        result = self.stage_set("official-2.40-qrflect3", version="01.08", fault="card-old-update")
        self.assertEqual(result.returncode, 0, result.stderr)
        sent = self.sent()
        self.assertIn("DELE /EXT0/QRFLECT7\r\n", sent)
        self.assertNotIn("-STOR 31914 /EXT0/QRFLECT7\r\n", sent)
        self.assertEqual(sum(1 for s in sent if s.startswith("DELE")), 10)
        self.assertIn("QRFLECT3", result.stdout)
        self.assertNotRegex(result.stdout, r"file\s+\d+ .* QRFLECT7")

    def test_stale_qrflect5_is_swept_not_refused(self):
        result = self.spare(fault="card-qrflect5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DELE /EXT0/QRFLECT5\r\n", self.sent())

    def test_exact_set_check_stops_when_an_extra_qrflect_survives(self):
        result = self.spare(fault="stuck-qrflect")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("not exactly the staged set", result.stderr)
        self.assertIn("QRFLECT9", result.stderr)
        self.assertNotIn("Nothing has been flashed", result.stdout)

    def stage_set(self, name, *extra, version, fault="none"):
        return self.mock("--mock-version", version, "--require-version", version,
                         "--require-camera-id", "0011223344556677", "stage-update",
                         "--i-understand-this-writes-the-card", "--set", name, *extra, fault=fault)

    def test_each_set_stages_exactly_its_files(self):
        cases = [("official-2.40-pair", "01.08", [], ["QRFLECT3", "QRFLECT7"], "02.40"),
                 ("official-2.40-qrflect3", "02.31", [], ["QRFLECT3"], "02.40"),
                 ("official-2.31-qrflect3", "02.40", ["--allow-older-update"], ["QRFLECT3"], "02.31")]
        for name, version, extra, names, target in cases:
            with self.subTest(set=name):
                result = self.stage_set(name, *extra, version=version)
                self.assertEqual(result.returncode, 0, result.stderr)
                loaded = self.stage.load_payloads(name)
                self.assertEqual(self.sent(), self.stage.planned_wire(loaded))
                self.assertEqual(self.stage.target_version(loaded), target)
                self.assertEqual(self.stage.staged_names(loaded), names)
                self.assertEqual(sum(1 for s in self.sent() if s.startswith("-STOR")), len(names))
                self.assertIn(f"set {name}", result.stdout)
                self.assertRegex(result.stdout, r"Nothing has been flashed.*" + re.escape(name))

    def test_older_update_needs_the_flag(self):
        for name, version in (("official-2.31-qrflect3", "02.40"), ("official-2.40-qrflect3", "02.40"),
                              ("official-2.31-qrflect3", "02.31")):
            with self.subTest(set=name, version=version):
                result = self.stage_set(name, version=version)
                self.assertEqual(result.returncode, 2)
                self.assertIn("already updated", result.stderr)
                self.assertFalse(os.path.exists(self.log), "refused before any USB access")
        # the flag lifts it for a strictly newer camera only; an equal version stays refused
        result = self.stage_set("official-2.31-qrflect3", "--allow-older-update", version="02.31")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(os.path.exists(self.log))
        # camera-side check also honours the flag
        result = self.stage_set("official-2.31-qrflect3", "--allow-older-update", version="02.40")
        self.assertEqual(result.returncode, 0, result.stderr)
        dry = run("--dry-run", "--require-version", "02.40", "--require-camera-id", "X", "stage-update",
                  "--i-understand-this-writes-the-card", "--set", "official-2.31-qrflect3", "--allow-older-update")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("8cbf2d3a", dry.stdout)

    def test_dry_run_lists_every_request_and_matches_the_mock(self):
        listed = self.dry(*SPARE, *STAGE)
        grouped = [w for i, w in enumerate(self.wire)
                   if not (w.startswith("<data") and i and self.wire[i - 1].startswith("<data"))]
        self.assertEqual(len(listed), len(grouped))
        for shown, expected in zip(listed, grouped):
            if expected.startswith("<data"):
                self.assertIn("data frame(s) on channel 2", shown)
            else:
                self.assertEqual(shown, expected)
        self.assertFalse(os.path.exists(self.log))
        result = run("--dry-run", "--live", "--transfer-log", self.tlog, *SPARE, *STAGE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(self.tlog))
        self.assertIn("34 data frame(s) on channel 2, 2202472 bytes", result.stdout)

    def test_transfer_log_is_never_overwritten(self):
        Path(self.tlog).write_text("earlier\n")
        result = self.mock("--mock-version", "01.08", *SPARE, *STAGE, fresh_log=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("nothing was sent", result.stderr)
        self.assertFalse(os.path.exists(self.log))
        self.assertEqual(Path(self.tlog).read_text(), "earlier\n")

    # ---- refusals before any USB access ----
    def test_option_refusals_send_nothing(self):
        cases = [(("--require-camera-id", "0011223344556677", *STAGE), "--require-version"),
                 (("--require-version", "01.08", *STAGE), "--require-camera-id"),
                 ((*SPARE, "stage-update"), "--i-understand-this-writes-the-card"),
                 (("--require-version", "01.08", "--require-version", "02.40", "--require-camera-id", "X", *STAGE),
                  "already updated")]
        for args, needle in cases:
            with self.subTest(args=args):
                result = self.mock("--mock-version", "01.08", *args)
                self.assertEqual(result.returncode, 2)
                self.assertIn(needle, result.stderr)
                self.assertFalse(os.path.exists(self.log), "refused before any USB access")
                self.assertFalse(os.path.exists(self.tlog))
                self.assertEqual(run("--dry-run", *args).returncode, 2)
        result = run(*SPARE, *STAGE)
        self.assertEqual(result.returncode, 2)
        self.assertIn("pass --live", result.stderr)

    def test_payload_hash_mismatch_is_refused(self):
        from tools.grdusb.protocol import CommandRefused
        with self.assertRaisesRegex(CommandRefused, "pinned"):
            self.stage.load_payloads(read=lambda path: b"not ricoh's file")
        self.assertEqual(self.stage.target_version(self.loaded), "02.40")
        self.assertEqual([len(item.data) for item in self.loaded], [2202472, 31914])
        for name in self.stage.PAYLOAD_SETS:
            with self.subTest(set=name), self.assertRaisesRegex(CommandRefused, "pinned"):
                self.stage.load_payloads(name, read=lambda path: b"not ricoh's file")
        with self.assertRaisesRegex(CommandRefused, "unknown payload set"):
            self.stage.load_payloads("other")
        self.assertEqual(self.stage.load_payloads("official-2.31-qrflect3")[0].payload.sha256,
                         "8cbf2d3a97376efe32dc9beceee9aed3a85a2e817b28ffd702330c688b91731c")

    def test_write_builders_are_closed(self):
        from tools.grdusb.protocol import CommandRefused
        for target in ("/EXT0/QUICKVER.UP", "/EXT0/SCRIPT.MG1", "/EXT0/QRFLECT10", "/IROM/QRFLECT3", "/EXT0/qrflect3"):
            with self.assertRaises(CommandRefused):
                self.stage.delete_command(target)
            with self.assertRaises(CommandRefused):
                self.stage.store_command(10, target)
        self.assertEqual(self.stage.delete_command("/EXT0/QRFLECT5"), b"DELE /EXT0/QRFLECT5\r\n")
        with self.assertRaises(CommandRefused):
            self.stage.store_command(10, "/EXT0/QRFLECT5")
        for size in (0, 10 ** 8):
            with self.assertRaises(CommandRefused):
                self.stage.store_command(size, "/EXT0/QRFLECT3")
        with self.assertRaises(CommandRefused):
            self.stage.encode_data_frame(b"x" * 65537)
        self.assertEqual(self.stage.store_command(5, "/EXT0/QRFLECT7"), b"-STOR 5 /EXT0/QRFLECT7\r\n")
        frame = self.stage.encode_data_frame(b"abc")
        self.assertEqual(frame, b"\x01\x02\x00\x00\x03\x00\x00\x00abc\x03")

    def test_write_verbs_only_in_stage_module(self):
        for name in Boundary.CLIENT:
            self.assertNotIn("stage.py", name)
        self.assertIn("DELE", (PKG / "stage.py").read_text())

    def test_read_only_commands_still_cannot_write_on_the_mock(self):
        from tools.grdusb import mock
        from tools.grdusb.protocol import encode_frame
        camera = mock.MockTransport(version="01.08")
        camera.bring_up()
        camera.write(encode_frame(1, b"DELE /EXT0/QRFLECT3\r\n"))
        camera.write(self.stage.encode_data_frame(b"abc"))
        self.assertEqual(len(camera.violations), 2)

    # ---- gate refusals on the camera ----
    def test_version_mismatch_sends_only_ver(self):
        result = self.spare(version="02.40")
        self.assertEqual(result.returncode, 2)
        self.assertIn("firmware 02.40", result.stderr)
        self.assertEqual(self.sent(), ["-VER\r\n"])
        self.assertIn("No write or delete command was sent", result.stderr)
        self.assertEqual(self.record()["events"][-2:], ["release_interface(0)", "dispose_resources"])

    def test_camera_id_mismatch(self):
        result = self.mock("--mock-version", "01.08", "--require-version", "01.08", "--require-camera-id", "DEAD",
                           *STAGE)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.sent(), ["-VER\r\n", "-GCID\r\n"])

    def test_no_card(self):
        result = self.spare(fault="no-card")
        self.assert_stopped_after(result, "-DCAP /EXT0\r\n", code=2)
        self.assertIn("Insert the SD memory card", result.stderr)

    def test_short_free_space(self):
        result = self.spare(fault="short-free")
        self.assert_stopped_after(result, "-DCAP /EXT0\r\n", code=2)
        self.assertIn("below 2234386 + 1048576", result.stderr)

    def test_dangerous_existing_files(self):
        for fault, name in (("card-quickver", "QUICKVER.UP"), ("card-script", "SCRIPT.MG1")):
            with self.subTest(fault=fault):
                result = self.spare(fault=fault)
                self.assert_stopped_after(result, "-NLST /EXT0\r\n", code=2)
                self.assertIn(name, result.stderr)
                self.assertNotIn("DELE /EXT0/QRFLECT3\r\n", self.sent())

    # ---- faults during the write ----
    def test_unexpected_dele_reply_stops(self):
        result = self.spare(fault="dele-odd")
        self.assert_stopped_after(result, "DELE /EXT0/QRFLECT0\r\n")
        self.assertIn("may have been deleted", result.stderr)

    def test_bad_150_stops_before_data(self):
        result = self.spare(fault="stor-open-error")
        self.assert_stopped_after(result, "-STOR 2202472 /EXT0/QRFLECT3\r\n")
        self.assertIn("450 Open error", result.stderr)
        self.assertIn("missing, empty or partial", result.stderr)

    def test_write_error_after_data_stops(self):
        result = self.spare(fault="write-error")
        sent = self.assert_stopped_after(result, "<data 39784>")
        self.assertNotIn("-STOR 31914 /EXT0/QRFLECT7\r\n", sent)
        self.assertIn("451 Write error", result.stderr)

    def test_timeout_mid_stor_stops(self):
        result = self.spare(fault="stor-timeout")
        sent = self.assert_stopped_after(result, "<data 65536>")
        self.assertEqual(sum(1 for s in sent if s.startswith("<data")), 2)
        self.assertIn("TransportTimeout", result.stderr)
        self.assertIn("Power cycle", result.stderr)
        self.assertIn("2 frame(s) attempted, 65536 of 2202472 bytes accepted", result.stderr)

    def test_short_data_write_stops(self):
        result = self.spare(fault="short-data-write")
        sent = self.assert_stopped_after(result, "<data 65536>")
        self.assertEqual(sum(1 for s in sent if s.startswith("<data")), 1)
        self.assertIn("ShortWrite", result.stderr)

    def test_corrupted_read_back_stops_without_retry(self):
        result = self.spare(fault="corrupt-readback")
        sent = self.assert_stopped_after(result, "RETR /EXT0/QRFLECT3\r\n")
        self.assertEqual(sent.count("RETR /EXT0/QRFLECT3\r\n"), 1)
        self.assertIn("first difference at offset 100", result.stderr)
        self.assertIn("does NOT match", result.stderr)
        self.assertIn("Before any keyed power-on, delete QRFLECT3 and QRFLECT7", result.stderr)

    def test_already_updated_check_runs_on_the_camera_too(self):
        from types import SimpleNamespace
        from tools.grdusb import mock
        camera = mock.MockTransport(version="02.40", writable=True)
        camera.bring_up()
        args = SimpleNamespace(require_version=["02.40"], require_camera_id="0011223344556677", allow_older_update=False)
        stager = self.stage.Stager(self.stage.WriteSession(camera), args, self.loaded, out=lambda *_: None)
        with self.assertRaisesRegex(self.stage.StageRefused, "already updated"):
            stager.run()
        self.assertEqual(camera.sent, ["-VER\r\n"])


if __name__ == "__main__":
    unittest.main()
