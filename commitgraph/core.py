"""Public commit graph index API."""

from __future__ import annotations

import heapq
import re
from typing import Iterable, NamedTuple

from .errors import (
    CycleError,
    InvalidCommitError,
    MissingParentError,
    ParentConflictError,
    UnknownCommitError,
)
from .store import Store

_ID_RE = re.compile(r"[0-9a-fA-F]{40}\Z")


class Commit(NamedTuple):
    """One commit record: 40-hex id, ordered parent ids, timestamp."""

    id: str
    parents: tuple[str, ...]
    ts: int


class QueryResult(NamedTuple):
    """Query outcome plus the number of distinct commits touched."""

    value: object
    touched: int


def _normalize(record) -> Commit:
    """Coerce a (id, parents, ts) triple into a validated Commit."""
    try:
        cid, parents, ts = record
    except (TypeError, ValueError) as exc:
        raise InvalidCommitError(f"malformed commit record: {record!r}") from exc
    if not isinstance(cid, str) or not _ID_RE.match(cid):
        raise InvalidCommitError(f"bad commit id: {cid!r}")
    parents = tuple(parents)
    for pid in parents:
        if not isinstance(pid, str) or not _ID_RE.match(pid):
            raise InvalidCommitError(f"bad parent id {pid!r} in commit {cid!r}")
    if not isinstance(ts, int):
        raise InvalidCommitError(f"bad timestamp {ts!r} in commit {cid!r}")
    return Commit(cid, parents, ts)


class CommitGraph:
    """A persistent commit graph index backed by a single SQLite file."""

    def __init__(self, path: str) -> None:
        self._store = Store(path)

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> "CommitGraph":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- ingest

    def ingest(self, commits: Iterable[Commit]) -> None:
        """Atomically ingest a batch of commits.

        The whole batch is rejected (leaving the index untouched) when any
        parent is missing, when the batch contains a cycle, or when an id
        that is already stored is resubmitted with a different parent list.
        Resubmissions with identical parents are silently ignored.
        """
        records = [_normalize(r) for r in commits]

        # Deduplicate inside the batch; conflicting redefinitions reject it.
        by_id: dict[str, Commit] = {}
        for rec in records:
            prev = by_id.get(rec.id)
            if prev is not None:
                if prev.parents != rec.parents:
                    raise ParentConflictError(rec.id)
                continue
            by_id[rec.id] = rec

        # Already-stored ids must agree on the ordered parent list.
        stored = self._store.filter_existing(by_id.keys())
        if stored:
            stored_parents = self._store.get_parents_many(stored)
            for cid in stored:
                if tuple(stored_parents[cid]) != by_id[cid].parents:
                    raise ParentConflictError(cid)
        new_ids = [cid for cid in by_id if cid not in stored]
        if not new_ids:
            return
        new_set = set(new_ids)

        # Every parent must be stored already or live inside this batch.
        external_parents: set[str] = set()
        for cid in new_ids:
            for pid in by_id[cid].parents:
                if pid not in new_set:
                    external_parents.add(pid)
        known = self._store.filter_existing(external_parents)
        for cid in new_ids:
            for pid in by_id[cid].parents:
                if pid not in new_set and pid not in known:
                    raise MissingParentError(cid, pid)

        # Topological order of the batch (Kahn); leftovers mean a cycle.
        pending: dict[str, int] = {}
        children_of: dict[str, list[str]] = {cid: [] for cid in new_ids}
        for cid in new_ids:
            deps = sum(1 for pid in by_id[cid].parents if pid in new_set)
            pending[cid] = deps
            for pid in by_id[cid].parents:
                if pid in new_set:
                    children_of[pid].append(cid)
        ready = [cid for cid in new_ids if pending[cid] == 0]
        topo: list[str] = []
        while ready:
            cid = ready.pop()
            topo.append(cid)
            for child in children_of[cid]:
                pending[child] -= 1
                if pending[child] == 0:
                    ready.append(child)
        if len(topo) != len(new_ids):
            raise CycleError("batch introduces a commit cycle")

        # Generations: 1 + max(parent generations); roots get 1.
        gens = self._store.get_generations_many(external_parents & known)
        for cid in topo:
            gen = 1
            for pid in by_id[cid].parents:
                pgen = gens[pid]
                if pgen + 1 > gen:
                    gen = pgen + 1
            gens[cid] = gen

        commit_rows = [(cid, gens[cid], by_id[cid].ts) for cid in new_ids]
        parent_rows = [
            (cid, ord_, pid)
            for cid in new_ids
            for ord_, pid in enumerate(by_id[cid].parents)
        ]
        self._store.insert_batch(commit_rows, parent_rows)

    # ---------------------------------------------------------------- queries

    _P1 = 1
    _P2 = 2
    _STALE = 4

    def _require_gen(self, cid: str) -> int:
        gen = self._store.get_generation(cid)
        if gen is None:
            raise UnknownCommitError(cid)
        return gen

    def _paint_down(self, a: str, b: str) -> tuple[list[str], int, int, int]:
        """Generation-ordered paint-down walk from tips a and b.

        Commits are popped in strictly decreasing generation order, so a
        commit's flags are final the first time it is popped: every child
        that could propagate flags to it has a larger generation and has
        therefore been popped already.  The walk stops as soon as every
        queued commit is stale (reachable from both tips); anything below
        that frontier is common history and never touched.
        """
        gen_a = self._require_gen(a)
        gen_b = self._require_gen(b)

        flags: dict[str, int] = {}
        heap: list[tuple[int, int, str]] = []
        seq = 0
        nonstale = 0
        touched = 0

        def add_flag(cid: str, flag: int, gen: int) -> None:
            nonlocal seq, nonstale, touched
            cur = flags.get(cid)
            if cur is None:
                flags[cid] = flag
                heapq.heappush(heap, (-gen, seq, cid))
                seq += 1
                touched += 1
                if not flag & self._STALE:
                    nonstale += 1
            else:
                new = cur | flag
                if new == cur:
                    return
                flags[cid] = new
                if not cur & self._STALE and new & self._STALE:
                    nonstale -= 1

        add_flag(a, self._P1, gen_a)
        add_flag(b, self._P2, gen_b)

        bases: list[str] = []
        ahead = 0
        behind = 0
        while nonstale > 0:
            _, _, cid = heapq.heappop(heap)
            f = flags[cid]
            if f & self._STALE:
                pass
            else:
                # A non-stale entry leaves the queue.
                nonstale -= 1
                if f & self._P1 and f & self._P2:
                    bases.append(cid)
                    f |= self._STALE
                    flags[cid] = f
                elif f & self._P1:
                    ahead += 1
                else:
                    behind += 1
            for pid, pgen in self._store.get_parents_with_generations(cid):
                add_flag(pid, f, pgen)

        return bases, ahead, behind, touched

    def merge_bases(self, a: str, b: str) -> tuple[list[str], int]:
        """All best common ancestors of a and b, plus touched count."""
        bases, _, _, touched = self._paint_down(a, b)
        return bases, touched

    def is_ancestor(self, a: str, b: str) -> tuple[bool, int]:
        """Whether a is reachable from b along parent edges (a == b counts)."""
        gen_a = self._require_gen(a)
        gen_b = self._require_gen(b)
        if a == b:
            return True, 0
        if gen_b <= gen_a:
            # Ancestors have strictly smaller generations.
            return False, 0
        seen = {b}
        heap: list[tuple[int, int, str]] = [(-gen_b, 0, b)]
        seq = 1
        touched = 1
        while heap:
            _, _, cid = heapq.heappop(heap)
            if cid == a:
                return True, touched
            for pid, pgen in self._store.get_parents_with_generations(cid):
                if pgen >= gen_a and pid not in seen:
                    seen.add(pid)
                    heapq.heappush(heap, (-pgen, seq, pid))
                    seq += 1
                    touched += 1
        return False, touched

    def ahead_behind(self, a: str, b: str) -> tuple[tuple[int, int], int]:
        """(reachable from a only, reachable from b only), plus touched."""
        _, ahead, behind, touched = self._paint_down(a, b)
        return (ahead, behind), touched
