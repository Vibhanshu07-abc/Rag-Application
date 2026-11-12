from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    and_,
    func,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import (
    DatabaseError,
    DocumentNotFoundError,
    DuplicateDocumentError,
)
from backend.app.database.enums import (
    DocumentVisibility,
    DocumentStatus,
)
from backend.app.database.models import (
    DocumentChunkRecord,
    DocumentRecord,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class ChunkReference:
    """
    Information required to connect one MySQL
    chunk record with one vector stored in Chroma.
    """

    chunk_id: str
    vector_id: str
    page_number: int
    chunk_index: int


class DocumentRepository:
    """
    MySQL operations for documents and chunk references.

    Every read operation is scoped by organization visibility
    and the requester's active department memberships.

    Organization administrators can explicitly request access
    to all departments using include_all_departments=True.

    The uploader user_id remains audit metadata and is not
    a read-access boundary.

    The repository flushes changes but does not normally commit.
    Transaction control belongs to services.
    """

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    # ---------------------------------------------------------
    # Get document
    # ---------------------------------------------------------

    async def get_by_id(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        department_ids: Sequence[str] = (),
        include_all_departments: bool = False,
        published_only: bool = False,
        managed_department_ids: Sequence[str] = (),
    ) -> DocumentRecord | None:

        statement = (
            select(DocumentRecord)
            .where(
                DocumentRecord.id == document_id,
                DocumentRecord.organization_id == organization_id,
                self._access_condition(
                    department_ids=department_ids,
                    include_all_departments=include_all_departments,
                    published_only=published_only,
                    managed_department_ids=managed_department_ids,
                ),
            )
        )

        result = await self.session.execute(statement)

        return result.scalar_one_or_none()

    # ---------------------------------------------------------
    # Get document with row lock
    # ---------------------------------------------------------

    async def get_by_id_for_update(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        department_ids: Sequence[str] = (),
        include_all_departments: bool = False,
    ) -> DocumentRecord | None:

        statement = (
            select(DocumentRecord)
            .where(
                DocumentRecord.id == document_id,
                DocumentRecord.organization_id == organization_id,
                self._visibility_condition(
                    department_ids,
                    include_all_departments,
                ),
            )
            .with_for_update()
        )

        result = await self.session.execute(statement)

        return result.scalar_one_or_none()

    # ---------------------------------------------------------
    # Duplicate lookup
    # ---------------------------------------------------------

    async def get_by_hash(
        self,
        *,
        organization_id: str,
        user_id: str,
        knowledge_base_id: str,
        file_hash: str,
    ) -> DocumentRecord | None:

        statement = (
            select(DocumentRecord)
            .where(
                DocumentRecord.organization_id == organization_id,
                DocumentRecord.knowledge_base_id == knowledge_base_id,
                DocumentRecord.file_hash == file_hash,
                DocumentRecord.is_current.is_(True),
                DocumentRecord.status.notin_(
                    (
                        DocumentStatus.FAILED.value,
                        DocumentStatus.ARCHIVED.value,
                        DocumentStatus.DELETING.value,
                    )
                ),
            )
            .limit(1)
        )

        result = await self.session.execute(statement)

        return result.scalar_one_or_none()

    async def get_next_version_number(
        self,
        *,
        organization_id: str,
        knowledge_base_id: str,
        logical_document_id: str,
    ) -> int:
        statement = select(
            func.coalesce(func.max(DocumentRecord.version_number), 0) + 1
        ).where(
            DocumentRecord.organization_id == organization_id,
            DocumentRecord.knowledge_base_id == knowledge_base_id,
            DocumentRecord.logical_document_id == logical_document_id,
        )
        result = await self.session.execute(statement)
        return int(result.scalar_one())

    # ---------------------------------------------------------
    # Create document
    # ---------------------------------------------------------

    async def create_document(
        self,
        *,
        organization_id: str,
        user_id: str,
        visibility: DocumentVisibility = DocumentVisibility.ORGANIZATION,
        department_id: str | None = None,
        knowledge_base_id: str,
        filename: str,
        file_hash: str,
        file_size_bytes: int,
        logical_document_id: str | None = None,
        version_number: int = 1,
        is_current: bool = True,
        total_pages: int | None = None,
    ) -> DocumentRecord:

        document_id = str(uuid4())

        document = DocumentRecord(
            id=document_id,
            organization_id=organization_id,
            user_id=user_id,
            visibility=visibility.value,
            department_id=department_id,
            knowledge_base_id=knowledge_base_id,
            logical_document_id=logical_document_id or document_id,
            version_number=version_number,
            is_current=is_current,
            is_published=False,
            filename=filename,
            file_hash=file_hash,
            file_size_bytes=file_size_bytes,
            total_pages=total_pages,
            status=DocumentStatus.PENDING.value,
        )

        try:
            async with self.session.begin_nested():
                self.session.add(document)
                await self.session.flush()

        except IntegrityError as exc:
            raise DuplicateDocumentError(
                details={
                    "organization_id": organization_id,
                    "user_id": user_id,
                    "knowledge_base_id": knowledge_base_id,
                    "file_hash": file_hash,
                }
            ) from exc

        return document

    # ---------------------------------------------------------
    # Version lifecycle & publishing
    # ---------------------------------------------------------

    async def archive_current_version(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        department_ids: Sequence[str] = (),
        include_all_departments: bool = False,
    ) -> DocumentRecord:

        document = await self.get_by_id_for_update(
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

        document.is_current = False
        document.is_published = False
        document.status = DocumentStatus.ARCHIVED.value
        document.archived_at = _utc_now()
        document.archived_by_user_id = user_id

        await self.session.flush()
        await self.session.refresh(document)

        return document

    async def activate_version(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        department_ids: Sequence[str] = (),
        include_all_departments: bool = False,
    ) -> DocumentRecord:
        document = await self.get_by_id_for_update(
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

        document.is_current = True
        await self.session.flush()
        await self.session.refresh(document)

        return document

    async def mark_published(
        self,
        *,
        document: DocumentRecord,
        actor_user_id: str,
    ) -> DocumentRecord:
        document.status = DocumentStatus.PUBLISHED.value
        document.is_published = True
        document.published_at = _utc_now()
        document.published_by_user_id = actor_user_id
        await self.session.flush()
        await self.session.refresh(document)
        return document

    async def mark_unpublished(
        self,
        *,
        document: DocumentRecord,
    ) -> DocumentRecord:
        document.status = DocumentStatus.COMPLETED.value
        document.is_published = False
        await self.session.flush()
        await self.session.refresh(document)
        return document

    async def mark_archived(
        self,
        *,
        document: DocumentRecord,
        actor_user_id: str,
    ) -> DocumentRecord:
        document.status = DocumentStatus.ARCHIVED.value
        document.is_published = False
        document.is_current = False
        document.archived_at = _utc_now()
        document.archived_by_user_id = actor_user_id
        await self.session.flush()
        await self.session.refresh(document)
        return document

    # ---------------------------------------------------------
    # Add chunk references
    # ---------------------------------------------------------

    async def add_chunk_references(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        chunk_references: Sequence[ChunkReference],
    ) -> list[DocumentChunkRecord]:

        document = await self.get_by_id(
            document_id=document_id,
            organization_id=organization_id,
            user_id=user_id,
            include_all_departments=True,
        )

        if document is None:
            raise DocumentNotFoundError(
                details={"document_id": document_id}
            )

        if not chunk_references:
            return []

        records = [
            DocumentChunkRecord(
                document_record_id=document.id,
                chunk_id=reference.chunk_id,
                vector_id=reference.vector_id,
                page_number=reference.page_number,
                chunk_index=reference.chunk_index,
            )
            for reference in chunk_references
        ]

        try:
            self.session.add_all(records)

            document.chunk_count += len(records)

            await self.session.flush()

        except IntegrityError as exc:
            raise DatabaseError(
                message=(
                    "One or more chunk references "
                    "could not be stored."
                ),
                details={
                    "document_id": document_id,
                    "error_type": type(exc).__name__,
                },
            ) from exc

        return records

    # ---------------------------------------------------------
    # Update status
    # ---------------------------------------------------------

    async def update_status(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        department_ids: Sequence[str] = (),
        include_all_departments: bool = False,
        status: DocumentStatus,
        embedding_provider: str | None = None,
        embedding_model: str | None = None,
        embedding_dimension: int | None = None,
        vector_store_provider: str | None = None,
        total_pages: int | None = None,
        error_message: str | None = None,
    ) -> DocumentRecord:

        document = await self.get_by_id(
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

        document.status = status.value

        if status == DocumentStatus.FAILED:
            document.is_current = False
            document.is_published = False

        if embedding_provider is not None:
            document.embedding_provider = embedding_provider

        if embedding_model is not None:
            document.embedding_model = embedding_model

        if embedding_dimension is not None:
            document.embedding_dimension = embedding_dimension

        if vector_store_provider is not None:
            document.vector_store_provider = vector_store_provider

        if total_pages is not None:
            document.total_pages = total_pages

        document.error_message = error_message

        await self.session.flush()

        return document

    # ---------------------------------------------------------
    # List documents
    # ---------------------------------------------------------

    async def list_documents(
        self,
        *,
        organization_id: str,
        user_id: str,
        department_ids: Sequence[str] = (),
        knowledge_base_id: str | None = None,
        department_id: str | None = None,
        status: str | None = None,
        is_published: bool | None = None,
        is_current: bool | None = None,
        offset: int = 0,
        limit: int = 50,
        include_all_departments: bool = False,
        published_only: bool = False,
        managed_department_ids: Sequence[str] = (),
    ) -> list[DocumentRecord]:

        statement = (
            select(DocumentRecord)
            .where(
                DocumentRecord.organization_id == organization_id,
                self._access_condition(
                    department_ids=department_ids,
                    include_all_departments=include_all_departments,
                    published_only=published_only,
                    managed_department_ids=managed_department_ids,
                ),
            )
        )

        statement = self._apply_filters(
            statement=statement,
            knowledge_base_id=knowledge_base_id,
            department_id=department_id,
            status=status,
            is_published=is_published,
            is_current=is_current,
        )

        statement = (
            statement
            .order_by(DocumentRecord.created_at.desc())
            .offset(offset)
            .limit(limit)
        )

        result = await self.session.execute(statement)

        return list(result.scalars().all())

    # ---------------------------------------------------------
    # Count documents
    # ---------------------------------------------------------

    async def count_documents(
        self,
        *,
        organization_id: str,
        user_id: str,
        department_ids: Sequence[str] = (),
        knowledge_base_id: str | None = None,
        department_id: str | None = None,
        status: str | None = None,
        is_published: bool | None = None,
        is_current: bool | None = None,
        include_all_departments: bool = False,
        published_only: bool = False,
        managed_department_ids: Sequence[str] = (),
    ) -> int:

        statement = (
            select(func.count(DocumentRecord.id))
            .where(
                DocumentRecord.organization_id == organization_id,
                self._access_condition(
                    department_ids=department_ids,
                    include_all_departments=include_all_departments,
                    published_only=published_only,
                    managed_department_ids=managed_department_ids,
                ),
            )
        )

        statement = self._apply_filters(
            statement=statement,
            knowledge_base_id=knowledge_base_id,
            department_id=department_id,
            status=status,
            is_published=is_published,
            is_current=is_current,
        )

        result = await self.session.execute(statement)

        return int(result.scalar_one())

    @staticmethod
    def _apply_filters(
        *,
        statement,
        knowledge_base_id: str | None,
        department_id: str | None,
        status: str | None,
        is_published: bool | None,
        is_current: bool | None,
    ):
        if knowledge_base_id is not None:
            statement = statement.where(
                DocumentRecord.knowledge_base_id == knowledge_base_id
            )
        if department_id is not None:
            statement = statement.where(
                DocumentRecord.department_id == department_id
            )
        if status is not None:
            statement = statement.where(
                DocumentRecord.status == status
            )
        if is_published is not None:
            statement = statement.where(
                DocumentRecord.is_published.is_(is_published)
            )
        if is_current is not None:
            statement = statement.where(
                DocumentRecord.is_current.is_(is_current)
            )
        return statement

    # ---------------------------------------------------------
    # List vector IDs
    # ---------------------------------------------------------

    async def list_vector_ids(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        department_ids: Sequence[str] = (),
        include_all_departments: bool = False,
    ) -> list[str]:

        document = await self.get_by_id(
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

        statement = (
            select(DocumentChunkRecord.vector_id)
            .where(
                DocumentChunkRecord.document_record_id == document.id
            )
            .order_by(DocumentChunkRecord.chunk_index.asc())
        )

        result = await self.session.execute(statement)

        return list(result.scalars().all())

    # ---------------------------------------------------------
    # Delete document
    # ---------------------------------------------------------

    async def delete_document_record(
        self,
        *,
        document_id: str,
        organization_id: str,
        user_id: str,
        department_ids: Sequence[str] = (),
        include_all_departments: bool = False,
    ) -> bool:

        document = await self.get_by_id(
            document_id=document_id,
            organization_id=organization_id,
            user_id=user_id,
            department_ids=department_ids,
            include_all_departments=include_all_departments,
        )

        if document is None:
            return False

        await self.session.delete(document)

        await self.session.flush()

        return True

    # ---------------------------------------------------------
    # Visibility condition
    # ---------------------------------------------------------

    @classmethod
    def _access_condition(
        cls,
        *,
        department_ids: Sequence[str],
        include_all_departments: bool = False,
        published_only: bool = False,
        managed_department_ids: Sequence[str] = (),
    ):
        visibility_expr = cls._visibility_condition(
            department_ids,
            include_all_departments,
        )
        if include_all_departments or not published_only:
            return visibility_expr

        published_current_expr = and_(
            visibility_expr,
            DocumentRecord.is_published.is_(True),
            DocumentRecord.is_current.is_(True),
            DocumentRecord.status == DocumentStatus.PUBLISHED.value,
        )

        managed_ids = tuple(
            dept_id for dept_id in managed_department_ids if dept_id in set(department_ids)
        )
        if not managed_ids:
            return published_current_expr

        managed_dept_expr = and_(
            DocumentRecord.visibility == DocumentVisibility.DEPARTMENT.value,
            DocumentRecord.department_id.in_(managed_ids),
        )
        return or_(published_current_expr, managed_dept_expr)

    @staticmethod
    def _visibility_condition(
        department_ids: Sequence[str],
        include_all_departments: bool = False,
    ):
        conditions = [
            DocumentRecord.visibility
            == DocumentVisibility.ORGANIZATION.value,
        ]

        if include_all_departments:
            conditions.append(
                DocumentRecord.visibility
                == DocumentVisibility.DEPARTMENT.value
            )

        elif department_ids:
            conditions.append(
                and_(
                    DocumentRecord.visibility
                    == DocumentVisibility.DEPARTMENT.value,
                    DocumentRecord.department_id.in_(department_ids),
                )
            )

        return or_(*conditions)