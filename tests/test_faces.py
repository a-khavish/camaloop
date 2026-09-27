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

"""Finding a face, and holding it steady between detections."""

import math
import unittest

import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camaloop.core import faces                              # noqa: E402
from camaloop.core.faces import EYE_GAP_TO_FACE, Face, FaceTracker  # noqa: E402


CASCADES = faces.cascades_supported()
NO_CASCADES = ("this OpenCV has no cascade classifier, so there is no face "
               "detection here to test; OpenCV 5 removed it")


class WhereAFaceIs(unittest.TestCase):
    def test_it_records_the_basics(self):
        face = Face(centre=(100.0, 80.0), width=200.0, angle=12.0)
        self.assertEqual(face.centre, (100.0, 80.0))
        self.assertEqual(face.width, 200.0)
        self.assertEqual(face.angle, 12.0)

    def test_area_grows_with_the_square_of_the_width(self):
        self.assertEqual(Face(centre=(0, 0), width=10.0, angle=0).area, 100.0)
        self.assertEqual(Face(centre=(0, 0), width=20.0, angle=0).area, 400.0)

    def test_the_nearest_face_is_the_biggest_one(self):
        """How every effect decides which face is 'me'."""
        crowd = [Face(centre=(0, 0), width=w, angle=0) for w in (40, 210, 90)]
        self.assertEqual(max(crowd, key=lambda f: f.area).width, 210)

    def test_the_eye_gap_ratio_is_sane(self):
        self.assertGreater(EYE_GAP_TO_FACE, 0.3)
        self.assertLess(EYE_GAP_TO_FACE, 0.6)


class Smoothing(unittest.TestCase):
    def test_it_eases_rather_than_jumps(self):
        self.assertAlmostEqual(faces._smooth(0.0, 10.0, 0.5), 5.0)
        self.assertAlmostEqual(faces._smooth(0.0, 10.0, 1.0), 10.0)

    def test_the_first_reading_is_taken_as_it_is(self):
        self.assertEqual(faces._smooth(None, 10.0, 0.5), 10.0)

    def test_repeated_easing_converges(self):
        value = 0.0
        for _ in range(40):
            value = faces._smooth(value, 100.0, 0.45)
        self.assertAlmostEqual(value, 100.0, places=3)


@unittest.skipUnless(CASCADES, NO_CASCADES)
class Tracking(unittest.TestCase):
    def setUp(self):
        self.tracker = FaceTracker()

    def test_an_empty_frame_has_no_faces(self):
        found = self.tracker.detect(np.full((240, 320, 3), 60, np.uint8), 0)
        self.assertEqual(found, [])

    def test_it_finds_a_drawn_face(self):
        from tests.test_facedata import drawn_face

        found = self.tracker.detect(drawn_face(), 0)
        self.assertTrue(found, "no face was found")
        face = max(found, key=lambda f: f.area)
        self.assertGreater(face.width, 40)
        self.assertLess(abs(face.angle), 40)

    def test_the_face_is_roughly_where_it_was_drawn(self):
        from tests.test_facedata import drawn_face

        frame = drawn_face(640, 480)
        face = max(self.tracker.detect(frame, 0), key=lambda f: f.area)
        self.assertLess(abs(face.centre[0] - 320), 70)
        self.assertLess(abs(face.centre[1] - 240), 90)

    def test_it_only_looks_every_few_frames(self):
        """Detection is the expensive part, so it is not done every frame."""
        from tests.test_facedata import drawn_face

        frame = drawn_face()
        calls = []
        original = self.tracker._detect_haar
        self.tracker._detect_haar = lambda f: (calls.append(1), original(f))[1]
        for index in range(9):
            self.tracker.detect(frame, index, every=3)
        self.assertEqual(len(calls), 3)

    def test_it_keeps_reporting_between_detections(self):
        from tests.test_facedata import drawn_face

        frame = drawn_face()
        self.tracker.detect(frame, 0, every=3)
        self.assertTrue(self.tracker.detect(frame, 1, every=3))
        self.assertTrue(self.tracker.detect(frame, 2, every=3))

    def test_a_tiny_frame_does_not_raise(self):
        for shape in ((8, 8, 3), (1, 1, 3), (32, 2, 3)):
            with self.subTest(shape=shape):
                self.tracker.detect(np.zeros(shape, np.uint8), 0)


if __name__ == "__main__":
    unittest.main()
