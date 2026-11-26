import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, ApiError } from '../api';
import { BenchmarkResult } from '../models';

type Provider = 'claude' | 'openai';

@Component({
  selector: 'app-provider-benchmark-panel',
  imports: [FormsModule],
  template: `
    <details class="card" (toggle)="load($event)">
      <summary><b>Compare Claude and OpenAI</b></summary>
      <p class="small">Run the same fictional candidate and posting through both providers. This makes paid API
        calls with your keys and reports measured usage, writing checks, and sample drafts.</p>
      <label>Sample job
        <select [(ngModel)]="caseId">
          @for (job of cases(); track job.id) { <option [value]="job.id">{{ job.title }}</option> }
        </select>
      </label>
      <div class="row">
        @for (p of providers; track p) {
          <div class="panel">
            <b>{{ p === 'claude' ? 'Claude' : 'OpenAI' }}</b>
            <label>Temporary API key
              <input type="password" [(ngModel)]="config[p].key" placeholder="Leave blank to use saved key for this provider" autocomplete="off">
            </label>
            <label>Model (optional)
              <input [(ngModel)]="config[p].model" placeholder="Saved model or provider default">
            </label>
            <div class="row">
              <label>Input dollars per million tokens<input type="number" min="0" step="0.01" [(ngModel)]="config[p].input"></label>
              <label>Output dollars per million tokens<input type="number" min="0" step="0.01" [(ngModel)]="config[p].output"></label>
            </div>
          </div>
        }
      </div>
      <p class="small muted">Enter both token rates for an estimate. Provider discounts, cached input rates, and account pricing can change the bill.</p>
      <button type="button" class="secondary" [disabled]="busy() || !caseId" (click)="compare()">
        {{ busy() ? 'Running sample jobs…' : 'Run paid comparison' }}</button>
      @if (message()) { <p class="warn-text" role="alert">{{ message() }}</p> }
      <div class="row">
        @for (r of results(); track r.provider) {
          <div class="panel">
            <h4>{{ r.provider }} · {{ r.model }}</h4>
            <p class="small">{{ r.seconds }} s · {{ r.calls }} model calls · {{ r.input_tokens ?? '—' }} input /
              {{ r.output_tokens ?? '—' }} output tokens · {{ r.estimated_usd === null ? 'estimate unavailable' : '$' + r.estimated_usd }}</p>
            @if (r.error) { <p class="warn-text">{{ r.error }}</p> }
            @else {
              <p class="small">Resume warnings {{ r.resume_warnings?.length ?? 0 }} · Letter warnings {{ r.letter_warnings?.length ?? 0 }}
                · Sentences removed {{ r.letter_removed ?? 0 }} · Style check {{ r.humanizer_score ?? '—' }}</p>
              <p>{{ r.summary }}</p>
              <p class="small">{{ r.skills?.join(' · ') }}</p>
              <details><summary>Cover letter sample</summary><p style="white-space:pre-wrap">{{ r.letter }}</p></details>
            }
          </div>
        }
      </div>
    </details>
  `,
})
export class ProviderBenchmarkPanel {
  private api = inject(Api);
  protected readonly providers: Provider[] = ['claude', 'openai'];
  protected readonly cases = signal<{ id: string; title: string }[]>([]);
  protected readonly results = signal<BenchmarkResult[]>([]);
  protected readonly message = signal('');
  protected readonly busy = signal(false);
  protected caseId = '';
  protected config = {
    claude: { key: '', model: '', input: null as number | null, output: null as number | null },
    openai: { key: '', model: '', input: null as number | null, output: null as number | null },
  };

  protected async load(event: Event) {
    if (!(event.target as HTMLDetailsElement).open || this.cases().length) return;
    try {
      const cases = await this.api.benchmarkCases();
      this.cases.set(cases);
      this.caseId = cases[0]?.id ?? '';
    } catch (e) { this.message.set((e as ApiError).message); }
  }

  protected async compare() {
    this.busy.set(true);
    this.message.set('');
    this.results.set([]);
    const { claude, openai } = this.config;
    const rates: Record<string, { input: number; output: number }> = {};
    for (const p of this.providers) {
      const { input, output } = this.config[p];
      if (input !== null && output !== null) rates[p] = { input, output };
    }
    try {
      const response = await this.api.compareProviders({
        case_id: this.caseId, claude_key: claude.key.trim() || undefined, openai_key: openai.key.trim() || undefined,
        claude_model: claude.model.trim() || undefined, openai_model: openai.model.trim() || undefined, rates,
      });
      this.results.set(response.results);
    } catch (e) { this.message.set((e as ApiError).message); }
    finally {
      claude.key = openai.key = '';
      this.busy.set(false);
    }
  }
}
