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

"""Small building blocks shared by the tabs."""

from __future__ import annotations

import os
from typing import Optional

import numpy as np
from PyQt5.QtCore import QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
    QLayout,
    QSizePolicy,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import theme


def panel(*, role: str = "panel") -> QFrame:
    frame = QFrame()
    frame.setProperty("role", role)
    return frame


def heading(text: str, role: str = "heading") -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    return label


# --------------------------------------------------------------------------


class FlowLayout(QLayout):
    """A row of controls that wraps onto another line when it runs short of
    room, instead of forcing the window to be wider than the screen.

    Anything with an expanding size policy shares out whatever is left over
    on its line, so on a wide window a bar looks exactly as it would have
    done laid out in a plain row.
    """

    def __init__(self, parent=None, spacing: int = 12, line_spacing: int = 10):
        super().__init__(parent)
        self._items = []
        self._gap = spacing
        self._line_gap = line_spacing

    # -- the five methods QLayout requires ------------------------------

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Horizontal)

    # -- sizing ---------------------------------------------------------

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), place=False)

    def sizeHint(self) -> QSize:
        """Everything on one line, at its natural width."""
        margins = self.contentsMargins()
        width = sum(item.sizeHint().width() for item in self._items)
        width += self._gap * max(0, len(self._items) - 1)
        height = max((item.sizeHint().height() for item in self._items),
                     default=0)
        return QSize(width + margins.left() + margins.right(),
                     height + margins.top() + margins.bottom())

    def minimumSize(self) -> QSize:
        """As narrow as the widest single control, because the rest wraps."""
        margins = self.contentsMargins()
        width = max((item.minimumSize().width() for item in self._items),
                    default=0)
        width += margins.left() + margins.right()
        return QSize(width, self.heightForWidth(width))

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, place=True)

    # -- the work --------------------------------------------------------

    def _arrange(self, rect: QRect, place: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())
        if not self._items:
            return margins.top() + margins.bottom()

        lines, current, used = [], [], 0
        for item in self._items:
            wanted = item.sizeHint().width()
            gap = self._gap if current else 0
            if current and used + gap + wanted > area.width():
                lines.append((current, used))
                current, used, gap = [], 0, 0
            current.append(item)
            used += gap + wanted
        lines.append((current, used))

        y = area.y()
        for items, used in lines:
            height = max(item.sizeHint().height() for item in items)
            growers = [
                item for item in items
                if item.widget() is not None
                and item.widget().sizePolicy().horizontalPolicy()
                & QSizePolicy.ExpandFlag
            ]
            share = max(0, area.width() - used) // len(growers) if growers else 0
            x = area.x()
            for item in items:
                width = item.sizeHint().width()
                if item in growers:
                    width += share
                if place:
                    item.setGeometry(QRect(x, y, width, height))
                x += width + self._gap
            y += height + self._line_gap

        return (y - self._line_gap) - rect.y() + margins.bottom()


def spacer() -> QWidget:
    """Blank, stretchy filler - a flow layout can grow this one.

    A plain QWidget would pick up the panel's border from the stylesheet and
    show as a hairline across the gap, so it is explicitly given none.
    """
    filler = QWidget()
    filler.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    filler.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    filler.setStyleSheet("background: transparent; border: none;")
    return filler


def wraps(widget):
    """Let a widget's height follow from its width, for a wrapping bar."""
    policy = widget.sizePolicy()
    policy.setHeightForWidth(True)
    policy.setVerticalPolicy(QSizePolicy.Minimum)
    widget.setSizePolicy(policy)
    return widget


class PreviewWidget(QWidget):
    """Draws the processed frame, letterboxed, with an idle state."""

    def __init__(self, idle_text: str = "No camera running", parent=None):
        super().__init__(parent)
        self._image: Optional[QImage] = None
        self._idle_text = idle_text
        self._badge = ""
        self._recording = False
        self._flash = 0.0
        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._fade_flash)
        self.setMinimumSize(360, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def flash(self) -> None:
        """A brief white wash, the way a camera shutter reads."""
        self._flash = 1.0
        self._flash_timer.start(28)
        self.update()

    def _fade_flash(self) -> None:
        self._flash = max(0.0, self._flash - 0.16)
        if self._flash <= 0:
            self._flash_timer.stop()
        self.update()

    def set_recording(self, recording: bool) -> None:
        self._recording = recording
        self.update()

    def set_frame(self, rgb: np.ndarray) -> None:
        h, w, _ = rgb.shape
        self._image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        self.update()

    def clear_frame(self) -> None:
        self._image = None
        self.update()

    def set_badge(self, text: str) -> None:
        self._badge = text
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 10, 10)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor("#08090C"))

        if self._image is None:
            painter.setPen(QColor(theme.FAINT))
            font = QFont(painter.font())
            font.setPointSize(11)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignCenter, self._idle_text)
            painter.end()
            return

        scaled = self._image.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawImage(x, y, scaled)

        if self._badge:
            painter.setFont(theme.mono_font(8))
            metrics = painter.fontMetrics()
            w = metrics.horizontalAdvance(self._badge) + 16
            rect = QRect(x + 10, y + 10, w, 22)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawRoundedRect(rect, 5, 5)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(rect, Qt.AlignCenter, self._badge)

        if self._recording:
            painter.setFont(theme.mono_font(8))
            label = "REC"
            metrics = painter.fontMetrics()
            w = metrics.horizontalAdvance(label) + 34
            rect = QRect(x + scaled.width() - w - 10, y + 10, w, 22)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawRoundedRect(rect, 5, 5)
            painter.setBrush(QColor(theme.LIVE))
            painter.drawEllipse(rect.left() + 9, rect.top() + 7, 8, 8)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(
                rect.adjusted(24, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, label
            )

        if self._flash > 0:
            painter.fillRect(
                self.rect(), QColor(255, 255, 255, int(200 * self._flash))
            )
        painter.end()


class TallyLight(QWidget):
    """The one red thing in the interface: on when other apps can see the feed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._live = False
        self._phase = 0.0
        self.setFixedSize(12, 12)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_live(self, live: bool) -> None:
        if live == self._live:
            return
        self._live = live
        if live:
            self._timer.start(60)
        else:
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.12) % (2 * np.pi)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        if self._live:
            glow = 0.55 + 0.45 * float(np.sin(self._phase))
            colour = QColor(theme.LIVE)
            colour.setAlphaF(0.45 + 0.55 * glow)
            painter.setBrush(colour)
        else:
            painter.setBrush(QColor("#2E343E"))
        painter.drawEllipse(1, 1, 10, 10)
        painter.end()


# --------------------------------------------------------------------------


class SliderRow(QWidget):
    """Label, slider and a readout. Works for both integer and float values."""

    changed = pyqtSignal(object)

    def __init__(self, label, value, minimum, maximum, step, is_float, suffix="", parent=None):
        super().__init__(parent)
        self._is_float = is_float
        self._step = step if step else (0.01 if is_float else 1)
        self._min = minimum
        self._suffix = suffix
        self._guard = False

        steps = max(1, int(round((maximum - minimum) / self._step)))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, steps)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(max(1, steps // 10))

        self.name = QLabel(label)
        self.name.setProperty("role", "sub")
        self.readout = QLabel()
        self.readout.setProperty("role", "value")
        self.readout.setFont(theme.mono_font(9))
        self.readout.setMinimumWidth(52)
        self.readout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.name)
        top.addStretch(1)
        top.addWidget(self.readout)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(5)
        layout.addLayout(top)
        layout.addWidget(self.slider)

        self.slider.valueChanged.connect(self._on_slider)
        self.set_value(value)

    def _to_value(self, position: int):
        raw = self._min + position * self._step
        return round(raw, 3) if self._is_float else int(round(raw))

    def _on_slider(self, position: int) -> None:
        value = self._to_value(position)
        self._render(value)
        if not self._guard:
            self.changed.emit(value)

    def _render(self, value) -> None:
        text = f"{value:.2f}" if self._is_float else f"{value}"
        self.readout.setText(text + self._suffix)

    def set_value(self, value) -> None:
        self._guard = True
        position = int(round((float(value) - self._min) / self._step))
        self.slider.setValue(max(self.slider.minimum(), min(self.slider.maximum(), position)))
        self._render(self._to_value(self.slider.value()))
        self._guard = False


class ColourRow(QWidget):
    changed = pyqtSignal(str)

    def __init__(self, label: str, value: str, parent=None):
        super().__init__(parent)
        self._value = value or "#000000"

        name = QLabel(label)
        name.setProperty("role", "sub")
        self.swatch = QPushButton()
        self.swatch.setFixedSize(54, 26)
        self.swatch.setCursor(Qt.PointingHandCursor)
        self.swatch.clicked.connect(self._pick)
        self.code = QLabel(self._value)
        self.code.setFont(theme.mono_font(9))
        self.code.setProperty("role", "note")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 7, 0, 7)
        layout.addWidget(name)
        layout.addStretch(1)
        layout.addWidget(self.code)
        layout.addWidget(self.swatch)
        self._paint()

    def _paint(self) -> None:
        self.swatch.setStyleSheet(
            f"background: {self._value}; border: 1px solid {theme.LINE}; border-radius: 6px;"
        )
        self.code.setText(self._value.upper())

    def _pick(self) -> None:
        colour = QColorDialog.getColor(QColor(self._value), self, "Pick a colour")
        if colour.isValid():
            self._value = colour.name()
            self._paint()
            self.changed.emit(self._value)

    def set_value(self, value: str) -> None:
        self._value = value or "#000000"
        self._paint()


class FileRow(QWidget):
    changed = pyqtSignal(str)

    def __init__(self, label: str, value: str, filters: str, parent=None):
        super().__init__(parent)
        self._filters = filters
        self._value = value

        name = QLabel(label)
        name.setProperty("role", "sub")
        self.field = QLineEdit(value)
        self.field.setPlaceholderText("Choose a file...")
        self.field.setReadOnly(True)
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse)
        clear = QPushButton("x")
        clear.setFixedWidth(30)
        clear.setProperty("tone", "quiet")
        clear.clicked.connect(self._clear)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.field, 1)
        row.addWidget(browse)
        row.addWidget(clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 7, 0, 7)
        layout.setSpacing(4)
        layout.addWidget(name)
        layout.addLayout(row)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a file", os.path.expanduser("~"), self._filters
        )
        if path:
            self.set_value(path)
            self.changed.emit(path)

    def _clear(self) -> None:
        self.set_value("")
        self.changed.emit("")

    def set_value(self, value: str) -> None:
        self._value = value or ""
        self.field.setText(self._value)
        self.field.setToolTip(self._value)


class ChoiceRow(QWidget):
    changed = pyqtSignal(str)

    def __init__(self, label: str, value: str, choices, parent=None):
        super().__init__(parent)
        name = QLabel(label)
        name.setProperty("role", "sub")
        self.combo = QComboBox()
        self.combo.addItems(list(choices))
        index = self.combo.findText(str(value))
        self.combo.setCurrentIndex(max(0, index))
        self.combo.currentTextChanged.connect(self.changed.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 7, 0, 7)
        layout.addWidget(name)
        layout.addStretch(1)
        layout.addWidget(self.combo, 1)

    def set_value(self, value: str) -> None:
        index = self.combo.findText(str(value))
        if index >= 0:
            self.combo.setCurrentIndex(index)


class TextRow(QWidget):
    changed = pyqtSignal(str)

    def __init__(self, label: str, value: str, parent=None):
        super().__init__(parent)
        name = QLabel(label)
        name.setProperty("role", "sub")
        self.field = QLineEdit(str(value))
        self.field.textChanged.connect(self.changed.emit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 7, 0, 7)
        layout.setSpacing(4)
        layout.addWidget(name)
        layout.addWidget(self.field)

    def set_value(self, value: str) -> None:
        self.field.setText(str(value))


class BoolRow(QCheckBox):
    def __init__(self, label: str, value: bool, parent=None):
        super().__init__(label, parent)
        self.setChecked(bool(value))
