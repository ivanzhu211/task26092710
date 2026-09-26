"""Data model for commits."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Commit:
    """A single commit node.

    id:        40 lowercase hex characters.
    parents:   ordered parent ids; empty for roots, >=2 for (octopus) merges.
    timestamp: committer timestamp; stored but never used by queries.
    """

    id: str
    parents: tuple = ()
    timestamp: int = 0

    def __post_init__(self):
        object.__setattr__(self, "parents", tuple(self.parents))
