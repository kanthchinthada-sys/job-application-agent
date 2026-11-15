import { TestBed } from '@angular/core/testing';

import { button, settle, setup, text } from '../testing/setup';
import { ApplyPlan } from './apply-plan';

const PLAN = {
  apply_url: 'https://boards.greenhouse.io/examplecorp/jobs/111', approved: false, warnings: [],
  fields: [
    { name: 'first_name', label: 'First Name', type: 'text', required: true, options: [], answer: 'Jordan',
      source: 'profile', needs_you: false, sensitive: false, note: '' },
    { name: 'resume', label: 'Resume', type: 'file', required: true, options: [], answer: '',
      source: 'file', needs_you: false, sensitive: false, note: 'Your tailored resume (PDF)' },
    { name: 'question_1', label: 'How did you hear about us?', type: 'text', required: true, options: [], answer: '',
      source: 'you', needs_you: true, sensitive: false, note: '' },
    { name: 'question_4[]', label: 'Privacy policy', type: 'multi_select', required: true, options: ['Acknowledge/Confirm'],
      answer: '', source: 'you', needs_you: true, sensitive: true, note: 'Consent: only you can give it.' },
  ],
};
const doc = (plan: object, version = 1) => ({ id: 30, application_id: 7, kind: 'application_plan', question: '',
  version, content: JSON.stringify(plan), source: 'agent', review: {}, created_at: '2026-11-14T10:00:00' });

async function render() {
  const ctx = setup([ApplyPlan]);
  const fixture = TestBed.createComponent(ApplyPlan);
  fixture.componentRef.setInput('appId', 7);
  fixture.detectChanges();
  ctx.http.expectOne('/applications/7/submission/plan').flush({ detail: 'No plan yet' }, { status: 404, statusText: 'Not Found' });
  await settle(fixture);
  return { ...ctx, fixture };
}

describe('ApplyPlan', () => {
  it('shows the real form questions, flags the ones that need you, and never pre-ticks consent', async () => {
    const { fixture, http } = await render();
    button(fixture, 'Assisted apply').click();
    http.expectOne(r => r.method === 'POST' && r.url === '/applications/7/submission/plan').flush(doc(PLAN));
    await settle(fixture);
    expect(text(fixture)).toContain('not approved yet');
    expect(text(fixture)).toContain('you click Submit yourself');
    expect(fixture.nativeElement.querySelectorAll('tr.needs-you').length).toBe(2);
    const consent = fixture.nativeElement.querySelector('input[type=checkbox]') as HTMLInputElement;
    expect(consent.checked).toBe(false);
  });

  it('sends your answers when you approve, and shows the fill command', async () => {
    const { fixture, http } = await render();
    button(fixture, 'Assisted apply').click();
    http.expectOne(r => r.method === 'POST' && r.url === '/applications/7/submission/plan').flush(doc(PLAN));
    await settle(fixture);

    const heard = fixture.nativeElement.querySelector('input[aria-label="How did you hear about us?"]') as HTMLInputElement;
    heard.value = 'Company website';
    heard.dispatchEvent(new Event('input'));
    const consent = fixture.nativeElement.querySelector('input[type=checkbox]') as HTMLInputElement;
    consent.checked = true;
    consent.dispatchEvent(new Event('change'));
    button(fixture, 'Save my answers and approve').click();

    const req = http.expectOne('/applications/7/submission/approve');
    expect(req.request.body.answers).toEqual({
      first_name: 'Jordan', question_1: 'Company website', 'question_4[]': 'Acknowledge/Confirm',
    });
    req.flush(doc({ ...PLAN, approved: true }, 2));
    await settle(fixture);
    expect(text(fixture)).toContain('approved');
    expect(text(fixture)).toContain('python scripts/apply_greenhouse.py --application 7');
  });

  it('shows why approval was refused', async () => {
    const { fixture, http } = await render();
    button(fixture, 'Assisted apply').click();
    http.expectOne(r => r.method === 'POST' && r.url === '/applications/7/submission/plan').flush(doc(PLAN));
    await settle(fixture);
    button(fixture, 'Save my answers and approve').click();
    http.expectOne('/applications/7/submission/approve')
      .flush({ detail: 'Required question not answered: How did you hear about us?' }, { status: 409, statusText: 'Conflict' });
    await settle(fixture);
    expect(text(fixture)).toContain('Required question not answered');
  });
});
