import { Injectable, effect, inject, signal } from '@angular/core';

import { Api, ApiError } from './api';
import { FoundJob, SearchResult } from './models';

export interface RowState { step: string; kind: 'info' | 'ok' | 'bad'; applicationId?: number; applyUrl?: string; }

const KEY = 'find-jobs-state-v1';
const iso = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
export const daysAgo = (n: number) => iso(new Date(Date.now() - n * 86400000));
export const today = () => iso(new Date());

/**
 * Find jobs remembers your last search while you use the app: filters, results and what each Apply is doing.
 * Leaving the tab (e.g. to read the Materials) and coming back shows the same screen. It is also kept for the
 * browser tab's session (sessionStorage), so a page refresh doesn't lose it; nothing is stored anywhere else.
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
  private watching = new Set<number>();

  constructor() {
    this.restore();
    effect(() => {                                   // save on every change
      const snapshot = { title: this.title(), country: this.country(), dateFrom: this.dateFrom(),
                         dateTo: this.dateTo(), result: this.result(), rows: this.rows() };
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
      // resume following any preparation that was still running
      for (const [url, row] of Object.entries(s.rows ?? {}) as [string, RowState][]) {
        if (row.applicationId && row.kind === 'info') this.follow(url, row.applicationId, '', row.applyUrl);
      }
    } catch { /* nothing saved, or saved by an older version */ }
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
