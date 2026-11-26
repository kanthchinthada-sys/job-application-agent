import { Component, computed, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../../api';
import { Doc, SectionEdit, SectionReview } from '../../models';

interface Choice { key: string; label: string; edit: SectionEdit; }

@Component({
  selector: 'app-section-editor',
  imports: [FormsModule],
  template: `
    <details class="panel">
      <summary><b>Edit one section or compare versions</b></summary>
      @if (choices().length) {
        <label>Section
          <select [ngModel]="key()" (ngModelChange)="choose($event)">
            <option value="">Choose a section</option>
            @for (c of choices(); track c.key) { <option [value]="c.key">{{ c.label }}</option> }
          </select>
        </label>
        @if (selected(); as c) {
          <div class="row">
            <div>
              <label>Compare with version
                <select [(ngModel)]="compareId">
                  <option [ngValue]="null">Current version {{ doc().version }}</option>
                  @for (v of versions(); track v.id) {
                    @if (v.id !== doc().id) { <option [ngValue]="v.id">Version {{ v.version }}</option> }
                  }
                </select>
              </label>
              <div class="panel" style="white-space:pre-wrap">{{ comparison(compareId, c) }}</div>
            </div>
            <div>
              <label>Edited section
                <textarea rows="5" maxlength="5000" [ngModel]="text()" (ngModelChange)="text.set($event); review.set(null)"></textarea>
              </label>
              @if (c.edit.section === 'skills') { <p class="small muted">Use one group per line as Group: skill, skill.</p> }
            </div>
          </div>
          <div class="actions">
            <button class="secondary" type="button" [disabled]="busy() || !text().trim() || text().trim() === c.edit.text"
                    (click)="check(c, false)">Check edit</button>
            @if (review()) { <button class="primary" type="button" [disabled]="busy()" (click)="check(c, true)">Save new version</button> }
          </div>
          @if (review(); as r) {
            @if (!r.warnings.length) { <p class="small good">The checks found no issues in this edit.</p> }
            @for (warning of r.warnings; track $index) { <p class="small warn-text">{{ warning }}</p> }
            @for (fact of r.evidence; track fact.id) {
              <p class="small"><b>{{ fact.id }} · {{ fact.owner }}</b> {{ fact.text }}</p>
            }
          }
        }
      } @else { <p class="muted">Open a resume or cover letter version to edit a section.</p> }
      @if (error()) { <p class="warn-text" role="alert">{{ error() }}</p> }
    </details>
  `,
})
export class SectionEditor {
  private api = inject(Api);
  readonly doc = input.required<Doc>();
  readonly versions = input.required<Doc[]>();
  readonly saved = output<Doc>();
  protected readonly key = signal('');
  protected readonly text = signal('');
  protected readonly review = signal<SectionReview | null>(null);
  protected readonly error = signal('');
  protected readonly busy = signal(false);
  protected compareId: number | null = null;
  protected readonly choices = computed(() => this.options(this.doc()));
  protected readonly selected = computed(() => this.choices().find(x => x.key === this.key()));

  private options(doc: Doc): Choice[] {
    if (doc.kind === 'cover_letter') {
      return doc.content.trim().split(/\n\s*\n/).map((text, i) => ({
        key: 'paragraph-' + i, label: 'Paragraph ' + (i + 1), edit: { section: 'paragraph', text, item_index: i },
      }));
    }
    if (doc.kind !== 'resume') return [];
    try {
      const r = JSON.parse(doc.content) as {
        summary: string; skill_groups?: { name: string; skills: string[] }[]; skills: string[];
        experience: { company: string; bullets: { text: string; source_id?: string | null }[] }[];
      };
      const skills = r.skill_groups?.map(g => g.name + ': ' + g.skills.join(', ')).join('\n')
        || 'Skills: ' + r.skills.join(', ');
      return [
        { key: 'summary', label: 'Professional summary', edit: { section: 'summary', text: r.summary } },
        { key: 'skills', label: 'Core Skills', edit: { section: 'skills', text: skills } },
        ...r.experience.flatMap((role, i) => role.bullets.map((bullet, j): Choice => ({
          key: 'bullet-' + (bullet.source_id ?? i + '-' + j), label: role.company + ' · bullet ' + (j + 1),
          edit: { section: 'bullet', role_index: i, item_index: j, text: bullet.text },
        }))),
      ];
    } catch { return []; }
  }

  protected comparison(id: number | null, choice: Choice) {
    if (id === null) return choice.edit.text;
    const doc = this.versions().find(d => d.id === id);
    const related = doc && this.options(doc).find(c => c.key === choice.key);
    return related?.edit.text ?? 'This section could not be matched in that version.';
  }

  protected choose(key: string) {
    this.key.set(key);
    this.text.set(this.selected()?.edit.text ?? '');
    this.error.set('');
    this.review.set(null);
    this.compareId = null;
  }

  protected async check(c: Choice, save: boolean) {
    this.busy.set(true);
    this.error.set('');
    try {
      const result = await this.api.editSection(this.doc().id, { ...c.edit, text: this.text().trim(), save });
      if (result.document) {
        this.choose('');
        this.saved.emit(result.document);
      } else { this.review.set(result); }
    } catch (e) { this.error.set((e as ApiError).message); }
    finally { this.busy.set(false); }
  }
}
