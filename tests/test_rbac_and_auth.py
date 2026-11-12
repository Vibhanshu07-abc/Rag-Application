from __future__ import annotations

import httpx
import pytest


async def _register_org(
    client: httpx.AsyncClient,
    *,
    email: str,
    org_name: str,
    full_name: str = "Org Owner",
    password: str = "StrongPassword123!",
) -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "full_name": full_name,
            "organization_name": org_name,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_rbac_hierarchy_departments_idor_and_inactive_lockout(
    api_client: httpx.AsyncClient,
    pdf_factory,
) -> None:
    # 1. Register Organization A (owner) and Organization B (owner)
    org_a = await _register_org(api_client, email="owner-a@example.com", org_name="Alpha Corp")
    org_b = await _register_org(api_client, email="owner-b@example.com", org_name="Beta Corp")
    owner_a_headers = _headers(org_a["access_token"])
    owner_b_headers = _headers(org_b["access_token"])

    # 2. Org Admin creates Dept Admin candidate and Employee inside Alpha Corp
    dept_admin_res = await api_client.post(
        "/api/v1/organizations/current/members",
        headers=owner_a_headers,
        json={
            "email": "dept-admin@example.com",
            "password": "StrongPassword123!",
            "full_name": "Engineering Dept Admin",
            "role": "member",
        },
    )
    assert dept_admin_res.status_code == 201, dept_admin_res.text
    dept_admin_user_id = dept_admin_res.json()["user_id"]

    emp_res = await api_client.post(
        "/api/v1/organizations/current/members",
        headers=owner_a_headers,
        json={
            "email": "employee@example.com",
            "password": "StrongPassword123!",
            "full_name": "Plain Employee",
            "role": "member",
        },
    )
    assert emp_res.status_code == 201, emp_res.text
    emp_membership_id = emp_res.json()["membership_id"]
    emp_user_id = emp_res.json()["user_id"]

    # 3. Org Admin creates two departments: Engineering and Finance
    eng_dept = (
        await api_client.post(
            "/api/v1/organizations/current/departments",
            headers=owner_a_headers,
            json={"name": "Engineering"},
        )
    )
    assert eng_dept.status_code == 201, eng_dept.text
    eng_id = eng_dept.json()["id"]

    fin_dept = (
        await api_client.post(
            "/api/v1/organizations/current/departments",
            headers=owner_a_headers,
            json={"name": "Finance"},
        )
    )
    assert fin_dept.status_code == 201, fin_dept.text
    fin_id = fin_dept.json()["id"]

    # Assign dept-admin as DEPARTMENT_ADMIN of Engineering, employee as EMPLOYEE of Engineering
    assign_admin = await api_client.post(
        f"/api/v1/organizations/current/departments/{eng_id}/members",
        headers=owner_a_headers,
        json={"user_id": dept_admin_user_id, "role": "department_admin"},
    )
    assert assign_admin.status_code == 201, assign_admin.text

    assign_emp = await api_client.post(
        f"/api/v1/organizations/current/departments/{eng_id}/members",
        headers=owner_a_headers,
        json={"user_id": emp_user_id, "role": "employee"},
    )
    assert assign_emp.status_code == 201, assign_emp.text

    # Login as Dept Admin & Employee and verify /auth/me includes department_memberships
    da_login = (
        await api_client.post(
            "/api/v1/auth/login",
            json={"email": "dept-admin@example.com", "password": "StrongPassword123!"},
        )
    ).json()
    da_headers = _headers(da_login["access_token"])

    emp_login = (
        await api_client.post(
            "/api/v1/auth/login",
            json={"email": "employee@example.com", "password": "StrongPassword123!"},
        )
    ).json()
    emp_headers = _headers(emp_login["access_token"])

    me_da = await api_client.get("/api/v1/auth/me", headers=da_headers)
    assert me_da.status_code == 200
    assert me_da.json()["department_memberships"] == [
        {"department_id": eng_id, "department_name": "Engineering", "role": "department_admin"}
    ]

    # 4. Enforce org/dept role boundaries
    # Neither Employee nor Dept Admin can manage org members or create departments
    assert (await api_client.get("/api/v1/organizations/current/members", headers=emp_headers)).status_code == 403
    assert (await api_client.get("/api/v1/organizations/current/members", headers=da_headers)).status_code == 403
    assert (
        await api_client.post(
            "/api/v1/organizations/current/departments",
            headers=da_headers,
            json={"name": "Rogue Dept"},
        )
    ).status_code == 403

    # Dept list is scoped: Org Admin sees Engineering+Finance; Dept Admin/Employee see only Engineering
    assert len((await api_client.get("/api/v1/organizations/current/departments", headers=owner_a_headers)).json()["departments"]) == 2
    assert [
        d["id"]
        for d in (await api_client.get("/api/v1/organizations/current/departments", headers=emp_headers)).json()["departments"]
    ] == [eng_id]

    # Plain Employee cannot inspect or manage department members; Dept Admin can manage Engineering but NOT Finance
    assert (
        await api_client.get(f"/api/v1/organizations/current/departments/{eng_id}/members", headers=emp_headers)
    ).status_code == 403
    assert (
        await api_client.get(f"/api/v1/organizations/current/departments/{eng_id}/members", headers=da_headers)
    ).status_code == 200
    assert (
        await api_client.get(f"/api/v1/organizations/current/departments/{fin_id}/members", headers=da_headers)
    ).status_code == 403

    # Upload permissions: Employee blocked on org and dept uploads; Dept Admin blocked on org-wide & Finance, allowed on Engineering
    pdf_bytes = pdf_factory("Engineering onboarding handbook.")
    assert (
        await api_client.post(
            "/api/v1/documents",
            headers=emp_headers,
            data={"knowledge_base_id": "default", "visibility": "organization"},
            files={"file": ("emp-org.pdf", pdf_bytes, "application/pdf")},
        )
    ).status_code == 403
    assert (
        await api_client.post(
            "/api/v1/documents",
            headers=emp_headers,
            data={"knowledge_base_id": "default", "visibility": "department", "department_id": eng_id},
            files={"file": ("emp-dept.pdf", pdf_bytes, "application/pdf")},
        )
    ).status_code == 403
    assert (
        await api_client.post(
            "/api/v1/documents",
            headers=da_headers,
            data={"knowledge_base_id": "default", "visibility": "organization"},
            files={"file": ("da-org.pdf", pdf_bytes, "application/pdf")},
        )
    ).status_code == 403
    assert (
        await api_client.post(
            "/api/v1/documents",
            headers=da_headers,
            data={"knowledge_base_id": "default", "visibility": "department", "department_id": fin_id},
            files={"file": ("da-fin.pdf", pdf_bytes, "application/pdf")},
        )
    ).status_code == 403

    ok_da_upload = await api_client.post(
        "/api/v1/documents",
        headers=da_headers,
        data={"knowledge_base_id": "default", "visibility": "department", "department_id": eng_id},
        files={"file": ("da-eng.pdf", pdf_bytes, "application/pdf")},
    )
    assert ok_da_upload.status_code == 201, ok_da_upload.text
    eng_job_id = ok_da_upload.json()["job_id"]

    # 5. Cross-organization IDOR protection
    assert (
        await api_client.get(f"/api/v1/organizations/current/departments/{eng_id}/members", headers=owner_b_headers)
    ).status_code in (403, 404)
    assert (await api_client.get(f"/api/v1/documents/jobs/{eng_job_id}", headers=owner_b_headers)).status_code in (403, 404)
    # Employee in Alpha Corp also cannot read/retry Dept Admin's job
    assert (await api_client.get(f"/api/v1/documents/jobs/{eng_job_id}", headers=emp_headers)).status_code in (403, 404)

    # 6. Inactive membership lockout even with previously issued valid access token
    deactivate = await api_client.delete(
        f"/api/v1/organizations/current/members/{emp_membership_id}",
        headers=owner_a_headers,
    )
    assert deactivate.status_code == 200
    assert (await api_client.get("/api/v1/documents", headers=emp_headers)).status_code in (401, 403)
