import { Component, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../api';
import { AiSettings } from '../models';
import { Msg, MsgBox } from '../ui';

/**
 * Which AI writes your resumes and cover letters: Claude or OpenAI with your own API key, or the free local model.
 * The key is checked with the provider before it is saved, stays on this server, and is never shown again.
 */
@Component({
  selector: 'app-ai-provider-panel',
  imports: [FormsModule, MsgBox],
  styles: [`
    .choices { display: flex; flex-wrap: wrap; gap: 8px 20px; margin: 8px 0 12px; }
    .choices label { display: inline-flex; align-items: center; gap: 6px; font-weight: 500; cursor: pointer; }
    .choices input[type=radio] { width: auto; margin: 0; }
  `],
  template: `
    <div class="card" data-testid="ai-provider">
      <h2>AI provider</h2>
      <p class="hint">Claude or OpenAI write faster, better resumes and cover letters than the local model. They need
        an <b>API key</b> (billed by the provider per use; a Claude Pro or ChatGPT Plus subscription can't be used
        here). Without a key, the free local model (Ollama) is used. With a key, the posting and the parts of your
        profile each step needs are sent to that provider.</p>
      @if (settings(); as s) {
        <p data-testid="ai-provider-current"><b>Now using:</b> {{ label(s) }}</p>
      }
      <div class="choices" role="radiogroup" aria-label="AI provider">
        <label><input type="radio" name="ai-provider" value="claude" [(ngModel)]="provider"> Claude</label>
        <label><input type="radio" name="ai-provider" value="openai" [(ngModel)]="provider"> OpenAI</label>
        <label><input type="radio" name="ai-provider" value="ollama" [(ngModel)]="provider"> Local model (Ollama, free)</label>
      </div>
      @if (provider !== 'ollama') {
        <div class="row">
          <input class="wide" type="password" autocomplete="off" aria-label="API key" data-testid="ai-key"
                 [(ngModel)]="apiKey"
                 [placeholder]="keyPlaceholder()">
        </div>
        <p class="hint small">
          @if (provider === 'claude') { Create a key at console.anthropic.com (API keys). }
          @else { Create a key at platform.openai.com (API keys). }
          It is checked with {{ provider === 'claude' ? 'Anthropic' : 'OpenAI' }} first, at no cost.
        </p>
      }
      <button class="primary" type="button" data-testid="ai-save" [disabled]="busy() || !canSave()" (click)="save()">
        {{ busy() ? 'Checking…' : 'Save' }}</button>
      <app-msg [msg]="msg()" />
    </div>
  `,
})
export class AiProviderPanel implements OnInit {
  private api = inject(Api);
  protected readonly settings = signal<AiSettings | null>(null);
  protected readonly busy = signal(false);
  protected readonly msg = signal<Msg | null>(null);
  protected provider: 'claude' | 'openai' | 'ollama' = 'ollama';
  protected apiKey = '';

  async ngOnInit() {
    try {
      const s = await this.api.aiSettings();
      this.settings.set(s);
      this.provider = s.provider;
    } catch { /* shown as the local model */ }
  }

  protected label(s: AiSettings): string {
    if (s.provider === 'ollama' || !s.has_key) return 'the local model (Ollama)';
    const name = s.provider === 'claude' ? 'Claude' : 'OpenAI';
    return `${name}, ${s.model} (key ${s.key_hint ?? 'saved'})`;
  }

  protected keyPlaceholder(): string {
    const s = this.settings();
    return s && s.has_key && s.provider === this.provider
      ? `Saved key ${s.key_hint ?? ''}: leave empty to keep it`
      : this.provider === 'claude' ? 'Paste your Claude API key (sk-ant-…)' : 'Paste your OpenAI API key (sk-…)';
  }

  protected canSave(): boolean {
    if (this.provider === 'ollama') return true;
    const s = this.settings();
    return !!this.apiKey.trim() || !!(s && s.has_key && s.provider === this.provider);
  }

  async save() {
    this.busy.set(true);
    this.msg.set(null);
    try {
      const s = await this.api.saveAiSettings({ provider: this.provider, api_key: this.apiKey.trim() || null });
      this.settings.set(s);
      this.apiKey = '';
      this.msg.set({ kind: 'ok', text: `Saved. Now using ${this.label(s)}.` });
      try { window.dispatchEvent(new Event('ai-provider-changed')); } catch { /* no window */ }
    } catch (e) {
      this.msg.set({ kind: 'bad', text: e instanceof ApiError ? e.message : 'Could not save.' });
    } finally {
      this.busy.set(false);
    }
  }
}
