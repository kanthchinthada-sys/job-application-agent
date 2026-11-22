import { TestBed } from '@angular/core/testing';

import { APP, settle, setup, text } from '../testing/setup';
import { ApplicationsPage } from './applications-page';

const APPS = [
  { ...APP, documents_ready: ['resume', 'cover_letter'], last_activity: '2026-11-20T10:00:00Z' },
  { ...APP, id: 8, job_title: 'SDET', company: 'Northwind (fictional)', status: 'submitted_confirmed',
    documents_ready: [], last_activity: '2026-11-21T10:00:00Z' },
];

async function render() {
  sessionStorage.clear();
  const ctx = setup([ApplicationsPage]);
  const fixture = TestBed.createComponent(ApplicationsPage);
  fixture.detectChanges();
  ctx.http.expectOne('/applications').flush(APPS);
  await settle(fixture);
  return { ...ctx, fixture };
}

const rows = (fixture: any) => fixture.nativeElement.querySelectorAll('tbody tr').length;

describe('ApplicationsPage', () => {
  it('lists compact rows: status apart from document readiness, newest activity first', async () => {
    const { fixture } = await render();
    expect(rows(fixture)).toBe(2);
    const first = fixture.nativeElement.querySelector('tbody tr').textContent;
    expect(first).toContain('Northwind (fictional)');
    expect(text(fixture)).toContain('Resume ready');
    expect(text(fixture)).toContain('submitted confirmed');
    expect(fixture.nativeElement.querySelector('textarea')).toBeNull();          // no always-open notes boxes
  });

  it('searches by company and filters by status', async () => {
    const { fixture } = await render();
    const search = fixture.nativeElement.querySelector('#a-search') as HTMLInputElement;
    search.value = 'example corp';
    search.dispatchEvent(new Event('input'));
    await settle(fixture);
    expect(rows(fixture)).toBe(1);
    search.value = '';
    search.dispatchEvent(new Event('input'));
    const status = fixture.nativeElement.querySelector('#a-status') as HTMLSelectElement;
    status.value = 'submitted_confirmed';
    status.dispatchEvent(new Event('change'));
    await settle(fixture);
    expect(rows(fixture)).toBe(1);
    expect(text(fixture)).toContain('SDET');
  });
});
