from enum import StrEnum


class DocumentStatus(StrEnum):
    """Possible states of the document ingestion process."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETING = "deleting"


class DocumentVisibility(StrEnum):
    """Audience allowed to read a shared organization document."""

    ORGANIZATION = "organization"
    DEPARTMENT = "department"


class DocumentJobStatus(StrEnum):
    """Persistent lifecycle states for queued document ingestion."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
