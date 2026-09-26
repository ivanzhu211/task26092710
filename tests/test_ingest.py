"""Rejection-path tests: failed ingests must leave the database untouched."""

import os
import sqlite3
import tempfile
import unittest

from commitgraph import (
    Commit,
    CommitGraph,
    CycleError,
    InvalidCommitError,
    MissingParentError,
    ParentConflictError,
    UnknownCommitError,
)


def make_id(n):
    return f"{n:040x}"


def dump_database(path):
    conn = sqlite3.connect(path)
    try:
        commits = sorted(conn.execute("SELECT * FROM commits"))
        edges = sorted(conn.execute("SELECT * FROM edges"))
    finally:
        conn.close()
    return commits, edges


class RejectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "graph.db")
        self.graph = CommitGraph(self.path)
        # base graph: 0 (root) <- 1 <- 2, plus root 3
        self.graph.ingest(
            [
                Commit(make_id(0), (), 1),
                Commit(make_id(1), (make_id(0),), 2),
                Commit(make_id(2), (make_id(1),), 3),
                Commit(make_id(3), (), 4),
            ]
        )
        self.before = dump_database(self.path)

    def tearDown(self):
        self.graph.close()
        self.tmp.cleanup()

    def assert_unchanged(self):
        self.assertEqual(dump_database(self.path), self.before)

    def test_missing_parent_rejected(self):
        with self.assertRaises(MissingParentError) as ctx:
            self.graph.ingest([Commit(make_id(10), (make_id(99),), 5)])
        self.assertEqual(ctx.exception.missing, (make_id(99),))
        self.assert_unchanged()

    def test_missing_parent_rejects_whole_batch(self):
        # valid commits in the same batch must not be written either
        with self.assertRaises(MissingParentError):
            self.graph.ingest(
                [
                    Commit(make_id(10), (make_id(2),), 5),
                    Commit(make_id(11), (make_id(10),), 6),
                    Commit(make_id(12), (make_id(99),), 7),
                ]
            )
        self.assert_unchanged()

    def test_cycle_rejected(self):
        with self.assertRaises(CycleError):
            self.graph.ingest(
                [
                    Commit(make_id(10), (make_id(11),), 5),
                    Commit(make_id(11), (make_id(12),), 6),
                    Commit(make_id(12), (make_id(10),), 7),
                    Commit(make_id(13), (make_id(2),), 8),  # valid, still rejected
                ]
            )
        self.assert_unchanged()

    def test_self_loop_rejected(self):
        with self.assertRaises(CycleError):
            self.graph.ingest([Commit(make_id(10), (make_id(10),), 5)])
        self.assert_unchanged()

    def test_parent_conflict_rejected(self):
        with self.assertRaises(ParentConflictError):
            self.graph.ingest([Commit(make_id(2), (make_id(3),), 9)])
        self.assert_unchanged()

    def test_parent_order_conflict_rejected(self):
        self.graph.ingest([Commit(make_id(4), (make_id(0), make_id(3)), 5)])
        before = dump_database(self.path)
        with self.assertRaises(ParentConflictError):
            self.graph.ingest([Commit(make_id(4), (make_id(3), make_id(0)), 6)])
        self.assertEqual(dump_database(self.path), before)

    def test_conflict_within_batch_rejected(self):
        with self.assertRaises(ParentConflictError):
            self.graph.ingest(
                [
                    Commit(make_id(10), (make_id(0),), 5),
                    Commit(make_id(10), (make_id(3),), 5),
                ]
            )
        self.assert_unchanged()

    def test_identical_reingest_ignored(self):
        inserted = self.graph.ingest(
            [Commit(make_id(2), (make_id(1),), 999)]  # timestamp differs: ignored
        )
        self.assertEqual(inserted, 0)
        self.assert_unchanged()

    def test_invalid_id_rejected(self):
        with self.assertRaises(InvalidCommitError):
            self.graph.ingest([Commit("xyz", (), 1)])
        with self.assertRaises(InvalidCommitError):
            self.graph.ingest([Commit(make_id(10), ("not-hex",), 1)])
        self.assert_unchanged()

    def test_unknown_commit_raises_in_queries(self):
        for query in (
            self.graph.merge_bases,
            self.graph.is_ancestor,
            self.graph.ahead_behind,
        ):
            with self.assertRaises(UnknownCommitError):
                query(make_id(0), make_id(77))
            with self.assertRaises(UnknownCommitError):
                query(make_id(77), make_id(0))


if __name__ == "__main__":
    unittest.main()

