"""SQLite persistence layer for the commit graph index.

Schema is deliberately minimal so storage stays linear in the number of
commits: one row per commit (id, generation, timestamp) and one row per
parent edge.  No transitive closure, no per-pair precomputation.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS commits (
    id  TEXT PRIMARY KEY,
    gen INTEGER NOT NULL,
    ts  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS parents (
    child  TEXT NOT NULL,
    ord    INTEGER NOT NULL,
    parent TEXT NOT NULL,
    PRIMARY KEY (child, ord)
);
"""


class Store:
    """Thin wrapper around the SQLite file backing one index instance."""

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def exists(self, commit_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM commits WHERE id = ?", (commit_id,)
        ).fetchone()
        return row is not None

    def get_generation(self, commit_id: str) -> Optional[int]:
        row = self._conn.execute(
            "SELECT gen FROM commits WHERE id = ?", (commit_id,)
        ).fetchone()
        return None if row is None else row[0]

    def get_parents(self, commit_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT parent FROM parents WHERE child = ? ORDER BY ord",
            (commit_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_parents_with_generations(
        self, commit_id: str
    ) -> list[tuple[str, int]]:
        """Ordered parents of a commit together with their generations."""
        rows = self._conn.execute(
            "SELECT p.parent, c.gen FROM parents p "
            "JOIN commits c ON c.id = p.parent "
            "WHERE p.child = ? ORDER BY p.ord",
            (commit_id,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def insert_batch(
        self,
        commits: Iterable[tuple[str, int, int]],
        parents: Iterable[tuple[str, int, str]],
    ) -> None:
        """Insert one validated batch atomically."""
        with self._conn:
            self._conn.executemany(
                "INSERT INTO commits (id, gen, ts) VALUES (?, ?, ?)", commits
            )
            self._conn.executemany(
                "INSERT INTO parents (child, ord, parent) VALUES (?, ?, ?)",
                parents,
            )

    def dump_commits(self) -> list[tuple]:
        return self._conn.execute(
            "SELECT id, gen, ts FROM commits ORDER BY id"
        ).fetchall()

    def dump_parents(self) -> list[tuple]:
        return self._conn.execute(
            "SELECT child, ord, parent FROM parents ORDER BY child, ord"
        ).fetchall()

    def filter_existing(self, ids: Iterable[str]) -> set[str]:
        """Return the subset of ids already stored."""
        found_set: set[str] = set()
        id_list = list(ids)
        for i in range(0, len(id_list), 500):
            chunk = id_list[i : i + 500]
            marks = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT id FROM commits WHERE id IN ({marks})", chunk
            ).fetchall()
            found_set.update(r[0] for r in rows)
        return found_set

    def get_parents_many(self, ids: Iterable[str]) -> dict[str, list[str]]:
        """Ordered parent lists for a set of commit ids."""
        result: dict[str, list[str]] = {i: [] for i in ids}
        id_list = list(result)
        for i in range(0, len(id_list), 500):
            chunk = id_list[i : i + 500]
            marks = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT child, parent FROM parents "
                f"WHERE child IN ({marks}) ORDER BY child, ord",
                chunk,
            ).fetchall()
            for child, parent in rows:
                result[child].append(parent)
        return result

    def get_generations_many(self, ids: Iterable[str]) -> dict[str, int]:
        """Generations for a set of commit ids."""
        result: dict[str, int] = {}
        id_list = list(ids)
        for i in range(0, len(id_list), 500):
            chunk = id_list[i : i + 500]
            marks = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT id, gen FROM commits WHERE id IN ({marks})", chunk
            ).fetchall()
            result.update(rows)
        return result
