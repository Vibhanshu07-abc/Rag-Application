from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database.enums import DocumentJobStatus, DocumentVisibility
from backend.app.database.models import DocumentIngestionJobRecord


class DocumentJobRepository:
    """MySQL persistence for asynchronously processed document uploads."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, *, organization_id: str, user_id: str, knowledge_base_id: str,
        visibility: DocumentVisibility, department_id: str | None,
        replaces_document_id: str | None, filename: str, file_path: str,
        max_attempts: int = 3,
    ) -> DocumentIngestionJobRecord:
        job = DocumentIngestionJobRecord(
            organization_id=organization_id, user_id=user_id,
            knowledge_base_id=knowledge_base_id, visibility=visibility.value,
            department_id=department_id, replaces_document_id=replaces_document_id,
            filename=filename, file_path=file_path,
            status=DocumentJobStatus.QUEUED.value,
            max_attempts=max_attempts,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def get_for_update(self, job_id: str) -> DocumentIngestionJobRecord | None:
        result = await self.session.execute(
            select(DocumentIngestionJobRecord)
            .where(DocumentIngestionJobRecord.id == job_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def claim_queued_job(self, job_id: str) -> DocumentIngestionJobRecord | None:
        """Atomically claim a queued job; competing workers receive None."""
        job = await self.get_for_update(job_id)
        if job is None or job.status != DocumentJobStatus.QUEUED.value:
            return None
        await self.mark_processing(job)
        return job

    async def get_for_user(
        self, *, job_id: str, organization_id: str, user_id: str,
    ) -> DocumentIngestionJobRecord | None:
        result = await self.session.execute(
            select(DocumentIngestionJobRecord).where(
                DocumentIngestionJobRecord.id == job_id,
                DocumentIngestionJobRecord.organization_id == organization_id,
                DocumentIngestionJobRecord.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def mark_processing(self, job: DocumentIngestionJobRecord) -> None:
        job.status = DocumentJobStatus.PROCESSING.value
        job.started_at = datetime.utcnow()
        job.error_message = None
        job.next_attempt_at = None
        job.attempt_count += 1
        await self.session.flush()

    async def mark_completed(self, job: DocumentIngestionJobRecord, document_id: str) -> None:
        job.status = DocumentJobStatus.COMPLETED.value
        job.document_id = document_id
        job.completed_at = datetime.utcnow()
        await self.session.flush()

    async def mark_failed(self, job: DocumentIngestionJobRecord, error_message: str) -> None:
        job.status = DocumentJobStatus.FAILED.value
        job.error_message = error_message[:2000]
        job.completed_at = datetime.utcnow()
        await self.session.flush()

    async def schedule_failure(
        self, job: DocumentIngestionJobRecord, error_message: str, retry_delay_seconds: int,
    ) -> bool:
        """Persist exponential retry intent; returns True when another try is allowed."""
        job.error_message = error_message[:2000]
        if job.attempt_count < job.max_attempts:
            job.status = DocumentJobStatus.QUEUED.value
            job.next_attempt_at = datetime.utcnow() + timedelta(seconds=retry_delay_seconds)
            await self.session.flush()
            return True
        await self.mark_failed(job, error_message)
        return False

    async def reset_failed_for_retry(
        self, *, job_id: str, organization_id: str, user_id: str,
    ) -> DocumentIngestionJobRecord | None:
        job = await self.get_for_update(job_id)
        if job is None or job.organization_id != organization_id or job.user_id != user_id:
            return None
        if job.status != DocumentJobStatus.FAILED.value:
            return None
        job.status = DocumentJobStatus.QUEUED.value
        job.attempt_count = 0
        job.next_attempt_at = None
        job.error_message = None
        await self.session.flush()
        return job

    async def recover_interrupted(self, timeout_seconds: int) -> list[str]:
        """Return stale processing jobs to queued state when a worker died."""
        cutoff = datetime.utcnow() - timedelta(seconds=timeout_seconds)
        result = await self.session.execute(
            select(DocumentIngestionJobRecord)
            .where(
                DocumentIngestionJobRecord.status == DocumentJobStatus.PROCESSING.value,
                DocumentIngestionJobRecord.started_at < cutoff,
            )
            .with_for_update()
        )
        jobs = list(result.scalars())
        for job in jobs:
            job.status = DocumentJobStatus.QUEUED.value
            job.next_attempt_at = None
            job.error_message = "Recovered after worker interruption."
        await self.session.flush()
        return [job.id for job in jobs]

    async def status_counts(self, organization_id: str) -> dict[str, int]:
        result = await self.session.execute(
            select(DocumentIngestionJobRecord.status, func.count(DocumentIngestionJobRecord.id))
            .where(DocumentIngestionJobRecord.organization_id == organization_id)
            .group_by(DocumentIngestionJobRecord.status)
        )
        return {str(status): int(count) for status, count in result.all()}
