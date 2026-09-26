"""Commit graph index: ingestion and generation-bounded reachability queries."""

import re
import heapq
import itertools
from collections import deque

from .errors import (
    CycleError,
    InvalidCommitError,
    MissingParentError,
    ParentConflictError,
    UnknownCommitError,
)
from .store import Store

_ID_RE = re.compile(r"[0-9a-f]{40}")

_P1 = 1  # reachable from the first tip
_P2 = 2  # reachable from the second tip
_STALE = 4  # known to be reachable from both (or from a common ancestor)
_BOTH = _P1 | _P2


class _Walk:
    """Per-query store wrapper that records every distinct commit loaded."""

    def __init__(self, store):
        self._store = store
        self.touched = set()

    def gen(self, cid):
        self.touched.add(cid)
        gen = self._store.get_gen(cid)
        if gen is None:
            raise UnknownCommitError(cid)
        return gen

    def parents(self, cid):
        self.touched.add(cid)
        return self._store.get_parents(cid)


def _check_id(commit_id):
    if not isinstance(commit_id, str) or not _ID_RE.fullmatch(commit_id):
        raise InvalidCommitError(f"bad commit id (need 40 lowercase hex): {commit_id!r}")


class CommitGraph:
    """A commit graph persisted in a single SQLite file.

    Query methods return (result, touched) where touched is the number of
    distinct commits loaded during the query.  Query cost depends only on
    the graph segment between the given tips and the answer, never on the
    total length of the history below it, and never on timestamps.
    """

    def __init__(self, path):
        self._store = Store(path)

    def close(self):
        self._store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def ingest(self, commits):
        """Insert a batch of Commit objects. Returns the number inserted.

        The batch is validated as a whole; on MissingParentError, CycleError
        or ParentConflictError nothing is written.  Already-known commits
        with identical parent lists are silently ignored.
        """
        pending = {}
        for commit in commits:
            _check_id(commit.id)
            parents = tuple(commit.parents)
            for parent in parents:
                _check_id(parent)
            if len(set(parents)) != len(parents):
                raise InvalidCommitError(f"duplicate parents in commit {commit.id!r}")
            if commit.id in pending:
                if pending[commit.id][0] != parents:
                    raise ParentConflictError(commit.id)
                continue
            pending[commit.id] = (parents, int(commit.timestamp))
        if not pending:
            return 0

        existing = self._store.get_existing_parents(pending.keys())
        to_insert = {}
        for cid, (parents, ts) in pending.items():
            if cid in existing:
                if existing[cid] != parents:
                    raise ParentConflictError(cid)
            else:
                to_insert[cid] = (parents, ts)
        if not to_insert:
            return 0

        external = set()
        for parents, _ in to_insert.values():
            for parent in parents:
                if parent not in to_insert:
                    external.add(parent)
        ext_gens = self._store.get_gens(external)
        missing = external.difference(ext_gens)
        if missing:
            raise MissingParentError(sorted(missing))

        indegree = {cid: 0 for cid in to_insert}
        children = {cid: [] for cid in to_insert}
        for cid, (parents, _) in to_insert.items():
            for parent in parents:
                if parent in to_insert:
                    indegree[cid] += 1
                    children[parent].append(cid)
        queue = deque(cid for cid, deg in indegree.items() if deg == 0)
        order = []
        while queue:
            cid = queue.popleft()
            order.append(cid)
            for child in children[cid]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(order) != len(to_insert):
            remaining = sorted(cid for cid, deg in indegree.items() if deg > 0)
            raise CycleError(remaining)

        gens = {}
        for cid in order:
            parents = to_insert[cid][0]
            gen = 1
            for parent in parents:
                parent_gen = gens[parent] if parent in gens else ext_gens[parent]
                gen = max(gen, parent_gen + 1)
            gens[cid] = gen

        commit_rows = [(cid, gens[cid], to_insert[cid][1]) for cid in order]
        edge_rows = [
            (cid, idx, parent)
            for cid in order
            for idx, parent in enumerate(to_insert[cid][0])
        ]
        self._store.insert(commit_rows, edge_rows)
        return len(order)

    def merge_bases(self, a, b):
        """Return ([best common ancestors of a and b], touched)."""
        walk = _Walk(self._store)
        walk.gen(a)
        walk.gen(b)
        if a == b:
            return [a], len(walk.touched)
        _, candidates = self._paint_down(walk, a, b, collect=True)
        result = []
        for cid in sorted(candidates, key=lambda c: -walk.gen(c)):
            if not any(self._reachable(walk, kept, cid) for kept in result):
                result.append(cid)
        return sorted(result), len(walk.touched)

    def is_ancestor(self, a, b):
        """Return (True if a is reachable from b via parent edges, touched)."""
        walk = _Walk(self._store)
        gen_a = walk.gen(a)
        gen_b = walk.gen(b)
        if a == b:
            return True, len(walk.touched)
        if gen_a > gen_b:
            return False, len(walk.touched)
        return self._reachable(walk, b, a), len(walk.touched)

    def ahead_behind(self, a, b):
        """Return ((ahead, behind), touched); same semantics as
        `git rev-list --left-right --count a...b`."""
        walk = _Walk(self._store)
        walk.gen(a)
        walk.gen(b)
        if a == b:
            return (0, 0), len(walk.touched)
        flags, _ = self._paint_down(walk, a, b, collect=False)
        ahead = sum(1 for bits in flags.values() if bits == _P1)
        behind = sum(1 for bits in flags.values() if bits == _P2)
        return (ahead, behind), len(walk.touched)

    def _paint_down(self, walk, a, b, collect):
        """Best-first bidirectional walk from tips a and b, highest generation
        first.  Stops as soon as every frontier entry is known to be common
        (collect=False) or stale (collect=True), so commits below the answer
        segment are never loaded.  When collect is True, also returns the
        commits where the two colors first met (merge-base candidates)."""
        flags = {}
        heap = []
        counter = itertools.count()
        interesting = 0
        candidates = []

        def is_interesting(bits):
            return not (bits & _STALE) if collect else bits != _BOTH

        def mark(cid, bits):
            nonlocal interesting
            old = flags.get(cid, 0)
            new = old | bits
            if new == old:
                return
            flags[cid] = new
            gen = walk.gen(cid)
            heapq.heappush(heap, (-gen, next(counter), new, cid))
            if is_interesting(new):
                interesting += 1

        mark(a, _P1)
        mark(b, _P2)
        while interesting:
            _, _, pushed_bits, cid = heapq.heappop(heap)
            if is_interesting(pushed_bits):
                interesting -= 1
            bits = flags[cid]
            if collect and bits & _BOTH == _BOTH and not bits & _STALE:
                candidates.append(cid)
                bits |= _STALE
                flags[cid] = bits
            for parent in walk.parents(cid):
                mark(parent, bits)
        return flags, candidates

    def _reachable(self, walk, src, dst):
        """True if dst is reachable from src, assuming gen(src) >= gen(dst).
        Never expands commits with generation below gen(dst)."""
        gen_dst = walk.gen(dst)
        seen = {src}
        heap = [(-walk.gen(src), 0, src)]
        counter = itertools.count(1)
        while heap:
            neg_gen, _, cid = heapq.heappop(heap)
            if cid == dst:
                return True
            for parent in walk.parents(cid):
                if parent in seen:
                    continue
                gen_parent = walk.gen(parent)
                if gen_parent < gen_dst:
                    continue
                seen.add(parent)
                heapq.heappush(heap, (-gen_parent, next(counter), parent))
        return False
