import { HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';

import { Api, ApiError, toApiError } from './api';
import { setup } from './testing/setup';

describe('toApiError', () => {
  it('lists every field problem from a 422', () => {
    const err = toApiError(new HttpErrorResponse({
      status: 422,
      error: { detail: [{ loc: ['body', 'title'], msg: 'Field required' }, { loc: ['body'], msg: 'Bad input' }] },
    }));
    expect(err.status).toBe(422);
    expect(err.message).toBe('title: Field required\ninput: Bad input');
  });

  it('keeps the detail and extra data of a 409', () => {
    const err = toApiError(new HttpErrorResponse({ status: 409, error: { detail: 'Already added', existing_id: 3 } }));
    expect(err.message).toBe('Already added');
    expect(err.data.existing_id).toBe(3);
  });

  it('explains a server that is not running', () => {
    expect(toApiError(new HttpErrorResponse({ status: 0, headers: new HttpHeaders() })).message)
      .toContain('Cannot reach the app server');
  });
});

describe('Api', () => {
  it('sends a new job and returns it', async () => {
    const { http } = setup();
    const api = TestBed.inject(Api);
    const saved = api.createJob({ title: 'QA Lead', company: 'X', description: 'd', required_skills: ['Python'], preferred_skills: [] });
    const req = http.expectOne('/jobs');
    expect(req.request.method).toBe('POST');
    expect(req.request.body.required_skills).toEqual(['Python']);
    req.flush({ id: 5, title: 'QA Lead' });
    expect((await saved).id).toBe(5);
  });

  it('turns a backend error into an ApiError', async () => {
    const { http } = setup();
    const api = TestBed.inject(Api);
    const result = api.job(99).catch(e => e);
    http.expectOne('/jobs/99').flush({ detail: 'Job 99 not found' }, { status: 404, statusText: 'Not Found' });
    const err = await result;
    expect(err instanceof ApiError).toBe(true);
    expect(err.message).toBe('Job 99 not found');
  });

  it('sends you to sign in when the session has expired (401)', async () => {
    const { http, loginRedirects } = setup();
    const api = TestBed.inject(Api);
    const result = api.jobs().catch(e => e);
    http.expectOne('/jobs').flush({ detail: 'Sign in first.' }, { status: 401, statusText: 'Unauthorized' });
    await result;
    expect(loginRedirects.length).toBe(1);
  });
});
