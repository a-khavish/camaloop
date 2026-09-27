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

"""Create, edit and remove v4l2loopback virtual cameras.

Two back-ends are used, in order of preference:

1. ``v4l2loopback-ctl`` plus the ``/dev/v4l2loopback`` control node. This adds
   and removes single devices while the module stays loaded, so other running
   streams are untouched. Needs v4l2loopback >= 0.13.
2. Reloading the kernel module with a new device list. This works everywhere
   but tears down every virtual camera for a moment, so it is only used when
   back-end 1 is unavailable.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from . import v4l2

MODULE = "v4l2loopback"
CONTROL_NODE = "/dev/v4l2loopback"
MODPROBE_CONF = "/etc/modprobe.d/camaloop.conf"
MODULES_LOAD_CONF = "/etc/modules-load.d/camaloop.conf"
# Init systems other than systemd read this one.
LEGACY_MODULES_FILE = "/etc/modules"


class LoopbackError(RuntimeError):
    """Raised with a message meant to be shown verbatim in the interface."""

    def __init__(self, message: str, command: str = ""):
        super().__init__(message)
        self.command = command


@dataclass
class VirtualCamera:
    path: str
    index: int
    label: str
    in_use: bool = False

    @property
    def device_number(self) -> int:
        return self.index


# --------------------------------------------------------------------------
# Environment probing
# --------------------------------------------------------------------------


def module_loaded() -> bool:
    return os.path.isdir(f"/sys/module/{MODULE}")


def module_version() -> str:
    """The loaded module's version string, e.g. '0.12.7'. Empty if unknown."""
    try:
        with open(f"/sys/module/{MODULE}/version", "r") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def module_installed() -> bool:
    if module_loaded():
        return True
    modprobe = shutil.which("modprobe") or "/sbin/modprobe"
    try:
        result = subprocess.run(
            [modprobe, "-n", MODULE], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ctl_path() -> Optional[str]:
    return shutil.which("v4l2loopback-ctl")


def supports_dynamic_devices() -> bool:
    return ctl_path() is not None and os.path.exists(CONTROL_NODE)


def list_cameras() -> List[VirtualCamera]:
    cameras = []
    for dev in v4l2.list_loopback_devices():
        cameras.append(
            VirtualCamera(
                path=dev.path,
                index=dev.index,
                label=dev.card,
                in_use=_device_busy(dev.index),
            )
        )
    return cameras


def _device_busy(index: int) -> bool:
    """True when at least one process has the node open."""
    try:
        with open(f"/sys/devices/virtual/video4linux/video{index}/dev", "r"):
            pass
    except OSError:
        pass
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        fd_dir = f"/proc/{pid}/fd"
        try:
            for fd in os.listdir(fd_dir):
                try:
                    target = os.readlink(os.path.join(fd_dir, fd))
                except OSError:
                    continue
                if target == f"/dev/video{index}":
                    return True
        except OSError:
            continue
    return False


def next_free_index(start: int = 10) -> int:
    used = {d.index for d in v4l2.list_devices()}
    index = start
    while index in used:
        index += 1
    return index


def environment_report() -> str:
    """One paragraph describing what is and is not available, for the log panel."""
    lines = []
    if module_loaded():
        version = module_version()
        lines.append("v4l2loopback module: loaded" + (f" {version}" if version else ""))
    elif module_installed():
        lines.append("v4l2loopback module: installed but not loaded")
    else:
        lines.append("v4l2loopback module: not installed")
    lines.append(
        "device management: "
        + ("live add/remove" if supports_dynamic_devices() else "module reload")
    )
    if os.geteuid() == 0:
        lines.append("privileges: running as root")
    elif shutil.which("pkexec"):
        lines.append("privileges: pkexec")
    elif shutil.which("sudo"):
        lines.append("privileges: sudo")
    else:
        lines.append("privileges: none found - device changes will need a terminal")
    return " | ".join(lines)


# --------------------------------------------------------------------------
# Running privileged commands
# --------------------------------------------------------------------------


def _run_privileged(argv: Sequence[str], reason: str) -> Tuple[int, str]:
    """Run argv as root. Returns (returncode, combined output)."""
    printable = " ".join(shlex.quote(a) for a in argv)

    if os.geteuid() == 0:
        prefix: List[str] = []
    elif shutil.which("pkexec") and os.environ.get("DISPLAY"):
        prefix = ["pkexec"]
    elif shutil.which("sudo"):
        prefix = ["sudo", "-n"]
    else:
        raise LoopbackError(
            "Neither pkexec nor sudo is available, so " + reason + " needs a terminal.\n\n"
            "Run this yourself:\n  sudo " + printable,
            command="sudo " + printable,
        )

    try:
        result = subprocess.run(
            list(prefix) + list(argv), capture_output=True, text=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        raise LoopbackError(
            "The password prompt timed out.", command="sudo " + printable
        ) from None
    except OSError as exc:
        raise LoopbackError(str(exc), command="sudo " + printable) from exc

    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        if prefix[:1] == ["sudo"]:
            output += (
                "\n\nsudo could not prompt for a password from the app. "
                "Run this in a terminal instead:\n  sudo " + printable
            )
        raise LoopbackError(
            f"Could not {reason}.\n\n{output or 'The command gave no output.'}",
            command="sudo " + printable,
        )
    return result.returncode, output


def _sh(script: str, reason: str) -> Tuple[int, str]:
    return _run_privileged(["/bin/sh", "-c", script], reason)


def load_module(initial_devices: int = 0) -> str:
    """Load the kernel module, optionally with a first batch of cameras."""
    modprobe = shutil.which("modprobe") or "/sbin/modprobe"
    argv = [modprobe, MODULE]
    if initial_devices > 0:
        start = next_free_index()
        numbers = ",".join(str(start + i) for i in range(initial_devices))
        labels = ",".join(f"Virtual Camera {i + 1}" for i in range(initial_devices))
        ones = ",".join("1" for _ in range(initial_devices))
        argv += [
            f"devices={initial_devices}",
            f"video_nr={numbers}",
            f"card_label={labels}",
            f"exclusive_caps={ones}",
        ]
    _, out = _run_privileged(argv, "load the v4l2loopback module")
    return out


def unload_module() -> str:
    modprobe = shutil.which("modprobe") or "/sbin/modprobe"
    _, out = _run_privileged([modprobe, "-r", MODULE], "unload the v4l2loopback module")
    return out


# --------------------------------------------------------------------------
# Camera lifecycle
# --------------------------------------------------------------------------


def create_camera(
    label: str,
    index: Optional[int] = None,
    exclusive_caps: bool = True,
    buffers: int = 4,
    max_width: int = 1920,
    max_height: int = 1080,
) -> VirtualCamera:
    """Add one virtual camera and return it."""
    label = label.strip() or "Virtual Camera"
    if index is None:
        index = next_free_index()
    if any(d.index == index for d in v4l2.list_devices()):
        raise LoopbackError(f"/dev/video{index} is already taken. Pick another number.")

    if not module_loaded():
        # Loading the module with the device baked in is one prompt instead of two.
        modprobe = shutil.which("modprobe") or "/sbin/modprobe"
        _run_privileged(
            [
                modprobe,
                MODULE,
                "devices=1",
                f"video_nr={index}",
                f"card_label={label}",
                f"exclusive_caps={1 if exclusive_caps else 0}",
                f"max_buffers={buffers}",
            ],
            f"create {label}",
        )
        return _find_camera(index, label)

    if supports_dynamic_devices():
        ctl = ctl_path() or "v4l2loopback-ctl"
        argv = [
            ctl,
            "add",
            "-n",
            label,
            "-x",
            "1" if exclusive_caps else "0",
            "-b",
            str(buffers),
            "--max-width",
            str(max_width),
            "--max-height",
            str(max_height),
            f"/dev/video{index}",
        ]
        _run_privileged(argv, f"create {label}")
        return _find_camera(index, label)

    existing = list_cameras()
    wanted = existing + [VirtualCamera(f"/dev/video{index}", index, label)]
    _reload_with(wanted, exclusive_caps, buffers, f"create {label}")
    return _find_camera(index, label)


def delete_camera(camera: VirtualCamera) -> None:
    if camera.in_use:
        raise LoopbackError(
            f"{camera.label} is open in another program. Close it there first."
        )
    if supports_dynamic_devices():
        ctl = ctl_path() or "v4l2loopback-ctl"
        _run_privileged([ctl, "delete", camera.path], f"remove {camera.label}")
        return

    remaining = [c for c in list_cameras() if c.index != camera.index]
    if not remaining:
        unload_module()
        return
    _reload_with(remaining, True, 4, f"remove {camera.label}")


def rename_camera(camera: VirtualCamera, new_label: str) -> VirtualCamera:
    """The card label is fixed at creation, so this removes and re-adds the node."""
    new_label = new_label.strip()
    if not new_label:
        raise LoopbackError("Give the camera a name.")
    if new_label == camera.label:
        return camera
    if camera.in_use:
        raise LoopbackError(
            f"{camera.label} is open in another program, and renaming recreates the "
            "device. Close it there first."
        )
    delete_camera(camera)
    return create_camera(new_label, index=camera.index)


def set_fps(camera: VirtualCamera, fps: int) -> str:
    ctl = ctl_path()
    if not ctl:
        raise LoopbackError(
            "Setting the frame rate needs v4l2loopback-ctl, which is not installed."
        )
    _, out = _run_privileged(
        [ctl, "set-fps", camera.path, str(int(fps))], f"set the frame rate of {camera.label}"
    )
    return out


def set_caps(camera: VirtualCamera, caps: str) -> str:
    """caps example: 'video/x-raw,format=RGB,width=1280,height=720'."""
    ctl = ctl_path()
    if not ctl:
        raise LoopbackError(
            "Pinning the format needs v4l2loopback-ctl, which is not installed."
        )
    _, out = _run_privileged(
        [ctl, "set-caps", camera.path, caps], f"set the format of {camera.label}"
    )
    return out


def set_placeholder_image(camera: VirtualCamera, image_path: str, timeout_ms: int = 3000) -> str:
    """Show a still image whenever nothing is streaming to the camera."""
    ctl = ctl_path()
    if not ctl:
        raise LoopbackError(
            "Placeholder images need v4l2loopback-ctl, which is not installed."
        )
    _run_privileged(
        [ctl, "set-timeout-image", "-t", str(timeout_ms), camera.path, image_path],
        f"set the placeholder image for {camera.label}",
    )
    return f"Placeholder set from {os.path.basename(image_path)}"


def _find_camera(index: int, label: str) -> VirtualCamera:
    for cam in list_cameras():
        if cam.index == index:
            return cam
    # The node usually appears instantly, but udev can lag a little.
    return VirtualCamera(path=f"/dev/video{index}", index=index, label=label)


def _reload_with(
    cameras: Sequence[VirtualCamera], exclusive_caps: bool, buffers: int, reason: str
) -> None:
    busy = [c.label for c in list_cameras() if c.in_use]
    if busy:
        raise LoopbackError(
            "This system can only change virtual cameras by reloading the kernel "
            "module, and these are currently open: " + ", ".join(busy) + ".\n\n"
            "Close them, or install v4l2loopback 0.13 or newer for live changes."
        )
    modprobe = shutil.which("modprobe") or "/sbin/modprobe"
    numbers = ",".join(str(c.index) for c in cameras)
    labels = ",".join(c.label.replace(",", " ") for c in cameras)
    ones = ",".join("1" if exclusive_caps else "0" for _ in cameras)
    script = (
        "{m} -r {mod}; {m} {mod} devices={n} video_nr={nr} "
        "card_label={lb} exclusive_caps={x} max_buffers={b}"
    ).format(
        m=shlex.quote(modprobe),
        mod=MODULE,
        n=len(cameras),
        nr=shlex.quote(numbers),
        lb=shlex.quote(labels),
        x=shlex.quote(ones),
        b=buffers,
    )
    _sh(script, reason)


# --------------------------------------------------------------------------
# Persistence across reboots
# --------------------------------------------------------------------------


def persistence_snippet(cameras: Sequence[VirtualCamera]) -> str:
    numbers = ",".join(str(c.index) for c in cameras)
    labels = ",".join(c.label.replace(",", " ") for c in cameras)
    ones = ",".join("1" for _ in cameras)
    return (
        f"options {MODULE} devices={len(cameras)} video_nr={numbers} "
        f'card_label="{labels}" exclusive_caps={ones}\n'
    )


def make_persistent(cameras: Sequence[VirtualCamera]) -> str:
    """Write the current camera list where the system will read it at boot."""
    if not cameras:
        raise LoopbackError("There are no virtual cameras to save.")
    conf = persistence_snippet(cameras)
    parts = [
        "printf %s {conf} > {path}".format(
            conf=shlex.quote(conf), path=shlex.quote(MODPROBE_CONF)),
        # systemd reads /etc/modules-load.d.
        "printf %s {mod} > {load}".format(
            mod=shlex.quote(MODULE + "\n"),
            load=shlex.quote(MODULES_LOAD_CONF)),
    ]
    # OpenRC, runit, s6 and sysvinit read /etc/modules instead, and some
    # systems have both. Adding the line to whichever exists costs nothing
    # and means the cameras come back on every init system.
    parts.append(
        "if [ -f {legacy} ] && ! grep -qx {mod} {legacy}; then "
        "printf '%s\\n' {mod} >> {legacy}; fi".format(
            legacy=shlex.quote(LEGACY_MODULES_FILE),
            mod=shlex.quote(MODULE))
    )
    _sh(" && ".join(parts), "save the camera list for the next reboot")
    return f"Saved {len(cameras)} camera(s) to {MODPROBE_CONF}"


def clear_persistence() -> str:
    script = (
        f"rm -f {shlex.quote(MODPROBE_CONF)} {shlex.quote(MODULES_LOAD_CONF)}; "
        f"if [ -f {shlex.quote(LEGACY_MODULES_FILE)} ]; then "
        f"sed -i {shlex.quote('/^' + MODULE + '$/d')} "
        f"{shlex.quote(LEGACY_MODULES_FILE)}; fi"
    )
    _sh(script, "clear the saved camera list")
    return "Cleared the saved camera list. Cameras made from now on are temporary."
