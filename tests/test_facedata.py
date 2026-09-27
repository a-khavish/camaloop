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

"""Finding OpenCV's face detection data, wherever it happens to live.

Every effect that looks for a face depends on two XML files that OpenCV
installs in a different place depending on how it was installed - and on
Debian and Ubuntu may not install at all, because the Python bindings and
the detection data are separate packages and nothing links them. When the
files were not found, all four face effects handed back an unchanged picture
and said nothing, which looks exactly like the effects being broken.
"""

import os
import tempfile
import unittest
from unittest import mock

import numpy as np

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2                                                    # noqa: E402

from camaloop.core import faces                               # noqa: E402
from camaloop.core.effects import EFFECTS_BY_ID, test_pattern  # noqa: E402

FACE_EFFECTS = ("faceprivacy", "facewarp")


CASCADES = faces.cascades_supported()
NO_CASCADES = ("this OpenCV has no cascade classifier, so there is no face "
               "detection here to test; OpenCV 5 removed it")


class WhatIsShippedWithTheApp(unittest.TestCase):
    def test_both_files_are_there(self):
        for name in (faces.FACE_CASCADE, faces.EYE_CASCADE):
            path = os.path.join(faces.BUNDLED_DATA, name)
            with self.subTest(file=name):
                self.assertTrue(os.path.isfile(path), f"{path} is missing")
                self.assertGreater(os.path.getsize(path), 100_000)

    @unittest.skipUnless(CASCADES, NO_CASCADES)
    def test_they_load(self):
        for name in (faces.FACE_CASCADE, faces.EYE_CASCADE):
            path = os.path.join(faces.BUNDLED_DATA, name)
            with self.subTest(file=name):
                self.assertFalse(cv2.CascadeClassifier(path).empty())

    def test_they_keep_their_licence_notice(self):
        """They are redistributable as long as the notice travels with them."""
        for name in (faces.FACE_CASCADE, faces.EYE_CASCADE):
            head = Path(faces.BUNDLED_DATA, name).read_text(
                errors="replace")[:4000]
            with self.subTest(file=name):
                self.assertIn("License Agreement", head)
                self.assertIn("Redistribution", head)


class Searching(unittest.TestCase):
    def test_it_finds_the_data_on_this_machine(self):
        self.assertTrue(faces.cascade_file(faces.FACE_CASCADE))
        self.assertTrue(faces.cascade_file(faces.EYE_CASCADE))

    def test_the_places_it_looks_cover_the_distributions(self):
        places = " ".join(faces._cascade_directories())
        for expected in ("/usr/share/opencv4/haarcascades",
                         "/usr/share/opencv/haarcascades",
                         faces.BUNDLED_DATA):
            self.assertIn(expected, places)

    def test_an_override_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            name = "haarcascade_frontalface_default.xml"
            Path(folder, name).write_text("not really a cascade")
            with mock.patch.dict(os.environ, {"CAMALOOP_CASCADES": folder}):
                self.assertEqual(faces.cascade_file(name),
                                 os.path.join(folder, name))

    @unittest.skipUnless(CASCADES, NO_CASCADES)
    def test_a_file_that_will_not_load_counts_as_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            name = "haarcascade_frontalface_default.xml"
            Path(folder, name).write_text("<opencv_storage></opencv_storage>")
            with mock.patch.object(faces, "_cascade_directories",
                                   return_value=[folder]):
                self.assertIsNone(faces.load_cascade(name))

    @unittest.skipUnless(CASCADES, NO_CASCADES)
    def test_the_shipped_copy_is_used_when_the_system_has_none(self):
        """The situation on a Debian or Ubuntu box without opencv-data."""
        with mock.patch.object(faces, "_cascade_directories",
                               return_value=["/usr/share/opencv4/haarcascades",
                                             faces.BUNDLED_DATA]):
            found = faces.cascade_file(faces.FACE_CASCADE)
            self.assertTrue(found.startswith(faces.BUNDLED_DATA))
            self.assertIsNotNone(faces.load_cascade(faces.FACE_CASCADE))


# Taking the data away only reaches the "no data" message on an OpenCV
# that still has the classifier. Without one, the earlier and more
# accurate refusal wins, which TheReasonIsNeverBlank covers instead.
@unittest.skipUnless(CASCADES, NO_CASCADES)
class WhenThereIsNoDataAnywhere(unittest.TestCase):
    """The failure has to be visible, not silent."""

    def setUp(self):
        self.empty = tempfile.mkdtemp()
        self.patch = mock.patch.object(
            faces, "_cascade_directories", return_value=[self.empty])
        self.patch.start()
        for eid in FACE_EFFECTS:
            EFFECTS_BY_ID[eid].__init__()

    def tearDown(self):
        self.patch.stop()
        os.rmdir(self.empty)
        for eid in FACE_EFFECTS:
            EFFECTS_BY_ID[eid].__init__()

    def test_the_tracker_finds_nothing_and_does_not_raise(self):
        tracker = faces.FaceTracker()
        self.assertEqual(tracker.detect(test_pattern(320, 240), 0), [])

    def test_every_face_effect_says_it_cannot_work(self):
        for eid in FACE_EFFECTS:
            effect = EFFECTS_BY_ID[eid]
            usable, reason = effect.availability()
            with self.subTest(effect=effect.name):
                self.assertFalse(usable, "it claims to work with no data")
                self.assertIn("face detection data", reason)

    def test_the_reason_names_the_command_to_run(self):
        _usable, reason = EFFECTS_BY_ID["facewarp"].availability()
        self.assertIn("apt install opencv-data", reason)
        self.assertIn("dnf install opencv-data", reason)
        self.assertIn("pip install", reason)

    def test_the_pipeline_skips_them_rather_than_running_them(self):
        from camaloop.core.effects import Pipeline

        pipeline = Pipeline()
        pipeline.reset()
        pipeline.set_enabled("framing", False)   # on by default, and it mirrors
        for eid in FACE_EFFECTS:
            pipeline.set_enabled(eid, True)
        frame = test_pattern(320, 240)
        out = pipeline.process(frame.copy(), {"frame_index": 0, "fps": 30})
        np.testing.assert_array_equal(out, frame)

    def test_a_cascade_that_exists_but_will_not_load_is_reported(self):
        """Availability passes, the load fails - the effect must still speak."""
        effect = EFFECTS_BY_ID["faceprivacy"]
        effect.__init__()
        with mock.patch.object(faces, "cascade_file", return_value="/no/such"):
            context = {"frame_index": 0, "fps": 30}
            frame = test_pattern(320, 240)
            out = effect.apply(frame.copy(), effect.defaults(), context)
            np.testing.assert_array_equal(out, frame)
            self.assertTrue(context.get("errors"),
                            "it failed without saying anything")


@unittest.skipUnless(CASCADES, NO_CASCADES)
class WithTheDataPresent(unittest.TestCase):
    def test_every_face_effect_is_available(self):
        for eid in FACE_EFFECTS:
            effect = EFFECTS_BY_ID[eid]
            effect.__init__()
            usable, reason = effect.availability()
            with self.subTest(effect=effect.name):
                self.assertTrue(usable, reason)

    def test_a_face_is_actually_found(self):
        """A drawn face, checked through the same path the app uses."""
        frame = drawn_face()
        tracker = faces.FaceTracker()
        self.assertTrue(tracker.detect(frame, 0), "no face was found")

    def test_the_face_effects_change_a_frame_with_a_face_in_it(self):
        frame = drawn_face()
        for eid in FACE_EFFECTS:
            effect = EFFECTS_BY_ID[eid]
            effect.__init__()
            params = effect.defaults()
            out = frame
            for index in range(3):
                out = effect.apply(frame.copy(), params,
                                   {"frame_index": index, "fps": 30})
            with self.subTest(effect=effect.name):
                self.assertFalse(
                    np.array_equal(out, frame),
                    f"{effect.name} left the picture untouched")


def drawn_face(width=640, height=480):
    """A face with the light and dark areas a Haar detector keys on."""
    img = np.zeros((height, width, 3), np.uint8)
    img[:] = (58, 52, 48)
    cx, cy = width // 2, int(height * 0.50)
    fw, fh = int(width * 0.17), int(height * 0.28)
    cv2.ellipse(img, (cx, cy), (fw, fh), 0, 0, 360, (158, 186, 214), -1, cv2.LINE_AA)
    cv2.ellipse(img, (cx, int(cy - fh * 0.42)), (int(fw * 0.8), int(fh * 0.3)),
                0, 0, 360, (178, 204, 230), -1, cv2.LINE_AA)
    for side in (-1, 1):
        cv2.ellipse(img, (cx + side * int(fw * 0.52), int(cy + fh * 0.16)),
                    (int(fw * 0.4), int(fh * 0.26)), 0, 0, 360,
                    (172, 198, 226), -1, cv2.LINE_AA)
    eye_y = int(cy - fh * 0.14)
    for side in (-1, 1):
        ex = cx + side * int(fw * 0.44)
        cv2.ellipse(img, (ex, eye_y), (int(fw * 0.34), int(fh * 0.13)),
                    0, 0, 360, (96, 118, 146), -1, cv2.LINE_AA)
    cv2.ellipse(img, (cx, eye_y), (int(fw * 0.13), int(fh * 0.14)),
                0, 0, 360, (174, 200, 228), -1, cv2.LINE_AA)
    for side in (-1, 1):
        ex = cx + side * int(fw * 0.44)
        cv2.ellipse(img, (ex, eye_y), (int(fw * 0.22), int(fh * 0.085)),
                    0, 0, 360, (238, 242, 246), -1, cv2.LINE_AA)
        cv2.circle(img, (ex, eye_y), int(fw * 0.105), (58, 78, 104), -1, cv2.LINE_AA)
        cv2.circle(img, (ex, eye_y), int(fw * 0.048), (16, 16, 18), -1, cv2.LINE_AA)
        cv2.ellipse(img, (ex, eye_y - int(fh * 0.17)), (int(fw * 0.28), int(fh * 0.075)),
                    0, 180, 360, (52, 60, 78), int(fh * 0.035), cv2.LINE_AA)
    cv2.ellipse(img, (cx, int(cy + fh * 0.22)), (int(fw * 0.2), int(fh * 0.13)),
                0, 200, 340, (126, 152, 182), int(fh * 0.018), cv2.LINE_AA)
    cv2.ellipse(img, (cx, int(cy + fh * 0.48)), (int(fw * 0.34), int(fh * 0.1)),
                0, 10, 170, (96, 96, 140), int(fh * 0.024), cv2.LINE_AA)
    cv2.ellipse(img, (cx, int(cy - fh * 0.58)), (int(fw * 1.06), int(fh * 0.52)),
                0, 180, 360, (44, 48, 62), -1, cv2.LINE_AA)
    return cv2.GaussianBlur(img, (5, 5), 0)


class TheReasonIsNeverBlank(unittest.TestCase):
    """Saying no without saying why is the bug this guards against.

    An unavailable face effect puts face_detection_ready()'s reason straight
    into the activity log. When that reason is empty the log gets a blank
    line and the effect looks broken rather than unavailable, which is how
    OpenCV 5 first showed up here: the shipped data file was present, so the
    check said yes, while the classifier it needed had been removed.
    """

    def test_saying_no_always_comes_with_a_reason(self):
        usable, why = faces.face_detection_ready()
        if not usable:
            self.assertTrue(why.strip(), "refused without saying why")

    def test_a_missing_classifier_is_refused_and_explained(self):
        with mock.patch.object(faces, "cascades_supported", return_value=False):
            usable, why = faces.face_detection_ready()
        self.assertFalse(usable)
        self.assertTrue(why.strip())
        self.assertIn("opencv-python", why)

    def test_every_face_effect_explains_itself_without_a_classifier(self):
        with mock.patch.object(faces, "cascades_supported", return_value=False):
            for effect in EFFECTS_BY_ID.values():
                usable, why = effect.availability()
                if not usable:
                    with self.subTest(effect=effect.name):
                        self.assertTrue(why.strip(),
                                        f"{effect.name} refused silently")

    def test_nothing_is_detected_rather_than_raising(self):
        with mock.patch.object(faces, "cascades_supported", return_value=False):
            tracker = faces.FaceTracker()
            frame = np.zeros((240, 320, 3), np.uint8)
            self.assertEqual(tracker.detect(frame, 0), [])


if __name__ == "__main__":
    unittest.main()
