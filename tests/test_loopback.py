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

"""Virtual camera bookkeeping, without needing the kernel module."""

import unittest
from unittest import mock

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camaloop.core import loopback
from camaloop.core.loopback import LoopbackError, VirtualCamera


def camera(index, label="Camaloop"):
    return VirtualCamera(index=index, path=f"/dev/video{index}", label=label)


class Persistence(unittest.TestCase):
    def test_one_camera(self):
        line = loopback.persistence_snippet([camera(10, "Studio")])
        self.assertIn("options v4l2loopback", line)
        self.assertIn("devices=1", line)
        self.assertIn("video_nr=10", line)
        self.assertIn('card_label="Studio"', line)
        self.assertIn("exclusive_caps=1", line)
        self.assertTrue(line.endswith("\n"))

    def test_several_cameras(self):
        line = loopback.persistence_snippet(
            [camera(10, "One"), camera(11, "Two"), camera(12, "Three")]
        )
        self.assertIn("devices=3", line)
        self.assertIn("video_nr=10,11,12", line)
        self.assertIn('card_label="One,Two,Three"', line)
        self.assertIn("exclusive_caps=1,1,1", line)

    def test_a_comma_in_a_name_cannot_break_the_list(self):
        line = loopback.persistence_snippet([camera(10, "Work, home")])
        self.assertIn('card_label="Work  home"', line)
        self.assertEqual(line.count(","), 0)

    def test_saving_nothing_is_refused(self):
        with self.assertRaises(LoopbackError):
            loopback.make_persistent([])

    def test_the_saved_setup_reaches_every_init_system(self):
        captured = {}

        def fake_sh(script, reason):
            captured["script"] = script
            return 0, ""

        with mock.patch.object(loopback, "_sh", side_effect=fake_sh):
            loopback.make_persistent([camera(10, "Studio")])
        script = captured["script"]
        self.assertIn(loopback.MODPROBE_CONF, script)
        self.assertIn(loopback.MODULES_LOAD_CONF, script)
        self.assertIn(loopback.LEGACY_MODULES_FILE, script)

    def test_clearing_removes_every_trace(self):
        captured = {}
        with mock.patch.object(loopback, "_sh",
                               side_effect=lambda s, r: (captured.setdefault("s", s), (0, ""))[1]):
            loopback.clear_persistence()
        self.assertIn(loopback.MODPROBE_CONF, captured["s"])
        self.assertIn(loopback.LEGACY_MODULES_FILE, captured["s"])


class Describing(unittest.TestCase):
    def test_a_camera_knows_its_node(self):
        one = camera(12, "Meeting")
        self.assertEqual(one.path, "/dev/video12")
        self.assertIn("Meeting", one.label)

    def test_the_environment_report_always_returns_text(self):
        text = loopback.environment_report()
        self.assertIsInstance(text, str)
        self.assertTrue(text.strip())

    def test_listing_never_raises(self):
        self.assertIsInstance(loopback.list_cameras(), list)

    def test_the_module_checks_never_raise(self):
        for check in (loopback.module_loaded, loopback.module_installed,
                      loopback.supports_dynamic_devices):
            self.assertIn(check(), (True, False))
        self.assertIsInstance(loopback.module_version(), str)

    def test_a_free_index_is_found(self):
        index = loopback.next_free_index(10)
        self.assertGreaterEqual(index, 10)

    def test_a_free_index_skips_what_is_taken(self):
        from camaloop.core import v4l2

        taken = [v4l2.VideoDevice(path=f"/dev/video{n}", index=n)
                 for n in (0, 10, 11)]
        with mock.patch.object(loopback.v4l2, "list_devices", return_value=taken):
            self.assertEqual(loopback.next_free_index(10), 12)
            self.assertEqual(loopback.next_free_index(1), 1)


if __name__ == "__main__":
    unittest.main()
