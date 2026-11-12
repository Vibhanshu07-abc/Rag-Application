import asyncio
import signal
from contextlib import suppress
from pathlib import Path

from backend.app.database.enums import DocumentVisibility
from backend.app.database.repositories import DocumentJobRepository, DocumentRepository
from backend.app.database.repositories.audit_repository import AuditRepository
from backend.app.database.session import close_database_connection, get_session_factory
from backend.app.embeddings.factory import get_embedding_provider
from backend.app.ingestion.loaders.docxloader import DOCXDocumentLoader
from backend.app.ingestion.loaders.pdf_loader import PDFDocumentLoader
from backend.app.ingestion.parsers.docx_parser import DOCXDocumentParser
from backend.app.ingestion.parsers.pdf_parser import PDFDocumentParser
from backend.app.ingestion.processors.chunker import DocumentChunker
from backend.app.ingestion.processors.text_cleaner import TextCleaner
from backend.app.rate_limiting.service import close_rate_limiter, get_redis_client
from backend.app.core.config import settings
from backend.app.services.document_ingestion import DocumentIngestionService
from backend.app.services.document_job_queue import RedisDocumentJobQueue
from backend.app.services.worker_telemetry import WorkerTelemetry
from backend.app.vectorstores.factory import get_vector_store


async def process_one(job_id: str, queue: RedisDocumentJobQueue) -> None:
    """Claim and process once; the row lock prevents duplicate consumers."""
    async with get_session_factory()() as session:
        jobs = DocumentJobRepository(session)
        job = await jobs.claim_queued_job(job_id)
        if job is None:
            return
        await session.commit()
        vector_store = None
        replacement_lock_key = None
        try:
            embedding_provider = get_embedding_provider()
            path = Path(job.file_path)
            is_docx = path.suffix.lower() == ".docx"
            vector_store = get_vector_store(
                embedding_provider=embedding_provider, organization_id=job.organization_id,
                user_id=job.user_id, knowledge_base_id=job.knowledge_base_id,
            )
            documents = DocumentRepository(session)
            replaced_document = None
            if job.replaces_document_id:
                replacement_lock_key = await queue.acquire_document_lock(
                    job.replaces_document_id, job.id,
                )
                replaced_document = await documents.get_by_id_for_update(
                    document_id=job.replaces_document_id,
                    organization_id=job.organization_id,
                    user_id=job.user_id,
                )
                if replaced_document is None or not replaced_document.is_current:
                    raise ValueError("The replacement source is no longer current.")
            service = DocumentIngestionService(
                session=session, repository=documents,
                loader=DOCXDocumentLoader() if is_docx else PDFDocumentLoader(),
                parser=DOCXDocumentParser() if is_docx else PDFDocumentParser(),
                cleaner=TextCleaner(), chunker=DocumentChunker(), vector_store=vector_store,
                embedding_provider_name=type(embedding_provider).__name__,
                embedding_model_name=embedding_provider.model_name, embedding_dimension=384,
                vector_store_provider_name=type(vector_store).__name__,
            )
            result = await service.ingest_document(
                file_path=path, organization_id=job.organization_id, user_id=job.user_id,
                knowledge_base_id=job.knowledge_base_id,
                visibility=DocumentVisibility(job.visibility), department_id=job.department_id,
                allowed_extensions=(".docx",) if is_docx else (".pdf",),
                logical_document_id=(replaced_document.logical_document_id if replaced_document else None),
                version_number=(replaced_document.version_number + 1 if replaced_document else 1),
            )
            if replaced_document is not None:
                previous_vector_ids = await documents.list_vector_ids(
                    document_id=replaced_document.id, organization_id=job.organization_id,
                    user_id=job.user_id,
                )
                await asyncio.to_thread(
                    vector_store.set_current_version, previous_vector_ids, is_current=False,
                )
                await documents.archive_current_version(
                    document_id=replaced_document.id, organization_id=job.organization_id,
                    user_id=job.user_id,
                )
            await jobs.mark_completed(job, result.document_id)
            await AuditRepository(session).record(
                organization_id=job.organization_id, actor_user_id=job.user_id,
                event_type="document_ingestion_completed", resource_type="document_ingestion_job",
                resource_id=job.id, details={"document_id": result.document_id},
            )
            await session.commit()
            Path(job.file_path).unlink(missing_ok=True)
        except Exception as exc:
            await session.rollback()
            locked_job = await jobs.get_for_update(job_id)
            if locked_job is not None:
                retry_delay = settings.document_job_retry_base_seconds * (
                    2 ** max(locked_job.attempt_count - 1, 0)
                )
                retry_scheduled = await jobs.schedule_failure(
                    locked_job, str(exc), retry_delay,
                )
                await AuditRepository(session).record(
                    organization_id=locked_job.organization_id, actor_user_id=locked_job.user_id,
                    event_type=("document_ingestion_retry_scheduled" if retry_scheduled else "document_ingestion_failed"),
                    resource_type="document_ingestion_job", resource_id=locked_job.id,
                    details={"attempt_count": locked_job.attempt_count},
                )
                await session.commit()
                if retry_scheduled:
                    await queue.enqueue_after(job_id, retry_delay)
        finally:
            if replacement_lock_key is not None:
                await queue.release_document_lock(replacement_lock_key, job.id)
            close_method = getattr(vector_store, "close", None)
            if callable(close_method):
                await asyncio.to_thread(close_method)


async def _consume(
    queue: RedisDocumentJobQueue,
    stop_event: asyncio.Event,
    telemetry: WorkerTelemetry,
) -> None:
    """One consumer in the worker's bounded producer-consumer pool."""
    while not stop_event.is_set():
        await telemetry.heartbeat(active_jobs=0)
        await queue.promote_due()
        job_id = await queue.dequeue(settings.document_worker_poll_seconds)
        if job_id is not None:
            await telemetry.heartbeat(active_jobs=1)
            await process_one(job_id, queue)


def _install_shutdown_handlers(stop_event: asyncio.Event) -> None:
    """Stop dequeuing on SIGINT/SIGTERM and let active jobs finish safely."""
    loop = asyncio.get_running_loop()
    for signal_value in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signal_value, stop_event.set)


async def run() -> None:
    queue = RedisDocumentJobQueue(get_redis_client())
    async with get_session_factory()() as session:
        recovered_ids = await DocumentJobRepository(session).recover_interrupted(
            settings.document_job_processing_timeout_seconds,
        )
        await session.commit()
    for job_id in recovered_ids:
        await queue.enqueue(job_id)
    stop_event = asyncio.Event()
    _install_shutdown_handlers(stop_event)
    consumers = [
        asyncio.create_task(_consume(queue, stop_event, WorkerTelemetry(get_redis_client())))
        for _ in range(settings.document_worker_concurrency)
    ]
    try:
        await stop_event.wait()
    finally:
        # No cancellation: finish jobs already claimed before closing Redis/MySQL.
        await asyncio.gather(*consumers, return_exceptions=True)
        await close_rate_limiter()
        await close_database_connection()


if __name__ == "__main__":
    asyncio.run(run())
