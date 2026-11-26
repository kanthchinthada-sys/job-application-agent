import { DatePipe } from '@angular/common';
import { Component, computed, DestroyRef, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../../api';
import { Application, Doc, Job, Preparation } from '../../models';
import { Msg, MsgBox } from '../../ui';
import { ApplyPlan } from '../apply-plan';
import { HandoffPanel } from '../handoff-panel';
import { AutoApplyPanel } from './auto-apply';
import { LetterView } from './letter-view';
import { ResumeView } from './resume-view';
import { SectionEditor } from './section-editor';

type Kind = 'resume' | 'cover_letter';
export const KIND_LABEL: Record<Kind, string> = { resume: 'Resume', cover_letter: 'Cover letter' };

/** Resume and cover letter first: generate them (with real progress per document), then preview, edit, copy, export.
 *  Opening this tab only reads what is saved: it never calls the model. */
@Component({
  selector: 'app-documents-tab',
  imports: [FormsModule, DatePipe, MsgBox, ResumeView, LetterView, HandoffPanel, ApplyPlan, SectionEditor, AutoApplyPanel],
  template: `
    @if (excerpt(); as j) {
      <div class="card" data-testid="excerpt">
        <b>Only a short excerpt of this posting is saved</b> ({{ j.description.length }} characters), so the
        documents can't use the real requirements. Paste the full description first:
        <textarea aria-label="Full job description" [(ngModel)]="fullDescription" placeholder="Paste the full job description"></textarea>
        <div class="actions">
          <button class="secondary" type="button" [disabled]="fullDescription().trim().length < 200" (click)="saveDescription(j)">Save description</button>
          @if (j.source_url) { <button class="secondary" type="button" [disabled]="refreshing()" (click)="refresh(j)">
            {{ refreshing() ? 'Reading the posting…' : 'Try reading the full posting from its link' }}</button> }
        </div>
      </div>
    }

    <div class="card" data-testid="generation">
      @if (loaded() && !hasAny() && !anyRunning()) {
        <h3 style="margin-top:0">No documents yet</h3>
        <p class="hint">Your local AI writes both from your profile only. It usually takes 2–4 minutes; you can leave this
          page and come back.</p>
        <div class="actions">
          <button class="primary" type="button" data-testid="generate-both" [disabled]="starting()" (click)="generate()">
            Generate resume and cover letter</button>
          <button class="link-btn small" type="button" [disabled]="starting()" (click)="generate(['resume'])">Resume only</button>
          <button class="link-btn small" type="button" [disabled]="starting()" (click)="generate(['cover_letter'])">Cover letter only</button>
        </div>
      }
      @for (k of kinds; track k) {
        <div class="status-row" [attr.data-testid]="'status-' + k">
          <span class="name">{{ label[k] }}</span>
          @switch (state(k)) {
            @case ('queued') { <span class="badge run"><span class="spinner"></span> Queued</span>
              <span class="small muted">{{ prep()?.message }}</span> }
            @case ('running') { <span class="badge run"><span class="spinner"></span> Generating… {{ elapsed() }}</span>
              <span class="small muted">{{ k === 'cover_letter' && prep()?.step === 'resume' ? 'starts right after the resume' : 'writing and checking every line against your profile' }}</span> }
            @case ('failed') { <span class="badge bad">{{ prep()?.failed_steps?.[k]?.startsWith('Interrupted') ? 'Interrupted' : 'Failed' }}</span>
              <span class="small">{{ prep()?.failed_steps?.[k] }}</span>
              <button class="secondary tight" type="button" [disabled]="starting() || anyRunning()" (click)="generate([k])">Retry {{ label[k].toLowerCase() }}</button> }
            @case ('ready') { <span class="badge ok">Ready</span>
              <span class="small muted">v{{ latest(k)!.version }} · {{ latest(k)!.source === 'ai' ? 'AI draft' : 'your edit' }} · {{ latest(k)!.created_at | date: 'MMM d, h:mm a' }}</span> }
            @default { <span class="badge">Not generated</span>
              @if (hasAny()) { <button class="secondary tight" type="button" [disabled]="starting() || anyRunning()" (click)="generate([k])">Generate {{ label[k].toLowerCase() }}</button> } }
          }
        </div>
      }
      <app-msg [msg]="msg()" />
    </div>

    @if (hasAny()) {
      <div class="card">
        <div class="actions" style="justify-content:space-between">
          <div class="seg" role="group" aria-label="Document">
            @for (k of kinds; track k) {
              <button type="button" [attr.aria-pressed]="view() === k" [disabled]="!latest(k)" (click)="view.set(k)"
                      [attr.data-testid]="'view-' + k">{{ label[k] }}</button>
            }
          </div>
          @if (versionsOf(view()).length) {
            <label class="inline small">Version
              <select style="width:auto" [attr.aria-label]="'Version of the ' + label[view()].toLowerCase()" data-testid="version"
                      [ngModel]="selectedId(view())" (ngModelChange)="pick(view(), +$event)">
                @for (v of versionsOf(view()); track v.id) {
                  <option [value]="v.id">v{{ v.version }} · {{ v.source === 'ai' ? 'AI draft' : 'your edit' }} · {{ v.created_at | date: 'MMM d, h:mm a' }}</option>
                }
              </select></label>
          }
        </div>
        @if (selected(view()); as d) {
          @for (version of [d]; track version.id) {
            <app-section-editor [doc]="version" [versions]="versionsOf(view())" (saved)="afterSave($event)" />
          }
          @if (profileChangedSince(view())) {
            <div class="msg info" data-testid="profile-changed">Profile updated since this draft.
              <button class="secondary tight" type="button" style="margin-left:8px" [disabled]="starting() || anyRunning()"
                      (click)="generate([view()])">Generate a new version</button>
              <span class="small">Your current versions stay available.</span></div>
          }
          @if (view() === 'resume') {
            <app-resume-view [doc]="d" [reviewDoc]="reviewFor('resume', d)" [versions]="versionsOf('resume')"
                             [working]="starting() || anyRunning()" (saved)="afterSave($event)" (regenerate)="generate(['resume'])" />
          } @else {
            <app-letter-view [doc]="d" [reviewDoc]="reviewFor('cover_letter', d)"
                             [working]="starting() || anyRunning()" (saved)="afterSave($event)" (regenerate)="generate(['cover_letter'])" />
          }
        }
      </div>
    }

    <app-auto-apply [applicationId]="application().id" [documents]="docs()" (changed)="changed.emit()" />

    <details class="card tools" data-testid="application-tools">
      <summary><b>Application tools</b> · screening answers, manual checklist, Greenhouse assisted plan</summary>
      <h3>Screening answers</h3>
      <div class="row">
        <input class="wide" aria-label="Screening question" [(ngModel)]="question" placeholder="e.g. Are you legally eligible to work in Canada?">
        <button class="secondary tight" type="button" [disabled]="answering()" (click)="draftAnswer()">
          {{ answering() ? 'Writing…' : 'Draft answer' }}</button>
      </div>
      @for (a of answers(); track a.id) {
        <div class="panel">
          <b>{{ a.question }}</b> <span class="label">v{{ a.version }} · {{ a.source === 'ai' ? 'AI draft' : 'your edit' }}</span>
          @for (w of a.review?.warnings ?? []; track $index) { <div class="small warn-text">⚠ {{ w }}</div> }
          <textarea [attr.aria-label]="'Answer to ' + a.question" [ngModel]="a.content" (ngModelChange)="answerEdits.set(a.id, $event)"></textarea>
          <div class="actions">
            <button class="secondary" type="button" (click)="saveAnswer(a)">Save edits as new version</button>
            <button class="secondary" type="button" (click)="copy(answerEdits.get(a.id) ?? a.content)">Copy</button>
          </div>
        </div>
      }
      <h3>Apply by hand: checklist</h3>
      <app-handoff-panel [appId]="application().id" (changed)="changed.emit()" />
      <h3>Greenhouse assisted plan</h3>
      <app-apply-plan [appId]="application().id" (changed)="changed.emit()" />
    </details>
  `,
})
export class DocumentsTab {
  private api = inject(Api);

  readonly application = input.required<Application>();
  /** A document version to open (from Activity). */
  readonly docId = input<number | null>(null);
  readonly changed = output<void>();

  protected readonly kinds: Kind[] = ['resume', 'cover_letter'];
  protected readonly label = KIND_LABEL;
  protected readonly docs = signal<Doc[]>([]);
  protected readonly loaded = signal(false);
  protected readonly prep = signal<Preparation | null>(null);
  protected readonly starting = signal(false);
  protected readonly msg = signal<Msg | null>(null);
  protected readonly view = signal<Kind>('resume');
  private readonly picked = signal<Record<string, number>>({});
  protected readonly profileUpdatedAt = signal<string | null>(null);
  protected readonly excerpt = signal<Job | null>(null);
  protected readonly fullDescription = signal('');
  protected readonly refreshing = signal(false);
  protected readonly question = signal('');
  protected readonly answering = signal(false);
  protected readonly answerEdits = new Map<number, string>();
  private readonly now = signal(Date.now());
  private timer: ReturnType<typeof setTimeout> | null = null;
  private clock: ReturnType<typeof setInterval> | null = null;

  protected readonly anyRunning = computed(() => ['queued', 'running'].includes(this.prep()?.state ?? ''));
  protected readonly hasAny = computed(() => this.kinds.some(k => this.latest(k)));
  protected readonly answers = computed(() => {
    const seen = new Set<string>();
    return this.docs().filter(d => d.kind === 'screening_answer').sort((a, b) => b.version - a.version)
      .filter(d => !seen.has(d.question) && !!seen.add(d.question));
  });
  protected readonly elapsed = computed(() => {
    const start = this.prep()?.started_at;
    if (!start) return '';
    const s = Math.max(0, Math.round((this.now() - new Date(start).getTime()) / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  });

  constructor() {
    effect(() => {
      const id = this.application().id;
      untracked(() => this.open(id));
    });
    effect(() => {
      const docId = this.docId();
      const docs = this.docs();
      untracked(() => {
        const d = docs.find(x => x.id === docId);
        if (d && (d.kind === 'resume' || d.kind === 'cover_letter')) {
          this.view.set(d.kind);
          this.picked.update(p => ({ ...p, [d.kind]: d.id }));
        }
      });
    });
    inject(DestroyRef).onDestroy(() => { this.stop(); if (this.clock) clearInterval(this.clock); });
    this.clock = setInterval(() => this.now.set(Date.now()), 1000);
  }

  private async open(id: number) {
    this.stop();
    this.loaded.set(false);
    this.msg.set(null);
    await Promise.all([this.loadDocs(id), this.loadProfileTime(), this.checkExcerpt()]);
    this.loaded.set(true);
    if (!this.latest('resume') && this.latest('cover_letter')) this.view.set('cover_letter');
    await this.watch(id);
  }

  versionsOf(kind: Kind): Doc[] {
    return this.docs().filter(d => d.kind === kind).sort((a, b) => b.version - a.version);
  }
  latest(kind: Kind): Doc | null { return this.versionsOf(kind)[0] ?? null; }
  selectedId(kind: Kind): number | null { return this.selected(kind)?.id ?? null; }
  selected(kind: Kind): Doc | null {
    const id = this.picked()[kind];
    return this.versionsOf(kind).find(d => d.id === id) ?? this.latest(kind);
  }
  pick(kind: Kind, id: number) { this.picked.update(p => ({ ...p, [kind]: id })); }

  /** The checks belong to the AI draft: an edited version shows the checks of the draft it came from. */
  reviewFor(kind: Kind, d: Doc): Doc {
    if (d.source === 'ai' || d.review?.method === 'section_edit') return d;
    return this.versionsOf(kind).find(v => v.source === 'ai' && v.version < d.version) ?? d;
  }

  state(kind: Kind): 'queued' | 'running' | 'failed' | 'ready' | 'none' {
    // a failed or interrupted (app restarted) document offers a retry; ready ones stay available
    const p = this.prep();
    const inRun = p && (p.steps ?? ['resume', 'cover_letter']).includes(kind);
    if (p && inRun && (p.state === 'queued' || p.state === 'running') && !p.done_steps.includes(kind)
        && !p.failed_steps?.[kind]) {
      return p.state === 'queued' || (kind === 'cover_letter' && p.step === 'resume') ? 'queued' : 'running';
    }
    if (p?.failed_steps?.[kind]) return 'failed';        // this run, or an earlier one it didn't redo
    return this.latest(kind) ? 'ready' : 'none';
  }

  /** "Profile updated since this draft": you added evidence after the latest AI draft was made. */
  profileChangedSince(kind: Kind): boolean {
    const ai = this.versionsOf(kind).find(v => v.source === 'ai');
    const changed = this.profileUpdatedAt();
    return !!ai && !!changed && new Date(changed).getTime() > new Date(ai.created_at).getTime()
      && this.state(kind) === 'ready';
  }

  async generate(steps?: Kind[]) {
    if (this.starting() || this.anyRunning()) return;              // one click is one run
    this.starting.set(true);
    this.msg.set(null);
    try {
      this.prep.set(await this.api.prepare(this.application().id, steps));
      this.changed.emit();
      await this.watch(this.application().id);
    } catch (e) {
      const err = e as ApiError;
      this.msg.set({ kind: 'bad', text: err.message });
    } finally {
      this.starting.set(false);
    }
  }

  private stop() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  /** Follow a background run: each document appears as soon as it is saved. Reading progress never calls the model. */
  private async watch(id: number) {
    this.stop();
    let p: Preparation;
    try { p = await this.api.preparation(id); } catch { return; }
    if (id !== this.application().id) return;
    const before = this.prep();
    this.prep.set(p);
    const progressed = (before?.done_steps.length ?? 0) !== p.done_steps.length || before?.state !== p.state;
    if (progressed && before) {
      await this.loadDocs(id);
      const fresh = p.done_steps[p.done_steps.length - 1] as Kind | undefined;
      if (fresh && !this.picked()[fresh]) this.view.set(fresh);
      if (p.state === 'done' || p.state === 'failed') this.changed.emit();
    }
    if (p.state === 'queued' || p.state === 'running') this.timer = setTimeout(() => this.watch(id), 2500);
  }

  async loadDocs(id = this.application().id) {
    try {
      this.docs.set(await this.api.documents(id));
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }

  private async loadProfileTime() {
    try {
      const p = await this.api.profile();
      this.profileUpdatedAt.set(String(p['updated_at'] ?? '') || null);
    } catch { this.profileUpdatedAt.set(null); }
  }

  private async checkExcerpt() {
    try {
      const j = await this.api.job(this.application().job_id);
      const placeholder = j.required_skills.length === 1 && [j.title, 'Untitled job'].includes(j.required_skills[0]);
      this.excerpt.set(j.description.length < 800 || j.description.trimEnd().endsWith('…') || placeholder ? j : null);
    } catch { this.excerpt.set(null); }
  }

  async refresh(j: Job) {
    this.refreshing.set(true);
    try {
      await this.api.refreshJob(j.id);
      await this.checkExcerpt();
      this.msg.set({ kind: 'ok', text: 'Full posting read. Generate the documents to use it.' });
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    } finally {
      this.refreshing.set(false);
    }
  }

  async saveDescription(j: Job) {
    try {
      await this.api.updateJob(j.id, { description: this.fullDescription().trim() });
      this.excerpt.set(null);
      this.msg.set({ kind: 'ok', text: 'Description saved. Generate the documents to use it.' });
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }

  /** A new version was saved (your edit): show it. */
  async afterSave(d: Doc) {
    await this.loadDocs();
    this.pick(d.kind as Kind, d.id);
    this.changed.emit();
  }

  async draftAnswer() {
    const q = this.question().trim();
    if (!q) { this.msg.set({ kind: 'bad', text: 'Type the screening question first.' }); return; }
    if (this.answering()) return;
    this.answering.set(true);
    try {
      await this.api.draft(this.application().id, { kind: 'screening_answer', question: q });
      this.question.set('');
      await this.loadDocs();
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    } finally {
      this.answering.set(false);
    }
  }

  async saveAnswer(a: Doc) {
    const text = this.answerEdits.get(a.id);
    if (text === undefined || text === a.content) return;
    try {
      await this.api.saveVersion(a.id, text);
      this.answerEdits.delete(a.id);
      await this.loadDocs();
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }

  copy(text: string) { navigator.clipboard?.writeText(text); }
}
