#!/usr/bin/env python3
"""Static parity checks for web/koma-setup.html.

Honest scope: this does NOT execute the JavaScript. It checks that the page carries the verified
constants (checksum at 0x0ffc: big-endian u16 sum stored as a 4-byte big-endian word; KOMA header
at 0x8b0), that the checksum, field codec and merge code contain the algorithm's tokens, that the
embedded data matches the fixtures and settings-map.json, and that task text has no jargon.
The Python reference checksum below is checked against the owner's real block (demo-main.cur).
Run behaviour checks by hand in a browser (?demo=copy|edit|mode|backup).
"""
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import komaset  # noqa: E402

HTML = (HERE / "koma-setup.html").read_text()
TD = HERE / "testdata"
MAP = HERE.parent / "settings-map.json"
REPO = HERE.parents[2]
SUMS = REPO / "build" / "SHA256SUMS"
MODULES = {"1": "koma04.grmod", "2": "koma04w.grmod"}  # Koma 1 (Rev024M), Koma 2 (240K2, SDHC)


def func_body(name):
    i = HTML.index("function " + name)
    j = HTML.index("\nfunction ", i + 1)
    return HTML[i:j]


def region(start, end):
    i = HTML.index(start)
    return HTML[i:HTML.index(end, i)]


def embedded(id_):
    m = re.search(r'<script type="application/json" id="%s">(.*?)</script>' % id_, HTML, re.S)
    assert m, id_ + " not embedded; run testdata/make_demo.py"
    return json.loads(m.group(1).replace("<\\/", "</"))


def node(prog, stdin=""):
    res = subprocess.run(["node", "-e", prog], input=stdin, capture_output=True, text=True)
    if res.returncode:
        raise AssertionError(res.stderr)
    return json.loads(res.stdout)


def ref_checksum(b):
    """Verified 2026-11-07 on a real block: BE u16 sum over 0x000..0xffb, stored BE u32 at 0xffc."""
    return sum(int.from_bytes(b[i:i + 2], "big") for i in range(0, 0xFFC, 2)) & 0xFFFFFFFF


def demo():
    m = re.search(r'<script type="application/json" id="demoData">(.*?)</script>', HTML, re.S)
    assert m, "demo data not embedded; run testdata/make_demo.py"
    return json.loads(m.group(1).replace("<\\/", "</"))


class Parity(unittest.TestCase):
    def test_constants(self):
        self.assertIn("CS_OFF_DEFAULT = 0x0ffc", HTML)
        self.assertIn("CS_SIZE = 4", HTML)
        self.assertIn("CS_ALGO = 'u32-sum-of-u16be'", HTML)
        self.assertIn("'MG1H'", HTML)
        self.assertIn("'KOMA'", HTML)
        self.assertEqual(komaset.KOMA_OFF, 0x8B0)
        self.assertIn("KOMA_OFF = 0x%x" % komaset.KOMA_OFF, HTML)
        self.assertIn("KOMA_VER_OFF = 0x8b4", HTML)
        self.assertEqual(komaset.KOMA_MAGIC, b"KOMA")
        self.assertIn("BLOCK_SIZE = 4096", HTML)

    def test_old_variants_gone(self):
        for tok in ("'u16-sum'", "'u16-sum-be'", "'byte-sum'", 'id="algo"', "$('algo')"):
            self.assertNotIn(tok, HTML)

    def test_map_meta_matches_engine(self):
        if not MAP.exists():
            self.skipTest("settings-map.json not present")
        cs = json.loads(MAP.read_text())["meta"]["checksum"]
        self.assertEqual(cs["algo"], "u32-sum-of-u16be")
        self.assertEqual(int(str(cs["offset"]), 0), 0xFFC)
        self.assertEqual(cs.get("size"), 4)
        self.assertEqual(cs.get("endian"), "big")
        self.assertEqual(cs["range"], [0, 4092])

    def test_checksum_code_path(self):
        body = func_body("computeChecksum")
        self.assertIn("i += 2", body)
        self.assertIn("(b[i] << 8) | b[i + 1]", body)       # big-endian halfword
        self.assertIn(">>> 0", body)                        # unsigned 32-bit sum
        self.assertIn("CS_OFF_DEFAULT", body)               # default range ends at 0xffc
        self.assertIn("(b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]", func_body("storedChecksum"))
        st = func_body("setChecksum")
        for tok in ("b[o] = (v >>> 24) & 0xff", "b[o + 1] = (v >>> 16) & 0xff", "b[o + 2] = (v >>> 8) & 0xff", "b[o + 3] = v & 0xff"):
            self.assertIn(tok, st)
        # the map is refused unless it declares this exact checksum
        self.assertIn("o.csAlgo !== CS_ALGO || o.csSize !== CS_SIZE || o.csEndian !== 'big'", func_body("parseMap"))

    def test_field_codec_big_endian(self):
        self.assertIn("f.size - 1 - k", func_body("bytePos"))
        self.assertIn("'u32'", func_body("parseMap"))

    def test_reference_checksum_on_real_block(self):
        b = (TD / "demo-main.cur").read_bytes()
        self.assertEqual(len(b), 4096)
        self.assertEqual(b[:4], b"MG1H")
        self.assertEqual(int.from_bytes(b[0xFFC:], "big"), ref_checksum(b))
        s = (TD / "demo-spare.cur").read_bytes()
        self.assertEqual(int.from_bytes(s[0xFFC:], "big"), ref_checksum(s))

    def test_merge_rules(self):
        body = func_body("mergeInto")
        for tok in ("M.magicOff", "M.csOff", "M.csSize", "M.exclude", "komaVersion(tgt)", "komaVersion(source)",
                    "Koma versions differ", "ksioTag(tgt)", "tagWarning(source, tgt)", "setChecksum(tgt)", "refusing", "!force"):
            self.assertIn(tok, body)
        self.assertIn("mergeInto(blk, src, force, incl)", HTML)          # Advanced view
        self.assertIn("mergeInto(merged, C.from, true, false)", HTML)    # copy task

    def test_tag_constants_and_copy_checkbox(self):
        self.assertIn("KSIO_OFF = 0x8d0", HTML)
        self.assertIn("KSIO_TAG_OFF = 0x8d5", HTML)
        self.assertEqual((komaset.KSIO_OFF, komaset.KSIO_TAG_OFF), (0x8D0, 0x8D5))
        self.assertIn("tagDiffers(C.from, C.to)", HTML)
        self.assertIn("versionDiff && !C.anyway", HTML)              # tag mismatch: warning only, no Copy anyway
        self.assertNotIn("(versionDiff || tagDiff) && !C.anyway", HTML)
        self.assertIn("Settings still copy; Koma version matches.", func_body("tagWarning"))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_merge_into_runs_in_node(self):
        """Run the real mergeInto/ksioTag source on two synthetic blocks and compare with komaset's tag rule."""
        m = json.loads(MAP.read_text())
        js = "\n".join(func_body(n) for n in ("mergeInto", "ksioTag", "tagDiffers", "tagWarning", "setChecksum"))
        def blk(tag, seed):
            b = bytearray((i * seed + 13) & 0xFF for i in range(4096))
            b[0:4] = b"MG1H"
            if tag:
                b[0x8D0:0x8D4] = b"KSIO"
                b[0x8D5:0x8DC] = tag
            return b
        prog = (
            "const M={size:4096,magicOff:0,magic:[77,71,49,72],csOff:4092,csSize:4,exclude:[]};\n"
            "const KSIO_OFF=0x8d0,KSIO_TAG_OFF=0x8d5,KSIO_TAG_LEN=7,KOMA_OFF=0x8b0,KOMA_VER_OFF=0x8b4,KOMA_MAGIC_TEXT='KOMA',MAGIC_TEXT='MG1H';\n"
            "function magicOk(b){return true;} function komaVersion(b){return null;}\n"
            "function setChecksum(b){}\n"
            + js.replace("function setChecksum", "function _unused") +
            "\nconst d=JSON.parse(require('fs').readFileSync(0,'utf8'));\n"
            "const r=d.map(c=>{const t=Uint8Array.from(c[0]),s=Uint8Array.from(c[1]);"
            "const a=mergeInto(t,s,false,true);return [a.ok,a.problems.join('|'),a.warnings.join('|')];});\n"
            "console.log(JSON.stringify(r));")
        cases = [(blk(b"Rev024M", 7), blk(b"Rev025A", 11)), (blk(b"Rev024M", 7), blk(b"Rev024M", 11)),
                 (blk(b"Rev024M", 7), blk(None, 11)), (blk(None, 7), blk(None, 11))]
        res = subprocess.run(["node", "-e", prog], input=json.dumps([[list(a), list(b)] for a, b in cases]),
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        got = json.loads(res.stdout)
        # a tag mismatch no longer refuses (2026-11-07): same result as komaset merge, with the same warning text
        self.assertEqual([g[0] for g in got], [True, True, True, True])
        self.assertEqual([g[1] for g in got], ["", "", "", ""])
        want = komaset.tag_problem(cases[0][0], cases[0][1])
        self.assertEqual(got[0][2], want[1])
        self.assertEqual(got[0][2], "These cameras run different firmware tags (Rev025A, Rev024M). Settings still copy; "
                                    "Koma version matches.")
        self.assertEqual([g[2] for g in got[1:]], ["", "", ""])

    def test_demo_embed_matches_fixtures(self):
        d = demo()
        if MAP.exists():
            self.assertEqual(d["map"], json.loads(MAP.read_text()), "embedded map is stale; rerun testdata/make_demo.py")
        self.assertEqual(base64.b64decode(d["main"]), (TD / "demo-main.cur").read_bytes())
        self.assertEqual(base64.b64decode(d["spare"]), (TD / "demo-spare.cur").read_bytes())

    def test_task_text_has_no_jargon(self):
        # File names may appear only in muted hints; never in task headings or body text.
        main = HTML[HTML.index('<section id="v-home"'):HTML.index('<section id="v-advanced"')]
        main = re.sub(r'<p class="hint"[^>]*>.*?</p>', "", main, flags=re.S)
        for word in ("PARAM.", "checksum", "block", "merge", "envelope", "0x"):
            self.assertNotIn(word, main, word)

    def test_no_em_dash_no_cdn(self):
        self.assertNotIn("—", HTML)
        self.assertNotRegex(HTML, r"https?://(?!localhost)")


    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_scripts_parse(self):
        """node --check on the page's two scripts, alone and together (a duplicate top-level const is an error)."""
        import tempfile
        scripts = re.findall(r"<script>(.*?)</script>", HTML, re.S)
        self.assertEqual(len(scripts), 2)
        with tempfile.TemporaryDirectory() as d:
            for name, text in (("engine.js", scripts[0]), ("ui.js", scripts[1]), ("all.js", "\n".join(scripts))):
                f = Path(d) / name
                f.write_text(text)
                res = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, name + ": " + res.stderr)

    def test_recipe_table_matches_bwrecipes(self):
        m = re.search(r"const RCP_TABLE = (\{.*?\});\n", HTML)
        self.assertTrue(m, "RCP_TABLE missing")
        table = json.loads(m.group(1))
        sys.path.insert(0, str(HERE.parents[1]))
        try:
            from koma.features import bwrecipes as B
        except Exception as error:  # noqa: BLE001
            self.skipTest(f"bwrecipes not importable: {error}")
        self.assertEqual(table["n"], B.N)
        self.assertEqual(table["defaults"], [list(r["params"]) + [r["type"]] for r in B.RECIPES])
        self.assertEqual(table["limits"], [B.LIMITS[t] for t in range(3)])
        alts = {r["id"]: r["variants"]["alts"] for r in B.ROWS if "variants" in r}
        self.assertEqual(table["slots"], [[[alts[k][t][0], list(alts[k][t][1])] for k in ("rp_p5", "rp_p6", "rp_p7")]
                                          for t in range(3)])
        for tok in ("RCP.rec + 12 * (id - 1)", "b[o + 10] === 0xa5", "x = 0x5a", "0x50 | g | (v << 2)", "rec: 0x910",
                    "fx: 0xa00", "act: 0x8c0", "edit: 0x8c1", "grain: 0x8cc", "vig: 0x8cd"):
            self.assertIn(tok, HTML)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_overlay_helpers_run_in_node(self):
        js = "\n".join(func_body(n) for n in ("ovLuma", "ovHistogram", "ovPaint"))
        prog = js + """
const w = 8, h = 6, rgba = new Uint8ClampedArray(w * h * 4);
for (let i = 0; i < w * h; i++) { const v = (i % w) < 4 ? 20 : 252; rgba.set([v, v, v, 255], i * 4); }
const y = ovLuma(rgba, w * h), hs = ovHistogram(y), out = new Uint8ClampedArray(w * h * 4);
const a = ovPaint(y, w, h, out, 250, 50), b = ovPaint(y, w, h, out, 0, 0), c = ovPaint(y, w, h, out, 250, 0);
console.log(JSON.stringify([y[0], y[7], hs[y[0]], hs[y[7]], a, b, c]));"""
        res = subprocess.run(["node", "-e", prog], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        y0, y7, n0, n7, a, b, c = json.loads(res.stdout)
        self.assertEqual((y0, y7, n0, n7), (20, 252, 24, 24))
        self.assertEqual(b, {"zebra": 0, "peak": 0})
        self.assertEqual(c, {"zebra": 24, "peak": 0})
        self.assertEqual(a["peak"], 8)     # the two columns either side of the step, inner rows only
        self.assertEqual(a["zebra"], 20)

    # ---------- Set up a card, the card check, the phone app shell
    def test_setup_view_strings(self):
        home = HTML[HTML.index('<section id="v-home"'):HTML.index('<!-- TASK 1: COPY -->')]
        self.assertIn('<h2>Set up a card</h2>', home)
        self.assertIn('href="#setup"', home)
        self.assertNotIn('id="setupTask" class="task off"', home)            # always enabled
        self.assertIn("'looks', 'setup', 'advanced'", HTML)                   # VIEWS
        self.assertIn("else if (UI.view === 'setup') renderSetup();", HTML)
        for tok in ("Choose the module file you downloaded", "Save as GRMOD.BIN", "saveBytes(SU.bytes, 'GRMOD.BIN')",
                    "crypto.subtle.digest('SHA-256'", "sha256Js(bytes)", "'../../build/koma04.grmod'",
                    "'../../build/koma04w.grmod'", "'../../build/SHA256SUMS'", "sumsLine(sums, KOMA_FILES[koma])",
                    "if (!/^https?:$/.test(location.protocol)) { drawSetupModule(); return; }  // file://",
                    "Which Koma does your camera run?", "build_koma04w.py -o build/koma04w.grmod",
                    "bridgeJson('/ram?off=' + KSIO_OFF", "<h2>3. Check a card</h2>",
                    "webkitdirectory", "window.showDirectoryPicker", "vsRun(card)",
                    "docs/install.md", "That step is a firmware flash",
                    "Format the card in the camera", "copy GRMOD.BIN to the top level of the card",
                    "Enter the code from the bridge"):
            self.assertIn(tok, HTML, tok)
        # verify_session wording is carried over verbatim
        vs = (HERE.parent / "verify_session.py").read_text()
        for line in ("the mode rows can ship", "4-byte DONE marker (remove did nothing, create truncated: consumed)",
                     "the import did not complete.", "no import was attempted.", "KSIO: magic absent (the block was not written by the settings-io feature)",
                     "The loader never ran the telemetry init (no GRMOD.BIN,", "OK (import applied)", "BAD (import refused, see PARAM.BAD)"):
            self.assertIn(line, vs)
            self.assertIn(line, HTML)

    def test_module_hash_embedded(self):
        d = embedded("moduleData")["modules"]
        self.assertEqual({k: m["name"] for k, m in d.items()}, MODULES)
        for k, m in d.items():
            self.assertRegex(m["sha256"], r"^[0-9a-f]{64}$", k)
        if not SUMS.exists():
            self.skipTest("SHA256SUMS not present")
        sums = {l.split()[1].lstrip("*"): l.split()[0] for l in SUMS.read_text().splitlines() if len(l.split()) == 2}
        for m in d.values():
            self.assertEqual(m["sha256"], sums.get(m["name"]), m["name"] + ": embedded hash is stale; rerun testdata/make_demo.py")

    def test_module_files_match_sums(self):
        """Repository state, not page code: the page refuses to save a module whose hash differs from SHA256SUMS."""
        if not SUMS.exists():
            self.skipTest("SHA256SUMS not present")
        sums = {l.split()[1].lstrip("*"): l.split()[0] for l in SUMS.read_text().splitlines() if len(l.split()) == 2}
        for name in MODULES.values():
            mod = REPO / "build" / name
            if not mod.exists():
                continue
            self.assertEqual(hashlib.sha256(mod.read_bytes()).hexdigest(), sums.get(name),
                             "build/%s does not match its SHA256SUMS line (rebuilt without updating the sums?)" % name)

    def test_koma_of_tag(self):
        """The firmware tag picks the package: 240K2 -> Koma 2 (koma04w), Rev024M -> Koma 1 (koma04), else no pick."""
        js = func_body("komaOfTag")
        prog = ("const KOMA_TAGS={1:'Rev024M',2:'240K2'};\n" + js +
                "\nconsole.log(JSON.stringify(['240K2.4','240K2\\0\\0','Rev024M','Rev0240','',null].map(komaOfTag)));")
        self.assertEqual(node(prog), [2, 2, 1, 0, 0, 0])
        self.assertIn("const KOMA_TAGS = {1: 'Rev024M', 2: '240K2'};", HTML)
        self.assertIn("const KOMA_FILES = {1: 'koma04.grmod', 2: 'koma04w.grmod'};", HTML)

    def test_manifest(self):
        m = json.loads((HERE / "manifest.webmanifest").read_text())
        self.assertEqual(m["name"], "Koma Setup")
        self.assertEqual(m["display"], "standalone")
        self.assertNotIn("token", m["start_url"])
        root = dict(re.findall(r"--([a-z0-9-]+):([^;]+);", re.search(r":root\{(.*?)\}", HTML, re.S).group(1)))
        self.assertEqual(m["theme_color"], root["bg"])
        self.assertEqual(m["background_color"], root["bg"])
        types = {(i["type"], i["sizes"]) for i in m["icons"]}
        self.assertTrue({("image/svg+xml", "any"), ("image/png", "192x192"), ("image/png", "512x512")} <= types)
        for i in m["icons"]:
            raw = base64.b64decode(i["src"].split(",", 1)[1])
            if i["type"] == "image/png":
                self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
                n = int(i["sizes"].split("x")[0])
                self.assertEqual((int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")), (n, n))
        self.assertIn('rel="apple-touch-icon" href="data:image/png;base64,', HTML)
        # the manifest link and the worker are added only over http(s)
        shell = region("(function appShell() {", "})();")
        self.assertIn("if (!/^https?:$/.test(location.protocol)) return;", shell)
        self.assertIn("'manifest.webmanifest' + q", shell)
        self.assertIn("navigator.serviceWorker.register('sw.js' + q)", shell)
        self.assertNotIn('rel="manifest"', HTML)

    def test_token_kept_for_the_app(self):
        self.assertIn("const BR_TOKEN = URL_TOKEN || (SAME_ORIGIN ? lsGet(APP_TOKEN_KEY) || '' : '');", HTML)
        self.assertIn("if (URL_TOKEN && SAME_ORIGIN) lsSet(APP_TOKEN_KEY, URL_TOKEN);", HTML)
        self.assertIn("if (SAME_ORIGIN && !BR_TOKEN && STANDALONE) codePrompt(true);", HTML)
        self.assertIn("if (SAME_ORIGIN && e && e.status === 403) codePrompt(true);", HTML)

    def test_service_worker(self):
        sw = (HERE / "sw.js").read_text()
        for tok in ("'./', './settings-map.json', './manifest.webmanifest'", "'./koma-setup.html', '../settings-map.json'",
                    "if (req.method !== 'GET') return;", "if (!KEYS.has(k)) return;", "searchParams.set('token', TOKEN)"):
            self.assertIn(tok, sw)
        self.assertNotRegex(sw, r"https?://")
        if shutil.which("node"):
            res = subprocess.run(["node", "--check", str(HERE / "sw.js")], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_sha256_fallback_in_node(self):
        prog = region("// SHA256-JS START", "// SHA256-JS END") + """
const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify(d.map(x => sha256Js(Uint8Array.from(Buffer.from(x, 'base64'))))));"""
        cases = [b"", b"abc", b"a" * 55, b"a" * 56, b"a" * 64, bytes(range(256)) * 9]
        cases += [(REPO / "build" / n).read_bytes() for n in MODULES.values() if (REPO / "build" / n).exists()]
        got = node(prog, json.dumps([base64.b64encode(c).decode() for c in cases]))
        self.assertEqual(got, [hashlib.sha256(c).hexdigest() for c in cases])

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_verify_port_matches_verify_session(self):
        """Card fixtures built from demo-main.cur, checked by the page's port (node) and by verify_session.py."""
        import tempfile
        import verify_session as V
        engine = (region("const BLOCK_SIZE = 4096;", "const $ = ") + "const toInt = v => { const n = typeof v === 'string' ? "
                  "Number(v.trim()) : Number(v); if (!Number.isFinite(n)) throw new Error('bad number ' + v); return n; };\n"
                  + "\n".join(func_body(n) for n in ("parseMap", "computeChecksum", "storedChecksum", "komaVersion", "ksioTag"))
                  + "function csOff() { return M ? M.csOff : CS_OFF_DEFAULT; }\n")
        prog = engine + region("// VERIFY-PORT START", "// VERIFY-PORT END") + """
const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
var M = d.map ? parseMap(d.map) : null;
console.log(JSON.stringify(d.cards.map(c => {
  const card = {files: c.files.map(f => ({name: f[0], data: Uint8Array.from(Buffer.from(f[1], 'base64'))})).map(f => Object.assign(f, {size: f.data.length})), dcim: c.dcim};
  const r = vsRun(card, c.tag); return [r.out, r.verdicts, r.code, r.summary];
})));"""

        def block(tag=b"Rev024M", status=1, ksio=True):
            b = bytearray((TD / "demo-main.cur").read_bytes())
            if ksio:
                b[0x8D0:0x8D4] = b"KSIO"
                b[0x8D4] = 1
                b[0x8D5:0x8DC] = tag
                b[0x8DC] = status
            b[0xFFC:] = ref_checksum(b).to_bytes(4, "big")
            return bytes(b)

        def log(stage=3, pool="00000F40", end=True, header=True):
            rows = [("STAGE", "%08X" % stage), ("BUILD", "33335EAC"), ("RES  ", "003C78A0"), ("LOOPS", "00040000")]
            if pool is not None:
                rows.append(("POOL ", pool))
            text = ("KOMA TLM V1\n" if header else "") + "".join("%s=%s\n" % r for r in rows) + ("END\n" if end else "")
            return text.encode()

        clean = {"KOMALOG.TXT": log(), "PARAM.CUR": block(), "PARAM.DAT": b"DONE", "PARAM.OK": b"OK\r\n"}
        def card(base=clean, drop=(), tag=None, **over):
            files = {k: v for k, v in base.items() if k not in drop}
            files.update(over)
            return {"files": files, "tag": tag}
        cards = [
            card(tag="Rev024M"),                                                          # 1 clean
            card(drop=("PARAM.OK",), **{"PARAM.BAD": b"BAD TAG\r\n", "PARAM.CUR": block(status=2)}),  # 2 bad import
            card(drop=("KOMALOG.TXT",)),                                                  # 3 no telemetry
            card(drop=("PARAM.OK",), tag="Rev0240", **{"PARAM.DAT": b"\x01" * 4096}),     # unconsumed, wrong tag
            card(drop=("PARAM.DAT", "PARAM.OK"), **{"param.ok": b"OK", "KOMALOG.TXT": log(pool="00000400")}),
            card(**{"KOMALOG.TXT": log(stage=1, pool=None, end=False), "PARAM.CUR": block(ksio=False)}),
            card(**{"KOMALOG.TXT": log(header=False, pool="--------"), "PARAM.DAT": b"DONE" + bytes(4092)}),
            card(**{"PARAM.DAT": b"DONE!", "PARAM.CUR": block()[:-1]}),
            card(**{"PARAM.CUR": block(tag=b"Rev'24\x01", status=7), "KOMALOG.TXT": log(pool="00001A00")}),
            {"files": {}, "tag": None},                                                   # empty card
        ]
        got = node(prog, json.dumps({"map": json.loads(MAP.read_text()), "cards": [
            {"files": [[k, base64.b64encode(v).decode()] for k, v in c["files"].items()], "dcim": None, "tag": c["tag"]}
            for c in cards]}))
        with tempfile.TemporaryDirectory() as tmp:
            for i, (c, (out, verdicts, code, summary)) in enumerate(zip(cards, got)):
                root = Path(tmp) / str(i)
                root.mkdir()
                for k, v in c["files"].items():
                    (root / k).write_bytes(v)
                pout, pver = V.run(root, c["tag"])
                self.assertEqual(out, pout, "card %d" % i)
                self.assertEqual([tuple(v) for v in verdicts], pver, "card %d" % i)
                vs = {v for _, v in pver}
                want = 1 if "ATTENTION" in vs else 2 if any(v == "MISSING" and t in ("KOMALOG", "PARAM.CUR") for t, v in pver) else 0
                self.assertEqual(code, want, "card %d" % i)
                self.assertEqual(summary, "SUMMARY: " + "; ".join(f"{t} {v}" for t, v in pver))
        self.assertEqual([g[2] for g in got[:3]], [0, 1, 2])   # the three verify_session sample sessions
        # photos: counted, not read
        one = node(prog, json.dumps({"map": None, "cards": [{"files": [], "dcim": {"jpegs": 2}, "tag": None}]}))
        self.assertIn("DCIM: 2 JPEG(s)", one[0][0][-3])
        self.assertEqual(one[0][1][-1], ["JPEGs", "OK"])


if __name__ == "__main__":
    unittest.main()
