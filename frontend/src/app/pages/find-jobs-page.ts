import { Component, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { Api, ApiError } from '../api';
import { CountryOption, FoundJob, SearchResult } from '../models';
import { Msg, MsgBox } from '../ui';

interface RowState { step: string; kind: 'info' | 'ok' | 'bad'; applicationId?: number; }

const iso = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const daysAgo = (n: number) => iso(new Date(Date.now() - n * 86400000));

/** Find jobs: three filters (title, country, date range) across free job sources; Apply prepares everything. */
@Component({
  selector: 'app-find-jobs-page',
  imports: [FormsModule, MsgBox, RouterLink],
  template: `
    <div class="card">
      <h2>Find jobs</h2>
      <div class="row filters">
        <div><label for="f-title">Job title</label>
          <input id="f-title" [(ngModel)]="title" placeholder="e.g. QA Engineer" (keyup.enter)="search()"></div>
        <div><label for="f-country">Country</label>
          <select id="f-country" [(ngModel)]="country">
            @for (c of countries(); track c.code) { <option [value]="c.code">{{ c.name }}</option> }
          </select></div>
        <div><label for="f-from">Posted from</label><input id="f-from" type="date" [(ngModel)]="dateFrom" [max]="today"></div>
        <div><label for="f-to">to</label><input id="f-to" type="date" [(ngModel)]="dateTo" [max]="today"></div>
      </div>
      <div class="small">
        @for (p of presets; track p.days) {
          <button class="secondary tight" type="button" (click)="preset(p.days)">{{ p.label }}</button>
        }
      </div>
      <button class="primary" type="button" [disabled]="busy()" (click)="search()">{{ busy() ? 'Searching…' : 'Search' }}</button>
      <app-msg [msg]="msg()" />
      @if (result(); as r) {
        <p class="small muted" data-testid="sources">
          @for (s of r.sources; track s.name) {
            <span [class.warn-text]="!s.ok" [title]="s.note">{{ s.name }}: {{ s.ok ? s.kept + ' kept of ' + s.found : 'off' }}</span>
            @if (!$last) { · }
          }
        </p>
        @for (s of r.sources; track s.name) {
          @if (!s.ok && s.note) { <div class="small muted">{{ s.name }}: {{ s.note }}</div> }
        }
      }
    </div>

    @if (result(); as r) {
      <div class="card" data-testid="found-jobs">
        @if (r.results.length) {
          <table>
            <thead><tr><th>Job</th><th>Where</th><th>Posted</th><th>Source</th><th></th></tr></thead>
            <tbody>
              @for (j of r.results; track j.url) {
                <tr>
                  <td><b><a [href]="j.url" target="_blank" rel="noopener">{{ j.title }}</a></b><br>
                    <span class="muted small">{{ j.company }}</span></td>
                  <td class="small">{{ j.where }}</td>
                  <td class="small">{{ j.posted || '—' }}</td>
                  <td class="small muted">{{ j.source }}</td>
                  <td>
                    @if (state()[j.url]; as s) {
                      <div class="small" [class.good]="s.kind === 'ok'" [class.warn-text]="s.kind === 'bad'">{{ s.step }}</div>
                      @if (s.applicationId) {
                        <a class="small" [routerLink]="['/applications', s.applicationId]">Materials</a>
                      }
                    } @else {
                      <button class="primary tight" type="button" (click)="apply(j)">{{ j.already_added_job_id ? 'Apply (saved)' : 'Apply' }}</button>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
          <p class="small muted">Apply opens the employer's page in a new tab and prepares your tailored resume and cover
            letter here. You submit the application yourself; nothing is sent for you.</p>
        } @else {
          <p class="muted">No jobs matched. Try a shorter title, a longer date range, or another country.</p>
        }
      </div>
    }
  `,
})
export class FindJobsPage implements OnInit {
  private api = inject(Api);

  protected readonly today = iso(new Date());
  protected readonly presets = [
    { label: 'Last 24 hours', days: 1 }, { label: '3 days', days: 3 }, { label: '7 days', days: 7 },
    { label: '14 days', days: 14 }, { label: '30 days', days: 30 },
  ];
  protected readonly title = signal('');
  protected readonly country = signal('CA');
  protected readonly dateFrom = signal(daysAgo(6));
  protected readonly dateTo = signal(iso(new Date()));
  protected readonly countries = signal<CountryOption[]>([]);
  protected readonly busy = signal(false);
  protected readonly msg = signal<Msg | null>(null);
  protected readonly result = signal<SearchResult | null>(null);
  protected readonly state = signal<Record<string, RowState>>({});

  async ngOnInit() {
    try {
      const c = await this.api.searchCountries();
      this.countries.set(c.countries);
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    }
    try {   // start from your own target role and country, if your profile has them
      const p = await this.api.profile() as any;
      const role = p?.preferences?.target_roles?.[0];
      if (role) this.title.set(role);
      const locs: string[] = (p?.preferences?.locations ?? []).map((x: string) => x.toLowerCase());
      const hit = this.countries().find(c => locs.some(l => l.includes(c.name.toLowerCase())));
      if (hit) this.country.set(hit.code);
    } catch { /* no profile yet: fine */ }
  }

  preset(days: number) {
    this.dateFrom.set(daysAgo(days - 1));
    this.dateTo.set(iso(new Date()));
  }

  async search() {
    if (this.title().trim().length < 2) {
      this.msg.set({ kind: 'bad', text: 'Enter a job title.' });
      return;
    }
    this.busy.set(true);
    this.result.set(null);
    this.state.set({});
    this.msg.set({ kind: 'info', text: 'Searching the job sources…' });
    try {
      const r = await this.api.searchJobs({ title: this.title().trim(), country: this.country(),
                                            date_from: this.dateFrom(), date_to: this.dateTo() });
      this.result.set(r);
      this.msg.set({ kind: 'ok', text: `${r.results.length} jobs found in ${r.country}, posted ${r.date_from} to ${r.date_to}.` });
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    } finally {
      this.busy.set(false);
    }
  }

  private setRow(url: string, s: RowState) {
    this.state.update(all => ({ ...all, [url]: s }));
  }

  async apply(j: FoundJob) {
    window.open(j.url, '_blank', 'noopener');          // must happen right in the click, or the browser blocks it
    this.setRow(j.url, { step: 'Saving the job…', kind: 'info' });
    let appId: number | undefined;
    try {
      const r = await this.api.applyFound(j);
      appId = r.application_id;
      this.setRow(j.url, { step: 'Tailoring your resume…', kind: 'info', applicationId: appId });
      await this.api.draft(appId, { kind: 'resume' });
      this.setRow(j.url, { step: 'Writing your cover letter…', kind: 'info', applicationId: appId });
      await this.api.draft(appId, { kind: 'cover_letter' });
      const note = r.notes.length ? ` ${r.notes[0]}` : '';
      this.setRow(j.url, { step: `Ready: resume and cover letter prepared.${note}`, kind: 'ok', applicationId: appId });
    } catch (e) {
      const err = e as ApiError;
      const text = err.status === 404 && /profile/i.test(err.message)
        ? 'Saved. Add your profile (Profile tab) to prepare the resume and cover letter.' : err.message;
      this.setRow(j.url, { step: text, kind: 'bad', applicationId: appId });
    }
  }
}
