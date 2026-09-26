"""Cross-check CommitGraph queries against a naive reference on random DAGs."""

import os
import random
import tempfile
import unittest

from commitgraph import Commit, CommitGraph

from .refgraph import RefGraph


def make_id(n):
    return f"{n:040x}"


def random_dag(rng, size):
    """Build a random DAG (id -> parents) in topological order.

    Always contains multiple roots, plain merges, an octopus merge and a
    criss-cross structure; the remaining nodes are random.
    """
    parents = {}
    order = []

    def add(plist):
        cid = make_id(len(order))
        parents[cid] = tuple(plist)
        order.append(cid)
        return cid

    roots = [add(()) for _ in range(rng.randint(2, 3))]
    # criss-cross: two branches swap-merge each other's tips
    x1 = add((roots[0],))
    y1 = add((roots[0],))
    x2 = add((x1, y1))
    y2 = add((y1, x1))
    tip_x = add((x2,))
    tip_y = add((y2,))
    # octopus merge (3-4 parents)
    add((x2, y2, roots[1], *roots[2:]))
    # random fill: mostly single parents, some merges, rare extra roots
    while len(order) < size:
        roll = rng.random()
        if roll < 0.05:
            add(())
        elif roll < 0.30:
            k = min(len(order), rng.randint(2, 4))
            add(tuple(rng.sample(order, k)))
        else:
            add((rng.choice(order),))
    return parents, order


def random_timestamps(rng, order):
    if rng.random() < 0.25:
        return {cid: 1000 for cid in order}  # all identical
    # fully random: clock skew and child-older-than-parent included
    return {cid: rng.randrange(0, 10**6) for cid in order}


def shuffled_batches(rng, order):
    """Split a topological order into random batches (parents never land in a
    later batch than their children), shuffling inside each batch."""
    batches = []
    pos = 0
    while pos < len(order):
        step = rng.randint(1, max(1, len(order) // 3))
        batch = order[pos : pos + step]
        rng.shuffle(batch)
        batches.append(batch)
        pos += step
    return batches


class QueryCrossCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "graph.db")

    def tearDown(self):
        self.tmp.cleanup()

    def check_graph(self, seed):
        rng = random.Random(seed)
        parents, order = random_dag(rng, size=rng.randint(40, 80))
        timestamps = random_timestamps(rng, order)
        path = os.path.join(self.tmp.name, f"graph-{seed}.db")

        ref = RefGraph()
        for cid in order:
            ref.add(cid, parents[cid])

        graph = CommitGraph(path)
        for batch in shuffled_batches(rng, order):
            graph.ingest(Commit(cid, parents[cid], timestamps[cid]) for cid in batch)

        pairs = [(rng.choice(order), rng.choice(order)) for _ in range(40)]
        pairs += [(order[0], order[0]), (order[-1], order[-1])]
        for a, b in pairs:
            with self.subTest(seed=seed, a=a, b=b):
                bases, touched = graph.merge_bases(a, b)
                self.assertEqual(sorted(bases), ref.merge_bases(a, b))
                self.assertGreater(touched, 0)
                self.assertLessEqual(touched, len(order))

                is_anc, touched = graph.is_ancestor(a, b)
                self.assertEqual(is_anc, ref.is_ancestor(a, b))
                self.assertGreater(touched, 0)

                ab, touched = graph.ahead_behind(a, b)
                self.assertEqual(ab, ref.ahead_behind(a, b))
                self.assertGreater(touched, 0)
        graph.close()

        # persistence: reopen and re-verify a few pairs
        graph = CommitGraph(path)
        for a, b in pairs[:5]:
            self.assertEqual(sorted(graph.merge_bases(a, b)[0]), ref.merge_bases(a, b))
            self.assertEqual(graph.ahead_behind(a, b)[0], ref.ahead_behind(a, b))
        graph.close()

    def test_random_dags_match_reference(self):
        for seed in range(12):
            self.check_graph(seed)

    def test_features_present(self):
        # sanity: the generator really exercises the required structures
        rng = random.Random(0)
        parents, _ = random_dag(rng, 60)
        degrees = [len(p) for p in parents.values()]
        self.assertGreaterEqual(degrees.count(0), 2)  # multiple roots
        self.assertIn(2, degrees)  # plain merges
        self.assertTrue(any(d >= 3 for d in degrees))  # octopus merges


if __name__ == "__main__":
    unittest.main()
