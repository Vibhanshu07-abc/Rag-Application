"use client";

import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Archive,
  CircleAlert,
  FileCheck2,
  FileText,
  Filter,
  Globe,
  LoaderCircle,
  Lock,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { useAuth } from "@/components/providers/auth-provider";
import { useToast } from "@/components/providers/toast-provider";
import {
  ApiError,
  normalizeDepartments,
  normalizeDocumentJob,
  normalizeDocuments,
  normalizeMonitoring,
} from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type { Department, DocumentJob, DocumentMonitoring, DocumentRecord } from "@/lib/types";

export function DocumentsWorkspace() {
  const { apiFetch, user } = useAuth();
  const { showToast } = useToast();
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [job, setJob] = useState<DocumentJob | null>(null);
  const [monitoring, setMonitoring] = useState<DocumentMonitoring | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [knowledgeBase, setKnowledgeBase] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [busyDocId, setBusyDocId] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploadKb, setUploadKb] = useState("default");
  const [visibility, setVisibility] = useState<"organization" | "department">("organization");
  const [departmentId, setDepartmentId] = useState("");
  const [replacementId, setReplacementId] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const isOrgAdmin = user?.role === "owner" || user?.role === "admin";
  const managedDepartmentIds = useMemo(
    () =>
      new Set(
        (user?.department_memberships ?? [])
          .filter((membership) => membership.role === "department_admin")
          .map((membership) => membership.department_id),
      ),
    [user?.department_memberships],
  );
  const isDeptAdmin = managedDepartmentIds.size > 0;
  const canUpload = isOrgAdmin || isDeptAdmin;
  const canMonitor = isOrgAdmin;

  const manageableDepartments = useMemo(
    () =>
      departments.filter(
        (department) => department.is_active && (isOrgAdmin || managedDepartmentIds.has(department.id)),
      ),
    [departments, isOrgAdmin, managedDepartmentIds],
  );

  const departmentNameById = useMemo(
    () => new Map(departments.map((department) => [department.id, department.name])),
    [departments],
  );

  function canManageDocument(document: DocumentRecord): boolean {
    if (isOrgAdmin) return true;
    if (document.visibility === "department" && document.department_id) {
      return managedDepartmentIds.has(document.department_id);
    }
    return false;
  }

  async function loadDocuments() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ offset: "0", limit: "100" });
      if (knowledgeBase.trim()) params.set("knowledge_base_id", knowledgeBase.trim());
      setDocuments(normalizeDocuments(await apiFetch<unknown>(`/documents?${params}`)).documents);
    } catch (error) {
      showToast({
        tone: "error",
        title: "Unable to load documents",
        description: error instanceof ApiError ? error.message : "Please try again.",
      });
    } finally {
      setLoading(false);
    }
  }

  async function loadMonitoring() {
    if (canMonitor) {
      try {
        setMonitoring(normalizeMonitoring(await apiFetch<unknown>("/documents/monitoring")));
      } catch {
        setMonitoring(null);
      }
    }
  }

  useEffect(() => {
    void loadDocuments();
  }, [knowledgeBase]);

  useEffect(() => {
    void apiFetch<unknown>("/organizations/current/departments")
      .then((value) => setDepartments(normalizeDepartments(value)))
      .catch(() => undefined);
    void loadMonitoring();
  }, [canMonitor]);

  useEffect(() => {
    if (!job || !["queued", "processing"].includes(job.status)) return;
    const timer = window.setInterval(() => {
      void apiFetch<unknown>(`/documents/jobs/${encodeURIComponent(job.id)}`)
        .then((value) => {
          const next = normalizeDocumentJob(value);
          setJob(next);
          if (next.status === "completed" || next.status === "failed") {
            void loadDocuments();
            void loadMonitoring();
          }
        })
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [apiFetch, job?.id, job?.status]);

  const filtered = useMemo(
    () =>
      documents.filter((item) =>
        `${item.filename} ${item.knowledge_base_id} ${item.status} ${item.is_published ? "published" : "draft"}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      ),
    [documents, query],
  );

  const knowledgeBases = useMemo(
    () => [...new Set(documents.map((item) => item.knowledge_base_id))].sort(),
    [documents],
  );

  function openNewUploadModal(target?: DocumentRecord) {
    if (target) {
      setUploadKb(target.knowledge_base_id);
      setVisibility(target.visibility === "department" ? "department" : "organization");
      setDepartmentId(target.department_id || "");
      setReplacementId(target.id);
    } else {
      setUploadKb("default");
      if (isOrgAdmin) {
        setVisibility("organization");
        setDepartmentId("");
      } else {
        setVisibility("department");
        setDepartmentId(manageableDepartments[0]?.id || "");
      }
      setReplacementId("");
    }
    setFile(null);
    setUploadOpen(true);
  }

  async function uploadDocument() {
    if (!file || !uploadKb.trim() || (visibility === "department" && !departmentId)) return;
    setUploading(true);
    try {
      const form = new FormData();
      form.append("knowledge_base_id", uploadKb.trim());
      form.append("visibility", visibility);
      if (visibility === "department") form.append("department_id", departmentId);
      if (replacementId) form.append("replaces_document_id", replacementId);
      form.append("file", file);
      setJob(normalizeDocumentJob(await apiFetch<unknown>("/documents", { method: "POST", body: form })));
      showToast({
        tone: "success",
        title: replacementId ? "Replacement queued" : "Document queued",
        description: `${file.name} will be indexed in the background.`,
      });
      setUploadOpen(false);
      setFile(null);
      setReplacementId("");
    } catch (error) {
      showToast({
        tone: "error",
        title: "Upload failed",
        description: error instanceof ApiError ? error.message : "The document could not be uploaded.",
      });
    } finally {
      setUploading(false);
    }
  }

  async function retryJob() {
    if (!job) return;
    try {
      setJob(normalizeDocumentJob(await apiFetch<unknown>(`/documents/jobs/${encodeURIComponent(job.id)}/retry`, { method: "POST" })));
      showToast({ tone: "success", title: "Job requeued" });
    } catch (error) {
      showToast({
        tone: "error",
        title: "Retry failed",
        description: error instanceof ApiError ? error.message : "Please try again.",
      });
    }
  }

  async function runDocumentAction(document: DocumentRecord, action: "publish" | "unpublish" | "archive") {
    setBusyDocId(document.id);
    try {
      await apiFetch<unknown>(`/documents/${encodeURIComponent(document.id)}/${action}`, { method: "POST" });
      await loadDocuments();
      const labelMap = {
        publish: "Document published",
        unpublish: "Document unpublished",
        archive: "Document archived",
      };
      showToast({ tone: "success", title: labelMap[action] });
    } catch (error) {
      showToast({
        tone: "error",
        title: `Failed to ${action} document`,
        description: error instanceof ApiError ? error.message : "Please try again.",
      });
    } finally {
      setBusyDocId(null);
    }
  }

  async function deleteDocument(document: DocumentRecord) {
    if (!confirm(`Delete ${document.filename} (v${document.version_number || 1})? This removes its vectors and database record.`)) return;
    setBusyDocId(document.id);
    try {
      await apiFetch<void>(`/documents/${encodeURIComponent(document.id)}`, { method: "DELETE" });
      setDocuments((items) => items.filter((item) => item.id !== document.id));
      showToast({ tone: "success", title: "Document deleted" });
    } catch (error) {
      showToast({
        tone: "error",
        title: "Delete failed",
        description: error instanceof ApiError ? error.message : "Please try again.",
      });
    } finally {
      setBusyDocId(null);
    }
  }

  function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0] || null;
    if (selected && !/\.(pdf|docx)$/i.test(selected.name)) {
      showToast({ tone: "error", title: "PDF or DOCX required", description: "Choose a PDF or DOCX document." });
      event.target.value = "";
      return;
    }
    setFile(selected);
  }

  function renderStatusBadge(document: DocumentRecord) {
    const normalized = document.status.toLowerCase();
    const badgeClass = document.is_published ? "published" : normalized;
    const label = document.is_published
      ? "published"
      : normalized === "completed"
        ? "completed (unpublished)"
        : normalized;
    return (
      <span className={`status-badge status-${badgeClass}`}>
        {document.is_published ? <Globe size={12} /> : normalized === "completed" ? <FileCheck2 size={12} /> : null}
        {label}
      </span>
    );
  }

  return (
    <section className="page-workspace">
      <header className="page-header">
        <div>
          <p className="eyebrow dark">Knowledge library</p>
          <h1>Documents</h1>
          <p>
            {canUpload
              ? "Manage organization or department documents, versions, publishing workflow, and retrieval readiness."
              : "Browse published knowledge base documents authorized for your organization and departments."}
          </p>
        </div>
        {canUpload ? (
          <button className="primary-button" onClick={() => openNewUploadModal()}>
            <Plus size={17} /> Upload document
          </button>
        ) : null}
      </header>

      {!canUpload ? (
        <div className="inline-banner compact">
          <CircleAlert size={18} />
          <div>
            <strong>Employee access</strong>
            <p>You can inspect published, authorized documents and ask grounded questions. Uploading and publishing require admin rights.</p>
          </div>
        </div>
      ) : null}

      {job ? (
        <article className="job-card">
          <div>
            <strong>Latest job: {job.filename}</strong>
            <p>
              <span className={`status-badge status-${job.status}`}>{job.status}</span> Attempt {job.attempt_count}/{job.max_attempts}
              {job.error_message ? ` — ${job.error_message}` : ""}
            </p>
          </div>
          {job.status === "failed" && canUpload ? (
            <button className="secondary-button" onClick={() => void retryJob()}>
              <RotateCcw size={16} /> Retry
            </button>
          ) : null}
        </article>
      ) : null}

      {canMonitor && monitoring ? (
        <article className="monitor-card">
          <div>
            <Activity size={18} />
            <strong>Operations</strong>
          </div>
          <p>{Object.entries(monitoring.job_counts).map(([key, value]) => `${key}: ${value}`).join(" · ") || "No jobs yet"}</p>
          <small>
            {monitoring.workers.length} active worker slot(s) ·{" "}
            {monitoring.workers.map((worker) => `CPU ${worker.cpu_seconds || "0"}s / ${formatBytes(Number(worker.python_memory_bytes || 0))}`).join(" · ")}
          </small>
        </article>
      ) : null}

      <div className="toolbar-card">
        <label className="search-field">
          <Search size={17} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search filename, status, or knowledge base" />
        </label>
        <label className="filter-select">
          <Filter size={16} />
          <select value={knowledgeBase} onChange={(event) => setKnowledgeBase(event.target.value)}>
            <option value="">All knowledge bases</option>
            {knowledgeBases.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <button
          className="icon-button bordered"
          onClick={() => {
            void loadDocuments();
            void loadMonitoring();
          }}
          title="Refresh"
        >
          <RefreshCw size={17} className={loading ? "spin" : ""} />
        </button>
      </div>

      <div className="document-summary">
        <span>
          <strong>{filtered.length}</strong> documents shown
        </span>
        <span>
          <strong>{knowledgeBases.length}</strong> knowledge bases
        </span>
      </div>

      {loading ? (
        <div className="table-loader">
          <LoaderCircle size={25} className="spin" />
          <p>Loading documents…</p>
        </div>
      ) : filtered.length ? (
        <div className="document-table-wrap">
          <table className="document-table">
            <thead>
              <tr>
                <th>Document</th>
                <th>Scope / version</th>
                <th>Knowledge base</th>
                <th>Status</th>
                <th>Size</th>
                <th>Chunks</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filtered.map((document) => {
                const canManage = canManageDocument(document);
                const statusLower = document.status.toLowerCase();
                const isBusy = busyDocId === document.id;
                const deptLabel = document.department_id
                  ? departmentNameById.get(document.department_id) || document.department_id
                  : null;
                const canPublish = canManage && !document.is_published && statusLower === "completed";
                const canUnpublish = canManage && Boolean(document.is_published);
                const canArchive = canManage && statusLower !== "archived" && statusLower !== "deleting";
                const canReplace =
                  canManage &&
                  document.is_current !== false &&
                  statusLower !== "archived" &&
                  statusLower !== "deleting";

                return (
                  <tr key={document.id}>
                    <td>
                      <div className="document-name">
                        <span className="file-icon">
                          <FileText size={19} />
                        </span>
                        <div>
                          <strong>{document.filename}</strong>
                          <small>{document.id}</small>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className="kb-pill">
                        {document.visibility === "department" ? `dept: ${deptLabel || "department"}` : "organization"} · v
                        {document.version_number || 1}
                        {document.is_current === false ? " (obsolete)" : ""}
                      </span>
                    </td>
                    <td>
                      <span className="kb-pill">{document.knowledge_base_id}</span>
                    </td>
                    <td>{renderStatusBadge(document)}</td>
                    <td>{formatBytes(document.file_size_bytes)}</td>
                    <td>{document.chunk_count ?? "—"}</td>
                    <td>
                      {canManage ? (
                        <div className="table-actions">
                          {canPublish ? (
                            <button
                              className="action-pill accent"
                              disabled={isBusy}
                              onClick={() => void runDocumentAction(document, "publish")}
                              title="Publish to authorized readers"
                            >
                              <Globe size={12} /> Publish
                            </button>
                          ) : null}
                          {canUnpublish ? (
                            <button
                              className="action-pill"
                              disabled={isBusy}
                              onClick={() => void runDocumentAction(document, "unpublish")}
                              title="Unpublish back to draft"
                            >
                              <Lock size={12} /> Unpublish
                            </button>
                          ) : null}
                          {canReplace ? (
                            <button
                              className="action-pill"
                              disabled={isBusy}
                              onClick={() => openNewUploadModal(document)}
                              title="Upload new version"
                            >
                              <UploadCloud size={12} /> Replace
                            </button>
                          ) : null}
                          {canArchive ? (
                            <button
                              className="action-pill"
                              disabled={isBusy}
                              onClick={() => void runDocumentAction(document, "archive")}
                              title="Archive document"
                            >
                              <Archive size={12} /> Archive
                            </button>
                          ) : null}
                          <button
                            className="danger-icon-button"
                            disabled={isBusy}
                            onClick={() => void deleteDocument(document)}
                            title="Delete document"
                          >
                            {isBusy ? <LoaderCircle size={17} className="spin" /> : <Trash2 size={17} />}
                          </button>
                        </div>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty-state-card">
          <UploadCloud size={34} />
          <h2>No documents found</h2>
          <p>{canUpload ? "Upload a PDF or DOCX document to get started." : "No published documents are available yet."}</p>
          {canUpload ? (
            <button className="secondary-button" onClick={() => openNewUploadModal()}>
              Upload a document
            </button>
          ) : null}
        </div>
      )}

      {uploadOpen ? (
        <div className="modal-layer">
          <button className="modal-backdrop" onClick={() => !uploading && setUploadOpen(false)} aria-label="Close dialog" />
          <div className="modal-card" role="dialog" aria-modal="true">
            <div className="modal-header">
              <div>
                <h2>{replacementId ? "Queue replacement version" : "Queue document ingestion"}</h2>
                <p>Uploaded documents stay unpublished until an admin publishes them.</p>
              </div>
              <button className="icon-button" onClick={() => setUploadOpen(false)} disabled={uploading}>
                <X size={19} />
              </button>
            </div>
            <div className="modal-body form-stack">
              <label className="field-label">
                Knowledge base ID
                <input
                  className="text-input"
                  value={uploadKb}
                  onChange={(event) => setUploadKb(event.target.value)}
                  maxLength={128}
                  disabled={Boolean(replacementId)}
                />
              </label>
              <label className="field-label">
                Visibility
                <select
                  className="text-input"
                  value={visibility}
                  onChange={(event) => setVisibility(event.target.value as "organization" | "department")}
                  disabled={!isOrgAdmin || Boolean(replacementId)}
                >
                  {isOrgAdmin ? <option value="organization">Organization-wide</option> : null}
                  <option value="department">Department-only</option>
                </select>
              </label>
              {visibility === "department" ? (
                <label className="field-label">
                  Department
                  <select
                    className="text-input"
                    value={departmentId}
                    onChange={(event) => setDepartmentId(event.target.value)}
                    disabled={Boolean(replacementId)}
                  >
                    <option value="">Select department</option>
                    {manageableDepartments.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <label className="field-label">
                Replace current version (optional)
                <select
                  className="text-input"
                  value={replacementId}
                  onChange={(event) => {
                    const nextId = event.target.value;
                    setReplacementId(nextId);
                    const target = documents.find((item) => item.id === nextId);
                    if (target) {
                      setVisibility(target.visibility === "department" ? "department" : "organization");
                      setDepartmentId(target.department_id || "");
                    }
                  }}
                >
                  <option value="">Create new logical document</option>
                  {documents
                    .filter(
                      (item) =>
                        item.is_current !== false &&
                        item.status !== "archived" &&
                        item.status !== "deleting" &&
                        item.knowledge_base_id === uploadKb &&
                        canManageDocument(item),
                    )
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.filename} · v{item.version_number || 1}
                      </option>
                    ))}
                </select>
              </label>
              <button className={`drop-zone ${file ? "has-file" : ""}`} type="button" onClick={() => fileRef.current?.click()}>
                <input
                  ref={fileRef}
                  type="file"
                  accept="application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx"
                  hidden
                  onChange={handleFile}
                />
                {file ? (
                  <>
                    <FileCheck2 size={30} />
                    <strong>{file.name}</strong>
                    <span>{formatBytes(file.size)}</span>
                  </>
                ) : (
                  <>
                    <UploadCloud size={31} />
                    <strong>Choose PDF or DOCX</strong>
                    <span>Maximum size follows the backend limit</span>
                  </>
                )}
              </button>
            </div>
            <div className="modal-footer">
              <button className="secondary-button" onClick={() => setUploadOpen(false)} disabled={uploading}>
                Cancel
              </button>
              <button
                className="primary-button"
                onClick={() => void uploadDocument()}
                disabled={!file || !uploadKb.trim() || (visibility === "department" && !departmentId) || uploading}
              >
                {uploading ? (
                  <>
                    <LoaderCircle size={17} className="spin" /> Queuing…
                  </>
                ) : (
                  <>
                    <UploadCloud size={17} /> Queue ingestion
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
