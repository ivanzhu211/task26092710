"""Exception types raised by commitgraph."""


class CommitGraphError(Exception):
    """Base class for all commitgraph errors."""


class UnknownCommitError(CommitGraphError, KeyError):
    """A query referenced a commit id that is not in the graph."""

    def __init__(self, commit_id):
        self.commit_id = commit_id
        super().__init__(f"unknown commit: {commit_id!r}")


class IngestError(CommitGraphError):
    """Base class for ingest rejections. Nothing is written when raised."""


class InvalidCommitError(IngestError, ValueError):
    """A commit is malformed (bad id, duplicate parents, ...)."""


class MissingParentError(IngestError):
    """A commit references a parent that is neither in the batch nor stored."""

    def __init__(self, missing):
        self.missing = tuple(missing)
        super().__init__(f"missing parents: {list(self.missing)!r}")


class CycleError(IngestError):
    """The batch introduces a cycle among its own commits."""

    def __init__(self, members):
        self.members = tuple(members)
        super().__init__(f"cycle involving: {list(self.members)!r}")


class ParentConflictError(IngestError):
    """An existing commit id was re-ingested with a different parent list."""

    def __init__(self, commit_id):
        self.commit_id = commit_id
        super().__init__(f"conflicting parents for existing commit: {commit_id!r}")
