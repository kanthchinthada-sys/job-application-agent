import { TestBed } from '@angular/core/testing';

import { button, settle, setup, text } from '../testing/setup';
import { FindJobsPage } from './find-jobs-page';

const JOB = {
  source: 'Remotive', title: 'QA Engineer', company: 'Globex (fictional)', location: 'Worldwide', posted: '2026-11-14',
  url: 'https://example.com/jobs/2', description: 'FICTIONAL.', description_is_excerpt: false, remote: true,
  where: 'Remote (worldwide)', title_match: 'strong', already_added_job_id: null,
};

describe('FindJobsPage', () => {
  beforeEach(() => sessionStorage.clear());

  async function render() {
    const ctx = setup([FindJobsPage]);
    const fixture = TestBed.createComponent(FindJobsPage);
    fixture.detectChanges();
    ctx.http.expectOne('/search/countries').flush({
      countries: [{ code: 'CA', name: 'Canada', adzuna: true }, { code: 'US', name: 'United States', adzuna: true }],
      adzuna_configured: false,
    });
    await settle(fixture);
    ctx.http.expectOne('/profile').flush({ full_name: 'Jordan (fictional)',
      preferences: { target_roles: ['QA Engineer'], locations: ['Canada'] } });
    await settle(fixture);
    return { ...ctx, fixture };
  }

  it('starts from your target role and searches with the three filters', async () => {
    const { fixture, http } = await render();
    expect((fixture.nativeElement.querySelector('#f-title') as HTMLInputElement).value).toBe('QA Engineer');
    button(fixture, 'Search').click();
    const req = http.expectOne('/search');
    expect(Object.keys(req.request.body).sort()).toEqual(['country', 'date_from', 'date_to', 'title']);
    expect(req.request.body.country).toBe('CA');
    req.flush({ method: 'job_search_v1', query: 'QA Engineer', country: 'Canada', date_from: '2026-11-08',
                date_to: '2026-11-14', results: [JOB],
                sources: [{ name: 'Remotive', ok: true, found: 4, kept: 1, note: '' },
                          { name: 'Adzuna', ok: false, found: 0, kept: 0, note: 'Off: add a free key.' }] });
    await settle(fixture);
    expect(text(fixture)).toContain('1 jobs in Canada');
    expect(text(fixture)).toContain('Remote (worldwide)');
    expect(text(fixture)).toContain('Adzuna: Off: add a free key.');
  });

  it('apply opens the employer page, and the resume and letter are prepared in the background', async () => {
    const { fixture, http } = await render();
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    button(fixture, 'Search').click();
    http.expectOne('/search').flush({ method: 'job_search_v1', query: 'QA Engineer', country: 'Canada',
      date_from: '2026-11-08', date_to: '2026-11-14', results: [JOB], sources: [] });
    await settle(fixture);
    button(fixture, 'Apply').click();
    expect(open).toHaveBeenCalledWith(JOB.url, '_blank', 'noopener');
    http.expectOne('/search/apply').flush({ job_id: 3, application_id: 5, apply_url: JOB.url, new_job: true,
                                            new_application: true, notes: [], preparation: 'queued' });
    await settle(fixture);
    http.expectOne('/applications/5/prepare').flush({ application_id: 5, state: 'done', step: null, message: '',
      done_steps: ['resume', 'cover_letter'], started_at: null, finished_at: null });
    await settle(fixture);
    expect(text(fixture)).toContain('Ready: resume and cover letter prepared.');
    open.mockRestore();
  });

  it('keeps your search when you leave the tab and come back', async () => {
    const first = await render();
    button(first.fixture, 'Search').click();
    first.http.expectOne('/search').flush({ method: 'job_search_v1', query: 'QA Engineer', country: 'Canada',
      date_from: '2026-11-08', date_to: '2026-11-14', results: [JOB], sources: [] });
    await settle(first.fixture);
    first.fixture.destroy();                                   // you open another tab ...
    const again = TestBed.createComponent(FindJobsPage);       // ... and come back
    again.detectChanges();
    first.http.expectOne('/search/countries').flush({ countries: [{ code: 'CA', name: 'Canada', adzuna: true }],
                                                      adzuna_configured: false });
    await settle(again);
    expect(text(again)).toContain('Globex (fictional)');
    expect((again.nativeElement.querySelector('#f-title') as HTMLInputElement).value).toBe('QA Engineer');
  });
});
