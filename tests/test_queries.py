"""Cross-check queries against a naive full-ancestor-set reference."""

from __future__ import annotations

import os
import random
import tempfile
import unittest

from commitgraph import CommitGraph
from tests.naive import NaiveGraph


def make_id(i: int) -> str:
    return f"{i:040x}"


def random_graph(rng: random.Random, size: int) -> list[tuple[str, list[str], int]]:
    """Random DAG with roots, merges, octopus merges and criss-crosses."""
    commits: list[tuple[str, list[str], int]] = []
    for i in range(size):
        cid = make_id(i)
        if i == 0 or rng.random() < 0.08:
            parents: list[str] = []  # extra roots
        else:
            n_parents = rng.choices([1, 2, 3, 4], weights=[70, 20, 6, 4])[0]
            n_parents = min(n_parents, i)
            pool = list(range(i))
            rng.shuffle(pool)
            parents = [make_id(p) for p in pool[:n_parents]]
        commits.append((cid, parents, rng.randrange(0, 10**9)))
    # Sprinkle explicit criss-cross structures on top.
    for _ in range(max(1, size // 25)):
        if len(commits) < 6:
            break
        i = len(commits)
        x, y = rng.randrange(i), rng.randrange(i)
        m = make_id(i)
        n = make_id(i + 1)
        commits.append((m, [make_id(x), make_id(y)], rng.randrange(0, 10**9)))
        commits.append((n, [make_id(y), make_id(x)], rng.randrange(0, 10**9)))
    return commits


def shuffled_batches(
    rng: random.Random, commits: list[tuple[str, list[str], int]]
) -> list[list[tuple[str, list[str], int]]]:
    """Split a topo-ordered list into random chunks, shuffled internally."""
    n_batches = rng.randrange(1, max(2, min(8, len(commits))))
    cuts = sorted(rng.sample(range(1, len(commits)), n_batches - 1)) if len(commits) > 1 else []
    batches, prev = [], 0
    for cut in cuts + [len(commits)]:
        batch = commits[prev:cut]
        rng.shuffle(batch)
        batches.append(batch)
        prev = cut
    return batches


class CrossCheckTest(unittest.TestCase):
    def run_one_graph(self, seed: int, size: int) -> None:
        rng = random.Random(seed)
        commits = random_graph(rng, size)
        naive = NaiveGraph()
        for cid, parents, _ in commits:
            naive.add(cid, parents)

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "graph.db")
            graph = CommitGraph(path)
            for batch in shuffled_batches(rng, commits):
                graph.ingest(batch)

            ids = [c[0] for c in commits]
            for _ in range(60):
                a = rng.choice(ids)
                b = rng.choice(ids)

                bases, _ = graph.merge_bases(a, b)
                self.assertEqual(
                    set(bases),
                    naive.merge_bases(a, b),
                    f"merge_bases({a}, {b}) seed={seed}",
                )

                got, _ = graph.is_ancestor(a, b)
                self.assertEqual(
                    got, naive.is_ancestor(a, b), f"is_ancestor({a}, {b}) seed={seed}"
                )

                counts, _ = graph.ahead_behind(a, b)
                self.assertEqual(
                    counts,
                    naive.ahead_behind(a, b),
                    f"ahead_behind({a}, {b}) seed={seed}",
                )
            graph.close()

            # Persistence: reopen and re-verify a sample of queries.
            graph = CommitGraph(path)
            for _ in range(20):
                a = rng.choice(ids)
                b = rng.choice(ids)
                bases, _ = graph.merge_bases(a, b)
                self.assertEqual(set(bases), naive.merge_bases(a, b))
                counts, _ = graph.ahead_behind(a, b)
                self.assertEqual(counts, naive.ahead_behind(a, b))
            graph.close()

    def test_random_graphs(self) -> None:
        for seed in range(12):
            with self.subTest(seed=seed):
                self.run_one_graph(seed, size=40 + seed * 5)

    def test_degenerate_timestamps(self) -> None:
        # All timestamps identical and children older than parents must
        # not affect results (timestamps are never used by queries).
        rng = random.Random(999)
        commits = random_graph(rng, 60)
        commits = [(cid, parents, 42) for cid, parents, _ in commits]
        naive = NaiveGraph()
        for cid, parents, _ in commits:
            naive.add(cid, parents)
        with tempfile.TemporaryDirectory() as tmp:
            graph = CommitGraph(os.path.join(tmp, "graph.db"))
            for batch in shuffled_batches(rng, commits):
                graph.ingest(batch)
            ids = [c[0] for c in commits]
            for _ in range(40):
                a, b = rng.choice(ids), rng.choice(ids)
                bases, _ = graph.merge_bases(a, b)
                self.assertEqual(set(bases), naive.merge_bases(a, b))
                counts, _ = graph.ahead_behind(a, b)
                self.assertEqual(counts, naive.ahead_behind(a, b))
            graph.close()


if __name__ == "__main__":
    unittest.main()
