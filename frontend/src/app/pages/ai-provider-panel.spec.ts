import { TestBed } from '@angular/core/testing';

import { button, settle, setup, text } from '../testing/setup';
import { AiProviderPanel } from './ai-provider-panel';

describe('AiProviderPanel', () => {
  async function render(settings: object) {
    const ctx = setup([AiProviderPanel]);
    const fixture = TestBed.createComponent(AiProviderPanel);
    fixture.detectChanges();
    ctx.http.expectOne('/ai/settings').flush(settings);
    await settle(fixture);
    return { ...ctx, fixture };
  }

  it('starts on the local model and saves a Claude key, showing only its last characters', async () => {
    const { fixture, http } = await render({ provider: 'ollama', model: null, has_key: false, key_hint: null, updated_at: null });
    expect(text(fixture)).toContain('Now using: the local model (Ollama)');
    const claude = fixture.nativeElement.querySelector('input[value="claude"]') as HTMLInputElement;
    claude.click();
    await settle(fixture);
    expect((button(fixture, 'Save') as HTMLButtonElement).disabled).toBe(true);       // no key yet
    const key = fixture.nativeElement.querySelector('[data-testid="ai-key"]') as HTMLInputElement;
    key.value = 'sk-ant-fictional-test-key-0000000000001234';
    key.dispatchEvent(new Event('input'));
    await settle(fixture);
    button(fixture, 'Save').click();
    const req = http.expectOne('/ai/settings');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual({ provider: 'claude', api_key: 'sk-ant-fictional-test-key-0000000000001234' });
    req.flush({ provider: 'claude', model: 'claude-sonnet-5-5', has_key: true, key_hint: '…1234', updated_at: 'now' });
    await settle(fixture);
    expect(text(fixture)).toContain('Claude, claude-sonnet-5-5 (key …1234)');
    expect(key.value).toBe('');                                                         // the key is not kept on screen
  });

  it('shows why a key was not accepted', async () => {
    const { fixture, http } = await render({ provider: 'openai', model: 'gpt-5.1', has_key: true, key_hint: '…5678', updated_at: 'x' });
    expect(text(fixture)).toContain('OpenAI, gpt-5.1 (key …5678)');
    button(fixture, 'Save').click();                                                     // keeps the saved key
    const req = http.expectOne('/ai/settings');
    expect(req.request.body).toEqual({ provider: 'openai', api_key: null });
    req.flush({ detail: 'Not saved: API key not accepted (gpt-5.1).' }, { status: 400, statusText: 'Bad Request' });
    await settle(fixture);
    expect(text(fixture)).toContain('API key not accepted');
  });
});
