import { KeyValuePipe } from '@angular/common';
import { Component, DestroyRef, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../../api';
import { AutoApplyQuestion, AutoApplyRun, AutoApplyScreening, AutoApplySetup, Doc } from '../../models';

const ACTIVE = new Set(['queued', 'filling', 'submitting']);
const LABELS: Record<string, string> = {
  first_name: 'First name', last_name: 'Last name', full_name: 'Full name', email: 'Email', phone: 'Phone',
  location: 'Location', country: 'Country', current_company: 'Current company', linkedin: 'LinkedIn', github: 'GitHub', website: 'Website',
};

@Component({
  selector: 'app-auto-apply',
  imports: [FormsModule, KeyValuePipe],
  template: `
    <details class="card" data-testid="auto-apply" (toggle)="toggle($event)">
      <summary><b>Auto-apply</b></summary>
      @if (error()) { <p class="warn-text" role="status">{{ error() }}</p> }
      @if (notice()) { <p class="small" role="status">{{ notice() }}</p> }
      @if (setup(); as s) {
        @if (!s.supported) { <p class="small muted">{{ s.reason }}</p> }
        @if (s.manual_url) { <a [href]="s.manual_url" target="_blank" rel="noopener">Open application form</a> }
        @if (s.can_set_portal) {
          <details class="panel" [open]="!s.supported && !s.manual_url"><summary>Direct employer application link</summary>
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
                @if (r.uploaded?.includes('resume')) { <p class="small">Résumé attached</p> }
                @if (r.uploaded?.includes('cover_letter')) { <p class="small">Cover letter attached</p> }
                @if (r.evidence) { <p class="small">{{ r.evidence }}</p> }
              </div>
            }
          }
          @if (!s.blocked && canEdit()) {
            @if (!materials('resume').length) {
              <p class="small muted">Generate a tailored résumé to auto-apply.</p>
            } @else {
              <div class="row">
                <label>Résumé to submit <select [ngModel]="selectedResume()" (ngModelChange)="chooseResume($event)">
                  @for (d of materials('resume'); track d.id) { <option [ngValue]="d.id">Version {{ d.version }}</option> }
                </select></label>
                @if (screening()?.cover_letter === false) {
                  <p class="small muted">The résumé is the available application document upload.</p>
                } @else {
                  <label>Cover letter to submit <select [ngModel]="selectedCover()" (ngModelChange)="coverId.set($event)">
                    <option [ngValue]="null">Skip cover letter</option>
                    @for (d of materials('cover_letter'); track d.id) { <option [ngValue]="d.id">Version {{ d.version }}</option> }
                  </select></label>
                }
              </div>
              <div class="actions">
                @if (selectedResume(); as resume) { <a [href]="'/documents/' + resume + '/print'" target="_blank" rel="noopener">Review résumé</a> }
                @if (selectedCover(); as cover) { <a [href]="'/documents/' + cover + '/print'" target="_blank" rel="noopener">Review cover letter</a> }
              </div>
              <details class="panel"><summary>Contact details from the selected résumé and profile</summary>
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
              @if (screening(); as review) {
                @if (review.warning) { <p class="small muted">{{ review.warning }} The app can read them in the employer browser.</p> }
                @if (review.questions.length) {
                  <div class="panel" data-testid="auto-apply-screening">
                    <h4>Review employer screening answers</h4>
                    @for (q of review.questions; track q.key) {
                      @if (q.manual) { <p class="small"><b>{{ q.label }}</b> · Complete in the employer browser.</p> }
                      @else {
                        <label>{{ q.label }}{{ q.required ? ' *' : '' }}
                          @if (q.options.length) {
                            @if (q.multiple && !isClearance(q)) {
                              <select multiple [ngModel]="screenSelections(q)" (ngModelChange)="setScreening(q.key, $event.join('; '))">
                                @for (option of q.options; track option) { <option [value]="option">{{ option }}</option> }
                              </select>
                            } @else {
                              <select [ngModel]="screenValue(q)" (ngModelChange)="setScreening(q.key, $event)">
                                <option value="">Choose an answer</option>
                                @for (option of q.options; track option) { <option [value]="option">{{ option }}</option> }
                              </select>
                            }
                          } @else {
                            <textarea rows="2" maxlength="4000" [ngModel]="screenValue(q)"
                                      (ngModelChange)="setScreening(q.key, $event)"></textarea>
                          }
                        </label>
                        @if (q.source && screeningAnswers()[q.key] === undefined) { <div class="small muted">{{ q.source }}</div> }
                        @for (warning of warnings()[q.key] ?? []; track $index) { <p class="small warn-text">{{ warning }}</p> }
                      }
                    }
                    @if (needsDrafts().length && !triedDrafts()) {
                      <p class="small muted">The app drafts eligible written answers using your experience. Review them before sending.</p>
                    }
                    @if (missingScreening().length) {
                      <p class="small muted">Choose or enter the required answers before submitting.</p>
                    }
                  </div>
                }
              } @else if (screeningLoading()) { <p class="small muted">Loading employer screening questions…</p> }
              @if (run()?.state === 'needs_you') {
                <div class="panel" data-testid="auto-apply-questions">
                  @for (q of browserQuestions(); track q.key) {
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
              <button type="button" class="primary" [disabled]="busy() || !!writing() || loading() || screeningLoading() || !selectedResume() ||
                      (!shouldDraft() && missingScreening().length > 0 && !reviewedBrowser)" (click)="start()">
                {{ shouldDraft() ? 'Draft screening answers for review' : run()?.state === 'needs_you' ? 'Approve answers and resume auto-apply' : 'Approve and submit' }}</button>
              @if (shouldDraft()) {
                <button type="button" class="secondary" [disabled]="busy()" (click)="triedDrafts.set(true)">Skip optional drafts</button>
              }
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
  protected readonly coverId = signal<number | null | undefined>(undefined);
  protected readonly answers = signal<Record<string, string>>({});
  protected readonly screening = signal<AutoApplyScreening | null>(null);
  protected readonly screeningAnswers = signal<Record<string, string>>({});
  protected readonly screeningLoading = signal(false);
  protected readonly triedDrafts = signal(false);
  protected readonly notice = signal('');
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
      this.coverId.set(undefined);
      this.reviewedBrowser = false;
      this.portalUrl = '';
      this.answers.set({});
      this.screening.set(null);
      this.screeningAnswers.set({});
      this.triedDrafts.set(false);
      this.notice.set('');
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

  protected selectedCover() {
    if (this.screening()?.cover_letter === false) return null;
    const selected = this.coverId();
    if (selected !== undefined) return selected;
    return this.screening()?.cover_letter ? this.materials('cover_letter')[0]?.id ?? null : null;
  }

  protected chooseResume(id: number) {
    this.resumeId.set(id);
    this.screening.set(null);
    void this.load(this.applicationId());
  }

  protected screenValue(q: AutoApplyQuestion) { return this.screeningAnswers()[q.key] ?? q.suggested ?? ''; }
  protected screenSelections(q: AutoApplyQuestion) { return this.screenValue(q).split(';').map(v => v.trim()).filter(Boolean); }
  protected isClearance(q: AutoApplyQuestion) { return /security clearance/i.test(q.label); }
  protected needsDrafts() { return (this.screening()?.questions ?? []).filter(q => q.draftable && !this.screenValue(q).trim()); }
  protected shouldDraft() { return !this.triedDrafts() && this.needsDrafts().length > 0; }
  protected missingScreening() {
    return (this.screening()?.questions ?? []).filter(q => q.required && !q.manual && !this.screenValue(q).trim());
  }

  protected browserQuestions() {
    const label = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
    const names = new Set((this.screening()?.questions ?? []).map(q => label(q.label)));
    return (this.run()?.questions ?? []).filter(q => !names.has(label(q.label)));
  }

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
      const requested = this.resumeId();
      const s = await this.api.autoApplySetup(id, requested);
      if (id !== this.applicationId() || !this.open || (requested !== null && requested !== this.resumeId())) return;
      const before = this.run()?.state;
      this.setup.set(s);
      this.run.set(s.run ?? null);
      if (!this.resumeId()) {
        this.resumeId.set(s.run?.resume_id ?? this.materials('resume')[0]?.id ?? null);
        if (s.run?.resume_id) this.coverId.set(s.run.cover_letter_id);
      }
      if (!this.screening() && s.supported && s.provider === 'greenhouse' && !s.blocked && this.canEdit()) {
        await this.loadScreening(id);
      }
      if (before && ACTIVE.has(before) && !ACTIVE.has(s.run?.state ?? '')) this.changed.emit();
      this.watch();
    } catch (e) { if (id === this.applicationId() && this.open) this.error.set((e as ApiError).message); }
    finally { if (id === this.applicationId()) this.loading.set(false); }
  }

  protected answer(key: string, value: string) { this.answers.update(a => ({ ...a, [key]: value })); }

  protected setScreening(key: string, value: string) { this.screeningAnswers.update(a => ({ ...a, [key]: value })); }

  private async loadScreening(id: number) {
    const resumeId = this.selectedResume();
    this.screeningLoading.set(true);
    try {
      const result = await this.api.autoApplyScreening(id, resumeId);
      if (id === this.applicationId() && resumeId === this.selectedResume()) this.screening.set(result);
    } catch {
      if (id === this.applicationId() && resumeId === this.selectedResume()) {
        this.screening.set({ questions: [], cover_letter: null, warning: 'The employer’s questions could not be loaded in advance.' });
      }
    } finally { if (id === this.applicationId() && resumeId === this.selectedResume()) this.screeningLoading.set(false); }
  }

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
        this.screening.set(null);
        this.screeningAnswers.set({});
        this.triedDrafts.set(false);
        if (s.provider === 'greenhouse') await this.loadScreening(id);
      }
    } catch (e) { if (id === this.applicationId()) this.error.set((e as ApiError).message); }
    finally { if (id === this.applicationId()) this.busy.set(false); }
  }

  protected async start() {
    const id = this.applicationId(), preview = this.setup()?.preview;
    const resumeId = this.selectedResume();
    if (!preview || !resumeId || this.busy()) return;
    if (this.shouldDraft()) {
      this.triedDrafts.set(true);
      for (const question of this.needsDrafts().slice(0, 4)) {
        if (id !== this.applicationId()) return;
        await this.draft(question, true);
      }
      if (id === this.applicationId()) this.notice.set('Review the drafted answers and complete any required questions before approving.');
      return;
    }
    if (this.missingScreening().length && !this.reviewedBrowser) return;
    const pending = new Set(this.browserQuestions().filter(q => !q.manual).map(q => q.key));
    const answers = Object.fromEntries(Object.entries(this.answers()).filter(([key, value]) => pending.has(key) && !!value.trim()));
    const screening = this.screening()?.questions.filter(q => !q.manual);
    const screeningAnswers = screening?.length ? Object.fromEntries(screening.map(q => [q.key, this.screenValue(q)])) : undefined;
    this.busy.set(true);
    this.error.set('');
    this.notice.set('');
    try {
      const run = await this.api.autoApply(id, { preview, resume_id: resumeId, cover_letter_id: this.selectedCover(),
        answers, screening_answers: screeningAnswers, reviewed_in_browser: this.reviewedBrowser });
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

  protected async draft(q: AutoApplyQuestion, screening = false) {
    const id = this.applicationId();
    this.writing.set(q.key);
    this.error.set('');
    try {
      const d = await this.api.draft(id, { kind: 'screening_answer', question: q.label });
      if (id === this.applicationId()) {
        if (screening) this.setScreening(q.key, d.content);
        else this.answer(q.key, d.content);
        this.warnings.update(w => ({ ...w, [q.key]: d.review?.warnings ?? [] }));
      }
    } catch (e) { if (id === this.applicationId()) this.error.set((e as ApiError).message); }
    finally { if (id === this.applicationId()) this.writing.set(null); }
  }
}
