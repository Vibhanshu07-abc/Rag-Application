"""add document lifecycle versions

Revision ID: f5a9e3000005
Revises: e4c8d2000004
Create Date: 2026-11-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f5a9e3000005"
down_revision: Union[str, Sequence[str], None] = "e4c8d2000004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_records", sa.Column("logical_document_id", sa.String(length=36), nullable=True))
    op.add_column("document_records", sa.Column("version_number", sa.Integer(), server_default="1", nullable=False))
    op.add_column("document_records", sa.Column("is_current", sa.Boolean(), server_default=sa.text("1"), nullable=False))
    op.add_column("document_records", sa.Column("archived_at", sa.DateTime(), nullable=True))
    # Every pre-lifecycle upload becomes version 1 of its own logical document.
    op.execute("UPDATE document_records SET logical_document_id = id WHERE logical_document_id IS NULL")
    op.alter_column(
        "document_records",
        "logical_document_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_document_logical_version",
        "document_records",
        ["organization_id", "knowledge_base_id", "logical_document_id", "version_number"],
    )
    op.create_index(
        "ix_document_logical_current",
        "document_records",
        ["organization_id", "knowledge_base_id", "logical_document_id", "is_current"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_document_logical_current", table_name="document_records")
    op.drop_constraint("uq_document_logical_version", "document_records", type_="unique")
    op.drop_column("document_records", "archived_at")
    op.drop_column("document_records", "is_current")
    op.drop_column("document_records", "version_number")
    op.drop_column("document_records", "logical_document_id")
