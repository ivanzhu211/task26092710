"""Naive reference implementation used to cross-check the index.

Computes full ancestor sets in memory; only suitable for small graphs.
"""

from __future__ import annotations


class NaiveGraph:
    def __init__(self) -> None:
        self.parents: dict[str, list[str]] = {}

    def add(self, cid: str, parents: list[str]) -> None:
        self.parents[cid] = list(parents)

    def ancestors(self, cid: str) -> set[str]:
        """Inclusive ancestor set of cid."""
        seen: set[str] = set()
        stack = [cid]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.parents[cur])
        return seen

    def merge_bases(self, a: str, b: str) -> set[str]:
        common = self.ancestors(a) & self.ancestors(b)
        best = set()
        for cand in common:
            dominated = False
            for other in common:
                if other != cand and cand in self.ancestors(other):
                    dominated = True
                    break
            if not dominated:
                best.add(cand)
        return best

    def is_ancestor(self, a: str, b: str) -> bool:
        return a in self.ancestors(b)

    def ahead_behind(self, a: str, b: str) -> tuple[int, int]:
        anc_a = self.ancestors(a)
        anc_b = self.ancestors(b)
        return len(anc_a - anc_b), len(anc_b - anc_a)
