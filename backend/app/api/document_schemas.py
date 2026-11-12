from datetime import datetime

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    """
    Public document metadata returned by the API.
    """

    id: str
    knowledge_base_id: str
    logical_document_id: str
    version_number: int
    is_current: bool
    is_published: bool
    published_at: datetime | None
    published_by_user_id: str | None
    archived_at: datetime | None
    archived_by_user_id: str | None
    visibility: str
    department_id: str | None
    filename: str
    status: str
    file_size_bytes: int
    total_pages: int | None
    chunk_count: int
    embedding_provider: str | None
    embedding_model: str | None
    embedding_dimension: int | None
    vector_store_provider: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    """
    Paginated document-list response.
    """

    documents: list[DocumentResponse]
    total: int
    offset: int
    limit: int


class DocumentJobResponse(BaseModel):
    id: str
    status: str
    filename: str
    document_id: str | None
    error_message: str | None
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class DocumentMonitoringResponse(BaseModel):
    job_counts: dict[str, int]
    workers: list[dict[str, str]]
