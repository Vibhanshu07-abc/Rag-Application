from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database.enums import DocumentJobStatus, DocumentVisibility
from backend.app.database.models import DocumentIngestionJobRecord


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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

    async def has_active_replacement_job(
        self,
        *,
        organization_id: str,
        replaces_document_id: str,
    ) -> bool:
        statement = (
            select(DocumentIngestionJobRecord.id)
            .where(
                DocumentIngestionJobRecord.organization_id == organization_id,
                DocumentIngestionJobRecord.replaces_document_id == replaces_document_id,
                DocumentIngestionJobRecord.status.in_(
                    (
                        DocumentJobStatus.QUEUED.value,
                        DocumentJobStatus.PROCESSING.value,
                    )
                ),
            )
            .limit(1)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

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
        if job.next_attempt_at is not None and job.next_attempt_at > _utc_now():
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

    async def get_for_actor(
        self,
        *,
        job_id: str,
        organization_id: str,
        include_all_departments: bool = False,
        managed_department_ids: Sequence[str] = (),
    ) -> DocumentIngestionJobRecord | None:
        statement = select(DocumentIngestionJobRecord).where(
            DocumentIngestionJobRecord.id == job_id,
            DocumentIngestionJobRecord.organization_id == organization_id,
        )
        if not include_all_departments:
            if not managed_department_ids:
                return None
            statement = statement.where(
                DocumentIngestionJobRecord.visibility == DocumentVisibility.DEPARTMENT.value,
                DocumentIngestionJobRecord.department_id.in_(tuple(managed_department_ids)),
            )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def mark_processing(self, job: DocumentIngestionJobRecord) -> None:
        job.status = DocumentJobStatus.PROCESSING.value
        job.started_at = _utc_now()
        job.error_message = None
        job.next_attempt_at = None
        job.attempt_count += 1
        await self.session.flush()

    async def mark_completed(self, job: DocumentIngestionJobRecord, document_id: str) -> None:
        job.status = DocumentJobStatus.COMPLETED.value
        job.document_id = document_id
        job.completed_at = _utc_now()
        await self.session.flush()

    async def mark_failed(self, job: DocumentIngestionJobRecord, error_message: str) -> None:
        job.status = DocumentJobStatus.FAILED.value
        job.error_message = error_message[:2000]
        job.completed_at = _utc_now()
        await self.session.flush()

    async def schedule_failure(
        self, job: DocumentIngestionJobRecord, error_message: str, retry_delay_seconds: int,
    ) -> bool:
        """Persist exponential retry intent; returns True when another try is allowed."""
        job.error_message = error_message[:2000]
        if job.attempt_count < job.max_attempts:
            job.status = DocumentJobStatus.QUEUED.value
            job.next_attempt_at = _utc_now() + timedelta(seconds=retry_delay_seconds)
            await self.session.flush()
            return True
        await self.mark_failed(job, error_message)
        return False

    async def reset_failed_for_retry(
        self,
        *,
        job_id: str,
        organization_id: str,
        user_id: str | None = None,
        include_all_departments: bool = False,
        managed_department_ids: Sequence[str] = (),
    ) -> DocumentIngestionJobRecord | None:
        job = await self.get_for_update(job_id)
        if job is None or job.organization_id != organization_id:
            return None
        if not include_all_departments:
            if managed_department_ids:
                if (
                    job.visibility != DocumentVisibility.DEPARTMENT.value
                    or job.department_id not in set(managed_department_ids)
                ):
                    return None
            elif user_id is None or job.user_id != user_id:
                return None
        if job.status != DocumentJobStatus.FAILED.value:
            return None
        job.status = DocumentJobStatus.QUEUED.value
        job.attempt_count = 0
        job.next_attempt_at = None
        job.error_message = None
        job.started_at = None
        job.completed_at = None
        await self.session.flush()
        return job

    async def recover_interrupted(self, timeout_seconds: int) -> list[str]:
        """Return stale processing jobs to queued state when a worker died."""
        cutoff = _utc_now() - timedelta(seconds=timeout_seconds)
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

    async def list_due_queued_job_ids(self, *, grace_seconds: int = 10, limit: int = 50) -> list[str]:
        """Find due QUEUED jobs in MySQL to reconcile any missed Redis deliveries."""
        now = _utc_now()
        created_cutoff = now - timedelta(seconds=grace_seconds)
        result = await self.session.execute(
            select(DocumentIngestionJobRecord.id)
            .where(
                DocumentIngestionJobRecord.status == DocumentJobStatus.QUEUED.value,
                DocumentIngestionJobRecord.created_at <= created_cutoff,
                or_(
                    DocumentIngestionJobRecord.next_attempt_at.is_(None),
                    DocumentIngestionJobRecord.next_attempt_at <= now,
                ),
            )
            .order_by(DocumentIngestionJobRecord.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def status_counts(self, organization_id: str) -> dict[str, int]:
        result = await self.session.execute(
            select(DocumentIngestionJobRecord.status, func.count(DocumentIngestionJobRecord.id))
            .where(DocumentIngestionJobRecord.organization_id == organization_id)
            .group_by(DocumentIngestionJobRecord.status)
        )
        return {str(status): int(count) for status, count in result.all()}
