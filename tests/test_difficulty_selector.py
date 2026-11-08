"""Owned-draw factorized selection; no model or game dependencies."""

import math
import unittest


class DifficultySelectorTests(unittest.TestCase):
    def test_served_hard_is_group_first_not_joint_argmax_and_consumes_zero_draws(self):
        from shared_player.difficulty import select_temperature

        class NoDraw:
            def random(self):
                raise AssertionError("Hard zero must consume no RNG")

        result = select_temperature(
            [0.1, 0], [0, 0, 20], [0, 0, 1], temperature=0, rng=NoDraw()
        )
        self.assertLess(
            math.exp(0.1) / (math.exp(0.1) + 1) / 2, 1 / (math.exp(0.1) + 1)
        )
        self.assertEqual(result["chosen_action"], 0)
        self.assertEqual(result["draws_consumed"], 0)
        self.assertEqual(result["selection_rule"], "served-group-first-v1")

    def test_nonzero_temperature_records_exact_group_times_conditional_law(self):
        from shared_player.difficulty import select_temperature

        class Draws:
            def __init__(self):
                self.draws = iter([0.8, 0.6])

            def random(self):
                return next(self.draws)

        row = select_temperature(
            [0, 0], [0, 0, 0], [0, 0, 1], temperature=1, rng=Draws()
        )
        self.assertEqual(row["group_probabilities"], [0.5, 0.5])
        self.assertEqual(row["joint_probabilities"], [0.25, 0.25, 0.5])
        self.assertEqual(row["chosen_action"], 2)
        self.assertEqual(row["draws_consumed"], 2)
        self.assertEqual(row["action_rng_draws"], [0.8, 0.6])

    def test_owned_rng_replay_and_domain_rejection(self):
        from shared_player.difficulty import OwnedActionRng, select_temperature

        one, two = OwnedActionRng(7), OwnedActionRng(7)
        a = select_temperature([0, 1], [0, 1, 0], [0, 0, 1], temperature=0.25, rng=one)
        b = select_temperature([0, 1], [0, 1, 0], [0, 0, 1], temperature=0.25, rng=two)
        self.assertEqual(a, b)
        self.assertEqual(one.cursor, 2)
        for seed in (True, -1, 4294967296):
            with self.assertRaises(ValueError):
                OwnedActionRng(seed)

    def test_invalid_support_temperature_or_uniforms_cannot_produce_a_choice(self):
        from shared_player.difficulty import select_temperature

        class BadDraw:
            def random(self):
                return 1

        for groups, local, ids, temp in (
            ([0], [0], [1], 1),
            ([0, 0], [0], [0], 1),
            ([0], [float("nan")], [0], 1),
            ([0], [0], [0], True),
            ([0], [0], [0], float("inf")),
        ):
            with self.assertRaises(ValueError):
                select_temperature(groups, local, ids, temperature=temp, rng=BadDraw())
        with self.assertRaises(ValueError):
            select_temperature([0], [0], [0], temperature=1, rng=BadDraw())


if __name__ == "__main__":
    unittest.main()
