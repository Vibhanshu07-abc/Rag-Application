import asyncio

from backend.app.services.document_job_queue import RedisDocumentJobQueue


class FakeRedis:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    async def rpush(self, key: str, value: str) -> int:
        self.items.append((key, value))
        return len(self.items)

    async def blpop(self, key: str, timeout: int):
        for index, (stored_key, value) in enumerate(self.items):
            if stored_key == key:
                self.items.pop(index)
                return stored_key, value
        return None


async def main() -> None:
    queue = RedisDocumentJobQueue(FakeRedis())
    await queue.enqueue("job-1")
    await queue.enqueue("job-2")
    assert await queue.dequeue() == "job-1"
    assert await queue.dequeue() == "job-2"
    assert await queue.dequeue() is None
    print("Redis document queue FIFO contract passed.")


if __name__ == "__main__":
    asyncio.run(main())
