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

"""The window has to fit the screen, on every screen.

These are the checks that would have caught a window taller than the display
with its bottom row of buttons off the edge: the minimum the layout demands
is compared against the room a real screen leaves, and every control is
checked for being inside the window rather than past its edge.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtWidgets import (                                   # noqa: E402
    QAbstractScrollArea, QApplication, QWidget,
)

APP = None

# Width, height, and a name. These are the common desktop and laptop sizes;
# the smallest is the one the window must still fit inside.
SCREENS = [
    (1280, 720, "720p laptop"),
    (1366, 768, "the commonest laptop panel"),
    (1440, 900, "13-inch"),
    (1600, 900, "16:9 laptop"),
    (1920, 1080, "1080p desktop"),
    (2560, 1440, "1440p desktop"),
]

# What a desktop takes off the top and bottom before an application gets a
# look in: a panel or dock, plus the window's own title bar.
DESKTOP_FURNITURE = 96


def setUpModule():
    global APP
    APP = QApplication.instance() or QApplication(["camaloop-tests"])


def room_available():
    """How much space a window can actually be given here.

    Qt's offscreen platform pretends the screen is 800x600, so on a build
    machine with no display only the smallest sizes can be exercised. Run
    the suite under a virtual display to test the rest:

        QT_QPA_PLATFORM=xcb xvfb-run -a --server-args="-screen 0 2560x1440x24" \
            python3 -m unittest discover -s tests

    The platform has to be named explicitly. This module falls back to the
    offscreen platform so the suite runs anywhere, and that fallback would
    otherwise win even with a virtual display present, leaving these checks
    skipped on a machine that could have run them.
    """
    screen = QApplication.primaryScreen()
    if screen is None:
        return 800, 600
    geometry = screen.availableGeometry()
    return geometry.width(), geometry.height()


def testable_screens():
    """The sizes from the list that this display can actually show."""
    room_w, room_h = room_available()
    return [(w, h, name) for w, h, name in SCREENS
            if w <= room_w and h - DESKTOP_FURNITURE <= room_h]


def smallest_testable():
    usable = testable_screens()
    return usable[0] if usable else (min(800, room_available()[0]),
                                     min(600, room_available()[1]) + DESKTOP_FURNITURE,
                                     "this display")


def fresh_window():
    from camaloop.ui.main_window import MainWindow

    window = MainWindow()
    # The lock is about staying maximised, which no window manager is here to
    # do. These tests are about whether the layout fits, so it is turned off.
    window.apply_window_lock(False)
    return window


def inside_a_scroll_area(widget) -> bool:
    """A widget in a scroll area is allowed to be out of view."""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return True
        parent = parent.parentWidget()
    return False


def escaping_widgets(window, page):
    """Visible controls whose rectangle falls outside the window."""
    escaped = []
    frame = window.rect()
    for child in page.findChildren(QWidget):
        if not child.isVisible() or child.width() < 4 or child.height() < 4:
            continue
        if inside_a_scroll_area(child):
            continue
        if child.children():
            continue                      # containers, not controls
        top_left = child.mapTo(window, child.rect().topLeft())
        bottom_right = child.mapTo(window, child.rect().bottomRight())
        if (top_left.y() < frame.top() - 1
                or bottom_right.y() > frame.bottom() + 1
                or top_left.x() < frame.left() - 1
                or bottom_right.x() > frame.right() + 1):
            label = getattr(child, "text", lambda: "")() or ""
            escaped.append(
                f"{child.__class__.__name__}"
                f"{'(' + label[:24] + ')' if label else ''}"
                f" at {top_left.x()},{top_left.y()} "
                f"to {bottom_right.x()},{bottom_right.y()}"
            )
    return escaped


class WhatTheLayoutDemands(unittest.TestCase):
    def setUp(self):
        self.window = fresh_window()
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def test_the_width_it_demands_fits_the_smallest_screen(self):
        width, height, _name = SCREENS[0]   # the target, not this display
        demanded = self.window.minimumSizeHint().width()
        self.assertLessEqual(
            demanded, width,
            f"the layout demands {demanded} px of width; a "
            f"{width}x{height} screen cannot give it")

    def test_the_height_it_demands_is_not_absurd(self):
        """A height measured at the narrowest the layout can go.

        Bars that wrap are taller when they are narrow, so this figure is
        always pessimistic and is not compared against a screen. What it
        does catch is the real fault: a word-wrapped label put straight into
        a panel asks for however many hundreds of pixels its text needs, and
        the tab passes that demand up to the window.
        """
        ceiling = 1000
        demanded = self.window.minimumSizeHint().height()
        self.assertLessEqual(
            demanded, ceiling,
            f"the layout demands {demanded} px of height even at its "
            f"narrowest; something long is not in a scroll area")

    def test_the_minimum_the_window_asks_for_fits_this_display(self):
        room_w, room_h = room_available()
        minimum = self.window.minimumSize()
        self.assertLessEqual(
            minimum.width(), room_w,
            "the window insists on being wider than the screen it is on")
        self.assertLessEqual(
            minimum.height(), room_h,
            "the window insists on being taller than the screen it is on")

    def test_no_single_tab_is_out_of_proportion(self):
        width, _height, _name = SCREENS[0]   # the target, not this display
        too_big = []
        for index in range(self.window.tabs.count()):
            page = self.window.tabs.widget(index)
            hint = page.minimumSizeHint()
            if hint.width() > width or hint.height() > 1000:
                too_big.append(
                    f"{self.window.tabs.tabText(index)} wants "
                    f"{hint.width()}x{hint.height()}")
        self.assertEqual(too_big, [])


class NothingIsCutOff(unittest.TestCase):
    def setUp(self):
        self.window = fresh_window()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def test_every_control_is_inside_the_window_at_every_size(self):
        screens = testable_screens() or [smallest_testable()]
        problems = []
        for width, height, name in screens:
            self.window.resize(width, height - DESKTOP_FURNITURE)
            self.window.show()
            APP.processEvents()
            # Without this the rest of the check is worthless: if the window
            # refused to shrink, every control is trivially inside the
            # oversized window it kept.
            self.assertEqual(
                (self.window.width(), self.window.height()),
                (width, height - DESKTOP_FURNITURE),
                f"the window would not resize to {name}; it stayed "
                f"{self.window.width()}x{self.window.height()}")
            for index in range(self.window.tabs.count()):
                self.window.tabs.setCurrentIndex(index)
                for _ in range(3):
                    APP.processEvents()
                page = self.window.tabs.currentWidget()
                escaped = escaping_widgets(self.window, page)
                if escaped:
                    label = self.window.tabs.tabText(index)
                    problems.append(
                        f"{name} ({width}x{height}), {label}: "
                        + "; ".join(escaped[:4]))
        self.assertEqual(problems, [])

    def test_the_studio_controls_stay_reachable(self):
        """The buttons along the bottom are the ones that went missing."""
        for width, height, name in testable_screens() or [smallest_testable()]:
            self.window.resize(width, height - DESKTOP_FURNITURE)
            self.window.tabs.setCurrentIndex(0)
            self.window.show()
            for _ in range(3):
                APP.processEvents()
            self.assertEqual(
                (self.window.width(), self.window.height()),
                (width, height - DESKTOP_FURNITURE),
                f"the window would not resize to {name}")
            studio = self.window.studio
            for attribute in ("start_button", "record_button", "live_button",
                              "output_combo", "source_combo"):
                control = getattr(studio, attribute, None)
                if control is None:
                    continue
                with self.subTest(screen=name, control=attribute):
                    self.assertTrue(control.isVisible(), "not shown at all")
                    bottom = control.mapTo(
                        self.window, control.rect().bottomRight())
                    self.assertLessEqual(
                        bottom.y(), self.window.height(),
                        f"{attribute} runs {bottom.y() - self.window.height()} px "
                        f"past the bottom of the window")
                    self.assertLessEqual(
                        bottom.x(), self.window.width(),
                        f"{attribute} runs past the right edge")


class BarsThatWrap(unittest.TestCase):
    """The controls along the top and bottom of Studio fold onto a second
    line rather than making the window wider than the screen."""

    def setUp(self):
        self.window = fresh_window()
        self.window.tabs.setCurrentIndex(0)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def heights(self, width):
        self.window.show()
        self.window.resize(width, min(700, room_available()[1]))
        for _ in range(4):
            APP.processEvents()
        studio = self.window.studio
        return {
            name: bar.height()
            for name, bar in (("source", studio.source_combo.parentWidget()),
                              ("capture", studio.record_button.parentWidget()),
                              ("output", studio.live_button.parentWidget()))
        }

    def test_a_narrow_window_makes_the_bars_taller_not_wider(self):
        room_w, _ = room_available()
        if room_w < 1400:
            self.skipTest(f"this display is only {room_w} px wide; "
                          "run under a larger virtual display")
        wide = self.heights(min(1900, room_w))
        narrow = self.heights(1000)
        self.assertTrue(
            any(narrow[name] > wide[name] for name in wide),
            f"nothing wrapped: wide {wide}, narrow {narrow}")

    def test_a_wide_window_keeps_them_on_one_line(self):
        room_w, _ = room_available()
        if room_w < 1400:
            self.skipTest(f"this display is only {room_w} px wide")
        wide = self.heights(min(1900, room_w))
        for name, height in wide.items():
            self.assertLess(height, 110,
                            f"the {name} bar is on more than one line at 1900 px")

    def test_the_buttons_keep_their_size_when_they_wrap(self):
        room_w, _ = room_available()
        if room_w < 1100:
            self.skipTest(f"this display is only {room_w} px wide")
        self.heights(1000)
        studio = self.window.studio
        for control in (studio.record_button, studio.photo_button,
                        studio.live_button, studio.start_button):
            self.assertGreaterEqual(control.width(), 60)
            self.assertGreaterEqual(control.height(), 20)


class TheWindowCanActuallyShrink(unittest.TestCase):
    """Resizing has to take effect, or every other check here is hollow."""

    def setUp(self):
        self.window = fresh_window()
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def test_it_goes_down_to_the_size_it_says_it_can(self):
        smallest = self.window.minimumSize()
        self.window.resize(smallest.width(), smallest.height())
        APP.processEvents()
        self.assertEqual(self.window.width(), smallest.width())
        self.assertEqual(self.window.height(), smallest.height())

    def test_it_fits_the_height_a_1080p_screen_leaves(self):
        """The screen the fault was reported on: 1920x1080 with a panel."""
        room_w, room_h = room_available()
        if room_w < 1920 or room_h < 1040:
            self.skipTest("this display is smaller than the one to check")
        self.window.resize(1920, 1040)
        APP.processEvents()
        self.assertEqual(
            (self.window.width(), self.window.height()), (1920, 1040),
            "the window will not fit under a desktop panel on a 1080p screen")

    def test_it_fits_a_720p_laptop(self):
        room_w, room_h = room_available()
        if room_w < 1280 or room_h < 640:
            self.skipTest("this display is smaller than the one to check")
        self.window.resize(1280, 640)
        APP.processEvents()
        self.assertEqual((self.window.width(), self.window.height()),
                         (1280, 640))


class StayingMaximised(unittest.TestCase):
    def setUp(self):
        self.window = fresh_window()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def test_locking_does_not_freeze_the_window_at_a_size(self):
        """A fixed size is the wrong tool for this.

        Asking for a maximised window and then freezing whatever size it
        happens to have at that instant gives the size from before the window
        manager has maximised anything - which is how the window ended up
        both too small for the screen and too tall for it at the same time.
        """
        self.window.apply_window_lock(True)
        APP.processEvents()
        self.assertNotEqual(
            self.window.minimumSize(), self.window.maximumSize(),
            "the window has been frozen at one exact size")

    def test_locking_never_asks_for_more_room_than_the_screen_has(self):
        room_w, room_h = room_available()
        self.window.apply_window_lock(True)
        APP.processEvents()
        minimum = self.window.minimumSize()
        self.assertLessEqual(minimum.width(), room_w)
        self.assertLessEqual(minimum.height(), room_h)

    def test_a_locked_window_is_pulled_back_inside_the_screen(self):
        room_w, room_h = room_available()
        self.window.apply_window_lock(True)
        self.window.show()
        self.window.resize(room_w + 600, room_h + 600)
        self.window.fit_to_screen()
        APP.processEvents()
        self.assertLessEqual(self.window.width(), room_w)
        self.assertLessEqual(self.window.height(), room_h)

    def test_the_lock_can_be_turned_off_and_on(self):
        for state in (True, False, True, False):
            self.window.apply_window_lock(state)
            APP.processEvents()
        self.window.resize(1280, 640)
        APP.processEvents()
        self.assertGreater(self.window.width(), 0)


if __name__ == "__main__":
    unittest.main()
