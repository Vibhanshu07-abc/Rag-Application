from backend.app.auth.roles import (
    DepartmentRole,
    OrganizationRole,
    can_manage_department_documents,
    can_manage_department_members,
    can_manage_departments,
    can_manage_members,
    can_read_documents,
    can_write_documents,
)


def test_organization_role_permissions() -> None:
    assert can_read_documents(OrganizationRole.OWNER)
    assert can_read_documents(OrganizationRole.ADMIN)
    assert can_read_documents(OrganizationRole.MEMBER)
    assert can_read_documents(OrganizationRole.VIEWER)

    assert can_write_documents(OrganizationRole.OWNER)
    assert can_write_documents(OrganizationRole.ADMIN)
    assert not can_write_documents(OrganizationRole.MEMBER)
    assert not can_write_documents(OrganizationRole.VIEWER)

    assert can_manage_members(OrganizationRole.OWNER)
    assert can_manage_members(OrganizationRole.ADMIN)
    assert not can_manage_members(OrganizationRole.MEMBER)
    assert not can_manage_members(OrganizationRole.VIEWER)

    assert can_manage_departments(OrganizationRole.OWNER)
    assert can_manage_departments(OrganizationRole.ADMIN)
    assert not can_manage_departments(OrganizationRole.MEMBER)
    assert not can_manage_departments(OrganizationRole.VIEWER)


def test_department_role_permissions() -> None:
    assert can_manage_department_members(DepartmentRole.DEPARTMENT_ADMIN)
    assert not can_manage_department_members(DepartmentRole.EMPLOYEE)
    assert not can_manage_department_members("invalid")

    assert can_manage_department_documents(DepartmentRole.DEPARTMENT_ADMIN)
    assert not can_manage_department_documents(DepartmentRole.EMPLOYEE)
    assert not can_manage_department_documents("invalid")
