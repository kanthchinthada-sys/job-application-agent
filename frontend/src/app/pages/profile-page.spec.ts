import { TestBed } from '@angular/core/testing';

import { button, settle, setup, text } from '../testing/setup';
import { ProfilePage } from './profile-page';

const CURRENT = {
  full_name: 'Jordan Sample (fictional)', contact: {}, skills: ['Python'], experience: [],
  experience_bank: [{ id: 'EX01', text: 'Built a fictional suite.', owner: 'Example Corp', owner_kind: 'experience',
                      group: null, status: 'verified', status_note: null, caveat: null }],
  preferences: { target_roles: ['QA Engineer'] }, resume_settings: { max_bullets: [6] }, updated_at: '2026-11-21T00:00:00Z',
};
const READ = {
  method: 'resume_import_v1', warnings: ['No job title found for Example Corp; please fill it in.'], sections_found: [],
  uncertain: ['experience.0.title'],
  profile: { full_name: 'Jordan Sample (fictional)', contact: { email: 'jordan@example.com' }, skills: ['Python', 'Selenium'],
             experience: [{ title: 'Role', company: 'Example Corp (fictional)', start_date: '2021-01',
                            highlights: ['Wrote fictional Selenium tests.'] }] },
};

describe('ProfilePage resume review', () => {
  it('flags what it could not read, lets you fix it, and keeps your bank, preferences and settings', async () => {
    const { http } = setup([ProfilePage]);
    const fixture = TestBed.createComponent(ProfilePage);
    fixture.detectChanges();
    http.expectOne('/auth/me').flush({ username: 'alex.fictional', accounts: true, local_data_available: false });
    http.match('/profile').forEach(r => r.flush(CURRENT));                 // the page and the bank panel
    await settle(fixture);
    const area = fixture.nativeElement.querySelector('[aria-label="Resume text"]') as HTMLTextAreaElement;
    area.value = 'Jordan Sample (fictional) resume text';
    area.dispatchEvent(new Event('input'));
    button(fixture, 'Read resume').click();
    http.expectOne('/profile/resume').flush(READ);
    await settle(fixture);
    expect(text(fixture)).toContain("couldn't be read confidently");
    const title = fixture.nativeElement.querySelector('#rv-t0') as HTMLInputElement;
    expect(title.classList).toContain('unsure');
    title.value = 'QA Analyst';
    title.dispatchEvent(new Event('input'));
    button(fixture, 'Replace my profile').click();
    await settle(fixture);
    http.expectOne('/profile').flush(CURRENT);
    await settle(fixture);
    const put = http.expectOne(r => r.method === 'PUT' && r.url === '/profile');
    expect(put.request.body.experience[0].title).toBe('QA Analyst');
    expect(put.request.body.skills).toEqual(['Python', 'Selenium']);
    expect(put.request.body.experience_bank).toEqual(CURRENT.experience_bank);
    expect(put.request.body.preferences).toEqual(CURRENT.preferences);
    expect(put.request.body.resume_settings).toEqual(CURRENT.resume_settings);
  });
});
