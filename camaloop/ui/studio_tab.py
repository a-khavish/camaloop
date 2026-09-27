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

"""The tab where the camera is watched and adjusted."""

from __future__ import annotations

import os
import time
from typing import List, Optional

import cv2
import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core import loopback, presets, v4l2
from ..core.effects import Pipeline
from ..core.engine import Engine, SourceSettings
from . import theme
from .effects_panel import EffectsPanel
from .widgets import (
    FlowLayout, PreviewWidget, TallyLight, heading, panel, spacer, wraps,
)

RESOLUTIONS = [
    ("640 x 360", 640, 360),
    ("640 x 480", 640, 480),
    ("960 x 540", 960, 540),
    ("1280 x 720", 1280, 720),
    ("1920 x 1080", 1920, 1080),
]
FRAME_RATES = [15, 24, 30, 60]


class StudioTab(QWidget):
    log = pyqtSignal(str)
    live_changed = pyqtSignal(bool)
    recording_changed = pyqtSignal(bool)

    def __init__(self, pipeline: Pipeline, parent=None):
        super().__init__(parent)
        self.pipeline = pipeline
        self.engine: Optional[Engine] = None
        self._last_frame: Optional[np.ndarray] = None
        self._cameras: List[v4l2.VideoDevice] = []
        self._last_network_url = "rtsp://"
        self._last_screen_area = ""
        self._outputs: List[loopback.VirtualCamera] = []

        self._build()
        self.refresh_sources()
        self.refresh_outputs()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # An explicit minimum of its own. Without one, Qt takes this page's
        # minimum from the layout measured at its very narrowest - where the
        # wrapping bars are stacked several lines deep - and hands that
        # height to the window, which then will not fit under a desktop
        # panel. Below this size the bars wrap and the preview shrinks.
        self.setMinimumSize(720, 470)

        root = QHBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(20)

        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(self._source_bar())
        left.addWidget(self._preview_area(), 1)
        left.addWidget(self._capture_bar())
        left.addWidget(self._output_bar())

        right = QVBoxLayout()
        right.setSpacing(14)
        right.addWidget(self._effects_header())
        self.effects = EffectsPanel(self.pipeline)
        right.addWidget(self.effects, 1)
        right.addWidget(self._preset_bar())

        right_holder = QWidget()
        right_holder.setLayout(right)
        # Room enough for the widest control at its natural size, but able
        # to give ground on a small screen rather than pushing the preview
        # off the edge.
        right_holder.setMinimumWidth(340)
        right_holder.setMaximumWidth(448)
        right_holder.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        root.addLayout(left, 3)
        root.addWidget(right_holder, 1)

    def _source_bar(self) -> QFrame:
        # A wrapping layout, so a narrow window puts these on two lines
        # rather than forcing the whole window wider than the screen.
        bar = wraps(panel(role="bar"))
        layout = FlowLayout(bar, spacing=14)
        layout.setContentsMargins(18, 14, 18, 14)

        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(260)
        self.source_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.resolution_combo = QComboBox()
        for label, _w, _h in RESOLUTIONS:
            self.resolution_combo.addItem(label)
        self.resolution_combo.setCurrentIndex(3)

        self.fps_combo = QComboBox()
        for rate in FRAME_RATES:
            self.fps_combo.addItem(f"{rate} fps")
        self.fps_combo.setCurrentIndex(2)

        rescan = QPushButton("Rescan")
        rescan.setToolTip("Look for cameras that have been plugged in since the app started")
        rescan.clicked.connect(self.refresh_sources)

        self.start_button = QPushButton("Start camera")
        self.start_button.setProperty("tone", "primary")
        self.start_button.setMinimumWidth(130)
        self.start_button.clicked.connect(self.toggle_capture)

        layout.addWidget(QLabel("Source"))
        layout.addWidget(self.source_combo)
        layout.addWidget(self.resolution_combo)
        layout.addWidget(self.fps_combo)
        layout.addWidget(rescan)
        layout.addWidget(self.start_button)
        return bar

    def _preview_area(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.preview = PreviewWidget("Pick a source and press Start camera")

        self.before = PreviewWidget("Straight from the camera")
        self.before.setFixedHeight(126)
        self.before.setVisible(False)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.before, 1)
        row.addStretch(2)

        layout.addWidget(self.preview, 1)
        layout.addLayout(row)
        return holder

    def _output_bar(self) -> QFrame:
        bar = wraps(panel(role="bar"))
        layout = FlowLayout(bar, spacing=14)
        layout.setContentsMargins(18, 14, 18, 14)

        self.tally = TallyLight()
        self.output_combo = QComboBox()
        self.output_combo.setMinimumWidth(240)
        self.output_state = QLabel("Not shared")
        self.output_state.setProperty("role", "sub")

        self.live_button = QPushButton("Share with other apps")
        self.live_button.setProperty("tone", "live")
        self.live_button.setMinimumWidth(180)
        self.live_button.clicked.connect(self.toggle_live)

        layout.addWidget(self.tally)
        layout.addWidget(QLabel("Send to"))
        layout.addWidget(self.output_combo)
        layout.addWidget(self.output_state)
        layout.addWidget(spacer())
        layout.addWidget(self.live_button)
        return bar

    def _capture_bar(self) -> QFrame:
        """Zoom, photo and recording - the things used while the camera runs."""
        bar = wraps(panel(role="bar"))
        layout = FlowLayout(bar, spacing=12)
        layout.setContentsMargins(18, 12, 18, 12)

        zoom_label = QLabel("Zoom")
        zoom_label.setProperty("role", "sub")

        self.zoom_out_button = QPushButton("-")
        self.zoom_out_button.setProperty("compact", "1")
        self.zoom_out_button.setFixedWidth(40)
        self.zoom_out_button.setToolTip("Zoom out  (Ctrl and -)")
        self.zoom_out_button.clicked.connect(lambda: self.nudge_zoom(-0.1))

        self.zoom_readout = QLabel("1.00x")
        self.zoom_readout.setFont(theme.mono_font(9))
        self.zoom_readout.setProperty("role", "value")
        self.zoom_readout.setMinimumWidth(52)
        self.zoom_readout.setAlignment(Qt.AlignCenter)

        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setProperty("compact", "1")
        self.zoom_in_button.setFixedWidth(40)
        self.zoom_in_button.setToolTip("Zoom in  (Ctrl and +)")
        self.zoom_in_button.clicked.connect(lambda: self.nudge_zoom(0.1))

        zoom_reset = QPushButton("Reset")
        zoom_reset.setProperty("tone", "quiet")
        zoom_reset.setToolTip("Back to 1x (Ctrl and 0)")
        zoom_reset.clicked.connect(self.reset_zoom)

        self.photo_button = QPushButton("Take a photo")
        self.photo_button.setToolTip("Save the current frame as a picture  (Space)")
        self.photo_button.clicked.connect(self.save_snapshot)

        self.record_button = QPushButton("Start recording")
        self.record_button.setProperty("tone", "live")
        self.record_button.setMinimumWidth(158)
        self.record_button.setToolTip("Record what you see to a video file  (Ctrl and R)")
        self.record_button.clicked.connect(self.toggle_recording)

        self.record_time = QLabel("")
        self.record_time.setFont(theme.mono_font(9))
        self.record_time.setMinimumWidth(96)
        # Hidden until there is a time to show. An empty label still takes
        # its width, and on a narrow window that was enough to push the
        # record button onto a line of its own.
        self.record_time.setVisible(False)

        self.compare_button = QPushButton("Compare")
        self.compare_button.setCheckable(True)
        self.compare_button.setToolTip("Show the untouched camera picture alongside")
        self.compare_button.toggled.connect(self._toggle_compare)

        self.freeze_button = QPushButton("Freeze")
        self.freeze_button.setCheckable(True)
        self.freeze_button.setToolTip("Hold the last frame - handy before you step away")
        self.freeze_button.toggled.connect(self._toggle_freeze)

        layout.addWidget(zoom_label)
        layout.addWidget(self.zoom_out_button)
        layout.addWidget(self.zoom_readout)
        layout.addWidget(self.zoom_in_button)
        layout.addWidget(zoom_reset)
        layout.addWidget(spacer())
        layout.addWidget(self.compare_button)
        layout.addWidget(self.freeze_button)
        layout.addWidget(self.photo_button)
        layout.addWidget(self.record_time)
        layout.addWidget(self.record_button)
        return bar

    def _effects_header(self) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.addWidget(heading("Effects"))
        layout.addStretch(1)
        collapse = QPushButton("Collapse all")
        collapse.setProperty("tone", "quiet")
        collapse.clicked.connect(lambda: self.effects.collapse_all())
        layout.addWidget(collapse)
        return holder

    def _preset_bar(self) -> QFrame:
        bar = panel(role="bar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        self.preset_combo = QComboBox()
        self.preset_combo.setEditable(False)
        self.preset_combo.activated.connect(self._load_preset)

        row = QHBoxLayout()
        row.setSpacing(6)
        save = QPushButton("Save")
        save.clicked.connect(self._save_preset)
        delete = QPushButton("Delete")
        delete.setProperty("tone", "danger")
        delete.clicked.connect(self._delete_preset)
        reset = QPushButton("Reset all")
        reset.clicked.connect(self._reset)
        row.addWidget(save)
        row.addWidget(delete)
        row.addStretch(1)
        row.addWidget(reset)

        label = QLabel("Saved looks")
        label.setProperty("role", "sub")
        layout.addWidget(label)
        layout.addWidget(self.preset_combo)
        layout.addLayout(row)
        self.refresh_presets()
        return bar

    # ------------------------------------------------------------------
    # Device lists
    # ------------------------------------------------------------------

    def refresh_sources(self) -> None:
        previous = self.source_combo.currentData()
        self.source_combo.clear()
        self._cameras = [
            d for d in v4l2.list_capture_devices() if not d.is_loopback
        ]
        for device in self._cameras:
            self.source_combo.addItem(device.label, ("camera", device.path))
        for device in v4l2.list_loopback_devices():
            self.source_combo.addItem(
                f"{device.card}  ({device.path}) - virtual", ("camera", device.path)
            )
        self.source_combo.addItem("Network camera...", ("network", ""))
        self.source_combo.addItem("Screen or a part of it...", ("screen", ""))
        self.source_combo.addItem("Test pattern", ("pattern", ""))
        self.source_combo.addItem("Video file...", ("file", ""))
        self.source_combo.addItem("Still image...", ("image", ""))

        if previous:
            for i in range(self.source_combo.count()):
                if self.source_combo.itemData(i) == previous:
                    self.source_combo.setCurrentIndex(i)
                    break
        if not self._cameras:
            self.log.emit(
                "No physical cameras found. You can still use the test pattern, a "
                "video file or a still image as the source."
            )

    def refresh_outputs(self) -> None:
        previous = self.output_combo.currentData()
        self.output_combo.clear()
        self._outputs = loopback.list_cameras()
        for cam in self._outputs:
            suffix = " - in use" if cam.in_use else ""
            self.output_combo.addItem(f"{cam.label}  ({cam.path}){suffix}", cam.path)
        if not self._outputs:
            self.output_combo.addItem("No virtual cameras yet", "")
        self.live_button.setEnabled(bool(self._outputs))
        if previous:
            index = self.output_combo.findData(previous)
            if index >= 0:
                self.output_combo.setCurrentIndex(index)

    def refresh_presets(self) -> None:
        current = self.preset_combo.currentText()
        self.preset_combo.clear()
        names = presets.list_presets()
        self.preset_combo.addItem("-")
        self.preset_combo.addItems(names)
        index = self.preset_combo.findText(current)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self.engine is not None and self.engine.isRunning()

    def toggle_capture(self) -> None:
        if self.is_running:
            self.stop_capture()
        else:
            self.start_capture()

    def _chosen_source(self) -> Optional[SourceSettings]:
        data = self.source_combo.currentData()
        if not data:
            return None
        kind, device = data

        if kind == "file" and not device:
            device, _ = QFileDialog.getOpenFileName(
                self, "Pick a video file", os.path.expanduser("~"),
                "Video (*.mp4 *.mkv *.avi *.mov *.webm);;All files (*)",
            )
            if not device:
                return None
        if kind == "network" and not device:
            device, ok = QInputDialog.getText(
                self, "Network camera",
                "Address of the stream:\n\n"
                "    rtsp://user:password@192.168.1.50:554/stream1\n"
                "    http://192.168.1.51:8080/video\n",
                text=self._last_network_url,
            )
            if not ok or not device.strip():
                return None
            device = device.strip()
            self._last_network_url = device
            # Remember it, so it can be picked again without retyping.
            index = self.source_combo.currentIndex()
            self.source_combo.setItemText(index, f"Network camera  ({device[:38]})")
            self.source_combo.setItemData(index, ("network", device))

        if kind == "screen" and not device:
            from ..core.screen import ScreenGrabber, backends, describe_support

            if "portal" in backends():
                # On Wayland the desktop runs the picker, so asking for a
                # rectangle here would be meaningless.
                box = QMessageBox(self)
                box.setWindowTitle("Screen as a camera")
                box.setIcon(QMessageBox.Question)
                box.setText("What should the camera show?")
                box.setInformativeText(
                    describe_support()
                    + "\n\nYour desktop will ask you to choose, and you can "
                      "stop sharing from there at any time."
                )
                whole = box.addButton("Whole screen", QMessageBox.AcceptRole)
                window = box.addButton("A single window", QMessageBox.AcceptRole)
                box.addButton("Cancel", QMessageBox.RejectRole)
                box.exec_()
                clicked = box.clickedButton()
                if clicked is whole:
                    device = "monitor"
                elif clicked is window:
                    device = "window"
                else:
                    return None
                label = "Whole screen" if device == "monitor" else "A window"
            else:
                width, height = ScreenGrabber.screen_size()
                device, ok = QInputDialog.getText(
                    self, "Screen as a camera",
                    "Which part of the screen?\n\n"
                    f"    the whole screen is {width}x{height}\n"
                    "    a rectangle looks like  1280x720+100+50\n\n"
                    + describe_support() + "\n",
                    text=self._last_screen_area or f"{width}x{height}+0+0",
                )
                if not ok or not device.strip():
                    return None
                device = device.strip()
                self._last_screen_area = device
                label = device

            index = self.source_combo.currentIndex()
            self.source_combo.setItemText(index, f"Screen  ({label})")
            self.source_combo.setItemData(index, ("screen", device))

        if kind == "image" and not device:
            device, _ = QFileDialog.getOpenFileName(
                self, "Pick an image", os.path.expanduser("~"),
                "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)",
            )
            if not device:
                return None

        _label, width, height = RESOLUTIONS[self.resolution_combo.currentIndex()]
        return SourceSettings(
            kind=kind,
            device=device,
            width=width,
            height=height,
            fps=FRAME_RATES[self.fps_combo.currentIndex()],
        )

    def start_capture(self) -> None:
        source = self._chosen_source()
        if source is None:
            return
        target = self.output_combo.currentData()
        if source.kind == "camera" and target and source.device == target:
            QMessageBox.warning(
                self, "Pick a different source",
                "That is the same device you are sending to, which would loop the "
                "picture back into itself. Pick your real camera as the source.",
            )
            return

        self.engine = Engine(self.pipeline)
        self.engine.configure(source)
        self.engine.set_preview_raw(self.compare_button.isChecked())
        self.engine.frame_ready.connect(self._on_frame)
        self.engine.raw_ready.connect(self.before.set_frame)
        self.engine.stats_ready.connect(self._on_stats)
        self.engine.failed.connect(self._on_failure)
        self.engine.notice.connect(self.log.emit)
        self.engine.output_changed.connect(self._on_output_changed)
        self.engine.recording_changed.connect(self._on_recording)
        self.engine.recording_tick.connect(self._on_recording_tick)
        self.engine.snapshot_saved.connect(self._on_snapshot)
        self.engine.finished.connect(self._on_finished)
        self.engine.start()

        self.start_button.setText("Stop camera")
        self.start_button.setProperty("tone", "")
        self._restyle(self.start_button)
        self.log.emit(f"Started {source.device or source.kind} at {source.width}x{source.height}.")

    def stop_capture(self) -> None:
        if self.engine is not None:
            if self.engine.is_recording():
                self.engine.stop_recording()
                self.engine.msleep(120)   # let the file close before the thread ends
            self.engine.stop()
            self.engine.wait(3000)

    def _on_finished(self) -> None:
        self.engine = None
        self.preview.clear_frame()
        self.before.clear_frame()
        self.tally.set_live(False)
        self.live_changed.emit(False)
        self.output_state.setText("Not shared")
        self.live_button.setText("Share with other apps")
        self.start_button.setText("Start camera")
        self.start_button.setProperty("tone", "primary")
        self._restyle(self.start_button)
        self.record_button.setText("Start recording")
        self.record_button.setProperty("tone", "live")
        self._restyle(self.record_button)
        self.record_time.setText("")
        self.record_time.setVisible(False)
        self.preview.set_recording(False)
        self.recording_changed.emit(False)
        self.log.emit("Camera stopped.")

    def _on_frame(self, rgb: np.ndarray) -> None:
        self._last_frame = rgb
        self.preview.set_frame(rgb)

    def _on_stats(self, stats: dict) -> None:
        if stats.get("width"):
            self.preview.set_badge(
                f"{stats['width']}x{stats['height']}  {stats['fps']:.0f} fps"
            )
        self.stats = stats

    def _on_failure(self, message: str) -> None:
        self.log.emit("Problem: " + message)
        QMessageBox.warning(self, "Camera problem", message)

    def _on_output_changed(self, live: bool, path: str) -> None:
        self.tally.set_live(live)
        self.live_changed.emit(live)
        self.output_state.setText("Live to other apps" if live else "Not shared")
        self.live_button.setText("Stop sharing" if live else "Share with other apps")

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def toggle_live(self) -> None:
        if not self.is_running:
            QMessageBox.information(
                self, "Start the camera first",
                "There is nothing to send yet. Press Start camera, then share it.",
            )
            return
        path = self.output_combo.currentData()
        if not path:
            QMessageBox.information(
                self, "No virtual camera",
                "Make one on the Virtual cameras tab first. Other apps will see it "
                "under the name you give it.",
            )
            return
        currently_live = self.live_button.text() == "Stop sharing"
        self.engine.set_output(path, not currently_live)
        if currently_live:
            self.log.emit(f"Stopped sharing to {path}.")

    def _toggle_compare(self, on: bool) -> None:
        self.before.setVisible(on)
        if self.engine is not None:
            self.engine.set_preview_raw(on)

    def _toggle_freeze(self, on: bool) -> None:
        if self.engine is not None:
            self.engine.set_paused(on)
        self.freeze_button.setText("Frozen" if on else "Freeze")

    # ------------------------------------------------------------------
    # Zoom, photos and recording
    # ------------------------------------------------------------------

    def nudge_zoom(self, delta: float) -> None:
        """Live zoom, straight from the toolbar. Turns the effect on as needed."""
        card = next(
            (c for c in self.effects.cards if c.effect.id == "zoom"), None
        )
        current = self.pipeline.get_params("zoom")["zoom"]
        value = round(min(4.0, max(1.0, current + delta)), 2)

        self.pipeline.set_param("zoom", "zoom", value)
        if value > 1.001 and not self.pipeline.is_enabled("zoom"):
            self.pipeline.set_enabled("zoom", True)
        elif value <= 1.001 and self.pipeline.is_enabled("zoom"):
            self.pipeline.set_enabled("zoom", False)

        if card is not None:
            card.sync()
        self._show_zoom(value)

    def reset_zoom(self) -> None:
        self.pipeline.set_param("zoom", "zoom", 1.0)
        self.pipeline.set_param("zoom", "pan_x", 0.0)
        self.pipeline.set_param("zoom", "pan_y", 0.0)
        self.pipeline.set_enabled("zoom", False)
        card = next((c for c in self.effects.cards if c.effect.id == "zoom"), None)
        if card is not None:
            card.sync()
        self._show_zoom(1.0)

    def _show_zoom(self, value: float) -> None:
        self.zoom_readout.setText(f"{value:.2f}x")
        self.zoom_in_button.setEnabled(value < 4.0)
        self.zoom_out_button.setEnabled(value > 1.0)

    # The main window replaces this with one that reads the setting.
    capture_folder = staticmethod(lambda: "")

    def _default_folder(self) -> str:
        chosen = self.capture_folder()
        if chosen and os.path.isdir(chosen):
            return chosen
        videos = os.path.join(os.path.expanduser("~"), "Videos")
        return videos if os.path.isdir(videos) else os.path.expanduser("~")

    def save_snapshot(self) -> None:
        if not self.is_running:
            QMessageBox.information(
                self, "Nothing to save", "Start the camera and try again."
            )
            return
        path = os.path.join(
            self._default_folder(), f"camaloop-{time.strftime('%Y%m%d-%H%M%S')}.png"
        )
        # Ask the capture thread for the very next frame, so the picture is
        # exactly what is being sent, not a copy the preview happened to keep.
        self.engine.take_snapshot(path)
        self.preview.flash()

    def _on_snapshot(self, path: str) -> None:
        self.log.emit(f"Photo saved to {path}")

    def toggle_recording(self) -> None:
        if not self.is_running:
            QMessageBox.information(
                self, "Start the camera first",
                "There is nothing to record yet. Press Start camera, then record.",
            )
            return
        if self.engine.is_recording():
            self.engine.stop_recording()
            return
        path = os.path.join(
            self._default_folder(), f"camaloop-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        )
        self.engine.start_recording(path)

    def _on_recording(self, active: bool, path: str) -> None:
        self.record_button.setText("Stop recording" if active else "Start recording")
        self.record_button.setProperty("tone", "" if active else "live")
        self._restyle(self.record_button)
        self.preview.set_recording(active)
        if not active:
            self.record_time.setText("")
        self.record_time.setVisible(False)
        self.recording_changed.emit(active)

    def _on_recording_tick(self, seconds: float, frames: int) -> None:
        minutes, secs = divmod(int(seconds), 60)
        self.record_time.setText(f"REC {minutes:02d}:{secs:02d}   {frames} frames")
        self.record_time.setStyleSheet(f"color: {theme.LIVE};")
        self.record_time.setVisible(True)

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save this look", "Name")
        if not ok or not name.strip():
            return
        presets.save(name.strip(), self.pipeline.snapshot())
        self.refresh_presets()
        index = self.preset_combo.findText(name.strip())
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)
        self.log.emit(f"Saved the look \"{name.strip()}\".")

    def _load_preset(self, index: int) -> None:
        name = self.preset_combo.itemText(index)
        if not name or name == "-":
            return
        try:
            self.pipeline.restore(presets.load(name))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not open that look", str(exc))
            return
        self.effects.sync()
        self.log.emit(f"Loaded the look \"{name}\".")

    def _delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        if not name or name == "-":
            return
        presets.delete(name)
        self.refresh_presets()
        self.log.emit(f"Deleted the look \"{name}\".")

    def _reset(self) -> None:
        self.pipeline.reset()
        self.effects.sync()
        self.log.emit("Effects reset.")

    # ------------------------------------------------------------------

    @staticmethod
    def _restyle(widget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
