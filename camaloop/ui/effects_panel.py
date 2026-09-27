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

"""The right-hand column: one card per effect, grouped by what it does."""

from __future__ import annotations

from typing import Dict, List

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.effects import EFFECTS, GROUP_ORDER, Effect, Pipeline
from . import theme
from .widgets import BoolRow, ChoiceRow, ColourRow, FileRow, SliderRow, TextRow

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)"


class CardHeader(QWidget):
    """The top row of a card. Clicking anywhere on it opens the settings.

    The tick box keeps its own clicks, so turning an effect on and opening
    its settings stay separate actions.
    """

    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class EffectCard(QFrame):
    changed = pyqtSignal()

    def __init__(self, effect: Effect, pipeline: Pipeline, parent=None):
        super().__init__(parent)
        self.effect = effect
        self.pipeline = pipeline
        self.rows: Dict[str, QWidget] = {}
        self.setProperty("role", "panel")

        self.toggle = QCheckBox()
        self.toggle.setChecked(pipeline.is_enabled(effect.id))
        self.toggle.setToolTip(f"Turn {effect.name} on or off")
        self.toggle.setCursor(Qt.PointingHandCursor)

        self.title = QLabel(effect.name)
        self.title.setToolTip(effect.blurb)
        font = self.title.font()
        font.setWeight(60)
        self.title.setFont(font)

        # A QToolButton draws its own arrow, so this does not depend on the
        # installed fonts carrying a chevron glyph.
        self.expander = QToolButton()
        self.expander.setArrowType(Qt.DownArrow)
        self.expander.setAutoRaise(True)
        self.expander.setFixedSize(26, 26)
        self.expander.setCursor(Qt.PointingHandCursor)
        self.expander.setToolTip("Show the settings for this effect")

        self.header = CardHeader()
        self.header.setCursor(Qt.PointingHandCursor)
        header = QHBoxLayout(self.header)
        header.setContentsMargins(18, 14, 12, 14)
        header.setSpacing(8)
        header.addWidget(self.toggle)
        header.addWidget(self.title, 1)
        header.addWidget(self.expander)

        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(18, 2, 18, 18)
        body_layout.setSpacing(8)

        blurb = QLabel(effect.blurb)
        blurb.setProperty("role", "note")
        blurb.setWordWrap(True)
        body_layout.addWidget(blurb)

        for param in effect.params:
            row = self._build_row(param)
            if row is not None:
                self.rows[param.key] = row
                body_layout.addWidget(row)

        usable, reason = effect.availability()
        if not usable:
            self.toggle.setEnabled(False)
            self.title.setEnabled(False)
            note = QLabel(reason)
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {theme.AMBER};")
            note.setProperty("role", "note")
            body_layout.addWidget(note)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.body)

        self.body.setVisible(False)
        self.expander.clicked.connect(self._flip)
        self.header.clicked.connect(self._flip)
        self.toggle.toggled.connect(self._on_toggle)
        self._apply_enabled(self.toggle.isChecked())

    # -- construction ------------------------------------------------------

    def _build_row(self, param):
        value = self.pipeline.get_params(self.effect.id)[param.key]
        key = param.key

        if param.kind in ("float", "int"):
            row = SliderRow(
                param.label, value, param.minimum, param.maximum, param.step,
                param.kind == "float", param.suffix,
            )
            row.changed.connect(lambda v, k=key: self._set(k, v))
        elif param.kind == "bool":
            row = BoolRow(param.label, value)
            row.toggled.connect(lambda v, k=key: self._set(k, v))
        elif param.kind == "choice":
            row = ChoiceRow(param.label, value, param.choices)
            row.changed.connect(lambda v, k=key: self._set(k, v))
        elif param.kind == "color":
            row = ColourRow(param.label, value)
            row.changed.connect(lambda v, k=key: self._set(k, v))
        elif param.kind == "file":
            row = FileRow(param.label, value, param.filters or IMAGE_FILTER)
            row.changed.connect(lambda v, k=key: self._set(k, v))
        elif param.kind == "text":
            row = TextRow(param.label, value)
            row.changed.connect(lambda v, k=key: self._set(k, v))
        else:
            return None

        if param.hint:
            row.setToolTip(param.hint)
        return row

    # -- behaviour ---------------------------------------------------------

    def _set(self, key: str, value) -> None:
        self.pipeline.set_param(self.effect.id, key, value)
        self.changed.emit()

    def _flip(self) -> None:
        self.set_expanded(not self.body.isVisible())

    def set_expanded(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self.expander.setArrowType(Qt.UpArrow if expanded else Qt.DownArrow)

    def _on_toggle(self, checked: bool) -> None:
        self.pipeline.set_enabled(self.effect.id, checked)
        self._apply_enabled(checked)
        if checked and not self.body.isVisible():
            self.set_expanded(True)
        self.changed.emit()

    def _apply_enabled(self, enabled: bool) -> None:
        for row in self.rows.values():
            row.setEnabled(enabled)
        self.setStyleSheet(
            f"QFrame[role='panel'] {{ border-color: {theme.AMBER_DIM}; }}"
            if enabled
            else ""
        )

    def sync(self) -> None:
        state = self.pipeline.get_params(self.effect.id)
        self.toggle.blockSignals(True)
        self.toggle.setChecked(self.pipeline.is_enabled(self.effect.id))
        self.toggle.blockSignals(False)
        for key, row in self.rows.items():
            row.blockSignals(True)
            if hasattr(row, "set_value"):
                row.set_value(state[key])
            elif isinstance(row, QCheckBox):
                row.setChecked(bool(state[key]))
            row.blockSignals(False)
        self._apply_enabled(self.toggle.isChecked())


class EffectsPanel(QScrollArea):
    changed = pyqtSignal()

    def __init__(self, pipeline: Pipeline, parent=None):
        super().__init__(parent)
        self.pipeline = pipeline
        self.cards: List[EffectCard] = []

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 16, 0)
        layout.setSpacing(12)

        by_group: Dict[str, List[Effect]] = {}
        for effect in EFFECTS:
            by_group.setdefault(effect.group, []).append(effect)

        for group in GROUP_ORDER:
            if group not in by_group:
                continue
            label = QLabel(group)
            label.setProperty("role", "sub")
            label.setContentsMargins(6, 16, 0, 2)
            layout.addWidget(label)
            for effect in by_group[group]:
                card = EffectCard(effect, pipeline)
                card.changed.connect(self.changed.emit)
                self.cards.append(card)
                layout.addWidget(card)

        layout.addStretch(1)
        self.setWidget(inner)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def sync(self) -> None:
        for card in self.cards:
            card.sync()

    def collapse_all(self) -> None:
        for card in self.cards:
            card.set_expanded(False)
