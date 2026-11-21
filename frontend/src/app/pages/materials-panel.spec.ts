import { TestBed } from '@angular/core/testing';

import { APP, button, settle, setup, text } from '../testing/setup';
import { MaterialsPanel } from './materials-panel';

const letter = (over: object = {}) => ({
  id: 11, application_id: 7, kind: 'cover_letter', question: '', version: 1, source: 'ai', created_at: '2026-11-14T10:00:00',
  content: 'Dear Hiring Team,\n\nI build pytest suites (fictional).',
  review: { warnings: ['Number "40%" is not in your profile or the posting.'], questions_for_you: [],
            humanizer: { score: 92, rewritten: false, remaining: [] }, facts_used: [{ id: 'F1', text: 'Built pytest suites' }] },
  ...over,
});

async function render(docs: object[]) {
  const ctx = setup([MaterialsPanel]);
  const fixture = TestBed.createComponent(MaterialsPanel);
  fixture.componentRef.setInput('application', APP);
  fixture.detectChanges();
  ctx.http.expectOne('/applications/7/documents').flush(docs);
  ctx.http.expectOne('/applications/7/submission/plan').flush({ detail: 'none' }, { status: 404, statusText: 'Not Found' });
  ctx.http.match('/applications/7/prepare').forEach(r => r.flush(IDLE));
  await settle(fixture);
  return { ...ctx, fixture };
}

const IDLE = { application_id: 7, state: 'idle', step: null, message: '', done_steps: [], started_at: null, finished_at: null };

describe('MaterialsPanel', () => {
  it('shows the honesty warnings and the human-style score with the draft', async () => {
    const { fixture } = await render([letter()]);
    const page = text(fixture);
    expect(page).toContain('Cover letter');
    expect(page).toContain('⚠ Number "40%" is not in your profile');
    expect(page).toContain('Human-style score: 92/100');
    expect(page).not.toContain('Automatic checks passed');            // there is a warning
    expect(page).toContain('Profile facts used (1)');
  });

  it('saves your edit as a new version', async () => {
    const { fixture, http } = await render([letter({ review: { warnings: [] } })]);
    expect(text(fixture)).toContain('Automatic checks passed');
    const box = fixture.nativeElement.querySelector('textarea') as HTMLTextAreaElement;
    box.value = 'Dear Hiring Team,\n\nMy own wording (fictional).';
    box.dispatchEvent(new Event('input'));
    button(fixture, 'Save my edits as new version').click();
    const req = http.expectOne('/documents/11/versions');
    expect(req.request.body).toEqual({ content: 'Dear Hiring Team,\n\nMy own wording (fictional).' });
    req.flush({});
    await settle(fixture);
    http.expectOne('/applications/7/documents').flush([letter({ id: 12, version: 2, source: 'user_edit', review: {} }), letter()]);
    await settle(fixture);
    expect(text(fixture)).toContain('v2 · your edit');
    expect(text(fixture)).toContain('Older versions (1)');
  });

  it('asks for the screening question before drafting', async () => {
    const { fixture, http } = await render([]);
    expect(text(fixture)).toContain('No materials yet');
    button(fixture, 'Draft answer').click();
    await settle(fixture);
    http.expectNone('/applications/7/drafts');
    expect(text(fixture)).toContain('Type the screening question first');
  });
});


describe('MaterialsPanel background preparation', () => {
  it('maps each requirement to the resume line that shows it, and lists what the ATS check left out', async () => {
    const resume = {
      id: 21, application_id: 7, kind: 'resume', question: '', version: 1, source: 'ai', created_at: '2026-11-14T10:00:00',
      content: JSON.stringify({ summary: 'Quality engineer (fictional).', skills: ['Python'], experience: [] }),
      review: {
        warnings: [], notes: [], tailoring: {}, keywords_added: [],
        ats: { score: 80, score_before: 70, max_honest_score: 90, compared: 2, gaps: [], missing_but_evidenced: [],
               matched: [{ keyword: 'Python', weight: 2, evidence: 'Built pytest suites in Python (fictional).' }],
               not_scored: ['curiosity', 'hybrid working policy'] },
        requirements_map: [
          { requirement: 'Python and SQL', kind: 'required', category: 'technical', strength: 'partial',
            found: ['python'], missing: ['sql'], where: null, not_printed: ['EX07'],
            on_resume: [{ id: 'EX01', owner: 'Example Corp', where: 'Example Corp, bullet 2',
                          line: 'Built pytest suites in Python (fictional).' }] },
          { requirement: 'Curiosity and initiative', kind: 'required', category: 'soft skill',
            strength: 'not shown', found: [], missing: ['curiosity'], where: null, on_resume: [], not_printed: [] },
        ],
      },
    };
    const { fixture } = await render([resume]);
    const map = text(fixture);
    expect(map).toContain('Requirements → your experience → where it is on the resume');
    expect(map).toContain('0 met · 1 partly · 0 not met · 1 soft skills');
    expect(map).toContain('Example Corp, bullet 2');
    expect(map).toContain('Cut to fit two pages: EX07');
    expect(map).toContain('✗ sql');
    expect(map).toContain('Not scored (people skills and ways of working, not technical keywords): curiosity · hybrid working policy');
  });

  it('shows the progress of Apply and the documents as soon as they are ready', async () => {
    const ctx = setup([MaterialsPanel]);
    const fixture = TestBed.createComponent(MaterialsPanel);
    fixture.componentRef.setInput('application', APP);
    fixture.detectChanges();
    ctx.http.expectOne('/applications/7/documents').flush([]);
    ctx.http.expectOne('/applications/7/submission/plan').flush({ detail: 'none' }, { status: 404, statusText: 'Not Found' });
    ctx.http.expectOne('/applications/7/prepare').flush({ ...IDLE, state: 'running', step: 'resume',
                                                          message: 'Tailoring your resume…' });
    await settle(fixture);
    expect(text(fixture)).toContain('Tailoring your resume…');
    expect(button(fixture, 'Tailor resume').disabled).toBe(true);          // no second resume while one is made
    await new Promise(r => setTimeout(r, 3100));                        // the panel checks every 3 seconds
    ctx.http.expectOne('/applications/7/prepare').flush({ ...IDLE, state: 'done', done_steps: ['resume', 'cover_letter'],
                                                          message: 'Resume and cover letter are ready.' });
    await settle(fixture);
    ctx.http.expectOne('/applications/7/documents').flush([]);
    await settle(fixture);
    expect(text(fixture)).not.toContain('Tailoring your resume…');
    expect(button(fixture, 'Tailor resume').disabled).toBe(false);
  }, 10000);
});
