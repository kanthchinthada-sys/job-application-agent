import { Injectable, effect, inject, signal } from '@angular/core';

import { Api, ApiError } from './api';
import { ApifyRun, ApifySettings, FoundJob, SearchResult } from './models';

export interface RowState { step: string; kind: 'info' | 'ok' | 'bad'; applicationId?: number; applyUrl?: string; }
type SearchFilters = { title: string; country: string; date_from: string; date_to: string };

const KEY = 'find-jobs-state-v1';
const iso = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
export const daysAgo = (n: number) => iso(new Date(Date.now() - n * 86400000));
export const today = () => iso(new Date());

/**
 * Find jobs remembers your last search while you use the app: filters, results and what each Apply is doing.
 * Leaving the tab (e.g. to read the Materials) and coming back shows the same screen. It is also kept for the
 * browser tab's session. The workspace also remembers the last completed search for a newly opened tab.
 */
@Injectable({ providedIn: 'root' })
export class FindJobsState {
  private api = inject(Api);

  readonly title = signal('');
  readonly country = signal('');
  readonly dateFrom = signal(daysAgo(6));
  readonly dateTo = signal(today());
  readonly result = signal<SearchResult | null>(null);
  readonly rows = signal<Record<string, RowState>>({});
  readonly searching = signal(false);
  readonly searchError = signal('');
  readonly scroll = signal(0);
  /** Apify (optional, paid): its settings, and the run you started (its progress, then its results). */
  readonly apifySettings = signal<ApifySettings | null>(null);
  readonly apifyRun = signal<ApifyRun | null>(null);
  readonly apifyError = signal('');
  private watching = new Set<number>();
  private followingRun: number | null = null;

  constructor() {
    this.restore();
    effect(() => {                                   // save on every change
      const snapshot = { title: this.title(), country: this.country(), dateFrom: this.dateFrom(),
                         dateTo: this.dateTo(), result: this.result(), rows: this.rows(), apifyRun: this.apifyRun(),
                         scroll: this.scroll() };
      try { sessionStorage.setItem(KEY, JSON.stringify(snapshot)); } catch { /* storage full or blocked */ }
    });
  }

  private restore() {
    try {
      const s = JSON.parse(sessionStorage.getItem(KEY) ?? 'null');
      if (!s) return;
      this.title.set(s.title ?? ''); this.country.set(s.country ?? '');
      this.dateFrom.set(s.dateFrom ?? daysAgo(6)); this.dateTo.set(s.dateTo ?? today());
      this.result.set(s.result ?? null); this.rows.set(s.rows ?? {});
      this.apifyRun.set(s.apifyRun ?? null);
      this.scroll.set(s.scroll ?? 0);
      // resume following any preparation that was still running
      for (const [url, row] of Object.entries(s.rows ?? {}) as [string, RowState][]) {
        if (row.applicationId && row.kind === 'info') this.follow(url, row.applicationId, '', row.applyUrl);
      }
    } catch { /* nothing saved, or saved by an older version */ }
  }

  private setFilters(filters: SearchFilters) {
    this.title.set(filters.title);
    this.country.set(filters.country);
    this.dateFrom.set(filters.date_from);
    this.dateTo.set(filters.date_to);
  }

  async restoreSearch() {
    if (this.result() || this.searching()) return;
    try {
      const saved = await this.api.latestSearch();
      if (saved && !this.result() && !this.searching() && !this.apifyRun()?.filters) {
        this.setFilters(saved.filters);
        this.result.set(saved.result);
        for (const [url, applicationId] of Object.entries(saved.applications)) {
          if (!this.rows()[url]) this.setRow(url, { step: 'Application saved', kind: 'ok', applicationId });
        }
      }
    } catch { /* a cached browser search can still be used */ }
  }

  async search(filters: SearchFilters) {
    if (this.searching()) return;
    this.searching.set(true);
    this.searchError.set('');
    try {
      this.result.set(await this.api.searchJobs(filters));
      this.scroll.set(0);
    } catch (e) {
      this.searchError.set((e as ApiError).message);
    } finally {
      this.searching.set(false);
    }
  }

  /** Apify's settings and your latest run; a run still active is followed again (e.g. after a page reload). */
  async loadApify(restoreFilters = false) {
    try {
      const s = await this.api.apifyStatus();
      this.apifySettings.set(s.settings);
      const current = this.apifyRun();
      if (s.latest && (!current || current.id === s.latest.id)) {
        this.apifyRun.set(s.latest);
        if (restoreFilters && !this.title() && !this.result() && !this.searching() && s.latest.filters) {
          this.setFilters(s.latest.filters);
        }
        if (s.latest.active) this.followApify(s.latest.id);
      } else if (current?.active) {
        this.followApify(current.id);
      }
    } catch { /* Find jobs works without it */ }
  }

  /** Start the paid search with the same filters. The server returns at once (or the run already active). */
  async startApify(filters: SearchFilters) {
    this.apifyError.set('');
    try {
      const run = await this.api.apifyStart(filters);
      this.apifyRun.set(run);
      if (run.active) this.followApify(run.id);
    } catch (e) {
      this.apifyError.set((e as ApiError).message);
    }
  }

  /** Ask for the run's progress every few seconds until it has finished (the free results stay usable). */
  private async followApify(id: number) {
    if (this.followingRun === id) return;
    this.followingRun = id;
    try {
      for (let i = 0; i < 600; i++) {
        await new Promise(r => setTimeout(r, 4000));
        const run = await this.api.apifyRun(id);
        this.apifyRun.set(run);
        if (!run.active) return;
      }
    } catch (e) {
      this.apifyError.set((e as ApiError).message);
    } finally {
      this.followingRun = null;
    }
  }

  setRow(url: string, row: RowState) {
    this.rows.update(all => ({ ...all, [url]: row }));
  }

  /** Save the job and start the application; the server prepares the resume and cover letter in the background. */
  async apply(job: FoundJob) {
    this.setRow(job.url, { step: 'Saving the job…', kind: 'info' });
    try {
      const r = await this.api.applyFound(job);
      const note = r.notes.length ? ` ${r.notes[0]}` : '';
      const applyUrl = r.apply_url !== job.url ? r.apply_url : undefined;      // the employer's own page, when found
      if (r.preparation === 'done') {
        this.setRow(job.url, { step: `Ready: resume and cover letter prepared.${note}`, kind: 'ok', applicationId: r.application_id, applyUrl });
      } else {
        this.setRow(job.url, { step: 'Preparing your resume and cover letter… (continues in the background)',
                               kind: 'info', applicationId: r.application_id, applyUrl });
        this.follow(job.url, r.application_id, note, applyUrl);
      }
    } catch (e) {
      this.setRow(job.url, { step: (e as ApiError).message, kind: 'bad' });
    }
  }

  /** Check the background preparation every few seconds until it is done. */
  private async follow(url: string, applicationId: number, note = '', applyUrl?: string) {
    if (this.watching.has(applicationId)) return;
    this.watching.add(applicationId);
    try {
      for (let i = 0; i < 400; i++) {                         // gives up after ~20 minutes
        const p = await this.api.preparation(applicationId);
        if (p.state === 'done') {
          this.setRow(url, { step: `Ready: resume and cover letter prepared.${note}`, kind: 'ok', applicationId, applyUrl });
          return;
        }
        if (p.state === 'failed') {
          this.setRow(url, { step: p.message, kind: 'bad', applicationId });
          return;
        }
        if (p.state === 'idle') {                              // the app restarted meanwhile
          this.setRow(url, { step: 'Not prepared yet: open Materials to tailor the resume.', kind: 'bad', applicationId });
          return;
        }
        this.setRow(url, { step: `${p.message} (${p.done_steps.length} of 2 done)`, kind: 'info', applicationId, applyUrl });
        await new Promise(r => setTimeout(r, 3000));
      }
    } catch (e) {
      this.setRow(url, { step: (e as ApiError).message, kind: 'bad', applicationId });
    } finally {
      this.watching.delete(applicationId);
    }
  }
}
