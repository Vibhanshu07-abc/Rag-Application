"""add document ingestion jobs

Revision ID: a6b4f4000006
Revises: f5a9e3000005
Create Date: 2026-11-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a6b4f4000006"
down_revision: Union[str, Sequence[str], None] = "f5a9e3000005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_ingestion_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=64), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=True),
        sa.Column("replaces_document_id", sa.String(length=36), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_ingestion_jobs")),
    )
    op.create_index("ix_document_ingestion_jobs_status", "document_ingestion_jobs", ["status"], unique=False)
    op.create_index("ix_document_job_organization_status", "document_ingestion_jobs", ["organization_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_document_job_organization_status", table_name="document_ingestion_jobs")
    op.drop_index("ix_document_ingestion_jobs_status", table_name="document_ingestion_jobs")
    op.drop_table("document_ingestion_jobs")
