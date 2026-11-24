import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../../api';
import { Doc } from '../../models';
import { Msg, MsgBox } from '../../ui';

/** The cover letter: the text first (preview or edit), what needs your attention beside it, diagnostics folded away. */
@Component({
  selector: 'app-letter-view',
  imports: [FormsModule, MsgBox],
  template: `
    @let d = doc();
    @let r = reviewDoc().review ?? {};
    <div class="actions" data-testid="letter-actions">
      @if (!editing()) {
        <button class="secondary" type="button" (click)="startEdit()">Edit</button>
      } @else {
        <button class="secondary" type="button" (click)="editing.set(false)">Cancel editing</button>
      }
      <button class="secondary" type="button" (click)="copy()">Copy text</button>
      <a class="button" [href]="'/documents/' + d.id + '/download'" download>Download .txt</a>
      <a class="button primary" [href]="'/documents/' + d.id + '/pdf'" download data-testid="download-pdf">Download PDF</a>
      <a class="button" [href]="'/documents/' + d.id + '/print'" target="_blank" rel="noopener">Print</a>
      <button class="secondary" type="button" [disabled]="working()" (click)="regenerate.emit()">Generate new version</button>
    </div>
    <app-msg [msg]="msg()" />
    <div class="doc-grid">
      <div>
        @if (editing()) {
          <textarea aria-label="Cover letter text" data-testid="letter-editor" style="min-height:460px" [(ngModel)]="text"></textarea>
          <div class="actions"><button class="secondary" type="button" [disabled]="saving()" (click)="save()">Save edits as new version</button></div>
        } @else {
          <div class="letter-preview" data-testid="letter-preview">{{ d.content }}</div>
        }
      </div>
      <div class="side">
        <div class="panel" data-testid="attention">
          <b>Needs your attention</b>
          @if (attention().length) {
            <ul class="attention small">@for (a of attention(); track $index) { <li>{{ a }}</li> }</ul>
          } @else { <div class="small good">Nothing flagged. Still read it before you send it.</div> }
          <div class="small muted">{{ words() }} words.</div>
        </div>
        @if (r.claim_checks?.length) {
          <details class="panel" data-testid="letter-claims" open>
            <summary><b>Checked against your records ({{ r.claim_checks.length }})</b>: what changed and why</summary>
            @for (c of r.claim_checks; track $index) {
              <div class="small claim">
                <div class="muted"><b>The AI wrote:</b> “{{ c.sentence }}”</div>
                @for (p of c.problems; track $index) { <div class="warn-text">{{ p }}</div> }
                <div><b>{{ c.now ? 'Now' : 'Left out' }}:</b> {{ c.now ? '“' + c.now + '”' : '' }} <span class="muted">({{ c.action }})</span></div>
                @if (c.records?.length) {
                  <div class="muted" data-testid="claim-records">Checked against: {{ ids(c.records) }}</div>
                } @else if (c.checked_against?.length) { <div class="muted">Checked against: {{ c.checked_against[0] }}</div> }
                @if (c.now && c.now_records?.length) {
                  <div class="muted" data-testid="claim-sources">Backed by: {{ ids(c.now_records) }}: “{{ c.now_records[0].text }}”</div>
                }
              </div>
            }
          </details>
        }
        @if (r.removed?.length) {
          <div class="panel" data-testid="letter-removed">
            <b>Left out: your profile doesn't support it</b>
            <ul class="small">@for (x of r.removed; track $index) { <li>“{{ x }}”</li> }</ul>
            <div class="small muted">The AI wrote these, but nothing in your profile backs them. If one is true, add it to
              your profile and generate again.</div>
          </div>
        }
      </div>
    </div>
    <details class="block">
      <summary><b>Writing diagnostics</b>: requirements it was written around, profile facts used, style check</summary>
      @if (r.stories?.length) {
        <p class="small"><b>Experience used in the letter</b></p>
        @for (story of r.stories; track $index) {
          <div class="small">
            <b>{{ story.requirement }}</b>
            <ul class="small muted">@for (record of story.records; track record.id) { <li>{{ record.id }} · {{ record.text }}</li> }</ul>
          </div>
        }
      }
      @if (r.priorities?.length) {
        <p class="small"><b>Written around the job's top requirements</b></p>
        <ol class="small muted">@for (p of r.priorities; track $index) { <li>{{ p.requirement }} <span class="label">{{ p.fact_ids.join(', ') }}</span></li> }</ol>
      }
      @if (r.facts_used?.length) {
        <p class="small"><b>Profile facts used ({{ r.facts_used.length }})</b></p>
        <ul class="small muted">@for (f of r.facts_used; track f.id) { <li><b>{{ f.id }}</b> {{ f.text }}</li> }</ul>
      }
      @if (r.humanizer; as h) {
        <p class="small">Style check (phrases that often read as AI-written): {{ h.remaining?.length ? h.remaining.length + ' left' : 'none found' }}.
          <span class="muted">This only looks at wording; it says nothing about whether the letter is accurate or ready.</span></p>
      }
    </details>
  `,
})
export class LetterView {
  private api = inject(Api);

  readonly doc = input.required<Doc>();
  readonly reviewDoc = input.required<Doc>();
  readonly working = input(false);
  readonly saved = output<Doc>();
  readonly regenerate = output<void>();

  protected readonly editing = signal(false);
  protected readonly saving = signal(false);
  protected readonly msg = signal<Msg | null>(null);
  protected text = '';

  protected readonly words = computed(() => this.doc().content.split(/\s+/).filter(Boolean).length);
  /** "AG24, AG25": the records a sentence was checked against, by their id. */
  protected ids(records: { id: string }[]): string {
    return records.map(r => r.id).join(', ');
  }
  protected readonly attention = computed(() => {
    const r: any = this.reviewDoc().review ?? {};
    return [...(r.warnings ?? []), ...(r.questions_for_you ?? []).map((q: string) => `The AI needs from you: ${q}`)];
  });

  constructor() {
    effect(() => { this.doc(); this.editing.set(false); });     // another version opened
  }

  startEdit() {
    this.text = this.doc().content;
    this.msg.set(null);
    this.editing.set(true);
  }

  async save() {
    if (this.text === this.doc().content) { this.msg.set({ kind: 'info', text: 'No changes to save.' }); return; }
    this.saving.set(true);
    try {
      const saved = await this.api.saveVersion(this.doc().id, this.text);
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
    await navigator.clipboard?.writeText(this.editing() ? this.text : this.doc().content);
    this.msg.set({ kind: 'ok', text: 'Cover letter copied.' });
  }
}
