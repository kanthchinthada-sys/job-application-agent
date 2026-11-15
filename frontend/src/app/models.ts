// Shapes of the backend's JSON (see app/schemas.py). Only the fields the screen uses.

export interface Job {
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
}

export interface AiStatus { reachable: boolean; model: string; model_available: boolean; }
export interface Health { status: string; password_protection: boolean; }

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
}
export interface TailoringReport {
  role_types: string[]; selected: Record<string, string[]>; rows: ReportRow[]; gaps: string[];
  excluded_by_status: string[]; removed_as_duplicate: string[]; caveats: Record<string, string>; eligibility: string[];
}

export const pretty = (s: string) => s.replaceAll('_', ' ');
export const splitSkills = (s: string) => s.split(',').map(x => x.trim()).filter(Boolean);

export interface ResumeImport { method: string; profile: any; warnings: string[]; sections_found: string[]; }
export interface Intake {
  method: string; title: string; company: string; location: string | null;
  required_skills: string[]; preferred_skills: string[]; found_by: Record<string, string>; notes: string[];
}
export interface QuickAdd { job: Job; intake: Intake; }
export interface CountryOption { code: string; name: string; adzuna: boolean; }
export interface FoundJob {
  source: string; title: string; company: string; location: string; posted: string | null; url: string;
  description: string; description_is_excerpt: boolean; remote: boolean; where: string; title_match: string;
  already_added_job_id: number | null;
}
export interface SourceStatus { name: string; ok: boolean; found: number; kept: number; note: string; }
export interface SearchResult {
  method: string; query: string; country: string; date_from: string; date_to: string;
  results: FoundJob[]; sources: SourceStatus[];
}
export interface ApplyResult {
  job_id: number; application_id: number; apply_url: string; new_job: boolean; new_application: boolean; notes: string[];
}
