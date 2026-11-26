import { Component, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { Api, ApiError } from '../api';
import { GapSuggestion } from '../models';

/** What you are about to add for one gap: the AI's line (or your own), where it happened, and your confirmation. */
interface GapForm { owner: string; text: string; confirmed: boolean; drafting: boolean; failed: string; }

@Component({
  selector: 'app-gap-review-panel',
  imports: [FormsModule, RouterLink],
  template: `
    @if (jobId() || opened()) {
      <div class="panel" data-testid="gap-review">
        <h3>Technical gaps to verify</h3>
        <p class="small muted">For each skill this job asks for that your records don't show yet, the AI writes a line at
          the employer or project where it fits best, from what you did there. Keep it only if it is true: adding it puts it
          in your experience bank. Tick every line that is true, then update the resume once for all of them.</p>
        @if (loading()) { <p class="muted">Loading suggestions…</p> }
        @if (message()) { <p role="status">{{ message() }}</p> }
        @if (latestApp()) {
          <a [routerLink]="['/applications', latestApp()]">Open application and check resume progress</a>
        }
        @for (r of suggestions(); track r.id) {
          <div class="card">
            <b>{{ r.skill }}</b> <span class="small muted">· {{ r.job_title }} at {{ r.job_company }}</span>
            <p class="small muted">The posting asks for: {{ r.requirement }}</p>
            @if (forms()[r.id]; as f) {
              @if (f.drafting) {
                <p class="muted" data-testid="gap-drafting">Writing a line from your experience…</p>
              } @else {
                @if (f.failed) { <p class="small">{{ f.failed }}</p> }
                <label>Employer or project
                  <select [(ngModel)]="f.owner" (ngModelChange)="touch(r.id)">
                    <option value="">Choose where you used it</option>
                    @for (name of owners(); track name) { <option [value]="name">{{ name }}</option> }
                  </select>
                </label>
                <label>{{ f.failed ? 'What you did with ' + r.skill : 'Suggested line (edit anything that is not exactly right)' }}
                  <textarea rows="3" maxlength="600" [(ngModel)]="f.text" (ngModelChange)="touch(r.id)" [placeholder]="r.example"></textarea>
                </label>
                @if (r.draft_note && !f.failed) { <p class="small muted">Why here: {{ r.draft_note }}</p> }
                <label class="small"><input type="checkbox" [(ngModel)]="f.confirmed" (ngModelChange)="touch(r.id)"> This is true: I did this work and can explain it in an interview.</label>
                <div class="actions">
                  <button type="button" class="secondary" [disabled]="saving()" (click)="redraft(r)">Write another</button>
                  <button type="button" class="link-btn" [disabled]="saving()" (click)="dismiss(r)">Dismiss</button>
                </div>
              }
            }
          </div>
        } @empty {
          @if (!loading()) { <p class="small muted">No pending technical suggestions.</p> }
        }
        @if (suggestions().length) {
          <div class="actions" data-testid="gap-add-all">
            <button type="button" class="primary" [disabled]="saving() || !ready().length || incomplete()"
                    (click)="approveAll()">{{ addLabel() }}</button>
            @if (incomplete()) { <span class="small muted">Each ticked line needs an employer or project and its text.</span> }
          </div>
        }
      </div>
    } @else {
      <button type="button" class="secondary" (click)="open()">Review job-related technical gaps</button>
    }
  `,
})
export class GapReviewPanel {
  private api = inject(Api);
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
      const rows = await this.api.experienceGaps(jobId);
      const kept = this.forms();
      this.forms.set(Object.fromEntries(rows.map(r => [r.id, kept[r.id] ?? this.formFor(r)])));
      this.suggestions.set(rows);
      await this.loadOwners();
    } catch (e) { this.message.set((e as ApiError).message); return; }
    finally { this.loading.set(false); }
    // the AI writes each missing line, one at a time (a saved line is reused, so this is quick the next time)
    for (const r of this.suggestions()) {
      if (!r.draft_text) await this.write(r, false);
    }
  }

  private formFor(r: GapSuggestion): GapForm {
    return { owner: r.draft_owner ?? '', text: r.draft_text ?? '', confirmed: false, drafting: !r.draft_text, failed: '' };
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

  private async write(r: GapSuggestion, again: boolean) {
    if (!this.forms()[r.id]) return;
    this.patch(r.id, { drafting: true, failed: '', confirmed: false });
    try {
      const d = await this.api.draftGap(r.id, again);
      this.suggestions.update(rows => rows.map(x => x.id === r.id ? { ...x, ...d } : x));
      this.patch(r.id, { drafting: false, owner: d.draft_owner ?? this.forms()[r.id]?.owner ?? '', text: d.draft_text ?? '' });
    } catch (e) {
      this.patch(r.id, { drafting: false, failed: `${(e as ApiError).message} You can write it yourself below.` });
    }
  }

  protected redraft(r: GapSuggestion) { void this.write(r, true); }

  /** The lines you ticked as true (only those still on the list). */
  protected readonly ready = computed(() => {
    const forms = this.forms();
    return this.suggestions().filter(r => forms[r.id]?.confirmed && !forms[r.id]?.drafting);
  });
  protected readonly incomplete = computed(() =>
    this.ready().some(r => !this.forms()[r.id].owner || this.forms()[r.id].text.trim().length < 10));
  protected readonly addLabel = computed(() => {
    const n = this.ready().length;
    return n ? `Add ${n} line${n > 1 ? 's' : ''} and update resume once` : 'Tick the lines that are true to add them';
  });

  /** Ticking or editing changes the form object in place; a new object lets the counts above update. */
  protected touch(id: number) { this.patch(id, {}); }

  protected async approveAll() {
    const rows = this.ready();
    this.saving.set(true);
    this.message.set('');
    try {
      const items = rows.map(r => ({ id: r.id, owner: this.forms()[r.id].owner, text: this.forms()[r.id].text.trim() }));
      const result = await this.api.confirmGaps(items);
      this.message.set(`${result.record_ids.join(', ')} verified. One new resume is being prepared for all of them` +
        ' (or queued behind the active generation).');
      this.latestApp.set(result.application_ids[0] ?? null);
      const done = new Set(rows.map(r => r.id));
      this.suggestions.update(list => list.filter(x => !done.has(x.id)));
      this.changed.emit();
    } catch (e) { this.message.set((e as ApiError).message); }
    finally { this.saving.set(false); }
  }

  protected async dismiss(row: GapSuggestion) {
    this.saving.set(true);
    try {
      await this.api.dismissGap(row.id);
      this.suggestions.update(rows => rows.filter(x => x.id !== row.id));
    } catch (e) { this.message.set((e as ApiError).message); }
    finally { this.saving.set(false); }
  }
}
