import { Component, effect, inject, input, OnInit, signal, untracked } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { Router } from '@angular/router';

import { Api, ApiError } from '../api';
import { AIMatchReport, Application, Job, MatchReport, pretty, SkillResult, SkillSection } from '../models';
import { Msg } from '../ui';

/** Jobs list, and the keyword skill match for the job in the URL (/jobs/:id). */
@Component({
  selector: 'app-jobs-page',
  template: `
    <div class="card">
      <h2>Jobs</h2>
      @if (jobs() === null) {
        <p class="muted">Loading…</p>
      } @else if (jobs()!.length === 0) {
        <p class="muted">No jobs yet. New here? 1. Upload your resume on the <b>Profile</b> tab. 2. Use <b>Find jobs</b> to
          search by title, country and date, or <b>Add job</b> to paste a posting you found.</p>
      } @else {
        <table>
          <thead><tr><th>Job</th><th>Location</th><th>Application</th><th></th></tr></thead>
          <tbody>
            @for (j of jobs(); track j.id) {
              <tr>
                <td><b>{{ j.title }}</b><br><span class="muted">{{ j.company }}</span>
                  @if (j.source_url) { <br><a [href]="j.source_url" target="_blank" rel="noopener" class="small">posting</a> }
                </td>
                <td>{{ j.location || '—' }}</td>
                <td>
                  @if (appsByJob()[j.id]; as a) {
                    <span class="label">{{ pretty(a.status) }}</span>
                  } @else {
                    <button class="secondary tight" type="button" (click)="track(j.id)">Track application</button>
                  }
                </td>
                <td><button class="secondary tight" type="button" (click)="openMatch(j.id)">Skill match</button></td>
              </tr>
            }
          </tbody>
        </table>
      }
      @if (error(); as e) { <div class="msg bad">{{ e }}</div> }
    </div>

    @if (id()) {
      <div class="card" data-testid="match">
        <h2>Skill match{{ matchJob() ? ' · ' + matchJob()!.title + ' at ' + matchJob()!.company : '' }}</h2>
        @if (matchError(); as e) {
          <p [class.msg]="!needsProfile()" [class.bad]="!needsProfile()">{{ e }}</p>
        } @else if (match(); as r) {
          <span class="label">{{ r.method_label }}</span>
          <ng-container *ngTemplateOutlet="section; context: { name: 'Required', s: r.required }" />
          <ng-container *ngTemplateOutlet="section; context: { name: 'Preferred', s: r.preferred }" />
          @if (hasMissing(r)) {
            <div class="ai-look">
              <button class="secondary" type="button" [disabled]="aiBusy()" (click)="askAi(r.job_id)">
                Ask AI to look for related experience behind the ✗ skills</button>
              @if (aiMsg(); as m) { <div class="msg" [class]="'msg ' + m.kind">{{ m.text }}</div> }
              @if (ai(); as a) {
                <p class="small muted">{{ a.method_label }}</p>
                @for (sg of a.suggestions; track sg.requirement) {
                  <span class="chip warn">AI: {{ sg.requirement }}</span>
                  <div class="evidence">“{{ sg.quote }}” ({{ sg.fact_id }}) · {{ sg.reason }}</div>
                }
                @if (a.still_missing.length) {
                  <p class="small">Still no evidence: {{ a.still_missing.join(', ') }}. If you do have this experience,
                    add it to your profile in your own words and it will be found next time.</p>
                }
              }
            </div>
          }
          <details><summary>How this comparison works</summary>
            <ul class="small muted">@for (l of r.limitations; track $index) { <li>{{ l }}</li> }</ul>
          </details>
        } @else {
          <p class="muted">Comparing…</p>
        }
      </div>
    }

    <ng-template #section let-name="name" let-s="s">
      @if (asSection(s).total) {
        <h3>{{ name }}: {{ asSection(s).matched.length + (asSection(s).related?.length ?? 0) }} of {{ asSection(s).total }} shown by your profile
          <span class="muted small">({{ asSection(s).coverage_percent }}%)</span></h3>
        @for (m of asSection(s).matched; track m.skill) {
          <span class="chip ok">✓ {{ m.skill }}</span>
          <div class="evidence">{{ explain(m) }}</div>
        }
        @for (m of asSection(s).related ?? []; track m.skill) {
          <span class="chip ok related">≈ {{ m.skill }}</span>
          <div class="evidence">related experience, check it: {{ explain(m) }}</div>
        }
        @for (m of asSection(s).mentioned_only; track m.skill) {
          <span class="chip warn">? {{ m.skill }}</span>
          <div class="evidence">mentioned in {{ m.evidence[0]?.source }}, but not in your skill lists. Check it yourself.</div>
        }
        @for (m of asSection(s).missing; track m) { <span class="chip bad">✗ {{ m }}</span> }
      }
    </ng-template>
  `,
  imports: [NgTemplateOutlet],
})
export class JobsPage implements OnInit {
  private api = inject(Api);
  private router = inject(Router);

  /** From the URL /jobs/:id (router input binding). */
  readonly id = input<string>();

  protected readonly jobs = signal<Job[] | null>(null);
  protected readonly appsByJob = signal<Record<number, Application>>({});
  protected readonly error = signal('');
  protected readonly match = signal<MatchReport | null>(null);
  protected readonly matchJob = signal<Job | null>(null);
  protected readonly matchError = signal('');
  protected readonly needsProfile = signal(false);
  protected readonly ai = signal<AIMatchReport | null>(null);
  protected readonly aiMsg = signal<Msg | null>(null);
  protected readonly aiBusy = signal(false);
  protected readonly pretty = pretty;

  constructor() {
    effect(() => {
      const id = this.id();
      untracked(() => { if (id) this.loadMatch(Number(id)); });
    });
  }

  async ngOnInit() {
    try {
      const [jobs, apps] = await Promise.all([this.api.jobs(), this.api.applications()]);
      this.appsByJob.set(Object.fromEntries(apps.map(a => [a.job_id, a])));
      this.jobs.set(jobs);
    } catch (e) {
      this.error.set((e as ApiError).message);
      this.jobs.set([]);
    }
  }

  openMatch(jobId: number) { this.router.navigate(['/jobs', jobId]); }

  async loadMatch(jobId: number) {
    this.match.set(null); this.matchJob.set(null); this.matchError.set(''); this.needsProfile.set(false);
    this.ai.set(null); this.aiMsg.set(null);
    try {
      const [job, report] = await Promise.all([this.api.job(jobId), this.api.match(jobId)]);
      this.matchJob.set(job);
      this.match.set(report);
    } catch (e) {
      const err = e as ApiError;
      const noProfile = err.status === 404 && /profile/i.test(err.message);
      this.needsProfile.set(noProfile);
      this.matchError.set(noProfile ? 'Save your profile first (Profile tab), then try again.' : err.message);
    }
  }

  async track(jobId: number) {
    try {
      const app = await this.api.startApplication(jobId);
      this.router.navigate(['/applications', app.id]);
    } catch (e) {
      this.error.set((e as ApiError).message);
    }
  }

  protected asSection(s: unknown) { return s as SkillSection; }

  /** "python, java, go (Scala not found) · from Automated Test Developer at …: “…”" */
  protected explain(m: SkillResult) {
    const first = m.evidence[0];
    const parts = m.found_parts?.length > 1 || m.missing_parts?.length
      ? `${m.found_parts.join(', ')}${m.missing_parts.length ? ` (not found: ${m.missing_parts.join(', ')})` : ''} · ` : '';
    const via = first.via && first.via.toLowerCase() !== m.skill.toLowerCase() ? `“${first.via}” in ` : '';
    const quote = first.text.length > 140 ? first.text.slice(0, 140) + '…' : first.text;
    return `${parts}${via}${first.source}: ${quote}`;
  }

  protected hasMissing(r: MatchReport) { return r.required.missing.length + r.preferred.missing.length > 0; }

  async askAi(jobId: number) {
    this.aiBusy.set(true);
    this.ai.set(null);
    this.aiMsg.set({ kind: 'info', text: 'The local AI is reading your profile… (up to a minute)' });
    try {
      this.ai.set(await this.api.aiMatch(jobId));
      this.aiMsg.set(null);
    } catch (e) {
      this.aiMsg.set({ kind: 'bad', text: (e as ApiError).message });
    } finally {
      this.aiBusy.set(false);
    }
  }
}
