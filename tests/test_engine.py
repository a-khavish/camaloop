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

"""The capture thread, driven with the built-in test pattern."""

import os
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np                                          # noqa: E402
from PyQt5.QtWidgets import QApplication                    # noqa: E402

from camaloop.core.effects import Pipeline                  # noqa: E402
from camaloop.core.engine import Engine, SourceSettings     # noqa: E402

APP = None


def setUpModule():
    global APP
    # A full QApplication, not a QCoreApplication: another test module in the
    # same run needs widgets, and only one application object may exist.
    APP = QApplication.instance() or QApplication(["camaloop-tests"])


class RunningTheEngine(unittest.TestCase):
    def setUp(self):
        self.pipeline = Pipeline()
        self.engine = Engine(self.pipeline)
        self.frames = []
        self.notices = []
        self.failures = []
        self.engine.frame_ready.connect(self.frames.append)
        self.engine.notice.connect(self.notices.append)
        self.engine.failed.connect(self.failures.append)

    def tearDown(self):
        self.engine.stop()
        self.engine.wait(4000)

    def run_for(self, seconds=1.2, until=None):
        self.engine.configure(SourceSettings(kind="pattern", width=320,
                                              height=240, fps=30))
        self.engine.start()
        deadline = time.time() + seconds
        while time.time() < deadline:
            APP.processEvents()
            if until is not None and until():
                break
            time.sleep(0.02)
        APP.processEvents()

    def test_it_produces_frames(self):
        self.run_for(until=lambda: len(self.frames) >= 5)
        self.assertGreaterEqual(len(self.frames), 5)
        self.assertEqual(self.failures, [])

    def test_the_frames_are_the_right_shape(self):
        self.run_for(until=lambda: len(self.frames) >= 3)
        frame = self.frames[-1]
        self.assertEqual(frame.dtype, np.uint8)
        self.assertEqual(frame.shape, (240, 320, 3))

    def test_an_effect_changes_what_comes_out(self):
        self.pipeline.set_enabled("look", True)
        self.pipeline.set_param("look", "style", "Inverted")
        self.run_for(until=lambda: len(self.frames) >= 4)
        inverted = self.frames[-1]
        self.pipeline.set_enabled("look", False)
        before = len(self.frames)
        deadline = time.time() + 1.0
        while time.time() < deadline and len(self.frames) < before + 4:
            APP.processEvents()
            time.sleep(0.02)
        self.assertFalse(np.array_equal(inverted, self.frames[-1]))

    def test_stopping_is_clean(self):
        self.run_for(until=lambda: len(self.frames) >= 3)
        self.engine.stop()
        self.assertTrue(self.engine.wait(4000))
        self.assertFalse(self.engine.isRunning())

    def test_it_reports_the_rate(self):
        stats = []
        self.engine.stats_ready.connect(stats.append)
        self.run_for(seconds=2.0, until=lambda: len(stats) >= 2)
        self.assertTrue(stats)
        self.assertIn("fps", stats[-1])
        self.assertGreater(stats[-1]["fps"], 0)

    def test_a_still_is_written(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = []
            self.engine.snapshot_saved.connect(saved.append)
            self.run_for(until=lambda: len(self.frames) >= 3)
            self.engine.take_snapshot(os.path.join(folder, "shot.png"))
            deadline = time.time() + 3.0
            while time.time() < deadline and not saved:
                APP.processEvents()
                time.sleep(0.02)
            self.assertTrue(saved, "no photo was saved")
            self.assertTrue(os.path.isfile(saved[0]))
            self.assertGreater(os.path.getsize(saved[0]), 500)

    def test_recording_writes_a_file(self):
        with tempfile.TemporaryDirectory() as folder:
            changes = []
            self.engine.recording_changed.connect(
                lambda on, path: changes.append((on, path))
            )
            self.run_for(until=lambda: len(self.frames) >= 3)
            target = os.path.join(folder, "clip.mp4")
            self.engine.start_recording(target)
            deadline = time.time() + 2.5
            while time.time() < deadline and not changes:
                APP.processEvents()
                time.sleep(0.02)
            self.assertTrue(changes and changes[0][0], "recording never began")
            time.sleep(0.8)
            APP.processEvents()
            self.engine.stop_recording()
            deadline = time.time() + 3.0
            while time.time() < deadline and len(changes) < 2:
                APP.processEvents()
                time.sleep(0.02)
            written = changes[0][1]
            self.assertTrue(os.path.isfile(written), written)
            self.assertGreater(os.path.getsize(written), 1000)


class Sources(unittest.TestCase):
    def test_the_defaults_are_sensible(self):
        source = SourceSettings()
        self.assertEqual(source.kind, "camera")
        self.assertGreater(source.width, 0)
        self.assertGreater(source.height, 0)
        self.assertGreater(source.fps, 0)

    def test_a_missing_file_is_reported_rather_than_crashing(self):
        pipeline = Pipeline()
        engine = Engine(pipeline)
        failures = []
        engine.failed.connect(failures.append)
        engine.configure(SourceSettings(kind="image",
                                         device="/nowhere/at/all.png"))
        engine.start()
        deadline = time.time() + 3.0
        while time.time() < deadline and not failures:
            APP.processEvents()
            time.sleep(0.02)
        engine.stop()
        engine.wait(3000)
        self.assertTrue(failures, "a missing file should be reported")


if __name__ == "__main__":
    unittest.main()
