import { Component, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../api';
import { Doc, PlanField, SubmissionPlan } from '../models';
import { Msg, MsgBox } from '../ui';

const SOURCE_LABEL: Record<string, string> = {
  profile: 'from your profile', document: 'your drafted answer', ai: 'AI', you: 'you', file: 'file',
};

/**
 * Greenhouse assisted apply: the real form questions with proposed answers. You edit and approve;
 * the fill script then fills the form in a visible browser and you click Submit yourself.
 */
@Component({
  selector: 'app-apply-plan',
  imports: [FormsModule, MsgBox],
  template: `
    <button class="secondary" type="button" (click)="prepare()">Assisted apply (Greenhouse): prepare plan</button>
    <app-msg [msg]="msg()" />
    @if (plan(); as p) {
      <div class="panel" data-testid="apply-plan">
        <h3 style="margin-top:0">Assisted apply plan
          <span class="label">v{{ version() }}{{ p.approved ? ' · approved' : ' · not approved yet' }}</span></h3>
        <p class="small muted">These are the real questions on the company's Greenhouse form. Orange rows need you.
          Consent and demographic questions are never answered for you. Nothing is submitted by the agent: you click Submit yourself.</p>
        @for (w of p.warnings; track $index) { <div class="msg bad">⚠ {{ w }}</div> }
        <table>
          <tbody>
            @for (f of p.fields; track f.name) {
              <tr [class.needs-you]="f.needs_you">
                <td class="small"><b>{{ f.label }}</b>{{ f.required ? ' *' : '' }}
                  @if (f.sensitive) { <span class="chip warn">sensitive</span> }
                  <div class="muted">{{ f.needs_you ? 'Needs you. ' : '' }}{{ f.type === 'file' ? '' : f.note }}</div></td>
                <td>
                  @switch (inputKind(f)) {
                    @case ('file') { <span class="small muted">{{ f.note }}</span> }
                    @case ('select') {
                      <select [attr.aria-label]="f.label" [ngModel]="answers()[f.name]" (ngModelChange)="setAnswer(f.name, $event)">
                        <option value="">(leave blank)</option>
                        @for (o of f.options; track o) { <option [value]="o">{{ o }}</option> }
                      </select>
                    }
                    @case ('multi') {
                      @for (o of f.options; track o) {
                        <label class="inline"><input type="checkbox" [checked]="isChecked(f.name, o)"
                               (change)="toggle(f.name, o, $any($event.target).checked)"> {{ o }}</label>
                      }
                    }
                    @case ('textarea') {
                      <textarea class="short" [attr.aria-label]="f.label" [ngModel]="answers()[f.name]" (ngModelChange)="setAnswer(f.name, $event)"></textarea>
                    }
                    @default {
                      <input [attr.aria-label]="f.label" [ngModel]="answers()[f.name]" (ngModelChange)="setAnswer(f.name, $event)">
                    }
                  }
                </td>
                <td class="small muted">{{ f.type === 'file' ? '' : sourceLabel(f.source) }}</td>
              </tr>
            }
          </tbody>
        </table>
        <button class="secondary" type="button" (click)="approve()">Save my answers and approve</button>
        <app-msg [msg]="approveMsg()" />
        @if (p.approved) {
          <div class="msg info">Approved. To fill the form, run this in the terminal (the app must be running):
<b>python scripts/apply_greenhouse.py --application {{ appId() }}</b>
A browser opens and fills the form. Review it and click "Submit application" yourself. The agent records the result.</div>
        }
      </div>
    }
  `,
})
export class ApplyPlan {
  private api = inject(Api);

  readonly appId = input.required<number>();
  readonly changed = output<void>();

  protected readonly plan = signal<SubmissionPlan | null>(null);
  protected readonly version = signal(0);
  protected readonly answers = signal<Record<string, string>>({});
  protected readonly msg = signal<Msg | null>(null);
  protected readonly approveMsg = signal<Msg | null>(null);

  constructor() {
    effect(() => {
      const id = this.appId();
      untracked(() => this.loadExisting(id));
    });
  }

  /** Show a plan saved earlier, if there is one (no plan yet is normal). */
  async loadExisting(id: number) {
    this.plan.set(null); this.msg.set(null); this.approveMsg.set(null);
    try { this.show(await this.api.plan(id)); } catch { /* no plan yet */ }
  }

  async prepare() {
    this.msg.set({ kind: 'info', text: 'Reading the form questions from Greenhouse…' });
    try {
      this.show(await this.api.preparePlan(this.appId()));
      this.msg.set(null);
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }

  async approve() {
    try {
      this.show(await this.api.approvePlan(this.appId(), this.answers()));
      this.approveMsg.set(null);
      this.changed.emit();
    } catch (e) {
      this.approveMsg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }

  private show(doc: Doc) {
    const plan = JSON.parse(doc.content) as SubmissionPlan;
    this.plan.set(plan);
    this.version.set(doc.version);
    this.answers.set(Object.fromEntries(plan.fields.filter(f => f.type !== 'file').map(f => [f.name, f.answer])));
  }

  protected inputKind(f: PlanField) {
    if (f.type === 'file') return 'file';
    if (f.type === 'single_select' && f.options.length) return 'select';
    if (f.type === 'multi_select') return 'multi';
    if (f.type === 'textarea') return 'textarea';
    return 'text';
  }
  protected sourceLabel(source: string) { return SOURCE_LABEL[source] ?? ''; }
  protected setAnswer(name: string, value: string) { this.answers.update(a => ({ ...a, [name]: value })); }
  protected isChecked(name: string, option: string) {
    return (this.answers()[name] ?? '').split(';').map(x => x.trim()).includes(option);
  }
  protected toggle(name: string, option: string, checked: boolean) {
    const chosen = (this.answers()[name] ?? '').split(';').map(x => x.trim()).filter(x => x && x !== option);
    if (checked) chosen.push(option);
    this.setAnswer(name, chosen.join('; '));
  }
}
