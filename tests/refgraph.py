"""Naive reference implementation used to cross-check CommitGraph.

Computes full ancestor sets in memory; only suitable for small test graphs.
"""


class RefGraph:
    def __init__(self):
        self.parents = {}

    def add(self, cid, parents):
        self.parents[cid] = tuple(parents)

    def ancestors(self, cid):
        """All commits reachable from cid via parent edges, cid included."""
        seen = set()
        stack = [cid]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(self.parents[node])
        return seen

    def merge_bases(self, a, b):
        common = self.ancestors(a) & self.ancestors(b)
        return sorted(
            c
            for c in common
            if not any(c != other and c in self.ancestors(other) for other in common)
        )

    def is_ancestor(self, a, b):
        return a in self.ancestors(b)

    def ahead_behind(self, a, b):
        anc_a = self.ancestors(a)
        anc_b = self.ancestors(b)
        return (len(anc_a - anc_b), len(anc_b - anc_a))
