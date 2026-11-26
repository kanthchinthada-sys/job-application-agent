import { Component, effect, inject, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Api, ApiError } from '../../api';
import { Doc, EvidencePreview, ResumePdfCheck, TechnicalCoverage, TechnicalRequirement } from '../../models';

@Component({
  selector: 'app-resume-coverage',
  imports: [RouterLink],
  template: `
    <div class="panel" data-testid="resume-coverage">
      <div class="stat"><span>Technical keyword coverage</span>
        <b data-testid="keyword-coverage">{{ displayScore(coverage()?.score) }}</b>
      </div>
      @if (coverage(); as match) {
        <div class="small"><b>Required</b> · {{ match.required.covered }}/{{ match.required.total }} present,
          {{ match.required.demonstrated }} in bullets</div>
        <div class="small"><b>Preferred</b> · {{ match.preferred.covered }}/{{ match.preferred.total }} present,
          {{ match.preferred.demonstrated }} in bullets</div>
        @if (match.score === null) {
          <p class="small muted">Add technical requirements from the full job posting to calculate coverage.</p>
        } @else {
          <p class="small muted">Required terms count twice. Employers set their own screening criteria.</p>
          <details>
            <summary>Review technical requirements</summary>
            @for (row of match.rows; track row.keyword) {
              <div class="requirement">
                <b>{{ row.keyword }}</b> <span class="small muted">· {{ row.required ? 'Required' : 'Preferred' }}</span>
                <div class="small">{{ labels[row.placement] }}{{ row.owner ? ' · ' + row.owner : '' }}</div>
                @if (row.evidence && row.placement !== 'verified') { <div class="small muted">{{ row.evidence }}</div> }
                @if (row.options[0]; as option) {
                  <button type="button" class="secondary tight" [disabled]="saving()"
                          (click)="preview(row.keyword, option.record_id)">Use verified evidence</button>
                  @if (row.options.length > 1) {
                    <details class="small"><summary>Other verified examples</summary>
                      @for (other of row.options.slice(1); track other.record_id) {
                        <button type="button" class="link" [disabled]="saving()" (click)="preview(row.keyword, other.record_id)">
                          {{ other.record_id }} · {{ other.owner }}</button>
                      }
                    </details>
                  }
                } @else if (row.placement === 'gap' || row.placement === 'transferable') {
                  <a class="small" [routerLink]="[]" [queryParams]="{tab: 'match', doc: null}"
                     queryParamsHandling="merge">Review job and skill match</a>
                }
                @if (proposed(); as edit) {
                  @if (edit.keyword === row.keyword) {
                    <div class="preview-edit" data-testid="verified-evidence-preview">
                      <b>Preview for {{ edit.keyword }}</b>
                      <div class="small muted">{{ edit.record_id }} · first bullet under {{ edit.owner }}</div>
                      @if (edit.replaces) { <p class="small">Previous bullet: {{ edit.replaces }}</p> }
                      <p class="small">{{ edit.text }}</p>
                      <div class="small">Coverage {{ displayScore(edit.before.score) }} → {{ displayScore(edit.after.score) }}</div>
                      <div class="small">Required skills in bullets {{ edit.before.required.demonstrated }} → {{ edit.after.required.demonstrated }}</div>
                      <div class="small">Preferred skills in bullets {{ edit.before.preferred.demonstrated }} → {{ edit.after.preferred.demonstrated }}</div>
                      @if (edit.printed_pages !== null) { <div class="small">{{ edit.printed_pages }} printed page(s)</div> }
                      @for (warning of edit.warnings; track warning) { <p class="small warn-text">{{ warning }}</p> }
                      <div class="actions">
                        <button type="button" class="primary tight" [disabled]="saving()" (click)="save(edit)">Save as new version</button>
                        <button type="button" class="secondary tight" [disabled]="saving()" (click)="proposed.set(null)">Cancel</button>
                      </div>
                    </div>
                  }
                }
              </div>
            }
          </details>
        }
      } @else if (loading()) { <p class="small muted">Checking this version…</p> }
      @if (message()) { <p class="small" role="status">{{ message() }}</p> }
      <div class="actions">
        <button type="button" class="secondary tight" [disabled]="checkingPdf()" (click)="checkPdf()">
          {{ checkingPdf() ? 'Checking PDF…' : 'Check exported PDF' }}</button>
      </div>
      @if (pdfError()) { <p class="small warn-text" role="status">{{ pdfError() }}</p> }
      @if (pdf(); as result) {
        <div class="small" role="status" data-testid="pdf-check-result">
          @if (result.readable && !result.issues.length && !result.required_missing.length) {
            <p>PDF text and reading order checks passed.</p>
          }
          <div>{{ result.required_found.length }} of {{ result.required_found.length + result.required_missing.length }}
            required terms already on the résumé were readable in the export.</div>
          @if (result.required_missing.length) {
            <p class="warn-text">Could not read: {{ result.required_missing.join(', ') }}.</p>
          }
          @for (issue of result.issues; track issue) { <p class="warn-text">{{ issue }}</p> }
        </div>
      }
    </div>
  `,
  styles: [`.requirement { border-top: 1px solid var(--border, #ddd); padding: 10px 0; }
    .preview-edit { border-left: 2px solid var(--border, #ddd); padding: 6px 0 6px 10px; margin-top: 8px; }
    details { margin: 8px 0; } p { margin: 6px 0; }`],
})
export class ResumeCoverage {
  private readonly api = inject(Api);
  readonly documentId = input.required<number>();
  readonly saved = output<Doc>();
  protected readonly coverage = signal<TechnicalCoverage | null>(null);
  protected readonly proposed = signal<EvidencePreview | null>(null);
  protected readonly pdf = signal<ResumePdfCheck | null>(null);
  protected readonly message = signal('');
  protected readonly pdfError = signal('');
  protected readonly loading = signal(false);
  protected readonly saving = signal(false);
  protected readonly checkingPdf = signal(false);
  protected readonly labels: Record<TechnicalRequirement['placement'], string> = {
    bullet: 'In a work or project bullet', skills: 'Listed as a skill', mention: 'Mentioned elsewhere on the résumé',
    verified: 'Verified example available', profile: 'In your profile', transferable: 'Related experience', gap: 'No supporting evidence found',
  };

  constructor() {
    effect(() => { void this.load(this.documentId()); });
  }

  protected displayScore(score?: number | null): string {
    return score == null ? '—' : `${score}%`;
  }

  private async load(id: number) {
    this.coverage.set(null);
    this.proposed.set(null);
    this.pdf.set(null);
    this.pdfError.set('');
    this.saving.set(false);
    this.checkingPdf.set(false);
    this.loading.set(true);
    try {
      const coverage = await this.api.technicalCoverage(id);
      if (id === this.documentId()) this.coverage.set(coverage);
    } catch (e) {
      if (id === this.documentId()) this.message.set((e as ApiError).message);
    } finally {
      if (id === this.documentId()) this.loading.set(false);
    }
  }

  protected async preview(keyword: string, recordId: string) {
    const id = this.documentId();
    this.saving.set(true);
    this.message.set('');
    this.proposed.set(null);
    try {
      const preview = await this.api.useVerifiedEvidence(id, keyword, recordId);
      if (id === this.documentId()) this.proposed.set(preview);
    } catch (e) {
      if (id === this.documentId()) this.message.set((e as ApiError).message);
    } finally {
      if (id === this.documentId()) this.saving.set(false);
    }
  }

  protected async save(edit: EvidencePreview) {
    const id = this.documentId();
    this.saving.set(true);
    this.message.set('');
    try {
      const result = await this.api.useVerifiedEvidence(id, edit.keyword, edit.record_id, edit.approval);
      if (id === this.documentId() && result.document) {
        this.proposed.set(null);
        this.message.set(`Saved version ${result.document.version}.`);
        this.saved.emit(result.document);
      }
    } catch (e) {
      if (id === this.documentId()) this.message.set((e as ApiError).message);
    } finally {
      if (id === this.documentId()) this.saving.set(false);
    }
  }

  protected async checkPdf() {
    const id = this.documentId();
    this.checkingPdf.set(true);
    this.pdfError.set('');
    this.pdf.set(null);
    try {
      const pdf = await this.api.checkResumePdf(id);
      if (id === this.documentId()) this.pdf.set(pdf);
    } catch (e) {
      if (id === this.documentId()) this.pdfError.set((e as ApiError).message);
    } finally {
      if (id === this.documentId()) this.checkingPdf.set(false);
    }
  }
}
