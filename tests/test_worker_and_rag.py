from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.core.exceptions import EmbeddingError, LLMProviderError
from backend.app.database.enums import DocumentJobStatus
from backend.app.database.repositories import DocumentJobRepository
from backend.app.services.document_job_queue import DocumentQueueError, RedisDocumentJobQueue
from backend.app.workers.document_ingestion_worker import process_one, reconcile_pending_jobs


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_worker_claim_locking_retries_and_reconciliation(
    api_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis,
    embedding_provider,
    pdf_factory,
) -> None:
    reg = (
        await api_client.post(
            "/api/v1/auth/register",
            json={
                "email": "worker-owner@example.com",
                "password": "StrongPassword123!",
                "full_name": "Worker Owner",
                "organization_name": "Worker Corp",
            },
        )
    ).json()
    headers = _headers(reg["access_token"])
    queue = RedisDocumentJobQueue(fake_redis)

    # 1. Queue lock prevents claiming an already-processing job
    up = (
        await api_client.post(
            "/api/v1/documents",
            headers=headers,
            data={"knowledge_base_id": "default", "visibility": "organization"},
            files={"file": ("doc.pdf", pdf_factory("Worker job locking verification."), "application/pdf")},
        )
    ).json()
    job_id = up["job_id"]

    async with session_factory() as session:
        repo = DocumentJobRepository(session)
        claimed = await repo.claim_queued_job(job_id)
        assert claimed is not None
        await session.commit()

        second_claim = await repo.claim_queued_job(job_id)
        assert second_claim is None

        # Backdate started_at to simulate a crashed worker holding a stale PROCESSING lease
        claimed.started_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
        await session.commit()

    # Reconcile recovers stale PROCESSING job back into queued FIFO
    fake_redis.lists.clear()
    requeued = await reconcile_pending_jobs(queue)
    assert requeued >= 1
    assert await queue.dequeue(timeout_seconds=1) == job_id

    # Process with simulated transient error -> schedules exponential backoff retry
    async def transient_fail(*args, **kwargs):
        raise RuntimeError("temporary chroma hiccup")

    from backend.app.services import document_ingestion

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(document_ingestion.DocumentIngestionService, "ingest_document", transient_fail)
        await process_one(job_id, queue, embedding_provider=embedding_provider, embedding_dimension=8)

    job_after_fail = (await api_client.get(f"/api/v1/documents/jobs/{job_id}", headers=headers)).json()
    assert job_after_fail["status"] == DocumentJobStatus.QUEUED.value
    assert job_after_fail["attempt_count"] == 2  # 1 from initial claim + 1 from post-recovery claim
    assert job_after_fail["next_attempt_at"] is not None

    # Replacement lock bounds & timeout behavior
    lock_key = await queue.acquire_document_lock("doc-x", "worker-1", timeout_seconds=0.5)
    with pytest.raises(DocumentQueueError):
        await queue.acquire_document_lock("doc-x", "worker-2", timeout_seconds=0.3)
    await queue.release_document_lock(lock_key, "worker-2")  # Wrong owner must NOT release
    assert await fake_redis.get(lock_key) == "worker-1"
    await queue.release_document_lock(lock_key, "worker-1")
    assert await fake_redis.get(lock_key) is None


@pytest.mark.asyncio
async def test_rag_query_grounding_citations_isolation_and_provider_failures(
    api_client: httpx.AsyncClient,
    fake_redis,
    embedding_provider,
    llm_provider,
    pdf_factory,
) -> None:
    # Register org + Employee + Engineering Dept + Finance Dept
    owner = (
        await api_client.post(
            "/api/v1/auth/register",
            json={
                "email": "rag-admin@example.com",
                "password": "StrongPassword123!",
                "full_name": "RAG Admin",
                "organization_name": "RAG Corp",
            },
        )
    ).json()
    admin_headers = _headers(owner["access_token"])

    eng = (
        await api_client.post(
            "/api/v1/organizations/current/departments",
            headers=admin_headers,
            json={"name": "Engineering"},
        )
    ).json()
    fin = (
        await api_client.post(
            "/api/v1/organizations/current/departments",
            headers=admin_headers,
            json={"name": "Finance"},
        )
    ).json()

    emp = (
        await api_client.post(
            "/api/v1/organizations/current/members",
            headers=admin_headers,
            json={
                "email": "rag-emp@example.com",
                "password": "StrongPassword123!",
                "full_name": "RAG Employee",
                "role": "member",
            },
        )
    ).json()
    await api_client.post(
        f"/api/v1/organizations/current/departments/{eng['id']}/members",
        headers=admin_headers,
        json={"user_id": emp["user_id"], "role": "employee"},
    )
    emp_headers = _headers(
        (
            await api_client.post(
                "/api/v1/auth/login",
                json={"email": "rag-emp@example.com", "password": "StrongPassword123!"},
            )
        ).json()["access_token"]
    )

    queue = RedisDocumentJobQueue(fake_redis)

    # Query before any published docs exist -> ungrounded fallback, LLM NOT called
    empty_rag = await api_client.post(
        "/api/v1/rag/query",
        headers=emp_headers,
        json={"knowledge_base_id": "default", "query": "What is our SLA?"},
    )
    assert empty_rag.status_code == 200
    assert empty_rag.json()["grounded"] is False
    assert empty_rag.json()["retrieved_chunk_count"] == 0
    assert llm_provider.calls == []

    # Upload & process Finance published doc + Engineering draft doc + Engineering published doc
    fin_up = (
        await api_client.post(
            "/api/v1/documents",
            headers=admin_headers,
            data={"knowledge_base_id": "default", "visibility": "department", "department_id": fin["id"]},
            files={"file": ("finance.pdf", pdf_factory("Executive bonus pool is five million."), "application/pdf")},
        )
    ).json()
    eng_draft_up = (
        await api_client.post(
            "/api/v1/documents",
            headers=admin_headers,
            data={"knowledge_base_id": "default", "visibility": "department", "department_id": eng["id"]},
            files={"file": ("eng-draft.pdf", pdf_factory("Unpublished engineering secret draft."), "application/pdf")},
        )
    ).json()
    eng_pub_up = (
        await api_client.post(
            "/api/v1/documents",
            headers=admin_headers,
            data={"knowledge_base_id": "default", "visibility": "department", "department_id": eng["id"]},
            files={"file": ("eng-sla.pdf", pdf_factory("Engineering incident SLA target is fifteen minutes."), "application/pdf")},
        )
    ).json()

    for jid in (fin_up["job_id"], eng_draft_up["job_id"], eng_pub_up["job_id"]):
        await process_one(jid, queue, embedding_provider=embedding_provider, embedding_dimension=8)

    fin_doc_id = (await api_client.get(f"/api/v1/documents/jobs/{fin_up['job_id']}", headers=admin_headers)).json()["document_id"]
    eng_pub_id = (await api_client.get(f"/api/v1/documents/jobs/{eng_pub_up['job_id']}", headers=admin_headers)).json()["document_id"]

    await api_client.post(f"/api/v1/documents/{fin_doc_id}/publish", headers=admin_headers)
    await api_client.post(f"/api/v1/documents/{eng_pub_id}/publish", headers=admin_headers)

    # Employee asks SLA question -> grounded ONLY in eng-sla.pdf (never finance.pdf or eng-draft.pdf)
    rag_res = await api_client.post(
        "/api/v1/rag/query",
        headers=emp_headers,
        json={"knowledge_base_id": "default", "query": "Engineering SLA executive bonus"},
    )
    assert rag_res.status_code == 200, rag_res.text
    payload = rag_res.json()
    assert payload["grounded"] is True
    assert payload["retrieved_chunk_count"] == 1
    assert payload["citations"] == ["S1"]
    assert payload["sources"][0]["document_id"] == eng_pub_id
    assert payload["sources"][0]["filename"] == "eng-sla.pdf"
    assert payload["sources"][0]["page_number"] == 1
    assert payload["cached"] is False

    # Second query -> Cache HIT (served from Redis, LLM not called again)
    call_count_before = len(llm_provider.calls)
    cached_res = await api_client.post(
        "/api/v1/rag/query",
        headers=emp_headers,
        json={"knowledge_base_id": "default", "query": "Engineering SLA executive bonus"},
    )
    assert cached_res.status_code == 200
    cached_payload = cached_res.json()
    assert cached_payload["cached"] is True
    assert len(llm_provider.calls) == call_count_before

    # Third query with bypass_cache=True -> Cache bypassed & refreshed (LLM called)
    bypass_res = await api_client.post(
        "/api/v1/rag/query",
        headers=emp_headers,
        json={"knowledge_base_id": "default", "query": "Engineering SLA executive bonus", "bypass_cache": True},
    )
    assert bypass_res.status_code == 200
    bypass_payload = bypass_res.json()
    assert bypass_payload["cached"] is False
    assert len(llm_provider.calls) == call_count_before + 1

    # Cross-department RBAC cache isolation test:
    # 1. Admin (has Finance access) queries finance document -> answer is cached
    admin_fin_res = await api_client.post(
        "/api/v1/rag/query",
        headers=admin_headers,
        json={"knowledge_base_id": "default", "document_id": fin_doc_id, "query": "What is the executive bonus pool?"},
    )
    assert admin_fin_res.status_code == 200
    assert admin_fin_res.json()["grounded"] is True
    assert admin_fin_res.json()["sources"][0]["document_id"] == fin_doc_id
    assert admin_fin_res.json()["sources"][0]["filename"] == "finance.pdf"

    # 2. Engineering employee queries the EXACT same question and document -> MUST NOT receive cached Finance answer
    emp_fin_res = await api_client.post(
        "/api/v1/rag/query",
        headers=emp_headers,
        json={"knowledge_base_id": "default", "document_id": fin_doc_id, "query": "What is the executive bonus pool?"},
    )
    assert emp_fin_res.status_code == 200
    emp_fin_payload = emp_fin_res.json()
    assert emp_fin_payload["grounded"] is False
    assert emp_fin_payload["retrieved_chunk_count"] == 0
    assert emp_fin_payload["citations"] == []
    assert emp_fin_payload["sources"] == []
    assert emp_fin_payload["cached"] is False

    # LLM provider unavailability returns clean 503 error
    llm_provider.error_to_raise = LLMProviderError(message="Gemini quota exhausted.")
    llm_err = await api_client.post(
        "/api/v1/rag/query",
        headers=emp_headers,
        json={"knowledge_base_id": "default", "query": "Engineering SLA"},
    )
    assert llm_err.status_code == 503
    llm_provider.error_to_raise = None

    # Embedding provider failure returns clean 500 error
    orig_embed = embedding_provider.embed_query

    def broken_embed(text: str) -> list[float]:
        raise EmbeddingError(message="Embedding model failed.")

    embedding_provider.embed_query = broken_embed  # type: ignore[method-assign]
    try:
        emb_err = await api_client.post(
            "/api/v1/rag/query",
            headers=emp_headers,
            json={"knowledge_base_id": "default", "query": "Engineering SLA"},
        )
        assert emb_err.status_code == 500
    finally:
        embedding_provider.embed_query = orig_embed  # type: ignore[method-assign]
