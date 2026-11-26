import { GapReviewPanel } from '../gap-review-panel';
import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { Api, ApiError } from '../../api';
import { Job, RequirementEvidence, Requirements } from '../../models';

/** The posting's requirements, each with direct evidence, related experience or a gap, and the exact records behind
 *  it. The same deterministic analysis the resume uses: no model calls. */
@Component({
  selector: 'app-match-tab',
  imports: [FormsModule, RouterLink, GapReviewPanel],
  template: `
    @if (job().pending_technical_gaps) {
      <app-gap-review-panel [jobId]="job().id" (changed)="load(job().id)" />
    }
    <div class="card" data-testid="match">
      @if (error(); as e) {
        <div class="msg bad">{{ e }}</div>
        @if (needsProfile()) { <p><a routerLink="/profile">Set up your profile</a> to see how your experience matches.</p> }
      } @else if (!data()) {
        <p class="muted"><span class="spinner"></span> Comparing the posting with your profile…</p>
      } @else {
        <h3 style="margin-top:0">What this job asks for, and your evidence</h3>
        <p class="small muted">{{ counts() }}. Only named technical skills are scored; people skills are shown by example
          and are never a gap. Open a requirement to see the employer and experience record behind it.</p>
        @if (!data()!.has_bank) {
          <div class="msg info">Add your experience bank (Profile) for record-level evidence; without it, matches come from
            your resume and skill lists.</div>
        }
        @for (row of data()!.rows; track $index) {
          <details class="req" [attr.data-testid]="'req-' + $index">
            <summary>
              <span class="badge" [class.ok]="['direct', 'shown by example'].includes(row.strength)"
                    [class.warn]="['partial', 'transferable', 'skills only'].includes(row.strength)"
                    [class.bad]="row.strength === 'gap'">{{ row.evidence }}</span>
              <span class="req-text">{{ row.requirement }}
                <span class="small muted">· {{ row.kind }}{{ row.category !== 'technical' ? ' · ' + row.category : '' }}</span></span>
            </summary>
            <div class="small" style="margin-top:8px">
              @if (row.found.length) { <div class="good">✓ Shown: {{ row.found.join(', ') }}</div> }
              @for (x of elsewhere(row); track x[0]) { <div class="good">✓ {{ x[0] }} <span class="muted">({{ x[1] }})</span></div> }
              @for (rec of row.records; track rec.id) {
                <div class="panel" style="margin-top:6px"><b>{{ rec.owner }}</b> <span class="label">{{ rec.id }}</span><br>{{ rec.text }}</div>
              }
              @if (!row.records.length && !row.found.length && !elsewhere(row).length) {
                <div class="muted">No record in your profile shows this.</div>
              }
              @if (row.missing.length && row.category === 'technical') {
                <div class="warn-text" style="margin-top:6px">✗ Not in your profile: {{ row.missing.join(', ') }}</div>
                @for (m of row.missing; track m) {
                  @if (!added().includes(m)) {
                    <button class="link-btn small" type="button" (click)="openGap(m)">I have {{ m }}</button>&nbsp;
                  } @else { <span class="good">✓ {{ m }} added</span>&nbsp; }
                }
              }
              @if (gap() && row.missing.includes(gap()!)) {
                <div class="gap-form" data-testid="gap-form">
                  <b>Where did you use {{ gap() }}?</b>
                  <label class="small">Employer or project
                    <select [(ngModel)]="gapOwner" name="gapOwner">
                      @for (o of owners(); track o) { <option [value]="o">{{ o }}</option> }
                    </select></label>
                  <label class="small">One line, in your words: what you did with {{ gap() }}
                    <textarea rows="2" [(ngModel)]="gapText" name="gapText" [placeholder]="'e.g. Managed release runs in ' + gap() + '.'"></textarea></label>
                  <div class="actions">
                    <button class="secondary" type="button" [disabled]="gapText.trim().length < 10 || !gapOwner" (click)="saveGap()">Add to my profile</button>
                    <button class="secondary" type="button" (click)="gap.set(null)">Cancel</button></div>
                  @if (gapMsg(); as m) { <div class="msg bad">{{ m }}</div> }
                </div>
              }
            </div>
          </details>
        }
        @if (added().length) {
          <div class="msg ok">Added to your profile: {{ added().join(', ') }}. Open <b>Documents</b> to generate a new
            version that can use it (your current versions stay available).</div>
        }
        @for (e of data()!.eligibility; track $index) { <div class="small muted">Check yourself: {{ e }}</div> }
      }
    </div>
    <details class="card">
      <summary><b>The job posting</b> ({{ job().description.length }} characters)</summary>
      <div style="white-space:pre-wrap; font-size:14px; margin-top:8px" data-testid="posting">{{ job().description }}</div>
    </details>
  `,
})
export class MatchTab {
  private api = inject(Api);

  readonly job = input.required<Job>();
  protected readonly data = signal<Requirements | null>(null);
  protected readonly error = signal('');
  protected readonly needsProfile = signal(false);
  protected readonly gap = signal<string | null>(null);
  protected readonly owners = signal<string[]>([]);
  protected readonly added = signal<string[]>([]);
  protected readonly gapMsg = signal<string | null>(null);
  protected gapOwner = '';
  protected gapText = '';

  protected readonly counts = computed(() => {
    const rows = (this.data()?.rows ?? []).filter(r => r.category !== 'soft skill');
    const n = (f: (r: RequirementEvidence) => boolean) => rows.filter(f).length;
    return `${n(r => r.strength === 'direct')} with direct evidence · `
      + `${n(r => ['partial', 'transferable', 'skills only'].includes(r.strength))} partly or related · `
      + `${n(r => r.strength === 'gap')} gaps`;
  });

  constructor() {
    effect(() => {
      const id = this.job().id;
      untracked(() => this.load(id));
    });
  }

  protected async load(id: number) {
    this.data.set(null); this.error.set(''); this.needsProfile.set(false);
    try {
      this.data.set(await this.api.requirements(id));
    } catch (e) {
      const err = e as ApiError;
      this.needsProfile.set(err.status === 404 && /profile/i.test(err.message));
      this.error.set(this.needsProfile() ? 'No profile yet.' : err.message);
    }
  }

  protected elsewhere(row: RequirementEvidence) { return Object.entries(row.found_elsewhere ?? {}); }

  async openGap(keyword: string) {
    this.gapMsg.set(null);
    this.gapText = '';
    if (!this.owners().length) {
      const p: any = await this.api.profile();
      this.owners.set([...(p.experience ?? []).map((e: any) => e.company), ...(p.projects ?? []).map((x: any) => x.name)]);
    }
    this.gapOwner = this.gapOwner || this.owners()[0] || '';
    this.gap.set(keyword);
  }

  async saveGap() {
    const k = this.gap();
    if (!k) return;
    try {
      await this.api.addExperienceLine({ owner: this.gapOwner, text: this.gapText.trim(), skill: k });
      this.added.update(a => [...a, k]);
      this.gap.set(null);
    } catch (e) {
      this.gapMsg.set((e as ApiError).message);
    }
  }
}
