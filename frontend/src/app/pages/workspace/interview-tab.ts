import { Component, effect, inject, input, signal, untracked } from '@angular/core';

import { Api, ApiError } from '../../api';
import { InterviewPrep } from '../../models';

@Component({
  selector: 'app-interview-tab',
  template: `
    <div class="card">
      <h3>Interview prep</h3>
      @if (error()) { <p class="warn-text" role="alert">{{ error() }}</p> }
      @else if (prep(); as p) {
        <p class="small">Practice with the job requirements and examples you can explain. For each example,
          describe the situation or task, your action, and the result you actually observed.</p>
        @for (v of versions(p); track v[0]) {
          <a class="button tight" [href]="'/documents/' + v[1].id + '/print'" target="_blank" rel="noopener">
            {{ v[0] === 'resume' ? 'Resume' : 'Cover letter' }} v{{ v[1].version }}
            · {{ v[1].submitted ? 'submitted' : 'latest draft' }}</a>
        }
        @for (q of p.questions; track q.record_id) {
          <details class="panel">
            <summary><b>{{ q.question }}</b> {{ q.topic }}</summary>
            <p><b>{{ q.owner }}</b> <span class="label">{{ q.record_id }}</span>
              @if (q.on_resume) { <span class="label">On this resume</span> }
              @if (q.in_letter) { <span class="label">In this cover letter</span> }</p>
            <p>{{ q.evidence }}</p>
            @if (q.caveat) { <p class="small muted">{{ q.caveat }}</p> }
          </details>
        } @empty { <p class="muted">Add verified examples to your experience bank to build practice stories.</p> }
        @if (p.topics_to_review.length) {
          <h4>Topics to prepare</h4>
          <p>{{ p.topics_to_review.join(' · ') }}</p>
          <p class="small muted">Practice explaining related experience and how you would get up to speed.</p>
        }
      } @else { <p class="muted">Loading interview prep…</p> }
    </div>
  `,
})
export class InterviewTab {
  private api = inject(Api);
  readonly appId = input.required<number>();
  protected readonly prep = signal<InterviewPrep | null>(null);
  protected readonly error = signal('');
  protected versions(p: InterviewPrep) { return Object.entries(p.versions); }

  constructor() {
    effect(() => { const id = this.appId(); untracked(() => this.load(id)); });
  }

  private async load(id: number) {
    this.error.set('');
    this.prep.set(null);
    try { this.prep.set(await this.api.interviewPrep(id)); }
    catch (e) { this.error.set((e as ApiError).message); }
  }
}
