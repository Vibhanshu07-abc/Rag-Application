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
from backend.app.core.exceptions import DocumentNotFoundError
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


router = APIRouter(
    prefix="/documents",
    tags=["Documents"],
)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------


def is_organization_admin(access: OrganizationAccess) -> bool:
    """
    Return True only for organization owners and administrators.
    """

    return access.role in (
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
    )


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
        archived_at=document.archived_at,
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
        require_document_read_access
    ),
    session: AsyncSession = Depends(
        get_database_session_dependency
    ),
) -> DocumentJobResponse:
    """
    Return the requesting uploader's durable ingestion job state.
    """

    job = await DocumentJobRepository(session).get_for_user(
        job_id=job_id,
        organization_id=access.organization_id,
        user_id=access.user_id,
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
        require_document_write_access
    ),
    session: AsyncSession = Depends(
        get_database_session_dependency
    ),
) -> DocumentJobResponse:
    """
    Safely requeue only the uploader's terminal failed job.
    """

    repository = DocumentJobRepository(session)

    job = await repository.reset_failed_for_retry(
        job_id=job_id,
        organization_id=access.organization_id,
        user_id=access.user_id,
    )

    if job is None:
        raise DocumentNotFoundError(
            details={"job_id": job_id}
        )

    await session.commit()

    await AuditRepository(session).record(
        organization_id=access.organization_id,
        actor_user_id=access.user_id,
        event_type="document_ingestion_retry_requested",
        resource_type="document_ingestion_job",
        resource_id=job.id,
    )

    await session.commit()

    await RedisDocumentJobQueue(
        get_redis_client()
    ).enqueue(job.id)

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

    Organization owners/admins can see documents in all departments.
    Other users can see organization-wide documents and documents
    belonging to their active departments.
    """

    service = build_document_service(session)

    department_ids = await active_department_ids(
        session=session,
        access=access,
    )

    include_all_departments = is_organization_admin(access)

    result = await service.list_documents(
        organization_id=access.organization_id,
        user_id=access.user_id,
        department_ids=department_ids,
        knowledge_base_id=knowledge_base_id,
        offset=offset,
        limit=limit,
        include_all_departments=include_all_departments,
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

    Organization owners/admins can access documents across
    all departments in their organization.
    """

    service = build_document_service(session)

    department_ids = await active_department_ids(
        session=session,
        access=access,
    )

    document = await service.get_document(
        document_id=document_id,
        organization_id=access.organization_id,
        user_id=access.user_id,
        department_ids=department_ids,
        include_all_departments=is_organization_admin(access),
    )

    return build_document_response(document)


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

    # Load the document first to identify its knowledge base.
    # Organization admins can load documents from every department.
    document = await service.get_document(
        document_id=document_id,
        organization_id=access.organization_id,
        user_id=access.user_id,
        department_ids=department_ids,
        include_all_departments=organization_admin,
    )

    actor_department_role = None

    if document.department_id is not None:
        membership = await DepartmentRepository(
            session
        ).get_active_membership(
            department_id=document.department_id,
            organization_id=access.organization_id,
            user_id=access.user_id,
        )

        if membership is not None:
            actor_department_role = str(membership.role)

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
        # Chroma close is synchronous.
        close_method = getattr(
            vector_store,
            "close",
            None,
        )

        if callable(close_method):
            await asyncio.to_thread(close_method)

    return Response(
        status_code=status.HTTP_204_NO_CONTENT
    )