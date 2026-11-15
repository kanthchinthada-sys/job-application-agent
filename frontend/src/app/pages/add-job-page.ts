import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { Api, ApiError } from '../api';
import { Job, QuickAdd, splitSkills } from '../models';
import { Msg, MsgBox } from '../ui';

/** Add a job with just its link and description. Title, company, location and skills are read from the posting. */
@Component({
  selector: 'app-add-job-page',
  imports: [FormsModule, MsgBox],
  template: `
    <div class="card">
      <h2>Add a job</h2>
      <p class="hint">Paste the link and the job description. Everything else (title, company, location, skills) is
        read from the posting. Greenhouse, Lever and Ashby links work on their own.</p>
      <label for="j-url">Job link</label>
      <input id="j-url" [(ngModel)]="url" placeholder="https://…">
      <label for="j-desc">Job description</label>
      <textarea id="j-desc" [(ngModel)]="description" placeholder="Paste the full job description here"></textarea>
      @if (duplicate(); as d) {
        <div class="msg bad">{{ d }}
          <div><button class="primary" type="button" (click)="add(true)">It's a different posting: add it anyway</button>
               <button class="secondary" type="button" (click)="duplicate.set('')">Cancel</button></div>
        </div>
      } @else {
        <button class="primary" type="button" [disabled]="busy()" (click)="add(false)">{{ busy() ? 'Reading the posting…' : 'Add job' }}</button>
      }
      <app-msg [msg]="msg()" />
    </div>

    @if (added(); as a) {
      <div class="card" data-testid="added-job">
        <h2>Added</h2>
        <div class="row">
          <div><label for="a-title">Job title</label><input id="a-title" [(ngModel)]="title"></div>
          <div><label for="a-company">Company</label><input id="a-company" [(ngModel)]="company"></div>
          <div><label for="a-location">Location</label><input id="a-location" [(ngModel)]="location"></div>
        </div>
        <label for="a-required">Required skills <span class="muted">(read from the posting; edit freely)</span></label>
        <input id="a-required" [(ngModel)]="required">
        <label for="a-preferred">Preferred skills</label>
        <input id="a-preferred" [(ngModel)]="preferred">
        <p class="small muted">Found by: {{ foundBy(a) }}</p>
        @for (n of a.intake.notes; track $index) { <div class="small warn-text">{{ n }}</div> }
        <button class="secondary" type="button" (click)="saveChanges(a.job)">Save changes</button>
        <button class="primary" type="button" (click)="router.navigate(['/jobs', a.job.id])">See skill match</button>
        <app-msg [msg]="editMsg()" />
      </div>
    }
  `,
})
export class AddJobPage {
  private api = inject(Api);
  protected router = inject(Router);

  protected readonly url = signal('');
  protected readonly description = signal('');
  protected readonly busy = signal(false);
  protected readonly msg = signal<Msg | null>(null);
  protected readonly duplicate = signal('');
  protected readonly added = signal<QuickAdd | null>(null);
  protected readonly title = signal('');
  protected readonly company = signal('');
  protected readonly location = signal('');
  protected readonly required = signal('');
  protected readonly preferred = signal('');
  protected readonly editMsg = signal<Msg | null>(null);

  async add(allowDuplicate: boolean) {
    this.busy.set(true);
    this.duplicate.set('');
    this.added.set(null);
    this.msg.set({ kind: 'info', text: 'Reading the posting… (with the local AI this can take up to a minute)' });
    try {
      const r = await this.api.quickAddJob({
        url: this.url().trim() || null, description: this.description().trim() || null, allow_duplicate: allowDuplicate,
      });
      const j = r.job;
      this.added.set(r);
      this.title.set(j.title); this.company.set(j.company); this.location.set(j.location ?? '');
      this.required.set(j.required_skills.join(', ')); this.preferred.set(j.preferred_skills.join(', '));
      this.msg.set({ kind: 'ok', text: `Added “${j.title}” at ${j.company}. Check the details below.` });
      this.url.set(''); this.description.set('');
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 409 && /allow_duplicate/.test(err.message)) {
        this.duplicate.set(err.message.replace(/ If this really is a different posting.*$/, ''));
        this.msg.set(null);
      } else {
        this.msg.set({ kind: 'bad', text: err.message });
      }
    } finally {
      this.busy.set(false);
    }
  }

  protected foundBy(a: QuickAdd): string {
    return Object.entries(a.intake.found_by).map(([k, v]) => `${k}: ${v}`).join(' · ');
  }

  async saveChanges(job: Job) {
    const required = splitSkills(this.required());
    if (!required.length) {
      this.editMsg.set({ kind: 'bad', text: 'Keep at least one required skill.' });
      return;
    }
    try {
      const body: Record<string, unknown> = {
        title: this.title(), company: this.company(), required_skills: required,
        preferred_skills: splitSkills(this.preferred()),
      };
      if (this.location().trim()) body['location'] = this.location().trim();
      await this.api.updateJob(job.id, body);
      this.editMsg.set({ kind: 'ok', text: 'Saved.' });
    } catch (e) {
      this.editMsg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }
}
