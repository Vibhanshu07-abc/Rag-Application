from enum import StrEnum


class OrganizationRole(StrEnum):
    """Roles available inside an organization."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class DepartmentRole(StrEnum):
    """Roles available inside one organization department."""

    DEPARTMENT_ADMIN = "department_admin"
    EMPLOYEE = "employee"


# Roles that can read organization documents.
DOCUMENT_READ_ROLES = frozenset(
    {
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
        OrganizationRole.MEMBER,
        OrganizationRole.VIEWER,
    }
)


# Organization-wide document write permissions.
# Department Admin permissions are checked separately
# against the department to which they belong.
DOCUMENT_WRITE_ROLES = frozenset(
    {
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
    }
)


# Roles that can manage organization members and departments.
MEMBER_MANAGEMENT_ROLES = frozenset(
    {
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
    }
)


def can_read_documents(role: str) -> bool:
    """Return True when the organization role can read documents."""

    try:
        normalized_role = OrganizationRole(role)
    except (ValueError, TypeError):
        return False

    return normalized_role in DOCUMENT_READ_ROLES


def can_write_documents(role: str) -> bool:
    """Return True when the organization role can write organization-wide documents."""

    try:
        normalized_role = OrganizationRole(role)
    except (ValueError, TypeError):
        return False

    return normalized_role in DOCUMENT_WRITE_ROLES


def can_manage_members(role: str) -> bool:
    """Return True when the organization role can manage organization members."""

    try:
        normalized_role = OrganizationRole(role)
    except (ValueError, TypeError):
        return False

    return normalized_role in MEMBER_MANAGEMENT_ROLES


def can_manage_departments(role: str) -> bool:
    """Return True when the organization role can manage all departments."""

    return can_manage_members(role)


def can_manage_department_members(role: str) -> bool:
    """Return True when the department role can manage members of its own department."""

    try:
        normalized_role = DepartmentRole(role)
    except (ValueError, TypeError):
        return False

    return normalized_role == DepartmentRole.DEPARTMENT_ADMIN


def can_manage_department_documents(role: str) -> bool:
    """Return True when the department role can manage documents in its own department."""

    try:
        normalized_role = DepartmentRole(role)
    except (ValueError, TypeError):
        return False

    return normalized_role == DepartmentRole.DEPARTMENT_ADMIN