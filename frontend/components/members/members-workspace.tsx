"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Building2,
  FolderKanban,
  LoaderCircle,
  Mail,
  Plus,
  Search,
  Shield,
  Trash2,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import { useAuth } from "@/components/providers/auth-provider";
import { useToast } from "@/components/providers/toast-provider";
import {
  ApiError,
  normalizeDepartmentMembers,
  normalizeDepartments,
  normalizeMembers,
  normalizeOrganization,
} from "@/lib/api";
import { formatDate, initials, isOrgAdminRole } from "@/lib/format";
import type {
  Department,
  DepartmentMember,
  DepartmentRole,
  OrganizationInfo,
  OrganizationMember,
  OrganizationRole,
} from "@/lib/types";

export function MembersWorkspace() {
  const { apiFetch, user } = useAuth();
  const { showToast } = useToast();

  const [organization, setOrganization] = useState<OrganizationInfo | null>(null);
  const [members, setMembers] = useState<OrganizationMember[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [selectedDepartmentId, setSelectedDepartmentId] = useState("");
  const [departmentMembers, setDepartmentMembers] = useState<DepartmentMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [deptMembersLoading, setDeptMembersLoading] = useState(false);

  const [query, setQuery] = useState("");
  const [createMemberOpen, setCreateMemberOpen] = useState(false);
  const [creatingMember, setCreatingMember] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [newFullName, setNewFullName] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState<OrganizationRole>("member");

  const [newDepartmentName, setNewDepartmentName] = useState("");
  const [creatingDepartment, setCreatingDepartment] = useState(false);

  const [assignUserId, setAssignUserId] = useState("");
  const [assignDepartmentRole, setAssignDepartmentRole] = useState<DepartmentRole>("employee");
  const [assigningMember, setAssigningMember] = useState(false);

  const isOrgAdmin = isOrgAdminRole(user?.role);
  const managedDepartmentIds = useMemo(
    () =>
      new Set(
        (user?.department_memberships ?? [])
          .filter((membership) => membership.role === "department_admin")
          .map((membership) => membership.department_id),
      ),
    [user?.department_memberships],
  );
  const canManageSelectedDepartment =
    Boolean(selectedDepartmentId) && (isOrgAdmin || managedDepartmentIds.has(selectedDepartmentId));

  async function loadTeamData() {
    setLoading(true);
    try {
      const [orgPayload, deptPayload, membersPayload] = await Promise.all([
        apiFetch<unknown>("/organizations/current"),
        apiFetch<unknown>("/organizations/current/departments").catch(() => ({ departments: [] })),
        isOrgAdmin ? apiFetch<unknown>("/organizations/current/members") : Promise.resolve(null),
      ]);
      setOrganization(normalizeOrganization(orgPayload));
      const deptList = normalizeDepartments(deptPayload);
      setDepartments(deptList);
      setSelectedDepartmentId((current) =>
        current && deptList.some((item) => item.id === current) ? current : deptList[0]?.id || "",
      );
      if (membersPayload) {
        setMembers(normalizeMembers(membersPayload));
      } else {
        setMembers([]);
      }
    } catch (error) {
      showToast({
        tone: "error",
        title: "Unable to load team workspace",
        description: error instanceof ApiError ? error.message : "Please try again.",
      });
    } finally {
      setLoading(false);
    }
  }

  async function loadDepartmentMembers(deptId: string) {
    if (!deptId || (!isOrgAdmin && !managedDepartmentIds.has(deptId))) {
      setDepartmentMembers([]);
      return;
    }
    setDeptMembersLoading(true);
    try {
      const payload = await apiFetch<unknown>(`/organizations/current/departments/${encodeURIComponent(deptId)}/members`);
      setDepartmentMembers(normalizeDepartmentMembers(payload, deptId));
    } catch (error) {
      setDepartmentMembers([]);
      showToast({
        tone: "error",
        title: "Unable to load department members",
        description: error instanceof ApiError ? error.message : "Please try again.",
      });
    } finally {
      setDeptMembersLoading(false);
    }
  }

  useEffect(() => {
    void loadTeamData();
  }, [isOrgAdmin]);

  useEffect(() => {
    void loadDepartmentMembers(selectedDepartmentId);
  }, [selectedDepartmentId, isOrgAdmin, managedDepartmentIds]);

  const filteredMembers = useMemo(
    () =>
      members.filter((member) =>
        `${member.full_name} ${member.email} ${member.role}`.toLowerCase().includes(query.toLowerCase()),
      ),
    [members, query],
  );

  const activeOrgMembers = useMemo(() => members.filter((member) => member.is_active), [members]);

  async function createOrganizationMember(event: FormEvent) {
    event.preventDefault();
    if (!newEmail.trim() || newPassword.length < 12) return;
    setCreatingMember(true);
    try {
      await apiFetch<unknown>("/organizations/current/members", {
        method: "POST",
        body: JSON.stringify({
          email: newEmail.trim(),
          password: newPassword,
          full_name: newFullName.trim() || null,
          role: newRole,
        }),
      });
      setCreateMemberOpen(false);
      setNewEmail("");
      setNewFullName("");
      setNewPassword("");
      setNewRole("member");
      await loadTeamData();
      showToast({ tone: "success", title: "Organization member added" });
    } catch (error) {
      showToast({
        tone: "error",
        title: "Could not add member",
        description: error instanceof ApiError ? error.message : "Please verify inputs and try again.",
      });
    } finally {
      setCreatingMember(false);
    }
  }

  async function changeOrgRole(member: OrganizationMember, role: OrganizationRole) {
    try {
      await apiFetch<unknown>(`/organizations/current/members/${encodeURIComponent(member.membership_id)}/role`, {
        method: "PATCH",
        body: JSON.stringify({ role }),
      });
      setMembers((items) => items.map((item) => (item.membership_id === member.membership_id ? { ...item, role } : item)));
      showToast({ tone: "success", title: "Role updated" });
    } catch (error) {
      showToast({
        tone: "error",
        title: "Role update failed",
        description: error instanceof ApiError ? error.message : "Please try again.",
      });
    }
  }

  async function deactivateOrgMember(member: OrganizationMember) {
    if (!confirm(`Deactivate ${member.full_name}?`)) return;
    try {
      await apiFetch<unknown>(`/organizations/current/members/${encodeURIComponent(member.membership_id)}`, {
        method: "DELETE",
      });
      setMembers((items) =>
        items.map((item) => (item.membership_id === member.membership_id ? { ...item, is_active: false } : item)),
      );
      showToast({ tone: "success", title: "Member deactivated" });
    } catch (error) {
      showToast({
        tone: "error",
        title: "Deactivation failed",
        description: error instanceof ApiError ? error.message : "Please try again.",
      });
    }
  }

  async function createDepartment(event: FormEvent) {
    event.preventDefault();
    if (!newDepartmentName.trim()) return;
    setCreatingDepartment(true);
    try {
      const created = await apiFetch<{ id?: string }>("/organizations/current/departments", {
        method: "POST",
        body: JSON.stringify({ name: newDepartmentName.trim() }),
      });
      setNewDepartmentName("");
      await loadTeamData();
      if (created?.id) setSelectedDepartmentId(created.id);
      showToast({ tone: "success", title: "Department created" });
    } catch (error) {
      showToast({
        tone: "error",
        title: "Department creation failed",
        description: error instanceof ApiError ? error.message : "Please try again.",
      });
    } finally {
      setCreatingDepartment(false);
    }
  }

  async function assignDepartmentMember(event: FormEvent) {
    event.preventDefault();
    if (!selectedDepartmentId || !assignUserId.trim()) return;
    setAssigningMember(true);
    try {
      await apiFetch<unknown>(
        `/organizations/current/departments/${encodeURIComponent(selectedDepartmentId)}/members`,
        {
          method: "POST",
          body: JSON.stringify({
            user_id: assignUserId.trim(),
            role: assignDepartmentRole,
          }),
        },
      );
      setAssignUserId("");
      await loadDepartmentMembers(selectedDepartmentId);
      showToast({ tone: "success", title: "Department member assigned" });
    } catch (error) {
      showToast({
        tone: "error",
        title: "Assignment failed",
        description: error instanceof ApiError ? error.message : "Please verify the user ID and try again.",
      });
    } finally {
      setAssigningMember(false);
    }
  }

  async function changeDepartmentMemberRole(member: DepartmentMember, role: DepartmentRole) {
    if (!selectedDepartmentId) return;
    try {
      await apiFetch<unknown>(
        `/organizations/current/departments/${encodeURIComponent(selectedDepartmentId)}/members/${encodeURIComponent(member.id)}/role`,
        {
          method: "PATCH",
          body: JSON.stringify({ role }),
        },
      );
      setDepartmentMembers((items) => items.map((item) => (item.id === member.id ? { ...item, role } : item)));
      showToast({ tone: "success", title: "Department role updated" });
    } catch (error) {
      showToast({
        tone: "error",
        title: "Role update failed",
        description: error instanceof ApiError ? error.message : "Please try again.",
      });
    }
  }

  async function deactivateDepartmentMember(member: DepartmentMember) {
    if (!selectedDepartmentId || !confirm(`Remove ${member.full_name} from this department?`)) return;
    try {
      await apiFetch<unknown>(
        `/organizations/current/departments/${encodeURIComponent(selectedDepartmentId)}/members/${encodeURIComponent(member.id)}`,
        { method: "DELETE" },
      );
      setDepartmentMembers((items) =>
        items.map((item) => (item.id === member.id ? { ...item, is_active: false } : item)),
      );
      showToast({ tone: "success", title: "Department member removed" });
    } catch (error) {
      showToast({
        tone: "error",
        title: "Removal failed",
        description: error instanceof ApiError ? error.message : "Please try again.",
      });
    }
  }

  return (
    <section className="page-workspace">
      <header className="page-header">
        <div>
          <p className="eyebrow dark">Workspace administration</p>
          <h1>Team &amp; Departments</h1>
          <p>
            {isOrgAdmin
              ? "Manage organization members, departments, and department roles."
              : managedDepartmentIds.size > 0
                ? "Manage members for departments where you serve as department admin."
                : "Inspect your organization and department access scope."}
          </p>
        </div>
        {isOrgAdmin ? (
          <button className="primary-button" onClick={() => setCreateMemberOpen(true)}>
            <UserPlus size={17} /> Add member
          </button>
        ) : null}
      </header>

      <article className="organization-card">
        <div className="organization-icon">
          <Building2 size={20} />
        </div>
        <div>
          <span>Active organization</span>
          <strong>{organization?.name || "Organization"}</strong>
          <small>{organization?.id || user?.organization_id}</small>
        </div>
        <span className="organization-role">
          <Shield size={13} /> {user?.role || "member"}
        </span>
      </article>

      {loading ? (
        <div className="table-loader">
          <LoaderCircle size={25} className="spin" />
          <p>Loading team directory…</p>
        </div>
      ) : (
        <>
          {isOrgAdmin ? (
            <div className="section-block">
              <div className="section-heading">
                <div>
                  <h2>Organization members</h2>
                  <p>Assign organization-wide roles and manage active accounts.</p>
                </div>
              </div>

              <div className="toolbar-card team-toolbar">
                <label className="search-field">
                  <Search size={17} />
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Search organization members by name, email, or role"
                  />
                </label>
                <div className="stat-inline">
                  <Users size={16} />
                  <strong>{members.filter((item) => item.is_active).length}</strong> active members
                </div>
              </div>

              <div className="member-grid">
                {filteredMembers.map((member) => (
                  <article className={`member-card ${member.is_active ? "" : "inactive"}`} key={member.membership_id}>
                    <div className="member-main">
                      <span className="avatar large-avatar">{initials(member.full_name)}</span>
                      <div>
                        <div className="member-name-row">
                          <h3>{member.full_name}</h3>
                          <span>{member.role}</span>
                          {!member.is_active ? <span className="inactive-pill">inactive</span> : null}
                        </div>
                        <p>
                          <Mail size={13} /> {member.email}
                        </p>
                        <small>
                          User ID: {member.user_id} · Joined {formatDate(member.created_at)}
                        </small>
                      </div>
                    </div>

                    {member.is_active ? (
                      <div className="member-actions">
                        <label>
                          Organization role
                          <select
                            value={member.role}
                            disabled={member.role === "owner" || member.user_id === user?.id}
                            onChange={(event) => void changeOrgRole(member, event.target.value as OrganizationRole)}
                          >
                            <option value="owner" disabled>
                              owner
                            </option>
                            <option value="admin">admin</option>
                            <option value="member">member</option>
                            <option value="viewer">viewer</option>
                          </select>
                        </label>
                        {member.role !== "owner" && member.user_id !== user?.id ? (
                          <button className="danger-text-button" onClick={() => void deactivateOrgMember(member)}>
                            <Trash2 size={14} /> Deactivate
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                  </article>
                ))}
              </div>
            </div>
          ) : null}

          <div className="section-block">
            <div className="section-heading">
              <div>
                <h2>Departments &amp; department memberships</h2>
                <p>
                  {isOrgAdmin
                    ? "Create departments and assign organization members as department admins or employees."
                    : managedDepartmentIds.size > 0
                      ? "Select a managed department to assign or update department members."
                      : "Your active department assignments."}
                </p>
              </div>
            </div>

            {isOrgAdmin ? (
              <form className="inline-form-row" onSubmit={(event) => void createDepartment(event)}>
                <input
                  className="text-input"
                  value={newDepartmentName}
                  onChange={(event) => setNewDepartmentName(event.target.value)}
                  placeholder="New department name (e.g. Engineering)"
                  maxLength={120}
                />
                <button className="primary-button" type="submit" disabled={!newDepartmentName.trim() || creatingDepartment}>
                  {creatingDepartment ? <LoaderCircle size={16} className="spin" /> : <Plus size={16} />} Create department
                </button>
              </form>
            ) : null}

            {departments.length ? (
              <>
                <div className="toolbar-card team-toolbar">
                  <label className="search-field">
                    <FolderKanban size={17} />
                    <select
                      className="text-input"
                      value={selectedDepartmentId}
                      onChange={(event) => setSelectedDepartmentId(event.target.value)}
                    >
                      {departments.map((dept) => {
                        const myMembership = user?.department_memberships?.find(
                          (item) => item.department_id === dept.id,
                        );
                        return (
                          <option key={dept.id} value={dept.id}>
                            {dept.name}
                            {myMembership ? ` (${myMembership.role})` : ""}
                          </option>
                        );
                      })}
                    </select>
                  </label>
                  <div className="stat-inline">
                    <FolderKanban size={16} />
                    <strong>{departments.length}</strong> accessible department(s)
                  </div>
                </div>

                {canManageSelectedDepartment ? (
                  <>
                    <form className="inline-form-row" onSubmit={(event) => void assignDepartmentMember(event)}>
                      {isOrgAdmin ? (
                        <select
                          className="text-input"
                          value={assignUserId}
                          onChange={(event) => setAssignUserId(event.target.value)}
                        >
                          <option value="">Select organization member…</option>
                          {activeOrgMembers.map((member) => (
                            <option key={member.user_id} value={member.user_id}>
                              {member.full_name} ({member.email})
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          className="text-input"
                          value={assignUserId}
                          onChange={(event) => setAssignUserId(event.target.value)}
                          placeholder="Organization member User ID"
                        />
                      )}
                      <select
                        className="text-input"
                        value={assignDepartmentRole}
                        onChange={(event) => setAssignDepartmentRole(event.target.value as DepartmentRole)}
                      >
                        <option value="employee">employee</option>
                        <option value="department_admin">department_admin</option>
                      </select>
                      <button
                        className="secondary-button"
                        type="submit"
                        disabled={!assignUserId.trim() || assigningMember}
                      >
                        {assigningMember ? <LoaderCircle size={16} className="spin" /> : <UserPlus size={16} />} Assign member
                      </button>
                    </form>

                    {deptMembersLoading ? (
                      <div className="table-loader">
                        <LoaderCircle size={22} className="spin" />
                        <p>Loading department members…</p>
                      </div>
                    ) : departmentMembers.length ? (
                      <div className="member-grid">
                        {departmentMembers.map((member) => (
                          <article
                            className={`member-card ${member.is_active ? "" : "inactive"}`}
                            key={member.id}
                          >
                            <div className="member-main">
                              <span className="avatar large-avatar">{initials(member.full_name)}</span>
                              <div>
                                <div className="member-name-row">
                                  <h3>{member.full_name}</h3>
                                  <span>{member.role}</span>
                                  {!member.is_active ? <span className="inactive-pill">inactive</span> : null}
                                </div>
                                <p>
                                  <Mail size={13} /> {member.email}
                                </p>
                                <small>User ID: {member.user_id}</small>
                              </div>
                            </div>
                            {member.is_active ? (
                              <div className="member-actions">
                                <label>
                                  Department role
                                  <select
                                    value={member.role}
                                    onChange={(event) =>
                                      void changeDepartmentMemberRole(member, event.target.value as DepartmentRole)
                                    }
                                  >
                                    <option value="employee">employee</option>
                                    <option value="department_admin">department_admin</option>
                                  </select>
                                </label>
                                <button
                                  className="danger-text-button"
                                  onClick={() => void deactivateDepartmentMember(member)}
                                >
                                  <Trash2 size={14} /> Remove
                                </button>
                              </div>
                            ) : null}
                          </article>
                        ))}
                      </div>
                    ) : (
                      <div className="empty-state-card">
                        <Users size={30} />
                        <h2>No department members</h2>
                        <p>Assign an organization user to this department above.</p>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="member-grid">
                    {(user?.department_memberships ?? []).map((membership) => (
                      <article className="member-card" key={membership.department_id}>
                        <div className="member-main">
                          <span className="avatar large-avatar">{initials(membership.department_name)}</span>
                          <div>
                            <div className="member-name-row">
                              <h3>{membership.department_name}</h3>
                              <span>{membership.role}</span>
                            </div>
                            <small>Department ID: {membership.department_id}</small>
                          </div>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div className="empty-state-card">
                <FolderKanban size={30} />
                <h2>No departments available</h2>
                <p>
                  {isOrgAdmin
                    ? "Create a department to isolate department-specific documents."
                    : "You have not been assigned to any department yet."}
                </p>
              </div>
            )}
          </div>
        </>
      )}

      {createMemberOpen ? (
        <div className="modal-layer">
          <button
            className="modal-backdrop"
            onClick={() => !creatingMember && setCreateMemberOpen(false)}
            aria-label="Close dialog"
          />
          <div className="modal-card" role="dialog" aria-modal="true">
            <div className="modal-header">
              <div>
                <h2>Add organization member</h2>
                <p>Create a user account inside your active organization.</p>
              </div>
              <button className="icon-button" onClick={() => setCreateMemberOpen(false)} disabled={creatingMember}>
                <X size={19} />
              </button>
            </div>
            <form onSubmit={(event) => void createOrganizationMember(event)}>
              <div className="modal-body form-stack">
                <label className="field-label">
                  Full name
                  <input
                    className="text-input"
                    value={newFullName}
                    onChange={(event) => setNewFullName(event.target.value)}
                    placeholder="Jane Doe"
                    maxLength={255}
                  />
                </label>
                <label className="field-label">
                  Email
                  <input
                    className="text-input"
                    type="email"
                    required
                    value={newEmail}
                    onChange={(event) => setNewEmail(event.target.value)}
                    placeholder="jane@company.com"
                  />
                </label>
                <label className="field-label">
                  Temporary password (min 12 characters)
                  <input
                    className="text-input"
                    type="password"
                    required
                    minLength={12}
                    maxLength={128}
                    value={newPassword}
                    onChange={(event) => setNewPassword(event.target.value)}
                  />
                </label>
                <label className="field-label">
                  Organization role
                  <select
                    className="text-input"
                    value={newRole}
                    onChange={(event) => setNewRole(event.target.value as OrganizationRole)}
                  >
                    <option value="member">member</option>
                    <option value="viewer">viewer</option>
                    <option value="admin">admin</option>
                  </select>
                </label>
              </div>
              <div className="modal-footer">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => setCreateMemberOpen(false)}
                  disabled={creatingMember}
                >
                  Cancel
                </button>
                <button
                  className="primary-button"
                  type="submit"
                  disabled={!newEmail.trim() || newPassword.length < 12 || creatingMember}
                >
                  {creatingMember ? (
                    <>
                      <LoaderCircle size={17} className="spin" /> Creating…
                    </>
                  ) : (
                    <>
                      <UserPlus size={17} /> Create member
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </section>
  );
}
