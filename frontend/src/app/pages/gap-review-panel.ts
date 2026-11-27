import { Component, computed, DestroyRef, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { Api, ApiError } from '../api';
import { GapSuggestion } from '../models';

/** What you are about to add for one gap: the AI's line (or your own), where it happened, and your confirmation. */
type GapEdit = { owner: string; text: string; confirmed: boolean };
interface GapForm extends GapEdit { drafting: boolean; failed: string; validation: string; }

@Component({
  selector: 'app-gap-review-panel',
  imports: [FormsModule, RouterLink],
  template: `
    @if (jobId() || opened()) {
      <div class="panel" data-testid="gap-review">
        <h3>Experience Bank suggestions</h3>
        @if (suggestions().length) {
          <p class="small muted">Review or edit each suggested bullet. Confirm the work you actually did to add it to
            your Experience Bank and generate a new résumé version for {{ jobId() ? 'this job' : 'each affected job' }}.</p>
        }
        @if (loading()) { <p class="muted">Loading suggestions…</p> }
        @for (r of suggestions(); track r.id) {
          <div class="card" [attr.data-testid]="'gap-row-' + r.id">
            <b>{{ r.skill }}</b> <span class="small muted">· {{ r.job_title }} at {{ r.job_company }}</span>
            <p class="small muted">The posting asks for: {{ r.requirement }}</p>
            @if (forms()[r.id]; as f) {
              @if (!r.draft_text) { <p class="small muted" data-testid="gap-example">{{ r.example }}</p> }
              @if (f.drafting) {
                <p class="small muted" data-testid="gap-drafting">Writing a suggested bullet…</p>
              }
              @if (f.failed) { <p class="small" role="status">{{ f.failed }}</p> }
              <label>Employer or project
                <select [ngModel]="f.owner" (ngModelChange)="edit(r.id, { owner: $event })" [disabled]="saving()">
                  <option value="">Choose where you used it</option>
                  @for (name of owners(); track name) { <option [value]="name">{{ name }}</option> }
                </select>
              </label>
              <label>{{ r.draft_is_example ? 'Draft to complete with your details' : r.draft_text ? 'Suggested bullet' : 'What you did with ' + r.skill }}
                <textarea rows="3" maxlength="600" [ngModel]="f.text" (ngModelChange)="edit(r.id, { text: $event })"
                          [disabled]="saving()" [attr.aria-invalid]="f.validation ? 'true' : null"></textarea>
              </label>
              @if (f.validation) { <p class="small warn-text" role="alert">{{ f.validation }}</p> }
              @if (r.draft_note && r.draft_owner === f.owner && r.draft_text === f.text) {
                <p class="small muted">Why here: {{ r.draft_note }}</p>
              }
              <label class="small"><input type="checkbox" [ngModel]="f.confirmed" (ngModelChange)="markConfirmed(r.id, $event)"
                  [disabled]="saving() || !canConfirm(f)"> I did this work and can explain it in an interview.</label>
              <div class="actions">
                <button type="button" class="secondary" [disabled]="saving() || f.drafting || f.confirmed" (click)="write(r, true, f.owner)">
                  {{ r.draft_text && !r.draft_is_example && r.draft_owner === f.owner ? 'Write another' : 'Suggest a bullet' }}</button>
                <button type="button" class="link-btn" [disabled]="saving()" (click)="dismiss(r)">Dismiss</button>
              </div>
            }
          </div>
        } @empty {
          @if (!loading()) { <p class="small muted">No pending technical suggestions.</p> }
        }
        @if (suggestions().length) {
          <div class="actions" data-testid="gap-add-all">
            <button type="button" class="primary" [disabled]="saving() || !ready().length" (click)="approveAll()">{{ addLabel() }}</button>
          </div>
        }
        @if (message()) { <p role="status" data-testid="gap-result">{{ message() }}</p> }
        @if (latestApp()) {
          <a [routerLink]="['/applications', latestApp()]">Open application and check resume progress</a>
        }
      </div>
    } @else {
      <button type="button" class="secondary" (click)="open()">Review job-related technical gaps</button>
    }
  `,
})
export class GapReviewPanel {
  private api = inject(Api);
  private destroyRef = inject(DestroyRef);
  private draftKey?: string;
  readonly jobId = input<number>();
  readonly changed = output<void>();
  protected readonly opened = signal(false);
  protected readonly suggestions = signal<GapSuggestion[]>([]);
  protected readonly owners = signal<string[]>([]);
  protected readonly latestApp = signal<number | null>(null);
  protected readonly saving = signal(false);
  protected readonly loading = signal(false);
  protected readonly message = signal('');
  protected readonly forms = signal<Record<number, GapForm>>({});

  constructor() {
    effect(() => {
      const jobId = this.jobId();
      if (jobId) untracked(() => this.load(jobId));
    });
  }

  protected open() {
    this.opened.set(true);
    void this.load();
  }

  private async load(jobId?: number) {
    this.loading.set(true);
    try {
      const [rows, account] = await Promise.all([this.api.experienceGaps(jobId), this.api.me().catch(() => null)]);
      if (this.destroyRef.destroyed || this.jobId() !== jobId) return;
      this.draftKey = account ? `job-agent.gap-edits.${account.username ?? 'local'}` : undefined;
      const edits = this.savedEdits();
      const kept = this.forms();
      this.forms.set(Object.fromEntries(rows.map(r => [r.id, kept[r.id] ?? this.formFor(r, edits[r.id])])));
      this.suggestions.set(rows);
      if (rows.length) await this.loadOwners();
    } catch (e) { this.message.set((e as ApiError).message); return; }
    finally { this.loading.set(false); }
    // Reuse cached drafts and let the user edit any row while the remaining drafts are written.
    for (const r of this.suggestions()) {
      if (this.destroyRef.destroyed || this.jobId() !== jobId) break;
      const form = this.forms()[r.id];
      if (!r.draft_text && !form?.text && !form?.owner) await this.write(r, false);
    }
  }

  private formFor(r: GapSuggestion, edit?: GapEdit): GapForm {
    return { owner: r.draft_owner ?? '', text: r.draft_text ?? '', confirmed: false, ...edit,
      drafting: false, failed: '', validation: '' };
  }

  private savedEdits(): Record<number, GapEdit> {
    if (!this.draftKey) return {};
    try { return JSON.parse(sessionStorage.getItem(this.draftKey) ?? '{}'); }
    catch { return {}; }
  }

  private storeEdits(save?: number, remove: number[] = []) {
    if (!this.draftKey) return;
    const edits = this.savedEdits();
    if (save !== undefined && this.forms()[save]) {
      const { owner, text, confirmed } = this.forms()[save];
      edits[save] = { owner, text, confirmed };
    }
    for (const id of remove) delete edits[id];
    try { sessionStorage.setItem(this.draftKey, JSON.stringify(edits)); } catch { /* storage unavailable */ }
  }

  private async loadOwners() {
    if (this.owners().length) return;
    const p = await this.api.profile() as { experience?: { company: string }[]; projects?: { name: string }[] };
    this.owners.set([...new Set([...(p.experience ?? []).map(x => x.company), ...(p.projects ?? []).map(x => x.name)])]);
  }

  /** Change one gap's form (a new object, so the screen redraws). */
  private patch(id: number, changes: Partial<GapForm>) {
    this.forms.update(all => all[id] ? { ...all, [id]: { ...all[id], ...changes } } : all);
  }

  protected async write(r: GapSuggestion, again: boolean, owner?: string) {
    const original = this.forms()[r.id];
    if (!original || original.drafting || original.confirmed || !this.suggestions().some(row => row.id === r.id)) return;
    this.patch(r.id, { drafting: true, failed: '' });
    const started = this.forms()[r.id];
    try {
      const d = await this.api.draftGap(r.id, again, owner);
      this.suggestions.update(rows => rows.map(x => x.id === r.id ? { ...x, ...d, draft_is_example: !!d.draft_is_example } : x));
      if (this.forms()[r.id] === started) {
        this.patch(r.id, { drafting: false, owner: d.draft_owner ?? original.owner,
          text: d.draft_text ?? original.text, validation: '' });
        if (this.savedEdits()[r.id]) this.storeEdits(r.id);
      } else {
        this.patch(r.id, { drafting: false });
      }
    } catch (e) {
      this.patch(r.id, { drafting: false, failed: (e as ApiError).message });
    }
  }

  protected edit(id: number, changes: Partial<Pick<GapForm, 'owner' | 'text'>>) {
    const original = this.suggestions().find(row => row.id === id);
    if (changes.owner !== undefined && original?.draft_owner !== changes.owner &&
        original?.draft_text === this.forms()[id]?.text) {
      changes = { ...changes, text: '' };
    }
    this.patch(id, { ...changes, confirmed: false, validation: '' });
    this.storeEdits(id);
  }

  protected markConfirmed(id: number, confirmed: boolean) {
    this.patch(id, { confirmed });
    this.storeEdits(id);
  }

  protected canConfirm(form: GapForm) {
    return !!form.owner && form.text.trim().length >= 10 && !/[\[\]]/.test(form.text);
  }

  /** The lines you ticked as true (only those still on the list). */
  protected readonly ready = computed(() => {
    const forms = this.forms();
    return this.suggestions().filter(r => forms[r.id]?.confirmed);
  });
  protected readonly addLabel = computed(() => {
    const n = this.ready().length;
    return n ? `Add ${n} line${n > 1 ? 's' : ''} and update resume once` : 'Tick the lines that are true to add them';
  });

  protected async approveAll() {
    const rows = this.ready();
    this.saving.set(true);
    this.message.set('');
    try {
      const items = rows.map(r => ({ id: r.id, owner: this.forms()[r.id].owner, text: this.forms()[r.id].text.trim() }));
      const result = await this.api.confirmGaps(items);
      const rejected = result.rejected ?? {};
      const done = new Set(result.record_ids.length ? rows.filter(r => !(r.id in rejected)).map(r => r.id) : []);
      for (const r of rows) {
        if (rejected[r.id]) this.patch(r.id, { validation: rejected[r.id] });
      }
      const saved = done.size ? `${done.size} ${done.size === 1 ? 'line' : 'lines'} verified. A new résumé has been requested.` : '';
      const issues = rows.filter(r => r.id in rejected).map(r => r.skill);
      const needsWork = issues.length ? `Review ${issues.join(', ')} above, then save your selected bullets together.` : '';
      this.message.set([saved, needsWork].filter(Boolean).join(' '));
      if (result.application_ids.length) this.latestApp.set(result.application_ids[0]);
      this.storeEdits(undefined, [...done]);
      this.suggestions.update(list => list.filter(x => !done.has(x.id)));
      if (done.size) this.changed.emit();
    } catch (e) { this.message.set((e as ApiError).message); }
    finally { this.saving.set(false); }
  }

  protected async dismiss(row: GapSuggestion) {
    this.saving.set(true);
    try {
      await this.api.dismissGap(row.id);
      this.storeEdits(undefined, [row.id]);
      this.suggestions.update(rows => rows.filter(x => x.id !== row.id));
    } catch (e) { this.message.set((e as ApiError).message); }
    finally { this.saving.set(false); }
  }
}
