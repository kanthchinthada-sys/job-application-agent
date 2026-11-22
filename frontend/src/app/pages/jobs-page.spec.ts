import { TestBed } from '@angular/core/testing';

import { APP, button, JOB, settle, setup, text } from '../testing/setup';
import { JobsPage } from './jobs-page';

const MATCH = {
  job_id: 1, method_label: 'Rule-based evidence comparison. Not an AI assessment.', limitations: ['Rule-based'],
  required: {
    total: 3, coverage_percent: 66.7, missing: ['Kubernetes'], mentioned_only: [],
    matched: [{ skill: 'Python/Java/Scala/go', normalized: 'python/java/scala/go', found_parts: ['python', 'java', 'go'],
                missing_parts: ['scala'], evidence: [{ kind: 'declared', source: 'profile skills', text: 'Python, Java, Go', via: 'python', part: 'python' }] }],
    related: [{ skill: 'Data pipelines', normalized: 'data pipelines', found_parts: ['data pipelines'], missing_parts: [],
                evidence: [{ kind: 'related', source: 'ETL Tester at BankCo (fictional)', text: 'Validated ETL mappings (fictional).', via: 'etl', part: 'data pipelines' }] }],
  },
  preferred: { total: 0, coverage_percent: null, matched: [], related: [], mentioned_only: [], missing: [] },
};

describe('JobsPage', () => {
  it('gives each saved job its next action: Prepare application, or Open application', async () => {
    const { http } = setup([JobsPage]);
    const fixture = TestBed.createComponent(JobsPage);
    fixture.detectChanges();
    http.expectOne('/jobs').flush([JOB, { ...JOB, id: 2, title: 'SDET' }]);
    http.expectOne('/applications').flush([{ ...APP, documents_ready: ['resume'] }]);
    await settle(fixture);
    const page = text(fixture);
    expect(page).toContain('Saved jobs');
    expect(page).toContain('Open application');         // job 1 has one
    expect(page).toContain('Prepare application');      // job 2 doesn't
    expect(page).toContain('Resume ready');
  });

  it('shows the keyword match with evidence for the job in the URL', async () => {
    const { http } = setup([JobsPage]);
    const fixture = TestBed.createComponent(JobsPage);
    fixture.componentRef.setInput('id', '1');
    fixture.detectChanges();
    http.expectOne('/jobs').flush([JOB]);
    http.expectOne('/applications').flush([]);
    http.expectOne('/jobs/1').flush(JOB);
    http.expectOne('/jobs/1/match').flush(MATCH);
    await settle(fixture);
    const page = text(fixture);
    expect(page).toContain('Rule-based evidence comparison');            // the method is always labelled
    expect(page).toContain('Required: 2 of 3 shown by your profile');
    expect(page).toContain('python, java, go (not found: scala)');
    expect(page).toContain('≈ Data pipelines');
    expect(page).toContain('related experience, check it: “etl” in ETL Tester at BankCo');
    expect(page).toContain('✗ Kubernetes');
  });

  it('asks the AI for a second look and shows only quoted suggestions', async () => {
    const { http } = setup([JobsPage]);
    const fixture = TestBed.createComponent(JobsPage);
    fixture.componentRef.setInput('id', '1');
    fixture.detectChanges();
    http.expectOne('/jobs').flush([JOB]);
    http.expectOne('/applications').flush([]);
    http.expectOne('/jobs/1').flush(JOB);
    http.expectOne('/jobs/1/match').flush(MATCH);
    await settle(fixture);
    button(fixture, 'Ask AI').click();
    http.expectOne('/jobs/1/match/ai').flush({
      method_label: 'AI suggestions', model: 'fake', rejected: 1, still_missing: [],
      suggestions: [{ requirement: 'Kubernetes', fact_id: 'F6', fact: 'Validated Helm upgrades (fictional).',
                      quote: 'Validated Helm upgrades', reason: 'Helm runs on Kubernetes' }],
    });
    await settle(fixture);
    expect(text(fixture)).toContain('AI: Kubernetes');
    expect(text(fixture)).toContain('“Validated Helm upgrades” (F6)');
  });

  it('asks for a profile before comparing', async () => {
    const { http } = setup([JobsPage]);
    const fixture = TestBed.createComponent(JobsPage);
    fixture.componentRef.setInput('id', '1');
    fixture.detectChanges();
    http.expectOne('/jobs').flush([JOB]);
    http.expectOne('/applications').flush([]);
    http.expectOne('/jobs/1').flush(JOB);
    http.expectOne('/jobs/1/match').flush({ detail: 'No profile yet' }, { status: 404, statusText: 'Not Found' });
    await settle(fixture);
    expect(text(fixture)).toContain('Save your profile first');
  });

  it('Prepare application starts it and opens its workspace', async () => {
    const { http } = setup([JobsPage]);
    const fixture = TestBed.createComponent(JobsPage);
    fixture.detectChanges();
    http.expectOne('/jobs').flush([JOB]);
    http.expectOne('/applications').flush([]);
    await settle(fixture);
    button(fixture, 'Prepare application').click();
    const req = http.expectOne('/applications');
    expect(req.request.body).toEqual({ job_id: 1 });
    req.flush(APP);
    await settle(fixture);
  });
});
