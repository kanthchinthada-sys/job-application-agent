import { TestBed } from '@angular/core/testing';

import { APP, button, JOB, settle, setup, text } from '../testing/setup';
import { AddJobPage } from './add-job-page';

function type(fixture: any, selector: string, value: string) {
  const el = fixture.nativeElement.querySelector(selector) as HTMLInputElement;
  el.value = value;
  el.dispatchEvent(new Event('input'));
}

const POSTING = 'Fictional posting: QA Lead at Example Corp. Python and pytest required, plus CI/CD with Jenkins.';
const ADDED = {
  job: { id: 7, title: 'QA Lead', company: 'Example Corp (fictional)', location: 'Remote', source_url: null,
         description: POSTING, required_skills: ['Python', 'pytest'], preferred_skills: ['k6'],
         created_at: '2026-11-15T00:00:00Z' },
  intake: { method: 'job_intake_v1', title: 'QA Lead', company: 'Example Corp (fictional)', location: 'Remote',
            required_skills: ['Python', 'pytest'], preferred_skills: ['k6'],
            found_by: { title: 'rules', skills: 'rules' }, notes: ['Company name taken from the link; check it.'] },
};

function render() {
  const ctx = setup([AddJobPage]);
  const fixture = TestBed.createComponent(AddJobPage);
  fixture.detectChanges();
  return { ...ctx, fixture };
}

describe('AddJobPage', () => {
  it('pastes a description, then lets you correct what was read', async () => {
    const { fixture, http } = render();
    type(fixture, '#j-desc', POSTING);
    button(fixture, 'Add job').click();
    const req = http.expectOne('/jobs/quick');
    expect(req.request.body).toEqual({ url: null, description: POSTING, allow_duplicate: false });
    req.flush(ADDED);
    await settle(fixture);
    expect((fixture.nativeElement.querySelector('#a-title') as HTMLInputElement).value).toBe('QA Lead');
    expect(text(fixture)).toContain('Company name taken from the link');
    type(fixture, '#a-location', 'Toronto, ON');
    button(fixture, 'Save changes').click();
    const patch = http.expectOne('/jobs/7');
    expect(patch.request.body.location).toBe('Toronto, ON');
    patch.flush(ADDED.job);
    await settle(fixture);
    expect(text(fixture)).toContain('Saved.');
  });

  it('keeps the link when an import fails and offers pasting right away', async () => {
    const { fixture, http } = render();
    button(fixture, 'Import from link').click();
    fixture.detectChanges();
    type(fixture, '#j-url', 'https://example.com/jobs/1');
    button(fixture, 'Import job').click();
    expect(http.expectOne('/jobs/quick').request.body).toEqual(
      { url: 'https://example.com/jobs/1', description: null, allow_duplicate: false });
    await settle(fixture);
  });

  it('shows the saved job and its application when you paste a posting you already saved', async () => {
    const { fixture, http } = render();
    type(fixture, '#j-desc', POSTING);
    button(fixture, 'Add job').click();
    http.expectOne('/jobs/quick').flush({ detail: 'You already saved this posting.', existing_id: 1 },
                                        { status: 409, statusText: 'Conflict' });
    await settle(fixture);
    http.expectOne('/jobs/1').flush(JOB);
    http.expectOne('/applications').flush([APP]);
    await settle(fixture);
    expect(text(fixture)).toContain('You already saved this job');
    expect(text(fixture)).toContain('Open application');
    button(fixture, "It's a different posting").click();
    expect(http.expectOne('/jobs/quick').request.body.allow_duplicate).toBe(true);
  });

  it('saves at once, highlights the missing company, and lets the AI fill only what you left alone', async () => {
    AddJobPage.pollMs = 0;
    const { fixture, http } = render();
    type(fixture, '#j-desc', POSTING);
    button(fixture, 'Add job').click();
    const pending = { ...ADDED, job: { ...ADDED.job, company: 'Unknown company', required_skills: ['Python'] },
      intake: { ...ADDED.intake, company: 'Unknown company', review: ['company', 'skills'], ai_state: 'pending', notes: [] } };
    http.expectOne('/jobs/quick').flush(pending);
    await settle(fixture);
    const company = fixture.nativeElement.querySelector('#a-company') as HTMLInputElement;
    expect(company.value).toBe('');                                   // never "Unknown company" as a value
    expect(company.classList).toContain('unsure');
    expect(text(fixture)).toContain('Not found');
    expect(text(fixture)).toContain('checking the highlighted fields in the background');
    expect(text(fixture)).toContain(POSTING);                          // the posting stays available
    type(fixture, '#a-required', 'Python, pytest, Jenkins');           // you correct the skills meanwhile
    await settle(fixture);
    http.expectOne('/jobs/7/intake').flush({ ai_state: 'done', review: [], notes: [], found_by: {},
      applied: ['company', 'skills'], kept: [],
      job: { ...ADDED.job, company: 'Example Corp (fictional)', required_skills: ['Python', 'Docker'] } });
    await settle(fixture);
    expect(company.value).toBe('Example Corp (fictional)');           // untouched: the AI's (checked) answer
    expect((fixture.nativeElement.querySelector('#a-required') as HTMLInputElement).value).toBe('Python, pytest, Jenkins');
    expect(text(fixture)).toContain('Kept your correction for: skills');
    button(fixture, 'Save changes').click();
    const patch = http.expectOne('/jobs/7');
    expect(patch.request.body).toEqual({ required_skills: ['Python', 'pytest', 'Jenkins'] });   // only your change
    http.verify();
  });

  it('sends a company you type and shows why the AI could not help', async () => {
    AddJobPage.pollMs = 0;
    const { fixture, http } = render();
    type(fixture, '#j-desc', POSTING);
    type(fixture, '#j-company', 'Example Corp (fictional)');
    button(fixture, 'Add job').click();
    const req = http.expectOne('/jobs/quick');
    expect(req.request.body.company).toBe('Example Corp (fictional)');
    req.flush({ ...ADDED, intake: { ...ADDED.intake, review: ['title'], ai_state: 'pending', notes: [] } });
    await settle(fixture);
    http.expectOne('/jobs/7/intake').flush({ ai_state: 'failed', review: ['title'], applied: [], kept: [], found_by: {},
      notes: ['AI not used (it did not answer in time): the fields read from the posting are kept.'], job: ADDED.job });
    await settle(fixture);
    expect(text(fixture)).toContain("couldn't finish");
    expect(text(fixture)).toContain('did not answer in time');
    expect((fixture.nativeElement.querySelector('#a-title') as HTMLInputElement).classList).toContain('unsure');
  });
});
