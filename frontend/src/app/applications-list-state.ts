import { effect, Injectable, signal } from '@angular/core';

const KEY = 'job-agent.applications-list';

/** The Applications list's search, filter and scroll position: kept while you open an application and come back,
 *  and across a page refresh (this browser tab only). */
@Injectable({ providedIn: 'root' })
export class ApplicationsListState {
  readonly search = signal('');
  readonly status = signal('');
  private y = 0;

  constructor() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(KEY) ?? '{}');
      this.search.set(saved.search ?? '');
      this.status.set(saved.status ?? '');
      this.y = saved.scrollY ?? 0;
    } catch { /* storage unavailable: start empty */ }
    effect(() => this.persist({ search: this.search(), status: this.status() }));
  }

  get scrollY() { return this.y; }
  set scrollY(v: number) { this.y = v; this.persist({ search: this.search(), status: this.status() }); }

  private persist(v: { search: string; status: string }) {
    try { sessionStorage.setItem(KEY, JSON.stringify({ ...v, scrollY: this.y })); } catch { /* fine */ }
  }
}
