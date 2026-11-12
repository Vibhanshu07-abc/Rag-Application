import os
import time
import tracemalloc
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError


class WorkerTelemetry:
    """Low-overhead worker heartbeat and process metrics using the existing Redis client."""

    key_prefix = "rag:document-worker:heartbeat:"

    def __init__(self, client: Redis, worker_id: str | None = None) -> None:
        self.client = client
        self.worker_id = worker_id or f"{os.getpid()}-{uuid4().hex[:8]}"
        self.started_at = time.monotonic()
        if not tracemalloc.is_tracing():
            tracemalloc.start()

    async def heartbeat(self, active_jobs: int) -> None:
        current, peak = tracemalloc.get_traced_memory()
        try:
            await self.client.hset(
                f"{self.key_prefix}{self.worker_id}",
                mapping={
                    "worker_id": self.worker_id,
                    "pid": str(os.getpid()),
                    "active_jobs": str(active_jobs),
                    "cpu_seconds": f"{time.process_time():.3f}",
                    "uptime_seconds": f"{time.monotonic() - self.started_at:.3f}",
                    "python_memory_bytes": str(current),
                    "python_memory_peak_bytes": str(peak),
                },
            )
            await self.client.expire(f"{self.key_prefix}{self.worker_id}", 30)
        except RedisError:
            pass

    async def snapshots(self) -> list[dict[str, str]]:
        snapshots: list[dict[str, str]] = []
        try:
            async for key in self.client.scan_iter(match=f"{self.key_prefix}*"):
                values = await self.client.hgetall(key)
                if values:
                    snapshots.append({str(name): str(value) for name, value in values.items()})
        except RedisError:
            return []
        return snapshots
