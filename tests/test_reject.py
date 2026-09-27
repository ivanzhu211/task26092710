"""Rejection paths: failed ingests must leave the index untouched."""

from __future__ import annotations

import os
import tempfile
import unittest

from commitgraph import (
    CommitGraph,
    CycleError,
    MissingParentError,
    ParentConflictError,
    UnknownCommitError,
)

A = "a" * 40
B = "b" * 40
C = "c" * 40
D = "d" * 40
E = "e" * 40
F = "f" * 40


class RejectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.graph = CommitGraph(os.path.join(self.tmp.name, "graph.db"))
        self.graph.ingest([(A, [], 1)])
        self.graph.ingest([(B, [A], 2), (C, [A, B], 3)])

    def tearDown(self) -> None:
        self.graph.close()
        self.tmp.cleanup()

    def snapshot(self) -> tuple[list, list]:
        store = self.graph._store
        return store.dump_commits(), store.dump_parents()

    def assert_unchanged(self, before: tuple[list, list]) -> None:
        self.assertEqual(self.snapshot(), before)

    def test_missing_parent(self) -> None:
        before = self.snapshot()
        with self.assertRaises(MissingParentError):
            self.graph.ingest([(D, [E], 4)])
        self.assert_unchanged(before)

    def test_missing_parent_mixed_batch(self) -> None:
        # Valid commits in the same batch must not be persisted either.
        before = self.snapshot()
        with self.assertRaises(MissingParentError):
            self.graph.ingest([(D, [B], 4), (E, [F], 5)])
        self.assert_unchanged(before)

    def test_in_batch_cycle(self) -> None:
        before = self.snapshot()
        with self.assertRaises(CycleError):
            self.graph.ingest([(D, [E], 4), (E, [D], 5)])
        self.assert_unchanged(before)

    def test_in_batch_self_loop(self) -> None:
        before = self.snapshot()
        with self.assertRaises(CycleError):
            self.graph.ingest([(D, [D], 4)])
        self.assert_unchanged(before)

    def test_parent_conflict_stored(self) -> None:
        before = self.snapshot()
        with self.assertRaises(ParentConflictError):
            self.graph.ingest([(B, [C], 9), (D, [B], 10)])
        self.assert_unchanged(before)

    def test_parent_conflict_within_batch(self) -> None:
        before = self.snapshot()
        with self.assertRaises(ParentConflictError):
            self.graph.ingest([(D, [A], 4), (D, [B], 5)])
        self.assert_unchanged(before)

    def test_identical_resubmit_is_ignored(self) -> None:
        before = self.snapshot()
        self.graph.ingest([(B, [A], 999), (C, [A, B], 888)])
        self.assert_unchanged(before)

    def test_unknown_query_ids(self) -> None:
        with self.assertRaises(UnknownCommitError):
            self.graph.merge_bases(A, D)
        with self.assertRaises(UnknownCommitError):
            self.graph.is_ancestor(D, A)
        with self.assertRaises(UnknownCommitError):
            self.graph.ahead_behind(D, E)


if __name__ == "__main__":
    unittest.main()
