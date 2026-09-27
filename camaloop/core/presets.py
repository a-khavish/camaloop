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

"""Saved effect setups, stored as JSON under the user's config directory."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "camaloop"
)
PRESET_DIR = os.path.join(CONFIG_DIR, "presets")


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug or "preset"


def ensure_dirs() -> None:
    os.makedirs(PRESET_DIR, exist_ok=True)


def list_presets() -> List[str]:
    ensure_dirs()
    names = []
    for entry in sorted(os.listdir(PRESET_DIR)):
        if entry.endswith(".json"):
            try:
                with open(os.path.join(PRESET_DIR, entry), "r") as fh:
                    names.append(json.load(fh).get("name", entry[:-5]))
            except (OSError, ValueError):
                continue
    return names


def _path_for(name: str) -> str:
    return os.path.join(PRESET_DIR, _slug(name) + ".json")


def save(name: str, state: Dict[str, Any]) -> str:
    ensure_dirs()
    path = _path_for(name)
    with open(path, "w") as fh:
        json.dump({"name": name, "version": 1, "effects": state}, fh, indent=2)
    return path


def load(name: str) -> Dict[str, Any]:
    with open(_path_for(name), "r") as fh:
        return json.load(fh).get("effects", {})


def delete(name: str) -> None:
    try:
        os.remove(_path_for(name))
    except OSError:
        pass


def export_to(path: str, name: str, state: Dict[str, Any]) -> None:
    with open(path, "w") as fh:
        json.dump({"name": name, "version": 1, "effects": state}, fh, indent=2)


def import_from(path: str) -> tuple:
    with open(path, "r") as fh:
        data = json.load(fh)
    return data.get("name", os.path.basename(path)), data.get("effects", {})
