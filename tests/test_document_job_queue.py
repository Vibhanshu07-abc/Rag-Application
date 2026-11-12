import asyncio
import pytest

from backend.app.services.document_job_queue import RedisDocumentJobQueue
from tests.conftest import FakeRedis


@pytest.mark.asyncio
async def test_redis_document_job_queue_fifo() -> None:
    queue = RedisDocumentJobQueue(FakeRedis())
    await queue.enqueue("job-1")
    await queue.enqueue("job-2")
    assert await queue.dequeue() == "job-1"
    assert await queue.dequeue() == "job-2"
    assert await queue.dequeue() is None


async def main() -> None:
    await test_redis_document_job_queue_fifo()
    print("Redis document queue FIFO contract passed.")


if __name__ == "__main__":
    asyncio.run(main())
