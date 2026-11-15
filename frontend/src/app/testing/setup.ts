import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { authInterceptor, GO_TO_LOGIN } from '../auth.interceptor';

/** Stands in for any page the code navigates to (e.g. /jobs/4 after saving), so navigation succeeds in tests. */
@Component({ selector: 'app-test-page', template: '' })
class AnyPage {}

/** Test setup: fake backend (HttpTestingController), no real navigation, sign-in redirects recorded. */
export function setup(imports: unknown[] = []) {
  const loginRedirects: number[] = [];
  TestBed.configureTestingModule({
    imports: imports as any[],
    providers: [
      provideRouter([{ path: '**', component: AnyPage }]),
      provideHttpClient(withInterceptors([authInterceptor])),
      provideHttpClientTesting(),
      { provide: GO_TO_LOGIN, useValue: () => loginRedirects.push(1) },
    ],
  });
  return { http: TestBed.inject(HttpTestingController), loginRedirects };
}

/** Let promises, signals and rendering finish. */
export async function settle(fixture?: ComponentFixture<unknown>) {
  for (let i = 0; i < 3; i++) {
    await new Promise(r => setTimeout(r));
    if (fixture) await fixture.whenStable();
  }
}

export const text = (fixture: ComponentFixture<unknown>) => (fixture.nativeElement as HTMLElement).textContent ?? '';

export function button(fixture: ComponentFixture<unknown>, label: string): HTMLButtonElement {
  const all = Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('button'));
  const found = all.find(b => b.textContent?.trim().startsWith(label));
  if (!found) throw new Error(`No button "${label}". Buttons: ${all.map(b => b.textContent?.trim()).join(' | ')}`);
  return found;
}

export const JOB = {
  id: 1, title: 'QA Lead', company: 'Example Corp (fictional)', location: 'Toronto, ON', source_url: null,
  description: 'Fictional posting', required_skills: ['Python'], preferred_skills: [],
};

export const APP = {
  id: 7, job_id: 1, job_title: 'QA Lead', company: 'Example Corp (fictional)', source_url: null,
  status: 'preparing', notes: '', submission_confirmation: null,
  history: [{ occurred_at: '2026-11-14T10:00:00', from_status: null, to_status: 'preparing' }],
  allowed_next: ['ready_for_review', 'withdrawn', 'submitted_confirmed'],
};
