"""Tests for packaging/koma_build.py's metadata: README.md's relative links made absolute for PyPI, and the core
metadata fields. Offline; builds nothing.

    python tools/check.py test          (runs these as "packaging tests")
    python -m unittest discover -s packaging -p 'test_*.py'
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import koma_build  # noqa: E402

RAW = "https://raw.githubusercontent.com/overscanindustries/koma/main/"
BLOB = "https://github.com/overscanindustries/koma/blob/main/"


class AbsoluteLinks(unittest.TestCase):
    def rewrite(self, text: str) -> str:
        return koma_build.absolute_links(text)

    def test_markdown_links_go_to_blob(self):
        self.assertEqual(self.rewrite("see [install](docs/install.md#windows) and [licence](LICENSE)."),
                         f"see [install]({BLOB}docs/install.md#windows) and [licence]({BLOB}LICENSE).")

    def test_markdown_images_go_to_raw(self):
        self.assertEqual(self.rewrite('![logo](./docs/assets/logo.svg "Koma")'),
                         f'![logo]({RAW}docs/assets/logo.svg "Koma")')

    def test_link_to_an_image_file_goes_to_raw(self):
        self.assertEqual(self.rewrite("[full size](docs/img/looks-real.jpg)"), f"[full size]({RAW}docs/img/looks-real.jpg)")

    def test_html_img_and_picture_source_srcset(self):
        html = ('<picture>\n'
                '  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/dark.svg">\n'
                '  <source srcset="docs/a.png 1x, docs/a@2x.png 2x">\n'
                '  <img src="docs/assets/light.svg" alt="Koma" width="320">\n'
                '</picture>\n<a href="docs/user-guide.md">guide</a>\n')
        out = self.rewrite(html)
        self.assertIn(f'srcset="{RAW}docs/assets/dark.svg"', out)
        self.assertIn(f'srcset="{RAW}docs/a.png 1x, {RAW}docs/a@2x.png 2x"', out)
        self.assertIn(f'src="{RAW}docs/assets/light.svg"', out)
        self.assertIn(f'href="{BLOB}docs/user-guide.md"', out)
        self.assertIn('media="(prefers-color-scheme: dark)"', out)

    def test_reference_definitions(self):
        self.assertEqual(self.rewrite("[guide]: docs/user-guide.md\n"), f"[guide]: {BLOB}docs/user-guide.md\n")

    def test_absolute_anchor_and_mailto_untouched(self):
        text = ("[a](https://pipx.pypa.io) [b](#install) [c](mailto:x@example.org) [d](/abs) "
                '<img src="https://example.org/x.png">\n')
        self.assertEqual(self.rewrite(text), text)

    def test_fenced_code_untouched(self):
        text = "```\n[not a link](docs/x.md)\n<img src=\"y.png\">\n```\n[link](docs/x.md)\n"
        out = self.rewrite(text)
        self.assertIn("[not a link](docs/x.md)\n<img src=\"y.png\">", out)
        self.assertTrue(out.endswith(f"[link]({BLOB}docs/x.md)\n"))

    def test_the_real_readme_has_no_relative_target_left(self):
        import re
        out = self.rewrite((koma_build.ROOT / "README.md").read_text(encoding="utf-8"))
        prose = re.sub(r"```.*?```", "", out, flags=re.S)
        for target in re.findall(r"\]\(\s*<?([^)\s>]+)", prose) + re.findall(r'(?:src|href)="([^"]+)"', prose):
            self.assertTrue(target.startswith(("https://", "http://", "#", "mailto:")), target)
        for srcset in re.findall(r'srcset="([^"]+)"', prose):
            for candidate in srcset.split(","):
                self.assertTrue(candidate.strip().startswith("https://"), candidate)


class Metadata(unittest.TestCase):
    def test_fields(self):
        text = koma_build.metadata("1.0.0", readme="# Koma\n\n[x](docs/x.md)\n")
        head, _, body = text.partition("\n\n")
        fields = [line.split(": ", 1) for line in head.splitlines()]
        keys = [k for k, _ in fields]
        self.assertEqual(fields[0], ["Metadata-Version", "2.4"])
        self.assertIn(["License-Expression", "MIT"], fields)
        self.assertIn(["License-File", "LICENSE"], fields)
        self.assertNotIn("License", keys)
        self.assertNotIn("Home-page", keys)
        classifiers = [v for k, v in fields if k == "Classifier"]
        self.assertFalse([c for c in classifiers if c.startswith("License ::")], "PEP 639: no licence classifiers")
        for want in ("Development Status :: 4 - Beta", "Programming Language :: Python :: 3.10",
                     "Programming Language :: Python :: 3.13", "Operating System :: MacOS",
                     "Topic :: Multimedia :: Graphics :: Capture :: Digital Camera"):
            self.assertIn(want, classifiers)
        urls = dict(v.split(", ", 1) for k, v in fields if k == "Project-URL")
        self.assertEqual(set(urls), {"Homepage", "Source", "Issues", "Changelog", "Documentation"})
        self.assertEqual(urls["Changelog"], BLOB + "CHANGELOG.md")
        self.assertEqual(body, f"# Koma\n\n[x]({BLOB}docs/x.md)\n")


if __name__ == "__main__":
    unittest.main()
