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

"""Pure-python V4L2 bindings.

Only the few ioctls the app needs are implemented, so the project has no
dependency on python-v4l2, pyvirtualcam or the v4l2-utils binaries for the
core capture/output path.

Tested against Linux 5.x/6.x on x86_64 and aarch64.
"""

from __future__ import annotations

import ctypes
import fcntl
import glob
import os
import re
import struct
from dataclasses import dataclass, field
from typing import List, Optional

# --------------------------------------------------------------------------
# ioctl plumbing
# --------------------------------------------------------------------------

_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_READ = 2


def _ioc(direction: int, type_char: str, nr: int, size: int) -> int:
    value = (direction << 30) | (size << 16) | (ord(type_char) << 8) | nr
    # fcntl.ioctl wants a signed C int on most builds.
    return ctypes.c_int32(value).value


# sizeof(struct v4l2_capability) == 104, sizeof(struct v4l2_format) == 208
VIDIOC_QUERYCAP = _ioc(_IOC_READ, "V", 0, 104)
VIDIOC_G_FMT = _ioc(_IOC_READ | _IOC_WRITE, "V", 4, 208)
VIDIOC_S_FMT = _ioc(_IOC_READ | _IOC_WRITE, "V", 5, 208)

V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_VIDEO_OUTPUT = 0x00000002
V4L2_CAP_VIDEO_M2M = 0x00008000
V4L2_CAP_STREAMING = 0x04000000
V4L2_CAP_DEVICE_CAPS = 0x80000000

V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
V4L2_BUF_TYPE_VIDEO_OUTPUT = 2

V4L2_FIELD_NONE = 1
V4L2_COLORSPACE_SRGB = 8


def fourcc(code: str) -> int:
    a, b, c, d = code
    return ord(a) | (ord(b) << 8) | (ord(c) << 16) | (ord(d) << 24)


V4L2_PIX_FMT_RGB24 = fourcc("RGB3")
V4L2_PIX_FMT_BGR24 = fourcc("BGR3")
V4L2_PIX_FMT_YUYV = fourcc("YUYV")


# --------------------------------------------------------------------------
# Device enumeration
# --------------------------------------------------------------------------


@dataclass
class VideoDevice:
    path: str
    index: int
    driver: str = ""
    card: str = ""
    bus_info: str = ""
    capabilities: int = 0
    accessible: bool = True
    error: str = ""
    virtual: bool = False       # no hardware behind it, per sysfs
    formats: List[str] = field(default_factory=list)

    @property
    def is_capture(self) -> bool:
        # A loopback made with exclusive_caps announces itself as output only
        # until something streams to it, and as capture once something does.
        # Either way an application can capture from it, so say so.
        if self.is_loopback:
            return True
        return bool(self.capabilities & V4L2_CAP_VIDEO_CAPTURE)

    @property
    def is_output(self) -> bool:
        return bool(self.capabilities & V4L2_CAP_VIDEO_OUTPUT)

    @property
    def is_loopback(self) -> bool:
        # The driver calls itself "v4l2 loopback" through VIDIOC_QUERYCAP but
        # "v4l2loopback" in sysfs, so the name is compared with the spacing
        # and punctuation taken out.
        if _squash(self.driver) == "v4l2loopback":
            return True
        # The node could not be queried - permissions, or it is busy because
        # it was made with exclusive_caps. sysfs still tells us what it is.
        return self.virtual and not self.driver

    @property
    def kind(self) -> str:
        if self.is_loopback:
            return "Virtual"
        if self.is_capture and self.is_output:
            return "Capture + output"
        if self.is_capture:
            return "Capture"
        if self.is_output:
            return "Output"
        return "Other"

    @property
    def label(self) -> str:
        name = self.card or "Unknown device"
        return f"{name}  ({self.path})"


SYSFS_ROOT = "/sys/class/video4linux"


def _squash(name: str) -> str:
    """Fold a driver name to one comparable form: 'v4l2 loopback' and
    'v4l2loopback' are the same driver reported through different interfaces."""
    return "".join(c for c in name.lower() if c.isalnum())


def _sysfs_name(index: int) -> str:
    try:
        with open(os.path.join(SYSFS_ROOT, f"video{index}", "name"), "r") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _sysfs_is_virtual(index: int) -> bool:
    """True for a device with no hardware behind it, such as a loopback.

    Read from sysfs rather than by opening the node, because opening can fail
    on permissions, and a loopback created with exclusive_caps refuses to open
    at all while another program is streaming from it.
    """
    try:
        target = os.path.realpath(os.path.join(SYSFS_ROOT, f"video{index}"))
    except OSError:
        return False
    return "/devices/virtual/" in target


def _sysfs_driver(index: int) -> str:
    """The module that owns the device, taken from sysfs."""
    for relative in ("device/driver", "driver"):
        try:
            link = os.path.join(SYSFS_ROOT, f"video{index}", relative)
            if os.path.islink(link):
                return os.path.basename(os.path.realpath(link))
        except OSError:
            continue
    return ""


def query_capability(path: str) -> Optional[VideoDevice]:
    """Describe a device node.

    Everything that can be learned without opening the node is read from
    sysfs first, so a device still appears when it cannot be opened - which
    happens on a permissions problem, and on a loopback made with
    exclusive_caps while another program is streaming from it.
    """
    match = re.search(r"(\d+)$", path)
    index = int(match.group(1)) if match else -1
    dev = VideoDevice(path=path, index=index)

    dev.card = _sysfs_name(index)
    dev.virtual = _sysfs_is_virtual(index)
    sysfs_driver = _sysfs_driver(index)
    if sysfs_driver:
        dev.driver = sysfs_driver

    fd = None
    for flags in (os.O_RDWR | os.O_NONBLOCK, os.O_RDONLY | os.O_NONBLOCK):
        try:
            fd = os.open(path, flags)
            break
        except OSError as exc:
            dev.error = exc.strerror or str(exc)
    if fd is None:
        dev.accessible = False
        return dev

    try:
        buf = bytearray(104)
        fcntl.ioctl(fd, VIDIOC_QUERYCAP, buf, True)
        driver, card, bus, _version, caps, device_caps, _res = struct.unpack(
            "16s32s32sIII12s", bytes(buf)
        )
        dev.driver = driver.split(b"\0", 1)[0].decode("utf-8", "replace") or dev.driver
        dev.card = card.split(b"\0", 1)[0].decode("utf-8", "replace") or dev.card
        dev.bus_info = bus.split(b"\0", 1)[0].decode("utf-8", "replace")
        dev.capabilities = device_caps if caps & V4L2_CAP_DEVICE_CAPS else caps
    except OSError as exc:
        # Keep the device: sysfs already told us what it is.
        dev.error = exc.strerror or str(exc)
    finally:
        os.close(fd)
    return dev


def list_devices() -> List[VideoDevice]:
    """Every /dev/video* node that answers to V4L2, ordered by index."""
    devices = []
    for path in glob.glob("/dev/video*"):
        if not re.fullmatch(r"/dev/video\d+", path):
            continue
        dev = query_capability(path)
        if dev is not None:
            devices.append(dev)
    devices.sort(key=lambda d: d.index)
    return devices


def list_capture_devices() -> List[VideoDevice]:
    return [d for d in list_devices() if d.is_capture]


def list_loopback_devices() -> List[VideoDevice]:
    return [d for d in list_devices() if d.is_loopback]


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


class V4L2OutputError(RuntimeError):
    pass


class V4L2Output:
    """Writes raw RGB24 frames into a v4l2loopback node.

    Any application that opens the same node - a browser, a video call, OBS -
    sees the frames as if they came from a physical camera.
    """

    def __init__(self, path: str, width: int, height: int):
        self.path = path
        self.width = int(width)
        self.height = int(height)
        self.frame_bytes = self.width * self.height * 3
        self._fd: Optional[int] = None
        self._open()

    def _open(self) -> None:
        try:
            self._fd = os.open(self.path, os.O_RDWR)
        except OSError as exc:
            raise V4L2OutputError(
                f"Cannot open {self.path}: {exc.strerror}. "
                "Check that the device exists and that your user is in the 'video' group."
            ) from exc

        buf = bytearray(208)
        struct.pack_into("I", buf, 0, V4L2_BUF_TYPE_VIDEO_OUTPUT)
        struct.pack_into(
            "8I",
            buf,
            8,  # the fmt union starts at offset 8 (4 bytes type + 4 padding)
            self.width,
            self.height,
            V4L2_PIX_FMT_RGB24,
            V4L2_FIELD_NONE,
            self.width * 3,  # bytesperline
            self.frame_bytes,  # sizeimage
            V4L2_COLORSPACE_SRGB,
            0,  # priv
        )
        try:
            fcntl.ioctl(self._fd, VIDIOC_S_FMT, buf, True)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            raise V4L2OutputError(
                f"{self.path} rejected {self.width}x{self.height} RGB24: {exc.strerror}. "
                "Another program may already be streaming to this device."
            ) from exc

        width, height, _pixfmt, _field, _bpl, sizeimage = struct.unpack_from("6I", buf, 8)
        # The driver may clamp the geometry; follow whatever it settled on.
        self.width, self.height = int(width), int(height)
        self.frame_bytes = int(sizeimage) or self.width * self.height * 3

    def send(self, rgb_frame) -> None:
        """rgb_frame: contiguous uint8 ndarray shaped (height, width, 3), RGB order."""
        if self._fd is None:
            raise V4L2OutputError("Output device is closed.")
        data = rgb_frame.tobytes()
        if len(data) != self.frame_bytes:
            raise V4L2OutputError(
                f"Frame is {len(data)} bytes, device wants {self.frame_bytes}."
            )
        os.write(self._fd, data)

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def __enter__(self) -> "V4L2Output":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
