import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DomSanitizer } from '@angular/platform-browser';

import { Api, ApiError } from '../../api';
import { Doc } from '../../models';
import { Msg, MsgBox } from '../../ui';
import { ResumeCard } from '../resume-card';

interface Bullet { text: string; original?: string; changed?: boolean; rejected_reason?: string; }
interface Role { company: string; title: string; bullets: Bullet[]; }
interface Tailored { summary: string; experience: Role[]; [k: string]: unknown; }

/** The resume: its printed preview and actions first, a compact summary beside it, the details below. */
@Component({
  selector: 'app-resume-view',
  imports: [FormsModule, MsgBox, ResumeCard],
  template: `
    @let d = doc();
    @let r = reviewDoc().review ?? {};
    <div class="actions" data-testid="resume-actions">
      @if (!editing()) {
        <button class="secondary" type="button" (click)="startEdit()">Edit</button>
      } @else {
        <button class="secondary" type="button" (click)="editing.set(false)">Cancel editing</button>
      }
      <button class="secondary" type="button" (click)="copy()">Copy text</button>
      <a class="button" [href]="'/documents/' + d.id + '/download'" download>Download .txt</a>
      <a class="button primary" [href]="'/documents/' + d.id + '/pdf'" download data-testid="download-pdf">Download PDF</a>
      <a class="button" [href]="'/documents/' + d.id + '/print'" target="_blank" rel="noopener" data-testid="print-resume">Print</a>
      <button class="secondary" type="button" [disabled]="working()" (click)="regenerate.emit()">Generate new version</button>
    </div>
    <app-msg [msg]="msg()" />
    <div class="doc-grid">
      <div>
        @if (editing()) {
          <div class="panel" data-testid="resume-editor" style="margin-top:0">
            <label for="ed-summary">Summary</label>
            <textarea id="ed-summary" [(ngModel)]="draft()!.summary" style="min-height:140px"></textarea>
            @for (role of draft()!.experience; track $index; let ri = $index) {
              <label>{{ role.title }} · {{ role.company }}</label>
              @for (b of role.bullets; track $index; let bi = $index) {
                <textarea class="short" [attr.aria-label]="'Bullet ' + (bi + 1) + ' at ' + role.company" [(ngModel)]="b.text"></textarea>
              }
            }
            <div class="actions"><button class="secondary" type="button" [disabled]="saving()" (click)="save()">Save edits as new version</button></div>
          </div>
        } @else {
          <iframe class="preview" [src]="previewUrl()" title="Resume preview" data-testid="resume-preview"></iframe>
        }
      </div>
      <div class="side" data-testid="resume-summary">
        <div class="panel">
          <div class="stat"><span>Page fit</span>
            @if (fit(); as f) {
              <b [class.good]="f.ok" [class.warn-text]="!f.ok" data-testid="page-fit">{{ f.text }}</b>
            } @else { <b class="muted">—</b> }
          </div>
          <div class="stat" style="margin-top:8px"><span>Job-description keyword coverage</span>
            <b data-testid="keyword-coverage">{{ r.ats?.compared ? r.ats.score + '%' : '—' }}</b></div>
          <div class="small muted">How many of the technical keywords in the job description (tools, languages,
            practices; weighted by importance) appear on this resume. People skills aren't counted. It is not a hiring
            prediction{{ r.ats?.max_honest_score ? '; ' + r.ats.max_honest_score + '% is the most your real experience supports' : '' }}.</div>
          @if (d.source !== 'ai') { <div class="small muted" style="margin-top:6px">Checks shown are from the AI draft v{{ reviewDoc().version }}; this is your edit v{{ d.version }}.</div> }
        </div>
        <div class="panel" data-testid="attention">
          <b>Needs your attention</b>
          @if (attention().length) {
            <ul class="attention small">@for (a of attention(); track $index) { <li>{{ a }}</li> }</ul>
          } @else { <div class="small good">Nothing flagged. Still read it before you send it.</div> }
        </div>
        @if (excluded().length) {
          <div class="panel" data-testid="excluded">
            <b>Left out: your profile doesn't support it</b>
            <ul class="small">@for (x of excluded(); track $index) { <li>{{ x }}</li> }</ul>
            <div class="small muted">If one of these is true, add it to your profile (Job &amp; skill match → “I have this”) and generate again.</div>
          </div>
        }
      </div>
    </div>
    <details class="block" data-testid="resume-details">
      <summary><b>Review details</b>: reworded bullets (original vs new), keyword evidence, requirements map, omitted
        skills, profile facts and writing notes</summary>
      <app-resume-card [versions]="[reviewDoc()]" [working]="working()" (regenerate)="regenerate.emit()" />
    </details>
  `,
})
export class ResumeView {
  private api = inject(Api);
  private sanitizer = inject(DomSanitizer);

  readonly doc = input.required<Doc>();
  /** The AI draft whose checks apply (the doc itself, or the draft an edit came from). */
  readonly reviewDoc = input.required<Doc>();
  readonly versions = input<Doc[]>([]);
  readonly working = input(false);
  readonly saved = output<Doc>();
  readonly regenerate = output<void>();

  protected readonly editing = signal(false);
  protected readonly saving = signal(false);
  protected readonly draft = signal<Tailored | null>(null);
  protected readonly msg = signal<Msg | null>(null);

  constructor() {
    effect(() => { this.doc(); this.editing.set(false); });     // another version opened
  }

  protected readonly previewUrl = computed(() =>
    this.sanitizer.bypassSecurityTrustResourceUrl(`/documents/${this.doc().id}/print`));

  /** Printed length against the page target: "Fits on 2 pages", "1 page (short)" or "Too long (3 pages)". */
  protected readonly fit = computed(() => {
    const lay: any = (this.reviewDoc().review ?? {}).layout;
    if (!lay) return null;
    const max = lay.max_pages ?? 2;
    const pages = lay.printed_pages ?? Math.ceil(lay.estimated_pages ?? max);
    if (pages > max) return { ok: false, pages, max, text: `Too long (${pages} pages)` };
    if (pages < max) return { ok: true, pages, max, text: `${pages} page${pages > 1 ? 's' : ''} (shorter than ${max})` };
    return { ok: true, pages, max, text: `Fits on ${max} pages` };
  });

  /** Plain-language list of what to check before sending. */
  protected readonly attention = computed(() => {
    const r: any = this.reviewDoc().review ?? {};
    const out: string[] = [...(r.warnings ?? [])];
    const f = this.fit();
    if (f && f.pages > f.max) out.push(`It is longer than ${f.max} pages.`);
    const gaps = (r.ats?.gaps ?? []).map((g: any) => g.keyword);
    if (gaps.length) out.push(`Keywords in the job description that your profile doesn't show: ${gaps.join(', ')}.`);
    for (const g of r.scorecard?.gaps ?? []) out.push(`Required, and nothing in your profile shows it: ${g}`);
    const rejected = this.bullets(b => !!b.rejected_reason).length;
    if (rejected) out.push(`${rejected} AI rewording${rejected > 1 ? 's were' : ' was'} rejected; your original wording was kept.`);
    for (const e of r.tailoring_report?.eligibility ?? []) out.push(`Check yourself: ${e}`);
    return out;
  });

  /** Claims the AI wrote that your profile doesn't back, so they were not used. */
  protected readonly excluded = computed(() => {
    const r: any = this.reviewDoc().review ?? {};
    const out: string[] = [];
    for (const n of r.notes ?? []) {
      const m = /weren't backed by your profile: (.*)$/.exec(n);
      if (m) out.push(...m[1].split(' | ').map((x: string) => `“${x.trim()}”`));
      else if (/added job skill not in the original|claims more ownership/.test(n)) out.push(n.replace(/^Summary sentence on '([^']*)'/, 'A summary sentence about $1'));
    }
    for (const b of this.bullets(x => !!x.rejected_reason)) out.push(`Reworded bullet not used (${b.rejected_reason}).`);
    return out;
  });

  private bullets(keep: (b: Bullet) => boolean): Bullet[] {
    try {
      const cv = JSON.parse(this.reviewDoc().content) as Tailored;
      return cv.experience.flatMap(role => role.bullets.filter(keep));
    } catch { return []; }
  }

  startEdit() {
    this.draft.set(JSON.parse(this.doc().content));
    this.msg.set(null);
    this.editing.set(true);
  }

  async save() {
    const d = this.draft();
    if (!d) return;
    this.saving.set(true);
    try {
      const saved = await this.api.saveVersion(this.doc().id, JSON.stringify(d));
      this.editing.set(false);
      this.msg.set({ kind: 'ok', text: `Saved as version ${saved.version}. The previous version is kept.` });
      this.saved.emit(saved);
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    } finally {
      this.saving.set(false);
    }
  }

  async copy() {
    try {
      const res = await fetch(`/documents/${this.doc().id}/download`, { credentials: 'same-origin' });
      await navigator.clipboard?.writeText(await res.text());
      this.msg.set({ kind: 'ok', text: 'Resume text copied.' });
    } catch {
      this.msg.set({ kind: 'bad', text: 'Could not copy. Use Download .txt instead.' });
    }
  }
}
