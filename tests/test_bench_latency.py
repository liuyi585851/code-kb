import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codekb.bench_latency import _percentile


class PercentileTests(unittest.TestCase):
    def test_percentile_interpolation(self):
        values = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(_percentile(values, 0), 1.0)
        self.assertEqual(_percentile(values, 100), 4.0)
        self.assertAlmostEqual(_percentile(values, 50), 2.5)

    def test_percentile_edge_cases(self):
        self.assertEqual(_percentile([], 50), 0.0)
        self.assertEqual(_percentile([7.0], 95), 7.0)


if __name__ == "__main__":
    unittest.main()
