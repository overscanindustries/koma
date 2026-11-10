#!/usr/bin/env python3
"""Parity checks for the settings engine as a module: tools/komaapp/src/engine/engine.js (the Koma app, D1).

The module is the engine of koma-setup.html lifted out unchanged. This test checks that:
- every engine function in the module is byte-identical to the page's (so the page's parity tests cover it too),
- the module carries the same verified constants and algorithm tokens as test_web_parity.py checks on the page,
- run in node (as an ES module), it matches komaset.py: the checksum on a real block, render and parse of
  every mapped field, merge (bytes and the tag rule) against `komaset.py merge`.

    python3 tools/komaset/web/test_engine_module.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import komaset  # noqa: E402

REPO = HERE.parents[2]
ENGINE = REPO / "tools" / "komaapp" / "src" / "engine" / "engine.js"
SRC = ENGINE.read_text()
HTML = (HERE / "koma-setup.html").read_text()
PAGE_ENGINE = HTML[HTML.index('<script>\n"use strict";'):]
PAGE_ENGINE = PAGE_ENGINE[:PAGE_ENGINE.index("</script>")]
MAP = HERE.parent / "settings-map.json"
TD = HERE / "testdata"

LIFTED = ("parseMap", "coverage", "unmappedRanges", "computeChecksum", "csOff", "storedChecksum", "setChecksum",
          "magicOk", "komaVersion", "ksioTag", "tagDiffers", "tagWarning", "rawBytes", "bytePos", "uintOf", "writeUint",
          "hexOf", "renderValue", "setFieldText", "fieldChanged", "changeCount", "mergeInto")


def function_text(text, name):
    """'function name(...) { ... }' by brace counting (strings in these bodies hold no unbalanced braces)."""
    i = text.index("function " + name + "(")
    j = text.index("{", i)
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[i:k + 1]
    raise AssertionError("unbalanced " + name)


def ref_checksum(b):
    return sum(int.from_bytes(b[i:i + 2], "big") for i in range(0, 0xFFC, 2)) & 0xFFFFFFFF


def run_module(prog, stdin=""):
    """Run prog as an ES module that has imported the engine as E."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "run.mjs"
        f.write_text("import * as E from %s;\n" % json.dumps(ENGINE.as_uri()) + prog)
        res = subprocess.run(["node", str(f)], input=stdin, capture_output=True, text=True)
    if res.returncode:
        raise AssertionError(res.stderr)
    return json.loads(res.stdout)


class ModuleParity(unittest.TestCase):
    def test_functions_identical_to_page(self):
        for name in LIFTED:
            self.assertEqual(function_text(SRC, name), function_text(PAGE_ENGINE, name), name)

    def test_constants(self):
        for tok in ("CS_OFF_DEFAULT = 0x0ffc", "CS_SIZE = 4", "CS_ALGO = 'u32-sum-of-u16be'", "'MG1H'", "'KOMA'",
                    "KOMA_OFF = 0x%x" % komaset.KOMA_OFF, "KOMA_VER_OFF = 0x8b4", "BLOCK_SIZE = 4096",
                    "KSIO_OFF = 0x8d0", "KSIO_TAG_OFF = 0x8d5"):
            self.assertIn(tok, SRC, tok)
        for tok in ("'u16-sum'", "'u16-sum-be'", "'byte-sum'", "document.", "window.", "localStorage"):
            self.assertNotIn(tok, SRC, tok)

    def test_algorithm_tokens(self):
        cc = function_text(SRC, "computeChecksum")
        for tok in ("i += 2", "(b[i] << 8) | b[i + 1]", ">>> 0", "CS_OFF_DEFAULT"):
            self.assertIn(tok, cc)
        self.assertIn("(b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]", function_text(SRC, "storedChecksum"))
        st = function_text(SRC, "setChecksum")
        for tok in ("b[o] = (v >>> 24) & 0xff", "b[o + 1] = (v >>> 16) & 0xff", "b[o + 2] = (v >>> 8) & 0xff",
                    "b[o + 3] = v & 0xff"):
            self.assertIn(tok, st)
        self.assertIn("o.csAlgo !== CS_ALGO || o.csSize !== CS_SIZE || o.csEndian !== 'big'", function_text(SRC, "parseMap"))
        self.assertIn("f.size - 1 - k", function_text(SRC, "bytePos"))
        mi = function_text(SRC, "mergeInto")
        for tok in ("M.magicOff", "M.csOff", "M.csSize", "M.exclude", "komaVersion(tgt)", "komaVersion(source)",
                    "Koma versions differ", "ksioTag(tgt)", "tagWarning(source, tgt)", "setChecksum(tgt)", "refusing", "!force"):
            self.assertIn(tok, mi)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_checksum_on_real_blocks(self):
        blocks = [(TD / "demo-main.cur").read_bytes(), (TD / "demo-spare.cur").read_bytes()]
        got = run_module("""
const fs = process.getBuiltinModule('fs');
const d = JSON.parse(fs.readFileSync(0, 'utf8'));
E.loadMap(d.map);
console.log(JSON.stringify(d.blocks.map(b => { const u = Uint8Array.from(b); const c = E.computeChecksum(u), s = E.storedChecksum(u);
  const x = u.slice(); x.fill(0, 0xffc); E.setChecksum(x); return [c, s, Array.from(x.slice(0xffc)), E.magicOk(u), E.komaVersion(u)]; })));
""", json.dumps({"map": json.loads(MAP.read_text()), "blocks": [list(b) for b in blocks]}))
        for b, (c, s, tail, mok, kv) in zip(blocks, got):
            self.assertEqual(c, ref_checksum(b))
            self.assertEqual(s, int.from_bytes(b[0xFFC:], "big"))
            self.assertEqual(bytes(tail), b[0xFFC:])
            self.assertTrue(mok)
            self.assertEqual(kv, komaset.koma_info(bytearray(b)))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_render_and_parse_match_komaset(self):
        """Every mapped field rendered on the real block, then each enum option / range end written and read back,
        the same bytes as komaset.Field.parse produces."""
        m = komaset.Map(MAP)
        blk = bytearray((TD / "demo-main.cur").read_bytes())
        cases = []
        for f in m.fields:
            if f.type == "enum":
                cases += [(f.name, str(k)) for k in sorted(f.values)][:6]
            elif f.type in ("u8", "u16", "u32") and f.range:
                cases += [(f.name, str(f.range[0])), (f.name, str(f.range[1]))]
        got = run_module("""
const fs = process.getBuiltinModule('fs');
const d = JSON.parse(fs.readFileSync(0, 'utf8'));
const M = E.loadMap(d.map), base = Uint8Array.from(d.blk);
const rendered = Object.fromEntries(M.fields.map(f => [f.name, E.renderValue(f, base)]));
const written = d.cases.map(([n, t]) => { const w = base.slice(); E.bind(w, base); const err = E.setFieldText(M.byName[n], t);
  const f = M.byName[n]; return [err, Array.from(w.slice(f.offset, f.end)), E.fieldChanged(f), E.changeCount()]; });
console.log(JSON.stringify({rendered, written}));
""", json.dumps({"map": json.loads(MAP.read_text()), "blk": list(blk), "cases": cases}))
        for f in m.fields:
            want = f.render(blk)[0]
            if f.type in ("u8", "u16", "u32") and f.range and not (f.range[0] <= f._uint(blk) <= f.range[1]):
                continue  # komaset keeps an out-of-range stored value as hex; the page shows the number
            self.assertEqual(got["rendered"][f.name], want, f.name)
        for (name, text), (err, raw, changed, count) in zip(cases, got["written"]):
            f = m.by_name[name]
            text_py = ("0x%x" % int(text)) if f.type == "enum" else text
            self.assertIsNone(err, (name, text))
            self.assertEqual(bytes(raw), f.parse(text_py), (name, text))
            self.assertEqual(changed, bytes(raw) != f.raw(blk), (name, text))
            self.assertEqual(count, int(changed), (name, text))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_merge_matches_komaset_cli(self):
        main = (TD / "demo-main.cur").read_bytes()
        spare = (TD / "demo-spare.cur").read_bytes()
        got = run_module("""
const fs = process.getBuiltinModule('fs');
const d = JSON.parse(fs.readFileSync(0, 'utf8'));
E.loadMap(d.map);
const out = d.pairs.map(([t, s, force, incl]) => { const a = Uint8Array.from(t); const r = E.mergeInto(a, Uint8Array.from(s), force, incl);
  return [r.ok, r.copied, r.problems, r.warnings, Array.from(a)]; });
console.log(JSON.stringify(out));
""", json.dumps({"map": json.loads(MAP.read_text()),
                 "pairs": [[list(spare), list(main), False, False], [list(spare), list(main), False, True],
                           [list(main), list(spare), True, False]]}))
        with tempfile.TemporaryDirectory() as d:
            for (tgt, srcb, _force, incl), (ok, copied, problems, warnings, merged) in zip(
                    [(spare, main, False, False), (spare, main, False, True), (main, spare, True, False)], got):
                t, s, o = Path(d) / "t", Path(d) / "s", Path(d) / "o"
                t.write_bytes(tgt)
                s.write_bytes(srcb)
                argv = ["merge", "--target", str(t), "--source", str(s), "-o", str(o)] + (["--include-excluded"] if incl else [])
                with open("/dev/null", "w") as null:
                    old = sys.stdout
                    sys.stdout = null
                    try:
                        rc = komaset.main(argv)
                    finally:
                        sys.stdout = old
                self.assertEqual(rc, 0)
                self.assertTrue(ok)
                self.assertEqual(bytes(merged), o.read_bytes())
                self.assertEqual(problems, [])

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_tag_rule_matches_komaset(self):
        def blk(tag, seed):
            b = bytearray((i * seed + 13) & 0xFF for i in range(4096))
            b[0:4] = b"MG1H"
            b[0x8B0:0x8B4] = b"XXXX"  # no Koma header on either: versions equal
            if tag:
                b[0x8D0:0x8D4] = b"KSIO"
                b[0x8D5:0x8DC] = tag
            return b
        cases = [(blk(b"Rev024M", 7), blk(b"Rev025A", 11)), (blk(b"Rev024M", 7), blk(b"Rev024M", 11)),
                 (blk(b"Rev024M", 7), blk(None, 11))]
        m = json.loads(MAP.read_text())
        got = run_module("""
const fs = process.getBuiltinModule('fs');
const d = JSON.parse(fs.readFileSync(0, 'utf8'));
E.loadMap(d.map);
console.log(JSON.stringify(d.cases.map(([t, s]) => { const r = E.mergeInto(Uint8Array.from(t), Uint8Array.from(s), false, true);
  return [r.ok, r.warnings.join('|'), E.tagDiffers(Uint8Array.from(s), Uint8Array.from(t))]; })));
""", json.dumps({"map": m, "cases": [[list(a), list(b)] for a, b in cases]}))
        self.assertEqual([g[0] for g in got], [True, True, True])
        self.assertEqual(got[0][1], komaset.tag_problem(cases[0][0], cases[0][1])[1])
        self.assertEqual([g[1] for g in got[1:]], ["", ""])
        self.assertEqual([g[2] for g in got], [True, False, False])


if __name__ == "__main__":
    unittest.main()
