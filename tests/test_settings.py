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

"""Settings, presets and starting with the session."""

import importlib
import json
import os
import tempfile
import unittest
from unittest import mock

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class InATemporaryHome(unittest.TestCase):
    """Each test gets its own config directory, so nothing real is touched."""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.patch = mock.patch.dict(
            os.environ, {"XDG_CONFIG_HOME": self.home.name}
        )
        self.patch.start()
        from camaloop.core import settings as module

        self.module = importlib.reload(module)

    def tearDown(self):
        self.patch.stop()
        self.home.cleanup()


class Storing(InATemporaryHome):
    def test_defaults_come_back_when_nothing_is_saved(self):
        store = self.module.Settings()
        self.assertEqual(store["close_action"], "ask")
        self.assertTrue(store["lock_window"])

    def test_a_change_is_written_straight_away(self):
        store = self.module.Settings()
        store["close_action"] = "tray"
        with open(self.module.SETTINGS_FILE) as fh:
            self.assertEqual(json.load(fh)["close_action"], "tray")

    def test_a_change_survives_a_restart(self):
        self.module.Settings()["capture_folder"] = "/tmp/shots"
        self.assertEqual(self.module.Settings()["capture_folder"], "/tmp/shots")

    def test_effect_settings_round_trip(self):
        store = self.module.Settings()
        store["last_effects"] = {"vignette": {"enabled": True,
                                              "params": {"strength": 0.4}}}
        again = self.module.Settings()["last_effects"]
        self.assertTrue(again["vignette"]["enabled"])
        self.assertAlmostEqual(again["vignette"]["params"]["strength"], 0.4)

    def test_a_damaged_file_falls_back_to_the_defaults(self):
        os.makedirs(self.module.CONFIG_DIR, exist_ok=True)
        with open(self.module.SETTINGS_FILE, "w") as fh:
            fh.write("{ this is not json")
        self.assertEqual(self.module.Settings()["close_action"], "ask")

    def test_unknown_keys_are_dropped(self):
        os.makedirs(self.module.CONFIG_DIR, exist_ok=True)
        with open(self.module.SETTINGS_FILE, "w") as fh:
            json.dump({"close_action": "quit", "nonsense": 1}, fh)
        store = self.module.Settings()
        self.assertEqual(store["close_action"], "quit")
        self.assertIsNone(store.get("nonsense"))

    def test_an_unknown_key_reads_as_nothing(self):
        self.assertIsNone(self.module.Settings().get("no-such-setting"))


class StartingWithTheSession(InATemporaryHome):
    def test_turning_it_on_writes_a_desktop_entry(self):
        self.module.Settings().apply_autostart(True)
        self.assertTrue(os.path.isfile(self.module.AUTOSTART_FILE))
        with open(self.module.AUTOSTART_FILE) as fh:
            text = fh.read()
        self.assertIn("[Desktop Entry]", text)
        self.assertIn("Exec=", text)
        self.assertIn("Camaloop", text)

    def test_turning_it_off_removes_it(self):
        store = self.module.Settings()
        store.apply_autostart(True)
        self.assertTrue(store.autostart_active())
        store.apply_autostart(False)
        self.assertFalse(os.path.exists(self.module.AUTOSTART_FILE))
        self.assertFalse(store.autostart_active())

    def test_turning_it_off_twice_is_harmless(self):
        store = self.module.Settings()
        self.assertIn("not", store.apply_autostart(False).lower())
        store.apply_autostart(False)

    def test_starting_hidden_is_passed_to_the_entry(self):
        store = self.module.Settings()
        store["start_minimised"] = True
        store.apply_autostart(True)
        with open(self.module.AUTOSTART_FILE) as fh:
            self.assertIn("--tray", fh.read())

    def test_there_is_always_a_command_to_relaunch_with(self):
        self.assertTrue(self.module.Settings.launcher_command())


class Presets(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.patch = mock.patch.dict(
            os.environ, {"XDG_CONFIG_HOME": self.home.name}
        )
        self.patch.start()
        from camaloop.core import presets as module

        self.presets = importlib.reload(module)

    def tearDown(self):
        self.patch.stop()
        self.home.cleanup()

    def test_nothing_saved_means_an_empty_list(self):
        self.assertEqual(self.presets.list_presets(), [])

    def test_save_then_load(self):
        data = {"vignette": {"enabled": True, "params": {"strength": 0.6}}}
        self.presets.save("Evening look", data)
        self.assertIn("Evening look", self.presets.list_presets())
        self.assertEqual(self.presets.load("Evening look"), data)

    def test_saving_again_replaces_it(self):
        self.presets.save("One", {"a": 1})
        self.presets.save("One", {"a": 2})
        self.assertEqual(self.presets.list_presets().count("One"), 1)
        self.assertEqual(self.presets.load("One"), {"a": 2})

    def test_deleting(self):
        self.presets.save("Gone soon", {"a": 1})
        self.presets.delete("Gone soon")
        self.assertNotIn("Gone soon", self.presets.list_presets())

    def test_awkward_names_still_work(self):
        for name in ("Café / night", "  spaces  ", "100% bright", "a+b"):
            self.presets.save(name, {"n": name})
            self.assertEqual(self.presets.load(name), {"n": name})

    def test_loading_something_that_is_not_there(self):
        with self.assertRaises(OSError):
            self.presets.load("never existed")

    def test_export_and_import(self):
        data = {"glitch": {"enabled": True, "params": {}}}
        path = os.path.join(self.home.name, "shared.json")
        self.presets.export_to(path, "Shared", data)
        name, restored = self.presets.import_from(path)
        self.assertEqual(name, "Shared")
        self.assertEqual(restored, data)

    def test_two_names_that_slug_alike_do_not_collide(self):
        self.presets.save("Night look", {"a": 1})
        self.presets.save("night-look", {"a": 2})
        # They share a file name, so the second wins and only one is listed.
        self.assertEqual(len(self.presets.list_presets()), 1)


if __name__ == "__main__":
    unittest.main()
