export type UserRole = 'doctor' | 'admin';
export type SessionStatus = 'active' | 'findings_approved' | 'final';
export type StudyStatus = 'new' | 'in_progress' | 'final';
export type View = 'PA' | 'AP' | 'LAT';
export type BoxCoordinates = [number, number, number, number];
export type ReviewAction = 'pending' | 'accepted' | 'edited' | 'rejected' | 'added';
export type ProviderMode = 'mock' | 'real';

export interface UserDTO {
  id: number;
  name: string;
  email: string;
  role: UserRole;
  is_active: boolean;
}

export interface AuthUser extends UserDTO {
  csrf_token: string;
  expires_at: string;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface PatientIdentity {
  id: number;
  code: string;
  name: string;
  birth_year: number | null;
  sex: string | null;
}

export interface PatientSummary extends PatientIdentity {
  study_count: number;
}

export interface StudyDTO {
  id: number;
  patient_id: number;
  study_date: string;
  indication: string;
  image_count: number;
  status: StudyStatus;
  own_session_id: number | null;
  own_session_status: SessionStatus | null;
}

export interface ImageDTO {
  id: number;
  study_id: number;
  view: View;
  width: number;
  height: number;
  sha256: string;
  media_type: 'image/png' | 'image/jpeg';
  content_url: string;
}

export interface StudyDetail extends StudyDTO {
  images: ImageDTO[];
}

export interface BoxDTO {
  id: number;
  image_id: number;
  bbox_xyxy: BoxCoordinates;
  label: string | null;
  note: string | null;
  source: 'ai' | 'doctor';
  review_action: ReviewAction;
  revision: number;
  deleted_at: string | null;
  ai_request_id: number | null;
}

export interface BoxSnapshot {
  box_id: number;
  revision: number;
  image_id: number;
  bbox_xyxy: BoxCoordinates;
}

export interface BoxCreate {
  image_id: number;
  bbox_xyxy: BoxCoordinates;
  label?: string | null;
  note?: string | null;
}

export type BoxPatch = Partial<Pick<BoxDTO, 'bbox_xyxy' | 'label' | 'note' | 'review_action'>>;

export interface FindingItem {
  id: string;
  text: string;
  location: string | null;
  image_id: number;
  source: 'ai' | 'doctor';
  action: ReviewAction;
  box_ids: number[];
}

export interface FindingsEdit {
  items: FindingItem[];
  no_abnormality_confirmed: boolean;
}

export interface FindingsDTO extends FindingsEdit {
  id: number;
  version: number;
  status: 'ai_proposed' | 'in_review' | 'approved';
  ai_items: FindingItem[];
  annotation_snapshot: BoxSnapshot[] | null;
  approved_by: number | null;
  approved_at: string | null;
}

export interface ReportNarrative {
  comparison: string;
  findings: string;
  impression: string;
  recommendation: string;
}

export interface ReportContent extends ReportNarrative {
  patient: PatientIdentity;
  study: {
    id: number;
    study_date: string;
    indication: string;
    image_ids: number[];
    analyzed_image_id: number;
  };
  source_item_ids: string[];
}

export interface ReportDTO {
  id: number;
  findings_version: number;
  status: 'draft' | 'stale' | 'final';
  content: ReportContent;
  approved_by: number | null;
  approved_at: string | null;
  updated_at: string;
}

export interface MessageDTO {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  image_id: number;
  box_id: number | null;
  box_snapshot: BoxSnapshot | null;
  model_version: string | null;
  latency_ms: number | null;
  created_at: string;
}

export type AICommand =
  | { operation: 'analyze'; image_id: number; retry_of?: number }
  | { operation: 'ask'; image_id: number; question: string; box_id?: number; retry_of?: number }
  | { operation: 'draft_report'; findings_version: number; retry_of?: number };

export type AIStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'timed_out' | 'cancelled';

export interface ModelArtifact {
  id: string;
  version: string;
  sha256: string;
}

export interface AIProvenance {
  provider_mode: ProviderMode;
  vlm_artifact?: ModelArtifact | null;
  cv_artifact?: ModelArtifact | null;
  prompt_version?: string;
  preprocessing_version?: string;
  image_sha256?: string | null;
  history_report_ids?: number[];
  history_hashes?: string[];
  message_ids?: number[];
  box_snapshot?: BoxSnapshot | null;
  source_findings_version?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  latency_ms?: number | null;
  invocations?: unknown[];
}

export interface AIRequest {
  request_id: number;
  operation: AICommand['operation'];
  status: AIStatus;
  poll_url: string;
  session_revision: number;
  deadline_at: string;
  retry_of: number | null;
  error: { code: string; message: string; retryable: boolean } | null;
  provenance: AIProvenance;
  output_refs: { findings_version?: number; box_ids?: number[]; message_ids?: number[]; report_id?: number };
  submitted_input?: AICommand;
}

export interface Capabilities {
  provider_mode: ProviderMode;
  can_detect: boolean;
  can_ask_roi: boolean;
  model_artifacts: ModelArtifact[];
}

export interface Blocker {
  gate: 'gate1' | 'gate2';
  code: string;
  resource_type: string;
  resource_id: string | number | null;
  message: string;
}

export interface SessionDTO {
  id: number;
  doctor_id: number;
  patient_id: number;
  study_id: number;
  status: SessionStatus;
  revision: number;
}

export interface HistoryReport {
  id: number;
  report_date: string;
  text: string;
}

export interface Workspace {
  session: SessionDTO;
  patient: PatientIdentity;
  study: StudyDTO;
  images: ImageDTO[];
  history: HistoryReport[];
  latest_findings: FindingsDTO | null;
  reports: ReportDTO[];
  boxes: BoxDTO[];
  messages: Page<MessageDTO>;
  live_request_id: number | null;
  latest_ai_request: AIRequest | null;
  initial_analysis?: AIRequest | null;
  blockers: Blocker[];
  capabilities: Capabilities;
}

export interface AuditEvent {
  id: number;
  type: string;
  created_at: string;
  user_id: number;
  data: Record<string, unknown>;
}

export interface AdminDoctor extends UserDTO {
  role: 'doctor';
  assigned_patient_ids: number[];
}

export interface AdminPatient {
  id: number;
  code: string;
  assigned_doctor_ids: number[];
}

export interface FixtureStatus {
  fixture_key: string;
  checksum: string;
  status: 'not_loaded' | 'loaded';
  patient_count: number;
  study_count: number;
  image_count: number;
}

export interface MutationReceipt {
  resource_type: 'doctor' | 'assignment' | 'fixture' | 'box' | 'findings' | 'report';
  resource_id: number | string;
  session_revision?: number;
}

export interface ErrorDetail {
  field: string;
  reason: string;
  value?: unknown;
}
