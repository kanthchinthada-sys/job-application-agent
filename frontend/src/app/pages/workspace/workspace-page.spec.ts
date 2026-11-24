import { TestBed } from '@angular/core/testing';

import { APP, button, JOB, settle, setup, text } from '../../testing/setup';
import { DocumentsTab } from './documents-tab';
import { WorkspacePage } from './workspace-page';

const IDLE = { application_id: 7, state: 'idle', step: null, message: '', done_steps: [], started_at: null, finished_at: null };
const LETTER = { id: 31, application_id: 7, kind: 'cover_letter', question: '', version: 2, content: 'Dear team (fictional),',
  source: 'user_edit', review: { warnings: [] }, created_at: '2026-11-21T10:05:00Z' };
const LETTER_AI = { ...LETTER, id: 30, version: 1, source: 'ai', content: 'AI draft (fictional).',
  review: { warnings: [], removed: ['I led a team of 40 (fictional).'], claim_checks: [{
    sentence: 'I ran release suites, which made it easier to catch issues (fictional).',
    problems: ['Unsupported claim (outcome): "which made" but no record it describes reports that result'],
    action: "kept what you did and removed the part your records don't state", now: 'I ran release suites (fictional).',
    checked_against: ['At Example Corp (fictional) as QA: Ran release suites (fictional).'],
    records: [{ id: 'EX03', text: 'Ran release suites (fictional).' }],
    now_records: [{ id: 'EX03', text: 'Ran release suites (fictional).' }] }] },
  created_at: '2026-11-21T10:00:00Z' };

async function workspace() {
  const ctx = setup([WorkspacePage]);
  const fixture = TestBed.createComponent(WorkspacePage);
  fixture.componentRef.setInput('id', '7');
  fixture.detectChanges();
  ctx.http.expectOne('/applications/7').flush(APP);
  await settle(fixture);
  ctx.http.match('/jobs/1').forEach(r => r.flush({ ...JOB, description: 'x'.repeat(900) }));   // the page and the tab
  await settle(fixture);
  return { ...ctx, fixture };
}

/** The Documents tab's own requests on open: documents, profile, job, progress (and the tools' reads). */
function openDocuments(http: any, docs: unknown[], prep: unknown = IDLE, profileAt = '2026-11-20T00:00:00Z') {
  http.expectOne('/applications/7/documents').flush(docs);
  http.expectOne('/profile').flush({ updated_at: profileAt });
  http.match('/jobs/1').forEach((r: any) => r.flush({ ...JOB, description: 'x'.repeat(900) }));
  http.match('/applications/7/handoff').forEach((r: any) => r.flush({ copy_fields: [], checklist: [], warnings: [],
    screening_answers: [], cover_letter: null, resume: null, company: 'x', apply_url: null }));
  http.match('/applications/7/submission/plan').forEach((r: any) => r.flush({}, { status: 404, statusText: 'No plan' }));
  return prep;
}

describe('WorkspacePage', () => {
  it('opens on its own page with a way back, the job, and the three tabs', async () => {
    const { fixture, http } = await workspace();
    openDocuments(http, []);
    await settle(fixture);
    http.expectOne('/applications/7/prepare').flush(IDLE);
    await settle(fixture);
    const page = text(fixture);
    expect(page).toContain('Back to applications');
    expect(page).toContain('QA Lead');
    expect(page).toContain('Example Corp (fictional)');
    for (const tab of ['Documents', 'Job & skill match', 'Activity & notes']) expect(page).toContain(tab);
    expect(page).toContain('Generate resume and cover letter');
    http.verify();                                    // opening the page asked for nothing else: no model calls
  });

  it('never marks an application submitted without confirmation evidence', async () => {
    const { fixture, http } = await workspace();
    openDocuments(http, []);
    await settle(fixture);
    http.expectOne('/applications/7/prepare').flush(IDLE);
    const select = fixture.nativeElement.querySelector('#app-status') as HTMLSelectElement;
    select.value = 'submitted_confirmed';
    select.dispatchEvent(new Event('change'));
    await settle(fixture);
    http.expectNone('/applications/7');               // nothing sent yet
    button(fixture, 'Mark submitted').click();
    await settle(fixture);
    expect(text(fixture)).toContain('Paste the confirmation evidence first.');
  });
});

describe('DocumentsTab', () => {
  async function tab(docs: unknown[], profileAt?: string) {
    const ctx = setup([DocumentsTab]);
    const fixture = TestBed.createComponent(DocumentsTab);
    fixture.componentRef.setInput('application', APP);
    fixture.detectChanges();
    openDocuments(ctx.http, docs, IDLE, profileAt);
    await settle(fixture);
    return { ...ctx, fixture };
  }

  it('one click starts one run; a second click while it runs does nothing', async () => {
    const { fixture, http } = await tab([]);
    http.expectOne('/applications/7/prepare').flush(IDLE);
    await settle(fixture);
    const generate = button(fixture, 'Generate resume and cover letter');
    generate.click();
    generate.click();
    const start = http.expectOne(r => r.url === '/applications/7/prepare' && r.method === 'POST');
    start.flush({ ...IDLE, state: 'queued', started_at: '2026-11-21T10:00:00Z' });
    await settle(fixture);
    http.match('/applications/7/prepare').forEach(r => r.flush({ ...IDLE, state: 'running', step: 'resume',
      started_at: '2026-11-21T10:00:00Z' }));
    await settle(fixture);
    expect(text(fixture)).toContain('Generating');
    http.expectNone(r => r.method === 'POST');
  });

  it('a failed document can be retried alone, and the other one stays ready', async () => {
    const { fixture, http } = await tab([LETTER_AI]);
    http.expectOne('/applications/7/prepare').flush({ ...IDLE, state: 'failed', done_steps: ['cover_letter'],
      failed_steps: { resume: 'The local AI isn\'t available.' }, steps: ['resume', 'cover_letter'] });
    await settle(fixture);
    expect(text(fixture)).toContain('Failed');
    button(fixture, 'Retry resume').click();
    const retry = http.expectOne(r => r.url === '/applications/7/prepare' && r.method === 'POST');
    expect(retry.request.body).toEqual({ steps: ['resume'] });
  });

  it('opens your saved edit first, keeps the AI draft one click away, and shows what was left out', async () => {
    const { fixture, http } = await tab([LETTER, LETTER_AI]);
    http.expectOne('/applications/7/prepare').flush(IDLE);
    await settle(fixture);
    expect(text(fixture)).toContain('Dear team (fictional),');
    expect(text(fixture)).toContain('v2 · your edit');
    expect(text(fixture)).toContain('I led a team of 40 (fictional).');      // excluded claim, in plain language
    expect(text(fixture)).toContain('Checked against your records (1)');     // what changed, and why
    expect(text(fixture)).toContain('Checked against: EX03');                  // the record, by its id
    expect(text(fixture)).toContain('Backed by: EX03');
    expect(text(fixture)).toContain('I ran release suites (fictional).');
    expect(text(fixture)).toContain('no record it describes reports that result');
    expect(text(fixture)).toContain('Download PDF');
  });

  it('says when your profile changed after the draft', async () => {
    const { fixture, http } = await tab([LETTER_AI], '2026-11-21T12:00:00Z');
    http.expectOne('/applications/7/prepare').flush(IDLE);
    await settle(fixture);
    expect(text(fixture)).toContain('Profile updated since this draft');
  });
});
