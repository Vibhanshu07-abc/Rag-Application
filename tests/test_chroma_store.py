from pathlib import Path

from langchain_core.documents import Document

from backend.app.vectorstores.chroma_store import ChromaVectorStore


def _make_chunk(
    *,
    content: str,
    chunk_id: str,
    document_id: str,
    organization_id: str,
    user_id: str = "user-1",
    knowledge_base_id: str = "kb-main",
    visibility: str = "organization",
    department_id: str | None = None,
    logical_document_id: str = "logical-1",
    version_number: int = 1,
    is_current: bool = True,
    is_published: bool = True,
    status: str = "published",
) -> Document:
    metadata = {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "organization_id": organization_id,
        "user_id": user_id,
        "knowledge_base_id": knowledge_base_id,
        "visibility": visibility,
        "logical_document_id": logical_document_id,
        "version_number": version_number,
        "is_current": is_current,
        "is_published": is_published,
        "status": status,
        "filename": "policy.pdf",
        "page_number": 1,
    }
    if department_id is not None:
        metadata["department_id"] = department_id
    return Document(page_content=content, metadata=metadata)


def test_chroma_isolation_visibility_publishing_and_versioning(
    tmp_path: Path,
    embedding_provider,
) -> None:
    persist_dir = str(tmp_path / "chroma")

    org1_store = ChromaVectorStore(
        embedding_provider=embedding_provider,
        organization_id="org-1",
        user_id="admin-1",
        knowledge_base_id="kb-main",
        persist_directory=persist_dir,
    )
    org2_store = ChromaVectorStore(
        embedding_provider=embedding_provider,
        organization_id="org-2",
        user_id="admin-2",
        knowledge_base_id="kb-main",
        persist_directory=persist_dir,
    )
    org1_other_kb = ChromaVectorStore(
        embedding_provider=embedding_provider,
        organization_id="org-1",
        user_id="admin-1",
        knowledge_base_id="kb-other",
        persist_directory=persist_dir,
    )

    org1_store.add_documents(
        [
            _make_chunk(
                content="Org-wide published leave policy allows twenty days.",
                chunk_id="c1",
                document_id="doc-org-pub",
                organization_id="org-1",
            ),
            _make_chunk(
                content="Org-wide unpublished draft policy allows ninety days.",
                chunk_id="c2",
                document_id="doc-org-draft",
                organization_id="org-1",
                is_published=False,
                status="completed",
            ),
            _make_chunk(
                content="Engineering department secret roadmap for Q4.",
                chunk_id="c3",
                document_id="doc-dept-eng",
                organization_id="org-1",
                visibility="department",
                department_id="dept-eng",
            ),
            _make_chunk(
                content="Finance department confidential budget report.",
                chunk_id="c4",
                document_id="doc-dept-fin",
                organization_id="org-1",
                visibility="department",
                department_id="dept-fin",
            ),
            _make_chunk(
                content="Obsolete version one archived leave policy.",
                chunk_id="c5",
                document_id="doc-old-v1",
                organization_id="org-1",
                version_number=1,
                is_current=False,
                is_published=False,
                status="archived",
            ),
        ]
    )
    org2_store.add_documents(
        [
            _make_chunk(
                content="Org two secret handbook.",
                chunk_id="c1",
                document_id="doc-org2",
                organization_id="org-2",
            )
        ]
    )
    org1_other_kb.add_documents(
        [
            _make_chunk(
                content="Other KB document inside org one.",
                chunk_id="c1",
                document_id="doc-kb2",
                organization_id="org-1",
                knowledge_base_id="kb-other",
            )
        ]
    )

    try:
        # User with no department memberships sees only current, published org-wide doc in kb-main.
        plain_reader = ChromaVectorStore(
            embedding_provider=embedding_provider,
            organization_id="org-1",
            user_id="emp-1",
            knowledge_base_id="kb-main",
            department_ids=(),
            persist_directory=persist_dir,
        )
        try:
            plain_results = plain_reader.similarity_search("policy roadmap budget", k=10)
            plain_doc_ids = {doc.metadata["document_id"] for doc in plain_results}
            assert plain_doc_ids == {"doc-org-pub"}
        finally:
            plain_reader.close()

        # Engineering member sees org-wide published + dept-eng published, never draft/obsolete/dept-fin/org2/kb2.
        eng_reader = ChromaVectorStore(
            embedding_provider=embedding_provider,
            organization_id="org-1",
            user_id="eng-1",
            knowledge_base_id="kb-main",
            department_ids=("dept-eng",),
            persist_directory=persist_dir,
        )
        try:
            eng_results = eng_reader.similarity_search("policy roadmap budget", k=10)
            eng_doc_ids = {doc.metadata["document_id"] for doc in eng_results}
            assert eng_doc_ids == {"doc-org-pub", "doc-dept-eng"}

            # Admin/internal caller querying unpublished sees draft too when published_only=False.
            all_current = eng_reader.similarity_search("policy roadmap", k=10, published_only=False)
            all_current_ids = {doc.metadata["document_id"] for doc in all_current}
            assert all_current_ids == {"doc-org-pub", "doc-org-draft", "doc-dept-eng"}
        finally:
            eng_reader.close()

        # Unpublishing removes document from published similarity search immediately.
        draft_vector_ids = org1_store.add_documents(
            [
                _make_chunk(
                    content="Engineering v2 replacement handbook.",
                    chunk_id="c3",
                    document_id="doc-dept-eng-v2",
                    organization_id="org-1",
                    visibility="department",
                    department_id="dept-eng",
                    version_number=2,
                    is_current=True,
                    is_published=True,
                    status="published",
                )
            ]
        )
        assert len(draft_vector_ids) == 1
        # Verify vector_id differs by document_id even with identical chunk_id.
        assert draft_vector_ids[0] != org1_store._create_vector_id("c3", document_id="doc-dept-eng")

        org1_store.set_publication_status(draft_vector_ids, is_published=False, status="completed")
        eng_reader2 = ChromaVectorStore(
            embedding_provider=embedding_provider,
            organization_id="org-1",
            user_id="eng-1",
            knowledge_base_id="kb-main",
            department_ids=("dept-eng",),
            persist_directory=persist_dir,
        )
        try:
            after_unpub = {
                doc.metadata["document_id"]
                for doc in eng_reader2.similarity_search("Engineering handbook", k=10)
            }
            assert "doc-dept-eng-v2" not in after_unpub
        finally:
            eng_reader2.close()

        # Delete removes vectors completely.
        org1_store.delete_chunks(draft_vector_ids)
        assert org1_store._store._collection.get(ids=draft_vector_ids)["ids"] == []
    finally:
        org1_store.close()
        org2_store.close()
        org1_other_kb.close()