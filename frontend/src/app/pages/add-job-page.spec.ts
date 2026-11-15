import { TestBed } from '@angular/core/testing';

import { button, settle, setup, text } from '../testing/setup';
import { AddJobPage } from './add-job-page';

function type(fixture: any, selector: string, value: string) {
  const el = fixture.nativeElement.querySelector(selector) as HTMLInputElement;
  el.value = value;
  el.dispatchEvent(new Event('input'));
}

const ADDED = {
  job: { id: 7, title: 'QA Lead', company: 'Example Corp (fictional)', location: 'Remote', source_url: null,
         description: 'Fictional posting', required_skills: ['Python', 'pytest'], preferred_skills: ['k6'],
         created_at: '2026-11-15T00:00:00Z' },
  intake: { method: 'job_intake_v1', title: 'QA Lead', company: 'Example Corp (fictional)', location: 'Remote',
            required_skills: ['Python', 'pytest'], preferred_skills: ['k6'],
            found_by: { title: 'rules', skills: 'rules' }, notes: ['Company name taken from the link; check it.'] },
};

describe('AddJobPage', () => {
  it('sends only the link and the description, then shows what was read', async () => {
    const { http } = setup([AddJobPage]);
    const fixture = TestBed.createComponent(AddJobPage);
    fixture.detectChanges();
    type(fixture, '#j-url', 'https://example.com/jobs/1');
    type(fixture, '#j-desc', 'Fictional posting: Python and pytest required.');
    button(fixture, 'Add job').click();
    const req = http.expectOne('/jobs/quick');
    expect(req.request.body).toEqual({ url: 'https://example.com/jobs/1',
      description: 'Fictional posting: Python and pytest required.', allow_duplicate: false });
    req.flush(ADDED);
    await settle(fixture);
    expect((fixture.nativeElement.querySelector('#a-title') as HTMLInputElement).value).toBe('QA Lead');
    expect((fixture.nativeElement.querySelector('#a-required') as HTMLInputElement).value).toBe('Python, pytest');
    expect(text(fixture)).toContain('Company name taken from the link');
  });

  it('saves fixes to what was read', async () => {
    const { http } = setup([AddJobPage]);
    const fixture = TestBed.createComponent(AddJobPage);
    fixture.detectChanges();
    type(fixture, '#j-desc', 'Fictional posting');
    button(fixture, 'Add job').click();
    http.expectOne('/jobs/quick').flush(ADDED);
    await settle(fixture);
    type(fixture, '#a-location', 'Toronto, ON');
    button(fixture, 'Save changes').click();
    const patch = http.expectOne('/jobs/7');
    expect(patch.request.method).toBe('PATCH');
    expect(patch.request.body.location).toBe('Toronto, ON');
    patch.flush(ADDED.job);
    await settle(fixture);
    expect(text(fixture)).toContain('Saved.');
  });

  it('asks before adding a second posting with the same title and company', async () => {
    const { http } = setup([AddJobPage]);
    const fixture = TestBed.createComponent(AddJobPage);
    fixture.detectChanges();
    type(fixture, '#j-desc', 'Fictional posting');
    button(fixture, 'Add job').click();
    http.expectOne('/jobs/quick').flush(
      { detail: 'You already have "QA Lead" at Example Corp (fictional) (job id 3). If this really is a different posting, add it again with allow_duplicate.', existing_id: 3 },
      { status: 409, statusText: 'Conflict' });
    await settle(fixture);
    expect(text(fixture)).toContain('You already have');
    expect(text(fixture)).not.toContain('allow_duplicate');
    button(fixture, "It's a different posting").click();
    const second = http.expectOne('/jobs/quick');
    expect(second.request.body.allow_duplicate).toBe(true);
    second.flush(ADDED);
    await settle(fixture);
  });

  it('says plainly when the posting was already added', async () => {
    const { http } = setup([AddJobPage]);
    const fixture = TestBed.createComponent(AddJobPage);
    fixture.detectChanges();
    button(fixture, 'Add job').click();
    http.expectOne('/jobs/quick').flush({ detail: 'This posting is already in your jobs (job id 3).', existing_id: 3 },
                                        { status: 409, statusText: 'Conflict' });
    await settle(fixture);
    expect(text(fixture)).toContain('already in your jobs (job id 3)');
  });
});
