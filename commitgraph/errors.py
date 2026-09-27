"""Exception types for the commit graph index."""


class CommitGraphError(Exception):
    """Base class for all commit graph errors."""


class UnknownCommitError(CommitGraphError, KeyError):
    """A query referenced a commit id that is not in the index."""

    def __init__(self, commit_id: str):
        self.commit_id = commit_id
        super().__init__(f"unknown commit: {commit_id!r}")


class IngestError(CommitGraphError, ValueError):
    """Base class for batch ingestion rejections."""


class InvalidCommitError(IngestError):
    """A commit record is malformed (bad id, negative timestamp, ...)."""


class MissingParentError(IngestError):
    """A commit references a parent that is neither stored nor in the batch."""

    def __init__(self, commit_id: str, parent_id: str):
        self.commit_id = commit_id
        self.parent_id = parent_id
        super().__init__(
            f"commit {commit_id!r} references missing parent {parent_id!r}"
        )


class CycleError(IngestError):
    """The batch introduces a cycle among its own commits."""


class ParentConflictError(IngestError):
    """An already-stored commit id was resubmitted with a different parent list."""

    def __init__(self, commit_id: str):
        self.commit_id = commit_id
        super().__init__(f"commit {commit_id!r} conflicts with stored parent list")
