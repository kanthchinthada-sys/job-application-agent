// Shapes of the backend's JSON (see app/schemas.py). Only the fields the screen uses.

export interface Job {
  pending_technical_gaps?: number;
  id: number;
  title: string;
  company: string;
  location: string | null;
  source_url: string | null;
  description: string;
  required_skills: string[];
  preferred_skills: string[];
}

export interface NewJob {
  title: string;
  company: string;
  description: string;
  required_skills: string[];
  preferred_skills: string[];
  location?: string;
  source_url?: string;
  allow_duplicate?: boolean;
}

export interface Evidence { kind: 'declared' | 'mentioned' | 'related' | 'ai'; source: string; text: string; via: string; part: string; }
export interface SkillResult {
  skill: string; normalized: string; evidence: Evidence[]; found_parts: string[]; missing_parts: string[];
}
export interface SkillSection {
  total: number;
  coverage_percent: number;
  matched: SkillResult[];
  related: SkillResult[];
  mentioned_only: SkillResult[];
  missing: string[];
}
export interface AISuggestion { requirement: string; fact_id: string; fact: string; quote: string; reason: string; }
export interface AIMatchReport { method_label: string; model: string; suggestions: AISuggestion[]; still_missing: string[]; rejected: number; }
export interface MatchReport {
  job_id: number;
  method_label: string;
  required: SkillSection;
  preferred: SkillSection;
  limitations: string[];
}

export interface HistoryEvent { occurred_at: string; from_status: string | null; to_status: string; note?: string | null; }
export interface Application {
  id: number;
  job_id: number;
  job_title: string;
  company: string;
  source_url: string | null;
  status: string;
  notes: string;
  submission_confirmation: string | null;
  history: HistoryEvent[];
  allowed_next: string[];
  documents_ready?: string[];        // document kinds saved for it: generation, not the application status
  last_activity?: string | null;
  created_at?: string;
  updated_at?: string;
  next_action?: string | null;
  due_date?: string | null;
  interview_at?: string | null;
  submitted_resume_id?: number | null;
  submitted_cover_letter_id?: number | null;
}

export interface AiStatus { provider?: string; reachable: boolean; model: string; model_available: boolean; error?: string; }
export interface AiSettings {
  provider: 'claude' | 'openai' | 'ollama'; model: string | null; has_key: boolean; key_hint: string | null;
  updated_at: string | null;
}
export interface Health { status: string; password_protection: boolean; accounts?: boolean; }
export interface Me {
  username: string | null; accounts: boolean; local_data_available: boolean;
}

export interface PreferenceCheck {
  title_match: string; title_reason: string;
  location_match: string; location_reason: string;
}
export interface ImportedJob {
  job: { title: string; company: string; location: string | null; source_url: string; description: string };
  preferences: PreferenceCheck;
  already_added_job_id: number | null;
}
export interface BoardSearch {
  total_postings: number;
  shown: number;
  results: {
    posting: { title: string; company: string; location: string | null; source_url: string };
    preferences: PreferenceCheck;
    already_added_job_id: number | null;
  }[];
}
export interface ExtractedSkills { required_skills: string[]; preferred_skills: string[]; needs_review: string[]; model: string; }

export type DocKind = 'cover_letter' | 'screening_answer' | 'resume' | 'application_plan';
export interface Doc {
  id: number;
  application_id: number;
  kind: DocKind;
  question: string;
  version: number;
  content: string;
  source: 'ai' | 'user_edit' | 'agent';
  review: any;          // differs by kind; see the materials components
  created_at: string;
}

export interface Handoff {
  company: string;
  apply_url: string | null;
  copy_fields: { label: string; value: string }[];
  cover_letter: Doc | null;
  resume: Doc | null;
  screening_answers: Doc[];
  checklist: string[];
  warnings: string[];
}

export interface PlanField {
  name: string;
  label: string;
  type: 'text' | 'textarea' | 'single_select' | 'multi_select' | 'file' | string;
  required: boolean;
  options: string[];
  answer: string;
  source: string;
  needs_you: boolean;
  sensitive: boolean;
  note: string;
}
export interface SubmissionPlan { apply_url: string; fields: PlanField[]; approved: boolean; warnings: string[]; }

export type RecordStatus = 'verified' | 'partly_true' | 'unverified' | 'not_true';
export interface BankRecord {
  id: string; text: string; owner: string; owner_kind: 'experience' | 'project'; group: string | null;
  status: RecordStatus; status_note: string | null; caveat: string | null;
}
export interface BankImportReport {
  imported: number; by_owner: Record<string, number>; duplicates_removed: string[]; rerouted: string[];
  missing_ids: string[]; notes_kept_as_caveats: string[]; labels: string[]; status_counts: Record<string, number>;
}
export interface ReportRow {
  requirement: string; kind: string; selected_ids: string[]; strength: string; location: string[]; not_found_parts: string[];
  found_parts?: string[]; category?: 'technical' | 'soft skill' | 'education';
}
/** One requirement of the posting, the records that meet it, and where each one printed on the resume. */
export interface RequirementMapRow {
  requirement: string; kind: string; category: 'technical' | 'soft skill' | 'education'; strength: string;
  found: string[]; missing: string[]; where: string | null; found_elsewhere?: Record<string, string>;
  on_resume: { id: string; owner: string; where: string; line: string }[]; more_on_resume?: number; not_printed: string[];
}
export interface TailoringReport {
  role_types: string[]; selected: Record<string, string[]>; rows: ReportRow[]; gaps: string[];
  excluded_by_status: string[]; removed_as_duplicate: string[]; caveats: Record<string, string>; eligibility: string[];
}

export const pretty = (s: string) => s.replaceAll('_', ' ');
export const splitSkills = (s: string) => s.split(',').map(x => x.trim()).filter(Boolean);

export interface ResumeImport { method: string; profile: any; warnings: string[]; sections_found: string[]; uncertain?: string[]; }
export interface Intake {
  method: string; title: string; company: string; location: string | null;
  required_skills: string[]; preferred_skills: string[]; found_by: Record<string, string>; notes: string[];
  review?: string[];                  // fields to check: missing, or read an uncertain way
  ai_state?: 'not_needed' | 'pending' | 'done' | 'failed' | 'off';
}
/** GET /jobs/{id}/intake: the local AI's background step after adding a job. */
export interface IntakeStatus {
  ai_state: 'not_needed' | 'pending' | 'done' | 'failed' | 'off'; review: string[]; notes: string[];
  found_by: Record<string, string>; applied: string[]; kept: string[]; job: Job;
}
export interface QuickAdd { job: Job; intake: Intake; }
export interface CountryOption { code: string; name: string; adzuna: boolean; }
export interface FoundJob {
  source: string; title: string; company: string; location: string; posted: string | null; url: string;
  description: string; description_is_excerpt: boolean; remote: boolean; where: string; title_match: string;
  already_added_job_id: number | null; credit_url?: string;
  new_since_last_run?: boolean; technical_matches?: string[]; technical_gaps?: string[]; salary_text?: string | null; workplace?: string | null;
}
export interface ElsewhereLink { name: string; url: string; note: string; }
export interface SourceStatus { name: string; ok: boolean; found: number; kept: number; note: string; }
export interface SearchResult {
  method: string; query: string; country: string; date_from: string; date_to: string;
  results: FoundJob[]; sources: SourceStatus[]; elsewhere?: ElsewhereLink[];
}
/** Apify (optional, paid): structured listings from employer career sites, run in the background. */
export interface ApifySettings {
  configured: boolean; actor: string; max_results: number; timeout_secs: number; max_cost_usd: number; reuse_minutes: number;
}
export interface ApifyRun {
  id: number; status: string; message: string; actor: string;
  filters: { title: string; country: string; date_from: string; date_to: string };
  found: number | null; kept: number; cost_usd: number | null; max_cost_usd: number; max_results: number;
  timeout_secs: number; started_at: string; finished_at: string | null; active: boolean; results: FoundJob[];
  reused?: string;
}
export interface ApplyResult {
  job_id: number; application_id: number; preparation: string; apply_url: string; new_job: boolean; new_application: boolean; notes: string[];
}
export interface Preparation {
  application_id: number; state: 'idle' | 'queued' | 'running' | 'done' | 'failed' | 'interrupted'; step: string | null;
  message: string; done_steps: string[]; started_at: string | null; finished_at: string | null;
  failed_steps?: Record<string, string>; steps?: string[];
}
export interface RequirementEvidence {
  requirement: string; kind: string; category: 'technical' | 'soft skill' | 'education'; strength: string;
  evidence: string; found: string[]; missing: string[]; found_elsewhere: Record<string, string>;
  records: { id: string; owner: string; text: string }[];
}
export interface Requirements { job_id: number; role_types: string[]; has_bank: boolean; eligibility: string[];
  rows: RequirementEvidence[]; }
export interface ActivityItem { at: string; type: 'status' | 'document'; text: string; note?: string | null;
  document_id?: number; kind?: string; version?: number; source?: string; }
export interface Activity { application_id: number; notes: string; notes_saved_at: string; items: ActivityItem[]; }


export interface GapSuggestion {
  id: number; job_id: number; job_title: string; job_company: string;
  skill: string; requirement: string; owner_hint: string | null; example: string;
}
export interface SearchFilters { title: string; country: string; date_from: string; date_to: string; }
export interface SearchPreset { id: number; name: string; filters: SearchFilters; last_run_at: string | null; }
export interface InterviewPrep {
  versions: Record<string, { id: number; version: number; submitted: boolean }>;
  questions: { topic: string; question: string; owner: string; record_id: string; evidence: string;
               caveat: string | null; on_resume: boolean; in_letter: boolean }[];
  topics_to_review: string[];
}
export interface SectionEdit {
  section: 'summary' | 'skills' | 'bullet' | 'paragraph'; text: string; role_index?: number; item_index?: number; save?: boolean;
}
export interface SectionReview {
  previous: string; candidate: string; warnings: string[]; evidence: BankRecord[]; document: Doc | null;
}
export interface BenchmarkResult {
  provider: string; model: string; seconds: number; input_tokens: number | null; output_tokens: number | null;
  estimated_usd: number | null; calls: number; error?: string; summary?: string; skills?: string[];
  letter?: string; resume_warnings?: string[]; letter_warnings?: string[]; letter_removed?: number; humanizer_score?: number | null;
}
export interface CoverageCount {
  total: number;
  covered: number;
  demonstrated: number;
}

export interface VerifiedEvidence {
  record_id: string;
  owner: string;
  text: string;
}

export interface TechnicalRequirement {
  keyword: string;
  required: boolean;
  placement: 'bullet' | 'skills' | 'mention' | 'verified' | 'profile' | 'transferable' | 'gap';
  evidence: string | null;
  owner: string | null;
  options: VerifiedEvidence[];
}

export interface TechnicalCoverage {
  score: number | null;
  required: CoverageCount;
  preferred: CoverageCount;
  rows: TechnicalRequirement[];
}

export interface EvidencePreview extends VerifiedEvidence {
  approval: string;
  keyword: string;
  replaces: string | null;
  before: TechnicalCoverage;
  after: TechnicalCoverage;
  printed_pages: number | null;
  warnings: string[];
  document: Doc | null;
}

export interface ResumePdfCheck {
  readable: boolean;
  required_found: string[];
  required_missing: string[];
  issues: string[];
}
export interface AutoApplyQuestion {
  key: string;
  label: string;
  options: string[];
  manual: boolean;
  draftable: boolean;
}

export interface AutoApplyRun {
  state: string;
  message: string;
  questions: AutoApplyQuestion[];
  resume_id: number | null;
  cover_letter_id: number | null;
  evidence: string | null;
}

export interface AutoApplySetup {
  supported: boolean;
  reason?: string;
  provider?: string;
  url?: string;
  preview?: string;
  job_title?: string;
  company?: string;
  contact?: Record<string, string>;
  saved_answers?: { name: string; label: string; value: string }[];
  materials?: Pick<Doc, 'id' | 'version' | 'kind'>[];
  run?: AutoApplyRun;
  blocked?: boolean;
  can_set_portal?: boolean;
}

export interface AutoApplyApproval {
  preview: string;
  resume_id: number;
  cover_letter_id: number | null;
  answers: Record<string, string>;
  reviewed_in_browser: boolean;
}
