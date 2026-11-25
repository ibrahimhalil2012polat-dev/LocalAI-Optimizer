import unittest
from adaptive_search import finalists


class AdaptiveTests(unittest.TestCase):
    def records(self, values):
        return [dict(candidate=c, speed=s, output_hash="same", tokens=128)
                for c, speeds in values.items() for s in speeds]

    def select(self, values):
        return finalists(list(values), self.records(values), "candidate", "speed", 0)

    def test_clear_loser_and_baseline(self):
        self.assertEqual(self.select({0: [5, 5], 1: [10, 10], 2: [6, 6]}), [0, 1])

    def test_close_candidates(self):
        self.assertEqual(self.select({0: [9, 9], 1: [10, 10], 2: [9, 9]}), [0, 1, 2])

    def test_noisy_falls_back(self):
        self.assertEqual(self.select({0: [9, 9], 1: [10, 6], 2: [2, 2]}), [0, 1, 2])

    def test_mismatch_falls_back(self):
        rows = self.records({0: [10, 10], 1: [2, 2]})
        rows[0]["output_hash"] = "different"
        self.assertEqual(finalists([0, 1], rows, "candidate", "speed", 0), [0, 1])

    def test_incomplete_falls_back(self):
        self.assertEqual(finalists([0, 1], [], "candidate", "speed", 0), [0, 1])

    def test_default_none_baseline(self):
        self.assertEqual(finalists([None, 4, 8], self.records({None: [3, 3], 4: [2, 2], 8: [9, 9]}), "candidate", "speed", None), [None, 8])

    def test_invalid_metric_falls_back(self):
        self.assertEqual(self.select({0: [10, 10], 1: [float('nan'), 2]}), [0, 1])


if __name__ == "__main__":
    unittest.main()
