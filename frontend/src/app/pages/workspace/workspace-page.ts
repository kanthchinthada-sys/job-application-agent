import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { Api, ApiError } from '../../api';
import { Application, Job, pretty } from '../../models';
import { ActivityTab } from './activity-tab';
import { DocumentsTab } from './documents-tab';
import { MatchTab } from './match-tab';

type Tab = 'documents' | 'match' | 'activity';

/** One application's own page: the job at the top, then Documents, Job & skill match, and Activity & notes. */
@Component({
  selector: 'app-workspace-page',
  imports: [FormsModule, RouterLink, DocumentsTab, MatchTab, ActivityTab],
  template: `
    <div class="page-head" data-testid="workspace">
      <a class="back" routerLink="/applications" data-testid="back">← Back to applications</a>
      @if (app(); as a) {
        <h2 data-testid="workspace-title">{{ a.job_title }}</h2>
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
        @if (statusMsg(); as m) { <div class="msg bad">{{ m }}</div> }
        @if (a.submission_confirmation) { <div class="small muted">Confirmation: {{ a.submission_confirmation }}</div> }
        @if (confirming()) {
          <div class="panel small">
            <label for="proof">Confirmation evidence (required to mark it submitted)</label>
            <input id="proof" [(ngModel)]="proof" placeholder="Confirmation number, email subject, …">
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
        <button role="tab" type="button" [attr.aria-selected]="current() === 'activity'" (click)="go('activity')">Activity &amp; notes</button>
      </div>
      @switch (current()) {
        @case ('documents') { <app-documents-tab [application]="a" [docId]="docId()" (changed)="reload()" /> }
        @case ('match') { @if (job(); as j) { <app-match-tab [job]="j" /> } }
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
  private pendingSelect: HTMLSelectElement | null = null;

  protected readonly current = computed<Tab>(() =>
    (['documents', 'match', 'activity'] as Tab[]).includes(this.tab() as Tab) ? this.tab() as Tab : 'documents');
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
        if (id) this.load(id);
      });
    });
  }

  private async load(id: number) {
    try {
      const a = await this.api.application(id);
      this.app.set(a);
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
      return;
    }
    await this.update({ status });
    select.value = this.app()?.status ?? select.value;
  }

  async confirmSubmitted() {
    if (!this.proof().trim()) { this.statusMsg.set('Paste the confirmation evidence first.'); return; }
    await this.update({ status: 'submitted_confirmed', submission_confirmation: this.proof().trim() });
    this.pendingSelect = null;
    this.confirming.set(false);
  }

  cancelConfirm() {
    if (this.pendingSelect) this.pendingSelect.value = this.app()!.status;
    this.pendingSelect = null;
    this.confirming.set(false);
  }

  private async update(body: { status?: string; submission_confirmation?: string }) {
    try {
      this.app.set(await this.api.updateApplication(this.app()!.id, body));
    } catch (e) {
      this.statusMsg.set((e as ApiError).message);
      await this.reload();
    }
  }
}
