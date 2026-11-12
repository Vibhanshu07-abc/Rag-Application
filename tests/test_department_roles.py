from backend.app.auth.roles import (
    DepartmentRole,
    OrganizationRole,
    can_manage_departments,
    can_manage_department_members,
)


def main() -> None:
    assert can_manage_departments(OrganizationRole.OWNER) is True
    assert can_manage_departments(OrganizationRole.ADMIN) is True
    assert can_manage_departments(OrganizationRole.MEMBER) is False
    assert can_manage_departments(OrganizationRole.VIEWER) is False

    assert (
        can_manage_department_members(
            DepartmentRole.DEPARTMENT_ADMIN
        )
        is True
    )
    assert can_manage_department_members(DepartmentRole.EMPLOYEE) is False

    print("Department role authorization helpers passed.")


if __name__ == "__main__":
    main()
