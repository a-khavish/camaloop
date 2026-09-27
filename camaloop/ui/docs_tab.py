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

"""The manual, inside the app.

Written so that someone who has never used a virtual camera can follow it
from top to bottom, with a contents list to jump around.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .widgets import heading, panel

# Each section is a title and a body. Bodies use a very small subset of
# markup: a line starting with "- " is a bullet, "## " a sub-heading, and
# "`" wraps something to be shown in the monospace face.
SECTIONS = [
    ("What Camaloop does", """
Camaloop sits between your camera and everything else on the computer.

It opens your real camera, applies whatever effects you switch on, and writes
the finished picture into a virtual camera. Other applications - a browser, a
video call, a recorder - see that virtual camera in their camera list, exactly
as though it were a second webcam, with the effects already applied.

## Why a virtual camera at all
A camera on Linux can only be opened by one program at a time. If Camaloop is
holding your webcam, nothing else can also open it. The virtual camera is the
way round that: Camaloop holds the real one, and everything else reads the
virtual one. Several programs can read the same virtual camera at once, so a
browser, a meeting and a recorder can all show the same picture together.
"""),

    ("Getting started", """
## 1. Make a virtual camera
Go to the Virtual cameras tab and press New camera. Give it a name you will
recognise in a video call - that name is exactly what other applications will
show. You will be asked for your password, because creating one changes a
kernel module.

## 2. Start your camera
On the Studio tab, choose your real camera under Source and press Start camera.
The preview shows what Camaloop is seeing.

## 3. Switch on some effects
Click any effect on the right to open its settings, and tick its box to turn it
on. Everything updates as you drag.

## 4. Share it
Press Share with other apps. The light beside it turns red while the picture is
going out.

## 5. Pick it in the other application
Open Zoom, Meet, Discord, OBS or a browser and choose your virtual camera by
name. Applications that were already running usually need restarting before a
newly made camera appears in their list.
"""),

    ("Sources you can use", """
Camaloop can take its picture from several places, all listed under Source.

- Any camera the system sees - built-in, USB, or a capture card. USB cameras
  appear on their own as soon as they are plugged in; the list refreshes by
  itself every couple of seconds.
- A network camera, over RTSP or HTTP. Choose Network camera... and give the
  address, for example `rtsp://user:password@192.168.1.50:554/stream1` or
  `http://192.168.1.51:8080/video`. An old phone running an IP-camera app works
  well this way.
- A video file, played on a loop at its own frame rate.
- A still image, held on screen.
- A test pattern, for checking the rest of the chain without a camera.
- The screen, or a rectangle of it. Choose Screen or a part of it... and give
  either the whole size or something like `1280x720+100+50`. This turns a
  presentation, a browser tab or any other window into a camera that other
  applications can see.

## Screen capture on X11 and on Wayland
Camaloop works out how to grab the screen on its own, and tells you which way
it is using before it starts.

- On an X11 session it uses ffmpeg's X11 grabber, which can take the whole
  screen or any rectangle you give it.
- On a Wayland session it asks the desktop through the screen cast portal,
  and your desktop shows its own picker so you can choose a screen or a single
  window. This is the way that works properly on GNOME, KDE, Sway, Hyprland
  and the rest, and you can stop sharing from your desktop at any moment.
- If there is no portal but grim is installed - common on the wlroots
  compositors - it falls back to grim. That works, at a lower frame rate.
- Failing all of that, it grabs through XWayland, which can only see windows
  that are themselves running under XWayland.

For the portal route you need `xdg-desktop-portal` plus the portal for your
desktop (`xdg-desktop-portal-gnome`, `-kde`, `-wlr` or `-hyprland`), the Python
GObject bindings (`python3-gi` or `python3-gobject`), and either ffmpeg 7.1 or
newer or `gstreamer1.0-pipewire`. The installer sets all of this up for you.

Sharing a window this way is also a neat trick in its own right: anything you
can put in a window - a slide deck, a browser tab, another camera app with its
own effects - becomes a camera that Zoom, Meet or Discord can select.

## Phones and Bluetooth
Linux has no standard way to take video from a Bluetooth camera, so there is
nothing to select. A phone works either over the network as above, or through a
tool such as DroidCam that makes its own /dev/video device - which Camaloop
then sees like any other camera.
"""),

    ("The effects, in order", """
Effects always run in the order they appear in the list, so what you do to the
framing happens before the colour, and the overlays land on top of everything.

## Camera
- Framing - mirror, flip, quarter turns, and a fine angle for straightening a
  tilted camera.
- Crop - trim each edge, or cut to 1:1, 4:3, 16:9, 9:16 or 21:9, then stretch
  back, keep the smaller size, or add bars.
- Zoom and reframe - crop into the sensor and move the crop around. The zoom
  buttons under the preview do the same thing live.

## Face
- Face warp - stretches the face itself: a big head, a tiny one, wide,
  narrow, tall, a bulge, a pinch, a wobble or a swirl. Reach controls how far
  past the face the warp fades out, so it always blends into the rest of the
  picture, and "Apply to" chooses everyone, only you, or everyone except you.
  It finds faces the same way Hide faces does.

## Background
- Green screen - key out a colour and put a colour, an image or a blur behind.
- Background without a green screen - separates you from the room using
  on-device segmentation. Needs the mediapipe package.
- Hide faces - finds faces and covers them. Cover everyone, everyone except
  you, or only you. "You" is taken to be the largest face in frame.
- Cover an area - hide a fixed rectangle: a doorway, a whiteboard, a window.
  It can also be inverted, to show only that rectangle.

## Colour
- Exposure - brightness, contrast and a shadow lift for a dark room.
- Colour - saturation, hue and warmth.
- Colour grade - a finished look in one step: Warm, Cool, Golden hour,
  Moonlight, Teal and orange, Vintage film, Faded, Noir, Vibrant, Pastel,
  Cyberpunk, Bleach bypass.
- Look - black and white, sepia, inverted, posterised, thermal, night vision,
  high contrast.
- Two-tone - map the picture between any two colours.
- Skin smoothing - softens skin and leaves eyes, hair and edges sharp. It
  finds skin by its colour, so it does not need to find a face first. Glow
  adds the soft halo that usually goes with it.

## Texture
- Softness and detail - smooth skin, soften everything, or sharpen.
- Glow - light blooms out of the bright parts. The soft, dreamy look.
- Light and flare - a wash of coloured light across the picture: a warm or
  cool leak down one side, a rainbow, a sun flare with its streak, a corner
  glow, or a flat haze. Drift lets it move slowly by itself.
- Motion trails - leaves a fading copy of whatever moved. Light trails keeps
  the brightest, Echo smears everything, Ghost smears only what is moving, and
  Freeze the background holds the still parts.
- Depth blur - keep one band sharp and blur away from it.
- Stylise - cartoon, pencil sketch, edges, emboss, mosaic.
- Comic dots - a printed halftone in dots, lines or crosshatch.
- Glitch - blocks slip sideways, colour separates, bands tear.
- Terminal - an old phosphor monitor in green, amber, cyan, white or red.
- Mirror and kaleidoscope - fold the picture back on itself.
- Text picture - rebuilds the whole picture out of typed characters, in
  colour or in a single ink. Five character sets, from solid blocks to a dense
  ramp of punctuation.
- Vignette - darken the corners.
- Film and tube - grain, scanlines and a colour split.

## Overlay
- Text - any text, plus `{time}`, `{date}` and `{fps}` which fill themselves in.
- Picture or video on top - a PNG with transparency, a photo, or a video that
  plays and loops. Place it anywhere, trim any edge, stretch, rotate, fade it,
  and key out a black background.
- Falling things - snow, rain, sparkles, confetti, bubbles, embers, leaves or
  stars, drifting down in front of the picture. How many, how fast, how big,
  and which way the wind blows.

## Output
- Output size - what other applications and recordings actually receive, which
  can differ from what the camera gives. Fit with bars, fill by cropping, or
  stretch.
- Frame and corners - round the corners, add a frame, a double frame, a
  polaroid border, crop to a circle, or fade the edge away.
"""),

    ("Photos and recording", """
Take a photo saves exactly the frame being sent, with every effect applied, at
the output size - not a copy of the preview.

Start recording writes the same picture to a video file, showing the elapsed
time and frame count as it goes, with a REC marker on the preview. Recording
keeps running while you change effects, so whatever you adjust mid-take is what
the file shows.

Both land in your Videos folder, or wherever you choose on the Settings tab,
named by the date and time.
"""),

    ("Saved looks", """
Any combination of effects and their settings can be saved as a named look and
brought back later, from the panel under the effects list.

Looks are ordinary JSON files in `~/.config/camaloop/presets/`, so they can be
copied between machines or shared.
"""),

    ("Keyboard", """
- `Space` - take a photo
- `Ctrl` and `R` - start or stop recording
- `Ctrl` and `L` - start or stop sharing with other applications
- `Ctrl` and `K` - start or stop the camera
- `Ctrl` and `+` / `Ctrl` and `-` - zoom in and out
- `Ctrl` and `0` - back to 1x
"""),

    ("Virtual cameras in detail", """
## Making one
New camera asks for a name and, if you want, a specific device number.
"Announce as a capture-only device" is on by default and should stay on:
Chrome, Zoom, Discord and Teams ignore devices that claim to do both capture
and output.

## Editing
A camera's name is fixed when it is created, so renaming removes and recreates
it - close anything using it first. Frame rate, a fixed size and a standby
image need the `v4l2loopback-ctl` tool.

## After a reboot
Cameras live in memory and disappear when the machine restarts. Keep after
reboot writes them into `/etc/modprobe.d/camaloop.conf` so they come back.

## Removing one
A camera that another program has open cannot be removed; the list marks those
as in use. On v4l2loopback older than 0.13 every change reloads the driver, so
all virtual cameras must be idle at that moment.
"""),

    ("When something goes wrong", """
## The camera will not open
Only one program can hold a camera at a time, so close anything else using it.
If it is refused outright, add yourself to the video group and log back in:
`sudo usermod -aG video $USER`

## The virtual camera is missing from another app
Restart that application - most look for cameras only when they start. On
Ubuntu, a snap-packaged Firefox or Chromium also needs
`snap connect firefox:camera`.

## Background replacement does nothing
It needs mediapipe, installed for the same Python Camaloop runs under. The
effect's own message names the exact command to use.

## Nothing that needs a face does anything
Hide faces and Face warp both need OpenCV's face detection data - two XML
files that are not always installed with OpenCV itself. On Debian and Ubuntu
the Python bindings come from python3-opencv and the data from a separate
opencv-data package that nothing depends on. Camaloop carries its own copy
and falls back to it, so this should not come up; if it ever does, those two
effects grey themselves out and say which command to run rather than quietly
doing nothing.

## The frame rate drops
Stylise and Green screen are the most expensive effects. The badge on the
preview shows the real rate; switching one of those off usually restores it.

## The window is the wrong size
The window fits itself to the screen, and "Keep the window maximised" on the
Settings tab holds it there. On a narrow screen the rows of buttons fold onto
a second line rather than running off the edge. If a control still looks out
of reach, `camaloop --check` reports the size the layout is asking for.

## Checking everything at once
Running `camaloop --check` in a terminal prints a report: the session type,
which libraries and tools are present, how the screen can be captured here,
whether face tracking is ready, and every virtual camera it can see. It is
the quickest way to find out what is missing.

## Nothing appears in the tray
Some desktops, GNOME in particular, need an extension before applications can
show a tray icon. Without one, closing the window will quit instead of hiding.
"""),

    ("Where files live", """
- `~/.config/camaloop/settings.json` - everything on the Settings tab
- `~/.config/camaloop/presets/` - your saved looks
- `~/.config/autostart/camaloop.desktop` - the entry that starts Camaloop
  with your session, when that is switched on
- `/etc/modprobe.d/camaloop.conf` - virtual cameras kept across reboots
- `~/.cache/camaloop/` - the background segmentation model, downloaded once

Deleting any of them is safe; Camaloop makes them again as needed.
"""),
]


class DocsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        root = QHBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 20)
        root.setSpacing(18)

        self.contents = QListWidget()
        self.contents.setFixedWidth(266)
        self.contents.setSpacing(3)
        self.contents.setWordWrap(True)
        for index, (title, _body) in enumerate(SECTIONS):
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, index)
            self.contents.addItem(item)
        self.contents.currentRowChanged.connect(self._jump)

        side = QVBoxLayout()
        side.setSpacing(10)
        side.addWidget(heading("Contents"))
        side.addWidget(self.contents, 1)

        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        body = QWidget()
        # Held to a readable line length rather than stretching to the screen.
        body.setMaximumWidth(920)
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(4, 0, 20, 30)
        self.body_layout.setSpacing(16)

        self._anchors = []
        self._bodies = []
        for title, text in SECTIONS:
            card = self._card(title, text)
            self._anchors.append(card)
            self.body_layout.addWidget(card)
        self.body_layout.addStretch(1)
        self.area.setWidget(body)

        root.addLayout(side)
        root.addWidget(self.area, 1)
        self.contents.setCurrentRow(0)

    # ------------------------------------------------------------------

    def _card(self, title: str, text: str) -> QWidget:
        card = panel()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(26, 22, 26, 24)
        layout.setSpacing(12)

        header = QLabel(title)
        font = header.font()
        font.setPointSize(font.pointSize() + 3)
        font.setWeight(63)
        header.setFont(font)
        layout.addWidget(header)

        # A text browser rather than labels: it lays out real lists, with the
        # wrapped lines hanging under the text instead of falling back to the
        # margin, which is something QLabel will not do.
        body = QTextBrowser()
        body.setOpenExternalLinks(False)
        body.setFrameStyle(QFrame.NoFrame)
        body.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.document().setDocumentMargin(0)
        body.setHtml(self._html(text))
        body.setStyleSheet(
            f"QTextBrowser {{ background: transparent; border: none;"
            f" color: {theme.TEXT}; }}"
        )
        layout.addWidget(body)
        self._bodies.append(body)
        return card

    @staticmethod
    def _rich(text: str) -> str:
        """Escape the text, and set anything in backticks in monospace."""
        parts = text.split("`")
        out = []
        for index, chunk in enumerate(parts):
            chunk = chunk.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if index % 2:
                out.append(
                    f'<span style="font-family:monospace; color:{theme.AMBER};">'
                    f"{chunk}</span>"
                )
            else:
                out.append(chunk)
        return "".join(out)

    def _html(self, text: str) -> str:
        """Turn the small markup used above into the HTML Qt will lay out."""
        blocks = []
        paragraph = []
        bullets = []

        def flush_paragraph():
            if paragraph:
                blocks.append(
                    f'<p style="margin:0 0 10px 0; line-height:150%;">'
                    f'{self._rich(" ".join(paragraph))}</p>'
                )
                paragraph.clear()

        def flush_bullets():
            if bullets:
                items = "".join(
                    f'<li style="margin-bottom:6px;">{self._rich(b)}</li>'
                    for b in bullets
                )
                blocks.append(
                    '<ul style="margin:0 0 10px 0; -qt-list-indent:1;">'
                    f"{items}</ul>"
                )
                bullets.clear()

        for raw in text.strip("\n").split("\n"):
            line = raw.rstrip()
            stripped = line.strip()
            if stripped.startswith("## "):
                flush_paragraph(); flush_bullets()
                blocks.append(
                    f'<p style="margin:14px 0 6px 0; font-weight:600;'
                    f' color:{theme.TEXT};">{self._rich(stripped[3:])}</p>'
                )
            elif stripped.startswith("- "):
                flush_paragraph()
                bullets.append(stripped[2:])
            elif not stripped:
                flush_paragraph(); flush_bullets()
            elif bullets and raw.startswith("  "):
                # An indented line carries on the bullet above it rather than
                # starting a paragraph of its own.
                bullets[-1] += " " + stripped
            else:
                flush_bullets()
                paragraph.append(stripped)
        flush_paragraph(); flush_bullets()
        return f'<div style="line-height:150%;">{"".join(blocks)}</div>'

    def _fit_bodies(self) -> None:
        """Give each browser exactly the height its text needs."""
        for body in self._bodies:
            width = max(320, body.viewport().width())
            body.document().setTextWidth(width)
            height = int(body.document().size().height()) + 4
            body.setFixedHeight(height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_bodies()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_bodies()

    def _jump(self, row: int) -> None:
        if 0 <= row < len(self._anchors):
            self.area.ensureWidgetVisible(self._anchors[row], 0, 24)
