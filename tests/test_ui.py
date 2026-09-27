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

"""The interface, built off screen.

Everything here runs under Qt's "offscreen" platform, so it needs no
display and can run on a build machine.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtWidgets import QApplication   # noqa: E402

from camaloop.core.effects import EFFECTS, GROUP_ORDER   # noqa: E402
from camaloop.ui import docs_tab                         # noqa: E402

APP = None


def setUpModule():
    global APP
    APP = QApplication.instance() or QApplication(["camaloop-tests"])


class Documentation(unittest.TestCase):
    def test_there_are_sections(self):
        self.assertGreaterEqual(len(docs_tab.SECTIONS), 8)

    def test_every_section_has_a_title_and_a_body(self):
        for title, body in docs_tab.SECTIONS:
            with self.subTest(section=title):
                self.assertTrue(title.strip())
                self.assertGreater(len(body.strip()), 120)

    def test_titles_are_unique(self):
        titles = [t for t, _ in docs_tab.SECTIONS]
        self.assertEqual(len(titles), len(set(titles)))

    def test_every_effect_is_documented(self):
        text = " ".join(body for _title, body in docs_tab.SECTIONS)
        missing = [e.name for e in EFFECTS if e.name not in text]
        self.assertEqual(missing, [], "these effects are not in the docs")

    def test_every_group_is_documented(self):
        text = " ".join(body for _title, body in docs_tab.SECTIONS)
        missing = [group for group in GROUP_ORDER if group not in text]
        self.assertEqual(missing, [], "these groups have no heading in the docs")

    def test_the_tab_builds_and_shows_every_section(self):
        tab = docs_tab.DocsTab()
        self.assertEqual(tab.contents.count(), len(docs_tab.SECTIONS))
        self.assertEqual(len(tab._anchors), len(docs_tab.SECTIONS))

    def test_a_bullet_that_wraps_stays_one_bullet(self):
        tab = docs_tab.DocsTab()
        html = tab._html(
            "Intro line.\n"
            "\n"
            "- first point\n"
            "  carried on to a second line\n"
            "- second point\n"
        )
        self.assertEqual(html.count("<li"), 2,
                         "an indented continuation line started a new bullet")
        self.assertIn("carried on", html)

    def test_markup_characters_in_the_text_are_escaped(self):
        tab = docs_tab.DocsTab()
        html = tab._html("Use <video0> & carry on.")
        self.assertIn("&lt;video0&gt;", html)
        self.assertIn("&amp;", html)


class TheWholeWindow(unittest.TestCase):
    def setUp(self):
        from camaloop.ui.main_window import MainWindow

        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def test_it_has_all_its_tabs(self):
        titles = [self.window.tabs.tabText(i)
                  for i in range(self.window.tabs.count())]
        for wanted in ("Studio", "Virtual cameras", "Documentation",
                       "Settings"):
            self.assertTrue(any(wanted in t for t in titles),
                            f"{wanted} is missing from {titles}")

    def test_every_tab_can_be_shown(self):
        for index in range(self.window.tabs.count()):
            self.window.tabs.setCurrentIndex(index)
            APP.processEvents()
            self.assertIsNotNone(self.window.tabs.currentWidget())

    def test_the_window_has_a_title_and_an_icon(self):
        self.assertIn("Camaloop", self.window.windowTitle())
        self.assertFalse(self.window.windowIcon().isNull())

    def test_it_draws_at_the_sizes_people_use(self):
        for width, height in ((1920, 1080), (1366, 768), (1280, 720)):
            with self.subTest(size=(width, height)):
                self.window.setMinimumSize(0, 0)
                self.window.resize(width, height)
                self.window.show()
                APP.processEvents()
                picture = self.window.grab()
                self.assertFalse(picture.isNull())
                self.assertGreater(picture.width(), 600)
                self.assertGreater(picture.height(), 400)

    def test_nothing_is_pushed_off_the_bottom(self):
        self.window.setMinimumSize(0, 0)
        self.window.resize(1280, 720)
        self.window.show()
        APP.processEvents()
        for index in range(self.window.tabs.count()):
            self.window.tabs.setCurrentIndex(index)
            APP.processEvents()
            page = self.window.tabs.currentWidget()
            with self.subTest(tab=self.window.tabs.tabText(index)):
                self.assertLessEqual(page.sizeHint().height(), 2400)


class TheEffectsPanel(unittest.TestCase):
    def setUp(self):
        from camaloop.core.effects import Pipeline
        from camaloop.ui.effects_panel import EffectsPanel

        self.pipeline = Pipeline()
        self.panel = EffectsPanel(self.pipeline)
        self.by_id = {card.effect.id: card for card in self.panel.cards}

    def tearDown(self):
        self.panel.deleteLater()
        APP.processEvents()

    def test_there_is_a_card_for_every_effect(self):
        self.assertEqual(len(self.panel.cards), len(EFFECTS))
        self.assertEqual(set(self.by_id), {e.id for e in EFFECTS})

    def test_every_face_warp_can_be_picked(self):
        widget = self.by_id["facewarp"].rows["style"].combo
        listed = [widget.itemText(i) for i in range(widget.count())]
        self.assertIn("Big head", listed)
        self.assertGreaterEqual(len(listed), 6)

    def test_a_card_opens_and_closes(self):
        card = self.by_id["vignette"]
        card.set_expanded(True)
        self.assertTrue((not card.body.isHidden()))
        card.set_expanded(False)
        self.assertFalse((not card.body.isHidden()))

    def test_collapse_all_closes_everything(self):
        for card in self.panel.cards:
            card.set_expanded(True)
        self.panel.collapse_all()
        self.assertFalse(any(not c.body.isHidden() for c in self.panel.cards))

    def test_turning_an_effect_on_reaches_the_pipeline(self):
        card = self.by_id["vignette"]
        card.toggle.setChecked(True)
        APP.processEvents()
        self.assertTrue(self.pipeline.is_enabled("vignette"))
        card.toggle.setChecked(False)
        APP.processEvents()
        self.assertFalse(self.pipeline.is_enabled("vignette"))

    def test_the_panel_follows_the_pipeline(self):
        self.pipeline.set_enabled("glitch", True)
        self.panel.sync()
        self.assertTrue(self.by_id["glitch"].toggle.isChecked())


class TheIcon(unittest.TestCase):
    def test_there_is_always_one(self):
        from camaloop.ui import theme

        icon = theme.app_icon()
        self.assertFalse(icon.isNull())
        self.assertTrue(icon.availableSizes())

    def test_it_can_be_painted_without_any_file(self):
        from camaloop.ui import theme

        pixmap = theme.draw_icon(64)
        self.assertEqual(pixmap.size().width(), 64)
        self.assertFalse(pixmap.isNull())
        # Not a blank square.
        image = pixmap.toImage()
        colours = {image.pixel(x, y)
                   for x in range(0, 64, 4) for y in range(0, 64, 4)}
        self.assertGreater(len(colours), 3)


if __name__ == "__main__":
    unittest.main()
