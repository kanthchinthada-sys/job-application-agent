import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../api';

import { Doc } from '../models';

interface Bullet { text: string; original?: string; changed?: boolean; rejected_reason?: string; source_id?: string | null; source_ids?: string[]; }
interface Role { company: string; bullets: Bullet[]; }
interface TailoredResume { summary: string; summary_source?: string; skills: string[]; experience: Role[]; }

/** The tailored resume: what changed and why, the ATS keyword score with evidence, and the printable page. */
@Component({
  selector: 'app-resume-card',
  imports: [DatePipe, FormsModule],
  template: `
    @let d = latest();
    @let cv = resume();
    @let r = d.review ?? {};
    @let t = r.tailoring ?? {};
    <div class="block" data-testid="doc-resume">
      <h3>Tailored resume <span class="label">v{{ d.version }} · {{ d.source === 'ai' ? 'AI tailored' : 'your edit' }}</span></h3>
      @for (w of r.warnings ?? []; track $index) { <div class="msg bad">⚠ {{ w }}</div> }
      @if (r.notes?.length) {
        <details class="small muted" data-testid="resume-notes"><summary>Notes on the AI's writing ({{ r.notes.length }})</summary>
          @for (n of r.notes; track $index) { <div>{{ n }}</div> }
        </details>
      }
      <p class="small muted">{{ t.bullets_reworded ?? 0 }} bullets reworded (checked against your originals),
        {{ t.rewordings_rejected ?? 0 }} AI rewordings rejected, {{ t.bullets_omitted ?? 0 }} less relevant bullets left out.
        Summary: {{ summaryNote() }}.</p>
      @if (r.ats; as ats) {
        @if (noSkills(ats)) {
          <div class="ats" data-testid="ats-unavailable">
            <b>Job-description keyword coverage: not available</b>
            <div class="small muted">This job lists no skills to compare: usually only a short excerpt of the posting
              was available. Paste the full description above, then tailor the resume again.</div>
          </div>
        } @else {
        <div class="ats">
          @if (showCoverageScore()) {
          <b>Job-description keyword coverage: {{ ats.score }}%</b>
          <span class="muted small">(the share of the job description's technical keywords that appear on the resume, as an
            applicant tracking system scans it; was {{ ats.score_before }}% before adding keywords; {{ ats.max_honest_score }}%
            is the most your real experience supports)</span>
          }
          @if (ats.matched?.length) {
            <details data-testid="ats-matched"><summary>Found on the resume ({{ ats.matched.length }}): where an ATS sees each one</summary>
              @for (k of ats.matched; track k.keyword) {
                <div class="small"><span class="chip ok">{{ k.keyword }}</span> <span class="muted">{{ k.evidence ?? '' }}</span></div>
              }
            </details>
          }
          @if (r.keywords_added?.length) {
            <details><summary>Keywords added in the job's wording ({{ r.keywords_added.length }}): each backed by your profile</summary>
              @for (k of r.keywords_added; track k.keyword) {
                <div class="small"><span class="chip ok">{{ k.keyword }}</span> <span class="muted">{{ k.evidence }}</span></div>
              }
            </details>
          }
          @if (relatedOnly(ats).length) {
            <details><summary>Related experience ({{ relatedOnly(ats).length }}): shown here, not added to your skills</summary>
              @for (k of relatedOnly(ats); track k.keyword) {
                <div class="small"><span class="chip warn">{{ k.keyword }}</span> <span class="muted">{{ k.evidence }}</span></div>
              }
            </details>
          }
          @if (ats.gaps?.length) {
            <div class="small" data-testid="gaps">Gaps your profile doesn't cover:
              @for (g of ats.gaps; track g.keyword) {
                <span class="chip bad">{{ g.keyword }}</span>
                @if (!addedGaps().includes(g.keyword)) {
                  <button class="link small" type="button" (click)="openGap(g.keyword)">I have this</button>
                } @else { <span class="good">✓ added</span> }
              }
              <br><span class="muted">Not in your profile, so it isn't on the resume. If you really have it, click
                "I have this" and say where you used it.</span></div>
            @if (gap(); as k) {
              <div class="gap-form" data-testid="gap-form">
                <b>Where did you use {{ k }}?</b>
                <label class="small">Employer or project
                  <select [(ngModel)]="gapOwner" name="gapOwner">
                    @for (o of owners(); track o) { <option [value]="o">{{ o }}</option> }
                  </select></label>
                <label class="small">One line, in your words: what you did with {{ k }}
                  <textarea rows="2" [(ngModel)]="gapText" name="gapText"
                    [placeholder]="'e.g. Managed regression test cases and release runs in ' + k + '.'"></textarea></label>
                <button class="secondary" type="button" [disabled]="gapText.trim().length < 10 || !gapOwner"
                  (click)="saveGap(k)">Add to my profile</button>
                <button class="secondary" type="button" (click)="gap.set(null)">Cancel</button>
                @if (gapMsg(); as m) { <div class="msg bad">{{ m }}</div> }
              </div>
            }
            @if (addedGaps().length) {
              <div class="msg ok">Added to your profile: {{ addedGaps().join(', ') }}.
                <button class="secondary" type="button" [disabled]="working()" (click)="regenerate.emit()">
                  {{ working() ? 'Tailoring the resume…' : 'Tailor the resume again' }}</button></div>
            }
          } @else {
            <div class="small good">No keyword gaps.</div>
          }
          @if (ats.not_scored?.length) {
            <div class="small muted" data-testid="ats-not-scored">Not scored (people skills and ways of working, not
              technical keywords): {{ ats.not_scored.join(' · ') }}</div>
          }
        </div>
        }
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
      @if (summaryChecks(r).length) {
        <details class="small" data-testid="summary-checks"><summary>Summary sentences checked against your records
          ({{ summaryChecks(r).length }} changed)</summary>
          @for (c of summaryChecks(r); track $index) {
            <div class="claim">
              @if (c.ai_sentence) { <div class="muted"><b>The AI wrote:</b> “{{ c.ai_sentence }}”</div> }
              @for (u of c.unsupported; track $index) { <div class="warn-text">Unsupported {{ u.kind }}: {{ u.detail }}</div> }
              @for (x of c.style; track $index) { <div class="muted">{{ x }}</div> }
              @if (c.trimmed) { <div class="muted">Filler removed: “{{ c.trimmed }}”</div> }
              @if (c.records?.length) { <div class="muted">From your records: {{ recordIds(c.records) }}</div> }
              <div><b>{{ c.decision }}</b>{{ c.sentence ? ': “' + c.sentence + '”' : '' }}</div>
            </div>
          }
        </details>
      }
      @if (summarySources(r).length) {
        <details class="small" data-testid="summary-sources"><summary>Where each summary sentence comes from</summary>
          @for (c of summarySources(r); track $index) {
            <div class="claim">“{{ c.sentence }}” <span class="muted">· records {{ recordIds(c.records) }}</span></div>
          }
        </details>
      }
      @if (r.priorities?.length) {
        <details class="small" data-testid="evidence-priorities"><summary>What this posting screens for, and your evidence</summary>
          @for (p of r.priorities; track $index) {
            <div><b>{{ p.match }}</b> · {{ p.ask }} <span class="muted">{{ p.records?.length ? '· records ' + p.records.join(', ') : '· no record shows this' }}{{ p.main ? '' : ' · not in the summary' }}</span></div>
          }
        </details>
      }
      @if (r.final_check?.fixed?.length || r.final_check?.problems?.length || r.final_check?.first_third?.record) {
        <details class="small" data-testid="final-check"><summary>Final check before handing it back</summary>
          @if (r.final_check.first_third?.record) {
            <div>Strongest evidence for the first priority: {{ r.final_check.first_third.record }} ({{ r.final_check.first_third.where }})</div>
          }
          @for (x of r.final_check.fixed; track $index) { <div class="muted">Fixed: {{ x }}</div> }
          @for (x of r.final_check.problems; track $index) { <div class="warn-text">Check: {{ x }}</div> }
        </details>
      }
      @if (r.core_skills?.length) {
        <details class="small" data-testid="core-skills-evidence"><summary>Core Skills: why each skill is listed</summary>
          @for (s of r.core_skills; track $index) {
            <div><b>{{ s.line }}:</b> {{ s.skill }} <span class="muted">· {{ s.why }}{{ s.records?.length ? ' · records ' + s.records.join(', ') : '' }}</span></div>
          }
        </details>
      }
      <p class="small"><b>Skills order:</b> {{ cv.skills.slice(0, 12).join(' · ') }}{{ cv.skills.length > 12 ? ' …' : '' }}</p>
      <a class="button" [href]="'/documents/' + d.id + '/pdf'" download>Download PDF</a>
      @if (changed().length) {
        <details open><summary>Reworded bullets ({{ changed().length }}): compare with your original</summary>
          @for (c of changed(); track $index) {
            <div class="small"><span class="muted">{{ c.company }}{{ c.b.source_ids?.length ? ' · records ' + c.b.source_ids.join(', ') : c.b.source_id ? ' · record ' + c.b.source_id : '' }}</span><br><b>New:</b> {{ c.b.text }}<br>
              <span class="muted"><b>Original:</b> {{ c.b.original }}</span></div>
          }
        </details>
      }
      @if (rejected().length) {
        <details><summary>AI rewordings rejected ({{ rejected().length }})</summary>
          @for (c of rejected(); track $index) { <div class="small muted">{{ c.b.rejected_reason }}. Kept: {{ c.b.original }}</div> }
        </details>
      }
      @if (r.scorecard; as sc) {
        <details class="private-review" data-testid="private-review" open>
          <summary><b>Private review: how a recruiter would screen this resume</b>
            <span class="muted small">(for you only: never printed)</span></summary>
          <p class="small"><b>Role:</b> {{ sc.role_family }}.
            <b>What the hiring team likely values</b> <span class="muted">(my interpretation, not the posting)</span>:
            {{ sc.my_interpretation }}</p>
          <table class="small">
            <thead><tr><th>Screening criterion (from the posting)</th><th>Your evidence</th><th>The resume line that shows it</th></tr></thead>
            <tbody>
              @for (c of sc.criteria; track $index) {
                <tr [class.needs-you]="c.evidence === 'missing' || c.evidence === 'skills only'">
                  <td>{{ c.requirement }} <span class="muted">({{ c.kind }})</span></td>
                  <td><span class="chip" [class.good]="c.evidence === 'direct'" [class.warn]="c.evidence === 'transferable'"
                        [class.bad]="c.evidence === 'missing' || c.evidence === 'skills only'">{{ c.evidence }}</span>
                    @if (c.buried) { <span class="warn-text"> · below bullet 3</span> }</td>
                  <td>@if (c.resume_line) { "{{ c.resume_line }}" <span class="muted">({{ c.where }})</span> } @else { — }</td>
                </tr>
              }
            </tbody>
          </table>
          @if (sc.reasons_to_pass.length) {
            <p class="small"><b>Why a recruiter might pass:</b></p>
            <ol class="small">@for (x of sc.reasons_to_pass; track $index) { <li>{{ x }}</li> }</ol>
          }
          @if (sc.gaps.length) {
            <p class="small"><b>Real gaps</b> (required, nothing in your profile shows it): {{ sc.gaps.join(' · ') }}</p>
          }
          <details class="small"><summary>The top third, as a recruiter sees it in 20 seconds</summary>
            @for (t of sc.top_third; track $index) { <div>{{ t }}</div> }
          </details>
        </details>
      }
      @if (r.requirements_map?.length) {
        <details class="req-map" data-testid="requirements-map" open>
          <summary><b>Requirements → your experience → where it is on the resume</b>
            <span class="muted small">({{ mapCounts(r.requirements_map) }})</span></summary>
          <table class="small">
            <thead><tr><th>What the job asks</th><th>Match</th><th>Where it is on this resume</th></tr></thead>
            <tbody>
              @for (row of r.requirements_map; track $index) {
                <tr [class.needs-you]="row.strength === 'gap' || row.strength === 'skills only'"
                    [class.soft]="row.category === 'soft skill'">
                  <td>{{ row.requirement }}
                    <div class="muted">{{ row.kind }}{{ row.category !== 'technical' ? ' · ' + row.category : '' }}</div></td>
                  <td><span class="chip" [class.good]="row.strength === 'direct' || row.strength === 'shown by example'"
                        [class.warn]="row.strength === 'partial' || row.strength === 'transferable'"
                        [class.bad]="row.strength === 'gap' || row.strength === 'skills only'">{{ row.strength }}</span>
                    @if (row.found.length) { <div class="good">✓ {{ row.found.join(', ') }}</div> }
                    @for (x of elsewhere(row); track x.part) {
                      <div class="good" data-testid="found-elsewhere">✓ {{ x.part }} <span class="muted">({{ x.where }})</span></div>
                    }
                    @if (row.missing.length && row.category === 'technical') { <div class="warn-text">✗ {{ row.missing.join(', ') }}</div> }</td>
                  <td>
                    @if (row.where) { {{ row.where }} }
                    @for (x of row.on_resume; track x.id) {
                      <div><b>{{ x.where }}</b> <span class="muted">({{ x.id }})</span>: {{ x.line }}</div>
                    }
                    @if (row.more_on_resume) { <div class="muted">…and {{ row.more_on_resume }} more bullets</div> }
                    @if (row.not_printed.length) {
                      <div class="muted">Cut to fit two pages: {{ row.not_printed.join(', ') }}</div>
                    }
                    @if (!row.where && !row.on_resume.length && !row.not_printed.length) { <span class="muted">—</span> }
                  </td>
                </tr>
              }
            </tbody>
          </table>
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
  /** Summary sentences the checks changed (written without AI, trimmed or left out), with the reasons. */
  protected recordIds(records: { id: string }[]): string {
    return records.map(r => r.id).join(', ');
  }

  /** Summary sentences on the resume, each with the records it was written from. */
  protected summarySources(r: any): any[] {
    return (r?.summary_checks ?? []).filter((c: any) => c.sentence && c.records?.length
      && !String(c.decision).startsWith('left out') && !String(c.decision).includes('(reserve)'));
  }

  protected summaryChecks(r: any): any[] {
    return (r?.summary_checks ?? []).filter((c: any) => c.decision !== 'kept' || c.trimmed || c.unsupported?.length);
  }

  /** All versions of the resume, newest first. */
  readonly versions = input.required<Doc[]>();
  readonly showCoverageScore = input(true);

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

  /** Skills of a requirement shown by your skills list or by bank records that are not on this resume. */
  protected elsewhere(row: any): { part: string; where: string }[] {
    return Object.entries(row.found_elsewhere ?? {}).map(([part, where]) => ({ part, where: String(where) }));
  }

  /** "5 met · 2 partly · 1 gap · 3 soft skills" for the requirements map's heading. */
  protected mapCounts(rows: any[]): string {
    const tech = rows.filter(r => r.category !== 'soft skill');
    const n = (f: (r: any) => boolean) => tech.filter(f).length;
    const parts = [`${n(r => r.strength === 'direct')} met`,
                   `${n(r => ['partial', 'transferable', 'skills only'].includes(r.strength))} partly`,
                   `${n(r => r.strength === 'gap')} not met`];
    const soft = rows.length - tech.length;
    return parts.join(' · ') + (soft ? ` · ${soft} soft skills` : '');
  }

  /** No skills were compared (an excerpt-only job; older reports show the "Untitled job" placeholder). */
  protected noSkills(ats: any): boolean {
    if (ats.compared === 0) return true;
    const kws = [...(ats.matched ?? []), ...(ats.missing_but_evidenced ?? []), ...(ats.gaps ?? [])];
    return kws.length > 0 && kws.every((k: any) => k.keyword === 'Untitled job');
  }

  /** Keywords your experience relates to but doesn't name: not added to the resume, shown for your judgement. */
  protected relatedOnly(ats: any): any[] {
    const added = new Set(((this.versions()[0]?.review as any)?.keywords_added ?? []).map((k: any) => k.keyword));
    return (ats.missing_but_evidenced ?? []).filter((k: any) => !added.has(k.keyword));
  }

  private readonly api = inject(Api);
  /** Asks the page to tailor the resume again (after you added experience). */
  readonly regenerate = output<void>();
  /** True while the page is making a document: the regenerate button is disabled so it isn't clicked twice. */
  readonly working = input(false);
  private readonly resetOnNewVersion = effect(() => {
    this.versions();                          // a new resume version: the "added" note has done its job
    this.addedGaps.set([]);
  });
  protected readonly gap = signal<string | null>(null);
  protected readonly owners = signal<string[]>([]);
  protected readonly addedGaps = signal<string[]>([]);
  protected readonly gapMsg = signal<string | null>(null);
  protected gapOwner = '';
  protected gapText = '';

  protected async openGap(keyword: string) {
    this.gapMsg.set(null);
    this.gapText = '';
    if (!this.owners().length) {
      const p: any = await this.api.profile();
      this.owners.set([...(p.experience ?? []).map((e: any) => e.company), ...(p.projects ?? []).map((x: any) => x.name)]);
    }
    this.gapOwner = this.gapOwner || this.owners()[0] || '';
    this.gap.set(keyword);
  }

  protected async saveGap(keyword: string) {
    try {
      await this.api.addExperienceLine({ owner: this.gapOwner, text: this.gapText.trim(), skill: keyword });
      this.addedGaps.update(a => [...a, keyword]);
      this.gap.set(null);
    } catch (e) {
      this.gapMsg.set((e as ApiError).message);
    }
  }
}
