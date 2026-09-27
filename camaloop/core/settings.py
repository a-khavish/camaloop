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

"""What the app remembers between runs, and starting it with the session.

Kept as plain JSON so it can be read, edited or deleted by hand.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Any, Dict

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "camaloop"
)
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")

AUTOSTART_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "autostart"
)
AUTOSTART_FILE = os.path.join(AUTOSTART_DIR, "camaloop.desktop")

DEFAULTS: Dict[str, Any] = {
    "close_action": "ask",        # ask | tray | quit
    "tray_icon": True,            # keep an icon in the system tray
    "start_minimised": False,     # start hidden in the tray
    "autostart": False,           # start when you log in
    "lock_window": True,          # stay maximised and refuse to be resized
    "remember_effects": True,     # reload the last effect settings on start
    "last_effects": {},           # the effect settings themselves
    "last_source": "",            # the source that was in use
    "capture_folder": "",         # empty means Videos, or home
}


def _read() -> Dict[str, Any]:
    try:
        with open(SETTINGS_FILE, "r") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        stored = {}
    merged = dict(DEFAULTS)
    if isinstance(stored, dict):
        merged.update({k: v for k, v in stored.items() if k in DEFAULTS})
    return merged


class Settings:
    """A small settings store that writes on every change."""

    def __init__(self):
        self._values = _read()

    def __getitem__(self, key: str) -> Any:
        return self._values.get(key, DEFAULTS.get(key))

    def get(self, key: str, fallback: Any = None) -> Any:
        return self._values.get(key, DEFAULTS.get(key, fallback))

    def __setitem__(self, key: str, value: Any) -> None:
        self._values[key] = value
        self.save()

    def update(self, **pairs: Any) -> None:
        self._values.update(pairs)
        self.save()

    def save(self) -> None:
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            temporary = SETTINGS_FILE + ".new"
            with open(temporary, "w") as fh:
                json.dump(self._values, fh, indent=2)
            os.replace(temporary, SETTINGS_FILE)
        except OSError:
            pass          # never let a settings problem stop the app

    def reset(self) -> None:
        self._values = dict(DEFAULTS)
        self.save()

    # -- starting with the session ---------------------------------------

    @staticmethod
    def launcher_command() -> str:
        """However the app was started, the command that repeats it."""
        installed = shutil.which("camaloop")
        if installed:
            return installed
        script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
        if script.endswith(".py") and os.path.isfile(script):
            return f'"{sys.executable}" "{script}"'
        return sys.executable + " -m camaloop"

    def apply_autostart(self, enabled: bool) -> str:
        """Add or remove the desktop entry that starts the app at login."""
        if not enabled:
            try:
                os.remove(AUTOSTART_FILE)
                return "Camaloop will not start automatically any more."
            except FileNotFoundError:
                return "Camaloop was not set to start automatically."
            except OSError as exc:
                return f"Could not change it: {exc}"

        command = self.launcher_command()
        if self["start_minimised"]:
            command += " --tray"
        entry = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Camaloop\n"
            "Comment=Live camera effects and virtual cameras\n"
            f"Exec={command}\n"
            "Icon=camaloop\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        try:
            os.makedirs(AUTOSTART_DIR, exist_ok=True)
            with open(AUTOSTART_FILE, "w") as fh:
                fh.write(entry)
            return f"Camaloop will start when you log in ({AUTOSTART_FILE})."
        except OSError as exc:
            return f"Could not write the autostart entry: {exc}"

    @staticmethod
    def autostart_active() -> bool:
        return os.path.isfile(AUTOSTART_FILE)
