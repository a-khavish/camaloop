#!/usr/bin/env python3
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

"""Start the interface, walk through it, and save a picture of each tab.

    python3 tools/smoketest.py [--out FOLDER] [--size 1920x1080]

Runs with no display of its own, so it works over ssh and on a build
machine. It fails loudly if a tab will not build, if a widget is missing,
or if anything writes to Qt's warning channel.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Use a real display when there is one, so the pictures come out at the
# size asked for; fall back to drawing into memory when there is not.
if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

COMPLAINTS = []


def collect(mode, context, message):
    text = str(message)
    # Offscreen rendering grumbles about things that do not matter here.
    for benign in ("QStandardPaths", "propagateSizeHints", "Wayland",
                   "libpng", "qt.qpa.fonts"):
        if benign in text:
            return
    COMPLAINTS.append(text)


def outside_the_window(window):
    """Visible controls whose rectangle falls outside the window."""
    from PyQt5.QtWidgets import QAbstractScrollArea, QWidget

    def scrollable(widget):
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                return True
            parent = parent.parentWidget()
        return False

    escaped = []
    frame = window.rect()
    page = window.tabs.currentWidget()
    if page is None:
        return escaped
    for child in page.findChildren(QWidget):
        if not child.isVisible() or child.width() < 4 or child.height() < 4:
            continue
        if child.children() or scrollable(child):
            continue
        low = child.mapTo(window, child.rect().bottomRight())
        high = child.mapTo(window, child.rect().topLeft())
        if (low.y() > frame.bottom() + 1 or low.x() > frame.right() + 1
                or high.y() < frame.top() - 1 or high.x() < frame.left() - 1):
            label = getattr(child, "text", lambda: "")() or ""
            escaped.append(
                f"{child.__class__.__name__}"
                f"{'(' + label[:20] + ')' if label else ''}"
                f" ends at {low.x()},{low.y()}")
    return escaped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="", help="where to save the pictures")
    parser.add_argument("--size", default="1920x1080")
    parser.add_argument("--no-shots", action="store_true")
    parser.add_argument("--sizes", default="",
                        help="also check these, e.g. 1280x720,1366x768")
    args = parser.parse_args()

    width, _, height = args.size.partition("x")
    width, height = int(width), int(height)

    from PyQt5.QtCore import qInstallMessageHandler
    from PyQt5.QtWidgets import QAbstractScrollArea, QApplication, QWidget

    qInstallMessageHandler(collect)

    from camaloop.core.effects import EFFECTS
    from camaloop.ui.main_window import MainWindow

    app = QApplication(["camaloop-smoketest"])

    from camaloop.ui import theme

    theme.apply(app)
    app.setWindowIcon(theme.app_icon())

    window = MainWindow()
    # Off screen there is no window manager, so "maximised" means whatever
    # Qt guessed. Unlock it and set the size by hand instead.
    window.apply_window_lock(False)
    window.resize(width, height)
    window.show()
    app.processEvents()

    print(f"window      {window.width()}x{window.height()}  "
          f"\"{window.windowTitle()}\"")
    print(f"icon        {'painted' if window.windowIcon() else 'missing'}, "
          f"sizes {len(window.windowIcon().availableSizes())}")
    print(f"effects     {len(EFFECTS)}")
    demanded = window.minimumSizeHint()
    smallest = window.minimumSize()
    print(f"asks for    at least {smallest.width()}x{smallest.height()}, "
          f"layout wants {demanded.width()}x{demanded.height()} at its narrowest")

    failures = []
    # A tab whose minimum is taller than a screen is the fault that puts the
    # bottom row of controls below the edge of the display.
    for index in range(window.tabs.count()):
        hint = window.tabs.widget(index).minimumSizeHint()
        if hint.height() > 1000 or hint.width() > 1280:
            failures.append(
                f"the {window.tabs.tabText(index)} tab demands "
                f"{hint.width()}x{hint.height()} at its narrowest")

    folder = args.out or os.path.join(os.getcwd(), "smoketest")
    if not args.no_shots:
        os.makedirs(folder, exist_ok=True)

    for index in range(window.tabs.count()):
        title = window.tabs.tabText(index)
        window.tabs.setCurrentIndex(index)
        for _ in range(3):
            app.processEvents()
        page = window.tabs.currentWidget()
        if page is None:
            failures.append(f"{title}: no widget")
            continue
        picture = window.grab()
        if picture.isNull():
            failures.append(f"{title}: nothing was drawn")
            continue
        note = ""
        if not args.no_shots:
            name = title.lower().replace(" ", "-") + ".png"
            path = os.path.join(folder, name)
            picture.save(path)
            note = f" -> {path}"
        print(f"  tab       {title:<18} {picture.width()}x{picture.height()}{note}")

    # Open every effect card, which builds every control in the app.
    panel = getattr(window.studio, "effects", None)
    if panel is not None:
        for card in panel.cards:
            card.set_expanded(True)
        app.processEvents()
        controls = sum(len(card.rows) for card in panel.cards)
        print(f"  controls  {controls} across {len(panel.cards)} effects")
        if not args.no_shots:
            window.tabs.setCurrentIndex(0)
            app.processEvents()
            window.grab().save(os.path.join(folder, "every-control.png"))
        panel.collapse_all()
    else:
        failures.append("the effects panel is not reachable")

    # Nothing may sit outside the window at any size someone might use it
    # at. This is the check that catches a control pushed off the bottom
    # edge, which is invisible in a screenshot taken at one size only.
    checked = args.sizes or "1280x720,1366x768,1600x900,1920x1080"
    room = app.primaryScreen().availableGeometry()
    print()
    for pair in checked.split(","):
        try:
            test_w, _, test_h = pair.strip().partition("x")
            test_w, test_h = int(test_w), int(test_h)
        except ValueError:
            continue
        if test_w > room.width() or test_h > room.height():
            print(f"  skip      {pair.strip():<12} this display is only "
                  f"{room.width()}x{room.height()}")
            continue
        window.resize(test_w, test_h)
        window.show()
        app.processEvents()
        if (window.width(), window.height()) != (test_w, test_h):
            print(f"  STUCK     {pair.strip():<12} the window would not resize "
                  f"(it is {window.width()}x{window.height()}); something has "
                  f"fixed its size")
            failures.append(f"{pair.strip()}: the window would not resize")
            continue
        escaped_here = []
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index)
            for _ in range(3):
                app.processEvents()
            escaped_here += [
                f"{window.tabs.tabText(index)}: {item}"
                for item in outside_the_window(window)
            ]
        if escaped_here:
            print(f"  CUT OFF   {pair.strip():<12} " + "; ".join(escaped_here[:3]))
            failures += escaped_here
        else:
            print(f"  fits      {pair.strip():<12} nothing outside the window")

    window.close()
    app.processEvents()

    if COMPLAINTS:
        print("\nQt complained:")
        for line in dict.fromkeys(COMPLAINTS):
            print("  " + line)
        failures.extend(COMPLAINTS)

    if failures:
        print("\nFAILED:")
        for line in failures:
            print("  " + line)
        return 1
    print("\nThe interface builds, draws and responds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
