import { KeyValuePipe } from '@angular/common';
import { Component, DestroyRef, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../../api';
import { AutoApplyQuestion, AutoApplyRun, AutoApplySetup, Doc } from '../../models';

const ACTIVE = new Set(['queued', 'filling', 'submitting']);
const LABELS: Record<string, string> = {
  first_name: 'First name', last_name: 'Last name', full_name: 'Full name', email: 'Email', phone: 'Phone',
  location: 'Location', country: 'Country', linkedin: 'LinkedIn', github: 'GitHub', website: 'Website',
};

@Component({
  selector: 'app-auto-apply',
  imports: [FormsModule, KeyValuePipe],
  template: `
    <details class="card" data-testid="auto-apply" (toggle)="toggle($event)">
      <summary><b>Auto-apply</b></summary>
      @if (error()) { <p class="warn-text" role="status">{{ error() }}</p> }
      @if (setup(); as s) {
        @if (!s.supported) { <p class="small muted">{{ s.reason }}</p> }
        @if (s.can_set_portal) {
          <details class="panel" [open]="!s.supported"><summary>Direct employer application link</summary>
            <label>Employer application URL
              <input type="url" [(ngModel)]="portalUrl" placeholder="Paste the direct employer posting or application URL">
            </label>
            <button type="button" class="secondary" [disabled]="busy() || !portalUrl.trim()" (click)="savePortal()">
              Save employer link</button>
          </details>
        }
        @if (s.supported) {
          <p><b>{{ s.job_title }} at {{ s.company }}</b></p>
          <a [href]="s.url" target="_blank" rel="noopener">Open employer posting</a>
          @if (run(); as r) {
            @if (r.state !== 'idle') {
              <div class="panel" role="status" data-testid="auto-apply-status">
                <b>{{ statusLabel(r.state) }}</b>
                <p>{{ r.message }}</p>
                @if (r.evidence) { <p class="small">{{ r.evidence }}</p> }
              </div>
            }
          }
          @if (!s.blocked && canEdit()) {
            @if (!materials('resume').length) {
              <p class="small muted">Generate a tailored résumé to auto-apply.</p>
            } @else {
              <div class="row">
                <label>Résumé to submit <select [ngModel]="selectedResume()" (ngModelChange)="resumeId.set($event)">
                  @for (d of materials('resume'); track d.id) { <option [ngValue]="d.id">Version {{ d.version }}</option> }
                </select></label>
                <label>Cover letter to submit <select [ngModel]="coverId()" (ngModelChange)="coverId.set($event)">
                  <option [ngValue]="null">Skip cover letter</option>
                  @for (d of materials('cover_letter'); track d.id) { <option [ngValue]="d.id">Version {{ d.version }}</option> }
                </select></label>
              </div>
              <div class="actions">
                @if (selectedResume(); as resume) { <a [href]="'/documents/' + resume + '/print'" target="_blank" rel="noopener">Review résumé</a> }
                @if (coverId()) { <a [href]="'/documents/' + coverId() + '/print'" target="_blank" rel="noopener">Review cover letter</a> }
              </div>
              <details class="panel"><summary>Profile details the app can send</summary>
                @for (f of s.contact | keyvalue; track f.key) {
                  <div class="small"><b>{{ labels[f.key] }}</b> · {{ f.value }}</div>
                }
              </details>
              @if (s.saved_answers?.length) {
                <details class="panel"><summary>Answers from your approved Greenhouse plan</summary>
                  @for (saved of s.saved_answers; track saved.name) {
                    <div class="small"><b>{{ saved.label }}</b> · {{ saved.value }}</div>
                  }
                </details>
              }
              @if (run()?.state === 'needs_you') {
                <div class="panel" data-testid="auto-apply-questions">
                  @for (q of run()?.questions ?? []; track q.key) {
                    @if (q.manual) { <p class="small"><b>{{ q.label }}</b> · Complete in the employer browser.</p> }
                    @else {
                      <label>{{ q.label }}
                        @if (q.options.length) {
                          <select [ngModel]="answers()[q.key] || ''" (ngModelChange)="answer(q.key, $event)">
                            <option value="">Choose an answer</option>
                            @for (option of q.options; track option) { <option [value]="option">{{ option }}</option> }
                          </select>
                        } @else {
                          <textarea [ngModel]="answers()[q.key] || ''" (ngModelChange)="answer(q.key, $event)" rows="2" maxlength="4000"></textarea>
                        }
                      </label>
                      @if (q.draftable) {
                        <button type="button" class="secondary tight" [disabled]="writing() !== null" (click)="draft(q)">
                          {{ writing() === q.key ? 'Drafting…' : 'Draft answer with AI' }}</button>
                        @for (warning of warnings()[q.key] ?? []; track $index) { <p class="small warn-text">{{ warning }}</p> }
                      }
                    }
                  }
                  <label class="small"><input type="checkbox" [(ngModel)]="reviewedBrowser">
                    I completed and checked the current employer page in the open browser.</label>
                </div>
              }
              <p class="small muted">The app can submit this application after approval. It pauses for account setup,
                verification, and employer questions that need your input.</p>
              <button type="button" class="primary" [disabled]="busy() || !!writing() || !selectedResume()" (click)="start()">
                {{ run()?.state === 'needs_you' ? 'Approve answers and resume auto-apply' : 'Approve and submit' }}</button>
            }
          }
          @if (canCancel()) {
            <button type="button" class="secondary" [disabled]="busy()" (click)="cancel()">Stop auto-apply</button>
          }
          @if (run()?.state === 'uncertain' || run()?.state === 'already_applied') {
            <button type="button" class="secondary" [disabled]="busy()" (click)="clearUnreceived()">
              I verified the employer did not receive it</button>
          }
        }
      } @else if (loading()) { <p class="small muted">Loading the application…</p> }
    </details>
  `,
})
export class AutoApplyPanel {
  private readonly api = inject(Api);
  readonly applicationId = input.required<number>();
  readonly documents = input<Doc[]>([]);
  readonly changed = output<void>();
  protected readonly setup = signal<AutoApplySetup | null>(null);
  protected readonly run = signal<AutoApplyRun | null>(null);
  protected readonly resumeId = signal<number | null>(null);
  protected readonly coverId = signal<number | null>(null);
  protected readonly answers = signal<Record<string, string>>({});
  protected readonly warnings = signal<Record<string, string[]>>({});
  protected readonly error = signal('');
  protected readonly busy = signal(false);
  protected readonly loading = signal(false);
  protected readonly writing = signal<string | null>(null);
  protected readonly labels = LABELS;
  protected reviewedBrowser = false;
  protected portalUrl = '';
  private open = false;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    effect(() => {
      const id = this.applicationId();
      this.stopPolling();
      this.setup.set(null);
      this.run.set(null);
      this.resumeId.set(null);
      this.coverId.set(null);
      this.reviewedBrowser = false;
      this.portalUrl = '';
      this.answers.set({});
      this.warnings.set({});
      this.error.set('');
      if (this.open) void this.load(id);
    });
    inject(DestroyRef).onDestroy(() => { this.open = false; this.stopPolling(); });
  }

  protected materials(kind: 'resume' | 'cover_letter') {
    const docs = this.documents().length ? this.documents() : this.setup()?.materials ?? [];
    return docs.filter(d => d.kind === kind).slice().sort((a, b) => b.version - a.version);
  }

  protected selectedResume() { return this.resumeId() ?? this.materials('resume')[0]?.id ?? null; }

  protected canEdit() { return !ACTIVE.has(this.run()?.state ?? '') && !['confirmed', 'uncertain', 'already_applied'].includes(this.run()?.state ?? ''); }
  protected canCancel() { return ['queued', 'filling', 'needs_you'].includes(this.run()?.state ?? ''); }
  protected statusLabel(state: string) {
    return ({ queued: 'Queued', filling: 'Filling the employer form', submitting: 'Submitting', needs_you: 'Needs your input',
      confirmed: 'Submitted and confirmed', uncertain: 'Submission needs verification', failed: 'Stopped',
      already_applied: 'Possible earlier application', interrupted: 'Interrupted', cancelled: 'Stopped' } as Record<string, string>)[state] ?? state;
  }

  protected toggle(event: Event) {
    this.open = (event.target as HTMLDetailsElement).open;
    if (this.open) void this.load(this.applicationId());
    else this.stopPolling();
  }

  private stopPolling() { if (this.timer) clearTimeout(this.timer); this.timer = null; }

  private watch() {
    this.stopPolling();
    if (this.open && ACTIVE.has(this.run()?.state ?? '')) {
      this.timer = setTimeout(() => void this.load(this.applicationId()), 1200);
    }
  }

  private async load(id: number) {
    this.loading.set(true);
    try {
      const s = await this.api.autoApplySetup(id);
      if (id !== this.applicationId() || !this.open) return;
      const before = this.run()?.state;
      this.setup.set(s);
      this.run.set(s.run ?? null);
      if (!this.resumeId()) {
        this.resumeId.set(s.run?.resume_id ?? this.materials('resume')[0]?.id ?? null);
        this.coverId.set(s.run?.cover_letter_id ?? null);
      }
      if (before && ACTIVE.has(before) && !ACTIVE.has(s.run?.state ?? '')) this.changed.emit();
      this.watch();
    } catch (e) { if (id === this.applicationId() && this.open) this.error.set((e as ApiError).message); }
    finally { if (id === this.applicationId()) this.loading.set(false); }
  }

  protected answer(key: string, value: string) { this.answers.update(a => ({ ...a, [key]: value })); }

  protected async savePortal() {
    const id = this.applicationId();
    this.error.set('');
    this.busy.set(true);
    try {
      const s = await this.api.setAutoApplyPortal(id, this.portalUrl.trim());
      if (id === this.applicationId()) {
        this.portalUrl = '';
        this.setup.set(s);
        this.run.set(s.run ?? null);
      }
    } catch (e) { if (id === this.applicationId()) this.error.set((e as ApiError).message); }
    finally { if (id === this.applicationId()) this.busy.set(false); }
  }

  protected async start() {
    const id = this.applicationId(), preview = this.setup()?.preview;
    const resumeId = this.selectedResume();
    if (!preview || !resumeId || this.busy()) return;
    const pending = new Set(this.run()?.questions.filter(q => !q.manual).map(q => q.key) ?? []);
    const answers = Object.fromEntries(Object.entries(this.answers()).filter(([key, value]) => pending.has(key) && !!value.trim()));
    this.busy.set(true);
    this.error.set('');
    try {
      const run = await this.api.autoApply(id, { preview, resume_id: resumeId, cover_letter_id: this.coverId(),
        answers, reviewed_in_browser: this.reviewedBrowser });
      if (id !== this.applicationId()) return;
      this.run.set(run);
      this.reviewedBrowser = false;
      this.watch();
    } catch (e) { if (id === this.applicationId()) this.error.set((e as ApiError).message); }
    finally { if (id === this.applicationId()) this.busy.set(false); }
  }

  protected async cancel() {
    const id = this.applicationId();
    this.busy.set(true);
    this.error.set('');
    try {
      const run = await this.api.cancelAutoApply(id);
      if (id === this.applicationId()) { this.run.set(run); this.reviewedBrowser = false; this.watch(); }
    } catch (e) { if (id === this.applicationId()) this.error.set((e as ApiError).message); }
    finally { if (id === this.applicationId()) this.busy.set(false); }
  }

  protected async clearUnreceived() {
    const id = this.applicationId();
    this.busy.set(true);
    this.error.set('');
    try {
      await this.api.reviewAutoApply(id);
      if (id === this.applicationId()) { await this.load(id); this.changed.emit(); }
    } catch (e) { if (id === this.applicationId()) this.error.set((e as ApiError).message); }
    finally { if (id === this.applicationId()) this.busy.set(false); }
  }

  protected async draft(q: AutoApplyQuestion) {
    const id = this.applicationId();
    this.writing.set(q.key);
    this.error.set('');
    try {
      const d = await this.api.draft(id, { kind: 'screening_answer', question: q.label });
      if (id === this.applicationId()) {
        this.answer(q.key, d.content);
        this.warnings.update(w => ({ ...w, [q.key]: d.review?.warnings ?? [] }));
      }
    } catch (e) { if (id === this.applicationId()) this.error.set((e as ApiError).message); }
    finally { if (id === this.applicationId()) this.writing.set(null); }
  }
}
