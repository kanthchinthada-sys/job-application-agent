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

  const SETTINGS = { configured: true, actor: 'jobo.world/ats-jobs-api', max_results: 25, timeout_secs: 120,
                     max_cost_usd: 0.2, reuse_minutes: 60 };
  const RUN = { id: 4, status: 'RUNNING', message: '', actor: 'jobo.world/ats-jobs-api',
    filters: { title: 'QA Engineer', country: 'CA', date_from: '2026-11-08', date_to: '2026-11-14' },
    found: null, kept: 0, cost_usd: null, max_cost_usd: 0.2, max_results: 25, timeout_secs: 120,
    started_at: new Date().toISOString(), finished_at: null, active: true, results: [] };
  const CAREER = { ...JOB, source: 'Workday career site (via Apify)', company: 'Initech (fictional)',
                   url: 'https://initech.example/careers/1', where: 'Toronto, ON' };
  // the same job as an Adzuna result: the employer's own link replaces it
  const AGG = { ...JOB, source: 'Adzuna', company: 'Initech (fictional)', url: 'https://adzuna.example/1',
                description_is_excerpt: true };

  it('runs the paid Apify search on request, shows progress and merges its results', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { fixture, http } = await render();
    http.expectOne('/search/apify').flush({ settings: SETTINGS, latest: null });
    await settle(fixture);
    expect(text(fixture)).toContain('Paid: up to 25 jobs from employer career sites');
    button(fixture, 'Search').click();
    http.expectOne('/search').flush({ method: 'job_search_v1', query: 'QA Engineer', country: 'Canada',
      date_from: '2026-11-08', date_to: '2026-11-14', results: [JOB, AGG], sources: [] });
    await settle(fixture);
    button(fixture, 'Search career sites (Apify)').click();
    const start = http.expectOne(r => r.method === 'POST' && r.url === '/search/apify');
    expect(Object.keys(start.request.body).sort()).toEqual(['country', 'date_from', 'date_to', 'title']);
    start.flush(RUN);
    await settle(fixture);
    expect(text(fixture)).toContain('running on Apify');
    expect(button(fixture, 'Apify search running').disabled).toBe(true);        // no second paid run
    expect(text(fixture)).toContain('Globex (fictional)');                          // free results still usable
    await vi.advanceTimersByTimeAsync(4100);
    http.expectOne('/search/apify/4').flush({ ...RUN, status: 'SUCCEEDED', active: false, found: 3, kept: 1,
                                               cost_usd: 0.01, results: [CAREER] });
    await settle(fixture);
    expect(text(fixture)).toContain('done: 3 found, 1 kept for your filters, cost US');
    const links = Array.from(fixture.nativeElement.querySelectorAll('td a')).map((a: any) => a.getAttribute('href'));
    expect(links).toContain('https://initech.example/careers/1');
    expect(links).not.toContain('https://adzuna.example/1');
    expect(text(fixture)).toContain('Workday career site (via Apify)');
    vi.useRealTimers();
  });

  it('says how to turn Apify on when it is not set up, and shows errors clearly', async () => {
    const { fixture, http } = await render();
    http.expectOne('/search/apify').flush({ settings: { ...SETTINGS, configured: false }, latest: null });
    await settle(fixture);
    expect(text(fixture)).toContain('add APIFY_API_TOKEN to .env');
    expect(() => button(fixture, 'Search career sites (Apify)')).toThrow();
  });

  it('shows the reason when Apify refuses a search', async () => {
    const { fixture, http } = await render();
    http.expectOne('/search/apify').flush({ settings: SETTINGS, latest: null });
    await settle(fixture);
    button(fixture, 'Search career sites (Apify)').click();
    http.expectOne(r => r.method === 'POST' && r.url === '/search/apify').flush(
      { detail: 'Apify refused the token (HTTP 401): check APIFY_API_TOKEN in .env.' }, { status: 502, statusText: 'Bad Gateway' });
    await settle(fixture);
    expect(text(fixture)).toContain('Apify refused the token');
  });
});
