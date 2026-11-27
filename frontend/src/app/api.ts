import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom, Observable } from 'rxjs';

import {
  AIMatchReport, AiSettings, AiStatus, Application, BankImportReport, BankRecord, RecordStatus, BoardSearch, Doc, ExtractedSkills, Handoff, Health, ImportedJob, Job, MatchReport,
  NewJob, QuickAdd, ResumeImport, CountryOption, SearchResult, FoundJob, ApplyResult, ApifyRun, ApifySettings, Preparation, Me, Requirements,
  Activity, IntakeStatus, GapSuggestion, SearchFilters, SearchPreset, InterviewPrep, SectionEdit, SectionReview, BenchmarkResult,
  TechnicalCoverage, EvidencePreview, ResumePdfCheck, AutoApplySetup, AutoApplyRun, AutoApplyApproval, AutoApplyScreening } from './models';

/** An error from the backend, with its readable message (FastAPI's "detail"). */
export class ApiError extends Error {
  constructor(public status: number, message: string, public data: any = {}) {
    super(message);
  }
}

/** Turns FastAPI error bodies into one readable message (422 lists every field problem). */
export function toApiError(err: unknown): ApiError {
  if (!(err instanceof HttpErrorResponse)) return new ApiError(0, String(err));
  const data = err.error && typeof err.error === 'object' ? err.error : {};
  let detail = data.detail;
  if (Array.isArray(detail)) {
    detail = detail.map((e: any) => `${(e.loc ?? []).slice(1).join(' → ') || 'input'}: ${e.msg}`).join('\n');
  }
  if (!detail) detail = err.status === 0 ? 'Cannot reach the app server. Is it running?' : `HTTP ${err.status}`;
  return new ApiError(err.status, detail, data);
}

/** Every call the screen makes to the backend. Same origin, so the sign-in cookie is sent automatically. */
@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);

  private async call<T>(request: Observable<T>): Promise<T> {
    try {
      return await firstValueFrom(request);
    } catch (err) {
      throw toApiError(err);
    }
  }

  health() { return this.call(this.http.get<Health>('/health')); }
  aiStatus() { return this.call(this.http.get<AiStatus>('/ai/status')); }
  aiSettings() { return this.call(this.http.get<AiSettings>('/ai/settings')); }
  saveAiSettings(body: { provider: string; api_key?: string | null; model?: string | null }) {
    return this.call(this.http.put<AiSettings>('/ai/settings', body));
  }
  logout() { return this.call(this.http.post('/logout', {})); }
  me() { return this.call(this.http.get<Me>('/auth/me')); }
  claimLocalData(setupCode: string) {
    return this.call(this.http.post<Record<string, unknown>>('/auth/claim-local-data', { setup_code: setupCode }));
  }
  requirements(jobId: number) { return this.call(this.http.get<Requirements>(`/jobs/${jobId}/requirements`)); }
  activity(appId: number) { return this.call(this.http.get<Activity>(`/applications/${appId}/activity`)); }

  jobs() { return this.call(this.http.get<Job[]>('/jobs')); }
  job(id: number) { return this.call(this.http.get<Job>(`/jobs/${id}`)); }
  match(id: number) { return this.call(this.http.get<MatchReport>(`/jobs/${id}/match`)); }
  aiMatch(id: number) { return this.call(this.http.post<AIMatchReport>(`/jobs/${id}/match/ai`, {})); }
  createJob(body: NewJob) { return this.call(this.http.post<Job>('/jobs', body)); }
  quickAddJob(body: { url: string | null; description: string | null; allow_duplicate?: boolean; company?: string; title?: string }) {
    return this.call(this.http.post<QuickAdd>('/jobs/quick', body));
  }
  jobIntake(id: number) { return this.call(this.http.get<IntakeStatus>(`/jobs/${id}/intake`)); }
  refreshJob(id: number) { return this.call(this.http.post<Job>(`/jobs/${id}/refresh`, {})); }
  updateJob(id: number, body: Record<string, unknown>) { return this.call(this.http.patch<Job>(`/jobs/${id}`, body)); }
  extractSkills(description: string) {
    return this.call(this.http.post<ExtractedSkills>('/ai/extract-skills', { description }));
  }
  importJob(url: string) { return this.call(this.http.post<ImportedJob>('/import/job', { url })); }
  searchBoard(url: string, onlySuitable: boolean) {
    return this.call(this.http.post<BoardSearch>('/import/board', { url, only_suitable: onlySuitable }));
  }

  searchCountries() {
    return this.call(this.http.get<{ countries: CountryOption[]; adzuna_configured: boolean }>('/search/countries'));
  }
  searchJobs(body: { title: string; country: string; date_from: string; date_to: string }) {
    return this.call(this.http.post<SearchResult>('/search', body));
  }
  latestSearch() {
    return this.call(this.http.get<{
      filters: { title: string; country: string; date_from: string; date_to: string };
      result: SearchResult; applications: Record<string, number>;
    } | null>('/search/latest'));
  }
  applyFound(job: FoundJob) { return this.call(this.http.post<ApplyResult>('/search/apply', { job })); }
  /** Apify: whether it is set up (the token never comes to the browser), its limits, and your latest run. */
  apifyStatus() { return this.call(this.http.get<{ settings: ApifySettings; latest: ApifyRun | null }>('/search/apify')); }
  apifyStart(body: { title: string; country: string; date_from: string; date_to: string }) {
    return this.call(this.http.post<ApifyRun>('/search/apify', body));
  }
  apifyRun(id: number) { return this.call(this.http.get<ApifyRun>(`/search/apify/${id}`)); }

  applications() { return this.call(this.http.get<Application[]>('/applications')); }
  application(id: number) { return this.call(this.http.get<Application>(`/applications/${id}`)); }
  startApplication(jobId: number) {
    return this.call(this.http.post<Application>('/applications', { job_id: jobId }));
  }
  updateApplication(id: number, body: { status?: string; notes?: string; submission_confirmation?: string;
    next_action?: string | null; due_date?: string | null; interview_at?: string | null;
    submitted_resume_id?: number | null; submitted_cover_letter_id?: number | null }) {
    return this.call(this.http.patch<Application>(`/applications/${id}`, body));
  }

  documents(appId: number) { return this.call(this.http.get<Doc[]>(`/applications/${appId}/documents`)); }
  draft(appId: number, body: { kind: string; question?: string }) {
    return this.call(this.http.post<Doc>(`/applications/${appId}/drafts`, body));
  }
  saveVersion(docId: number, content: string) {
    return this.call(this.http.post<Doc>(`/documents/${docId}/versions`, { content }));
  }
  technicalCoverage(docId: number) {
    return this.call(this.http.get<TechnicalCoverage>(`/documents/${docId}/technical-coverage`));
  }
  useVerifiedEvidence(docId: number, keyword: string, recordId: string, approval?: string) {
    return this.call(this.http.post<EvidencePreview>(`/documents/${docId}/verified-evidence`, {
      keyword, record_id: recordId, approval,
    }));
  }
  checkResumePdf(docId: number) {
    return this.call(this.http.get<ResumePdfCheck>(`/documents/${docId}/pdf-check`));
  }
  preparation(appId: number) { return this.call(this.http.get<Preparation>(`/applications/${appId}/prepare`)); }
  prepare(appId: number, steps?: string[]) {
    return this.call(this.http.post<Preparation>(`/applications/${appId}/prepare`, steps ? { steps } : {}));
  }
  handoff(appId: number) { return this.call(this.http.get<Handoff>(`/applications/${appId}/handoff`)); }

  plan(appId: number) { return this.call(this.http.get<Doc>(`/applications/${appId}/submission/plan`)); }
  preparePlan(appId: number) { return this.call(this.http.post<Doc>(`/applications/${appId}/submission/plan`, {})); }
  approvePlan(appId: number, answers: Record<string, string>) {
    return this.call(this.http.post<Doc>(`/applications/${appId}/submission/approve`, { answers }));
  }
  autoApplySetup(appId: number, resumeId?: number | null) {
    return this.call(this.http.get<AutoApplySetup>(`/applications/${appId}/auto-apply`,
      resumeId ? { params: { resume_id: resumeId } } : {}));
  }
  autoApplyScreening(appId: number, resumeId?: number | null) {
    return this.call(this.http.get<AutoApplyScreening>(`/applications/${appId}/auto-apply/screening`,
      resumeId ? { params: { resume_id: resumeId } } : {}));
  }
  setAutoApplyPortal(appId: number, url: string) {
    return this.call(this.http.put<AutoApplySetup>(`/applications/${appId}/auto-apply/portal`, { url }));
  }
  autoApply(appId: number, approval: AutoApplyApproval) {
    return this.call(this.http.post<AutoApplyRun>(`/applications/${appId}/auto-apply`, approval));
  }
  cancelAutoApply(appId: number) {
    return this.call(this.http.post<AutoApplyRun>(`/applications/${appId}/auto-apply/cancel`, {}));
  }
  reviewAutoApply(appId: number) {
    return this.call(this.http.post<AutoApplyRun>(`/applications/${appId}/auto-apply/review`, { verified_not_received: true }));
  }

  profile() { return this.call(this.http.get<Record<string, unknown>>('/profile')); }
  importBank(text: string) {
    return this.call(this.http.post<{ report: BankImportReport; records: BankRecord[] }>('/profile/bank', { text }));
  }
  setRecordStatus(id: string, status: RecordStatus, note: string | null = null) {
    return this.call(this.http.patch<BankRecord>(`/profile/bank/${id}`, { status, status_note: note }));
  }
  readResume(body: { filename?: string; content_base64?: string; text?: string }) {
    return this.call(this.http.post<ResumeImport>('/profile/resume', body));
  }
  addExperienceLine(body: { owner: string; text: string; skill?: string }) {
    return this.call(this.http.post<{ id: string | null; owner: string; skill_added: boolean }>(
      '/profile/experience-line', body));
  }
  saveProfile(profile: unknown) { return this.call(this.http.put('/profile', profile)); }
  experienceGaps(jobId?: number) {
    const params: Record<string, string> = jobId === undefined ? {} : { job_id: String(jobId) };
    return this.call(this.http.get<GapSuggestion[]>('/experience-gaps', { params }));
  }
  draftGap(id: number, again = false, owner?: string) {
    const params: Record<string, string> = {};
    if (again) params['again'] = 'true';
    if (owner) params['owner'] = owner;
    return this.call(this.http.post<GapSuggestion>(`/experience-gaps/${id}/draft`, {},
      { params }));
  }
  confirmGap(id: number, owner: string, text: string) {
    return this.call(this.http.post<{ record_id: string; application_id: number; preparation: Preparation }>(
      `/experience-gaps/${id}/confirm`, { owner, text, confirmed: true }));
  }
  confirmGaps(items: { id: number; owner: string; text: string }[]) {
    return this.call(this.http.post<{ record_ids: string[]; application_ids: number[]; preparations: Preparation[];
      rejected: Record<number, string> }>('/experience-gaps/confirm', { items, confirmed: true, report_errors: true }));
  }
  dismissGap(id: number) { return this.call(this.http.delete(`/experience-gaps/${id}`)); }
  savedSearches() { return this.call(this.http.get<SearchPreset[]>('/search/saved')); }
  saveSearch(name: string, filters: SearchFilters) {
    return this.call(this.http.post<SearchPreset>('/search/saved', { name, filters }));
  }
  runSavedSearch(id: number) { return this.call(this.http.post<SearchResult>(`/search/saved/${id}/run`, {})); }
  deleteSavedSearch(id: number) { return this.call(this.http.delete(`/search/saved/${id}`)); }
  interviewPrep(id: number) { return this.call(this.http.get<InterviewPrep>(`/applications/${id}/interview-prep`)); }
  editSection(id: number, body: SectionEdit) {
    return this.call(this.http.post<SectionReview>(`/documents/${id}/section`, body));
  }
  benchmarkCases() { return this.call(this.http.get<{ id: string; title: string }[]>('/ai/benchmark/cases')); }
  compareProviders(body: { case_id: string; claude_key?: string; openai_key?: string; claude_model?: string;
                          openai_model?: string; rates?: Record<string, { input: number; output: number }> }) {
    return this.call(this.http.post<{ results: BenchmarkResult[] }>('/ai/benchmark', body));
  }
  exampleProfile() { return this.call(this.http.get<Record<string, unknown>>('/static/fictional_profile.json')); }
}
