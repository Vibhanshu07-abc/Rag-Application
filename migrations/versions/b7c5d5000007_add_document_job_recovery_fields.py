"""add document job recovery fields

Revision ID: b7c5d5000007
Revises: a6b4f4000006
Create Date: 2026-11-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7c5d5000007"
down_revision: Union[str, Sequence[str], None] = "a6b4f4000006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_ingestion_jobs", sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("document_ingestion_jobs", sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False))
    op.add_column("document_ingestion_jobs", sa.Column("next_attempt_at", sa.DateTime(), nullable=True))
    op.create_index("ix_document_job_retry_due", "document_ingestion_jobs", ["status", "next_attempt_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_document_job_retry_due", table_name="document_ingestion_jobs")
    op.drop_column("document_ingestion_jobs", "next_attempt_at")
    op.drop_column("document_ingestion_jobs", "max_attempts")
    op.drop_column("document_ingestion_jobs", "attempt_count")
