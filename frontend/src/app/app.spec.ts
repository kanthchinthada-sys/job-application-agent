import { TestBed } from '@angular/core/testing';

import { App } from './app';
import { button, settle, setup, text } from './testing/setup';

describe('App', () => {
  it('shows the tabs and the AI status', async () => {
    const { http } = setup([App]);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    http.expectOne('/health').flush({ status: 'ok', password_protection: false });
    http.expectOne('/ai/status').flush({ reachable: true, model: 'qwen3:8b', model_available: true });
    await settle(fixture);
    const page = text(fixture);
    for (const tab of ['Jobs', 'Add job', 'Applications', 'Profile']) expect(page).toContain(tab);
    expect(page).toContain('AI ready · qwen3:8b');
    expect(page).not.toContain('Sign out');
  });

  it('says when the AI is offline, and offers Sign out when a password is set', async () => {
    const { http, loginRedirects } = setup([App]);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    http.expectOne('/health').flush({ status: 'degraded', password_protection: true });
    http.expectOne('/ai/status').flush({ reachable: false, model: 'qwen3:8b', model_available: false });
    await settle(fixture);
    expect(text(fixture)).toContain('AI offline');
    button(fixture, 'Sign out').click();
    http.expectOne('/logout').flush({});
    await settle(fixture);
    expect(loginRedirects.length).toBe(1);
  });
});
