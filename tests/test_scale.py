"""Scale test: identical top structures on trunks of very different length.

Query results and touched counts must be identical for both indexes,
proving query work depends only on the graph segment above the answer,
not on the total history length below it.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from commitgraph import CommitGraph

BATCH = 5000


def trunk_id(i: int) -> str:
    return f"{i:040x}"


def build_index(path: str, trunk_len: int) -> tuple[CommitGraph, dict[str, str]]:
    graph = CommitGraph(path)
    # Linear trunk, ingested in batches.
    for start in range(0, trunk_len, BATCH):
        batch = []
        for i in range(start, min(start + BATCH, trunk_len)):
            parents = [trunk_id(i - 1)] if i > 0 else []
            batch.append((trunk_id(i), parents, i))
        graph.ingest(batch)
    top = trunk_id(trunk_len - 1)
    # Top structure: fork + criss-cross merges, all above the trunk top.
    names = ("A1", "B1", "A2", "B2", "M1", "M2", "TIP1", "TIP2")
    ids = {name: f"f{i:039x}" for i, name in enumerate(names)}
    graph.ingest([
        (ids["A1"], [top], 1),
        (ids["B1"], [top], 2),
        (ids["A2"], [ids["A1"]], 3),
        (ids["B2"], [ids["B1"]], 4),
        (ids["M1"], [ids["A2"], ids["B2"]], 5),
        (ids["M2"], [ids["B2"], ids["A2"]], 6),
        (ids["TIP1"], [ids["M1"]], 7),
        (ids["TIP2"], [ids["M2"]], 8),
    ])
    return graph, ids


class ScaleTest(unittest.TestCase):
    def test_top_queries_ignore_trunk_length(self) -> None:
        results: dict[int, dict] = {}
        with tempfile.TemporaryDirectory() as tmp:
            for trunk_len in (10_000, 500_000):
                path = os.path.join(tmp, f"g{trunk_len}.db")
                graph, ids = build_index(path, trunk_len)
                t1, t2, a2 = ids["TIP1"], ids["TIP2"], ids["A2"]

                mb, mb_touched = graph.merge_bases(t1, t2)
                ab, ab_touched = graph.ahead_behind(t1, t2)
                anc, anc_touched = graph.is_ancestor(t1, t2)
                anc2, anc2_touched = graph.is_ancestor(a2, t2)

                results[trunk_len] = {
                    "merge_bases": (sorted(mb), mb_touched),
                    "ahead_behind": (ab, ab_touched),
                    "is_ancestor": (anc, anc_touched),
                    "is_ancestor_deep": (anc2, anc2_touched),
                }
                graph.close()

        small, large = results[10_000], results[500_000]

        # Results are as expected for the top structure.
        self.assertEqual(len(small["merge_bases"][0]), 2)  # criss-cross
        self.assertEqual(small["ahead_behind"][0], (2, 2))
        self.assertEqual(small["is_ancestor"][0], False)
        self.assertEqual(small["is_ancestor_deep"][0], True)

        # Identical results AND identical touched counts on both indexes.
        for key in ("merge_bases", "ahead_behind", "is_ancestor",
                    "is_ancestor_deep"):
            self.assertEqual(small[key], large[key], key)

        # The walk never descends into the trunk.
        for key, (_, touched) in small.items():
            self.assertLess(touched, 20, key)


if __name__ == "__main__":
    unittest.main()
