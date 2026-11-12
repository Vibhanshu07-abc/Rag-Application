"""add document publishing fields and hash index

Revision ID: d9e7f7000009
Revises: c8d6e6000008
Create Date: 2026-11-12 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d9e7f7000009"
down_revision: Union[str, Sequence[str], None] = "c8d6e6000008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_document_organization_knowledge_base_hash",
        "document_records",
        type_="unique",
    )
    op.create_index(
        "ix_document_organization_knowledge_base_hash",
        "document_records",
        ["organization_id", "knowledge_base_id", "file_hash"],
        unique=False,
    )
    op.add_column(
        "document_records",
        sa.Column(
            "is_published",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "document_records",
        sa.Column("published_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "document_records",
        sa.Column("published_by_user_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "document_records",
        sa.Column("archived_by_user_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_document_org_published_current",
        "document_records",
        ["organization_id", "knowledge_base_id", "is_published", "is_current"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_org_published_current",
        table_name="document_records",
    )
    op.drop_column("document_records", "archived_by_user_id")
    op.drop_column("document_records", "published_by_user_id")
    op.drop_column("document_records", "published_at")
    op.drop_column("document_records", "is_published")
    op.drop_index(
        "ix_document_organization_knowledge_base_hash",
        table_name="document_records",
    )
    op.create_unique_constraint(
        "uq_document_organization_knowledge_base_hash",
        "document_records",
        ["organization_id", "knowledge_base_id", "file_hash"],
    )
