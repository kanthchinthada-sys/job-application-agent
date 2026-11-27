import { DatePipe } from '@angular/common';
import { Component, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../../api';
import { Activity } from '../../models';

/** Your notes (with a visible saved state), and every status change and document version in date order. */
@Component({
  selector: 'app-activity-tab',
  imports: [FormsModule, DatePipe],
  template: `
    <div class="card" data-testid="notes">
      <h3 style="margin-top:0">Notes</h3>
      <textarea aria-label="Notes" [ngModel]="notes()" (ngModelChange)="notes.set($event); state.set('dirty')"
                [disabled]="!activity() || state() === 'saving'"
                placeholder="Recruiter name, salary range, follow-up dates…"></textarea>
      <div class="actions">
        <button class="secondary" type="button" [disabled]="!activity() || state() === 'saving' || state() === 'saved'" (click)="save()">Save notes</button>
        <span class="small" data-testid="notes-state" [class.good]="state() === 'saved'" [class.warn-text]="state() === 'dirty' || state() === 'error'">
          @switch (activity() ? state() : null) {
            @case ('saved') { ✓ Saved{{ savedAt() ? ' · ' + (savedAt() | date: 'MMM d, h:mm a') : '' }} }
            @case ('dirty') { Unsaved changes }
            @case ('saving') { Saving… }
            @case ('error') { Not saved: {{ error() }} }
          }
        </span>
      </div>
    </div>
    <div class="card">
      <h3 style="margin-top:0">Activity</h3>
      @if (activity(); as a) {
        @if (a.items.length) {
          <ul class="timeline" data-testid="timeline">
            @for (i of a.items; track $index) {
              <li><time>{{ i.at | date: 'MMM d, y, h:mm a' }}</time>
                <span>{{ i.text }}@if (i.note) { <span class="muted"> · {{ i.note }}</span> }
                  @if (i.document_id) { · <button class="link-btn small" type="button" (click)="openDoc.emit(i.document_id)">Open this version</button> }</span></li>
            }
          </ul>
        } @else { <p class="muted">No activity yet.</p> }
      } @else if (loadError()) {
        <p class="warn-text" role="alert">{{ loadError() }}</p>
        <button class="secondary" type="button" (click)="load(appId())">Retry loading activity</button>
      } @else { <p class="muted"><span class="spinner"></span> Loading…</p> }
    </div>
  `,
})
export class ActivityTab {
  private api = inject(Api);

  readonly appId = input.required<number>();
  readonly openDoc = output<number>();
  readonly changed = output<void>();

  protected readonly activity = signal<Activity | null>(null);
  protected readonly notes = signal('');
  protected readonly state = signal<'saved' | 'dirty' | 'saving' | 'error'>('saved');
  protected readonly savedAt = signal<string | null>(null);
  protected readonly error = signal('');
  protected readonly loadError = signal('');

  constructor() {
    effect(() => {
      const id = this.appId();
      untracked(() => this.load(id));
    });
  }

  protected async load(id: number) {
    this.loadError.set('');
    this.activity.set(null);
    try {
      const a = await this.api.activity(id);
      this.activity.set(a);
      this.notes.set(a.notes);
      this.savedAt.set(a.notes ? a.notes_saved_at : null);
      this.state.set('saved');
    } catch (e) {
      this.loadError.set((e as ApiError).message);
    }
  }

  async save() {
    this.state.set('saving');
    try {
      const app = await this.api.updateApplication(this.appId(), { notes: this.notes() });
      this.savedAt.set(String(app.updated_at ?? new Date().toISOString()));
      this.state.set('saved');
      this.changed.emit();
    } catch (e) {
      this.error.set((e as ApiError).message);
      this.state.set('error');
    }
  }
}
