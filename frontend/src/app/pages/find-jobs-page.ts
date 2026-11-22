import { Component, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { Api, ApiError } from '../api';
import { daysAgo, FindJobsState, today } from '../find-jobs-state';
import { CountryOption, FoundJob } from '../models';
import { Msg, MsgBox } from '../ui';

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
      <div class="seg" role="group" aria-label="Posted within" style="margin-top:10px; flex-wrap:wrap">
        @for (p of presets; track p.days) {
          <button type="button" [attr.aria-pressed]="activePreset() === p.days" (click)="preset(p.days)">{{ p.label }}</button>
        }
      </div>
      <div><button class="primary" type="button" [disabled]="busy()" (click)="search()">{{ busy() ? 'Searching…' : 'Search' }}</button></div>
      @if (msg()?.kind === 'bad') { <app-msg [msg]="msg()" /> }
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
        @if (r.elsewhere?.length) {
          <p class="small" data-testid="elsewhere">Search elsewhere (opens the same search on sites the app can't read):
            @for (l of r.elsewhere; track l.name) {
              <a class="button tight" [href]="l.url" target="_blank" rel="noopener" [title]="l.note">{{ l.name }}</a>
            }
          </p>
        }
      }
    </div>

    @if (busy()) {
      <div class="card empty" data-testid="searching"><span class="spinner"></span> Searching the job sources… this takes a few seconds.</div>
    }
    @if (result(); as r) {
      <div class="card" data-testid="found-jobs">
        <p class="small" style="margin-top:0"><b>{{ r.results.length }} jobs</b> in {{ r.country }}, posted {{ r.date_from }} to {{ r.date_to }}.</p>
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
                  <td class="small muted">
                    @if (j.credit_url) { via <a [href]="j.credit_url" target="_blank" rel="noopener">{{ j.source }}</a> }
                    @else { {{ j.source }} }</td>
                  <td>
                    @if (state.rows()[j.url]; as s) {
                      <div class="small" [class.good]="s.kind === 'ok'" [class.warn-text]="s.kind === 'bad'">{{ s.step }}</div>
                      @if (s.applicationId) {
                        <a class="small" [routerLink]="['/applications', s.applicationId]">Open application</a>
                      }
                      @if (s.applyUrl) {
                        · <a class="small" [href]="s.applyUrl" target="_blank" rel="noopener">Employer's apply page</a>
                      }
                    } @else {
                      <button class="secondary tight" type="button" (click)="apply(j)">{{ j.already_added_job_id ? 'Apply (saved)' : 'Apply' }}</button>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
          <p class="small muted">Apply opens the employer's page in a new tab and prepares your tailored resume and cover
            letter in the background (you can keep browsing; open Materials to see them). You submit the application
            yourself; nothing is sent for you.</p>
        } @else {
          <div class="empty" data-testid="no-results">No jobs matched. Try a shorter title, a longer date range, or another country.</div>
        }
      </div>
    }
  `,
})
export class FindJobsPage implements OnInit {
  private api = inject(Api);
  /** Filters, results and Apply progress live in a shared service, so they survive leaving this tab. */
  protected readonly state = inject(FindJobsState);

  protected readonly today = today();
  protected readonly presets = [
    { label: 'Last 24 hours', days: 1 }, { label: '3 days', days: 3 }, { label: '7 days', days: 7 },
    { label: '14 days', days: 14 }, { label: '30 days', days: 30 },
  ];
  protected readonly title = this.state.title;
  protected readonly country = this.state.country;
  protected readonly dateFrom = this.state.dateFrom;
  protected readonly dateTo = this.state.dateTo;
  protected readonly result = this.state.result;
  protected readonly countries = signal<CountryOption[]>([]);
  protected readonly busy = signal(false);
  protected readonly msg = signal<Msg | null>(null);

  async ngOnInit() {
    try {
      const c = await this.api.searchCountries();
      this.countries.set(c.countries);
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    }
    if (this.result()) return;                      // coming back: keep your last search as it was
    if (!this.country()) this.country.set('CA');
    try {   // a first visit starts from your own target role and country, if your profile has them
      const p = await this.api.profile() as any;
      const role = p?.preferences?.target_roles?.[0];
      if (role && !this.title()) this.title.set(role);
      const locs: string[] = (p?.preferences?.locations ?? []).map((x: string) => x.toLowerCase());
      const hit = this.countries().find(c => locs.some(l => l.includes(c.name.toLowerCase())));
      if (hit) this.country.set(hit.code);
    } catch { /* no profile yet: fine */ }
  }

  /** The preset that matches the chosen dates (highlighted), or null for custom dates. */
  protected activePreset(): number | null {
    if (this.dateTo() !== today()) return null;
    return this.presets.find(p => this.dateFrom() === daysAgo(p.days - 1))?.days ?? null;
  }

  preset(days: number) {
    this.dateFrom.set(daysAgo(days - 1));
    this.dateTo.set(today());
  }

  async search() {
    if (this.title().trim().length < 2) {
      this.msg.set({ kind: 'bad', text: 'Enter a job title.' });
      return;
    }
    this.busy.set(true);
    this.result.set(null);
    this.state.rows.set({});
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

  apply(j: FoundJob) {
    window.open(j.url, '_blank', 'noopener');          // must happen right in the click, or the browser blocks it
    this.state.apply(j);
  }
}
