from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DepartmentRecord(Base):
    """One department belonging to an organization."""

    __tablename__ = "department_records"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )

    organization_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "organization_records.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    slug: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "slug",
            name="uq_department_organization_slug",
        ),
        Index(
            "ix_department_organization_active",
            "organization_id",
            "is_active",
        ),
    )


class DepartmentMembershipRecord(Base):
    """A user's active role inside one organization department."""

    __tablename__ = "department_membership_records"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )

    department_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "department_records.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    organization_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "organization_records.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "user_records.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "department_id",
            "user_id",
            name="uq_department_membership_department_user",
        ),
        Index(
            "ix_department_membership_department_active",
            "department_id",
            "is_active",
        ),
        Index(
            "ix_department_membership_user_active",
            "user_id",
            "is_active",
        ),
    )
