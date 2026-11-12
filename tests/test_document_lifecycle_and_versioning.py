from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.database.enums import DocumentStatus
from backend.app.database.repositories import DocumentJobRepository, DocumentRepository
from backend.app.services.document_job_queue import RedisDocumentJobQueue
from backend.app.vectorstores.chroma_store import ChromaVectorStore
from backend.app.workers.document_ingestion_worker import process_one


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _bootstrap_org_and_users(client: httpx.AsyncClient) -> dict:
    owner = (
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "admin@example.com",
                "password": "StrongPassword123!",
                "full_name": "Org Admin",
                "organization_name": "Lifecycle Corp",
            },
        )
    ).json()
    admin_headers = _headers(owner["access_token"])

    eng_dept = (
        await client.post(
            "/api/v1/organizations/current/departments",
            headers=admin_headers,
            json={"name": "Engineering"},
        )
    ).json()
    fin_dept = (
        await client.post(
            "/api/v1/organizations/current/departments",
            headers=admin_headers,
            json={"name": "Finance"},
        )
    ).json()

    da_user = (
        await client.post(
            "/api/v1/organizations/current/members",
            headers=admin_headers,
            json={
                "email": "eng-admin@example.com",
                "password": "StrongPassword123!",
                "full_name": "Eng Admin",
                "role": "member",
            },
        )
    ).json()
    emp_user = (
        await client.post(
            "/api/v1/organizations/current/members",
            headers=admin_headers,
            json={
                "email": "emp@example.com",
                "password": "StrongPassword123!",
                "full_name": "Eng Employee",
                "role": "member",
            },
        )
    ).json()

    await client.post(
        f"/api/v1/organizations/current/departments/{eng_dept['id']}/members",
        headers=admin_headers,
        json={"user_id": da_user["user_id"], "role": "department_admin"},
    )
    await client.post(
        f"/api/v1/organizations/current/departments/{eng_dept['id']}/members",
        headers=admin_headers,
        json={"user_id": emp_user["user_id"], "role": "employee"},
    )

    da_token = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "eng-admin@example.com", "password": "StrongPassword123!"},
        )
    ).json()["access_token"]
    emp_token = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "emp@example.com", "password": "StrongPassword123!"},
        )
    ).json()["access_token"]

    return {
        "org_id": owner["user"]["organization_id"],
        "admin_id": owner["user"]["id"],
        "admin_headers": admin_headers,
        "da_headers": _headers(da_token),
        "emp_headers": _headers(emp_token),
        "eng_id": eng_dept["id"],
        "fin_id": fin_dept["id"],
    }


@pytest.mark.asyncio
async def test_pdf_docx_upload_validation_and_publishing_workflow(
    api_client: httpx.AsyncClient,
    fake_redis,
    embedding_provider,
    pdf_factory,
    docx_factory,
) -> None:
    ctx = await _bootstrap_org_and_users(api_client)
    queue = RedisDocumentJobQueue(fake_redis)

    # Reject invalid file extension and empty file
    bad_ext = await api_client.post(
        "/api/v1/documents",
        headers=ctx["admin_headers"],
        data={"knowledge_base_id": "default", "visibility": "organization"},
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )
    assert bad_ext.status_code == 400

    empty_pdf = await api_client.post(
        "/api/v1/documents",
        headers=ctx["admin_headers"],
        data={"knowledge_base_id": "default", "visibility": "organization"},
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert empty_pdf.status_code == 400

    # Upload organization PDF and engineering DOCX
    org_upload = await api_client.post(
        "/api/v1/documents",
        headers=ctx["admin_headers"],
        data={"knowledge_base_id": "default", "visibility": "organization"},
        files={"file": ("policy.pdf", pdf_factory("Annual leave allocation is twenty-two days."), "application/pdf")},
    )
    assert org_upload.status_code == 201
    org_job_id = org_upload.json()["job_id"]

    eng_upload = await api_client.post(
        "/api/v1/documents",
        headers=ctx["da_headers"],
        data={"knowledge_base_id": "default", "visibility": "department", "department_id": ctx["eng_id"]},
        files={
            "file": (
                "runbook.docx",
                docx_factory("Engineering incident escalation requires paging the on-call lead."),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert eng_upload.status_code == 201
    eng_job_id = eng_upload.json()["job_id"]

    # Process both jobs via worker
    await process_one(org_job_id, queue, embedding_provider=embedding_provider, embedding_dimension=8)
    await process_one(eng_job_id, queue, embedding_provider=embedding_provider, embedding_dimension=8)

    org_job = (await api_client.get(f"/api/v1/documents/jobs/{org_job_id}", headers=ctx["admin_headers"])).json()
    eng_job = (await api_client.get(f"/api/v1/documents/jobs/{eng_job_id}", headers=ctx["da_headers"])).json()
    assert org_job["status"] == "completed"
    assert eng_job["status"] == "completed"
    org_doc_id = org_job["document_id"]
    eng_doc_id = eng_job["document_id"]

    # Completed documents MUST remain unpublished until explicitly published; Employees must NOT see them yet
    emp_list_before = (await api_client.get("/api/v1/documents", headers=ctx["emp_headers"])).json()
    assert emp_list_before["documents"] == []
    assert (await api_client.get(f"/api/v1/documents/{org_doc_id}", headers=ctx["emp_headers"])).status_code == 404

    # Dept Admin sees completed draft inside Engineering, cannot publish org-wide document, CAN publish Engineering DOCX
    assert (await api_client.post(f"/api/v1/documents/{org_doc_id}/publish", headers=ctx["da_headers"])).status_code in (403, 404)
    assert (await api_client.post(f"/api/v1/documents/{org_doc_id}/publish", headers=ctx["emp_headers"])).status_code in (403, 404)

    pub_eng = await api_client.post(f"/api/v1/documents/{eng_doc_id}/publish", headers=ctx["da_headers"])
    assert pub_eng.status_code == 200
    assert pub_eng.json()["is_published"] is True
    assert pub_eng.json()["status"] == "published"

    pub_org = await api_client.post(f"/api/v1/documents/{org_doc_id}/publish", headers=ctx["admin_headers"])
    assert pub_org.status_code == 200
    assert pub_org.json()["is_published"] is True

    # Dept Admin and Employee still cannot unpublish/archive a published org-wide document (403 Forbidden)
    assert (await api_client.post(f"/api/v1/documents/{org_doc_id}/unpublish", headers=ctx["da_headers"])).status_code == 403
    assert (await api_client.post(f"/api/v1/documents/{org_doc_id}/archive", headers=ctx["emp_headers"])).status_code == 403

    # Employee now lists both published docs
    emp_list_after = (await api_client.get("/api/v1/documents", headers=ctx["emp_headers"])).json()
    assert {doc["id"] for doc in emp_list_after["documents"]} == {org_doc_id, eng_doc_id}

    # Verify Chroma metadata matches published state, then unpublish org doc and verify Chroma immediately excludes it
    reader = ChromaVectorStore(
        embedding_provider=embedding_provider,
        organization_id=ctx["org_id"],
        user_id=ctx["admin_id"],
        knowledge_base_id="default",
        department_ids=(ctx["eng_id"],),
    )
    try:
        assert {d.metadata["document_id"] for d in reader.similarity_search("leave escalation", k=10)} == {
            org_doc_id,
            eng_doc_id,
        }
        unpub_org = await api_client.post(f"/api/v1/documents/{org_doc_id}/unpublish", headers=ctx["admin_headers"])
        assert unpub_org.status_code == 200
        assert unpub_org.json()["is_published"] is False
        assert unpub_org.json()["status"] == "completed"

        emp_list_unpub = (await api_client.get("/api/v1/documents", headers=ctx["emp_headers"])).json()
        assert [doc["id"] for doc in emp_list_unpub["documents"]] == [eng_doc_id]
        assert {d.metadata["document_id"] for d in reader.similarity_search("leave escalation", k=10)} == {
            eng_doc_id,
        }

        # Archiving Engineering doc hides it from Employee and RAG retrieval too
        arch_eng = await api_client.post(f"/api/v1/documents/{eng_doc_id}/archive", headers=ctx["da_headers"])
        assert arch_eng.status_code == 200
        assert arch_eng.json()["status"] == "archived"
        assert arch_eng.json()["is_current"] is False
        assert arch_eng.json()["is_published"] is False
        assert (await api_client.get("/api/v1/documents", headers=ctx["emp_headers"])).json()["documents"] == []
        assert reader.similarity_search("escalation", k=10) == []
    finally:
        reader.close()


@pytest.mark.asyncio
async def test_atomic_version_replacement_conflict_and_failure_preservation(
    api_client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis,
    embedding_provider,
    pdf_factory,
) -> None:
    ctx = await _bootstrap_org_and_users(api_client)
    queue = RedisDocumentJobQueue(fake_redis)

    # 1. Upload, process, and publish v1
    v1_bytes = pdf_factory("Release schedule v1: deploy every Tuesday.")
    v1_up = (
        await api_client.post(
            "/api/v1/documents",
            headers=ctx["admin_headers"],
            data={"knowledge_base_id": "default", "visibility": "organization"},
            files={"file": ("release-v1.pdf", v1_bytes, "application/pdf")},
        )
    ).json()
    await process_one(v1_up["job_id"], queue, embedding_provider=embedding_provider, embedding_dimension=8)
    v1_id = (await api_client.get(f"/api/v1/documents/jobs/{v1_up['job_id']}", headers=ctx["admin_headers"])).json()["document_id"]
    pub_v1 = await api_client.post(f"/api/v1/documents/{v1_id}/publish", headers=ctx["admin_headers"])
    assert pub_v1.status_code == 200, pub_v1.text

    # 2. Queue replacement v2 (same file hash should NOT trigger false failure on v1; wait: use different content for v2 first, then test failed replacement!)
    v2_bytes = pdf_factory("Release schedule v2: deploy daily with canary checks.")
    v2_up = await api_client.post(
        "/api/v1/documents",
        headers=ctx["admin_headers"],
        data={"knowledge_base_id": "default", "visibility": "organization", "replaces_document_id": v1_id},
        files={"file": ("release-v2.pdf", v2_bytes, "application/pdf")},
    )
    assert v2_up.status_code == 201, v2_up.text
    v2_job_id = v2_up.json()["job_id"]

    # Concurrent replacement of same document while v2 job is queued MUST be rejected with 409 Conflict
    concurrent_up = await api_client.post(
        "/api/v1/documents",
        headers=ctx["admin_headers"],
        data={"knowledge_base_id": "default", "visibility": "organization", "replaces_document_id": v1_id},
        files={"file": ("release-v2b.pdf", pdf_factory("Compete"), "application/pdf")},
    )
    assert concurrent_up.status_code == 409

    # Simulate failure inside worker during v2 processing -> v1 MUST remain current & published!
    async def boom(*args, **kwargs):
        raise RuntimeError("transient embedding failure")

    from backend.app.services import document_ingestion

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(document_ingestion.DocumentIngestionService, "ingest_document", boom)
        await process_one(v2_job_id, queue, embedding_provider=embedding_provider, embedding_dimension=8)

    # Check v1 still visible & published to Employee
    emp_docs = (await api_client.get("/api/v1/documents", headers=ctx["emp_headers"])).json()["documents"]
    assert len(emp_docs) == 1
    assert emp_docs[0]["id"] == v1_id
    assert emp_docs[0]["is_published"] is True
    assert emp_docs[0]["is_current"] is True

    # Reset failed job so next attempt succeeds cleanly with restored ingest_document
    async with session_factory() as session:
        job_repo = DocumentJobRepository(session)
        failed_job = await job_repo.get_for_update(v2_job_id)
        assert failed_job is not None
        failed_job.status = "queued"
        failed_job.next_attempt_at = None
        await session.commit()

    await process_one(v2_job_id, queue, embedding_provider=embedding_provider, embedding_dimension=8)
    v2_job = (await api_client.get(f"/api/v1/documents/jobs/{v2_job_id}", headers=ctx["admin_headers"])).json()
    assert v2_job["status"] == "completed"
    v2_id = v2_job["document_id"]
    assert v2_id != v1_id

    # After atomic replacement: v1 is archived/obsolete, v2 is current v2 unpublished draft
    v1_after = (await api_client.get(f"/api/v1/documents/{v1_id}", headers=ctx["admin_headers"])).json()
    v2_after = (await api_client.get(f"/api/v1/documents/{v2_id}", headers=ctx["admin_headers"])).json()
    assert v1_after["status"] == DocumentStatus.ARCHIVED.value
    assert v1_after["is_current"] is False
    assert v1_after["is_published"] is False
    assert v2_after["version_number"] == 2
    assert v2_after["is_current"] is True
    assert v2_after["is_published"] is False
    assert v2_after["logical_document_id"] == v1_after["logical_document_id"]

    # Re-uploading v1_bytes (identical hash to now-archived v1) should succeed as a fresh current doc
    reupload = (
        await api_client.post(
            "/api/v1/documents",
            headers=ctx["admin_headers"],
            data={"knowledge_base_id": "default", "visibility": "organization"},
            files={"file": ("release-v1-again.pdf", v1_bytes, "application/pdf")},
        )
    ).json()
    await process_one(reupload["job_id"], queue, embedding_provider=embedding_provider, embedding_dimension=8)
    reupload_job = (await api_client.get(f"/api/v1/documents/jobs/{reupload['job_id']}", headers=ctx["admin_headers"])).json()
    assert reupload_job["status"] == "completed"
