import asyncio

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import (
    OrganizationAccess,
    get_database_session_dependency,
    get_embedding_provider_dependency,
    get_organization_access,
    is_organization_admin,
    require_document_read_access,
    require_document_write_access,
    require_organization_roles,
)
from backend.app.api.document_schemas import (
    DocumentJobResponse,
    DocumentListResponse,
    DocumentMonitoringResponse,
    DocumentResponse,
)
from backend.app.auth.roles import (
    DepartmentRole,
    OrganizationRole,
)
from backend.app.core.exceptions import (
    AuthorizationError,
    DocumentNotFoundError,
)
from backend.app.core.logging import get_logger
from backend.app.database.repositories import DocumentRepository
from backend.app.database.repositories.audit_repository import AuditRepository
from backend.app.database.repositories.department_repository import (
    DepartmentRepository,
)
from backend.app.database.repositories.document_job_repository import (
    DocumentJobRepository,
)
from backend.app.embeddings.base import BaseEmbeddingProvider
from backend.app.rate_limiting.service import get_redis_client
from backend.app.services.document_job_queue import RedisDocumentJobQueue
from backend.app.services.document_management import (
    DocumentManagementService,
    DocumentView,
)
from backend.app.services.worker_telemetry import WorkerTelemetry
from backend.app.vectorstores.factory import get_vector_store


logger = get_logger(__name__)

router = APIRouter(
    prefix="/documents",
    tags=["Documents"],
)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------


def build_document_response(
    document: DocumentView,
) -> DocumentResponse:
    """
    Convert service-layer document view into FastAPI response schema.
    """

    return DocumentResponse(
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


def build_document_service(
    session: AsyncSession,
) -> DocumentManagementService:
    """
    Build document-management service for one database session.
    """

    return DocumentManagementService(
        session=session,
        repository=DocumentRepository(session),
    )


async def active_department_ids(
    *,
    session: AsyncSession,
    access: OrganizationAccess,
) -> tuple[str, ...]:
    """
    Return the authenticated user's active department memberships.
    """

    department_ids = await DepartmentRepository(
        session
    ).list_active_department_ids(
        organization_id=access.organization_id,
        user_id=access.user_id,
    )

    return tuple(department_ids)


async def managed_department_ids(
    *,
    session: AsyncSession,
    access: OrganizationAccess,
) -> tuple[str, ...]:
    """Return active department IDs where the user is Department Admin."""

    memberships = await DepartmentRepository(
        session
    ).list_active_memberships_for_user(
        organization_id=access.organization_id,
        user_id=access.user_id,
    )

    return tuple(
        department.id
        for membership, department in memberships
        if membership.role == DepartmentRole.DEPARTMENT_ADMIN.value
    )


async def resolve_department_role_for_document(
    *,
    session: AsyncSession,
    access: OrganizationAccess,
    department_id: str | None,
) -> str | None:
    if department_id is None:
        return None
    membership = await DepartmentRepository(session).get_active_membership(
        department_id=department_id,
        organization_id=access.organization_id,
        user_id=access.user_id,
    )
    return str(membership.role) if membership is not None else None


async def close_vector_store_async(vector_store: object) -> None:
    close_method = getattr(vector_store, "close", None)
    if callable(close_method):
        await asyncio.to_thread(close_method)


# ---------------------------------------------------------
# Document ingestion jobs
# ---------------------------------------------------------


@router.get(
    "/jobs/{job_id}",
    response_model=DocumentJobResponse,
)
async def get_document_job(
    job_id: str,
    access: OrganizationAccess = Depends(
        get_organization_access
    ),
    session: AsyncSession = Depends(
        get_database_session_dependency
    ),
) -> DocumentJobResponse:
    """
    Return an ingestion job's state for authorized organization or department admins.
    """

    org_admin = is_organization_admin(access)
    managed_depts = (
        () if org_admin else await managed_department_ids(session=session, access=access)
    )
    if not org_admin and not managed_depts:
        raise AuthorizationError(
            message="Only organization or department administrators can inspect ingestion jobs."
        )

    job = await DocumentJobRepository(session).get_for_actor(
        job_id=job_id,
        organization_id=access.organization_id,
        include_all_departments=org_admin,
        managed_department_ids=managed_depts,
    )

    if job is None:
        raise DocumentNotFoundError(
            details={"job_id": job_id}
        )

    return DocumentJobResponse(
        id=job.id,
        status=job.status,
        filename=job.filename,
        document_id=job.document_id,
        error_message=job.error_message,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        next_attempt_at=job.next_attempt_at,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.post(
    "/jobs/{job_id}/retry",
    response_model=DocumentJobResponse,
)
async def retry_document_job(
    job_id: str,
    access: OrganizationAccess = Depends(
        get_organization_access
    ),
    session: AsyncSession = Depends(
        get_database_session_dependency
    ),
) -> DocumentJobResponse:
    """
    Safely requeue a terminal failed job for authorized organization or department admins.
    """

    org_admin = is_organization_admin(access)
    managed_depts = (
        () if org_admin else await managed_department_ids(session=session, access=access)
    )
    if not org_admin and not managed_depts:
        raise AuthorizationError(
            message="Only organization or department administrators can retry ingestion jobs."
        )

    repository = DocumentJobRepository(session)

    job = await repository.reset_failed_for_retry(
        job_id=job_id,
        organization_id=access.organization_id,
        include_all_departments=org_admin,
        managed_department_ids=managed_depts,
    )

    if job is None:
        raise DocumentNotFoundError(
            details={"job_id": job_id}
        )

    await AuditRepository(session).record(
        organization_id=access.organization_id,
        actor_user_id=access.user_id,
        event_type="document_ingestion_retry_requested",
        resource_type="document_ingestion_job",
        resource_id=job.id,
    )

    await session.commit()

    try:
        await RedisDocumentJobQueue(
            get_redis_client()
        ).enqueue(job.id)
    except Exception as exc:
        logger.warning(
            "document_job_retry_enqueue_deferred",
            job_id=job.id,
            error_type=type(exc).__name__,
        )

    return DocumentJobResponse(
        id=job.id,
        status=job.status,
        filename=job.filename,
        document_id=job.document_id,
        error_message=job.error_message,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        next_attempt_at=job.next_attempt_at,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


# ---------------------------------------------------------
# Document monitoring
# ---------------------------------------------------------


@router.get(
    "/monitoring",
    response_model=DocumentMonitoringResponse,
)
async def document_monitoring(
    access: OrganizationAccess = Depends(
        require_organization_roles(
            OrganizationRole.OWNER,
            OrganizationRole.ADMIN,
        )
    ),
    session: AsyncSession = Depends(
        get_database_session_dependency
    ),
) -> DocumentMonitoringResponse:
    """
    Organization-admin view of queued work and worker utilization.
    """

    job_counts = await DocumentJobRepository(
        session
    ).status_counts(access.organization_id)

    workers = await WorkerTelemetry(
        get_redis_client()
    ).snapshots()

    return DocumentMonitoringResponse(
        job_counts=job_counts,
        workers=workers,
    )


# ---------------------------------------------------------
# List documents
# ---------------------------------------------------------


@router.get(
    "",
    response_model=DocumentListResponse,
)
async def list_documents(
    access: OrganizationAccess = Depends(
        require_document_read_access
    ),
    session: AsyncSession = Depends(
        get_database_session_dependency
    ),
    knowledge_base_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=128,
    ),
    department_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=64,
    ),
    document_status: str | None = Query(
        default=None,
        alias="status",
        min_length=1,
        max_length=32,
    ),
    is_published: bool | None = Query(default=None),
    is_current: bool | None = Query(default=None),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=100,
    ),
) -> DocumentListResponse:
    """
    List documents accessible to the authenticated user.

    Organization owners/admins see all documents in all departments.
    Department Admins see all documents in their own managed departments
    and only published current organization/other-department documents.
    Employees see only published, current, authorized documents.
    """

    service = build_document_service(session)

    department_ids = await active_department_ids(
        session=session,
        access=access,
    )
    include_all_departments = is_organization_admin(access)
    managed_depts = (
        ()
        if include_all_departments
        else await managed_department_ids(session=session, access=access)
    )

    result = await service.list_documents(
        organization_id=access.organization_id,
        user_id=access.user_id,
        department_ids=department_ids,
        knowledge_base_id=knowledge_base_id,
        department_id=department_id,
        status=document_status,
        is_published=is_published,
        is_current=is_current,
        offset=offset,
        limit=limit,
        include_all_departments=include_all_departments,
        published_only=not include_all_departments,
        managed_department_ids=managed_depts,
    )

    return DocumentListResponse(
        documents=[
            build_document_response(document)
            for document in result.documents
        ],
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


# ---------------------------------------------------------
# Document detail
# ---------------------------------------------------------


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
)
async def get_document(
    document_id: str,
    access: OrganizationAccess = Depends(
        require_document_read_access
    ),
    session: AsyncSession = Depends(
        get_database_session_dependency
    ),
) -> DocumentResponse:
    """
    Return one document accessible to the authenticated user.
    """

    service = build_document_service(session)

    department_ids = await active_department_ids(
        session=session,
        access=access,
    )
    org_admin = is_organization_admin(access)
    managed_depts = (
        ()
        if org_admin
        else await managed_department_ids(session=session, access=access)
    )

    document = await service.get_document(
        document_id=document_id,
        organization_id=access.organization_id,
        user_id=access.user_id,
        department_ids=department_ids,
        include_all_departments=org_admin,
        published_only=not org_admin,
        managed_department_ids=managed_depts,
    )

    return build_document_response(document)


# ---------------------------------------------------------
# Document publishing and archiving
# ---------------------------------------------------------


async def _run_lifecycle_mutation(
    *,
    document_id: str,
    action: str,
    access: OrganizationAccess,
    session: AsyncSession,
    embedding_provider: BaseEmbeddingProvider,
) -> DocumentView:
    service = build_document_service(session)
    department_ids = await active_department_ids(session=session, access=access)
    org_admin = is_organization_admin(access)
    managed_depts = (
        () if org_admin else await managed_department_ids(session=session, access=access)
    )

    document = await service.get_document(
        document_id=document_id,
        organization_id=access.organization_id,
        user_id=access.user_id,
        department_ids=department_ids,
        include_all_departments=org_admin,
        published_only=not org_admin,
        managed_department_ids=managed_depts,
    )

    actor_department_role = await resolve_department_role_for_document(
        session=session,
        access=access,
        department_id=document.department_id,
    )

    vector_store = get_vector_store(
        embedding_provider=embedding_provider,
        organization_id=access.organization_id,
        user_id=access.user_id,
        knowledge_base_id=document.knowledge_base_id,
    )

    try:
        method = getattr(service, f"{action}_document")
        updated = await method(
            document_id=document_id,
            organization_id=access.organization_id,
            user_id=access.user_id,
            actor_role=access.role,
            actor_department_role=actor_department_role,
            department_ids=department_ids,
            vector_store=vector_store,
            include_all_departments=org_admin,
        )
        await AuditRepository(session).record(
            organization_id=access.organization_id,
            actor_user_id=access.user_id,
            event_type=f"document_{action}ed" if action != "archive" else "document_archived",
            resource_type="document",
            resource_id=document_id,
            details={"knowledge_base_id": document.knowledge_base_id},
        )
        await session.commit()
        return updated
    finally:
        await close_vector_store_async(vector_store)


@router.post(
    "/{document_id}/publish",
    response_model=DocumentResponse,
)
async def publish_document(
    document_id: str,
    access: OrganizationAccess = Depends(get_organization_access),
    session: AsyncSession = Depends(get_database_session_dependency),
    embedding_provider: BaseEmbeddingProvider = Depends(
        get_embedding_provider_dependency
    ),
) -> DocumentResponse:
    updated = await _run_lifecycle_mutation(
        document_id=document_id,
        action="publish",
        access=access,
        session=session,
        embedding_provider=embedding_provider,
    )
    return build_document_response(updated)


@router.post(
    "/{document_id}/unpublish",
    response_model=DocumentResponse,
)
async def unpublish_document(
    document_id: str,
    access: OrganizationAccess = Depends(get_organization_access),
    session: AsyncSession = Depends(get_database_session_dependency),
    embedding_provider: BaseEmbeddingProvider = Depends(
        get_embedding_provider_dependency
    ),
) -> DocumentResponse:
    updated = await _run_lifecycle_mutation(
        document_id=document_id,
        action="unpublish",
        access=access,
        session=session,
        embedding_provider=embedding_provider,
    )
    return build_document_response(updated)


@router.post(
    "/{document_id}/archive",
    response_model=DocumentResponse,
)
async def archive_document(
    document_id: str,
    access: OrganizationAccess = Depends(get_organization_access),
    session: AsyncSession = Depends(get_database_session_dependency),
    embedding_provider: BaseEmbeddingProvider = Depends(
        get_embedding_provider_dependency
    ),
) -> DocumentResponse:
    updated = await _run_lifecycle_mutation(
        document_id=document_id,
        action="archive",
        access=access,
        session=session,
        embedding_provider=embedding_provider,
    )
    return build_document_response(updated)


# ---------------------------------------------------------
# Delete document
# ---------------------------------------------------------


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    document_id: str,
    access: OrganizationAccess = Depends(
        get_organization_access
    ),
    session: AsyncSession = Depends(
        get_database_session_dependency
    ),
    embedding_provider: BaseEmbeddingProvider = Depends(
        get_embedding_provider_dependency
    ),
) -> Response:
    """
    Delete document metadata, chunk references and vectors.

    Organization owners/admins can delete documents across
    the organization.

    Department Admins can delete documents belonging to
    their own departments.

    Employees and ordinary Members cannot delete documents.
    """

    service = build_document_service(session)

    department_ids = await active_department_ids(
        session=session,
        access=access,
    )

    organization_admin = is_organization_admin(access)
    managed_depts = (
        ()
        if organization_admin
        else await managed_department_ids(session=session, access=access)
    )

    # Load the document first to identify its knowledge base.
    # Organization admins can load documents from every department.
    document = await service.get_document(
        document_id=document_id,
        organization_id=access.organization_id,
        user_id=access.user_id,
        department_ids=department_ids,
        include_all_departments=organization_admin,
        published_only=not organization_admin,
        managed_department_ids=managed_depts,
    )

    actor_department_role = await resolve_department_role_for_document(
        session=session,
        access=access,
        department_id=document.department_id,
    )

    vector_store = get_vector_store(
        embedding_provider=embedding_provider,
        organization_id=access.organization_id,
        user_id=access.user_id,
        knowledge_base_id=document.knowledge_base_id,
    )

    try:
        await service.delete_document(
            document_id=document_id,
            organization_id=access.organization_id,
            user_id=access.user_id,
            actor_role=access.role,
            actor_department_role=actor_department_role,
            department_ids=department_ids,
            vector_store=vector_store,
            include_all_departments=organization_admin,
        )

        await AuditRepository(session).record(
            organization_id=access.organization_id,
            actor_user_id=access.user_id,
            event_type="document_deleted",
            resource_type="document",
            resource_id=document_id,
            details={
                "knowledge_base_id": document.knowledge_base_id,
            },
        )

        await session.commit()

    finally:
        await close_vector_store_async(vector_store)

    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )