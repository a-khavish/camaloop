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

"""The tab where virtual cameras are made, renamed and removed."""

from __future__ import annotations

import os
from typing import Callable, List, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import loopback, v4l2
from . import theme
from .widgets import heading, panel


class Task(QThread):
    """Runs one privileged call off the interface thread so nothing freezes."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str, str)

    def __init__(self, work: Callable, parent=None):
        super().__init__(parent)
        self._work = work

    def run(self) -> None:
        try:
            self.done.emit(self._work())
        except loopback.LoopbackError as exc:
            self.failed.emit(str(exc), exc.command)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc), "")


class CreateDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New virtual camera")
        self.setMinimumWidth(430)

        self.name = QLineEdit("Studio Camera")
        self.name.setPlaceholderText("The name other apps will show")

        self.auto_number = QCheckBox("Choose the device number for me")
        self.auto_number.setChecked(True)
        self.number = QSpinBox()
        self.number.setRange(0, 63)
        self.number.setValue(loopback.next_free_index())
        self.number.setPrefix("/dev/video")
        self.number.setEnabled(False)
        self.auto_number.toggled.connect(lambda on: self.number.setEnabled(not on))

        self.exclusive = QCheckBox(
            "Announce as a capture-only device (needed by Chrome, Zoom and Teams)"
        )
        self.exclusive.setChecked(True)

        self.buffers = QSpinBox()
        self.buffers.setRange(2, 16)
        self.buffers.setValue(4)

        self.max_width = QSpinBox()
        self.max_width.setRange(320, 3840)
        self.max_width.setValue(1920)
        self.max_height = QSpinBox()
        self.max_height.setRange(240, 2160)
        self.max_height.setValue(1080)

        size_row = QHBoxLayout()
        size_row.addWidget(self.max_width)
        size_row.addWidget(QLabel("x"))
        size_row.addWidget(self.max_height)

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Name", self.name)
        form.addRow("", self.auto_number)
        form.addRow("Device", self.number)
        form.addRow("Compatibility", self.exclusive)
        form.addRow("Buffers", self.buffers)
        form.addRow("Largest size", size_row)

        note = QLabel(
            "Creating a camera changes a kernel module, so you will be asked for "
            "your password."
        )
        note.setProperty("role", "note")
        note.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Create camera")
        buttons.button(QDialogButtonBox.Ok).setProperty("tone", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        layout.addWidget(heading("New virtual camera"))
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {
            "label": self.name.text().strip(),
            "index": None if self.auto_number.isChecked() else self.number.value(),
            "exclusive_caps": self.exclusive.isChecked(),
            "buffers": self.buffers.value(),
            "max_width": self.max_width.value(),
            "max_height": self.max_height.value(),
        }


class EditDialog(QDialog):
    def __init__(self, camera: loopback.VirtualCamera, parent=None):
        super().__init__(parent)
        self.camera = camera
        self.setWindowTitle(f"Edit {camera.label}")
        self.setMinimumWidth(430)

        self.name = QLineEdit(camera.label)
        self.fps = QSpinBox()
        self.fps.setRange(1, 120)
        self.fps.setValue(30)
        self.fps.setSuffix(" fps")

        self.format = QComboBox()
        self.format.addItems(
            [
                "Leave as it is",
                "1920 x 1080",
                "1280 x 720",
                "960 x 540",
                "640 x 480",
            ]
        )

        self.placeholder = QLineEdit()
        self.placeholder.setReadOnly(True)
        self.placeholder.setPlaceholderText("Shown when nothing is streaming")
        browse = QPushButton("Choose")
        browse.clicked.connect(self._browse)
        placeholder_row = QHBoxLayout()
        placeholder_row.addWidget(self.placeholder, 1)
        placeholder_row.addWidget(browse)

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Name", self.name)
        form.addRow("Frame rate", self.fps)
        form.addRow("Fixed size", self.format)
        form.addRow("Standby image", placeholder_row)

        note = QLabel(
            "Renaming recreates the device, so close any app using it first. "
            "Frame rate, size and the standby image need v4l2loopback-ctl."
        )
        note.setProperty("role", "note")
        note.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Apply changes")
        buttons.button(QDialogButtonBox.Ok).setProperty("tone", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        layout.addWidget(heading(camera.path))
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Standby image", os.path.expanduser("~"),
            "Images (*.png *.jpg *.jpeg);;All files (*)",
        )
        if path:
            self.placeholder.setText(path)

    def plan(self) -> List[Callable]:
        """Turn the dialog into a list of things to do, in order."""
        steps: List[Callable] = []
        camera = self.camera

        new_name = self.name.text().strip()
        if new_name and new_name != camera.label:
            steps.append(lambda: loopback.rename_camera(camera, new_name))

        fps = self.fps.value()
        steps.append(lambda: loopback.set_fps(_current(camera), fps))

        size = self.format.currentText()
        if size != "Leave as it is":
            width, height = (int(part.strip()) for part in size.split("x"))
            caps = f"video/x-raw,format=RGB,width={width},height={height}"
            steps.append(lambda: loopback.set_caps(_current(camera), caps))

        image = self.placeholder.text().strip()
        if image:
            steps.append(lambda: loopback.set_placeholder_image(_current(camera), image))
        return steps


def _current(camera: loopback.VirtualCamera) -> loopback.VirtualCamera:
    """Re-read the camera; a rename gives it a fresh identity."""
    for cam in loopback.list_cameras():
        if cam.index == camera.index:
            return cam
    return camera


class DevicesTab(QWidget):
    log = pyqtSignal(str)
    devices_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._task: Optional[Task] = None
        self._cameras: List[loopback.VirtualCamera] = []
        self._build()
        self.refresh()

    # ------------------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(18)

        intro = QVBoxLayout()
        intro.setSpacing(2)
        intro.addWidget(heading("Virtual cameras"))
        subtitle = QLabel(
            "A virtual camera appears in the camera list of every other app on this "
            "machine. Send your edited picture to one and that is what they will see."
        )
        subtitle.setProperty("role", "sub")
        subtitle.setWordWrap(True)
        intro.addWidget(subtitle)
        root.addLayout(intro)

        root.addWidget(self._status_bar())
        root.addWidget(self._table(), 1)
        root.addWidget(self._all_devices_table())

    def _status_bar(self) -> QFrame:
        bar = panel(role="bar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        self.status_label = QLabel()
        self.status_label.setProperty("role", "sub")
        self.status_label.setWordWrap(True)

        self.load_button = QPushButton("Load the driver")
        self.load_button.clicked.connect(self._load_module)

        self.new_button = QPushButton("New camera")
        self.new_button.setProperty("tone", "primary")
        self.new_button.clicked.connect(self._create)

        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self._edit)

        self.delete_button = QPushButton("Remove")
        self.delete_button.setProperty("tone", "danger")
        self.delete_button.clicked.connect(self._delete)

        self.persist_button = QPushButton("Keep after reboot")
        self.persist_button.setToolTip(
            "Write the current list to /etc/modprobe.d so these cameras come back "
            "when the machine restarts"
        )
        self.persist_button.clicked.connect(self._persist)

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)

        layout.addWidget(self.status_label, 1)
        layout.addWidget(refresh)
        layout.addWidget(self.load_button)
        layout.addWidget(self.persist_button)
        layout.addWidget(self.delete_button)
        layout.addWidget(self.edit_button)
        layout.addWidget(self.new_button)
        return bar

    def _table(self) -> QWidget:
        """The camera list, with an empty state stacked behind it."""
        self.table_stack = QStackedWidget()

        empty = QFrame()
        empty.setProperty("role", "panel")
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(40, 40, 40, 40)
        empty_layout.setSpacing(10)
        empty_layout.addStretch(1)

        self.empty_title = QLabel("No virtual cameras yet")
        self.empty_title.setProperty("role", "heading")
        self.empty_title.setAlignment(Qt.AlignCenter)

        self.empty_body = QLabel(
            "Make one and it will show up in the camera list of every other app on "
            "this machine, under whatever name you give it."
        )
        self.empty_body.setProperty("role", "sub")
        self.empty_body.setAlignment(Qt.AlignCenter)
        self.empty_body.setWordWrap(True)
        self.empty_body.setMaximumWidth(460)

        self.empty_action = QPushButton("New camera")
        self.empty_action.setProperty("tone", "primary")
        self.empty_action.setMinimumWidth(160)
        self.empty_action.clicked.connect(self._create)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.empty_action)
        button_row.addStretch(1)

        body_row = QHBoxLayout()
        body_row.addStretch(1)
        body_row.addWidget(self.empty_body)
        body_row.addStretch(1)

        empty_layout.addWidget(self.empty_title)
        empty_layout.addLayout(body_row)
        empty_layout.addSpacing(6)
        empty_layout.addLayout(button_row)
        empty_layout.addStretch(1)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name", "Device", "Status", "Number"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(self._edit)

        self.table_stack.addWidget(empty)
        self.table_stack.addWidget(self.table)
        return self.table_stack

    def _all_devices_table(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel("Every video device on this machine")
        label.setProperty("role", "sub")

        self.all_table = QTableWidget(0, 4)
        self.all_table.setHorizontalHeaderLabels(["Device", "Name", "Driver", "Type"])
        self.all_table.verticalHeader().setVisible(False)
        self.all_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.all_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.all_table.setShowGrid(False)
        self.all_table.setFixedHeight(168)
        header = self.all_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)

        layout.addWidget(label)
        layout.addWidget(self.all_table)
        return holder

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        self._cameras = loopback.list_cameras()
        self.table.setRowCount(len(self._cameras))
        for row, cam in enumerate(self._cameras):
            name = QTableWidgetItem(cam.label)
            device = QTableWidgetItem(cam.path)
            device.setFont(theme.mono_font(9))
            status = QTableWidgetItem("In use by another app" if cam.in_use else "Idle")
            status.setForeground(
                QBrush(QColor(theme.AMBER if cam.in_use else theme.MUTED))
            )
            number = QTableWidgetItem(str(cam.index))
            number.setTextAlignment(Qt.AlignCenter)
            for column, item in enumerate((name, device, status, number)):
                self.table.setItem(row, column, item)

        devices = v4l2.list_devices()
        self.all_table.setRowCount(len(devices))
        for row, dev in enumerate(devices):
            path = QTableWidgetItem(dev.path)
            path.setFont(theme.mono_font(9))
            items = (
                path,
                QTableWidgetItem(dev.card or "-"),
                QTableWidgetItem(dev.driver or "-"),
                QTableWidgetItem(dev.kind),
            )
            for column, item in enumerate(items):
                self.all_table.setItem(row, column, item)

        self.table_stack.setCurrentIndex(1 if self._cameras else 0)
        if not loopback.module_installed():
            self.empty_title.setText("The driver is not installed yet")
            self.empty_body.setText(
                "Virtual cameras are provided by the v4l2loopback kernel module. "
                "Install it from your package manager, then come back here.\n\n"
                "Debian and Ubuntu:  sudo apt install v4l2loopback-dkms\n"
                "Fedora:  sudo dnf install akmod-v4l2loopback\n"
                "Arch:  sudo pacman -S v4l2loopback-dkms"
            )
            self.empty_action.setVisible(False)
        else:
            self.empty_title.setText("No virtual cameras yet")
            self.empty_body.setText(
                "Make one and it will show up in the camera list of every other app "
                "on this machine, under whatever name you give it."
            )
            self.empty_action.setVisible(True)

        loaded = loopback.module_loaded()
        if loaded:
            version = loopback.module_version()
            named = f"v4l2loopback {version}" if version else "v4l2loopback"
            if loopback.supports_dynamic_devices():
                method = "cameras can be added and removed one at a time"
            else:
                method = (
                    "every change reloads the driver, so all cameras must be idle "
                    "first. Version 0.13 or newer removes that restriction."
                )
            self.status_label.setText(
                f"{len(self._cameras)} virtual camera(s) | {named} | {method}"
            )
        elif loopback.module_installed():
            self.status_label.setText(
                "The v4l2loopback driver is installed but not loaded. Load it, or "
                "just make a camera and it will load itself."
            )
        else:
            self.status_label.setText("The v4l2loopback driver is not installed.")
        self.load_button.setVisible(not loaded and loopback.module_installed())
        self.new_button.setEnabled(loopback.module_installed())
        self.persist_button.setEnabled(bool(self._cameras))
        self._update_buttons()
        self.devices_changed.emit()

    def _selected(self) -> Optional[loopback.VirtualCamera]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        index = rows[0].row()
        if 0 <= index < len(self._cameras):
            return self._cameras[index]
        return None

    def _update_buttons(self) -> None:
        has = self._selected() is not None
        self.edit_button.setEnabled(has)
        self.delete_button.setEnabled(has)

    # ------------------------------------------------------------------

    def _busy(self, busy: bool) -> None:
        for button in (
            self.new_button, self.edit_button, self.delete_button,
            self.load_button, self.persist_button,
        ):
            button.setEnabled(not busy)
        if not busy:
            self._update_buttons()

    def _run(self, work: Callable, success: str) -> None:
        if self._task is not None and self._task.isRunning():
            return
        self._busy(True)
        self.log.emit("Waiting for permission...")
        self._task = Task(work, self)
        self._task.done.connect(lambda _r: self._on_done(success))
        self._task.failed.connect(self._on_failed)
        self._task.start()

    def _on_done(self, message: str) -> None:
        self._busy(False)
        self.log.emit(message)
        self.refresh()

    def _on_failed(self, message: str, command: str) -> None:
        self._busy(False)
        self.log.emit("Problem: " + message.replace("\n", " "))
        box = QMessageBox(self)
        box.setWindowTitle("That did not work")
        box.setIcon(QMessageBox.Warning)
        box.setText(message.split("\n")[0])
        details = message
        if command:
            details += f"\n\nCommand:\n{command}"
        box.setDetailedText(details)
        box.exec_()
        self.refresh()

    # ------------------------------------------------------------------

    def _load_module(self) -> None:
        self._run(lambda: loopback.load_module(1), "Driver loaded.")

    def _create(self) -> None:
        dialog = CreateDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["label"]:
            QMessageBox.information(self, "Name it", "Give the camera a name first.")
            return
        self._run(
            lambda: loopback.create_camera(**values),
            f"Created \"{values['label']}\". Other apps will see it after they rescan.",
        )

    def _edit(self) -> None:
        camera = self._selected()
        if camera is None:
            return
        dialog = EditDialog(camera, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        steps = dialog.plan()
        if not steps:
            return

        def work():
            messages = []
            for step in steps:
                try:
                    messages.append(str(step() or ""))
                except loopback.LoopbackError as exc:
                    if "not installed" in str(exc):
                        messages.append(str(exc))
                        continue
                    raise
            return messages

        self._run(work, f"Updated {camera.label}.")

    def _delete(self) -> None:
        camera = self._selected()
        if camera is None:
            return
        confirm = QMessageBox.question(
            self,
            "Remove this camera?",
            f"\"{camera.label}\" ({camera.path}) will disappear from every app on this "
            "machine.",
            QMessageBox.Cancel | QMessageBox.Yes,
            QMessageBox.Cancel,
        )
        if confirm != QMessageBox.Yes:
            return
        self._run(
            lambda: loopback.delete_camera(camera), f"Removed \"{camera.label}\"."
        )

    def _persist(self) -> None:
        cameras = list(self._cameras)
        snippet = loopback.persistence_snippet(cameras)
        confirm = QMessageBox.question(
            self,
            "Keep these cameras after a reboot?",
            "This writes the list below to /etc/modprobe.d/camaloop.conf:\n\n"
            + snippet,
            QMessageBox.Cancel | QMessageBox.Yes,
            QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return
        self._run(lambda: loopback.make_persistent(cameras), "Saved for next boot.")
