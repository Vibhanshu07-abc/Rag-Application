from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import io
import zipfile
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from pypdf import PdfWriter
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.app.api.app import create_app
from backend.app.api.dependencies import (
    get_database_session_dependency,
    get_embedding_provider_dependency,
    get_llm_provider_dependency,
)
from backend.app.core.config import settings
from backend.app.database import session as db_session
from backend.app.database.base import Base
import backend.app.database.models  # noqa: F401
from backend.app.embeddings.base import BaseEmbeddingProvider
from backend.app.llms.base import BaseLLMProvider
from backend.app.rate_limiting import service as rate_limit_service


class FakeRedis:
    """In-memory async Redis stand-in supporting queue, locks, hashes, and rate limiting."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.zsets: dict[str, dict[str, float]] = defaultdict(dict)
        self.kv: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = defaultdict(dict)
        self.counters: dict[str, int] = defaultdict(int)

    async def rpush(self, key: str, value: str) -> int:
        self.lists[key].append(value)
        return len(self.lists[key])

    async def blpop(self, key: str, timeout: int = 0) -> tuple[str, str] | None:
        items = self.lists.get(key)
        if items:
            return (key, items.pop(0))
        return None

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        added = 0
        for member, score in mapping.items():
            if member not in self.zsets[key]:
                added += 1
            self.zsets[key][member] = float(score)
        return added

    async def zrangebyscore(self, key: str, min_score: float | str, max_score: float | str) -> list[str]:
        low = float("-inf") if str(min_score) == "-inf" else float(min_score)
        high = float("inf") if str(max_score) == "+inf" else float(max_score)
        matched = [(member, score) for member, score in self.zsets.get(key, {}).items() if low <= score <= high]
        matched.sort(key=lambda pair: pair[1])
        return [member for member, _ in matched]

    async def zrem(self, key: str, member: str) -> int:
        if member in self.zsets.get(key, {}):
            del self.zsets[key][member]
            return 1
        return 0

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.kv:
                del self.kv[key]
                deleted += 1
            if key in self.counters:
                del self.counters[key]
                deleted += 1
        return deleted

    async def hset(self, key: str, mapping: dict[str, str]) -> int:
        self.hashes[key].update({str(k): str(v) for k, v in mapping.items()})
        return len(mapping)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def scan_iter(self, match: str = "*") -> AsyncIterator[str]:
        for key in list(self.hashes.keys()):
            if fnmatch.fnmatch(key, match):
                yield key

    async def ping(self) -> bool:
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        keys = list(keys_and_args[:numkeys])
        args = list(keys_and_args[numkeys:])
        if "redis.call('GET'" in script:
            key = keys[0]
            expected = str(args[0])
            if self.kv.get(key) == expected:
                del self.kv[key]
                return 1
            return 0
        key = keys[0]
        ttl = int(args[0]) if args else 60
        self.counters[key] += 1
        return [self.counters[key], ttl]

    async def aclose(self) -> None:
        return None


class DeterministicEmbeddingProvider(BaseEmbeddingProvider):
    """Fast deterministic embedding provider for offline unit and integration tests."""

    dimension = 8

    @property
    def provider_name(self) -> str:
        return "test_deterministic"

    @property
    def model_name(self) -> str:
        return "test-deterministic-embed-v1"

    def _embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [float(b) / 255.0 + 0.01 for b in digest[: self.dimension]]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class FakeLLMProvider(BaseLLMProvider):
    """Configurable test LLM provider capturing prompts and simulating failures."""

    def __init__(self, *, answer_prefix: str = "Grounded answer", error_to_raise: Exception | None = None) -> None:
        self.answer_prefix = answer_prefix
        self.error_to_raise = error_to_raise
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if self.error_to_raise is not None:
            raise self.error_to_raise
        return f"{self.answer_prefix} [S1]"


def build_minimal_pdf_bytes(text: str = "Company policy states annual leave is twenty days.") -> bytes:
    """Create a valid PDF payload with extractable text using pypdf."""
    from pypdf.generic import ContentStream, DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def build_minimal_docx_bytes(text: str = "Department onboarding handbook covers security procedures.") -> bytes:
    """Create a valid OOXML .docx archive parseable by docx2txt."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{escaped}</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


@pytest.fixture
def pdf_factory() -> Callable[[str], bytes]:
    return build_minimal_pdf_bytes


@pytest.fixture
def docx_factory() -> Callable[[str], bytes]:
    return build_minimal_docx_bytes


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(rate_limit_service, "_redis_client", client)
    monkeypatch.setattr(rate_limit_service, "_rate_limiter", None)
    monkeypatch.setattr(rate_limit_service, "get_redis_client", lambda: client)
    import backend.app.api.routes as api_routes
    import backend.app.api.document_management_routes as doc_routes
    import backend.app.workers.document_ingestion_worker as worker_mod

    monkeypatch.setattr(api_routes, "get_redis_client", lambda: client)
    monkeypatch.setattr(doc_routes, "get_redis_client", lambda: client)
    monkeypatch.setattr(worker_mod, "get_redis_client", lambda: client)
    return client


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    db_file = tmp_path / "test_rag.db"
    engine: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{db_file.as_posix()}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(db_session, "_session_factory", factory)
    monkeypatch.setattr(db_session, "get_engine", lambda: engine)
    monkeypatch.setattr(db_session, "get_session_factory", lambda: factory)
    import backend.app.workers.document_ingestion_worker as worker_mod

    monkeypatch.setattr(worker_mod, "get_session_factory", lambda: factory)

    try:
        yield factory
    finally:
        await engine.dispose()
        monkeypatch.setattr(db_session, "_engine", None)
        monkeypatch.setattr(db_session, "_session_factory", None)


@pytest_asyncio.fixture
async def db_session_instance(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "chroma_persist_directory", str(chroma_dir))
    monkeypatch.setattr(settings, "trusted_hosts", ["*"])
    yield tmp_path
    with suppress(Exception):
        from chromadb.api.client import SharedSystemClient

        SharedSystemClient.clear_system_cache()


@pytest.fixture
def embedding_provider() -> DeterministicEmbeddingProvider:
    return DeterministicEmbeddingProvider()


@pytest.fixture
def llm_provider() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest_asyncio.fixture
async def api_client(
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeRedis,
    isolated_storage: Path,
    embedding_provider: DeterministicEmbeddingProvider,
    llm_provider: FakeLLMProvider,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def override_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_database_session_dependency] = override_db
    app.dependency_overrides[get_embedding_provider_dependency] = lambda: embedding_provider
    app.dependency_overrides[get_llm_provider_dependency] = lambda: llm_provider

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        yield client
    app.dependency_overrides.clear()
