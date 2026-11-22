import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../api';
import { BankImportReport, BankRecord, RecordStatus } from '../models';
import { Msg, MsgBox } from '../ui';

const STATUS_LABEL: Record<RecordStatus, string> = {
  verified: 'verified (used on resumes)', partly_true: 'partly true (not used)',
  unverified: 'unverified (not used)', not_true: 'not true (not used)',
};

/** The numbered experience bank: paste it, see where each record went, and confirm or exclude records. */
@Component({
  selector: 'app-bank-panel',
  imports: [FormsModule, MsgBox],
  template: `
    <div class="card" data-testid="bank">
      <h2>Experience bank</h2>
      <p class="hint">Optional, for the best resumes: a longer list of everything you've done, one numbered record per
        line (e.g. "AB01: Built …"), under each employer's name as a heading. Every job then gets the records that fit it
        best. Records are yours, so they count as <b>verified</b>; add "(unverified)", "(partly true)" or "(not true)" to a
        line to keep it off your resumes. Duplicates are removed and notes are kept as context.</p>
      <textarea aria-label="Experience bank text" [(ngModel)]="text" placeholder="Northwind Bank | Toronto
AB01: Built …
AB02: Led …"></textarea>
      <button class="secondary" type="button" [disabled]="busy()" (click)="importText()">Import records</button>
      <app-msg [msg]="msg()" />

      @if (report(); as r) {
        <div class="panel small" data-testid="bank-report">
          <b>{{ r.imported }} records imported.</b>
          @for (o of owners(r); track o[0]) { <span class="label">{{ o[0] }}: {{ o[1] }}</span> }
          @if (r.duplicates_removed.length) { <div>Duplicates removed: {{ r.duplicates_removed.join(', ') }}</div> }
          @if (r.rerouted.length) { <div>Moved to the right place: {{ r.rerouted.join('; ') }}</div> }
          @if (r.missing_ids.length) { <div>Missing from your bank (not filled in): {{ r.missing_ids.join(', ') }}</div> }
          @if (r.notes_kept_as_caveats.length) { <div>Notes kept as context for: {{ r.notes_kept_as_caveats.join(', ') }}</div> }
        </div>
      }

      @if (records().length) {
        <div class="toolbar" style="margin-top:14px" data-testid="bank-filters">
          <div><label for="b-search">Search by keyword or record ID</label>
            <input id="b-search" type="search" [(ngModel)]="search" placeholder="e.g. Kubernetes or AG12"></div>
          <div style="flex:0 1 220px"><label for="b-owner">Employer or project</label>
            <select id="b-owner" [(ngModel)]="owner">
              <option value="">All ({{ records().length }})</option>
              @for (o of ownerNames(); track o) { <option [value]="o">{{ o }}</option> }
            </select></div>
          <div style="flex:0 1 220px"><label for="b-status">Verification status</label>
            <select id="b-status" aria-label="Filter records" [(ngModel)]="filter">
              <option value="all">All statuses</option>
              @for (s of statuses; track s) { <option [value]="s">{{ label(s) }} ({{ count(s) }})</option> }
            </select></div>
        </div>
        <p class="small muted" data-testid="bank-count">Showing {{ shown().length }} of {{ records().length }} records.</p>
        <div class="table-wrap">
        <table class="small">
          <thead><tr><th>ID</th><th>Record</th><th style="min-width:250px">Status</th></tr></thead>
          <tbody>
            @for (r of shown(); track r.id) {
              <tr [class.needs-you]="r.status !== 'verified'">
                <td><b>{{ r.id }}</b><div class="muted">{{ r.owner }}</div></td>
                <td>{{ r.text }}
                  @if (r.caveat) { <div class="muted">Context kept: {{ r.caveat }}</div> }</td>
                <td>
                  <select style="width:auto; max-width:100%" [attr.aria-label]="'Status of ' + r.id" [value]="r.status" (change)="setStatus(r, $any($event.target).value)">
                    @for (s of statuses; track s) { <option [value]="s" [selected]="s === r.status">{{ label(s) }}</option> }
                  </select>
                  @if (r.status_note) { <div class="muted" style="margin-top:4px">{{ r.status_note }}</div> }
                </td>
              </tr>
            } @empty {
              <tr><td colspan="3" class="empty">No record matches these filters.</td></tr>
            }
          </tbody>
        </table>
        </div>
      }
    </div>
  `,
})
export class BankPanel implements OnInit {
  private api = inject(Api);

  protected readonly statuses: RecordStatus[] = ['verified', 'partly_true', 'unverified', 'not_true'];
  protected readonly text = signal('');
  protected readonly busy = signal(false);
  protected readonly msg = signal<Msg | null>(null);
  protected readonly report = signal<BankImportReport | null>(null);
  protected readonly records = signal<BankRecord[]>([]);
  protected readonly filter = signal<string>('all');
  protected readonly search = signal('');
  protected readonly owner = signal('');
  protected readonly ownerNames = computed(() => [...new Set(this.records().map(r => r.owner))]);
  protected readonly shown = computed(() => {
    const q = this.search().trim().toLowerCase();
    return this.records()
      .filter(r => this.filter() === 'all' || r.status === this.filter())
      .filter(r => !this.owner() || r.owner === this.owner())
      .filter(r => !q || r.id.toLowerCase() === q || r.id.toLowerCase().startsWith(q) || r.text.toLowerCase().includes(q)
                   || (r.group ?? '').toLowerCase().includes(q));
  });

  async ngOnInit() {
    try {
      const p = await this.api.profile() as { experience_bank?: BankRecord[] };
      this.records.set(p.experience_bank ?? []);
    } catch { /* no profile yet */ }
  }

  async importText() {
    if (!this.text().trim()) { this.msg.set({ kind: 'bad', text: 'Paste your records first.' }); return; }
    this.busy.set(true);
    try {
      const r = await this.api.importBank(this.text());
      this.report.set(r.report);
      this.records.set(r.records);
      this.msg.set({ kind: 'ok', text: 'Imported. Check the records that are not verified below.' });
    } catch (e) {
      const err = e as ApiError;
      this.msg.set({ kind: 'bad', text: err.status === 404 ? 'Save your profile first, then import the bank.' : err.message });
    } finally {
      this.busy.set(false);
    }
  }

  async setStatus(r: BankRecord, status: RecordStatus) {
    try {
      const saved = await this.api.setRecordStatus(r.id, status, status === 'verified' ? 'Confirmed by you.' : r.status_note);
      this.records.update(list => list.map(x => (x.id === saved.id ? saved : x)));
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }

  protected count(s: string) { return this.records().filter(r => r.status === s).length; }
  protected label(s: RecordStatus) { return STATUS_LABEL[s]; }
  protected owners(r: BankImportReport) { return Object.entries(r.by_owner); }
}
