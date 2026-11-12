from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base


class DocumentRecord(Base):
    """
    Registry entry for one uploaded document.

    This table is used to:
    - detect duplicate uploads through file_hash
    - identify document ownership
    - track ingestion status
    - remember which embedding model was used
    - reconnect users to previously stored vectors
    """

    __tablename__ = "document_records"

    __table_args__ = (
        Index(
            "ix_document_organization_knowledge_base_hash",
            "organization_id",
            "knowledge_base_id",
            "file_hash",
        ),
        Index(
            "ix_document_owner_knowledge_base",
            "organization_id",
            "user_id",
            "knowledge_base_id",
        ),
        Index(
            "ix_document_organization_visibility",
            "organization_id",
            "visibility",
            "department_id",
        ),
        UniqueConstraint(
            "organization_id",
            "knowledge_base_id",
            "logical_document_id",
            "version_number",
            name="uq_document_logical_version",
        ),
        Index(
            "ix_document_logical_current",
            "organization_id",
            "knowledge_base_id",
            "logical_document_id",
            "is_current",
        ),
        Index(
            "ix_document_org_published_current",
            "organization_id",
            "knowledge_base_id",
            "is_published",
            "is_current",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    organization_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    user_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    visibility: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="organization",
        server_default="organization",
    )

    department_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "department_records.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    knowledge_base_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    # A logical document remains stable across replacement uploads.  Each
    # physical upload is one immutable version in this registry.
    logical_document_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
    )

    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    is_current: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
        server_default="1",
    )

    is_published: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="0",
    )

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    published_by_user_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    archived_by_user_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    file_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    file_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    total_pages: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    chunk_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )

    embedding_provider: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    embedding_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    embedding_dimension: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    vector_store_provider: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    chunks: Mapped[list["DocumentChunkRecord"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DocumentChunkRecord(Base):
    """
    MySQL reference to one vector-store chunk.

    The actual vector remains in Chroma.
    MySQL stores its chunk_id so it can later be tracked or deleted.
    """

    __tablename__ = "document_chunk_records"

    __table_args__ = (
        UniqueConstraint(
            "vector_id",
            name="uq_document_chunk_vector_id",
        ),

        Index(
            "ix_document_chunk_document_page",
            "document_record_id",
            "page_number",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )

    document_record_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "document_records.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    chunk_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    vector_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    page_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    document: Mapped["DocumentRecord"] = relationship(
        back_populates="chunks",
    )


class DocumentIngestionJobRecord(Base):
    """Durable work item; Redis carries only its identifier in FIFO order."""

    __tablename__ = "document_ingestion_jobs"

    __table_args__ = (
        Index("ix_document_job_organization_status", "organization_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    knowledge_base_id: Mapped[str] = mapped_column(String(64), nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    replaces_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", server_default="queued", index=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
