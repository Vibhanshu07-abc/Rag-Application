"""add document visibility

Revision ID: e4c8d2000004
Revises: 30f3b8010003
Create Date: 2026-11-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4c8d2000004"
down_revision: Union[str, Sequence[str], None] = "30f3b8010003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_records",
        sa.Column(
            "visibility",
            sa.String(length=32),
            server_default="organization",
            nullable=False,
        ),
    )
    op.add_column(
        "document_records",
        sa.Column("department_id", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_document_records_department_id_department_records"),
        "document_records",
        "department_records",
        ["department_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "uq_document_owner_knowledge_base_hash",
        "document_records",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_document_organization_knowledge_base_hash",
        "document_records",
        ["organization_id", "knowledge_base_id", "file_hash"],
    )
    op.create_index(
        "ix_document_organization_visibility",
        "document_records",
        ["organization_id", "visibility", "department_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_organization_visibility",
        table_name="document_records",
    )
    op.drop_constraint(
        "uq_document_organization_knowledge_base_hash",
        "document_records",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_document_owner_knowledge_base_hash",
        "document_records",
        ["organization_id", "user_id", "knowledge_base_id", "file_hash"],
    )
    op.drop_constraint(
        op.f("fk_document_records_department_id_department_records"),
        "document_records",
        type_="foreignkey",
    )
    op.drop_column("document_records", "department_id")
    op.drop_column("document_records", "visibility")
