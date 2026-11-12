"""create department records

Revision ID: 30f3b8010003
Revises: 6ff2b0500002
Create Date: 2026-11-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "30f3b8010003"
down_revision: Union[str, Sequence[str], None] = "6ff2b0500002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "department_records",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization_records.id"],
            name=op.f("fk_department_records_organization_id_organization_records"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_department_records")),
        sa.UniqueConstraint(
            "organization_id",
            "slug",
            name="uq_department_organization_slug",
        ),
    )
    op.create_index(
        "ix_department_organization_active",
        "department_records",
        ["organization_id", "is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_department_records_organization_id"),
        "department_records",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "department_membership_records",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["department_records.id"],
            name=op.f("fk_department_membership_records_department_id_department_records"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization_records.id"],
            name=op.f("fk_department_membership_records_organization_id_organization_records"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_records.id"],
            name=op.f("fk_department_membership_records_user_id_user_records"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_department_membership_records"),
        ),
        sa.UniqueConstraint(
            "department_id",
            "user_id",
            name="uq_department_membership_department_user",
        ),
    )
    op.create_index(
        "ix_department_membership_department_active",
        "department_membership_records",
        ["department_id", "is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_department_membership_records_department_id"),
        "department_membership_records",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_department_membership_records_organization_id"),
        "department_membership_records",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_department_membership_records_user_id"),
        "department_membership_records",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_department_membership_user_active",
        "department_membership_records",
        ["user_id", "is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_department_membership_user_active",
        table_name="department_membership_records",
    )
    op.drop_index(
        op.f("ix_department_membership_records_user_id"),
        table_name="department_membership_records",
    )
    op.drop_index(
        op.f("ix_department_membership_records_organization_id"),
        table_name="department_membership_records",
    )
    op.drop_index(
        op.f("ix_department_membership_records_department_id"),
        table_name="department_membership_records",
    )
    op.drop_index(
        "ix_department_membership_department_active",
        table_name="department_membership_records",
    )
    op.drop_table("department_membership_records")
    op.drop_index(
        op.f("ix_department_records_organization_id"),
        table_name="department_records",
    )
    op.drop_index(
        "ix_department_organization_active",
        table_name="department_records",
    )
    op.drop_table("department_records")
