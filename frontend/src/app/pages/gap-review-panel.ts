import { Component, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { Api, ApiError } from '../api';
import { GapSuggestion } from '../models';

@Component({
  selector: 'app-gap-review-panel',
  imports: [FormsModule, RouterLink],
  template: `
    @if (jobId() || opened()) {
      <div class="panel" data-testid="gap-review">
        <h3>Technical gaps to verify</h3>
        <p class="small muted">These examples are prompts. Confirm a record only if you can describe work you actually did.
          Confirmation adds that evidence to your bank and queues a new resume for this job.</p>
        @if (loading()) { <p class="muted">Loading suggestions…</p> }
        @if (message()) { <p role="status">{{ message() }}</p> }
        @if (latestApp()) {
          <a [routerLink]="['/applications', latestApp()]">Open application and check resume progress</a>
        }
        @for (r of suggestions(); track r.id) {
          <div class="card">
            <b>{{ r.skill }}</b> <span class="small muted">· {{ r.job_title }} at {{ r.job_company }}</span>
            <p class="small">{{ r.requirement }}</p>
            <p class="small">{{ r.example }}</p>
            @if (r.owner_hint) { <p class="small muted">Related evidence in your bank: {{ r.owner_hint }}. Choose where you actually used this skill.</p> }
            @if (active() === r.id) {
              <label>Employer or project
                <select [(ngModel)]="owner">
                  <option value="">Choose where you used it</option>
                  @for (name of owners(); track name) { <option [value]="name">{{ name }}</option> }
                </select>
              </label>
              <label>What you did, how you used the skill, and what happened
                <textarea rows="3" maxlength="600" [(ngModel)]="text"></textarea>
              </label>
              <label class="small"><input type="checkbox" [(ngModel)]="confirmed"> I did this work and can explain it in an interview.</label>
              <div class="actions">
                <button type="button" class="primary" [disabled]="saving() || !confirmed || !owner || text.trim().length < 10"
                        (click)="approve(r)">Enable and generate resume</button>
                <button type="button" class="secondary" [disabled]="saving()" (click)="active.set(null)">Cancel</button>
              </div>
            } @else {
              <div class="actions">
                <button type="button" class="secondary" [disabled]="saving()" (click)="review(r)">Review example</button>
                <button type="button" class="link-btn" [disabled]="saving()" (click)="dismiss(r)">Dismiss</button>
              </div>
            }
          </div>
        } @empty {
          @if (!loading()) { <p class="small muted">No pending technical suggestions.</p> }
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
  protected readonly active = signal<number | null>(null);
  protected readonly latestApp = signal<number | null>(null);
  protected readonly saving = signal(false);
  protected readonly loading = signal(false);
  protected readonly message = signal('');
  protected owner = '';
  protected text = '';
  protected confirmed = false;

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
    try { this.suggestions.set(await this.api.experienceGaps(jobId)); }
    catch (e) { this.message.set((e as ApiError).message); }
    finally { this.loading.set(false); }
  }

  protected async review(row: GapSuggestion) {
    this.active.set(row.id);
    this.owner = '';
    this.text = '';
    this.confirmed = false;
    this.message.set('');
    if (this.owners().length) return;
    try {
      const p = await this.api.profile() as { experience?: { company: string }[]; projects?: { name: string }[] };
      this.owners.set([...new Set([...(p.experience ?? []).map(x => x.company), ...(p.projects ?? []).map(x => x.name)])]);
    } catch (e) { this.message.set((e as ApiError).message); }
  }

  protected async approve(row: GapSuggestion) {
    this.saving.set(true);
    try {
      const result = await this.api.confirmGap(row.id, this.owner, this.text.trim());
      this.message.set(`${result.record_id} verified. A new resume is being prepared or queued behind the active generation.`);
      this.latestApp.set(result.application_id);
      this.active.set(null);
      this.suggestions.update(rows => rows.filter(x => x.id !== row.id));
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
