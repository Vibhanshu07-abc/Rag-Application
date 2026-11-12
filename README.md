# Production-Grade Retrieval-Augmented Generation (RAG) Platform

A full-stack, multi-tenant Retrieval-Augmented Generation (RAG) platform built with **FastAPI**, **Next.js**, **MySQL**, **Redis**, **ChromaDB**, **Hugging Face embeddings**, and pluggable LLMs (**Ollama** locally by default, **Google Gemini**, or **OpenAI**).

Beyond basic "upload a PDF and ask questions" demos, this platform implements JWT + refresh-token authentication, organization and department-level Role-Based Access Control (RBAC), asynchronous document ingestion via a Redis-backed worker, document lifecycle management (publish, unpublish, archive, and version replacement), audit logging, rate limiting, security middleware, health/readiness checks, and automated tests.

---

## What This Project Includes

- **Retrieval-Augmented Generation (RAG)** with grounded answers and source citations (`[S1]`, `[S2]`, ...)
- **Multi-format Document Ingestion** supporting **PDF** and **DOCX** files (`PyPDF` / `python-docx` loaders & parsers, text cleaning, chunking, metadata building)
- **Asynchronous Ingestion Worker** (`backend.app.workers.document_ingestion_worker`) with Redis job queueing, MySQL job state tracking, retry backoff, stale `PROCESSING` recovery, and monitoring telemetry
- **Document Lifecycle & Versioning** with draft/publish/unpublish/archive controls, SHA-256 hash indexing, atomic version replacement, and Chroma vector synchronization/cleanup
- **Local & Cloud LLM Support** via a unified provider factory:
  - **Ollama** (`llama3.2:3b` default, local HTTP `/api/chat`, no API key required)
  - **Google Gemini** (`gemini-2.5-flash`)
  - **OpenAI** (`gpt-5.4-mini`)
- **Hugging Face Embeddings** (`sentence-transformers/all-MiniLM-L6-v2` on CPU) and pluggable vector stores (**ChromaDB** persistent store, **Qdrant** adapter)
- **Authentication & Security** with Argon2 password hashing, JWT access tokens, refresh-token rotation/logout, CORS, trusted hosts, and security headers middleware
- **Organizations, Departments & RBAC**
  - Organization roles: `owner`, `admin`, `member`, `viewer`
  - Department roles: `department_admin`, `employee`
  - Organization-wide and department-scoped document visibility + audit event logging
- **Redis Sliding-Window / Counter Rate Limiting** per endpoint group (general, register, login, refresh, upload, RAG query)
- **Next.js + TypeScript Frontend** with dedicated workspaces for Chat, Documents (upload, lifecycle actions, job status/monitoring), Members & Departments, and Settings
- **Alembic Migrations & Pytest Suite** covering auth, RBAC, department roles, Chroma isolation, lifecycle/versioning, worker recovery, and LLM providers

---

## High-Level Architecture

```text
User
 │
 ▼
Next.js Frontend (http://localhost:3000)
 │
 ▼
FastAPI Backend (http://127.0.0.1:8000/api/v1)
 │
 ├── JWT Auth & Refresh Tokens
 ├── Organization & Department RBAC
 ├── Document Lifecycle & Job APIs
 ├── RAG Query Service
 ├── Audit Logging & Security Middleware
 └── Redis Rate Limiting
 │
 ├───────────────┬───────────────┬───────────────┬───────────────┐
 ▼               ▼               ▼               ▼               ▼
MySQL          Redis          ChromaDB      Ingestion        LLM Provider
(Users, Orgs,  (Rate Limits   (Tenant &     Worker           (Ollama /
 Depts, Docs,   & Job Queue)   Dept-Scoped   (PDF/DOCX ->     Gemini /
 Jobs, Audit)                  Vectors)       Embeddings)     OpenAI)
```

---

## Ingestion & RAG Flow

### Asynchronous Document Ingestion

```text
PDF / DOCX Upload ──► Local File Storage + MySQL Document/Job Record
                             │
                             ▼ (Job ID queued in Redis)
                 Document Ingestion Worker
                             │
                             ▼
           Loader (PDF/DOCX) ──► Parser ──► Text Cleaner ──► Chunker
                             │
                             ▼
             Hugging Face Embeddings ──► ChromaDB (Org/Dept Isolated)
```

### RAG Query Pipeline

```text
User Question
    │
    ▼
Query Embedding (Hugging Face)
    │
    ▼
Chroma Similarity Search (Filtered by Organization, Department & Published Status)
    │
    ▼
Retrieved Chunks + Grounding System Prompt
    │
    ▼
LLM Provider (Ollama / Gemini / OpenAI)
    │
    ▼
Grounded Answer + Source References
```

---

## Project Structure

```text
.
├── backend/
│   └── app/
│       ├── api/                        # FastAPI app, routes, schemas, middleware, rate limiting
│       │   ├── app.py
│       │   ├── auth_routes.py
│       │   ├── department_routes.py
│       │   ├── document_management_routes.py
│       │   ├── health_routes.py
│       │   ├── organization_routes.py
│       │   ├── routes.py
│       │   ├── middleware.py
│       │   └── rate_limit_middleware.py
│       ├── auth/                       # JWT, Argon2 password hashing, RBAC roles, auth service
│       ├── core/                       # Settings (pydantic-settings), exceptions, structlog logging
│       ├── database/                   # SQLAlchemy async models, enums, repositories, session
│       ├── embeddings/                 # Embedding factory (Hugging Face, OpenAI)
│       ├── generation/                 # Grounded RAG prompt templates & generation service
│       ├── ingestion/                  # PDF/DOCX loaders, parsers, cleaner, chunker, metadata builder
│       ├── llms/                       # LLM factory (Ollama, Gemini, OpenAI)
│       ├── organizations/              # Organization & department services
│       ├── rate_limiting/              # Redis rate limiter & dependencies
│       ├── retrieval/                  # Tenant-isolated semantic retrieval service
│       ├── services/                   # Document management, ingestion, file storage, job queue, telemetry, RAG query, health
│       ├── vectorstores/               # ChromaDB & Qdrant vector store adapters
│       ├── workers/                    # Background document ingestion worker
│       └── main.py                     # Uvicorn ASGI entrypoint
│
├── frontend/
│   ├── app/
│   │   ├── (auth)/                     # login & register pages
│   │   └── (dashboard)/                # chat, documents, members, settings pages
│   ├── components/                     # chat, documents, members, settings, layout, providers
│   ├── lib/                            # Typed API client, formatters, TypeScript types
│   ├── .env.example
│   ├── API_CONTRACT.md
│   ├── openapi.json
│   └── package.json
│
├── migrations/                         # Alembic environment & schema revisions
│   └── versions/
├── scripts/                            # Dev token & OpenAPI export utilities
├── tests/                              # Unit and integration pytest suite
├── .env.example
├── .env.docker.example
├── alembic.ini
├── compose.yml
├── pyproject.toml
└── uv.lock
```

---

## Running the Project

### 1. Configure Backend Environment

Copy the example environment file and update database credentials, JWT secret, and (optionally) cloud LLM keys:

```powershell
Copy-Item .env.example .env
```

By default, `.env.example` configures **local Ollama** (`LLM_PROVIDER=ollama`, `OLLAMA_LLM_MODEL=llama3.2:3b`), **Hugging Face embeddings** (`sentence-transformers/all-MiniLM-L6-v2`), **ChromaDB** (`./data/chroma`), **MySQL**, and **Redis**.

If using Ollama locally, ensure Ollama is running and the model is pulled:

```bash
ollama pull llama3.2:3b
```

*(To use Gemini or OpenAI instead, set `LLM_PROVIDER=gemini` with `GEMINI_API_KEY` or `LLM_PROVIDER=openai` with `OPENAI_API_KEY` in `.env`.)*

### 2. Install Backend Dependencies

This project uses `uv` for Python dependency management (Python 3.12+):

```bash
uv sync
```

### 3. Start Required Services (MySQL & Redis)

Start MySQL via Docker Compose (copy `.env.docker.example` to `.env.docker` and align credentials with `DATABASE_URL` in `.env` first):

```bash
docker compose up -d
```

Make sure a Redis server is also running on `127.0.0.1:6379` (for rate limiting and worker job queueing), e.g. via Docker:

```bash
docker run -d --name rag-redis -p 127.0.0.1:6379:6379 redis:7-alpine
```

### 4. Apply Database Migrations

```bash
uv run alembic upgrade head
```

### 5. Start the FastAPI Backend

```bash
uv run uvicorn backend.app.main:app --reload
```

- **Backend API**: `http://127.0.0.1:8000`
- **Swagger UI**: `http://127.0.0.1:8000/docs`

### 6. Start the Document Ingestion Worker

In a separate terminal, start the background worker so uploaded documents are parsed, chunked, embedded, and indexed:

```bash
uv run python -m backend.app.workers.document_ingestion_worker
```

### 7. Start the Frontend

In another terminal:

```bash
cd frontend
```

Create `.env.local`:

```powershell
Copy-Item .env.example .env.local
```

Install dependencies and start Next.js (`pnpm` or `npm`):

```bash
npm install
npm run dev
```

Open `http://localhost:3000` in your browser.

---

## API Overview

All routes are served under `/api/v1`:

```text
Authentication (/api/v1/auth)
├── POST   /register
├── POST   /login
├── POST   /refresh
├── POST   /logout
└── GET    /me

Organizations & Departments (/api/v1/organizations/current)
├── GET    /
├── GET    /members
├── POST   /members
├── PATCH  /members/{membership_id}/role
├── DELETE /members/{membership_id}
├── GET    /departments
├── POST   /departments
├── GET    /departments/{department_id}/members
├── POST   /departments/{department_id}/members
├── PATCH  /departments/{department_id}/members/{membership_id}/role
└── DELETE /departments/{department_id}/members/{membership_id}

Documents & Ingestion Jobs (/api/v1/documents)
├── POST   /
├── GET    /
├── GET    /{document_id}
├── POST   /{document_id}/publish
├── POST   /{document_id}/unpublish
├── POST   /{document_id}/archive
├── DELETE /{document_id}
├── GET    /jobs/{job_id}
├── POST   /jobs/{job_id}/retry
└── GET    /monitoring

RAG (/api/v1/rag)
└── POST   /query

Health (/api/v1/health)
├── GET    /
├── GET    /live
└── GET    /ready
```

---

## Testing & Verification

Run the backend test suite:

```bash
uv run pytest
```

Run frontend TypeScript checks:

```bash
npm --prefix frontend run typecheck
```

---

## Disclaimer

This project is intended for educational and development purposes. Before deploying to a production environment, review secrets management, database/Redis security, HTTPS/HSTS settings, LLM usage limits, backup strategies, and access controls.
