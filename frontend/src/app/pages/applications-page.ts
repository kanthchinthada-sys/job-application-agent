import { DatePipe } from '@angular/common';
import { Component, computed, inject, input, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { Api, ApiError } from '../api';
import { Application, pretty } from '../models';
import { MaterialsPanel } from './materials-panel';

/** Application tracker. Status changes follow the backend's rules; "confirmed" needs evidence. */
@Component({
  selector: 'app-applications-page',
  imports: [FormsModule, DatePipe, MaterialsPanel],
  template: `
    <div class="card">
      <h2>Applications</h2>
      @if (apps() === null) {
        <p class="muted">Loading…</p>
      } @else if (apps()!.length === 0) {
        <p class="muted">No applications yet. On the <b>Jobs</b> tab, click “Track application”.</p>
      } @else {
        <table>
          <thead><tr><th>Job</th><th>Status</th><th>Notes</th><th>History</th><th></th></tr></thead>
          <tbody>
            @for (a of apps(); track a.id) {
              <tr>
                <td><b>{{ a.job_title }}</b><br><span class="muted">{{ a.company }}</span></td>
                <td>
                  <select #sel [attr.aria-label]="'Status of ' + a.job_title" (change)="changeStatus(a, sel)">
                    @for (s of statusChoices(a); track s) { <option [value]="s" [selected]="s === a.status">{{ pretty(s) }}</option> }
                  </select>
                  @if (a.submission_confirmation) {
                    <div class="small muted">Confirmation: {{ a.submission_confirmation }}</div>
                  }
                  @if (confirming()?.id === a.id) {
                    <label [for]="'proof-' + a.id">Confirmation evidence (required)</label>
                    <input [id]="'proof-' + a.id" [(ngModel)]="proof" placeholder="Confirmation number, email subject, …">
                    <button class="primary" type="button" (click)="confirmSubmitted(a)">Mark confirmed</button>
                    <button class="secondary" type="button" (click)="cancelConfirm(a)">Cancel</button>
                  }
                </td>
                <td><textarea class="short" [attr.aria-label]="'Notes for ' + a.job_title" [ngModel]="a.notes"
                              (change)="saveNotes(a, $any($event.target).value)"></textarea></td>
                <td class="small muted">
                  @for (h of a.history; track $index) { {{ h.occurred_at | date: 'mediumDate' }} · {{ pretty(h.to_status) }}<br> }
                </td>
                <td><button class="secondary tight" type="button" (click)="open(a.id)">Materials</button></td>
              </tr>
            }
          </tbody>
        </table>
      }
      @if (error(); as e) { <div class="msg bad">{{ e }}</div> }
    </div>

    @if (current(); as a) {
      <app-materials-panel [application]="a" (changed)="reload()" />
    }
  `,
})
export class ApplicationsPage implements OnInit {
  private api = inject(Api);
  private router = inject(Router);

  /** From the URL /applications/:id: whose materials are open. */
  readonly id = input<string>();

  protected readonly apps = signal<Application[] | null>(null);
  /** The status box that is waiting for confirmation evidence, so Cancel can put it back. */
  private pendingSelect: HTMLSelectElement | null = null;
  protected readonly error = signal('');
  protected readonly confirming = signal<Application | null>(null);
  protected readonly proof = signal('');
  protected readonly current = computed(() => this.apps()?.find(a => a.id === Number(this.id())) ?? null);
  protected readonly pretty = pretty;

  ngOnInit() { return this.reload(); }

  async reload() {
    try {
      this.apps.set(await this.api.applications());
    } catch (e) {
      this.error.set((e as ApiError).message);
      this.apps.set([]);
    }
  }

  open(id: number) { this.router.navigate(['/applications', id]); }

  statusChoices(a: Application) { return [a.status, ...a.allowed_next.filter(s => s !== a.status)]; }

  async changeStatus(a: Application, select: HTMLSelectElement) {
    const status = select.value;
    this.error.set('');
    if (status === a.status) return;
    if (status === 'submitted_confirmed') {          // never without evidence
      this.proof.set('');
      this.pendingSelect = select;
      this.confirming.set(a);
      return;
    }
    await this.update(a.id, { status });
    // If the backend refused the change, show the status that is really saved.
    select.value = this.apps()?.find(x => x.id === a.id)?.status ?? select.value;
  }

  async confirmSubmitted(a: Application) {
    if (!this.proof().trim()) { this.error.set('Paste the confirmation evidence first.'); return; }
    await this.update(a.id, { status: 'submitted_confirmed', submission_confirmation: this.proof().trim() });
    this.pendingSelect = null;
    this.confirming.set(null);
  }

  cancelConfirm(a: Application) {
    if (this.pendingSelect) this.pendingSelect.value = a.status;   // nothing was saved: show the real status again
    this.pendingSelect = null;
    this.confirming.set(null);
  }

  async saveNotes(a: Application, notes: string) {
    await this.update(a.id, { notes });
  }

  private async update(id: number, body: { status?: string; notes?: string; submission_confirmation?: string }) {
    try {
      await this.api.updateApplication(id, body);
    } catch (e) {
      this.error.set((e as ApiError).message);
    }
    await this.reload();
  }
}
