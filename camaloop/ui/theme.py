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

"""The dark theme.

The look borrows from a broadcast control desk: cool slate surfaces so the
video is the brightest thing on screen, a signal amber for anything the user
has switched on, and a tally red reserved for one thing only - the camera
being live to other applications.
"""

import os

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (
    QColor, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter,
    QPainterPath, QPalette, QPen, QPixmap,
)
from PyQt5.QtWidgets import QApplication

INK = "#0E1116"
PANEL = "#161A21"
RAISED = "#1E232C"
HOVER = "#252B35"
LINE = "#2A3039"
TEXT = "#E4E8EF"
MUTED = "#8892A0"
FAINT = "#5D6674"
AMBER = "#E8A33D"
AMBER_DIM = "#8A6220"
LIVE = "#E2564D"
OK = "#5FB88E"

_SANS = ["Inter", "IBM Plex Sans", "Cantarell", "Ubuntu", "DejaVu Sans", "Sans Serif"]
_MONO = ["JetBrains Mono", "IBM Plex Mono", "DejaVu Sans Mono", "Monospace"]


def _first_available(candidates):
    families = set(QFontDatabase().families())
    for name in candidates:
        if name in families:
            return name
    return candidates[-1]


def mono_font(size: int = 9) -> QFont:
    font = QFont(_first_available(_MONO))
    font.setPointSize(size)
    return font


STYLESHEET = f"""
QWidget {{
    background: {INK};
    color: {TEXT};
    font-size: 10pt;
}}

QMainWindow, QDialog {{ background: {INK}; }}

QLabel {{ background: transparent; }}
QLabel[role="heading"] {{ font-size: 13pt; font-weight: 600; }}
QLabel[role="sub"] {{ color: {MUTED}; }}
QLabel[role="note"] {{ color: {FAINT}; font-size: 9pt; }}
QLabel[role="value"] {{ color: {AMBER}; }}

/* --- panels ----------------------------------------------------------- */
QFrame[role="panel"] {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 10px;
}}
QFrame[role="bar"] {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 10px;
}}
QFrame[role="divider"] {{ background: {LINE}; max-height: 1px; border: none; }}

/* --- tabs ------------------------------------------------------------- */
QTabWidget::pane {{ border: none; top: -1px; }}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 11px 26px;
    margin-right: 6px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {AMBER}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

/* --- buttons ---------------------------------------------------------- */
QPushButton {{
    background: {RAISED};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 9px 18px;
    color: {TEXT};
}}
QPushButton:hover {{ background: {HOVER}; border-color: #39414D; }}
QPushButton:pressed {{ background: #12161C; }}
QPushButton:disabled {{ color: {FAINT}; background: #14181F; border-color: #21262E; }}
QPushButton[tone="primary"] {{
    background: {AMBER}; color: #16120A; border: none; font-weight: 600;
}}
QPushButton[tone="primary"]:hover {{ background: #F2B457; }}
QPushButton[tone="primary"]:disabled {{ background: {AMBER_DIM}; color: #453317; }}
QPushButton[tone="live"] {{
    background: {LIVE}; color: #1A0D0C; border: none; font-weight: 600;
}}
QPushButton[tone="live"]:hover {{ background: #EC6A61; }}
QPushButton[tone="live"]:disabled {{ background: #4A2724; color: #8A5B57; }}
QPushButton[tone="danger"] {{ color: {LIVE}; }}
QPushButton[tone="danger"]:hover {{ background: #2A1E1E; border-color: #5A2F2C; }}
QPushButton[compact="1"] {{ padding: 6px 4px; font-size: 12pt; }}
QPushButton[tone="quiet"] {{ background: transparent; border-color: transparent; color: {MUTED}; }}
QPushButton[tone="quiet"]:hover {{ background: {RAISED}; color: {TEXT}; }}

QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 2px; }}
QToolButton:hover {{ background: {HOVER}; }}

/* --- inputs ----------------------------------------------------------- */
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background: {RAISED};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 8px 12px;
    selection-background-color: {AMBER};
    selection-color: #16120A;
}}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {AMBER};
}}
/* The drop-down subcontrol is left alone on purpose: styling it at all stops
   Fusion drawing the arrow, and a CSS border triangle does not render here. */
QComboBox QAbstractItemView {{
    background: {RAISED};
    border: 1px solid {LINE};
    selection-background-color: {HOVER};
    outline: none;
    padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 14px; border: none; }}

/* --- sliders ---------------------------------------------------------- */
QSlider::groove:horizontal {{
    height: 3px; background: {LINE}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {AMBER}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {TEXT};
    width: 13px; height: 13px;
    margin: -6px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {AMBER}; }}
QSlider::groove:horizontal:disabled {{ background: #22262D; }}
QSlider::sub-page:horizontal:disabled {{ background: {FAINT}; }}
QSlider::handle:horizontal:disabled {{ background: {FAINT}; }}

/* --- checkboxes ------------------------------------------------------- */
QCheckBox {{ spacing: 10px; padding: 2px 0; background: transparent; }}
QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid #3A414C;
    border-radius: 4px;
    background: {RAISED};
}}
QCheckBox::indicator:checked {{ background: {AMBER}; border-color: {AMBER}; }}
QCheckBox::indicator:hover {{ border-color: {AMBER}; }}

QRadioButton {{ spacing: 10px; padding: 3px 0; background: transparent; }}
QRadioButton::indicator {{
    width: 15px; height: 15px;
    border: 1px solid #3A414C;
    border-radius: 8px;
    background: {RAISED};
}}
QRadioButton::indicator:checked {{
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
                fx:0.5, fy:0.5, stop:0 {AMBER}, stop:0.45 {AMBER},
                stop:0.5 {RAISED}, stop:1 {RAISED});
    border-color: {AMBER};
}}
QRadioButton::indicator:hover {{ border-color: {AMBER}; }}
QListWidget {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 10px;
    padding: 6px;
    outline: none;
}}
QListWidget::item {{ padding: 9px 12px; border-radius: 6px; color: {MUTED}; }}
QListWidget::item:selected {{ background: {HOVER}; color: {TEXT}; }}
QListWidget::item:hover {{ color: {TEXT}; }}

/* --- tables ----------------------------------------------------------- */
QTableWidget {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 10px;
    gridline-color: transparent;
    selection-background-color: {HOVER};
    selection-color: {TEXT};
    outline: none;
}}
QTableWidget::item {{ padding: 12px 14px; border-bottom: 1px solid #1F242C; }}
QTableWidget::item:selected {{ background: {HOVER}; }}
QHeaderView::section {{
    background: {PANEL};
    text-align: left;
    color: {MUTED};
    padding: 12px 14px;
    border: none;
    border-bottom: 1px solid {LINE};
    font-weight: 500;
}}

/* --- scrollbars ------------------------------------------------------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #303742; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3D4653; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #303742; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* --- misc ------------------------------------------------------------- */
QToolTip {{
    background: {RAISED}; color: {TEXT};
    border: 1px solid {LINE}; padding: 6px 8px; border-radius: 6px;
}}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {LINE}; color: {MUTED}; }}
QStatusBar::item {{ border: none; }}
QMenu {{ background: {RAISED}; border: 1px solid {LINE}; padding: 5px; }}
QMenu::item {{ padding: 9px 28px 9px 16px; border-radius: 5px; }}
QMenu::item:selected {{ background: {HOVER}; }}
QPlainTextEdit {{ font-family: "DejaVu Sans Mono", monospace; font-size: 9pt; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")

    font = QFont(_first_available(_SANS))
    font.setPointSize(10)
    app.setFont(font)

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(INK))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(RAISED))
    palette.setColor(QPalette.AlternateBase, QColor(PANEL))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(RAISED))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.Highlight, QColor(AMBER))
    palette.setColor(QPalette.HighlightedText, QColor("#16120A"))
    palette.setColor(QPalette.ToolTipBase, QColor(RAISED))
    palette.setColor(QPalette.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(FAINT))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(FAINT))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(FAINT))
    app.setPalette(palette)

    app.setStyleSheet(STYLESHEET)


# --------------------------------------------------------------------------
# The application icon
# --------------------------------------------------------------------------

ICON_PATHS = (
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "packaging", "camaloop.svg",
    ),
    "/usr/share/icons/hicolor/scalable/apps/camaloop.svg",
    os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps/camaloop.svg"),
)


def draw_icon(size: int = 256) -> QPixmap:
    """The mark, painted rather than loaded.

    The installed file is used when it is there, but a tray icon that
    silently turns into nothing because a file was not copied is worse than
    one that is always drawn, so this is the fallback.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    unit = size / 64.0

    body = QPainterPath()
    body.addRoundedRect(QRectF(2 * unit, 2 * unit, 60 * unit, 60 * unit),
                        15 * unit, 15 * unit)
    painter.fillPath(body, QColor(PANEL))
    painter.setPen(QPen(QColor(LINE), 1.5 * unit))
    painter.drawPath(body)

    # The loop: the picture goes out and comes back as another camera.
    gradient = QLinearGradient(6 * unit, 0, 58 * unit, 64 * unit)
    gradient.setColorAt(0.0, QColor("#F2BE66"))
    gradient.setColorAt(1.0, QColor("#D4861F"))
    pen = QPen(gradient, 4.6 * unit)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.drawArc(QRectF(13 * unit, 13 * unit, 38 * unit, 38 * unit),
                    100 * 16, 300 * 16)

    arrow = QPainterPath()
    arrow.moveTo(QPointF(17.5 * unit, 19.2 * unit))
    arrow.lineTo(QPointF(18.3 * unit, 27.7 * unit))
    arrow.lineTo(QPointF(9.6 * unit, 22.7 * unit))
    arrow.closeSubpath()
    painter.fillPath(arrow, QColor("#F2BE66"))

    # Aperture.
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(INK))
    painter.drawEllipse(QRectF(22 * unit, 22 * unit, 20 * unit, 20 * unit))
    painter.setBrush(QColor(AMBER))
    painter.drawEllipse(QRectF(27 * unit, 27 * unit, 10 * unit, 10 * unit))
    painter.setBrush(QColor("#F7DCA8"))
    painter.drawEllipse(QRectF(28.5 * unit, 28.5 * unit, 3.4 * unit, 3.4 * unit))

    painter.end()
    return pixmap


def app_icon() -> QIcon:
    """The installed icon if it is there, otherwise one painted on the spot."""
    for candidate in ICON_PATHS:
        if os.path.isfile(candidate):
            icon = QIcon(candidate)
            if not icon.isNull() and icon.availableSizes():
                return icon
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(draw_icon(size))
    return icon
