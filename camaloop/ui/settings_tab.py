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

"""How the app behaves: closing, the tray, starting with the session."""

from __future__ import annotations

import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import CONFIG_DIR, SETTINGS_FILE, Settings
from . import theme
from .widgets import heading, panel

SECTION_SPACING = 18
ROW_SPACING = 10


class SettingsTab(QScrollArea):
    log = pyqtSignal(str)
    lock_changed = pyqtSignal(bool)
    tray_changed = pyqtSignal(bool)

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings

        inner = QWidget()
        self.column = QVBoxLayout(inner)
        self.column.setContentsMargins(22, 18, 24, 28)
        self.column.setSpacing(SECTION_SPACING)

        self.column.addWidget(heading("Settings"))
        subtitle = QLabel("These are remembered between sessions.")
        subtitle.setProperty("role", "sub")
        self.column.addWidget(subtitle)

        self._build_closing()
        self._build_startup()
        self._build_window()
        self._build_files()
        self._build_reset()
        self.column.addStretch(1)
        inner.setMaximumWidth(1000)

        self.setWidget(inner)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    # ------------------------------------------------------------------

    def _section(self, title: str, explanation: str = "") -> QVBoxLayout:
        card = panel()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(ROW_SPACING)

        label = QLabel(title)
        font = label.font()
        font.setWeight(62)
        font.setPointSize(font.pointSize() + 1)
        label.setFont(font)
        layout.addWidget(label)

        if explanation:
            note = QLabel(explanation)
            note.setProperty("role", "note")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addSpacing(4)

        self.column.addWidget(card)
        return layout

    # ------------------------------------------------------------------

    def _build_closing(self) -> None:
        layout = self._section(
            "When the window is closed",
            "Camaloop keeps sending video while it runs. Closing the window "
            "does not have to stop it.",
        )
        self.close_group = QButtonGroup(self)
        choices = (
            ("ask", "Ask me each time"),
            ("tray", "Keep running in the tray"),
            ("quit", "Stop the camera and quit"),
        )
        for value, label in choices:
            button = QRadioButton(label)
            button.setChecked(self.settings["close_action"] == value)
            button.toggled.connect(
                lambda checked, v=value: checked and self._set("close_action", v)
            )
            self.close_group.addButton(button)
            layout.addWidget(button)

        layout.addSpacing(6)
        self.tray_box = QCheckBox("Show an icon in the system tray")
        self.tray_box.setChecked(bool(self.settings["tray_icon"]))
        self.tray_box.toggled.connect(self._on_tray)
        layout.addWidget(self.tray_box)

        self.tray_note = QLabel()
        self.tray_note.setProperty("role", "note")
        self.tray_note.setWordWrap(True)
        layout.addWidget(self.tray_note)

    def _build_startup(self) -> None:
        layout = self._section(
            "Starting up",
            "An entry in your autostart folder launches Camaloop with your "
            "desktop session.",
        )
        self.autostart_box = QCheckBox("Start Camaloop when I log in")
        self.autostart_box.setChecked(Settings.autostart_active())
        self.autostart_box.toggled.connect(self._on_autostart)
        layout.addWidget(self.autostart_box)

        self.minimised_box = QCheckBox("Start hidden in the tray")
        self.minimised_box.setChecked(bool(self.settings["start_minimised"]))
        self.minimised_box.toggled.connect(
            lambda on: (self._set("start_minimised", on), self._refresh_autostart())
        )
        layout.addWidget(self.minimised_box)

        self.remember_box = QCheckBox("Reopen with the effects I last used")
        self.remember_box.setChecked(bool(self.settings["remember_effects"]))
        self.remember_box.toggled.connect(lambda on: self._set("remember_effects", on))
        layout.addWidget(self.remember_box)

    def _build_window(self) -> None:
        layout = self._section(
            "The window",
            "Camaloop is laid out for a full screen: the preview on the left "
            "and every effect on the right, without scrolling sideways.",
        )
        self.lock_box = QCheckBox("Keep the window maximised and stop it being resized")
        self.lock_box.setChecked(bool(self.settings["lock_window"]))
        self.lock_box.toggled.connect(self._on_lock)
        layout.addWidget(self.lock_box)

        note = QLabel(
            "Turn this off if you use a tiling window manager, or if you want "
            "Camaloop beside another window."
        )
        note.setProperty("role", "note")
        note.setWordWrap(True)
        layout.addWidget(note)

    def _build_files(self) -> None:
        layout = self._section(
            "Photos and recordings",
            "Where Take a photo and Start recording put their files.",
        )
        row = QHBoxLayout()
        row.setSpacing(10)
        self.folder_label = QLabel(self._folder_text())
        self.folder_label.setProperty("role", "sub")
        self.folder_label.setWordWrap(True)

        choose = QPushButton("Choose a folder")
        choose.clicked.connect(self._choose_folder)
        default = QPushButton("Use the default")
        default.setProperty("tone", "quiet")
        default.clicked.connect(lambda: self._set_folder(""))

        row.addWidget(self.folder_label, 1)
        row.addWidget(default)
        row.addWidget(choose)
        layout.addLayout(row)

    def _build_reset(self) -> None:
        layout = self._section(
            "Stored files",
            f"Settings and saved looks live in {CONFIG_DIR}.",
        )
        row = QHBoxLayout()
        row.setSpacing(10)
        reset = QPushButton("Reset every setting")
        reset.setProperty("tone", "danger")
        reset.clicked.connect(self._reset)
        row.addWidget(reset)
        row.addStretch(1)
        layout.addLayout(row)

    # ------------------------------------------------------------------

    def _set(self, key: str, value) -> None:
        self.settings[key] = value
        self.log.emit(f"Setting saved: {key} = {value}")

    def _on_tray(self, enabled: bool) -> None:
        self._set("tray_icon", enabled)
        self.tray_changed.emit(enabled)
        self.refresh_tray_note()

    def _on_lock(self, enabled: bool) -> None:
        self._set("lock_window", enabled)
        self.lock_changed.emit(enabled)

    def _on_autostart(self, enabled: bool) -> None:
        self._set("autostart", enabled)
        self.log.emit(self.settings.apply_autostart(enabled))

    def _refresh_autostart(self) -> None:
        if Settings.autostart_active():
            self.settings.apply_autostart(True)

    def _folder_text(self) -> str:
        chosen = self.settings["capture_folder"]
        if chosen:
            return chosen
        videos = os.path.join(os.path.expanduser("~"), "Videos")
        return (videos if os.path.isdir(videos) else os.path.expanduser("~")) + "  (default)"

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Where should photos and recordings go?", os.path.expanduser("~")
        )
        if folder:
            self._set_folder(folder)

    def _set_folder(self, folder: str) -> None:
        self._set("capture_folder", folder)
        self.folder_label.setText(self._folder_text())

    def _reset(self) -> None:
        self.settings.reset()
        self.settings.apply_autostart(False)
        self.log.emit("Every setting is back to how it started.")
        for box, key in (
            (self.tray_box, "tray_icon"),
            (self.minimised_box, "start_minimised"),
            (self.remember_box, "remember_effects"),
            (self.lock_box, "lock_window"),
        ):
            box.blockSignals(True)
            box.setChecked(bool(self.settings[key]))
            box.blockSignals(False)
        self.autostart_box.blockSignals(True)
        self.autostart_box.setChecked(False)
        self.autostart_box.blockSignals(False)
        for button in self.close_group.buttons():
            button.blockSignals(True)
            button.setChecked(button.text() == "Ask me each time")
            button.blockSignals(False)
        self.folder_label.setText(self._folder_text())
        self.lock_changed.emit(bool(self.settings["lock_window"]))
        self.tray_changed.emit(bool(self.settings["tray_icon"]))

    def refresh_tray_note(self, available: bool = True) -> None:
        if not self.tray_box.isChecked():
            self.tray_note.setText("")
            return
        if available:
            self.tray_note.setText("")
        else:
            self.tray_note.setText(
                "Your desktop is not showing a tray at the moment. GNOME needs "
                "an extension such as AppIndicator Support; without one, closing "
                "the window will quit instead of hiding."
            )
            self.tray_note.setStyleSheet(f"color: {theme.AMBER};")
