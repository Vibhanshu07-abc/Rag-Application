import asyncio
import signal
from contextlib import suppress
from pathlib import Path

from backend.app.core.config import settings
from backend.app.core.exceptions import DuplicateDocumentError, InvalidUploadError
from backend.app.core.logging import get_logger
from backend.app.database.enums import DocumentStatus, DocumentVisibility
from backend.app.database.repositories import DocumentJobRepository, DocumentRepository
from backend.app.database.repositories.audit_repository import AuditRepository
from backend.app.database.session import close_database_connection, get_session_factory
from backend.app.embeddings.base import BaseEmbeddingProvider
from backend.app.embeddings.factory import get_embedding_provider
from backend.app.ingestion.loaders.docxloader import DOCXDocumentLoader
from backend.app.ingestion.loaders.pdf_loader import PDFDocumentLoader
from backend.app.ingestion.parsers.docx_parser import DOCXDocumentParser
from backend.app.ingestion.parsers.pdf_parser import PDFDocumentParser
from backend.app.ingestion.processors.chunker import DocumentChunker
from backend.app.ingestion.processors.text_cleaner import TextCleaner
from backend.app.rate_limiting.service import close_rate_limiter, get_redis_client
from backend.app.services.document_ingestion import DocumentIngestionService
from backend.app.services.document_job_queue import RedisDocumentJobQueue
from backend.app.services.worker_telemetry import WorkerTelemetry
from backend.app.vectorstores.factory import get_vector_store


logger = get_logger(__name__)


def _resolve_embedding_dimension(embedding_provider: BaseEmbeddingProvider) -> int:
    dimension = getattr(embedding_provider, "dimension", None)
    if isinstance(dimension, int) and dimension > 0:
        return dimension
    return len(embedding_provider.embed_query("dimension probe"))


async def process_one(
    job_id: str,
    queue: RedisDocumentJobQueue,
    *,
    embedding_provider: BaseEmbeddingProvider | None = None,
    embedding_dimension: int | None = None,
) -> None:
    """Claim and process once; the row lock prevents duplicate consumers."""
    async with get_session_factory()() as session:
        jobs = DocumentJobRepository(session)
        job = await jobs.claim_queued_job(job_id)
        if job is None:
            return
        await session.commit()
        vector_store = None
        replacement_lock_key = None
        previous_vector_ids: list[str] = []
        previous_state: tuple[bool, bool, str] | None = None
        chroma_previous_updated = False
        try:
            provider = embedding_provider or get_embedding_provider()
            dimension = embedding_dimension or _resolve_embedding_dimension(provider)
            path = Path(job.file_path)
            is_docx = path.suffix.lower() == ".docx"
            vector_store = get_vector_store(
                embedding_provider=provider,
                organization_id=job.organization_id,
                user_id=job.user_id,
                knowledge_base_id=job.knowledge_base_id,
            )
            documents = DocumentRepository(session)
            replaced_document = None
            next_version = 1
            if job.replaces_document_id:
                replacement_lock_key = await queue.acquire_document_lock(
                    job.replaces_document_id,
                    job.id,
                )
                replaced_document = await documents.get_by_id_for_update(
                    document_id=job.replaces_document_id,
                    organization_id=job.organization_id,
                    user_id=job.user_id,
                    include_all_departments=True,
                )
                if replaced_document is None or not replaced_document.is_current:
                    raise InvalidUploadError(
                        message="The replacement source document is no longer current."
                    )
                next_version = await documents.get_next_version_number(
                    organization_id=job.organization_id,
                    knowledge_base_id=job.knowledge_base_id,
                    logical_document_id=replaced_document.logical_document_id,
                )
                previous_state = (
                    replaced_document.is_current,
                    replaced_document.is_published,
                    replaced_document.status,
                )

            service = DocumentIngestionService(
                session=session,
                repository=documents,
                loader=DOCXDocumentLoader() if is_docx else PDFDocumentLoader(),
                parser=DOCXDocumentParser() if is_docx else PDFDocumentParser(),
                cleaner=TextCleaner(),
                chunker=DocumentChunker(),
                vector_store=vector_store,
                embedding_provider_name=type(provider).__name__,
                embedding_model_name=provider.model_name,
                embedding_dimension=dimension,
                vector_store_provider_name=type(vector_store).__name__,
            )
            result = await service.ingest_document(
                file_path=path,
                organization_id=job.organization_id,
                user_id=job.user_id,
                knowledge_base_id=job.knowledge_base_id,
                visibility=DocumentVisibility(job.visibility),
                department_id=job.department_id,
                allowed_extensions=(".docx",) if is_docx else (".pdf",),
                logical_document_id=(
                    replaced_document.logical_document_id if replaced_document else None
                ),
                version_number=next_version,
                is_current_initial=(replaced_document is None),
                original_filename=job.filename,
            )
            if replaced_document is not None:
                previous_vector_ids = await documents.list_vector_ids(
                    document_id=replaced_document.id,
                    organization_id=job.organization_id,
                    user_id=job.user_id,
                    include_all_departments=True,
                )
                if previous_vector_ids:
                    await asyncio.to_thread(
                        vector_store.set_version_state,
                        previous_vector_ids,
                        is_current=False,
                        is_published=False,
                        status=DocumentStatus.ARCHIVED.value,
                    )
                    chroma_previous_updated = True
                if result.vector_ids:
                    await asyncio.to_thread(
                        vector_store.set_version_state,
                        list(result.vector_ids),
                        is_current=True,
                        is_published=False,
                        status=DocumentStatus.COMPLETED.value,
                    )
                await documents.archive_current_version(
                    document_id=replaced_document.id,
                    organization_id=job.organization_id,
                    user_id=job.user_id,
                    include_all_departments=True,
                )
                await documents.activate_version(
                    document_id=result.document_id,
                    organization_id=job.organization_id,
                    user_id=job.user_id,
                    include_all_departments=True,
                )
            await jobs.mark_completed(job, result.document_id)
            await AuditRepository(session).record(
                organization_id=job.organization_id,
                actor_user_id=job.user_id,
                event_type="document_ingestion_completed",
                resource_type="document_ingestion_job",
                resource_id=job.id,
                details={"document_id": result.document_id},
            )
            await session.commit()
            Path(job.file_path).unlink(missing_ok=True)
        except Exception as exc:
            if (
                chroma_previous_updated
                and vector_store is not None
                and previous_vector_ids
                and previous_state is not None
            ):
                prev_current, prev_published, prev_status = previous_state
                with suppress(Exception):
                    await asyncio.to_thread(
                        vector_store.set_version_state,
                        previous_vector_ids,
                        is_current=prev_current,
                        is_published=prev_published,
                        status=prev_status,
                    )

            await session.rollback()
            locked_job = await jobs.get_for_update(job_id)
            if locked_job is not None:
                non_retryable = isinstance(
                    exc, (DuplicateDocumentError, InvalidUploadError)
                )
                if non_retryable:
                    await jobs.mark_failed(locked_job, str(exc))
                    retry_scheduled = False
                else:
                    retry_delay = settings.document_job_retry_base_seconds * (
                        2 ** max(locked_job.attempt_count - 1, 0)
                    )
                    retry_scheduled = await jobs.schedule_failure(
                        locked_job, str(exc), retry_delay,
                    )
                await AuditRepository(session).record(
                    organization_id=locked_job.organization_id,
                    actor_user_id=locked_job.user_id,
                    event_type=(
                        "document_ingestion_retry_scheduled"
                        if retry_scheduled
                        else "document_ingestion_failed"
                    ),
                    resource_type="document_ingestion_job",
                    resource_id=locked_job.id,
                    details={"attempt_count": locked_job.attempt_count},
                )
                await session.commit()
                if retry_scheduled:
                    with suppress(Exception):
                        await queue.enqueue_after(job_id, retry_delay)
                else:
                    Path(job.file_path).unlink(missing_ok=True)
        finally:
            if replacement_lock_key is not None:
                await queue.release_document_lock(replacement_lock_key, job.id)
            close_method = getattr(vector_store, "close", None)
            if callable(close_method):
                await asyncio.to_thread(close_method)


async def reconcile_pending_jobs(queue: RedisDocumentJobQueue) -> int:
    """Recover stale PROCESSING jobs and re-enqueue due QUEUED MySQL jobs."""
    async with get_session_factory()() as session:
        repository = DocumentJobRepository(session)
        recovered_ids = await repository.recover_interrupted(
            settings.document_job_processing_timeout_seconds,
        )
        due_queued_ids = await repository.list_due_queued_job_ids(grace_seconds=5)
        await session.commit()

    requeued = 0
    seen: set[str] = set()
    for job_id in [*recovered_ids, *due_queued_ids]:
        if job_id in seen:
            continue
        seen.add(job_id)
        try:
            await queue.enqueue(job_id)
            requeued += 1
        except Exception as exc:
            logger.warning(
                "document_job_reconcile_enqueue_failed",
                job_id=job_id,
                error_type=type(exc).__name__,
            )
    return requeued


async def _reconcile_loop(
    queue: RedisDocumentJobQueue,
    stop_event: asyncio.Event,
    interval_seconds: float,
) -> None:
    while not stop_event.is_set():
        try:
            await reconcile_pending_jobs(queue)
        except Exception as exc:
            logger.warning(
                "document_worker_reconcile_failed",
                error_type=type(exc).__name__,
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


async def _consume(
    queue: RedisDocumentJobQueue,
    stop_event: asyncio.Event,
    telemetry: WorkerTelemetry,
    embedding_provider: BaseEmbeddingProvider,
    embedding_dimension: int,
) -> None:
    """One consumer in the worker's bounded producer-consumer pool."""
    while not stop_event.is_set():
        try:
            await telemetry.heartbeat(active_jobs=0)
            await queue.promote_due()
            job_id = await queue.dequeue(settings.document_worker_poll_seconds)
            if job_id is not None:
                await telemetry.heartbeat(active_jobs=1)
                await process_one(
                    job_id,
                    queue,
                    embedding_provider=embedding_provider,
                    embedding_dimension=embedding_dimension,
                )
        except Exception as exc:
            logger.warning(
                "document_worker_consumer_iteration_failed",
                worker_id=telemetry.worker_id,
                error_type=type(exc).__name__,
            )
            await asyncio.sleep(1.0)


def _install_shutdown_handlers(stop_event: asyncio.Event) -> None:
    """Stop dequeuing on SIGINT/SIGTERM and let active jobs finish safely."""
    loop = asyncio.get_running_loop()
    for signal_value in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signal_value, stop_event.set)


async def run() -> None:
    queue = RedisDocumentJobQueue(get_redis_client())
    embedding_provider = get_embedding_provider()
    embedding_dimension = _resolve_embedding_dimension(embedding_provider)

    await reconcile_pending_jobs(queue)

    stop_event = asyncio.Event()
    _install_shutdown_handlers(stop_event)

    reconcile_interval = max(5.0, float(settings.document_worker_poll_seconds) * 3.0)
    tasks = [
        asyncio.create_task(_reconcile_loop(queue, stop_event, reconcile_interval)),
        *[
            asyncio.create_task(
                _consume(
                    queue,
                    stop_event,
                    WorkerTelemetry(get_redis_client()),
                    embedding_provider,
                    embedding_dimension,
                )
            )
            for _ in range(settings.document_worker_concurrency)
        ],
    ]
    try:
        await stop_event.wait()
    finally:
        # No cancellation: finish jobs already claimed before closing Redis/MySQL.
        await asyncio.gather(*tasks, return_exceptions=True)
        await close_rate_limiter()
        await close_database_connection()


if __name__ == "__main__":
    asyncio.run(run())
