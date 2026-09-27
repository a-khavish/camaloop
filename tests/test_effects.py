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

"""Every effect, over frames of several shapes, through every branch."""

import time
import unittest

import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camaloop.core.effects import (
    EFFECTS, EFFECTS_BY_ID, GROUP_ORDER, Pipeline, test_pattern, _hex_to_bgr,
)


def usable_effects():
    return [e for e in EFFECTS if e.availability()[0]]


def variants(effect):
    """The defaults, plus one setting per choice, plus both ends of a slider."""
    base = effect.defaults()
    out = [base]
    for param in effect.params:
        if param.kind == "choice":
            for choice in param.choices:
                variant = dict(base)
                variant[param.key] = choice
                out.append(variant)
        elif param.kind in ("float", "int"):
            for value in (param.minimum, param.maximum):
                variant = dict(base)
                variant[param.key] = (
                    int(value) if param.kind == "int" else float(value)
                )
                out.append(variant)
        elif param.kind == "bool":
            for value in (True, False):
                variant = dict(base)
                variant[param.key] = value
                out.append(variant)
    return out


class Registry(unittest.TestCase):
    def test_ids_are_unique(self):
        ids = [e.id for e in EFFECTS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_names_are_unique(self):
        names = [e.name for e in EFFECTS]
        self.assertEqual(len(names), len(set(names)))

    def test_every_effect_is_described(self):
        for effect in EFFECTS:
            with self.subTest(effect=effect.id):
                self.assertTrue(effect.id)
                self.assertTrue(effect.name)
                self.assertTrue(effect.blurb)
                self.assertIn(effect.group, GROUP_ORDER)

    def test_every_parameter_is_described(self):
        kinds = {"float", "int", "bool", "choice", "color", "file", "text"}
        for effect in EFFECTS:
            for param in effect.params:
                with self.subTest(effect=effect.id, param=param.key):
                    self.assertTrue(param.label)
                    self.assertIn(param.kind, kinds)
                    if param.kind == "choice":
                        self.assertTrue(param.choices)
                        self.assertIn(param.default, param.choices)
                    if param.kind in ("float", "int"):
                        self.assertLessEqual(param.minimum, param.default)
                        self.assertLessEqual(param.default, param.maximum)
                        self.assertGreater(param.step, 0)
                    if param.kind == "color":
                        self.assertEqual(len(_hex_to_bgr(param.default)), 3)

    def test_the_groups_are_in_a_sensible_order(self):
        seen = [GROUP_ORDER.index(e.group) for e in EFFECTS]
        self.assertEqual(seen, sorted(seen),
                         "effects are applied in list order, so the groups "
                         "must not interleave")

    def test_there_are_plenty(self):
        self.assertGreaterEqual(len(EFFECTS), 30)


class EveryEffectRuns(unittest.TestCase):
    SHAPES = ((720, 1280), (360, 640), (96, 128), (64, 64))

    def test_shape_and_type_survive(self):
        for effect in usable_effects():
            for height, width in self.SHAPES:
                frame = test_pattern(width, height)
                for index, params in enumerate(variants(effect)):
                    with self.subTest(effect=effect.id, size=(width, height),
                                      variant=index):
                        out = effect.apply(frame.copy(), params,
                                           {"frame_index": index, "fps": 30})
                        self.assertIsNotNone(out)
                        self.assertEqual(out.dtype, np.uint8)
                        self.assertEqual(out.ndim, 3)
                        self.assertEqual(out.shape[2], 3)
                        self.assertTrue(out.flags["C_CONTIGUOUS"]
                                        or out.base is not None)

    def test_an_effect_never_writes_over_the_frame_it_was_given(self):
        # The preview and the recording share a frame, so an effect that
        # scribbles on its input corrupts the other one.
        allowed = {"trails"}       # this one is about the previous frame
        frame = test_pattern(320, 240)
        for effect in usable_effects():
            if effect.id in allowed:
                continue
            with self.subTest(effect=effect.id):
                given = frame.copy()
                effect.apply(given, effect.defaults(),
                             {"frame_index": 1, "fps": 30})
                np.testing.assert_array_equal(given, frame)

    def test_a_flat_frame_does_not_produce_nonsense(self):
        for effect in usable_effects():
            for value in (0, 255):
                frame = np.full((120, 160, 3), value, np.uint8)
                with self.subTest(effect=effect.id, value=value):
                    out = effect.apply(frame, effect.defaults(),
                                       {"frame_index": 2, "fps": 30})
                    self.assertFalse(np.isnan(out.astype(np.float32)).any())

    def test_nothing_is_too_slow_for_thirty_frames_a_second(self):
        """A guard against a regression, not a claim about any one machine.

        33 ms is 30 fps with a little room, and build machines are shared,
        so the number is deliberately loose. An effect that has quietly
        become several times more expensive will still trip it.
        """
        frame = test_pattern(1280, 720)
        budget_ms = 33.0
        slow = []
        for effect in usable_effects():
            params = effect.defaults()
            for i in range(3):
                effect.apply(frame.copy(), params, {"frame_index": i, "fps": 30})
            start = time.perf_counter()
            for i in range(6):
                effect.apply(frame.copy(), params, {"frame_index": i, "fps": 30})
            spent = (time.perf_counter() - start) * 1000 / 6
            if spent > budget_ms:
                slow.append(f"{effect.name}: {spent:.0f} ms")
        self.assertEqual(slow, [], "these would not keep up at 720p30")


class Colours(unittest.TestCase):
    def test_hex_reading(self):
        self.assertEqual(_hex_to_bgr("#ff0000"), (0, 0, 255))
        self.assertEqual(_hex_to_bgr("00ff00"), (0, 255, 0))
        self.assertEqual(_hex_to_bgr("#00f"), (255, 0, 0))
        self.assertEqual(_hex_to_bgr("nonsense"), (0, 0, 0))
        self.assertEqual(_hex_to_bgr(""), (0, 0, 0))
        self.assertEqual(_hex_to_bgr(None), (0, 0, 0))


class Geometry(unittest.TestCase):
    def test_output_size_really_resizes(self):
        effect = EFFECTS_BY_ID["size"] if "size" in EFFECTS_BY_ID \
            else EFFECTS_BY_ID["outsize"]
        params = effect.defaults()
        params["size"] = "1280 x 720"
        out = effect.apply(test_pattern(640, 480), params,
                           {"frame_index": 0, "fps": 30})
        self.assertEqual(out.shape[:2], (720, 1280))

    def test_rotation_swaps_the_sides(self):
        effect = EFFECTS_BY_ID["framing"]
        params = effect.defaults()
        params["rotate"] = "90°"
        params["fill"] = "Leave empty"
        out = effect.apply(test_pattern(640, 480), params,
                           {"frame_index": 0, "fps": 30})
        self.assertEqual(out.shape[:2], (640, 480))

    def test_mirroring_is_its_own_opposite(self):
        effect = EFFECTS_BY_ID["framing"]
        params = effect.defaults()
        params["flip_h"] = True
        frame = test_pattern(320, 240)
        once = effect.apply(frame.copy(), params, {"frame_index": 0, "fps": 30})
        twice = effect.apply(once.copy(), params, {"frame_index": 0, "fps": 30})
        np.testing.assert_array_equal(twice, frame)


class FaceWarpGoesTheRightWay(unittest.TestCase):
    """Bulge and Pinch are easy to get backwards, and were.

    The warp maps each point to where it should be *read from*, so the
    arithmetic runs opposite to the name: a bulge magnifies the middle,
    which means sampling from nearer the centre. Getting the sign wrong
    makes Bulge shrink the face and Pinch swell it, which is what these
    measure rather than eyeball.
    """

    def setUp(self):
        self.effect = EFFECTS_BY_ID["facewarp"]
        self.effect.__init__()
        self.frame = np.zeros((400, 400, 3), np.uint8)
        # A disc in the middle, so a change of scale can be counted.
        import cv2

        cv2.circle(self.frame, (200, 200), 60, (255, 255, 255), -1)

    def disc_area(self, image):
        return int((image[:, :, 0] > 128).sum())

    def warped(self, style, amount=0.8):
        return self.effect._warp(self.frame, (200.0, 200.0), 150.0,
                                 style, amount, 0.0)

    def test_bulge_makes_the_middle_bigger(self):
        before = self.disc_area(self.frame)
        after = self.disc_area(self.warped("Bulge"))
        self.assertGreater(after, before * 1.15,
                           f"Bulge shrank it: {before} -> {after}")

    def test_pinch_makes_the_middle_smaller(self):
        before = self.disc_area(self.frame)
        after = self.disc_area(self.warped("Pinch"))
        self.assertLess(after, before * 0.85,
                        f"Pinch swelled it: {before} -> {after}")

    def test_bulge_and_pinch_are_opposites(self):
        self.assertGreater(self.disc_area(self.warped("Bulge")),
                           self.disc_area(self.warped("Pinch")))

    def test_big_head_and_tiny_head_are_opposites(self):
        self.assertGreater(self.disc_area(self.warped("Big head")),
                           self.disc_area(self.warped("Tiny head")))

    def test_wide_spreads_sideways_more_than_down(self):
        out = self.warped("Wide")
        lit = out[:, :, 0] > 128
        rows = np.flatnonzero(lit.any(axis=1))
        cols = np.flatnonzero(lit.any(axis=0))
        self.assertGreater(len(cols), len(rows))

    def test_tall_stretches_downwards_more_than_across(self):
        out = self.warped("Tall")
        lit = out[:, :, 0] > 128
        rows = np.flatnonzero(lit.any(axis=1))
        cols = np.flatnonzero(lit.any(axis=0))
        self.assertGreater(len(rows), len(cols))

    def test_the_picture_outside_the_reach_is_untouched(self):
        out = self.warped("Bulge")
        corner = (slice(0, 20), slice(0, 20))
        np.testing.assert_array_equal(out[corner], self.frame[corner])

    def test_every_style_leaves_a_usable_frame(self):
        for style in self.effect.params[0].choices:
            with self.subTest(style=style):
                out = self.warped(style)
                self.assertEqual(out.shape, self.frame.shape)
                self.assertEqual(out.dtype, np.uint8)


class ThePipeline(unittest.TestCase):
    def test_nothing_on_leaves_the_frame_alone(self):
        pipeline = Pipeline()
        pipeline.reset()
        pipeline.set_enabled("framing", False)
        frame = test_pattern(320, 240)
        out = pipeline.process(frame.copy(), {"frame_index": 0, "fps": 30})
        np.testing.assert_array_equal(out, frame)

    def test_everything_on_still_gives_a_picture(self):
        pipeline = Pipeline()
        for effect in usable_effects():
            pipeline.set_enabled(effect.id, True)
        context = {"frame_index": 0, "fps": 30}
        out = pipeline.process(test_pattern(640, 360), context)
        self.assertEqual(out.dtype, np.uint8)
        self.assertEqual(out.ndim, 3)
        self.assertEqual(context.get("errors", []), [])

    def test_a_broken_effect_does_not_stop_the_stream(self):
        pipeline = Pipeline()
        effect = EFFECTS[0]
        pipeline.set_enabled(effect.id, True)
        original = effect.apply
        try:
            effect.apply = lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("deliberate")
            )
            context = {"frame_index": 0, "fps": 30}
            frame = test_pattern(160, 120)
            out = pipeline.process(frame.copy(), context)
            np.testing.assert_array_equal(out, frame)
            self.assertTrue(context["errors"])
        finally:
            effect.apply = original

    def test_settings_survive_a_round_trip(self):
        pipeline = Pipeline()
        pipeline.set_enabled("vignette", True)
        pipeline.set_param("vignette", "strength", 0.42)
        saved = pipeline.snapshot()
        pipeline.reset()
        self.assertFalse(pipeline.is_enabled("vignette"))
        pipeline.restore(saved)
        self.assertTrue(pipeline.is_enabled("vignette"))
        self.assertAlmostEqual(pipeline.get_params("vignette")["strength"], 0.42)
        self.assertEqual(pipeline.snapshot(), saved)

    def test_restoring_something_unknown_is_ignored(self):
        pipeline = Pipeline()
        pipeline.restore({"no-such-effect": {"enabled": True, "params": {}}})
        pipeline.restore({"vignette": {"enabled": True}})
        self.assertTrue(pipeline.is_enabled("vignette"))
        # A missing parameter falls back to the default rather than vanishing.
        self.assertIn("strength", pipeline.get_params("vignette"))

    def test_counting_what_is_on(self):
        pipeline = Pipeline()
        pipeline.reset()
        before = pipeline.active_count()
        pipeline.set_enabled("vignette", True)
        self.assertEqual(pipeline.active_count(), before + 1)


class TestPattern(unittest.TestCase):
    def test_it_is_a_picture(self):
        frame = test_pattern(640, 360)
        self.assertEqual(frame.shape, (360, 640, 3))
        self.assertEqual(frame.dtype, np.uint8)

    def test_it_moves(self):
        a = test_pattern(320, 240, t=0.0)
        b = test_pattern(320, 240, t=1.6)
        self.assertFalse(np.array_equal(a, b))


if __name__ == "__main__":
    unittest.main()
