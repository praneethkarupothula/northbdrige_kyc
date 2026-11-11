export type Role = "applicant" | "reviewer" | "admin";
export interface User { id: string; email: string; full_name: string; role: Role; email_verified: boolean; portal: "applicant" | "staff"; organization_ids: string[] }
export interface Status { key: string; tone: "success" | "danger" | "warning" | "info" | "neutral"; headline: string; detail: string }
export interface Extraction {
  document_type: string; fields: Record<string, string | null>; pay_period: string | null; gross_salary: string | null;
  net_salary: string | null; monthly_salary: string | null; currency: string | null; missing_fields: string[];
  quality_flags: string[]; extraction_method: string; ocr_confidence: number | null;
  provenance?: Record<string, { line: number; page: number; bbox: [number, number, number, number] }>;
  model_name?: string; llm_latency_ms?: number; entity_counts?: Record<string, number>;
}
export interface Doc {
  id: string; category: string; file_format: string; size_bytes: number; page_count: number; pages_processed: number[] | null;
  processing_status: string; current_stage: string | null; error_code: string | null; error_message: string | null;
  quality_flags: string[]; quality_suggestions: string[]; uploaded_at: string; purged: boolean; duplicate_of: string | null;
  extraction: Extraction | null; attention: string | null;
}
export interface Check { id: string; check_type: string; status: string; code: string; document_id: string | null; reference_document_id: string | null; detail: Record<string, unknown>; explanation: string }
export interface Version {
  id: string; version_number: number; is_current: boolean; state: string; processing_status: string; check_status: string | null;
  review_status: string; decision_source?: "staff" | "automatic"; pan_status: string; processing_attempt: number; row_version: number; submitted_at: string | null;
  processed_at: string | null; decided_at: string | null; applicant_message: string | null;
  requested_items: { category: string; instruction: string }[] | null; status: Status; documents: Doc[]; checks: Check[];
}
export interface Policy { pan_policy: "optional" | "required" | "not_requested"; max_salary_slips: number; required_salary_slips: number; accept_bank_statement: boolean; max_file_mb: number; retention_days_after_decision: number }
export interface HistoryItem { id: string; version_number: number; state: string; is_current: boolean; processing_status: string; check_status: string | null; review_status: string; submitted_at: string | null; decided_at: string | null }
export interface Application { id: string; reference: string; created_at: string; current: Version; history: HistoryItem[]; policy: Policy }
export interface ProgressEvent { id: number; version_id: string; document_id: string | null; attempt: number; stage: string; stage_label: string; status: string; message: string; created_at: string }
export interface Notification { id: string; kind: string; title: string; body: string; read: boolean; application_id: string | null; created_at: string }
