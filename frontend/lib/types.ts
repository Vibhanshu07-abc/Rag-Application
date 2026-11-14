export type OrganizationRole = "owner" | "admin" | "member" | "viewer";
export type DepartmentRole = "department_admin" | "employee";

export interface DepartmentMembershipSummary {
  department_id: string;
  department_name: string;
  role: DepartmentRole;
}

export interface UserProfile {
  id: string;
  organization_id: string;
  email: string;
  full_name: string;
  role: OrganizationRole;
  is_active: boolean;
  department_memberships: DepartmentMembershipSummary[];
}

export interface TokenBundle {
  accessToken: string;
  refreshToken: string;
  tokenType: string;
  expiresIn?: number;
  user?: UserProfile;
}

export interface DocumentRecord {
  id: string;
  knowledge_base_id: string;
  filename: string;
  status: string;
  file_size_bytes?: number | null;
  total_pages?: number | null;
  chunk_count?: number | null;
  embedding_provider?: string | null;
  embedding_model?: string | null;
  embedding_dimension?: number | null;
  vector_store_provider?: string | null;
  error_message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  logical_document_id?: string;
  version_number?: number;
  is_current?: boolean;
  is_published?: boolean;
  published_at?: string | null;
  published_by_user_id?: string | null;
  archived_at?: string | null;
  archived_by_user_id?: string | null;
  visibility?: "organization" | "department";
  department_id?: string | null;
}

export interface Department { id: string; name: string; slug: string; is_active: boolean; }

export interface DepartmentMember {
  id: string;
  department_id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: DepartmentRole;
  is_active: boolean;
}
export interface DocumentJob {
  id: string; status: string; filename: string; document_id: string | null;
  error_message: string | null; attempt_count: number; max_attempts: number;
  next_attempt_at: string | null; created_at: string; started_at: string | null; completed_at: string | null;
}

export interface DocumentMonitoring { job_counts: Record<string, number>; workers: Record<string, string>[]; }

export interface RagSource {
  citation_id: string;
  document_id?: string | null;
  chunk_id?: string | null;
  filename?: string | null;
  page_number?: number | null;
}

export interface RagResponse {
  query: string;
  answer: string;
  grounded: boolean;
  citations: string[];
  sources: RagSource[];
  retrieved_chunk_count: number;
  cached?: boolean;
}

export type ChatMessageRole = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: ChatMessageRole;
  content: string;
  createdAt: string;
  grounded?: boolean;
  sources?: RagSource[];
  requestId?: string;
  failed?: boolean;
  cached?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  knowledgeBaseId: string;
  documentId: string | null;
  topK: number;
  messages: ChatMessage[];
}

export interface OrganizationInfo {
  id: string;
  name: string;
  slug?: string;
  membership_id?: string;
  role: OrganizationRole;
}

export interface OrganizationMember {
  membership_id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: OrganizationRole;
  is_active: boolean;
  created_at?: string;
}
