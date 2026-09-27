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

"""The window: three tabs, a status strip and the activity log."""

from __future__ import annotations

import time

import glob

from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QMenu,
    QCheckBox,
    QFrame,
    QScrollArea,
    QMessageBox,
    QSystemTrayIcon,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QShortcut,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import loopback, presets
from ..core.effects import Pipeline
from ..core.settings import Settings
from . import theme
from .devices_tab import DevicesTab
from .docs_tab import DocsTab
from .settings_tab import SettingsTab
from .studio_tab import StudioTab
from .widgets import TallyLight, heading, panel

GUIDE = """How this works

1. Make a virtual camera on the Virtual cameras tab. Give it a name you will
   recognise in a video call - it appears in other apps exactly as you type it.
2. Come back to Studio, pick your real camera as the source and press
   Start camera.
3. Switch on the effects you want. Everything updates as you drag.
4. Press Share with other apps. The light turns red while the feed is live.
5. In the other app - a browser, a meeting, a recorder - choose the virtual
   camera by name. Apps that were already open usually need restarting before
   a brand new camera shows up in their list.

Photos and recordings

Take a photo saves exactly the frame being sent, effects and all. Start
recording writes the same picture to a video file, with the elapsed time and
frame count shown as it goes. Both land in your Videos folder, or your home
folder if you have not got one, named by the date and time.

Shaping the picture

- Framing mirrors, flips, rotates by a quarter turn, and straightens a tilted
  camera by any fine angle.
- Crop trims the edges or cuts to a shape - 1:1 for a profile, 9:16 for
  phone-shaped video - then stretches back, keeps the smaller size, or adds bars.
- Zoom and reframe crops into the sensor; the zoom buttons under the preview
  do the same thing live.
- Output size decides what other apps and recordings actually receive, which
  can differ from what the camera gives.

Putting things on top

Picture or video on top takes a PNG, a photo or a video file. It can be placed
in a corner or anywhere you like, trimmed on each edge, stretched, rotated,
faded, and told to loop. Hide black areas keys out a black background, which
is how most effect footage is supplied.

Keyboard

  Space          take a photo
  Ctrl and R     start or stop recording
  Ctrl and L     start or stop sharing
  Ctrl and K     start or stop the camera
  Ctrl and +/-   zoom in and out
  Ctrl and 0     back to 1x

Good to know

- Your real camera can only be opened by one program at a time. Let this app
  hold it, and give everything else the virtual camera.
- "Announce as a capture-only device" is on by default because Chrome, Zoom
  and Teams ignore devices that claim to do both capture and output.
- Cameras you make disappear when the machine restarts. Press
  Keep after reboot to write them into /etc/modprobe.d.
- Background replacement without a green screen needs one extra package:
  pip install mediapipe
- Accented and non-Latin names in the text overlay need Pillow:
  sudo apt install python3-pil
- If opening a camera is refused, add yourself to the video group:
  sudo usermod -aG video $USER    then log out and back in.
"""


# Qt's own "no maximum", and the smallest the window is ever allowed to ask
# for. The minimum has to fit the smallest screen anyone runs this on, or the
# window manager cannot make the window fit the display and the bottom of it
# ends up below the edge of the screen.
UNLIMITED = 16777215
MIN_WIDTH = 940
MIN_HEIGHT = 600


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Camaloop")
        self._locked = False
        self.resize(1440, 900)
        self.setMinimumSize(*self._smallest_size())

        self.settings = Settings()
        self.pipeline = Pipeline()
        if self.settings["remember_effects"]:
            saved = self.settings["last_effects"]
            if saved:
                self.pipeline.restore(saved)

        self.studio = StudioTab(self.pipeline)
        self.studio.capture_folder = lambda: self.settings["capture_folder"]
        self.devices = DevicesTab()
        self.docs = DocsTab()
        self.settings_tab = SettingsTab(self.settings)

        self.setWindowIcon(theme.app_icon())

        self.tabs = QTabWidget()
        self.tabs.addTab(self.studio, "Studio")
        self.tabs.addTab(self.devices, "Virtual cameras")
        self.tabs.addTab(self.docs, "Documentation")
        self.tabs.addTab(self.settings_tab, "Settings")
        self.tabs.addTab(self._help_tab(), "Activity")
        self.setCentralWidget(self.tabs)

        self.settings_tab.log.connect(self.write_log)
        self.settings_tab.lock_changed.connect(self.apply_window_lock)
        self.settings_tab.tray_changed.connect(self.apply_tray)
        self._tray = None
        self._really_quit = False

        self.studio.log.connect(self.write_log)
        self.studio.live_changed.connect(self._on_live)
        self.devices.log.connect(self.write_log)
        self.devices.devices_changed.connect(self.studio.refresh_outputs)

        self._build_status_bar()
        self._build_shortcuts()
        self._watch_devices()
        self.apply_tray(bool(self.settings["tray_icon"]))
        self.apply_window_lock(bool(self.settings["lock_window"]))
        # The environment goes in the log and in the strip on the right; the
        # left of the status bar is for whatever happened most recently.
        self.write_log(loopback.environment_report())
        self.status_text.setText("Ready")

    # ------------------------------------------------------------------

    def _help_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(14)

        guide_panel = panel()
        guide_layout = QVBoxLayout(guide_panel)
        guide_layout.setContentsMargins(20, 18, 20, 18)
        guide_layout.setSpacing(10)
        guide_layout.addWidget(heading("Getting set up"))

        guide = QLabel(GUIDE)
        guide.setWordWrap(True)
        guide.setTextInteractionFlags(Qt.TextSelectableByMouse)
        guide.setAlignment(Qt.AlignTop)

        # In a scroll area, not straight into the panel. A word-wrapped label
        # asks for whatever height its text needs at its narrowest, and this
        # one asked for over a thousand pixels - which the tab passed up to
        # the window, leaving it taller than the screen with its bottom row
        # of controls below the edge of the display.
        guide_scroll = QScrollArea()
        guide_scroll.setWidgetResizable(True)
        guide_scroll.setFrameShape(QFrame.NoFrame)
        guide_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        guide_scroll.setWidget(guide)
        guide_layout.addWidget(guide_scroll, 1)

        log_side = QWidget()
        log_layout = QVBoxLayout(log_side)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(heading("Activity"))
        header.addStretch(1)
        clear = QPushButton("Clear")
        clear.setProperty("tone", "quiet")
        clear.clicked.connect(lambda: self.log_view.clear())
        header.addWidget(clear)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(600)

        log_layout.addLayout(header)
        log_layout.addWidget(self.log_view, 1)

        layout.addWidget(guide_panel, 1)
        layout.addWidget(log_side, 1)
        return page

    def _watch_devices(self) -> None:
        """Notice cameras appearing or disappearing without being asked.

        Someone may create one in a terminal, plug a webcam in, or remove a
        camera from the other tab; the lists should not need a button press.
        """
        self._device_fingerprint = self._device_set()
        self._device_timer = QTimer(self)
        self._device_timer.timeout.connect(self._check_devices)
        self._device_timer.start(2000)

    @staticmethod
    def _device_set() -> frozenset:
        return frozenset(glob.glob("/dev/video*"))

    def _check_devices(self) -> None:
        current = self._device_set()
        if current == self._device_fingerprint:
            return
        self._device_fingerprint = current
        self.devices.refresh()
        self.studio.refresh_sources()
        self.studio.refresh_outputs()
        self.write_log("The list of video devices changed; refreshed.")

    def _build_shortcuts(self) -> None:
        """The handful worth reaching for while the camera is running."""
        studio = self.studio
        for keys, action in (
            (QKeySequence.ZoomIn, lambda: studio.nudge_zoom(0.1)),
            ("Ctrl++", lambda: studio.nudge_zoom(0.1)),
            ("Ctrl+=", lambda: studio.nudge_zoom(0.1)),
            (QKeySequence.ZoomOut, lambda: studio.nudge_zoom(-0.1)),
            ("Ctrl+-", lambda: studio.nudge_zoom(-0.1)),
            ("Ctrl+0", studio.reset_zoom),
            ("Space", studio.save_snapshot),
            ("Ctrl+R", studio.toggle_recording),
            ("Ctrl+L", studio.toggle_live),
            ("Ctrl+K", studio.toggle_capture),
        ):
            QShortcut(QKeySequence(keys), self, activated=action)

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)

        self.status_tally = TallyLight()
        self.status_text = QLabel("Ready")
        self.status_env = QLabel(loopback.environment_report())
        self.status_env.setProperty("role", "note")

        left = QWidget()
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(6, 0, 0, 0)
        left_layout.setSpacing(8)
        left_layout.addWidget(self.status_tally)
        left_layout.addWidget(self.status_text)

        bar.addWidget(left)
        bar.addPermanentWidget(self.status_env)

    # ------------------------------------------------------------------

    def write_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"{stamp}  {message}")
        self.status_text.setText(message.split("\n")[0][:120])

    def _on_live(self, live: bool) -> None:
        self.status_tally.set_live(live)
        if self._tray is not None:
            self._tray_share.setVisible(live)
            self._tray.setToolTip("Camaloop - sharing" if live else "Camaloop")
        if live:
            self.setWindowTitle("Camaloop - live")
        else:
            self.setWindowTitle("Camaloop")

    # ------------------------------------------------------------------
    # The window, the tray, and what closing means
    # ------------------------------------------------------------------

    def _smallest_size(self):
        """The smallest the window may ask for, never more than the screen.

        A minimum larger than the display is the one thing a window manager
        cannot work around: it has to give the window the size it insists
        on, and the part that does not fit goes off the bottom.
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            return MIN_WIDTH, MIN_HEIGHT
        room = screen.availableGeometry()
        return (min(MIN_WIDTH, max(320, room.width())),
                min(MIN_HEIGHT, max(240, room.height())))

    def fit_to_screen(self) -> None:
        """Pull the window back inside the display if it has spilled out."""
        screen = self.screen() if hasattr(self, "screen") else None
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return
        room = screen.availableGeometry()
        if self.width() > room.width() or self.height() > room.height():
            self.setGeometry(room)

    def apply_window_lock(self, locked: bool) -> None:
        """Keep the window maximised, or let it be moved and resized again."""
        self._locked = bool(locked)

        # Never freeze the window at a size. Maximising is a request to the
        # window manager, not something that has happened by the time the
        # next line runs - so the size read straight afterwards is the size
        # from *before* it was maximised. Freezing that leaves the window
        # both smaller than the screen and, where the layout wants more room
        # than the display has, taller than it.
        self.setMaximumSize(UNLIMITED, UNLIMITED)
        self.setMinimumSize(*self._smallest_size())

        if locked:
            self.showMaximized()
            QTimer.singleShot(0, self.fit_to_screen)
            self.write_log("The window will stay maximised.")
        else:
            if self.isMaximized():
                self.showNormal()
            self.write_log("The window can be moved and resized.")

    def changeEvent(self, event) -> None:
        """Put the window back if something un-maximises it while locked."""
        if (event.type() == QEvent.WindowStateChange
                and getattr(self, "_locked", False)
                and self.isVisible()
                and not self.isMaximized()
                and not self.isMinimized()):
            # On the next turn of the event loop, so the window manager is
            # not argued with in the middle of its own state change.
            QTimer.singleShot(0, self._restore_maximised)
        super().changeEvent(event)

    def _restore_maximised(self) -> None:
        if self._locked and self.isVisible() and not self.isMinimized():
            self.showMaximized()
            self.fit_to_screen()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self.fit_to_screen)

    def apply_tray(self, enabled: bool) -> None:
        available = QSystemTrayIcon.isSystemTrayAvailable()
        self.settings_tab.refresh_tray_note(available)

        if not enabled or not available:
            if self._tray is not None:
                self._tray.hide()
                self._tray = None
            return
        if self._tray is not None:
            return

        self._tray = QSystemTrayIcon(self.windowIcon(), self)
        self._tray.setToolTip("Camaloop")

        menu = QMenu()
        show = QAction("Show Camaloop", self)
        show.triggered.connect(self._restore)
        menu.addAction(show)

        self._tray_share = QAction("Stop sharing", self)
        self._tray_share.triggered.connect(self.studio.toggle_live)
        self._tray_share.setVisible(False)
        menu.addAction(self._tray_share)

        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_properly)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda reason: self._restore()
            if reason == QSystemTrayIcon.Trigger else None
        )
        self._tray.show()

    def _restore(self) -> None:
        self.showMaximized() if self.settings["lock_window"] else self.showNormal()
        self.raise_()
        self.activateWindow()

    def hide_to_tray(self) -> None:
        self.hide()
        if self._tray is not None:
            self._tray.showMessage(
                "Camaloop is still running",
                "Your camera keeps going. Click the tray icon to bring the "
                "window back.",
                QSystemTrayIcon.Information,
                4000,
            )

    def quit_properly(self) -> None:
        self._really_quit = True
        self.close()

    def _remember(self) -> None:
        if self.settings["remember_effects"]:
            self.settings["last_effects"] = self.pipeline.snapshot()

    def closeEvent(self, event) -> None:
        if self._really_quit:
            self._remember()
            self.studio.stop_capture()
            if self._tray is not None:
                self._tray.hide()
            event.accept()
            return

        action = self.settings["close_action"]
        can_hide = self._tray is not None

        if action == "tray" and can_hide:
            self._remember()
            event.ignore()
            self.hide_to_tray()
            return

        if action == "ask" and can_hide:
            box = QMessageBox(self)
            box.setWindowTitle("Close Camaloop?")
            box.setIcon(QMessageBox.Question)
            live = self.studio.is_running
            box.setText(
                "Camaloop is still sending video."
                if live else "Camaloop is still running."
            )
            box.setInformativeText(
                "Keep it running in the tray, or stop the camera and quit?"
            )
            keep = box.addButton("Keep running", QMessageBox.AcceptRole)
            leave = box.addButton("Quit", QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            remember = QCheckBox("Do this from now on, without asking")
            box.setCheckBox(remember)
            box.exec_()

            clicked = box.clickedButton()
            if clicked is keep:
                if remember.isChecked():
                    self.settings["close_action"] = "tray"
                self._remember()
                event.ignore()
                self.hide_to_tray()
                return
            if clicked is leave:
                if remember.isChecked():
                    self.settings["close_action"] = "quit"
                self._remember()
                self.studio.stop_capture()
                if self._tray is not None:
                    self._tray.hide()
                event.accept()
                return
            event.ignore()
            return

        self._remember()
        self.studio.stop_capture()
        if self._tray is not None:
            self._tray.hide()
        event.accept()
