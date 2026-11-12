import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth.roles import (
    DepartmentRole,
    can_manage_department_members,
    can_manage_departments,
)
from backend.app.core.exceptions import (
    AppError,
    AuthorizationError,
    ConflictError,
)
from backend.app.database.models.auth import UserRecord
from backend.app.database.models.department import (
    DepartmentMembershipRecord,
    DepartmentRecord,
)
from backend.app.database.repositories.audit_repository import (
    AuditRepository,
)
from backend.app.database.repositories.department_repository import (
    DepartmentRepository,
)


class DepartmentNotFoundError(AppError):
    code = "DEPARTMENT_NOT_FOUND"
    default_message = "Department was not found."
    status_code = 404


class DepartmentMembershipNotFoundError(AppError):
    code = "DEPARTMENT_MEMBERSHIP_NOT_FOUND"
    default_message = "Department membership was not found."
    status_code = 404


class DepartmentValidationError(AppError):
    code = "INVALID_DEPARTMENT_INPUT"
    default_message = "The department request is invalid."
    status_code = 422


@dataclass(frozen=True, slots=True)
class DepartmentSummary:
    """Public department representation returned by DepartmentService."""

    id: str
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DepartmentMember:
    """Public department membership representation."""

    membership_id: str
    user_id: str
    email: str
    full_name: str | None
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


def _slugify_department_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "department"


def _to_department_summary(record: DepartmentRecord) -> DepartmentSummary:
    return DepartmentSummary(
        id=record.id,
        name=record.name,
        slug=record.slug,
        is_active=record.is_active,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_department_member(
    membership: DepartmentMembershipRecord,
    user: UserRecord,
) -> DepartmentMember:
    return DepartmentMember(
        membership_id=membership.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=membership.role,
        is_active=membership.is_active,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


class DepartmentService:
    """
    Business logic for organization departments and department memberships.

    Authorization rules:
    - Organization Owners and Admins can create departments and manage members
      across all departments in their organization.
    - Department Admins can list and manage employee memberships strictly
      within their own active department.
    - Regular employees cannot manage departments or department memberships.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        department_repository: DepartmentRepository | None = None,
        audit_repository: AuditRepository | None = None,
    ) -> None:
        self.session = session
        self.departments = department_repository or DepartmentRepository(session)
        self.audit = audit_repository or AuditRepository(session)

    async def list_departments(
        self,
        *,
        organization_id: str,
        actor_role: str | None = None,
        actor_user_id: str | None = None,
    ) -> list[DepartmentSummary]:
        if actor_role is not None and not can_manage_departments(actor_role):
            if not actor_user_id:
                return []
            records = await self.departments.list_departments_for_user(
                organization_id=organization_id,
                user_id=actor_user_id,
            )
            return [_to_department_summary(record) for record in records]

        records = await self.departments.list_departments(
            organization_id=organization_id,
        )
        return [_to_department_summary(record) for record in records]

    async def create_department(
        self,
        *,
        organization_id: str,
        actor_role: str,
        name: str,
    ) -> DepartmentSummary:
        if not can_manage_departments(actor_role):
            raise AuthorizationError(
                message="Only organization administrators can create departments."
            )

        cleaned_name = " ".join(name.strip().split())
        if len(cleaned_name) < 2:
            raise DepartmentValidationError(
                message="Department name must contain at least 2 characters."
            )

        slug = _slugify_department_name(cleaned_name)
        record = await self.departments.create_department(
            organization_id=organization_id,
            name=cleaned_name,
            slug=slug,
        )
        await self.session.commit()
        return _to_department_summary(record)

    async def list_members(
        self,
        *,
        department_id: str,
        organization_id: str,
        actor_organization_role: str | None = None,
        actor_department_role: str | None = None,
    ) -> list[DepartmentMember]:
        if actor_organization_role is not None:
            self._require_member_management_permission(
                actor_organization_role=actor_organization_role,
                actor_department_role=actor_department_role,
            )

        department = await self.departments.get_department(
            department_id=department_id,
            organization_id=organization_id,
        )
        if department is None:
            raise DepartmentNotFoundError()

        rows = await self.departments.list_members(
            department_id=department_id,
            organization_id=organization_id,
        )
        return [_to_department_member(membership, user) for membership, user in rows]

    async def assign_member(
        self,
        *,
        department_id: str,
        organization_id: str,
        actor_user_id: str,
        actor_organization_role: str,
        actor_department_role: str | None,
        user_id: str,
        role: str,
    ) -> DepartmentMember:
        is_org_admin = self._require_member_management_permission(
            actor_organization_role=actor_organization_role,
            actor_department_role=actor_department_role,
        )

        normalized_role = self._validate_department_role(role)
        if not is_org_admin and normalized_role == DepartmentRole.DEPARTMENT_ADMIN:
            raise AuthorizationError(
                message="Only organization administrators can assign department admins."
            )

        department = await self.departments.get_department(
            department_id=department_id,
            organization_id=organization_id,
            active_only=True,
        )
        if department is None:
            raise DepartmentNotFoundError(message="Active department was not found.")

        org_membership = await self.departments.get_active_organization_membership(
            organization_id=organization_id,
            user_id=user_id,
        )
        if org_membership is None:
            raise DepartmentNotFoundError(
                message="Target user must be an active member of this organization."
            )

        target_user = await self.departments.get_user_by_id(user_id)
        if target_user is None or not target_user.is_active:
            raise DepartmentNotFoundError(
                message="Target user account is not active."
            )

        existing = await self.departments.get_membership_for_user(
            department_id=department_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if existing is not None:
            if existing.is_active:
                raise ConflictError(
                    message="This user is already assigned to the department."
                )
            membership = await self.departments.reactivate_membership(
                membership=existing,
                role=normalized_role.value,
            )
        else:
            membership = await self.departments.create_membership(
                department_id=department_id,
                organization_id=organization_id,
                user_id=user_id,
                role=normalized_role.value,
            )

        await self.audit.record(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            event_type="department_member_assigned",
            resource_type="department_membership",
            resource_id=membership.id,
            details={
                "department_id": department_id,
                "target_user_id": user_id,
                "role": membership.role,
            },
        )
        await self.session.commit()
        return _to_department_member(membership, target_user)

    async def change_member_role(
        self,
        *,
        department_id: str,
        organization_id: str,
        actor_user_id: str,
        actor_organization_role: str,
        actor_department_role: str | None,
        membership_id: str,
        role: str,
    ) -> DepartmentMember:
        is_org_admin = self._require_member_management_permission(
            actor_organization_role=actor_organization_role,
            actor_department_role=actor_department_role,
        )

        normalized_role = self._validate_department_role(role)
        membership = await self.departments.get_membership_for_update(
            membership_id=membership_id,
            department_id=department_id,
            organization_id=organization_id,
        )
        if membership is None or not membership.is_active:
            raise DepartmentMembershipNotFoundError()

        if membership.user_id == actor_user_id and not is_org_admin:
            raise AuthorizationError(
                message="You cannot change your own department role."
            )

        if not is_org_admin and (
            membership.role == DepartmentRole.DEPARTMENT_ADMIN.value
            or normalized_role == DepartmentRole.DEPARTMENT_ADMIN
        ):
            raise AuthorizationError(
                message="Only organization administrators can modify department admin roles."
            )

        target_user = await self.departments.get_user_by_id(membership.user_id)
        if target_user is None:
            raise DepartmentNotFoundError(message="Member user record was not found.")

        updated = await self.departments.set_role(
            membership=membership,
            role=normalized_role.value,
        )
        await self.audit.record(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            event_type="department_member_role_updated",
            resource_type="department_membership",
            resource_id=updated.id,
            details={
                "department_id": department_id,
                "target_user_id": updated.user_id,
                "role": updated.role,
            },
        )
        await self.session.commit()
        return _to_department_member(updated, target_user)

    async def deactivate_member(
        self,
        *,
        department_id: str,
        organization_id: str,
        actor_user_id: str,
        actor_organization_role: str,
        actor_department_role: str | None,
        membership_id: str,
    ) -> DepartmentMember:
        is_org_admin = self._require_member_management_permission(
            actor_organization_role=actor_organization_role,
            actor_department_role=actor_department_role,
        )

        membership = await self.departments.get_membership_for_update(
            membership_id=membership_id,
            department_id=department_id,
            organization_id=organization_id,
        )
        if membership is None or not membership.is_active:
            raise DepartmentMembershipNotFoundError()

        if membership.user_id == actor_user_id and not is_org_admin:
            raise AuthorizationError(
                message="You cannot remove yourself from the department."
            )

        if (
            not is_org_admin
            and membership.role == DepartmentRole.DEPARTMENT_ADMIN.value
        ):
            raise AuthorizationError(
                message="Only organization administrators can remove a department admin."
            )

        target_user = await self.departments.get_user_by_id(membership.user_id)
        if target_user is None:
            raise DepartmentNotFoundError(message="Member user record was not found.")

        updated = await self.departments.deactivate_membership(
            membership=membership,
        )
        await self.audit.record(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            event_type="department_member_deactivated",
            resource_type="department_membership",
            resource_id=updated.id,
            details={
                "department_id": department_id,
                "target_user_id": updated.user_id,
            },
        )
        await self.session.commit()
        return _to_department_member(updated, target_user)

    @staticmethod
    def _require_member_management_permission(
        *,
        actor_organization_role: str,
        actor_department_role: str | None,
    ) -> bool:
        if can_manage_departments(actor_organization_role):
            return True
        if actor_department_role and can_manage_department_members(
            actor_department_role
        ):
            return False
        raise AuthorizationError(
            message="You are not authorized to manage members in this department."
        )

    @staticmethod
    def _validate_department_role(role: str) -> DepartmentRole:
        try:
            return DepartmentRole(role)
        except ValueError as exc:
            raise DepartmentValidationError(
                message=f"Unsupported department role: {role}"
            ) from exc