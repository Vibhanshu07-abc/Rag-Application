from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.department_schemas import (
    CreateDepartmentMemberRequest,
    CreateDepartmentRequest,
    DepartmentListResponse,
    DepartmentMemberListResponse,
    DepartmentMemberResponse,
    DepartmentResponse,
    UpdateDepartmentMemberRoleRequest,
)
from backend.app.api.dependencies import (
    DepartmentAccess,
    OrganizationAccess,
    get_database_session_dependency,
    get_organization_access,
)
from backend.app.auth.roles import (
    DepartmentRole,
    OrganizationRole,
)
from backend.app.core.config import settings
from backend.app.core.exceptions import AuthorizationError
from backend.app.database.repositories.department_repository import (
    DepartmentRepository,
)
from backend.app.organizations.department_service import (
    DepartmentMember,
    DepartmentService,
    DepartmentSummary,
)


router = APIRouter(
    prefix=f"{settings.api_prefix}/organizations/current/departments",
    tags=["Departments"],
)


ORGANIZATION_ADMIN_ROLES = {
    OrganizationRole.OWNER.value,
    OrganizationRole.ADMIN.value,
}


async def get_department_access_optional(
    department_id: str,
    access: OrganizationAccess = Depends(get_organization_access),
    session: AsyncSession = Depends(get_database_session_dependency),
) -> DepartmentAccess | None:
    membership = await DepartmentRepository(session).get_active_membership(
        department_id=department_id,
        organization_id=access.organization_id,
        user_id=access.user_id,
    )

    if membership is None:
        return None

    return DepartmentAccess(
        department_id=membership.department_id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        membership_id=membership.id,
        role=membership.role,
    )


async def require_department_member_management_access(
    access: OrganizationAccess = Depends(get_organization_access),
    department_access: DepartmentAccess | None = Depends(
        get_department_access_optional
    ),
) -> None:
    if access.role in ORGANIZATION_ADMIN_ROLES:
        return

    if (
        department_access is not None
        and department_access.role == DepartmentRole.DEPARTMENT_ADMIN.value
    ):
        return

    raise AuthorizationError(
        message=(
            "Only organization admins or department admins can "
            "view department members."
        )
    )


@router.get("", response_model=DepartmentListResponse)
async def list_departments(
    access: OrganizationAccess = Depends(get_organization_access),
    session: AsyncSession = Depends(get_database_session_dependency),
) -> DepartmentListResponse:
    departments = await DepartmentService(session=session).list_departments(
        organization_id=access.organization_id,
    )

    return DepartmentListResponse(
        departments=[
            department_response(department)
            for department in departments
        ]
    )


@router.post(
    "",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_department(
    request: CreateDepartmentRequest,
    access: OrganizationAccess = Depends(get_organization_access),
    session: AsyncSession = Depends(get_database_session_dependency),
) -> DepartmentResponse:
    if access.role not in ORGANIZATION_ADMIN_ROLES:
        raise AuthorizationError(
            message="Only organization owners and admins can create departments."
        )

    department = await DepartmentService(session=session).create_department(
        organization_id=access.organization_id,
        actor_role=access.role,
        name=request.name,
    )

    return department_response(department)


@router.get(
    "/{department_id}/members",
    response_model=DepartmentMemberListResponse,
    dependencies=[
        Depends(require_department_member_management_access)
    ],
)
async def list_department_members(
    department_id: str,
    access: OrganizationAccess = Depends(get_organization_access),
    session: AsyncSession = Depends(get_database_session_dependency),
) -> DepartmentMemberListResponse:
    members = await DepartmentService(session=session).list_members(
        department_id=department_id,
        organization_id=access.organization_id,
    )

    return DepartmentMemberListResponse(
        members=[
            member_response(member)
            for member in members
        ]
    )


@router.post(
    "/{department_id}/members",
    response_model=DepartmentMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def assign_department_member(
    department_id: str,
    request: CreateDepartmentMemberRequest,
    organization_access: OrganizationAccess = Depends(get_organization_access),
    department_access: DepartmentAccess | None = Depends(
        get_department_access_optional
    ),
    session: AsyncSession = Depends(get_database_session_dependency),
) -> DepartmentMemberResponse:
    member = await DepartmentService(session=session).assign_member(
        department_id=department_id,
        organization_id=organization_access.organization_id,
        actor_user_id=organization_access.user_id,
        actor_organization_role=organization_access.role,
        actor_department_role=(
            department_access.role if department_access else None
        ),
        user_id=request.user_id,
        role=request.role.value,
    )

    return member_response(member)


@router.patch(
    "/{department_id}/members/{membership_id}/role",
    response_model=DepartmentMemberResponse,
)
async def change_department_member_role(
    department_id: str,
    membership_id: str,
    request: UpdateDepartmentMemberRoleRequest,
    organization_access: OrganizationAccess = Depends(get_organization_access),
    department_access: DepartmentAccess | None = Depends(
        get_department_access_optional
    ),
    session: AsyncSession = Depends(get_database_session_dependency),
) -> DepartmentMemberResponse:
    member = await DepartmentService(session=session).change_member_role(
        department_id=department_id,
        organization_id=organization_access.organization_id,
        actor_user_id=organization_access.user_id,
        actor_organization_role=organization_access.role,
        actor_department_role=(
            department_access.role if department_access else None
        ),
        membership_id=membership_id,
        role=request.role.value,
    )

    return member_response(member)


@router.delete(
    "/{department_id}/members/{membership_id}",
    response_model=DepartmentMemberResponse,
)
async def deactivate_department_member(
    department_id: str,
    membership_id: str,
    organization_access: OrganizationAccess = Depends(get_organization_access),
    department_access: DepartmentAccess | None = Depends(
        get_department_access_optional
    ),
    session: AsyncSession = Depends(get_database_session_dependency),
) -> DepartmentMemberResponse:
    member = await DepartmentService(session=session).deactivate_member(
        department_id=department_id,
        organization_id=organization_access.organization_id,
        actor_user_id=organization_access.user_id,
        actor_organization_role=organization_access.role,
        actor_department_role=(
            department_access.role if department_access else None
        ),
        membership_id=membership_id,
    )

    return member_response(member)


def department_response(
    department: DepartmentSummary,
) -> DepartmentResponse:
    return DepartmentResponse(
        id=department.id,
        name=department.name,
        slug=department.slug,
        is_active=department.is_active,
        created_at=department.created_at,
        updated_at=department.updated_at,
    )


def member_response(
    member: DepartmentMember,
) -> DepartmentMemberResponse:
    return DepartmentMemberResponse(
        membership_id=member.membership_id,
        user_id=member.user_id,
        email=member.email,
        full_name=member.full_name,
        role=member.role,
        is_active=member.is_active,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )