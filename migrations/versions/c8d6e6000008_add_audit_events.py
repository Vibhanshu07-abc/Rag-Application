"""add audit events

Revision ID: c8d6e6000008
Revises: b7c5d5000007
Create Date: 2026-11-12 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c8d6e6000008"
down_revision: Union[str, Sequence[str], None] = "b7c5d5000007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_event_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event_records")),
    )
    op.create_index("ix_audit_organization_created", "audit_event_records", ["organization_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_organization_created", table_name="audit_event_records")
    op.drop_table("audit_event_records")
