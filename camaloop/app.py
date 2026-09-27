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

"""Entry point."""

from __future__ import annotations

import argparse
import os
import platform
import sys

from . import __version__

MISSING_TEMPLATE = """Camaloop cannot start: {name} {verb} missing.

Install everything it needs with:
    pip install -r requirements.txt

Or from your distribution's packages:
    Debian/Ubuntu  sudo apt install python3-pyqt5 python3-opencv python3-numpy
    Fedora         sudo dnf install python3-qt5 python3-opencv python3-numpy
    Arch           sudo pacman -S python-pyqt5 python-opencv python-numpy
"""


def _other_interpreter_with_deps() -> str:
    """Find another Python on this machine that does have the libraries."""
    import subprocess

    probe = "import PyQt5, cv2, numpy"
    for candidate in ("/usr/bin/python3", "/usr/local/bin/python3"):
        # Compared as paths, not with samefile: a virtualenv's python3 is a
        # symlink to the system one, yet sees entirely different packages.
        if not os.path.isfile(candidate) or candidate == sys.executable:
            continue
        try:
            result = subprocess.run(
                [candidate, "-c", probe], capture_output=True, timeout=20
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return candidate
    return ""


def _check_dependencies() -> None:
    missing = []
    for module, name in (
        ("PyQt5", "PyQt5"),
        ("cv2", "OpenCV (opencv-python)"),
        ("numpy", "NumPy"),
    ):
        try:
            __import__(module)
        except ImportError:
            missing.append(name)
    if not missing:
        return

    print(
        MISSING_TEMPLATE.format(
            name=", ".join(missing), verb="is" if len(missing) == 1 else "are"
        ),
        file=sys.stderr,
    )
    other = _other_interpreter_with_deps()
    if other:
        print(
            f"\nThey are installed for {other}, but not for the Python running\n"
            f"this script ({sys.executable}). Start it with that one instead:\n"
            f"    {other} {os.path.basename(sys.argv[0]) or 'run.py'}",
            file=sys.stderr,
        )
    raise SystemExit(1)


def _print_devices() -> None:
    from .core import loopback, v4l2

    print(loopback.environment_report())
    print()
    devices = v4l2.list_devices()
    if not devices:
        print("No video devices found.")
        print("\nIf you have made virtual cameras, check the driver is loaded:")
        print("    lsmod | grep v4l2loopback")
        return

    width = max(len(d.card or "") for d in devices) + 2
    print(f"{'DEVICE':<14}{'NAME':<{width}}{'DRIVER':<16}{'TYPE':<20}STATUS")
    blocked = []
    for dev in devices:
        if dev.accessible:
            status = "ok"
        else:
            status = f"cannot open: {dev.error or 'unknown'}"
            blocked.append(dev)
        print(
            f"{dev.path:<14}{(dev.card or '-'):<{width}}"
            f"{(dev.driver or '-'):<16}{dev.kind:<20}{status}"
        )

    virtual = [d for d in devices if d.is_loopback]
    print(f"\n{len(virtual)} virtual camera(s), {len(devices) - len(virtual)} other device(s)")
    if blocked:
        print(
            "\nSome devices could not be opened. They are still listed, because "
            "what they\nare is read from sysfs. If this is unexpected, add "
            "yourself to the video group:\n    sudo usermod -aG video $USER"
            "     then log out and back in."
        )


def _self_check() -> int:
    """Everything the diagnostic tool reports, without needing the sources."""
    import os

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    verifier = os.path.join(here, "tools", "verify_pipeline.py")
    if os.path.isfile(verifier):
        import runpy

        sys.argv = [verifier, "--report"]
        try:
            runpy.run_path(verifier, run_name="__main__")
        except SystemExit as exit_code:
            return int(exit_code.code or 0)
        return 0

    # Installed without the tools folder: report the essentials inline.
    from .core import loopback, screen

    print(f"Camaloop {__version__}")
    print(f"  {screen.session_type() or 'unknown'} session on "
          f"{screen.desktop_name() or 'unknown'}")
    print(f"  screen capture: {', '.join(screen.backends()) or 'none'}")
    print(f"  {screen.describe_support()}")
    print(f"  {loopback.environment_report()}")
    _print_devices()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="camaloop",
        description="Live camera effects and virtual camera management for Linux.",
    )
    parser.add_argument("--version", action="version", version=f"Camaloop {__version__}")
    parser.add_argument(
        "--list-devices", action="store_true", help="print the video devices and exit"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="report what works on this machine, and exit",
    )
    parser.add_argument(
        "--tray", action="store_true",
        help="start hidden in the system tray instead of showing the window",
    )
    args = parser.parse_args(argv)

    if platform.system() != "Linux":
        print(
            "Camaloop talks to Video4Linux directly, so it only runs on Linux.",
            file=sys.stderr,
        )
        return 1

    _check_dependencies()

    if args.list_devices:
        _print_devices()
        return 0

    if args.check:
        return _self_check()

    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    from .ui import theme
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Camaloop")
    app.setApplicationDisplayName("Camaloop")
    app.setDesktopFileName("camaloop")
    theme.apply(app)

    # The icon the window, the tray and the task switcher all use.
    app.setWindowIcon(theme.app_icon())

    window = MainWindow()
    hidden = args.tray or window.settings["start_minimised"]
    if hidden and window._tray is not None:
        window.hide()
    elif window.settings["lock_window"]:
        window.showMaximized()
    else:
        window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
