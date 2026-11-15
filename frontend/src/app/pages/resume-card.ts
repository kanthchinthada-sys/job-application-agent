import { DatePipe } from '@angular/common';
import { Component, computed, input } from '@angular/core';

import { Doc } from '../models';

interface Bullet { text: string; original?: string; changed?: boolean; rejected_reason?: string; }
interface Role { company: string; bullets: Bullet[]; }
interface TailoredResume { summary: string; summary_source?: string; skills: string[]; experience: Role[]; }

/** The tailored resume: what changed and why, the ATS keyword score with evidence, and the printable page. */
@Component({
  selector: 'app-resume-card',
  imports: [DatePipe],
  template: `
    @let d = latest();
    @let cv = resume();
    @let r = d.review ?? {};
    @let t = r.tailoring ?? {};
    <div class="block" data-testid="doc-resume">
      <h3>Tailored resume <span class="label">v{{ d.version }} · {{ d.source === 'ai' ? 'AI tailored' : 'your edit' }}</span></h3>
      @for (w of r.warnings ?? []; track $index) { <div class="msg bad">⚠ {{ w }}</div> }
      <p class="small muted">{{ t.bullets_reworded ?? 0 }} bullets reworded (checked against your originals),
        {{ t.rewordings_rejected ?? 0 }} AI rewordings rejected, {{ t.bullets_omitted ?? 0 }} less relevant bullets left out.
        Summary: {{ summaryNote() }}.</p>
      @if (r.ats; as ats) {
        <div class="ats">
          <b>ATS keyword match: {{ ats.score }}%</b>
          <span class="muted small">(was {{ ats.score_before }}% before adding keywords; {{ ats.max_honest_score }}% is the most your real experience supports for this job)</span>
          @if (r.keywords_added?.length) {
            <details><summary>Keywords added in the job's wording ({{ r.keywords_added.length }}): each backed by your profile</summary>
              @for (k of r.keywords_added; track k.keyword) {
                <div class="small"><span class="chip ok">{{ k.keyword }}</span> <span class="muted">{{ k.evidence }}</span></div>
              }
            </details>
          }
          @if (ats.gaps?.length) {
            <div class="small">Gaps your profile doesn't cover:
              @for (g of ats.gaps; track g.keyword) { <span class="chip bad">{{ g.keyword }}</span> }
              <br><span class="muted">{{ ats.note }}</span></div>
          } @else {
            <div class="small good">No keyword gaps.</div>
          }
        </div>
      }
      @if (r.layout; as lay) {
        <p class="small" data-testid="layout">
          @if (lay.fits) { <span class="good">✓ Fits on {{ lay.max_pages }} pages</span> }
          @else { <span class="chip bad">Longer than {{ lay.max_pages }} pages</span> }
          (about {{ lay.estimated_pages }} pages).
          @if (lay.removed_bullets?.length) { {{ lay.removed_bullets.length }} less relevant bullets left out to fit. }
          @if (lay.skills_left_out?.length) { {{ lay.skills_left_out.length }} skills left out. }
        </p>
      }
      @if (r.line_fit?.changes?.length) {
        <details data-testid="line-fit"><summary>Lines adjusted so they print without big gaps ({{ r.line_fit.changes.length }}): check each one</summary>
          @for (c of r.line_fit.changes; track $index) {
            <div class="small"><span class="muted">{{ c.how }} · gap {{ c.gap_px_before }}px → {{ c.gap_px_after }}px</span><br>
              <b>Now:</b> {{ c.after }}<br><span class="muted"><b>Was:</b> {{ c.before }}</span></div>
          }
        </details>
      }
      <p><b>Summary:</b> {{ cv.summary }}</p>
      @if (t.summary_themes?.length) {
        <p class="small muted">What this job emphasises (summary focus): {{ t.summary_themes.join(', ') }}</p>
      }
      <p class="small"><b>Skills order:</b> {{ cv.skills.slice(0, 12).join(' · ') }}{{ cv.skills.length > 12 ? ' …' : '' }}</p>
      <a class="button" [href]="'/applications/' + d.application_id + '/resume'">Open latest resume (save as PDF)</a>
      @if (changed().length) {
        <details open><summary>Reworded bullets ({{ changed().length }}): compare with your original</summary>
          @for (c of changed(); track $index) {
            <div class="small"><span class="muted">{{ c.company }}</span><br><b>New:</b> {{ c.b.text }}<br>
              <span class="muted"><b>Original:</b> {{ c.b.original }}</span></div>
          }
        </details>
      }
      @if (rejected().length) {
        <details><summary>AI rewordings rejected ({{ rejected().length }})</summary>
          @for (c of rejected(); track $index) { <div class="small muted">{{ c.b.rejected_reason }}. Kept: {{ c.b.original }}</div> }
        </details>
      }
      @if (r.tailoring_report; as tr) {
        <details data-testid="tailoring-report"><summary>Tailoring report: how your experience was chosen for this job
          ({{ tr.role_types.join(' + ') }})</summary>
          <table class="small">
            <thead><tr><th>Job requirement</th><th>Type</th><th>Your records</th><th>Match</th><th>Where on the resume</th></tr></thead>
            <tbody>
              @for (row of tr.rows; track $index) {
                <tr [class.needs-you]="row.strength === 'gap'">
                  <td>{{ row.requirement }}</td><td>{{ row.kind }}</td>
                  <td>{{ row.selected_ids.join(', ') || '-' }}</td><td>{{ row.strength }}</td>
                  <td>{{ row.location.join(', ') || '-' }}</td>
                </tr>
              }
            </tbody>
          </table>
          @if (tr.gaps.length) { <p class="small"><b>Not enough evidence:</b> {{ tr.gaps.join(' · ') }}</p> }
          @if (tr.excluded_by_status.length) { <p class="small"><b>Left out because of their status:</b> {{ tr.excluded_by_status.join(' · ') }}</p> }
          @if (tr.removed_as_duplicate.length) { <p class="small"><b>Left out as near-duplicates:</b> {{ tr.removed_as_duplicate.join(' · ') }}</p> }
          @if (tr.eligibility.length) { <p class="small"><b>Check yourself:</b> {{ tr.eligibility.join(' · ') }}</p> }
        </details>
      }
      @if (versions().length > 1) {
        <details><summary>Older versions ({{ versions().length - 1 }})</summary>
          @for (v of versions().slice(1); track v.id) {
            <div class="small"><a [href]="'/documents/' + v.id + '/print'">v{{ v.version }}</a> · {{ v.created_at | date: 'medium' }}</div>
          }
        </details>
      }
    </div>
  `,
})
export class ResumeCard {
  /** All versions of the resume, newest first. */
  readonly versions = input.required<Doc[]>();

  protected readonly latest = computed(() => this.versions()[0]);
  protected readonly resume = computed(() => JSON.parse(this.latest().content) as TailoredResume);
  protected readonly changed = computed(() => this.bullets(b => !!b.changed));
  protected readonly rejected = computed(() => this.bullets(b => !!b.rejected_reason));
  protected readonly summaryNote = computed(() => {
    const s = this.resume().summary_source;
    return s === 'ai' ? 'AI-written, passed the checks'
      : s === 'profile_reordered' ? 'your own summary, sentences reordered for this job'
      : s === 'composed' ? 'written for this job from your own experience' : 'your own profile summary';
  });

  private bullets(keep: (b: Bullet) => boolean) {
    return this.resume().experience.flatMap(role => role.bullets.filter(keep).map(b => ({ company: role.company, b })));
  }
}
