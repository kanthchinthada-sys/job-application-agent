import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { Api, ApiError } from '../../api';
import { Application, Doc, Job, pretty } from '../../models';
import { ActivityTab } from './activity-tab';
import { DocumentsTab } from './documents-tab';
import { MatchTab } from './match-tab';
import { InterviewTab } from './interview-tab';

type Tab = 'documents' | 'match' | 'activity' | 'interview';

/** One application's own page: the job at the top, then Documents, Job & skill match, and Activity & notes. */
@Component({
  selector: 'app-workspace-page',
  imports: [FormsModule, RouterLink, DocumentsTab, MatchTab, ActivityTab, InterviewTab],
  template: `
    <div class="page-head" data-testid="workspace">
      <a class="back" routerLink="/applications" data-testid="back">← Back to applications</a>
      @if (app(); as a) {
        <h2 data-testid="workspace-title">{{ a.job_title }}</h2>
        @if (editingTitle()) {
          <div class="panel small">
            <label for="workspace-job-title">Job title</label>
            <input id="workspace-job-title" maxlength="300" [(ngModel)]="jobTitle" (keyup.enter)="saveTitle()">
            <div class="actions">
              <button type="button" class="secondary" [disabled]="savingTitle() || !jobTitle.trim()" (click)="saveTitle()">Save job title</button>
              <button type="button" class="link-btn" [disabled]="savingTitle()" (click)="editingTitle.set(false)">Cancel</button>
            </div>
            @if (titleError()) { <p role="alert">{{ titleError() }}</p> }
          </div>
        } @else {
          <button type="button" class="link-btn small" (click)="editTitle(a.job_title)">Edit job title</button>
        }
        <div class="meta">
          <span><b>{{ a.company }}</b></span>
          @if (job()?.location) { <span>· {{ job()!.location }}</span> }
          @if (a.source_url) { <span>· <a [href]="a.source_url" target="_blank" rel="noopener">Original posting ↗</a></span> }
          <span>·</span>
          <label class="inline" for="app-status" style="margin:0">Application status</label>
          <select id="app-status" style="width:auto" [value]="a.status" #sel (change)="changeStatus(sel)">
            @for (s of statusChoices(); track s) { <option [value]="s" [selected]="s === a.status">{{ pretty(s) }}</option> }
          </select>
        </div>
        @if (job()?.pending_technical_gaps && current() !== 'match') {
          <button class="link-btn" type="button" (click)="go('match')">
            Review {{ job()!.pending_technical_gaps }} technical gap suggestions</button>
        }
        @if (statusMsg(); as m) { <div class="msg bad">{{ m }}</div> }
        @if (a.submission_confirmation) { <div class="small muted">Confirmation: {{ a.submission_confirmation }}</div> }
        @if (a.submitted_resume_id || a.submitted_cover_letter_id) {
          <p class="small">
            @if (a.submitted_resume_id) { <a [href]="'/documents/' + a.submitted_resume_id + '/print'" target="_blank" rel="noopener">Submitted resume</a> }
            @if (a.submitted_cover_letter_id) { · <a [href]="'/documents/' + a.submitted_cover_letter_id + '/print'" target="_blank" rel="noopener">Submitted cover letter</a> }
          </p>
        }
        <details class="panel">
          <summary><b>Next action and interview</b> @if (a.next_action) { · {{ a.next_action }} }</summary>
          <div class="row">
            <div><label for="next-action">Next action</label><input id="next-action" maxlength="300" [(ngModel)]="nextAction"></div>
            <div><label for="action-due">Due date</label><input id="action-due" type="date" [(ngModel)]="dueDate"></div>
            <div><label for="interview-at">Interview date and time</label><input id="interview-at" type="datetime-local" [(ngModel)]="interviewAt"></div>
          </div>
          <button class="secondary" type="button" (click)="saveActions()">Save next action</button>
          @if (actionMessage()) { <span role="status" class="small"> {{ actionMessage() }}</span> }
          @if (a.submission_confirmation) {
            <button class="link-btn" type="button" (click)="recordVersions()">Record submitted files</button>
          }
        </details>
        @if (confirming()) {
          <div class="panel small">
            <label for="proof">Confirmation evidence (required to mark it submitted)</label>
            <input id="proof" [(ngModel)]="proof" placeholder="Confirmation number, email subject, …">
            @if (submissionDocs().length) {
              <label>Resume actually sent <select [(ngModel)]="sentResume">
                <option [ngValue]="null">Unknown / external file</option>
                @for (d of versions('resume'); track d.id) { <option [ngValue]="d.id">Version {{ d.version }}</option> }
              </select></label>
              <label>Cover letter actually sent <select [(ngModel)]="sentLetter">
                <option [ngValue]="null">None / unknown / external file</option>
                @for (d of versions('cover_letter'); track d.id) { <option [ngValue]="d.id">Version {{ d.version }}</option> }
              </select></label>
            }
            <div class="actions"><button class="secondary" type="button" (click)="confirmSubmitted()">Mark submitted (confirmed)</button>
              <button class="secondary" type="button" (click)="cancelConfirm()">Cancel</button></div>
          </div>
        }
      } @else if (error()) {
        <div class="msg bad">{{ error() }}</div>
      } @else {
        <p class="muted"><span class="spinner"></span> Loading…</p>
      }
    </div>

    @if (app(); as a) {
      <div class="tabs" role="tablist" aria-label="Application sections">
        <button role="tab" type="button" [attr.aria-selected]="current() === 'documents'" (click)="go('documents')">Documents</button>
        <button role="tab" type="button" [attr.aria-selected]="current() === 'match'" (click)="go('match')">Job &amp; skill match</button>
        <button role="tab" type="button" [attr.aria-selected]="current() === 'interview'" (click)="go('interview')">Interview prep</button>
        <button role="tab" type="button" [attr.aria-selected]="current() === 'activity'" (click)="go('activity')">Activity &amp; notes</button>
      </div>
      @switch (current()) {
        @case ('documents') { <app-documents-tab [application]="a" [docId]="docId()" (changed)="reload()" /> }
        @case ('match') { @if (job(); as j) { <app-match-tab [job]="j" /> } }
        @case ('interview') { <app-interview-tab [appId]="a.id" /> }
        @case ('activity') { <app-activity-tab [appId]="a.id" (openDoc)="openDoc($event)" (changed)="reload()" /> }
      }
    }
  `,
})
export class WorkspacePage {
  private api = inject(Api);
  private router = inject(Router);

  /** From the URL: /applications/:id?tab=match&doc=12 */
  readonly id = input<string>();
  readonly tab = input<string>();
  readonly doc = input<string>();

  protected readonly app = signal<Application | null>(null);
  protected readonly job = signal<Job | null>(null);
  protected readonly error = signal('');
  protected readonly statusMsg = signal('');
  protected readonly confirming = signal(false);
  protected readonly proof = signal('');
  protected readonly pretty = pretty;
  protected readonly submissionDocs = signal<Doc[]>([]);
  protected readonly actionMessage = signal('');
  protected readonly editingTitle = signal(false);
  protected readonly savingTitle = signal(false);
  protected readonly titleError = signal('');
  protected jobTitle = '';
  protected nextAction = '';
  protected dueDate = '';
  protected interviewAt = '';
  protected sentResume: number | null = null;
  protected sentLetter: number | null = null;
  private pendingSelect: HTMLSelectElement | null = null;

  protected readonly current = computed<Tab>(() =>
    (['documents', 'match', 'activity', 'interview'] as Tab[]).includes(this.tab() as Tab) ? this.tab() as Tab : 'documents');
  protected readonly docId = computed(() => (this.doc() ? Number(this.doc()) : null));
  protected readonly statusChoices = computed(() => {
    const a = this.app();
    return a ? [a.status, ...a.allowed_next.filter(s => s !== a.status)] : [];
  });

  constructor() {
    effect(() => {
      const id = Number(this.id());
      untracked(() => {
        window.scrollTo(0, 0);                       // a new application opens at the top
        this.app.set(null); this.job.set(null); this.error.set('');
        this.editingTitle.set(false);
        if (id) this.load(id);
      });
    });
  }

  private async load(id: number) {
    try {
      const a = await this.api.application(id);
      this.app.set(a);
      this.nextAction = a.next_action ?? '';
      this.dueDate = a.due_date ?? '';
      const interview = a.interview_at ? new Date(a.interview_at) : null;
      this.interviewAt = interview ? new Date(interview.getTime() - interview.getTimezoneOffset() * 60000)
        .toISOString().slice(0, 16) : '';
      this.actionMessage.set('');
      this.job.set(await this.api.job(a.job_id));
    } catch (e) {
      this.error.set((e as ApiError).status === 404 ? 'This application was not found in your workspace.'
        : (e as ApiError).message);
    }
  }

  async reload() {
    const a = this.app();
    if (!a) return;
    try { this.app.set(await this.api.application(a.id)); } catch { /* keep what is shown */ }
  }

  protected editTitle(title: string) {
    this.jobTitle = title;
    this.titleError.set('');
    this.editingTitle.set(true);
  }

  protected async saveTitle() {
    const application = this.app();
    const title = this.jobTitle.trim();
    if (!application || !title || this.savingTitle()) return;
    this.savingTitle.set(true);
    this.titleError.set('');
    try {
      const job = await this.api.updateJob(application.job_id, { title });
      if (this.app()?.id === application.id) {
        this.job.set(job);
        this.app.update(current => current ? { ...current, job_title: job.title } : current);
        this.editingTitle.set(false);
      }
    } catch (e) {
      this.titleError.set((e as ApiError).message);
    } finally {
      this.savingTitle.set(false);
    }
  }

  go(tab: Tab) {
    this.router.navigate([], { queryParams: { tab: tab === 'documents' ? null : tab, doc: null }, queryParamsHandling: 'merge' });
  }

  openDoc(docId: number) {
    this.router.navigate([], { queryParams: { tab: null, doc: docId }, queryParamsHandling: 'merge' });
  }

  async changeStatus(select: HTMLSelectElement) {
    const a = this.app()!;
    const status = select.value;
    this.statusMsg.set('');
    if (status === a.status) return;
    if (status === 'submitted_confirmed') {          // never without evidence
      this.proof.set('');
      this.pendingSelect = select;
      this.confirming.set(true);
      if (a.documents_ready?.length) void this.loadSubmissionDocs(a);
      return;
    }
    await this.update({ status });
    select.value = this.app()?.status ?? select.value;
  }

  async confirmSubmitted() {
    if (!this.proof().trim()) { this.statusMsg.set('Paste the confirmation evidence first.'); return; }
    if (await this.update({ status: 'submitted_confirmed', submission_confirmation: this.proof().trim(),
          ...(this.submissionDocs().length ? { submitted_resume_id: this.sentResume,
              submitted_cover_letter_id: this.sentLetter } : {}) })) {
      this.pendingSelect = null;
      this.confirming.set(false);
    }
  }

  cancelConfirm() {
    if (this.pendingSelect) this.pendingSelect.value = this.app()!.status;
    this.pendingSelect = null;
    this.confirming.set(false);
  }

  protected versions(kind: string) { return this.submissionDocs().filter(d => d.kind === kind); }

  private async loadSubmissionDocs(a: Application) {
    this.sentResume = a.submitted_resume_id ?? null;
    this.sentLetter = a.submitted_cover_letter_id ?? null;
    this.submissionDocs.set([]);
    try { this.submissionDocs.set(await this.api.documents(a.id)); }
    catch (e) { this.statusMsg.set((e as ApiError).message); }
  }

  protected recordVersions() {
    const a = this.app();
    if (!a) return;
    this.proof.set(a.submission_confirmation ?? '');
    this.confirming.set(true);
    void this.loadSubmissionDocs(a);
  }

  protected async saveActions() {
    this.actionMessage.set('');
    const ok = await this.update({ next_action: this.nextAction.trim() || null, due_date: this.dueDate || null,
      interview_at: this.interviewAt ? new Date(this.interviewAt).toISOString() : null });
    if (ok) this.actionMessage.set('Saved.');
  }

  private async update(body: Parameters<Api['updateApplication']>[1]) {
    try {
      this.app.set(await this.api.updateApplication(this.app()!.id, body));
      return true;
    } catch (e) {
      this.statusMsg.set((e as ApiError).message);
      await this.reload();
      return false;
    }
  }
}
