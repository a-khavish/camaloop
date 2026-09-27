# Camaloop - one camera in, any camera out.
# Copyright (c) 2026 Khavish Auckaloo.
#
# This program is free software: you may redistribute it and/or modify it
# under the terms of version 3 of the GNU General Public License as published
# by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY - without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.

"""Checks on the tree itself, so it stays consistent and clean."""

import os
import re
import unittest

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import camaloop            # noqa: E402

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "build", "dist",
             ".mypy_cache", ".pytest_cache"}


def walk(suffixes=None):
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = Path(base) / name
            if suffixes and path.suffix.lower() not in suffixes:
                continue
            yield path


class Version(unittest.TestCase):
    def test_the_package_and_the_project_agree(self):
        text = (ROOT / "pyproject.toml").read_text()
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), camaloop.__version__)

    def test_the_version_looks_like_a_version(self):
        self.assertRegex(camaloop.__version__, r"^\d+\.\d+\.\d+$")

    def test_there_is_a_name_and_a_motto(self):
        self.assertTrue(camaloop.MOTTO)


class Packaging(unittest.TestCase):
    def test_the_desktop_entry_is_well_formed(self):
        text = (ROOT / "packaging" / "camaloop.desktop").read_text()
        self.assertTrue(text.startswith("[Desktop Entry]"))
        for key in ("Name=", "Exec=", "Icon=", "Type=", "Categories="):
            self.assertIn(key, text)

    def test_the_icon_is_a_plain_svg(self):
        text = (ROOT / "packaging" / "camaloop.svg").read_text()
        self.assertTrue(text.lstrip().startswith("<svg"))
        self.assertIn("</svg>", text)

    def test_the_scripts_are_there_and_executable_by_bash(self):
        for name in ("setup.sh", "install.sh", "packaging/build-deb.sh"):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_the_licence_and_the_guides_are_there(self):
        for name in ("LICENSE", "README.md", "CONTRIBUTING.md",
                     "CHANGELOG.md", ".gitignore"):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_nothing_references_the_old_name(self):
        # Spelled indirectly so this file is not its own counter-example.
        old = "v" + "cam"
        stale = []
        for path in walk({".py", ".sh", ".toml", ".desktop", ".md", ".txt"}):
            if path.name == Path(__file__).name:
                continue
            if old in path.read_text(errors="ignore").lower():
                stale.append(str(path.relative_to(ROOT)))
        self.assertEqual(stale, [])


class Licensing(unittest.TestCase):
    """The licence is GPLv3 and the whole tree agrees about it."""

    def test_the_licence_file_is_the_gpl_itself(self):
        text = (ROOT / "LICENSE").read_text()
        self.assertIn("GNU GENERAL PUBLIC LICENSE", text)
        self.assertIn("Version 3, 29 June 2007", text)
        self.assertIn("TERMS AND CONDITIONS", text)
        # The verbatim text is long; a truncated copy is not the licence.
        self.assertGreater(len(text.splitlines()), 600)

    def test_there_is_a_notice_file(self):
        text = (ROOT / "NOTICE").read_text()
        self.assertIn("Khavish Auckaloo", text)
        self.assertIn("haarcascade_frontalface_default.xml", text)

    def test_the_version_file_agrees_with_the_package(self):
        text = (ROOT / "VERSION").read_text().strip()
        self.assertEqual(text, camaloop.__version__)

    def test_the_project_metadata_says_gpl(self):
        text = (ROOT / "pyproject.toml").read_text()
        self.assertIn('license = { text = "GPL-3.0-only" }', text)
        self.assertIn("GNU General Public License v3 (GPLv3)", text)

    def test_nothing_still_claims_the_old_licence(self):
        # Spelled indirectly so this file is not its own counter-example.
        old = "M" + "IT"
        stale = []
        for path in walk({".py", ".sh", ".toml", ".desktop", ".md", ".txt",
                          ".yml", ".yaml", ".cfg"}):
            if path.name == Path(__file__).name:
                continue
            for number, line in enumerate(path.read_text(errors="ignore")
                                          .splitlines(), 1):
                if re.search(r"\b" + old + r"\b", line):
                    stale.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(stale, [])

    def test_every_module_carries_the_licence_header(self):
        missing = []
        for path in walk({".py"}):
            head = "\n".join(path.read_text().splitlines()[:16])
            if "GNU General Public License" not in head:
                missing.append(str(path.relative_to(ROOT)))
        self.assertEqual(missing, [])

    def test_no_placeholders_are_left_in_the_links(self):
        leftovers = []
        for path in walk({".md", ".toml", ".sh", ".yml"}):
            if path.name == Path(__file__).name:
                continue
            text = path.read_text(errors="ignore")
            for marker in ("<owner>", "example.invalid"):
                if marker in text:
                    leftovers.append(f"{path.relative_to(ROOT)}: {marker}")
        self.assertEqual(leftovers, [])


class OneAuthor(unittest.TestCase):
    """The tree carries one author's name and no tooling's.

    This repository is portfolio work. Nothing in it should credit, mention
    or hint at an authoring tool, in source, in prose or in file metadata.
    """

    # Spelled indirectly so this file is not its own counter-example.
    # "Cursor" is deliberately absent: Qt uses the word for the mouse pointer
    # all over the interface code, so it cannot be a needle here.
    NAMES = ("Cla" + "ude", "Anthro" + "pic", "Cop" + "ilot", "Chat" + "GPT",
             "Open" + "AI", "Gem" + "ini", "Code" + "ium", "Wind" + "surf",
             "co-authored-by", "generated by", "written by an",
             "language model", "AI-generated", "AI assistant")

    def test_no_tool_is_named_anywhere_in_the_text(self):
        offenders = []
        for path in walk({".py", ".sh", ".toml", ".desktop", ".md", ".txt",
                          ".yml", ".yaml", ".cfg", ".svg", ".editorconfig",
                          ".gitattributes", ".gitignore"}):
            if path.name == Path(__file__).name:
                continue
            lowered = path.read_text(errors="ignore").lower()
            for name in self.NAMES:
                if name.lower() in lowered:
                    offenders.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(offenders, [])

    def test_no_tool_is_named_inside_the_binaries(self):
        offenders = []
        for path in walk({".png", ".jpg", ".jpeg", ".webp", ".mp4", ".xml"}):
            data = path.read_bytes().lower()
            for name in self.NAMES:
                if name.lower().encode() in data:
                    offenders.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(offenders, [])


class NoStrayMetadata(unittest.TestCase):
    """Nothing in the tree should carry provenance or tooling metadata.

    Image and vector files can arrive with manifests embedded in them, which
    are invisible in an editor and travel with the file into the repository.
    """

    NEEDLES = (b"c2pa", b"C2PA", b"jumbf", b"xmpmeta")

    def test_no_embedded_manifests(self):
        offenders = []
        for path in walk({".svg", ".png", ".jpg", ".jpeg", ".webp", ".mp4"}):
            data = path.read_bytes()
            if any(needle in data for needle in self.NEEDLES):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], "these carry embedded metadata")

    def test_pngs_hold_nothing_but_the_picture(self):
        """A PNG carries metadata in its own chunks, so check those directly
        rather than searching the compressed bytes for text."""
        import struct

        wanted = {"IHDR", "PLTE", "IDAT", "IEND", "tRNS", "gAMA", "sRGB",
                  "pHYs", "bKGD", "cHRM", "sBIT"}
        offenders = []
        for path in walk({".png"}):
            data = path.read_bytes()
            if not data.startswith(b"\x89PNG"):
                offenders.append(f"{path.relative_to(ROOT)}: not a PNG")
                continue
            offset = 8
            while offset + 8 <= len(data):
                length = struct.unpack(">I", data[offset:offset + 4])[0]
                kind = data[offset + 4:offset + 8].decode("ascii", "replace")
                if kind not in wanted:
                    offenders.append(f"{path.relative_to(ROOT)}: {kind}")
                offset += 12 + length
                if kind == "IEND":
                    break
        self.assertEqual(offenders, [])

    def test_no_editor_leftovers(self):
        leftovers = [str(p.relative_to(ROOT)) for p in walk()
                     if p.name.endswith(("~", ".orig", ".rej", ".bak"))]
        self.assertEqual(leftovers, [])


class SourceHygiene(unittest.TestCase):
    def test_every_module_parses(self):
        import ast

        for path in walk({".py"}):
            with self.subTest(file=str(path.relative_to(ROOT))):
                ast.parse(path.read_text(), filename=str(path))

    def test_no_tabs_in_python(self):
        offenders = [str(p.relative_to(ROOT)) for p in walk({".py"})
                     if "\t" in p.read_text()]
        self.assertEqual(offenders, [])

    def test_no_trailing_whitespace_in_python(self):
        offenders = []
        for path in walk({".py"}):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if line != line.rstrip():
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(offenders[:10], [])

    def test_nothing_left_to_do(self):
        # Spelled indirectly so this file is not its own counter-example.
        markers = ("TO" + "DO", "FIX" + "ME", "X" + "XX", "HA" + "CK")
        offenders = []
        for path in walk({".py", ".sh"}):
            text = path.read_text()
            for marker in markers:
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {marker}")
        self.assertEqual(offenders, [])

    def test_lines_stay_readable(self):
        long_lines = []
        for path in walk({".py"}):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if len(line) > 100:
                    long_lines.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(long_lines[:10], [])


if __name__ == "__main__":
    unittest.main()
