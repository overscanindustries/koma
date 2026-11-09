#!/usr/bin/env python3
"""Tests for selftest.py (komabridge --selftest), all against the SIMULATED camera (mockcard.py). No USB device is
opened.

Run: python3 tools/komaset/bridge/test_selftest.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import komabridge as KB  # noqa: E402
import mockcard  # noqa: E402
import selftest as ST  # noqa: E402

KOMA_ARTIFACTS = {"PARAM-1.CUR", "PARAM-2.CUR", "KOMALOG-1.TXT", "KOMALOG-2.TXT", "photos.json", "live-mode0.png",
                  "live-mode1.jpg", "live-mode2.png", "report.md", "report.json"}
FORBIDDEN = ("-STOR", "-CCTL poff", "DELE", "/IROM")


def fast_link(body) -> KB.Link:
    link = KB.Link(KB.MockBackend(body))
    link.af_wait_s = link.press_s = 0.02
    link.first_poll_s = link.poll_s = link.stable_poll_s = 0.05
    link.ksta_poll_s = link.settle_unseen_s = link.settle_delay_s = link.list_retry_s = 0.02
    link.live_max_fps = 1000.0
    link.kset_busy_wait_s, link.kset_busy_poll_s = 0.2, 0.05
    return link


class SelftestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "session"

    def tearDown(self):
        self.tmp.cleanup()

    def sent(self, body) -> list:
        return [c for t in body.transports for c in t.sent]

    def assert_clean(self, body):
        for t in body.transports:
            self.assertEqual(t.violations, [])
        for c in self.sent(body):
            text = c.decode("latin1") if isinstance(c, bytes) else str(c)
            for bad in FORBIDDEN:
                self.assertNotIn(bad, text)

    def steps(self, report) -> dict:
        return {s["n"]: s for s in report["steps"]}


class KomaMockTest(SelftestCase):
    """--mock --mock-tether: a Koma camera. Every step PASS, the expected artifacts saved, nothing left on."""

    def test_step6_shot_settles(self):
        body = mockcard.MockBody(tether=True, shot_delay_s=0.1, grow_s=0.05)
        ST.seed_mock_koma(body)
        link, logs = fast_link(body), []
        link.log = logs.append
        report = ST.SelfTest(link, self.out, log=lambda x: None).run()
        self.assertEqual(self.steps(report)[6]["status"], ST.PASS)
        self.assertTrue(any("shot settled after" in x for x in logs))

    def test_full_run(self):
        body = mockcard.MockBody(tether=True, shot_delay_s=0.1, grow_s=0.05)
        ST.seed_mock_koma(body)
        ev_before = body.ram[ST.EV_OFF]
        lines = []
        report = ST.SelfTest(fast_link(body), self.out, log=lines.append).run()
        steps = self.steps(report)
        self.assertEqual({n: s["status"] for n, s in steps.items()}, {n: ST.PASS for n in range(1, 10)},
                         [(s["n"], s["summary"]) for s in report["steps"]])
        self.assertEqual(report["result"], ST.PASS)
        self.assertTrue(report["koma"] and report["simulated"])
        files = {p.name for p in self.out.iterdir()}
        self.assertLessEqual(KOMA_ARTIFACTS, files)
        shots = [f for f in files if f.startswith("tether-RIMG")]
        self.assertEqual(len(shots), 1)
        self.assertEqual(set(report["send_back"]), files)
        # artifacts are what they say
        self.assertEqual((self.out / "PARAM-1.CUR").read_bytes(), mockcard.DEMO_CUR.read_bytes())
        self.assertTrue((self.out / "live-mode0.png").read_bytes().startswith(b"\x89PNG"))
        self.assertTrue((self.out / "live-mode2.png").read_bytes().startswith(b"\x89PNG"))
        self.assertTrue((self.out / "live-mode1.jpg").read_bytes().startswith(b"\xff\xd8"))
        self.assertTrue((self.out / shots[0]).read_bytes().startswith(b"\xff\xd8"))
        # decoded numbers
        self.assertEqual(steps[3]["decode"]["build_id"], "4B4D0400")
        self.assertEqual(steps[3]["decode"]["pool"], 1888)
        self.assertEqual([m["frames"] for m in steps[7]["modes"]], [ST.LIVE_PULLS] * 3)
        self.assertTrue(all(m["fps"] and m["bytes_per_s"] for m in steps[7]["modes"]))
        self.assertIn("jpeg", steps[5]["rates"])
        self.assertIn("param_cur", steps[5]["rates"])
        # the camera is left as found: EV restored, remote control off, no key held, card settings untouched
        self.assertEqual(body.ram[ST.EV_OFF], ev_before)
        self.assertEqual([v for _, v in body.kset_log], [ev_before + 2, ev_before])
        self.assertEqual(body.remote, 0)
        self.assertEqual(body.keys, set())
        self.assertEqual(body.card["/EXT0/PARAM.CUR"], mockcard.DEMO_CUR.read_bytes())
        self.assert_clean(body)
        # report text
        md = (self.out / "report.md").read_text()
        self.assertIn("SIMULATED", md)
        self.assertIn("| 9 | Second pull and diff | PASS |", md)
        self.assertIn("## Send back", md)
        self.assertIn("`live-mode1.jpg`", md)
        self.assertEqual(json.loads((self.out / "report.json").read_text())["result"], ST.PASS)
        self.assertEqual(len(lines), 9)

    def cctl(self, body) -> list:
        return [(c if isinstance(c, str) else c.decode()).strip() for c in self.sent(body)
                if (c if isinstance(c, str) else c.decode()).startswith("-CCTL")]

    def test_steps_6_to_8_share_one_remote_session(self):
        body = mockcard.MockBody(tether=True, shot_delay_s=0.1, grow_s=0.05)
        ST.seed_mock_koma(body)
        report = ST.SelfTest(fast_link(body), self.out).run()
        self.assertEqual(report["result"], ST.PASS)
        cmds = self.cctl(body)
        self.assertEqual(cmds.count("-CCTL remtin"), 1)
        self.assertEqual(cmds.count("-CCTL remtout"), 1)
        self.assertEqual(cmds.count("-CCTL diaset,4"), 2)  # the start, and the re-entry after the shoot's RETR
        self.assertEqual(cmds[-2:], ["-CCTL keyset,0,2", "-CCTL remtout"])  # released, then out, only at the end
        lv = [i for i, c in enumerate(self.sent(body)) if str(c).startswith("-LVFR")]
        self.assertTrue(lv)
        self.assertLess(cmds.index("-CCTL remtin"), len(cmds))
        self.assertEqual(body.remote, 0)

    def test_diaset_again_after_retr_before_live(self):
        """The shoot's -INFO / RETR drop live view on the camera: diaset,4 again, then -LVFR 0 until 150, before the
        step 7 pulls; step 8 (after step 7, no RETR) does not repeat it."""
        body = mockcard.MockBody(tether=True, shot_delay_s=0.1, grow_s=0.05)
        ST.seed_mock_koma(body)
        report = ST.SelfTest(fast_link(body), self.out).run()
        self.assertEqual(report["result"], ST.PASS)
        sent = [(c if isinstance(c, str) else c.decode()).strip() for c in self.sent(body)]
        retr = max(i for i, c in enumerate(sent) if c.startswith("RETR /EXT0/DCIM/"))
        again = [i for i, c in enumerate(sent) if c == "-CCTL diaset,4"][1]
        self.assertLess(retr, again)
        self.assertTrue(sent[again + 1].startswith("-LVFR 0,"))
        self.assertFalse([c for c in sent[retr:again] if c.startswith("-LVFR")])
        details = self.steps(report)[7]["details"]
        self.assertTrue(any(d.startswith("re-entry after RETR/-INFO: -CCTL diaset,4 -> 200") for d in details), details)
        self.assertTrue(any("-LVFR 0 (wait)" in d for d in details), details)
        self.assertFalse(any("re-entry" in d for d in self.steps(report)[8]["details"]))

    def test_failure_in_step_7_still_closes_the_session(self):
        body = mockcard.MockBody(tether=True, shot_delay_s=0.1, grow_s=0.05)
        ST.seed_mock_koma(body)
        orig = KB.live_pull
        KB.live_pull = lambda link, mode: (_ for _ in ()).throw(RuntimeError("boom"))
        self.addCleanup(setattr, KB, "live_pull", orig)
        report = ST.SelfTest(fast_link(body), self.out).run()
        steps = self.steps(report)
        self.assertEqual(steps[7]["status"], ST.FAIL)
        self.assertNotEqual(steps[8]["status"], ST.SKIP)  # (may be busy: step 7 skipped its rec-review wait)
        cmds = self.cctl(body)
        self.assertEqual((cmds.count("-CCTL remtin"), cmds.count("-CCTL remtout")), (1, 1))
        self.assertEqual(body.remote, 0)

    def test_busy_exposure(self):
        body = mockcard.MockBody(tether=True, shot_delay_s=0.1, grow_s=0.05)
        ST.seed_mock_koma(body)
        body.kset_busy = lambda: True
        ev_before = body.ram[ST.EV_OFF]
        report = ST.SelfTest(fast_link(body), self.out).run()
        steps = self.steps(report)
        self.assertEqual(steps[8]["status"], ST.FAIL)
        self.assertIn("busy", steps[8]["summary"])
        self.assertEqual(steps[9]["status"], ST.PASS)  # a busy camera does not stop the run
        self.assertEqual(report["result"], ST.FAIL)
        self.assertEqual(body.ram[ST.EV_OFF], ev_before)
        self.assertEqual(body.remote, 0)

    def test_busy_after_ev_change_retried(self):
        # hardware (session 7 step 8): busy for a moment after the EV change; the restore waits it out
        body = mockcard.MockBody(tether=True, shot_delay_s=0.1, grow_s=0.05)
        ST.seed_mock_koma(body)
        body.kset_busy_after_s = 0.4
        ev_before = body.ram[ST.EV_OFF]
        link = fast_link(body)
        t = ST.SelfTest(link, self.out)
        t.ev_settle_s = 0.1
        steps = self.steps(t.run())
        self.assertEqual(steps[8]["status"], ST.PASS, steps[8])
        self.assertTrue(0.3 <= steps[8]["busy_after_ev_s"] < 1.0, steps[8])
        self.assertTrue(any(d.startswith("measured: busy for") for d in steps[8]["details"]), steps[8]["details"])
        self.assertEqual(body.ram[ST.EV_OFF], ev_before)

    def test_busy_longer_than_restore_window(self):
        body = mockcard.MockBody(tether=True, shot_delay_s=0.1, grow_s=0.05)
        ST.seed_mock_koma(body)
        body.kset_busy_after_s = 3.0
        link = fast_link(body)
        t = ST.SelfTest(link, self.out)
        t.ev_settle_s, t.ev_restore_wait_s = 0.05, 0.3
        steps = self.steps(t.run())
        self.assertEqual(steps[8]["status"], ST.FAIL)
        self.assertIn("busy", steps[8]["summary"])
        self.assertTrue(any("busy retries" in d for d in steps[8]["details"]), steps[8]["details"])


class ArmingTest(SelftestCase):
    """Hardware session 9: remtin/diaset,4 answered 200 but -LVFR stayed 451 and the keys fired nothing; step 7's
    re-entry diaset,4 then got 150 at once. Step 6 fails "remote mode never armed" with no key pressed; step 7 re-arms."""

    def test_step6_never_armed_step7_recovers(self):
        body = mockcard.MockBody(tether=True, shot_delay_s=0.1, grow_s=0.05)
        ST.seed_mock_koma(body)
        link = fast_link(body)
        body.diaset_duds = 1 + link.lvfr_rearms   # the start and all its re-arms
        link.lvfr_wait_s, link.lvfr_rearm_s, link.lvfr_poll_s = 0.6, 0.1, 0.02
        link.log = lambda m: None
        steps = self.steps(ST.SelfTest(link, self.out).run())
        self.assertEqual(steps[6]["status"], ST.FAIL)
        self.assertIn("Remote mode never armed", steps[6]["summary"])
        self.assertIn("no -LVFR 150 (start", steps[6]["summary"])
        self.assertTrue(any("-KSTA (re-arm 1)" in d and "remtin_flag=" in d for d in steps[6]["details"]))
        self.assertFalse([k for k in body.key_log if k[0] == 1])  # no key held during step 6
        self.assertEqual(steps[7]["status"], ST.PASS, steps[7])
        self.assertIn("first -LVFR 150 after", steps[7]["summary"])
        self.assertIn("(re-entry)", steps[7]["summary"])

    def test_first_150_time_in_step_summary(self):
        body = mockcard.MockBody(tether=True, shot_delay_s=0.1, grow_s=0.05)
        ST.seed_mock_koma(body)
        steps = self.steps(ST.SelfTest(fast_link(body), self.out).run())
        self.assertEqual(steps[6]["status"], ST.PASS, steps[6])
        self.assertRegex(steps[6]["summary"], r"first -LVFR 150 after \d+\.\d s \(start\)")

    def test_restore_window_default(self):
        self.assertEqual(ST.EV_RESTORE_WAIT_S, 15.0)


class StockMockTest(SelftestCase):
    """--mock without --mock-tether and no KOMALOG.TXT: a stock camera. Steps 6-8 skip, no remote/RAM verb is sent."""

    def test_stock_skips_koma_steps(self):
        body = mockcard.MockBody()
        report = ST.SelfTest(fast_link(body), self.out).run()
        steps = self.steps(report)
        for n in (6, 7, 8):
            self.assertEqual(steps[n]["status"], ST.SKIP)
            self.assertIn("needs Koma firmware", steps[n]["summary"])
        self.assertEqual(steps[3]["status"], ST.SKIP)
        for n in (1, 2, 4, 5, 9):
            self.assertEqual(steps[n]["status"], ST.PASS, steps[n]["summary"])
        self.assertEqual(report["result"], ST.PASS)
        self.assertFalse(report["koma"])
        sent = b"".join(c if isinstance(c, bytes) else str(c).encode() for c in self.sent(body))
        for verb in (b"-CCTL", b"-LVFR", b"-KSET", b"-KGET"):
            self.assertNotIn(verb, sent)
        self.assert_clean(body)
        self.assertFalse((self.out / "live-mode1.jpg").exists())

    def test_ksta_proves_koma_when_komalog_is_missing(self):
        body = mockcard.MockBody(tether=True, shot_delay_s=0.1, grow_s=0.05)  # no KOMALOG.TXT seeded, -KSTA answers
        report = ST.SelfTest(fast_link(body), self.out).run()
        steps = self.steps(report)
        self.assertTrue(report["koma"])
        self.assertTrue(any("-KSTA" in d for d in steps[3]["details"]))
        for n in (6, 7, 8):
            self.assertNotIn("needs Koma firmware", steps[n]["summary"])
            self.assertNotEqual(steps[n]["status"], ST.SKIP)

    def test_no_camera_stops_the_run(self):
        body = mockcard.MockBody(returns_after_poff=False)
        body.power_off()
        report = ST.SelfTest(fast_link(body), self.out).run()
        steps = self.steps(report)
        self.assertEqual(steps[1]["status"], ST.FAIL)
        for n in range(2, 10):
            self.assertEqual(steps[n]["status"], ST.SKIP)
            self.assertIn("step 1 stopped the run", steps[n]["summary"])
        self.assertTrue((self.out / "report.md").exists())

    def test_cli_stock(self):
        rc = KB.main(["--mock", "--selftest", "--out", str(self.out)])
        self.assertEqual(rc, 0)
        report = json.loads((self.out / "report.json").read_text())
        self.assertEqual([s["status"] for s in report["steps"]],
                         [ST.PASS, ST.PASS, ST.SKIP, ST.PASS, ST.PASS, ST.SKIP, ST.SKIP, ST.SKIP, ST.PASS])


if __name__ == "__main__":
    unittest.main()
