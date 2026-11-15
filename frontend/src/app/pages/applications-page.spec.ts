import { TestBed } from '@angular/core/testing';

import { APP, button, settle, setup, text } from '../testing/setup';
import { ApplicationsPage } from './applications-page';

async function render() {
  const ctx = setup([ApplicationsPage]);
  const fixture = TestBed.createComponent(ApplicationsPage);
  fixture.detectChanges();
  ctx.http.expectOne('/applications').flush([APP]);
  await settle(fixture);
  return { ...ctx, fixture };
}

function choose(fixture: any, value: string) {
  const select = fixture.nativeElement.querySelector('select') as HTMLSelectElement;
  select.value = value;
  select.dispatchEvent(new Event('change'));
}

describe('ApplicationsPage', () => {
  it('offers only the statuses the backend allows next', async () => {
    const { fixture } = await render();
    const options = Array.from(fixture.nativeElement.querySelectorAll('option')).map((o: any) => o.value);
    expect(options).toEqual(['preparing', 'ready_for_review', 'withdrawn', 'submitted_confirmed']);
  });

  it('never lists a status twice', async () => {
    const { fixture } = await render();
    const page = fixture.componentInstance as any;
    expect(page.statusChoices({ ...APP, status: 'ready_for_review', allowed_next: ['ready_for_review', 'withdrawn'] }))
      .toEqual(['ready_for_review', 'withdrawn']);
  });

  it('changes a status', async () => {
    const { fixture, http } = await render();
    choose(fixture, 'ready_for_review');
    const req = http.expectOne('/applications/7');
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual({ status: 'ready_for_review' });
    req.flush({ ...APP, status: 'ready_for_review' });
    await settle(fixture);
    http.expectOne('/applications').flush([{ ...APP, status: 'ready_for_review' }]);
    await settle(fixture);
  });

  it('never marks an application confirmed without evidence', async () => {
    const { fixture, http } = await render();
    choose(fixture, 'submitted_confirmed');
    await settle(fixture);
    http.expectNone('/applications/7');                          // nothing sent yet
    expect(text(fixture)).toContain('Confirmation evidence (required)');

    button(fixture, 'Mark confirmed').click();                   // empty evidence
    await settle(fixture);
    http.expectNone('/applications/7');
    expect(text(fixture)).toContain('Paste the confirmation evidence first');

    const input = fixture.nativeElement.querySelector('#proof-7') as HTMLInputElement;
    input.value = 'Confirmation email "Thanks for applying" (fictional)';
    input.dispatchEvent(new Event('input'));
    button(fixture, 'Mark confirmed').click();
    const req = http.expectOne('/applications/7');
    expect(req.request.body).toEqual({
      status: 'submitted_confirmed', submission_confirmation: 'Confirmation email "Thanks for applying" (fictional)',
    });
    req.flush({});
    await settle(fixture);
    http.expectOne('/applications').flush([APP]);
    await settle(fixture);
  });
});

describe('ApplicationsPage cancel', () => {
  it('puts the status back when you cancel confirming', async () => {
    const { fixture, http } = await render();
    choose(fixture, 'submitted_confirmed');
    await settle(fixture);
    button(fixture, 'Cancel').click();
    await settle(fixture);
    http.expectNone('/applications/7');                          // nothing saved
    expect((fixture.nativeElement.querySelector('select') as HTMLSelectElement).value).toBe('preparing');
    expect(text(fixture)).not.toContain('Confirmation evidence (required)');
  });
});

describe('ApplicationsPage refused change', () => {
  it('shows the saved status again when the backend refuses a change', async () => {
    const { fixture, http } = await render();
    choose(fixture, 'withdrawn');
    http.expectOne('/applications/7').flush({ detail: 'Not allowed (fictional rule)' }, { status: 409, statusText: 'Conflict' });
    await settle(fixture);
    http.expectOne('/applications').flush([APP]);
    await settle(fixture);
    expect(text(fixture)).toContain('Not allowed (fictional rule)');
    expect((fixture.nativeElement.querySelector('select') as HTMLSelectElement).value).toBe('preparing');
  });
});
