"""Scale test: identical top structures on 10k vs 500k linear trunks must
produce identical query results and identical touched counts."""

import os
import tempfile
import unittest

from commitgraph import Commit, CommitGraph

TOP_BASE = 1 << 130  # top-structure ids live far above any trunk id
BATCH = 20000


def trunk_id(n):
    return f"{n:040x}"


def top_id(n):
    return f"{TOP_BASE + n:040x}"


def top_structure(base_id):
    """Criss-cross merges, a side fork and an octopus merge on top of base_id.
    Ids are identical across libraries; every timestamp is the same constant,
    so nothing can be derived from time."""
    return [
        Commit(top_id(1), (base_id,), 1000),          # x1
        Commit(top_id(2), (top_id(1),), 1000),        # x2
        Commit(top_id(3), (top_id(2),), 1000),        # x3
        Commit(top_id(4), (base_id,), 1000),          # y1
        Commit(top_id(5), (top_id(4),), 1000),        # y2
        Commit(top_id(6), (top_id(5),), 1000),        # y3
        Commit(top_id(7), (top_id(3), top_id(6)), 1000),  # m1 = merge(x3, y3)
        Commit(top_id(8), (top_id(6), top_id(3)), 1000),  # m2 = merge(y3, x3)
        Commit(top_id(9), (top_id(7),), 1000),        # tipX
        Commit(top_id(10), (top_id(8),), 1000),       # tipY
        Commit(top_id(11), (top_id(2),), 1000),       # f1 (side fork off x2)
        Commit(top_id(12), (top_id(11), top_id(5)), 1000),  # f2 = merge(f1, y2)
        Commit(top_id(13), (top_id(7), top_id(8), top_id(12), top_id(1)), 1000),  # octo
    ]


def build_library(path, trunk_len):
    graph = CommitGraph(path)
    for start in range(0, trunk_len, BATCH):
        stop = min(start + BATCH, trunk_len)
        graph.ingest(
            Commit(trunk_id(n), (trunk_id(n - 1),) if n else (), 1000)
            for n in range(start, stop)
        )
    graph.ingest(top_structure(trunk_id(trunk_len - 1)))
    return graph


class ScaleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.small = build_library(os.path.join(cls.tmp.name, "small.db"), 10_000)
        cls.large = build_library(os.path.join(cls.tmp.name, "large.db"), 500_000)

    @classmethod
    def tearDownClass(cls):
        cls.small.close()
        cls.large.close()
        cls.tmp.cleanup()

    def run_queries(self, graph):
        x2, m1, tip_x, tip_y, f1, octo = (top_id(n) for n in (2, 7, 9, 10, 11, 13))
        return {
            "bases_tips": graph.merge_bases(tip_x, tip_y),
            "bases_octo": graph.merge_bases(tip_x, octo),
            "anc_true": graph.is_ancestor(x2, tip_x),
            "anc_false": graph.is_ancestor(m1, tip_y),
            "anc_octo": graph.is_ancestor(f1, octo),
            "ab_tips": graph.ahead_behind(tip_x, tip_y),
            "ab_octo": graph.ahead_behind(tip_x, octo),
        }

    def test_results_and_touched_counts_match(self):
        small = self.run_queries(self.small)
        large = self.run_queries(self.large)
        self.assertEqual(small, large)  # results and touched counts identical
        for name, (result, touched) in small.items():
            with self.subTest(query=name):
                self.assertLess(touched, 100)  # trunk length must not leak in

    def test_expected_values(self):
        x3, y3, m1 = top_id(3), top_id(6), top_id(7)
        results = self.run_queries(self.small)
        self.assertEqual(results["bases_tips"][0], sorted([x3, y3]))  # criss-cross
        self.assertEqual(results["bases_octo"][0], [m1])
        self.assertEqual(results["anc_true"][0], True)
        self.assertEqual(results["anc_false"][0], False)
        self.assertEqual(results["anc_octo"][0], True)
        self.assertEqual(results["ab_tips"][0], (2, 2))
        self.assertEqual(results["ab_octo"][0], (1, 4))


if __name__ == "__main__":
    unittest.main()

