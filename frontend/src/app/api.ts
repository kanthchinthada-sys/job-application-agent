import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom, Observable } from 'rxjs';

import {
  AIMatchReport, AiStatus, Application, BankImportReport, BankRecord, RecordStatus, BoardSearch, Doc, ExtractedSkills, Handoff, Health, ImportedJob, Job, MatchReport,
  NewJob, QuickAdd, ResumeImport, CountryOption, SearchResult, FoundJob, ApplyResult, ApifyRun, ApifySettings, Preparation, Me, Requirements,
  Activity, IntakeStatus } from './models';

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
  quickAddJob(body: { url: string | null; description: string | null; allow_duplicate?: boolean; company?: string }) {
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
  updateApplication(id: number, body: { status?: string; notes?: string; submission_confirmation?: string }) {
    return this.call(this.http.patch<Application>(`/applications/${id}`, body));
  }

  documents(appId: number) { return this.call(this.http.get<Doc[]>(`/applications/${appId}/documents`)); }
  draft(appId: number, body: { kind: string; question?: string }) {
    return this.call(this.http.post<Doc>(`/applications/${appId}/drafts`, body));
  }
  saveVersion(docId: number, content: string) {
    return this.call(this.http.post<Doc>(`/documents/${docId}/versions`, { content }));
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
  exampleProfile() { return this.call(this.http.get<Record<string, unknown>>('/static/fictional_profile.json')); }
}
