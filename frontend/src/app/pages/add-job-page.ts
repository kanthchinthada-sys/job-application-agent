import { GapReviewPanel } from './gap-review-panel';
import { Component, inject, OnDestroy, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { Api, ApiError } from '../api';
import { Application, IntakeStatus, Job, QuickAdd, splitSkills } from '../models';
import { Msg, MsgBox } from '../ui';

type Mode = 'link' | 'paste';

/** Add a job two ways: import it from its link (Greenhouse, Lever, Ashby and most job pages), or paste the description.
 *  Title, company, location and skills are read from the posting and the job is saved at once; fields that are
 *  missing or uncertain are highlighted. If the local AI is checking them in the background, its answer fills only
 *  the fields you haven't changed (the server enforces this too). */
type Field = 'title' | 'company' | 'location' | 'skills';
const UNKNOWN_COMPANY = 'Unknown company';
@Component({
  selector: 'app-add-job-page',
  imports: [FormsModule, MsgBox, GapReviewPanel],
  template: `
    <div class="card">
      <h2>Add a job</h2>
      <div class="seg" role="group" aria-label="How to add the job" style="margin-bottom:6px">
        <button type="button" [attr.aria-pressed]="mode() === 'link'" (click)="setMode('link')" data-testid="mode-link">Import from link</button>
        <button type="button" [attr.aria-pressed]="mode() === 'paste'" (click)="setMode('paste')" data-testid="mode-paste">Paste description</button>
      </div>
      @if (mode() === 'link') {
        <p class="hint">Greenhouse, Lever and Ashby links are read from the job board itself; most other job pages work too.</p>
        <label for="j-url">Job link</label>
        <input id="j-url" [(ngModel)]="url" placeholder="https://boards.greenhouse.io/…" (keyup.enter)="add(false)">
      } @else {
        <p class="hint">Paste the whole posting. Title, company, location and skills are read from it.</p>
        <label for="j-desc">Job description</label>
        <textarea id="j-desc" [(ngModel)]="description" placeholder="Paste the full job description here" style="min-height:220px"></textarea>
        <div class="row">
          <div><label for="j-url2">Link to the posting <span class="muted">(optional)</span></label>
            <input id="j-url2" [(ngModel)]="url" placeholder="https://…"></div>
          <div><label for="j-company">Company <span class="muted">(optional: kept exactly as you type it)</span></label>
            <input id="j-company" [(ngModel)]="companyIn" placeholder="Read from the posting if left empty"></div>
        </div>
      }
      @if (importFailed()) {
        <div class="msg bad" data-testid="import-failed">{{ importFailed() }}
          <div><button class="secondary" type="button" (click)="setMode('paste')">Paste the description instead</button></div>
        </div>
      }
      @if (duplicate(); as d) {
        <div class="panel" data-testid="duplicate">
          <b>You already saved this job:</b> {{ d.job.title }} at {{ d.job.company }}{{ d.job.location ? ' · ' + d.job.location : '' }}
          <div class="actions">
            @if (d.app) {
              <button class="primary" type="button" (click)="router.navigate(['/applications', d.app.id])">Open application</button>
            } @else {
              <button class="primary" type="button" (click)="prepareExisting(d.job)">Prepare application</button>
            }
            <button class="secondary" type="button" (click)="add(true)">It's a different posting: add it anyway</button>
          </div>
        </div>
      } @else if (!added()) {
        <button class="primary" type="button" [disabled]="busy()" (click)="add(false)">
          {{ busy() ? (mode() === 'link' ? 'Importing…' : 'Reading the posting…') : (mode() === 'link' ? 'Import job' : 'Add job') }}</button>
      }
      <app-msg [msg]="msg()" />
    </div>

    @if (added(); as a) {
      <div class="card" data-testid="added-job">
        <h2>Check the details</h2>
        @if (a.job.pending_technical_gaps) {
          <app-gap-review-panel [jobId]="a.job.id" />
        }
        @if (aiState() === 'pending') {
          <div class="msg info" data-testid="ai-pending">The local AI is checking the highlighted fields in the background.
            You can correct them now: anything you change is kept, even if the AI answers later.</div>
        } @else if (aiState() === 'failed') {
          <div class="msg warn" data-testid="ai-failed">The local AI couldn't finish, so only what was read from the posting is
            filled in. Check the highlighted fields.</div>
        }
        @if (applied().length) { <div class="small ok-text">Filled in by the local AI: {{ applied().join(', ') }}.</div> }
        @if (kept().length) { <div class="small">Kept your correction for: {{ kept().join(', ') }}.</div> }
        <div class="row">
          <div><label for="a-title">Job title @if (needs('title')) { <span class="unsure-chip">Check this</span> }</label>
            <input id="a-title" [class.unsure]="needs('title')" [(ngModel)]="title"></div>
          <div><label for="a-company">Company @if (needs('company')) { <span class="unsure-chip">{{ company() ? 'Check this' : 'Not found' }}</span> }</label>
            <input id="a-company" [class.unsure]="needs('company')" [(ngModel)]="company" placeholder="Company name (not found in the posting)"></div>
          <div><label for="a-location">Location</label><input id="a-location" [(ngModel)]="location"></div>
        </div>
        <label for="a-required">Required skills <span class="muted">(read from the posting; edit freely)</span>
          @if (needs('skills')) { <span class="unsure-chip">Few found</span> }</label>
        <input id="a-required" [class.unsure]="needs('skills')" [(ngModel)]="required">
        <label for="a-preferred">Preferred skills</label>
        <input id="a-preferred" [(ngModel)]="preferred">
        <p class="small muted">Found by: {{ foundBy(a) }}</p>
        @for (n of notes(); track $index) { <div class="small warn-text">{{ n }}</div> }
        <details class="small" data-testid="posting-kept">
          <summary>The posting as saved{{ a.job.source_url ? ' and its link' : '' }}</summary>
          @if (a.job.source_url) { <p><a [href]="a.job.source_url" target="_blank" rel="noopener">{{ a.job.source_url }}</a></p> }
          <pre class="posting-text">{{ a.job.description }}</pre>
        </details>
        <div class="actions">
          <button class="primary" type="button" [disabled]="preparing()" (click)="prepare(a.job)">
            {{ preparing() ? 'Opening…' : 'Prepare application' }}</button>
          <button class="secondary" type="button" (click)="saveChanges(a.job)">Save changes</button>
          <button class="secondary" type="button" (click)="reset()">Add another job</button>
        </div>
        <app-msg [msg]="editMsg()" />
      </div>
    }
  `,
  styles: [`.posting-text { white-space: pre-wrap; max-height: 260px; overflow: auto; background: var(--bg, #f8fafc);
    padding: 8px; border-radius: 6px; font: inherit; }`],
})
export class AddJobPage implements OnDestroy {
  static pollMs = 2000;           // how often the AI's background step is checked (tests: 0)
  private api = inject(Api);
  protected router = inject(Router);

  protected readonly mode = signal<Mode>('paste');
  protected readonly url = signal('');
  protected readonly description = signal('');
  protected readonly busy = signal(false);
  protected readonly msg = signal<Msg | null>(null);
  protected readonly importFailed = signal('');
  protected readonly duplicate = signal<{ job: Job; app: Application | null } | null>(null);
  protected readonly added = signal<QuickAdd | null>(null);
  protected readonly title = signal('');
  protected readonly company = signal('');
  protected readonly location = signal('');
  protected readonly required = signal('');
  protected readonly preferred = signal('');
  protected readonly editMsg = signal<Msg | null>(null);
  protected readonly preparing = signal(false);
  protected readonly companyIn = signal('');
  protected readonly review = signal<string[]>([]);
  protected readonly notes = signal<string[]>([]);
  protected readonly aiState = signal<string>('not_needed');
  protected readonly applied = signal<string[]>([]);
  protected readonly kept = signal<string[]>([]);
  /** The values as saved on the server: a form field that differs from these is your edit. */
  private saved: Record<Field | 'preferred', string> = { title: '', company: '', location: '', skills: '', preferred: '' };
  private timer: ReturnType<typeof setTimeout> | null = null;
  private polling = 0;            // which add the poll belongs to: an older poll stops after "Add another job"

  ngOnDestroy() { this.stopPolling(); }

  private stopPolling() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.polling++;
  }

  private fill(j: Job) {
    const company = j.company === UNKNOWN_COMPANY ? '' : j.company;
    this.title.set(j.title); this.company.set(company); this.location.set(j.location ?? '');
    this.required.set(j.required_skills.join(', ')); this.preferred.set(j.preferred_skills.join(', '));
    this.saved = { title: j.title, company, location: j.location ?? '', skills: j.required_skills.join(', '),
                   preferred: j.preferred_skills.join(', ') };
  }

  private value(f: Field | 'preferred'): string {
    return { title: this.title, company: this.company, location: this.location, skills: this.required,
             preferred: this.preferred }[f]().trim();
  }

  /** You changed this field in the form (not yet saved, or saved by you). */
  protected edited(f: Field | 'preferred'): boolean { return this.value(f) !== this.saved[f].trim() || this.mine.has(f); }
  private mine = new Set<string>();

  protected needs(f: Field): boolean { return this.review().includes(f) && !this.edited(f); }

  /** While the AI runs: take only the fields it filled and you haven't touched. */
  private poll(jobId: number) {
    const mine = this.polling;
    const tick = async () => {
      if (mine !== this.polling) return;
      let s: IntakeStatus;
      try {
        s = await this.api.jobIntake(jobId);
      } catch {
        this.timer = setTimeout(tick, AddJobPage.pollMs * 2);
        return;
      }
      if (mine !== this.polling) return;
      if (s.ai_state === 'pending') { this.timer = setTimeout(tick, AddJobPage.pollMs); return; }
      const j = s.job;
      const fresh: Record<string, () => void> = {
        title: () => { this.title.set(j.title); this.saved.title = j.title; },
        company: () => { this.company.set(j.company); this.saved.company = j.company; },
        location: () => { this.location.set(j.location ?? ''); this.saved.location = j.location ?? ''; },
        skills: () => {
          this.required.set(j.required_skills.join(', ')); this.saved.skills = j.required_skills.join(', ');
          if (!this.edited('preferred')) {
            this.preferred.set(j.preferred_skills.join(', ')); this.saved.preferred = j.preferred_skills.join(', ');
          }
        },
      };
      const took = s.applied.filter(f => !this.edited(f as Field));
      took.forEach(f => fresh[f]?.());
      this.applied.set(took);
      this.kept.set([...new Set([...s.kept, ...s.applied.filter(f => !took.includes(f))])]);
      this.review.set(s.review);
      this.notes.set(s.notes);
      this.aiState.set(s.ai_state);
      const a = this.added();
      if (a) this.added.set({ ...a, job: s.job, intake: { ...a.intake, found_by: s.found_by } });
    };
    this.timer = setTimeout(tick, AddJobPage.pollMs);
  }

  setMode(m: Mode) {
    this.mode.set(m);
    this.importFailed.set('');
    this.msg.set(null);
  }

  reset() {
    this.stopPolling();
    this.review.set([]); this.notes.set([]); this.aiState.set('not_needed'); this.applied.set([]); this.kept.set([]);
    this.mine.clear(); this.companyIn.set('');
    this.added.set(null); this.duplicate.set(null); this.msg.set(null); this.editMsg.set(null);
    this.url.set(''); this.description.set('');
  }

  async add(allowDuplicate: boolean) {
    if (this.busy()) return;
    const url = this.url().trim();
    const description = this.mode() === 'paste' ? this.description().trim() : '';
    if (this.mode() === 'link' && !url) { this.msg.set({ kind: 'bad', text: 'Paste the job link first.' }); return; }
    if (this.mode() === 'paste' && description.length < 50) {
      this.msg.set({ kind: 'bad', text: 'Paste the whole job description (it looks too short).' });
      return;
    }
    this.busy.set(true);
    this.stopPolling();
    this.mine.clear(); this.applied.set([]); this.kept.set([]);
    this.duplicate.set(null);
    this.importFailed.set('');
    this.added.set(null);
    this.msg.set({ kind: 'info', text: 'Reading the posting…' });
    try {
      const body: { url: string | null; description: string | null; allow_duplicate: boolean; company?: string } =
        { url: url || null, description: description || null, allow_duplicate: allowDuplicate };
      if (this.mode() === 'paste' && this.companyIn().trim()) body.company = this.companyIn().trim();
      const r = await this.api.quickAddJob(body);
      const j = r.job;
      this.added.set(r);
      this.fill(j);
      this.review.set(r.intake.review ?? []);
      this.notes.set(r.intake.notes);
      this.aiState.set(r.intake.ai_state ?? 'not_needed');
      const where = j.company === UNKNOWN_COMPANY ? '' : ` at ${j.company}`;
      this.msg.set({ kind: 'ok', text: `Saved “${j.title}”${where}. ` + (this.review().length
        ? 'Check the highlighted fields below.' : 'Check the details below.') });
      if (this.aiState() === 'pending') this.poll(j.id);
    } catch (e) {
      const err = e as ApiError;
      this.msg.set(null);
      if (err.status === 409 && err.data?.existing_id) {
        await this.showDuplicate(err.data.existing_id);
      } else if (this.mode() === 'link') {
        // keep the link, and offer the other way right away
        this.importFailed.set(`Couldn't import from this link: ${err.message}`);
      } else {
        this.msg.set({ kind: 'bad', text: err.message });
      }
    } finally {
      this.busy.set(false);
    }
  }

  private async showDuplicate(jobId: number) {
    try {
      const [job, apps] = await Promise.all([this.api.job(jobId), this.api.applications()]);
      this.duplicate.set({ job, app: apps.find(a => a.job_id === jobId) ?? null });
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }

  protected foundBy(a: QuickAdd): string {
    return Object.entries(a.intake.found_by).map(([k, v]) => `${k}: ${v}`).join(' · ');
  }

  /** Start the application and open its workspace, where one click generates both documents. */
  async prepare(job: Job) {
    this.preparing.set(true);
    try {
      if (!(await this.saveChanges(job, true))) return;
      await this.prepareExisting(job);
    } finally {
      this.preparing.set(false);
    }
  }

  async prepareExisting(job: Job) {
    try {
      let appId: number;
      try {
        appId = (await this.api.startApplication(job.id)).id;
      } catch (e) {
        const err = e as ApiError;
        if (err.status !== 409 || !err.data?.existing_id) throw e;
        appId = err.data.existing_id;
      }
      this.router.navigate(['/applications', appId]);
    } catch (e) {
      this.editMsg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }

  /** Saves only what you changed, so fields you left alone can still be filled in by the AI. */
  async saveChanges(job: Job, quiet = false): Promise<boolean> {
    const body: Record<string, unknown> = {};
    if (this.edited('title')) {
      if (!this.title().trim()) { this.editMsg.set({ kind: 'bad', text: 'Keep a job title.' }); return false; }
      body['title'] = this.title().trim();
    }
    if (this.edited('company')) {
      if (!this.company().trim()) { this.editMsg.set({ kind: 'bad', text: 'Add the company name.' }); return false; }
      body['company'] = this.company().trim();
    }
    if (this.edited('location') && this.location().trim()) body['location'] = this.location().trim();
    if (this.edited('skills')) {
      const required = splitSkills(this.required());
      if (!required.length) { this.editMsg.set({ kind: 'bad', text: 'Keep at least one required skill.' }); return false; }
      body['required_skills'] = required;
    }
    if (this.edited('preferred')) body['preferred_skills'] = splitSkills(this.preferred());
    if (!Object.keys(body).length) {
      if (!quiet) this.editMsg.set({ kind: 'ok', text: 'Nothing changed.' });
      return true;
    }
    try {
      const saved = await this.api.updateJob(job.id, body);
      for (const k of Object.keys(body)) this.mine.add(k === 'required_skills' ? 'skills' : k === 'preferred_skills' ? 'preferred' : k);
      this.review.set(this.review().filter(f => !this.mine.has(f)));
      const a = this.added();
      if (a) this.added.set({ ...a, job: saved });
      if (!quiet) this.editMsg.set({ kind: 'ok', text: 'Saved.' });
      return true;
    } catch (e) {
      this.editMsg.set({ kind: 'bad', text: (e as ApiError).message });
      return false;
    }
  }
}
