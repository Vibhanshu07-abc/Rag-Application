import asyncio
import hashlib
import json
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.dependencies import (
    OrganizationAccess,
    get_database_session_dependency,
    get_embedding_provider_dependency,
    get_llm_provider_dependency,
    get_organization_access,
    is_organization_admin,
    require_document_read_access,
)
from backend.app.api.schemas import (
    DocumentUploadResponse,
    HealthResponse,
    RAGQueryRequest,
    RAGQueryResponse,
    RAGSourceResponse,
)
from backend.app.auth.roles import (
    DepartmentRole,
    can_manage_departments,
)
from backend.app.core.config import settings
from backend.app.core.exceptions import (
    AuthorizationError,
    ConflictError,
    InvalidUploadError,
)
from backend.app.core.logging import get_logger
from backend.app.database.enums import DocumentVisibility
from backend.app.database.repositories import DocumentRepository
from backend.app.database.repositories.audit_repository import AuditRepository
from backend.app.database.repositories.department_repository import (
    DepartmentRepository,
)
from backend.app.database.repositories.document_job_repository import (
    DocumentJobRepository,
)
from backend.app.generation import GroundedAnswerService
from backend.app.rate_limiting.dependencies import (
    require_user_rate_limit,
)
from backend.app.rate_limiting.service import get_redis_client
from backend.app.retrieval import RetrievalService
from backend.app.services.document_file_storage import DocumentFileStorage
from backend.app.services.document_job_queue import RedisDocumentJobQueue
from backend.app.services.rag_query import RAGQueryService
from backend.app.vectorstores.factory import get_vector_store


logger = get_logger(__name__)

router = APIRouter(
    prefix=settings.api_prefix,
    tags=["RAG API"],
)


# =============================================================
# Endpoint-specific rate limits
# =============================================================

upload_rate_limit = require_user_rate_limit(
    scope="document-upload",
    limit=settings.rate_limit_upload_requests,
    window_seconds=settings.rate_limit_upload_window_seconds,
)

rag_query_rate_limit = require_user_rate_limit(
    scope="rag-query",
    limit=settings.rate_limit_rag_requests,
    window_seconds=settings.rate_limit_rag_window_seconds,
)


# =============================================================
# Access helpers
# =============================================================

async def get_accessible_department_ids(
    *,
    session: AsyncSession,
    access: OrganizationAccess,
) -> tuple[str, ...]:
    """
    Organization owners/admins receive all active department IDs.

    Other organization members receive only their own active
    department memberships.
    """

    repository = DepartmentRepository(session)

    if is_organization_admin(access):
        departments = await repository.list_departments(
            organization_id=access.organization_id,
        )

        return tuple(
            department.id
            for department in departments
            if department.is_active
        )

    department_ids = await repository.list_active_department_ids(
        organization_id=access.organization_id,
        user_id=access.user_id,
    )

    return tuple(department_ids)


async def require_department_admin(
    *,
    repository: DepartmentRepository,
    department_id: str,
    organization_id: str,
    user_id: str,
) -> None:
    membership = await repository.get_active_membership(
        department_id=department_id,
        organization_id=organization_id,
        user_id=user_id,
    )

    if (
        membership is None
        or membership.role != DepartmentRole.DEPARTMENT_ADMIN.value
    ):
        raise AuthorizationError(
            message=(
                "Only organization owners/admins or the "
                "selected department's Department Admin "
                "can manage documents in this department."
            )
        )


# =============================================================
# Health
# =============================================================

@router.get(
    "/health",
    response_model=HealthResponse,
)
async def health() -> HealthResponse:
    return HealthResponse(
        status="healthy",
        application=settings.app_name,
        environment=settings.app_environment,
    )


# =============================================================
# Document upload
# =============================================================

@router.post(
    "/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(upload_rate_limit),
    ],
)
async def upload_document(
    knowledge_base_id: str = Form(...),
    visibility: DocumentVisibility = Form(
        default=DocumentVisibility.ORGANIZATION,
    ),
    department_id: str | None = Form(default=None),
    replaces_document_id: str | None = Form(default=None),
    file: UploadFile = File(...),
    access: OrganizationAccess = Depends(
        get_organization_access
    ),
    session: AsyncSession = Depends(
        get_database_session_dependency
    ),
) -> DocumentUploadResponse:
    """
    Upload a PDF or DOCX document for asynchronous ingestion.

    Organization owners/admins:
        - Can upload organization-wide documents.
        - Can upload documents to any active department.

    Department Admins:
        - Can upload documents only to their own department.

    Employees and Viewers:
        - Cannot upload documents.
    """

    validate_document_upload(file)

    organization_admin = is_organization_admin(access)
    department_repository = DepartmentRepository(session)

    # ---------------------------------------------------------
    # Validate upload visibility and permissions
    # ---------------------------------------------------------

    if visibility == DocumentVisibility.DEPARTMENT:
        if not department_id:
            raise InvalidUploadError(
                message="A department document requires a department ID."
            )

        department = await department_repository.get_department(
            department_id=department_id,
            organization_id=access.organization_id,
            active_only=True,
        )

        if department is None:
            raise InvalidUploadError(
                message="The requested department was not found."
            )

        if not organization_admin:
            await require_department_admin(
                repository=department_repository,
                department_id=department_id,
                organization_id=access.organization_id,
                user_id=access.user_id,
            )

    elif visibility == DocumentVisibility.ORGANIZATION:
        if not organization_admin:
            raise AuthorizationError(
                message=(
                    "Only organization owners and admins can "
                    "upload organization-wide documents."
                )
            )

        department_id = None

    else:
        raise InvalidUploadError(
            message="The requested document visibility is invalid."
        )

    # ---------------------------------------------------------
    # Validate replacement permissions and scope
    # ---------------------------------------------------------

    repository = DocumentRepository(session)

    if replaces_document_id:
        accessible_department_ids = await get_accessible_department_ids(
            session=session,
            access=access,
        )

        replaced_document = await repository.get_by_id(
            document_id=replaces_document_id,
            organization_id=access.organization_id,
            user_id=access.user_id,
            department_ids=accessible_department_ids,
            include_all_departments=organization_admin,
        )

        if (
            replaced_document is None
            or not replaced_document.is_current
        ):
            raise InvalidUploadError(
                message=(
                    "The document to replace was not found, "
                    "is inaccessible, or is already archived."
                )
            )

        if (
            replaced_document.knowledge_base_id != knowledge_base_id
            or replaced_document.visibility != visibility.value
            or replaced_document.department_id != department_id
        ):
            raise InvalidUploadError(
                message=(
                    "A replacement must preserve its knowledge "
                    "base and visibility scope."
                )
            )

        if (
            replaced_document.visibility
            == DocumentVisibility.ORGANIZATION.value
            and not organization_admin
        ):
            raise AuthorizationError(
                message=(
                    "Only organization owners and admins can "
                    "replace organization-wide documents."
                )
            )

        if (
            replaced_document.visibility
            == DocumentVisibility.DEPARTMENT.value
            and not organization_admin
        ):
            await require_department_admin(
                repository=department_repository,
                department_id=replaced_document.department_id,
                organization_id=access.organization_id,
                user_id=access.user_id,
            )

    # ---------------------------------------------------------
    # Store file and create durable ingestion job
    # ---------------------------------------------------------

    jobs = DocumentJobRepository(session)

    if replaces_document_id:
        has_active_replacement = await jobs.has_active_replacement_job(
            organization_id=access.organization_id,
            replaces_document_id=replaces_document_id,
        )
        if has_active_replacement:
            raise ConflictError(
                message="A replacement upload for this document is already queued or processing.",
                details={"replaces_document_id": replaces_document_id},
            )

    safe_filename = Path(
        (file.filename or "document").replace("\\", "/")
    ).name

    stored_path = await DocumentFileStorage().save(
        file,
        safe_filename,
    )

    try:
        job = await jobs.create(
            organization_id=access.organization_id,
            user_id=access.user_id,
            knowledge_base_id=knowledge_base_id,
            visibility=visibility,
            department_id=department_id,
            replaces_document_id=replaces_document_id,
            filename=safe_filename,
            file_path=str(stored_path),
            max_attempts=settings.document_job_max_attempts,
        )

        await AuditRepository(session).record(
            organization_id=access.organization_id,
            actor_user_id=access.user_id,
            event_type="document_ingestion_queued",
            resource_type="document_ingestion_job",
            resource_id=job.id,
            details={
                "filename": safe_filename,
                "visibility": visibility.value,
                "department_id": department_id,
            },
        )

        await session.commit()

    except Exception:
        await session.rollback()
        stored_path.unlink(missing_ok=True)
        raise

    # ---------------------------------------------------------
    # Enqueue ingestion job
    # ---------------------------------------------------------

    try:
        await RedisDocumentJobQueue(
            get_redis_client()
        ).enqueue(job.id)
    except Exception as exc:
        logger.warning(
            "document_job_enqueue_deferred",
            job_id=job.id,
            error_type=type(exc).__name__,
        )

    return DocumentUploadResponse(
        job_id=job.id,
        status=job.status,
        filename=safe_filename,
    )


# =============================================================
# RAG query
# =============================================================

@router.post(
    "/rag/query",
    response_model=RAGQueryResponse,
    dependencies=[
        Depends(rag_query_rate_limit),
    ],
)
async def rag_query(
    request: RAGQueryRequest,
    access: OrganizationAccess = Depends(
        require_document_read_access
    ),
    session: AsyncSession = Depends(
        get_database_session_dependency
    ),
    embedding_provider=Depends(
        get_embedding_provider_dependency
    ),
    llm_provider=Depends(
        get_llm_provider_dependency
    ),
) -> RAGQueryResponse:
    """
    Execute a retrieval-augmented generation query.

    Organization owners/admins can retrieve from all active
    departments. Other users are restricted to their own
    active department memberships.
    """

    department_ids = await get_accessible_department_ids(
        session=session,
        access=access,
    )

    query_normalized = " ".join(request.query.lower().strip().split())
    query_hash = hashlib.sha256(query_normalized.encode("utf-8")).hexdigest()
    cache_key = (
        f"rag_cache:{access.organization_id}:{request.knowledge_base_id}:"
        f"{request.document_id or 'all'}:{query_hash}"
    )

    redis_client = get_redis_client()

    if not request.bypass_cache:
        try:
            cached_raw = await redis_client.get(cache_key)
            if cached_raw:
                cached_data = json.loads(cached_raw)
                cached_data["cached"] = True
                logger.info(
                    "rag_query_cache_hit",
                    organization_id=access.organization_id,
                    knowledge_base_id=request.knowledge_base_id,
                    document_id=request.document_id,
                )
                return RAGQueryResponse(**cached_data)
        except Exception as exc:
            logger.warning(
                "rag_query_cache_read_failed",
                error=str(exc),
            )

    vector_store = get_vector_store(
        embedding_provider=embedding_provider,
        organization_id=access.organization_id,
        user_id=access.user_id,
        knowledge_base_id=request.knowledge_base_id,
        department_ids=department_ids,
    )

    try:
        retrieval_service = RetrievalService(
            vector_store=vector_store,
            default_k=settings.retrieval_top_k,
            max_k=20,
        )

        generation_service = GroundedAnswerService(
            llm_provider=llm_provider,
            max_context_characters=24_000,
        )

        rag_service = RAGQueryService(
            retrieval_service=retrieval_service,
            generation_service=generation_service,
        )

        result = await rag_service.answer(
            query=request.query,
            k=request.k,
            document_id=request.document_id,
        )
    finally:
        close_method = getattr(vector_store, "close", None)
        if callable(close_method):
            await asyncio.to_thread(close_method)

    response = RAGQueryResponse(
        query=result.query,
        answer=result.answer,
        grounded=result.grounded,
        citations=list(result.citations),
        sources=[
            RAGSourceResponse(
                citation_id=source.citation_id,
                document_id=source.document_id,
                chunk_id=source.chunk_id,
                filename=source.filename,
                page_number=source.page_number,
            )
            for source in result.sources
        ],
        retrieved_chunk_count=result.retrieved_chunk_count,
        cached=False,
    )

    try:
        await redis_client.set(
            cache_key,
            response.model_dump_json(),
        )
        logger.info(
            "rag_query_cache_stored",
            cache_key=cache_key,
        )
    except Exception as exc:
        logger.warning(
            "rag_query_cache_write_failed",
            error=str(exc),
        )

    return response


# =============================================================
# Upload validation
# =============================================================

def validate_document_upload(
    file: UploadFile,
) -> None:
    filename = file.filename or ""

    if not filename.lower().endswith((".pdf", ".docx")):
        raise InvalidUploadError(
            message="Only PDF and DOCX documents are accepted.",
            details={
                "filename": filename,
                "content_type": file.content_type,
            },
        )

    allowed_content_types = {
        "application/pdf",
        "application/x-pdf",
        (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    }

    if file.content_type not in allowed_content_types:
        raise InvalidUploadError(
            message="Only PDF and DOCX content types are accepted.",
            details={
                "filename": filename,
                "content_type": file.content_type,
            },
        )


# =============================================================
# Save temporary upload
# =============================================================

async def save_upload_to_temp_file(
    *,
    file: UploadFile,
    destination: Path,
) -> None:
    """
    Save an uploaded file to a temporary destination while
    enforcing the configured maximum upload size.
    """

    total_bytes = 0

    with destination.open("wb") as temp_file:
        while True:
            chunk = await file.read(1024 * 1024)

            if not chunk:
                break

            total_bytes += len(chunk)

            if total_bytes > settings.upload_max_bytes:
                destination.unlink(missing_ok=True)

                raise InvalidUploadError(
                    message="The uploaded document is too large.",
                    details={
                        "maximum_bytes": settings.upload_max_bytes,
                        "received_bytes": total_bytes,
                    },
                )

            temp_file.write(chunk)

    if total_bytes == 0:
        destination.unlink(missing_ok=True)

        raise InvalidUploadError(
            message="The uploaded document is empty."
        )