from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.exceptions import ConflictError
from backend.app.database.models.auth import UserRecord
from backend.app.database.models.department import (
    DepartmentMembershipRecord,
    DepartmentRecord,
)
from backend.app.database.models.membership import MembershipRecord


class DepartmentRepository:
    """Database operations for departments and department memberships."""

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        self.session = session

    async def create_department(
        self,
        *,
        organization_id: str,
        name: str,
        slug: str,
    ) -> DepartmentRecord:
        department = DepartmentRecord(
            id=uuid4().hex,
            organization_id=organization_id,
            name=name,
            slug=slug,
            is_active=True,
        )

        try:
            async with self.session.begin_nested():
                self.session.add(department)
                await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                message=(
                    "A department with this name already exists "
                    "in the organization."
                )
            ) from exc

        return department

    async def get_department(
        self,
        *,
        department_id: str,
        organization_id: str,
        active_only: bool = False,
    ) -> DepartmentRecord | None:
        statement = (
            select(DepartmentRecord)
            .where(DepartmentRecord.id == department_id)
            .where(DepartmentRecord.organization_id == organization_id)
        )

        if active_only:
            statement = statement.where(
                DepartmentRecord.is_active.is_(True)
            )

        result = await self.session.execute(statement)

        return result.scalar_one_or_none()

    async def list_departments(
        self,
        *,
        organization_id: str,
    ) -> list[DepartmentRecord]:
        statement = (
            select(DepartmentRecord)
            .where(DepartmentRecord.organization_id == organization_id)
            .order_by(DepartmentRecord.name.asc())
        )

        result = await self.session.execute(statement)

        return list(result.scalars().all())

    async def get_active_organization_membership(
        self,
        *,
        organization_id: str,
        user_id: str,
    ) -> MembershipRecord | None:
        statement = (
            select(MembershipRecord)
            .where(MembershipRecord.organization_id == organization_id)
            .where(MembershipRecord.user_id == user_id)
            .where(MembershipRecord.is_active.is_(True))
        )

        result = await self.session.execute(statement)

        return result.scalar_one_or_none()

    async def get_user_by_id(
        self,
        user_id: str,
    ) -> UserRecord | None:
        statement = select(UserRecord).where(
            UserRecord.id == user_id
        )

        result = await self.session.execute(statement)

        return result.scalar_one_or_none()

    async def create_membership(
        self,
        *,
        department_id: str,
        organization_id: str,
        user_id: str,
        role: str,
    ) -> DepartmentMembershipRecord:
        membership = DepartmentMembershipRecord(
            id=uuid4().hex,
            department_id=department_id,
            organization_id=organization_id,
            user_id=user_id,
            role=role,
            is_active=True,
        )

        try:
            async with self.session.begin_nested():
                self.session.add(membership)
                await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                message=(
                    "This user is already assigned to the department."
                )
            ) from exc

        return membership

    async def get_active_membership(
        self,
        *,
        department_id: str,
        organization_id: str,
        user_id: str,
    ) -> DepartmentMembershipRecord | None:
        statement = (
            select(DepartmentMembershipRecord)
            .where(DepartmentMembershipRecord.department_id == department_id)
            .where(DepartmentMembershipRecord.organization_id == organization_id)
            .where(DepartmentMembershipRecord.user_id == user_id)
            .where(DepartmentMembershipRecord.is_active.is_(True))
        )

        result = await self.session.execute(statement)

        return result.scalar_one_or_none()

    async def list_active_department_ids(
        self,
        *,
        organization_id: str,
        user_id: str,
    ) -> list[str]:
        statement = (
            select(DepartmentMembershipRecord.department_id)
            .where(DepartmentMembershipRecord.organization_id == organization_id)
            .where(DepartmentMembershipRecord.user_id == user_id)
            .where(DepartmentMembershipRecord.is_active.is_(True))
        )

        result = await self.session.execute(statement)

        return list(result.scalars().all())

    async def get_membership_for_update(
        self,
        *,
        membership_id: str,
        department_id: str,
        organization_id: str,
    ) -> DepartmentMembershipRecord | None:
        statement = (
            select(DepartmentMembershipRecord)
            .where(DepartmentMembershipRecord.id == membership_id)
            .where(DepartmentMembershipRecord.department_id == department_id)
            .where(DepartmentMembershipRecord.organization_id == organization_id)
            .with_for_update()
        )

        result = await self.session.execute(statement)

        return result.scalar_one_or_none()

    async def list_members(
        self,
        *,
        department_id: str,
        organization_id: str,
    ) -> list[tuple[DepartmentMembershipRecord, UserRecord]]:
        statement = (
            select(DepartmentMembershipRecord, UserRecord)
            .join(
                UserRecord,
                UserRecord.id == DepartmentMembershipRecord.user_id,
            )
            .where(DepartmentMembershipRecord.department_id == department_id)
            .where(DepartmentMembershipRecord.organization_id == organization_id)
            .order_by(DepartmentMembershipRecord.created_at.asc())
        )

        result = await self.session.execute(statement)

        return list(result.all())

    async def set_role(
        self,
        *,
        membership: DepartmentMembershipRecord,
        role: str,
    ) -> DepartmentMembershipRecord:
        membership.role = role
        await self.session.flush()

        return membership

    async def deactivate_membership(
        self,
        *,
        membership: DepartmentMembershipRecord,
    ) -> DepartmentMembershipRecord:
        membership.is_active = False
        await self.session.flush()

        return membership
