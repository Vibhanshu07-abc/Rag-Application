import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import (
    AppError,
    AuthorizationError,
    ConflictError,
    DocumentDeletionError,
    DocumentNotFoundError,
)
from backend.app.auth.roles import (
    DepartmentRole,
    can_manage_departments,
)
from backend.app.database.enums import DocumentStatus
from backend.app.database.models import DocumentRecord
from backend.app.database.repositories import DocumentRepository


class VectorDeletionProtocol(Protocol):
    """
    Minimal vector-store interface required for document lifecycle mutations.
    """

    def delete_chunks(self, chunk_ids: list[str]) -> None:
        """
        Delete vectors using their stored vector IDs.
        """
        ...


class VectorLifecycleProtocol(VectorDeletionProtocol, Protocol):
    """Extended vector-store interface supporting version and publication metadata."""

    def set_version_state(
        self,
        vector_ids: Sequence[str],
        *,
        is_current: bool | None = None,
        is_published: bool | None = None,
        status: str | None = None,
    ) -> None:
        ...


@dataclass(frozen=True, slots=True)
class DocumentView:
    """
    Safe API-facing representation of a document.

    Internal ownership information such as organization_id,
    user_id and file_hash is intentionally not exposed.
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


@dataclass(frozen=True, slots=True)
class DocumentListResult:
    """
    Paginated document-list result returned by the service layer.
    """

    documents: tuple[DocumentView, ...]
    total: int
    offset: int
    limit: int


class DocumentManagementService:
    """
    Business logic for listing, reading, publishing, archiving,
    and deleting authenticated user documents.

    All repository calls remain scoped by organization_id.

    Department visibility is controlled through department_ids.
    Organization administrators can explicitly request access
    to all departments through include_all_departments.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: DocumentRepository,
    ) -> None:
        self.session = session
        self.repository = repository

    # ---------------------------------------------------------
    # Document conversion
    # ---------------------------------------------------------

    @staticmethod
    def _to_view(document: DocumentRecord) -> DocumentView:
        """
        Convert an ORM DocumentRecord into a safe,
        immutable service-layer view.
        """

        return DocumentView(
            id=document.id,
            knowledge_base_id=document.knowledge_base_id,
            logical_document_id=document.logical_document_id,
            version_number=document.version_number,
            is_current=document.is_current,
            is_published=document.is_published,
            published_at=document.published_at,
            published_by_user_id=document.published_by_user_id,
            archived_at=document.archived_at,
            archived_by_user_id=document.archived_by_user_id,
            visibility=document.visibility,
            department_id=document.department_id,
            filename=document.filename,
            status=document.status,
            file_size_bytes=document.file_size_bytes,
            total_pages=document.total_pages,
            chunk_count=document.chunk_count,
            embedding_provider=document.embedding_provider,
            embedding_model=document.embedding_model,
            embedding_dimension=document.embedding_dimension,
            vector_store_provider=document.vector_store_provider,
            error_message=document.error_message,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )

    # ---------------------------------------------------------
    # List documents
    # ---------------------------------------------------------

    async def list_documents(
        self,
        *,
        organization_id: str,
        user_id: str,
        department_ids: tuple[str, ...],
        knowledge_base_id: str | None,
        offset: int,
        limit: int,
        include_all_departments: bool = False,
        published_only: bool = False,
        managed_department_ids: tuple[str, ...] = (),
        department_id: str | None = None,
        status: str | None = None,
        is_published: bool | None = None,
        is_current: bool | None = None,
    ) -> DocumentListResult:
        """
        Return a paginated list of accessible documents.

        Organization administrators may set include_all_departments=True.
        Regular employees receive only published, current documents.
        Department Admins receive all documents in their managed
        departments and published, current documents elsewhere.
        """

        normalized_knowledge_base_id = (
            knowledge_base_id.strip() if knowledge_base_id is not None else None
        )
        normalized_department_id = (
            department_id.strip() if department_id is not None else None
        )

        documents = await self.repository.list_documents(
            organization_id=organization_id,
            user_id=user_id,
            department_ids=department_ids,
            knowledge_base_id=normalized_knowledge_base_id,
            department_id=normalized_department_id,
            status=status,
            is_published=is_published,
            is_current=is_current,
            offset=offset,
            limit=limit,
            include_all_departments=include_all_departments,
            published_only=published_only,
            managed_department_ids=managed_department_ids,
        )

        total = await self.repository.count_documents(
            organization_id=organization_id,
            user_id=user_id,
            department_ids=department_ids,
            knowledge_base_id=normalized_knowledge_base_id,
            department_id=normalized_department_id,
            status=status,
            is_published=is_published,
            is_current=is_current,
            include_all_departments=include_all_departments,
            published_only=published_only,
            managed_department_ids=managed_department_ids,
        )

        return DocumentListResult(
            documents=tuple(
                self._to_view(document)
                for document in documents
            ),
            total=total,
            offset=offset,
            limit=limit,
        )

    # ---------------------------------------------------------
    # Get document
    # ---------------------------------------------------------

    async def get_document(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        department_ids: tuple[str, ...],
        include_all_departments: bool = False,
        published_only: bool = False,
        managed_department_ids: tuple[str, ...] = (),
    ) -> DocumentView:
        """
        Return one document only when it is accessible
        to the authenticated organization user.
        """

        document = await self.repository.get_by_id(
            document_id=document_id,
            organization_id=organization_id,
            user_id=user_id,
            department_ids=department_ids,
            include_all_departments=include_all_departments,
            published_only=published_only,
            managed_department_ids=managed_department_ids,
        )

        if document is None:
            raise DocumentNotFoundError(
                details={"document_id": document_id}
            )

        return self._to_view(document)

    # ---------------------------------------------------------
    # Publish / unpublish / archive document
    # ---------------------------------------------------------

    async def publish_document(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        actor_role: str,
        department_ids: tuple[str, ...],
        vector_store: VectorLifecycleProtocol,
        actor_department_role: str | None = None,
        include_all_departments: bool = False,
    ) -> DocumentView:
        """Publish a completed current document and sync vector metadata."""

        document = await self._authorize_and_lock_document(
            document_id=document_id,
            organization_id=organization_id,
            user_id=user_id,
            actor_role=actor_role,
            department_ids=department_ids,
            actor_department_role=actor_department_role,
            include_all_departments=include_all_departments,
            action_label="publish",
        )

        if not document.is_current or document.status == DocumentStatus.ARCHIVED.value:
            raise ConflictError(
                message="Archived or superseded document versions cannot be published."
            )

        if document.status not in (
            DocumentStatus.COMPLETED.value,
            DocumentStatus.PUBLISHED.value,
        ):
            raise ConflictError(
                message="Document ingestion must complete before publishing.",
                details={"status": document.status},
            )

        try:
            vector_ids = await self.repository.list_vector_ids(
                document_id=document_id,
                organization_id=organization_id,
                user_id=user_id,
                department_ids=department_ids,
                include_all_departments=include_all_departments,
            )
            updated = await self.repository.mark_published(
                document=document,
                actor_user_id=user_id,
            )
            if vector_ids:
                await asyncio.to_thread(
                    vector_store.set_version_state,
                    vector_ids,
                    is_current=True,
                    is_published=True,
                    status=DocumentStatus.PUBLISHED.value,
                )
            await self.session.commit()
            return self._to_view(updated)
        except Exception:
            await self.session.rollback()
            raise

    async def unpublish_document(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        actor_role: str,
        department_ids: tuple[str, ...],
        vector_store: VectorLifecycleProtocol,
        actor_department_role: str | None = None,
        include_all_departments: bool = False,
    ) -> DocumentView:
        """Unpublish a document back to completed draft state and sync vectors."""

        document = await self._authorize_and_lock_document(
            document_id=document_id,
            organization_id=organization_id,
            user_id=user_id,
            actor_role=actor_role,
            department_ids=department_ids,
            actor_department_role=actor_department_role,
            include_all_departments=include_all_departments,
            action_label="unpublish",
        )

        if document.status not in (
            DocumentStatus.PUBLISHED.value,
            DocumentStatus.COMPLETED.value,
        ):
            raise ConflictError(
                message="Only completed or published documents can be unpublished.",
                details={"status": document.status},
            )

        try:
            vector_ids = await self.repository.list_vector_ids(
                document_id=document_id,
                organization_id=organization_id,
                user_id=user_id,
                department_ids=department_ids,
                include_all_departments=include_all_departments,
            )
            updated = await self.repository.mark_unpublished(document=document)
            if vector_ids:
                await asyncio.to_thread(
                    vector_store.set_version_state,
                    vector_ids,
                    is_current=updated.is_current,
                    is_published=False,
                    status=DocumentStatus.COMPLETED.value,
                )
            await self.session.commit()
            return self._to_view(updated)
        except Exception:
            await self.session.rollback()
            raise

    async def archive_document(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        actor_role: str,
        department_ids: tuple[str, ...],
        vector_store: VectorLifecycleProtocol,
        actor_department_role: str | None = None,
        include_all_departments: bool = False,
    ) -> DocumentView:
        """Archive a document and exclude its vectors from active retrieval."""

        document = await self._authorize_and_lock_document(
            document_id=document_id,
            organization_id=organization_id,
            user_id=user_id,
            actor_role=actor_role,
            department_ids=department_ids,
            actor_department_role=actor_department_role,
            include_all_departments=include_all_departments,
            action_label="archive",
        )

        if document.status in (
            DocumentStatus.PENDING.value,
            DocumentStatus.PROCESSING.value,
            DocumentStatus.DELETING.value,
        ):
            raise ConflictError(
                message="Document cannot be archived while processing or deleting.",
                details={"status": document.status},
            )

        try:
            vector_ids = await self.repository.list_vector_ids(
                document_id=document_id,
                organization_id=organization_id,
                user_id=user_id,
                department_ids=department_ids,
                include_all_departments=include_all_departments,
            )
            updated = await self.repository.mark_archived(
                document=document,
                actor_user_id=user_id,
            )
            if vector_ids:
                await asyncio.to_thread(
                    vector_store.set_version_state,
                    vector_ids,
                    is_current=False,
                    is_published=False,
                    status=DocumentStatus.ARCHIVED.value,
                )
            await self.session.commit()
            return self._to_view(updated)
        except Exception:
            await self.session.rollback()
            raise

    async def _authorize_and_lock_document(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        actor_role: str,
        department_ids: tuple[str, ...],
        actor_department_role: str | None,
        include_all_departments: bool,
        action_label: str,
    ) -> DocumentRecord:
        is_organization_admin = can_manage_departments(actor_role)

        if include_all_departments and not is_organization_admin:
            raise AuthorizationError(
                message=(
                    "Only organization owners or admins can access "
                    "documents across all departments."
                )
            )

        document = await self.repository.get_by_id_for_update(
            document_id=document_id,
            organization_id=organization_id,
            user_id=user_id,
            department_ids=department_ids,
            include_all_departments=include_all_departments,
        )

        if document is None:
            raise DocumentNotFoundError(
                details={"document_id": document_id}
            )

        is_department_admin = (
            actor_department_role == DepartmentRole.DEPARTMENT_ADMIN.value
            and document.department_id is not None
            and document.department_id in department_ids
        )

        if not (is_organization_admin or is_department_admin):
            raise AuthorizationError(
                message=(
                    f"Only organization owners/admins or the Department Admin "
                    f"of the document's department can {action_label} this document."
                )
            )

        return document

    # ---------------------------------------------------------
    # Delete document
    # ---------------------------------------------------------

    async def delete_document(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        actor_role: str,
        department_ids: tuple[str, ...],
        vector_store: VectorDeletionProtocol,
        actor_department_role: str | None = None,
        include_all_departments: bool = False,
    ) -> None:
        """
        Completely delete one document.

        Authorization:
        1. Organization Owner/Admin can delete documents across
           the organization.
        2. Department Admin can delete documents belonging
           to their own department.
        3. Employees, Viewers and ordinary Members cannot delete.

        Deletion order:
        1. Lock MySQL document row.
        2. Load vector IDs.
        3. Mark document as deleting.
        4. Delete vectors from vector store.
        5. Delete document from MySQL.
        6. Commit transaction.
        """

        is_organization_admin = can_manage_departments(actor_role)

        # Only organization owners/admins may use the
        # include_all_departments override.
        if include_all_departments and not is_organization_admin:
            raise AuthorizationError(
                message=(
                    "Only organization owners or admins can access "
                    "documents across all departments."
                )
            )

        document = await self.repository.get_by_id_for_update(
            document_id=document_id,
            organization_id=organization_id,
            user_id=user_id,
            department_ids=department_ids,
            include_all_departments=include_all_departments,
        )

        if document is None:
            raise DocumentNotFoundError(
                details={"document_id": document_id}
            )

        is_department_admin = (
            actor_department_role
            == DepartmentRole.DEPARTMENT_ADMIN.value
            and document.department_id is not None
            and document.department_id in department_ids
        )

        if not (is_organization_admin or is_department_admin):
            raise AuthorizationError(
                message=(
                    "Only organization owners/admins or the "
                    "Department Admin of the document's department "
                    "can delete this document."
                )
            )

        try:
            # -------------------------------------------------
            # Load vector IDs while MySQL references still exist
            # -------------------------------------------------

            vector_ids = await self.repository.list_vector_ids(
                document_id=document_id,
                organization_id=organization_id,
                user_id=user_id,
                department_ids=department_ids,
                include_all_departments=include_all_departments,
            )

            # -------------------------------------------------
            # Mark document as deleting
            # -------------------------------------------------

            await self.repository.update_status(
                document_id=document_id,
                organization_id=organization_id,
                user_id=user_id,
                department_ids=department_ids,
                status=DocumentStatus.DELETING,
                error_message=None,
                include_all_departments=include_all_departments,
            )

            # -------------------------------------------------
            # Delete external vectors first
            # -------------------------------------------------

            if vector_ids:
                await asyncio.to_thread(
                    vector_store.delete_chunks,
                    vector_ids,
                )

            # -------------------------------------------------
            # Delete MySQL document
            # -------------------------------------------------

            deleted = await self.repository.delete_document_record(
                document_id=document_id,
                organization_id=organization_id,
                user_id=user_id,
                department_ids=department_ids,
                include_all_departments=include_all_departments,
            )

            if not deleted:
                raise DocumentNotFoundError(
                    details={"document_id": document_id}
                )

            # -------------------------------------------------
            # Commit complete deletion
            # -------------------------------------------------

            await self.session.commit()

        except AppError:
            await self.session.rollback()
            raise

        except Exception as exc:
            await self.session.rollback()

            raise DocumentDeletionError(
                details={
                    "document_id": document_id,
                    "error_type": type(exc).__name__,
                }
            ) from None