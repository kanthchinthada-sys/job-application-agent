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
  await settle(fixture);
  return { ...ctx, fixture };
}

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
