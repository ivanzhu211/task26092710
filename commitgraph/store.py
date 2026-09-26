"""SQLite persistence layer.

Stores one row per commit and one row per parent edge.  Storage is linear
in the number of commits; no transitive closure or pairwise data is kept.
"""

import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS commits (
    id  TEXT PRIMARY KEY,
    gen INTEGER NOT NULL,
    ts  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
    child  TEXT NOT NULL,
    parent TEXT NOT NULL,
    ord    INTEGER NOT NULL,
    PRIMARY KEY (child, parent)
);
"""

_CHUNK = 500  # stay well below SQLite's variable limit


class Store:
    """Low-level access to the SQLite file backing a CommitGraph."""

    def __init__(self, path):
        self._conn = sqlite3.connect(path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def get_gen(self, commit_id):
        """Return the generation number of commit_id, or None if unknown."""
        row = self._conn.execute(
            "SELECT gen FROM commits WHERE id = ?", (commit_id,)
        ).fetchone()
        return row[0] if row is not None else None

    def get_parents(self, commit_id):
        """Return the ordered parent list of commit_id ([] if unknown/root)."""
        rows = self._conn.execute(
            "SELECT parent FROM edges WHERE child = ? ORDER BY ord", (commit_id,)
        )
        return [row[0] for row in rows]

    def get_gens(self, commit_ids):
        """Return {id: gen} for the subset of commit_ids that exist."""
        result = {}
        ids = list(commit_ids)
        for i in range(0, len(ids), _CHUNK):
            chunk = ids[i : i + _CHUNK]
            marks = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT id, gen FROM commits WHERE id IN ({marks})", chunk
            )
            result.update(rows)
        return result

    def get_existing_parents(self, commit_ids):
        """Return {id: (parents...)} for the subset of commit_ids that exist."""
        existing = self.get_gens(commit_ids)
        parents = {cid: [] for cid in existing}
        ids = list(existing)
        for i in range(0, len(ids), _CHUNK):
            chunk = ids[i : i + _CHUNK]
            marks = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT child, parent FROM edges WHERE child IN ({marks})"
                " ORDER BY child, ord",
                chunk,
            )
            for child, parent in rows:
                parents[child].append(parent)
        return {cid: tuple(plist) for cid, plist in parents.items()}

    def insert(self, commit_rows, edge_rows):
        """Atomically insert commit rows (id, gen, ts) and edges (child, ord, parent)."""
        with self._conn:
            self._conn.executemany(
                "INSERT INTO commits (id, gen, ts) VALUES (?, ?, ?)", commit_rows
            )
            self._conn.executemany(
                "INSERT INTO edges (child, ord, parent) VALUES (?, ?, ?)", edge_rows
            )
