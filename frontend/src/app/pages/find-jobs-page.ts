import { afterNextRender, Component, computed, inject, Injector, OnDestroy, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { Api, ApiError } from '../api';
import { daysAgo, FindJobsState, today } from '../find-jobs-state';
import { ApifyRun, CountryOption, FoundJob, SearchPreset } from '../models';
import { Msg, MsgBox } from '../ui';

/** Find jobs: three filters (title, country, date range) across free job sources; Apply prepares everything. */
@Component({
  selector: 'app-find-jobs-page',
  imports: [FormsModule, MsgBox, RouterLink],
  host: { '(window:scroll)': 'rememberScroll()' },
  template: `
    <div class="card">
      <h2>Find jobs</h2>
      <div class="row filters">
        <div><label for="f-title">Job title</label>
          <input id="f-title" [(ngModel)]="title" placeholder="e.g. QA Engineer" (keyup.enter)="search()"></div>
        <div><label for="f-country">Country</label>
          <select id="f-country" [(ngModel)]="country">
            @for (c of countries(); track c.code) { <option [value]="c.code">{{ c.name }}</option> }
          </select></div>
        <div><label for="f-from">Posted from</label><input id="f-from" type="date" [(ngModel)]="dateFrom" [max]="today"></div>
        <div><label for="f-to">to</label><input id="f-to" type="date" [(ngModel)]="dateTo" [max]="today"></div>
      </div>
      <div class="seg" role="group" aria-label="Posted within" style="margin-top:10px; flex-wrap:wrap">
        @for (p of presets; track p.days) {
          <button type="button" [attr.aria-pressed]="activePreset() === p.days" (click)="preset(p.days)">{{ p.label }}</button>
        }
      </div>
      <div><button class="primary" type="button" [disabled]="busy()" (click)="search()">{{ busy() ? 'Searching…' : 'Search' }}</button></div>
      <details class="panel" (toggle)="openPresets($event)">
        <summary><b>Saved searches</b></summary>
        <div class="row">
          <label>Save these filters as<input [(ngModel)]="saveName" maxlength="100" placeholder="e.g. SDET Canada"></label>
          <button class="secondary" type="button" [disabled]="!saveName.trim() || !title().trim() || !country()"
                  (click)="savePreset()">Save search</button>
        </div>
        <p class="small muted">Running a saved search moves its date range forward and marks jobs newly found since the last run.</p>
        @for (preset of saved(); track preset.id) {
          <div class="actions">
            <b>{{ preset.name }}</b> <span class="small muted">{{ preset.filters.title }} · {{ preset.filters.country }}</span>
            <button class="secondary tight" type="button" [disabled]="busy()" (click)="runPreset(preset)">Run</button>
            <button class="link-btn" type="button" (click)="removePreset(preset)">Remove</button>
          </div>
        }
        @if (presetMessage()) { <p role="status" class="small">{{ presetMessage() }}</p> }
      </details>
      <div class="apify small" data-testid="apify">
        @if (state.apifySettings(); as a) {
          @if (a.configured) {
            <button class="secondary" type="button" [disabled]="state.apifyRun()?.active" (click)="searchApify()">
              {{ state.apifyRun()?.active ? 'Apify search running…' : 'Search career sites (Apify)' }}</button>
            <span class="muted"> Paid: up to {{ a.max_results }} jobs from employer career sites, stops after
              {{ a.timeout_secs }} s, at most {{ money(a.max_cost_usd) }} a search.</span>
          } @else {
            <span class="muted">Employer career sites (Apify, paid) are off: add APIFY_API_TOKEN to .env and restart
              the app (see docs/apify.md).</span>
          }
        }
        @if (state.apifyRun(); as run) {
          <div data-testid="apify-progress" [class.warn-text]="isFailed(run.status)">
            @if (run.active) { <span class="spinner"></span> }
            @if (run.filters; as filters) {
              Apify search "{{ filters.title }}" ({{ filters.date_from }} to {{ filters.date_to }}):
            }
            {{ statusText(run) }}
          </div>
        }
        @if (state.apifyError()) { <div class="warn-text" data-testid="apify-error">{{ state.apifyError() }}</div> }
      </div>
      @if (msg()?.kind === 'bad') { <app-msg [msg]="msg()" /> }
      @if (state.searchError()) { <p class="warn-text" role="alert">{{ state.searchError() }}</p> }
      @if (result(); as r) {
        <p class="small muted" data-testid="sources">
          @for (s of r.sources; track s.name) {
            <span [class.warn-text]="!s.ok" [title]="s.note">{{ s.name }}: {{ s.ok ? s.kept + ' kept of ' + s.found : 'off' }}</span>
            @if (!$last) { · }
          }
        </p>
        @for (s of r.sources; track s.name) {
          @if (!s.ok && s.note) { <div class="small muted">{{ s.name }}: {{ s.note }}</div> }
        }
        @if (r.elsewhere?.length) {
          <p class="small" data-testid="elsewhere">Search elsewhere (opens the same search on sites the app can't read):
            @for (l of r.elsewhere; track l.name) {
              <a class="button tight" [href]="l.url" target="_blank" rel="noopener" [title]="l.note">{{ l.name }}</a>
            }
          </p>
        }
      }
    </div>

    @if (busy()) {
      <div class="card empty" data-testid="searching"><span class="spinner"></span> Searching the job sources… this takes a few seconds.</div>
    }
    @if (result() || apifyJobs().length) {
      <div class="card" data-testid="found-jobs">
        @if (result(); as r) {
          <p class="small" style="margin-top:0"><b>{{ jobs().length }} jobs</b> in {{ r.country }}, posted {{ r.date_from }} to {{ r.date_to }}{{ apifyJobs().length ? ' (including employer career sites from Apify)' : '' }}.</p>
        } @else {
          <p class="small" style="margin-top:0"><b>{{ jobs().length }} jobs</b> from employer career sites (Apify).</p>
        }
        @if (jobs().length) {
          <div class="toolbar">
            <label>Order <select [(ngModel)]="order"><option value="recent">Newest</option><option value="fit">Technical overlap</option></select></label>
            <label><input type="checkbox" [(ngModel)]="newOnly"> Only new since last saved search</label>
          </div>
          <table>
            <thead><tr><th>Job</th><th>Where</th><th>Posted</th><th>Source</th><th></th></tr></thead>
            <tbody>
              @for (j of visibleJobs(); track j.url) {
                <tr>
                  <td><b><a [href]="j.url" target="_blank" rel="noopener">{{ j.title }}</a></b><br>
                    <span class="muted small">{{ j.company }}</span>
                    @if (j.new_since_last_run) { <span class="label">New</span> }
                    @if (j.salary_text) { <div class="small">{{ j.salary_text }}</div> }
                    @if (j.technical_matches?.length) { <div class="small good">Evidence: {{ j.technical_matches!.slice(0, 5).join(', ') }}</div> }
                    @if (j.technical_gaps?.length) { <div class="small warn-text">Review: {{ j.technical_gaps!.slice(0, 5).join(', ') }}</div> }</td>
                  <td class="small">{{ j.where }} @if (j.workplace && !j.where.toLowerCase().includes(j.workplace)) { · {{ j.workplace }} }</td>
                  <td class="small">{{ j.posted || '—' }}</td>
                  <td class="small muted">
                    @if (j.credit_url) { via <a [href]="j.credit_url" target="_blank" rel="noopener">{{ j.source }}</a> }
                    @else { {{ j.source }} }</td>
                  <td>
                    @if (state.rows()[j.url]; as s) {
                      <div class="small" [class.good]="s.kind === 'ok'" [class.warn-text]="s.kind === 'bad'">{{ s.step }}</div>
                      @if (s.applicationId) {
                        <a class="small" [routerLink]="['/applications', s.applicationId]">Open application</a>
                      }
                      @if (s.applyUrl) {
                        · <a class="small" [href]="s.applyUrl" target="_blank" rel="noopener">Employer's apply page</a>
                      }
                    } @else {
                      <button class="secondary tight" type="button" (click)="apply(j)">{{ j.already_added_job_id ? 'Apply (saved)' : 'Apply' }}</button>
                    }
                  </td>
                </tr>
              } @empty { <tr><td colspan="5">No newly found jobs in this saved search run.</td></tr> }
            </tbody>
          </table>
          <p class="small muted">Apply opens the employer's page in a new tab and prepares your tailored resume and cover
            letter in the background (you can keep browsing; open Materials to see them). You submit the application
            yourself; nothing is sent for you.</p>
        } @else {
          <div class="empty" data-testid="no-results">No jobs matched. Try a shorter title, a longer date range, or another country.</div>
        }
      </div>
    }
  `,
})
export class FindJobsPage implements OnInit, OnDestroy {
  private api = inject(Api);
  private injector = inject(Injector);
  private lastScroll: number | null = null;
  /** Filters, results and Apply progress live in a shared service, so they survive leaving this tab. */
  protected readonly state = inject(FindJobsState);

  protected readonly today = today();
  protected readonly presets = [
    { label: 'Last 24 hours', days: 1 }, { label: '3 days', days: 3 }, { label: '7 days', days: 7 },
    { label: '14 days', days: 14 }, { label: '30 days', days: 30 },
  ];
  protected readonly title = this.state.title;
  protected readonly country = this.state.country;
  protected readonly dateFrom = this.state.dateFrom;
  protected readonly dateTo = this.state.dateTo;
  protected readonly result = this.state.result;
  protected readonly countries = signal<CountryOption[]>([]);
  protected readonly apifyJobs = computed(() => {
    const run = this.state.apifyRun();
    return run && run.status === 'SUCCEEDED' ? run.results : [];
  });
  /** Your free results and the Apify results in one list: the same job once (title and company), the employer's
   * own career-site link preferred over an aggregator's. */
  protected readonly jobs = computed(() => {
    const key = (j: FoundJob) => `${j.title}|${j.company}`.toLowerCase().replace(/\W+/g, ' ').trim();
    const aggregator = (j: FoundJob) => /adzuna|jooble|jsearch/i.test(j.source) || j.description_is_excerpt;
    const out = new Map<string, FoundJob>();
    for (const j of this.result()?.results ?? []) out.set(key(j), j);
    for (const j of this.apifyJobs()) {
      const old = out.get(key(j));
      if (!old || aggregator(old)) out.set(key(j), j);
    }
    return [...out.values()].sort((a, b) => (b.posted ?? '').localeCompare(a.posted ?? ''));
  });
  protected readonly saved = signal<SearchPreset[]>([]);
  protected readonly presetMessage = signal('');
  protected readonly order = signal('recent');
  protected readonly newOnly = signal(false);
  protected saveName = '';
  protected readonly visibleJobs = computed(() => {
    const jobs = this.jobs().filter(j => !this.newOnly() || j.new_since_last_run);
    if (this.order() === 'fit') jobs.sort((a, b) =>
      (b.technical_matches?.length ?? 0) - (a.technical_matches?.length ?? 0) ||
      (a.technical_gaps?.length ?? 0) - (b.technical_gaps?.length ?? 0));
    return jobs;
  });
  protected readonly busy = this.state.searching;
  protected readonly msg = signal<Msg | null>(null);

  protected openPresets(e: Event) {
    if ((e.target as HTMLDetailsElement).open) void this.loadPresets();
  }

  private async loadPresets() {
    try { this.saved.set(await this.api.savedSearches()); }
    catch (e) { this.presetMessage.set((e as ApiError).message); }
  }

  protected async savePreset() {
    try {
      await this.api.saveSearch(this.saveName.trim(), { title: this.title(), country: this.country(),
        date_from: this.dateFrom(), date_to: this.dateTo() });
      this.saveName = '';
      this.presetMessage.set('Search saved.');
      await this.loadPresets();
    } catch (e) { this.presetMessage.set((e as ApiError).message); }
  }

  protected async runPreset(preset: SearchPreset) {
    this.newOnly.set(false);
    await this.state.runPreset(preset.id, preset.filters.title);
    await this.loadPresets();
  }

  protected async removePreset(preset: SearchPreset) {
    try { await this.api.deleteSavedSearch(preset.id); await this.loadPresets(); }
    catch (e) { this.presetMessage.set((e as ApiError).message); }
  }

  async ngOnInit() {
    const restoring = this.state.restoreSearch();
    const apify = restoring.then(() => this.state.loadApify(!this.title() && !this.result()));
    try {
      const c = await this.api.searchCountries();
      this.countries.set(c.countries);
    } catch (e) {
      this.msg.set({ kind: 'bad', text: (e as ApiError).message });
    }
    await restoring;
    if (this.result() || this.busy()) {
      this.restoreScroll();
      return;
    }
    if (!this.country()) this.country.set('CA');
    if (!this.title()) await apify;
    if (this.title() || this.state.apifyRun()?.filters || this.busy()) {
      this.restoreScroll();
      return;
    }
    try {   // a first visit starts from your own target role and country, if your profile has them
      const p = await this.api.profile() as { preferences?: { target_roles?: string[]; locations?: string[] } };
      if (this.title() || this.busy()) return;
      const role = p?.preferences?.target_roles?.[0];
      if (role && !this.title()) this.title.set(role);
      const locs: string[] = (p?.preferences?.locations ?? []).map((x: string) => x.toLowerCase());
      const hit = this.countries().find(c => locs.some(l => l.includes(c.name.toLowerCase())));
      if (hit) this.country.set(hit.code);
    } catch { /* no profile yet: fine */ }
  }

  ngOnDestroy() {
    if (this.result() || this.apifyJobs().length) this.state.scroll.set(this.lastScroll ?? window.scrollY);
  }

  protected rememberScroll() {
    this.lastScroll = window.scrollY;
  }

  private restoreScroll() {
    const position = this.state.scroll();
    this.lastScroll = position;
    if (position > 0) afterNextRender(() => window.scrollTo(0, position), { injector: this.injector });
  }

  /** The preset that matches the chosen dates (highlighted), or null for custom dates. */
  protected activePreset(): number | null {
    if (this.dateTo() !== today()) return null;
    return this.presets.find(p => this.dateFrom() === daysAgo(p.days - 1))?.days ?? null;
  }

  preset(days: number) {
    this.dateFrom.set(daysAgo(days - 1));
    this.dateTo.set(today());
  }

  async search() {
    if (this.title().trim().length < 2) {
      this.msg.set({ kind: 'bad', text: 'Enter a job title.' });
      return;
    }
    this.msg.set(null);
    await this.state.search({ title: this.title().trim(), country: this.country(),
                              date_from: this.dateFrom(), date_to: this.dateTo() });
  }

  /** Start the paid Apify search with the same filters (an explicit choice; the free results stay usable). */
  searchApify() {
    if (this.title().trim().length < 2) {
      this.msg.set({ kind: 'bad', text: 'Enter a job title.' });
      return;
    }
    this.state.startApify({ title: this.title().trim(), country: this.country(), date_from: this.dateFrom(),
                            date_to: this.dateTo() });
  }

  protected money(usd: number) { return 'US' + String.fromCharCode(36) + usd.toFixed(2); }

  protected isFailed(status: string) { return ['FAILED', 'ABORTED', 'TIMED-OUT', 'ERROR'].includes(status); }

  protected statusText(run: ApifyRun): string {
    if (run.active) {
      const secs = Math.max(0, Math.round((Date.now() - Date.parse(run.started_at)) / 1000));
      return `running on Apify (${secs} s of at most ${run.timeout_secs} s)… your other results stay usable.`;
    }
    if (run.status === 'SUCCEEDED') {
      const cost = run.cost_usd != null ? `, cost ${this.money(run.cost_usd)}` : '';
      const reused = run.reused === 'recent' ? ' (the same search from earlier: not charged again)' : '';
      return `done: ${run.found ?? 0} found, ${run.kept} kept for your filters${cost}.${reused}`;
    }
    return `${run.status.toLowerCase()}: ${run.message || 'no results'}`;
  }

  apply(j: FoundJob) {
    window.open(j.url, '_blank', 'noopener');          // must happen right in the click, or the browser blocks it
    this.state.apply(j);
  }
}
