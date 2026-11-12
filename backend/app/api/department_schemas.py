from datetime import datetime

from pydantic import BaseModel, Field

from backend.app.auth.roles import DepartmentRole


class DepartmentResponse(BaseModel):
    id: str
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DepartmentListResponse(BaseModel):
    departments: list[DepartmentResponse]


class CreateDepartmentRequest(BaseModel):
    name: str = Field(
        ...,
        min_length=2,
        max_length=255,
    )


class DepartmentMemberResponse(BaseModel):
    membership_id: str
    user_id: str
    email: str
    full_name: str | None
    role: DepartmentRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DepartmentMemberListResponse(BaseModel):
    members: list[DepartmentMemberResponse]


class CreateDepartmentMemberRequest(BaseModel):
    user_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
    )
    role: DepartmentRole = DepartmentRole.EMPLOYEE


class UpdateDepartmentMemberRoleRequest(BaseModel):
    role: DepartmentRole