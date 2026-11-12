import json
from asyncio import sleep
from time import monotonic, time

from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.app.core.exceptions import AppError
from backend.app.core.logging import get_logger


logger = get_logger(__name__)


class DocumentQueueError(AppError):
    code = "DOCUMENT_QUEUE_UNAVAILABLE"
    default_message = "The document processing queue is unavailable."
    status_code = 503


class RedisDocumentJobQueue:
    """Small FIFO adapter. Durable job data remains in MySQL."""

    queue_key = "rag:document-ingestion:queue"
    delayed_queue_key = "rag:document-ingestion:delayed"

    def __init__(self, client: Redis) -> None:
        self.client = client

    async def enqueue(self, job_id: str) -> None:
        try:
            await self.client.rpush(self.queue_key, json.dumps({"job_id": job_id}))
        except RedisError as exc:
            raise DocumentQueueError(details={"error_type": type(exc).__name__}) from exc

    async def dequeue(self, timeout_seconds: int = 5) -> str | None:
        try:
            item = await self.client.blpop(self.queue_key, timeout=timeout_seconds)
        except RedisError as exc:
            raise DocumentQueueError(details={"error_type": type(exc).__name__}) from exc
        if item is None:
            return None
        _, payload = item
        return str(json.loads(payload)["job_id"])

    async def enqueue_after(self, job_id: str, delay_seconds: int) -> None:
        try:
            await self.client.zadd(self.delayed_queue_key, {job_id: time() + delay_seconds})
        except RedisError as exc:
            raise DocumentQueueError(details={"error_type": type(exc).__name__}) from exc

    async def promote_due(self) -> int:
        """Move due retry IDs back to FIFO; persistence remains the source of truth."""
        try:
            job_ids = await self.client.zrangebyscore(self.delayed_queue_key, "-inf", time())
            for job_id in job_ids:
                if await self.client.zrem(self.delayed_queue_key, job_id):
                    await self.enqueue(str(job_id))
            return len(job_ids)
        except RedisError as exc:
            raise DocumentQueueError(details={"error_type": type(exc).__name__}) from exc

    async def acquire_document_lock(
        self,
        document_id: str,
        owner_id: str,
        *,
        timeout_seconds: float = 30.0,
        ttl_seconds: int = 3600,
    ) -> str:
        """Bounded mutual exclusion for replacement versions across worker processes."""
        key = f"rag:document-ingestion:replacement-lock:{document_id}"
        deadline = monotonic() + max(timeout_seconds, 0.1)
        while monotonic() < deadline:
            try:
                acquired = await self.client.set(key, owner_id, nx=True, ex=ttl_seconds)
            except RedisError as exc:
                raise DocumentQueueError(details={"error_type": type(exc).__name__}) from exc
            if acquired:
                return key
            await sleep(0.25)
        raise DocumentQueueError(
            message="Timed out waiting for document replacement lock.",
            details={"document_id": document_id},
        )

    async def release_document_lock(self, key: str, owner_id: str) -> None:
        # Compare-and-delete prevents one worker releasing another's lease.
        try:
            await self.client.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0",
                1,
                key,
                owner_id,
            )
        except RedisError as exc:
            logger.warning(
                "document_lock_release_failed",
                key=key,
                error_type=type(exc).__name__,
            )
