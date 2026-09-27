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

"""Reading the screen as though it were a camera.

Three ways of grabbing are supported, picked automatically:

X11
    ffmpeg's x11grab, which can take the whole screen or any rectangle.

Wayland, through the desktop portal
    The compositor is asked for a screen cast over D-Bus. It shows its own
    picker, the user chooses a screen or a window, and the frames arrive on a
    PipeWire stream. This is the route that works on GNOME, KDE and on the
    wlroots compositors, and it is the only one a well behaved Wayland
    compositor allows.

Wayland, through grim
    A fallback for wlroots compositors (Sway, Hyprland, river) where the
    portal is not installed. One screenshot per frame, so it is slower, but
    it needs nothing but grim.

Whichever is used, frames come back as BGR uint8 arrays.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from typing import List, Optional, Tuple

import numpy as np


class ScreenGrabError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Working out what kind of session this is
# --------------------------------------------------------------------------


def session_type() -> str:
    """'x11', 'wayland', or '' when it cannot be told."""
    kind = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    if kind in ("x11", "wayland"):
        return kind
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return ""


def desktop_name() -> str:
    """The desktop in use, lower case: 'gnome', 'kde', 'sway' and so on."""
    for key in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION"):
        value = (os.environ.get(key) or "").strip().lower()
        if value:
            return value.split(":")[0]
    if os.environ.get("SWAYSOCK"):
        return "sway"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    return ""


def _wlroots_like() -> bool:
    return desktop_name() in (
        "sway", "hyprland", "river", "wayfire", "labwc", "niri", "dwl",
    )


def portal_available() -> bool:
    """True when a desktop portal that can screen cast is reachable."""
    if session_type() != "wayland":
        return False
    try:
        from gi.repository import Gio  # noqa: F401
    except Exception:
        return False
    try:
        from gi.repository import Gio

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            _variant("(s)", ("org.freedesktop.portal.Desktop",)),
            None, Gio.DBusCallFlags.NONE, 1500, None,
        )
        return bool(reply.unpack()[0])
    except Exception:
        return False


def _variant(signature, value):
    from gi.repository import GLib

    return GLib.Variant(signature, value)


def backends() -> List[str]:
    """The grabbing methods that would work here, best first."""
    found = []
    kind = session_type()
    have_ffmpeg = shutil.which("ffmpeg") is not None
    if kind == "wayland":
        if portal_available() and (have_ffmpeg or shutil.which("gst-launch-1.0")):
            found.append("portal")
        if shutil.which("grim"):
            found.append("grim")
        if have_ffmpeg and os.environ.get("DISPLAY"):
            found.append("x11")           # XWayland only, but better than nothing
    else:
        if have_ffmpeg:
            found.append("x11")
    return found


def describe_support() -> str:
    """One line on whether screen capture will work here, and how."""
    kind = session_type()
    usable = backends()
    if kind == "wayland":
        if "portal" in usable:
            return (
                "Wayland session: capture goes through the desktop portal. "
                "Your desktop will ask which screen or window to share the "
                "first time you start it."
            )
        if "grim" in usable:
            return (
                "Wayland session: no screen cast portal was found, so grim "
                "will be used instead. It works, but at a lower frame rate. "
                "Installing xdg-desktop-portal makes it smooth."
            )
        if "x11" in usable:
            return (
                "Wayland session with no portal and no grim. Only windows "
                "running through XWayland can be grabbed; a native Wayland "
                "window will come out black. Install xdg-desktop-portal and "
                "the portal for your desktop to fix this."
            )
        return (
            "Wayland session, and nothing here can grab the screen. Install "
            "xdg-desktop-portal plus the portal for your desktop "
            "(xdg-desktop-portal-gnome, -kde or -wlr), or install grim."
        )
    if shutil.which("ffmpeg") is None:
        return "Screen capture needs ffmpeg, which is not installed."
    if kind == "x11":
        return "X11 session: screen capture available."
    return "Could not tell what kind of session this is; capture may not work."


# --------------------------------------------------------------------------
# The Wayland portal
# --------------------------------------------------------------------------


class PortalScreenCast:
    """Asks the desktop for a screen cast and hands back a PipeWire stream.

    The conversation is the one described by the org.freedesktop.portal
    ScreenCast interface: create a session, say what may be shared, start it
    - which is when the desktop shows its picker - and finally ask for the
    PipeWire connection.
    """

    def __init__(self, sources: str = "monitor", cursor: bool = True,
                 timeout: float = 120.0):
        self.sources = sources
        self.cursor = cursor
        self.timeout = timeout
        self.node_id: Optional[int] = None
        self.size: Optional[Tuple[int, int]] = None
        self.fd: Optional[int] = None
        self._session = ""
        self._bus = None
        self._loop = None

    # -- plumbing ------------------------------------------------------

    def _token(self, prefix: str) -> str:
        return f"camaloop_{prefix}_{os.getpid()}_{id(self) & 0xFFFF}"

    def _call(self, method: str, body, reply_signature="(o)"):
        from gi.repository import Gio

        return self._bus.call_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.ScreenCast",
            method, body, None, Gio.DBusCallFlags.NONE,
            int(self.timeout * 1000), None,
        )

    def _await_response(self, request_path: str):
        """Wait for the portal's Response signal on one request object."""
        from gi.repository import GLib

        result = {}
        done = threading.Event()

        def on_response(_conn, _sender, _path, _iface, _signal, params):
            code, results = params.unpack()
            result["code"] = code
            result["results"] = results
            done.set()
            if self._loop is not None and self._loop.is_running():
                self._loop.quit()

        sub = self._bus.signal_subscribe(
            "org.freedesktop.portal.Desktop",
            "org.freedesktop.portal.Request", "Response",
            request_path, None, 0, on_response,
        )
        try:
            self._loop = GLib.MainLoop()
            # A guard so a user who never answers the picker cannot wedge us.
            GLib.timeout_add_seconds(
                int(self.timeout),
                lambda: (self._loop.quit(), False)[1],
            )
            self._loop.run()
        finally:
            self._bus.signal_unsubscribe(sub)
            self._loop = None

        if not done.is_set():
            raise ScreenGrabError(
                "The screen sharing request was not answered in time."
            )
        if result.get("code") != 0:
            raise ScreenGrabError("Screen sharing was cancelled.")
        return result.get("results") or {}

    # -- the conversation ----------------------------------------------

    def start(self) -> None:
        try:
            from gi.repository import Gio
        except Exception as exc:
            raise ScreenGrabError(
                "Wayland screen capture needs the Python GObject bindings. "
                "Install python3-gi (Debian, Ubuntu), python3-gobject "
                "(Fedora, openSUSE) or python-gobject (Arch)."
            ) from exc

        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as exc:
            raise ScreenGrabError(f"Could not reach the session bus: {exc}") from exc

        session_token = self._token("session")
        reply = self._call("CreateSession", _variant("(a{sv})", ({
            "handle_token": _variant("s", self._token("create")),
            "session_handle_token": _variant("s", session_token),
        },)))
        results = self._await_response(reply.unpack()[0])
        self._session = results.get("session_handle", "")
        if not self._session:
            raise ScreenGrabError("The desktop did not open a screen cast session.")

        wanted = {"monitor": 1, "window": 2, "both": 3, "virtual": 4}.get(
            self.sources, 1
        )
        reply = self._call("SelectSources", _variant("(oa{sv})", (
            self._session, {
                "handle_token": _variant("s", self._token("select")),
                "types": _variant("u", wanted),
                "multiple": _variant("b", False),
                "cursor_mode": _variant("u", 2 if self.cursor else 1),
            },
        )))
        self._await_response(reply.unpack()[0])

        reply = self._call("Start", _variant("(osa{sv})", (
            self._session, "", {
                "handle_token": _variant("s", self._token("start")),
            },
        )))
        results = self._await_response(reply.unpack()[0])

        streams = results.get("streams") or []
        if not streams:
            raise ScreenGrabError("The desktop did not share anything.")
        node_id, props = streams[0]
        self.node_id = int(node_id)
        size = props.get("size")
        if size and len(size) == 2:
            self.size = (int(size[0]), int(size[1]))

        from gi.repository import Gio, GLib

        reply, fd_list = self._bus.call_with_unix_fd_list_sync(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.ScreenCast",
            "OpenPipeWireRemote",
            _variant("(oa{sv})", (self._session, {})),
            GLib.VariantType.new("(h)"), Gio.DBusCallFlags.NONE,
            int(self.timeout * 1000), None, None,
        )
        index = reply.unpack()[0]
        self.fd = fd_list.get(index)

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        if self._session and self._bus is not None:
            try:
                from gi.repository import Gio

                self._bus.call_sync(
                    "org.freedesktop.portal.Desktop", self._session,
                    "org.freedesktop.portal.Session", "Close",
                    None, None, Gio.DBusCallFlags.NONE, 2000, None,
                )
            except Exception:
                pass
            self._session = ""


# --------------------------------------------------------------------------
# The grabbers
# --------------------------------------------------------------------------


class _PipeReader:
    """Reads whole frames from a child process writing raw BGR on a pipe."""

    def __init__(self, process, width: int, height: int):
        self._process = process
        self.width = width
        self.height = height
        self._frame_bytes = width * height * 3

    def read(self) -> Optional[np.ndarray]:
        # An unbuffered pipe hands back whatever one read syscall gave, which
        # is usually less than a whole frame, so keep asking until the frame
        # is complete or the stream ends.
        chunks = []
        remaining = self._frame_bytes
        while remaining > 0:
            piece = self._process.stdout.read(remaining)
            if not piece:
                return None
            chunks.append(piece)
            remaining -= len(piece)
        return np.frombuffer(b"".join(chunks), dtype=np.uint8).reshape(
            (self.height, self.width, 3)
        ).copy()

    def error_text(self) -> str:
        try:
            return (self._process.stderr.read() or b"").decode("utf-8", "replace").strip()
        except Exception:
            return ""

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
        for stream in (self._process.stdout, self._process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass


class ScreenGrabber:
    """Frames from the screen, however this system allows it."""

    def __init__(self, geometry: str = "", fps: int = 30,
                 backend: str = "auto", sources: str = "monitor",
                 cursor: bool = True):
        self.backend = ""
        self._reader: Optional[_PipeReader] = None
        self._portal: Optional[PortalScreenCast] = None
        self._grim_geometry = ""
        self.width = 0
        self.height = 0

        choices = backends() if backend in ("", "auto") else [backend]
        if not choices:
            raise ScreenGrabError(describe_support())

        problems = []
        for name in choices:
            try:
                if name == "x11":
                    self._start_x11(geometry, fps)
                elif name == "portal":
                    self._start_portal(geometry, fps, sources, cursor)
                elif name == "grim":
                    self._start_grim(geometry)
                else:
                    continue
                self.backend = name
                return
            except ScreenGrabError as exc:
                problems.append(f"{name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{name}: {exc}")
        raise ScreenGrabError(
            "Screen capture could not be started.\n" + "\n".join(problems)
        )

    # -- X11 -----------------------------------------------------------

    def _start_x11(self, geometry: str, fps: int) -> None:
        if shutil.which("ffmpeg") is None:
            raise ScreenGrabError(
                "Screen capture needs ffmpeg. Install it with your package "
                "manager, for example: sudo apt install ffmpeg"
            )
        width, height, x, y = self._parse(geometry)
        display = os.environ.get("DISPLAY", ":0.0")
        command = [
            "ffmpeg", "-loglevel", "error", "-nostdin",
            "-f", "x11grab",
            "-framerate", str(max(1, int(fps))),
            "-video_size", f"{width}x{height}",
            "-i", f"{display}+{x},{y}",
            "-pix_fmt", "bgr24", "-f", "rawvideo", "-",
        ]
        self._spawn(command, width, height)

    # -- Wayland through the portal -------------------------------------

    def _start_portal(self, geometry: str, fps: int, sources: str,
                      cursor: bool) -> None:
        portal = PortalScreenCast(sources=sources, cursor=cursor)
        portal.start()
        self._portal = portal
        if portal.fd is None or portal.node_id is None:
            raise ScreenGrabError("The desktop did not hand over a stream.")

        if geometry:
            width, height, _x, _y = self._parse(geometry)
        elif portal.size:
            width, height = portal.size
        else:
            width, height = self.screen_size()
        # Odd sizes upset some converters, and nothing here needs them.
        width, height = max(2, width - width % 2), max(2, height - height % 2)

        if self._ffmpeg_has_pipewire():
            command = [
                "ffmpeg", "-loglevel", "error", "-nostdin",
                "-f", "lavfi",
                "-i", f"pipewiregrab=fd={portal.fd}:node={portal.node_id}",
                "-vf", f"scale={width}:{height}",
                "-r", str(max(1, int(fps))),
                "-pix_fmt", "bgr24", "-f", "rawvideo", "-",
            ]
        elif shutil.which("gst-launch-1.0"):
            command = [
                "gst-launch-1.0", "-q",
                "pipewiresrc", f"fd={portal.fd}", f"path={portal.node_id}",
                "!", "videoconvert", "!", "videoscale", "!",
                "video/x-raw,format=BGR,"
                f"width={width},height={height},pixel-aspect-ratio=1/1",
                "!", "fdsink", "fd=1",
            ]
        else:
            raise ScreenGrabError(
                "A PipeWire stream was offered but nothing here can read it. "
                "Install ffmpeg 7.1 or newer, or gstreamer1.0-pipewire."
            )
        self._spawn(command, width, height, pass_fds=(portal.fd,))

    @staticmethod
    def _ffmpeg_has_pipewire() -> bool:
        if shutil.which("ffmpeg") is None:
            return False
        try:
            out = subprocess.run(
                ["ffmpeg", "-hide_banner", "-filters"],
                capture_output=True, text=True, timeout=8,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return "pipewiregrab" in out

    # -- Wayland through grim -------------------------------------------

    def _start_grim(self, geometry: str) -> None:
        if shutil.which("grim") is None:
            raise ScreenGrabError("grim is not installed.")
        if geometry:
            width, height, x, y = self._parse(geometry)
            self._grim_geometry = f"{x},{y} {width}x{height}"
        else:
            width, height = self.screen_size()
            self._grim_geometry = ""
        self.width, self.height = width, height
        probe = self._grim_frame()
        if probe is None:
            raise ScreenGrabError(
                "grim could not take a picture of the screen. On wlroots "
                "compositors this usually means the compositor does not "
                "support wlr-screencopy."
            )
        self.height, self.width = probe.shape[:2]

    def _grim_frame(self) -> Optional[np.ndarray]:
        command = ["grim"]
        if self._grim_geometry:
            command += ["-g", self._grim_geometry]
        command += ["-t", "ppm", "-"]
        try:
            done = subprocess.run(command, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0 or not done.stdout:
            return None
        return self._read_ppm(done.stdout)

    @staticmethod
    def _read_ppm(data: bytes) -> Optional[np.ndarray]:
        if not data.startswith(b"P6"):
            return None
        fields, offset = [], 2
        while len(fields) < 3 and offset < len(data):
            while offset < len(data) and data[offset : offset + 1].isspace():
                offset += 1
            if data[offset : offset + 1] == b"#":
                while offset < len(data) and data[offset] != 0x0A:
                    offset += 1
                continue
            start = offset
            while offset < len(data) and not data[offset : offset + 1].isspace():
                offset += 1
            fields.append(int(data[start:offset]))
        offset += 1
        width, height, _maximum = fields
        expected = width * height * 3
        if len(data) - offset < expected:
            return None
        rgb = np.frombuffer(data, dtype=np.uint8, count=expected, offset=offset)
        return rgb.reshape((height, width, 3))[:, :, ::-1].copy()

    # -- shared ----------------------------------------------------------

    def _spawn(self, command, width: int, height: int, pass_fds=()) -> None:
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0, pass_fds=pass_fds,
            )
        except OSError as exc:
            raise ScreenGrabError(f"Could not start {command[0]}: {exc}") from exc
        self.width, self.height = width, height
        self._reader = _PipeReader(process, width, height)

    @staticmethod
    def _parse(geometry: str) -> Tuple[int, int, int, int]:
        if not geometry or geometry.lower() in ("full", "screen", "whole screen"):
            return (*ScreenGrabber.screen_size(), 0, 0)
        try:
            size, _, offset = geometry.partition("+")
            w, h = (int(v) for v in size.lower().split("x"))
            if offset:
                x, _, y = offset.partition("+")
                return w, h, int(x or 0), int(y or 0)
            return w, h, 0, 0
        except ValueError:
            raise ScreenGrabError(
                f'Could not read "{geometry}". Use something like 1280x720+100+50.'
            ) from None

    @staticmethod
    def screen_size() -> Tuple[int, int]:
        """The screen's size, asked of whatever can answer."""
        if session_type() == "wayland":
            size = _wayland_screen_size()
            if size:
                return size
        for command in (["xdpyinfo"], ["xrandr"]):
            if shutil.which(command[0]) is None:
                continue
            try:
                out = subprocess.run(
                    command, capture_output=True, text=True, timeout=5
                ).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            for line in out.splitlines():
                if "dimensions:" in line:
                    part = line.split("dimensions:")[1].strip().split()[0]
                    w, _, h = part.partition("x")
                    return int(w), int(h)
                if " connected" in line and "x" in line:
                    for token in line.split():
                        if "x" in token and "+" in token:
                            size = token.split("+")[0]
                            w, _, h = size.partition("x")
                            if w.isdigit() and h.isdigit():
                                return int(w), int(h)
        return 1920, 1080

    def read(self) -> Optional[np.ndarray]:
        if self.backend == "grim":
            return self._grim_frame()
        if self._reader is None:
            return None
        return self._reader.read()

    def error_text(self) -> str:
        return self._reader.error_text() if self._reader else ""

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None
        if self._portal is not None:
            self._portal.close()
            self._portal = None


def _wayland_screen_size() -> Optional[Tuple[int, int]]:
    """The size of the first monitor, asked of the compositor."""
    if shutil.which("wlr-randr"):
        try:
            out = subprocess.run(["wlr-randr"], capture_output=True, text=True,
                                 timeout=5).stdout
            match = re.search(r"(\d{3,5})x(\d{3,5})\s+px", out)
            if match:
                return int(match.group(1)), int(match.group(2))
        except (OSError, subprocess.SubprocessError):
            pass
    if shutil.which("swaymsg"):
        try:
            import json

            out = subprocess.run(["swaymsg", "-t", "get_outputs", "-r"],
                                 capture_output=True, text=True, timeout=5).stdout
            for output in json.loads(out):
                mode = output.get("current_mode") or {}
                if mode.get("width"):
                    return int(mode["width"]), int(mode["height"])
        except Exception:
            pass
    if shutil.which("hyprctl"):
        try:
            import json

            out = subprocess.run(["hyprctl", "-j", "monitors"],
                                 capture_output=True, text=True, timeout=5).stdout
            for monitor in json.loads(out):
                if monitor.get("width"):
                    return int(monitor["width"]), int(monitor["height"])
        except Exception:
            pass
    return None
