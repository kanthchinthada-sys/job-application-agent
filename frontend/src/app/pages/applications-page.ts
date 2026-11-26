import { DatePipe } from '@angular/common';
import { Component, computed, inject, OnDestroy, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { Api, ApiError } from '../api';
import { ApplicationsListState } from '../applications-list-state';
import { today } from '../find-jobs-state';
import { Application, pretty } from '../models';

export const STATUS_ORDER = ['preparing', 'ready_for_review', 'submitted_confirmed', 'submission_uncertain',
  'interviewing', 'offer', 'rejected', 'withdrawn'];

/** Every application as one compact row: job, company, status, documents, last activity, Open. */
@Component({
  selector: 'app-applications-page',
  imports: [FormsModule, DatePipe, RouterLink],
  template: `
    <div class="card">
      <h2>Applications</h2>
      @if (todayRows().length) {
        <section class="panel" aria-label="Today">
          <h3>Today</h3>
          @for (a of todayRows(); track a.id) {
            <div class="actions">
              <button class="link-btn" type="button" (click)="open(a.id)">{{ a.job_title }} · {{ a.company }}</button>
              @if (a.due_date) {
                <span class="small" [class.warn-text]="a.due_date < todayDate">{{ a.due_date < todayDate ? 'Overdue' : a.due_date === todayDate ? 'Due today' : a.due_date }} · {{ a.next_action || 'Follow up' }}</span>
              }
              @if (a.interview_at && a.interview_at >= nowIso) {
                <span class="small">Interview {{ a.interview_at | date:'MMM d, h:mm a' }}</span>
              }
              @if (readyToSubmit(a)) { <span class="small badge ok">Ready to review and submit</span> }
            </div>
          }
        </section>
      }
      <div class="toolbar">
        <div><label for="a-search">Search by job title or company</label>
          <input id="a-search" type="search" [(ngModel)]="state.search" placeholder="e.g. Acme or QA Lead"></div>
        <div style="flex:0 1 220px"><label for="a-status">Status</label>
          <select id="a-status" [(ngModel)]="state.status">
            <option value="">All statuses</option>
            @for (s of statuses(); track s) { <option [value]="s">{{ pretty(s) }}</option> }
          </select></div>
      </div>
      @if (apps() === null) {
        <p class="muted"><span class="spinner"></span> Loading…</p>
      } @else if (apps()!.length === 0) {
        <div class="empty">No applications yet. Open a job in <a routerLink="/jobs">Saved jobs</a> and click
          “Prepare application”, or <a routerLink="/add">add a job</a>.</div>
      } @else if (shown().length === 0) {
        <div class="empty">No application matches “{{ state.search() }}”{{ state.status() ? ' with status ' + pretty(state.status()) : '' }}.</div>
      } @else {
        <div class="table-wrap">
        <table data-testid="applications-table">
          <thead><tr><th>Job</th><th>Company</th><th>Status</th><th class="hide-narrow">Documents</th>
            <th class="hide-narrow">Last activity</th><th></th></tr></thead>
          <tbody>
            @for (a of shown(); track a.id) {
              <tr>
                <td><b>{{ a.job_title }}</b></td>
                <td>{{ a.company }}</td>
                <td><span class="badge" [class.ok]="['submitted_confirmed', 'interviewing', 'offer'].includes(a.status)"
                          [class.warn]="a.status === 'submission_uncertain'">{{ pretty(a.status) }}</span></td>
                <td class="small hide-narrow">
                  @for (d of a.documents_ready ?? []; track d) { <span class="badge ok">{{ d === 'resume' ? 'Resume ready' : 'Cover letter ready' }}</span> }
                  @if (!(a.documents_ready ?? []).length) { <span class="muted">No documents yet</span> }
                </td>
                <td class="small muted hide-narrow">{{ (a.last_activity ?? a.updated_at) | date: 'MMM d, y, h:mm a' }}</td>
                <td><button class="secondary tight" type="button" (click)="open(a.id)" [attr.aria-label]="'Open ' + a.job_title + ' at ' + a.company">Open</button></td>
              </tr>
            }
          </tbody>
        </table>
        </div>
      }
      @if (error(); as e) { <div class="msg bad">{{ e }}</div> }
    </div>
  `,
})
export class ApplicationsPage implements OnInit, OnDestroy {
  private api = inject(Api);
  private router = inject(Router);
  protected readonly state = inject(ApplicationsListState);

  protected readonly apps = signal<Application[] | null>(null);
  protected readonly error = signal('');
  protected readonly pretty = pretty;
  protected readonly todayDate = today();
  protected readonly nowIso = new Date().toISOString();
  protected readonly todayRows = computed(() => (this.apps() ?? []).filter(a =>
    !['offer', 'rejected', 'withdrawn'].includes(a.status) &&
    (!!a.due_date && a.due_date <= this.todayDate || !!a.interview_at && a.interview_at >= this.nowIso || this.readyToSubmit(a)))
    .sort((a, b) => (a.due_date ?? a.interview_at ?? '9999').localeCompare(b.due_date ?? b.interview_at ?? '9999')));
  protected readyToSubmit(a: Application) {
    return a.status === 'ready_for_review' ||
      a.status === 'preparing' && ['resume', 'cover_letter'].every(kind => a.documents_ready?.includes(kind));
  }
  protected readonly statuses = computed(() => {
    const used = new Set((this.apps() ?? []).map(a => a.status));
    return [...STATUS_ORDER.filter(s => used.has(s)), ...[...used].filter(s => !STATUS_ORDER.includes(s))];
  });
  protected readonly shown = computed(() => {
    const q = this.state.search().trim().toLowerCase();
    const st = this.state.status();
    return (this.apps() ?? [])
      .filter(a => !q || a.job_title.toLowerCase().includes(q) || a.company.toLowerCase().includes(q))
      .filter(a => !st || a.status === st)
      .sort((a, b) => String(b.last_activity ?? b.updated_at ?? '').localeCompare(String(a.last_activity ?? a.updated_at ?? '')));
  });

  async ngOnInit() {
    try {
      this.apps.set(await this.api.applications());
    } catch (e) {
      this.error.set((e as ApiError).message);
      this.apps.set([]);
    }
    const y = this.state.scrollY;
    if (y) setTimeout(() => window.scrollTo(0, y));          // back from an application: where you were
  }

  ngOnDestroy() { /* scroll is saved in open() */ }

  open(id: number) {
    this.state.scrollY = window.scrollY;
    this.router.navigate(['/applications', id]);
  }
}
