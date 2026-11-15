import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../api';
import { Application, Doc } from '../models';
import { Msg, MsgBox } from '../ui';
import { ApplyPlan } from './apply-plan';
import { HandoffPanel } from './handoff-panel';
import { ResumeCard } from './resume-card';

/** One application's materials: AI drafts with their checks, your edits as versions, and the ways to apply. */
@Component({
  selector: 'app-materials-panel',
  imports: [FormsModule, DatePipe, MsgBox, ResumeCard, HandoffPanel, ApplyPlan],
  template: `
    <div class="card" data-testid="materials">
      <h2>Materials · {{ application().job_title }} at {{ application().company }}</h2>
      <p class="hint">Drafted by your local AI from your profile only. Always read the checks before using a draft.</p>
      <button class="primary" type="button" [disabled]="busy()" (click)="draft({ kind: 'cover_letter' })">Draft cover letter</button>
      <button class="primary" type="button" [disabled]="busy()" (click)="draft({ kind: 'resume' })">Tailor resume</button>
      <label for="sq">Screening question</label>
      <div class="row">
        <input id="sq" class="wide" [(ngModel)]="question" placeholder="e.g. Are you legally eligible to work in Canada?">
        <button class="secondary tight" type="button" [disabled]="busy()" (click)="draftAnswer()">Draft answer</button>
      </div>
      <app-msg [msg]="msg()" />

      <button class="secondary" type="button" (click)="showHandoff.set(!showHandoff())">Apply by hand: checklist</button>
      @if (showHandoff()) { <app-handoff-panel [appId]="application().id" (changed)="changed.emit()" /> }
      <app-apply-plan [appId]="application().id" (changed)="changed.emit()" />

      @if (groups().length === 0) {
        <p class="muted">No materials yet.</p>
      }
      @for (versions of groups(); track versions[0].id) {
        @let d = versions[0];
        @if (d.kind === 'resume') {
          <app-resume-card [versions]="versions" />
        } @else {
          <div class="block" [attr.data-testid]="'doc-' + d.kind">
            <h3>{{ d.kind === 'cover_letter' ? 'Cover letter' : 'Screening: “' + d.question + '”' }}
              <span class="label">v{{ d.version }} · {{ d.source === 'ai' ? 'AI draft' : 'your edit' }}</span></h3>
            @for (w of d.review?.warnings ?? []; track $index) { <div class="msg bad">⚠ {{ w }}</div> }
            @if (d.review?.questions_for_you?.length) {
              <div class="msg info">The AI needs from you:@for (q of d.review.questions_for_you; track $index) {
• {{ q }}}</div>
            }
            @if (d.review?.humanizer; as h) {
              <div class="small">Human-style score: <b>{{ h.score }}/100</b>
                @if (h.rewritten) { <span class="muted">(first draft {{ h.score_first_draft }}, rewritten by the humanizer)</span> }
                @if (h.remaining?.length) {
                  <br><span class="muted">Still sounds AI-written:</span>
                  @for (f of h.remaining; track $index) { <span class="chip warn" [title]="f.pattern">{{ f.text }}</span> }
                }
              </div>
            }
            @if (d.source === 'ai' && d.review?.warnings?.length === 0) {
              <div class="small good">✓ Automatic checks passed. Still read it before use.</div>
            }
            <textarea [attr.aria-label]="'Text of ' + d.kind" [style.min-height.px]="d.kind === 'cover_letter' ? 260 : 110"
                      [ngModel]="d.content" (ngModelChange)="edits.set(d.id, $event)"></textarea>
            <button class="secondary" type="button" (click)="saveVersion(d)">Save my edits as new version</button>
            <button class="secondary" type="button" (click)="copy(edits.get(d.id) ?? d.content)">Copy</button>
            @if (d.review?.priorities?.length) {
              <details><summary>Written around the job's top requirements ({{ d.review.priorities.length }})</summary>
                <ol class="small muted">@for (p of d.review.priorities; track $index) { <li>{{ p.requirement }} <span class="label">{{ p.fact_ids.join(', ') }}</span></li> }</ol>
              </details>
            }
            @if (d.review?.facts_used?.length) {
              <details><summary>Profile facts used ({{ d.review.facts_used.length }})</summary>
                <ul class="small muted">@for (f of d.review.facts_used; track f.id) { <li><b>{{ f.id }}</b> {{ f.text }}</li> }</ul>
              </details>
            }
            @if (versions.length > 1) {
              <details><summary>Older versions ({{ versions.length - 1 }})</summary>
                @for (v of versions.slice(1); track v.id) {
                  <div class="small muted">v{{ v.version }} · {{ v.source === 'ai' ? 'AI draft' : 'your edit' }} · {{ v.created_at | date: 'medium' }}</div>
                  <pre class="old">{{ v.content }}</pre>
                }
              </details>
            }
          </div>
        }
      }
    </div>
  `,
})
export class MaterialsPanel {
  private api = inject(Api);

  readonly application = input.required<Application>();
  /** Tells the page to reload the applications (a status may have changed). */
  readonly changed = output<void>();

  protected readonly docs = signal<Doc[]>([]);
  protected readonly question = signal('');
  protected readonly busy = signal(false);
  protected readonly msg = signal<Msg | null>(null);
  protected readonly showHandoff = signal(false);
  /** Unsaved text in each document's box. */
  protected readonly edits = new Map<number, string>();

  private readonly appId = computed(() => this.application().id);

  /** Versions grouped per document (newest first), the plan excluded. */
  protected readonly groups = computed(() => {
    const groups = new Map<string, Doc[]>();
    for (const d of this.docs()) {
      if (d.kind === 'application_plan') continue;
      const key = `${d.kind}|${d.question}`;
      groups.set(key, [...(groups.get(key) ?? []), d]);
    }
    return [...groups.values()].map(v => [...v].sort((a, b) => b.version - a.version));
  });

  constructor() {
    effect(() => {
      const id = this.appId();          // only when a different application is opened, not on every reload
      untracked(() => {
        this.msg.set(null); this.showHandoff.set(false); this.edits.clear();
        this.loadDocs(id);
      });
    });
  }

  async loadDocs(id = this.application().id) {
    try {
      this.docs.set(await this.api.documents(id));
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }

  async draft(body: { kind: string; question?: string }) {
    this.busy.set(true);
    this.msg.set({ kind: 'info', text: 'Writing with your local AI… (usually 30-120 seconds)' });
    try {
      await this.api.draft(this.application().id, body);
      this.msg.set(null);
      await this.loadDocs();
    } catch (e) {
      const err = e as ApiError;
      this.msg.set({ kind: 'bad', text: err.status === 404 && /profile/i.test(err.message) ? 'Save your profile first.' : err.message });
    } finally {
      this.busy.set(false);
    }
  }

  async draftAnswer() {
    const q = this.question().trim();
    if (!q) { this.msg.set({ kind: 'bad', text: 'Type the screening question first.' }); return; }
    await this.draft({ kind: 'screening_answer', question: q });
  }

  async saveVersion(d: Doc) {
    const text = this.edits.get(d.id);
    if (text === undefined || text === d.content) { this.msg.set({ kind: 'info', text: 'No changes to save.' }); return; }
    try {
      await this.api.saveVersion(d.id, text);
      this.edits.delete(d.id);
      this.msg.set({ kind: 'ok', text: 'Saved as a new version.' });
      await this.loadDocs();
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }

  copy(text: string) { navigator.clipboard?.writeText(text); }
}
