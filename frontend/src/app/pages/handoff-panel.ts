import { Component, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../api';
import { Handoff } from '../models';
import { Msg, MsgBox } from '../ui';

/** Everything needed to apply by hand, with copy buttons; then you record what happened. */
@Component({
  selector: 'app-handoff-panel',
  imports: [FormsModule, MsgBox],
  template: `
    <app-msg [msg]="msg()" />
    @if (handoff(); as h) {
      <div class="panel" data-testid="handoff">
        <h3 style="margin-top:0">Apply by hand at {{ h.company }}</h3>
        @for (w of h.warnings; track $index) { <div class="msg bad">⚠ {{ w }}</div> }
        <ol class="small">@for (c of h.checklist; track $index) { <li>{{ c }}</li> }</ol>
        <p>
          @if (h.apply_url) { <a [href]="h.apply_url" target="_blank" rel="noopener">Open the posting</a> }
          @else { No posting link saved. }
          @if (h.resume) { · <a [href]="'/documents/' + h.resume.id + '/print'">Open tailored resume (v{{ h.resume.version }})</a> }
        </p>
        <table class="small">
          <tbody>
            @for (f of h.copy_fields; track f.label) {
              <tr><td class="muted">{{ f.label }}</td><td>{{ f.value }} <button class="secondary tight" type="button" (click)="copy(f.value)">Copy</button></td></tr>
            }
            @if (h.cover_letter; as cl) {
              <tr><td class="muted">Cover letter</td><td>v{{ cl.version }} <button class="secondary tight" type="button" (click)="copy(cl.content)">Copy</button></td></tr>
            }
            @for (a of h.screening_answers; track a.id) {
              <tr><td class="muted">{{ a.question }}</td>
                <td>{{ a.content.length > 80 ? a.content.slice(0, 80) + '…' : a.content }}
                  <button class="secondary tight" type="button" (click)="copy(a.content)">Copy</button></td></tr>
            }
          </tbody>
        </table>

        <p class="small">After you submit:</p>
        @if (askProof()) {
          <label for="handoff-proof">Confirmation evidence (email subject, confirmation number, …)</label>
          <input id="handoff-proof" [(ngModel)]="proof">
          <button class="primary" type="button" (click)="record('confirmed')">Save as confirmed</button>
          <button class="secondary" type="button" (click)="askProof.set(false)">Cancel</button>
        } @else {
          <button class="primary" type="button" (click)="askProof.set(true)">I submitted and got a confirmation</button>
          <button class="secondary" type="button" (click)="record('uncertain')">Not sure it went through</button>
        }
        <app-msg [msg]="recordMsg()" />
      </div>
    }
  `,
})
export class HandoffPanel {
  private api = inject(Api);

  readonly appId = input.required<number>();
  readonly changed = output<void>();

  protected readonly handoff = signal<Handoff | null>(null);
  protected readonly msg = signal<Msg | null>(null);
  protected readonly recordMsg = signal<Msg | null>(null);
  protected readonly askProof = signal(false);
  protected readonly proof = signal('');

  constructor() {
    effect(() => {
      const id = this.appId();
      untracked(() => this.load(id));
    });
  }

  async load(id: number) {
    this.handoff.set(null);
    try {
      this.handoff.set(await this.api.handoff(id));
      this.msg.set(null);
    } catch (e) {
      const err = e as ApiError;
      this.msg.set({ kind: 'bad', text: err.status === 404 && /profile/i.test(err.message) ? 'Save your profile first.' : err.message });
    }
  }

  async record(kind: 'confirmed' | 'uncertain') {
    if (kind === 'confirmed' && !this.proof().trim()) {
      this.recordMsg.set({ kind: 'bad', text: 'Paste the confirmation evidence first.' });
      return;
    }
    const body = kind === 'confirmed'
      ? { status: 'submitted_confirmed', submission_confirmation: this.proof().trim() }
      : { status: 'submission_uncertain' };
    try {
      await this.api.updateApplication(this.appId(), body);
      this.askProof.set(false);
      this.recordMsg.set({ kind: 'ok', text: kind === 'confirmed' ? 'Recorded as confirmed.' : 'Recorded as uncertain. Check your email for a confirmation.' });
      this.changed.emit();
    } catch (e) {
      this.recordMsg.set({ kind: 'bad', text: (e as ApiError).message });
    }
  }

  copy(text: string) { navigator.clipboard?.writeText(text); }
}
