import { TestBed } from '@angular/core/testing';

import { App } from './app';
import { button, settle, setup, text } from './testing/setup';

function boot(health: object, me: object, ai: object) {
  const ctx = setup([App]);
  const fixture = TestBed.createComponent(App);
  fixture.detectChanges();
  ctx.http.expectOne('/health').flush(health);
  ctx.http.expectOne('/auth/me').flush(me);
  ctx.http.expectOne('/ai/status').flush(ai);
  return { ...ctx, fixture };
}

describe('App', () => {
  it('shows the sidebar, Add job everywhere, and the AI status with the model name', async () => {
    const { fixture } = boot({ status: 'ok', password_protection: false },
                             { username: null, accounts: false, local_data_available: false },
                             { reachable: true, model: 'qwen3:8b', model_available: true });
    await settle(fixture);
    const page = text(fixture);
    for (const item of ['Find jobs', 'Saved jobs', 'Applications', 'Profile', '+ Add job']) expect(page).toContain(item);
    expect(page).toContain('AI ready');
    expect(fixture.nativeElement.querySelector('[data-testid=ai-model]').textContent).toBe('qwen3:8b');
    expect(page).not.toContain('Sign out');
  });

  it('shows the signed-in account, says how to reconnect an offline AI, and signs out', async () => {
    const { fixture, http, loginRedirects } = boot(
      { status: 'degraded', password_protection: false, accounts: true },
      { username: 'alex.fictional', accounts: true, local_data_available: false },
      { reachable: false, model: 'qwen3:8b', model_available: false });
    await settle(fixture);
    expect(text(fixture)).toContain('alex.fictional');
    expect(text(fixture)).toContain('AI offline');
    expect(text(fixture)).toContain('ollama serve');
    button(fixture, 'Sign out').click();
    http.expectOne('/logout').flush({});
    await settle(fixture);
    expect(loginRedirects.length).toBe(1);
  });

  it('never shows other people\'s local data, and signing out clears what this tab kept', async () => {
    sessionStorage.setItem('job-agent.applications-list', JSON.stringify({ search: 'Acme', status: '' }));
    const { fixture, http } = boot({ status: 'ok', password_protection: false, accounts: true },
      { username: 'alex.fictional', accounts: true, local_data_available: true },
      { reachable: true, model: 'qwen3:8b', model_available: true });
    await settle(fixture);
    expect(text(fixture)).not.toContain('Existing local data');            // the owner brings it in on Profile
    button(fixture, 'Sign out').click();
    http.expectOne('/logout').flush({});
    await settle(fixture);
    expect(sessionStorage.getItem('job-agent.applications-list')).toBeNull();
  });
});
